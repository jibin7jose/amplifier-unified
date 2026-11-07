"""Operator originals recover through Full's existing native effect boundary.

Core/Foundation children and the real Worker bridge are mounted. Native writes
use the subprocess fixture; its mount/lease setup remains synthetic.
"""
import asyncio
from copy import deepcopy
import json

import pytest

from amplifier_web.configuration_recovery import evidence
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_configuration_authority import finished, revoke
from test_full_access_configuration_recovery import replace_mode
from test_full_configuration_continuation import child, system
from test_queued_configuration_boundary import (
    deferred_native_evidence, operation, plan, process_apply, process_boundary, recovery,
)


async def operator_failure(b, origin='ui', outcome='none'):
    if outcome == 'none':
        await b.runtime._request(b.owner['id'], 'fixture.busy', count=1)
    else:
        await b.runtime._request(b.owner['id'], 'fixture.partial')
    args = {'id': b.owner['id'], 'config': plan(2)}
    with deferred_native_evidence():
        if origin == 'internal':
            await b.app.management.command('configuration.apply', args, 'operator-apply')
        else:
            await b.app.dispatch('configuration.apply', args, origin=origin, command_id='operator-apply')
        assert (await finished(b.app, 'operator-apply'))['phase'] == 'error'
    saved = operation(b)
    assert saved['request']['authorization'] == origin
    assert saved['request']['authority'] is None
    assert saved['state']['effect'] == 'unknown'
    assert evidence(saved['attempts'][-1])['effect'] == outcome
    b.owner['messages'] = []
    return saved


async def system_recovery(b, verb, identity=None, **extra):
    saved = operation(b)
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'], **extra}
    if verb != 'read':
        args['expectedRevision'] = saved['revision']
    return await system(b.app, b.owner, 'configuration.operation.' + verb, args,
                        identity or verb + '-' + str(saved['revision']))


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('origin', ['ui', 'user', 'internal'])
@pytest.mark.parametrize('actor', ['child', 'system'])
async def test_full_actor_continues_operator_original(process_boundary, tmp_path, monkeypatch, origin, actor):
    b = process_boundary
    saved = await operator_failure(b, origin)
    accepted = []
    async def exercise(invoke, principal):
        args = {'sessionId': b.owner['id'], 'operationId': saved['id']}
        view = (await invoke('configuration.operation.read', {**args, 'includeRecovery': True}, 'operator-view'))['result']
        assert view['recovery']['canReconcile'] and not view['recovery']['canResume']
        assert operation(b) == saved
        expected_source = {'origin': 'agent' if actor == 'child' else 'system',
            'sessionId': b.owner['id'], 'runtimeSessionId': principal,
            'generationId': b.owner['collaborationGeneration']['id'] if actor == 'child' else None,
            'interruptionRevision': b.owner.get('interruptionRevision', 0),
            'inputIds': b.owner['collaborationGeneration']['inputIds'] if actor == 'child' else [],
            'inputCoverage': 'complete' if actor == 'child' else 'unavailable',
            'accessRevision': b.app.access_mode.snapshot(b.owner)['revision']}
        reconciled = (await invoke('configuration.operation.reconcile', {
            **args, 'expectedRevision': saved['revision']}, 'operator-reconcile'))['result']
        assert reconciled['state']['effect'] == 'none'
        assert operation(b)['history'][-1]['recoverySource'] == {
            **expected_source, 'action': 'configuration.operation.reconcile'}
        view = (await invoke('configuration.operation.read', {**args, 'includeRecovery': True}, 'operator-reconciled'))['result']
        assert view['recovery']['canResume']
        assert all('recoverySource' not in row for row in view['history'])
        assert 'recoverySource' not in operation(b)['state']
        assert 'recoverySource' not in b.owner['pendingConfiguration']
        await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
        b.owner['status'] = 'working'
        resume_args = {**args, 'expectedRevision': reconciled['revision']}
        result = await invoke('configuration.operation.resume', resume_args, 'operator-resume')
        accepted.append((resume_args, result['result']))
        resumed = operation(b)
        assert result['result']['state']['phase'] == 'queued'
        assert resumed['history'][-1]['recoverySource'] == {
            **expected_source, 'action': 'configuration.operation.resume'}
        assert resumed['resumptions'] == [{**expected_source,
            'workspace': b.owner['workspace'], 'effect': 'runtime_configuration', 'scope': 'session'}]
        assert resumed['request'] == saved['request'] and resumed['requestHash'] == saved['requestHash']
        assert resumed['history'][:len(saved['history'])] == saved['history']
        assert not b.app.configuration_authority.rows(b.owner['id'])
    if actor == 'child':
        await child(b.app, b.owner, tmp_path, monkeypatch, exercise)
    else:
        await exercise(lambda action, args, identity: system(b.app, b.owner, action, args, identity), None)
    b.owner['status'] = 'idle'
    await asyncio.wait_for(b.app.management.pending_config_tasks[b.owner['id']], 12)
    final = operation(b)
    assert final['state']['effect'] == 'mounted'
    assert final['request'] == saved['request'] and final['requestHash'] == saved['requestHash']
    assert final['history'][:len(saved['history'])] == saved['history']
    assert final['attempts'][0] == saved['attempts'][0] and len(final['attempts']) == 2
    assert json.loads(override_path(b.owner['runtimeSessionId']).read_text()) == plan(2)
    assert not b.owner['approvals'] and not b.app.configuration_authority.rows(b.owner['id'])
    if actor == 'system':
        await replace_mode(b, 'ask')
        duplicate = await system(b.app, b.owner, 'configuration.operation.resume', accepted[0][0], 'operator-resume')
        assert duplicate['duplicate'] and duplicate['result'] == accepted[0][1]
        assert operation(b) == final


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('actor', ['root', 'system'])
async def test_ask_cannot_borrow_operator_origin(process_boundary, actor):
    b = process_boundary
    saved = await operator_failure(b)
    await replace_mode(b, 'ask')
    invoke = system_recovery if actor == 'system' else recovery
    view = (await invoke(b, 'read', includeRecovery=True))['result']
    assert not view['recovery']['canReconcile'] and not view['recovery']['canResume']
    with pytest.raises(AppError):
        await invoke(b, 'reconcile')
    assert operation(b) == saved
    assert not override_path(b.owner['runtimeSessionId']).exists()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('actor', ['child', 'system'])
