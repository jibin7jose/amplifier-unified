"""Real Core/live/event-pump follow-up; scripted provider and owned file tool.

No model service, subprocess ownership or deployed host behavior is claimed.
"""
import asyncio
import copy
import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')
from amplifier_core import ToolResult
from amplifier_core.message_models import ChatResponse, ToolCall, Usage
from amplifier_web import runtime_worker
from amplifier_web.service import AppService
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output, message_text


class NativeHost(DirectNativeHost):
    async def before_execute(self, worker):
        from amplifier_web.shared_state import ActivationGate
        worker.activation_gate = ActivationGate()
        worker.activation = worker.activation_gate.activate()
        worker.runtime.capture_activation = worker.activation_gate.current
        coordinator = worker.session.coordinator
        coordinator.register_capability('live.activation', worker.activation_gate)
        # Match host.session's canonical checkpoint contract: retain controls
        # before saving messages, including after a goal's live pointer clears.
        checkpoint = coordinator.get_capability('live.checkpoint')
        async def retained_checkpoint(*args, **kwargs):
            coordinator.get_capability('web.controls.persist')()
            return await checkpoint(*args, **kwargs)
        coordinator.register_capability('live.checkpoint', retained_checkpoint)
        worker.goal_progress = []
        async def progress(event, data):
            from amplifier_core import HookResult
            worker.goal_progress.append(copy.deepcopy(data))
            return HookResult()
        coordinator.hooks.register('orchestrator:goal_progress', progress, name='probe-goal-progress')

    async def automatic_input(self, session, args, guard, steering):
        worker = self.roots[session['id']]
        token = worker.activation_gate.bind(worker.activation)
        try:
            return await super().automatic_input(session, args, guard, steering)
        finally:
            worker.activation_gate.reset(token)

    async def control(self, sid, operation, args, *, guard=None):
        worker = self.roots[sid]
        async with worker.command_lock:
            if guard:
                guard()
            return await worker.controls.perform(operation, args)


class CorrectionTool:
    name = 'correct_fixture'
    description = 'Correct the owned test artifact.'
    input_schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    def __init__(self, path):
        self.path, self.calls = path, 0
    async def execute(self, args):
        self.calls += 1
        self.path.write_text('Corrected result\n')
        return ToolResult(success=True, output={'reference': self.path.name + '@sha256:' + hashlib.sha256(self.path.read_bytes()).hexdigest(), 'corrected': True})


class Member(ScriptedProvider):
    def __init__(self, sid, path, successor):
        super().__init__()
        self.sid, self.path, self.successor = sid, path, successor
        self.before = self.worker = None
        self.accounted = []
    async def complete(self, request, **kwargs):
        text = next((message_text(m) for m in request.messages if m.role == 'system'), '')
        if 'tool-less evaluator' in text:
            goal = self.worker.session.coordinator.session_state['goal']
            self.accounted.append(goal['turns_used'])
            return self.final('NO\nThe follow-up needs its next authorized step.')
        if 'single, short line for a developer' in text:
            return self.final('The selected turn limit was reached.')
        self.calls += 1
        if self.successor == 'active' and self.calls == 5:
            return self.final('One further authorized automatic turn completed.')
        envelope, _ = peer_envelopes(request)[-1]
        assert envelope['inputId'] == 'fresh-owner-correction'
        assert 'humanResume' not in envelope
        assert envelope['completedTaskHandling']['taskId'] == self.before['id']
        step = self.calls
        if self.successor:
            if step == 1:
                return self.dispatch('create-correction-task', 'task.create', {
                    'sessionId': self.sid, 'expectedRevision': self.before['revision'],
                    'objective': 'Correct the retained result'})
            if step == 2:
                result = tool_output(request, 'create-correction-task')
                assert result['accepted'], result
                assert self.worker.controls.tasks.record()['maxTurns'] == self.before.get('maxTurns')
            step -= 1
        if step == 1:
            return ChatResponse(content=[], tool_calls=[ToolCall(
                id='actual-correction-tool', name='correct_fixture', arguments={})],
                usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2))
        if step == 2:
            correction = tool_output(request, 'actual-correction-tool')
            assert correction['corrected'] is True
            self.reference = correction['reference']
        if step == 2 and self.successor and self.successor != 'active':
            task = self.worker.controls.tasks.record()
            return self.dispatch('finish-correction-task', 'task.complete', {
                'sessionId': self.sid, 'expectedRevision': task['revision'],
                'condition': task['objective'], 'evidence': [self.reference]})
        if self.successor and self.successor != 'active':
            step -= 1
        if step == 2:
            if self.successor and self.successor != 'active':
                assert tool_output(request, 'finish-correction-task')['accepted']
            return self.dispatch('report-correction', 'coordination.reply', {
                'requestId': 'fresh-owner-correction', 'kind': 'result', 'outcome': 'success',
                'text': 'Corrected the retained result.', 'references': [self.reference]})
        assert step == 3
        report = tool_output(request, 'report-correction')
        assert report['accepted'] and report['result']['status'] == 'staged', report
        return self.final('The corrected result is saved.')


