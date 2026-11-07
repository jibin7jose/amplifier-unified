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


@pytest.mark.parametrize('case',['newer','missing-package','unrelated-error','unknown-activation','config','graph','lock','partial','staged','app-unverified','rollback'])
def test_current_unknown_or_new_problem_is_not_hidden_by_old_success(tmp_path,monkeypatch,case):
    m,stage,project,source=mounted_fixture(tmp_path,monkeypatch);u=m.service.state['updates']
    if case=='newer':next(iter(u['diagnostics']['activeFailures'].values()))['latest']['at']=10**11
    elif case=='missing-package':source['runtime-installed.json'][:]=[{'name':'other','version':'1'}];(stage/'runtime-installed.json').write_text(json.dumps(source['runtime-installed.json']))
    elif case=='unrelated-error':next(iter(u['diagnostics']['activeFailures'].values()))['latest']['reason']='disk-full'
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
    if case not in {'partial','staged'}:assert status['actionRequired']


def test_render_projection_does_no_filesystem_work_and_config_revision_invalidates(tmp_path,monkeypatch):
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch);m._active_update_evidence=health.active_evidence(m)
    m._update_health_configuration_revision=1
    monkeypatch.setattr(Path,'read_bytes',lambda *a:pytest.fail('render read'))
    monkeypatch.setattr(Path,'read_text',lambda *a:pytest.fail('render read'))
    for _ in range(100):health.publish(m)
    assert m.service.state['updates']['currentStatus']['status']=='current'
    m.service.state['configurationRevision']=2;health.publish(m)
    assert m.service.state['updates']['currentStatus']['actionRequired']


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
    m,_,_,_=mounted_fixture(tmp_path,monkeypatch)
    m.lock=asyncio.Lock();m.closed=False;m.awaiting_restart=lambda:False
    m.independent_tool_results=lambda s:False
    m.inventory=[{'id':'fixture','kind':'runtime environment','status':'update','eligible':True,'latest':'A'}]
    m.service.lock=asyncio.Lock();u=m.service.state['updates'];u['sequence']={'stage':'included'}
    calls={'stage':0,'validate':0}
    async def publish(**kw):u.update(kw)
    async def stage(*a):calls['stage']+=1
    async def build(*a):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    async def validate(*a):calls['validate']+=1;raise ProtectedRuntimeSource('amplifier-foundation','runtime-source-constraint')
    m.publish=publish;m.stage_cache_sources=stage;m.validate=validate
    monkeypatch.setattr(update_plan,'build',build)
    monkeypatch.setattr(health,'preparation_key',lambda m,rows:rows[0]['latest'])
    # Avoid any real shared configuration; the full install orchestration owns
    # stage creation, the preflight guard and failure recording in this fixture.
    from amplifier_web import updates,session_files
    monkeypatch.setattr(updates,'foundation_home',lambda h:tmp_path/'absent')
    monkeypatch.setattr(session_files,'amplifier_home',lambda:tmp_path/'empty-shared')
    await UpdateManager.install(m,automatic=True)
    assert calls=={'stage':1,'validate':1}
    for _ in range(10):
        u['phase']='available'
        await UpdateManager.install(m,automatic=True)
    assert calls=={'stage':1,'validate':1}
    m.inventory[0]['latest']='B'
    await UpdateManager.install(m,automatic=True)
    assert calls=={'stage':2,'validate':2}
    await UpdateManager.install(m)  # Deliberate fresh install intent.
    assert calls=={'stage':3,'validate':3}
