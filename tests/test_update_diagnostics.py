import json
import errno
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from amplifier_web import app_updates
from amplifier_web.updates import process,UpdateManager
from amplifier_web.update_diagnostics import CommandFailure,CommandTimeout,PROBE_PREFIX,probe_record
from amplifier_web.service import AppService
from test_service import Runtime
from test_app_updates import prepared_activation
from test_runtime_ref_identity import normalized_runtime


@pytest.mark.parametrize('code, reason', [(errno.ENOSPC, 'disk-full'), (errno.EDQUOT, 'disk-quota-exceeded'),
                                          (errno.EACCES, 'storage-permission-denied')])
async def test_background_os_failure_identifies_cause_without_private_text(tmp_path, monkeypatch, code, reason):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    waits = 0
    async def wait(delay):
        nonlocal waits
        waits += 1
        if waits > 1:
            manager.closed = True
    async def fail():
        raise OSError(code, 'private-provider-secret', '/private/user/path')
    monkeypatch.setattr(manager, 'wait_for_work', wait)
    monkeypatch.setattr(manager, 'tick', fail)
    await manager.loop()
    receipt = service.state['updates']['diagnostics']['lastFailure']
    assert receipt['osError'] == errno.errorcode[code]
    assert receipt['reason'] == reason
    assert receipt['phase'] == 'background-update'
    assert 'private-provider-secret' not in json.dumps(service.state['updates'])
    assert '/private/user/path' not in json.dumps(service.state['updates'])
    assert 'Review its diagnostic receipt' not in service.state['updates']['error']
    await service.close()


async def test_operation_and_probe_preserve_os_cause_in_sanitized_receipt(tmp_path):
    from amplifier_web.update_diagnostics import probe_failure
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    def fail():
        raise OSError(errno.ENOSPC, 'private-output', '/private/file')
    with pytest.raises(OSError) as caught:
        manager.diagnostics.sync('candidate-record', fail)
    assert manager.diagnostics.state['lastFailure']['osError'] == 'ENOSPC'
    caught.value.diagnostic_facts = {'private': '/private/credential', 'reason': 'private-value'}
    raw_probe = probe_failure(caught.value, 'prepare')
    assert 'private' not in json.dumps(raw_probe)
    parsed = probe_record(PROBE_PREFIX + json.dumps(raw_probe))
    assert parsed['reason'] == 'disk-full' and parsed['osError'] == 'ENOSPC'
    assert 'private' not in json.dumps(parsed)
    await service.close()


async def test_subprocess_failure_retains_safe_classification_not_output(tmp_path):
    secret='fixture-sensitive-credential'
    code="import sys; print('"+secret+"'); sys.stderr.write('ImportError: '+ '"+secret+"'); raise SystemExit(17)"
    with pytest.raises(CommandFailure) as caught:await process(sys.executable,'-c',code)
    facts=caught.value.diagnostic_facts
    assert facts['exitCode']==17 and facts['errorType']=='ImportError'
    assert facts['stdoutBytes']>0 and facts['stderrBytes']>0
    assert secret not in json.dumps(facts)+str(caught.value)


async def test_command_timeout_has_distinct_diagnostic():
    with pytest.raises(CommandTimeout) as caught:
        await process(sys.executable,'-c','import time; time.sleep(30)',timeout=.05)
    assert caught.value.diagnostic_facts['timedOut'] is True
    assert caught.value.diagnostic_facts['errorType']=='TimeoutError'


def test_probe_frame_filters_private_fields_and_nonversion_output():
    raw=PROBE_PREFIX+json.dumps({'ok':True,'version':'0.6.4','stage':'complete','private':'secret','path':'/private/path','errorType':'secret'})
    assert probe_record('provider warning\n'+raw)=={'ok':True,'version':'0.6.4','stage':'complete'}
    assert probe_record(PROBE_PREFIX+'not-json') is None
    assert probe_record(PROBE_PREFIX+json.dumps({'ok':False,'stage':[],'errorType':{},'version':[]}))=={'ok':False}


