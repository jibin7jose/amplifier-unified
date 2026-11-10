import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace

from aiohttp import web
import pytest

from amplifier_web import __version__, update_readiness
from amplifier_web.auth import data_identity
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager
from test_service import Runtime


REVISION='a'*40
ATTEMPT='b'*32
INSTANCE='c'*32
TARGET={'version':__version__,'revision':REVISION,'attemptId':ATTEMPT,
        'sourceInstanceId':'d'*32,'requestedAt':1.0}
IDENTITY={'version':__version__,'revision':REVISION,'instanceId':INSTANCE}


def make_manager(tmp_path,monkeypatch,*,state=None,identity=None):
    monkeypatch.setattr(update_readiness,'running_identity',lambda:dict(identity or IDENTITY))
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    service.state['updates']=deepcopy(state if state is not None else {'phase':'activating','pendingRestart':TARGET})
    manager=UpdateManager(service)
    service.update_manager=manager
    return service,manager


def health(manager,**changes):
    return {'ok':True,'app':'amplifier-unified','dataIdentity':data_identity(manager.home),
            **manager.running_identity,**changes}


def fake_distribution(monkeypatch,*,located=None,payload=None):
    location=located or Path(update_readiness.__file__).parent
    distribution=SimpleNamespace(locate_file=lambda _:location,
        read_text=lambda _:json.dumps(payload if payload is not None else {'vcs_info':{'commit_id':REVISION}}))
    monkeypatch.setattr(update_readiness.metadata,'distribution',lambda _:distribution)
    return distribution


def test_running_identity_attests_only_own_installed_revision(monkeypatch):
    fake_distribution(monkeypatch)
    first=update_readiness.running_identity()
    second=update_readiness.running_identity()
    assert first['version']==__version__ and first['revision']==REVISION
    assert len(first['instanceId'])==32 and first['instanceId']!=second['instanceId']


@pytest.mark.parametrize('payload',[
    {},{'vcs_info':{}},{'vcs_info':{'commit_id':'bad'}},{'vcs_info':{'commit_id':'A'*40}},
    {'vcs_info':{'commit_id':None}},{'vcs_info':{'commit_id':'a'*39}},
])
def test_missing_or_invalid_installed_revision_is_not_attested(monkeypatch,payload):
    fake_distribution(monkeypatch,payload=payload)
    assert update_readiness.running_identity()['revision'] is None


def test_checkout_does_not_attest_unrelated_installed_distribution(tmp_path,monkeypatch):
    fake_distribution(monkeypatch,located=tmp_path/'other-install/amplifier_web')
    assert update_readiness.running_identity()['revision'] is None


async def test_running_identity_remains_captured_when_installation_is_replaced(tmp_path,monkeypatch):
    distribution=fake_distribution(monkeypatch)
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    try:
        old=deepcopy(manager.running_identity)
        distribution.read_text=lambda _:json.dumps({'vcs_info':{'commit_id':'e'*40}})
        assert update_readiness.running_identity()['revision']=='e'*40
        assert manager.running_identity==old
        assert old['revision']==REVISION
    finally:
        await service.close()


