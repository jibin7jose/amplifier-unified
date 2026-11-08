"""Lost RPC acknowledgement with real task persistence and native lease fencing.

The transport/coordinator are synthetic; Core/live-loop coverage is maintained
separately in test_assignment_readmission_native.py. No provider or process claim.
"""
import copy
import json
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest

from amplifier_operations.coordination import fingerprint
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppError, AppService
from amplifier_web.task_continuity import CONTROL_BYTES
from test_assignments import app, agent_action
from test_assignment_task_limits import limited
from test_assignment_task_completion import arguments, complete
from test_assignment_retirement import (adoption_args, full_access, reviewed_args,
                                       retire, save_task, settle, setup)
from test_collaborative_workspaces import Runtime


@pytest.fixture
async def member(limited):
    ctx = limited
    await settle(ctx.app, ctx.target)
    manager = RuntimeManager()
    ctx.app.runtime.stopped_boundary = manager.stopped_boundary
    try:
        yield ctx
    finally:
        await manager.close()


@asynccontextmanager
async def reopened(ctx, restart):
    if not restart:
        yield ctx.app, ctx.source, ctx.target
        return
    original = ctx.app
    original.management = None  # Restore the transport fixture's original owner.
    await original.close()
    runtime = Runtime()
    runtime.stopped_boundary = original.runtime.stopped_boundary
    restored = runtime.app = AppService(original.data_dir, runtime, workspace=ctx.source['workspace'])
    try:
        yield restored, restored._session(ctx.source['id']), restored._session(ctx.target['id'])
    finally:
        await restored.close()


def lose_reply(ctx):
    original = ctx.app.runtime.control

    async def lost(sid, operation, values, *, guard=None):
        await original(sid, operation, values, guard=guard)
        raise TimeoutError('Lost after canonical task commit')

    ctx.app.runtime.control = lost


async def retire_ui(app, source, target):
    return await app.dispatch('assignment.retire', {
        'sessionId': source['id'], 'targetSessionId': target['id'],
        'expectedRevision': app.assignments.current(source)['revision']},
        command_id='retire-after-recovery', origin='ui')


@pytest.mark.parametrize('restart', [False, True])
@pytest.mark.parametrize('retry', [False, True])
async def test_lost_completion_retry_publishes_but_retirement_does_not_hydrate(member, retry, restart):
    ctx = member
    request = arguments(ctx)
    stale = copy.deepcopy(ctx.target['task'])
    lose_reply(ctx)
    with pytest.raises(TimeoutError):
        await complete(ctx, request)
    if retry:
        assert (await complete(ctx, request))['duplicate']
    expected_projection = ctx.native.tasks.record() if retry else stale
    assert len(ctx.calls) == 1 and ctx.target['task'] == expected_projection
    ctx.app._save()
    saved = ctx.native.state_path().read_bytes()
    task = json.loads(saved)['task']
    assert task['status'] == 'completed'
    async with reopened(ctx, restart) as (app, source, target):
        before = copy.deepcopy(target)
        receipt = await retire_ui(app, source, target)
        assert receipt['reuseBinding']['targetTask'] == fingerprint(task)
        assert receipt['result']['inputSent'] is False
        assert app.assignments.current(source)['members'][target['id']]['status'] == 'retired'
        assert target['task'] == expected_projection
        for key in ('messages', 'lastInterruption', 'interruptionRevision'):
            assert target.get(key) == before.get(key)
        assert not app.runtime.inputs
    assert ctx.native.state_path().read_bytes() == saved and len(ctx.calls) == 1


@pytest.mark.parametrize('restart', [False, True])
async def test_lost_successor_cannot_retire_stale_completed_projection(member, restart):
    ctx = member
    await complete(ctx)
    stale = copy.deepcopy(ctx.target['task'])
    ctx.app._save()
    lose_reply(ctx)
    with pytest.raises(TimeoutError):
        await ctx.app.dispatch('task.create', {
            'sessionId': ctx.target['id'], 'expectedRevision': stale['revision'],
            'objective': 'Unfinished successor'}, command_id='successor', origin='ui')
    saved = ctx.native.state_path().read_bytes()
    assert json.loads(saved)['task']['status'] == 'active'
    async with reopened(ctx, restart) as (app, source, target):
        before = copy.deepcopy(app.assignments.current(source))
        with pytest.raises(AppError, match='saved task'):
            await retire_ui(app, source, target)
        assert app.assignments.current(source) == before
        assert target['task'] == stale and not app.runtime.inputs
    assert ctx.native.state_path().read_bytes() == saved


