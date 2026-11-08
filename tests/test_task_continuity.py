import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.service import AppService, AppError


class Coordinator:
    def __init__(self):
        self.session_state, self.capabilities = {}, {"live.continuation_guard_supported": True}
        self.config = {'session': {'context': {'module': 'context-simple'}}, 'providers': []}
        self.loop = SimpleNamespace(goal_stall_threshold=3)
        self.context = SimpleNamespace(get_messages=AsyncMock(return_value=[]))
    def get(self, name): return {'orchestrator': self.loop, 'context': self.context, 'providers': {}}.get(name)
    def get_capability(self, name): return self.capabilities.get(name)
    def register_capability(self, name, value): self.capabilities[name] = value


def controls(sid='task'):
    coordinator = Coordinator()
    return RuntimeControls(SimpleNamespace(session_id=sid, coordinator=coordinator), SimpleNamespace(generation=None, queued_inputs=0))


async def create(controller, **changes):
    return await controller.perform('task.create', {'commandId': 'create', 'expectedRevision': 0, 'objective': 'Ship the verified report', 'constraints': ['Preserve originals'], 'operationIds': ['unknown-operation'], 'artifactRefs': ['report.md'], **changes})


async def test_explicit_resume_input_does_not_rewrite_task_or_clear_capacity_denial(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    control = controls()
    await create(control, maxTurns=4)
    await control.perform("task.pause", {"commandId": "pause", "expectedRevision": 1})
    before = copy.deepcopy(control.tasks.snapshot())
    assert await control.tasks.continuation_allowed(resume=True)
    assert not await control.tasks.continuation_allowed()
    assert control.tasks.snapshot() == before
    control.capacity.last_denial = {"budgetRevision": control.capacity.policy["revision"]}
    assert await control.tasks.continuation_allowed(resume=True)
    assert not await control.tasks.continuation_allowed()
    assert control.capacity.last_denial
    assert control.tasks.snapshot() == before


@pytest.mark.parametrize("origin,prior,allowed", [
    ("agent", "active", True), ("ui", "active", False),
    (None, "active", False), ("agent", "paused", False),
])
async def test_dependency_block_input_policy_is_owned_and_not_goal_resume(tmp_path, monkeypatch, origin, prior, allowed):
    from amplifier_web.task_binding import dependency_blocked
    monkeypatch.setenv("AMPLIFIER_WEB_HOME", str(tmp_path))
    control = controls()
    try:
        await create(control, maxTurns=3)
        revision = 1
        if prior == "paused":
            await control.perform("task.pause", {"commandId": "pause", "expectedRevision": revision, "origin": "ui"})
            revision += 1
        await control.perform("task.block", {"commandId": "block", "expectedRevision": revision,
            "reason": "Awaiting the exact peer result", "origin": origin})
        before = copy.deepcopy(control.tasks.record())
        assert dependency_blocked(before) is allowed
        assert await control.tasks.peer_input_allowed() is allowed
        assert not await control.tasks.continuation_allowed()
        assert control.tasks.record() == before and control.coordinator.session_state["goal"] is None
        if allowed:
            control.tasks.reserve_peer_input("first")
            control.tasks.reserve_peer_input("first")
            assert control.tasks.record()["goalState"]["turns_used"] == 1
            restored = controls()
            try:
                await restored.restore()
                assert await restored.tasks.peer_input_allowed()
                restored.tasks.reserve_peer_input("second")
                assert restored.tasks.record()["goalState"]["turns_used"] == 2
                assert restored.tasks.record()["revision"] == before["revision"]
                assert restored.coordinator.session_state["goal"] is None
                restored.capacity.last_denial = {"budgetRevision": restored.capacity.policy["revision"]}
                assert not await restored.tasks.peer_input_allowed()
                assert restored.capacity.last_denial
                restored.tasks.reserve_peer_input("third")
                with pytest.raises(ValueError, match="exhausted"):
                    restored.tasks.reserve_peer_input("fourth")
            finally:
                await restored.close()
        # A human re-recording the SAME reason must revoke the input policy too.
        await control.perform("task.block", {"commandId": "human-block",
            "expectedRevision": control.tasks.record()["revision"],
            "reason": before["blockedReason"], "origin": "ui"})
        assert not await control.tasks.peer_input_allowed()
    finally:
        await control.close()


def test_legacy_dependency_origin_requires_recorded_active_transition():
    from amplifier_web.task_binding import dependency_blocked
    task = {"id": "saved", "revision": 3, "status": "blocked", "changes": [
        {"revision": 1, "origin": "ui", "fields": {"status": "active"}},
        {"revision": 2, "origin": "agent", "fields": {"status": "blocked", "blockedReason": "Wait"}},
        {"revision": 3, "origin": "agent", "fields": {"artifactRefs": ["exact-result"]}},
    ]}
    assert dependency_blocked(task)
    for origin in ("ui", "user", None):
        changed = copy.deepcopy(task)
        changed["changes"][1]["origin"] = origin
        assert not dependency_blocked(changed)
    for history in (None, [], task["changes"][1:], list(reversed(task["changes"]))):
        assert not dependency_blocked({**task, "changes": history})
    assert not dependency_blocked({**task, "blockProvenance": {}})
    assert not dependency_blocked({**task, "status": "paused"})
    # Legacy no-op human block at revision3 did not append a change row. Neither
    # a trailing gap nor a later artifact update can prove agent block ownership.
    assert not dependency_blocked({**task, "changes": task["changes"][:2]})
    assert not dependency_blocked({**task, "revision": 4, "changes": [
        *task["changes"][:2], {**task["changes"][2], "revision": 4}]})


async def test_cas_receipts_pause_restart_and_explicit_completion(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls()
    record = (await create(first))['task']
    assert record['appliedRevision'] == 1
    first.runtime.generation = 'running'
    update = {'commandId': 'correct', 'expectedRevision': 1, 'correction': 'Use the corrected source', 'origin': 'ui'}
    record = (await first.perform('task.update', update))['task']
    assert record['revision'] == 2 and record['appliedRevision'] == 1
    assert (await first.perform('task.update', update))['duplicate']
    with pytest.raises(ValueError, match='different contents'):
        await first.perform('task.update', {**update, 'correction': 'Wrong'})
    with pytest.raises(ValueError, match='revision changed'):
        await first.perform('task.pause', {'commandId': 'stale', 'expectedRevision': 1})
    hook = await first.tasks.boundary('provider:request', {'session_id': 'task'})
    assert 'Use the corrected source' in hook.context_injection
    assert first.tasks.record()['appliedRevision'] == 2
    await first.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 2})
    assert not await first.tasks.continuation_allowed()
    assert first.coordinator.session_state['goal'] is None
    restored = controls()
    await restored.restore()
    state = await restored.perform('task.get')
    assert state['task']['status'] == 'paused' and state['goal'] is None
    assert state['task']['operationIds'] == ['unknown-operation']
    assert state['task']['artifactRefs'] == ['report.md']
    assert (await restored.perform('task.update', update))['duplicate']
    with pytest.raises(ValueError, match='saved task controls'):
        await restored.perform('goals.set', {'condition': 'Bypass pause'})
    await restored.perform('task.resume', {'commandId': 'resume', 'expectedRevision': 3})
    assert await restored.tasks.continuation_allowed()
    restored.coordinator.session_state['goal'] = None  # A model's goal judge or turn completion is not task completion.
    assert restored.tasks.record()['status'] == 'active'
    with pytest.raises(ValueError, match='evidence'):
        await restored.perform('task.complete', {'commandId': 'no-evidence', 'expectedRevision': 4, 'condition': 'Ship the verified report', 'evidence': []})
    with pytest.raises(ValueError, match='exact current objective'):
        await restored.perform('task.complete', {'commandId': 'wrong-goal', 'expectedRevision': 4, 'condition': 'Old condition', 'evidence': ['Tests pass']})
    await restored.perform('task.complete', {'commandId': 'finish', 'expectedRevision': 4, 'condition': 'Ship the verified report', 'evidence': ['Report checked against revised source']})
    await create(restored, commandId='next', expectedRevision=5, objective='Next task')
    newest = controls(); await newest.restore()
    assert newest.tasks.history[0]['completionEvidence'] == ['Report checked against revised source']
    assert newest.tasks.record()['objective'] == 'Next task'
    for item in (first, restored, newest): await item.close()


