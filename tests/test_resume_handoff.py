"""Three-root handoff through the real service and authenticated bridge fixtures."""
import asyncio
import copy
import time

import pytest

from amplifier_web.service import AppError, AppService
from test_peer_resume import app, paused_member
from test_assignments import generation, agent_action
from test_collaborative_workspaces import settled, Runtime


async def roots(app, *, cross_workspace=False, interrupted=False):
    if interrupted:
        from test_interrupted_peer_recovery import interrupted_member
        owner, member, _, _, _ = await interrupted_member(app)
    else:
        owner, member, _ = await paused_member(app)
    origin = next(row for row in app.state['sessions'] if row['id'] not in {owner['id'], member['id']})
    if cross_workspace:
        other = app.data_dir.parent / 'other-workspace'
        other.mkdir(exist_ok=True)
        origin['workspace'] = str(other)
        origin['workingDirectory'] = str(other)
    # Different origin and commissioning-owner assignments are intentional.
    app.assignments.ensure(origin, 'origin-assignment')
    owner['status'] = 'idle'
    owner['collaborationGeneration']['terminal'] = True
    human = app._message(origin, 'user', 'Resume the exact interrupted member; reconcile old effects.',
                         inputId='origin-human', inputOrigin='ui')
    await generation(app, origin, 'origin-generation', [human['inputId']])
    app.runtime.inputs.clear()
    return origin, owner, member, human


async def issue(app, origin, owner, member, human, identity='handoff', **patch):
    return await agent_action(app, origin, 'coordination.send', {
        'sessionId': owner['id'], 'text': 'Assess the original human request for this member.',
        'mode': 'queue', 'resumeHandoff': {'sourceMessageId': human['id'],
            'memberSessionId': member['id'], 'expectedInterruptionRevision': member.get('interruptionRevision', 0)},
        **patch}, identity)


async def receive(app, owner, identities=('handoff',)):
    for identity in identities:
        owner['status'] = 'idle'
        await app.collaboration.drain(owner['id'])
        await settled(app, identity, 'accepted')
    await generation(app, owner, 'owner-handoff', list(identities))
    app.runtime.inputs.clear()


async def consume(app, owner, member, identity='member-resume', handoff='handoff', inputs=None, **patch):
    return await agent_action(app, owner, 'coordination.send', {
        'sessionId': member['id'], 'text': 'Reconcile saved effects then continue within the human instruction.',
        'mode': 'queue', 'resume': {'sourceRequestId': handoff,
            'expectedInterruptionRevision': member.get('interruptionRevision', 0)}, **patch}, identity, inputs=inputs)


@pytest.mark.parametrize('interrupted', [False, True])
async def test_handoff_preserves_ownership_task_and_nonhuman_input(app, interrupted):
    origin, owner, member, human = await roots(app, interrupted=interrupted)
    before = copy.deepcopy(member['task']) if member.get('task') else None
    assignments = [copy.deepcopy(app.assignments.current(row)) for row in (origin, owner, member)]
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    context = await agent_action(app, owner, 'coordination.context', {'sessionId': owner['id']}, 'owner-context')
    assert context['result']['humanInput'] is None
    # Normal source completion does not turn peer input into human input or
    # require keeping the original model call alive.
    origin['collaborationGeneration']['terminal'] = True
    await consume(app, owner, member)
    receipt = await settled(app, 'member-resume', 'accepted')
    assert receipt['humanResume']['handoffRequestId'] == 'handoff'
    if interrupted:
        assert receipt['humanResume']['interruptedRecovery']['workerAbsent']
    assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == 'member-resume'
    message = next(m for m in member['messages'] if m.get('inputId') == 'member-resume')
    assert message['inputOrigin'] == 'peer' and message['peerEnvelope']['humanResume']
    assert (member.get('task') if before is not None else None) == before
    assert [app.assignments.current(row) for row in (origin, owner, member)] == assignments
    assert app.collaboration.receipt('handoff')['resumeHandoff']['consumption']['requestId'] == 'member-resume'
    await consume(app, owner, member)
    assert len(app.runtime.inputs) == 1