class Manager(ScriptedProvider):
    def __init__(self, target, artifact, notify=True):
        super().__init__()
        self.target, self.artifact, self.notify = target, artifact, notify
        self.result = None
    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.dispatch('fresh-owner-correction', 'coordination.send', {
                'sessionId': self.target, 'mode': 'queue', 'returnNotification': self.notify,
                'text': 'Correct the retained result and report back within the existing assignment.'})
        if self.calls == 2:
            assert tool_output(request, 'fresh-owner-correction')['accepted']
            return self.final('The commissioned owner is handling the correction.')
        if self.calls == 3:
            envelope, _ = peer_envelopes(request)[-1]
            assert envelope['replyToRequestId'] == 'fresh-owner-correction'
            self.return_id = envelope['inputId']
            return self.dispatch('read-correction', 'coordination.result', {'requestId': 'fresh-owner-correction'})
        assert self.calls == 4
        self.result = tool_output(request, 'read-correction')['result']
        assert self.result['qualified']
        assert self.artifact.read_text() == 'Corrected result\n'
        return self.final('The fresh correction is verified.')


@pytest.mark.parametrize('successor,cap,independent', [
    (False, 3, False), (True, 4, False), (True, None, False), ('active', 4, False), (False, 2, False),
    (False, 3, True), (True, 4, True), (False, 2, True)])
