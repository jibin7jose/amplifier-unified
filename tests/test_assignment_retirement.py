"""Finished participation releases capacity, not history or spending."""
import copy

import pytest

from amplifier_web.capacity import admission
from amplifier_web.execution import ingest
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError
from test_assignments import app, child, limits, call, agent_action, generation
from test_collaborative_workspaces import settled
from test_task_continuity import controls, create


async def save_task(app, target, status=None):
    """Persist native authority even when the transport fixture has no task."""
    native = controls(target.get('runtimeSessionId') or target['id'])
    native.state_path = lambda: app.data_dir / 'sessions' / native.session.session_id / 'control-state.json'
    try:
        if status:
            await create(native)
            if status != 'active':
                await native.perform('task.' + {'completed': 'complete', 'paused': 'pause', 'blocked': 'block'}[status], {
                    'commandId': 'settle-task', 'expectedRevision': 1,
                    'condition': native.tasks.record()['objective'], 'evidence': ['Accepted contribution'],
                    'reason': 'Waiting for acceptance'})
        native.persist(task_only=True)
        return copy.deepcopy(native.tasks.record())
    finally:
        await native.close()


async def setup(app):
    source = app.state['sessions'][0]
    target, created = await child(app, source)
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    await settle(app, target)
    await save_task(app, target)
    return source, target, created


async def settle(app, target):
    active = target['collaborationGeneration']
    await app.on_runtime_event('runtime.generation', {
        'sessionId': target['id'], 'rootSessionId': target['id'],
        'generation_id': active['id'], 'input_ids': active['inputIds'],
        'event': 'generation.finished', 'text': 'Finished work',
        'disposition': 'manager_turn_finished', 'active_job_ids': [],
    })
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})


async def retire(app, source, target, identity='retire'):
    args = {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': app.assignments.current(source)['revision']}
    return await agent_action(app, source, 'assignment.retire', args, identity)


async def adoption_args(app, source, target):
    human = app._message(source, 'user', 'Use the finished chat for the next follow-up.',
                         inputId='follow-up-human', inputOrigin='ui')
    await generation(app, source, 'follow-up-generation', [human['inputId']])
    observation = (await app.dispatch('assignment.adoption', {
        'sessionId': source['id'], 'targetSessionId': target['id']}))['result']
    assert observation['eligible'], observation['reason']
    return {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': observation['assignmentRevision'],
            'expectedTargetRevision': observation['revision'], 'sourceMessageId': human['id']}


async def test_retirement_releases_slot_preserving_history_usage_and_stop(app):
    source, target, created = await setup(app)
    await limits(app, source, maxMembers=2)
    row = call(target, 'charged')
    assert (await admission(app, target['id'], {'call': row}))['allowed']
    ingest(target, {**row, 'phase': 'completed', 'endedAt': 4, 'revision': 2,
                   'usage': {'totalTokens': 15}})
    target.update(status='stopped', interruptionRevision=1, lastInterruption={'at': 1, 'kind': 'user_stop'})
    before = copy.deepcopy(target)
    original = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    inputs = len(app.runtime.inputs)
    receipt = await retire(app, source, target)
    assignment = app.assignments.current(source)
    assert assignment['members'][target['id']]['status'] == 'retired'
    assert app.assignments.current(target) is None
    assert app.assignments.active_member_count(assignment) == 1
    assert receipt['result']['inputSent'] is False
    assert target['messages'] == before['messages']
    assert target.get('lastInterruption') == before.get('lastInterruption')
    assert target.get('interruptionRevision') == before.get('interruptionRevision')
    assert len(app.runtime.inputs) == inputs
    assert app.collaboration.receipt(created['initialInputId']) == original
    assert app.assignments.usage(assignment)['metrics']['grossTotalTokens']['value'] == 15
    newer, _ = await child(app, source, 'new-member')
    assignment = app.assignments.current(source)
    assert len(assignment['members']) == 3
    assert app.assignments.active_member_count(assignment) == 2
    assert assignment['members'][newer['id']]['status'] == 'bound'
    assert app.assignments.usage(assignment)['metrics']['grossTotalTokens']['value'] == 15


async def test_readmit_same_chat_fences_old_requests_and_keeps_spend(app):
    source, target, created = await setup(app)
    await limits(app, source, maxMembers=2, maxTotalTokens=100)
    row = call(target, 'charged')
    assert (await admission(app, target['id'], {'call': row}))['allowed']
    ingest(target, {**row, 'phase': 'completed', 'endedAt': 4, 'revision': 2,
                   'usage': {'totalTokens': 15}})
    old = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    await retire(app, source, target)
    assert 'retired' in app.collaboration.guard(old)
    before = len(app.runtime.inputs)
    args = await adoption_args(app, source, target)
    await agent_action(app, source, 'assignment.adopt', args, 'readmit')
    value = app.assignments.current(target)
    member = value['members'][target['id']]
    assert member['role'] == 'adopted' and member['previousRetirementId'] == 'retire'
    assert member['joinedAt'] > old['acceptedAt']
    assert 'earlier assignment membership' in app.collaboration.guard(old)
    assert len(app.runtime.inputs) == before
    assert app.collaboration.receipt(created['initialInputId']) == old
    assert app.assignments.usage(value)['metrics']['grossTotalTokens']['value'] == 15
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': 'queue', 'text': 'Fresh follow-up',
        'returnNotification': False}, 'new-followup')
    await settled(app, 'new-followup', 'accepted')
    assert len(app.runtime.inputs) == before + 1


