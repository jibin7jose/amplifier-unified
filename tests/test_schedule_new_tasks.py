"""Explicit new-task scheduling through the same UI/agent action paths."""
import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from amplifier_web.service import AppService, AppError
from test_schedules import Runtime, attach, config, finish
from test_task_continuity import controls


class ConfiguredRuntime(Runtime):
    async def control(self, sid, operation, args, *, guard=None):
        if sid not in self.workers:
            control = self.workers[sid] = controls(sid)
            path = control.state_path().with_name("configuration.json")
            if path.exists(): control.coordinator.config = json.loads(path.read_text())
            original = control.coordinator.get
            control.coordinator.get = lambda name: {"synthetic": SimpleNamespace()} if name == "providers" else original(name)
            await control.restore()
        if guard is not None: guard()
        return await self.workers[sid].perform(operation, args)


async def fixture(tmp_path, monkeypatch, *, managed=False):
    data = tmp_path / 'app'
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(data))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'native'))
    runtime, now = ConfiguredRuntime(), [1800000000.0]
    app = AppService(data, runtime, workspace=tmp_path)
    attach(app, runtime, now)
    await app.dispatch('session.create', {'title': 'Schedule owner', **({'location': {'kind': 'managed'}} if managed else {})})
    sid = app._session()['id']
    directory = data / 'sessions' / sid
    directory.mkdir(exist_ok=True, parents=True)
    plan = {'session': {'context': {'module': 'context-simple'}}, 'providers': [{'module': 'synthetic', 'config': {'api_key': 'synthetic-private-value', 'default_model': 'test-model'}}]}
    (directory / 'effective-configuration.json').write_text(json.dumps(plan))
    control = controls(sid); control.coordinator.config = plan
    control.selection = {'instance': 'synthetic', 'model': 'test-model', 'effort': 'high'}
    control.persist(); await control.close()
    app._session(sid)['selection'] = dict(control.selection)
    return app, runtime, now, sid


async def create(app, sid, now, **extra):
    args = {'sessionId': sid, **config(now, 'task'), 'destination': 'new_task', 'newTaskTitle': 'Scheduled review', 'newTaskMaxTurns': 1, **extra}
    preview = (await app.dispatch('schedule.preview', args))['result']
    created = (await app.dispatch('schedule.create', {**args, 'expectedRevision': 0, 'previewHash': preview['previewHash']}, command_id='create-new-task'))['result']['schedule']
    return created, preview