async def test_completed_recipient_executes_and_reports_under_existing_authority(tmp_path, monkeypatch, successor, cap, independent):
    host = NativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path/'app'))
    workspace = tmp_path/'workspace'; workspace.mkdir()
    artifact = workspace/'result.txt'; artifact.write_text('Original result\n')
    app = host.app = AppService(tmp_path/'app', host, workspace=workspace)
    source, target = [app._new_session({'title': name}) for name in ('Manager', 'Completed member')]
    app.state['sessions'] = [source, target]
    if independent:
        host.workers = host.roots  # Expose actual mounted roots to configuration retirement.
        from test_access_mode import save as access
        await access(app, source, scope='app')
        assert not source.get('fileAccessPending') and not target.get('fileAccessPending')
    else:
        assignment = app.assignments.ensure(source, 'existing-assignment')
        assignment['members'][target['id']] = {'status': 'bound', 'role': 'commissioned'}
        target['assignmentId'] = assignment['id']
        app.assignments.save(assignment)
    app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    manager, member = Manager(target['id'], artifact, notify=cap != 2), Member(target['id'], artifact, successor)
    async def ensure(session, **kwargs):
        assert session['id'] in host.roots
    from amplifier_web.management import Management
    app.management = Management(app)
    monkeypatch.setattr(app.management, 'ensure_runtime', ensure)
    try:
        async with asyncio.timeout(45):
            parent = await host.mount(source, manager, paths)
            worker = member.worker = await host.mount(target, member, paths)
            tool = CorrectionTool(artifact)
            await worker.session.coordinator.mount('tools', tool, name=tool.name)
            await worker.controls.perform('task.create', {'commandId': 'original-task',
                'expectedRevision': 0, 'objective': 'Produce the original result', 'maxTurns': cap})
            worker.session.coordinator.session_state['goal'].update(turns_used=2, continuations=1)
            member.before = copy.deepcopy((await worker.controls.perform('task.complete', {
                'commandId': 'original-done', 'expectedRevision': 1,
                'condition': 'Produce the original result', 'evidence': ['Retained original evidence']}))['task'])
            target['task'] = copy.deepcopy(member.before)
            old_receipts = copy.deepcopy(worker.controls.tasks.receipts)
            # Current manager input is peer-origin, not a fresh human mandate.
            # Use actual native ingress with no invented latest-human message.
            from amplifier_module_loop_live.runtime import Input
            await parent.runtime.submit(Input('user', 'Finish the existing assignment.', id='standing-manager-input', activation=parent.activation))
            await host.outcome(parent, 'standing-manager-input')
            if cap == 2:
                from test_collaborative_workspaces import until
                await until(lambda: app.collaboration.receipt('fresh-owner-correction').get('delivery') == 'suppressed')
                rejected = app.collaboration.receipt('fresh-owner-correction')
                assert 'exhausted' in rejected['admission']['reason']
                assert member.calls == 0 and tool.calls == 0
                assert worker.controls.tasks.record() == member.before
                assert worker.controls.tasks.receipts == old_receipts
                assert not [e for e in worker.runtime.events if e['type'] == 'generation.started']
                return
            terminal = await host.outcome(worker, 'fresh-owner-correction')
            async with asyncio.timeout(30):
                while not manager.result:
                    await asyncio.sleep(.01)
            await host.outcome(parent, manager.return_id)
            await host.flush()
            assert not host.errors and not host.approvals
            assert tool.calls == 1 and manager.calls == 4
            assert not [m for m in target['messages'] if m.get('inputOrigin') == 'ui']
            assert len([e for e in worker.runtime.events if e['type'] == 'generation.started']) == 1
            receipt = app.collaboration.receipt('fresh-owner-correction')
            assert receipt['delivery'] == 'accepted'
            assert receipt['completedTaskFollowup']['version'] == (2 if independent else 1)
            assert receipt['response']['admittedInputIds'] == ['fresh-owner-correction']
            assert receipt['response']['generationId'] == terminal['generation_id']
            assert receipt['response']['status'] == 'sealed'
            current = worker.controls.tasks.record()
            if successor == 'active':
                assert current['status'] == 'active'
                assert member.accounted == [3, 4] and member.calls == 5
                assert worker.session.coordinator.session_state['goal'] is None
                assert worker.goal_progress[-1]['state'] == 'cap_hit'
                assert worker.goal_progress[-1]['turn'] == 4
                assert current['goalState']['turns_used'] == 4
                persisted = json.loads(worker.controls.state_path().read_text())
                assert persisted['goal'] is None and persisted['task']['goalState']['turns_used'] == 4
                from test_task_continuity import controls
                from amplifier_web.collaboration_input import exhausted
                restored = controls(target['id'])
                # Mount the saved module configuration surface on this fresh
                # test controller; accounting restoration itself is canonical.
                restored.coordinator.loop.max_iterations = persisted['budget']['maxIterations']
                restored.coordinator.context.max_tokens = persisted['budget'].get('contextTokens')
                try:
                    await restored.restore()
                    assert restored.tasks.record()['goalState']['turns_used'] == 4
                    assert exhausted(restored)
                finally:
                    await restored.close()
            else:
                assert current['status'] == 'completed'
                assert current['goalState']['turns_used'] == 3
            if successor:
                previous = worker.controls.tasks.history[0]
                expected = copy.deepcopy(member.before)
                expected['goalState']['turns_used'] = 3
                expected['lastCompletedInputId'] = 'fresh-owner-correction'
                assert previous == expected
                assert current['followupOf']['taskId'] == previous['id']
            else:
                assert current['id'] == member.before['id']
            for identity, saved in old_receipts.items():
                assert worker.controls.tasks.receipts[identity] == saved
            assert len(list(app.db.execute("SELECT id FROM commands WHERE json_extract(receipt,'$.dependencyRequestId')='fresh-owner-correction'"))) == 1
    finally:
        await app.close()