async def test_failed_replacement_probe_has_phase_and_correlation_without_termination(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):
        if '-c' in args:
            raise CommandFailure({'exitCode':1,'durationMs':42,'stdoutBytes':93,'stderrBytes':0,
                'probe':{'ok':False,'stage':'imports','errorType':'ModuleNotFoundError','private':'secret'}})
        return ''
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('no termination'))
    await app_updates.activate(manager)
    updates=service.state['updates'];failure=updates['diagnostics']['lastFailure']
    assert failure['phase']=='replacement-probe' and failure['exitCode']==1
    assert failure['attemptId'] and failure['commandId']
    assert failure['probe']['errorType']=='ModuleNotFoundError'
    assert 'replacement probe' in updates['error']
    await service.diagnostics.flush()
    text=json.dumps(service.diagnostics.read(stream='updates'))
    assert 'secret' not in text and '/fixture' not in text
    assert service.diagnostics.path.stat().st_mode&0o777==0o600
    assert updates['pendingApp'] is None
    await service.close()


async def test_framed_probe_accepts_warnings_and_reports_actual_mismatch(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    manager.diagnostics.begin('application','a'*40)
    output='warning: third-party startup message\n'+PROBE_PREFIX+json.dumps({'ok':True,'version':'99.0.0','stage':'complete'})
    assert app_updates.verified_version(manager,output,'v99.0.0','candidate-version')=='99.0.0'
    with pytest.raises(ValueError):app_updates.verified_version(manager,output,'98.0.0','candidate-version')
    assert manager.diagnostics.state['lastFailure']['observedVersion']=='99.0.0'
    assert manager.diagnostics.state['lastFailure']['expectedVersion']=='98.0.0'
    await service.close()


async def test_restart_success_clears_current_failure_and_preserves_history(tmp_path,monkeypatch):
    from amplifier_web import __version__
    from amplifier_web.auth import data_identity
    identity={'version':__version__,'revision':'a'*40,'instanceId':'c'*32}
    monkeypatch.setattr('amplifier_web.update_readiness.running_identity',lambda:dict(identity))
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    failure={'phase':'replacement-probe','status':'failed'}
    service.state['updates']={'error':'prior failure','diagnostics':{'lastFailure':failure,'events':[failure]},
        'pendingRestart':{'version':__version__,'revision':'a'*40,'attemptId':'b'*32}}
    manager=UpdateManager(service);service.update_manager=manager
    updates=service.state['updates']
    assert updates['error']=='prior failure' and updates['phase']=='activating'
    assert updates['diagnostics']['lastFailure']==failure
    assert await manager.confirm_readiness({'ok':True,'app':'amplifier-unified',
        'dataIdentity':data_identity(tmp_path),**identity})
    assert updates['error'] is None and 'lastFailure' not in updates['diagnostics']
    assert updates['diagnostics']['events'][0]==failure
    assert updates['diagnostics']['latest']['phase']=='restart-ack'
    assert updates['diagnostics']['latest']['attemptId']=='b'*32
    await service.close()


async def test_collector_and_bounded_update_journal_receive_only_sanitized_original(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path);received=[]
    service.diagnostics.record=lambda *args:received.append(args)
    manager=UpdateManager(service);service.update_manager=manager
    manager.diagnostics.begin('application','a'*40)
    manager.diagnostics.record('candidate-install','failed',errorType='RuntimeError',stdout='secret')
    assert received[0][0]=='updates' and received[0][1]['event']=='update:candidate-install'
    assert 'secret' not in json.dumps(received)
    retained=[json.loads(line) for line in (manager.directory/'diagnostics.jsonl').read_text().splitlines()]
    assert retained == [received[0][1]['data']]
    assert 'secret' not in json.dumps(retained)
    await service.close()


async def test_candidate_install_failure_cannot_replace_host_or_become_generic(tmp_path,monkeypatch):
    monkeypatch.setattr(app_updates.components,'installed_graph',lambda:[{'name':'amplifier-core','version':'1.6.1'}])
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['application']={'status':'update','revision':'a'*40,'latest':'99.0.0'}
    monkeypatch.setattr(app_updates.shutil,'which',lambda name:'/fixture/tool')
    async def command(*args,**kwargs):raise CommandFailure({'exitCode':2,'durationMs':1,'stdoutBytes':0,'stderrBytes':7})
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr(app_updates,'installed_target',lambda:pytest.fail('no replacement after failed staging'))
    await manager.command('app')
    assert service.state['updates']['diagnostics']['lastFailure']['phase']=='candidate-install'
    assert 'candidate install' in service.state['updates']['error']
    assert not (manager.directory/'previous-app.json').exists()
    await service.close()


async def test_restart_helper_failure_keeps_host_and_distinguishes_installed_package(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    private_detail='fixture-restart-launch-secret-4ad670'
    async def command(*args,**kwargs):return '99.0.0' if '-c' in args else ''
    async def spawn(*args,**kwargs):raise PermissionError(private_detail)
    monkeypatch.setattr(app_updates,'process',command)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('no termination'))
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home:False)
    await app_updates.activate(manager)
    failure=service.state['updates']['diagnostics']['lastFailure']
    assert failure['phase']=='restart-helper' and failure['errorType']=='PermissionError'
    assert 'installed' in service.state['updates']['error']
    assert service.state['updates']['phase']=='activating'
    assert service.state['updates']['pendingRestart']['version']=='99.0.0'
    # Match the private diagnostic, not ordinary words in public release notes.
    assert private_detail not in json.dumps(service.state['updates'])
    await service.close()


