"""An actual question answer is fresh input, never permission or a replay."""
import copy
import json

import pytest

from amplifier_web.service import AppError
from test_assignments import app, agent_action, generation, until
from test_collaborative_workspaces import settled
from test_peer_resume import paused_member, submit


async def answered_member(app, *, next_bundle=False, no_op_bundle=False):
    source, target, _ = await paused_member(app)
    if next_bundle:
        from amplifier_web.management import Management
        app.management = Management(app)
        target.update(status='idle', bundle='anchors',
                      task={'id': 'paused-report', 'revision': 2, 'status': 'paused',
                            'maxTurns': 5, 'goalState': {'turns_used': 1}})
        await app.dispatch('conversation.configure', {'sessionId': target['id'],
            'expectedRevision': 0, 'bundle': 'anchors' if no_op_bundle else 'next-report'},
            command_id='saved-before-answer')
        if no_op_bundle:
            assert not target.get('pendingBundleSelection'), 'Fixture needs an actual idle no-op saved choice'
    q = (await agent_action(app, source, 'question.create', {
        'sessionId': source['id'], 'prompt': 'Resume the unfinished report?',
        'required': True, 'dependency': 'Continue the existing report task',
        'options': [{'id': 'resume', 'label': 'Resume the unfinished report'}],
    }, 'ask-resume'))['result']
    args = {'sessionId': source['id'], 'id': q['id'], 'expectedRevision': 1,
            'optionId': 'resume'}
    await app.dispatch('question.answer', args, command_id='answer-resume')
    human = next(m for m in source['messages'] if m.get('questionId') == q['id'])
    await generation(app, source, 'answer-generation', [human['inputId']])
    app.runtime.inputs.clear()
    return source, target, human, args


async def context(app, source):
    return (await agent_action(app, source, 'coordination.context',
        {'sessionId': source['id']}, 'answer-context'))['result']['humanInput']


async def test_exact_ui_answer_drives_existing_resume_once(app):
    from amplifier_web import peer_resume
    print('peerResumeSource=' + peer_resume.__file__)
    source, target, human, args = await answered_member(app)
    target['task'] = {'id': 'unfinished', 'revision': 7, 'status': 'paused',
                      'maxTurns': 8, 'goalState': {'turns_used': 3},
                      'operationIds': ['unknown-effect']}
    before = copy.deepcopy(target['task'])
    view = copy.deepcopy(app.state['view'])
    found = await context(app, source)
    assert found is not None, 'A real UI answer must be exposed without another typed Resume'
    assert found['sourceMessageId'] == human['id']
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    assert receipt['humanResume']['questionAnswer']['id'] == args['id']
    assert receipt['humanResume']['questionAnswer']['revision'] == 2
    assert target['task'] == before and app.state['view'] == view
    assert len(app.runtime.inputs) == 1
    assert (await app.dispatch('question.answer', args, command_id='answer-resume'))['duplicate']
    await submit(app, source, target, human)
    with pytest.raises(AppError, match='already has receipt'):
        await submit(app, source, target, human, identity='second-resume')
    with pytest.raises(AppError, match='already closed'):
        await app.dispatch('question.answer', args, command_id='second-answer')
    assert len(app.runtime.inputs) == 1


@pytest.mark.parametrize('change', ['answer', 'revision', 'status', 'provenance', 'text',
                                   'input', 'question', 'new_human', 'schedule', 'old_input'])
async def test_answer_discovery_requires_exact_canonical_provenance(app, change):
    source, target, human, args = await answered_member(app)
    q = app.questions.store.get(source['id'], args['id'])
    if change == 'answer': q['answer']['text'] = 'Do not resume'
    elif change == 'revision': q['revision'] += 1
    elif change == 'status': q['status'] = 'cancelled'
    elif change == 'provenance': q['answer']['provenance']['origin'] = 'agent'
    elif change == 'text': human['text'] = 'Different instruction'
    elif change == 'input': human['inputId'] = 'unrelated-input'
    elif change == 'question': human['questionId'] = 'not-the-question'
    elif change == 'new_human': app._message(source, 'user', 'Wait.', inputOrigin='ui')
    elif change == 'schedule': human['scheduledRunId'] = 'schedule'
    elif change == 'old_input': await generation(app, source, 'later', ['unrelated-input'])
    app.questions.store.put(q)
    assert await context(app, source) is None
    with pytest.raises(AppError):
        await submit(app, source, target, human)
    assert not app.runtime.inputs


@pytest.mark.parametrize('change', ['answer', 'revision', 'status', 'source_stop', 'target_stop',
                                   'target_input', 'target_question', 'task', 'assignment',
                                   'settings', 'target', 'expiry', 'source_access', 'target_access',
                                   'source_task', 'source_input'])
