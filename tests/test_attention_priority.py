"""Current health drives attention; attempt evidence retains its own semantics.

The former per-incident badge contract is superseded by the 2026-11-07 Updates
contract. Application qualification remains tested independently below: removing
historical badges must not weaken feature, browser, storage or occurrence guards.
"""
import copy
import json
from types import SimpleNamespace
import pytest
from amplifier_web.attention import snapshot
from amplifier_web.update_diagnostics import UpdateDiagnostics, disposition_occurrence
from amplifier_web import update_health


def fixture(tmp_path, updates=None):
    state={'attentionRead':{},'updates':updates or {},'feedback':{'requests':[]}}
    service=SimpleNamespace(state=state,_save_changes=lambda **kw:None)
    manager=SimpleNamespace(service=service,directory=tmp_path)
    diagnostics=UpdateDiagnostics(manager);manager.diagnostics=diagnostics
    return state,diagnostics


def pending(diagnostics):
    return list(diagnostics.state['activeFailures'].values())


def project(state):
    state['updates']['currentStatus']=update_health.current(state['updates'])
    return snapshot(state)


def read_all(state):
    result=project(state)
    state['attentionRead'].update({i['id']:i['fingerprint'] for i in result['items']})


def fail(diagnostics, *, component='amplifier-module-loop-live', kind='ecosystem'):
    diagnostics.begin(kind,'c'*40,components=[component],application_extras=[] if kind=='application' else None)
    return diagnostics.record(kind+'-validation','failed',component=component,
        reason='runtime-resolution-mismatch',errorType='ValueError')


def test_news_and_success_receipts_are_quiet_but_retained(tmp_path):
    state,_=fixture(tmp_path)
    state['updates']['application']={'releaseNotes':[{'version':'1.0.0','notices':[
        {'id':str(i),'title':f'News {i}','detail':'Guidance','action':'Review when useful'} for i in range(53)]}]}
    state['feedback']['requests']=[{'requestId':f'sent-{i}','status':'submitted','title':'Reported','message':'Sent'} for i in range(3)]
    before=copy.deepcopy(state);result=snapshot(state)
    assert len(result['items'])==56 and result['badgeUnread']==0 and state==before
    state['feedback']['requests'][0]['status']='failed';assert snapshot(state)['badgeUnread']==1


def test_many_old_attempts_do_not_create_many_current_chores(tmp_path):
    state,d=fixture(tmp_path)
    first=fail(d)
    for i in range(35):fail(d,component=f'amplifier-component-{i}')
    assert len(pending(d))==32 and d.state['failureOverflow']['occurrences']==4
    result=project(state);assert result['badgeUnread']==1
    item=next(i for i in result['items'] if i.get('actionRequired'))
    assert item['id']=='updates:current' and 'runtime-resolution-mismatch' not in item['title']
    read_all(state);assert project(state)['badgeUnread']==1
    assert first['id'] in d.path.read_text()


def test_check_or_stage_does_not_rewrite_history_or_claim_health(tmp_path):
    state,d=fixture(tmp_path);fail(d);before=copy.deepcopy(d.state['activeFailures'])
    d.begin('ecosystem',components=['other']);d.clear_failure();d.record('ecosystem-stage','succeeded')
    assert project(state)['badgeUnread']==1 and d.state['activeFailures']==before
    d.record('ecosystem-activation','succeeded')
    assert project(state)['badgeUnread']==1 and d.state['activeFailures']==before


def test_current_error_survives_read_and_old_application_success(tmp_path):
    state,d=fixture(tmp_path);fail(d);fail(d,kind='application')
    d.begin('application','c'*40,application_extras=[]);d.clear_failure();d.record('restart-ack','succeeded')
    assert len(pending(d))==1
    read_all(state);state['updates']['error']='Application release could not be checked.'
    result=project(state);assert result['badgeUnread']==1
    assert any(i['id']=='updates:current' and i['actionRequired'] for i in result['items'])


def test_original_failure_survives_event_eviction_and_metadata_collector(tmp_path):
    state,d=fixture(tmp_path);d.manager.service.diagnostics=SimpleNamespace(record=lambda *a:None)
    original=fail(d)
    for _ in range(55):d.record('ecosystem-check','succeeded')
    assert original not in d.state['events']
    assert json.loads(d.path.read_text().splitlines()[0])==original
    assert pending(d)[0]['first']==original


