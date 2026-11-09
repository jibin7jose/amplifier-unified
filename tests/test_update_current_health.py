"""Current installation evidence is distinct from immutable attempt history."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amplifier_web import update_health as health
from amplifier_web.attention import snapshot
from amplifier_web.update_diagnostics import UpdateDiagnostics

CAPTURE = Path(__file__).parent / 'fixtures/update-current-health-captured060.json'


def captured():
    value = json.loads(CAPTURE.read_text())
    observed = value['observedState']
    get = lambda path: observed[path]['value']
    state = {'phase': 'checked', 'error': None, 'available': 0,
             'release': value['active']['current'], 'sequence': get('/updates/sequence'),
             'applicationResult': get('/updates/applicationResult'),
             'diagnostics': {'events': [], 'activeFailures': get('/updates/diagnostics/activeFailures'),
                             'completedBatches': list(get('/updates/diagnostics/completedBatches').values())},
             'application': {'current': '0.60.0', 'runningRevision': get('/updates/applicationResult')['revision'], 'status': 'current'},
             'items': []}
    return value, state


def mounted_fixture(tmp_path, monkeypatch):
    value, state = captured()
    manager = SimpleNamespace(home=tmp_path, directory=tmp_path/'updates',
        service=SimpleNamespace(state={'updates': state, 'configurationRevision': 1}, _save_changes=lambda **kw: None))
    manager.directory.mkdir()
    (manager.directory/'active.json').write_text(json.dumps(value['active']))
    generation = value['active']['current'];source = value['generations'][generation]
    stage = manager.directory/'releases'/generation;stage.mkdir(parents=True)
    for name in ('plan.json','validated.json','profiles-qualified.json','runtime-installed.json'):
        (stage/name).write_text(json.dumps(source[name]))
    project = tmp_path/'runtime';project.mkdir();(project/'uv.lock').write_text('exact graph')
    (stage/'runtime.lock').write_text('exact graph')
    from amplifier_web import runtime_environment, runtime_qualification, runtime_profiles
    monkeypatch.setattr(runtime_environment, 'project_path', lambda *a: project)
    # Sanitized reference has names/versions, not host-private paths. The actual
    # recorded-graph comparator is exercised against the same synthetic graph.
    monkeypatch.setattr(runtime_qualification, 'installed_graph', lambda p: source['runtime-installed.json'])
    monkeypatch.setattr(health, 'configuration', lambda m: ('fixture', SimpleNamespace(settings={})))
    monkeypatch.setattr(runtime_profiles, 'configuration_key', lambda *a: source['profiles-qualified.json']['configuration'])
    manager.diagnostics=UpdateDiagnostics(manager)
    return manager, stage, project, source


def test_captured060_current_graph_removes_old_health_cards_not_history(tmp_path, monkeypatch):
    manager,stage,project,source=mounted_fixture(tmp_path,monkeypatch)
    original=copy.deepcopy(manager.diagnostics.state)
    evidence=health.active_evidence(manager)
    assert evidence and 'amplifier-foundation' in evidence['packages']
    manager._active_update_evidence=evidence
    health.publish(manager)
    status=manager.service.state['updates']['currentStatus']
    assert status['status']=='current' and not status['actionRequired']
    assert snapshot(manager.service.state)['pages'].get('updates',0)==0
    assert manager.diagnostics.state==original  # No resolve/delete/dismissal.
    manager.diagnostics=UpdateDiagnostics(manager)
    assert manager.diagnostics.state==original
    assert health.current(manager.service.state['updates'],health.active_evidence(manager))['status']=='current'


@pytest.mark.parametrize('case',['newer','missing-package','unrelated-domain','unknown-activation','config','graph','lock','partial','staged','app-unverified','rollback'])
def test_current_unknown_or_new_problem_is_not_hidden_by_old_success(tmp_path,monkeypatch,case):
    m,stage,project,source=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    if case=='newer':next(iter(u['diagnostics']['activeFailures'].values()))['latest']['at']=10**11
    elif case=='missing-package':source['runtime-installed.json'][:]=[{'name':'other','version':'1'}];(stage/'runtime-installed.json').write_text(json.dumps(source['runtime-installed.json']))
    elif case=='unrelated-domain':next(iter(u['diagnostics']['activeFailures'].values()))['latest']['kind']='application'
    elif case=='unknown-activation':u['diagnostics']['completedBatches']=[]
    elif case=='config':(stage/'profiles-qualified.json').write_text(json.dumps({'profiles':['work'],'configuration':'different'}))
    elif case=='graph':source['runtime-installed.json'][0]['version']='changed'
    elif case=='lock':(project/'uv.lock').write_text('changed')
    elif case=='partial':u['sequence']={'stage':'included'}
    elif case=='staged':u.update(phase='staged',pendingRelease='b'*32)
    elif case=='app-unverified':u['pendingReplacement']={'unqualified':True}
    elif case=='rollback':u['pendingRollback']='c'*32;u['phase']='error';u['error']='Rollback not confirmed'
    status=health.current(u,health.active_evidence(m))
    assert status['status']!='current'
    if case not in {'partial','staged','unknown-activation'}:assert status['actionRequired']


def test_render_projection_does_no_filesystem_work_and_config_revision_invalidates(tmp_path,monkeypatch):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch);m._active_update_evidence=health.active_evidence(m)
    m._update_health_configuration_revision=1
    monkeypatch.setattr(Path,'read_bytes',lambda *a:pytest.fail('render read'))
    monkeypatch.setattr(Path,'read_text',lambda *a:pytest.fail('render read'))
    for _ in range(100):health.publish(m)
    assert m.service.state['updates']['currentStatus']['status']=='current'
    m.service.state['configurationRevision']=2;health.publish(m)
    assert not m.service.state['updates']['currentStatus']['actionRequired']
    assert m.service.state['updates']['currentStatus']['title']=='Checking current configuration'


def test_retry_witness_preserves_manual_and_unknown_boundaries():
    batch={};failure={'reason':'runtime-source-constraint','status':'failed'}
    health.record_retry(batch,'A',failure)
    assert health.retry_decision(batch,'A',now=10**12)=='changed-inputs'
    assert health.retry_decision(batch,'B') is None
    health.record_retry(batch,'A',{'status':'interrupted'})
    assert health.retry_decision(batch,'B')=='inspect'
    health.record_retry(batch,'A',{'status':'failed','errorType':'TimeoutError'})
    assert health.retry_decision(batch,'A',now=0)=='backoff'
    assert health.retry_decision(batch,'A',now=10**12) is None


async def test_automatic_unchanged_conflict_does_zero_duplicate_stage_work(tmp_path,monkeypatch):
    from amplifier_web.updates import UpdateManager
    from amplifier_web.runtime_environment import ProtectedRuntimeSource
    from amplifier_web import update_plan
    from amplifier_web.update_sequence import app_selection
    from amplifier_web.host import config as host_config
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.closed=False;m.awaiting_restart=lambda:False
    m.independent_tool_results=lambda s:False
    m.inventory=[{'id':'fixture','kind':'runtime environment','status':'update','eligible':True,'latest':'A'}]
    m.service.lock=asyncio.Lock();u=m.service.state['updates'];u['sequence']={'stage':'included'}
    calls={'validate':0}
    releases=m.directory/'releases';existing=set(releases.iterdir())
    async def publish(**kw):u.update(kw)
    async def build(*a):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    async def validate(stage,release):
        assert stage.is_dir() and stage.name==release
        calls['validate']+=1
        raise ProtectedRuntimeSource('amplifier-foundation','runtime-source-constraint')
    m.publish=publish;m.validate=validate
    monkeypatch.setattr(update_plan,'build',build)
    monkeypatch.setattr(health,'preparation_key',lambda m,rows:rows[0]['latest'])
    # Avoid any real shared configuration; the full install orchestration owns
    # stage creation, the preflight guard and failure recording in this fixture.
    from amplifier_web import updates,session_files
    monkeypatch.setattr(updates,'foundation_home',lambda h:tmp_path/'absent')
    monkeypatch.setattr(session_files,'amplifier_home',lambda:tmp_path/'empty-shared')
    monkeypatch.setattr(host_config,'amplifier_home',lambda:tmp_path/'empty-shared')
    # Supply the real checked witness after isolating configuration. Live and
    # staged selection verification still run in UpdateManager.install.
    m.inventory[0]['appSelection']=app_selection(m.home)['witness']
    await UpdateManager.install(m,automatic=True)
    assert calls=={'validate':1}
    first=set(releases.iterdir())
    assert len(first-existing)==1
    for _ in range(10):
        u['phase']='available'
        await UpdateManager.install(m,automatic=True)
    assert calls=={'validate':1}
    assert set(releases.iterdir())==first
    m.inventory[0]['latest']='B'
    await UpdateManager.install(m,automatic=True)
    assert calls=={'validate':2}
    assert len(set(releases.iterdir())-existing)==2
    await UpdateManager.install(m)  # Deliberate fresh install intent.
    assert calls=={'validate':3}
    assert len(set(releases.iterdir())-existing)==3


@pytest.mark.parametrize('reason',[None,'disk-full','bundle-load-failed','runtime-source-constraint'])
def test_later_complete_verified_graph_is_current_for_older_preparation_failures(tmp_path,monkeypatch,reason):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    for group in m.diagnostics.state['activeFailures'].values():
        group['latest'].pop('reason',None)
        if reason:group['latest']['reason']=reason
    before=copy.deepcopy(m.diagnostics.state)
    assert health.current(m.service.state['updates'],health.active_evidence(m))['status']=='current'
    assert m.diagnostics.state==before


@pytest.mark.parametrize('case',['covered','unknown-features','missing-feature','newer','wrong-running'])
def test_application_result_covers_old_generic_candidate_failure_only_with_current_scope(case):
    u={'phase':'checked','sequence':{'stage':'complete'},'application':{'status':'current','current':'0.60.1','runningRevision':'a'*40},
       'applicationResult':{'phase':'restart-ack','attemptId':'a'*32,'revision':'a'*40,'version':'0.60.1','verifiedAt':30},
       'diagnostics':{'completedBatches':[{'kind':'application','phase':'restart-ack','attemptId':'a'*32,'revision':'a'*40,'startedAt':20,'completedAt':31,'applicationExtras':['managed-browser']}],
         'activeFailures':{'old':{'latest':{'kind':'application','status':'failed','phase':'candidate-install','at':10,'applicationExtras':[]}}}}}
    failure=u['diagnostics']['activeFailures']['old']['latest']
    if case=='unknown-features':failure.pop('applicationExtras')
    elif case=='missing-feature':failure['applicationExtras']=['tui']
    elif case=='newer':failure['at']=32
    elif case=='wrong-running':u['application']['runningRevision']='b'*40
    value=health.current(u,{'generation':'c'*32,'startedAt':20,'verifiedAt':31,'packages':{'example'}})
    assert (value['status']=='current')==(case=='covered')


@pytest.mark.parametrize('automatic',[True,False])
async def test_interrupted_preparation_cannot_be_replayed_by_manual_intent(tmp_path,monkeypatch,automatic):
    from amplifier_web.updates import UpdateManager
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.awaiting_restart=lambda:False
    m.diagnostics.state['batch']={'retry':{'mode':'inspect','inputKey':'old'}}
    before=copy.deepcopy(m.diagnostics.state)
    if automatic:
        await UpdateManager.install(m,automatic=True)
    else:
        with pytest.raises(ValueError,match='interrupted'):
            await UpdateManager.install(m)
    assert m.diagnostics.state==before


@pytest.mark.parametrize('case',['local-only','mixed-check-failure','unknown-issue','new-conflict'])
def test_custom_source_is_information_without_hiding_current_conflicts(tmp_path,monkeypatch,case):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    u['items']=[{'id':'custom','label':'Custom module','status':'local_changes','updateTier':'included'}]
    u['sequence']={'stage':'included','included':{'issues':1}}
    if case=='mixed-check-failure':
        u['items'].append({'id':'broken','label':'Unavailable source','status':'check_failed'})
    elif case=='unknown-issue':u['sequence']['included']['issues']=2
    elif case=='new-conflict':
        next(iter(u['diagnostics']['activeFailures'].values()))['latest']['at']=10**11
    m._active_update_evidence=health.active_evidence(m);health.publish(m)
    assert u['currentStatus']['actionRequired']==(case!='local-only')
    if case=='local-only':
        assert u['currentStatus']['status']=='unchecked'
        notices=snapshot(m.service.state)
        assert notices['pages'].get('updates',0)==0
        assert [row['id'] for row in notices['items']]==['source:custom']
        assert notices['items'][0]['informational']


@pytest.mark.parametrize('case',['evicted','legacy-event','legacy-missing','newer','activation-failure','outside-graph','changed-graph'])
def test_history_retention_is_not_activation_authority(tmp_path,monkeypatch,case):
    m,stage,_,source=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    batches=u['diagnostics']['completedBatches']
    activation=next(row for row in batches if row.get('revision')==u['release'])
    if case.startswith('legacy'):
        activation.pop('revision')
        if case=='legacy-event':
            u['diagnostics']['events']=[{'revision':u['release'],'phase':'ecosystem-activation',
                'status':'succeeded','at':activation['completedAt'],'attemptId':activation['attemptId']}]
    else:
        # More than the retained eight later batches, none for this generation.
        batches[:]=[{'revision':str(i),'phase':'check','completedAt':activation['completedAt']+i} for i in range(10)]
        batches[:]=batches[-8:]
    failure=next(iter(u['diagnostics']['activeFailures'].values()))['latest']
    if case=='newer':failure['at']=10**11
    elif case=='activation-failure':failure['phase']='ecosystem-activation'
    elif case=='outside-graph':failure['package']='not-in-qualified-graph'
    elif case=='changed-graph':source['runtime-installed.json'][0]['version']='different'
    before=copy.deepcopy(m.diagnostics.state)
    evidence=health.active_evidence(m)
    value=health.current(u,evidence)
    if case=='legacy-event':
        assert value['status']=='current'
    elif case in {'evicted','legacy-missing'}:
        assert value['status']=='unchecked' and not value['actionRequired']
        assert 'activation receipt' in value['detail']
        assert evidence['activationUnknown']
    else:
        assert value['actionRequired'] and value['status']!='current'
    assert m.diagnostics.state==before


async def test_configuration_revalidation_gap_is_neutral_but_failed_proof_is_not(tmp_path,monkeypatch):
    m,stage,_,_=mounted_fixture(tmp_path,monkeypatch)
    m._active_update_evidence=health.active_evidence(m);m._update_health_configuration_revision=1
    m.service.state['configurationRevision']=2
    health.publish(m)
    assert not m.service.state['updates']['currentStatus']['actionRequired']
    (stage/'profiles-qualified.json').write_text('{}')
    await health.refresh(m);health.publish(m)
    assert m.service.state['updates']['currentStatus']['actionRequired']


@pytest.mark.parametrize('case',['available','invalid-graph','local-only'])
def test_local_customization_does_not_mask_available_updates_or_invalid_graph(tmp_path,monkeypatch,case):
    m,stage,_,_=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    u['diagnostics']['activeFailures']={}
    u['items']=[{'id':'custom','label':'Custom source','status':'local_changes','updateTier':'included'}]
    if case=='available':u['available']=1
    elif case=='invalid-graph':(stage/'runtime-installed.json').write_text('[]')
    value=health.current(u,health.active_evidence(m))
    assert value['status']=={'available':'available','invalid-graph':'unverified','local-only':'unchecked'}[case]
    assert value['actionRequired']==(case=='invalid-graph')


async def test_inspection_fence_quietly_waits_through_ten_actual_automatic_ticks(tmp_path,monkeypatch):
    from amplifier_web.updates import UpdateManager
    import time
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.service.state['settings']={'updates':{'autoInstall':True,'autoCheck':True}}
    m.service.state['updates'].update(phase='available',available=1,lastCheck=time.time(),
                                    adoption={'activeWorkers':0,'pendingWorkers':0})
    m.diagnostics.state['batch']={'retry':{'mode':'inspect'}}
    m.awaiting_restart=lambda:False
    m.lock=asyncio.Lock();m.cleanup_task=None;m._storage_last_pass=time.time()
    m.service.runtime=SimpleNamespace(generation_usage=lambda:[])
    m.requires_idle=lambda state:False
    # Any further work is a defect: tick must wait before checks/staging or its
    # exception handler can create another background-update failure.
    async def forbidden(*a,**kw):pytest.fail('automatic retry work')
    m.check=m.publish=forbidden
    m.install=lambda **kw:UpdateManager.install(m,**kw)
    before=copy.deepcopy(m.service.state)
    for _ in range(10):await UpdateManager.tick(m)
    assert m.service.state==before


async def interrupted_copy_fixture(tmp_path,monkeypatch):
    """Cancel a real copy thread in the actual install orchestration."""
    import threading
    from amplifier_web import updates, update_storage, update_plan, session_files
    from amplifier_web.update_sequence import app_selection
    from amplifier_web.host import config as host_config
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.closed=False;m.awaiting_restart=lambda:False
    m.independent_tool_results=lambda s:False
    m.service.lock=asyncio.Lock()
    u=m.service.state['updates'];u.update(phase='available',available=1,sequence={'stage':'included'})
    m.inventory=[{'id':'fixture','kind':'runtime environment','status':'update','eligible':True,'latest':'A'}]
    calls={'validate':0}
    async def publish(**kw):u.update(kw)
    async def build(*a):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    async def validate(stage,release):
        assert stage.is_dir() and stage.name==release
        calls['validate']+=1
        raise RuntimeError('fresh candidate stopped during validation')
    m.publish=publish;m.validate=validate
    monkeypatch.setattr(update_plan,'build',build)
    monkeypatch.setattr(health,'preparation_key',lambda m,rows:'A')
    source=tmp_path/'copy-source';source.mkdir();(source/'keep').write_text('unchanged')
    monkeypatch.setattr(updates,'foundation_home',lambda h:source)
    monkeypatch.setattr(session_files,'amplifier_home',lambda:tmp_path/'empty-shared')
    monkeypatch.setattr(host_config,'amplifier_home',lambda:tmp_path/'empty-shared')
    m.inventory[0]['appSelection']=app_selection(m.home)['witness']
    releases=m.directory/'releases';existing=set(releases.iterdir())
    started=threading.Event();release=threading.Event();finished=threading.Event()
    copy_snapshot=update_storage.copy_snapshot
    def copy_with_gate(source,target):
        started.set()
        assert release.wait(3), 'test did not release its owned copy'
        result=copy_snapshot(source,target);finished.set()
        return result
    monkeypatch.setattr(update_storage,'copy_snapshot',copy_with_gate)
    task=asyncio.create_task(updates.UpdateManager.install(m))
    assert await asyncio.to_thread(started.wait,3)
    task.cancel()
    await asyncio.sleep(0)  # Give cancellation the blocked owned copy to settle.
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert finished.is_set()
    assert calls=={'validate':0}
    proof=m.diagnostics.state['batch']['preparation']
    assert proof['boundary']=='copy-only' and proof['copySettled']
    assert set(releases.iterdir())-existing=={releases/proof['candidate']}
    assert (source/'keep').read_text()=='unchanged'
    return m,calls,copy.deepcopy(proof),copy.deepcopy(m.diagnostics.state['lastFailure'])


@pytest.mark.parametrize('case',['safe','pointer-drift','promotion','rollback','unsettled','validation','legacy','stage-replaced'])
async def test_manual_fresh_candidate_requires_actual_settled_copy_and_unchanged_pointer(tmp_path,monkeypatch,case):
    from amplifier_web.updates import UpdateManager
    m,calls,proof,failure=await interrupted_copy_fixture(tmp_path,monkeypatch)
    releases=m.directory/'releases';existing=set(releases.iterdir())
    u=m.service.state['updates'];batch=m.diagnostics.state.get('componentPreparation',m.diagnostics.state['batch']);old=m.diagnostics.state['attemptId']
    if case=='pointer-drift':
        (m.directory/'active.json').write_text(json.dumps({'current':'b'*32}))
    elif case=='promotion':u['pendingRelease']=proof['candidate']
    elif case=='rollback':u['pendingRollback']='c'*32
    elif case=='unsettled':batch['preparation']['copySettled']=False
    elif case=='validation':batch['preparation']['boundary']='preparing'
    elif case=='legacy':batch.pop('preparation')
    elif case=='stage-replaced':batch['preparation']['directoryIdentity']=[0,0]
    if case=='safe':
        await UpdateManager.install(m)
        assert calls=={'validate':1}
        assert m.diagnostics.state['attemptId']!=old
        assert m.diagnostics.state['revision']!=proof['candidate']
        assert set(releases.iterdir())-existing=={releases/m.diagnostics.state['revision']}
        assert any(row['phase']=='ecosystem-preparation-reconciled' for row in m.diagnostics.state['events'])
        assert any(group.get('latest')==failure for group in m.diagnostics.state['activeFailures'].values())
    else:
        before=copy.deepcopy(m.diagnostics.state)
        with pytest.raises(ValueError,match='no proof'):await UpdateManager.install(m)
        assert m.diagnostics.state==before
        assert calls=={'validate':0}
        assert set(releases.iterdir())==existing


@pytest.mark.parametrize('kind',['application','smart-tools','rollback'])
@pytest.mark.parametrize('safe',[True,False])
async def test_unrelated_batch_preserves_exact_component_fence_and_copy_recovery(tmp_path,monkeypatch,kind,safe):
    from amplifier_web.updates import UpdateManager
    m,calls,proof,_=await interrupted_copy_fixture(tmp_path,monkeypatch)
    releases=m.directory/'releases';existing=set(releases.iterdir())
    if not safe:
        m.diagnostics.state.get('componentPreparation',m.diagnostics.state['batch'])['preparation']['boundary']='preparing'
    m.diagnostics.begin(kind,'d'*40 if kind=='application' else 'd'*32)
    m.diagnostics.record('unrelated-check','succeeded')
    # Reload the same retained host state. The display batch and latest event
    # are now unrelated; neither may discard the unresolved component operation.
    m.diagnostics=UpdateDiagnostics(m)
    if safe:
        await UpdateManager.install(m)
        assert calls['validate']==1
        assert m.diagnostics.state['revision']!=proof['candidate']
        assert set(releases.iterdir())-existing=={releases/m.diagnostics.state['revision']}
        assert not health.pending_preparation(m.diagnostics.state)
    else:
        with pytest.raises(ValueError,match='interrupted'):await UpdateManager.install(m)
        assert calls['validate']==0
        assert set(releases.iterdir())==existing
        assert health.pending_preparation(m.diagnostics.state)


async def test_component_fence_allows_health_storage_adoption_and_independent_app(tmp_path,monkeypatch):
    from amplifier_web.updates import UpdateManager
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.service.state['settings']={'updates':{'autoCheck':False,'autoInstall':True}}
    m.diagnostics.state['batch']={'kind':'ecosystem','attemptId':'a'*32,'retry':{'mode':'inspect'}}
    m.awaiting_restart=lambda:False;m.requires_idle=lambda u:False
    m.service.runtime=SimpleNamespace(generation_usage=lambda:[])
    m.cleanup_task=None;m._storage_policy_due=True;m._update_health_initial=True
    calls={'health':0,'storage':0,'app':0}
    async def refresh(manager):calls['health']+=1
    async def retire():calls['storage']+=1
    async def app():
        calls['app']+=1
        m.diagnostics.begin('application','e'*40)
    async def publish(**kw):m.service.state['updates'].update(kw)
    m.publish=publish;m.retire_storage=retire;m.app=app
    m.service.state['updates']['appAvailable']=True
    monkeypatch.setattr(health,'refresh',refresh)
    await UpdateManager.tick(m)
    await m.cleanup_task
    assert calls=={'health':1,'storage':1,'app':1}
    assert m.service.state['updates']['adoption']=={'activeWorkers':0,'pendingWorkers':0}
    assert health.pending_preparation(m.diagnostics.state)