async def test_answer_resume_rechecks_at_actual_admission(app, change):
    source, target, human, args = await answered_member(app)
    async def delayed(session, values, guard, emit):
        if change in {'answer', 'revision', 'status'}:
            q = app.questions.store.get(source['id'], args['id'])
            if change == 'answer': q['answer']['text'] = 'Not now'
            elif change == 'revision': q['revision'] += 1
            else: q['status'] = 'superseded'
            app.questions.store.put(q)
        elif change == 'source_stop': source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
        elif change == 'target_stop': target['interruptionRevision'] += 1
        elif change == 'target_input': app._message(target, 'user', 'Wait', inputOrigin='ui')
        elif change == 'target_question':
            app._message(target, 'user', 'A new question answer', 'question', questionId='new-answer')
        elif change == 'task': target['task'] = {'id': 'new-task', 'revision': 1, 'status': 'active'}
        elif change == 'source_task': source['task'] = {'id': 'new-source-task', 'revision': 1, 'status': 'active'}
        elif change == 'source_input': app._message(source, 'user', 'Wait for my next instruction', inputOrigin='ui')
        elif change == 'assignment':
            value = app.assignments.current(source)
            value['revision'] += 1
            app.assignments.save(value)
        elif change == 'settings':
            app.state['configurationRevision'] = app.state.get('configurationRevision', 0) + 1
        elif change == 'target':
            target['workspace'] = source['workspace'] + '-changed'
        elif change == 'expiry':
            row = app.collaboration.receipt(values['inputId'])
            assert row['delivery'] == 'submitting'
            row['expiresAt'] = 1
            app.collaboration.save(values['inputId'], row)
        else:
            changed = source if change == 'source_access' else target
            policy = app.access_mode.snapshot(changed)
            # A real saved policy transition, after which the simulated runtime
            # reports its mount refreshed. No transient pending flag may hide
            # whether the captured access revision itself is checked.
            await app.dispatch('access.set', {'sessionId': changed['id'], 'scope': 'session',
                'mode': 'full', 'expectedRevision': policy['revision']}, command_id='change-access')
            changed['fileAccessPending'] = False
            changed['configurationPending'] = False
        assert guard(), change
        assert not app.collaboration.admission(target['id'], values)['admitted']
        return {'accepted': False, 'reason': guard()}
    app.runtime.collaboration_input = delayed
    await submit(app, source, target, human)
    await settled(app, 'resume-member', 'suppressed')
    assert not app.runtime.inputs


async def test_expired_answer_resume_stays_expired_without_dispatch(app, monkeypatch):
    source, target, human, _ = await answered_member(app)
    async def hold(*args, **kwargs):
        pass
    monkeypatch.setattr(app.collaboration, 'drain', hold)
    await submit(app, source, target, human)
    row = app.collaboration.receipt('resume-member')
    assert row['delivery'] == 'queued'
    row['expiresAt'] = 1
    app.collaboration.save('resume-member', row)
    assert 'expired' in app.collaboration.guard(row)
    assert not app.collaboration.admission(target['id'], {'inputId': 'resume-member'})['admitted']
    assert not app.runtime.inputs


@pytest.mark.parametrize('origin', ['agent', 'ui'])
async def test_recorded_historical_answer_cannot_mint_fresh_resume(app, origin):
    source, target, original = await paused_member(app)
    q = (await agent_action(app, source, 'question.create', {
        'sessionId': source['id'], 'prompt': 'Resume?', 'required': True,
        'dependency': 'Existing work'}, 'ask'))['result']
    reply = app._message(source, 'user', 'Resume', inputOrigin='ui')
    args = {'sessionId': source['id'], 'id': q['id'], 'expectedRevision': 1,
            'text': 'Resume', 'sourceMessageId': reply['id']}
    if origin == 'agent':
        await agent_action(app, source, 'question.answer', args, 'record-answer')
    else:
        await app.dispatch('question.answer', args, command_id='record-answer')
    message = next(m for m in source['messages'] if m.get('questionId') == q['id'])
    await generation(app, source, 'recorded-answer-generation', [message['inputId']])
    app.runtime.inputs.clear()
    assert await context(app, source) is None
    with pytest.raises(AppError):
        await submit(app, source, target, message)
    assert not app.runtime.inputs


async def test_unknown_peer_effect_is_not_relabelled_or_replayed_by_answer(app):
    source, target, human, args = await answered_member(app)
    app.runtime.fail = True
    await submit(app, source, target, human)
    before = copy.deepcopy(await settled(app, 'resume-member', 'unknown'))
    assert len(app.runtime.inputs) == 1
    await app.dispatch('question.answer', args, command_id='answer-resume')
    await submit(app, source, target, human)
    assert app.collaboration.receipt('resume-member') == before
    assert len(app.runtime.inputs) == 1


