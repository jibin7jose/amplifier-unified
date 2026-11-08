"""Cross-root admission and Stop use actual service actions and retained receipts."""
import asyncio
import copy
import json
import sqlite3

import pytest

from amplifier_web.capacity import admission
from amplifier_web.execution import ingest
from amplifier_web.service import AppError, AppService
from test_collaborative_workspaces import app, agent_action, generation, until, finish, send
from test_peer_creation_operation import plan, commission, initial_accepted


def call(session, identity, **extra):
    return dict(id=identity, sessionId=session['id'], rootSessionId=session['id'],
                kind='llm', phase='running', revision=1, producerId='process', **extra)


async def start(app):
    source = app.state['sessions'][0]
    return (await agent_action(app, source, 'assignment.start', {'sessionId': source['id']}, 'start'))['result']['assignment']


async def limits(app, source, **patch):
    value = app.assignments.current(source)
    return await agent_action(app, source, 'assignment.set', {
        'sessionId': source['id'], 'expectedRevision': value['revision'], **patch}, 'limits-' + str(value['revision']))


async def child(app, source, identity='helper'):
    plan(app, source)
    created = await commission(app, source, identity)
    await initial_accepted(app, created['initialInputId'])
    target = app._session(created['sessionId'])
    await generation(app, target, 'generation-' + identity, [created['initialInputId']])
    return target, created


async def test_new_assignment_has_no_default_count_limits(app):
    value = await start(app)
    assert value['limits']['maxOpenTasks'] is None
    assert value['limits']['maxConcurrentCalls'] is None
    assert value['limits']['maxMembers'] is None
    source = app.state['sessions'][0]
    # A deliberately selected member cap still applies.
    await limits(app, source, maxOpenTasks=100, maxConcurrentCalls=100, maxMembers=1)
    plan(app, source)
    with pytest.raises(AppError, match='assignment member limit'):
        await commission(app, source, 'over-roster')
    assert len(app.assignments.current(source)['members']) == 1


@pytest.mark.parametrize('counts', [(3, 2), (None, None)])
async def test_saved_finite_and_explicit_uncapped_counts_survive_restart(app, counts):
    source = app.state['sessions'][0]
    await start(app)
    await limits(app, source, maxOpenTasks=3, maxConcurrentCalls=2)
    await limits(app, source, maxOpenTasks=counts[0], maxConcurrentCalls=counts[1], maxMembers=counts[0])
    before = copy.deepcopy(app.assignments.current(source))
    app._save()
    await app.close()
    restored = AppService(app.data_dir, workspace=source['workspace'])
    try:
        assert restored.assignments.current(restored._session(source['id'])) == before
    finally:
        await restored.close()


@pytest.mark.parametrize('field,value', [
    ('maxOpenTasks', 0), ('maxOpenTasks', True), ('maxConcurrentCalls', -1),
    ('maxConcurrentCalls', 1.5), ('maxMembers', 0), ('maxMembers', True),
])
async def test_optional_execution_counts_still_validate_values(app, field, value):
    source = app.state['sessions'][0]
    await start(app)
    before = app.assignments.current(source)
    with pytest.raises((AppError, ValueError)):
        await limits(app, source, **{field: value})
    assert app.assignments.current(source) == before


@pytest.mark.parametrize('budget', ['maxTotalTokens', 'maxCostUsd'])
async def test_uncapped_counts_keep_unknown_receipts_and_budget_policy(app, budget):
    source = app.state['sessions'][0]
    await start(app)
    await limits(app, source, maxOpenTasks=None, maxConcurrentCalls=None, **{budget: 100})
    row = call(source, 'unknown-budget-call')
    assert (await admission(app, source['id'], {'call': row}))['allowed']
    from amplifier_web.capacity import restore_observation
    restore_observation(source)
    before = copy.deepcopy(app.assignments.usage(app.assignments.current(source))['receipts'])
    result = await admission(app, source['id'], {'call': call(source, 'blocked-by-budget')})
    assert not result['allowed'] and any('unknown' in reason for reason in result['reasons'])
    assert app.assignments.usage(app.assignments.current(source))['receipts'] == before


