"""A restart command receipt is not evidence that the successor is serving."""
from amplifier_web.state_records import load as load_saved_state
import asyncio
import copy
import json
import os
import sys

import pytest

from amplifier_web import app_updates
from amplifier_web.service import AppService
from amplifier_web.update_diagnostics import CommandFailure, CommandOutput, CommandTimeout
from amplifier_web.updates import UpdateManager
from test_app_updates import prepared_activation
from test_service import Runtime


@pytest.fixture(autouse=True)
def systemd_restart_contract(monkeypatch, tmp_path):
    # These command assertions describe systemd, independently of the test host.
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr('amplifier_web.deployment_service.unit_path',lambda:tmp_path/'unit.service')


async def restart_fixture(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service)
    service.update_manager=manager
    marker={'version':'99.0.0','revision':'a'*40,'attemptId':'b'*32,
            'sourceInstanceId':'c'*32,'requestedAt':1.0}
    service.state['updates'].update(phase='activating',pendingRestart=marker)
    manager.diagnostics.begin('application',marker['revision'],marker['attemptId'])
    return service,manager,marker


async def assert_retained_handoff(service, marker, status):
    updates=service.state['updates']
    assert updates['phase']=='activating'
    assert updates['pendingRestart']=={**marker,'requestStatus':status}
    saved=load_saved_state(service.db)
    assert saved['updates']['pendingRestart']==updates['pendingRestart']
    with pytest.raises(Exception,match='update is activating'):
        await service.dispatch('conversation.send',{'text':'Do not admit work during handoff'})
    assert not any(row['phase']=='restart-ack' for row in updates['diagnostics']['events'])


async def test_accepted_restart_is_queued_not_acknowledged(tmp_path,monkeypatch):
    service,manager,marker=await restart_fixture(tmp_path)
    calls=[]
    async def command(*args,**kwargs):
        calls.append((args,kwargs))
        assert service.state['updates']['pendingRestart']['requestStatus']=='requesting'
        return CommandOutput('',{'exitCode':0,'durationMs':12})
    monkeypatch.setattr(app_updates,'process',command)
    try:
        await app_updates.request_managed_restart(manager)
        assert calls==[(('systemctl','--user','--no-block','restart','amplifier-unified.service'),{'timeout':30})]
        await assert_retained_handoff(service,marker,'accepted')
        assert manager.diagnostics.state['latest']['status']=='accepted'
        assert not service.state['updates'].get('error')
        assert 'become ready' in service.state['updates']['detail']
    finally:
        await service.close()


async def test_legacy_unit_reloaded_before_restart_dispatch(tmp_path,monkeypatch):
    from amplifier_web import deployment_service
    service,manager,marker=await restart_fixture(tmp_path)
    unit=deployment_service.unit_path()
    unit.write_text(deployment_service.MARKER+'\n[Service]\nExecStart=/owned/amplifier-unified --no-open --data-dir '+str(tmp_path)+' --workspace /workspace serve\n')
    calls=[]
    monkeypatch.setattr(deployment_service,'_systemctl',lambda *a,**k:calls.append((a,k)))
    async def command(*args,**kwargs):
        assert calls==[(('daemon-reload',),{'timeout':30})]
        assert 'KillMode=mixed\n' in unit.read_text()
        calls.append((args,kwargs))
        return CommandOutput('',{'exitCode':0})
    monkeypatch.setattr(app_updates,'process',command)
    try:
        await app_updates.request_managed_restart(manager)
        await assert_retained_handoff(service,marker,'accepted')
        assert len(calls)==2
    finally:
        await service.close()


