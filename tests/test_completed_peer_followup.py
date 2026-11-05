"""Fresh manager follow-up preserves completed work and current authority."""
import copy
from types import SimpleNamespace

import pytest

from amplifier_web.collaboration_input import admit
from amplifier_web.completed_followup import matches_request
from test_assignment_task_limits import limited, args, change, human
from test_assignments import app, child, agent_action, generation, call
from amplifier_web.execution import ingest
from test_collaborative_workspaces import finish, until
from test_task_continuity import controls, create
from test_access_mode import save as access


async def completed(control, **fields):
    await create(control, **fields)
    control.coordinator.session_state['goal'].update(turns_used=2, continuations=1)
    return (await control.perform('task.complete', {
        'commandId': 'complete', 'expectedRevision': 1, 'condition': 'Ship the verified report',
        'evidence': ['Retained result'], 'origin': 'agent'}))['task']


@pytest.mark.parametrize('cap', [None, 3])
async def test_completed_input_charge_preserves_task_and_restart(tmp_path, monkeypatch, cap):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    native = controls()
    try:
        before = copy.deepcopy(await completed(native, maxTurns=cap))
        assert await native.tasks.peer_input_allowed(completed=True)
        assert not await native.tasks.continuation_allowed()
        native.tasks.reserve_peer_input('fresh', completed=True)
        native.tasks.reserve_peer_input('fresh', completed=True)
        expected = copy.deepcopy(before)
        expected['goalState']['turns_used'] = 3
        expected['lastCompletedInputId'] = 'fresh'
        assert native.tasks.record() == expected
        assert native.coordinator.session_state['goal'] is None
        restored = controls()
        try:
            await restored.restore()
            assert restored.tasks.record() == expected
            if cap:
                with pytest.raises(ValueError, match='exhausted'):
                    restored.tasks.reserve_peer_input('second', completed=True)
            else:
                restored.tasks.reserve_peer_input('second', completed=True)
                assert restored.tasks.record()['goalState']['turns_used'] == 4
            restored.capacity.last_denial = {'budgetRevision': restored.capacity.policy['revision']}
            assert not await restored.tasks.peer_input_allowed(completed=True)
            assert restored.capacity.last_denial
        finally:
            await restored.close()
    finally:
        await native.close()


async def test_completed_manager_limit_correction_keeps_completion_and_usage(limited):
    ctx = limited
    before = (await ctx.native.perform('task.complete', {
        'commandId': 'finish-before-limit-change', 'expectedRevision': 1,
        'condition': ctx.native.tasks.record()['objective'], 'evidence': ['Finished original']}))['task']
    ctx.target['task'] = copy.deepcopy(before)
    capacity = copy.deepcopy(ctx.native.capacity.policy)
    assignment = ctx.app.assignments.current(ctx.source)
    ingest(ctx.target, {**call(ctx.target, 'prior-charge', assignmentId=assignment['id']),
        'phase': 'completed', 'endedAt': 4, 'revision': 2, 'usage': {'totalTokens': 17}})
    ingest(ctx.target, {**call(ctx.target, 'uncertain-prior-call', assignmentId=assignment['id']),
        'phase': 'outcome_unknown', 'revision': 2})
    usage = copy.deepcopy(ctx.app.assignments.usage(ctx.app.assignments.current(ctx.source)))
    assert usage['calls'] == 2 and usage['metrics']['grossTotalTokens']['value'] == 17
    history = copy.deepcopy(ctx.native.tasks.history)
    result = await change(ctx, args(ctx, sourceMessageId=human(ctx)['id']))
    task = ctx.native.tasks.record()
    assert task['maxTurns'] is None and task['revision'] == before['revision'] + 1
    for key in ('status', 'id', 'objective', 'goalState', 'completedAt', 'completionEvidence'):
        assert task[key] == before[key]
    assert ctx.native.tasks.history == history
    assert ctx.native.coordinator.session_state['goal'] is None
    assert ctx.native.capacity.policy == capacity
    assert ctx.app.assignments.usage(ctx.app.assignments.current(ctx.source)) == usage
    assert result['result']['inputSent'] is False and not ctx.app.runtime.inputs
    with pytest.raises(ValueError, match='complete'):
        await ctx.native.perform('task.update', {'commandId': 'ordinary-edit',
            'expectedRevision': task['revision'], 'maxTurns': 12})


