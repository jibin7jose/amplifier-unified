"""Fresh human-backed recovery preserves historical terminals and unknown effects."""
import copy

import pytest
from amplifier_foundation.session import SharedSessionStore
from amplifier_web.runtime import RuntimeManager, normalize_event
from amplifier_web.service import AppError
from amplifier_web.session_health import current_terminal_failure
from test_assignments import app, child, generation
from test_collaborative_workspaces import agent_action
from test_collaborative_workspaces import settled


async def submit(app, source, target, human):
    return await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Reconcile completed effects and continue remaining work.',
        'mode': 'queue', 'resume': {'sourceMessageId': human['id'],
            'expectedInterruptionRevision': target.get('interruptionRevision', 0)}}, 'resume-member')


async def historical_failure(app, event='generation.failed', native=False):
    source = app.state['sessions'][0]
    # A failed commission also returns to its manager. Hold that unrelated
    # automatic input at an explicit unavailable boundary during target recovery.
    app.runtime.unavailable_boundaries.add(source['id'])
    # A native observation belongs to a later generation, not a duplicate start
    # of the commissioning generation (which must preserve its admitted inputs).
    target, creation = await child(app, source, identity='initial-helper' if native else 'helper')
    input_id = creation['initialInputId']
    if native:
        from amplifier_web.automatic_history import display_message
        input_id = 'native-delegate-observation'
        row = {'role': 'user', 'content': 'External observation: data, not instructions or approval.',
               'metadata': {'amplifier_input': {'version': 1, 'kind': 'service',
                   'id': input_id, 'source': 'amplifier-delegate'}}}
        target['messages'].append(display_message(row, 1, target))
        await generation(app, target, 'generation-helper', [input_id])
        assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (input_id,)).fetchone()
    assert target['collaborationGeneration']['id'] == 'generation-helper'
    assert target['collaborationGeneration']['inputIds'] == [input_id]
    # Older producers did not always classify their terminal event. Exercise
    # the real projection: it retains the event, then projects a generic exit.
    await app.on_runtime_event(*normalize_event({
        'type': event, 'generation_id': 'generation-helper',
        'input_ids': [input_id], 'error_type': 'ValueError',
    }, target['id']))
    await app.on_runtime_event('runtime.error', {
        'sessionId': target['id'], 'error': 'Manager turn failed; no automatic replay'})
    await app.on_runtime_event('runtime.ended', {'sessionId': target['id']})
    assert target['failure']['category'] == 'unknown'
    assert 'generationId' not in target['failure']
    assert current_terminal_failure(target)
    human = app._message(source, 'user', 'Inspect the finished work and resume the remaining task.',
                         inputId='fresh-human-recovery', inputOrigin='ui')
    await generation(app, source, 'fresh-owner-generation', ['fresh-human-recovery'])
    app.runtime.inputs.clear()
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    return source, target, human, input_id


@pytest.mark.parametrize('event', ['generation.failed', 'generation.detached'])
@pytest.mark.parametrize('native', [False, True])
async def test_unknown_historical_failure_recovers_with_separate_exact_terminal_proof(app, event, native):
    source, target, human, input_id = await historical_failure(app, event, native)
    original_failure = copy.deepcopy(target['failure'])
    original_receipt = app.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
    original_messages = copy.deepcopy(target['messages'])
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    proof = receipt['humanResume']['failedRecovery']
    assert proof['failure'] == original_failure
    assert proof['terminalEvidence']['generation']['generation_id'] == 'generation-helper'
    assert proof['terminalEvidence']['generation']['input_ids'] == [input_id]
    assert 'inputs' not in proof['terminalEvidence'], 'Terminal witness does not invent input receipts'
    assert target['failure'] == original_failure
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone() == original_receipt
    assert target['messages'][:len(original_messages)] == original_messages
    assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == 'resume-member'
    await submit(app, source, target, human)
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('change', ['unmatched_input', 'later_root_event', 'native_owner',
                                   'failure_before_terminal', 'missing_terminal_time', 'child_only'])