@pytest.mark.parametrize('error',[RuntimeError('reload failed'),TimeoutError(),asyncio.CancelledError()])
async def test_shutdown_preparation_failure_never_dispatches_restart(tmp_path,monkeypatch,error):
    service,manager,marker=await restart_fixture(tmp_path)
    def prepare(home):raise error
    monkeypatch.setattr('amplifier_web.deployment_service.prepare_managed_shutdown',prepare)
    async def command(*args,**kwargs):pytest.fail('No restart after failed preparation')
    monkeypatch.setattr(app_updates,'process',command)
    try:
        if isinstance(error,asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await app_updates.request_managed_restart(manager)
        else:
            await app_updates.request_managed_restart(manager)
        await assert_retained_handoff(service,marker,'rejected')
        assert 'service restart' in service.state['updates']['error']
    finally:
        await service.close()


@pytest.mark.parametrize('error',[
    CommandFailure({'exitCode':-15}),
    CommandFailure({'exitCode':-9}),
    CommandTimeout(30),
    TimeoutError(),
    RuntimeError('ambiguous transport failure'),
    asyncio.CancelledError(),
])
async def test_ambiguous_restart_keeps_identity_and_gate_closed(tmp_path,monkeypatch,error):
    service,manager,marker=await restart_fixture(tmp_path)
    async def command(*args,**kwargs):raise error
    monkeypatch.setattr(app_updates,'process',command)
    try:
        if isinstance(error,asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await app_updates.request_managed_restart(manager)
        else:
            await app_updates.request_managed_restart(manager)
        await assert_retained_handoff(service,marker,'uncertain')
        assert manager.diagnostics.state['latest']['status']=='uncertain'
        assert not service.state['updates'].get('error')
        assert 'confirmation was interrupted' in service.state['updates']['detail']
        if isinstance(error,CommandFailure):
            assert manager.diagnostics.state['latest']['exitCode']==error.diagnostic_facts['exitCode']
    finally:
        await service.close()


@pytest.mark.parametrize('error',[
    CommandFailure({'exitCode':1}),
    CommandFailure({'exitCode':5}),
    FileNotFoundError('missing systemctl'),
    PermissionError('not executable'),
])
async def test_rejected_request_is_visible_without_discarding_restart_identity(tmp_path,monkeypatch,error):
    service,manager,marker=await restart_fixture(tmp_path)
    async def command(*args,**kwargs):raise error
    monkeypatch.setattr(app_updates,'process',command)
    try:
        await app_updates.request_managed_restart(manager)
        await assert_retained_handoff(service,marker,'rejected')
        assert manager.diagnostics.state['lastFailure']['phase']=='service-restart-request'
        assert 'request was rejected' in service.state['updates']['error']
        assert 'amplifier-unified service restart' in service.state['updates']['error']
    finally:
        await service.close()


@pytest.mark.skipif(os.name=='nt',reason='POSIX signal return codes')
async def test_real_sigterm_child_is_an_unconfirmed_handoff(tmp_path,monkeypatch):
    service,manager,marker=await restart_fixture(tmp_path)
    original_process=app_updates.process
    async def command(*args,**kwargs):
        return await original_process(sys.executable,'-c','import os, signal; os.kill(os.getpid(), signal.SIGTERM)',timeout=5)
    monkeypatch.setattr(app_updates,'process',command)
    try:
        await app_updates.request_managed_restart(manager)
        await assert_retained_handoff(service,marker,'uncertain')
        assert manager.diagnostics.state['latest']['exitCode']==-15
    finally:
        await service.close()


@pytest.mark.parametrize('error',[CommandFailure({'exitCode':-15}),CommandTimeout(30)])
async def test_managed_activation_never_falls_back_to_an_unmanaged_host(tmp_path,monkeypatch,error):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):
        if args[0]=='systemctl':raise error
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home:True)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',lambda *args,**kwargs:pytest.fail('no detached helper under systemd'))
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('only systemd owns managed host termination'))
    try:
        await app_updates.activate(manager)
        marker=service.state['updates']['pendingRestart']
        assert marker['sourceInstanceId']==manager.running_identity['instanceId']
        assert marker['requestedAt']>0
        assert marker['requestStatus']=='uncertain'
        assert service.state['updates']['phase']=='activating'
    finally:
        await service.close()