async def test_new_tasks_are_distinct_user_owned_and_preserve_source_and_draft(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, preview = await create(app, sid, now[0])
        assert record['taskId'] is None  # No source objective required.
        assert 'synthetic-private-value' not in json.dumps(preview)
        await app.dispatch('session.create', {'title': 'Keep selected'})
        selected = app._session()['id']
        await app.dispatch('view.update', {'patch': {'draft': 'Unsent draft'}})
        source = copy.deepcopy(app._session(sid))
        now[0] += 61
        await app.schedules.tick(); await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        target = run['destinationSessionId']
        assert run['phase'] == 'accepted' and run['creationConfirmed'], run['detail']
        assert len(runtime.inputs) == 1 and runtime.inputs[0][0] == target != sid
        assert run['taskId'] == runtime.workers[target].tasks.record()['id']
        assert runtime.workers[target].tasks.record()['maxTurns'] == 1
        assert app._session(target)['title'] == 'Scheduled review'
        assert app._session(target)['selection'] == source['selection']
        assert not app._session(target).get('parentSessionId')
        assert app._session(target)['workspace'] == source['workspace']
        plan = json.loads((app.data_dir / 'sessions' / target / 'configuration.json').read_text())
        assert plan['providers'][0]['config']['default_model'] == 'test-model'
        controls = json.loads((app.data_dir / 'sessions' / target / 'control-state.json').read_text())
        assert controls.get('task', {}).get('id') != 'must-not-copy'
        incoming = (await app.app_bridge('dispatch', {'action':'schedule.list','args':{}}, target))['result']
        assert incoming['items'] == [] and incoming['incomingRuns'][0]['id'] == run['id']
        read = (await app.app_bridge('dispatch', {'action': 'schedule.read', 'args': {'id': record['id']}}, target))['result']
        assert read['readOnly'] and read['runs'][0]['id'] == run['id']
        await finish(app, target, run, values={'count': 4})
        assert app.schedules.store.run(sid, run['id'])['phase'] == 'completed'
        now[0] += 60
        await app.schedules.tick()
        second = app.schedules.store.runs(sid)[0]
        assert second['destinationSessionId'] not in {sid, target}
        assert len(runtime.inputs) == 2
        assert app.state['selectedSessionId'] == selected and app.state['view']['draft'] == 'Unsent draft'
        assert app._session(sid)['messages'] == source['messages'] == []
        assert app._session(sid).get('task') is None
        with pytest.raises(AppError):
            await app.app_bridge('dispatch', {'action': 'schedule.pause', 'args': {'id': record['id'], 'expectedRevision': record['revision']}}, target)
        with pytest.raises(AppError):
            await app.app_bridge('dispatch', {'action': 'schedule.report', 'args': {'runId': second['id'], 'expectedRevision': second['revision'], 'outcome': 'unchanged', 'detail': 'Wrong destination'}}, target)
    finally: await app.close()


async def test_agent_new_task_choice_requires_user_provenance_and_exact_configuration(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        args = {'sessionId': sid, **config(now[0]), 'destination': 'new_task'}
        preview = (await app.app_bridge('dispatch', {'action': 'schedule.preview', 'args': args}, sid))['result']
        request = {**args, 'expectedRevision': 0, 'previewHash': preview['previewHash']}
        with pytest.raises(AppError, match='actual user message'):
            await app.app_bridge('dispatch', {'action': 'schedule.create', 'args': request}, sid)
        source = app._message(app._session(sid), 'user', 'Create a new task every minute to check the report.', inputOrigin='ui')
        request['sourceMessageId'] = source['id']
        result = await app.app_bridge('dispatch', {'action': 'schedule.create', 'id': 'user-new', 'args': request}, sid)
        duplicate = await app.app_bridge('dispatch', {'action': 'schedule.create', 'id': 'user-new', 'args': request}, sid)
        assert duplicate['result']['duplicate']
        record = result['result']['schedule']
        app._session(sid)['selection']['effort'] = 'low'
        with pytest.raises(AppError, match='preview changed'):
            await app.dispatch('schedule.create', request, command_id='stale')
        now[0] += 61; await app.schedules.tick()
        assert not runtime.inputs and len(app.state['sessions']) == 1
        assert 'configuration' in app.schedules.store.get(sid, record['id'])['reviewReason']
    finally: await app.close()


@pytest.mark.parametrize('failure_point', ['before_create', 'after_create', 'after_task', 'after_admission'])
async def test_unknown_new_task_preparation_or_submission_never_replays_after_restart(tmp_path, monkeypatch, failure_point):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    record, _ = await create(app, sid, now[0])
    original = app.dispatch
    async def interrupted(action, *args, **kwargs):
        if kwargs.get('origin') == 'scheduler':
            if action == 'session.create' and failure_point == 'before_create': raise RuntimeError('Connection lost before create')
            result = await original(action, *args, **kwargs)
            if (action == 'session.create' and failure_point == 'after_create') or (action == 'task.create' and failure_point == 'after_task'): raise RuntimeError('Connection lost after commit')
            return result
        return await original(action, *args, **kwargs)
    app.dispatch = interrupted
    if failure_point == 'after_admission': runtime.failure = RuntimeError('Submission receipt lost')
    now[0] += 61; await app.schedules.tick()
    run = app.schedules.store.runs(sid)[0]
    assert run['phase'] == 'unknown' and run['destinationSessionId']
    identities = {row['id'] for row in app.directory.select(limit=-1)}
    assert len(runtime.inputs) == (1 if failure_point == 'after_admission' else 0)
    await app.close()
    runtime2 = ConfiguredRuntime()
    restored = AppService(tmp_path / 'app', runtime2, workspace=tmp_path)
    attach(restored, runtime2, now)
    try:
        now[0] += 1000
        await restored.schedules.tick(); await restored.schedules.tick()
        assert {row['id'] for row in restored.directory.select(limit=-1)} == identities
        assert not runtime2.inputs
        latest = restored.schedules.store.run(sid, run['id'])
        assert latest['destinationSessionId'] == run['destinationSessionId'] and latest['phase'] == 'unknown'
        await restored.dispatch('schedule.reconcile', {'sessionId': sid, 'runId': run['id'], 'expectedRevision': latest['revision'], 'resolution': 'abandoned', 'evidence': 'Inspected the reserved task and original effects; do not repeat.'})
        await restored.schedules.tick()
        assert not runtime2.inputs
        assert restored.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
    finally: await restored.close()


@pytest.mark.parametrize('mutation', ['source_stop', 'destination_stop', 'destination_configuration', 'cancel'])
async def test_changes_winning_admission_leave_one_visible_destination_without_execution(tmp_path, monkeypatch, mutation):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        record, _ = await create(app, sid, now[0])
        def change():
            run = app.schedules.store.runs(sid)[0]
            if mutation == 'source_stop': app._session(sid)['interruptionRevision'] = 1
            if mutation == 'destination_stop': app._session(run['destinationSessionId'])['interruptionRevision'] = 1
            if mutation == 'destination_configuration': app._session(run['destinationSessionId'])['selection']['model'] = 'another'
            if mutation == 'cancel':
                saved = app.schedules.store.get(sid, record['id']); saved.update(status='cancelled', revision=2); app.schedules.store.put(saved)
        runtime.before_admission = change
        now[0] += 61; await app.schedules.tick()
        assert not runtime.inputs
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'skipped' and run['creationConfirmed']
        if mutation == 'destination_configuration':
            comparison = run['destinationConfigurationComparison']
            assert comparison['before']['stage'] == 'destination_created'
            assert comparison['after']['stage'] == 'input_admission'
            assert comparison['before']['configurationHash'] == run['destinationConfigurationHash']
            assert comparison['after']['configurationHash'] != run['destinationConfigurationHash']
            assert comparison['changedCategories'] == ['controls.selection', 'selection']
            assert 'selection' in run['detail']
            assert 'another' not in json.dumps(comparison)
            assert 'synthetic-private-value' not in json.dumps(comparison)
        assert len(app.state['sessions']) == 2
    finally: await app.close()


@pytest.mark.parametrize('mutation', [
    'configuration', 'configuration_without_parts', 'stop', 'stop_and_configuration',
    'execution', 'busy', 'pending_access',
])
async def test_task_creation_guard_retains_only_actual_configuration_comparison(tmp_path, monkeypatch, mutation):
    from amplifier_web.session_creation import template
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    record, _ = await create(app, sid, now[0])
    original = app.dispatch
    compared = {}
    destination_reads = []
    if mutation == 'configuration_without_parts':
        transition = app.schedules.store.transition
        def without_parts(*args, **kwargs):
            if kwargs.get('creationConfirmed'):
                kwargs['destinationConfigurationParts'] = {}
            return transition(*args, **kwargs)
        monkeypatch.setattr(app.schedules.store, 'transition', without_parts)

    async def change(action, args=None, **kwargs):
        if action == 'task.create' and kwargs.get('origin') == 'scheduler':
            target = app._session(args['sessionId'])
            compared['before'] = template(app, target)[1]
            if 'configuration' in mutation:
                target['selection']['model'] = 'private-changed-model'
            if mutation.startswith('stop'):
                target['interruptionRevision'] = 1
            if mutation == 'execution':
                target['executionRevision'] = 1
            if mutation == 'busy':
                target['configurationBusy'] = True
            if mutation == 'pending_access':
                target['fileAccessPending'] = True
            compared['after'] = template(app, target)[1]
            def observed_template(service, row):
                destination_reads.append(row['id'])
                return template(service, row)
            monkeypatch.setattr('amplifier_web.scheduled_destinations.template', observed_template)
        return await original(action, args, **kwargs)

    app.dispatch = change
    try:
        now[0] += 61
        await app.schedules.tick(); await app.schedules.tick()
        run = app.schedules.store.runs(sid)[0]
        assert run['phase'] == 'skipped' and run['creationConfirmed'], run
        assert not run.get('taskId') and not runtime.inputs
        assert app.schedules.store.get(sid, record['id'])['status'] == 'needs_review'
        identities = {row['id'] for row in app.directory.select(limit=-1)}
        assert len(identities) == 2
        if mutation.startswith('configuration'):
            assert destination_reads == [run['destinationSessionId']]
            comparison = run['destinationConfigurationComparison']
            assert comparison['before']['stage'] == 'destination_created'
            assert comparison['after']['stage'] == 'task_creation_admission'
            assert comparison['before']['configurationHash'] == compared['before']['configurationHash']
            assert comparison['after']['configurationHash'] == compared['after']['configurationHash']
            assert comparison['before']['configurationHash'] != comparison['after']['configurationHash']
            assert comparison['changedCategories'] == (
                [] if mutation == 'configuration_without_parts' else ['controls.selection', 'selection'])
            for side in ('before', 'after'):
                assert set(comparison[side]) == {'stage', 'configurationHash', 'configurationParts'}
            encoded = json.dumps(comparison)
            assert all(value not in encoded for value in (
                'private-changed-model', 'synthetic-private-value', str(tmp_path), 'test-model'))
            public = (await app.dispatch('schedule.read', {'sessionId': sid, 'id': record['id']}))['result']['runs'][0]
            assert public['destinationConfigurationComparison'] == comparison
        else:
            assert destination_reads == []
            assert 'destinationConfigurationComparison' not in run
            assert 'destination changed or stopped' in run['detail']
    finally:
        await app.close()

    restored_runtime = ConfiguredRuntime()
    restored = AppService(tmp_path / 'app', restored_runtime, workspace=tmp_path)
    attach(restored, restored_runtime, now)
    try:
        now[0] += 1000
        await restored.schedules.tick(); await restored.schedules.tick()
        assert restored.schedules.store.run(sid, run['id']) == run
        assert {row['id'] for row in restored.directory.select(limit=-1)} == identities
        assert not restored_runtime.inputs
    finally:
        await restored.close()


async def test_creation_command_receipt_and_reserved_id_cannot_overwrite_existing(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        identity = str(uuid.uuid4())
        args = {'id': identity, 'select': False, 'title': 'Fresh'}
        first = await app.dispatch('session.create', args, command_id='fresh')
        second = await app.dispatch('session.create', args, command_id='fresh')
        assert first['result']['sessionId'] == second['result']['sessionId'] == identity
        assert app.state['selectedSessionId'] == sid
        with pytest.raises(AppError, match='already exists'):
            await app.dispatch('session.create', args, command_id='different')
    finally: await app.close()


async def test_source_task_pause_is_independent_and_mode_changes_require_review(tmp_path, monkeypatch):
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch)
    try:
        await app.dispatch('task.create', {'sessionId':sid,'expectedRevision':0,'objective':'Original independent objective'}, command_id='source-task-create')
        record, _ = await create(app,sid,now[0])
        await app.dispatch('task.pause', {'sessionId':sid,'expectedRevision':1}, command_id='source-task-pause')
        now[0]+=61; await app.schedules.tick()
        run=app.schedules.store.runs(sid)[0]
        assert run['phase']=='accepted',run['detail']
        assert runtime.workers[sid].tasks.record()['status']=='paused'
        await finish(app,run['destinationSessionId'],run)
        await app.dispatch('task.resume',{'sessionId':sid,'expectedRevision':2}, command_id='source-task-resume')
        fields={'sessionId':sid,**config(now[0]),'destination':'same_task'}
        preview=(await app.dispatch('schedule.preview',fields))['result']
        updated=(await app.dispatch('schedule.update',{**fields,'id':record['id'],'expectedRevision':record['revision'],'previewHash':preview['previewHash']}))['result']['schedule']
        assert updated['destination']=='same_task' and 'newTaskConfiguration' not in updated
        now[0]+=61;await app.schedules.tick()
        assert runtime.inputs[-1][0]==sid
    finally:await app.close()


async def test_crash_with_only_reserved_creation_identity_is_not_replayed(tmp_path, monkeypatch):
    app, runtime, now, sid=await fixture(tmp_path,monkeypatch)
    record,_=await create(app,sid,now[0])
    now[0]+=61
    assert app.schedules.store.acquire(now[0])
    run=app.schedules.store.claim(sid,record['id'],now[0])
    destination=str(uuid.uuid4())
    app.schedules.store.transition(sid,run['id'],['claimed'],'creating',now[0],destinationSessionId=destination)
    await app.close()
    second=ConfiguredRuntime();restored=AppService(tmp_path/'app',second,workspace=tmp_path);attach(restored,second,now)
    try:
        now[0]+=31;await restored.schedules.tick()
        recovered=restored.schedules.store.run(sid,run['id'])
        assert recovered['phase']=='unknown' and recovered['destinationSessionId']==destination
        assert len(restored.state['sessions'])==1 and not second.inputs
    finally:await restored.close()


async def test_explicit_identity_cannot_replace_unloaded_native_history(tmp_path, monkeypatch):
    app,runtime,now,sid=await fixture(tmp_path,monkeypatch)
    try:
        identity=str(uuid.uuid4())
        native=tmp_path/'native'/'projects'/'unloaded-workspace'/'sessions'/identity
        native.mkdir(parents=True)
        transcript=native/'transcript.jsonl';transcript.write_text('preserve original history')
        with pytest.raises(AppError,match='already exists'):
            await app.dispatch('session.create',{'id':identity,'select':False})
        assert transcript.read_text()=='preserve original history'
        assert len(app.state['sessions'])==1
    finally:await app.close()


async def test_managed_new_tasks_allocate_distinct_folders_for_each_occurrence(tmp_path, monkeypatch):
    from pathlib import Path
    from amplifier_web.managed_chats import metadata
    app, runtime, now, sid = await fixture(tmp_path, monkeypatch, managed=True)
    try:
        source = app._session(sid)
        source_folder = Path(source['workspace'])
        (source_folder / 'result.txt').write_text('Original result')
        app._message(source, 'user', 'Original context must not be copied')
        messages = copy.deepcopy(source['messages'])
        registrations = copy.deepcopy(app.state['workspaces'])
        record, preview = await create(app, sid, now[0])
        assert record['newTaskConfiguration']['location'] == {'kind': 'managed'}
        assert len(list((app.data_dir / 'chats').iterdir())) == 1
        folders = {source_folder}
        for _ in range(2):
            now[0] += 61
            await app.schedules.tick(); await app.schedules.tick()
            run = app.schedules.store.runs(sid)[0]
            assert run['phase'] == 'accepted' and run['creationConfirmed'], run['detail']
            target = app._session(run['destinationSessionId'])
            folder = Path(target['workspace'])
            assert target['location'] == {'kind': 'managed'}
            assert folder == app.data_dir / 'chats' / target['id'] / 'files'
            assert folder not in folders and metadata(folder)['id'] == target['id']
            assert not (folder / 'result.txt').exists()
            (folder / 'result.txt').write_text('Independent scheduled result')
            assert (source_folder / 'result.txt').read_text() == 'Original result'
            assert target['selection'] == source['selection']
            assert len(target['messages']) == 1 and target['messages'][0]['text'] == record['prompt']
            folders.add(folder)
            await finish(app, target['id'], run)
        assert len(folders) == len(list((app.data_dir / 'chats').iterdir())) == 3
        assert app.state['workspaces'] == registrations
        assert app.state['selectedSessionId'] == sid
        assert source['messages'] == messages
        assert len(runtime.inputs) == 2
    finally:
        await app.close()


async def test_managed_inheritance_rejects_reusing_source_folder_before_allocation(tmp_path, monkeypatch):
    from amplifier_web.session_creation import template
    app, runtime, _, sid = await fixture(tmp_path, monkeypatch, managed=True)
    try:
        source = app._session(sid)
        _, reviewed = template(app, source)
        identity = str(uuid.uuid4())
        with pytest.raises(AppError, match='reviewed location'):
            await app.dispatch('session.create', {
                'id': identity, 'select': False, 'workspace': source['workspace'], 'bundle': source['bundle'],
                'inheritConfiguration': {'sessionId': sid, 'configurationHash': reviewed['configurationHash']},
            })
        assert len(app.state['sessions']) == len(list((app.data_dir / 'chats').iterdir())) == 1
        assert not (app.data_dir / 'sessions' / identity).exists()
        assert not runtime.inputs
    finally:
        await app.close()


async def test_new_scheduled_root_does_not_copy_administrative_grants(tmp_path,monkeypatch):
    app,runtime,now,sid=await fixture(tmp_path,monkeypatch)
    try:
        await app.dispatch('capability.grant',{'sessionId':sid,'effects':['file_access'],'scopes':['global'],'reason':'Source-only setup'})
        await create(app,sid,now[0])
        now[0]+=61
        await app.schedules.tick()
        run=app.schedules.store.runs(sid)[0]
        target=app._session(run['destinationSessionId'])
        assert run['phase']=='accepted'
        assert app.configuration_authority.rows(target['id'])==[]
        target['collaborationGeneration']={'id':'scheduled-generation','inputIds':[run['inputId']],'terminal':False}
        from test_coordination_generation import agent
        with pytest.raises(AppError,match='Configuration authority is required'):
            await agent(app,target,'permissions.save',{'scope':'global','allowed':[],'denied':[]},'scheduled-admin')
    finally:
        await app.close()