async def test_cross_workspace_human_origin_cannot_issue_member_resume_scope(app):
    origin, owner, member, human = await roots(app, cross_workspace=True)
    with pytest.raises(AppError, match='same workspace'):
        await issue(app, origin, owner, member, human)
    assert not app.runtime.inputs
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='handoff'").fetchone()


@pytest.mark.parametrize('invalid', ['peer', 'generated', 'scheduled', 'question', 'historical',
    'unrelated', 'missing_transport', 'wrong_native', 'wrong_owner', 'wrong_member', 'stop_after_human',
    'notify', 'steer', 'recursive'])
async def test_only_direct_current_native_human_can_issue_exact_scope(app, invalid):
    origin, owner, member, human = await roots(app)
    patch = {}
    if invalid in {'peer', 'generated'}: human['inputOrigin'] = invalid
    elif invalid == 'scheduled': human['scheduledRunId'] = 'scheduled'
    elif invalid == 'question': human['questionId'] = 'question'
    elif invalid == 'historical':
        app._message(origin, 'user', 'Do not resume.', inputId='new-human', inputOrigin='ui')
    elif invalid == 'unrelated': await generation(app, origin, 'unrelated-generation', ['unrelated'])
    elif invalid == 'wrong_owner':
        assignment = app.assignments.current(owner)
        assignment['ownerSessionId'] = member['id']; app.assignments.save(assignment)
    elif invalid == 'wrong_member': member['assignmentId'] = 'other'
    elif invalid == 'stop_after_human': human['createdAt'] = member['lastInterruption']['at'] - 1
    elif invalid in {'notify', 'steer'}: patch['mode'] = invalid
    elif invalid == 'recursive':
        await issue(app, origin, owner, member, human)
        await receive(app, owner)
        origin = owner
    if invalid in {'missing_transport', 'wrong_native'}:
        with pytest.raises(AppError):
            await app.app_bridge('dispatch', {'action': 'coordination.send',
                'args': {'sessionId': owner['id'], 'text': 'forged',
                    'resumeHandoff': {'sourceMessageId': human['id'], 'memberSessionId': member['id'],
                        'expectedInterruptionRevision': member['interruptionRevision']}},
                '_runtimeSessionId': 'foreign' if invalid == 'wrong_native' else origin['id'],
                '_generationId': origin['collaborationGeneration']['id'],
                '_inputBindings': []}, origin['id'])
    else:
        with pytest.raises(AppError):
            await issue(app, origin, owner, member, human, identity='invalid', **patch)
    assert not app.runtime.inputs
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='invalid'").fetchone()


@pytest.mark.parametrize('invalid', ['unknown', 'submitting', 'queued', 'missing_native_input',
    'unrelated_generation', 'wrong_member', 'unscoped_old_message'])
async def test_consumer_needs_exact_positively_admitted_handoff(app, invalid):
    origin, owner, member, human = await roots(app)
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    inputs = None
    if invalid in {'unknown', 'submitting', 'queued'}:
        request = app.collaboration.receipt('handoff')
        request['delivery'] = invalid; app.collaboration.save('handoff', request); app.db.commit()
    elif invalid == 'missing_native_input': inputs = ['not-the-handoff']
    elif invalid == 'unrelated_generation': await generation(app, owner, 'later', ['unrelated'])
    elif invalid == 'wrong_member': member = origin
    else:
        request = app.collaboration.receipt('handoff')
        request.pop('resumeHandoff'); app.collaboration.save('handoff', request); app.db.commit()
    with pytest.raises(AppError):
        await consume(app, owner, member, inputs=inputs)
    assert not app.runtime.inputs


def change_boundary(app, kind, row):
    if kind == 'stop': row['interruptionRevision'] = row.get('interruptionRevision', 0) + 1
    elif kind == 'human': app._message(row, 'user', 'Wait, changed instruction.', inputOrigin='ui')
    elif kind == 'task': row['task'] = {'id': 'changed-task', 'revision': 1, 'status': 'active'}
    elif kind == 'access': row['fileAccessPending'] = True
    elif kind == 'placement': row['executionRevision'] = row.get('executionRevision', 0) + 1
    elif kind == 'workspace': row['workspace'] = str(app.data_dir.parent / 'moved-workspace')
    elif kind == 'config': app.state['configurationRevision'] = app.state.get('configurationRevision', 0) + 1
    else:
        value = app.assignments.current(row); value['revision'] += 1; app.assignments.save(value)


