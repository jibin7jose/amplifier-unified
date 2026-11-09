"""Native progress commits its owners without encoding unchanged global registries."""
import copy
import asyncio
import json
import threading
import time

import pytest

from amplifier_web import directory_attention
from amplifier_web.session_files import project_slug
from amplifier_web.state_records import load as load_saved_state
from amplifier_web.storage_migration import close_maintenance
from test_automatic_history import app_factory, native_session


@pytest.mark.parametrize('admission', [True, False])
@pytest.mark.parametrize('outcome', ['success', 'error', 'cancel'])
async def test_admission_history_commits_result_without_an_intermediate_full_save(
        tmp_path, app_factory, monkeypatch, admission, outcome):
    from amplifier_web import automatic_history
    from amplifier_web.cold_display import load
    from amplifier_web.state_records import load_owner

    directory = native_session(tmp_path / 'native', 'history-commit')
    app = app_factory()
    await app.history.refresh()
    row = app._session('history-commit')
    row.update(historyManaged=False, draft='Unsent draft')
    app._publish_changes(sessions={row['id']}, detail_only=True)
    await close_maintenance(app)

    def saved():
        descriptor, _ = load_owner(app.db, row['id'])
        return load(app.db, descriptor['$viewPayload'])

    before = saved()
    source_before = (directory / 'transcript.jsonl').read_bytes()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original_read, original_save = automatic_history.read_transcript, app._save
    commits = []

    def read(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        if outcome == 'error':
            raise OSError('Unreadable saved history')
        return original_read(*args, **kwargs)

    def save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        commits.append(saved())
        return result

    monkeypatch.setattr(automatic_history, 'read_transcript', read)
    monkeypatch.setattr(app, '_save', save)
    pending = asyncio.create_task(app.history.ensure_loaded(row['id']) if admission
                                  else app.history.load(row['id'], canonical=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert row['historyLoading'] and app.history.loads[row['id']].locked()
        assert len(commits) == (0 if admission else 1)
        assert saved() == (before if admission else commits[0])
        if not admission:
            assert saved()['historyLoading']
        row['draft'] = 'Changed while reading'
        if outcome == 'cancel':
            pending.cancel()
    finally:
        release.set()
        result = (await asyncio.gather(pending, return_exceptions=True))[0]

    if outcome == 'cancel':
        assert isinstance(result, asyncio.CancelledError)
        assert len(commits) == (0 if admission else 1)
        assert not app.history.loads[row['id']].locked()
        # Cancellation is not success. A subsequent admission must still read
        # and durably settle the same source without running conversation work.
        monkeypatch.setattr(automatic_history, 'read_transcript', original_read)
        await app.history.ensure_loaded(row['id'])
    elif outcome == 'error':
        assert isinstance(result, ValueError) if admission else result is None
        assert saved()['historyError'] and not saved()['historyLoaded']
    else:
        assert result is None
    assert len(commits) == (1 if admission else 2)
    after = saved()
    assert after['draft'] == 'Changed while reading' and not after['historyLoading']
    if outcome != 'error':
        assert after['historyLoaded'] and not after['historyError']
        assert [message['text'] for message in after['messages']] == ['Saved CLI question', 'Saved CLI answer']
    assert (directory / 'transcript.jsonl').read_bytes() == source_before
    assert not app.runtime.started and not app.runtime.sent


async def prepared(tmp_path, app_factory, monkeypatch):
    workspace = tmp_path / 'native'
    paths = {
        'root': native_session(workspace, 'root'),
        'worker': native_session(workspace, 'worker', metadata={'parent_id': 'root'}),
    }
    app = app_factory()
    await app.history.refresh()
    owners = {key: app._session(key, hydrate=False) for key in paths}
    # Retained registrations are unrelated to the native transcript update.
    app.state['workspaces'].extend(
        {'id': f'extra-{number}', 'name': f'Workspace {number}',
         'path': str(workspace), 'available': True, 'description': 'retained ' * 256}
        for number in range(128))
    app._publish()
    index = app.history.index
    index._reconcile_at = time.monotonic() + 60
    monkeypatch.setattr(index, '_invalidations', lambda: (True, set()))
    await app.history.refresh()  # Settle the newly learned registered paths.
    await app.history.refresh(force=False)
    await close_maintenance(app)
    assert app.state['sharedHistory']['error'] is None
    return app, workspace, paths, owners


@pytest.mark.parametrize('kind', ['root', 'worker'])
async def test_native_progress_does_not_serialize_unchanged_globals(
        tmp_path, app_factory, monkeypatch, kind):
    app, workspace, paths, owners = await prepared(tmp_path, app_factory, monkeypatch)
    prior = {key: copy.deepcopy(app.state[key]) for key in ('workspaces', 'sharedHistory')}
    publications, deltas, encoded, encoded_bytes = [], [], [], []
    original_publish, original_scan, original_dumps = app._publish, app.history.index.scan_changes, json.dumps

    def publish(**kwargs):
        publications.append(kwargs)
        return original_publish(**kwargs)

    def scan(**kwargs):
        token, delta = original_scan(**kwargs)
        deltas.append(copy.deepcopy(delta))
        return token, delta

    def dumps(value, *args, **kwargs):
        text = original_dumps(value, *args, **kwargs)
        if isinstance(value, dict) and value.get('present') is True:
            for key in prior:
                if value.get('value') is app._state[key]:
                    encoded.append(key)
                    encoded_bytes.append(len(text.encode()))
        return text

    monkeypatch.setattr(app, '_publish', publish)
    monkeypatch.setattr(app.history.index, 'scan_changes', scan)
    monkeypatch.setattr(json, 'dumps', dumps)
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), kind, 'transcript')}))
    with (paths[kind] / 'transcript.jsonl').open('a') as stream:
        stream.write(original_dumps({'role': 'assistant', 'content': 'New native progress'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert not deltas[0]['reset']
    assert [row['nativeIdentity'] for row in deltas[0]['sessions']] == [kind]
    assert deltas[0]['workspaces'] == []
    assert deltas[0]['removed'] == [] and deltas[0]['removedProjects'] == []
    print({'kind': kind, 'publications': len(publications), 'globalEncodes': encoded,
           'globalEncodedBytes': sum(encoded_bytes),
           'declaredGlobals': [sorted(row.get('global_keys', ())) for row in publications],
           'sharedChanges': {key: [prior['sharedHistory'].get(key), value]
                             for key, value in app.state['sharedHistory'].items()
                             if prior['sharedHistory'].get(key) != value}})
    assert len(publications) == 1
    assert publications[0]['session_ids'] == {owners[kind]['id']}
    assert publications[0]['global_keys'] == set()
    assert publications[0]['detail_only'] is True
    assert encoded == []
    assert all(app.state[key] == value for key, value in prior.items())
    saved = load_saved_state(app.db)
    assert all(saved[key] == value for key, value in prior.items())
    assert owners[kind]['nativeRevision'][1] == (paths[kind] / 'transcript.jsonl').stat().st_size
    assert app.runtime.started == [] and app.runtime.sent == []


async def test_new_native_worker_still_publishes_changed_counts_and_workspace(
        tmp_path, app_factory, monkeypatch):
    app, workspace, _, _ = await prepared(tmp_path, app_factory, monkeypatch)
    publications = []
    original = app._publish
    def publish(**kwargs):
        publications.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(app, '_publish', publish)
    native_session(workspace, 'second-worker', metadata={'parent_id': 'root'})
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project_slug(workspace), 'second-worker', 'metadata')}))
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert len(publications) == 1
    assert publications[0]['global_keys'] == {'workspaces', 'sharedHistory'}
    assert publications[0]['detail_only'] is False
    assert app.state['sharedHistory']['workerSessionCount'] == 2
    assert load_saved_state(app.db)['sharedHistory']['workerSessionCount'] == 2
    assert app.directory.resolve('second-worker')['sessionKind'] == 'worker'
    assert 'second-worker' not in {row['id'] for row in app._state['sessions']}


