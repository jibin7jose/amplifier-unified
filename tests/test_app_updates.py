from amplifier_web.state_records import load as load_saved_state
import asyncio
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from amplifier_web import app_updates
from amplifier_web.service import AppError, AppService
from amplifier_web.updates import UpdateManager,work_paused
from test_service import Runtime


@pytest.fixture(autouse=True)
def base_installation(monkeypatch):
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    monkeypatch.setattr(app_updates.components, 'installed_graph', lambda: [{'name':'amplifier-core','version':'1.6.1'}])
    monkeypatch.setattr(app_updates.components, 'updates', AsyncMock(return_value=[]))
    monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=[
        {'name':'amplifier-unified','version':'99.0.0','url':app_updates.SOURCE,'revision':'a'*40}]))


def test_packaging_probe_rejects_broken_login_dependency(tmp_path):
    import os
    import subprocess
    import sys

    # Model the observed wheel's undeclared import in a fresh child process.
    # A candidate with a usable server but broken PAM must not replace the host.
    (tmp_path / 'pam.py').write_text('raise ModuleNotFoundError("missing PAM dependency")\n')
    # Keep the app under test importable when this checkout uses dependencies
    # from a separate environment. Otherwise an unrelated namespace package can
    # fail before the deliberately broken login dependency is even reached.
    source = str(Path(app_updates.__file__).resolve().parent.parent)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(
        [str(tmp_path), source, os.environ.get('PYTHONPATH', '')])}
    result = subprocess.run([sys.executable, '-c', app_updates.PROBE],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    from amplifier_web.update_diagnostics import probe_record
    assert probe_record(result.stdout)['errorType']=='ModuleNotFoundError'
    assert 'missing PAM dependency' not in result.stdout+result.stderr


@pytest.mark.parametrize("bootstrap_kind", ["current", "web-only", "leaking", "missing-operations"])
def test_candidate_probe_checks_worker_import_boundary(tmp_path, bootstrap_kind):
    import shutil
    import subprocess
    import sys
    from amplifier_web.update_diagnostics import probe_record

    # The simulated host sees the app-owned sibling packages. Only the clean child can
    # distinguish the original bootstrap defect from a healthy host import.
    source = Path(app_updates.__file__).parent
    package = tmp_path / "amplifier_web"
    package.mkdir()
    (package / "__init__.py").write_text('__version__="99.0.0"\n')
    (package / "server.py").write_text("def create_app(): pass\n")
    (package / "static").mkdir()
    (package / "static/index.html").write_text("")
    ui_use = tmp_path / "amplifier_ui_use"
    ui_use.mkdir()
    (ui_use / "__init__.py").write_text("")
    (ui_use / "browser.py").write_text("def observation_for(*args): pass\n")
    shutil.copy2(source / "runtime_bootstrap.py", package)
    shutil.copy2(source / "collaboration_input.py", package)
    shutil.copy2(source / "task_binding.py", package)
    shutil.copytree(source.parent / "amplifier_operations", tmp_path / "amplifier_operations")
    shutil.copytree(source.parent / "amplifier_kernels", tmp_path / "amplifier_kernels")
    (tmp_path / "pam.py").write_text("def authenticate(*args): return True\n")
    metadata = tmp_path / "amplifier_unified-99.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: amplifier-unified\nVersion: 99.0.0\n")
    if bootstrap_kind == "web-only":
        path = package / "runtime_bootstrap.py"
        original = path.read_text()
        broken = original.replace(
            '("amplifier_web", "amplifier_operations", "amplifier_kernels")', '("amplifier_web",)'
        )
        assert broken != original, "The web-only fault must change the current bootstrap"
        path.write_text(broken)
    elif bootstrap_kind == "leaking":
        path = package / "runtime_bootstrap.py"
        path.write_text(path.read_text().replace(
            '    app_root = Path(__file__).resolve().parent.parent',
            '    app_root = Path(__file__).resolve().parent.parent\n    sys.path.insert(0, str(app_root))',
        ))
    elif bootstrap_kind == "missing-operations":
        shutil.rmtree(tmp_path / "amplifier_operations")
    runner = (
        "import sys\n"
        "sys.path.insert(0,sys.argv[1])\n"
        "sys.prefix=sys.argv[1]\n"
        "sys.argv=sys.argv[:1]\n"
        "exec(" + repr(app_updates.PROBE) + ")\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", runner, str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    record = probe_record(result.stdout)
    if bootstrap_kind == "current":
        assert result.returncode == 0, result.stderr + result.stdout
        assert record["ok"] and record["workerImportsAvailable"]
    else:
        assert result.returncode != 0
        assert record["stage"] == "worker-imports" and record["errorType"] == "AssertionError"
        assert "workerImportsAvailable" not in record
        assert "Traceback" not in result.stdout + result.stderr
        assert str(tmp_path) not in result.stdout + result.stderr


async def test_release_check_requires_real_tag_revision(monkeypatch):
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/bin/tool')
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        if args[0]=='gh':return json.dumps({'tag_name':'v99.0.0','html_url':'https://github.com/example/release'})
        return 'a'*40+'\trefs/tags/v99.0.0\n'+'b'*40+'\trefs/tags/v99.0.0^{}'
    monkeypatch.setattr(app_updates,'process',process)
    async def metadata(path, **kwargs):
        return await app_updates.process('gh', 'api', path)
    monkeypatch.setattr(app_updates, 'github_read', metadata)
    result=await app_updates.check()
    assert result['status']=='update' and result['revision']=='b'*40
    assert len(calls)==3
    assert calls[-1][-1].endswith('?ref='+'b'*40)
    assert result['releaseNotesWarning']
    async def fail(*args,**kwargs):raise RuntimeError('private credential detail')
    monkeypatch.setattr(app_updates,'process',fail)
    result=await app_updates.check()
    assert result['status']=='check_failed' and 'credential' not in result['detail']

async def test_candidate_isolated_validated_and_busy_host_not_replaced(tmp_path,monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['application']={'status':'update','revision':'a'*40,'latest':'v99.0.0'}
    calls=[]
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/bin/tool')
    async def process(*args,**kwargs):
        calls.append((args,kwargs))
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    await app_updates.stage(manager)
    assert calls[0][1]['env']['UV_TOOL_DIR'].startswith(str(tmp_path/'updates/applications'))
    assert service.state['updates']['pendingApp']['revision']=='a'*40
    service._session()['status']='working'
    await app_updates.activate(manager)
    assert len(calls)==2
    assert service.state['updates']['phase']=='app-staged'
    await service.close()

async def test_unvalidated_app_cannot_replace_installed_host(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['pendingApp']={'revision':'a'*40}
    with pytest.raises(ValueError,match='validation'):await app_updates.activate(manager)
    await service.close()


async def test_activation_queued_across_shutdown_snapshot_does_not_discover_or_install(tmp_path, monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    revision='a'*40
    service.state['updates']['pendingApp']={'revision':revision,'latest':'v99.0.0'}
    marker=manager.directory/'applications'/revision/'validated.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'revision':revision,'version':'99.0.0'}))
    start=asyncio.Event()
    target=AsyncMock(side_effect=AssertionError('closed host must not discover an installation target'))
    installer=AsyncMock(side_effect=AssertionError('closed host must not install an application update'))
    monkeypatch.setattr(app_updates,'installed_target',target)
    monkeypatch.setattr(app_updates,'process',installer)

    async def queued_activation():
        await start.wait()
        await app_updates.activate(manager)

    await service.runtime_lifecycle_lock.acquire()
    activation=asyncio.create_task(queued_activation())
    await asyncio.sleep(0)
    shutdown=asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert service.closed and not shutdown.done()
    start.set()
    with pytest.raises(RuntimeError,match='host is closing'):
        await activation
    target.assert_not_awaited()
    installer.assert_not_awaited()
    service.runtime_lifecycle_lock.release()
    await asyncio.wait_for(shutdown,3)


@pytest.mark.parametrize("marker_kind", ["missing", "corrupt-json", "list", "null", "number", "string",
                                        "wrong-revision", "parent-is-file"])
async def test_invalid_validation_marker_fails_before_activation_side_effects(tmp_path, monkeypatch, marker_kind):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    revision='a'*40
    service.state['updates']['pendingApp']={'revision':revision,'latest':'v99.0.0'}
    marker=manager.directory/'applications'/revision/'validated.json'
    contents={'corrupt-json':'{','list':'[]','null':'null','number':'42','string':'"text"',
              'wrong-revision':json.dumps({'revision':'b'*40,'version':'99.0.0'})}
    if marker_kind=='parent-is-file':
        marker.parent.parent.mkdir(parents=True,exist_ok=True)
        marker.parent.write_text('not a directory')
    elif marker_kind in contents:
        marker.parent.mkdir(parents=True,exist_ok=True)
        marker.write_text(contents[marker_kind])
    target=AsyncMock(side_effect=AssertionError('must not inspect an installation for an invalid marker'))
    process=AsyncMock(side_effect=AssertionError('must not install or restart for an invalid marker'))
    close=AsyncMock()
    monkeypatch.setattr(app_updates,'installed_target',target)
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr(service.runtime,'close',close)
    try:
        with pytest.raises(ValueError,match='must pass isolated validation'):
            await app_updates.activate(manager)
        target.assert_not_awaited()
        process.assert_not_awaited()
        close.assert_not_awaited()
        assert service.state['updates']['phase']!='activating'
        assert not (manager.directory/'previous-app.json').exists()
    finally:
        await service.close()


async def test_development_host_cannot_overwrite_global_uv_tool(tmp_path,monkeypatch):
    monkeypatch.setattr(app_updates.shutil,'which',lambda name: str(tmp_path/'bin'/name))
    async def process(*args,**kwargs):return str(tmp_path/('bin' if '--bin' in args else 'tools'))
    monkeypatch.setattr(app_updates,'process',process)
    with pytest.raises(ValueError,match='not running from the active uv tool'):
        await app_updates.installed_target()

async def prepared_activation(tmp_path,monkeypatch):
    monkeypatch.setattr(app_updates, 'installed_extras', lambda: [])
    monkeypatch.setattr(app_updates.components, 'installed_graph', lambda: [{'name':'amplifier-core','version':'1.6.1'}])
    monkeypatch.setattr(app_updates.components, 'read_graph', AsyncMock(return_value=[
        {'name':'amplifier-unified','version':'99.0.0','url':app_updates.SOURCE,'revision':'a'*40}]))
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    service.port=8941
    manager=UpdateManager(service);service.update_manager=manager
    release={'status':'update','revision':'a'*40,'latest':'v99.0.0','attemptId':'d'*32}
    service.state['updates']['pendingApp']=release
    folder=manager.directory/'applications'/release['revision'];folder.mkdir(parents=True)
    (folder/'validated.json').write_text(json.dumps({'revision':release['revision'],'version':'99.0.0'}))
    candidate=folder/'tools/amplifier-unified/bin/python';candidate.parent.mkdir(parents=True);candidate.touch()
    async def target():return '/fixture/uv','/fixture/launcher',Path('/fixture/python'),{'version':'old','source':'file:///actual/previous/source'}
    monkeypatch.setattr(app_updates,'installed_target',target)
    return service,manager,candidate

async def test_manually_launched_host_with_installed_unit_uses_helper_and_termination(tmp_path,monkeypatch):
    service,manager,candidate=await prepared_activation(tmp_path,monkeypatch)
    await service.dispatch('session.create', {})
    calls=[]
    async def process(*args,**kwargs):
        assert service.state['updates']['phase']=='activating'
        calls.append(args)
        return '99.0.0' if '-c' in args else ''
    async def spawn(*args,**kwargs):
        assert service.state['updates']['phase']=='activating'
        assert args[0]==str(candidate)
        result = await service.dispatch('conversation.send', {'text': 'Must not start during restart'})
        assert result['delivery'] == 'queued_for_update' and not service.runtime.sent
        calls.append(('helper',))
    def kill(*args):
        assert service.state['updates']['phase']=='activating'
        calls.append(('terminate',))
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr('amplifier_web.deployment_service.managed_unit', lambda home: True)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed', lambda home: False)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(app_updates.os,'kill',kill)
    await app_updates.activate(manager)
    assert calls[-2:]==[('helper',),('terminate',)]
    assert json.loads((manager.directory/'previous-app.json').read_text())['source']=='file:///actual/previous/source'
    assert service.state['updates']['pendingRestart']['version']=='99.0.0'
    await service.close()


async def test_managed_service_restart_does_not_spawn_a_second_host(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr('amplifier_web.deployment_service.current_process_is_unit_managed',lambda home: True)
    monkeypatch.setattr(app_updates.asyncio,'create_subprocess_exec',lambda *args,**kwargs: pytest.fail('managed service must not spawn a helper'))
    monkeypatch.setattr(app_updates.os,'kill',lambda *args: pytest.fail('managed service must not terminate itself'))
    await app_updates.activate(manager)
    from amplifier_web.deployment_service import managed_restart_command
    assert calls[-1]==managed_restart_command()
    assert service.state['updates']['pendingRestart']['version']=='99.0.0'
    await service.close()


async def test_failed_installed_probe_retains_host_and_holds_work_until_verified(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    await service.dispatch('session.create', {})
    async def process(*args,**kwargs):return '98.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    monkeypatch.setattr(app_updates.os,'kill',lambda *args:pytest.fail('The retained host must not be terminated'))
    await app_updates.activate(manager)
    assert service.state['updates']['phase']=='error'
    assert service.state['updates']['pendingApp'] is None
    assert service.state['updates']['pendingReplacement']
    assert work_paused(service.state) and manager.awaiting_restart()
    result = await service.dispatch('conversation.send', {'text': 'Wait for exact installation verification'})
    assert result['delivery'] == 'queued_for_update' and not service.runtime.sent
    await service.close()

@pytest.mark.parametrize('receipt',[
    'private malformed receipt',
    ['private malformed receipt'],
    0,
    {},
    {'version':'0.0.1'},
    {'version':'0.0.1','revision':'a'*40},
])
async def test_invalid_restart_receipt_is_retired_but_uncertain_replacement_stays_held(tmp_path,monkeypatch,receipt):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    service.state['updates']={'phase':'activating','pendingRestart':receipt,'pendingApp':{'revision':'old'},
                              'pendingRelease':'c'*32}
    saves=0
    original_save=service._save
    def save():
        nonlocal saves
        saves+=1
        original_save()
    monkeypatch.setattr(service,'_save',save)
    manager=UpdateManager(service);service.update_manager=manager
    updates=service.state['updates']
    assert updates['phase']=='activating'
    assert updates['pendingRestart'] is None and updates['pendingApp'] is None
    assert updates['pendingReplacement']['unqualified'] is True
    assert updates['pendingRelease']=='c'*32
    assert 'No restart success was inferred' in updates['error']
    assert 'Work remains paused' in updates['detail']
    assert work_paused(service.state) and manager.awaiting_restart()
    repair=manager.diagnostics.state['latest']
    assert repair['phase']=='restart-repair' and repair['status']=='failed' and repair['errorType']=='ValueError'
    assert not any(event['phase'] in {'restart-ack','restart-reconcile'} for event in manager.diagnostics.state['events'])
    assert 'private malformed receipt' not in json.dumps(updates)
    assert saves==1
    saved=load_saved_state(service.db)
    assert saved['updates']['pendingRestart'] is None and saved['updates']['pendingApp'] is None
    assert saved['updates']['pendingReplacement']==updates['pendingReplacement']
    started=[]
    async def allowed():started.append(True)
    for action in ('check','app','install'):
        monkeypatch.setattr(manager,action,allowed)
        await manager.command(action)
    assert started==[]
    await service.close()


async def test_restart_repair_preserves_existing_diagnostic_failure(tmp_path,monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    prior={'phase':'candidate-install','status':'failed','errorType':'RuntimeError'}
    service.state['updates']={'phase':'activating','pendingRestart':{'version':'0.0.1'},
                              'diagnostics':{'attemptId':'b'*32,'kind':'application',
                                             'events':[prior],'lastFailure':prior}}
    saves=0
    original_save=service._save
    def save():
        nonlocal saves
        saves+=1
        original_save()
    monkeypatch.setattr(service,'_save',save)
    manager=UpdateManager(service);service.update_manager=manager
    assert service.state['updates']['phase']=='interrupted'
    assert service.state['updates']['pendingRestart'] is None
    assert not service.state['updates'].get('pendingReplacement')
    assert not work_paused(service.state) and not manager.awaiting_restart()
    assert manager.diagnostics.state['lastFailure']==prior
    repair=manager.diagnostics.state['events'][-1]
    assert repair['phase']=='restart-repair' and repair['attemptId']=='b'*32
    assert saves==1
    saved=load_saved_state(service.db)
    assert saved['updates']['diagnostics']['lastFailure']==prior
    await service.close()

async def test_installed_application_removed_from_pending_updates_but_sources_preserved(tmp_path):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    from amplifier_web import __version__
    app={'id':'application','latest':'v'+__version__,'current':'0.0.1','status':'update'}
    service.state['updates']={'application':app,'items':[app,{'id':'community','status':'update'}],'available':2,'appAvailable':True}
    manager=UpdateManager(service);service.update_manager=manager
    assert service.state['updates']['available']==1
    assert service.state['updates']['items'][0]['status']=='current'
    assert service.state['updates']['items'][1]['status']=='update'
    assert not service.state['updates']['appAvailable']
    await service.close()


async def test_current_version_is_visible_and_saved_before_any_check(tmp_path):
    from amplifier_web import __version__
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    app=service.state['updates']['application']
    assert app['status']=='not_checked' and app['current']==__version__
    assert app['channel']=='github-releases'
    saved=load_saved_state(service.db)
    assert saved['updates']['application']['current']==__version__
    await service.close()


@pytest.mark.parametrize('ahead', [True, False])
async def test_installed_ahead_of_release_is_explicit_and_never_tracks_main(monkeypatch, ahead):
    from amplifier_web import __version__
    tag = 'v0.0.1' if ahead else 'v' + __version__
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/fixture/tool')
    calls=[]
    async def process(*args,**kwargs):
        calls.append(args)
        if args[0]=='gh':return json.dumps({'tag_name':tag,'published_at':'2026-01-01T00:00:00Z'})
        return 'a'*40+'\trefs/tags/'+tag
    monkeypatch.setattr(app_updates,'process',process)
    async def component_updates(*args): return []
    monkeypatch.setattr(app_updates.components,'updates',component_updates)
    async def metadata(path, **kwargs):
        return await app_updates.process('gh', 'api', path)
    monkeypatch.setattr(app_updates, 'github_read', metadata)
    result=await app_updates.check()
    assert result['status']=='current' and result['releaseBehind'] is ahead
    assert result['latest']==tag
    if ahead:
        assert 'newer than' in result['detail']
    else:
        # Application check cannot attest a separate ecosystem batch succeeded.
        assert result['detail']=='The latest published application release is installed.'
    assert result['publishedAt']=='2026-01-01T00:00:00Z'
    assert not any('refs/heads/main' in part for call in calls for part in call)


async def test_restart_preserves_effective_server_overrides_without_saving_them(tmp_path,monkeypatch):
    from amplifier_web.cli import _parse,_server_overrides
    from amplifier_web.deployment import load_server_config,validate_server
    import sys
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    before=load_server_config(tmp_path)
    saved=(tmp_path/'config/server.yaml').read_text()
    service.port=9443
    service.server_config=validate_server({**before,'bind':['127.0.0.1','192.0.2.5'],'port':9443,
        'public_origins':['https://app.example:9443'],'session_ttl_seconds':300,
        'tls':{'method':'ca','cert':'custom-cert.pem','key':'custom-key.pem'}})
    options=app_updates.restart_arguments(manager,'/fixture/launcher')
    monkeypatch.setattr(sys,'argv',options)
    parsed=_parse()
    assert load_server_config(tmp_path,overrides=_server_overrides(parsed))==service.server_config
    assert (tmp_path/'config/server.yaml').read_text()==saved
    assert parsed.workspace==service.default_workspace
    await service.close()


async def test_running_smart_tool_defers_application_activation(tmp_path,monkeypatch):
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    service.state['smartTools']={'operations':[{'id':'tool','status':'running'}]}
    process=AsyncMock(side_effect=AssertionError('must wait for active Smart Tool'))
    monkeypatch.setattr(app_updates,'process',process)
    await app_updates.activate(manager)
    process.assert_not_awaited()
    assert service.state['updates'].get('phase')!='activating'
    await service.close()


async def test_accepted_smart_tool_task_blocks_update_before_operation_record(tmp_path,monkeypatch):
    import asyncio
    from amplifier_web.service import AppError
    service,manager,_=await prepared_activation(tmp_path,monkeypatch)
    started=asyncio.Event();finish=asyncio.Event()
    async def command(*args):
        started.set();await finish.wait()
    service.smart_tools=SimpleNamespace(close=AsyncMock())
    service.smart_canvas=SimpleNamespace(command=command)
    await service.dispatch('smartTools.call',{'id':'fixture','name':'work'},command_id='queued-tool')
    assert manager.busy()
    await started.wait()
    assert manager.busy()
    service.state['updates']['phase']='activating'
    with pytest.raises(AppError,match='update is activating'):
        await service.dispatch('smartTools.call',{'id':'fixture','name':'second'})
    finish.set()
    await asyncio.gather(*service.smart_tool_tasks)
    assert not manager.busy()
    await service.close()


async def test_candidate_probe_is_isolated_from_checkout_and_pythonpath(tmp_path,monkeypatch):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    service.state['updates']['application']={'status':'update','revision':'a'*40,'latest':'v99.0.0'}
    calls=[]
    monkeypatch.setattr(app_updates.shutil,'which',lambda _: '/fixture/uv')
    async def process(*args,**kwargs):
        calls.append(args)
        return '99.0.0' if '-c' in args else ''
    monkeypatch.setattr(app_updates,'process',process)
    await app_updates.stage(manager)
    assert calls[-1][-3:]==('-I','-c',app_updates.PROBE)
    assert 'is_relative_to(Path(sys.prefix)' in app_updates.PROBE
    await service.close()


async def test_direct_voice_connect_claims_work_under_update_lock(tmp_path,monkeypatch):
    import asyncio
    from amplifier_web.voice import VoiceCall,VoiceError,VoiceService
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    await service.dispatch('session.create',{})
    manager=UpdateManager(service);service.update_manager=manager
    voice=VoiceService(service,api_key='fixture-only',http=object())
    create=AsyncMock(return_value={'id':'fixture','sdp':'answer'})
    monkeypatch.setattr(VoiceCall,'create',create)
    # A request that started before activation but waited for the state lock
    # must still fail before opening a paid provider connection.
    async with service.lock:
        pending=asyncio.create_task(voice.connect('v=0'))
        await asyncio.sleep(0)
        service.state['updates']['phase']='activating'
    with pytest.raises(VoiceError,match='update is activating'):
        await pending
    create.assert_not_awaited()
    assert voice.call is None
    service.state['updates']['phase']='app-staged'
    await voice.connect('v=0')
    assert manager.busy()
    create.assert_awaited_once()
    voice.call.closed=True
    await service.close()


@pytest.mark.parametrize('status,expected',[('queued',True),('sending',True),('submitted',False),('unknown',False)])
async def test_feedback_submission_defers_update(tmp_path,status,expected):
    service=AppService(tmp_path,Runtime(),workspace=tmp_path)
    manager=UpdateManager(service);service.update_manager=manager
    service.state['feedback']={'requests':[{'id':'fixture','status':status}]}
    assert manager.busy() is expected
    await service.close()

async def test_public_release_metadata_needs_no_github_cli(monkeypatch):
    import httpx
    from amplifier_web import app_updates
    monkeypatch.setattr(app_updates.shutil,'which',lambda _:None)
    requests=[]
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,headers):
            requests.append((url,headers))
            return httpx.Response(200,json={'tag_name':'v0.20.101'},request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    assert json.loads(await app_updates.github_read('repos/microsoft/amplifier-unified/releases/latest'))['tag_name']=='v0.20.101'
    assert requests[0][0].startswith('https://api.github.com/')
    assert 'Authorization' not in requests[0][1]


async def test_public_release_check_falls_back_without_account(monkeypatch):
    import httpx
    from amplifier_web import reset
    monkeypatch.setenv('AMPLIFIER_GITHUB_ACCESS', 'anonymous')
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers):
            return httpx.Response(403, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    monkeypatch.setattr(reset, 'public_release_link', lambda timeout: {'tag_name': 'v1.2.3'})
    monkeypatch.setattr(app_updates, 'process', AsyncMock(side_effect=AssertionError('No authenticated fallback')))
    assert json.loads(await app_updates.github_read(f'repos/{app_updates.REPOSITORY}/releases/latest'))['tag_name'] == 'v1.2.3'
    # A contents failure must not accidentally receive release metadata.
    with pytest.raises(RuntimeError, match='release channel'):
        await app_updates.github_read(f'repos/{app_updates.REPOSITORY}/contents/missing', raw=True)