async def test_cancelled_application_replacement_clears_automatic_pending_retry(tmp_path,monkeypatch):
    import asyncio
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    async def command(*args,**kwargs):raise asyncio.CancelledError()
    monkeypatch.setattr(app_updates,'process',command)
    with pytest.raises(asyncio.CancelledError):await app_updates.activate(manager)
    updates=service.state['updates']
    assert updates['phase']=='interrupted' and updates['pendingApp'] is None
    assert updates['diagnostics']['lastFailure']['phase']=='replacement-install'
    assert updates['diagnostics']['lastFailure']['status']=='interrupted'
    await service.close()


from test_updates import app, repo, prepare


@pytest.mark.parametrize('kind', ['protected', 'timeout', 'cancelled'])
async def test_runtime_preflight_failure_is_attributed_and_closes_parent(app, repo, monkeypatch, kind):
    import asyncio
    from amplifier_web import runtime_environment
    manager, cache = await prepare(app, repo)
    sessions = json.loads(json.dumps(app.state['sessions']))
    async def fail(home):
        if kind == 'protected':
            raise runtime_environment.ProtectedRuntimeSource('amplifier-legacy-hooks')
        if kind == 'timeout':
            raise CommandTimeout(12)
        raise asyncio.CancelledError()
    monkeypatch.setattr(runtime_environment, 'update_manifest', fail)
    if kind == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            await manager.install()
    else:
        await manager.install()
    diagnostics = app.state['updates']['diagnostics']
    failure = diagnostics['lastFailure']
    assert failure['phase'] == 'ecosystem-runtime-preflight'
    assert failure['commandId'] and failure['attemptId']
    assert failure['status'] == ('interrupted' if kind == 'cancelled' else 'failed')
    assert 'runtime preflight' in app.state['updates']['error']
    assert any(row['phase'] == 'ecosystem-checkout' and row['status'] == 'succeeded' for row in diagnostics['events'])
    if kind == 'protected':
        assert failure['reason'] == 'protected-runtime-source'
        assert failure['package'] == 'amplifier-legacy-hooks'
    if kind == 'timeout':
        assert failure['timedOut'] and failure['durationMs'] == 12000
    parent = [row for row in diagnostics['events'] if row['phase'] == 'ecosystem-stage']
    assert [row['status'] for row in parent] == ['started', failure['status']]
    assert parent[0]['commandId'] == parent[1]['commandId']
    assert app.state['sessions'] == sessions and app.runtime.closed == 0
    assert not (app.data_dir / 'updates/active.json').exists()