@pytest.mark.parametrize('participant', [0, 1, 2])
@pytest.mark.parametrize('kind', ['stop', 'human', 'task', 'access', 'placement', 'workspace', 'config', 'assignment'])
@pytest.mark.parametrize('phase', ['consume', 'final_admission'])
async def test_all_three_boundaries_rechecked_through_preparation(app, participant, kind, phase):
    rows = await roots(app)
    origin, owner, member, human = rows
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    if phase == 'consume':
        change_boundary(app, kind, rows[participant])
        with pytest.raises(AppError):
            await consume(app, owner, member)
    else:
        async def preparing(session, args, guard, emit):
            change_boundary(app, kind, rows[participant])
            assert guard()
            allowed = app.collaboration.admission(session['id'], args)
            assert not allowed['admitted']
            return {'accepted': False, 'effect': 'none', 'reason': allowed['reason']}
        app.runtime.collaboration_input = preparing
        await consume(app, owner, member)
        await settled(app, 'member-resume', 'suppressed')
    assert not app.runtime.inputs


@pytest.mark.parametrize('when', ['before_consume', 'after_owner_handled', 'submitting', 'expiry',
                                 'member_expiry', 'submitting_after_owner_handled'])
async def test_cancellation_and_original_deadline_fence_unadmitted_consumption(app, when):
    origin, owner, member, human = await roots(app)
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    if when in {'after_owner_handled', 'submitting_after_owner_handled'}:
        request = app.collaboration.receipt('handoff')
        request['response'] = {'status': 'sealed', 'kind': 'result', 'outcome': 'success'}
        app.collaboration.save('handoff', request); app.db.commit()
    async def revoke():
        return await agent_action(app, origin, 'coordination.cancel',
            {'sessionId': origin['id'], 'requestId': 'handoff'}, 'revoke-handoff')
    if when in {'before_consume', 'after_owner_handled'}:
        await revoke()
        assert app.collaboration.receipt('handoff')['cancellation']
        with pytest.raises(AppError): await consume(app, owner, member)
    else:
        async def preparing(session, args, guard, emit):
            if when in {'expiry', 'member_expiry'}:
                request = app.collaboration.receipt('handoff' if when == 'expiry' else args['inputId'])
                request['expiresAt'] = time.time() - 1
                app.collaboration.save(request['requestId'], request); app.db.commit()
            else: await revoke()
            assert guard()
            return {'accepted': False, 'effect': 'none', 'reason': guard()}
        app.runtime.collaboration_input = preparing
        await consume(app, owner, member)
        await settled(app, 'member-resume', 'suppressed')
    assert not app.runtime.inputs


async def test_alternate_handoffs_share_exactly_once_fence_and_unknown_is_never_replayed(app):
    origin, owner, member, human = await roots(app)
    await issue(app, origin, owner, member, human)
    await issue(app, origin, owner, member, human, identity='alternate-handoff')
    await receive(app, owner, ('handoff', 'alternate-handoff'))
    app.runtime.fail = True
    results = await asyncio.gather(consume(app, owner, member),
        consume(app, owner, member, identity='alternate-resume', handoff='alternate-handoff'),
        return_exceptions=True)
    assert sum(isinstance(row, AppError) for row in results) == 1
    admitted = next(row for row in results if not isinstance(row, Exception))
    identity = admitted['requestId']
    await settled(app, identity, 'unknown')
    assert len(app.runtime.inputs) == 1
    before = copy.deepcopy(app.collaboration.receipt(identity))
    app._save(); await app.close()
    other = AppService(app.data_dir, Runtime(), workspace=owner['workspace'])
    other.runtime.app = other
    try:
        assert other.collaboration.receipt(identity) == before
        assert not other.collaboration.fair_rows(member['id'])
        target = other._session(member['id'])
        saved_owner = other._session(owner['id'])
        await generation(other, saved_owner, 'new-after-restart', ['handoff', 'alternate-handoff'])
        with pytest.raises(AppError):
            await consume(other, saved_owner, target, 'never-replay', handoff='alternate-handoff')
        assert not other.runtime.inputs
    finally:
        await other.close()