async def test_completed_owner_request_is_fresh_and_bound_to_membership(app):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await finish(app, target, 'initial')
    target['status'] = 'idle'
    target['task'] = {'id': 'completed-task', 'revision': 4, 'continuationEpoch': 4, 'status': 'completed'}
    result = await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': 'queue', 'text': 'Correct the retained result.'}, 'fresh-correction')
    await until(lambda: app.collaboration.receipt('fresh-correction').get('delivery') == 'accepted')
    receipt = app.collaboration.receipt('fresh-correction')
    assert result['accepted'] and receipt['completedTaskFollowup'] == {
        'version': 1, 'taskId': 'completed-task', 'taskRevision': 4}
    message = next(m for m in target['messages'] if m.get('inputId') == 'fresh-correction')
    assert 'humanResume' not in message['peerEnvelope']
    assert message['peerEnvelope']['completedTaskHandling']['taskId'] == 'completed-task'
    assert app.collaboration.guard(receipt) is None
    assert app.collaboration.guard(receipt, for_reply=True) is None
    successor = {'id': 'new-task', 'revision': 6, 'status': 'completed',
                 'followupOf': {'taskId': 'completed-task', 'revision': 4,
                                'inputId': 'fresh-correction', 'generationId': 'native-generation'}}
    target['task'] = successor
    assert app.collaboration.guard(receipt) == 'The recipient task is not active.'
    assert app.collaboration.guard(receipt, for_reply=True) is None
    successor['followupOf']['inputId'] = 'another-request'
    assert app.collaboration.guard(receipt, for_reply=True) == 'The recipient task is not active.'
    successor['followupOf']['inputId'] = 'fresh-correction'
    assignment = app.assignments.current(source)
    assignment['members'][target['id']]['status'] = 'retired'
    app.assignments.save(assignment)
    assert 'retired' in app.collaboration.guard(receipt, for_reply=True)


@pytest.mark.parametrize('other_workspace', [False, True])
async def test_independent_full_manager_accepts_fresh_work_without_reopening_completed_task(app, other_workspace):
    source, target = app.state['sessions']
    app.runtime.workers = {}  # No mounted configuration needs retirement for access setup.
    if other_workspace:
        target['workspace'] = str(app.data_dir / 'other-workspace')
    await access(app, source)
    await access(app, target)
    target['task'] = {'id': 'previous-release', 'revision': 3, 'status': 'completed',
                      'completionEvidence': ['Published release'], 'maxTurns': 8}
    prior = copy.deepcopy(target['task'])
    draft = copy.deepcopy(app.state['view']['draft'])
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Publish the newly accepted work.', 'mode': 'queue'}, 'new-release')
    await until(lambda: app.collaboration.receipt('new-release').get('delivery') != 'queued')
    receipt = app.collaboration.receipt('new-release')
    assert receipt['delivery'] == 'accepted', receipt
    assert receipt['completedTaskFollowup']['version'] == 2
    assert app.collaboration.guard(receipt) is None
    assert target['task'] == prior and app.state['view']['draft'] == draft
    assert len(app.runtime.inputs) == 1
    assert not receipt.get('humanResume')
    # The exact retry observes the same input, not another wake or a reopened goal.
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Publish the newly accepted work.', 'mode': 'queue'}, 'new-release')
    assert len(app.runtime.inputs) == 1
    target['task'] = {'id': 'next-release', 'revision': 4, 'status': 'active',
                      'followupOf': {'taskId': prior['id'], 'revision': prior['revision'],
                                     'inputId': 'new-release', 'generationId': 'new-generation'}}
    assert app.collaboration.guard(receipt, for_reply=True) is None
    assert app.collaboration.guard(receipt) == 'The recipient task changed.'


