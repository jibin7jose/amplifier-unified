"""A manager may reconcile a settled interrupted worker after fresh user input."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_foundation.session import SharedSessionStore, SessionBusyError
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppError
from test_peer_resume import app, arguments, submit
from test_assignments import child, generation
from test_collaborative_workspaces import agent_action
from test_collaborative_workspaces import settled


async def interrupted_member(app):
    source = app.state['sessions'][0]
    target, creation = await child(app, source)
    active = target['collaborationGeneration']
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.detached', 'generation_id': active['id'],
        'input_ids': active.get('inputIds', []),
    }, target['id']))
    await app.on_runtime_event('runtime.ended', {
        'sessionId': target['id'], 'status': 'interrupted', 'reason': 'shutdown'})
    assert target['status'] == 'interrupted'
    assert target['collaborationGeneration']['terminal']
    human = app._message(source, 'user', 'Resume the worker after the update; reconcile saved results first.',
                         inputId='human-resume', inputOrigin='ui')
    await generation(app, source, 'resume-generation', ['human-resume'])
    target.setdefault('interruptionRevision', 0)
    runtime = RuntimeManager()
    app.runtime.stopped_boundary = runtime.stopped_boundary
    app.runtime.inputs.clear()
    return source, target, human, runtime, creation['initialInputId']


@pytest.mark.parametrize('cold_ready', [False, True])
async def test_settled_interruption_resumes_once_and_preserves_prior_effects(app, monkeypatch, cold_ready):
    source, target, human, _, initial = await interrupted_member(app)
    target['task'] = {'id': 'saved-task', 'revision': 7, 'status': 'blocked',
                      'operationIds': ['unknown-effect']}
    before = copy.deepcopy(target['task'])
    old = app.collaboration.receipt(initial)
    old['delivery'] = 'unknown'
    app.collaboration.save(initial, old)
    app.db.commit()
    if cold_ready:
        deliver = app.runtime.collaboration_input

        async def prepare_then_deliver(session, args, guard, emit):
            await emit('runtime.status', {'sessionId': session['id'], 'status': 'ready'})
            return await deliver(session, args, guard, emit)

        app.runtime.collaboration_input = prepare_then_deliver
    old_receipt = copy.deepcopy(app.collaboration.receipt(initial))
    publish = app.collaboration.publish_receipt

    def check_lock(receipt):
        with pytest.raises(SessionBusyError):
            SharedSessionStore(target['workspace'], target['id']).acquire(app='another-owner')
        return publish(receipt)

    with monkeypatch.context() as patch:
        patch.setattr(app.collaboration, 'publish_receipt', check_lock)
        await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    proof = receipt['humanResume']['interruptedRecovery']
    assert proof['workerAbsent'] and not proof['inputsReplayed']
    assert proof['generation']['terminal']
    assert target['task'] == before
    assert app.collaboration.receipt(initial) == old_receipt
    assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == 'resume-member'
    await submit(app, source, target, human)
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('block', ['native_owner', 'live_process', 'preparing', 'child',
    'unterminated', 'missing_generation', 'configuration', 'safety', 'capture', 'stopping'])
async def test_interrupted_resume_still_requires_a_proven_stopped_boundary(app, block):
    source, target, human, runtime, _ = await interrupted_member(app)
    held = None
    if block == 'native_owner':
        held = SharedSessionStore(target['workspace'], target['id']).acquire(app='independent-owner')
    elif block == 'live_process':
        runtime.workers[target['id']] = {'process': SimpleNamespace(returncode=None)}
    elif block == 'preparing': runtime._preparations[target['id']] = object()
    elif block == 'child': target['workers'] = [{'status': 'running'}]
    elif block == 'unterminated': target['collaborationGeneration']['terminal'] = False
    elif block == 'missing_generation': target['collaborationGeneration'] = {}
    elif block == 'configuration': target['configurationBusy'] = True
    elif block == 'stopping': target['status'] = 'stopping'
    else: target['failure'] = {'category': 'content_filter' if block == 'safety' else 'computer_capture_stop'}
    try:
        with pytest.raises(AppError):
            await submit(app, source, target, human)
        assert not app.runtime.inputs
        assert not app.db.execute("SELECT 1 FROM commands WHERE id='resume-member'").fetchone()
    finally:
        if held: held.release()


@pytest.mark.parametrize('change', ['generation', 'failure', 'stop', 'human'])
async def test_interrupted_recovery_rechecks_before_delivery(app, change):
    source, target, human, _, _ = await interrupted_member(app)

    async def delayed(session, args, guard, emit):
        if change == 'generation': target['collaborationGeneration']['id'] = 'new-generation'
        elif change == 'failure': target['failure'] = {'category': 'authentication'}
        elif change == 'stop': target['interruptionRevision'] += 1
        else: app._message(source, 'user', 'Wait, keep it paused.', inputOrigin='ui')
        assert guard()
        assert not app.collaboration.admission(target['id'], args)['admitted']
        return {'accepted': False, 'reason': guard()}

    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'suppressed')
    assert not app.runtime.inputs


async def test_ordinary_peer_input_cannot_resume_interrupted_work(app):
    source, target, human, _, _ = await interrupted_member(app)
    args = arguments(target, human)
    args.pop('resume')
    await agent_action(app, source, 'coordination.send', args, 'ordinary-peer-input')
    old = copy.deepcopy(await settled(app, 'ordinary-peer-input', 'suppressed'))
    assert not app.runtime.inputs
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'accepted')
    assert app.collaboration.receipt('ordinary-peer-input') == old
    assert [item[1] for item in app.runtime.inputs] == ['resume-member']