@pytest.mark.parametrize('operation', ['issue', 'consume'])
@pytest.mark.parametrize('failure', ['precommit', 'lost_ack'])
async def test_handoff_transactions_rollback_or_drain_exact_committed_input(app, monkeypatch, operation, failure):
    origin, owner, member, human = await roots(app)
    if operation == 'consume':
        await issue(app, origin, owner, member, human)
        await receive(app, owner)
    identity = 'handoff' if operation == 'issue' else 'member-resume'
    target = owner if operation == 'issue' else member
    before = copy.deepcopy(target['messages'])
    publish = app.collaboration.publish_receipt
    def failing(receipt):
        if receipt.get('requestId') != identity:
            return publish(receipt)
        if failure == 'lost_ack':
            publish(receipt)
        raise RuntimeError('Injected commit/ack failure')
    with monkeypatch.context() as patch:
        patch.setattr(app.collaboration, 'publish_receipt', failing)
        with pytest.raises(RuntimeError, match='Injected'):
            if operation == 'issue': await issue(app, origin, owner, member, human)
            else: await consume(app, owner, member)
    if failure == 'precommit':
        assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (identity,)).fetchone()
        assert target['messages'] == before and not app.runtime.inputs
        if operation == 'consume':
            assert app.collaboration.receipt('handoff')['resumeHandoff']['consumption']['status'] == 'available'
    else:
        await settled(app, identity, 'accepted')
        assert len(app.runtime.inputs) == 1 and app.runtime.inputs[0][1] == identity
        if operation == 'issue': await issue(app, origin, owner, member, human)
        else: await consume(app, owner, member)
        assert len(app.runtime.inputs) == 1


async def test_unused_origin_witness_survives_restart_without_borrowing_new_generation(app):
    origin, owner, member, human = await roots(app)
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    app._save(); await app.close()
    other = AppService(app.data_dir, Runtime(), workspace=owner['workspace'])
    other.runtime.app = other
    try:
        saved_owner, saved_member = other._session(owner['id']), other._session(member['id'])
        await generation(other, saved_owner, 'fresh-owner-after-restart', ['handoff'])
        await consume(other, saved_owner, saved_member)
        await settled(other, 'member-resume', 'accepted')
        assert len(other.runtime.inputs) == 1
        assert other.collaboration.receipt('handoff')['resumeHandoff']['sourceWitness']['generationId'] == 'origin-generation'
    finally:
        await other.close()


@pytest.mark.parametrize('participant', [0, 1, 2])
async def test_saved_access_revision_change_without_pending_flag_invalidates_scope(app, participant):
    rows = await roots(app)
    origin, owner, member, human = rows
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    row = rows[participant]
    await app.dispatch('access.set', {'sessionId': row['id'], 'scope': 'session', 'mode': 'full',
        'expectedRevision': app.access_mode.snapshot(row)['revision']},
                       command_id='new-access')
    row['fileAccessPending'] = False
    with pytest.raises(AppError): await consume(app, owner, member)
    assert not app.runtime.inputs


@pytest.mark.parametrize('phase', ['owner', 'member'])
@pytest.mark.parametrize('ack', ['exact', 'later_edit', 'wrong_bundle', 'missing'])
async def test_saved_next_message_application_normalizes_only_exact_ack(app, phase, ack):
    origin, owner, member, human = await roots(app)
    row = owner if phase == 'owner' else member
    row.update(nextMessageRevision=3, appliedConfigurationRevision=2,
               pendingBundleSelection={'bundle': 'saved-choice'})
    deliver = app.runtime.collaboration_input
    async def preparing(session, args, guard, emit):
        if session['id'] == row['id']:
            if ack != 'missing':
                await emit('runtime.nextMessageApplied', {'sessionId': row['id'], 'revision': 3,
                                                         'bundle': 'saved-choice'})
                assert row['appliedConfigurationRevision'] == 3
                assert not row.get('pendingBundleSelection')
            else:
                row['bundle'] = 'saved-choice'
                row.pop('pendingBundleSelection', None)
            if ack == 'wrong_bundle': row['bundle'] = 'wrong-choice'
            if ack == 'later_edit': row['nextMessageRevision'] = 4
            if ack != 'exact':
                assert guard()
                return {'accepted': False, 'effect': 'none', 'reason': guard()}
        return await deliver(session, args, guard, emit)
    app.runtime.collaboration_input = preparing
    await issue(app, origin, owner, member, human)
    if phase == 'owner' and ack != 'exact':
        await settled(app, 'handoff', 'suppressed')
        assert not app.runtime.inputs
        return
    await receive(app, owner)
    await consume(app, owner, member)
    await settled(app, 'member-resume', 'accepted' if ack == 'exact' else 'suppressed')
    assert len(app.runtime.inputs) == (1 if ack == 'exact' else 0)