@pytest.mark.parametrize('boundary', ['source-ask', 'target-ask', 'stop', 'paused', 'cancelled', 'other-owner'])
async def test_independent_completion_does_not_bypass_current_authority(app, boundary):
    source, target = app.state['sessions']
    app.runtime.workers = {}
    source['status'] = 'idle'  # Set the standing policy before starting fixture work.
    await access(app, source, scope='app')
    if boundary == 'other-owner':
        independent = target
        target, _ = await child(app, source)
        await finish(app, target, 'initial')
        source = independent
        await generation(app, source, 'independent-generation', ['independent-input'])
    if boundary == 'source-ask':
        await access(app, source, mode='ask')
    if boundary == 'target-ask':
        await access(app, target, mode='ask')
    target['status'] = 'stopped' if boundary == 'stop' else 'idle'
    target['task'] = {'id': 'done', 'revision': 2,
                      'status': boundary if boundary in {'paused', 'cancelled'} else 'completed'}
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Fresh work.', 'mode': 'queue'}, 'not-authorized')
    await until(lambda: app.collaboration.receipt('not-authorized').get('delivery') == 'suppressed')
    assert not app.collaboration.receipt('not-authorized').get('completedTaskFollowup') or boundary == 'stop'
    assert not any(row[1] == 'not-authorized' for row in app.runtime.inputs)


@pytest.mark.parametrize('change', ['source-policy', 'target-policy', 'source-stop', 'task', 'old-request'])
async def test_independent_completion_rechecks_frozen_request_without_upgrading_old_receipts(app, change):
    source, target = app.state['sessions']
    app.runtime.workers = {}
    await access(app, source)
    await access(app, target)
    target['status'] = 'working'  # Deliberately hold queue delivery at the existing idle boundary.
    target['task'] = {'id': 'done', 'revision': 2, 'status': 'completed'}
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Fresh work.', 'mode': 'queue'}, 'held-followup')
    receipt = app.collaboration.receipt('held-followup')
    assert receipt['delivery'] == 'queued' and app.collaboration.guard(receipt) is None
    if change.endswith('policy'):
        await access(app, source if change == 'source-policy' else target, mode='ask')
    elif change == 'source-stop':
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    elif change == 'task':
        target['task']['revision'] += 1
    else:
        receipt.pop('completedTaskFollowup')
    assert app.collaboration.guard(receipt)  # Policy changes may first require runtime retirement.
    assert not app.runtime.inputs


@pytest.mark.parametrize('boundary', ['stopped', 'paused', 'cancelled', 'independent', 'nonowner'])
async def test_completion_policy_never_broadens_other_boundaries(app, boundary):
    source, independent = app.state['sessions']
    target, _ = await child(app, source)
    await finish(app, target, 'initial')
    target['status'] = 'idle'
    target['task'] = {'id': 'finished', 'revision': 2, 'status': 'completed'}
    if boundary == 'stopped':
        target['status'] = 'stopped'
    elif boundary in {'paused', 'cancelled'}:
        target['task']['status'] = boundary
    elif boundary == 'independent':
        target.pop('assignmentId', None)
        target['collaboration'].pop('assignmentId', None)
    elif boundary == 'nonowner':
        source = independent
        await generation(app, source, "independent-generation", ["independent-input"])
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': 'queue', 'text': 'A new request.'}, 'excluded')
    await until(lambda: app.collaboration.receipt('excluded').get('delivery') == 'suppressed')
    receipt = app.collaboration.receipt('excluded')
    assert 'not active' in receipt['detail'] or 'stopped or unavailable' in receipt['detail']
    assert 'humanResume' not in receipt