async def test_constructor_waits_for_real_listener_readiness(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert service.state['updates']['phase']=='activating'
        assert service.state['updates']['pendingRestart']==TARGET
        assert not manager.diagnostics.state['events']
        with pytest.raises(Exception,match='update is activating'):
            await service.dispatch('conversation.send',{'text':'Must wait for readiness'})
        assert await manager.confirm_readiness(health(manager))
        state=service.state['updates']
        assert state['phase']=='installed' and state['pendingRestart'] is None
        receipt=state['diagnostics']['latest']
        assert receipt['phase']=='restart-ack' and receipt['attemptId']==ATTEMPT
        assert receipt['observedRevision']==REVISION
        assert not await manager.confirm_readiness(health(manager))
        assert sum(event['phase']=='restart-ack' for event in manager.diagnostics.state['events'])==1
    finally:
        await service.close()


@pytest.mark.parametrize('scope', ['app', 'whole'])
async def test_exact_restart_intent_controls_component_continuation(tmp_path, monkeypatch, scope):
    intent = {'id': 'explicit-request', 'scope': scope}
    target = {**TARGET, 'updateIntent': intent}
    service, manager = make_manager(tmp_path, monkeypatch, state={
        'phase': 'activating', 'pendingRestart': target,
        'applicationIntent': intent, 'sequence': {'stage': 'application', 'install': True, 'nextStage': 'other'}})
    try:
        assert await manager.confirm_readiness(health(manager))
        sequence = service.state['updates']['sequence']
        if scope == 'app':
            assert sequence['stage'] == 'complete' and sequence['install'] is False
            assert 'nextStage' not in sequence
            assert service.state['updates']['applicationIntent'] == intent
        else:
            assert sequence['stage'] == 'included' and sequence['nextStage'] == 'included'
            assert sequence['install'] is True
        assert not await manager.confirm_readiness(health(manager))
    finally:
        await service.close()


@pytest.mark.parametrize('outcome', ['pass','failed','changed-target'])
async def test_successor_of_old_updater_proves_browser_recovery_before_clearing_attention(tmp_path,monkeypatch,outcome):
    from amplifier_web import app_updates, app_replacement
    from test_ui_use_packaging import QUALIFIED_BROWSER
    service,manager=make_manager(tmp_path,monkeypatch)
    diagnostics=manager.diagnostics
    try:
        diagnostics.begin('application', 'e'*40, application_extras=['managed-browser'])
        failure=diagnostics.record('managed-browser-install','failed',errorType='CommandFailure')
        diagnostics.begin('application', REVISION, ATTEMPT, application_extras=['managed-browser'])
        # This is the persisted shape produced by 0.47.1, before this repair.
        diagnostics.state['batch'].pop('browserFailures')
        diagnostics.state['batch'].pop('verifiedBrowserFailures')
        service.state['updates']['pendingRestart']={**TARGET,'qualification':{'extras':['managed-browser']}}
        monkeypatch.setattr(app_replacement,'matches_running',lambda *_:True)
        calls=[]
        async def process(*args,**kwargs):
            calls.append(args)
            if outcome=='failed':raise RuntimeError('Synthetic browser launch failure')
            if outcome=='changed-target':service.state['updates']['pendingRestart']['attemptId']='f'*32
            return json.dumps(QUALIFIED_BROWSER)
        monkeypatch.setattr(app_updates,'process',process)
        assert await manager.confirm_readiness(health(manager)) is (outcome!='changed-target')
        assert len(calls)==1 and 'qualify_managed_browser' in calls[0][-1]
        failures=list(diagnostics.state['activeFailures'].values())
        if outcome=='pass':assert failures==[]
        else:assert any(group['latest']['id']==failure['id'] for group in failures)
        assert failure['id'] in diagnostics.path.read_text(), 'Recovery retains historical failure'
    finally:
        await service.close()


@pytest.mark.parametrize('healthy', [False, True])
async def test_first_updated_process_settles_old_storage_failure_only_after_verified_ack(tmp_path,monkeypatch,healthy):
    from amplifier_web import app_replacement
    service,manager=make_manager(tmp_path,monkeypatch)
    diagnostics=manager.diagnostics
    try:
        diagnostics.begin('application','e'*40,application_extras=[])
        failure=diagnostics.record('candidate-install','failed',errorType='OSError',reason='disk-full')
        diagnostics.begin('application',REVISION,ATTEMPT,application_extras=[])
        diagnostics.record('stage','started')
        diagnostics.record('stage','succeeded')
        diagnostics.begin('application',REVISION,ATTEMPT,application_extras=[])
        diagnostics.state['batch'].pop('storageFailures')
        diagnostics.state['batch'].pop('verifiedStorageFailures')
        service.state['updates']['pendingRestart']={**TARGET,'qualification':{'extras':[]}}
        verified=[]
        def matches(*_):
            verified.append(True)
            return healthy
        monkeypatch.setattr(app_replacement,'matches_running',matches)
        assert await manager.confirm_readiness(health(manager)) is healthy
        assert verified==[True]
        assert bool(diagnostics.state['activeFailures']) is not healthy
        assert failure['id'] in diagnostics.path.read_text()
    finally:
        await service.close()


async def test_application_result_survives_component_failure_and_service_reload(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert not await manager.confirm_readiness(health(manager,revision='e'*40))
        assert 'applicationResult' not in service.state['updates']
        assert await manager.confirm_readiness(health(manager))
        result=deepcopy(service.state['updates']['applicationResult'])
        assert result['attemptId']==ATTEMPT and result['phase']=='restart-ack'
        assert result['version']==__version__ and result['revision']==REVISION
        assert result['verifiedAt']>0
        # This result is not a diagnostic ring entry: later component batches
        # must not erase it or turn it into a claim about the failed component.
        for index in range(9):
            manager.diagnostics.begin('ecosystem',format(index,'032x'),tier='included')
            manager.diagnostics.record('ecosystem-activation','succeeded')
        manager.diagnostics.begin('ecosystem','f'*32,tier='other',components=['foundation'])
        manager.diagnostics.record('ecosystem-validation','failed',reason='runtime-source-changed')
        await manager.publish(phase='error',error='Component sources changed.',sequence={'stage':'other'})
        assert service.state['updates']['applicationResult']==result
    finally:
        await service.close()
    restored=AppService(tmp_path,Runtime(),workspace=tmp_path)
    try:
        restored.update_manager=UpdateManager(restored)
        assert restored.state['updates']['applicationResult']==result
        assert restored.state['updates']['phase']=='error'
        assert restored.state['updates']['diagnostics']['lastFailure']['reason']=='runtime-source-changed'
    finally:
        await restored.close()


async def test_legacy_application_state_preserves_restart_readiness(tmp_path,monkeypatch):
    from amplifier_web import app_updates
    monkeypatch.setattr(app_updates,'application_state',lambda:{'current':__version__,'status':'current'})
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert service.state['updates']['application']['canInstall'] is True
        assert service.state['updates']['pendingRestart']==TARGET
        assert service.state['updates']['phase']=='activating'
        assert await manager.confirm_readiness(health(manager))
        assert service.state['updates']['pendingRestart'] is None
    finally:
        await service.close()


@pytest.mark.parametrize('changes',[
    {'ok':False},{'ok':1},{'app':'another-app'},{'version':'0.0.0'},{'revision':'e'*40},
    {'revision':None},{'instanceId':'e'*32},{'dataIdentity':'another-data-directory'},
])
async def test_incorrect_health_never_acknowledges(tmp_path,monkeypatch,changes):
    service,manager=make_manager(tmp_path,monkeypatch)
    try:
        assert not await manager.confirm_readiness(health(manager,**changes))
        assert service.state['updates']['pendingRestart']==TARGET
        assert service.state['updates']['phase']=='activating'
        assert not manager.diagnostics.state['events']
    finally:
        await service.close()


@pytest.mark.parametrize('identity',[
    {**IDENTITY,'version':'0.0.0'},{**IDENTITY,'revision':'e'*40},
    {**IDENTITY,'revision':None},{**IDENTITY,'instanceId':TARGET['sourceInstanceId']},
])
async def test_wrong_install_or_outgoing_instance_cannot_self_acknowledge(tmp_path,monkeypatch,identity):
    service,manager=make_manager(tmp_path,monkeypatch,identity=identity)
    try:
        assert not await manager.confirm_readiness(health(manager))
        assert service.state['updates']['pendingRestart']==TARGET
        assert not manager.diagnostics.state['events']
    finally:
        await service.close()


async def test_probe_from_superseded_attempt_cannot_acknowledge_current_marker(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    newer={**TARGET,'attemptId':'e'*32}
    service.state['updates']['pendingRestart']=newer
    try:
        assert not await manager.confirm_readiness(health(manager),expected=TARGET)
        assert service.state['updates']['pendingRestart']==newer
        assert not manager.diagnostics.state['events']
        assert await manager.confirm_readiness(health(manager),expected=newer)
        assert manager.diagnostics.state['latest']['attemptId']==newer['attemptId']
    finally:
        await service.close()


def legacy_state():
    failure={'kind':'application','phase':'service-restart','status':'failed',
             'attemptId':ATTEMPT,'revision':REVISION,'exitCode':-15}
    probe={'kind':'application','phase':'replacement-probe','status':'succeeded',
           'attemptId':ATTEMPT,'revision':REVISION,'probe':{'ok':True,'version':__version__}}
    return {'phase':'error','error':update_readiness.LEGACY_RESTART_ERROR,'pendingRestart':None,
            'diagnostics':{'kind':'application','attemptId':ATTEMPT,'revision':REVISION,
                           'lastFailure':failure,'events':[probe,failure]}}


def validated_record(manager,value=None):
    path=manager.directory/'applications'/REVISION/'validated.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value or {'version':__version__,'revision':REVISION,'attemptId':ATTEMPT}))
    return path


async def test_legacy_erased_marker_reconciles_current_health_without_fake_ack(tmp_path,monkeypatch):
    original=legacy_state()
    service,manager=make_manager(tmp_path,monkeypatch,state=original)
    validated_record(manager)
    try:
        assert service.state['updates']['phase']=='error'
        candidate=update_readiness.recovery_candidate(manager)
        assert candidate['legacy'] is True and candidate['attemptId']==ATTEMPT
        assert await manager.confirm_readiness(health(manager))
        state=service.state['updates']
        assert state['phase']=='installed' and state['error'] is None
        assert state['diagnostics']['lastFailure']==original['diagnostics']['lastFailure']
        assert state['diagnostics']['events'][:2]==original['diagnostics']['events']
        assert state['diagnostics']['latest']['phase']=='restart-reconcile'
        assert not any(row['phase']=='restart-ack' for row in state['diagnostics']['events'])
        assert state['reconciliation']['attemptId']==ATTEMPT
        assert state['reconciliation']['verifiedAt']>0
        assert 'no additional restart' in state['detail']
    finally:
        await service.close()


@pytest.mark.parametrize('broken',[
    'different-error','different-phase','different-operation','different-kind',
    'failure-attempt','failure-revision','probe-attempt','probe-revision','probe-version',
    'probe-failed','probe-not-ok','probe-missing','validated-missing','validated-invalid',
    'validated-attempt','validated-revision','validated-version',
])
async def test_legacy_recovery_requires_all_correlated_evidence(tmp_path,monkeypatch,broken):
    state=legacy_state()
    diagnostic=state['diagnostics'];failure=diagnostic['lastFailure'];probe=diagnostic['events'][0]
    validation={'version':__version__,'revision':REVISION,'attemptId':ATTEMPT}
    if broken=='different-error':state['error']='Other failure'
    elif broken=='different-phase':state['phase']='idle'
    elif broken=='different-operation':failure['phase']='replacement-install'
    elif broken=='different-kind':failure['kind']='ecosystem'
    elif broken=='failure-attempt':failure['attemptId']='e'*32
    elif broken=='failure-revision':failure['revision']='e'*40
    elif broken=='probe-attempt':probe['attemptId']='e'*32
    elif broken=='probe-revision':probe['revision']='e'*40
    elif broken=='probe-version':probe['probe']['version']='0.0.0'
    elif broken=='probe-failed':probe['status']='failed'
    elif broken=='probe-not-ok':probe['probe']['ok']=False
    elif broken=='probe-missing':diagnostic['events']=[failure]
    elif broken=='validated-attempt':validation['attemptId']='e'*32
    elif broken=='validated-revision':validation['revision']='e'*40
    elif broken=='validated-version':validation['version']='0.0.0'
    service,manager=make_manager(tmp_path,monkeypatch,state=state)
    if broken!='validated-missing':
        path=validated_record(manager,validation)
        if broken=='validated-invalid':path.write_text('{')
    try:
        assert update_readiness.recovery_candidate(manager) is None
        assert not await manager.confirm_readiness(health(manager))
        assert service.state['updates']['error']==state['error']
        assert service.state['updates']['diagnostics']['lastFailure']==state['diagnostics']['lastFailure']
        assert not any(row['phase'] in {'restart-ack','restart-reconcile'} for row in manager.diagnostics.state['events'])
    finally:
        await service.close()


async def listener(manager,handler):
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    sock.setblocking(False)
    manager.service.port=sock.getsockname()[1]
    manager.service.server_config={'bind':['127.0.0.1'],'tls':{'method':'none'}}
    app=web.Application()
    app.router.add_get('/api/health',handler)
    runner=web.AppRunner(app)
    await runner.setup()
    return runner,web.SockSite(runner,sock),sock


async def test_waiter_requires_listener_then_authenticated_health(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    requests=[]
    async def handle(request):
        requests.append(dict(request.headers))
        if request.headers.get('Authorization')!='Bearer fixture-control-token':
            return web.json_response({},status=401)
        return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-control-token',timeout=1,interval=.01))
    try:
        await asyncio.sleep(.04)
        assert service.state['updates']['phase']=='activating' and not requests
        assert not task.done()
        await site.start()
        await asyncio.wait_for(task,2)
        assert service.state['updates']['phase']=='installed'
        assert requests[0]['Authorization']=='Bearer fixture-control-token'
        assert requests[0]['Host']==f'localhost:{service.port}'
        assert manager.diagnostics.state['latest']['phase']=='restart-ack'
        assert 'fixture-control-token' not in json.dumps(manager.diagnostics.state)
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('response_kind',['unauthorized','wrong-revision','redirect'])
async def test_listener_without_verified_health_times_out_and_keeps_gate(tmp_path,monkeypatch,response_kind):
    service,manager=make_manager(tmp_path,monkeypatch)
    redirected=[]
    async def handle(request):
        if request.query.get('redirected'):
            redirected.append(request)
            return web.json_response(health(manager))
        if response_kind=='unauthorized':return web.json_response({},status=401)
        if response_kind=='redirect':raise web.HTTPFound('/api/health?redirected=true')
        return web.json_response(health(manager,revision='e'*40))
    runner,site,sock=await listener(manager,handle)
    try:
        await site.start()
        await update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.06,interval=.01)
        state=service.state['updates']
        assert state['phase']=='activating' and state['pendingRestart']==TARGET
        assert 'not been confirmed' in state['error']
        failure=manager.diagnostics.state['lastFailure']
        assert failure['phase']=='restart-readiness' and failure['errorType']=='TimeoutError'
        assert not redirected
        assert not any(row['phase']=='restart-ack' for row in manager.diagnostics.state['events'])
        with pytest.raises(Exception,match='update is activating'):
            await service.dispatch('conversation.send',{'text':'Still waiting for readiness'})
    finally:
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('finish', ['timeout', 'cancel'])
async def test_slow_target_preparation_cannot_stall_loop_or_reset_budget(tmp_path, monkeypatch, finish):
    import threading
    service, manager = make_manager(tmp_path, monkeypatch)
    started, release = threading.Event(), threading.Event()
    def slow_targets(manager):
        started.set()
        release.wait(3)
        return [], None
    monkeypatch.setattr(update_readiness, 'probe_targets', slow_targets)
    task = asyncio.create_task(update_readiness.wait_for_readiness(
        manager, 'fixture-token', timeout=.15 if finish == 'timeout' else 2))
    try:
        deadline = asyncio.get_running_loop().time() + 1
        while not started.is_set():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.005)
        # The resolver thread is blocked, but the event loop remains usable.
        await asyncio.wait_for(asyncio.sleep(.01), .1)
        assert not release.is_set() and not task.done()
        if finish == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, .5)
        failure = manager.diagnostics.state['lastFailure']
        assert failure['errorType'] == ('TimeoutError' if finish == 'timeout' else 'CancelledError')
        assert service.state['updates']['pendingRestart'] == TARGET
        assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        release.set()
        await service.close()


@pytest.mark.parametrize('slow_phase', ['response', 'confirmation'])
async def test_total_readiness_deadline_covers_multiple_targets_and_confirmation(tmp_path, monkeypatch, slow_phase):
    service, manager = make_manager(tmp_path, monkeypatch)
    service.port = 8941
    requests, confirmed = [], []
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self):
            await asyncio.sleep(.04 if slow_phase == 'response' else .001)
            return health(manager)
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url, **kwargs):
            requests.append(url)
            return Response()
    async def confirm(*args, **kwargs):
        await asyncio.sleep(.04)
        confirmed.append(True)
        return True
    urls = ['https://127.0.0.1:8941/api/health', 'https://127.0.0.2:8941/api/health']
    monkeypatch.setattr(update_readiness, 'probe_targets', lambda manager: (urls, None))
    monkeypatch.setattr(update_readiness.aiohttp, 'ClientSession', Client)
    monkeypatch.setattr(update_readiness, 'confirm_readiness', confirm)
    try:
        await update_readiness.wait_for_readiness(manager, 'fixture-token', timeout=.02, interval=.001)
        assert requests == urls[:1]  # Never start target two after total budget.
        assert not confirmed  # No late confirmation, including an in-flight one.
        assert service.state['updates']['pendingRestart'] == TARGET
        assert manager.diagnostics.state['lastFailure']['errorType'] == 'TimeoutError'
        assert not any(row['phase'] == 'restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        await service.close()


async def test_cancelled_readiness_preserves_marker_and_reports_interruption(tmp_path,monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch)
    async def handle(request):return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-token',timeout=2,interval=.01))
    try:
        await asyncio.sleep(.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert service.state['updates']['pendingRestart']==TARGET
        assert service.state['updates']['phase']=='activating'
        failure=manager.diagnostics.state['lastFailure']
        assert failure['status']=='interrupted' and failure['errorType']=='CancelledError'
        assert not any(row['phase']=='restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('finish',['timeout','cancel'])
async def test_waiter_for_superseded_attempt_does_not_ack_or_overwrite_new_attempt(tmp_path,monkeypatch,finish):
    service,manager=make_manager(tmp_path,monkeypatch)
    requested=asyncio.Event();release=asyncio.Event()
    async def handle(request):
        requested.set()
        await release.wait()
        return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    await site.start()
    task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.1,interval=.01))
    try:
        await asyncio.wait_for(requested.wait(),1)
        newer={**TARGET,'attemptId':'e'*32}
        await manager.publish(pendingRestart=newer,error='Current attempt detail')
        manager.diagnostics.begin('application',REVISION,newer['attemptId'])
        latest=deepcopy(manager.diagnostics.record('current-attempt','started'))
        release.set()
        if finish=='cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        else:
            await asyncio.wait_for(task,1)
        state=service.state['updates']
        assert state['pendingRestart']==newer and state['error']=='Current attempt detail'
        assert manager.diagnostics.state['latest']==latest
        assert not any(row['phase']=='restart-ack' for row in manager.diagnostics.state['events'])
    finally:
        release.set();task.cancel();await asyncio.gather(task,return_exceptions=True)
        await runner.cleanup();sock.close();await service.close()


async def test_failed_legacy_health_probe_retains_original_failure(tmp_path,monkeypatch):
    original=legacy_state()
    service,manager=make_manager(tmp_path,monkeypatch,state=original)
    validated_record(manager)
    async def handle(request):return web.json_response(health(manager,revision='e'*40))
    runner,site,sock=await listener(manager,handle)
    try:
        await site.start()
        await update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.05,interval=.01)
        state=service.state['updates']
        assert state['phase']=='error' and state['error']==update_readiness.LEGACY_RESTART_ERROR
        assert state['diagnostics']['lastFailure']==original['diagnostics']['lastFailure']
        assert state['diagnostics']['latest']['phase']=='restart-readiness'
        assert state['diagnostics']['latest']['status']=='failed'
        assert not any(row['phase'] in {'restart-ack','restart-reconcile'} for row in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup();sock.close();await service.close()


@pytest.mark.parametrize('operation',['check','app','install','activate','rollback','tick'])
async def test_pending_restart_blocks_later_updates_from_erasing_receipt(tmp_path,monkeypatch,operation):
    service,manager=make_manager(tmp_path,monkeypatch)
    service.state['updates']['error']='Pending restart needs review'
    manager.diagnostics.begin('application',REVISION,ATTEMPT)
    manager.diagnostics.record('service-restart-request','uncertain',exitCode=-15)
    before=deepcopy(service.state['updates'])
    async def unexpected(*args,**kwargs):pytest.fail('no update may start during pending handoff')
    monkeypatch.setattr(manager,'inventory_sources',unexpected)
    monkeypatch.setattr('amplifier_web.app_updates.stage',unexpected)
    monkeypatch.setattr('amplifier_web.app_updates.activate',unexpected)
    try:
        await getattr(manager,operation)()
        assert service.state['updates']==before
        assert manager.awaiting_restart()
    finally:
        await service.close()


@pytest.mark.parametrize('identity',[{**IDENTITY,'revision':'e'*40},{**IDENTITY,'version':'0.0.0'}])
async def test_old_legacy_failure_does_not_permanently_block_repairs(tmp_path,monkeypatch,identity):
    service,manager=make_manager(tmp_path,monkeypatch,state=legacy_state(),identity=identity)
    validated_record(manager)
    async def handle(request):return web.json_response(health(manager))
    runner,site,sock=await listener(manager,handle)
    calls=[]
    async def inventory(**kwargs):calls.append('inventory');return []
    async def application():return {'id':'application','current':__version__,'status':'current'}
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr('amplifier_web.app_updates.check',application)
    await site.start()
    manager.readiness_task=asyncio.create_task(update_readiness.wait_for_readiness(manager,'fixture-token',timeout=.06,interval=.01))
    try:
        assert manager.awaiting_restart()
        await manager.check()
        assert not calls
        await manager.readiness_task
        assert not manager.awaiting_restart()
        assert service.state['updates']['phase']=='error'
        await manager.check()
        assert calls==['inventory']
        assert service.state['updates']['phase']=='checked'
        assert service.state['updates']['diagnostics']['lastFailure']['phase']=='service-restart'
        assert not any(row['phase'] in {'restart-ack','restart-reconcile'} for row in manager.diagnostics.state['events'])
    finally:
        await runner.cleanup();sock.close();await service.close()


async def test_verified_app_restart_continues_manual_component_install(tmp_path, monkeypatch):
    service,manager=make_manager(tmp_path,monkeypatch,state={
        'phase':'activating','pendingRestart':TARGET,
        'sequence':{'stage':'application','install':True},
    })
    service.state['settings']['updates']['autoInstall']=False
    try:
        assert not service.state['settings']['updates']['autoInstall']
        assert 'nextStage' not in service.state['updates']['sequence']
        assert await manager.confirm_readiness(health(manager))
        sequence=service.state['updates']['sequence']
        assert sequence['nextStage']=='included' and sequence['install'] is True
        assert sequence['included']['status']=='waiting'
        assert sequence['other']['status']=='waiting'
    finally:
        await service.close()