async def test_long_human_instruction_never_drops_tail_restrictions(app):
    origin, owner, member, human = await roots(app)
    human['text'] = 'Resume only as scoped. ' * 220 + 'IMPORTANT: keep this member stopped.'
    with pytest.raises(AppError, match='complete human instruction exceeds'):
        await issue(app, origin, owner, member, human)
    assert not app.runtime.inputs
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='handoff'").fetchone()


async def test_auto_handoff_and_owner_completion_before_member_admission(app):
    origin, owner, member, human = await roots(app)
    owner['status'] = 'working'
    await issue(app, origin, owner, member, human, mode='auto')
    await settled(app, 'handoff', 'accepted')
    await generation(app, owner, 'owner-handoff', ['handoff'])
    app.runtime.inputs.clear()
    deliver = app.runtime.collaboration_input
    async def preparing(session, args, guard, emit):
        owner['collaborationGeneration']['terminal'] = True
        return await deliver(session, args, guard, emit)
    app.runtime.collaboration_input = preparing
    await consume(app, owner, member, mode='auto')
    await settled(app, 'member-resume', 'accepted')
    assert len(app.runtime.inputs) == 1


async def test_peer_only_owner_cannot_mint_another_distinct_handoff_or_lend_it_to_child(app):
    origin, owner, member, human = await roots(app)
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    # A new distinct receiving owner/member ensures forwarding fails on human
    # provenance, not merely on identical endpoint rejection.
    other_owner, other_member = [app._new_session({'title': title}) for title in ('Other owner', 'Other member')]
    app.state['sessions'].extend([other_owner, other_member])
    assignment = app.assignments.ensure(other_owner, 'other-assignment')
    assignment['members'][other_member['id']] = {'status': 'bound', 'role': 'member'}
    other_member['assignmentId'] = assignment['id']; app.assignments.save(assignment)
    with pytest.raises(AppError, match='direct human'):
        await issue(app, owner, other_owner, other_member, human, identity='recursive')
    with pytest.raises(AppError, match='child cannot borrow'):
        await app.app_bridge('dispatch', {'action': 'coordination.send',
            'args': {'sessionId': member['id'], 'text': 'child must not consume',
                'resume': {'sourceRequestId': 'handoff',
                    'expectedInterruptionRevision': member['interruptionRevision']}},
            '_runtimeSessionId': 'actual-native-child',
            '_generationId': owner['collaborationGeneration']['id'],
            '_inputBindings': [{'inputId': 'handoff'}]}, owner['id'])
    assert app.collaboration.receipt('handoff')['resumeHandoff']['consumption']['status'] == 'available'
    assert not app.runtime.inputs


@pytest.mark.parametrize('block', ['safety_retained_detach', 'native_owner', 'unterminated'])
async def test_handoff_does_not_relax_interrupted_native_stopped_boundary(app, block):
    from amplifier_foundation.session import SharedSessionStore
    origin, owner, member, human = await roots(app, interrupted=True)
    await issue(app, origin, owner, member, human)
    await receive(app, owner)
    held = None
    if block == 'safety_retained_detach':
        member['generations'].append({'sessionId': member['id'], 'rootSessionId': member['id'],
            'generation_id': member['collaborationGeneration']['id'], 'event': 'generation.failed',
            'error_category': 'content_filter'})
    elif block == 'unterminated': member['collaborationGeneration']['terminal'] = False
    else: held = SharedSessionStore(member['workspace'], member['id']).acquire(app='independent-writer')
    try:
        with pytest.raises(AppError): await consume(app, owner, member)
        assert not app.runtime.inputs
        assert app.collaboration.receipt('handoff')['resumeHandoff']['consumption']['status'] == 'available'
    finally:
        if held: held.release()