async def test_nested_commissioning_cannot_evade_assignment_limits_or_take_control(app):
    source, independent = app.state['sessions']
    value = await start(app)
    await limits(app, source, maxOpenTasks=1)
    target, created = await child(app, source)
    assert app.assignments.current(target)['id'] == value['id']
    assert app.assignments.current(independent) is None
    plan(app, target)
    with pytest.raises(AppError, match='assignment open-task limit'):
        await commission(app, target, 'nested')
    value = app.assignments.current(source)
    with pytest.raises(AppError, match='commissioning owner'):
        await agent_action(app, target, 'assignment.set', {'sessionId': target['id'], 'expectedRevision': value['revision'], 'maxOpenTasks': 8}, 'escalate')
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='nested'").fetchone()
    assert len(app.state['sessions']) == 3


async def test_concurrent_roots_and_native_children_share_atomic_reservations(app):
    source, independent = app.state['sessions']
    target, created = await child(app, source)
    await limits(app, source, maxTotalTokens=100, reserveTokens=60, maxConcurrentCalls=2)
    results = await asyncio.gather(admission(app, source['id'], {'call': call(source, 'one')}),
                                   admission(app, target['id'], {'call': call(target, 'two')}))
    assert sum(row['allowed'] for row in results) == 1
    assert 'unreserved' in results[1]['reasons'][0]
    assert (await admission(app, independent['id'], {'call': call(independent, 'unrelated')}))['allowed']
    ingest(source, {**call(source, 'one'), 'phase': 'completed', 'endedAt': 3, 'revision': 2, 'usage': {'inputTokens': 10, 'outputTokens': 10}})
    target['workers'].append({'id': 'native', 'sessionId': 'native'})
    nested = {**call(target, 'nested'), 'sessionId': 'native'}
    assert (await admission(app, target['id'], {'call': nested}))['allowed']
    assert not (await admission(app, source['id'], {'call': call(source, 'three')}))['allowed']
    usage = app.assignments.usage(app.assignments.current(source))
    assert usage['calls'] == 2
    assert usage['metrics']['grossTotalTokens']['value'] == 20
    assert not (await admission(app, target['id'], {'call': nested}))['allowed']


async def test_unknown_settled_usage_stays_attributed_after_restart_and_late_correction(app):
    source = app.state['sessions'][0]
    value = await start(app)
    await limits(app, source, maxTotalTokens=100, reserveTokens=20)
    row = call(source, 'unknown')
    assert (await admission(app, source['id'], {'call': row}))['allowed']
    app._save()
    await app.close()
    other = AppService(app.data_dir, workspace=source['workspace'])
    try:
        saved = other._session(source['id'])
        result = await admission(other, saved['id'], {'call': call(saved, 'next')})
        assert not result['allowed'] and any('unknown' in text for text in result['reasons'])
        ingest(saved, {**row, 'phase': 'completed', 'endedAt': 4, 'revision': 3, 'usage': {'totalTokens': 30}})
        assert other.assignments.usage(other.assignments.current(saved))['metrics']['grossTotalTokens']['value'] == 30
        assert (await admission(other, saved['id'], {'call': call(saved, 'next')}))['allowed']
    finally:
        await other.close()


async def test_stop_fences_before_interrupt_and_preserves_independent_root_and_drafts(app):
    source, independent = app.state['sessions']
    target, created = await child(app, source)
    await generation(app, independent, 'independent', ['human-input'])
    await app.on_runtime_event('runtime.status', {'sessionId': independent['id'], 'status': 'working'})
    before = copy.deepcopy(independent)
    release, entered = asyncio.Event(), asyncio.Event()
    original = app.runtime.stop
    async def delayed(sid):
        entered.set()
        await release.wait()
        return await original(sid)
    app.runtime.stop = delayed
    value = app.assignments.current(source)
    args = {'sessionId': source['id'], 'expectedRevision': value['revision']}
    result = await app.dispatch('assignment.stop', args, command_id='stop')
    await entered.wait()
    try:
        assert app.assignments.current(target)['status'] == 'stopped'
        assert not (await admission(app, target['id'], {'call': call(target, 'late')}))['allowed']
        assert app.collaboration.guard(app.collaboration.receipt(created['initialInputId']))
        assert (await app.dispatch('assignment.stop', args, command_id='stop'))['duplicate']
        assert independent == before
        assert app.state['view']['draft'] == 'Private unsent draft'
        assert app.state['selectedSessionId'] == source['id']
        with pytest.raises(AppError, match='settle'):
            await app.dispatch('assignment.detach', {'sessionId': target['id'], 'expectedRevision': value['revision'] + 1})
    finally:
        release.set()
    await until(lambda: all(v == 'returned' for v in app.collaboration.receipt('stop')['interruptions'].values()))
    assert independent['status'] == 'working'
    assert set(result['interruptions']) == {source['id'], target['id']}