async def test_authorized_plan_revision_and_followup_preserve_consumed_work(tmp_path, monkeypatch):
    """The documented recovery path revises a plan without erasing its history."""
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first, restored = controls(), None
    try:
        task = (await create(first, maxTurns=2,
                             artifactRefs=['failed-check-1.json', 'failed-check-2.json']))['task']
        original_id = task['id']
        first.coordinator.session_state['goal']['turns_used'] = 2
        original_capacity = copy.deepcopy(first.capacity.policy)

        # Routine progress never silently relaxes a recorded hard limit.
        await first.perform('task.update', {
            'commandId': 'record-harness-fix', 'expectedRevision': 1,
            'correction': 'The harness selected the wrong interpreter.', 'origin': 'agent'})
        assert first.coordinator.session_state['goal']['cap'] == 2
        assert first.coordinator.session_state['goal']['turns_used'] == 2

        # An authorized owner can explicitly revise its plan using the existing
        # action; neither counters nor failed evidence become a fresh allowance.
        await first.perform('task.update', {
            'commandId': 'revise-plan', 'expectedRevision': 2, 'maxTurns': 4,
            'correction': 'Owner revised its plan for the corrected harness.', 'origin': 'agent'})
        goal = first.coordinator.session_state['goal']
        assert goal['cap'] == 4 and goal['turns_used'] == 2
        assert first.tasks.record()['id'] == original_id
        assert first.tasks.record()['artifactRefs'] == ['failed-check-1.json', 'failed-check-2.json']
        assert first.capacity.policy == original_capacity

        await first.perform('task.complete', {
            'commandId': 'complete-corrected-check', 'expectedRevision': 3,
            'condition': 'Ship the verified report', 'evidence': ['corrected-check.json']})
        await create(first, commandId='authorized-followup', expectedRevision=4,
                     objective='Complete the already-authorized next deliverable')
        restored = controls()
        await restored.restore()
        previous = restored.tasks.history[-1]
        assert previous['id'] == original_id and previous['status'] == 'completed'
        assert previous['goalState']['turns_used'] == 2
        assert previous['maxTurns'] == 4
        assert previous['artifactRefs'] == ['failed-check-1.json', 'failed-check-2.json']
        assert previous['completionEvidence'] == ['corrected-check.json']
        assert len(previous['corrections']) == 2
        assert restored.tasks.record()['id'] != original_id
        assert restored.coordinator.session_state['goal']['turns_used'] == 0
        assert restored.coordinator.session_state['goal']['cap'] is None
        assert restored.capacity.policy == original_capacity
    finally:
        await first.close()
        if restored is not None:
            await restored.close()


async def test_shared_agent_ui_actions_target_session_preserve_draft_and_never_send(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    worker = None
    runtime = SimpleNamespace(control=AsyncMock(side_effect=lambda sid, operation, args: worker.perform(operation, args)), close=AsyncMock())
    # AsyncMock side effects returning coroutines are not awaited automatically.
    async def control(sid, operation, args): return await worker.perform(operation, args)
    runtime.control = control
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
    try:
        await app.dispatch('session.create', {'title': 'Target'})
        sid = app._session()['id']
        worker = controls(sid)
        await app.dispatch('session.create', {'title': 'Selected'})
        selected = app._session()['id']
        await app.dispatch('view.update', {'patch': {'draft': 'Keep typing'}})
        result = await app.app_bridge('dispatch', {'action': 'task.create', 'id': 'agent-create', 'args': {'expectedRevision': 0, 'objective': 'Verify outputs'}}, sid)
        assert result['result']['task']['objective'] == 'Verify outputs'
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'task.pause', 'args': {'sessionId': selected, 'expectedRevision': 1}}, sid)
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': 'runtime.control', 'args': {'sessionId': selected, 'operation': 'task.pause', 'args': {'expectedRevision': 1}}}, sid)
        pause = await app.dispatch('task.pause', {'sessionId': sid, 'expectedRevision': 1}, command_id='ui-pause')
        assert pause['result']['task']['status'] == 'paused'
        assert app.state['selectedSessionId'] == selected
        assert app.state['view']['draft'] == 'Keep typing'
        assert (await app.app_bridge('dispatch', {'action': 'task.get', 'args': {}}, sid))['result']['task']['status'] == 'paused'
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


