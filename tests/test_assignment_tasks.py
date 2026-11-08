"""Shared UI/agent task actions qualify ownership, not automatic tool execution."""
import asyncio
import copy
import sqlite3

import pytest

from amplifier_web.service import AppError, AppService, ACTION_DEFINITIONS
from test_assignments import app, child, start
from test_collaborative_workspaces import agent_action, generation


async def create(app, owner, title='Check draft', dependencies=(), identity=None):
    revision = app.assignments.current(owner)['revision']
    return (await app.dispatch('assignment.tasks.create', {'sessionId': owner['id'],
        'expectedAssignmentRevision': revision, 'title': title, 'dependsOn': list(dependencies)},
        command_id=identity or 'create-' + str(revision)))['result']['task']


async def change(app, actor, verb, task, identity=None, agent=False, **extra):
    args = {'sessionId': actor['id'], 'taskId': task['id'], 'expectedRevision': task['revision'], **extra}
    if verb in {'release', 'complete'}:
        args.setdefault('claimId', task['claim']['id'])
    identity = identity or verb + '-' + str(task['revision']) + '-' + actor['id']
    result = await agent_action(app, actor, 'assignment.tasks.' + verb, args, identity) if agent else await app.dispatch('assignment.tasks.' + verb, args, command_id=identity)
    return result


async def test_atomic_agent_self_claim_has_one_winner_and_historical_retry(app):
    owner = app.state['sessions'][0]
    a, _ = await child(app, owner, 'a'); b, _ = await child(app, owner, 'b')
    task = await create(app, owner)
    results = await asyncio.gather(change(app, a, 'claim', task, 'claim-a', agent=True),
                                   change(app, b, 'claim', task, 'claim-b', agent=True), return_exceptions=True)
    assert sum(isinstance(row, dict) for row in results) == 1
    assert sum(isinstance(row, AppError) and row.status == 409 for row in results) == 1
    winner = a if isinstance(results[0], dict) else b
    receipt = next(row for row in results if isinstance(row, dict))
    claimed = receipt['result']['task']
    assert claimed['claim']['sessionId'] == winner['id'] and claimed['claim']['status'] == 'current'
    await change(app, winner, 'release', claimed, agent=True)
    retry = await change(app, winner, 'claim', task, 'claim-a' if winner is a else 'claim-b', agent=True)
    assert retry['duplicate'] and retry['historical'] and retry['result'] == receipt['result']
    assert app.assignments.tasks.get(app.assignments.current(owner), task['id'])['status'] == 'open'
    assert 'tasks' not in receipt['result']  # A command does not duplicate the entire board.


async def test_dependencies_blocks_cycles_and_immutable_completion(app):
    owner = app.state['sessions'][0]; await start(app)
    first = await create(app, owner, 'Inspect source')
    second = await create(app, owner, 'Review report', [first['id']])
    with pytest.raises(AppError, match='blocked'):
        await change(app, owner, 'claim', second)
    with pytest.raises(AppError, match='cycle'):
        await change(app, owner, 'update', first, dependsOn=[second['id']])
    claimed = (await change(app, owner, 'claim', first))['result']['task']
    with pytest.raises(AppError, match='Release'):
        await change(app, owner, 'update', claimed, description='Change acceptance')
    done = (await change(app, owner, 'complete', claimed, report='Source checked; no artifact verification claimed.'))['result']['task']
    assert done['completion']['verification'] == 'participant_report'
    listing = (await app.dispatch('assignment.tasks.list', {'sessionId': owner['id']}))['result']
    assert next(t for t in listing['tasks'] if t['id'] == second['id'])['readiness'] == 'ready'
    for verb, patch in [('update', {'dependsOn': []}), ('cancel', {}), ('reassign', {'holderSessionId': owner['id']})]:
        with pytest.raises(AppError, match='cannot be changed'):
            await change(app, owner, verb, done, **patch)


async def test_explicit_block_and_cancelled_dependency_do_not_unlock(app):
    owner = app.state['sessions'][0]; await start(app)
    task = await create(app, owner)
    blocked = (await change(app, owner, 'update', task, blockedReason='Need source approval'))['result']['task']
    assert blocked['readiness'] == 'blocked'
    with pytest.raises(AppError, match='blocked'):
        await change(app, owner, 'claim', blocked)
    dependent = await create(app, owner, 'Dependent', [task['id']])
    await change(app, owner, 'cancel', blocked)
    with pytest.raises(AppError, match='blocked'):
        await change(app, owner, 'claim', dependent)


