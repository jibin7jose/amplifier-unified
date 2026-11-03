"""Full retains its standing policy through the same unsent-request refit."""
import asyncio
import copy
import importlib
import json
from pathlib import Path

import pytest

from test_pretransport_refit_native import native_case
from test_output_image_pairs import pair


@pytest.mark.parametrize('mode', ['pair_refit_authority_unavailable',
                                  'pair_refit_authority_changed',
                                  'pair_refit_authority_delay',
                                  'pair_refit_budget_work_changed', 'pair_work_changed'])
@pytest.mark.parametrize('preparation_context', [False, True])
async def test_full_image_refit_does_not_reintroduce_work_assurance_gate(pair, mode, preparation_context):
    session, state = await native_case(mode, output_pair=pair, selected=True,
        preparation_context=preparation_context, full_access=True)
    try:
        assert await session.execute('Compare the saved images once, then finish.') == 'Done'
        assert state.authority_checks == 0
        assert state.tools == 1 and len(state.bound) == 1
        assert len(state.measurements) == 3 and len(state.transports) == 2
        assert state.transports[-1] == state.measurements[-1]
        assert state.context_snapshots == (2 if preparation_context else 0)
        attempts = state.delivery.attempts.values()
        assert all(attempt['coverage']['complete'] is False for attempt in attempts)
        assert all(attempt['coverage']['effectAuthorized'] is False for attempt in attempts)
    finally:
        await session.cleanup()


@pytest.mark.parametrize('response,cause', [
    (TimeoutError('private endpoint'), 'bridge_timeout'),
    (ValueError('private payload'), 'bridge_error'),
    (None, 'invalid_response'),
    ({'required': {'status': 'unavailable', 'reason': 'private body'}}, 'required_unavailable'),
])
async def test_ask_refusal_preserves_specific_content_free_diagnostic(response, cause):
    from amplifier_web.coordination_delivery import CoordinationDelivery
    from amplifier_web.surface_delivery import SurfaceProvider
    from amplifier_web.request_refit import RequestFitChanged
    from amplifier_web.support_diagnostics import exception_chain, public_exception_chain
    from amplifier_web.session_health import generation_failure, terminal_exception_failure
    from test_coordination_delivery import Previous, available_requirements, empty_discovery, request
    required = available_requirements()
    async def bridge(operation, args):
        if operation == 'coordination.current':
            return {'work': {'available': True, 'required': required, 'discovery': empty_discovery()}}
        if isinstance(response, BaseException):
            raise response
        return response
    class Provider:
        calls = 0
        async def complete(self, *args, **kwargs):
            self.calls += 1
    provider = Provider()
    wrapped = SurfaceProvider(provider, CoordinationDelivery(Previous(), bridge))
    with pytest.raises(RequestFitChanged) as caught:
        await wrapped.complete(request())
    assert provider.calls == 0 and caught.value.cause == cause
    chain = public_exception_chain(exception_chain(caught.value))
    assert chain[0]['requestFit'] == {'reason': 'authority_unavailable', 'components': [], 'cause': cause}
    event = {'event': 'generation.failed', 'generation_id': 'g', 'input_ids': ['i'],
             'error_type': 'RequestFitChanged', 'error_stage': 'manager_turn', 'error_category': 'unknown'}
    failure = {**generation_failure(event), 'generationId': 'g', 'inputIds': ['i'], 'recordedAt': 100}
    session = {'id': 'root', 'status': 'error', 'failure': failure, 'generations': [event]}
    before = copy.deepcopy(session)
    detail = terminal_exception_failure(session, {'generationId': 'g', 'inputIds': ['i'],
        'errorType': 'RequestFitChanged', 'exceptionChain': chain})
    assert detail['category'] == 'request_preparation'
    assert detail['requestFit']['cause'] == cause
    assert detail['recordedAt'] == 100 and detail['retryable'] is False
    assert 'not sent' in detail['guidance'] and 'private' not in json.dumps(detail)
    assert session == before