async def test_full_operator_partial_write_remains_unknown(process_boundary, tmp_path, monkeypatch, actor):
    b = process_boundary
    saved = await operator_failure(b, outcome='unknown')
    before = override_path(b.owner['runtimeSessionId']).read_bytes()
    async def exercise(invoke, principal):
        args = {'sessionId': b.owner['id'], 'operationId': saved['id']}
        reconciled = (await invoke('configuration.operation.reconcile', {
            **args, 'expectedRevision': saved['revision']}, 'operator-partial-reconcile'))['result']
        assert reconciled['state']['effect'] == 'unknown'
        view = (await invoke('configuration.operation.read', {**args, 'includeRecovery': True}, 'operator-partial-read'))['result']
        assert not view['recovery']['canResume']
        with pytest.raises((AppError, AssertionError), match='no-effect'):
            await invoke('configuration.operation.resume', {
                **args, 'expectedRevision': reconciled['revision']}, 'operator-partial-resume')
    if actor == 'child':
        await child(b.app, b.owner, tmp_path, monkeypatch, exercise)
    else:
        await exercise(lambda action, args, identity: system(b.app, b.owner, action, args, identity), None)
    assert operation(b)['attempts'] == saved['attempts']
    assert operation(b)['request'] == saved['request']
    assert override_path(b.owner['runtimeSessionId']).read_bytes() == before


async def invalidate(b, change):
    if change in {'ask', 'off-on'}:
        await replace_mode(b, 'ask')
        if change == 'off-on':
            await replace_mode(b, 'full')
    elif change == 'stop':
        # Cross the captured Stop boundary without terminating the subprocess:
        # the waiting native admission must itself reject before either write.
        b.owner['interruptionRevision'] = b.owner.get('interruptionRevision', 0) + 1
    else:
        b.owner['executionRevision'] = b.owner.get('executionRevision', 0) + 1


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('stage', ['queued', 'native-admission'])
@pytest.mark.parametrize('change', ['ask', 'off-on', 'stop', 'target'])
async def test_operator_resume_rechecks_captured_full_and_target(process_boundary, monkeypatch, stage, change):
    b = process_boundary
    saved = await operator_failure(b)
    await system_recovery(b, 'reconcile')
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
    entered, release = asyncio.Event(), asyncio.Event()
    original = b.app._app_bridge
    async def hold(operation, args, session_id, **kwargs):
        if operation == 'configuration.effect.admit':
            entered.set()
            await release.wait()
        return await original(operation, args, session_id, **kwargs)
    if stage == 'queued':
        b.owner['status'] = 'working'
    else:
        monkeypatch.setattr(b.app, '_app_bridge', hold)
    await system_recovery(b, 'resume')
    pending = b.app.management.pending_config_tasks[b.owner['id']]
    try:
        if stage == 'native-admission':
            await asyncio.wait_for(entered.wait(), 6)
        await invalidate(b, change)
    finally:
        b.owner['status'] = 'idle'
        release.set()
    await asyncio.wait_for(pending, 12)
    final = operation(b)
    assert final['state']['phase'] == 'error'
    assert final['request'] == saved['request'] and final['requestHash'] == saved['requestHash']
    if stage == 'queued':
        assert final['state']['effect'] == 'none' and final['attempts'] == saved['attempts']
    else:
        assert len(final['attempts']) == 2
        assert evidence(final['attempts'][-1])['effect'] == 'none'
    assert not override_path(b.owner['runtimeSessionId']).exists()
    view = (await system_recovery(b, 'read', includeRecovery=True))['result']
    assert not view['recovery']['canReconcile'] and not view['recovery']['canResume']
    with pytest.raises(AppError):
        await system_recovery(b, 'resume', identity='cannot-renew-resumption')
    assert operation(b) == final


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_actual_stop_cancels_operator_resume(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    await system_recovery(b, 'reconcile')
    b.owner['status'] = 'working'
    await system_recovery(b, 'resume')
    pending = b.app.management.pending_config_tasks[b.owner['id']]
    await b.app.dispatch('conversation.stop', {'sessionId': b.owner['id']})
    await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 12)
    final = operation(b)
    assert final['request'] == saved['request'] and final['attempts'] == saved['attempts']
    assert not override_path(b.owner['runtimeSessionId']).exists()
    view = (await system_recovery(b, 'read', includeRecovery=True))['result']
    assert not view['recovery']['canResume']


async def test_full_does_not_replace_revoked_original_discrete_grant(process_boundary):
    b = process_boundary
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=1)
    assert (await process_apply(b))['phase'] == 'error'
    saved = deepcopy(operation(b))
    await revoke(b.app, b.authorized)
    await replace_mode(b, 'full')
    view = (await system_recovery(b, 'read', includeRecovery=True))['result']
    assert not view['recovery']['canReconcile'] and not view['recovery']['canResume']
    with pytest.raises(AppError, match='revoked'):
        await system_recovery(b, 'reconcile')
    assert operation(b) == saved
    assert not override_path(b.owner['runtimeSessionId']).exists()
