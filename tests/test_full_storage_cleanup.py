"""Full cleanup through real dispatch, native children and qualified code owners."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web import storage_cleanup as cleanup, update_retention as retention
from amplifier_web.errors import AppError
from test_full_child_configuration import app, mounted, dispatch as native_dispatch
from test_full_feedback import call, mode
from test_update_retention import manager_fixture
from test_dependency_layers import prepared, source as dependency_source, settle as release_layer


@pytest.fixture
def storage(app, tmp_path, monkeypatch):
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools = SmartToolsManager(app)
    manager, names, project = manager_fixture(tmp_path / 'owned-code', monkeypatch)
    manager.service = app
    manager.wakeup = asyncio.Event()
    manager.notify_idle = lambda: None
    manager.configuration_changed = lambda: False
    manager.close = AsyncMock()
    app.update_manager = manager
    return SimpleNamespace(app=app, target=manager.directory / 'releases' / names[3],
                           names=names, project=project)


def values(revision=0):
    return {'expectedRevision': revision, 'enabled': True, 'keepDays': 0, 'maxUnusedBytes': 0}


def saved(app, identity):
    row = app.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


async def change(app, source, boundary):
    if boundary == 'stop':
        await app.dispatch('conversation.stop', {'sessionId': source['id']})
    elif boundary in {'revoke', 'off-on'}:
        await mode(app, source, 'ask')
        if boundary == 'off-on':
            await mode(app, source)
    elif boundary == 'terminal':
        source['collaborationGeneration']['terminal'] = True
    elif boundary == 'placement':
        source['workspace'] += '/moved'
    elif boundary == 'native':
        source['runtimeSessionId'] = 'replacement-native'
    elif boundary == 'execution':
        source['executionRevision'] = source.get('executionRevision', 0) + 1


@pytest.mark.parametrize('kind', ['finite', 'nested'])
@pytest.mark.parametrize('input_count', [1, 17])
async def test_mounted_full_child_saves_and_removes_only_qualified_code(mounted, storage, kind, input_count):
    h = mounted
    await mode(h.app, h.source)
    assigned = ['assigned-' + str(i) for i in range(input_count)]
    h.worker.runtime.generation['input_ids'] = list(assigned)
    h.source['collaborationGeneration']['inputIds'] = list(assigned)
    h.worker.context_inputs = list(assigned)
    h.worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in assigned}
    messages = copy.deepcopy(h.source['messages'])
    completed = []

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        if kind == 'nested' and coordinator.session_id == 'child':
            await h.registry.spawn('worker', 'clean unused code', h.registry.sessions['child'], sub_session_id='grandchild')
            return 'delegated'
        for action, args, identity in [('storage.cleanup.save', values(), 'child-save'),
                ('storage.cleanup.run', {'expectedRevision': 1}, 'child-run')]:
            response = await native_dispatch(tools['app_control'], action, args, identity)
            assert response.success, response.error
            assert '_authority' not in json.dumps(response.output)
        completed.append(coordinator.session_id)
        return 'cleaned'

    h.install_execute(execute)
    await h.registry.spawn('worker', 'clean unused code', h.parent, sub_session_id='child')
    assert completed == (['grandchild'] if kind == 'nested' else ['child'])
    retained = cleanup.record(h.app, 'storage-cleanup-run:child-run')
    authority = retained['_authority']
    assert authority == saved(h.app, 'child-save')['_authority']
    assert authority['sessionId'] == h.source['id']
    assert authority['runtimeSessionId'] == completed[0] and authority['origin'] == 'agent'
    assert authority['inputIds'] == assigned
    assert authority['inputCoverage'] == 'complete'
    assert retained['status'] == 'completed' and retained['removedFileBytes'] > 0
    assert not storage.target.exists() and storage.project.exists()
    assert all((h.app.update_manager.directory / 'releases' / name).exists() for name in storage.names[:3])
    assert h.source['messages'] == messages and not h.source['approvals'] and not h.app.clients.records
    h.approval_prompt.assert_not_awaited()
    await change(h.app, h.source, 'stop')
    await change(h.app, h.source, 'revoke')
    # A later system actor in the same conversation observes the child's
    # original receipt without becoming its author or replaying the run.
    repeated = await call(h.app, h.source, 'storage.cleanup.run', {'expectedRevision': 1},
                          origin='system', command='child-run')
    assert repeated['result'] == cleanup.public(retained)
    assert cleanup.record(h.app, 'storage-cleanup-run:child-run') == retained


async def test_system_policy_is_durable_and_receipts_are_source_bound_private_observations(app, storage, monkeypatch):
    source, peer = app.state['sessions']
    await mode(app, source)
    first = await call(app, source, 'storage.cleanup.save', values(), origin='system', command='system-save')
    original = saved(app, 'system-save')
    authority = original['_authority']
    assert authority['origin'] == 'system' and authority['sessionId'] == source['id']
    assert not {'runtimeSessionId', 'generationId', 'inputIds'} & authority.keys()
    assert '_authority' not in first and app.update_manager.wakeup.is_set()
    await change(app, source, 'terminal')
    await change(app, source, 'stop')
    await change(app, source, 'revoke')
    # Committed configuration drives future maintenance even after setter Stop/revocation.
    result = await cleanup.run(app, 'automatic-after-setter', automatic=True)
    assert result['status'] == 'completed' and not storage.target.exists()
    assert '_authority' not in result and cleanup.policy(app)['revision'] == 1
    monkeypatch.setattr(app.access_mode, 'capture_full', lambda *a: pytest.fail('Receipt recaptured Full'))
    monkeypatch.setattr(app.history, 'ensure_loaded', lambda *a: pytest.fail('Receipt hydrated history'))
    again = await call(app, source, 'storage.cleanup.save', values(), origin='system', command='system-save')
    assert again['duplicate'] and again['result'] == first['result']
    assert saved(app, 'system-save') == original
    with pytest.raises(AppError, match='different calling conversation'):
        await call(app, peer, 'storage.cleanup.save', values(), origin='system', command='system-save')
    with pytest.raises(AppError, match='different contents'):
        await call(app, source, 'storage.cleanup.save', {**values(), 'enabled': False}, origin='system', command='system-save')
    read = await call(app, source, 'storage.cleanup.read', {}, origin='system')
    assert '_authority' not in json.dumps(read)


async def test_mounted_child_accepted_run_outlives_its_turn(mounted, storage, monkeypatch):
    h = mounted
    await mode(h.app, h.source)
    await call(h.app, h.source, 'storage.cleanup.save', values(), origin='system', command='child-policy')
    entered, release = asyncio.Event(), asyncio.Event()
    original = cleanup.preview
    async def preview(app):
        result = await original(app)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(cleanup, 'preview', preview)
    pending = []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        pending.append(asyncio.create_task(native_dispatch(tools['app_control'], 'storage.cleanup.run',
            {'expectedRevision': 1}, 'outlive-child')))
        await asyncio.wait_for(entered.wait(), 5)
        return 'cleanup accepted'
    h.install_execute(execute)
    try:
        await h.registry.spawn('worker', 'clean unused code', h.parent, sub_session_id='child')
        h.source['collaborationGeneration']['terminal'] = True
    finally:
        release.set()
    result = await pending[0]
    assert result.success, result.error
    assert result.output['result']['status'] == 'completed' and not storage.target.exists()
    assert cleanup.record(h.app, 'storage-cleanup-run:outlive-child')['_authority']['runtimeSessionId'] == 'child'
    h.approval_prompt.assert_not_awaited()


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on', 'placement', 'native', 'execution', 'terminal'])
async def test_save_captures_before_lock_and_checks_at_commit(app, storage, monkeypatch, boundary):
    source = app.state['sessions'][0]
    await mode(app, source)
    original_revision = app.access_mode.snapshot(source)['revision']
    entered, release = asyncio.Event(), asyncio.Event()
    original_lock = app.lock
    # Delay only this save's acquisition. Trusted controls still use the real
    # lock and policy owner while the captured operation waits to enter it.
    class HeldLock:
        async def __aenter__(self):
            if asyncio.current_task() is task:
                entered.set()
                await release.wait()
            return await original_lock.__aenter__()
        async def __aexit__(self, *args):
            return await original_lock.__aexit__(*args)
        def locked(self):
            return original_lock.locked()
    monkeypatch.setattr(app, 'lock', HeldLock())
    task = asyncio.create_task(call(app, source, 'storage.cleanup.save', values(), origin='system', command='waiting-save'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await change(app, source, boundary)
        if boundary == 'off-on':
            assert app.access_mode.full(source)
            assert app.access_mode.snapshot(source)['revision'] != original_revision
    finally:
        release.set()
    if boundary == 'terminal':
        assert (await task)['accepted'] and cleanup.policy(app)['revision'] == 1
    else:
        with pytest.raises(AppError, match='authority changed|stopped'):
            await task
        assert cleanup.policy(app)['revision'] == 0 and saved(app, 'waiting-save') is None


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on', 'terminal'])
@pytest.mark.parametrize('wait_at', ['lock', 'preview', 'final-ecosystem'])
async def test_explicit_run_fences_waits_and_real_final_retirement(app, storage, monkeypatch, boundary, wait_at):
    source = app.state['sessions'][0]
    await mode(app, source)
    await call(app, source, 'storage.cleanup.save', values(), origin='system', command='setup')
    entered, release = asyncio.Event(), asyncio.Event()
    original_preview = cleanup.preview
    original_probe = retention._probe
    captured = app.access_mode.capture_full
    async def preview(value):
        report = await original_preview(value)
        if wait_at == 'preview':
            entered.set()
            await release.wait()
        return report
    async def probe(operation, values, **kwargs):
        result = await original_probe(operation, values, **kwargs)
        if (wait_at == 'final-ecosystem' and operation == 'measure'
                and cleanup.record(app, 'storage-cleanup-run:held-run') is not None
                and app.runtime_lifecycle_lock.locked()
                and any(storage.names[3] in path for path in values['paths'])):
            entered.set()
            await release.wait()
        return result
    def capture(*args):
        result = captured(*args)
        if wait_at == 'lock':
            entered.set()
        return result
    monkeypatch.setattr(cleanup, 'preview', preview)
    monkeypatch.setattr(retention, '_probe', probe)
    monkeypatch.setattr(app.access_mode, 'capture_full', capture)
    if wait_at == 'lock':
        await cleanup.lock(app).acquire()
    task = asyncio.create_task(call(app, source, 'storage.cleanup.run', {'expectedRevision': 1},
                                    origin='system', command='held-run'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await change(app, source, boundary)
    finally:
        release.set()
        if wait_at == 'lock':
            cleanup.lock(app).release()
    if boundary == 'terminal':
        assert (await task)['result']['status'] == 'completed'
        assert not storage.target.exists()
    elif wait_at == 'final-ecosystem':
        receipt = (await task)['result']
        assert receipt['status'] == 'interrupted' and not receipt['effects']
        assert storage.target.exists() and '_authority' not in receipt
    else:
        with pytest.raises(AppError, match='authority changed|stopped'):
            await task
        assert storage.target.exists() and cleanup.record(app, 'storage-cleanup-run:held-run') is None


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_unknown_run_is_observed_after_stop_without_replay_or_fresh_authority(app, storage, monkeypatch, origin):
    source, peer = app.state['sessions']
    await mode(app, source)
    await call(app, source, 'storage.cleanup.save', values(), origin='system', command='setup')
    original = retention.reclaim
    async def uncertain(manager, **kwargs):
        if kwargs.get('inspect_only'):
            return await original(manager, **kwargs)
        kwargs['evidence']('attempting', {'kind': 'ecosystem', 'id': storage.names[3]})
        raise asyncio.CancelledError
    monkeypatch.setattr(retention, 'reclaim', uncertain)
    with pytest.raises(asyncio.CancelledError):
        await call(app, source, 'storage.cleanup.run', {'expectedRevision': 1}, origin=origin, command='unknown')
    frozen = cleanup.record(app, 'storage-cleanup-run:unknown')
    assert frozen['status'] == 'interrupted' and frozen['effects'][0]['status'] == 'attempting'
    await change(app, source, 'stop')
    await change(app, source, 'revoke')
    monkeypatch.setattr(app.access_mode, 'capture_full', lambda *a: pytest.fail('Unknown receipt refreshed authority'))
    monkeypatch.setattr(cleanup, 'preview', AsyncMock(side_effect=AssertionError('Unknown receipt replayed')))
    retry = await call(app, source, 'storage.cleanup.run', {'expectedRevision': 1}, origin=origin, command='unknown')
    assert retry['result'] == cleanup.public(frozen)
    assert cleanup.record(app, 'storage-cleanup-run:unknown') == frozen and storage.target.exists()
    assert '_authority' not in json.dumps(retry)
    with pytest.raises(AppError, match='different calling conversation'):
        await call(app, peer, 'storage.cleanup.run', {'expectedRevision': 1}, origin='system', command='unknown')
    with pytest.raises(AppError, match='different contents'):
        await call(app, source, 'storage.cleanup.run', {'expectedRevision': 2}, origin=origin, command='unknown')


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_ask_refuses_mutation_but_passive_reads_and_preview_need_no_capture(app, storage, monkeypatch, origin):
    source = app.state['sessions'][0]
    for action, args in [('storage.cleanup.save', values()), ('storage.cleanup.applyPreset', {'expectedRevision': 0, 'preset': 'temporary-test-workspaces'}), ('storage.cleanup.run', {'expectedRevision': 0})]:
        with pytest.raises(AppError, match='Only the user'):
            await call(app, source, action, args, origin=origin, command=action)
    monkeypatch.setattr(app.access_mode, 'capture_full', lambda *a: pytest.fail('Passive cleanup captured authority'))
    for action in ['storage.cleanup.read', 'storage.cleanup.evidence', 'storage.cleanup.preview']:
        result = await call(app, source, action, {}, origin=origin)
        assert result['accepted'] and '_authority' not in json.dumps(result)
    assert storage.target.exists() and cleanup.policy(app)['revision'] == 0


async def test_stopped_assignment_blocks_fresh_system_work_but_not_saved_policy(app, storage):
    source = app.state['sessions'][0]
    await mode(app, source)
    await call(app, source, 'storage.cleanup.save', values(), origin='system', command='before-assignment-stop')
    assignment = (await app.dispatch('assignment.start', {'sessionId': source['id']}))['result']['assignment']
    await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': assignment['revision']})
    assert app.assignments.blocked(source) and app.access_mode.full(source)
    for action, args in [('storage.cleanup.save', values(1)), ('storage.cleanup.applyPreset', {'expectedRevision': 1, 'preset': 'temporary-test-workspaces'}), ('storage.cleanup.run', {'expectedRevision': 1})]:
        with pytest.raises(AppError, match='authority changed|stopped'):
            await call(app, source, action, args, origin='system', command='fresh-' + action)
    assert storage.target.exists() and cleanup.policy(app)['revision'] == 1
    assert (await cleanup.run(app, 'automatic-after-assignment', automatic=True))['status'] == 'completed'
    assert not storage.target.exists()


async def test_full_selected_chat_does_not_supply_missing_system_caller(app, storage):
    source = app.state['sessions'][0]
    await mode(app, source)
    for action, args in [('storage.cleanup.save', values()), ('storage.cleanup.applyPreset', {'expectedRevision': 0, 'preset': 'temporary-test-workspaces'}), ('storage.cleanup.run', {'expectedRevision': 0})]:
        with pytest.raises(AppError, match='calling conversation'):
            await app.dispatch(action, args, origin='system')
    assert cleanup.policy(app)['revision'] == 0 and storage.target.exists()


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on'])
async def test_runner_final_owner_callback_rechecks_full(app, storage, monkeypatch, boundary):
    from amplifier_web import managed_runner_updates as runners, task_environments, test_environment_setup as private
    from test_storage_cleanup_runner import git
    source = app.state['sessions'][0]
    await mode(app, source)
    await call(app, source, 'storage.cleanup.save', values(), origin='system', command='runner-policy')
    folder = app.smart_tools.root / 'installs' / ('a' * 24)
    code = folder / 'source'
    code.mkdir(parents=True)
    git(code, 'init', '-q')
    git(code, 'config', 'user.name', 'Fixture')
    git(code, 'config', 'user.email', 'fixture@example.invalid')
    (code / 'code.py').write_text('answer = 42\n')
    git(code, 'add', '.')
    git(code, 'commit', '-qm', 'Installed source')
    item = {'id': 'a' * 24, 'commit': git(code, 'rev-parse', 'HEAD'),
            'runnerCandidate': True, 'repository': task_environments.REPOSITORY}
    app.smart_tools.state['installations'].append(item)
    private.write_json(runners.record_path(app, 'c' * 32), {'state': 'superseded',
        'candidate': {'installationId': item['id']}, 'runtime': {'fixture': True}})
    monkeypatch.setattr(runners, 'referenced', lambda *args: False)
    original_probe = retention._probe
    async def probe(operation, values, **kwargs):
        if operation == 'runner':
            return None  # Native runner identity has separate qualification.
        return await original_probe(operation, values, **kwargs)
    monkeypatch.setattr(retention, '_probe', probe)
    original_references = retention.inspect_references
    entered, release = asyncio.Event(), asyncio.Event()
    async def references(*args, **kwargs):
        if app.smart_tools.install_lock.locked():
            entered.set()
            await release.wait()
        return await original_references(*args, **kwargs)
    monkeypatch.setattr(retention, 'inspect_references', references)
    task = asyncio.create_task(call(app, source, 'storage.cleanup.run', {'expectedRevision': 1},
                                    origin='system', command='runner-run'))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        await change(app, source, boundary)
    finally:
        release.set()
    result = (await task)['result']
    assert result['status'] == 'interrupted' and folder.exists()
    assert item in app.smart_tools.state['installations']
    assert not any(row['kind'] == 'runner' for row in result['effects'])


@pytest.mark.parametrize('boundary', ['stop', 'revoke', 'off-on'])
async def test_dependency_final_native_guard_keeps_unknown_receipt(app, storage, prepared, monkeypatch, boundary):
    from amplifier_web import test_dependency_layers as layers, test_environment_preparation as preparation
    source = app.state['sessions'][0]
    await mode(app, source)
    await call(app, source, 'storage.cleanup.save', values(), origin='system', command='dependency-policy')
    config = prepared[2]
    root = app.data_dir / 'saved-source'
    snapshot = dependency_source(root)
    preparation.prepare(config, root, snapshot, 'python-project', 'dependency-source',
        host_reservation={'admitted': True}, dependency_store=layers.store(app.data_dir))
    release_layer(root)
    row = layers.inspect(app.data_dir)['candidates'][0]
    entry = layers.store(app.data_dir) / row['id']
    receipt = json.loads((entry / 'preparation/receipt.json').read_text())
    name, image = receipt['intent']['resourceName'], receipt['image']
    entered, release = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    removals = []
    async def boundary_wait():
        entered.set()
        await release.wait()
    def engine(command, *args, **kwargs):
        if 'ls' in command:
            return image
        if 'inspect' in command:
            asyncio.run_coroutine_threadsafe(boundary_wait(), loop).result(timeout=10)
            return json.dumps([{'Id': image, 'Config': {'Labels': {'io.amplifier.preparation': name}},
                                'RepoTags': [name + ':latest']}])
        if 'rm' in command:
            removals.append(command)
            return ''
        raise AssertionError(command)
    monkeypatch.setattr(preparation, 'run', engine)
    task = asyncio.create_task(call(app, source, 'storage.cleanup.run', {'expectedRevision': 1},
                                    origin='system', command='dependency-run'))
    try:
        await asyncio.wait_for(entered.wait(), 8)
        await change(app, source, boundary)
    finally:
        release.set()
    result = (await task)['result']
    effect = next(item for item in result['effects'] if item['kind'] == 'test-dependencies')
    assert result['status'] == 'interrupted' and effect['status'] == 'attempting'
    assert not removals and not (entry / 'preparation/cleanup.json').exists()
    assert (root / 'source.tar').read_bytes() and '_authority' not in json.dumps(result)
    assert (await call(app, source, 'storage.cleanup.run', {'expectedRevision': 1},
                       origin='system', command='dependency-run'))['result'] == result


@pytest.mark.parametrize('origin', ['agent', 'system'])
async def test_full_caller_sets_standing_temporary_preset_once_without_user_approval(app, storage, origin):
    source = app.state['sessions'][0]
    await mode(app, source)
    messages = copy.deepcopy(source['messages'])
    args = {'expectedRevision': 0, 'preset': 'temporary-test-workspaces'}
    first = await call(app, source, 'storage.cleanup.applyPreset', args, origin=origin, command='full-preset')
    assert first['accepted'] and first['result']['policy']['workspaceArchiveKeepDays'] == 7
    assert source['messages'] == messages and '_authority' not in json.dumps(first)
    await change(app, source, 'revoke')
    retry = await call(app, source, 'storage.cleanup.applyPreset', args, origin=origin, command='full-preset')
    assert retry['duplicate'] and retry['result'] == first['result']
    assert cleanup.policy(app)['revision'] == 1
    with pytest.raises(AppError, match='Only the user'):
        await call(app, source, 'storage.cleanup.applyPreset', {**args, 'expectedRevision': 1}, origin=origin, command='new-preset')