@pytest.mark.parametrize('broken', ['missing', 'json', 'task-field', 'task-shape'])
async def test_unavailable_saved_authority_never_uses_cached_completion(app, broken, monkeypatch):
    source, target, _ = await setup(app)
    target['task'] = await save_task(app, target, 'completed')
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    if broken == 'missing':
        path.unlink()
    else:
        path.write_text({'json': '{', 'task-field': '{}', 'task-shape': '{"task":{}}'}[broken])
    from amplifier_web import assignment_adoption
    async def no_retirement(*args, **kwargs):
        pytest.fail('Unknown task authority must refuse before retiring an idle worker')
    monkeypatch.setattr(assignment_adoption, 'retire_idle', no_retirement)
    before = copy.deepcopy(app.assignments.current(source))
    with pytest.raises(AppError, match='unavailable') as error:
        await retire(app, source, target)
    assert error.value.status == 409 and app.assignments.current(source) == before


async def test_unfinished_task_refuses_before_idle_worker_retirement(app, monkeypatch):
    source, target, _ = await setup(app)
    await save_task(app, target, 'active')
    from amplifier_web import assignment_adoption
    async def no_retirement(*args, **kwargs):
        pytest.fail('Unfinished task must refuse before retiring an idle worker')
    monkeypatch.setattr(assignment_adoption, 'retire_idle', no_retirement)
    with pytest.raises(AppError, match='saved task'):
        await retire(app, source, target)


@pytest.mark.parametrize('broken', ['missing', 'json'])
async def test_unavailable_task_cannot_reuse_full_retirement_from_cached_completion(app, broken):
    source, target, _ = await setup(app)
    await full_access(app, source)
    target['task'] = await save_task(app, target, 'completed')
    await retire(app, source, target)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    if broken == 'missing':
        path.unlink()
    else:
        path.write_text('{')
    before = copy.deepcopy(app.assignments.current(source))
    with pytest.raises(AppError, match='human instruction'):
        await agent_action(app, source, 'assignment.adopt',
                           await reviewed_args(app, source, target), 'unknown-full-readmit')
    assert app.assignments.current(source) == before


@pytest.mark.parametrize('action', ['retire', 'readmit'])
async def test_task_change_at_lease_acquisition_is_rechecked(app, action):
    source, target, _ = await setup(app)
    await full_access(app, source)
    if action == 'readmit':
        await retire(app, source, target)
        args = await reviewed_args(app, source, target)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    prior = path.read_bytes()
    await save_task(app, target, 'active')
    successor = path.read_bytes()
    path.write_bytes(prior)
    original = app.runtime.stopped_boundary
    entered = []
    @contextmanager
    def race(session):
        # A preceding writer commits just before the real acquisition succeeds.
        path.write_bytes(successor)
        with original(session) as held:
            entered.append(held)
            yield held
    app.runtime.stopped_boundary = race
    before = copy.deepcopy(app.assignments.current(source))
    with pytest.raises(AppError):
        if action == 'retire':
            await retire(app, source, target)
        else:
            await agent_action(app, source, 'assignment.adopt', args, 'raced-readmit')
    assert len(entered) == 1 and app.assignments.current(source) == before
    assert path.read_bytes() == successor


@pytest.mark.parametrize('origin', ['ui', 'human'])
async def test_unknown_task_preserves_ordinary_adoption_path(app, origin):
    source, target, _ = await setup(app)
    await full_access(app, source)
    await retire(app, source, target)
    (app.data_dir / 'sessions' / target['id'] / 'control-state.json').unlink()
    if origin == 'human':
        args = await adoption_args(app, source, target)
        receipt = await agent_action(app, source, 'assignment.adopt', args, 'human-readmit')
    else:
        receipt = await app.dispatch('assignment.adopt', await reviewed_args(app, source, target),
                                     command_id='ui-readmit', origin='ui')
    assert receipt['accepted'] and 'readmissionAuthority' not in receipt
    assert not receipt['result']['inputSent']


async def test_available_null_task_is_read_only_bounded_and_reused_inside_lease(app, monkeypatch, record_property):
    source, target, _ = await setup(app)
    path = app.data_dir / 'sessions' / target['id'] / 'control-state.json'
    # History is irrelevant to current eligibility; the existing reader still
    # parses the bounded whole file, but does not validate historical tasks.
    saved = json.loads(path.read_text())
    saved['taskHistory'] = 'Not current task authority'
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    original = Path.open
    reads = []
    class Observed:
        def __enter__(self):
            self.stream = original(path, 'rb')
            return self
        def read(self, size):
            raw = self.stream.read(size)
            reads.append((size, len(raw)))
            return raw
        def __exit__(self, *args):
            self.stream.close()
    def observe(current, *args, **kwargs):
        return Observed() if current == path and args == ('rb',) else original(current, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', observe)
    receipt = await retire(app, source, target)
    assert reads == [(CONTROL_BYTES + 1, len(before))] * 2
    assert receipt['reuseBinding']['targetTask'] == fingerprint({})
    record_property('current_task_reads', len(reads))
    record_property('current_task_bytes', sum(size for _, size in reads))
    with original(path, 'rb') as stream:
        assert stream.read() == before
