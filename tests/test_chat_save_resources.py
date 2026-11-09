"""Storage-only message references avoid rewriting history for progress changes."""
import copy
import json
import sqlite3

import pytest

from amplifier_web import cold_display, resource_files, state_records, storage_migration
from amplifier_web.host.storage import SessionStore
from amplifier_web.session_projection import view_path
from test_automatic_history import app_factory
from test_scoped_state_records import chats


def large_chat(app_factory):
    app, rows = chats(app_factory)
    row = rows[0]
    row['messages'] = [{'id': f'm-{i}', 'role': 'assistant' if i % 2 else 'user',
                        'via': 'text', 'createdAt': i, 'text': 'x' * 4000 + str(i)} for i in range(80)]
    row['status'] = 'working'
    app._publish()
    return app, rows


def committed(app, identity):
    pointer = next(row['$viewPayload'] for row in state_records.load(app.db)['sessions'] if row['id'] == identity)
    return cold_display.load(app.db, pointer)


def contents(app, identity):
    return cold_display.materialize(committed(app, identity), app.db)


def committed_restart_evidence(app):
    """Observe only durable restart roots, never resident bodies or client caches."""
    observer = sqlite3.connect(
        (app.data_dir / 'app.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        state = state_records.load(observer)
        payloads = {row['id']: cold_display.load(observer, row['$viewPayload'])
                    for row in state['sessions']}
        bodies = {identity: cold_display.materialize(payload, observer)
                  for identity, payload in payloads.items()}
        # No module-level persisted-draft loader exists; keep the raw JSON exact.
        clients = dict(observer.execute('SELECT id,value FROM client_views'))
        return {'state': state, 'payloads': payloads, 'bodies': bodies, 'clients': clients}
    finally:
        observer.close()  # A SQLite context manager alone does not close it.


async def close_without_checkpoint(app):
    before = committed_restart_evidence(app)  # Close the reader before any await.
    task = getattr(app, '_storage_maintenance_task', None)
    await app.publishing.close()
    # A real crash has no old writer; settle its worker without saving dirty state.
    await storage_migration.close_maintenance(app)
    assert task is None or task.done()
    assert getattr(app, '_storage_maintenance_task', None) is None
    assert not app.lock.locked()
    assert not app.db.in_transaction
    assert committed_restart_evidence(app) == before


def test_small_progress_writes_do_not_duplicate_large_message_body(app_factory, monkeypatch):
    app, rows = large_chat(app_factory)
    row = rows[0]
    messages = row['messages']
    expected = copy.deepcopy(messages)
    writes = []
    atomic = SessionStore._atomic
    def observe(path, text):
        writes.append(len(text.encode()))
        return atomic(path, text)
    monkeypatch.setattr(SessionStore, '_atomic', staticmethod(observe))
    row['streaming'] = 'small progress'
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert sum(writes) < 150_000
    assert row['messages'] is messages and row['messages'] == expected
    assert 'messages' not in dict.get(row, cold_display.MARKER, {})
    saved = committed(app, row['id'])
    assert saved['_coldMessageCount'] == len(messages)
    assert contents(app, row['id'])['messages'] == expected
    assert cold_display.notifications(cold_display.ColdRecord(saved, app.db)) == cold_display.notifications(row)


def test_committed_message_preparation_reused_during_advancing_execution(app_factory, monkeypatch):
    app, rows = large_chat(app_factory)
    row = rows[0]
    encoded = []
    original = resource_files.prepare
    def observe(value):
        if isinstance(value, list) and value and isinstance(value[0], dict) and 'text' in value[0]:
            encoded.append(True)
        return original(value)
    monkeypatch.setattr(resource_files, 'prepare', observe)
    for revision in range(3):
        row['execution'] = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
            {'id': 'receipt', 'kind': 'llm', 'sessionId': row['id'], 'revision': revision,
             'producerId': 'synthetic', 'usage': {'inputTokens': revision}}]}
        app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert encoded == [], 'Unchanged committed messages were canonically encoded again'
    assert contents(app, row['id'])['messages'] == row['messages']
    # The cache must still notice a borrowed equal-length nested edit.
    row['messages'][0]['text'] = 'y' + row['messages'][0]['text'][1:]
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert encoded == [True]
    assert contents(app, row['id'])['messages'] == row['messages']


