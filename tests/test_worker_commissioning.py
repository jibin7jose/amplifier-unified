"""One manager commissions many isolated goal-driven roots without setup turns.

Git, creation receipts, task persistence and admission are real owners. The
runtime adapter substitutes only module mounting/provider execution: no paid
model request, native-process restart or model efficacy is claimed here.
"""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from amplifier_web.management import Management
from amplifier_web.service import AppService
from test_collaborative_workspaces import app, Runtime, agent_action, generation, finish, settled, until
from test_peer_creation_operation import commission, plan, initial_accepted
from test_task_continuity import controls
from test_worktrees import repository


class GoalRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.controllers = {}
        self.observed = []
        self.preparing = set()
        self.concurrent_preparations = 0
        self.preparation_barrier = None

    async def start(self, session, emit, *, guard=None, **kwargs):
        if guard:
            guard()
        if self.preparation_barrier and session.get('collaboration', {}).get('checkoutId'):
            self.preparing.add(session['id'])
            self.concurrent_preparations = max(self.concurrent_preparations, len(self.preparing))
            if len(self.preparing) == 10:
                self.preparation_barrier.set()
            await asyncio.wait_for(self.preparation_barrier.wait(), 10)
        if session['id'] not in self.controllers:
            control = controls(session['id'])
            await control.restore()
            self.controllers[session['id']] = control

    async def control(self, sid, operation, args, *, guard=None):
        if guard:
            guard()
        return await self.controllers[sid].perform(operation, args)

    async def collaboration_input(self, session, args, guard, emit):
        if session.get('collaboration', {}).get('checkoutId'):
            saved = json.loads((self.app.data_dir / 'sessions' / session['id'] / 'control-state.json').read_text())
            operation = self.app.collaboration.receipt(session['collaboration']['requestId'])
            checkout = self.app.worktrees.record(session['id'], operation['checkoutId'])
            assert checkout['status'] == 'ready'
            assert session['workingDirectory'] == checkout['path']
            assert Path(checkout['path'], 'a.txt').read_text() == 'original\n'
            assert saved['task']['status'] == 'active'
            assert saved['task']['objective'] == operation['objective']['objective']
            assert saved['goal']['task_id'] == saved['task']['id']
            assert saved['taskReceipts'][operation['requestId'] + ':objective']['receipt']['taskId'] == saved['task']['id']
            self.observed.append((session['id'], checkout['path'], saved['task']['id']))
        return await super().collaboration_input(session, args, guard, emit)

    async def close(self):
        for controller in self.controllers.values():
            await controller.close()
        self.controllers.clear()