async def test_answer_is_not_a_schedule_repair_or_foreign_assignment_grant(app):
    from amplifier_web.schedule_handoff import latest_human
    source, target, human, _ = await answered_member(app)
    assert latest_human(source) != human, 'This narrow change must not expand schedule authority'
    other = app.state['sessions'][1]
    other['interruptionRevision'] = other.get('interruptionRevision', 0)
    with pytest.raises(AppError):
        await submit(app, source, other, human)
    assert not app.runtime.inputs


async def test_question_backed_recovery_preserves_failed_generation(app):
    from amplifier_web.runtime import RuntimeManager
    source, target, human, _ = await answered_member(app)
    target.update(status='error', failure={'generationId': 'failed-turn',
        'category': 'unknown', 'errorType': 'ValueError', 'effects': 'not_rolled_back'},
        collaborationGeneration={'id': 'failed-turn', 'terminal': True, 'completed': False})
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    before = copy.deepcopy(target['failure'])
    await submit(app, source, target, human)
    receipt = await settled(app, 'resume-member', 'accepted')
    assert receipt['humanResume']['failedRecovery']['failure'] == before
    assert target['failure'] == before and len(app.runtime.inputs) == 1


@pytest.mark.parametrize('change', ['unchanged', 'no_op', 'later_edit', 'wrong_bundle', 'missing_ack', 'stop', 'placement'])
async def test_saved_next_message_transition_is_not_an_intervening_edit(app, monkeypatch, change):
    """Real prepare + runtime acknowledgement + host final admission, no process."""
    from amplifier_web import draft_defaults, next_message_configuration
    from amplifier_web.runtime import RuntimeManager
    source, target, human, _ = await answered_member(app, next_bundle=True, no_op_bundle=change == 'no_op')
    expected_bundle = 'anchors' if change == 'no_op' else 'next-report'
    before_task = copy.deepcopy(target['task'])
    runtime = RuntimeManager()
    next_message_configuration.bind(app, runtime)
    async def resolve(*args, **kwargs):
        assert args[2] == 'next-report'
        return {'bundle': 'next-report', 'modelCompatible': True, 'selection': None}
    monkeypatch.setattr(draft_defaults, 'resolve_defaults', resolve)
    started = []
    async def start(session, emit, **kwargs):
        kwargs['guard']()
        started.append(session['bundle'])
    monkeypatch.setattr(runtime, 'start', start)
    original = app.runtime.collaboration_input
    acknowledgements = []
    async def preparing(session, args, guard, emit):
        async def observe(event, payload):
            if event == 'runtime.nextMessageApplied':
                acknowledgements.append(copy.deepcopy(payload))
                if change == 'missing_ack':
                    target['bundle'] = payload['bundle']
                    return
            await emit(event, payload)
        configuration_guard = await runtime._start_for_input(session, observe)
        assert started == [expected_bundle] and len(acknowledgements) == 1
        if change == 'later_edit':
            await app.dispatch('conversation.configure', {'sessionId': target['id'],
                'expectedRevision': 1, 'bundle': 'later-choice'}, command_id='new-choice')
        elif change == 'wrong_bundle': target['bundle'] = 'not-the-captured-choice'
        elif change == 'stop': target['interruptionRevision'] += 1
        elif change == 'placement': target['executionRevision'] = target.get('executionRevision', 0) + 1
        if change not in {'unchanged', 'no_op'}:
            assert guard(), change
            assert not app.collaboration.admission(target['id'], args)['admitted']
            return {'accepted': False, 'reason': guard()}
        configuration_guard()
        if reason := guard():
            return {'accepted': False, 'reason': reason}
        return await original(session, args, guard, emit)
    app.runtime.collaboration_input = preparing
    await submit(app, source, target, human)
    await until(lambda: app.collaboration.receipt('resume-member')['delivery'] in {'accepted', 'suppressed', 'unknown', 'not_sent'})
    receipt = app.collaboration.receipt('resume-member')
    print('NEXT_MESSAGE_OBSERVATION=' + json.dumps({
        'change': change, 'started': started, 'acknowledgements': acknowledgements,
        'delivery': receipt['delivery'], 'reason': receipt.get('admission', {}).get('reason'),
        'appliedRevision': target.get('appliedConfigurationRevision'),
        'bundle': target.get('bundle'), 'taskUnchanged': target['task'] == before_task,
    }, sort_keys=True))
    assert receipt['delivery'] == ('accepted' if change in {'unchanged', 'no_op'} else 'suppressed'), (
        'Applying the already-saved choice must not consume the answer as a failed resume', receipt)
    assert target['task'] == before_task
    assert len(app.runtime.inputs) == (1 if change in {'unchanged', 'no_op'} else 0)