async def full_access(app, source, mode='full'):
    # This transport-only fixture has no mounted native processes. Declare that
    # before changing access so refresh does not use its generic Stop stub.
    app.runtime.workers = {}
    current = app.access_mode.snapshot(source)
    await app.dispatch('access.set', {
        'sessionId': source['id'], 'scope': 'session', 'mode': mode,
        'expectedRevision': current['revision']})
    # This fixture has no mounted file tools to retire.
    source['fileAccessPending'] = source['configurationPending'] = False


async def reviewed_args(app, source, target):
    observation = (await app.dispatch('assignment.adoption', {
        'sessionId': source['id'], 'targetSessionId': target['id']}))['result']
    return {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': observation['assignmentRevision'],
            'expectedTargetRevision': observation['revision']}


async def test_full_owner_readmits_completed_retired_member_from_peer_only_input(app):
    source, target, created = await setup(app)
    await full_access(app, source)
    target['task'] = await save_task(app, target, 'completed')
    await limits(app, source, maxMembers=2, maxTotalTokens=100)
    charged = call(target, 'charged')
    assert (await admission(app, target['id'], {'call': charged}))['allowed']
    ingest(target, {**charged, 'phase': 'completed', 'endedAt': 4, 'revision': 2,
                   'usage': {'totalTokens': 15}})
    old = copy.deepcopy(app.collaboration.receipt(created['initialInputId']))
    await retire(app, source, target)
    retirement = copy.deepcopy(app.collaboration.receipt('retire'))
    before = copy.deepcopy(target)
    count = len(app.runtime.inputs)
    assert not app.collaboration.current_human_input(source)
    args = await reviewed_args(app, source, target)
    from amplifier_web.assignment_retirement import reuse_binding
    assert app.access_mode.full(source)
    assert retirement['reuseBinding'] == reuse_binding(app, source, target, target['task'])
    assert target['status'] == 'idle' and not target.get('failure'), target.get('failure')
    receipt = await agent_action(app, source, 'assignment.adopt', args, 'full-readmit')
    value = app.assignments.current(target)
    assert value['members'][target['id']]['previousRetirementId'] == 'retire'
    assert receipt['membership']['sourceMessageId'] is None
    assert receipt['result']['inputSent'] is False
    assert target['task'] == before['task'] and target['messages'] == before['messages']
    assert len(app.runtime.inputs) == count
    assert app.collaboration.receipt('retire') == retirement
    assert app.collaboration.receipt(created['initialInputId']) == old
    assert 'earlier assignment membership' in app.collaboration.guard(old)
    assert app.assignments.usage(value)['metrics']['grossTotalTokens']['value'] == 15
    assert (await agent_action(app, source, 'assignment.adopt', args, 'full-readmit'))['duplicate']
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'mode': 'queue', 'text': 'One deliberate fresh follow-up',
        'returnNotification': False}, 'fresh-full-followup')
    await settled(app, 'fresh-full-followup', 'accepted')
    assert len(app.runtime.inputs) == count + 1
    assert app.runtime.inputs[-1][1] == 'fresh-full-followup'
    assert target['task'] == before['task']


