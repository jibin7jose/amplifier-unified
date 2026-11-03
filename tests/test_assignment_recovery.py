"""Explicit human recovery of stopped assignments never replays old work."""
import copy
import pytest
from amplifier_web.service import AppError, AppService
from amplifier_web.capacity import admission
from test_assignments import app, start, child, call, send, generation, until, agent_action, limits


async def stop(app, source):
    value = app.assignments.current(source)
    result = await app.dispatch('assignment.stop', {'sessionId': source['id'], 'expectedRevision': value['revision']}, command_id='stop-recovery')
    await until(lambda: not app.assignments.interrupting)
    # The lightweight fixture emits only status on Stop. Supply the confirmed
    # process-exit event that the actual runtime emits, without inventing success.
    for sid, status in app.collaboration.receipt('stop-recovery')['interruptions'].items():
        if status == 'returned':
            await app.on_runtime_event('runtime.ended', {'sessionId': sid, 'rootSessionId': sid})
    return app.assignments.current(source)


async def resume(app, source, identity='resume-recovery', **extra):
    return await app.dispatch('assignment.resume', {'sessionId': source['id'], 'expectedRevision': app.assignments.current(source)['revision'], **extra}, command_id=identity)


async def test_resume_preserves_topology_limits_accounting_and_does_not_run(app):
    source, independent = app.state['sessions']
    target, created = await child(app, source)
    await limits(app, source, maxTotalTokens=100, reserveTokens=20)
    row = call(source, 'recorded', endedAt=10, usage={'totalTokens': 30})
    row['phase'] = 'completed'
    assert (await admission(app, source['id'], {'call': row}))['allowed']
    value = await stop(app, source)
    before = copy.deepcopy(value)
    usage = copy.deepcopy(app.assignments.usage(value))
    revisions = {s['id']: s.get('interruptionRevision') for s in (source, target)}
    inputs = list(app.runtime.inputs)
    result = await resume(app, source)
    current = result['result']['assignment']
    assert current['id'] == before['id'] and current['status'] == 'active'
    for key in ('members', 'limits', 'tasks', 'integration', 'createdAt', 'stoppedAt'):
        assert current.get(key) == before.get(key)
    assert app.assignments.usage(current) == usage
    assert {s['id']: s.get('interruptionRevision') for s in (source, target)} == revisions
    assert app.runtime.inputs == inputs
    assert app.assignments.current(independent) is None
    assert app.collaboration.creation_guard(app.collaboration.receipt(created['requestId']))
    # Reopen does not forgive the retained cumulative budget.
    assert (await admission(app, source['id'], {'call': call(source, 'new')}))['allowed']
    await app.dispatch('assignment.set', {'sessionId':source['id'], 'expectedRevision':current['revision'], 'maxTotalTokens':30})
    assert not (await admission(app, target['id'], {'call': call(target, 'over-budget')}))['allowed']


async def test_stopped_send_rejected_before_history_or_worker_change(app):
    source = app.state['sessions'][0]
    await start(app); await stop(app, source)
    before = copy.deepcopy(source); draft = copy.deepcopy(app.state['view'])
    with pytest.raises(AppError, match='Resume assignment') as error:
        await app.dispatch('conversation.send', {'sessionId': source['id'], 'text': 'Continue'}, command_id='blocked-send')
    assert error.value.status == 409 and error.value.code == 'assignment_stopped'
    assert source == before and app.state['view'] == draft and not app.runtime.inputs
    await resume(app, source)
    await app.dispatch('conversation.send', {'sessionId': source['id'], 'text': 'Continue'}, command_id='new-send')
    await until(lambda: len(app.runtime.inputs) == 1)
    assert app.runtime.inputs[0][1] == 'new-send'


async def test_human_owner_revision_and_duplicate_receipt_survive_restart(app):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await stop(app, source)
    value = copy.deepcopy(app.assignments.current(source)); args={'sessionId': source['id'], 'expectedRevision': value['revision']}
    with pytest.raises(AppError):
        await agent_action(app, source, 'assignment.resume', args, 'agent-resume')
    with pytest.raises(AppError, match='commissioning owner'):
        await app.dispatch('assignment.resume', {**args, 'sessionId':target['id']})
    with pytest.raises(AppError, match='revision changed'):
        await app.dispatch('assignment.resume', {**args, 'expectedRevision':value['revision']-1})
    assert app.assignments.current(source) == value
    first=await app.dispatch('assignment.resume', args, command_id='resume-once')
    await app.close()
    other=AppService(app.data_dir, workspace=source['workspace'])
    try:
        again=await other.dispatch('assignment.resume', args, command_id='resume-once')
        assert again['duplicate'] and again['result']==first['result']
        assert other.assignments.current(other._session(source['id']))['revision']==value['revision']+1
    finally:
        await other.close()