@pytest.mark.parametrize('cap', [None, 4])
async def test_successor_preserves_allowance_and_idempotent_reserved_turn_witness(tmp_path, monkeypatch, cap):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    native = controls()
    try:
        await completed(native, maxTurns=cap)
        native.tasks.reserve_peer_input('followup', completed=True)
        before = copy.deepcopy(native.tasks.record())
        native.runtime.generation = {'id': 'native-generation', 'input_ids': ['followup']}
        native.coordinator.register_capability('goal.reserved_turn_accounting', 1)
        result = await native.perform('task.create', {'commandId': 'successor',
            'expectedRevision': before['revision'], 'objective': 'Correct the completed result'})
        successor = result['task']
        assert successor['id'] != before['id'] and successor['maxTurns'] == cap
        assert native.tasks.history == [before]
        assert successor['goalState'] == {'turns_used': 3, 'continuations': 1}
        assert successor['followupOf'] == {'taskId': before['id'], 'revision': before['revision'],
            'inputId': 'followup', 'generationId': 'native-generation', 'turnsUsed': 3, 'continuations': 1}
        native.tasks.apply()
        goal = native.coordinator.session_state['goal']
        assert native.tasks.turn_already_charged(goal) is True
        assert native.tasks.turn_already_charged(goal) is True
        # Creation/apply persistence completed; simulate loss before callback
        # response. Restoring the exact generation must return the same answer.
        restored = controls()
        try:
            await restored.restore()
            restored.runtime.generation = copy.deepcopy(native.runtime.generation)
            current = restored.coordinator.session_state['goal']
            assert restored.tasks.turn_already_charged(current) is True
            current['continuations'] += 1
            assert restored.tasks.turn_already_charged(current) is False
            current['continuations'] -= 1
            restored.runtime.generation['id'] = 'another-generation'
            assert restored.tasks.turn_already_charged(current) is False
            restored.runtime.generation['id'] = 'native-generation'
            del restored.tasks.record()['followupOf']['turnsUsed']
            with pytest.raises(KeyError):
                restored.tasks.turn_already_charged(current)
        finally:
            await restored.close()
        await native.perform('task.complete', {'commandId': 'completed-successor',
            'expectedRevision': successor['revision'], 'condition': successor['objective'],
            'evidence': ['Corrected artifact']})
        again = await native.perform('task.create', {'commandId': 'unrelated-successor',
            'expectedRevision': native.tasks.record()['revision'], 'objective': 'Unrelated new work'})
        assert again['task']['followupOf'] == successor['followupOf']
        assert again['task']['maxTurns'] == cap
        assert again['task']['goalState']['turns_used'] == 3
        assert len(native.tasks.history) == 2
        await native.perform('task.complete', {'commandId': 'finished-decomposition',
            'expectedRevision': again['task']['revision'], 'condition': again['task']['objective'],
            'evidence': ['Decomposed correction result']})
        native.runtime.generation = {'id': 'fresh-native-generation', 'input_ids': ['fresh-unrelated']}
        fresh = await native.perform('task.create', {'commandId': 'fresh-unrelated-task',
            'expectedRevision': native.tasks.record()['revision'], 'objective': 'Genuinely fresh work'})
        assert 'followupOf' not in fresh['task'] and not fresh['task']['goalState']
    finally:
        await native.close()


