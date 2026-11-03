"""Standing choices follow retained task sources, never copied Full flags."""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.access_mode import read_mode
from amplifier_web.host.config import read_config
from amplifier_web.host.session import _apply_host_policy
from amplifier_web.resource_files import detach, read_detached
from amplifier_web.service import AppError, AppService
from test_access_mode import save
from test_configuration_authority import app
from test_coordination_generation import agent, app as collaboration_app, until
from test_schedule_new_tasks import create as create_schedule, fixture as schedule_fixture


def link(app, target, source, kind='commissioned'):
    if kind == 'commissioned':
        target['collaboration'] = {'creatorSessionId': source['id'], 'requestId': 'create-' + target['id']}
    else:
        target['scheduledOrigin'] = {'sessionId': source['id'], 'scheduleId': 'schedule', 'runId': 'run-' + target['id']}
    app._publish_changes(sessions={target['id']})


def both_modes(app, target):
    return (app.access_mode.snapshot(target)['mode'],
            read_mode(app.data_dir, target['workspace'], target.get('runtimeSessionId') or target['id']))


@pytest.mark.parametrize('kind', ['commissioned', 'scheduled'])
async def test_source_full_revocation_and_file_policy_use_one_resolver(app, kind):
    source, target = app.state['sessions']
    await save(app, source)
    link(app, target, source, kind)
    assert both_modes(app, target) == ('full', 'full')
    assert app.access_mode.snapshot(target)['source'] == 'inherited'
    assert not app.configuration_authority.rows(target['id'])
    bundle = SimpleNamespace(tools=[{'module': 'tool-filesystem', 'config': {
        'allowed_write_paths': [], 'denied_write_paths': [source['workspace']]}}], agents={})
    config = read_config(target['workspace'], home=app.data_dir, session_id=target['id'])
    _apply_host_policy(bundle, config)
    assert bundle.tools[0]['config']['denied_write_paths'] == []
    revision = app.access_mode.snapshot(target)['revision']
    await save(app, source, mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    assert not app.access_mode.full(target, revision)
    config = read_config(target['workspace'], home=app.data_dir, session_id=target['id'])
    _apply_host_policy(bundle, config)
    assert bundle.tools[0]['config']['allowed_write_paths'] == []
    assert bundle.tools[0]['config']['denied_write_paths'] == [source['workspace']]


async def test_specific_source_choice_precedes_defaults_but_target_choice_is_independent(app):
    source, target = app.state['sessions']
    await save(app, scope='workspace', mode='ask')
    await save(app, source)
    link(app, target, source)
    assert both_modes(app, target) == ('full', 'full')
    await save(app, target, mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target, mode='full')
    independent = app.access_mode.snapshot(target)['revision']
    await save(app, source, mode='ask')
    assert both_modes(app, target) == ('full', 'full')
    assert app.access_mode.full(target, independent)
    await save(app, target, mode='inherit')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_app_full_does_not_override_source_or_receiving_ask(app):
    source, target = app.state['sessions']
    await save(app, scope='app')
    await save(app, source, mode='ask')
    link(app, target, source)
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, source, mode='inherit')
    await save(app, target, scope='workspace', mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_destination_workspace_choice_precedes_cross_workspace_source(app, tmp_path):
    source, target = app.state['sessions']
    workspace = tmp_path / 'destination'
    workspace.mkdir()
    target['workspace'] = str(workspace)
    target['workingDirectory'] = str(workspace)
    link(app, target, source, 'scheduled')
    await save(app, source)
    assert both_modes(app, target) == ('full', 'full')
    await save(app, target, scope='workspace', mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target, scope='workspace', mode='full')
    independent = app.access_mode.snapshot(target)['revision']
    await save(app, source, mode='ask')
    assert app.access_mode.full(target, independent)
    await save(app, target, scope='workspace', mode='inherit')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_source_stop_fences_captured_effect_without_changing_saved_mode(app):
    source, target = app.state['sessions']
    await save(app, source)
    link(app, target, source)
    authority = {'accessRevision': app.access_mode.snapshot(target)['revision'],
        'sessionId': target['id'], 'workspace': target['workspace'], 'interruptionRevision': 0,
        'effect': 'runtime_configuration', 'scope': 'session'}
    app.configuration_authority.check(authority)
    source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app._publish_changes(sessions={source['id']})
    assert both_modes(app, target) == ('full', 'full')
    with pytest.raises(AppError, match='Full access changed'):
        app.configuration_authority.check(authority)


@pytest.mark.parametrize('kind', ['missing', 'cyclic', 'ambiguous'])
async def test_unavailable_source_preserves_independent_workspace_and_app_choice(app, kind):
    source, target = app.state['sessions']
    link(app, target, {'id': 'missing-source'} if kind == 'missing' else source)
    if kind == 'cyclic':
        link(app, source, target)
    elif kind == 'ambiguous':
        link(app, target, source, 'scheduled')
    assert both_modes(app, target) == ('ask', 'ask')
    assert app.access_mode.snapshot(target)['source'] == 'unavailable'
    await save(app, scope='app')
    assert both_modes(app, target) == ('full', 'full')
    assert app.access_mode.snapshot(target)['source'] == 'unavailable'
    revision = app.access_mode.snapshot(target)['revision']
    target['collaboration']['requestId'] = 'changed-source-relationship'
    app._publish_changes(sessions={target['id']})
    assert not app.access_mode.full(target, revision)
    await save(app, target, scope='workspace', mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target, scope='workspace', mode='full')
    assert both_modes(app, target) == ('full', 'full')
    await save(app, target, mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')
    await save(app, target)
    assert both_modes(app, target) == ('full', 'full')


async def test_native_alias_locator_reads_canonical_source_not_directory_projection(app):
    source, target = app.state['sessions']
    await save(app, source)
    target['runtimeSessionId'] = 'target-native'
    link(app, target, source, 'scheduled')
    assert both_modes(app, target) == ('full', 'full')
    # scheduledOrigin deliberately is not in the history directory summary.
    summary = json.loads(app.db.execute('SELECT summary FROM history_local_rows WHERE id=?', (target['id'],)).fetchone()[0])
    assert 'scheduledOrigin' not in summary
    app.db.execute('UPDATE history_local_rows SET summary=? WHERE id=?', ('{}', target['id']))
    app.db.commit()
    assert both_modes(app, target) == ('full', 'full')


async def test_committed_payload_wins_over_mutable_view_without_loading_cold_history(app, monkeypatch):
    from amplifier_web import resource_files
    from amplifier_web.session_projection import view_path
    source, target = app.state['sessions']
    await save(app, source)
    source['messages'] = [{'id': 'retained-history', 'role': 'assistant', 'text': 'History body. ' * 2000}]
    app._publish_changes(sessions={source['id']})
    link(app, target, source)
    def payload(session):
        row = app.db.execute("SELECT value FROM state_records WHERE kind='session' AND id=?", (session['id'],)).fetchone()
        manifest = json.loads(row[0])
        assert 'collaboration' not in manifest
        return read_detached(detach(app.db, manifest['$viewPayload']['$resource']))
    cold_messages = payload(source)['_coldFields']['messages']['$resource']
    uncommitted = payload(target)
    uncommitted['collaboration']['creatorSessionId'] = 'uncommitted-source'
    uncommitted['nativeIdentity'] = 'uncommitted-native'
    path = view_path(app.data_dir, target)
    path.write_text(json.dumps(uncommitted))
    reads = []
    original = resource_files.read_detached
    def read(source, **kwargs):
        reads.append(source.identity)
        assert source.identity != cold_messages
        return original(source, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(resource_files, 'read_detached', read)
        assert both_modes(app, target) == ('full', 'full')
    assert reads and cold_messages not in reads
    assert json.loads(path.read_text())['collaboration']['creatorSessionId'] == 'uncommitted-source'


@pytest.mark.parametrize('fault', ['missing_file', 'corrupt_file', 'missing_index'])
@pytest.mark.parametrize('mode', ['ask', 'full'])
async def test_unavailable_source_payload_preserves_independent_policy(app, fault, mode):
    from amplifier_web.resource_files import root
    source, target = app.state['sessions']
    if mode == 'full':
        await save(app, scope='app')
    link(app, target, source)
    before = app.access_mode.snapshot(target)['revision']
    manifest = json.loads(app.db.execute("SELECT value FROM state_records WHERE kind='session' AND id=?", (source['id'],)).fetchone()[0])
    identity = manifest['$viewPayload']['$resource']
    index = app.db.execute('SELECT value FROM state_resources WHERE id=?', (identity,)).fetchone()[0]
    path = root(app.db) / (identity + '.json')
    original = path.read_bytes()
    try:
        if fault == 'missing_file':
            path.unlink()
        elif fault == 'corrupt_file':
            path.write_bytes(original[:-1] + b'!')
        else:
            app.db.execute('DELETE FROM state_resources WHERE id=?', (identity,))
            app.db.commit()
        assert both_modes(app, target) == (mode, mode)
        report = app.access_mode.snapshot(target)
        assert report['source'] == 'unavailable'
        assert report['revision'] != before
        assert both_modes(app, source) == (mode, mode)
    finally:
        path.write_bytes(original)
        app.db.execute('INSERT OR REPLACE INTO state_resources VALUES (?,?)', (identity, index))
        app.db.commit()


async def test_current_worker_alias_uses_verified_host_owner_for_file_policy(app):
    source, target = app.state['sessions']
    await save(app, source)
    target.update(nativeIdentity='older-native-alias', runtimeSessionId='current-native-root')
    link(app, target, source)
    assert app.access_mode.full(target)
    config = read_config(target['workspace'], home=app.data_dir, session_id='current-native-root',
        owner_session_id=target['id'])
    assert config.access_mode == 'full'
    assert read_mode(app.data_dir, target['workspace'], 'unrelated-native', owner_session_id=target['id']) == 'ask'
    await save(app, source, mode='ask')
    assert read_config(target['workspace'], home=app.data_dir, session_id='current-native-root',
        owner_session_id=target['id']).access_mode == 'ask'


async def test_inherited_mode_controls_actual_native_file_mount_after_revocation(app, tmp_path):
    filesystem = pytest.importorskip('amplifier_module_tool_filesystem')
    pytest.importorskip('amplifier_module_loop_live')
    pytest.importorskip('amplifier_module_context_simple')
    from amplifier_core import AmplifierSession
    source, target = app.state['sessions']
    target.update(nativeIdentity='older-native-alias', runtimeSessionId='current-native-root')
    link(app, target, source)
    external = tmp_path.parent / (tmp_path.name + '-external')
    external.mkdir()
    bundle = SimpleNamespace(tools=[{'module': 'tool-filesystem'}], agents={})
    async def write(name):
        config = read_config(target['workspace'], home=app.data_dir, session_id=target['runtimeSessionId'],
            owner_session_id=target['id'])
        _apply_host_policy(bundle, config)
        native = AmplifierSession({'session': {'orchestrator': {'module': 'loop-live',
            'config': {'configured_bundle': True, 'native_provider': True}},
            'context': {'module': 'context-simple'}}, 'providers': []})
        try:
            await native.initialize()
            native.coordinator.register_capability('session.working_dir', target['workspace'])
            await filesystem.mount(native.coordinator, bundle.tools[0]['config'])
            return await native.coordinator.get('tools')['write_file'].execute({
                'file_path': str(external / name), 'content': name})
        finally:
            await native.cleanup()
    assert not (await write('before.txt')).success
    await save(app, source)
    assert (await write('full.txt')).success
    await save(app, source, mode='ask')
    assert not (await write('after.txt')).success
    assert (external / 'full.txt').read_text() == 'full.txt'
    assert not (external / 'before.txt').exists() and not (external / 'after.txt').exists()


async def test_nested_sources_have_no_human_checkpoint_and_ignore_unrelated_history(app):
    source = app.state['sessions'][0]
    await save(app, source)
    target = source
    for index in range(20):
        child = app._new_session({'title': 'Task ' + str(index)})
        app.state['sessions'].append(child)
        link(app, child, target, 'scheduled' if index % 2 else 'commissioned')
        target = child
    assert both_modes(app, target) == ('full', 'full')
    unrelated = [('session', 'unrelated-' + str(index), 'deliberately-not-json') for index in range(512)]
    def traced_read():
        statements = []
        app.db.set_trace_callback(statements.append)
        try:
            assert both_modes(app, target) == ('full', 'full')
        finally:
            app.db.set_trace_callback(None)
        return statements
    baseline = traced_read()
    try:
        # A broad source scan/JSON extraction would fail on these unrelated
        # bodies. Exact canonical lookups must not visit them at all.
        app.db.executemany('INSERT INTO state_records(kind,id,value) VALUES(?,?,?)', unrelated)
        app.db.commit()
        before = app.db.total_changes
        assert traced_read() == baseline
        assert app.db.total_changes == before
    finally:
        app.db.executemany('DELETE FROM state_records WHERE kind=? AND id=?',
            [(kind, identity) for kind, identity, _ in unrelated])
        app.db.commit()
    await save(app, source, mode='ask')
    assert both_modes(app, target) == ('ask', 'ask')


async def test_real_commission_from_peer_origin_inherits_without_grant(collaboration_app):
    app = collaboration_app
    source = app.state['sessions'][0]
    await save(app, source)
    receipt = await agent(app, source, 'coordination.create',
        {'title': 'Inherited task', 'text': 'Continue the delegated work'}, 'full-create')
    await until(lambda: len(app.runtime.inputs) == 1)
    target = app._session(receipt['sessionId'])
    assert target['collaboration']['creatorSessionId'] == source['id']
    assert both_modes(app, target) == ('full', 'full')
    assert not app.configuration_authority.rows(target['id'])
    assert not source['messages']  # No fresh human message was fabricated.


async def test_scheduled_root_inherits_after_host_restart_without_source_residency(tmp_path, monkeypatch):
    app, runtime, now, sid = await schedule_fixture(tmp_path, monkeypatch)
    target_id = None
    try:
        await save(app, app._session(sid))
        record, _ = await create_schedule(app, sid, now[0])
        first_mount_modes = []
        first_mount_origins = []
        original_control = AsyncMock(wraps=runtime.control)
        async def control(identity, operation, args, *, guard=None):
            if identity != sid and identity not in runtime.workers:
                first_mount_modes.append(read_mode(app.data_dir, app._session(identity)['workspace'], identity))
                saved = app.db.execute("SELECT value FROM state_records WHERE kind='session' AND id=?", (identity,)).fetchone()
                manifest = json.loads(saved[0])
                payload = read_detached(detach(app.db, manifest['$viewPayload']['$resource']))
                first_mount_origins.append(payload.get('scheduledOrigin'))
            return await original_control(identity, operation, args, guard=guard)
        runtime.control = control
        now[0] += 61
        await app.schedules.tick()
        await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'accepted', run.get('detail')
        target_id = run['destinationSessionId']
        assert first_mount_modes == ['full']
        assert first_mount_origins == [{'sessionId': sid, 'scheduleId': record['id'], 'runId': run['id']}]
        assert both_modes(app, app._session(target_id)) == ('full', 'full')
        # The observer must forward the exact guard, not merely accept or run it.
        original_control.reset_mock()
        rejection = ValueError('Synthetic admission rejection')
        checks = []
        def reject():
            checks.append('checked')
            raise rejection
        with pytest.raises(ValueError) as caught:
            await runtime.control(target_id, 'task.get', {}, guard=reject)
        assert caught.value is rejection and checks == ['checked']
        original_control.assert_awaited_once_with(target_id, 'task.get', {}, guard=reject)
        assert first_mount_modes == ['full']  # The negative probe did not remount.
    finally:
        await app.close()
    restored = AppService(tmp_path / 'app', None, workspace=tmp_path)
    try:
        # Reads use exact canonical source records even if the source is cold.
        restored.state['sessions'] = [row for row in restored.state['sessions'] if row['id'] != sid]
        target = restored._session(target_id, hydrate=False)
        assert both_modes(restored, target) == ('full', 'full')
        assert sid not in {row['id'] for row in restored.state['sessions']}
        assert not restored.configuration_authority.rows(target_id)
    finally:
        await restored.close()


@pytest.mark.parametrize('mismatch', ['source', 'run', 'destination'])
async def test_scheduled_origin_requires_the_reserved_source_run_and_destination(tmp_path, monkeypatch, mismatch):
    app, runtime, now, sid = await schedule_fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create_schedule(app, sid, now[0])
        now[0] += 61
        assert app.schedules.store.acquire(now[0])
        run = app.schedules.store.claim(sid, record['id'], now[0])
        destination = str(uuid.uuid4())
        app.schedules.store.transition(sid, run['id'], ['claimed'], 'creating', now[0], destinationSessionId=destination)
        identity = str(uuid.uuid4()) if mismatch == 'destination' else destination
        source_id = str(uuid.uuid4()) if mismatch == 'source' else sid
        run_id = str(uuid.uuid4()) if mismatch == 'run' else run['id']
        with pytest.raises(AppError, match='does not belong|matching new-task reservation'):
            await app.dispatch('session.create', {'id': identity, 'select': False,
                'workspace': record['newTaskConfiguration']['workspace'],
                'bundle': record['newTaskConfiguration']['bundle'],
                'inheritConfiguration': {'sessionId': source_id, 'scheduledRunId': run_id,
                    'configurationHash': record['newTaskConfiguration']['configurationHash']}},
                origin='scheduler', command_id='invalid-scheduled-source')
        assert {row['id'] for row in app.state['sessions']} == {sid}
        assert not runtime.workers and not runtime.inputs
    finally:
        await app.close()