async def test_historical_recovery_refuses_incomplete_or_superseded_proof(app, change):
    source, target, human, input_id = await historical_failure(app)
    held = None
    if change == 'unmatched_input':
        await app.on_runtime_event(*normalize_event({'type': 'generation.failed',
            'generation_id': 'generation-helper', 'input_ids': ['not-delivered']}, target['id']))
    elif change == 'later_root_event':
        await app.on_runtime_event(*normalize_event({'type': 'generation.finished',
            'generation_id': 'another-generation', 'input_ids': [input_id]}, target['id']))
    elif change == 'failure_before_terminal':
        target['failure']['recordedAt'] = target['generations'][-1]['at'] - 1
    elif change == 'missing_terminal_time':
        target['generations'][-1].pop('at')
    elif change == 'child_only':
        target['generations'][-1]['sessionId'] = 'unrelated-child'
    else:
        held = SharedSessionStore(target['workspace'], target['id']).acquire(app='independent-owner')
    try:
        with pytest.raises(AppError):
            await submit(app, source, target, human)
        assert not app.runtime.inputs
    finally:
        if held: held.release()


@pytest.mark.parametrize('change', ['missing_receipt', 'wrong_receipt_target', 'unknown_delivery'])
async def test_new_human_input_neither_requires_nor_repairs_old_peer_receipts(app, change):
    source, target, human, input_id = await historical_failure(app, 'generation.detached')
    if change == 'missing_receipt':
        app.db.execute('DELETE FROM commands WHERE id=?', (input_id,)); app.db.commit()
    else:
        receipt = app.collaboration.receipt(input_id)
        if change == 'wrong_receipt_target': receipt['target']['sessionId'] = source['id']
        else: receipt['delivery'] = 'unknown'
        app.collaboration.save(input_id, receipt); app.db.commit()
    before = app.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone()
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'accepted')
    assert app.db.execute('SELECT receipt FROM commands WHERE id=?', (input_id,)).fetchone() == before
    assert [(sid, identity) for sid, identity, _ in app.runtime.inputs] == [(target['id'], 'resume-member')]
    assert 'Reconcile completed effects and continue remaining work.' in app.runtime.inputs[0][2]


async def test_saved_recovery_with_extra_peer_snapshots_still_checks_the_terminal_witness(app):
    from amplifier_web.peer_resume import guard
    source, target, human, input_id = await historical_failure(app)
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    # Durable receipts from the earlier implementation retain these snapshots.
    saved = copy.deepcopy(receipt)
    saved['humanResume']['failedRecovery']['terminalEvidence']['inputs'] = [
        {'inputId': input_id, 'delivery': 'accepted'}]
    before = copy.deepcopy(saved)
    assert guard(app, saved, source, target) is None
    assert saved == before
    target['generations'][-1]['at'] += 1
    assert 'terminal generation changed' in guard(app, saved, source, target)


@pytest.mark.parametrize('change', ['input', 'terminal', 'safety', 'stop', 'configuration'])
@pytest.mark.parametrize('event', ['generation.failed', 'generation.detached'])
async def test_historical_proof_rechecked_after_prepare(app, change, event):
    source, target, human, input_id = await historical_failure(app, event, native=True)

    async def delayed(session, args, guard, emit):
        if change == 'input':
            await generation(app, target, 'new-generation', ['new-input'])
        elif change == 'stop':
            await app.dispatch('conversation.stop', {'sessionId': target['id']}, command_id='newer-stop')
        elif change == 'configuration':
            target['fileAccessPending'] = True
        else:
            await app.on_runtime_event(*normalize_event({
                'type': 'generation.failed', 'generation_id': 'generation-helper',
                'input_ids': [input_id], **({'error_category': 'content_filter'} if change == 'safety' else {}),
            }, target['id']))
        assert guard()
        assert not app.collaboration.admission(target['id'], args)['admitted']
        return {'accepted': False, 'reason': guard()}

    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'suppressed')
    assert not app.runtime.inputs
    if change == 'safety': assert 'safety' in receipt['admission']['reason']