def test_provider_exception_cannot_claim_local_refusal_and_diagnostics_drop_unknown_content():
    from amplifier_web.request_refit import RequestFitChanged
    from amplifier_web.support_diagnostics import exception_chain, public_exception_chain, terminal_exception_capture
    error = RequestFitChanged('content_changed')
    assert 'requestFit' not in exception_chain(error)[0]
    assert terminal_exception_capture({'generationId': 'g', 'inputIds': ['i'],
        'errorType': 'ValueError', 'exceptionChain': exception_chain(error)}) is None
    for value in ({'reason': 'private prompt'}, {'reason': 'content_changed', 'cause': 'private token'},
                  {'reason': 'content_changed', 'components': ['private payload']},
                  {'reason': 'content_changed', 'components': 'messages'}):
        result = public_exception_chain([{'errorType': 'RequestFitChanged', 'frames': [], 'requestFit': value}])
        assert result == [{'errorType': 'RequestFitChanged', 'frames': []}]


@pytest.mark.parametrize('manager_wrapper', [False, True])
async def test_later_budget_failure_keeps_its_cause_not_the_handled_local_refusal(manager_wrapper):
    from amplifier_web.request_refit import RequestFitChanged
    from amplifier_web.support_diagnostics import exception_chain, terminal_exception_capture
    from amplifier_web.session_health import generation_failure, terminal_exception_failure
    from amplifier_module_loop_live.failures import ManagerTurnError
    from amplifier_module_loop_streaming._request_refit import RefitProvider
    event = {'event': 'generation.failed', 'generation_id': 'g', 'input_ids': ['i'],
             'error_type': 'ValueError', 'error_stage': 'manager_turn', 'error_category': 'unknown'}
    class Provider:
        async def complete(self, request, **kwargs):
            local = RequestFitChanged('content_changed')
            local.local_refusal = True
            raise local
    async def failed_measurement(request, error, options, **kwargs):
        raise ValueError('private budget failure')
    # The real loop retains the handled local refusal as __context__ when its
    # refit measurement fails. Only the later failure can explain the terminal.
    try:
        await RefitProvider(Provider(), failed_measurement).complete(None)
    except ValueError as budget_error:
        if manager_wrapper:
            try:
                raise ManagerTurnError({**event, 'error_message': 'Manager turn failed.'}) from budget_error
            except ManagerTurnError as error:
                chain = exception_chain(error)
        else:
            chain = exception_chain(budget_error)
    capture = terminal_exception_capture({'generationId': 'g', 'inputIds': ['i'],
        'errorType': 'ValueError', 'exceptionChain': chain})
    failure = {**generation_failure(event), 'generationId': 'g', 'inputIds': ['i'], 'recordedAt': 100}
    detail = terminal_exception_failure({'id': 'root', 'status': 'error',
        'failure': failure, 'generations': [event]}, capture)
    assert detail['category'] == 'unknown' and 'requestFit' not in detail
    assert detail['guidance'] == failure['guidance']
    assert 'private budget failure' not in json.dumps(detail)


