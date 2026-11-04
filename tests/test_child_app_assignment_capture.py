"""First child mount cannot capture authority from newer root work."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_web.collaboration import BINDING, PRINCIPAL
from test_coordination_generation import app


@pytest.mark.parametrize('advance', ['unchanged', 'input', 'generation', 'stop',
                                   'activation', 'missing-owner', 'replaced-owner'])
async def test_first_child_mount_retains_invoking_assignment_through_provider_wait(app, tmp_path, monkeypatch, advance):
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver
    from amplifier_module_loop_live.runtime import Runtime
    from amplifier_web.app_guidance import install_app_access
    from amplifier_web.host.approvals import Approvals
    from amplifier_web.host.children import install_children
    from amplifier_web.host.storage import SessionStore
    from amplifier_web.provider_isolation import ProviderIsolation
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web.runtime_worker import Worker
    from amplifier_web.shared_state import ActivationGate

    spec = importlib.util.spec_from_file_location('assignment_child_fixture',
        Path(__file__).parent / 'fixtures/standalone_children_probe.py')
    fixture = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, fixture)
    spec.loader.exec_module(fixture)
    plan = {'session': {'orchestrator': {'module': 'loop-live'}, 'context': {'module': 'context-simple'}},
            'agents': {'worker': {'instruction': 'Inspect only'}}}
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    prepared = fixture.ProbePrepared(plan, BundleModuleResolver(paths),
        Bundle(name='fixture', session=plan['session'], agents=plan['agents']))
    approvals = Approvals(None, AsyncMock(return_value='deny'))
    parent = await prepared.create_session(session_id='root', approval_system=approvals)
    parent.coordinator.register_capability('session.working_dir', str(tmp_path))
    parent.coordinator.register_capability('web.provider_isolation', ProviderIsolation())

    source = app.state['sessions'][0]
    source['runtimeSessionId'] = 'root'
    source['collaborationGeneration'] = {'id': 'original-generation', 'inputIds': ['original-input'],
                                       'interruptionRevision': 0, 'terminal': False}
    worker = Worker()
    worker.session = parent
    worker.runtime = SimpleNamespace(generation={'id': 'original-generation', 'input_ids': ['original-input']},
                                     closed=False, stop_requested=False)
    worker.activation_gate = ActivationGate()
    activation = worker.activation_gate.activate()
    worker.start_config = {key: source.get(key, 0) for key in
        ('interruptionRevision', 'executionRevision', 'configurationPendingRevision')}
    original_execution = deepcopy(worker.start_config)
    worker.context_inputs = ['original-input']
    worker.context_bindings = {'original-input': {'clientId': 'original-browser', 'targets': []}}
    envelopes, attempts, actors, outcomes, failures = [], [], [], [], []
    manager = RuntimeManager()
    owner = {'process': SimpleNamespace(returncode=None), 'runtimeOwnerId': uuid.uuid4().hex}
    manager.workers[source['id']] = owner
    monkeypatch.setattr(app.runtime, 'workers', manager.workers, raising=False)
    manager.app_bridge = app.app_bridge
    replies = {}

    async def write(row, reply):
        assert row is owner
        replies[reply['id']] = reply

    manager._write = write

    async def transport(operation, args):
        envelopes.append(deepcopy(args))
        identity = 'bridge-' + str(len(envelopes))
        await manager._bridge(source['id'], owner, {'id': identity, 'operation': operation, 'args': args})
        reply = replies.pop(identity)
        if reply.get('error'):
            raise RuntimeError(reply['error'])
        return reply['result']

    async def current_execution(operation, args, sid, **options):
        # Exercise the canonical host consumer after the actual service bridge
        # establishes the child's principal and private transport binding.
        assert sid == source['id'] and operation == 'list_actions'
        attempts.append((PRINCIPAL.get(), deepcopy(BINDING.get())))
        app.collaboration.active_binding(source, allow_descendant=True)
        assert BINDING.get()['_runtimeOwnerId'] == owner['runtimeOwnerId']
        actors.append((PRINCIPAL.get(), deepcopy(BINDING.get())))
        return {'actions': []}

    worker.bridge = transport
    monkeypatch.setattr(app, '_app_bridge', current_execution)
    await install_app_access(parent.coordinator, worker.app_access_bridge(parent.coordinator))
    registry = await install_children(parent, prepared, Runtime('root'), SessionStore(tmp_path / 'children'), approvals)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_provider_preparation(bundle, prepared, *, isolation):
        entered.set()
        await release.wait()

    monkeypatch.setattr('amplifier_web.provider_environment.materialize_bundle_providers', held_provider_preparation)

    async def execute(self, prompt, context, providers, tools, hooks, coordinator):
        result = await tools['app_control'].execute({'operation': 'list_actions', 'args': {
            '_runtimeSessionId': 'root', '_generationId': 'later-generation',
            '_runtimeOwnerId': 'forged-owner'}})
        outcomes.append(result.success)
        assert coordinator.session_id == 'child'
        assert envelopes[-1]['_runtimeSessionId'] == 'child'
        assert envelopes[-1]['_inputBindings'] == [{'inputId': 'original-input', 'clientId': 'original-browser'}]
        assert envelopes[-1]['_generationId'] == 'original-generation'
        origin = envelopes[-1]['_childOrigin']
        assert origin == {'rootSessionId': 'root', 'runtimeSessionId': 'child',
                          'generationId': 'original-generation', 'inputIds': ['original-input'],
                          'runId': registry.rows['child']['runId'], **original_execution}
        assert '_runtimeOwnerId' not in envelopes[-1]  # The host, not model/child, binds it.
        if advance in {'unchanged', 'input', 'generation'}:
            assert result.success
        else:
            assert advance in {'missing-owner', 'replaced-owner'}
            assert not result.success
            assert result.error['message'] == 'The calling runtime no longer owns this bridge.'
        return 'done'

    async def checked(*args, **kwargs):
        try:
            return await execute(*args, **kwargs)
        except AssertionError as exc:
            failures.append(exc)
            raise

    monkeypatch.setattr(fixture.FiniteOrchestrator, 'execute', checked)
    spawn = asyncio.create_task(registry.spawn('worker', 'inspect', parent, sub_session_id='child'))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert not any(row['id'] == 'child' for row in fixture.created)
        if advance == 'input':
            worker.runtime.generation['input_ids'].append('later-input')
            worker.context_inputs = ['later-input']
            worker.context_bindings = {'later-input': {'clientId': 'later-browser', 'targets': []}}
            source['collaborationGeneration']['inputIds'].append('later-input')
        elif advance == 'generation':
            worker.runtime.generation = {'id': 'later-generation', 'input_ids': ['later-input']}
            worker.context_inputs = ['later-input']
            worker.context_bindings = {'later-input': {'clientId': 'later-browser', 'targets': []}}
            source['collaborationGeneration'] = {'id': 'later-generation', 'inputIds': ['later-input'],
                                               'interruptionRevision': 0, 'terminal': False}
        elif advance == 'stop':
            # Stop's epoch changes before its native terminal event. Keep the
            # generation visible to prove the installer retained the old Stop.
            worker.stop_revision += 1
            source['interruptionRevision'] = 1
        elif advance == 'activation':
            worker.activation_gate.release(activation)
        elif advance == 'missing-owner':
            manager.workers.pop(source['id'])
        elif advance == 'replaced-owner':
            manager.workers[source['id']] = {**owner, 'runtimeOwnerId': uuid.uuid4().hex}
        release.set()
        cancelled = False
        try:
            await asyncio.wait_for(spawn, 30)
        except (asyncio.CancelledError, RuntimeError) as exc:
            if failures:
                raise failures[0] from None
            assert advance in {'stop', 'activation'}
            assert str(exc) in {
                'The originating native child run is no longer current.',
                'Execution failed: CancelledError: The originating native child run is no longer current.'}
            cancelled = True
        assert worker.start_config == original_execution
        if advance in {'stop', 'activation'}:
            assert cancelled and not outcomes and not envelopes and not attempts and not actors
        elif advance in {'missing-owner', 'replaced-owner'}:
            assert not cancelled and outcomes == [False] and not attempts and not actors
        else:
            assert not cancelled and outcomes == [True] and len(attempts) == len(actors) == 1
            assert actors[0][0] == actors[0][1]['_runtimeSessionId'] == 'child'
            assert actors[0][1]['_generationId'] == 'original-generation'
            assert actors[0][1]['_inputBindings'] == [{'inputId': 'original-input', 'clientId': 'original-browser'}]
        assert not replies and not app.runtime.inputs
    finally:
        release.set()
        if not spawn.done():
            spawn.cancel()
        await asyncio.gather(spawn, return_exceptions=True)
        await parent.cleanup()