async def test_detach_is_user_only_retains_usage_and_allows_new_independent_work(app):
    source = app.state['sessions'][0]
    target, created = await child(app, source)
    assert (await admission(app, target['id'], {'call': call(target, 'charge')}))['allowed']
    ingest(target, {**call(target, 'charge'), 'phase': 'completed', 'endedAt': 4, 'revision': 2, 'usage': {'totalTokens': 15}})
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
    value = app.assignments.current(source)
    args = {'sessionId': target['id'], 'expectedRevision': value['revision']}
    with pytest.raises(AppError, match='Only the user'):
        await agent_action(app, target, 'assignment.detach', args, 'escape')
    await app.dispatch('assignment.detach', args, command_id='detach')
    assert app.assignments.current(target) is None
    assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 15
    assert (await admission(app, target['id'], {'call': call(target, 'new-unrelated')}))['allowed']
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 1
    value = app.assignments.current(source)
    stopped = await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']}, command_id='stop')
    assert target['id'] not in stopped['interruptions']


async def test_stale_child_and_independent_principals_cannot_change_assignment(app):
    source, peer = app.state['sessions']
    value = await start(app)
    await generation(app, peer, 'peer', ['human-input'])
    args = {'sessionId': source['id'], 'expectedRevision': value['revision']}
    for transport in ({'_runtimeSessionId': 'native-child'}, {'_generationId': 'old-generation'}):
        with pytest.raises(AppError):
            await app.app_bridge('dispatch', {'action': 'assignment.stop', 'args': args, 'id': 'bad',
                '_runtimeSessionId': source['id'], '_generationId': source['collaborationGeneration']['id'],
                '_inputBindings': [{'inputId': value} for value in source['collaborationGeneration']['inputIds']],
                **transport}, source['id'])
    with pytest.raises(AppError):
        await agent_action(app, peer, 'assignment.stop', args, 'foreign')
    with pytest.raises(AppError, match='revision changed'):
        await app.dispatch('assignment.set', {**args, 'expectedRevision': 0}, command_id='stale')
    assert app.assignments.current(source)['status'] == 'active'


async def test_legacy_commissioning_never_enrolled_by_inference(app):
    source, peer = app.state['sessions']
    peer['collaboration'] = {'creatorSessionId': source['id'], 'requestId': 'old'}
    await start(app)
    assert app.assignments.current(peer) is None
    assert len(app.assignments.current(source)['members']) == 1


@pytest.mark.parametrize('action', ['assignment.start', 'assignment.stop', 'assignment.set'])
async def test_failed_receipt_rolls_back_authority_and_never_interrupts(app, action):
    source = app.state['sessions'][0]
    if action != 'assignment.start':
        await start(app)
    before = copy.deepcopy(source)
    original = copy.deepcopy(app.assignments.current(source))
    app.db.execute("""CREATE TEMP TRIGGER reject_assignment BEFORE INSERT ON commands
        WHEN NEW.id='failed' BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END""")
    args = {'sessionId': source['id'], 'expectedRevision': 1, **({'maxOpenTasks': 1} if action.endswith('set') else {})}
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        await app.dispatch(action, {'sessionId': source['id']} if action.endswith('start') else args, command_id='failed')
    assert source == before
    assert app.assignments.current(source) == original
    assert not app.assignments.interrupting
    assert not app.db.in_transaction


async def test_stop_committed_before_lost_ack_still_requests_interruption_once(app, monkeypatch):
    source = app.state['sessions'][0]
    value = await start(app)
    original = app._publish_changes
    def lost(**kw):
        original(**kw)
        raise ConnectionError('lost after durable commit')
    monkeypatch.setattr(app, '_publish_changes', lost)
    with pytest.raises(ConnectionError):
        await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']}, command_id='lost')
    monkeypatch.setattr(app, '_publish_changes', original)
    await until(lambda: app.collaboration.receipt('lost')['interruptions'][source['id']] != 'requested')
    assert app.assignments.current(source)['status'] == 'stopped'
    assert source['status'] == 'stopped'