async def test_actual_live_manager_catch_retains_local_refusal_diagnostic(monkeypatch):
    from amplifier_core.models import ProviderInfo
    from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
    from amplifier_module_loop_live.runtime import Input, Runtime
    from amplifier_web import runtime_worker
    from amplifier_web.coordination_delivery import CoordinationDelivery
    from amplifier_web.surface_delivery import SurfaceProvider
    from amplifier_web.session_health import generation_failure, terminal_exception_failure
    from test_coordination_delivery import Previous, available_requirements, empty_discovery
    plan = {'session': {'orchestrator': {'module': 'loop-live', 'config': {'use_streaming': False}},
                        'context': {'module': 'context-simple'}}}
    paths = {name: Path(importlib.import_module('amplifier_module_' + name.replace('-', '_')).__file__).parent
             for name in ('loop-live', 'context-simple')}
    session = await PreparedBundle(plan, BundleModuleResolver(paths),
        Bundle(name='refusal-fixture', session=plan['session'])).create_session(session_id='refusal-root')
    worker = runtime_worker.Worker()
    worker.session = session
    published, raw = [], []
    monkeypatch.setattr(runtime_worker, 'publish', lambda row: published.append(copy.deepcopy(row)))
    def observe(event):
        raw.append(copy.deepcopy(event))
        worker.observe(event)
    worker.runtime = Runtime(session.session_id, observer=observe)
    session.coordinator.register_capability('live.runtime', worker.runtime)
    worker.capture_manager_failures()
    required = available_requirements()
    async def bridge(operation, args):
        if operation == 'coordination.current':
            return {'work': {'available': True, 'required': required, 'discovery': empty_discovery()}}
        raise TimeoutError('private endpoint')
    class Provider:
        name = 'fixture'
        calls = 0
        def get_info(self):
            return ProviderInfo(id=self.name, display_name='Fixture', capabilities=['tools'],
                                defaults={'model': 'fixture'})
        async def list_models(self):
            return []
        async def complete(self, request, **kwargs):
            self.calls += 1
            raise AssertionError('No provider transport is expected')
    class Tool:
        name = 'app_control'
        description = 'Synthetic app fixture'
        input_schema = {'type': 'object'}
    provider = Provider()
    await session.coordinator.mount('tools', Tool(), name='app_control')
    await session.coordinator.mount('providers', provider, name='fixture')
    # The installed worker applies its execution adapter after Core mounting;
    # mounting the adapter itself would flatten its exception at Core's bridge.
    session.coordinator.get('orchestrator').root_provider = SurfaceProvider(provider,
        CoordinationDelivery(Previous(), bridge))
    task = worker.execution = asyncio.create_task(session.execute(''))
    try:
        await worker.runtime.wait_for(lambda row: row['type'] == 'session.idle', timeout=10)
        await worker.runtime.submit(Input('user', 'Inspect current work.', id='input-1'))
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        terminal = next(row for row in raw if row['type'] == 'generation.failed')
        captures = [row['terminalFailure'] for row in published if row.get('terminalFailure')]
        assert captures
        capture = captures[0]
        event = {**terminal, 'event': 'generation.failed'}
        failure = {**generation_failure(event), 'generationId': terminal['generation_id'],
                   'inputIds': terminal['input_ids'], 'recordedAt': 100}
        detail = terminal_exception_failure({'id': session.session_id, 'status': 'error',
            'failure': failure, 'generations': [event]}, capture)
        assert provider.calls == 0
        assert detail['errorType'] == terminal['error_type'] == 'ValueError'
        assert detail['category'] == 'request_preparation'
        assert detail['requestFit']['cause'] == 'bridge_timeout'
        assert 'private endpoint' not in json.dumps(capture)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await session.cleanup()


@pytest.mark.parametrize('mode,reason', [
    ('pair_refit_permission_revoked', 'authority_unavailable'),
    ('pair_refit_scope_stop', 'native_scope_changed'),
    ('pair_input_changed', 'native_scope_changed'),
])
async def test_full_refit_keeps_actual_permission_stop_and_input_boundary(pair, mode, reason):
    session, state = await native_case(mode, output_pair=pair, selected=True,
        preparation_context=True, full_access=True)
    try:
        with pytest.raises(RuntimeError, match=reason):
            await session.execute('Compare the saved images once, then finish.')
        assert state.tools == 1 and len(state.transports) == 1
        assert state.context_snapshots == 2
    finally:
        await session.cleanup()


@pytest.mark.parametrize('revoked', [False, True])
async def test_refit_that_relied_on_full_cannot_outlive_revocation(pair, revoked):
    mode = 'pair_refit_observation_unavailable' + ('_revoked' if revoked else '')
    session, state = await native_case(mode, output_pair=pair, selected=True, full_access=True)
    try:
        if revoked:
            with pytest.raises(RuntimeError, match='authority_changed'):
                await session.execute('Compare the saved images once, then finish.')
        else:
            assert await session.execute('Compare the saved images once, then finish.') == 'Done'
        assert state.tools == 1
        assert len(state.transports) == (1 if revoked else 2)
        assert len(state.measurements) == 3 and state.authority_checks == 0
    finally:
        await session.cleanup()