async def test_recovery_details_exclude_unknown_reason_package_paths_and_exception_text(tmp_path):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    async def fail():
        error = ValueError('private credential-bearing exception message')
        error.diagnostic_facts = {'reason': 'private reason', 'package': 'https://user:secret@example.invalid/module',
                                  'stdout': 'secret', 'path': '/private/local/cache'}
        raise error
    with pytest.raises(ValueError):
        await manager.diagnostics.run('ecosystem-runtime-preflight', fail)
    failure = manager.diagnostics.state['lastFailure']
    assert failure['errorType'] == 'ValueError'
    assert not {'reason', 'package', 'stdout', 'path'} & failure.keys()
    assert 'secret' not in json.dumps(failure) and 'private' not in json.dumps(failure)
    await service.close()


@pytest.mark.parametrize('changes,fields', [
    ({'revision': 'b' * 40}, ['revision']),
    ({'repository': 'https://other.invalid/private-repository'}, ['repository']),
    ({'ref': 'private-other-ref'}, ['ref']),
    ({'subdirectory': 'private/other/module'}, ['subdirectory']),
    ({'repository': 'https://other.invalid/private-repository', 'revision': 'b' * 40},
     ['repository', 'revision']),
    (None, ['source']),
])
async def test_resolution_failure_receipt_identifies_mismatch_without_source_values(app, repo, changes, fields):
    from urllib.parse import urlencode, urlsplit
    from amplifier_web.runtime_environment import verify_resolution
    manager, cache = await prepare(app, repo)
    expected = {'repository': 'https://user:private-token@example.invalid/private-repository',
                'ref': 'private-ref', 'subdirectory': 'private/module', 'revision': 'a' * 40}
    observed = {**expected, **(changes or {})}
    resolved = None if changes is None else urlsplit(
        observed['repository'] + '?' + urlencode({'rev': observed['ref'], 'subdirectory': observed['subdirectory']})
        + '#' + observed['revision'])
    async def validate(*args):
        verify_resolution(resolved, {'package': 'component-example', 'url': expected['repository'],
                                     'ref': expected['ref'], 'subdirectory': expected['subdirectory'],
                                     'latest': expected['revision']})
    manager.validate = validate
    sessions = json.loads(json.dumps(app.state['sessions']))
    await manager.install()
    failure = manager.diagnostics.state['lastFailure']
    assert failure['phase'] == 'ecosystem-validation'
    assert failure['errorType'] == 'ValueError'
    assert failure['reason'] == 'runtime-resolution-mismatch'
    assert failure['component'] == 'component-example'
    assert failure['resolutionMismatches'] == fields
    assert failure['expectedRevision'] == expected['revision']
    if changes is not None:
        assert failure['observedRevision'] == observed['revision']
    else:
        assert 'observedRevision' not in failure
    assert 'private' not in json.dumps(failure)
    assert 'example.invalid' not in json.dumps(failure)
    assert app.state['sessions'] == sessions and app.runtime.closed == 0
    assert not (app.data_dir / 'updates/active.json').exists()


async def test_resolution_receipt_filters_untrusted_values(tmp_path):
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    failure = manager.diagnostics.record('ecosystem-validation', 'failed',
        reason='runtime-resolution-mismatch', component='https://user:private-token@example.invalid/repo',
        resolutionMismatches=['revision', 'private-ref', {'path': '/private/file'}, 'revision'],
        expectedRevision='/private/file', observedRevision='private-token')
    assert failure['resolutionMismatches'] == ['revision']
    assert not {'component', 'expectedRevision', 'observedRevision'} & failure.keys()
    assert 'private' not in json.dumps(failure)
    await service.close()


