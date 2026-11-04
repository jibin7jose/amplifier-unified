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


async def install_host_access(parent, app, monkeypatch, diagnostic):
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
    monkeypatch.setattr(app.runtime, 'workers', manager.workers, raising=False)
    replies, checks = {}, []

    async def write(row, reply):
        assert row is owner
        replies[reply['id']] = reply

    manager._write = write
    async def bridge(operation, arguments):
        assert operation == 'work.effect.check'
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