@pytest.mark.parametrize('with_worker', [False, True])
async def test_dormant_root_change_still_publishes_directory_and_cannot_be_worker_detail(
        tmp_path, app_factory, monkeypatch, with_worker):
    app, workspace, paths, owners = await prepared(tmp_path, app_factory, monkeypatch)
    dormant = native_session(workspace, 'dormant-root')
    project = project_slug(workspace)
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project, 'dormant-root', 'metadata')}))
    await app.history.refresh(force=False)
    await app.history.refresh(force=False)
    await close_maintenance(app)
    previous_epoch = app.directory.observation_revision
    saved_globals = {key: copy.deepcopy(app.state[key]) for key in ('sharedHistory', 'workspaces')}
    publications = []
    original = app._publish
    def publish(**kwargs):
        publications.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(app, '_publish', publish)
    metadata = json.loads((dormant / 'metadata.json').read_text())
    (dormant / 'metadata.json').write_text(json.dumps({**metadata, 'name': 'Changed dormant title'}))
    invalidations = {(project, 'dormant-root', 'metadata')}
    if with_worker:
        with (paths['worker'] / 'transcript.jsonl').open('a') as stream:
            stream.write(json.dumps({'role': 'assistant', 'content': 'Worker progress too'}) + '\n')
        invalidations.add((project, 'worker', 'transcript'))
    monkeypatch.setattr(app.history.index, '_invalidations', lambda: (True, invalidations))
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert len(publications) == 1
    assert publications[0]['session_ids'] == ({owners['worker']['id']} if with_worker else set())
    assert publications[0]['global_keys'] == set()
    assert publications[0]['detail_only'] is False
    assert app._publish_client_narrow is False
    assert not app.db.in_transaction
    assert app.directory.observation_revision != previous_epoch
    assert app.directory.resolve('dormant-root')['title'] == 'Changed dormant title'
    assert 'dormant-root' not in {row['id'] for row in app._state['sessions']}
    saved = load_saved_state(app.db)
    assert all(saved[key] == value for key, value in saved_globals.items())
    assert app.runtime.started == [] and app.runtime.sent == []