async def test_reassignment_fences_old_holder_and_does_not_send_or_stop(app):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    task = await create(app, owner)
    claimed = (await change(app, peer, 'claim', task, agent=True))['result']['task']
    inputs = copy.deepcopy(app.runtime.inputs); before = peer['status']
    reassigned = (await change(app, owner, 'reassign', claimed, holderSessionId=owner['id']))['result']['task']
    assert reassigned['claim']['id'] != claimed['claim']['id']
    assert app.runtime.inputs == inputs and peer['status'] == before
    with pytest.raises(AppError, match='revision changed'):
        await change(app, peer, 'complete', claimed, report='Late report', agent=True)
    with pytest.raises(AppError, match='claim ID'):
        await change(app, peer, 'complete', reassigned, claimId=claimed['claim']['id'], report='Forged current revision', agent=True)
    with pytest.raises(AppError, match='Only the claim holder'):
        await change(app, peer, 'complete', reassigned, report='Borrow current claim', agent=True)


async def test_stop_then_new_generation_does_not_restore_old_claim(app):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    task = await create(app, owner); claimed = (await change(app, peer, 'claim', task, agent=True))['result']['task']
    await app.dispatch('conversation.stop', {'sessionId': peer['id']}, command_id='stop-holder')
    listing = (await app.dispatch('assignment.tasks.read', {'sessionId': owner['id'], 'taskId': task['id']}))['result']['task']
    assert listing['claim']['status'] == 'stale'
    epoch = peer['interruptionRevision']
    await generation(app, peer, 'explicit-later-turn', ['new-authorized-input'], interruption_revision=epoch)
    assert peer['collaborationGeneration'] == {
        'id': 'explicit-later-turn', 'inputIds': ['new-authorized-input'], 'terminal': False, 'interruptionRevision': epoch}
    with pytest.raises(AppError, match='stale'):
        await change(app, peer, 'complete', claimed, report='Late completion', agent=True)
    await change(app, owner, 'release', claimed)
    assert app.assignments.tasks.get(app.assignments.current(owner), task['id'])['status'] == 'open'


async def test_detachment_blocks_reads_retries_and_keeps_item_exclusive(app):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    task = await create(app, owner); await change(app, peer, 'claim', task, 'original-claim', agent=True)
    peer['status'] = 'idle';peer['workers'] = []
    value = app.assignments.current(owner)
    await app.dispatch('assignment.detach', {'sessionId': peer['id'], 'expectedRevision': value['revision']})
    with pytest.raises(AppError, match='no assignment'):
        await app.dispatch('assignment.tasks.read', {'sessionId': peer['id'], 'taskId': task['id']})
    with pytest.raises(AppError, match='bound assignment member'):
        await change(app, peer, 'claim', task, 'original-claim', agent=True)
    current = (await app.dispatch('assignment.tasks.read', {'sessionId': owner['id'], 'taskId': task['id']}))['result']['task']
    assert current['claim']['status'] == 'stale' and current['status'] == 'claimed'
    with pytest.raises(AppError, match='already claimed'):
        await change(app, owner, 'claim', current)


async def test_assignment_stop_preserves_claim_and_only_explicit_cleanup(app):
    owner = app.state['sessions'][0]; await start(app)
    task = await create(app, owner); claimed = (await change(app, owner, 'claim', task))['result']['task']
    value = app.assignments.current(owner)
    await app.dispatch('assignment.stop', {'sessionId': owner['id'], 'expectedRevision': value['revision']})
    with pytest.raises(AppError, match='stopped'):
        await change(app, owner, 'complete', claimed, report='Not admitted')
    released = (await change(app, owner, 'release', claimed))['result']['task']
    with pytest.raises(AppError, match='stopped'):
        await change(app, owner, 'claim', released)
    await change(app, owner, 'cancel', released)


