"""Own-task settings wait through AppService, Worker, Core/live and streaming.

Provider replies and goal evaluation are scripted. Process retirement/loading
are transport adapters; they use the actual configuration save/readback controls
but do not claim subprocess replacement or live-provider qualification. The
native goal yield, checkpoint/park, writer lease and continuation admission run
without manufactured generation events or a fabricated boundary receipt.
"""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('amplifier_module_loop_live.orchestrator')
pytest.importorskip('amplifier_module_loop_streaming')

from fixtures.collaboration_native_probe import PUBLISH_ROOT, ScriptedProvider, peer_envelopes, tool_output
from test_owner_update_wait_native import OwnerUpdateHost
from test_configuration_authority import grant
from amplifier_web import configuration_continuation, runtime_worker
from amplifier_web.handoff_boundary import inspect
from amplifier_web.management import Management
from amplifier_web.runtime_controls import override_path
from amplifier_web.service import AppService


HUMAN, APPLY = 'settings-native-human', 'settings-native-apply'


class SettingsHost(OwnerUpdateHost):
    needs_mount = False

    def diagnostic(self, worker, stage):
        sid = worker.runtime.session_id
        source = self.app._session(sid)
        operation = source.get('pendingConfiguration', {}).get('operationId')
        record = self.app.management.configuration_record(sid, operation) if operation else {}
        return (super().diagnostic(worker, stage) + '; configuration=' + json.dumps(record.get('state'))
            + '; boundary=' + json.dumps(record.get('boundary'))
            + '; intent=' + str(configuration_continuation.intent_reason(self.app, record) if record else None)
            + '; pending=' + repr([(t.get_name(), [f.f_code.co_name for f in t.get_stack()])
                for t in self.app.tasks]))

    async def before_execute(self, worker):
        await super().before_execute(worker)
        self.workers[worker.runtime.session_id]['process'] = SimpleNamespace(returncode=None)
        self.workers[worker.runtime.session_id]['native_worker_pid'] = os.getpid()

    async def handoff_boundary(self, sid, operation, cancel=False, *, configuration=False, receipt=None):
        assert configuration
        expected = receipt['configurationWait']
        assert expected['owner'] == self.workers[sid]['runtimeOwnerId']
        return await inspect(self.roots[sid], operation, cancel, configuration=True, expected=expected)

    async def stop(self, sid, *, reason):
        assert reason == 'configuration'
        worker = self.roots[sid]
        assert worker.runtime.generation is None and not worker.runtime.queued_inputs
        marker = getattr(worker, '_handoff_boundary', None)
        if marker:
            await inspect(worker, marker['id'], cancel=True, configuration=True, expected=marker['intent'])
        self.needs_mount = True

    async def start(self, session, emit, *, guard=None, **kwargs):
        token = PUBLISH_ROOT.set(session['id'])
        try:
            if guard:
                guard()
            worker = self.roots[session['id']]
            await worker.acquire_for_mutation()
            if self.needs_mount:
                path = override_path(session['id'])
                if path.exists():
                    # The transport adapter models loading the saved plan.
                    # Core exposes this dictionary through a read-only property.
                    loaded = worker.session.coordinator.config
                    loaded.clear()
                    loaded.update(json.loads(path.read_text()))
                await worker.controls.restore()
                self.needs_mount = False
        finally:
            PUBLISH_ROOT.reset(token)

    async def control(self, sid, operation, args, *, guard=None):
        token = PUBLISH_ROOT.set(sid)
        try:
            if operation != 'configuration.apply':
                return await super().control(sid, operation, args, guard=guard)
            worker = self.roots[sid]
            async with worker.command_lock:
                guard()
                from amplifier_web.configuration_recovery import native_apply
                return await native_apply(worker.controls, args['config'], args['_configurationAttempt'],
                    lambda value: self.app._app_bridge('configuration.effect.admit', value, sid),
                    lambda: self.app._session(sid).get('interruptionRevision', 0))
        finally:
            PUBLISH_ROOT.reset(token)


class Owner(ScriptedProvider):
    def __init__(self, host, sid):
        super().__init__()
        self.host, self.sid = host, sid
        self.resumed, self.release = asyncio.Event(), asyncio.Event()

    async def complete(self, request, **kwargs):
        try:
            self.calls += 1
            worker = self.host.roots[self.sid]
            if self.calls == 1:
                assert worker.runtime.generation['input_ids'] == [HUMAN]
                source = self.host.app._session(self.sid)
                assert configuration_continuation.capture(self.host.app, source, self.sid, 'agent'), {
                    'task': source.get('task'), 'generation': source.get('collaborationGeneration'),
                    'worker': self.host.workers[self.sid], 'nativeTask': worker.controls.tasks.record()}
                return self.dispatch(APPLY, 'configuration.apply', {
                    'id': self.sid, 'whenIdle': True, 'config': self.plan})
            if self.calls == 2:
                assert tool_output(request, APPLY)['accepted']
                source = self.host.app._session(self.sid)
                assert source.get('pendingConfiguration'), self.host.app.state.get('management')
                record = self.host.app.management.configuration_record(self.sid,
                    source['pendingConfiguration']['operationId'])
                assert record['request']['source'].get('continuation'), record['request']['source']
                assert getattr(worker, '_handoff_boundary', None), record
                # A settings receipt alone must not start the next turn.
                return self.final('Settings are queued; finish this turn and retain the task.')
            assert self.calls == 3, 'An extra goal turn or input replay occurred'
            envelope, text = peer_envelopes(request)[-1]
            operation = self.host.app._session(self.sid)['pendingConfiguration']['operationId']
            assert envelope['replyToRequestId'] == 'configuration-wait:' + operation
            assert 'settings are applied and verified' in text, json.dumps(
                self.host.app.management.configuration_record(self.sid, operation)['state'])
            self.input_id = envelope['inputId']
            assert worker.runtime.generation['input_ids'] == [self.input_id]
            self.goal = copy.deepcopy(worker.session.coordinator.session_state['goal'])
            self.task = copy.deepcopy(worker.controls.tasks.record())
            self.resumed.set()
            await asyncio.wait_for(self.release.wait(), 15)
            return self.final('Finished the retained task without repeating the earlier upload.')
        except Exception as exc:
            self.host.fail('settings provider', exc)
            raise


