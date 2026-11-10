"""Native metadata journals do not make unchanged app-owned history dirty."""
import copy
import json
import time

import pytest

from amplifier_web import session_projection
from amplifier_web.session_files import project_slug
from amplifier_web.state_records import load as load_saved_state
from amplifier_web.storage_migration import close_maintenance
from test_automatic_history import app_factory, native_session
from test_history_publication_scope import prepared


async def owned_history(tmp_path, app_factory, monkeypatch, *, kind='root', large=False):
    app, workspace, paths, owners = await prepared(tmp_path, app_factory, monkeypatch)
    owner = owners[kind]
    owner.update(historyManaged=False, status='working', historyLoaded=True)
    count = 3500 if large else 8
    owner['messages'] = [{'id': f'message-{i}', 'role': 'assistant',
                          'text': f'Preserved synthetic answer {i}: ' + 'x' * 2200,
                          'createdAt': 100 + i} for i in range(count)]
    owner['execution'] = {
        'nodes': [{'id': f'node-{i}', 'turnId': 'old-turn', 'sessionId': owner['id'],
                   'kind': 'tool', 'phase': 'completed', 'label': 'Saved step',
                   'output': 'y' * 2200} for i in range(8000 if large else 8)],
        'retiredUsageNodes': [{'id': f'usage-{i}', 'turnId': 'old-turn',
                               'sessionId': owner['id'], 'kind': 'llm',
                               'phase': 'completed', 'usage': {'inputTokens': i, 'outputTokens': 2}}
                              for i in range(14000 if large else 8)],
        'turns': [{'id': 'old-turn', 'phase': 'completed'}],
    }
    app._publish_changes(sessions={owner['id']})
    await app.history.refresh()  # Settle adopted naming/placement provenance.
    await app.history.refresh(force=False)
    await close_maintenance(app)
    assert app.state['sharedHistory']['error'] is None
    return app, workspace, paths, owner


def observe(monkeypatch, app):
    result = {'saves': [], 'deltas': [], 'messagePreparations': 0, 'executionPreparations': 0}
    persist = session_projection.persist
    scan = app.history.index.scan_changes
    messages = app._message_preparation.prepare
    execution = app._execution_preparation.prepare

    def save(*args, **kwargs):
        result['saves'].append(sorted(kwargs.get('session_ids') or ()))
        return persist(*args, **kwargs)

    def changes(**kwargs):
        token, delta = scan(**kwargs)
        result['deltas'].append(copy.deepcopy(delta))
        return token, delta

    def prepare_messages(*args, **kwargs):
        result['messagePreparations'] += 1
        return messages(*args, **kwargs)

    def prepare_execution(*args, **kwargs):
        result['executionPreparations'] += 1
        return execution(*args, **kwargs)

    monkeypatch.setattr(session_projection, 'persist', save)
    monkeypatch.setattr(app.history.index, 'scan_changes', changes)
    monkeypatch.setattr(app._message_preparation, 'prepare', prepare_messages)
    monkeypatch.setattr(app._execution_preparation, 'prepare', prepare_execution)
    return result


def metadata_change(monkeypatch, app, workspace, paths, kind, **updates):
    path = paths[kind] / 'metadata.json'
    path.write_text(json.dumps({**json.loads(path.read_text()), **updates}))
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), kind, 'metadata')}))


@pytest.mark.parametrize('kind', ['root', 'worker'])
async def test_metadata_only_journal_keeps_unchanged_resident_history_out_of_save(
        tmp_path, app_factory, monkeypatch, kind):
    app, workspace, paths, owner = await owned_history(
        tmp_path, app_factory, monkeypatch, kind=kind, large=kind == 'root')
    saved = copy.deepcopy(owner)
    observed = observe(monkeypatch, app)
    metadata_change(monkeypatch, app, workspace, paths, kind,
                    updated_at=9999999999, turn_count=99)
    started = time.monotonic()
    await app.history.refresh(force=False)
    elapsed = time.monotonic() - started
    assert app.state['sharedHistory']['error'] is None
    assert len(observed['deltas']) == 1
    delta = observed['deltas'][0]
    assert not delta['reset'] and [row['nativeIdentity'] for row in delta['sessions']] == [kind]
    assert not delta['workspaces'] and not delta['removed'] and not delta['removedProjects']
    print(json.dumps({'kind': kind, 'refreshSeconds': elapsed,
        'savedSessions': observed['saves'], 'messagePreparations': observed['messagePreparations'],
        'executionPreparations': observed['executionPreparations'],
        'messagesBytes': len(json.dumps(saved['messages']).encode()),
        'executionBytes': len(json.dumps(saved['execution']).encode())}))
    assert owner == saved
    assert observed['saves'] == [[]]
    assert observed['messagePreparations'] == observed['executionPreparations'] == 0
    assert not app.db.in_transaction
    assert app.runtime.started == [] and app.runtime.sent == []


