"""Actual Core/Foundation child lifecycle installs isolated source observations."""
import importlib.util
import json
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('amplifier_module_loop_live')
pytest.importorskip('amplifier_module_context_simple')
pytest.importorskip('amplifier_module_tool_filesystem')
pytest.importorskip('amplifier_module_tool_apply_patch')

from native_tool_helpers import execute_tool
from test_collaborative_workspaces import app


async def install_host_access(parent, app, monkeypatch, diagnostic, *, handles=None):
    """Keep the real worker and host Work checks around this file-policy probe."""
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.shared_state import ActivationGate
    sid = parent.coordinator.session_id
    generation = app._session(sid)['collaborationGeneration']
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(
        generation={'id': generation['id'], 'input_ids': list(generation['inputIds'])},
        closed=False, stop_requested=False)
    worker.activation_gate = ActivationGate()
    worker.activation_gate.activate()
    worker.start_config = {key: app._session(sid).get(key, 0) for key in
        ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}
    worker.context_inputs = list(generation['inputIds'])
    worker.context_bindings = {identity: {'clientId': None, 'targets': []} for identity in worker.context_inputs}
    manager = RuntimeManager()
    manager.app_bridge = app.app_bridge
    owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': uuid.uuid4().hex}
    manager.workers[sid] = owner
    if handles is not None:
        handles.update(worker=worker, manager=manager, owner=owner)
    monkeypatch.setattr(app.runtime, 'workers', manager.workers, raising=False)
    replies, checks = {}, []

    async def write(row, reply):
        assert row is owner
        replies[reply['id']] = reply

    manager._write = write
    async def bridge(operation, arguments):
        assert operation == 'work.effect.check'
        if handles is not None:
            handles['origins'].append(dict(arguments.get('_childOrigin') or {}))
        identity = str(uuid.uuid4())
        await manager._bridge(sid, owner, {'id': identity, 'operation': operation, 'args': arguments})
        reply = replies.pop(identity)
        if reply.get('error'):
            diagnostic['bridge'] = 'error'
            raise RuntimeError(reply['error'])
        result = reply['result']
        # Observe the real host verdict without asserting inside a bridge that
        # Core can wrap. The outer test checks it after exercising file policy.
        observation = {key: result.get(key) if type(result.get(key)) is bool else None
                       for key in ('applicable', 'allowed', 'effectAuthorized')}
        checks.append(observation)
        diagnostic['bridge'] = observation
        return result
    worker.bridge = bridge
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    return checks


def retain_native_failures(execute, failures, diagnostic):
    """Keep this probe's original assertion outside Core's wrapped exception."""
    async def checked(*args, **kwargs):
        try:
            return await execute(*args, **kwargs)
        except Exception as exc:
            failures.append(exc)
            try:
                print('NATIVE_FILE_PROBE ' + json.dumps({**diagnostic, 'exceptionType': type(exc).__name__[:80]}))
            except Exception:
                pass  # Diagnostic output must not replace the original assertion.
            raise
    return checked


def file_result(diagnostic, phase, result, *, target, sibling=None):
    error = result.error if isinstance(result.error, dict) else {}
    code = error.get('code')
    diagnostic.update(phase=phase, success=bool(result.success),
        resultCode=code if isinstance(code, str) and code in {
            'file_source_unread', 'file_source_access', 'file_source_stale',
            'work_precondition_unavailable', 'work_check_timeout', 'work_check_unavailable'} else
        'none' if code is None else 'other', targetExists=target.exists(),
        siblingExists=sibling.exists() if sibling is not None else None)