@pytest.mark.parametrize('change', [
    'ask', 'stop', 'paused', 'new-task', 'failed', 'unknown-input', 'unknown-call',
    'queued', 'foreign-owner', 'detached', 'placement', 'configuration', 'access',
    'native-identity', 'new-human', 'generation', 'preparation', 'worker',
    'pending-settings', 'claim', 'check', 'missing-receipt', 'legacy-receipt',
    'forged-receipt', 'manager-stop', 'manager-task', 'manager-paused',
])
async def test_full_readmission_refuses_changed_or_unsettled_retirement(app, change):
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    if change == 'ask':
        await full_access(app, source, 'ask')
    elif change in {'stop', 'failed'}:
        target['status'] = 'stopped' if change == 'stop' else 'error'
        target['interruptionRevision'] = 1
    elif change in {'paused', 'new-task'}:
        target['task'] = await save_task(app, target, 'paused' if change == 'paused' else 'active')
    elif change in {'unknown-input', 'queued'}:
        app.collaboration.insert('unsettled', 'fixture', {
            'commandAction': 'coordination.send', 'target': {'sessionId': target['id']},
            'delivery': 'unknown' if change == 'unknown-input' else 'queued'})
    elif change == 'unknown-call':
        ingest(target, {**call(target, 'uncertain'), 'phase': 'outcome_unknown'})
    elif change == 'foreign-owner':
        app.assignments.ensure(target, 'other-assignment')
    elif change == 'detached':
        value = app.assignments.current(source)
        value['members'][target['id']]['status'] = 'detached'
        app.assignments.save(value)
    elif change == 'placement':
        target['executionRevision'] = 1
    elif change == 'configuration':
        target['configurationPendingRevision'] = target.get('configurationPendingRevision', 0) + 1
    elif change == 'access':
        await full_access(app, target, 'ask')
    elif change == 'native-identity':
        target['runtimeSessionId'] = 'another-native'
    elif change == 'new-human':
        app._message(target, 'user', 'Independent new direction', inputOrigin='ui', inputId='new-human')
    elif change == 'generation':
        target['collaborationGeneration']['terminal'] = False
    elif change == 'preparation':
        app.runtime._preparations = {target['id']: object()}
    elif change == 'worker':
        target['workers'].append({'id': 'native-child', 'status': 'idle'})
    elif change == 'pending-settings':
        target['configurationPending'] = True
    elif change == 'claim':
        value = app.assignments.current(source)
        value['tasks'] = {'claimed': {'status': 'claimed', 'claim': {'sessionId': target['id']}}}
        app.assignments.save(value)
    elif change == 'check':
        app.collaboration.insert('active-check', 'fixture', {
            'commandAction': 'workspace.tests.submit', 'senderSessionId': target['id'],
            'job': {'status': 'checking'}})
    elif change == 'missing-receipt':
        app.db.execute("DELETE FROM commands WHERE id='retire'")
    elif change in {'legacy-receipt', 'forged-receipt'}:
        record = app.collaboration.receipt('retire')
        if change == 'legacy-receipt':
            record.pop('reuseBinding')
        else:
            record['senderSessionId'] = 'other-manager'
        app.collaboration.save('retire', record)
    elif change == 'manager-stop':
        source['interruptionRevision'] = 1
    elif change == 'manager-task':
        source['task'] = {'id': 'different-scope', 'revision': 1, 'status': 'active'}
    else:
        source['task'] = {'id': 'paused-manager', 'revision': 1, 'status': 'paused'}
    # Even a new inspection cannot erase a changed retirement or borrow Full.
    args = await reviewed_args(app, source, target)
    before = copy.deepcopy(app.assignments.current(source))
    inputs = len(app.runtime.inputs)
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt', args, 'invalid-readmit')
    assert app.assignments.current(source) == before
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='invalid-readmit'").fetchone()
    assert len(app.runtime.inputs) == inputs