@pytest.mark.parametrize('old,new',[(None,[]),([],None),(['managed-browser'],[]),([],['managed-browser']),(['tui'],['managed-browser'])])
def test_application_recovery_requires_same_known_feature_scope(tmp_path,old,new):
    _,d=fixture(tmp_path);d.begin('application','a'*40,application_extras=old)
    d.record('candidate-install','failed',reason='disk-full')
    d.begin('application','b'*40,application_extras=new);d.record('stage','succeeded');d.record('restart-ack','succeeded')
    assert len(pending(d))==1


@pytest.mark.parametrize('reason',['disk-full','disk-quota-exceeded','storage-permission-denied','read-only-storage'])
@pytest.mark.parametrize('change',[None,'no-stage','wrong-revision','late-occurrence','new-attempt'])
def test_storage_recovery_keeps_exact_stage_and_occurrence_fence(tmp_path,reason,change):
    _,d=fixture(tmp_path);d.begin('application','a'*40,application_extras=[])
    old=d.record('candidate-install','failed',reason=reason)
    attempt=d.begin('application','b'*40,application_extras=[])
    if change!='no-stage':d.record('stage','succeeded')
    if change=='late-occurrence':pending(d)[0]['latest']={**old,'id':'later'}
    d.begin('application','c'*40 if change=='wrong-revision' else 'b'*40,
            None if change=='new-attempt' else attempt,application_extras=[])
    d.record('restart-ack','succeeded')
    assert len(pending(d))==(0 if change is None else 1)
    assert old['id'] in d.path.read_text()


@pytest.mark.parametrize('change',[None,'no-browser','unknown-old','late-occurrence','new-attempt','superseded'])
def test_browser_launch_and_restart_evidence_remains_exact(tmp_path,change):
    _,d=fixture(tmp_path);d.begin('application','a'*40,application_extras=None if change=='unknown-old' else ['managed-browser'])
    old=d.record('managed-browser-install','failed',errorType='CommandFailure')
    attempt=d.begin('application','b'*40,application_extras=['managed-browser'])
    if change!='no-browser':d.browser_qualified()
    assert len(pending(d))==1
    if change=='late-occurrence':pending(d)[0]['latest']={**old,'id':'later'}
    d.begin('application','b'*40,None if change=='new-attempt' else attempt,application_extras=['managed-browser'])
    d.record('restart-superseded' if change=='superseded' else 'restart-ack','succeeded')
    assert len(pending(d))==(0 if change is None else 1)


def test_application_unknown_scope_disposition_does_not_change_current_error(tmp_path):
    state,d=fixture(tmp_path);d.begin('application','a'*40)
    original=d.record('candidate-install','failed');group=pending(d)[0]
    key=next(iter(d.state['activeFailures']));identity='updates:failure:'+key
    token=disposition_occurrence(identity,group)
    state['updates'].update(phase='error',error='Current installation remains unverified.')
    receipt=d.dispose(identity,token)
    assert receipt['recoveryVerified'] is False and not pending(d)
    assert project(state)['badgeUnread']==1 and original['id'] in d.path.read_text()
    d.begin('application','a'*40);d.record('candidate-install','failed')
    with pytest.raises(ValueError):d.dispose(identity,token)


async def test_real_read_ack_does_not_change_health_or_history(tmp_path):
    from amplifier_web.service import AppService
    from amplifier_web.updates import UpdateManager
    app=AppService(tmp_path/'app',workspace=tmp_path)
    try:
        app.update_manager=UpdateManager(app);fail(app.update_manager.diagnostics)
        await app.update_manager.publish(phase='error',error='Current update failed.')
        before=copy.deepcopy(app.state['updates']['diagnostics']['activeFailures'])
        item=next(i for i in app.get_state()['attention']['items'] if i['id']=='updates:current')
        await app.dispatch('attention.read',{'ids':[item['id']],'fingerprints':{item['id']:item['fingerprint']}})
        assert app.get_state()['attention']['badgeUnread']==1
        assert app.state['updates']['diagnostics']['activeFailures']==before
    finally:await app.close()


def test_public_failure_export_keeps_fixed_reason_and_excludes_private_values(tmp_path):
    from amplifier_web.update_report import event
    _,d=fixture(tmp_path);original=fail(d)
    clean=event({**original,'private':'secret'})
    assert clean['reason']=='runtime-resolution-mismatch' and 'secret' not in json.dumps(clean)