@pytest.mark.parametrize('mode', ['finite', 'resumed', 'persistent', 'nested'])
async def test_native_child_file_reads_are_not_inherited(tmp_path, monkeypatch, app, mode):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children, _PERSISTENT
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals

    spec = importlib.util.spec_from_file_location('file_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
        'tools': [{'module': 'tool-filesystem', 'config': {'working_dir': str(tmp_path)}},
                  {'module': 'tool-apply-patch', 'config': {'working_dir': str(tmp_path), 'engine': 'native'}}],
        'agents': {'worker': {'instruction': 'Inspect files'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple', 'tool-filesystem', 'tool-apply-patch')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
        Bundle(name='fixture', session=plan['session'], tools=plan['tools'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id=app.state['sessions'][0]['id'], approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    diagnostic, failures = {'phase': 'root-setup'}, []
    checks = await install_host_access(parent, app, monkeypatch, diagnostic)
    path = tmp_path / 'file.txt'
    path.write_text('original\n')
    assert (await execute_tool(parent.coordinator, 'read_file', {'file_path': 'file.txt'})).success
    registry = await install_children(parent, prepared, Runtime(parent.coordinator.session_id), SessionStore(tmp_path / 'children'), approvals)
    guards = []
    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        diagnostic.clear()
        diagnostic.update(phase='child-file-guard', targetExists=path.exists(), siblingExists=None)
        guards.append(coordinator.get_capability('web.file_source_guard'))
        assert guards[-1] is not parent.coordinator.get_capability('web.file_source_guard')
        update = {'type': 'update_file', 'path': 'file.txt', 'diff': '@@\n-original\n+child'}
        diagnostic.update(phase='patch-before-read', resultCode=None, success=None)
        result = await execute_tool(coordinator, 'apply_patch', update)
        file_result(diagnostic, 'patch-before-read', result, target=path)
        assert not result.success and result.error['code'] == 'file_source_unread'
        diagnostic.update(phase='child-read', resultCode=None, success=None)
        result = await execute_tool(coordinator, 'read_file', {'file_path': 'file.txt'})
        file_result(diagnostic, 'child-read', result, target=path)
        assert result.success
        diagnostic.update(phase='patch-after-read', resultCode=None, success=None)
        result = await execute_tool(coordinator, 'apply_patch', update)
        file_result(diagnostic, 'patch-after-read', result, target=path)
        assert result.success
        # Restore bytes externally only for the next independently mounted child.
        path.write_text('original\n')
        if mode == 'nested' and coordinator.session_id == 'child':
            await registry.spawn('worker', 'nested', registry.sessions['child'], sub_session_id='grandchild')
        return 'done'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', retain_native_failures(execute, failures, diagnostic))
    token = _PERSISTENT.set(mode == 'persistent')
    try:
        await registry.spawn('worker', 'inspect', parent, sub_session_id='child')
        if mode == 'resumed':
            await registry.resume('child', 'continue', parent)
        assert len({id(guard) for guard in guards}) == (2 if mode in {'resumed', 'nested'} else 1)
        assert checks and all(row == {'applicable': False, 'allowed': True, 'effectAuthorized': False} for row in checks)
    except Exception:
        if failures:
            raise failures[0] from None
        raise
    finally:
        _PERSISTENT.reset(token)
        await parent.cleanup()


async def test_resumed_native_child_retains_saved_write_ceiling_after_parent_grant(tmp_path, monkeypatch, app):
    """A new root grant permits a fresh child, not escalation of a saved child.

    Uses actual Core/Foundation initialization, checkpoint and resume, with a
    deterministic orchestrator invoking the actual filesystem writer. This is
    not the outer RuntimeManager or a Stories import/review/export acceptance.
    """
    import copy
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.host.approvals import Approvals

    async def deny_network(*args, **kwargs):
        raise AssertionError('Native child policy acceptance must not call a provider')
    monkeypatch.setattr('httpx.AsyncClient.send', deny_network)
    spec = importlib.util.spec_from_file_location('grant_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    workspace = tmp_path / 'workspace'
    approved = tmp_path / 'approved'
    sibling = tmp_path / 'sibling'
    for folder in (workspace, approved, sibling):
        folder.mkdir()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple', 'tool-filesystem')}
    store = SessionStore(tmp_path / 'saved-children')
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    observations, failures, checks = [], [], []
    diagnostic = {'phase': 'root-setup'}

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        target = approved / (prompt + '.txt')
        sibling_target = sibling / (prompt + '.txt')
        diagnostic.clear()
        diagnostic.update(phase=prompt+'-before-write', resultCode=None, success=None,
                          targetExists=target.exists(), siblingExists=sibling_target.exists())
        result = await execute_tool(coordinator, 'write_file', {'file_path': str(target), 'content': prompt})
        file_result(diagnostic, prompt+'-target-write', result, target=target, sibling=sibling_target)
        # Check the target's exact refusal before another call can obscure it.
        if not result.success:
            assert result.error['code'] == 'file_source_access'
            assert result.error['effect'] == 'none'
            assert ('ask the parent to start a new worker' in result.error['message']) == (prompt == 'resumed-denied')
        diagnostic.update(phase=prompt+'-sibling-write', resultCode=None, success=None)
        sibling_result = await execute_tool(coordinator, 'write_file', {
            'file_path': str(sibling_target), 'content': 'must not exist'})
        observations.append((prompt, bool(result.success), target.exists(), bool(sibling_result.success)))
        file_result(diagnostic, prompt+'-sibling-write', sibling_result, target=target, sibling=sibling_target)
        assert not sibling_result.success and not sibling_target.exists()
        assert sibling_result.error['effect'] == 'none'
        assert ('ask the parent to start a new worker' in sibling_result.error['message']) == prompt.startswith('resumed-')
        await context.add_message({'role': 'user', 'content': prompt})
        await context.add_message({'role': 'assistant', 'content': 'allowed' if result.success else 'denied'})
        return 'allowed' if result.success else 'denied'
    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', retain_native_failures(execute, failures, diagnostic))

    async def root(allowed):
        diagnostic.clear()
        diagnostic['phase'] = 'root-setup'
        plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
                'tools': [{'module': 'tool-filesystem', 'config': {
                    'working_dir': str(workspace), 'allowed_write_paths': allowed}}],
                'agents': {'worker': {'instruction': 'Write only within the mounted permission'}}}
        prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
            Bundle(name='fixture', session=plan['session'], tools=plan['tools'], agents=plan['agents']))
        parent = await prepared.create_session(session_id=app.state['sessions'][0]['id'], approval_system=approvals)
        parent.coordinator.register_capability('session.working_dir', str(workspace))
        # Production access installation propagates the file guard to both
        # newly created and resumed native child coordinators.
        checks.append(await install_host_access(parent, app, monkeypatch, diagnostic))
        registry = await install_children(parent, prepared, Runtime(parent.coordinator.session_id), store, approvals)
        return parent, registry

    parent, registry = await root([str(workspace)])
    try:
        await registry.spawn('worker', 'initial-denied', parent, sub_session_id='saved-child')
        saved_messages, saved_metadata = copy.deepcopy(store.load('saved-child'))
    except Exception:
        if failures:
            raise failures[0] from None
        raise
    finally:
        await parent.cleanup()
    assert observations == [('initial-denied', False, False, False)]

    # Model the supported safe boundary: retire the old parent and initialize a
    # new real native mount with the newly saved exact-directory policy.
    parent, registry = await root([str(workspace), str(approved)])
    try:
        resumed = await registry.resume('saved-child', 'resumed-denied', parent)
        assert resumed['output'] == 'denied'
        messages, metadata = store.load('saved-child')
        assert messages[:len(saved_messages)] == saved_messages
        assert metadata['mount_plan']['tools'] == saved_metadata['mount_plan']['tools']
        fresh = await registry.spawn('worker', 'fresh-allowed', parent, sub_session_id='fresh-child')
        assert fresh['output'] == 'allowed'
        assert (approved / 'fresh-allowed.txt').read_text() == 'fresh-allowed'
        fresh_resumed = await registry.resume('fresh-child', 'resumed-allowed', parent)
        assert fresh_resumed['output'] == 'allowed'
        assert (approved / 'resumed-allowed.txt').read_text() == 'resumed-allowed'
        assert all(row['status'] == 'completed' for row in registry.snapshot())
        assert [row['resumed'] for row in fixture.created if row['id'] == 'saved-child'] == [False, True]
        assert [row['resumed'] for row in fixture.created if row['id'] == 'fresh-child'] == [False, True]
        assert all(rows and all(row == {'applicable': False, 'allowed': True, 'effectAuthorized': False}
                               for row in rows) for rows in checks)
    except Exception:
        if failures:
            raise failures[0] from None
        raise
    finally:
        await parent.cleanup()
    assert observations == [
        ('initial-denied', False, False, False),
        ('resumed-denied', False, False, False),
        ('fresh-allowed', True, True, False),
        ('resumed-allowed', True, True, False),
    ]


@pytest.mark.parametrize('placement', ['derived', 'pinned'])
async def test_native_child_resume_follows_committed_worktree_handoff(tmp_path, tmp_path_factory, monkeypatch, app, placement):
    """Real child checkpoint/remount across host placement, not a live worker drain."""
    import asyncio
    import copy
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_worktrees.git import git
    from amplifier_worktrees import GitWorktrees
    from test_worktrees import settle

    async def deny_network(*args, **kwargs):
        raise AssertionError('Child handoff must not call a provider')
    monkeypatch.setattr('httpx.AsyncClient.send', deny_network)
    home = tmp_path.resolve()
    # This app fixture keeps its data under workspace; real managed checkout
    # storage must instead be outside the source repository.
    monkeypatch.setattr(app.worktrees, 'git', GitWorktrees(tmp_path_factory.mktemp('handoff-checkouts')))
    git(home, 'init', '-b', 'main')
    git(home, 'config', 'user.name', 'Fixture')
    git(home, 'config', 'user.email', 'fixture@example.invalid')
    original = 'original\n'
    (home / 'handoff.txt').write_text(original)
    git(home, 'add', 'handoff.txt')
    git(home, 'commit', '-m', 'fixture')
    sid = app.state['sessions'][0]['id']
    inspected = (await app.dispatch('worktree.inspect', {'sessionId': sid, 'mode': 'clean'}))['result']
    checkout = (await app.dispatch('worktree.create', {
        'sessionId': sid, 'mode': 'clean', 'expectedCommit': inspected['repository']['expectedCommit']},
        command_id='child-handoff-checkout'))['result']
    target = Path(checkout['path'])
    # Authorize both exact files before spawn. Handoff cannot widen a saved
    # A-only ceiling; unrelated old-home and sibling files remain forbidden.
    allowed = [str(home / 'handoff.txt'), str(target / 'handoff.txt')]
    forbidden = [home / 'old-home-private.txt', target.parent / 'sibling-private.txt']
    for path in forbidden:
        path.write_text('protected\n')
    spec = importlib.util.spec_from_file_location('handoff_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple', 'tool-filesystem', 'tool-apply-patch')}
    store = SessionStore.for_app(app.data_dir, str(home))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    diagnostic, failures, guards, runs, invoked, access = {}, [], [], [], [], []
    saved_messages = []
    checkpoint_messages = []
    parents = []

    async def root():
        cwd = Path(app._session(sid).get('workingDirectory') or home)
        policy = {'allowed_write_paths': allowed + ([str(forbidden[0])] if cwd == target else [])}
        config = {**policy, **({'working_dir': str(home)} if placement == 'pinned' else {})}
        plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
                'tools': [{'module': 'tool-filesystem', 'config': copy.deepcopy(config)},
                          {'module': 'tool-apply-patch', 'config': {**config, 'engine': 'native'}}],
                'agents': {'worker': {'instruction': 'Inspect the assigned file'}}}
        prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
            Bundle(name='fixture', session=plan['session'], tools=plan['tools'], agents=plan['agents']))
        parent = await prepared.create_session(session_id=sid, session_cwd=cwd, approval_system=approvals)
        parents.append(parent)
        parent.coordinator.register_capability('web.history_workspace', str(home))
        handles = {'origins': []}
        access.append(handles)
        handles['checks'] = await install_host_access(parent, app, monkeypatch, diagnostic, handles=handles)
        registry = await install_children(parent, prepared, Runtime(sid), store, approvals)
        return parent, registry

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        diagnostic.update(phase=prompt)
        invoked.append(prompt)
        resumed = prompt == 'after-handoff'
        expected_cwd = target if resumed else home
        expected_file = home / 'handoff.txt' if placement == 'pinned' else expected_cwd / 'handoff.txt'
        assert coordinator.session_id == 'handoff-child'
        assert Path(coordinator.get_capability('session.working_dir')) == expected_cwd
        assert coordinator.get_capability('web.history_workspace') == str(home)
        assert coordinator.get_capability('web.child.resumed') is resumed
        guard = coordinator.get_capability('web.file_source_guard')
        guards.append(guard)
        runs.append(coordinator.get_capability('web.worker_run'))
        assert guard is not parent.coordinator.get_capability('web.file_source_guard')
        if resumed:
            assert guard is not guards[0] and runs[-1] != runs[0]
            assert await context.get_messages() == saved_messages
        update = {'type': 'update_file', 'path': 'handoff.txt', 'diff': '@@\n-original\n+child'}
        before = {path: path.read_bytes() for path in [home / 'handoff.txt', target / 'handoff.txt', *forbidden]}
        unread = await execute_tool(coordinator, 'apply_patch', update)
        assert not unread.success and unread.error['code'] == 'file_source_unread'
        assert unread.error['effect'] == 'none'
        assert {path: path.read_bytes() for path in before} == before
        read = await execute_tool(coordinator, 'read_file', {'file_path': 'handoff.txt'})
        assert read.success and 'original' in str(read.output)
        # A spawn read is checkpointed but must not authorize the resumed patch.
        if resumed:
            patched = await execute_tool(coordinator, 'apply_patch', update)
            assert patched.success
            assert expected_file.read_text() == 'child\n'
            untouched = target if placement == 'pinned' else home
            assert (untouched / 'handoff.txt').read_text() == original
        for path in forbidden:
            assert (await execute_tool(coordinator, 'read_file', {'file_path': str(path)})).success
            refused_patch = await execute_tool(coordinator, 'apply_patch', {
                'type': 'update_file', 'path': str(path), 'diff': '@@\n-protected\n+escape'})
            assert not refused_patch.success and refused_patch.error['code'] == 'file_source_access'
            assert refused_patch.error['effect'] == 'none' and path.read_text() == 'protected\n'
            refused = await execute_tool(coordinator, 'write_file', {'file_path': str(path), 'content': 'escape'})
            assert not refused.success and refused.error['code'] == 'file_source_access'
            assert refused.error['effect'] == 'none' and path.read_text() == 'protected\n'
        origin = access[-1]['origins'][-1]
        assert origin['runtimeSessionId'] == 'handoff-child' and origin['rootSessionId'] == sid
        assert origin['runId'] == runs[-1]
        assert origin['executionRevision'] == int(resumed)
        assert origin['interruptionRevision'] == stop_epoch
        if resumed:
            from amplifier_web.child_execution import current
            from amplifier_web.collaboration import BINDING, PRINCIPAL
            principal = PRINCIPAL.set('handoff-child')
            binding = BINDING.set({'_childOrigin': origin, '_generationId': origin['generationId'],
                                  '_runtimeOwnerId': access[-1]['owner']['runtimeOwnerId']})
            try:
                assert current(app, app._session(sid)) == origin
                # Same current owner, but the exact captured A execution envelope
                # cannot authorize the moved session.
                BINDING.set({**BINDING.get(), '_childOrigin': access[0]['origins'][-1]})
                assert current(app, app._session(sid)) is None
            finally:
                BINDING.reset(binding)
                PRINCIPAL.reset(principal)
            before_stop = {path: path.read_bytes() for path in before}
            positive_checks = len(access[-1]['checks'])
            await app.dispatch('conversation.stop', {'sessionId': sid}, command_id='stop-resumed-child')
            assert app._session(sid)['interruptionRevision'] == stop_epoch + 1
            stopped = await execute_tool(coordinator, 'apply_patch', {
                'type': 'update_file', 'path': 'handoff.txt', 'diff': '@@\n-child\n+after-stop'})
            assert not stopped.success and stopped.error['code'] == 'work_precondition_unavailable'
            assert len(access[-1]['checks']) > positive_checks
            assert access[-1]['checks'][-1]['allowed'] is False
            assert access[-1]['checks'][-1]['effectAuthorized'] is False
            assert {path: path.read_bytes() for path in before_stop} == before_stop
            access[-1]['positive_checks'] = positive_checks
        await context.add_message({'role': 'user', 'content': prompt})
        await context.add_message({'role': 'assistant', 'content': 'observed'})
        await coordinator.get_capability('live.checkpoint')()
        checkpoint_messages.append(copy.deepcopy(await context.get_messages()))
        assert store.load('handoff-child')[0] == checkpoint_messages[-1]
        return 'observed'

    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute',
        retain_native_failures(execute, failures, diagnostic))
    stop_epoch = app._session(sid).get('interruptionRevision', 0)
    try:
        parent, registry = await root()
        first = await registry.spawn('worker', 'before-handoff', parent, sub_session_id='handoff-child')
        assert first['session_id'] == 'handoff-child'
        saved_messages, saved_metadata = copy.deepcopy(store.load('handoff-child'))
        assert saved_metadata['parent_id'] == sid
        saved_files = {name: (store.directory('handoff-child') / name).read_bytes()
                       for name in ('transcript.jsonl', 'metadata.json')}
        await parent.cleanup()
        parents.remove(parent)
        # The native mount settled. Use the real no-live-worker ownership proof,
        # not a synthetic release receipt or a direct workingDirectory mutation.
        manager = access[-1]['manager']
        manager.workers.pop(sid)
        monkeypatch.setattr(app.runtime, 'quiesce_for_handoff', manager.quiesce_for_handoff, raising=False)
        pending = (await app.dispatch('worktree.handoff', {
            'sessionId': sid, 'id': checkout['id'], 'expectedExecutionRevision': 0},
            command_id='child-handoff-a-b'))['result']
        await asyncio.wait_for(settle(app), 10)
        receipt = app.worktrees.read(sid, pending['id'])
        assert receipt['phase'] == 'applied' and receipt['inputsReplayed'] is False
        assert receipt['release']['quiesced'] and receipt['release']['nativeSessionId'] == sid
        assert receipt['release']['historyHome'] == str(home)
        source = app._session(sid)
        assert source['workspace'] == str(home) and source['workingDirectory'] == str(target)
        assert source['executionRevision'] == 1 and source.get('interruptionRevision', 0) == stop_epoch
        assert {name: (store.directory('handoff-child') / name).read_bytes() for name in saved_files} == saved_files
        parent, registry = await root()
        result = await registry.resume('handoff-child', 'after-handoff', parent)
        assert result['session_id'] == 'handoff-child' and result['output'] == 'observed'
        messages, metadata = store.load('handoff-child')
        assert messages == checkpoint_messages[-1]
        assert messages[:len(saved_messages)] == saved_messages
        assert [{key: message[key] for key in ('role', 'content')}
                for message in messages[len(saved_messages):]] == [
            {'role': 'user', 'content': 'after-handoff'}, {'role': 'assistant', 'content': 'observed'}]
        assert metadata['parent_id'] == sid and metadata['mount_plan']['tools'] == saved_metadata['mount_plan']['tools']
        assert [row['resumed'] for row in fixture.created if row['id'] == 'handoff-child'] == [False, True]
        assert invoked == ['before-handoff', 'after-handoff']
        assert all(handle['checks'] and all(row == {'applicable': False, 'allowed': True, 'effectAuthorized': False}
                   for row in handle['checks'][:handle.get('positive_checks')]) for handle in access)
        assert app._session(sid)['executionRevision'] == 1
    except Exception:
        if failures:
            raise failures[0] from None
        raise
    finally:
        for mounted in reversed(parents):
            await mounted.cleanup()
        for handle in access:
            if 'manager' in handle:
                handle['manager'].workers.pop(sid, None)
                await handle['manager'].retention.close()