async def assert_message_saved_during_failed_handoff(service, session, identity, text):
    from amplifier_web.session_projection import hydrate
    marker = copy.deepcopy(service.state['updates']['pendingRestart'])
    args = {'sessionId': session['id'], 'text': text}
    receipt = await service.dispatch('conversation.send', args, command_id=identity)
    assert receipt['accepted'] and receipt['delivery'] == 'queued_for_update'
    assert (receipt['sessionId'], receipt['inputId']) == (session['id'], identity)
    message, = [row for row in session['messages'] if row.get('inputId') == identity]
    assert message['role'] == 'user' and message['text'] == text
    assert message['delivery']['status'] == 'queued_for_update'
    assert message['delivery']['attemptId'] == service.state['updates']['drain']['attemptId']
    assert message['updateAdmission']['interruptionRevision'] == session.get('interruptionRevision', 0)
    saved = load_saved_state(service.db)
    hydrate(service.data_dir, saved, service.db)
    stored_session, = [row for row in saved['sessions'] if row['id'] == session['id']]
    stored_message, = [row for row in stored_session['messages'] if row.get('inputId') == identity]
    assert {key: stored_message[key] for key in ('id', 'inputId', 'role', 'text', 'delivery', 'updateAdmission')} == {
        key: message[key] for key in ('id', 'inputId', 'role', 'text', 'delivery', 'updateAdmission')}
    command = json.loads(service.db.execute('SELECT receipt FROM commands WHERE id=?', (identity,)).fetchone()[0])
    assert command['inputId'] == identity and command['delivery'] == 'queued_for_update'
    assert [session['id'], identity] in saved['updates']['drain']['messages']
    assert service.state['updates']['pendingRestart'] == saved['updates']['pendingRestart'] == marker
    assert not service.runtime.sent
    assert not any(row['phase'] == 'restart-ack' for row in service.state['updates']['diagnostics']['events'])
    with pytest.raises(Exception, match='update is activating'):
        await service.dispatch('conversation.retry', {'sessionId': session['id'], 'inputId': identity})
    assert not service.runtime.sent and service.state['updates']['pendingRestart'] == marker


async def test_unmanaged_helper_failure_preserves_installed_handoff(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):return '99.0.0' if '-c' in args else ''
    async def spawn(*args,**kwargs):raise OSError('Cannot start helper')
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home:False)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('failed helper must not terminate the retained host'))
    try:
        await service.dispatch('session.create', {'title': 'Synthetic handoff message fixture'})
        session = service._session()
        await app_updates.activate(manager)
        assert service.state['updates']['phase']=='activating'
        assert service.state['updates']['pendingRestart']['version']=='99.0.0'
        assert 'helper could not start' in service.state['updates']['error']
        await assert_message_saved_during_failed_handoff(service, session, 'helper-failure-message',
                                                       'Do not admit work after helper failure')
    finally:
        await service.close()


@pytest.mark.parametrize('failure_phase',['restart-configuration','restart-helper-file'])
async def test_postreplacement_helper_setup_failure_keeps_work_paused(tmp_path,monkeypatch,failure_phase):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home:False)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('failed helper setup must not terminate host'))
    def invalid_arguments(*args):raise ValueError('invalid fixture restart settings')
    original_write=app_updates.write_private
    def unavailable_helper(path,*args,**kwargs):
        if path.name=='restart.py':raise PermissionError('fixture helper storage unavailable')
        return original_write(path,*args,**kwargs)
    if failure_phase=='restart-configuration':
        monkeypatch.setattr(app_updates,'restart_arguments',invalid_arguments)
    else:
        monkeypatch.setattr(app_updates,'write_private',unavailable_helper)
    try:
        await service.dispatch('session.create', {'title': 'Synthetic handoff setup fixture'})
        session = service._session()
        await manager.command('app')
        state=service.state['updates']
        assert state['phase']=='activating'
        assert state['pendingRestart']['version']=='99.0.0'
        assert state['diagnostics']['lastFailure']['phase']==failure_phase
        assert state['error']
        await assert_message_saved_during_failed_handoff(service, session, 'setup-failure-message',
                                                       'Cannot use outgoing process after replacement')
    finally:
        await service.close()


@pytest.mark.parametrize('action,args',[
    ('conversation.send',{'text':'Do not admit new work'}),
    ('worker.spawn',{'instruction':'Do not admit new work'}),
    ('worker.steer',{'id':'fixture-worker','text':'Do not admit new work'}),
    ('call.start',{}),
    ('feedback.submit',{'requestId':'fixture-request','title':'Fixture','body':'Do not submit','category':'bug'}),
    ('smartTools.connect',{'id':'fixture-server'}),
])
async def test_pending_restart_blocks_work_even_if_phase_reports_error(tmp_path,action,args):
    service,manager,_=await restart_fixture(tmp_path)
    service.state['updates'].update(phase='error',error='Restart needs attention')
    try:
        with pytest.raises(Exception,match='update is activating'):
            await service.dispatch(action,args)
        assert service.state['updates']['pendingRestart']
    finally:
        await service.close()