@pytest.mark.parametrize('name,reason', [('ModuleActivationError','module-prepare-failed'), ('BundleLoadError','bundle-load-failed')])
def test_recognized_prepare_failure_keeps_class_and_guidance_without_exception_text(name, reason):
    from amplifier_web.update_diagnostics import probe_failure
    error = type(name, (Exception,), {})('secret source URL and local path')
    frame = PROBE_PREFIX + json.dumps({'stage': 'prepare', **probe_failure(error, 'prepare')})
    result = probe_record(frame)
    assert result == {'ok': False, 'stage': 'prepare', 'errorType': name, 'reason': reason}
    assert 'secret' not in frame
    assert 'reason' not in probe_failure(error, 'cleanup')


async def test_installed_batch_survives_new_attempt_event_eviction_and_restart(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    first=manager.diagnostics.begin('ecosystem','a'*32,tier='included',
                                    components=['foundation','foundation','https://user:secret@host/repo','/private/workspace'])
    manager.diagnostics.record('ecosystem-stage','succeeded')
    assert not manager.diagnostics.state.get('completedBatches')
    manager.diagnostics.record('ecosystem-activation','succeeded')
    manager.diagnostics.begin('ecosystem','b'*32,tier='other',components=['approval'])
    for _ in range(60):manager.diagnostics.record('ecosystem-prepare','succeeded')
    recent=manager.diagnostics.state['completedBatches']
    assert len(recent)==1 and recent[0]['attemptId']==first and recent[0]['components']==['foundation']
    assert 'secret' not in json.dumps(manager.diagnostics.state) and '/private/' not in json.dumps(manager.diagnostics.state)
    restored=UpdateManager(service)
    assert restored.diagnostics.state['completedBatches']==recent
    assert restored.diagnostics.state['batch']['components']==['approval']
    await service.close()


async def test_cleanup_cancellation_keeps_primary_failure_but_real_interruption_is_recorded(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    manager.diagnostics.begin('ecosystem','a'*32)
    primary=manager.diagnostics.record('ecosystem-prepare','failed',errorType='BundleNotFoundError')
    cleanup=manager.diagnostics.record('ecosystem-prepare','interrupted',errorType='CancelledError')
    assert manager.diagnostics.state['lastFailure']==primary
    assert cleanup in manager.diagnostics.state['events']
    manager.diagnostics.begin('ecosystem','b'*32)
    interruption=manager.diagnostics.record('ecosystem-prepare','interrupted',errorType='CancelledError')
    assert manager.diagnostics.state['lastFailure']==interruption
    await service.close()


async def test_successful_peer_does_not_replace_the_receipt_for_failed_validation(app,repo):
    manager,_=await prepare(app,repo)
    primary=None
    async def fail(*args):
        nonlocal primary
        primary=manager.diagnostics.record('ecosystem-compatibility','failed',commandId='c'*32,
                                           errorType='BundleNotFoundError',exitCode=1)
        manager.diagnostics.record('ecosystem-compatibility','succeeded',commandId='d'*32)
        raise ValueError('validation failed')
    manager.validate=fail
    await manager.install()
    assert manager.diagnostics.state['lastFailure']==primary


async def test_rollback_does_not_mark_the_failed_new_batch_installed(app,repo):
    manager,_=await prepare(app,repo)
    async def validate(*args):pass
    manager.validate=validate
    await manager.install()
    installed=list(manager.diagnostics.state['completedBatches'])
    failed=manager.diagnostics.begin('ecosystem','f'*32,tier='other',components=['never-installed'])
    manager.diagnostics.record('ecosystem-stage','failed')
    await manager.rollback()
    assert manager.diagnostics.state['completedBatches']==installed
    assert manager.diagnostics.state['latest']['phase']=='ecosystem-rollback'
    assert manager.diagnostics.state['attemptId']!=failed


async def test_partial_tool_staging_lists_only_the_tools_that_activated(app,monkeypatch):
    manager=app.update_manager
    manager.inventory=[{'kind':'smart tool','status':'update','eligible':True,'label':name,'installationId':name}
                       for name in ['tool-a','tool-b']]
    async def stage(row):
        if row['label']=='tool-b':raise ValueError('could not stage')
        return {'id':'new-'+row['label']}
    async def activate(previous,target):return {}
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools=SmartToolsManager(app)
    monkeypatch.setattr(app.smart_tools,'stage_update',stage)
    monkeypatch.setattr(app.smart_tools,'activate_update',activate)
    await manager.installSmartTools()
    assert len(app.state['updates']['pendingSmartTools'])==1
    assert app.state['updates']['phase']=='error'
    assert app.state['updates']['pendingSmartTools'][0]['previous']=='tool-b'
    completed=manager.diagnostics.state['completedBatches'][-1]
    assert completed['phase']=='smart-tools-partial'
    assert completed['components']==['tool-a'] and completed['componentCount']==1


def test_prepare_value_errors_keep_safe_location_and_actionable_reason():
    from amplifier_web.host.config import expand_environment
    from amplifier_web.host.session import live_plan
    from amplifier_web.update_diagnostics import probe_failure
    cases = [(lambda: expand_environment('${PRIVATE_TOKEN}', environment={}),
              'missing-configured-environment', 'amplifier_web.host.config:substitute:'),
             (lambda: live_plan({'session': {'orchestrator': {'module': 'private-custom-loop'}}}),
              'incompatible-conversation-loop', 'amplifier_web.host.session:live_plan:')]
    for operation, reason, location in cases:
        try:
            operation()
        except ValueError as error:
            facts = probe_failure(error, 'prepare')
        assert facts['reason'] == reason
        assert facts['failureLocation'].startswith(location)
        result = probe_record(PROBE_PREFIX + json.dumps({'stage': 'prepare', 'profileIndex': 2, **facts}))
        assert result['failureLocation'] == facts['failureLocation']
        assert result['profileIndex'] == 2
        assert 'PRIVATE_TOKEN' not in json.dumps(result)
        assert 'private-custom-loop' not in json.dumps(result)


def test_probe_location_rejects_paths_messages_and_nonlibrary_frames():
    for value in ['/home/private/settings.yaml:1', 'https://user:secret@host/file',
                  'private_project:run:1', 'amplifier_web.host:run:1 secret', {}, None]:
        result = probe_record(PROBE_PREFIX + json.dumps({'ok': False, 'failureLocation': value}))
        assert result == {'ok': False}


async def test_installed_source_mismatch_has_safe_actionable_receipt(tmp_path):
    from amplifier_web.update_diagnostics import failure_guidance
    from amplifier_web.update_report import event
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    async def fail():
        error = ValueError('private source path /private/cache with credential')
        error.diagnostic_facts = {
            'reason': 'runtime-installed-source-mismatch', 'package': 'amplifier-foundation',
            'resolutionMismatches': ['revision', 'private/path', 'revision', {'secret': 'token'}],
            'expectedRevision': 'a' * 40, 'observedRevision': 'b' * 40,
            'stdout': 'private credential', 'path': '/private/cache',
        }
        raise error
    try:
        with pytest.raises(ValueError):
            await manager.diagnostics.run('ecosystem-validation', fail)
        receipt = manager.diagnostics.state['lastFailure']
        exported = event(receipt)
        assert receipt['reason'] == exported['reason'] == 'runtime-installed-source-mismatch'
        assert exported['package'] == 'amplifier-foundation'
        assert exported['resolutionMismatches'] == ['revision']
        assert exported['expectedRevision'] == 'a' * 40
        assert exported['observedRevision'] == 'b' * 40
        assert 'kept the current components active' in failure_guidance(receipt)
        assert 'private' not in json.dumps(receipt)
        assert 'credential' not in json.dumps(exported)
    finally:
        await service.close()


async def test_authored_constraint_has_specific_sanitized_guidance(tmp_path):
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    from amplifier_web.update_diagnostics import failure_guidance, probe_failure
    from amplifier_web.update_report import event
    service = AppService(tmp_path, Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    async def fail():
        verify_checked_selection({'amplifier-foundation': {'url': 'private'}},
            {'batchPaths': {'amplifier-foundation': '/private/source'},
             'packages': {'amplifier-foundation': {'status': 'authored-constraint'}}})
    try:
        with pytest.raises(ValueError) as caught:
            await manager.diagnostics.run('ecosystem-source-preflight', fail)
        exported = event(manager.diagnostics.state['lastFailure'])
        assert exported['reason'] == 'runtime-source-constraint'
        assert exported['errorType'] == 'ProtectedRuntimeSource'
        assert exported['package'] == 'amplifier-foundation'
        assert 'does not establish local edits' in failure_guidance(exported)
        assert 'private' not in json.dumps(exported)
        probe = probe_record(PROBE_PREFIX + json.dumps(probe_failure(caught.value, 'prepare')))
        assert probe['reason'] == 'runtime-source-constraint'
        assert probe['errorType'] == 'ProtectedRuntimeSource'
        assert probe['package'] == 'amplifier-foundation'
        assert 'private' not in json.dumps(probe)
    finally:
        await service.close()


async def test_real_freeze_mismatch_reaches_install_error_and_sanitized_report(normalized_runtime, monkeypatch):
    """Exercise install/validate/stage/freeze; only external resolver/probe is controlled."""
    from amplifier_web import updates, runtime_environment as environments
    from amplifier_web.update_report import event
    from test_runtime_ref_identity import ROW

    fixture_manager, _, _, graph = normalized_runtime
    runtime = Runtime()
    service = AppService(fixture_manager.home, runtime, workspace=fixture_manager.home.parent)
    manager = UpdateManager(service)
    service.update_manager = manager
    manager.inventory = [{**ROW, 'status': 'update', 'label': 'Fixture component'}]
    resolver = updates.process
    preparation_probes = []

    async def process(*args, **kwargs):
        if '--source-policy-only' in args:
            Path(args[args.index('--source-policy-only') + 1]).write_text(json.dumps({
                'packages': {}, 'batchPaths': {}, 'protectedPaths': []}))
        elif '--profiles' in args:
            preparation_probes.append(args)
            # The dynamic preparation finishes with a different installed commit.
            # Do not seed diagnostic facts: the production freeze guard must emit them.
            graph[0]['directUrl']['vcs_info']['commit_id'] = 'd' * 40
        return await resolver(*args, **kwargs)

    monkeypatch.setattr(updates, 'process', process)
    before = json.loads(json.dumps(service.state['sessions']))
    try:
        await manager.install()
        assert len(preparation_probes) == 1
        state = service.state['updates']
        failure = state['diagnostics']['lastFailure']
        assert state['phase'] == 'error' and state['pendingRelease'] is None
        assert failure['phase'] == 'ecosystem-validation'
        assert failure['reason'] == 'runtime-installed-source-mismatch'
        assert failure['resolutionMismatches'] == ['revision']
        assert failure['expectedRevision'] == ROW['latest']
        assert failure['observedRevision'] == 'd' * 40
        assert 'kept the current components active' in state['error']
        assert 'Check for an app update' in state['error']
        assert 'during ecosystem validation' not in state['error']
        assert event(failure)['resolutionMismatches'] == ['revision']
        exported = json.dumps(event(failure))
        assert ROW['url'] not in exported and str(fixture_manager.home) not in exported
        assert service.state['sessions'] == before
        assert not runtime.started and not runtime.stopped and not runtime.sent
        assert not (manager.directory / 'active.json').exists()
        receipt = environments.receipt_directory(manager.home, failure['revision'])
        assert not (receipt / 'runtime.lock').exists()
        assert not (receipt / 'runtime-installed.json').exists()
    finally:
        await service.close()
