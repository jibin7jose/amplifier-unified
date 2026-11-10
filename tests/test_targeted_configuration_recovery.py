"""Explicit Full root recovery of an idle, fenced original; no target turn."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web.configuration_recovery import receipt_path
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppError
from test_coordination_generation import agent
from test_configuration_authority import revoke
from test_full_operator_configuration_recovery import operator_failure
from test_queued_configuration_boundary import (
    operation, plan, process_apply, process_boundary,
)


async def manager(b, mode='full'):
    source = b.app._new_session({'title': 'Recovery operator'})
    source['collaborationGeneration'] = {
        'id': 'operator-generation', 'inputIds': ['operator-human'], 'terminal': False}
    b.app.state['sessions'].append(source)
    b.app._publish()
    await mode_for(b, source, mode)
    return source


async def mode_for(b, source, mode):
    snapshot = (await b.app.dispatch('access.get', {'sessionId': source['id']}))['result']
    await b.app.dispatch('access.set', {'sessionId': source['id'], 'scope': 'session',
        'mode': mode, 'expectedRevision': snapshot['revision']})


async def invoke(b, source, verb, identity=None, **extra):
    saved = operation(b)
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'], **extra}
    if verb != 'read':
        args.setdefault('expectedRevision', saved['revision'])
    return await agent(b.app, source, 'configuration.operation.' + verb, args,
        identity or 'operator-' + verb + '-' + str(saved['revision']))


async def saved_failure(b, monkeypatch):
    await b.runtime._request(b.owner['id'], 'fixture.lose_reply')
    original = asyncio.wait_for
    async def shortened(awaitable, timeout):
        return await original(awaitable, .5 if timeout == 30 else timeout)
    monkeypatch.setattr(asyncio, 'wait_for', shortened)
    assert (await process_apply(b))['phase'] == 'error'
    saved = operation(b)
    assert saved['state']['effect'] == 'unknown'
    return saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_targeted_saved_resume_never_rewrites_or_wakes(process_boundary, monkeypatch):
    b = process_boundary
    saved = await saved_failure(b, monkeypatch)
    source = await manager(b)
    assert b.owner['configurationBusy'] and b.owner['status'] == 'idle'
    settings = override_path(b.owner['runtimeSessionId']).read_bytes()
    seal = receipt_path(saved['attempts'][0]).read_bytes()
    drain = AsyncMock()
    monkeypatch.setattr(b.app.collaboration, 'drain', drain)
    before_messages = deepcopy(b.owner['messages'])
    view = (await invoke(b, source, 'read', includeRecovery=True))['result']
    assert view['nativeEffect'] == 'saved_not_mounted'
    assert view['recovery']['canReconcile'] and not view['recovery']['canResume']
    assert operation(b) == saved
    reconciled = (await invoke(b, source, 'reconcile'))['result']
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'],
        'expectedRevision': reconciled['revision']}
    accepted = await agent(b.app, source, 'configuration.operation.resume', args, 'operator-saved-resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 10)
    # Present stale mount is observed, not replaced or declared successful.
    assert operation(b)['state']['effect'] == 'saved_not_mounted'
    assert operation(b)['state']['phase'] == 'error'
    assert b.owner['configurationBusy']
    await b.runtime.stop(b.owner['id'], reason='configuration')
    assert b.owner['id'] not in b.runtime.workers
    cold = (await invoke(b, source, 'read', includeRecovery=True, commandId='operator-saved-resume'))['result']
    assert cold['command']['accepted'] and cold['command']['phase'] == 'queued'
    assert b.owner['id'] not in b.runtime.workers  # Read never warms.
    await invoke(b, source, 'resume', identity='operator-absent-resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 12)
    final = operation(b)
    assert final['state']['phase'] == 'ready' and final['state']['effect'] == 'mounted'
    assert final['request'] == saved['request'] and final['requestHash'] == saved['requestHash']
    assert final['attempts'] == saved['attempts']
    assert final['history'][:len(saved['history'])] == saved['history']
    assert final['resumptions'][0]['chain'][0]['sessionId'] == source['id']
    assert final['history'][len(saved['history'])]['recoverySource']['sessionId'] == source['id']
    assert override_path(b.owner['runtimeSessionId']).read_bytes() == settings
    assert receipt_path(saved['attempts'][0]).read_bytes() == seal
    assert b.owner['messages'] == before_messages
    assert b.owner['status'] == 'idle'
    drain.assert_not_awaited()
    duplicate = await agent(b.app, source, 'configuration.operation.resume', args, 'operator-saved-resume')
    assert duplicate['duplicate'] and duplicate['result'] == accepted['result']
    other = await manager(b)
    with pytest.raises(AppError, match='different contents'):
        await agent(b.app, other, 'configuration.operation.resume', args, 'operator-saved-resume')
    assert operation(b) == final
    public = json.dumps(cold)
    assert all(key not in public for key in ('recoverySource', 'resumptions', 'payloadHash',
        'accessRevision', 'workerPid', 'signature', '"config"'))


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('failure', ['ask', 'workspace', 'missing-operation', 'forged',
    'missing-principal', 'child', 'system', 'stop', 'assignment', 'task', 'refence'])
async def test_target_route_fails_closed(process_boundary, failure):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b, 'ask' if failure == 'ask' else 'full')
    args = {'sessionId': b.owner['id'], 'operationId': saved['id'],
        'expectedRevision': saved['revision']}
    transport = {}
    if failure == 'workspace':
        source['workspace'] += '-elsewhere'
    elif failure == 'missing-operation':
        args.pop('operationId')
    elif failure == 'forged':
        transport['_runtimeSessionId'] = b.owner['runtimeSessionId']
    elif failure == 'missing-principal':
        transport['_runtimeSessionId'] = None
    elif failure == 'child':
        transport['_childOrigin'] = {}
    elif failure == 'stop':
        source['interruptionRevision'] = 1
    elif failure == 'assignment':
        monkey = lambda owner: 'stopped'
        b.app.assignments.blocked = monkey
    elif failure == 'task':
        b.app.coordination.task = lambda sid: {'id': 'task', 'status': 'paused'}
    elif failure == 'refence':
        b.owner['configurationOperationFence'] = 'other-operation'
    with pytest.raises((AppError, ValueError)):
        if failure == 'system':
            await b.app.dispatch('configuration.operation.reconcile', args,
                origin='system', caller_session_id=source['id'])
        else:
            await agent(b.app, source, 'configuration.operation.reconcile', args, 'refused-target', **transport)
    assert operation(b) == saved
    assert not override_path(b.owner['runtimeSessionId']).exists()


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('change', ['caller-off-on', 'caller-stop', 'caller-placement',
    'original-off-on', 'target-stop', 'target-placement', 'target-refence', 'target-native', 'saved-plan', 'partial'])
async def test_saved_resume_retains_both_authority_boundaries(process_boundary, monkeypatch, change):
    b = process_boundary
    saved = await saved_failure(b, monkeypatch)
    source = await manager(b)
    await invoke(b, source, 'reconcile')
    before = override_path(b.owner['runtimeSessionId']).read_bytes()
    if change.endswith('off-on'):
        selected = source if change.startswith('caller') else b.owner
        await mode_for(b, selected, 'ask')
        if change.startswith('caller'):
            # Capture before the await below; off/on is tested at final admission.
            await mode_for(b, selected, 'full')
    if change == 'target-stop':
        b.owner['interruptionRevision'] = 1
    elif change == 'target-placement':
        b.owner['executionRevision'] = 1
    elif change == 'target-refence':
        b.owner['configurationOperationFence'] = 'other-operation'
    elif change == 'target-native':
        b.owner['runtimeSessionId'] = 'changed-native-owner'
    elif change == 'saved-plan':
        override_path(b.owner['runtimeSessionId']).write_text(json.dumps(plan(3)))
    elif change == 'partial':
        from amplifier_web.configuration_recovery import seal
        receipt_path(saved['attempts'][0]).write_text(json.dumps(seal(saved['attempts'][0], 'unknown', terminal=True)))
    if change.startswith('caller'):
        entered, release = asyncio.Event(), asyncio.Event()
        original = b.runtime.control
        async def hold(*args, **kwargs):
            if args[1] == 'configuration.inspect':
                entered.set()
                await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(b.runtime, 'control', hold)
        await invoke(b, source, 'resume')
        task = b.app.management.pending_config_tasks[b.owner['id']]
        await asyncio.wait_for(entered.wait(), 5)
        if change == 'caller-off-on':
            await mode_for(b, source, 'ask')
            await mode_for(b, source, 'full')
        elif change == 'caller-stop':
            source['interruptionRevision'] = 1
        else:
            source['executionRevision'] = 1
        release.set()
        await asyncio.wait_for(task, 10)
        assert operation(b)['state']['phase'] == 'error'
    else:
        with pytest.raises(AppError):
            await invoke(b, source, 'resume')
    assert operation(b)['attempts'] == saved['attempts']
    assert operation(b)['request'] == saved['request']
    if change != 'saved-plan':
        assert override_path(saved['attempts'][0]['nativeSessionId']).read_bytes() == before


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_exact_command_read_never_exposes_foreign_ledger(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b)
    # Neither a suggestive ID nor an unrelated receipt establishes a recovery binding.
    for identity in ('missing-resume', 'operator-apply'):
        view = (await invoke(b, source, 'read', commandId=identity))['result']
        assert view['command']['status'] == 'unconfirmed' and view['command']['accepted'] is None
    await invoke(b, source, 'reconcile', identity='retained-reconcile')
    view = (await invoke(b, source, 'read', commandId='retained-reconcile'))['result']
    assert view['command']['status'] == 'accepted'
    assert view['command']['expectedRevision'] == saved['revision']
    assert view['command']['operationId'] == saved['id']
    assert operation(b)['attempts'] == saved['attempts']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_stale_rejected_recovery_is_read_without_replay(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b)
    # Match the stable GUI command form, but only a retained binding is proof.
    identity = f"configuration-recovery-{saved['id']}-{saved['revision'] - 1}-resume"
    with pytest.raises(AppError):
        await b.app.dispatch('configuration.operation.resume', {
            'sessionId': b.owner['id'], 'operationId': saved['id'],
            'expectedRevision': saved['revision'] - 1}, command_id=identity)
    first = operation(b)
    view = (await invoke(b, source, 'read', commandId=identity))['result']
    assert view['command']['status'] == 'rejected'
    assert view['command']['errorCategory'] == 'recovery_refused'
    assert view['command']['expectedRevision'] == saved['revision'] - 1
    assert 'error' not in view['command']
    assert operation(b) == first == saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
@pytest.mark.parametrize('peer_mode', ['ask', 'full'])
async def test_recovery_cannot_launder_peer_authority(process_boundary, monkeypatch, peer_mode):
    b = process_boundary
    saved = await saved_failure(b, monkeypatch)
    source = await manager(b)
    sender = await manager(b, peer_mode)
    request = await agent(b.app, sender, 'coordination.send', {
        'sessionId': source['id'], 'text': 'Recover the original', 'mode': 'notify'}, 'recovery-peer')
    source['collaborationGeneration'] = {'id': 'peer-requested-recovery',
        'inputIds': [request['requestId']], 'terminal': False}
    if peer_mode == 'ask':
        with pytest.raises(AppError, match='peer'):
            await invoke(b, source, 'reconcile')
        assert operation(b) == saved
        return
    await invoke(b, source, 'reconcile')
    entered, release = asyncio.Event(), asyncio.Event()
    original = b.runtime.control
    async def hold(*args, **kwargs):
        if args[1] == 'configuration.inspect':
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(b.runtime, 'control', hold)
    await invoke(b, source, 'resume')
    task = b.app.management.pending_config_tasks[b.owner['id']]
    await asyncio.wait_for(entered.wait(), 5)
    retained = operation(b)['resumptions'][0]['chain']
    assert retained[1]['sessionId'] == sender['id']
    await mode_for(b, sender, 'ask')
    await mode_for(b, sender, 'full')
    release.set()
    await asyncio.wait_for(task, 10)
    assert operation(b)['state']['phase'] == 'error'
    assert operation(b)['attempts'] == saved['attempts']


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_recovery_preserves_revoked_original_grant(process_boundary):
    b = process_boundary
    await mode_for(b, b.owner, 'ask')
    # Original discrete grants remain independent of the manager's Full choice.
    from test_configuration_authority import grant
    b.authorized = await grant(b.app, ['runtime_configuration'], ['session'])
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=1)
    assert (await process_apply(b))['phase'] == 'error'
    saved = operation(b)
    source = await manager(b)
    await revoke(b.app, b.authorized)
    with pytest.raises(AppError):
        await invoke(b, source, 'reconcile')
    assert operation(b) == saved


@pytest.mark.parametrize('process_boundary', ['full'], indirect=True)
async def test_cross_owner_no_effect_recovery_holds_existing_queued_input(process_boundary):
    b = process_boundary
    saved = await operator_failure(b)
    source = await manager(b)
    await agent(b.app, source, 'coordination.send', {'sessionId': b.owner['id'],
        'text': 'Do not run incidentally during recovery', 'mode': 'queue'}, 'queued-before-recovery')
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'
    await invoke(b, source, 'reconcile')
    await b.runtime._request(b.owner['id'], 'fixture.busy', count=0)
    await invoke(b, source, 'resume')
    task = b.app.management.pending_config_tasks.get(b.owner['id'])
    if task:
        await asyncio.wait_for(task, 12)
    assert operation(b)['state']['effect'] == 'mounted'
    assert len(operation(b)['attempts']) == 2
    assert operation(b)['attempts'][0] == saved['attempts'][0]
    await b.app.collaboration.drain(b.owner['id'])
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'
    assert b.owner['status'] == 'idle'
    admitted = []
    async def input_adapter(session, args, guard, emit):
        assert not guard()
        result = b.app.collaboration.admission(session['id'], args)
        assert result['admitted'], result
        admitted.append(args['inputId'])
        return {'accepted': True}
    b.runtime.collaboration_input = input_adapter
    await agent(b.app, source, 'coordination.send', {'sessionId': b.owner['id'],
        'text': 'Genuinely fresh explicit work after recovery', 'mode': 'queue'}, 'fresh-after-recovery')
    async with asyncio.timeout(5):
        while not admitted:
            await asyncio.sleep(.01)
    assert admitted == ['fresh-after-recovery']
    assert b.app.collaboration.receipt('queued-before-recovery')['delivery'] == 'queued'