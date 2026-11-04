"""No global history demand; explicit human recovery and equivalent config proof."""
import copy
import json
from unittest.mock import AsyncMock

import pytest

from amplifier_web.service import AppError
from amplifier_web.session_creation import template, configuration_matches
from test_schedule_new_tasks import fixture, create


def test_focused_schedule_navigation_is_valid_and_repeatable_without_authority_change():
    from amplifier_web.runtime_navigation import update
    value = update({}, {'panel': 'chat-work', 'runtimeDraft': {'section': 'schedules'}})
    assert value['panel'] == 'chat-work'
    assert value['runtimeDraft'] == {'section': 'schedules', 'tab': 'direction', 'revealRevision': 1}
    again = update(value, {'runtimeDraft': {'section': 'schedules'}})
    assert again['panel'] == 'chat-work' and again['runtimeDraft']['revealRevision'] == 2
    with pytest.raises(AppError, match='different Chat controls tab'):
        update({}, {'runtimeDraft': {'section': 'schedules', 'tab': 'computer'}})


async def test_overview_is_bounded_read_without_owner_loading_or_work(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        for index in range(75):
            app.schedules.store.put({**record, 'id': f'schedule-{index:03}', 'sessionId': 'missing-owner' if index == 0 else sid})
        app.history.ensure_loaded = AsyncMock(side_effect=AssertionError('Overview loaded history'))
        app.management.ensure_runtime = AsyncMock(side_effect=AssertionError('Overview started runtime'))
        before = copy.deepcopy(app.state['sessions'])
        first = (await app.app_bridge('dispatch', {'action': 'schedule.overview', 'args': {'limit': 10}}, sid))['result']
        assert first['total'] == 76 and len(first['items']) == 10 and first['hasMore']
        assert any(row['displayState'] == 'owner_unavailable' for row in first['items'])
        second = (await app.dispatch('schedule.overview', {'offset': 10, 'limit': 10}))['result']
        assert not {row['id'] for row in first['items']} & {row['id'] for row in second['items']}
        assert app.state['sessions'] == before and not runtime.inputs
        assert not app.history.ensure_loaded.called and not app.management.ensure_runtime.called
        assert all('authorization' not in row and 'newTaskConfiguration' not in row for row in first['items'])
    finally: await app.close()


async def test_equivalent_checkpoint_controls_do_not_invalidate_but_real_choices_do(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        source = app._session(sid)
        path = app.data_dir / 'sessions' / sid / 'control-state.json'
        original = json.loads(path.read_text())
        original['configurator'] = {'disabled': {'tools': ['tool-b', 'tool-a'], 'hooks': []}}
        path.write_text(json.dumps(original))
        record, _ = await create(app, sid, now[0])
        canonical = template(app, source)[1]
        equivalent = copy.deepcopy(original)
        equivalent['configurator']['disabled'] = {'tools': ['tool-a', 'tool-b']}
        equivalent.pop('mode', None)
        equivalent.pop('selection', None)  # Exact effective selection remains on source.
        equivalent['budget']['maxOutputTokens'] = None
        path.write_text(json.dumps(equivalent))
        current = template(app, source)[1]
        assert canonical['legacyConfigurationHash'] != current['legacyConfigurationHash']
        assert canonical['configurationHash'] == current['configurationHash']
        assert app.schedules.dependency_reason(source, None, record) is None
        # A real budget change stays binding and diagnostics expose category only.
        equivalent['budget']['maxIterations'] = 3
        path.write_text(json.dumps(equivalent))
        reason = app.schedules.dependency_reason(source, None, record)
        assert 'controls.budget' in reason and 'synthetic-private-value' not in reason
        path.write_text(json.dumps(original))
        legacy = {'configurationHash': canonical['legacyConfigurationHash']}
        assert configuration_matches(legacy, template(app, source)[1])
    finally: await app.close()


@pytest.mark.parametrize('status,stopped', [('error', False), ('stopped', True)])
async def test_human_recovery_does_not_wake_owner_and_next_due_runs_once(tmp_path, monkeypatch, status, stopped):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        owner = app._session(sid)
        owner.update(status=status, interruptionRevision=int(stopped))
        app.schedules.store.review(sid, record['id'], 'Configuration needs review', now[0])
        record = app.schedules.store.get(sid, record['id'])
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        warm = app.management.ensure_runtime = AsyncMock()
        preview = (await app.dispatch('schedule.repairPreview', args))['result']
        assert preview['requiresStopAcknowledgement'] is stopped
        assert not warm.called and not runtime.inputs
        confirm = {**args, 'previewHash': preview['previewHash']}
        if stopped:
            with pytest.raises(AppError, match='after Stop'):
                await app.dispatch('schedule.repairResume', confirm)
        result = await app.dispatch('schedule.repairResume', {**confirm, 'acknowledgeStop': stopped}, command_id='repair')
        assert result['result']['schedule']['status'] == 'active'
        assert owner['status'] == status and not warm.called and not runtime.inputs
        overview = (await app.dispatch('schedule.overview', {}))['result']
        assert overview['items'][0]['displayState'] == 'active'
        # Persist/checkpoint the same effective choices before the actual due tick.
        from test_task_continuity import controls
        control = controls(sid)
        control.coordinator.config = json.loads((app.data_dir / 'sessions' / sid / 'effective-configuration.json').read_text())
        control.selection = dict(owner['selection'])
        await control.checkpoint(); await control.close()
        now[0] += 61
        await app.schedules.tick(); await app.schedules.tick()
        assert len(runtime.inputs) == 1 and runtime.inputs[0][0] != sid
        assert owner['status'] == status
        assert all(call.args[0]['id'] != sid for call in warm.call_args_list)
        assert app.schedules.store.runs(sid)[0]['phase'] == 'accepted'
    finally: await app.close()


async def test_recovery_due_uses_real_management_and_preserves_both_occurrences(tmp_path, monkeypatch):
    """The browser fixture uses Management, not attach()'s mocked preparation."""
    from amplifier_web.management import Management
    from test_schedules import finish
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    app.management = Management(app)
    started = []
    original_start = runtime.start
    async def observed_start(session, emit, **kwargs):
        started.append(session['id'])
        return await original_start(session, emit, **kwargs)
    runtime.start = observed_start
    try:
        record, _ = await create(app, sid, now[0])
        owner = app._session(sid)
        before_configuration = template(app, owner)[1]
        owner.update(status='stopped', interruptionRevision=1)
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        preview = (await app.dispatch('schedule.repairPreview', args))['result']
        await app.dispatch('schedule.repairResume', {
            **args, 'previewHash': preview['previewHash'], 'acknowledgeStop': True})
        assert not started and not runtime.inputs
        previous = None
        destinations = set()
        for occurrence in range(2):
            now[0] = app.schedules.store.get(sid, record['id'])['nextDue'] + 1
            await app.schedules.tick(); await app.schedules.tick()
            run = app.schedules.store.runs(sid)[0]
            assert run['phase'] == 'accepted', run
            assert run['creationConfirmed'] and run['taskId']
            assert run['destinationSessionId'] not in destinations | {sid}
            destinations.add(run['destinationSessionId'])
            assert len(runtime.inputs) == occurrence + 1
            assert runtime.inputs[-1][0] == run['destinationSessionId']
            await finish(app, run['destinationSessionId'], run)
            if previous:
                assert app.schedules.store.run(sid, previous['id']) == previous
            previous = copy.deepcopy(app.schedules.store.run(sid, run['id']))
            assert previous['phase'] == 'completed'
        assert set(started) == destinations and sid not in started
        assert owner['status'] == 'stopped' and owner['interruptionRevision'] == 1
        assert owner['messages'] == [] and template(app, owner)[1] == before_configuration
    finally:
        await app.close()


async def test_synthetic_start_honors_guard_after_management_preflight(tmp_path, monkeypatch):
    from amplifier_web.management import Management
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch)
    app.management = Management(app)
    calls = []
    def guard():
        calls.append('checked')
        if len(calls) == 2:
            raise ValueError('Stop won the startup boundary')
    try:
        with pytest.raises(ValueError, match='Stop won'):
            await app.management.ensure_runtime(app._session(sid), restart_guard=guard)
        assert calls == ['checked', 'checked']  # host preflight and runtime boundary
        assert not runtime.inputs and not runtime.workers
    finally:
        await app.close()


async def test_recovery_denies_agent_stale_stop_configuration_and_unknown(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        with pytest.raises(AppError, match='person'):
            await app.app_bridge('dispatch', {'action': 'schedule.repairPreview', 'args': args}, sid)
        preview = (await app.dispatch('schedule.repairPreview', args))['result']
        app._session(sid)['interruptionRevision'] = app._session(sid).get('interruptionRevision', 0) + 1
        with pytest.raises(AppError, match='Stop intent changed'):
            await app.dispatch('schedule.repairResume', {**args, 'previewHash': preview['previewHash'], 'acknowledgeStop': True})
        app._session(sid)['interruptionRevision'] -= 1
        app._session(sid)['selection']['effort'] = 'low'
        with pytest.raises(AppError, match='configuration'):
            await app.dispatch('schedule.repairResume', {**args, 'previewHash': preview['previewHash']})
        app.schedules.store.put_run({'id': 'uncertain', 'sessionId': sid, 'scheduleId': record['id'], 'dueAt': now[0], 'phase': 'unknown', 'revision': 1})
        with pytest.raises(AppError, match='uncertain'):
            await app.dispatch('schedule.repairPreview', args)
        assert not runtime.inputs
    finally: await app.close()


@pytest.mark.parametrize('guard', ['assignment', 'configurationBusy', 'execution', 'missing_workspace'])
async def test_repair_rechecks_current_authority_and_execution_before_effect(tmp_path, monkeypatch, guard):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        owner = app._session(sid)
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        preview = (await app.dispatch('schedule.repairPreview', args))['result']
        if guard == 'assignment':
            app.assignments.blocked = lambda session: 'This assignment was stopped; automatic work is suppressed.'
            overview = (await app.dispatch('schedule.overview', {}))['result']
            assert overview['items'][0]['displayState'] == 'blocked'
        elif guard == 'configurationBusy': owner['configurationBusy'] = True
        elif guard == 'execution': owner['executionRevision'] = owner.get('executionRevision', 0) + 1
        else: owner['workingDirectory'] = str(tmp_path / 'removed-folder')
        with pytest.raises(AppError):
            await app.dispatch('schedule.repairResume', {**args, 'previewHash': preview['previewHash']})
        assert app.schedules.store.get(sid, record['id'])['revision'] == record['revision']
        assert not runtime.inputs
    finally: await app.close()


async def test_stop_wins_final_admission_after_human_recovery(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        owner = app._session(sid); owner['status'] = 'error'
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        preview = (await app.dispatch('schedule.repairPreview', args))['result']
        await app.dispatch('schedule.repairResume', {**args, 'previewHash': preview['previewHash']})
        runtime.before_admission = lambda: owner.update(status='stopped', interruptionRevision=owner.get('interruptionRevision', 0)+1)
        now[0] += 61
        await app.schedules.tick(); await app.schedules.tick()
        assert not runtime.inputs
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'skipped' and 'stop' in run['detail'].lower()
        assert owner['status'] == 'stopped'
    finally: await app.close()


async def test_missing_configuration_is_prepared_only_for_explicit_normal_preview(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        path = app.data_dir / 'sessions' / sid / 'effective-configuration.json'
        saved = path.read_text(); path.unlink()
        app.management.ensure_runtime = AsyncMock(side_effect=lambda owner: path.write_text(saved))
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        with pytest.raises(AppError, match='Prepare the source'):
            await app.dispatch('schedule.repairPreview', args)
        assert not app.management.ensure_runtime.called
        await app.dispatch('schedule.preview', {'sessionId': sid, **app.schedules.configuration(record)})
        assert app.management.ensure_runtime.await_count == 1
        assert not runtime.inputs
    finally: await app.close()


@pytest.mark.parametrize('expected', [None, '', 123, {}, 'g'*64, 'a'*63])
def test_missing_or_malformed_configuration_fingerprint_never_matches(expected):
    assert not configuration_matches({'configurationHash': expected}, {'configurationHash': expected})
    assert not configuration_matches({'configurationHash': expected}, {'legacyConfigurationHash': expected})
    assert not configuration_matches({}, {})


async def test_future_recovery_preserves_an_already_accepted_target(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        now[0] += 61; await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'accepted'
        target = copy.deepcopy(app._session(run['destinationSessionId']))
        app._session(sid)['status'] = 'error'
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        preview = (await app.dispatch('schedule.repairPreview', args))['result']
        await app.dispatch('schedule.repairResume', {**args, 'previewHash': preview['previewHash']})
        assert app.schedules.store.run(sid, run['id']) == run
        assert app._session(run['destinationSessionId']) == target
        assert len(runtime.inputs) == 1
        now[0] += 61; await app.schedules.tick()
        assert len(runtime.inputs) == 1  # Outstanding accepted target still prevents overlap.
    finally: await app.close()


async def test_recovery_finds_uncertain_run_behind_newer_completed_history(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        for index in range(105):
            app.schedules.store.put_run({'id': 'run-' + str(index), 'sessionId': sid, 'scheduleId': record['id'], 'dueAt': now[0]+index, 'phase': 'unknown' if index==0 else 'completed', 'revision': 1})
        args = {'sessionId': sid, 'id': record['id'], 'expectedRevision': record['revision']}
        # No JSON history read or page can accidentally omit the older unknown.
        app.schedules.store.runs = lambda *args: (_ for _ in ()).throw(AssertionError('Unbounded history read'))
        overview = (await app.dispatch('schedule.overview', {}))['result']
        assert overview['items'][0]['lastRun']['phase'] == 'completed'
        assert overview['items'][0]['displayState'] == 'needs_review'
        with pytest.raises(AppError, match='uncertain'):
            await app.dispatch('schedule.repairPreview', args)
        assert not runtime.inputs
    finally: await app.close()