async def test_failed_durable_write_does_not_acknowledge_or_change_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    def fail(**kwargs): raise OSError('Read-only storage')
    monkeypatch.setattr(worker, 'persist', fail)
    with pytest.raises(OSError, match='Read-only'):
        await create(worker)
    assert worker.tasks.record() is None
    assert worker.tasks.receipts == {}
    assert worker.coordinator.session_state['goal'] is None
    await worker.close()


@pytest.mark.parametrize('fault', ['continuity', 'publication', 'session-lookup'])
@pytest.mark.parametrize('via', ['ui', 'agent'])
async def test_postcommit_observation_failure_preserves_exact_saved_receipt(tmp_path, monkeypatch, fault, via):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Committed receipt survives'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        def fail(*args, **kwargs):
            raise OSError('Synthetic observation failure after commit')
        if fault == 'continuity':
            worker.coordinator.register_capability('web.continuity.status', fail)
        elif fault == 'publication':
            monkeypatch.setattr(app, '_publish_changes', fail)
        async def control(target, operation, args):
            result = await worker.perform(operation, args)
            if fault == 'session-lookup':
                original = app._session
                def unavailable_once(*args, **kwargs):
                    monkeypatch.setattr(app, '_session', original)
                    fail()
                monkeypatch.setattr(app, '_session', unavailable_once)
            return result
        app.runtime = SimpleNamespace(control=control, close=AsyncMock())
        app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
        async def pause():
            if via == 'agent':
                return await app.app_bridge('dispatch', {'action': 'task.pause',
                    'args': {'sessionId': sid, 'expectedRevision': 1}, 'id': 'committed-pause'}, sid)
            return await app.dispatch('task.pause', {'sessionId': sid, 'expectedRevision': 1},
                                      command_id='committed-pause')
        reply = (await pause())['result']
        saved = json.loads((app.data_dir / 'sessions' / sid / 'control-state.json').read_text())
        assert reply['receipt'] == saved['taskReceipts']['committed-pause']['receipt']
        assert reply['receipt']['revision'] == saved['task']['revision'] == 2
        assert saved['task']['status'] == 'paused'
        assert not await worker.tasks.continuation_allowed()
        if fault in {'publication', 'session-lookup'}:
            assert reply['publication']['status'] == 'unavailable'
            assert reply['status'] == 'unavailable'
            assert reply['snapshotIsCurrent'] is None
        else:
            assert reply['continuity'] == {'supported': False, 'status': 'unavailable'}
        receipt_read = (await app.dispatch('task.receipt', {
            'sessionId': sid, 'commandId': 'committed-pause'}))['result']
        assert receipt_read['receipt'] == reply['receipt']
        duplicate = (await pause())['result']
        assert duplicate['duplicate'] and duplicate['receipt'] == reply['receipt']
        assert worker.tasks.record()['revision'] == 2
        assert encoded_size(reply) <= TASK_CONTEXT_BYTES
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