async def test_native_settings_checkpoint_and_single_continuation(tmp_path, monkeypatch):
    host = SettingsHost()
    monkeypatch.setattr(runtime_worker, 'publish', host.publish)
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'app'))
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    app = host.app = AppService(tmp_path / 'app', host, workspace=workspace)
    app.management = Management(app)
    source = app._new_session({'title': 'Native settings wait', 'bundle': 'synthetic-settings'})
    app.state['sessions'] = [source]
    sid = source['id']
    paths = {name: Path(importlib.util.find_spec('amplifier_module_' + name.replace('-', '_')).origin).parent
             for name in ('loop-live', 'context-simple')}
    provider = Owner(host, sid)
    host.pump = asyncio.create_task(host.route_events())
    try:
        async with asyncio.timeout(35):
            worker = await host.mount(source, provider, paths)
            provider.plan = copy.deepcopy(worker.session.coordinator.config)
            # This fixture mounts its scripted provider directly after Core's
            # module loading. Include it in the saved plan as a normal mount.
            provider.plan['providers'] = [{'module': provider.name, 'config': {}}]
            provider.plan['session']['orchestrator']['config']['max_iterations'] = 17
            await grant(app, ['runtime_configuration'], ['session'])
            evaluations = []

            async def evaluate(*args):
                evaluations.append(copy.deepcopy(worker.session.coordinator.session_state['goal']))
                return len(evaluations) == 2, 'Keep the saved task moving.'

            worker.session.coordinator.get('orchestrator')._evaluate_goal = evaluate
            created = await app.dispatch('task.create', {
                'sessionId': sid, 'expectedRevision': 0, 'objective': 'Finish the saved report.',
                'maxTurns': 8, 'artifactRefs': ['previous-upload-receipt'],
            }, origin='ui', command_id='settings-native-task', include_state=False)
            assert created['accepted']
            goal = worker.session.coordinator.session_state['goal']
            goal.update(turns_used=2, continuations=1, reasons=['Prior progress.'])
            worker.controls.persist(task_only=True)
            task_before = copy.deepcopy(worker.controls.tasks.record())
            sent = await app.dispatch('conversation.send', {'sessionId': sid, 'text': 'Finish the report with the authorized settings.'},
                                      origin='ui', command_id=HUMAN, include_state=False)
            assert sent['accepted']
            await host.wait_stage(worker, host.delivered(sid, HUMAN), 'human input delivery')
            await host.wait_stage(worker, host.outcome(worker, HUMAN), 'native goal yield')
            assert provider.calls == 2 and len(evaluations) == 1
            operation = source['pendingConfiguration']['operationId']
            yielded = copy.deepcopy(worker.session.coordinator.session_state['goal'])
            assert yielded['turns_used'] == 3 and yielded['cap'] == 8 and yielded['continuations'] == 1
            assert worker._handoff_boundary['yieldedGoal'] == yielded
            await configuration_continuation.settle_worker(app.management, sid, operation)
            assert not app.management.configuration_record(sid, operation)['boundary']['ready']
            old_handle = worker.shared_handle
            await host.checkpoint_and_park(worker)
            assert not old_handle.active
            saved = json.loads(worker.controls.state_path().read_text())
            assert saved['configurationCheckpoint']['operationId'] == operation
            assert saved['goal'] == yielded
            await host.wait_stage(worker, provider.resumed.wait(), 'verified settings continuation')
            assert provider.goal == yielded
            for key in ('id', 'objective', 'maxTurns', 'constraints', 'artifactRefs', 'continuationEpoch'):
                assert provider.task.get(key) == task_before.get(key)
            record = app.management.configuration_record(sid, operation)
            assert record['state']['phase'] == 'ready' and record['state']['effect'] == 'mounted'
            assert app.management.configuration_readback_matches(provider.plan, source['configuration']['plan'])
            provider.release.set()
            await host.wait_stage(worker, host.outcome(worker, provider.input_id), 'continued task finished')
            await host.settle()
            assert [g['turns_used'] for g in evaluations] == [3, 4]
            assert worker.controls.tasks.record()['goalState']['turns_used'] == 4
            configuration_continuation.publish_outcome(app.management, source, operation)
            await app.collaboration.drain(sid)
            await host.settle()
            assert provider.calls == 3 and provider.tool_calls == [APPLY]
            assert sum(a['accepted'] is True and a['inputId'] == provider.input_id for a in host.admissions) == 1
            assert not host.failures and not host.errors
    finally:
        provider.release.set()
        await app.close()