def test_failed_message_save_does_not_advance_preparation(app_factory, monkeypatch):
    app, rows = large_chat(app_factory)
    row = rows[0]
    previous = app._message_preparation.entries[row['id']]
    row['messages'][0]['text'] = 'changed before failed commit'
    old = committed(app, row['id'])
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic save failure')
        patch.setattr(state_records, 'save_working', fail)
        with pytest.raises(RuntimeError, match='synthetic save failure'):
            app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert app._message_preparation.entries[row['id']] is previous
    assert committed(app, row['id']) == old
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert app._message_preparation.entries[row['id']] is not previous
    assert contents(app, row['id'])['messages'] == row['messages']


def test_cached_messages_still_validate_files_and_readopt_collected_blob(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    reference = committed(app, row['id'])['_coldFields']['messages']
    path = resource_files.root(app.db) / (reference['$resource'] + '.json')
    original = path.read_text()
    path.write_text('corrupt')
    with pytest.raises(ValueError, match='conflicts'):
        app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    path.unlink()
    app.db.execute('DELETE FROM state_resources WHERE id=?', (reference['$resource'],))
    app.db.commit()
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert path.read_text() == original
    assert contents(app, row['id'])['messages'] == row['messages']


@pytest.mark.parametrize('cold', [False, True])
def test_successful_inline_or_cold_save_retires_previous_preparation(app_factory, monkeypatch, cold):
    app, rows = large_chat(app_factory)
    row = rows[0]
    previous = app._message_preparation.entries[row['id']]
    if cold:
        # The actual retirement seam saves the cold pointer while the resident
        # field is still present; only after commit does it remove that field.
        row[cold_display.MARKER] = {'messages': committed(app, row['id'])['_coldFields']['messages']}
    else:
        row['messages'] = [{'id': 'short', 'role': 'user', 'text': 'short'}]
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic save failure')
        patch.setattr(state_records, 'save_working', fail)
        with pytest.raises(RuntimeError, match='synthetic save failure'):
            app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert app._message_preparation.entries[row['id']] is previous
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert row['id'] not in app._message_preparation.entries
    assert contents(app, row['id'])['messages'] == row['messages']


async def test_same_identity_equal_length_nested_edit_survives_unclean_restart(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    messages, nested = row['messages'], row['messages'][10]
    old_text = nested['text']
    old_ref = committed(app, row['id'])[cold_display.MARKER]['messages']
    nested['text'] = 'y' + old_text[1:]
    assert len(nested['text'].encode()) == len(old_text.encode())
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert row['messages'] is messages and row['messages'][10] is nested
    assert committed(app, row['id'])[cold_display.MARKER]['messages'] != old_ref
    expected = copy.deepcopy(messages)
    assert contents(app, row['id'])['messages'] == expected
    await close_without_checkpoint(app)  # Do not run close's checkpoint before reopening.
    restored = app_factory(home=app.data_dir)
    assert restored._session(row['id'])['messages'] == expected
    assert restored.clients.records['0']['drafts'][row['id']] == 'private-0'
    assert restored.clients.records['1']['drafts'][rows[1]['id']] == 'private-1'


@pytest.mark.parametrize('restore_view_fails', [False, True])
async def test_failed_save_keeps_old_payload_and_unclean_restart(app_factory, monkeypatch, restore_view_fails):
    app, rows = large_chat(app_factory)
    row = rows[0]
    expected = copy.deepcopy(row['messages'])
    prior = state_records.load(app.db)
    old_views = dict(app._view_cache)
    old_refs = copy.deepcopy(app._session_projection_refs)
    old_clients = dict(app.clients.saved)
    row['messages'][0]['text'] = 'changed before failure'
    with app.clients.bind('0'):
        app.clients.draft(row['id'], 'not committed yet')
    real_save = state_records.save
    atomic = SessionStore._atomic
    calls = 0
    def fail(*args, **kwargs):
        real_save(*args, **kwargs)
        raise sqlite3.OperationalError('after staging')
    def write(path, text):
        nonlocal calls
        if path.name == 'view.json':
            calls += 1
            if restore_view_fails and calls == 2:
                raise OSError('presentation restoration failure')
        return atomic(path, text)
    with monkeypatch.context() as m:
        m.setattr(state_records, 'save', fail)
        m.setattr(SessionStore, '_atomic', staticmethod(write))
        with pytest.raises(sqlite3.OperationalError):
            app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert state_records.load(app.db) == prior
    assert app._session_projection_refs == old_refs
    assert app.clients.saved == old_clients
    assert app._progress_dirty
    if not restore_view_fails:
        assert app._view_cache == old_views
    else:
        assert app._state['_viewRecoveryPending']
        assert resource_files.collect(app.db, app._state) == []
    assert contents(app, row['id'])['messages'] == expected
    assert app.clients.records['0']['drafts'][row['id']] == 'not committed yet'
    await close_without_checkpoint(app)
    assert app._progress_dirty
    assert app.clients.records['0']['drafts'][row['id']] == 'not committed yet'
    restored = app_factory(home=app.data_dir)
    assert restored._session(row['id'])['messages'] == expected
    assert restored.clients.records['0']['drafts'][row['id']] == 'private-0'


def test_gc_keeps_message_blob_until_last_committed_owner_changes(app_factory):
    app, rows = large_chat(app_factory)
    first, second = rows
    second['messages'] = copy.deepcopy(first['messages'])
    app._publish()
    shared = committed(app, first['id'])[cold_display.MARKER]['messages']['$resource']
    assert committed(app, second['id'])[cold_display.MARKER]['messages']['$resource'] == shared
    expected = copy.deepcopy(second['messages'])
    first['messages'][0]['text'] = 'A changed'
    app._publish(session_ids={first['id']}, detail_only=True, record_only=True)
    stale = resource_files.collect(app.db, app._state)
    assert shared not in stale
    app.db.commit()
    resource_files.remove_files(app.db, stale)
    assert contents(app, second['id'])['messages'] == expected
    second['messages'][0]['text'] = 'B changed'
    app._publish(session_ids={second['id']}, detail_only=True, record_only=True)
    stale = resource_files.collect(app.db, app._state)
    assert shared in stale
    app.db.commit()
    resource_files.remove_files(app.db, stale)
    assert contents(app, first['id'])['messages'][0]['text'] == 'A changed'
    assert contents(app, second['id'])['messages'][0]['text'] == 'B changed'


def test_existing_cold_manifest_and_nested_nonmessage_mutations_preserved(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    expected_messages = copy.deepcopy(row['messages'])
    reference = resource_files.put(app.db, expected_messages)
    row['_coldFields'] = {'messages': reference}
    dict.pop(row, 'messages')
    row['_coldMessageCount'] = len(expected_messages)
    row['execution'] = {'nodes': [{'id':'tool','kind':'tool','output':{'value':'before'}}], 'turns': []}
    row['configuration'] = {'custom':{'value':'before'}}
    app._publish()
    row['execution']['nodes'][0]['output']['value'] = 'after!'
    row['configuration']['custom']['value'] = 'after!'
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    saved = committed(app, row['id'])
    assert saved['_coldFields']['messages'] == reference
    assert 'messages' not in dict.keys(row)
    full = cold_display.materialize(saved, app.db)
    assert full['messages'] == expected_messages
    assert full['execution']['nodes'][0]['output']['value'] == 'after!'
    assert full['configuration']['custom']['value'] == 'after!'


async def test_command_receipt_replay_stays_exact_with_large_messages(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    with app.clients.bind('0'):
        result = await app.dispatch('session.pin', {'id':row['id'], 'pinned':True}, command_id='pin-once', include_state=False)
        revision = app._state['revision']
        duplicate = await app.dispatch('session.pin', {'id':row['id'], 'pinned':True}, command_id='pin-once', include_state=False)
    assert result['accepted'] and duplicate['duplicate']
    assert app._state['revision'] == revision
    assert contents(app, row['id'])['messages'] == row['messages']
    assert app.db.execute('SELECT count(*) FROM commands WHERE id=?', ('pin-once',)).fetchone()[0] == 1


@pytest.mark.parametrize('damage', ['missing', 'valid_json_corruption'])
def test_resource_damage_refuses_exact_read_without_losing_reference(app_factory, damage):
    app, rows = large_chat(app_factory)
    saved = committed(app, rows[0]['id'])
    reference = saved['_coldFields']['messages']
    path = resource_files.root(app.db) / (reference['$resource'] + '.json')
    original = path.read_bytes()
    try:
        if damage == 'missing':
            path.unlink()
            error = FileNotFoundError
        else:
            path.write_text('[]')
            error = ValueError
        reader = cold_display.ColdRecord(saved, app.db)
        with pytest.raises(error):
            reader['messages']
        assert dict.get(reader, '_coldFields')['messages'] == reference
        assert 'messages' not in dict.keys(reader)
    finally:
        path.write_bytes(original)


def test_shrunk_messages_override_old_cold_summary_and_reference(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    old = committed(app, row['id'])['_coldFields']['messages']['$resource']
    row['messages'][:] = [{'id':'short', 'role':'assistant', 'via':'text', 'text':'small', 'createdAt':999}]
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    saved = committed(app, row['id'])
    reader = cold_display.ColdRecord(saved, app.db)
    assert saved['messages'] == row['messages']
    assert 'messages' not in saved.get('_coldFields', {})
    assert cold_display.message_count(reader) == 1
    assert cold_display.notifications(reader) == cold_display.notifications(row)
    assert old in resource_files.collect(app.db, app._state)
    app.db.rollback()


async def test_managed_deletion_removes_unique_large_body_and_retains_shared(app_factory):
    from test_live_clients import command
    app = app_factory()
    app.clients.attach('web')
    ids = []
    for _ in range(2):
        result = await command(app, 'web', 'session.create', {'location':{'kind':'managed'}})
        ids.append(result['sessionId'])
    first, second = (app._session(identity) for identity in ids)
    first['messages'] = [{'id':'same', 'role':'user', 'text':'x'*40000}]
    second['messages'] = copy.deepcopy(first['messages'])
    app._publish()
    shared = committed(app, ids[0])['_coldFields']['messages']['$resource']
    assert committed(app, ids[1])['_coldFields']['messages']['$resource'] == shared
    first['messages'][0]['text'] = 'y'*40000
    app._publish()
    unique = committed(app, ids[0])['_coldFields']['messages']['$resource']
    preview = (await command(app, 'web', 'session.deletePreview', {'id':ids[0]}))['result']
    result = await command(app, 'web', 'session.delete', {'id':ids[0], 'confirmationToken':preview['confirmationToken']})
    assert result['result']['deleted'] and not result['result']['cleanupPending']
    root = resource_files.root(app.db)
    assert not (root/(unique+'.json')).exists()
    assert (root/(shared+'.json')).exists()
    assert contents(app, ids[1])['messages'][0]['text'] == 'x'*40000


async def test_restart_shrink_and_json_export_do_not_keep_stale_notification_text(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    sentinel = 'REMOVED-ASSISTANT-SENTINEL'
    row['messages'][1]['text'] = sentinel + row['messages'][1]['text']
    app._publish()
    await close_without_checkpoint(app)
    restored = app_factory(home=app.data_dir)
    current = restored._session(row['id'])
    # A borrowed list can be edited in place after hydration.
    current['messages'][:] = [{'id':'short','role':'user','text':'remaining'}]
    restored._publish(session_ids={current['id']}, detail_only=True, record_only=True)
    saved = committed(restored, current['id'])
    assert sentinel not in json.dumps(saved)
    result = await restored.dispatch('session.export', {'id':current['id']}, include_state=False)
    exported = next(effect['content'] for effect in result['effects'] if effect['type']=='download')
    assert sentinel not in exported
    assert json.loads(exported)['messages'] == current['messages']


async def test_large_accounting_resource_reuses_content_but_keeps_nested_edits(app_factory, monkeypatch):
    from amplifier_web.session_projection import stored_execution
    app, rows = large_chat(app_factory)
    row = rows[0]
    row['execution'] = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
        {'id':f'call-{i}', 'kind':'llm', 'sessionId':row['id'], 'rootSessionId':row['id'],
         'producerId': 'producer-'+'x'*80, 'revision':1, 'phase':'completed',
         'usage':{'inputTokens':100,'outputTokens':20,'totalTokens':120}}
        for i in range(400)]}
    app._publish()
    original = committed(app, row['id'])['_coldFields']['execution']
    writes = []
    atomic = SessionStore._atomic
    def observe(path, text):
        writes.append(len(text.encode()))
        return atomic(path, text)
    with monkeypatch.context() as m:
        m.setattr(SessionStore, '_atomic', staticmethod(observe))
        row['streaming'] = 'small update'
        app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert committed(app, row['id'])['_coldFields']['execution'] == original
    assert sum(writes) < 150_000
    nested = row['execution']['retiredUsageNodes'][10]['usage']
    nested['inputTokens'] = 101
    nested['totalTokens'] = 121
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert committed(app, row['id'])['_coldFields']['execution'] != original
    expected = stored_execution(row['execution'])
    assert contents(app, row['id'])['execution'] == expected
    await close_without_checkpoint(app)
    restored = app_factory(home=app.data_dir)
    # Startup rebuilds the visible-node aggregate; retired accounting stays exact.
    from amplifier_web.execution import rollup
    assert stored_execution(restored._session(row['id'])['execution']) == {
        **expected, 'aggregateUsage': rollup([])}


async def test_large_pending_accounting_restart_and_canonical_refresh_preserve_authority(app_factory):
    from amplifier_web.capacity import usage_snapshot
    from amplifier_web.event_log_view import EventLogView
    from test_accounting_canonical import receipt, observed_tree, populated_logs
    app, rows = large_chat(app_factory)
    row = rows[0]
    sid = row['id']
    tree = observed_tree(sid)
    tree['retiredUsageNodes'] = [receipt(sid, f'older-{i}') for i in range(130)]
    tree['retiredUsageNodes'].extend([
        receipt(sid, 'pending-call', phase='running', endedAt=None, usage={}),
        receipt(sid, 'unknown-call', phase='outcome_unknown', endedAt=None, usage={}),
    ])
    row['execution'] = tree
    paths = populated_logs(row)
    canonical_bytes = {path: path.read_bytes() for path in paths}
    app._publish()
    assert committed(app, sid)['_coldFields']['execution']['pendingObservation'] is True
    before = usage_snapshot(row)
    await close_without_checkpoint(app)
    restored = app_factory(home=app.data_dir)
    current = restored._session(sid)
    by_id = {entry['id']: entry for entry in usage_snapshot(current)['receipts']}
    assert by_id['pending-call']['phase'] == 'outcome_unknown'
    assert by_id['unknown-call']['phase'] == 'outcome_unknown'
    assert len(by_id) == before['calls'] == 134
    await EventLogView(restored).refresh(sid)
    after = usage_snapshot(current)
    assert after['calls'] == before['calls']
    for key, metric in before['metrics'].items():
        assert after['metrics'][key] == {**metric, 'pendingCalls':0,
                                         'unknownCalls':metric['unknownCalls'] + metric['pendingCalls']}
    assert {entry['id'] for entry in after['receipts']} == {entry['id'] for entry in before['receipts']}
    child = next(entry for entry in after['receipts'] if entry['id'] == 'child-call')
    assert child['sessionId'] == 'child' and child['producerId'] == 'producer'
    assert next(entry for entry in after['receipts'] if entry['id'] == 'pending-call')['phase'] == 'outcome_unknown'
    assert all(path.read_bytes() == original for path, original in canonical_bytes.items())


async def test_large_payload_authenticated_http_replay_and_restart(authenticated_client, tmp_path):
    from amplifier_web.server import create_app
    from test_service import Runtime
    from test_live_clients import read_event
    runtime = Runtime()
    app = await create_app(tmp_path/'http-app', workspace=tmp_path, runtime=runtime,
                           voice=False, background_updates=False, preload_providers=False)
    client = await authenticated_client(app)
    service = app['service']
    rows = []
    for number in range(2):
        await service.dispatch('session.create', {'title':str(number)})
        row = service._session()
        row['messages'] = [{'id':f'm-{i}', 'role':'user', 'text':f'exact-{i}-'+'x'*1000} for i in range(40)]
        rows.append(row)
    service._publish()
    for number, row in enumerate(rows):
        identity = f'http-{number}'
        response = await client.post('/api/clients/attach', json={'clientId':identity,'kind':'tui'})
        assert response.status == 200
        with service.clients.bind(identity):
            service.clients.draft(row['id'], f'private-{number}')
    service._publish()
    sid = rows[0]['id']
    endpoint = f'/api/sessions/{sid}'
    payload = {'id':'http-once','action':'session.rename','args':{'id':sid,'title':'Named once'}}
    headers = {'X-Amplifier-Client':'http-0'}
    first = await client.post(endpoint+'/commands', json=payload, headers=headers)
    assert first.status == 200, await first.text()
    duplicate = await client.post(endpoint+'/commands', json=payload, headers=headers)
    assert (await duplicate.json())['duplicate']
    await service.on_runtime_event('assistant.delta', {'sessionId':sid,'text':'new progress'})
    await service._flush_pending_progress()
    stream = await client.get(endpoint+'/events?clientId=http-0')
    state = await read_event(stream)
    stream.close()
    assert state['session']['streaming'] == 'new progress'
    assert state['session']['messages'] == rows[0]['messages']
    expected = copy.deepcopy(rows[0]['messages'])
    await client.close()
    restarted = await create_app(tmp_path/'http-app', workspace=tmp_path, runtime=Runtime(),
                                 voice=False, background_updates=False, preload_providers=False)
    second_client = await authenticated_client(restarted)
    again = await second_client.post(endpoint+'/commands', json=payload, headers=headers)
    assert (await again.json())['duplicate']
    stream = await second_client.get(endpoint+'/events?clientId=http-0')
    recovered = await read_event(stream)
    stream.close()
    assert recovered['session']['messages'][:-1] == expected
    interrupted = recovered['session']['messages'][-1]
    assert interrupted['role'] == 'assistant'
    assert interrupted['text'] == '[Interrupted response]\n\nnew progress'
    assert recovered['session']['draft'] == 'private-0'
    assert restarted['service'].clients.records['http-1']['drafts'][rows[1]['id']] == 'private-1'
    assert not runtime.sent and not restarted['service'].runtime.sent


def test_saved_message_reference_keeps_bounded_body_free_media_hints(app_factory):
    app, rows = large_chat(app_factory)
    row = rows[0]
    messages = row['messages']
    messages.extend({'id': f'anchored-{i}', 'role': 'assistant', 'nativeIndex': i,
                     'text': 'Bounded hint does not include this text'} for i in range(256))
    app._save(session_ids={row['id']}, record_only=True)
    saved = committed(app, row['id'])
    reference = saved[cold_display.MARKER]['messages']
    hint = reference['mediaBindings']
    assert len(hint['bindings']) == 128
    assert hint['resource'] == reference['$resource']
    assert hint['scope'] == (row.get('nativeIdentity') or row.get('runtimeSessionId') or row['id'])
    assert all('text' not in binding for binding in hint['bindings'])
    assert row['messages'] is messages and contents(app, row['id'])['messages'] == messages


def test_execution_preparation_cannot_acknowledge_failed_save_or_skip_blob_validation(app_factory, monkeypatch):
    from amplifier_web.session_projection import stored_execution
    app, rows = large_chat(app_factory)
    row = rows[0]
    row['execution'] = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
        {'id': str(i), 'kind': 'llm', 'sessionId': row['id'], 'revision': 1,
         'phase': 'completed', 'usage': {'inputTokens': i},
         'assignmentReservation': {'tokens': 100}}
        for i in range(130)]}
    app._publish()
    previous = contents(app, row['id'])['execution']
    row['execution']['retiredUsageNodes'][10]['usage']['inputTokens'] = 999
    with monkeypatch.context() as patch:
        def fail(*a, **kw):
            raise sqlite3.OperationalError('staging failed')
        patch.setattr(state_records, 'save_working', fail)
        with pytest.raises(sqlite3.OperationalError, match='staging failed'):
            app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert contents(app, row['id'])['execution'] == previous
    assert app._progress_dirty
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert contents(app, row['id'])['execution'] == stored_execution(row['execution'])
    reference = committed(app, row['id'])['_coldFields']['execution']
    path = resource_files.root(app.db) / (reference['$resource'] + '.json')
    correct = path.read_text()
    path.write_text('{}')
    with pytest.raises(ValueError, match='conflicts'):
        app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    path.unlink()
    app.db.execute('DELETE FROM state_resources WHERE id=?', (reference['$resource'],))
    app.db.commit()
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert path.read_text() == correct
    assert contents(app, row['id'])['execution'] == stored_execution(row['execution'])


def test_execution_cold_commit_releases_both_preparations(app_factory, monkeypatch):
    app, rows = large_chat(app_factory)
    row = rows[0]
    row['execution'] = {'nodes': [], 'turns': [], 'retiredUsageNodes': [
        {'id': str(i), 'kind': 'llm', 'sessionId': row['id'], 'revision': 1,
         'usage': {'inputTokens': i}} for i in range(130)]}
    # Exercise the detached fallback cache as well as encoded fragments; fully
    # canonical accounting can now be encoded directly without that cache.
    row['execution']['retiredUsageNodes'][0]['notAccounting'] = 'must be filtered'
    app._publish()
    cache = app._execution_preparation
    assert row['id'] in cache.accounting
    assert any(key[0] == row['id'] for key in cache.entries)
    row[cold_display.MARKER] = {'execution': committed(app, row['id'])['_coldFields']['execution']}
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic cooling failure')
        patch.setattr(state_records, 'save_working', fail)
        with pytest.raises(RuntimeError, match='synthetic cooling failure'):
            app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert row['id'] in cache.accounting
    app._publish(session_ids={row['id']}, detail_only=True, record_only=True)
    assert row['id'] not in cache.accounting
    assert not any(key[0] == row['id'] for key in cache.entries)