@pytest.mark.parametrize('change', ['revision', 'placement', 'workspace', 'stop'])
async def test_full_readmission_requires_exact_review(app, change):
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    args = await reviewed_args(app, source, target)
    if change == 'revision':
        await limits(app, source, maxMembers=5)
    elif change == 'workspace':
        target['workspace'] += '-elsewhere'
    elif change == 'placement':
        target['executionRevision'] = 1
    else:
        target['interruptionRevision'] = 1
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt', args, 'stale-readmit')
    assert app.assignments.current(target) is None


async def test_full_does_not_adopt_unrelated_root_or_lend_authority_to_native_child(app):
    source, target, _ = await setup(app)
    await full_access(app, source)
    independent = next(row for row in app.state['sessions'] if row['id'] not in {source['id'], target['id']})
    with pytest.raises(AppError, match='human instruction'):
        await agent_action(app, source, 'assignment.adopt',
                           await reviewed_args(app, source, independent), 'unrelated')
    await retire(app, source, target)
    args = await reviewed_args(app, source, target)
    with pytest.raises(AppError):
        await app.app_bridge('dispatch', {
            'action': 'assignment.adopt', 'args': args, 'id': 'child-borrow',
            '_runtimeSessionId': 'native-child',
            '_generationId': source['collaborationGeneration']['id'],
            '_inputBindings': [{'inputId': 'incoming-peer'}]}, source['id'])
    assert app.assignments.current(target) is None


async def test_full_readmission_refuses_foreign_native_writer(app):
    from amplifier_foundation.session import SharedSessionStore
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    held = SharedSessionStore(target['workspace'], target['id']).acquire(app='foreign-writer')
    try:
        with pytest.raises(AppError, match='confirm'):
            await agent_action(app, source, 'assignment.adopt',
                               await reviewed_args(app, source, target), 'foreign-native')
        assert held.active and app.assignments.current(target) is None
    finally:
        held.release()


@pytest.mark.parametrize('origin,allowed', [('agent', True), ('ui', False), (None, False)])
async def test_readmission_respects_manager_dependency_block_provenance(app, origin, allowed):
    source, target, _ = await setup(app)
    await full_access(app, source)
    source['task'] = {'id': 'same-manager-task', 'revision': 1, 'status': 'blocked',
        'blockProvenance': {'version': 1, 'origin': origin, 'revision': 1, 'dependency': origin == 'agent'}}
    await retire(app, source, target)
    args = await reviewed_args(app, source, target)
    if allowed:
        receipt = await agent_action(app, source, 'assignment.adopt', args, 'dependency-followup')
        assert not receipt['result']['inputSent']
        assert source['task']['status'] == 'blocked'
    else:
        with pytest.raises(AppError):
            await agent_action(app, source, 'assignment.adopt', args, 'dependency-followup')
        assert app.assignments.current(target) is None


async def test_newer_adoption_and_user_detach_cannot_restore_old_manager_authority(app):
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    other = next(row for row in app.state['sessions'] if row['id'] not in {source['id'], target['id']})
    app.assignments.ensure(other, 'new-manager')
    await app.dispatch('assignment.adopt', await reviewed_args(app, other, target), command_id='new-owner')
    await app.dispatch('assignment.detach', {'sessionId': target['id'],
        'expectedRevision': app.assignments.current(other)['revision']}, command_id='user-detach')
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt',
                           await reviewed_args(app, source, target), 'old-owner-reclaim')
    assert app.assignments.current(target) is None