@pytest.mark.parametrize('change', ['name', 'transcript', 'parent'])
async def test_real_native_resident_changes_still_save_current_owner(
        tmp_path, app_factory, monkeypatch, change):
    app, workspace, paths, owner = await owned_history(tmp_path, app_factory, monkeypatch)
    if change == 'name':
        metadata_change(monkeypatch, app, workspace, paths, 'root', name='New canonical title')
    elif change == 'parent':
        native_session(workspace, 'new-parent')
        await app.history.refresh()
        metadata_change(monkeypatch, app, workspace, paths, 'root', parent_id='new-parent')
    else:
        with (paths['root'] / 'transcript.jsonl').open('a') as stream:
            stream.write(json.dumps({'role': 'assistant', 'content': 'New progress'}) + '\n')
        monkeypatch.setattr(app.history.index, '_invalidations',
                            lambda: (True, {(project_slug(workspace), 'root', 'transcript')}))
    observed = observe(monkeypatch, app)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert observed['saves'] and owner['id'] in observed['saves'][0]
    assert observed['messagePreparations'] == 1
    if change == 'name':
        assert owner['title'] == app.directory.resolve(owner['id'])['title'] == 'New canonical title'
    elif change == 'parent':
        assert owner['parentId'] == 'new-parent' and owner['nativeParentId'] == 'new-parent'
    else:
        assert owner['_catalogRecentAt'] == (paths['root'] / 'transcript.jsonl').stat().st_mtime_ns / 1e9
    assert app.runtime.started == [] and app.runtime.sent == []


async def test_metadata_noop_does_not_drop_pending_runtime_scope(tmp_path, app_factory, monkeypatch):
    app, workspace, paths, owner = await owned_history(tmp_path, app_factory, monkeypatch)
    owner['messages'].append({'id': 'pending', 'role': 'assistant', 'text': 'Pending runtime answer'})
    app._publish_progress(session_ids={owner['id']}, record_only=True, detail_only=True)
    observed = observe(monkeypatch, app)
    metadata_change(monkeypatch, app, workspace, paths, 'root', updated_at=9999999999)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert any(owner['id'] in scope for scope in observed['saves'])
    assert observed['messagePreparations'] >= 1 and not app._progress_dirty
    reference = next(row for row in load_saved_state(app.db)['sessions'] if row['id'] == owner['id'])
    from amplifier_web.cold_display import load
    from amplifier_web.cold_display import MARKER
    payload = load(app.db, reference['$viewPayload'])
    messages = load(app.db, payload[MARKER]['messages'])
    assert messages[-1]['text'] == 'Pending runtime answer'


@pytest.mark.parametrize('force', [False, True])
async def test_failed_changed_resident_save_keeps_owner_in_retry_union(
        tmp_path, app_factory, monkeypatch, force):
    import sqlite3
    app, workspace, paths, owner = await owned_history(tmp_path, app_factory, monkeypatch)
    metadata_change(monkeypatch, app, workspace, paths, 'root', name='Title awaiting durable save')
    before = app.history._native_revision
    def fail():
        raise sqlite3.OperationalError('Fixture refuses publication')
    with monkeypatch.context() as patch:
        patch.setattr(app, '_save', fail)
        await app.history.refresh(force=force)
    assert owner['title'] == 'Title awaiting durable save'
    assert app.state['sharedHistory']['error']
    assert app.history._native_revision == before
    assert owner['id'] in app._progress_session_ids
    observed = observe(monkeypatch, app)
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None and not app._progress_dirty
    assert any(owner['id'] in scope for scope in observed['saves'])
    from amplifier_web.cold_display import load
    reference = next(row for row in load_saved_state(app.db)['sessions'] if row['id'] == owner['id'])
    assert load(app.db, reference['$viewPayload'])['title'] == 'Title awaiting durable save'