@pytest.mark.parametrize('kind', ['root', 'worker'])
async def test_live_progress_uses_existing_detail_guard_and_real_metadata_broadens(
        tmp_path, app_factory, monkeypatch, kind):
    app, workspace, paths, owners = await prepared(tmp_path, app_factory, monkeypatch)
    owner = owners[kind]
    # Mounted chats keep their settled navigation position during work.
    owner.update(historyManaged=False, status='working')
    app._publish_changes(sessions={owner['id']})
    await app.history.refresh()  # Reconcile the adopted runtime's title/source metadata.
    await close_maintenance(app)
    queues = []
    for client in ('viewing', 'home'):
        app.clients.attach(client)
        with app.clients.bind(client):
            if client == 'viewing':
                app.state['selectedSessionId'] = owner['id']
                app.state['view']['workSurface'] = 'chat'
            app.browser_state()
            queues.append(app.subscribe())
    for queue in queues:
        while not queue.empty():
            queue.get_nowait()
    attention_reads = []
    snapshot = directory_attention.snapshot
    def attention(*args, **kwargs):
        attention_reads.append(True)
        return snapshot(*args, **kwargs)
    monkeypatch.setattr(directory_attention, 'snapshot', attention)
    project = project_slug(workspace)
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project, kind, 'transcript')}))
    with (paths[kind] / 'transcript.jsonl').open('a') as stream:
        stream.write(json.dumps({'role': 'assistant', 'content': 'Live progress'}) + '\n')
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert not queues[0].empty()
    assert queues[1].empty(), 'Native progress must not rebuild an unrelated Home view'
    assert attention_reads == [], 'Unchanged attention must not be rescanned for progress'
    assert app._publish_client_narrow is True
    for queue in queues:
        while not queue.empty():
            queue.get_nowait()
    # Requesting detail scope cannot suppress a real title/attention change.
    metadata = json.loads((paths[kind] / 'metadata.json').read_text())
    (paths[kind] / 'metadata.json').write_text(json.dumps({**metadata, 'name': 'Renamed chat'}))
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project, kind, 'metadata')}))
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert owner['title'] == 'Renamed chat'
    assert app._publish_client_narrow is False
    assert app.directory.resolve(owner['id'])['title'] == 'Renamed chat'
    assert all(not queue.empty() for queue in queues)
    for queue in queues:
        while not queue.empty():
            queue.get_nowait()
    owner.update(status='error', error='Synthetic attention change')
    app._publish_changes(sessions={owner['id']}, detail_only=True)
    assert app._publish_client_narrow is False
    assert all(not queue.empty() for queue in queues)
    assert app.runtime.started == [] and app.runtime.sent == []
    for queue in queues:
        app.unsubscribe(queue)


async def test_workspace_placeholder_delta_retains_selection_pins_and_owner_references(
        tmp_path, app_factory, monkeypatch):
    app, workspace, _, owners = await prepared(tmp_path, app_factory, monkeypatch)
    project = project_slug(workspace)
    resolved = next(row for row in app.state['workspaces'] if row.get('nativeProject') == project)
    placeholder = {'id': 'old-placeholder', 'nativeProject': project, 'name': 'My alias',
                   'path': None, 'available': False}
    app.state['workspaces'].append(placeholder)
    app.state['selectedWorkspaceId'] = placeholder['id']
    app.state['pinnedWorkspaceIds'] = [placeholder['id']]
    app.state['pinnedItemIds'] = ['workspace:' + placeholder['id']]
    owners['root'].update(workspaceId=placeholder['id'], draft='Keep this draft')
    app._publish_changes(sessions={owners['root']['id']}, globals={
        'workspaces', 'selectedWorkspaceId', 'pinnedWorkspaceIds', 'pinnedItemIds'})
    publications = []
    original = app._publish
    def publish(**kwargs):
        publications.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(app, '_publish', publish)
    native_session(workspace, 'second-worker', metadata={'parent_id': 'root'})
    monkeypatch.setattr(app.history.index, '_invalidations',
                        lambda: (True, {(project, 'second-worker', 'metadata')}))
    await app.history.refresh(force=False)
    assert app.state['sharedHistory']['error'] is None
    assert len(publications) == 1
    assert owners['root']['id'] in publications[0]['session_ids']
    assert publications[0]['global_keys'] == {
        'workspaces', 'sharedHistory', 'selectedWorkspaceId', 'settings',
        'pinnedWorkspaceIds', 'pinnedItemIds'}
    saved = load_saved_state(app.db)
    assert saved['selectedWorkspaceId'] == resolved['id']
    assert saved['pinnedWorkspaceIds'] == [resolved['id']]
    assert saved['pinnedItemIds'] == ['workspace:' + resolved['id']]
    assert saved['settings']['workspace'] == str(workspace)
    assert placeholder['id'] not in {row['id'] for row in saved['workspaces']}
    saved_root = next(row for row in saved['sessions'] if row['id'] == owners['root']['id'])
    assert saved_root['workspaceId'] == resolved['id'] and saved_root['draft'] == 'Keep this draft'