async def test_retirement_witness_lookup_does_not_scan_unrelated_command_bodies(app, record_property):
    import json
    from amplifier_web.assignment_retirement import reuse_binding
    source, target, _ = await setup(app)
    def measured():
        steps = 0
        def step():
            nonlocal steps
            steps += 1
            return 0
        app.db.set_progress_handler(step, 1)
        try:
            value = reuse_binding(app, source, target, {})
        finally:
            app.db.set_progress_handler(None, 0)
        return value, steps
    before, small = measured()
    app.db.executemany('INSERT INTO commands VALUES(?,?,?)', [
        (f'unrelated-{i}', 'fixture', json.dumps({
            'commandAction': 'assignment.adopt', 'targetSessionId': f'unrelated-{i}',
            'result': 'x' * 4096})) for i in range(2000)])
    after, large = measured()
    assert after == before
    record_property('sqlite_steps_small', small)
    record_property('sqlite_steps_2000_unrelated_4k_receipts', large)
    # A lookup may traverse more B-tree levels, but must not parse 8 MB of history.
    assert large <= small + 20


@pytest.mark.parametrize('change', ['full-off-on', 'stop', 'placement', 'unknown'])
async def test_full_readmission_rechecks_after_idle_retirement(app, monkeypatch, change):
    from amplifier_web import assignment_adoption
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    args = await reviewed_args(app, source, target)
    async def retire_while_changed(*_args, **_kwargs):
        if change == 'full-off-on':
            await full_access(app, source, 'ask')
            await full_access(app, source)
        elif change == 'stop':
            source['interruptionRevision'] = 1
        elif change == 'placement':
            target['executionRevision'] = 1
        else:
            ingest(target, {**call(target, 'uncertain-after-wait'), 'phase': 'outcome_unknown'})
    monkeypatch.setattr(assignment_adoption, 'retire_idle', retire_while_changed)
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.adopt', args, 'changed-readmit')
    assert app.assignments.current(target) is None
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='changed-readmit'").fetchone()


async def test_retirement_does_not_notify_old_completion_after_readmission(app):
    source, target, created = await setup(app)
    old = app.collaboration.receipt(created['initialInputId'])
    await retire(app, source, target)
    await agent_action(app, source, 'assignment.adopt',
                       await adoption_args(app, source, target), 'readmit')
    old['terminalOutcome'] = {'status': 'observed', 'kind': 'admission',
        'outcome': 'failed', 'qualified': False, 'requestId': old['requestId'],
        'observedAt': member_time(app, target), 'text': 'Late retained evidence'}
    before = len(app.runtime.inputs)
    app.collaboration.enqueue_continuation(old)
    saved = app.collaboration.receipt(old['requestId'])
    assert saved['subscription']['status'] == 'suppressed'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?',
                             (old['subscription']['continuationId'],)).fetchone()
    assert len(app.runtime.inputs) == before


def member_time(app, target):
    return app.assignments.current(target)['members'][target['id']]['joinedAt'] + 1


@pytest.mark.parametrize('busy', ['generation', 'worker', 'preparation', 'settings',
                                 'pending', 'unknown', 'call', 'task', 'claim', 'check'])
async def test_retirement_refuses_unfinished_or_uncertain_work(app, busy):
    source, target, _ = await setup(app)
    if busy == 'generation':
        target['collaborationGeneration']['terminal'] = False
    elif busy == 'worker':
        target['workers'].append({'id': 'child', 'status': 'idle'})
    elif busy == 'preparation':
        app.runtime._preparations = {target['id']: object()}
    elif busy == 'settings':
        target['configurationBusy'] = True
    elif busy in {'pending', 'unknown'}:
        app.collaboration.insert('pending', 'fingerprint', {
            'commandAction': 'coordination.send', 'target': {'sessionId': target['id']},
            'delivery': 'queued' if busy == 'pending' else 'unknown'})
    elif busy == 'call':
        ingest(target, {**call(target, 'uncertain'), 'phase': 'outcome_unknown'})
    elif busy == 'task':
        target['task'] = await save_task(app, target, 'blocked')
    elif busy == 'claim':
        value = app.assignments.current(source)
        value['tasks'] = {'claimed': {'status': 'claimed', 'claim': {'sessionId': target['id']}}}
        app.assignments.save(value)
    else:
        app.collaboration.insert('check', 'fingerprint', {
            'commandAction': 'workspace.tests.submit', 'senderSessionId': target['id'],
            'job': {'status': 'checking'}})
    before = copy.deepcopy(app.assignments.current(source))
    with pytest.raises(AppError):
        await retire(app, source, target)
    assert app.assignments.current(source) == before
    assert app.assignments.current(target)['id'] == before['id']