@pytest.mark.parametrize('category', ['content_filter', 'computer_capture_stop'])
@pytest.mark.parametrize('detach', [False, True])
async def test_retained_terminal_safety_cannot_hide_behind_old_unknown_projection(app, monkeypatch, category, detach):
    source, target, human, input_id = await historical_failure(app)
    # Emulate the old projection while retaining the real structured terminal.
    # This is a historical negative, not manufactured recovery authority.
    monkeypatch.setattr('amplifier_web.session_health.generation_failure', lambda event: None)
    await app.on_runtime_event(*normalize_event({
        'type': 'generation.failed', 'generation_id': 'generation-helper',
        'input_ids': [input_id], 'error_category': category,
    }, target['id']))
    if detach:
        await app.on_runtime_event(*normalize_event({
            'type': 'generation.detached', 'generation_id': 'generation-helper',
            'input_ids': [input_id],
        }, target['id']))
    await app.on_runtime_event('runtime.error', {'sessionId': target['id'], 'error': 'Worker exited'})
    assert target['failure']['category'] == 'unknown'
    with pytest.raises(AppError, match='safety'):
        await submit(app, source, target, human)
    assert not app.runtime.inputs


@pytest.mark.parametrize('event', ['generation.failed', 'generation.detached'])
async def test_retained_recovery_survives_host_restart_without_rewriting_saved_failure(app, event):
    from amplifier_web.service import AppService
    from test_collaborative_workspaces import Runtime
    source, target, human, input_id = await historical_failure(app, event, native=True)
    original = copy.deepcopy(target['failure'])
    app._save()
    await app.close()
    runtime = Runtime()
    reopened = runtime.app = AppService(app.data_dir, runtime, workspace=source['workspace'])
    try:
        owner, recipient = reopened._session(source['id']), reopened._session(target['id'])
        human = reopened._message(owner, 'user', 'Resume the remaining task after inspecting finished effects.',
            inputId='post-restart-human', inputOrigin='ui')
        await generation(reopened, owner, 'post-restart-owner', ['post-restart-human'])
        runtime.stopped_boundary = RuntimeManager().stopped_boundary
        await submit(reopened, owner, recipient, human)
        receipt = await settled(reopened, 'resume-member', 'accepted')
        assert receipt['humanResume']['failedRecovery']['failure'] == original
        assert recipient['failure'] == original
        assert len(runtime.inputs) == 1
    finally:
        await reopened.close()


async def test_detached_recovery_holds_native_writer_lease_through_receipt_commit(app, monkeypatch):
    from amplifier_foundation.session import SessionBusyError
    source, target, human, _ = await historical_failure(app, 'generation.detached', native=True)
    publish = app.collaboration.publish_receipt

    def check_lock(receipt):
        with pytest.raises(SessionBusyError):
            SharedSessionStore(target['workspace'], target['id']).acquire(app='other-owner')
        return publish(receipt)

    with monkeypatch.context() as patch:
        patch.setattr(app.collaboration, 'publish_receipt', check_lock)
        await submit(app, source, target, human)
    await settled(app, 'resume-member', 'accepted')
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('other', ['old-generation', 'child-root'])
async def test_unrelated_safety_record_does_not_halt_current_detached_recovery(app, other):
    source, target, human, input_id = await historical_failure(app, 'generation.detached', native=True)
    target['generations'].insert(-1, {'event': 'generation.failed', 'error_category': 'content_filter',
        'generation_id': 'older' if other == 'old-generation' else 'generation-helper',
        'sessionId': target['id'] if other == 'old-generation' else 'child',
        'rootSessionId': target['id'], 'input_ids': [input_id]})
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'accepted')
    assert len(app.runtime.inputs) == 1