def install_runtime(app, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    runtime = app.runtime = GoalRuntime()
    runtime.app = app
    app.management = Management(app)
    return runtime


def execution(app, root):
    return {'kind': 'worktree', 'repositoryPath': str(root),
            'sourceRevision': app.worktrees.git.inspect(root)['sourceRevision']}


async def test_ten_independent_workers_have_placement_and_saved_goals_before_any_input(app, tmp_path, monkeypatch):
    runtime = install_runtime(app, monkeypatch)
    runtime.preparation_barrier = asyncio.Event()
    source = app.state['sessions'][0]
    plan(app, source)
    root = repository(tmp_path)
    placement = execution(app, root)
    selected, view = app.state['selectedSessionId'], copy.deepcopy(app.state['view'])
    requests = await asyncio.gather(*(commission(app, source, f'worker-{i}',
        title=f'Independent change {i}', objective=f'Complete and verify change {i}',
        constraints=['Preserve unrelated work'], execution=placement) for i in range(10)))
    for request in requests:
        await initial_accepted(app, request['initialInputId'])
    assert len(runtime.observed) == 10
    assert runtime.concurrent_preparations == 10
    assert len({row[1] for row in runtime.observed}) == 10
    assert len({row[2] for row in runtime.observed}) == 10
    assert app.state['selectedSessionId'] == selected and app.state['view'] == view
    assert (root / 'a.txt').read_text() == 'original\n'
    assignment = app.assignments.current(source)
    assert len([member for member in assignment['members'].values() if member.get('role') == 'commissioned']) == 10
    # Distinct checkout bytes prove writer isolation, not merely different labels.
    for sid, path, _ in runtime.observed:
        Path(path, 'a.txt').write_text(sid)
    for sid, path, _ in runtime.observed:
        assert Path(path, 'a.txt').read_text() == sid
    for request in requests:
        assert (await commission(app, source, request['requestId'],
            title=f"Independent change {requests.index(request)}",
            objective=f"Complete and verify change {requests.index(request)}",
            constraints=['Preserve unrelated work'], execution=placement))['duplicate']
    assert len(runtime.inputs) == 10 and len(app.worktrees.git.records()) == 10
    # A genuine negative result returns to the manager; correction uses the
    # same worker and original goal instead of another commissioning cycle.
    failed = requests[0]
    target = app._session(failed['sessionId'])
    goal_id = app.coordination.task(target['id'])['id']
    await generation(app, target, 'failed-check', [failed['initialInputId']])
    await agent_action(app, target, 'coordination.reply', {
        'requestId': failed['initialInputId'], 'kind': 'result', 'outcome': 'failed',
        'text': 'The fixture assertion failed; correction is required.'}, 'failure-report')
    await finish(app, target, failed['initialInputId'])
    await app.on_runtime_event('runtime.status', {'sessionId': target['id'], 'status': 'idle'})
    result = await agent_action(app, source, 'coordination.result',
        {'requestId': failed['requestId']}, 'review-failure')
    assert result['result']['initialRequest']['response']['outcome'] == 'failed'
    assert result['result']['qualified'] is False
    # Restore a clean source artifact, as the corrected work itself would do.
    Path(target['workingDirectory'], 'a.txt').write_text('original\n')
    await agent_action(app, source, 'coordination.send', {
        'sessionId': target['id'], 'text': 'Correct the failed assertion and finish the same objective.',
        'mode': 'queue'}, 'correct-existing-worker')
    await settled(app, 'correct-existing-worker', 'accepted')
    assert app.coordination.task(target['id'])['id'] == goal_id
    assert len(app.worktrees.git.records()) == 10
    worker_ids = {request['sessionId'] for request in requests}
    assert len([row for row in runtime.inputs if row[0] in worker_ids]) == 11
    assert len([row for row in runtime.inputs if row[0] == source['id']]) == 1


@pytest.mark.parametrize('stop', [False, True])
async def test_restart_after_checkout_commit_recovers_receipt_without_duplicate_git_or_goal(app, tmp_path, monkeypatch, stop):
    import amplifier_web.worker_commissioning as commissioning
    install_runtime(app, monkeypatch)
    source = app.state['sessions'][0]
    plan(app, source)
    root = repository(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = commissioning.prepare_objective
    async def suspend(*args):
        entered.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(commissioning, 'prepare_objective', suspend)
    request = await commission(app, source, objective='Complete verified work', execution=execution(app, root))
    await asyncio.wait_for(entered.wait(), 5)
    if stop:
        source['interruptionRevision'] = source.get('interruptionRevision', 0) + 1
    app._publish()
    await app.close()
    monkeypatch.setattr(commissioning, 'prepare_objective', original)
    runtime = GoalRuntime()
    reopened = runtime.app = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    reopened.management = Management(reopened)
    try:
        reopened.collaboration.start()
        await settled(reopened, request['requestId'], 'suppressed' if stop else 'created')
        if not stop:
            await initial_accepted(reopened, request['initialInputId'])
            assert len(runtime.observed) == 1
        else:
            assert not runtime.inputs
        assert len(reopened.worktrees.git.records()) == 1
    finally:
        await reopened.close()


async def test_failed_checkout_preserves_owner_and_fresh_corrected_commission_can_run(app, tmp_path, monkeypatch):
    runtime = install_runtime(app, monkeypatch)
    source = app.state['sessions'][0]
    plan(app, source)
    root = repository(tmp_path)
    placement = execution(app, root)
    # A genuine stale-source refusal, not a fake test pass or hidden failure.
    (root / 'a.txt').write_text('new source revision\n')
    refused = await commission(app, source, 'stale', objective='Complete work', execution=placement)
    await settled(app, refused['requestId'], 'unknown')
    assert app._session(refused['sessionId'])['status'] == 'idle'
    assert not any(row[0] == refused['sessionId'] for row in runtime.inputs)
    assert not app.worktrees.git.records()
    corrected = await commission(app, source, 'corrected', objective='Complete work', execution=execution(app, root))
    await initial_accepted(app, corrected['initialInputId'])
    assert len(runtime.observed) == 1
    assert app.collaboration.receipt(refused['requestId'])['delivery'] == 'unknown'


async def test_commissioned_objective_is_not_an_inherited_manager_goal(app, monkeypatch):
    runtime = install_runtime(app, monkeypatch)
    source = app.state['sessions'][0]
    plan(app, source)
    inherited = {'capacity': {'revision': 7, 'enabled': True, 'maxTotalTokens': 500000,
                              'maxCostUsd': 15, 'unknownPolicy': 'pause'}}
    (app.data_dir / 'sessions' / source['id'] / 'control-state.json').write_text(json.dumps(inherited))
    request = await commission(app, source, objective='Complete this independent assignment',
                               constraints=['Only this assignment'])
    await initial_accepted(app, request['initialInputId'])
    target = app._session(request['sessionId'])
    task = app.coordination.task(target['id'])
    assert task['objective'] == 'Complete this independent assignment'
    assert task['constraints'] == ['Only this assignment']
    assert target['workspace'] == source['workspace']
    assert target['workingDirectory'] == source['workspace']
    assert len(runtime.inputs) == 1
    saved = json.loads((app.data_dir / 'sessions' / target['id'] / 'control-state.json').read_text())
    for key, value in inherited['capacity'].items():
        assert saved['capacity'][key] == (0 if key == 'revision' else value)


async def test_restart_after_objective_commit_reuses_exact_task_receipt(app, tmp_path, monkeypatch):
    import amplifier_web.worker_commissioning as commissioning
    install_runtime(app, monkeypatch)
    source = app.state['sessions'][0]
    plan(app, source)
    root = repository(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = commissioning.task_continuity.dispatch
    async def suspend(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(commissioning.task_continuity, 'dispatch', suspend)
    request = await commission(app, source, objective='Finish without resetting consumed work', execution=execution(app, root))
    await asyncio.wait_for(entered.wait(), 5)
    task = copy.deepcopy(app.coordination.task(request['sessionId']))
    await app.close()
    monkeypatch.setattr(commissioning.task_continuity, 'dispatch', original)
    runtime = GoalRuntime()
    reopened = runtime.app = AppService(app.data_dir, runtime, workspace=app.default_workspace)
    reopened.management = Management(reopened)
    try:
        reopened.collaboration.start()
        await initial_accepted(reopened, request['initialInputId'])
        current = reopened.coordination.task(request['sessionId'])
        assert current['id'] == task['id'] and current['revision'] == task['revision']
        assert len(runtime.inputs) == 1
        assert len(reopened.worktrees.git.records()) == 1
    finally:
        await reopened.close()


async def test_outside_repository_is_refused_before_root_or_checkout(app, tmp_path):
    from amplifier_web.service import AppError
    source = app.state['sessions'][0]
    plan(app, source)
    outside = tmp_path.parent / 'outside-commission'
    outside.mkdir()
    root = repository(outside)
    with pytest.raises(AppError, match='inside this task'):
        await commission(app, source, execution=execution(app, root))
    assert len(app.state['sessions']) == 2
    assert not app.worktrees.git.records()


@pytest.mark.parametrize('change', ['stop', 'placement', 'configuration'])
async def test_changed_target_cannot_start_after_checkout_preparation(app, tmp_path, monkeypatch, change):
    import amplifier_web.worker_commissioning as commissioning
    runtime = install_runtime(app, monkeypatch)
    source = app.state['sessions'][0]
    plan(app, source)
    root = repository(tmp_path)
    original = commissioning.prepare_objective
    async def changed(owner, identity):
        target = app._session(owner.receipt(identity)['sessionId'])
        if change == 'stop':
            target['interruptionRevision'] = 1
        elif change == 'placement':
            target['workingDirectory'] = str(root)
        else:
            target['configurationBusy'] = True
        return await original(owner, identity)
    monkeypatch.setattr(commissioning, 'prepare_objective', changed)
    request = await commission(app, source, objective='Do not begin after a changed boundary',
                               execution=execution(app, root))
    await settled(app, request['requestId'], 'unknown')
    assert not any(row[0] == request['sessionId'] for row in runtime.inputs)
    assert len(app.worktrees.git.records()) == 1
    assert app.worktrees.git.records()[0]['sessionId'] == request['sessionId']
