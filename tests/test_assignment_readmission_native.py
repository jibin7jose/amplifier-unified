"""Offline Core/live-loop proof: peer-woken owner reuses a finished member.

The maintained direct native host replaces process transport, not Core, the live
loop, app_control stamping or input admission. Native lease fencing uses the real
Foundation store. This is not subprocess or paid-provider acceptance.
"""
import asyncio
import copy
import importlib.util
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_context_simple')
from fixtures.collaboration_native_probe import ScriptedProvider, tool_output
from test_completed_peer_native import NativeHost
from test_collaborative_workspaces import until
from amplifier_web import runtime_worker
from amplifier_web.runtime import RuntimeManager
from amplifier_web.service import AppService


class Member(ScriptedProvider):
    async def complete(self, request, **kwargs):
        self.calls += 1
        assert self.calls <= 2, 'Old input or follow-up was replayed'
        return self.final('Original result' if self.calls == 1 else 'Fresh follow-up result')


class Manager(ScriptedProvider):
    def __init__(self, app, host, source, target, member):
        super().__init__()
        self.app, self.host = app, host
        self.source, self.target, self.member = source, target, member
        self.retirement_result = None

    async def complete(self, request, **kwargs):
        self.calls += 1
        base = {'sessionId': self.source['id'], 'targetSessionId': self.target['id']}
        if self.calls == 1:
            assert self.app.collaboration.current_human_input(self.source) is None
            return self.dispatch('native-retire', 'assignment.retire', {
                **base, 'expectedRevision': self.app.assignments.current(self.source)['revision']})
        if self.calls == 2:
            result = tool_output(request, 'native-retire')
            self.retirement_result = result
            assert result['accepted'] and not result['result']['inputSent'], result
            assert self.member.calls == 1
            return self.dispatch('native-review', 'assignment.adoption', base)
        if self.calls == 3:
            observation = tool_output(request, 'native-review')['result']
            assert observation['eligible'], observation
            return self.dispatch('native-readmit', 'assignment.adopt', {
                **base, 'expectedRevision': observation['assignmentRevision'],
                'expectedTargetRevision': observation['revision']})
        if self.calls == 4:
            result = tool_output(request, 'native-readmit')
            assert result['accepted'] and not result['result']['inputSent'], result
            assert result['membership']['sourceMessageId'] is None
            assert result['readmissionAuthority']['origin'] == 'agent'
            assert self.member.calls == 1
            assert self.host.roots[self.target['id']].controls.tasks.record() == self.before
            return self.dispatch('native-fresh-followup', 'coordination.send', {
                'sessionId': self.target['id'], 'mode': 'queue',
                'text': 'One new deliberate follow-up', 'returnNotification': False})
        assert self.calls == 5
        result = tool_output(request, 'native-fresh-followup')
        assert result['accepted'], result
        return self.final('Readmitted the same finished member and sent one fresh input.')