async def test_stop_during_preparation_creates_no_root(app, monkeypatch):
    source = app.state['sessions'][0]
    plan(app, source)
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.collaboration._finish_create
    async def pause(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(app.collaboration, '_finish_create', pause)
    created = await commission(app, source)
    await entered.wait()
    value = app.assignments.current(source)
    await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']}, command_id='stop')
    release.set()
    await until(lambda: app.collaboration.receipt(created['requestId'])['delivery'] == 'suppressed')
    # The resumed preparation must recheck authority before the root effect.
    assert not any(row['id'] == created['sessionId'] for row in app.state['sessions'])


async def test_late_result_after_stop_retains_evidence_without_continuation(app):
    source = app.state['sessions'][0]
    target, created = await child(app, source)
    await agent_action(app, target, 'coordination.reply', {'requestId': created['initialInputId'],
        'kind': 'result', 'outcome': 'success', 'text': 'Late result'}, 'reply')
    value = app.assignments.current(source)
    await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']}, command_id='stop')
    await finish(app, target, created['initialInputId'])
    request = app.collaboration.receipt(created['initialInputId'])
    assert request['response']['text'] == 'Late result'
    assert not app.db.execute('SELECT 1 FROM commands WHERE id=?', (created['subscription']['continuationId'],)).fetchone()


async def test_host_reservation_counts_child_before_topology_event(app):
    source = app.state['sessions'][0]
    await start(app)
    await limits(app, source, maxConcurrentCalls=1)
    row = {**call(source, 'early-child'), 'sessionId': 'child-not-yet-observed'}
    assert (await admission(app, source['id'], {'call': row}))['allowed']
    assert not (await admission(app, source['id'], {'call': call(source, 'root')}))['allowed']


async def test_failed_admission_commit_leaves_no_phantom_reservation(app, monkeypatch):
    source = app.state['sessions'][0]
    await start(app)
    await limits(app, source, maxConcurrentCalls=1)
    source['capacityProducerId'] = 'process'
    original = app._save_changes
    def failed(**kwargs):
        app.db.rollback()
        raise OSError('no admission commit')
    monkeypatch.setattr(app, '_save_changes', failed)
    with pytest.raises(OSError, match='no admission'):
        await admission(app, source['id'], {'call': call(source, 'not-sent')})
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 0
    monkeypatch.setattr(app, '_save_changes', original)
    assert (await admission(app, source['id'], {'call': call(source, 'not-sent')}))['allowed']


async def test_history_edit_and_late_correction_retain_original_assignment_charge(app):
    source = app.state['sessions'][0]
    value = await start(app)
    row = call(source, 'charged')
    assert (await admission(app, source['id'], {'call': row}))['allowed']
    source['execution']['retiredUsageNodes'] = source['execution']['nodes']
    source['execution']['nodes'] = []
    ingest(source, {**row, 'phase': 'completed', 'endedAt': 5, 'revision': 3, 'usage': {'totalTokens': 17}})
    usage = app.assignments.usage(value)
    assert usage['calls'] == 1 and usage['metrics']['grossTotalTokens']['value'] == 17


async def test_lost_admission_ack_keeps_committed_reservation(app, monkeypatch):
    source = app.state['sessions'][0]
    await start(app)
    await limits(app, source, maxConcurrentCalls=1)
    source['capacityProducerId'] = 'process'
    original = app._save_changes
    def lost(**kwargs):
        original(**kwargs)
        raise ConnectionError('lost committed admission')
    monkeypatch.setattr(app, '_save_changes', lost)
    with pytest.raises(ConnectionError):
        await admission(app, source['id'], {'call': call(source, 'committed')})
    monkeypatch.setattr(app, '_save_changes', original)
    assert app.assignments.usage(app.assignments.current(source))['calls'] == 1
    assert not (await admission(app, source['id'], {'call': call(source, 'committed')}))['allowed']
    assert not (await admission(app, source['id'], {'call': call(source, 'another')}))['allowed']


async def test_native_display_reconstruction_preserves_host_admission_and_late_accounting(app):
    from amplifier_web.session_projection import stored_execution, accounting_projection
    source = app.state['sessions'][0]
    value = await start(app)
    row = call(source, 'charged')
    assert (await admission(app, source['id'], {'call': row}))['allowed']
    ingest(source, {**row, 'liveObservation': True, 'phase': 'completed', 'endedAt': 5,
                    'revision': 2, 'usage': {'totalTokens': 17}})
    source['execution'] = stored_execution(source['execution'])
    assert source['execution']['nodes'] == []
    assert source['execution']['retiredUsageNodes'][0]['assignmentId'] == value['id']
    # Canonical display data is rebuildable; only newer observed usage updates
    # the retained call. The original host binding remains authoritative.
    source['execution']['nodes'] = [{**row, 'liveObservation': True, 'canonicalHistory': True,
        'revision': 3, 'phase': 'completed', 'endedAt': 6, 'usage': {'totalTokens': 19}}]
    source['execution']['retiredUsageNodes'] = accounting_projection(source['execution'])
    usage = app.assignments.usage(value)
    assert usage['calls'] == 1 and usage['metrics']['grossTotalTokens']['value'] == 19


async def test_limited_assignment_cannot_start_unaccounted_independent_work(app):
    source, independent = app.state['sessions']
    await start(app)
    await limits(app, source, maxTotalTokens=100)
    with pytest.raises(AppError, match='request-scoped assignment budget'):
        await send(app)
    assert not app.runtime.inputs
    notified = await send(app, identity='notice', mode='notify')
    assert notified['delivery'] == 'notified' and not app.runtime.inputs


async def test_stop_reports_request_only_adapter_limit_without_stopping_independent_chat(app):
    source, independent = app.state['sessions']
    await start(app)
    request = await send(app)
    await until(lambda: app.collaboration.receipt(request['requestId'])['delivery'] == 'accepted')
    value = app.assignments.current(source)
    receipt = await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']}, command_id='stop')
    assert receipt['result']['independentWork']['interruptionSupported'] is False
    assert receipt['result']['independentWork']['requests'][0]['requestId'] == request['requestId']
    assert independent['id'] not in receipt['interruptions']
    assert independent['status'] == 'working'


async def test_restart_preserves_stop_and_uncertain_interruptions_without_replay(app, monkeypatch):
    source = app.state['sessions'][0]
    value = await start(app)
    entered = asyncio.Event()
    async def hold(identity, stops):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(app.assignments, 'interrupt', hold)
    args = {'sessionId': source['id'], 'expectedRevision': value['revision']}
    await app.dispatch('assignment.stop', args, command_id='pending-stop')
    await entered.wait()
    await app.close()
    restored = AppService(app.data_dir, workspace=source['workspace'])
    try:
        receipt = await restored.dispatch('assignment.stop', args, command_id='pending-stop')
        assert receipt['duplicate']
        assert set(receipt['interruptions'].values()) == {'unknown'}
        assert not restored.assignments.interrupting
        assert not (await admission(restored, source['id'], {'call': call(source, 'late')}))['allowed']
    finally:
        await restored.close()


async def test_new_task_schedule_inherits_assignment_and_stop_suppresses_future_occurrences(tmp_path, monkeypatch):
    from test_schedule_new_tasks import fixture, create
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        value = (await app.dispatch('assignment.start', {'sessionId': sid}, command_id='assignment'))['result']['assignment']
        record, preview = await create(app, sid, now[0])
        now[0] += 61
        await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        target = app._session(run['destinationSessionId'])
        assert app.assignments.current(target)['id'] == value['id']
        value = app.assignments.current(app._session(sid))
        await app.dispatch('assignment.stop', {'sessionId': sid, 'expectedRevision': value['revision']}, command_id='stop')
        now[0] += 60
        await app.schedules.tick()
        assert len(runtime.inputs) == 1
    finally:
        await app.close()


async def test_member_reservation_crosses_old_roster_ceiling_without_losing_history(app):
    source = app.state['sessions'][0]
    await start(app)
    for index in range(65):
        app.assignments.reserve_member(source, f'reserved-{index}', f'create-{index}')
    before = copy.deepcopy(app.assignments.current(source))
    assert app.assignments.active_member_count(before) == 66
    # Operators may choose a larger explicit ceiling or remove a previous one.
    await limits(app, source, maxMembers=66)
    with pytest.raises(AppError, match='assignment member limit'):
        app.assignments.reserve_member(source, 'next-member', 'create-next')
    await limits(app, source, maxMembers=None)
    app.assignments.reserve_member(source, 'next-member', 'create-next')
    after = app.assignments.current(source)
    assert all(after['members'][key] == row for key, row in before['members'].items())
    assert app.assignments.active_member_count(after) == 67