async def test_older_runtime_cannot_claim_durable_pause_support(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    worker.coordinator.capabilities.pop('live.continuation_guard_supported')
    with pytest.raises(ValueError, match='continuation guard support'):
        await create(worker)
    assert worker.tasks.record() is None
    await worker.close()


async def test_downgraded_runtime_restores_record_without_enabling_unsafe_goal(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls(); await create(first)
    old = controls(); old.coordinator.capabilities.pop('live.continuation_guard_supported')
    await old.restore()
    snapshot = await old.perform('task.get')
    assert snapshot['task']['status'] == 'active'
    assert snapshot['goal'] is None and not snapshot['continuationSupported']
    assert not await old.tasks.continuation_allowed()
    await first.close(); await old.close()


async def test_observation_epoch_survives_progress_but_fences_changed_authority(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        record = (await create(worker))['task']
        epoch = record['continuationEpoch']
        for changes in ({'artifactRefs': ['verified-report.md']},
                        {'constraints': ['Preserve originals', 'Review findings before publishing'],
                         'correction': 'Use the latest findings, do not replay the check'},
                        {'maxTurns': 2}):
            record = (await worker.perform('task.update', {'commandId': str(record['revision']),
                'expectedRevision': record['revision'], **changes}))['task']
            assert record['continuationEpoch'] == epoch
        restored = controls(); await restored.restore()
        assert restored.tasks.record()['continuationEpoch'] == epoch
        await restored.close()
        for operation, changes in [('task.update', {'objective': 'A different objective'}),
                                   ('task.pause', {}), ('task.resume', {}),
                                   ('task.block', {'reason': 'Waiting for a decision'}),
                                   ('task.resume', {}),
                                   ('task.complete', {'condition': 'A different objective', 'evidence': ['Finished']})]:
            record = (await worker.perform(operation, {'commandId': operation + str(record['revision']),
                'expectedRevision': record['revision'], **changes}))['task']
            assert record['continuationEpoch'] > epoch
            epoch = record['continuationEpoch']
    finally:
        await worker.close()


# Preparation-only contract candidates, intentionally not xfailed. New read DTOs
# need interface review before implementation. Never use real task data here.
TASK_CONTEXT_BYTES = 12 * 1024


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


async def corrected_task(worker, count=113):
    await create(worker, objective='Original promise: preserve the exact source.')
    for index in range(count):
        await worker.perform('task.update', {
            'commandId': f'correction-{index}',
            'expectedRevision': worker.tasks.record()['revision'],
            'correction': f'Historical correction {index}: ' + 'retain evidence; ' * 280,
        })
    await worker.perform('task.update', {
        'commandId': 'current-promise',
        'expectedRevision': worker.tasks.record()['revision'],
        'objective': 'Corrected promise: verify the source and retain earlier evidence.',
        'constraints': ['Do not publish.', 'Read current constraints before dependent work.'],
    })


@pytest.mark.parametrize('projection', ['snapshot', 'boundary'])
async def test_large_correction_history_is_not_repeated_in_current_projection(
        tmp_path, monkeypatch, projection):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker)
        original = copy.deepcopy(worker.tasks.record())
        if projection == 'snapshot':
            text = json.dumps(worker.tasks.snapshot(), ensure_ascii=False)
        else:
            hook = await worker.tasks.boundary('provider:request', {'session_id': 'task'})
            text = hook.context_injection
        assert len(text.encode('utf-8')) <= TASK_CONTEXT_BYTES
        assert 'Historical correction 0:' not in text
        assert 'Corrected promise: verify the source' in text
        assert 'Do not publish.' in text
        projected = worker.tasks.snapshot()
        summary = projected['task']
        assert summary['correctionCount'] == 113
        assert summary['historyRef']['action'] == 'task.history'
        # "Old" is not a semantic supersession decision. The preceding update
        # did not explicitly fold correction-only constraints into current scope.
        assert projected['detailRequired'] is True
        assert any(ref['args']['field'] == 'corrections' for ref in projected['detailRefs'])
        assert worker.tasks.record()['corrections'] == original['corrections']
        assert worker.tasks.record()['objective'] == original['objective']
    finally:
        await worker.close()


async def test_exact_mutation_receipt_is_small_and_does_not_become_current_state(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker)
        revision = worker.tasks.record()['revision']
        args = {'commandId': 'saved-update', 'expectedRevision': revision,
                'artifactRefs': ['evidence:unchanged-candidate']}
        first = await worker.perform('task.update', args)
        assert encoded_size(first['receipt']) <= TASK_CONTEXT_BYTES
        # Internal worker RPC remains canonical for scheduling. The registered
        # service mutation test below checks the actual bounded public envelope.
        assert len(first['task']['corrections']) == 113
        assert first['receipt']['commandId'] == 'saved-update'
        assert first['receipt']['revision'] == revision + 1
        await worker.perform('task.pause', {
            'commandId': 'new-pause', 'expectedRevision': revision + 1})
        replay = await worker.perform('task.update', args)
        assert replay['duplicate'] is True
        assert replay['receipt'] == first['receipt']
        assert worker.tasks.record()['status'] == 'paused'
        assert worker.tasks.record()['revision'] == revision + 2
        saved = await worker.perform('task.receipt', {'commandId': 'saved-update'})
        assert saved['receipt'] == first['receipt']
        assert encoded_size(saved) <= TASK_CONTEXT_BYTES
    finally:
        await worker.close()


async def test_failed_update_preserves_full_record_history_receipts_and_saved_bytes(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker, count=3)
        before = copy.deepcopy((worker.tasks.record(), worker.tasks.history,
                                worker.tasks.receipts, worker.coordinator.session_state['goal']))
        disk = worker.state_path().read_bytes()
        def fail(**kwargs):
            raise OSError('Synthetic write refusal')
        monkeypatch.setattr(worker, 'persist', fail)
        with pytest.raises(OSError, match='Synthetic write refusal'):
            await worker.perform('task.update', {
                'commandId': 'failed-update', 'expectedRevision': worker.tasks.record()['revision'],
                'objective': 'Must not replace the promise', 'correction': 'Must not land'})
        assert (worker.tasks.record(), worker.tasks.history, worker.tasks.receipts,
                worker.coordinator.session_state['goal']) == before
        assert worker.state_path().read_bytes() == disk
        assert 'failed-update' not in worker.tasks.receipts
    finally:
        await worker.close()


async def test_correction_history_pages_recover_every_exact_body_after_reopen(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls()
    restored = None
    try:
        await corrected_task(first)
        expected = copy.deepcopy(first.tasks.record()['corrections'])
        identity = first.tasks.record()['id']
        restored = controls()
        await restored.restore()
        args = {'taskId': identity, 'limit': 20}
        found, cursors = [], set()
        for _ in range(10):
            page = await restored.perform('task.history', args)
            assert encoded_size(page) <= TASK_CONTEXT_BYTES
            assert len(page['items']) <= 20
            assert page['coverage'] != 'unknown'
            for row in page['items']:
                if row['kind'] != 'correction':
                    continue
                assert 'text' not in row
                ref = row['detailRef']
                text, detail_args = '', dict(ref['args'])
                for _ in range(10):
                    detail = await restored.perform(ref['action'], detail_args)
                    assert encoded_size(detail) <= TASK_CONTEXT_BYTES
                    text += detail['text']
                    if detail['nextOffset'] is None:
                        break
                    detail_args['offset'] = detail['nextOffset']
                else:
                    pytest.fail('Correction detail made no bounded progress')
                found.append((row['revision'], text))
            if page['nextAfter'] is None:
                break
            assert page['nextAfter'] not in cursors
            cursors.add(page['nextAfter'])
            args['after'] = page['nextAfter']
        else:
            pytest.fail('History made no bounded progress')
        assert found == [(row['revision'], row['text']) for row in expected]
        assert restored.tasks.record()['corrections'] == expected
        assert restored.tasks.record()['status'] == 'active'
    finally:
        await first.close()
        if restored is not None:
            await restored.close()


async def test_oversized_current_constraints_require_exact_paged_detail(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        constraints = [f'Constraint {index}: ' + '\u754c' * 3900 for index in range(100)]
        await create(worker, constraints=constraints)
        task = worker.tasks.record()
        summary = worker.tasks.snapshot()
        assert encoded_size(summary) <= TASK_CONTEXT_BYTES
        assert summary['detailRequired'] is True
        ref = next(ref for ref in summary['detailRefs'] if ref['args']['field'] == 'constraints')
        assert ref['args']['expectedRevision'] == task['revision']
        pieces, args = [], dict(ref['args'])
        for _ in range(500):
            page = await worker.perform(ref['action'], args)
            assert encoded_size(page) <= TASK_CONTEXT_BYTES
            assert page['revision'] == task['revision']
            pieces.append(page['text'])
            if page['nextOffset'] is None:
                break
            assert page['nextOffset'] > args.get('offset', 0)
            args['offset'] = page['nextOffset']
        else:
            pytest.fail('Current constraints cannot be read completely through bounded pages')
        assert json.loads(''.join(pieces)) == constraints
        old_args = dict(ref['args'])
        await worker.perform('task.update', {
            'commandId': 'new-constraints', 'expectedRevision': task['revision'],
            'constraints': ['New constraint: no dependent effects.']})
        with pytest.raises(ValueError, match='revision|changed|stale'):
            await worker.perform(ref['action'], old_args)
        assert worker.tasks.record()['constraints'] == ['New constraint: no dependent effects.']
    finally:
        await worker.close()


async def test_original_and_corrected_objectives_remain_distinct_after_reopen(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    first = controls()
    restored = None
    try:
        await corrected_task(first, count=2)
        identity = first.tasks.record()['id']
        restored = controls()
        await restored.restore()
        original = await restored.perform('task.detail', {
            'taskId': identity, 'expectedRevision': restored.tasks.record()['revision'],
            'field': 'originalObjective'})
        assert original['text'] == 'Original promise: preserve the exact source.'
        assert restored.tasks.record()['objective'] == (
            'Corrected promise: verify the source and retain earlier evidence.')
        assert len(restored.tasks.record()['corrections']) == 2
    finally:
        await first.close()
        if restored is not None:
            await restored.close()


async def test_delayed_completion_cannot_close_corrected_paused_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await create(worker)
        late = {'commandId': 'late-worker-completion', 'expectedRevision': 1,
                'condition': worker.tasks.record()['objective'], 'evidence': ['Old worker return']}
        await worker.perform('task.update', {
            'commandId': 'correction', 'expectedRevision': 1,
            'objective': 'Current corrected promise', 'constraints': ['Do not run old work.']})
        await worker.perform('task.pause', {'commandId': 'stop', 'expectedRevision': 2})
        before = copy.deepcopy(worker.tasks.record())
        with pytest.raises(ValueError, match='revision changed'):
            await worker.perform('task.complete', late)
        assert worker.tasks.record() == before
        assert not await worker.tasks.continuation_allowed()
        assert 'late-worker-completion' not in worker.tasks.receipts
    finally:
        await worker.close()


async def test_saved_task_get_is_cold_and_does_not_return_the_app_snapshot(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Cold task'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        await worker.perform('task.pause', {'commandId': 'pause', 'expectedRevision': 1})
        await worker.close()
        worker = None
        app.runtime = SimpleNamespace(
            control=AsyncMock(side_effect=AssertionError('No worker control on a cold read')),
            close=AsyncMock())
        app.management = SimpleNamespace(
            ensure_runtime=AsyncMock(side_effect=AssertionError('No worker warming')),
            close=AsyncMock())
        monkeypatch.setattr(app.history, 'ensure_loaded',
                            AsyncMock(side_effect=AssertionError('No transcript hydration')))
        monkeypatch.setattr(app, '_admit_session',
                            lambda *a, **kw: pytest.fail('No dormant overlay admission'))
        monkeypatch.setattr(app, 'browser_state',
                            lambda *a, **kw: pytest.fail('No full app snapshot'))
        result = await app.dispatch('task.get', {'sessionId': sid}, include_state=True)
        assert result['result']['task']['status'] == 'paused'
        assert result['result']['task']['objective'] == 'Ship the verified report'
        assert encoded_size(result) <= TASK_CONTEXT_BYTES
        assert 'sessions' not in result.get('state', {})
        app.runtime.control.assert_not_called()
        app.management.ensure_runtime.assert_not_called()
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


@pytest.mark.parametrize('action', ['task.get', 'task.history', 'task.detail', 'task.receipt'])
@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('via', ['ui', 'agent'])
async def test_registered_task_reads_leave_dormant_owner_cold(tmp_path, monkeypatch, action, alias, via):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Dormant saved owner'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        task = copy.deepcopy(worker.tasks.record())
        app._save_full(reason='Synthetic dormant task read fixture')
        app.state['sessions'] = [row for row in app.state['sessions'] if row['id'] != sid]
        app.projections.invalidate()
        before = copy.deepcopy(app.state['sessions'])
        def forbidden(*args, **kwargs):
            pytest.fail('Passive task read admitted or published a dormant owner')
        monkeypatch.setattr(app, '_admit_session', forbidden)
        monkeypatch.setattr(app, '_publish_changes', forbidden)
        monkeypatch.setattr(app.history, 'refresh', AsyncMock(side_effect=forbidden))
        monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=forbidden))
        values = {
            'task.get': {}, 'task.history': {'limit': 1},
            'task.detail': {'taskId': task['id'], 'expectedRevision': task['revision'], 'field': 'objective'},
            'task.receipt': {'commandId': 'create'},
        }[action]
        arguments = ({'sessionId': sid, 'operation': action, 'args': values} if alias
                     else {'sessionId': sid, **values})
        if via == 'agent':
            result = await app.app_bridge('dispatch', {
                'action': 'runtime.control' if alias else action, 'args': arguments}, sid)
        else:
            result = await app.dispatch('runtime.control' if alias else action, arguments, include_state=True)
        assert result['accepted'] is True and 'state' not in result
        value = result['result']
        if action == 'task.get':
            assert value['status'] == 'available' and value['task']['objective'] == task['objective']
        elif action == 'task.history':
            assert value['items'][0]['id'] == task['id']
        elif action == 'task.detail':
            assert value['text'] == task['objective']
        else:
            assert value['receipt']['taskId'] == task['id'] and value['receipt']['revision'] == 1
        assert app.state['sessions'] == before
        assert encoded_size(result) <= TASK_CONTEXT_BYTES
    finally:
        await app.close()
        if worker is not None:
            await worker.close()


@pytest.mark.parametrize('action', ['task.get', 'task.history', 'task.detail', 'task.receipt'])
async def test_task_read_rejects_missing_caller_and_cross_workspace(tmp_path, monkeypatch, action):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Reader'})
        reader = app._session()['id']
        await app.dispatch('session.create', {'title': 'Other workspace owner'})
        target = app._session()
        target['workspace'] = str(tmp_path / 'other')
        worker = controls(target['id'])
        await create(worker)
        task = worker.tasks.record()
        args = {'sessionId': target['id'], **{
            'task.get': {}, 'task.history': {},
            'task.detail': {'taskId': task['id'], 'expectedRevision': task['revision'], 'field': 'objective'},
            'task.receipt': {'commandId': 'create'},
        }[action]}
        with pytest.raises(AppError, match='calling conversation'):
            await app.dispatch(action, args, origin='agent')
        with pytest.raises(AppError, match='history scope'):
            await app.dispatch(action, args, origin='agent', caller_session_id=reader)
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', {'action': action, 'args': args}, reader)
        with pytest.raises(AppError, match='native project'):
            await app.dispatch(action, {**args, 'nativeProject': 'not-this-project'})
    finally:
        await app.close()
        if worker is not None:
            await worker.close()


@pytest.mark.parametrize('via', ['ui', 'agent', 'agent_alias'])
async def test_mutation_dispatch_acknowledges_without_serializing_app_state(
        tmp_path, monkeypatch, via):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Mutation target'})
        sid = app._session()['id']
        worker = controls(sid)
        await corrected_task(worker)
        async def control(target, operation, args):
            assert target == sid
            return await worker.perform(operation, args)
        app.runtime = SimpleNamespace(control=control, close=AsyncMock())
        app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
        monkeypatch.setattr(app, 'browser_state',
                            lambda *a, **kw: pytest.fail('Mutation must not serialize app state'))
        revision = worker.tasks.record()['revision']
        values = {'sessionId': sid, 'expectedRevision': revision, 'artifactRefs': ['exact-evidence-ref']}
        if via == 'ui':
            result = await app.dispatch('task.update', values,
                command_id='compact-mutation', include_state=True)
        else:
            action, args = ('runtime.control', {'sessionId': sid, 'operation': 'task.update',
                                               'args': {k: v for k, v in values.items() if k != 'sessionId'}}) if via == 'agent_alias' else ('task.update', values)
            result = await app.app_bridge('dispatch', {
                'action': action, 'args': args, 'id': 'compact-mutation'}, sid)
        assert result['accepted'] is True
        assert result['result']['receipt']['commandId'] == 'compact-mutation'
        assert result['result']['receipt']['revision'] == revision + 1
        assert encoded_size(result) <= TASK_CONTEXT_BYTES
        assert 'sessions' not in result.get('state', {})
        assert len(worker.tasks.record()['corrections']) == 113
        assert app.coordination.task(sid)['corrections'] == worker.tasks.record()['corrections']
        assert app._session(sid)['task'] == worker.tasks.record()
        before = copy.deepcopy(worker.tasks.record())
        if via != 'ui':
            with pytest.raises(AppError, match='revision changed'):
                await app.app_bridge('dispatch', {'action': 'task.update',
                    'args': {**values, 'expectedRevision': 0}, 'id': 'stale-no-effect'}, sid)
            assert worker.tasks.record() == before
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


async def test_mounted_unknown_mutation_is_reconciled_by_exact_receipt_without_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Unknown mutation acknowledgement'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        calls = []
        async def control(target, operation, args):
            calls.append((operation, args['commandId']))
            await worker.perform(operation, args)
            raise ConnectionError('Synthetic lost acknowledgement after worker commit')
        app.runtime = SimpleNamespace(control=control, close=AsyncMock())
        app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
        with pytest.raises(ConnectionError, match='lost acknowledgement'):
            await app.app_bridge('dispatch', {'action': 'task.pause', 'id': 'uncertain-pause',
                'args': {'sessionId': sid, 'expectedRevision': 1}}, sid)
        readback = await app.app_bridge('dispatch', {'action': 'task.receipt',
            'args': {'sessionId': sid, 'commandId': 'uncertain-pause'}}, sid)
        assert readback['result']['receipt']['revision'] == 2
        assert readback['result']['receipt']['status'] == 'paused'
        assert calls == [('task.pause', 'uncertain-pause')]
        assert encoded_size(readback) <= TASK_CONTEXT_BYTES and 'state' not in readback
        assert not await worker.tasks.continuation_allowed()
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()


@pytest.mark.parametrize('action', ['task.get', 'task.history', 'task.detail', 'task.receipt'])
async def test_runtime_identity_cold_reads_and_warm_detail_ref_share_owner(tmp_path, monkeypatch, action):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Directory identity'})
        sid = app._session()['id']
        native = 'synthetic-runtime-owner'
        app._session(sid).update(runtimeSessionId=native, nativeIdentity=native)
        worker = controls(native)
        objective = 'Exact current objective'
        await create(worker, objective=objective, constraints=['Large active constraint ' * 300])
        task = copy.deepcopy(worker.tasks.record())
        warm = worker.tasks.snapshot()
        ref = next(r for r in warm['detailRefs'] if r['args']['field'] == 'constraints')
        assert ref['args']['sessionId'] == native and native != sid
        other = (await app.dispatch('session.create', {'title': 'Other caller'}))
        other_id = app._session()['id']
        app._save_full(reason='Synthetic distinct runtime identity fixture')
        app.state['sessions'] = [row for row in app.state['sessions'] if row['id'] != sid]
        app.projections.invalidate()
        def forbidden(*args, **kwargs):
            pytest.fail('Identity read must not admit, hydrate, publish or warm')
        monkeypatch.setattr(app, '_admit_session', forbidden)
        monkeypatch.setattr(app, '_publish_changes', forbidden)
        monkeypatch.setattr(app.history, 'ensure_loaded', AsyncMock(side_effect=forbidden))
        monkeypatch.setattr(app.history, 'refresh', AsyncMock(side_effect=forbidden))
        args = {'sessionId': sid, **{
            'task.get': {}, 'task.history': {},
            'task.detail': {'taskId': task['id'], 'expectedRevision': task['revision'], 'field': 'objective'},
            'task.receipt': {'commandId': 'create'},
        }[action]}
        saved = (await app.app_bridge('dispatch', {'action': action, 'args': args}, sid))['result']
        if action == 'task.get':
            assert saved['task']['id'] == task['id'] and saved['task']['objective'] == objective
        elif action == 'task.history':
            assert saved['items'][0]['id'] == task['id']
        elif action == 'task.detail':
            assert saved['text'] == objective
        else:
            assert saved['receipt']['taskId'] == task['id']
        detail = (await app.app_bridge('dispatch', ref, sid))['result']
        assert detail['text'] and detail['offset'] == 0
        assert detail['taskId'] == task['id'] and detail['revision'] == task['revision']
        with pytest.raises(AppError, match='calling conversation'):
            await app.app_bridge('dispatch', ref, other_id)
        assert all(row['id'] != sid for row in app.state['sessions'])
    finally:
        await app.close()
        if worker is not None:
            await worker.close()


async def test_task_history_cursor_keeps_its_high_water_when_a_correction_arrives(
        tmp_path, monkeypatch):
    """Old history remains readable; it cannot masquerade as current scope."""
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker, count=21)
        task_id = worker.tasks.record()['id']
        revision = worker.tasks.record()['revision']
        args = {'taskId': task_id, 'limit': 20}
        first = await worker.perform('task.history', args)
        assert first['snapshotRevision'] == revision
        assert first['currentRevision'] == revision
        assert first['snapshotIsCurrent'] is True
        assert first['nextAfter']
        await worker.perform('task.update', {
            'commandId': 'late-history-append', 'expectedRevision': revision,
            'correction': 'Late restriction: do not execute the old candidate.'})
        page = await worker.perform('task.history', {**args, 'after': first['nextAfter']})
        assert page['snapshotRevision'] == revision
        assert page['currentRevision'] == revision + 1
        assert page['snapshotIsCurrent'] is False
        assert all(row['revision'] <= revision for row in page['items'])
        assert encoded_size(page) <= TASK_CONTEXT_BYTES
        current = worker.tasks.snapshot()
        assert current['task']['revision'] == revision + 1
        assert current['detailRequired'] is True
        assert len(worker.tasks.record()['corrections']) == 22
    finally:
        await worker.close()


async def test_history_cursor_cannot_be_rebound_to_a_replacement_task(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await corrected_task(worker, count=21)
        old = copy.deepcopy(worker.tasks.record())
        page = await worker.perform('task.history', {'taskId': old['id'], 'limit': 20})
        assert page['nextAfter']
        await worker.perform('task.complete', {
            'commandId': 'explicit-completion', 'expectedRevision': old['revision'],
            'condition': old['objective'], 'evidence': ['Synthetic retained check']})
        await create(worker, commandId='next-task',
                     expectedRevision=worker.tasks.record()['revision'],
                     objective='Separate next objective')
        current = copy.deepcopy(worker.tasks.record())
        with pytest.raises(ValueError, match='cursor|scope|task|changed'):
            await worker.perform('task.history', {
                'taskId': current['id'], 'limit': 20, 'after': page['nextAfter']})
        assert worker.tasks.record() == current
        assert worker.tasks.history[0]['corrections'] == old['corrections']
    finally:
        await worker.close()


async def test_internal_task_rpc_keeps_all_questions_while_public_projection_omits(
        tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        questions = [f'question-{n}-' + 'x' * 100 for n in range(100)]
        await create(worker, questionIds=questions)
        internal = await worker.perform('task.get')
        assert internal['task']['questionIds'] == questions
        projected = worker.tasks.snapshot()
        assert projected['task']['questionIds'] is None
        assert projected['task']['questionIdsCount'] == 100
        assert any(ref['args']['field'] == 'questionIds' for ref in projected['detailRefs'])
        assert encoded_size(projected) <= TASK_CONTEXT_BYTES
    finally:
        await worker.close()


async def test_legacy_epoch_and_missing_original_are_not_reconstructed(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await create(worker)
        # Explicit legacy fixture: no original text or epoch was persisted.
        worker.tasks.record().pop('originalObjective')
        worker.tasks.record().pop('continuationEpoch')
        current = worker.tasks.record()
        projected = worker.tasks.snapshot()
        assert projected['task']['continuationEpoch'] == current['revision']
        assert projected['task']['originalObjectiveAvailable'] is False
        assert projected['task']['originalObjectiveRef'] is None
        with pytest.raises(ValueError, match='unavailable'):
            await worker.perform('task.detail', {
                'taskId': current['id'], 'expectedRevision': current['revision'],
                'field': 'originalObjective'})
    finally:
        await worker.close()


async def test_completed_tasks_remain_discoverable_without_their_bodies(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path))
    worker = controls()
    try:
        await create(worker, objective='Retained objective ' + 'x' * 10000)
        old = copy.deepcopy(worker.tasks.record())
        await worker.perform('task.complete', {
            'commandId': 'complete-old', 'expectedRevision': old['revision'],
            'condition': old['objective'], 'evidence': ['Synthetic check']})
        await create(worker, commandId='new-objective',
                     expectedRevision=worker.tasks.record()['revision'], objective='Next objective')
        first = await worker.perform('task.history', {'limit': 1})
        assert first['total'] == 2 and first['truncated'] is True
        assert 'Retained objective' not in json.dumps(first)
        second = await worker.perform('task.history', {'limit': 1, 'after': first['nextAfter']})
        assert second['items'][0]['id'] == old['id']
        ref = second['items'][0]['historyRef']
        changes = await worker.perform(ref['action'], ref['args'])
        assert changes['taskId'] == old['id']
        assert encoded_size(first) <= TASK_CONTEXT_BYTES
        assert encoded_size(second) <= TASK_CONTEXT_BYTES
    finally:
        await worker.close()


@pytest.mark.parametrize('operation', ['task.get', 'runtime.control'])
async def test_public_get_alias_is_bounded_and_does_not_change_canonical_cache(
        tmp_path, monkeypatch, operation):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Bounded public task'})
        sid = app._session()['id']
        worker = controls(sid)
        await corrected_task(worker)
        expected = copy.deepcopy(worker.tasks.record())
        before = copy.deepcopy(app.state.get('runtimeControl', {}))
        monkeypatch.setattr(app.history, 'ensure_loaded',
                            AsyncMock(side_effect=AssertionError('Cold read must not hydrate')))
        args = {'sessionId': sid}
        if operation == 'runtime.control':
            args.update(operation='task.get', args={})
        result = await app.dispatch(operation, args, include_state=True)
        assert encoded_size(result) <= TASK_CONTEXT_BYTES
        assert result['result']['task']['correctionCount'] == 113
        assert result['result']['task']['corrections'] is None
        assert app.state.get('runtimeControl', {}) == before
        assert worker.tasks.record() == expected
    finally:
        await app.close()
        if worker is not None:
            await worker.close()


@pytest.mark.parametrize('fault', ['missing', 'unreadable', 'malformed', 'oversized'])
@pytest.mark.parametrize('via', ['ui', 'agent', 'agent_alias'])
async def test_cold_saved_control_failure_is_unavailable_not_empty(
        tmp_path, monkeypatch, caplog, fault, via):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    try:
        await app.dispatch('session.create', {'title': 'Unreadable saved state'})
        sid = app._session()['id']
        path = app.data_dir / 'sessions' / sid / 'control-state.json'
        if fault != 'missing':
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{' if fault == 'malformed' else ' ' * 300)
        before = None if fault == 'missing' else path.read_bytes()
        if fault == 'oversized':
            monkeypatch.setattr('amplifier_web.task_continuity.CONTROL_BYTES', 256)
        original_open = type(path).open
        if fault == 'unreadable':
            # A deterministic read failure also works when tests run as root.
            def deny_control_open(target, *args, **kwargs):
                if target == path:
                    raise PermissionError(13, 'Synthetic private read failure', str(path))
                return original_open(target, *args, **kwargs)
            monkeypatch.setattr(type(path), 'open', deny_control_open)
        app._save_full(reason='Synthetic cold unavailable fixture')
        app.state['sessions'] = [row for row in app.state['sessions'] if row['id'] != sid]
        app.projections.invalidate()
        sessions_before = copy.deepcopy(app.state['sessions'])
        app.runtime = SimpleNamespace(
            control=AsyncMock(side_effect=AssertionError('No runtime control')), close=AsyncMock())
        app.management = SimpleNamespace(
            ensure_runtime=AsyncMock(side_effect=AssertionError('No worker warming')), close=AsyncMock())
        monkeypatch.setattr(app.history, 'ensure_loaded',
                            AsyncMock(side_effect=AssertionError('No transcript recovery')))
        monkeypatch.setattr(app.history, 'refresh',
                            AsyncMock(side_effect=AssertionError('No transcript refresh')))
        def no_admission(*args, **kwargs):
            pytest.fail('Passive failure must not admit a worker or publish state')
        monkeypatch.setattr(app, '_admit_session', no_admission)
        monkeypatch.setattr(app, '_publish_changes', no_admission)
        monkeypatch.setattr(app, 'browser_state', no_admission)
        caplog.clear()
        for action, values in {
            'task.get': {}, 'task.history': {},
            'task.detail': {'taskId': 'synthetic-task', 'expectedRevision': 1, 'field': 'objective'},
            'task.receipt': {'commandId': 'synthetic-command'},
        }.items():
            args = {'sessionId': sid, **values}
            if via == 'agent_alias':
                args = {'sessionId': sid, 'operation': action, 'args': values}
                action = 'runtime.control'
            if via == 'ui':
                result = await app.dispatch(action, args, include_state=True)
            else:
                result = await app.app_bridge('dispatch', {'action': action, 'args': args}, sid)
            assert result['accepted'] is True and 'state' not in result
            assert result['result'] == {
                'status': 'unavailable', 'task': None, 'coverage': 'unknown',
                'detailRequired': True,
                'reason': 'Saved task state unavailable; no worker was started.',
                'authority': 'Missing saved state is not an empty task or permission to continue.',
            }
            public = json.dumps(result)
            for private in (str(path), str(app.data_dir), 'control-state.json',
                            'Synthetic private read failure', '[Errno', 'line 1 column',
                            'bounded read limit'):
                assert private not in public
            assert encoded_size(result) <= TASK_CONTEXT_BYTES
        diagnostics = [row for row in caplog.records if row.name == 'amplifier_web.task_continuity']
        assert len(diagnostics) == 4
        expected_error = {'missing': FileNotFoundError, 'unreadable': PermissionError,
                          'malformed': json.JSONDecodeError, 'oversized': ValueError}[fault]
        assert all(row.getMessage() == 'Saved task state unavailable during cold read'
                   and isinstance(row.exc_info[1], expected_error) for row in diagnostics)
        if fault in {'missing', 'unreadable'}:
            assert all(row.exc_info[1].filename == str(path) for row in diagnostics)
        assert app.state['sessions'] == sessions_before
        app.runtime.control.assert_not_called()
        app.management.ensure_runtime.assert_not_called()
        if before is None:
            assert not path.exists()
        else:
            with original_open(path, 'rb') as stream:
                assert stream.read() == before
    finally:
        app.management = None
        await app.close()


@pytest.mark.parametrize('fault', ['incomplete', 'questionIds', 'corrections', 'revision', 'status', 'history'])
async def test_structurally_invalid_control_is_not_available_authority(tmp_path, monkeypatch, fault):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Invalid canonical fixture'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        path = app.data_dir / 'sessions' / sid / 'control-state.json'
        saved = json.loads(path.read_text())
        if fault == 'incomplete':
            saved['task'] = {'id': 'task-one', 'revision': 1}
        elif fault == 'history':
            saved['taskHistory'] = [{'id': 'task-old', 'revision': 1}]
        else:
            saved['task'][fault] = {
                'questionIds': 'not-a-list', 'corrections': [{'text': 'missing revision'}],
                'revision': 0, 'status': {'nested': 'not-a-status'},
            }[fault]
        path.write_text(json.dumps(saved))
        before = path.read_bytes()
        monkeypatch.setattr(app.history, 'ensure_loaded',
                            AsyncMock(side_effect=AssertionError('No repair or hydration on read')))
        result = (await app.dispatch('task.get', {'sessionId': sid}))['result']
        assert result['status'] == 'unavailable' and result['coverage'] == 'unknown'
        assert result['detailRequired'] is True
        assert path.read_bytes() == before
    finally:
        await app.close()
        if worker is not None:
            await worker.close()


async def test_late_mutation_rpc_cannot_replace_a_newer_paused_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    app = AppService(tmp_path / 'app', None, workspace=tmp_path)
    worker = None
    try:
        await app.dispatch('session.create', {'title': 'Out of order acknowledgements'})
        sid = app._session()['id']
        worker = controls(sid)
        await create(worker)
        async def control(target, operation, args):
            first = await worker.perform(operation, args)
            await worker.perform('task.pause', {
                'commandId': 'newer-stop', 'expectedRevision': first['task']['revision']})
            # An explicitly simulated later RPC reaches the existing host cache
            # first. Both records came from real TaskController transitions.
            newer = await worker.perform('task.get')
            app.state.setdefault('runtimeControl', {}).setdefault(sid, {})['task.get'] = newer
            app._session(sid)['task'] = newer['task']
            return first
        app.runtime = SimpleNamespace(control=control, close=AsyncMock())
        app.management = SimpleNamespace(ensure_runtime=AsyncMock(), close=AsyncMock())
        result = (await app.dispatch('task.update', {
            'sessionId': sid, 'expectedRevision': 1, 'correction': 'Retained earlier correction'},
            command_id='older-update'))['result']
        assert result['receipt']['revision'] == 2
        assert result['snapshotIsCurrent'] is False and result['currentRevision'] == 3
        assert app.coordination.task(sid)['status'] == 'paused'
        assert app._session(sid)['task']['revision'] == 3
        assert not await worker.tasks.continuation_allowed()
    finally:
        app.management = None
        await app.close()
        if worker is not None:
            await worker.close()