@pytest.mark.parametrize('used', [3, 8])
@pytest.mark.parametrize('lost_completion_reply', [False, True])
async def test_peer_only_full_native_readmission_preserves_task_and_fences_old_input(tmp_path, monkeypatch, used, lost_completion_reply, record_property):
    host = NativeHost()
    host.workers = {}  # Direct sessions below, no subprocess mounts to retire.
    host.stopped_boundary = RuntimeManager().stopped_boundary
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    app = host.app = AppService(tmp_path / 'app', host, workspace=workspace)
    source, target, director = [app._new_session({'title': title})
                                for title in ('Manager', 'Member', 'Director')]
    app.state['sessions'] = [source, target, director]
    assignment = app.assignments.ensure(source, 'fixture-assignment')
    assignment['members'][target['id']] = {'status': 'bound', 'role': 'commissioned',
                                         'creatorSessionId': source['id']}
    target['assignmentId'] = assignment['id']
    app.assignments.save(assignment)
    await app.dispatch('access.set', {'sessionId': source['id'], 'scope': 'session',
        'mode': 'full', 'expectedRevision': app.access_mode.snapshot(source)['revision']})
    # No native tools were mounted when this fixture preference changed.
    source['fileAccessPending'] = source['configurationPending'] = False
    app._publish()
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    host.pump = asyncio.create_task(host.route_events())
    member_provider = Member()
    manager_provider = Manager(app, host, source, target, member_provider)
    record_property('acceptance_scope', 'fresh-followup')
    try:
        async with asyncio.timeout(40):
            manager = await host.mount(source, manager_provider, paths)
            member = await host.mount(target, member_provider, paths)
            loop = manager.session.coordinator.get('orchestrator')
            loop_source = Path(inspect.getfile(type(loop)))
            record_property('mounted_loop_class', type(loop).__module__ + '.' + type(loop).__name__)
            record_property('mounted_loop_source', str(loop_source))
            record_property('mounted_loop_sha256', hashlib.sha256(loop_source.read_bytes()).hexdigest())
            await app.dispatch('coordination.send', {'senderSessionId': source['id'],
                'sessionId': target['id'], 'mode': 'queue', 'text': 'Original member work'},
                command_id='native-old-input', include_state=False)
            await host.outcome(member, 'native-old-input')
            await member.controls.perform('task.create', {'commandId': 'old-task',
                'origin': 'agent', 'expectedRevision': 0, 'objective': 'Retained accepted contribution',
                'maxTurns': 8})
            member.session.coordinator.session_state['goal'].update(turns_used=used, continuations=used - 1)
            target['task'] = copy.deepcopy(member.controls.tasks.record())
            if lost_completion_reply:
                # Real Core controls persist, then the transport loses only the
                # acknowledgement. No task publication repairs the host cache.
                original_control = host.control
                async def lost(sid, operation, args, *, guard=None):
                    await original_control(sid, operation, args, guard=guard)
                    raise TimeoutError('Lost native completion acknowledgement')
                async def prepared(session, **kwargs):
                    assert session['id'] == target['id']
                host.control = lost
                app.management = SimpleNamespace(ensure_runtime=prepared)
                with pytest.raises(TimeoutError):
                    await app.dispatch('task.complete', {
                        'sessionId': target['id'], 'expectedRevision': 1,
                        'condition': 'Retained accepted contribution',
                        'evidence': ['Original contribution checked']},
                        command_id='old-task-done', origin='ui')
                app.management = None
                host.control = original_control
                task = copy.deepcopy(member.controls.tasks.record())
                assert target['task']['status'] == 'active' and task['status'] == 'completed'
            else:
                task = (await member.controls.perform('task.complete', {'commandId': 'old-task-done',
                    'origin': 'agent', 'expectedRevision': 1,
                    'condition': 'Retained accepted contribution',
                    'evidence': ['Original contribution checked']}))['task']
                target['task'] = copy.deepcopy(task)
            manager_provider.before = copy.deepcopy(task)
            task_receipts = copy.deepcopy(member.controls.tasks.receipts)
            old = copy.deepcopy(app.collaboration.receipt('native-old-input'))
            app._publish()
            await app.dispatch('coordination.send', {'senderSessionId': director['id'],
                'sessionId': source['id'], 'mode': 'queue', 'text': 'Perform the authorized follow-up'},
                command_id='native-peer-trigger', include_state=False)
            try:
                await host.outcome(manager, 'native-peer-trigger')
            except TimeoutError:
                pytest.fail(str({'trigger': app.collaboration.receipt('native-peer-trigger'),
                    'managerCalls': manager_provider.calls, 'errors': host.errors,
                    'status': source['status'], 'task': source.get('task')}))
            if used == 8:
                await until(lambda: app.collaboration.receipt('native-fresh-followup')['delivery'] == 'suppressed')
                assert 'exhausted' in app.collaboration.receipt('native-fresh-followup')['admission']['reason']
                assert member.controls.tasks.record() == task
                assert member_provider.calls == 1
            else:
                await host.outcome(member, 'native-fresh-followup')
                await app.collaboration.drain(target['id'])
                current = member.controls.tasks.record()
                for key in ('id', 'status', 'objective', 'maxTurns', 'completionEvidence'):
                    assert current[key] == task[key]
                assert current['goalState']['turns_used'] == used + 1
                assert current['lastCompletedInputId'] == 'native-fresh-followup'
                assert member_provider.calls == 2
            for identity, record in task_receipts.items():
                assert member.controls.tasks.receipts[identity] == record
            assert app.collaboration.receipt('native-old-input') == old
            assert 'earlier assignment membership' in app.collaboration.guard(old)
            assert app.assignments.current(target)['id'] == assignment['id']
            assert app.collaboration.receipt('native-retire')['reuseBinding']
            assert [row['inputId'] for row in host.admissions
                    if row['sessionId'] == target['id'] and row['accepted']] == (
                ['native-old-input'] if used == 8
                else ['native-old-input', 'native-fresh-followup'])
            assert manager_provider.calls == 5
            assert not host.approvals and not host.errors
    finally:
        record_property('retirement_tool_result', json.dumps(manager_provider.retirement_result))
        app.management = None
        await app.close()