@pytest.mark.parametrize('failure', ['unsupported', 'persist', 'reset-cap'])
async def test_successor_accounting_refusal_has_no_partial_history_or_budget_reset(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    native = controls()
    try:
        await completed(native, maxTurns=4)
        native.tasks.reserve_peer_input('followup', completed=True)
        native.runtime.generation = {'id': 'native-generation', 'input_ids': ['followup']}
        if failure != 'unsupported':
            native.coordinator.register_capability('goal.reserved_turn_accounting', 1)
        before = copy.deepcopy(native.tasks.canonical_snapshot())
        values = {'commandId': 'new', 'expectedRevision': before['task']['revision'], 'objective': 'Correction'}
        if failure == 'persist':
            def fail(**kwargs):
                raise OSError('saved state unavailable')
            monkeypatch.setattr(native, 'persist', fail)
        if failure == 'reset-cap':
            values['maxTurns'] = None
        with pytest.raises((ValueError, OSError)):
            await native.perform('task.create', values)
        assert native.tasks.canonical_snapshot() == before
        assert 'new' not in native.tasks.receipts
    finally:
        await native.close()


@pytest.mark.parametrize('race', ['exhausted', 'revision', 'stop', 'unknown-submit'])
async def test_completed_native_admission_fences_and_unknown_charge(tmp_path, monkeypatch, race):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    native = controls()
    try:
        task = await completed(native, maxTurns=2 if race == 'exhausted' else 4)
        values = {'inputId': 'fresh', 'taskId': task['id'], 'taskRevision': task['revision']}
        calls, inputs, epoch = [], [], [0]
        async def authorize(args):
            calls.append(args)
            if len(calls) == 2:
                if race == 'revision':
                    native.tasks.record()['revision'] += 1
                elif race == 'stop':
                    epoch[0] += 1
            return {'admitted': True, 'completedTaskInput': True,
                'message': {'peerEnvelope': {'inputId': 'fresh'}, 'text': 'Correct the result'}}
        async def submit(value):
            inputs.append(value)
            raise OSError('lost submission response; outcome unknown')
        native.runtime.max_input_chars = 100000
        native.runtime.submit = submit
        if race == 'unknown-submit':
            with pytest.raises(OSError, match='outcome unknown'):
                await admit(native, native.runtime, values, None, authorize, lambda: epoch[0])
            assert len(inputs) == 1 and native.tasks.record()['goalState']['turns_used'] == 3
            restored = controls()
            try:
                await restored.restore()
                assert restored.tasks.record()['lastCompletedInputId'] == 'fresh'
                assert restored.tasks.record()['goalState']['turns_used'] == 3
            finally:
                await restored.close()
        else:
            result = await admit(native, native.runtime, values, None, authorize, lambda: epoch[0])
            assert result['accepted'] is False and not inputs
            assert native.tasks.record()['goalState']['turns_used'] == 2
            assert 'lastCompletedInputId' not in native.tasks.record()
        assert native.tasks.record()['status'] == 'completed'
    finally:
        await native.close()


async def test_live_progress_survives_terminal_clear_replacement_and_failed_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    native = controls()
    try:
        await create(native, maxTurns=4)
        task = native.tasks.record()
        goal = native.coordinator.session_state['goal']
        assert task['goalState'] is goal
        goal.update(turns_used=3, continuations=2)
        replacement = copy.deepcopy(goal)
        native.coordinator.session_state['goal'] = replacement
        native.persist(task_only=True)
        assert task['goalState'] is replacement
        original_persist = native.persist
        def fail(**kwargs):
            raise OSError('atomic persistence failed')
        monkeypatch.setattr(native, 'persist', fail)
        with pytest.raises(OSError):
            await native.perform('task.update', {'commandId': 'failed', 'expectedRevision': 1,
                'objective': 'Pending changed task'})
        monkeypatch.setattr(native, 'persist', original_persist)
        task = native.tasks.record(); goal = native.coordinator.session_state['goal']
        assert task['goalState'] is goal and task['revision'] == 1
        goal['turns_used'] = 4
        native.coordinator.session_state['goal'] = None
        native.persist(task_only=True)
        assert task['goalState']['turns_used'] == 4
        restored = controls()
        try:
            await restored.restore()
            from amplifier_web.collaboration_input import exhausted
            assert exhausted(restored)
            assert restored.tasks.record()['goalState'] is restored.coordinator.session_state['goal']
            assert restored.tasks.record()['goalState']['turns_used'] == 4
        finally:
            await restored.close()
    finally:
        await native.close()


async def test_restore_legacy_separate_goal_uses_fresher_current_progress(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    native = controls()
    try:
        await create(native, maxTurns=4)
        saved = json.loads(native.state_path().read_text())
        saved['goal']['turns_used'] = 4
        saved['task']['goalState']['turns_used'] = 3
        native.state_path().write_text(json.dumps(saved))
        await native.restore()
        assert native.tasks.record()['goalState'] is native.coordinator.session_state['goal']
        assert native.tasks.record()['goalState']['turns_used'] == 4
    finally:
        await native.close()
