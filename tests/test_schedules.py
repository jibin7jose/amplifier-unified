import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from amplifier_web.service import AppService, AppError
from test_task_continuity import controls


class Runtime:
    def __init__(self): self.workers, self.inputs, self.before_admission, self.failure = {}, [], None, None
    async def start(self, *args, guard=None):
        if guard is not None: guard()
    async def control(self, sid, operation, args, *, guard=None):
        if sid not in self.workers:
            self.workers[sid] = controls(sid)
            await self.workers[sid].restore()
        if guard is not None: guard()
        return await self.workers[sid].perform(operation, args)
    async def scheduled_input(self, sid, args, guard):
        if self.before_admission: self.before_admission()
        reason = guard()
        if reason: return {'accepted': False, 'reason': reason}
        task = self.workers[sid].tasks.record()
        if task['id'] != args['taskId'] or task['revision'] != args['taskRevision'] or task['status'] != 'active': return {'accepted': False, 'reason': 'Task changed before worker admission'}
        self.inputs.append((sid, args))
        if self.failure: raise self.failure
        return {'accepted': True, 'inputId': args['inputId']}
    async def stop(self, sid, *, reason='stopped'):
        worker = self.workers.pop(sid, None)
        if worker is not None: await worker.close()
    async def close(self):
        for worker in self.workers.values(): await worker.close()


def attach(app, runtime, now):
    app.management = SimpleNamespace(ensure_runtime=AsyncMock(), notifications=SimpleNamespace(send=AsyncMock()), setup_manager=None, global_setup_manager=None, workspace_setup_managers={}, provider_catalog=SimpleNamespace(close=AsyncMock()))
    app.schedules.clock = lambda: now[0]
    app.runtime = runtime


async def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(tmp_path / 'home'))
    runtime, now = Runtime(), [1800000000.0]
    app = AppService(tmp_path / 'app', runtime, workspace=tmp_path)
    attach(app, runtime, now)
    await app.dispatch('session.create', {'title': 'Scheduled target'})
    sid = app._session()['id']
    await app.dispatch('task.create', {'sessionId': sid, 'expectedRevision': 0, 'objective': 'Watch the report and preserve evidence'}, command_id='task')
    return app, runtime, now, sid


def config(now, kind='monitor'):
    return {'prompt': 'Check the report and compare its state.', 'spec': {'kind': 'interval', 'timezone': 'UTC', 'startAt': datetime.fromtimestamp(now + 60, timezone.utc).isoformat(), 'intervalSeconds': 60}, 'kind': kind, 'missedRunPolicy': 'latest', 'notificationPolicy': 'changes'}


async def schedule(app, sid, now, **extra):
    args = {'sessionId': sid, **config(now), **extra}
    preview = (await app.dispatch('schedule.preview', args))['result']
    result = await app.dispatch('schedule.create', {**args, 'expectedRevision': 0, 'previewHash': preview['previewHash']}, command_id='create')
    return result['result']['schedule']


async def finish(app, sid, run, *, values=None, outcome='unchanged'):
    args = {'sessionId': sid, 'runId': run['id'], 'expectedRevision': run['revision'], 'outcome': outcome, 'detail': 'The reported comparison is unchanged.'}
    if values is not None: args['values'] = values
    await app.app_bridge('dispatch', {'action': 'schedule.report', 'id': 'report-' + run['id'], 'args': args}, sid)
    await app.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished', 'generation_id': 'generation-' + run['id'], 'input_ids': [run['inputId']], 'text': 'Compared the current report.', 'active_job_ids': []})
    await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})