@pytest.mark.parametrize('block', ['pending_stop','unknown_stop','working','native_worker','preparing','generation'])
async def test_resume_refuses_unsettled_stop_or_member(app, block):
    source=app.state['sessions'][0]
    await start(app); await stop(app, source)
    value=copy.deepcopy(app.assignments.current(source))
    if block in {'pending_stop','unknown_stop'}:
        receipt=app.collaboration.receipt('stop-recovery')
        receipt['interruptions'][source['id']]='requested' if block=='pending_stop' else 'unknown'
        receipt['interruptionsPending']=int(block=='pending_stop')
        app.collaboration.save('stop-recovery',receipt); app.db.commit()
    elif block=='working':source['status']='working'
    elif block=='native_worker':source['workers'].append({'id':'native','status':'running'})
    elif block=='generation':
        epoch = source['interruptionRevision']  # Captured for this intentionally fresh admission.
        await generation(app,source,'unsettled-generation',[], interruption_revision=epoch)
        assert source['collaborationGeneration'] == {
            'id': 'unsettled-generation', 'inputIds': [], 'terminal': False, 'interruptionRevision': epoch}
    else:app.runtime._preparations={source['id']:object()}
    with pytest.raises(AppError,match='settle'):
        await resume(app,source)
    assert app.assignments.current(source)==value
    assert not app.runtime.inputs


@pytest.mark.parametrize('missing', [True, False])
async def test_generation_helper_does_not_refresh_a_missing_or_pre_stop_epoch(app, missing):
    source = app.state['sessions'][0]
    captured_epoch = source.get('interruptionRevision', 0)
    await app.dispatch('conversation.stop', {'sessionId': source['id']})
    assert source['interruptionRevision'] > captured_epoch
    stopped = copy.deepcopy(source['collaborationGeneration'])
    await generation(app, source, 'delayed-pre-stop-generation', [],
                     **({} if missing else {'interruption_revision': captured_epoch}))
    assert source['collaborationGeneration'] == stopped


async def test_queued_outbound_to_independent_chat_stays_suppressed_after_resume(app):
    source, peer=app.state['sessions']
    await start(app);peer['status']='working'
    request=await send(app,identity='old-peer-request')
    assert request['delivery']=='queued'
    await stop(app,source);await resume(app,source)
    assert 'interrupted' in app.collaboration.guard(app.collaboration.receipt('old-peer-request'))
    peer['status']='idle'
    await app.collaboration.drain(peer['id'])
    assert app.collaboration.receipt('old-peer-request')['delivery']=='suppressed'
    assert not app.runtime.inputs
    # A fresh explicit request has current lineage and can be admitted.
    epoch = source['interruptionRevision']
    await generation(app,source,'fresh-generation',['new-human-input'], interruption_revision=epoch)
    assert source['collaborationGeneration'] == {
        'id': 'fresh-generation', 'inputIds': ['new-human-input'], 'terminal': False, 'interruptionRevision': epoch}
    await send(app,identity='fresh-peer-request')
    await until(lambda:len(app.runtime.inputs)==1)
    assert app.runtime.inputs[0][1]=='fresh-peer-request'


async def test_late_saved_wait_does_not_manufacture_continuation_after_resume(app):
    source,peer=app.state['sessions']
    await start(app)
    request=await send(app,identity='old-wait')
    await until(lambda:len(app.runtime.inputs)==1)
    await agent_action(app,source,'coordination.subscribe',{'sessionId':source['id'],'requestId':'old-wait'},'subscribe-old-wait')
    await stop(app,source);await resume(app,source)
    request=app.collaboration.receipt('old-wait')
    # A late external outcome is durable evidence, not renewed input authority.
    request['terminalOutcome']={'status':'observed','outcome':'failed','text':'Late evidence','qualified':False}
    messages=copy.deepcopy(source['messages']); inputs=list(app.runtime.inputs)
    app.collaboration.enqueue_continuation(request)
    assert source['messages']==messages and app.runtime.inputs==inputs
    assert app.collaboration.receipt('old-wait')['subscription']['status']=='suppressed'