async def test_restart_retains_responsibility_and_later_turn_can_complete(app):
    owner = app.state['sessions'][0]; await start(app)
    task = await create(app, owner); claimed = (await change(app, owner, 'claim', task, agent=True))['result']['task']
    await app.close()
    restored = AppService(app.data_dir, workspace=owner['workspace'])
    try:
        row = restored._session(owner['id'])
        result = (await restored.dispatch('assignment.tasks.read', {'sessionId': row['id'], 'taskId': task['id']}))['result']['task']
        assert result['claim']['id'] == claimed['claim']['id'] and result['claim']['status'] == 'current'
        await generation(restored, row, 'after-restart', ['explicit-new-input'])
        done = await change(restored, row, 'complete', result, agent=True, report='Explicit completion after restart')
        assert done['result']['task']['status'] == 'completed'
    finally:
        await restored.close()


async def test_foreign_principal_owner_management_and_independent_reads(app):
    owner, independent = app.state['sessions']; peer, _ = await child(app, owner)
    task = await create(app, owner)
    assert (await app.dispatch('assignment.tasks.list', {'sessionId': independent['id']}))['result']['tasks'] == []
    with pytest.raises(AppError, match='calling conversation'):
        await agent_action(app, peer, 'assignment.tasks.read', {'sessionId': owner['id'], 'taskId': task['id']}, 'foreign-source')
    with pytest.raises(AppError, match='commissioning owner'):
        await change(app, peer, 'cancel', task, agent=True)
    with pytest.raises(AppError):
        await app.app_bridge('dispatch', {'action': 'assignment.tasks.claim', 'args': {'sessionId': owner['id'], 'taskId': task['id'], 'expectedRevision': task['revision']}, 'id': 'forged-child', '_runtimeSessionId': 'native-child', '_generationId': owner['collaborationGeneration']['id']}, owner['id'])
    with pytest.raises(AppError, match='Choose a current bound'):
        await change(app, owner, 'reassign', task, holderSessionId=independent['id'])
    assert app.assignments.current(independent) is None


async def test_task_reads_use_metadata_without_usage_or_worker(app, monkeypatch):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    task = await create(app, owner)
    monkeypatch.setattr(app.assignments, 'usage', lambda *a: (_ for _ in ()).throw(AssertionError('No usage/history scan')))
    monkeypatch.setattr(app.cold_display, 'hydrate', lambda *a: (_ for _ in ()).throw(AssertionError('No history hydration')))
    inputs = copy.deepcopy(app.runtime.inputs)
    result = (await app.dispatch('assignment.tasks.list', {'sessionId': peer['id']}))['result']
    assert result['tasks'][0]['title'] == task['title']
    assert all(row['label'] for row in result['members'])
    assert app.runtime.inputs == inputs


async def test_commit_failure_rolls_back_task_and_retry_applies_once(app, monkeypatch):
    owner = app.state['sessions'][0]; await start(app); task = await create(app, owner)
    original = app._publish_changes
    def fail(**kwargs):
        raise sqlite3.OperationalError('Injected durable commit failure')
    monkeypatch.setattr(app, '_publish_changes', fail)
    with pytest.raises(sqlite3.OperationalError):
        await change(app, owner, 'claim', task, 'retry-claim')
    assert app.assignments.tasks.get(app.assignments.current(owner), task['id'])['status'] == 'open'
    assert not app.db.execute("SELECT 1 FROM commands WHERE id='retry-claim'").fetchone()
    monkeypatch.setattr(app, '_publish_changes', original)
    first = await change(app, owner, 'claim', task, 'retry-claim')
    second = await change(app, owner, 'claim', task, 'retry-claim')
    assert second['duplicate'] and second['result'] == first['result']


async def test_lost_acknowledgement_retains_exact_claim(app, monkeypatch):
    owner = app.state['sessions'][0]; await start(app); task = await create(app, owner)
    original = app._publish_changes
    def lost(**kwargs):
        original(**kwargs)
        raise ConnectionError('Lost response after commit')
    monkeypatch.setattr(app, '_publish_changes', lost)
    with pytest.raises(ConnectionError):
        await change(app, owner, 'claim', task, 'lost-claim')
    current = app.assignments.tasks.get(app.assignments.current(owner), task['id'])
    monkeypatch.setattr(app, '_publish_changes', original)
    retry = await change(app, owner, 'claim', task, 'lost-claim')
    assert retry['duplicate'] and retry['result']['task']['claim']['id'] == current['claim']['id']


def test_task_schemas_are_discoverable():
    verbs = {'list', 'read', 'wait', 'create', 'update', 'claim', 'release', 'complete', 'cancel', 'reassign'}
    assert all('assignment.tasks.' + verb in ACTION_DEFINITIONS for verb in verbs)


