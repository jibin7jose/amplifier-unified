"""Real reference tools across live write defaults; no provider or paid calls."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

filesystem = pytest.importorskip('amplifier_module_tool_filesystem')
patch = pytest.importorskip('amplifier_module_tool_apply_patch')

from amplifier_web.configuration_authority import fingerprint
from amplifier_web.file_source_guard import install_file_source_guard, _locked
from amplifier_web.host.config import read_config
from amplifier_web.shared_settings import read_settings, settings_paths, update_settings
from amplifier_web.write_policy_refresh import WritePolicySource, install


def save(workspace, session_id, allowed, denied=()):
    path = settings_paths(workspace, session_id=session_id)['session']
    def edit(value):
        value.setdefault('overrides', {}).setdefault('tool-filesystem', {}).setdefault('config', {}).update(
            allowed_write_paths=[str(p) for p in allowed], denied_write_paths=[str(p) for p in denied])
    update_settings(path, edit)


async def mount(workspace, plan, *, policy=None, session_id='root'):
    tools, caps = {}, {'session.working_dir': str(workspace)}
    async def mounted(kind, tool, name):
        tools[name] = tool
    coordinator = SimpleNamespace(config=copy.deepcopy(plan), session_id=session_id,
        get=lambda kind: tools if kind == 'tools' else None, mount=mounted,
        get_capability=caps.get, register_capability=caps.__setitem__,
        hooks=SimpleNamespace(emit=AsyncMock()))
    for row in plan['tools']:
        if row['module'] == 'tool-filesystem':
            await filesystem.mount(coordinator, copy.deepcopy(row.get('config', {})))
        elif row['module'] == 'tool-apply-patch':
            await patch.mount(coordinator, copy.deepcopy(row.get('config', {})))
    if policy:
        coordinator.register_capability('web.write_policy', policy)
    return coordinator, tools


async def root(workspace, *, engine='function', file_config=None, patch_config=None, session_id='root'):
    config = read_config(workspace, session_id=session_id)
    declarations = SimpleNamespace(tools=[
        {'module': 'tool-filesystem', 'config': file_config or {}},
        {'module': 'tool-apply-patch', 'config': {'engine': engine, **(patch_config or {})}}],
        agents={})
    source = WritePolicySource(declarations, config)
    bundle = source.build(config.settings)
    coordinator, tools = await mount(workspace, {'tools': bundle.tools, 'agents': bundle.agents}, session_id=session_id)
    policy = install(coordinator, source, session_id, current=lambda: True)
    await install_file_source_guard(coordinator, workspace / 'locks')
    return coordinator, tools, policy


def create(path, engine='function'):
    if engine == 'native':
        return {'type': 'create_file', 'path': str(path), 'diff': '+new'}
    marker = '*' * 3
    return {'patch': f'{marker} Begin Patch\n{marker} Add File: {path}\n+new\n{marker} End Patch'}


@pytest.mark.parametrize('engine', ['native', 'function'])
async def test_next_writer_uses_saved_policy_without_remount_replay_or_new_input(tmp_path, engine):
    workspace, extra = tmp_path / 'project', tmp_path / 'approved'
    workspace.mkdir()
    coordinator, tools, policy = await root(workspace, engine=engine)
    original = tools['apply_patch'].tool
    old_config = copy.deepcopy(original.config)
    path = extra / 'new'
    assert not (await tools['apply_patch'].execute(create(path, engine))).success
    assert not path.exists()
    save(workspace, 'root', [extra])
    assert (await tools['apply_patch'].execute(create(path, engine))).success
    assert path.read_text().strip() == 'new'
    assert original.config == old_config
    assert tools['apply_patch'].tool is original
    assert str(extra) in coordinator.config['tools'][0]['config']['allowed_write_paths']
    assert policy.refresh()['settingsRevision'] == fingerprint(read_settings(workspace, session_id='root'))
    from amplifier_web.resource_access import observe
    observed = await observe(SimpleNamespace(coordinator=coordinator), [str(extra / 'other')])
    assert observed['status'] == 'observed'
    assert observed['paths'][str(extra / 'other')]['status'] == 'allowed'


@pytest.mark.parametrize('writer', ['write_file', 'edit_file', 'apply_patch'])
async def test_narrow_revoke_reset_denials_read_policy_and_symlink_alias(tmp_path, writer):
    workspace, extra = tmp_path / 'project', tmp_path / 'approved'
    workspace.mkdir()
    extra.mkdir()
    secret = extra / 'secret'
    secret.write_text('old')
    config = {'allowed_read_paths': [str(workspace)], 'denied_read_paths': [str(secret)],
              'denied_write_paths': [str(secret)]}
    coordinator, tools, policy = await root(workspace, file_config=config)
    save(workspace, 'root', [extra])
    assert not (await tools['read_file'].execute({'file_path': str(secret)})).success
    assert not (await tools['write_file'].execute({'file_path': str(secret), 'content': 'bad'})).success
    alias = workspace / 'alias'
    alias.symlink_to(secret)
    assert not (await tools['write_file'].execute({'file_path': str(alias), 'content': 'bad'})).success
    policy.refresh()
    target = extra / writer
    arguments = create(target) if writer == 'apply_patch' else {'file_path': str(target), 'content': 'new'}
    if writer == 'edit_file':
        target.write_text('old')
        arguments = {'file_path': str(target), 'old_string': 'old', 'new_string': 'new'}
        assert (await tools[writer].execute(arguments)).error['code'] == 'file_source_unread'
    else:
        assert (await tools[writer].execute(arguments)).success
    save(workspace, 'root', [], [extra])
    denied = await tools[writer].execute(arguments)
    assert not denied.success and denied.error['effect'] == 'none'
    update_settings(settings_paths(workspace, session_id='root')['session'], lambda _: {})
    assert not (await tools[writer].execute(arguments)).success
    assert secret.read_text() == 'old'


@pytest.mark.parametrize('allowed', [[], ['src']])
async def test_bundle_readonly_or_narrower_ceiling_never_expands(tmp_path, allowed):
    coordinator, tools, policy = await root(tmp_path, file_config={'allowed_write_paths': allowed})
    save(tmp_path, 'root', [tmp_path.parent])
    assert not (await tools['apply_patch'].execute(create(tmp_path / 'outside-src'))).success
    assert coordinator.config['tools'][0]['config']['allowed_write_paths'] == [str(tmp_path / p) for p in allowed]


async def test_existing_preparing_and_resumed_children_only_narrow_new_children_inherit(tmp_path):
    workspace, extra = tmp_path / 'project', tmp_path / 'approved'
    workspace.mkdir()
    parent, tools, policy = await root(workspace)
    captured = copy.deepcopy(parent.config)
    child, child_tools = await mount(workspace, captured, policy=policy, session_id='preparing')
    await install_file_source_guard(child, workspace / 'locks')
    save(workspace, 'root', [extra])
    policy.refresh()
    for identity in ('preparing', 'resumed'):
        if identity == 'resumed':
            child, child_tools = await mount(workspace, captured, policy=policy, session_id=identity)
            await install_file_source_guard(child, workspace / 'locks')
        assert not (await child_tools['apply_patch'].execute(create(extra / identity))).success
    fresh, fresh_tools = await mount(workspace, parent.config, policy=policy, session_id='fresh')
    await install_file_source_guard(fresh, workspace / 'locks')
    assert (await fresh_tools['apply_patch'].execute(create(extra / 'fresh'))).success
    save(workspace, 'root', [], [extra])
    assert not (await fresh_tools['apply_patch'].execute(create(extra / 'revoked'))).success


async def test_narrowing_rechecked_after_file_lock_wait_and_inflight_snapshot_is_immutable(tmp_path):
    coordinator, tools, policy = await root(tmp_path)
    path = tmp_path / 'new'
    raw = tools['write_file'].tool
    snapshot = policy.invocation(raw, 'tool-filesystem', coordinator)
    async with _locked(tmp_path / 'locks', [path]):
        pending = asyncio.create_task(tools['write_file'].execute({'file_path': str(path), 'content': 'new'}))
        await asyncio.sleep(.05)
        assert not pending.done()
        save(tmp_path, 'root', [], [path])
        policy.refresh()
    result = await pending
    assert not result.success and not path.exists()
    assert snapshot._check_write_access(path)[0]
    assert raw._check_write_access(path)[0]
    assert not policy.invocation(raw, 'tool-filesystem', coordinator)._check_write_access(path)[0]


async def test_unrelated_read_change_or_stopped_owner_fails_closed(tmp_path):
    coordinator, tools, policy = await root(tmp_path)
    save(tmp_path, 'root', [tmp_path.parent])
    path = settings_paths(tmp_path, session_id='root')['session']
    def change_read(value):
        value['overrides']['tool-filesystem']['config']['allowed_read_paths'] = []
    update_settings(path, change_read)
    result = await tools['apply_patch'].execute(create(tmp_path / 'new'))
    assert not result.success and result.error['effect'] == 'none'
    assert 'Other configuration changed' in result.error['message']
    policy.current = lambda: False
    with pytest.raises(ValueError, match='no longer current'):
        policy.refresh()


async def test_same_mounted_writer_running_during_grant_keeps_its_old_snapshot(tmp_path, monkeypatch):
    from amplifier_module_tool_filesystem.write import WriteTool
    workspace, extra = tmp_path / 'project', tmp_path / 'approved'
    workspace.mkdir()
    coordinator, tools, policy = await root(workspace)
    raw = tools['write_file'].tool
    entered, release = asyncio.Event(), asyncio.Event()
    original = WriteTool.execute
    snapshots = []
    async def held(self, arguments):
        snapshots.append(self)
        if arguments['file_path'] == str(workspace / 'held'):
            entered.set()
            await release.wait()
        return await original(self, arguments)
    monkeypatch.setattr(WriteTool, 'execute', held)
    pending = asyncio.create_task(tools['write_file'].execute({
        'file_path': str(workspace / 'held'), 'content': 'old invocation'}))
    await entered.wait()
    save(workspace, 'root', [extra])
    assert (await tools['write_file'].execute({
        'file_path': str(extra / 'new'), 'content': 'new invocation'})).success
    assert snapshots[0] is not snapshots[1] and all(snapshot is not raw for snapshot in snapshots)
    assert not snapshots[0]._check_write_access(extra / 'new')[0]
    assert snapshots[1]._check_write_access(extra / 'new')[0]
    release.set()
    assert (await pending).success
    assert (workspace / 'held').read_text() == 'old invocation'


@pytest.mark.parametrize('state', ['active', 'stale', 'parked', 'superseded-settings'])
async def test_worker_refresh_binds_only_existing_activation_and_never_starts_input(tmp_path, monkeypatch, state):
    from amplifier_web.runtime_worker import Worker
    from amplifier_foundation.session.activation import ActivationGate
    coordinator, tools, policy = await root(tmp_path)
    worker = Worker()
    worker.session = SimpleNamespace(coordinator=coordinator)
    worker.shared_handle = object()
    worker.activation_gate = ActivationGate()
    worker.activation = worker.activation_gate.activate()
    worker.ownership = SimpleNamespace(yielding=False)
    worker.parked = state == 'parked'
    policy.current = lambda: worker.activation_gate.current_valid and state != 'stale'
    save(tmp_path, 'root', [tmp_path.parent])
    revision = fingerprint(read_settings(tmp_path, session_id='root'))
    replies = []
    monkeypatch.setattr('amplifier_web.runtime_worker.publish', replies.append)
    # The command task starts outside the loop's activation context, like stdin.
    worker.activation_gate._current.set(None)
    await worker.command({'id': 'refresh', 'op': 'file_access.refresh',
                          'settingsRevision': revision if state != 'superseded-settings' else 'stale'})
    assert worker.activation_gate.current() is None
    assert worker.runtime is None and worker.execution is None and not worker.start_task
    assert len(replies) == 1
    if state == 'active':
        assert replies[0]['result'] == {'settingsRevision': revision, 'boundary': 'next-tool-call'}
    else:
        assert 'error' in replies[0]


async def test_runtime_refresh_uses_existing_process_only_and_rejects_changed_identity(tmp_path):
    import sys
    from amplifier_web.runtime import RuntimeManager
    fixture = r'''
import json,sys
for line in sys.stdin:
    data=json.loads(line)
    if data['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif data['op']=='file_access.refresh':
        print(json.dumps({'op':'reply','id':data['id'],'result':{
            'settingsRevision':data['settingsRevision'],'boundary':'next-tool-call'}}),flush=True)
    elif data['op']=='stop':
        break
'''
    manager = RuntimeManager(command=[sys.executable, '-u', '-c', fixture],
                             startup_timeout=3, retention={'prewarm_on_select': False})
    manager.home = tmp_path / 'host'
    async def emit(kind, payload):
        pass
    session = {'id': 'root', 'workspace': str(tmp_path), 'bundle': 'fixture'}
    try:
        assert not await manager.refresh_file_access('root', 'revision')
        await manager.start(session, emit)
        row = manager.workers['root']
        assert await manager.refresh_file_access('root', 'revision')
        assert manager.workers['root'] is row
        original = manager._request
        async def replaced(*args, **kwargs):
            result = await original(*args, **kwargs)
            manager.workers['root'] = dict(row)
            return result
        manager._request = replaced
        assert not await manager.refresh_file_access('root', 'revision')
        manager.workers['root'] = row
        manager._request = original
        await manager.stop('root')
        assert not await manager.refresh_file_access('root', 'revision')
        assert 'root' not in manager.workers
    finally:
        await manager.close()


def test_named_nested_filesystem_denial_survives_recomposition(tmp_path):
    config = read_config(tmp_path)
    config.settings = {'overrides': {'child-files': {'config': {
        'denied_write_paths': ['private']}}}}
    bundle = SimpleNamespace(tools=[{'module': 'tool-apply-patch'}], agents={
        'nested': {'tools': [{'module': 'tool-filesystem', 'id': 'child-files'}]}})
    source = WritePolicySource(bundle, config)
    fresh = source.build(config.settings)
    assert str(tmp_path / 'private') in fresh.tools[0]['config']['denied_write_paths']
    assert str(tmp_path / 'private') in fresh.agents['nested']['tools'][0]['config']['denied_write_paths']


async def test_real_image_tool_keeps_read_and_paid_policy_while_writes_narrow(tmp_path):
    image = pytest.importorskip('amplifier_module_tool_image')
    config = read_config(tmp_path)
    declarations = SimpleNamespace(tools=[{'module': 'tool-image', 'config': {
        'allowed_read_paths': [], 'allow_paid': False}}], agents={})
    source = WritePolicySource(declarations, config)
    fresh = source.build(config.settings)
    coordinator, tools = await mount(tmp_path, {'tools': fresh.tools})
    await image.mount(coordinator, fresh.tools[0]['config'])
    policy = install(coordinator, source, 'root', current=lambda: True)
    await install_file_source_guard(coordinator, tmp_path / 'locks')
    wrapped = tools['image_generate']
    old = policy.invocation(wrapped.tool, 'tool-image', coordinator)
    assert old.path('new.png', write=True) == tmp_path / 'new.png'
    save(tmp_path, 'root', [], [tmp_path / 'new.png'])
    narrowed = policy.invocation(wrapped.tool, 'tool-image', coordinator)
    with pytest.raises(ValueError, match='Access denied'):
        narrowed.path('new.png', write=True)
    with pytest.raises(ValueError, match='Access denied'):
        narrowed.path('new.png')
    assert old.path('new.png', write=True) == tmp_path / 'new.png'
    assert narrowed.config['allow_paid'] is False
    result = await wrapped.execute({'action': 'capabilities'})
    assert result.success


@pytest.mark.parametrize('settings_bytes', [100, 100_000])
async def test_unchanged_admission_does_not_reparse_copy_or_hash_settings(tmp_path, monkeypatch, settings_bytes):
    import amplifier_web.write_policy_refresh as refresh
    global_path = settings_paths(tmp_path)['global']
    update_settings(global_path, lambda _: {'unrelated': {'large': 'x' * settings_bytes}})
    coordinator, tools, policy = await root(tmp_path)
    original_read, original_hash = refresh.read_settings, refresh.fingerprint
    reads, hashes = [], []
    def read(*args, **kwargs):
        reads.append(1)
        return original_read(*args, **kwargs)
    def digest(value):
        hashes.append(1)
        return original_hash(value)
    monkeypatch.setattr(refresh, 'read_settings', read)
    monkeypatch.setattr(refresh, 'fingerprint', digest)
    for index in range(10):
        assert (await tools['write_file'].execute({
            'file_path': str(tmp_path / str(index)), 'content': 'new'})).success
    assert len(reads) == len(hashes) == 1
    save(tmp_path, 'root', [], [tmp_path / 'denied'])
    denied = await tools['write_file'].execute({'file_path': str(tmp_path / 'denied'), 'content': 'bad'})
    assert not denied.success
    assert len(reads) == len(hashes) == 2


async def test_actual_read_config_runtime_relocation_preserves_denied_subtree(tmp_path):
    home = tmp_path / 'app'
    runtime = home / 'updates/releases/selected/foundation'
    canonical = home / 'foundation'
    blocked = runtime / 'blocked'
    update_settings(settings_paths(tmp_path)['global'], lambda _: {'overrides': {
        'tool-filesystem': {'config': {'allowed_write_paths': [str(canonical)],
                                     'denied_write_paths': [str(canonical / 'blocked')]}}}})
    config = read_config(tmp_path, home=home, registry_home=runtime, session_id='root')
    assert config.settings['overrides']['tool-filesystem']['config']['denied_write_paths'] == [str(blocked)]
    source = WritePolicySource(SimpleNamespace(tools=[{'module': 'tool-filesystem'},
        {'module': 'tool-apply-patch', 'config': {'engine': 'function'}}], agents={}), config)
    fresh = source.build(config.settings)
    coordinator, tools = await mount(tmp_path, {'tools': fresh.tools})
    policy = install(coordinator, source, 'root', current=lambda: True)
    await install_file_source_guard(coordinator, tmp_path / 'locks')
    for after_save in (False, True):
        if after_save:
            save(tmp_path, 'root', [canonical, tmp_path / 'extra'], [canonical / 'blocked'])
        assert not (await tools['write_file'].execute({
            'file_path': str(blocked / 'denied'), 'content': 'bad'})).success
        assert not (await tools['apply_patch'].execute(create(blocked / 'denied-patch'))).success
        assert str(blocked) in coordinator.config['tools'][0]['config']['denied_write_paths']
    assert not blocked.exists()


@pytest.mark.parametrize('fault', ['raw-writer', 'foreign-guard', 'wrong-kind', 'missing-policy', 'foreign-image'])
async def test_confirmation_requires_enforcing_wrapper_and_exact_coordinator_binding(tmp_path, fault):
    from amplifier_web.file_source_guard import GuardedFileTool, FileSourceGuard
    from amplifier_web.write_policy_refresh import PolicyImageTool
    coordinator, tools, policy = await root(tmp_path)
    raw = tools['write_file'].tool
    if fault == 'raw-writer':
        tools['write_file'] = raw
    elif fault == 'foreign-guard':
        tools['write_file'] = GuardedFileTool(raw, FileSourceGuard(SimpleNamespace(), tmp_path), 'write')
    elif fault == 'wrong-kind':
        tools['write_file'] = GuardedFileTool(raw, coordinator.get_capability('web.file_source_guard'), 'read')
    elif fault == 'missing-policy':
        coordinator.register_capability('web.write_policy', None)
    else:
        tools['write_file'] = PolicyImageTool(raw, SimpleNamespace())
    save(tmp_path, 'root', [tmp_path.parent])
    with pytest.raises(ValueError, match='binding|complete live refresh'):
        policy.confirm(fingerprint(read_settings(tmp_path, session_id='root')))


async def test_multiple_named_declarations_preserve_unchanged_mount_but_refuse_unsafe_refresh(tmp_path):
    config = read_config(tmp_path)
    source = WritePolicySource(SimpleNamespace(tools=[
        {'module': 'tool-filesystem', 'id': name, 'config': {'denied_write_paths': ['private']}}
        for name in ('one', 'two')], agents={}), config)
    fresh = source.build(config.settings)
    coordinator, tools = await mount(tmp_path, {'tools': fresh.tools})
    policy = install(coordinator, source, 'root', current=lambda: True)
    await install_file_source_guard(coordinator, tmp_path / 'locks')
    assert (await tools['write_file'].execute({'file_path': str(tmp_path / 'ordinary'), 'content': 'ok'})).success
    assert not (await tools['write_file'].execute({'file_path': str(tmp_path / 'private/secret'), 'content': 'bad'})).success
    save(tmp_path, 'root', [tmp_path.parent])
    assert not (await tools['write_file'].execute({'file_path': str(tmp_path / 'changed'), 'content': 'bad'})).success
    with pytest.raises(ValueError, match='complete live refresh|binding'):
        policy.confirm(fingerprint(read_settings(tmp_path, session_id='root')))