"""Real native input/tool bridge with scripted providers and no network calls."""
import asyncio
import copy
import importlib.util
import time
from pathlib import Path

import pytest

pytest.importorskip("amplifier_module_loop_live.orchestrator")
pytest.importorskip("amplifier_module_context_simple")
from fixtures.collaboration_native_probe import DirectNativeHost, ScriptedProvider, peer_envelopes, tool_output, message_text
from amplifier_web import runtime_worker
from amplifier_web.service import AppService


class Manager(ScriptedProvider):
    def __init__(self, app, source, target):
        super().__init__()
        self.app, self.source, self.target = app, source, target

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            delivered = [message_text(row) for row in request.messages if row.role == 'user']
            negative = next((text for text in delivered if 'Keep it stopped' in text), None)
            if negative is not None:
                self.negative_delivered = negative
                return self.final('Kept stopped; no resume request issued.')
            return self.dispatch('inspect-input', 'coordination.context', {'sessionId': self.source['id']})
        if self.calls == 2:
            human = tool_output(request, 'inspect-input')['result']['humanInput']
            assert human is not None, 'The exact human input must be discoverable through the native bridge'
            return self.dispatch('request-resume', 'coordination.send', {
                'sessionId': self.target['id'], 'mode': 'queue',
                'text': 'Inspect what finished, then continue the existing task.',
                'resume': {'sourceMessageId': human['sourceMessageId'], 'expectedInterruptionRevision': 1}})
        result = tool_output(request, 'request-resume')
        assert result['accepted'], result
        self.request_id = result['requestId']
        return self.final('Resume requested; delivery is separate from task completion.')


class Member(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        envelope, text = peer_envelopes(request)[0]
        assert envelope['humanResume']['sourceMessageId']
        self.input_id = envelope['inputId']
        return self.final('Previous results reconciled; saved unresolved questions remain unresolved.')


class HandoffOrigin(ScriptedProvider):
    def __init__(self, source, owner, member):
        super().__init__()
        self.source, self.owner, self.member = source, owner, member

    async def complete(self, request, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self.dispatch('origin-context', 'coordination.context', {'sessionId': self.source['id']})
        if self.calls == 2:
            human = tool_output(request, 'origin-context')['result']['humanInput']
            assert human
            return self.dispatch('native-handoff', 'coordination.send', {
                'sessionId': self.owner['id'], 'mode': 'queue',
                'text': 'Review the host-bound original instruction, not this peer prose.',
                'resumeHandoff': {'sourceMessageId': human['sourceMessageId'],
                    'memberSessionId': self.member['id'], 'expectedInterruptionRevision': 1}})
        result = tool_output(request, 'native-handoff')
        assert result['accepted']
        return self.final('One scoped handoff committed; no member ownership transferred.')


class HandoffOwner(ScriptedProvider):
    def __init__(self, owner, member):
        super().__init__()
        self.owner, self.member = owner, member

    async def complete(self, request, **kwargs):
        self.calls += 1
        envelope, _ = peer_envelopes(request)[0]
        scope = envelope['resumeHandoff']
        assert scope['memberSessionId'] == self.member['id']
        instruction = scope['humanInstruction']
        assert instruction['text'] == 'Resume the exact paused member; preserve previous results.'
        assert not instruction['truncated']
        if self.calls == 1:
            return self.dispatch('peer-owner-context', 'coordination.context', {'sessionId': self.owner['id']})
        if self.calls == 2:
            assert tool_output(request, 'peer-owner-context')['result']['humanInput'] is None
            return self.dispatch('native-member-resume', 'coordination.send', {
                'sessionId': self.member['id'], 'mode': 'queue',
                'text': 'Reconcile previous results, then continue only the requested member work.',
                'resume': {'sourceRequestId': envelope['inputId'], 'expectedInterruptionRevision': 1}})
        result = tool_output(request, 'native-member-resume')
        assert result['accepted'], result
        return self.final('Deliberately sent one fresh member input after assessing original human intent.')


@pytest.mark.parametrize('interrupted', [False, True])
async def test_different_origin_and_owner_native_handoff_keeps_human_input_null(tmp_path, monkeypatch, interrupted):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path/'app'))
    workspace = tmp_path/'workspace'; workspace.mkdir()
    app = host.app = AppService(tmp_path/'app', host, workspace=workspace)
    origin, owner, member = [app._new_session({'title': title}) for title in ('Origin', 'Owner', 'Member')]
    app.state['sessions'] = [origin, owner, member]
    app.assignments.ensure(origin, 'origin-assignment')
    assignment = app.assignments.ensure(owner, 'owner-assignment')
    assignment['members'][member['id']] = {'status': 'bound', 'role': 'member'}
    member['assignmentId'] = assignment['id']; app.assignments.save(assignment); app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    source_provider = HandoffOrigin(origin, owner, member)
    owner_provider, member_provider = HandoffOwner(owner, member), Member()
    try:
        async with asyncio.timeout(30):
            source_worker = await host.mount(origin, source_provider, paths)
            owner_worker = await host.mount(owner, owner_provider, paths)
            member_worker = await host.mount(member, member_provider, paths)
            await member_worker.controls.perform('task.create', {
                'commandId': 'saved-task', 'expectedRevision': 0, 'objective': 'Preserve results', 'maxTurns': 3})
            task = (await member_worker.controls.perform('task.pause', {
                'commandId': 'pause-task', 'expectedRevision': 1, 'reason': 'Update boundary'}))['task']
            member.update(task=copy.deepcopy(task), status='stopped', interruptionRevision=1,
                          lastInterruption={'at': time.time()-1, 'origin': 'ui'})
            if interrupted:
                # Retained host lifecycle is fixture-supplied; the stopped
                # boundary itself acquires the real private native owner lock.
                # This remains scripted native evidence, not process recovery.
                from amplifier_web.runtime import RuntimeManager
                member.update(status='interrupted',
                    collaborationGeneration={'id': 'retained-interrupted-member', 'terminal': True,
                        'inputIds': ['prior-member-input'], 'interruptionRevision': 1},
                    failure={'category': 'unknown', 'type': 'ExecutionInterrupted',
                        'generationId': 'retained-interrupted-member'})
                host.stopped_boundary = RuntimeManager().stopped_boundary
            before = copy.deepcopy(member_worker.controls.tasks.record())
            await app.dispatch('conversation.send', {'sessionId': origin['id'],
                'text': 'Resume the exact paused member; preserve previous results.'},
                command_id='human-origin', include_state=False)
            await host.outcome(source_worker, 'human-origin')
            await host.outcome(owner_worker, 'native-handoff')
            await host.outcome(member_worker, 'native-member-resume')
            assert member_provider.calls == 1 and owner_provider.calls == source_provider.calls == 3
            assert member_worker.controls.tasks.record()['status'] == before['status']
            assert member_worker.controls.tasks.record()['revision'] == before['revision']
            assert not host.approvals and not host.errors
            assert len([row for row in host.admissions if row['sessionId'] == member['id']]) == 1
            assert app.collaboration.receipt('native-member-resume')['humanResume']['handoffRequestId'] == 'native-handoff'
            if interrupted:
                proof = app.collaboration.receipt('native-member-resume')['humanResume']['interruptedRecovery']
                assert proof['workerAbsent'] and not proof['inputsReplayed']
                assert proof['ownershipAcquisitionId'] and proof['generation']['terminal']
            assert not [row for row in owner['messages'] + member['messages'] if row.get('inputOrigin') == 'ui']
    finally:
        await app.close()