async def test_scoped_wait_observes_peer_claim_without_loading_history(app):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    task = await create(app, owner)
    initial = (await app.dispatch('assignment.tasks.list', {'sessionId': owner['id']}))['result']
    waiting = asyncio.create_task(app.dispatch('assignment.tasks.wait', {'sessionId': owner['id'], 'afterVersion': initial['version'], 'waitMs': 1000}))
    await asyncio.sleep(0)
    await change(app, peer, 'claim', task, agent=True)
    result = await waiting
    assert result['changed'] and not result['timedOut']
    assert result['result']['tasks'][0]['claim']['sessionId'] == peer['id']


async def test_scoped_wait_drops_contents_after_detachment(app):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    await create(app, owner, 'Private assignment item')
    initial = (await app.dispatch('assignment.tasks.list', {'sessionId': peer['id']}))['result']
    waiting = asyncio.create_task(app.dispatch('assignment.tasks.wait', {'sessionId': peer['id'], 'afterVersion': initial['version'], 'waitMs': 1000}))
    await asyncio.sleep(0)
    peer['status'] = 'idle';peer['workers'] = []
    await app.dispatch('assignment.detach', {'sessionId': peer['id'], 'expectedRevision': app.assignments.current(owner)['revision']})
    result = await waiting
    assert result['changed'] and result['result']['assignmentId'] is None and result['result']['tasks'] == []


async def test_queued_completion_rechecks_replaced_source_after_lock(app):
    owner = app.state['sessions'][0]; await start(app)
    task = await create(app, owner); claimed = (await change(app, owner, 'claim', task, agent=True))['result']['task']
    await app.lock.acquire()
    waiting = asyncio.create_task(change(app, owner, 'complete', claimed, agent=True, report='Delayed completion'))
    try:
        await asyncio.sleep(0)
        replacement = copy.deepcopy(owner)
        replacement['collaborationGeneration']['terminal'] = True
        replacement['interruptionRevision'] = owner.get('interruptionRevision', 0) + 1
        index = next(i for i, row in enumerate(app.state['sessions']) if row['id'] == owner['id'])
        app.state['sessions'][index] = replacement
    finally:
        app.lock.release()
    with pytest.raises(AppError, match='current transport-bound'):
        await waiting
    assert app.assignments.tasks.get(app.assignments.current(replacement), task['id'])['status'] == 'claimed'


@pytest.mark.parametrize('state,label', [('blocked', 'Work blocked'), ('paused', 'Task paused'),
    ('stopped', 'Stopped'), ('error', 'Needs attention'), ('approval', 'Approval requested')])
async def test_worker_observation_is_live_but_never_changes_claim_or_readiness(app, monkeypatch, state, label):
    owner = app.state['sessions'][0]; peer, _ = await child(app, owner)
    task = await create(app, owner)
    claimed = (await change(app, peer, 'claim', task, agent=True))['result']['task']
    before = copy.deepcopy(app.assignments.current(owner))
    initial = (await app.dispatch('assignment.tasks.list', {'sessionId': owner['id']}))['result']
    monkeypatch.setattr(app.cold_display, 'hydrate', lambda *a: (_ for _ in ()).throw(AssertionError('No history hydration')))
    waiting = asyncio.create_task(app.dispatch('assignment.tasks.wait', {
        'sessionId': owner['id'], 'afterVersion': initial['version'], 'waitMs': 1000}))
    await asyncio.sleep(0)
    peer['status'] = state if state in {'stopped', 'error'} else 'idle'
    if state in {'blocked', 'paused'}:
        peer['task'] = {'id': 'goal', 'revision': 2, 'status': state, 'blockedReason': 'Waiting for owner decision'}
    elif state == 'approval':
        peer['approvals'] = [{'id': 'pending', 'status': 'pending'}]
    app.coordination.notify()
    observed = await waiting
    projected = observed['result']['tasks'][0]
    assert observed['changed'] and not observed['timedOut']
    assert projected['worker']['label'] == label
    assert projected['worker']['blockedReason'] == ('Waiting for owner decision' if state == 'blocked' else '')
    assert projected['status'] == 'claimed' and projected['readiness'] == 'ready'
    assert projected['claim'] == claimed['claim']
    assert observed['result']['assignmentRevision'] == initial['assignmentRevision']
    assert app.assignments.current(owner) == before