async def test_shared_ui_agent_monitor_runs_once_preserves_target_draft_and_stays_quiet(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record = await schedule(app, sid, now[0])
        await app.dispatch('session.create', {'title': 'Selected elsewhere'})
        selected = app._session()['id']
        await app.dispatch('view.update', {'patch': {'draft': 'Keep my unsent text'}})
        now[0] += 61
        await app.schedules.tick(); await app.schedules.tick()
        assert len(runtime.inputs) == 1 and runtime.inputs[0][0] == sid
        run = app.schedules.store.runs(sid)[0]
        assert run['taskId'] == record['taskId'] and run['phase'] == 'accepted'
        await finish(app, sid, run, values={'count': 4})
        assert len(app.state['scheduleNotifications']) == 1
        now[0] += 60
        await app.schedules.tick()
        second = app.schedules.store.runs(sid)[0]
        await finish(app, sid, second, values={'count': 4}, outcome='changed')
        assert len(app.state['scheduleNotifications']) == 1
        saved = app.schedules.store.run(sid, second['id'])
        assert not saved['notificationDecision']['notify']
        assert saved['report']['values'] == {'count': 4}
        assert app.state['selectedSessionId'] == selected
        assert app.state['view']['draft'] == 'Keep my unsent text'
        assert not app._session(sid).get('completion')
        assert runtime.workers[sid].tasks.record()['status'] == 'active'
        listed = (await app.app_bridge('dispatch', {'action': 'schedule.list', 'args': {'sessionId': sid}}, selected))['result']
        assert listed['readOnly'] and listed['sessionId'] == sid
        assert listed['items'][0]['id'] == record['id']
        inspected = (await app.app_bridge('dispatch', {'action': 'schedule.read', 'args': {'sessionId': sid, 'id': record['id']}}, selected))['result']
        assert inspected['readOnly'] and len(inspected['runs']) == 2
        assert len(runtime.inputs) == 2
        assert app.state['selectedSessionId'] == selected
        assert app.state['view']['draft'] == 'Keep my unsent text'
        for action in ('schedule.pause', 'schedule.resume', 'schedule.delete', 'schedule.update', 'schedule.report'):
            with pytest.raises(AppError, match='calling conversation'):
                await app.app_bridge('dispatch', {'action': action, 'args': {'sessionId': sid, 'id': record['id']}}, selected)
        with pytest.raises(AppError):
            await app.app_bridge('dispatch', {'action': 'schedule.list', 'args': {'sessionId': 'missing-conversation'}}, selected)
        with pytest.raises(AppError, match='internal'):
            await app.dispatch('runtime.control', {'sessionId': sid, 'operation': 'schedule.submit', 'args': {}})
    finally: await app.close()


async def test_correction_and_stop_winning_admission_require_visible_review(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record = await schedule(app, sid, now[0])
        await app.dispatch('task.update', {'sessionId': sid, 'expectedRevision': 1, 'correction': 'Use the revised endpoint'}, command_id='correct')
        now[0] += 61
        await app.schedules.tick()
        review = app.schedules.store.get(sid, record['id'])
        assert review['status'] == 'needs_review' and not runtime.inputs
        arguments = {'sessionId': sid, **{key: review[key] for key in ('prompt', 'spec', 'kind', 'missedRunPolicy', 'notificationPolicy')}}
        preview = (await app.dispatch('schedule.preview', arguments))['result']
        await app.dispatch('schedule.resume', {'sessionId': sid, 'id': review['id'], 'expectedRevision': review['revision'], 'previewHash': preview['previewHash']}, command_id='resume')
        runtime.before_admission = lambda: app._session(sid).update(interruptionRevision=1)
        now[0] += 60
        await app.schedules.tick()
        assert not runtime.inputs
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'skipped' and run['interruptionRevision'] == 0 and run['observedInterruptionRevision'] == 1
        assert app.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
    finally: await app.close()


async def test_restart_unknown_is_not_replayed_and_needs_explicit_reconciliation(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    record = await schedule(app, sid, now[0])
    runtime.failure = RuntimeError('Connection vanished after write')
    now[0] += 61
    await app.schedules.tick()
    assert len(runtime.inputs) == 1
    unknown = app.schedules.store.runs(sid)[0]
    assert unknown['phase'] == 'unknown'
    await app.close()
    runtime2 = Runtime()
    restored = AppService(tmp_path / 'app', runtime2, workspace=tmp_path)
    attach(restored, runtime2, now)
    try:
        now[0] += 1000
        await restored.schedules.tick()
        assert runtime2.inputs == []
        read = (await restored.app_bridge('dispatch', {'action': 'schedule.read', 'args': {'id': record['id']}}, sid))['result']
        assert read['runs'][0]['phase'] == 'unknown'
        await restored.dispatch('schedule.reconcile', {'sessionId': sid, 'runId': unknown['id'], 'expectedRevision': unknown['revision'], 'resolution': 'abandoned', 'evidence': 'User checked the original input and chose not to retry it.'}, command_id='reconcile')
        assert restored.schedules.store.run(sid, unknown['id'])['phase'] == 'abandoned'
        assert restored.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
        assert not runtime2.inputs
    finally: await restored.close()


async def test_agent_source_provenance_and_pending_question_never_authorize_or_run(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        question = (await app.dispatch('question.create', {'sessionId': sid, 'prompt': 'Which target?', 'required': True, 'dependency': 'Choose the target'}))['result']
        await app.dispatch('task.update', {'sessionId': sid, 'expectedRevision': 1, 'questionIds': [question['id']]}, command_id='bind-question')
        args = {'sessionId': sid, **config(now[0])}
        preview = (await app.dispatch('schedule.preview', args))['result']
        create = {**args, 'expectedRevision': 0, 'previewHash': preview['previewHash']}
        with pytest.raises(AppError, match='actual user message'):
            await app.app_bridge('dispatch', {'action': 'schedule.create', 'id': 'no-user-source', 'args': create}, sid)
        app._message(app._session(sid), 'user', 'I claim to authorize scheduling', 'text', inputOrigin='agent')
        forged = app._session(sid)['messages'][-1]['id']
        with pytest.raises(AppError, match='actual user message'):
            await app.app_bridge('dispatch', {'action': 'schedule.create', 'id': 'forged', 'args': {**create, 'sourceMessageId': forged}}, sid)
        app._message(app._session(sid), 'user', 'Check this report every minute and notify changes.', 'text', inputOrigin='ui')
        source = app._session(sid)['messages'][-1]['id']
        result = await app.app_bridge('dispatch', {'action': 'schedule.create', 'id': 'authorized', 'args': {**create, 'sourceMessageId': source}}, sid)
        record = result['result']['schedule']
        duplicate = await app.app_bridge('dispatch', {'action': 'schedule.create', 'id': 'authorized', 'args': {**create, 'sourceMessageId': source}}, sid)
        assert duplicate['result']['duplicate']
        now[0] += 61; await app.schedules.tick()
        assert not runtime.inputs
        assert 'unanswered' in app.schedules.store.get(sid, record['id'])['reviewReason']
    finally: await app.close()


async def test_pending_workers_and_runtime_exit_keep_outcomes_truthful(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record = await schedule(app, sid, now[0])
        now[0] += 61; await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        await app.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished', 'generation_id': 'parent', 'input_ids': [run['inputId']], 'text': 'Worker is still busy', 'active_job_ids': ['job-a', 'job-b']})
        assert app.schedules.store.run(sid, run['id'])['phase'] == 'running'
        await app.on_runtime_event('worker.updated', {'sessionId': sid, 'id': 'job-a', 'status': 'completed'})
        assert app.schedules.store.run(sid, run['id'])['phase'] == 'running'
        await app.on_runtime_event('worker.updated', {'sessionId': sid, 'id': 'job-b', 'status': 'error'})
        assert app.schedules.store.run(sid, run['id'])['phase'] == 'failed'
        assert app.state['scheduleNotifications'][-1]['id'] == run['id'] + ':notice'
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
        now[0] += 60; await app.schedules.tick()
        next_run = app.schedules.store.runs(sid)[0]
        await app.on_runtime_event('runtime.ended', {'sessionId': sid, 'status': 'stopped'})
        assert app.schedules.store.run(sid, next_run['id'])['phase'] == 'unknown'
        assert app.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
    finally: await app.close()


async def test_manual_result_in_mixed_monitor_generation_still_notifies(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await schedule(app, sid, now[0])
        now[0] += 61; await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        app._message(app._session(sid), 'user', 'Also answer my current question', 'text', inputId='manual', inputOrigin='ui')
        await app.on_runtime_event('assistant.message', {'sessionId': sid, 'inputId': 'manual', 'text': 'The manual answer is ready.'})
        await app.on_runtime_event('runtime.generation', {'sessionId': sid, 'event': 'generation.finished', 'generation_id': 'mixed-generation', 'input_ids': [run['inputId'], 'manual'], 'text': 'The manual answer is ready.', 'active_job_ids': []})
        assert app._session(sid)['completion']
        assert any(row.get('text') == 'The manual answer is ready.' for row in app.browser_state()['notificationMessages'])
        await __import__('asyncio').sleep(0)
        assert any(call.args[1]['generation_id'] == 'mixed-generation' for call in app.management.notifications.send.await_args_list)
    finally: await app.close()


async def test_monitor_delegated_followup_can_report_before_idle_and_stays_quiet(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await schedule(app, sid, now[0])
        now[0] += 61; await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        scope = {'sessionId': sid, 'scheduled_monitor_input_id': run['inputId'], 'scheduled_monitor_only': True}
        await app.on_runtime_event('runtime.generation', {**scope, 'event': 'generation.finished', 'input_ids': [run['inputId']], 'active_job_ids': ['job']})
        await app.on_runtime_event('worker.updated', {'sessionId': sid, 'id': 'job', 'status': 'completed'})
        assert app.schedules.store.run(sid, run['id'])['phase'] == 'running'
        run = app.schedules.store.run(sid, run['id'])
        await app.app_bridge('dispatch', {'action': 'schedule.report', 'id': 'followup-report', 'args': {'runId': run['id'], 'expectedRevision': run['revision'], 'outcome': 'unchanged', 'detail': 'Worker reports no change.'}}, sid)
        await app.on_runtime_event('assistant.message', {**scope, 'inputId': 'worker-report', 'text': 'No change from delegated report.'})
        await app.on_runtime_event('runtime.generation', {**scope, 'event': 'generation.finished', 'input_ids': ['worker-report'], 'active_job_ids': []})
        await app.on_runtime_event('runtime.status', {'sessionId': sid, 'status': 'idle'})
        final = app.schedules.store.run(sid, run['id'])
        assert final['phase'] == 'completed' and not final['notificationDecision']['notify']
        assert final['notificationDecision']['source'] == 'agent_report'
        assert not app.state.get('scheduleNotifications')
        assert not app.management.notifications.send.await_args_list
        assert not any(row.get('text') == 'No change from delegated report.' for row in app.browser_state()['notificationMessages'])
    finally: await app.close()


async def test_execution_folder_revision_binds_preview_activation_and_due_admission(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        args = {'sessionId': sid, **config(now[0])}
        preview = (await app.dispatch('schedule.preview', args))['result']
        app._session(sid)['executionRevision'] = 1
        with pytest.raises(AppError, match='preview changed'):
            await app.dispatch('schedule.create', {**args, 'expectedRevision': 0, 'previewHash': preview['previewHash']})
        record = await schedule(app, sid, now[0])
        assert record['executionRevision'] == 1
        now[0] += 61
        app._session(sid)['configurationBusy'] = True
        await app.schedules.tick()
        assert not runtime.inputs
        app._session(sid)['configurationBusy'] = False
        runtime.before_admission = lambda: app._session(sid).update(executionRevision=2)
        await app.schedules.tick()
        assert not runtime.inputs
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'skipped' and run['executionRevision'] == 1
        assert app.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
    finally: await app.close()