async def test_only_owner_can_retire_current_helper(app):
    source, target, _ = await setup(app)
    with pytest.raises(AppError, match='owner'):
        await app.dispatch('assignment.retire', {'sessionId': target['id'], 'targetSessionId': source['id'],
            'expectedRevision': app.assignments.current(source)['revision']})
    with pytest.raises(AppError, match='owner'):
        await retire(app, source, source)


async def test_readmission_rechecks_capacity(app):
    source, target, _ = await setup(app)
    await limits(app, source, maxMembers=2)
    await retire(app, source, target)
    await child(app, source, 'replacement')
    args = await adoption_args(app, source, target)
    with pytest.raises(AppError, match='member limit'):
        await agent_action(app, source, 'assignment.adopt', args, 'full-readmission')
    assert app.assignments.current(target) is None
    assert app.assignments.current(source)['members'][target['id']]['status'] == 'retired'


async def test_failed_retirement_save_restores_membership(app, monkeypatch):
    source, target, _ = await setup(app)
    before = copy.deepcopy(app.assignments.current(source))
    def fail(**kwargs):
        raise OSError('Synthetic durable save failure')
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(OSError):
        await retire(app, source, target)
    assert app.assignments.current(source) == before
    assert app.assignments.current(target)['id'] == before['id']
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='retire'").fetchone()


async def test_retirement_retries_same_receipt_without_repeating_changes(app):
    source, target, _ = await setup(app)
    args = {'sessionId': source['id'], 'targetSessionId': target['id'],
            'expectedRevision': app.assignments.current(source)['revision']}
    first = await agent_action(app, source, 'assignment.retire', args, 'same-retirement')
    duplicate = await agent_action(app, source, 'assignment.retire', args, 'same-retirement')
    assert duplicate == {**first, 'duplicate': True}
    assert app.assignments.current(source)['revision'] == first['result']['assignment']['revision']
    assert app.collaboration.creation_guard(app.collaboration.receipt('helper')) is not None


async def test_retirement_refuses_foreign_native_writer(app):
    from amplifier_foundation.session import SharedSessionStore
    source, target, _ = await setup(app)
    owner = SharedSessionStore(target['workspace'], target['id']).acquire(app='other-native-writer')
    try:
        with pytest.raises(AppError, match='stopped'):
            await retire(app, source, target)
        assert app.assignments.current(target) is not None
    finally:
        owner.release()


@pytest.mark.parametrize('status', ['detached', 'not_created'])
async def test_historical_members_do_not_consume_current_capacity(app, status):
    source, target, _ = await setup(app)
    await limits(app, source, maxMembers=2)
    value = app.assignments.current(source)
    value['members'][target['id']]['status'] = status
    app.assignments.save(value)
    newer, _ = await child(app, source, 'replacement')
    value = app.assignments.current(source)
    assert value['members'][newer['id']]['status'] == 'bound'
    assert len(value['members']) == 3 and app.assignments.active_member_count(value) == 2


@pytest.mark.parametrize('state,blocked', [('running', True), ('unknown', True), ('stopped', False)])
async def test_retirement_observes_environment_owner_after_terminal_queue(app, state, blocked):
    from contextlib import closing
    from amplifier_web import task_environments as environments
    source, target, _ = await setup(app)
    row = {'id': 'retained-environment', 'kind': environments.KIND,
           'sessionId': target['id'], 'workspaceId': 'workspace',
           'status': 'ready', 'environmentState': state}
    with closing(environments.storage.connect(app.data_dir)) as db, db:
        environments.storage.save(db, row)
    app.collaboration.insert('terminal-check', 'fingerprint', {
        'commandAction': 'workspace.tests.submit', 'senderSessionId': target['id'],
        'job': {'status': 'unknown', 'resourceId': row['id']}})
    if blocked:
        with pytest.raises(AppError, match='test environment'):
            await retire(app, source, target)
    else:
        await retire(app, source, target)
    with closing(environments.storage.connect(app.data_dir)) as db:
        assert environments.storage.get(db, target['id'], row['id']) == row