async def test_resume_rollback_does_not_clear_stop(app):
    source=app.state['sessions'][0]
    await start(app);await stop(app,source)
    value=copy.deepcopy(app.assignments.current(source))
    app.db.execute("CREATE TEMP TRIGGER reject_resume BEFORE INSERT ON commands WHEN NEW.id='resume-recovery' BEGIN SELECT RAISE(FAIL,'disk full'); END")
    with pytest.raises(Exception,match='disk full'):
        await resume(app,source)
    assert app.assignments.current(source)==value
    assert not app.runtime.inputs


async def test_duplicate_resume_cannot_reopen_a_later_stop(app):
    source = app.state['sessions'][0]
    await start(app); await stop(app, source)
    args = {'sessionId': source['id'], 'expectedRevision': app.assignments.current(source)['revision']}
    first = await app.dispatch('assignment.resume', args, command_id='resume-once')
    await app.dispatch('assignment.stop', {'sessionId':source['id'], 'expectedRevision':app.assignments.current(source)['revision']}, command_id='later-stop')
    again = await app.dispatch('assignment.resume', args, command_id='resume-once')
    assert again['duplicate'] and again['result'] == first['result']
    assert app.assignments.current(source)['status'] == 'stopped'
    assert not app.runtime.inputs


async def test_resume_preserves_separate_budget_pause_until_explicit_budget_edit(app, monkeypatch):
    from test_task_continuity import controls
    from amplifier_web.capacity import saved_policy
    from amplifier_web.execution import ingest
    monkeypatch.setenv('AMPLIFIER_WEB_HOME', str(app.data_dir))
    source = app.state['sessions'][0]
    worker = controls(source['id'])
    worker.capacity.admit = lambda row: admission(app, source['id'], {'call':row})
    try:
        await worker.perform('capacity.set', {'commandId':'local-budget', 'expectedRevision':0, 'enabled':True, 'maxTotalTokens':100})
        await start(app)
        row = call(source,'retained-usage', endedAt=1, usage={'totalTokens':30})
        row['phase'] = 'completed'
        assert (await admission(app,source['id'],{'call':row}))['allowed']
        await stop(app,source)
        with pytest.raises(ValueError,match='assignment was stopped'):
            await worker.capacity.guard(call(source,'stopped-call'))
        before = saved_policy(app,source['id'])
        await resume(app,source)
        assert saved_policy(app,source['id']) == before
        assert not (await admission(app,source['id'],{'call':call(source,'still-paused')}))['allowed']
        # Existing control owner and revision-bound edit clear only its denial.
        await worker.perform('capacity.set', {'commandId':'reviewed-local-budget', 'expectedRevision':1, 'maxTotalTokens':100})
        assert saved_policy(app,source['id'])['maxTotalTokens'] == 100
        row=call(source,'new-call')
        assert (await admission(app,source['id'],{'call':row}))['allowed']
        ingest(source,{**row,'phase':'completed','endedAt':2,'revision':2,'usage':{'totalTokens':70}})
        assert app.assignments.usage(app.assignments.current(source))['metrics']['grossTotalTokens']['value'] == 100
        result=await admission(app,source['id'],{'call':call(source,'over-local-limit')})
        assert not result['allowed'] and any('limit reached' in reason for reason in result['reasons'])
    finally:
        await worker.close()


async def test_later_confirmed_stop_recovers_unknown_receipt_without_rewriting_it(app):
    source = app.state['sessions'][0]
    target, _ = await child(app, source)
    await stop(app, source)
    old = app.collaboration.receipt('stop-recovery')
    old['interruptions'][target['id']] = 'unknown'
    app.collaboration.save('stop-recovery', old); app.db.commit()
    await app.dispatch('assignment.stop', {
        'sessionId': source['id'],
        'expectedRevision': app.assignments.current(source)['revision'],
    }, command_id='confirmed-stop')
    await until(lambda: not app.assignments.interrupting)
    for sid in (source['id'], target['id']):
        await app.on_runtime_event('runtime.ended', {'sessionId': sid})
    before = copy.deepcopy(app.assignments.current(source))
    inputs = list(app.runtime.inputs)
    from amplifier_web.runtime import RuntimeManager
    app.runtime.stopped_boundary = RuntimeManager().stopped_boundary
    result = await resume(app, source)
    assert result['result']['stopRecovery']['assignmentRevision'] == before['revision']
    assert result['result']['stopRecovery']['members'][0]['stopRequestIds'] == ['stop-recovery']
    assert result['result']['assignment']['id'] == before['id']
    assert result['result']['assignment']['members'] == before['members']
    assert app.collaboration.receipt('stop-recovery') == old
    assert app.runtime.inputs == inputs