@pytest.mark.parametrize("task_state", ["paused", "blocked", "completed"])
@pytest.mark.parametrize("input_kind", ["chat", "question", "negative_question"])
async def test_human_instruction_in_manager_reaches_paused_member_native_input(tmp_path, monkeypatch, task_state, input_kind):
    host = DirectNativeHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path/'app'))
    workspace = tmp_path/'workspace'; workspace.mkdir()
    app = host.app = AppService(tmp_path/'app', host, workspace=workspace)
    source, target = [app._new_session({'title': title}) for title in ('Manager', 'Member')]
    app.state['sessions'] = [source, target]
    assignment = app.assignments.ensure(source, 'fixture-assignment')
    assignment['members'][target['id']] = {'status': 'bound', 'role': 'member'}
    target['assignmentId'] = assignment['id']; app.assignments.save(assignment); app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_'+name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    parent, child = Manager(app, source, target), Member()
    try:
        async with asyncio.timeout(30):
            manager = await host.mount(source, parent, paths)
            member = await host.mount(target, child, paths)
            await member.controls.perform('task.create', {
                'commandId': 'task', 'expectedRevision': 0, 'objective': 'Preserve the prior results', 'maxTurns': 3})
            operation = {'paused': 'task.pause', 'blocked': 'task.block', 'completed': 'task.complete'}[task_state]
            task = (await member.controls.perform(operation, {
                'commandId': 'previous-boundary', 'expectedRevision': 1,
                **({'condition': 'Preserve the prior results', 'evidence': ['Saved result']}
                   if task_state == 'completed' else {'reason': 'Awaiting resume and reconciliation'})}))['task']
            target.update(task=copy.deepcopy(task), status='stopped', interruptionRevision=1,
                          lastInterruption={'at': time.time()-1, 'origin': 'agent'})
            # Host stopped state and native saved goal are independently checked.
            # Process re-creation is covered by the companion subprocess test.
            before = copy.deepcopy(member.controls.tasks.snapshot())
            before_record = copy.deepcopy(member.controls.tasks.record())
            if input_kind in {'question', 'negative_question'}:
                question = (await app.dispatch('question.create', {
                    'sessionId': source['id'], 'prompt': 'Resume the paused member?',
                    'required': True, 'dependency': 'Reconcile the existing member task',
                    'options': [{'id': 'resume', 'label': 'Resume the paused member'},
                                {'id': 'stop', 'label': 'Keep it stopped'}]}))['result']
                input_id = 'question:' + question['id'] + ':answer'
                await app.dispatch('question.answer', {'sessionId': source['id'], 'id': question['id'],
                    'expectedRevision': 1, 'optionId': 'stop' if input_kind == 'negative_question' else 'resume'},
                    command_id='resume-answer', include_state=False)
            else:
                input_id = 'resume-human'
                await app.dispatch('conversation.send', {'sessionId': source['id'], 'text': 'Resume the paused member.'},
                                   command_id=input_id, include_state=False)
            await host.outcome(manager, input_id)
            if input_kind == 'negative_question':
                assert 'Resume the paused member?' in parent.negative_delivered
                assert 'Keep it stopped' in parent.negative_delivered
                assert not host.admissions and child.calls == 0 and parent.calls == 1
                assert target['status'] == 'stopped'
                assert member.controls.tasks.record() == before_record
                assert not host.errors
                return
            while not host.admissions:
                await asyncio.sleep(.01)
            assert host.admissions[0]['accepted'], host.admissions
            await host.outcome(member, host.admissions[0]['inputId'])
            assert child.calls == 1 and parent.calls == 3
            assert member.controls.tasks.record()['status'] == task_state
            assert member.controls.tasks.snapshot()['task']['revision'] == before['task']['revision']
            assert not host.approvals and not host.errors
            assert not [m for m in target['messages'] if m.get('inputOrigin') == 'ui']
    finally:
        await app.close()
