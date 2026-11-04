import asyncio
import json
from pathlib import Path
import subprocess
import time
import pytest
from amplifier_web.service import AppService,AppError
from amplifier_web.updates import UpdateManager,active_release,foundation_home,safe_label

class Runtime:
    def __init__(self): self.closed=0
    async def close(self): self.closed+=1

def git(path,*args):
    return subprocess.check_output(['git',*args],cwd=path,text=True,stderr=subprocess.DEVNULL).strip()

@pytest.fixture
async def app(tmp_path,monkeypatch,repo):
    from amplifier_web import app_updates,updates,update_sequence
    monkeypatch.setattr(update_sequence, 'included_sources', lambda: (set(), []))
    async def check():return {"id":"application","label":"Amplifier Unified","status":"current"}
    monkeypatch.setattr(app_updates,"check",check)
    original=updates.process
    async def process(*args,**kwargs):
        if 'fetch' in args:
            args=tuple(str(repo[0]) if arg=='https://example.invalid/repo' else arg for arg in args)
        return await original(*args,**kwargs)
    monkeypatch.setattr(updates,'process',process)
    service=AppService(tmp_path/'app',Runtime(),workspace=tmp_path)
    from amplifier_web.deployment import load_server_config
    service.server_config = load_server_config(service.data_dir)
    await service.dispatch('session.create',{'bundle':'anchors-amp-dev'})
    service.update_manager=UpdateManager(service)
    yield service
    await service.close()

@pytest.fixture
def repo(tmp_path):
    remote=tmp_path/'remote';remote.mkdir()
    git(remote,'init','-b','main');git(remote,'config','user.email','test@example.invalid');git(remote,'config','user.name','Fixture')
    (remote/'bundle.md').write_text('first')
    git(remote,'add','.');git(remote,'commit','-m','first');old=git(remote,'rev-parse','HEAD')
    (remote/'bundle.md').write_text('second');git(remote,'commit','-am','second');new=git(remote,'rev-parse','HEAD')
    return remote,old,new

async def prepare(app,repo):
    remote,old,new=repo
    root=app.data_dir/'foundation/cache/repository';root.parent.mkdir(parents=True)
    git(root.parent,'clone',str(remote),str(root));git(root,'checkout','--detach',old)
    (root/'.amplifier_cache_meta.json').write_text(json.dumps({'git_url':'https://example.invalid/repo','ref':'main','commit':old}))
    (app.data_dir/'config').mkdir(exist_ok=True);(app.data_dir/'config/settings.yaml').write_text('{}')
    manager=app.update_manager
    manager.inventory=[{'id':'repo','path':'cache/repository','url':'https://example.invalid/repo','label':'Fixture','current':old,'latest':new,'ref':'main','eligible':True,'status':'update'}]
    return manager,root

async def test_stage_activate_and_rollback_preserve_original_cache(app,repo):
    manager,root=await prepare(app,repo)
    async def validate(stage,release):
        assert git(stage/'foundation/cache/repository','rev-parse','HEAD')==repo[2]
        assert git(root,'rev-parse','HEAD')==repo[1]
    manager.validate=validate
    manager.diagnostics.begin('ecosystem', components=['repo'])
    failure=manager.diagnostics.record('ecosystem-validation','failed',errorType='ValueError')
    from amplifier_web.attention import snapshot
    assert any(item.get('actionRequired') for item in snapshot(app.state)['items'])
    published=[]
    publish=app._publish_changes
    def observe(**kwargs):
        published.append(snapshot(app.state))
        return publish(**kwargs)
    app._publish_changes=observe
    await manager.install()
    assert app.state['updates']['phase']=='installed'
    assert not any(item.get('actionRequired') for item in snapshot(app.state)['items'])
    assert any(not any(item.get('actionRequired') for item in state['items']) for state in published)
    assert failure in manager.diagnostics.state['events']
    assert git(foundation_home(app.data_dir)/'cache/repository','rev-parse','HEAD')==repo[2]
    assert git(root,'rev-parse','HEAD')==repo[1]
    await manager.rollback()
    assert active_release(app.data_dir)['current'] is None
    assert foundation_home(app.data_dir)==app.data_dir/'foundation'

async def test_failed_validation_does_not_activate_or_close_sessions(app,repo):
    manager,root=await prepare(app,repo)
    async def fail(*args):raise RuntimeError('incompatible')
    manager.validate=fail
    await manager.install()
    assert app.state['updates']['phase']=='error'
    assert app.state['updates']['detail']=='Component update preparation failed. Current components remain active.'
    assert not active_release(app.data_dir)
    assert app.runtime.closed==0
    assert git(root,'rev-parse','HEAD')==repo[1]

async def test_active_work_defers_activation_then_uses_validated_snapshot(app,repo):
    manager,root=await prepare(app,repo)
    async def validate(*args):pass
    manager.validate=validate
    app.state['sessions'][0]['status']='working'
    await manager.install()
    assert app.state['updates']['phase']=='staged'
    assert not active_release(app.data_dir)
    app.state['sessions'][0]['status']='idle'
    await manager.tick()
    assert active_release(app.data_dir)['current']

async def test_modified_cache_cannot_be_overwritten(app,repo):
    manager,root=await prepare(app,repo)
    (root/'bundle.md').write_text('User edit')
    await manager.install()
    assert app.state['updates']['phase']=='error'
    assert (root/'bundle.md').read_text()=='User edit'
    assert not active_release(app.data_dir)

async def test_check_is_read_only_and_failures_are_not_current(app,repo,monkeypatch):
    manager,root=await prepare(app,repo)
    from amplifier_web import updates
    original=updates.process
    async def process(*args,**kwargs):
        if args[1]=='ls-remote':raise RuntimeError('offline')
        return await original(*args,**kwargs)
    monkeypatch.setattr(updates,'process',process)
    await manager.check()
    assert app.state['updates']['items'][0]['status']=='check_failed'
    assert git(root,'rev-parse','HEAD')==repo[1]
    assert 'url' not in app.state['updates']['items'][0]

async def test_default_and_update_settings_preserve_existing_conversation(app):
    assert app.state['settings']['bundle']=='work'
    assert app.state['settings']['updates']['intervalHours']==4
    assert app.state['sessions'][0]['bundle']=='anchors-amp-dev'
    await app.dispatch('settings.update',{'patch':{'updates':{'autoCheck':False}}})
    with pytest.raises(AppError):await app.dispatch('settings.update',{'patch':{'updates':{'autoInstall':True}}})
    with pytest.raises(AppError):await app.dispatch('settings.update',{'patch':{'updates':{'intervalHours':0}}})
    app.state['updates']['phase']='activating'
    with pytest.raises(AppError):await app.dispatch('conversation.send',{'text':'new work'})
    with pytest.raises(AppError):await app.voice_delegate('new work','voice:1')


@pytest.mark.parametrize('interval', [1, 4, 8, 24])
async def test_supported_check_intervals_use_shared_settings_action(app, interval):
    await app.dispatch('settings.update', {'patch': {'updates': {'intervalHours': interval}}})
    assert app.state['settings']['updates']['intervalHours'] == interval


@pytest.mark.parametrize('interval', [6, 168, 0, True, 4.0, '4'])
async def test_new_check_intervals_reject_unsupported_values_without_mutation(app, interval):
    original = dict(app.state['settings']['updates'])
    with pytest.raises(AppError, match='1, 4 or 8 hours, or daily'):
        await app.dispatch('settings.update', {'patch': {'updates': {'intervalHours': interval}}})
    assert app.state['settings']['updates'] == original


@pytest.mark.parametrize('interval', [6, 24, 168])
async def test_saved_check_interval_survives_restart_and_unrelated_switch_change(tmp_path, interval):
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    service.state['settings']['updates'] = {'autoCheck': False, 'autoInstall': False, 'intervalHours': interval}
    service._publish()
    await service.close()
    restored = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    try:
        assert restored.state['settings']['updates'] == {'autoCheck': False, 'autoInstall': False, 'intervalHours': interval}
        await restored.dispatch('settings.update', {'patch': {'updates': {'autoCheck': True}}})
        assert restored.state['settings']['updates']['intervalHours'] == interval
        await restored.dispatch('settings.update', {'patch': {'updates': {'intervalHours': 4}}})
        assert restored.state['settings']['updates']['intervalHours'] == 4
    finally:
        await restored.close()


@pytest.mark.parametrize('saved_interval', [None, 1, 4, 8, 24, 6, 168])
async def test_background_check_respects_schedule_and_recent_attempt(app, monkeypatch, saved_interval):
    options = app.state['settings']['updates']
    options['autoInstall'] = False
    if saved_interval is None:
        options.pop('intervalHours')
    else:
        options['intervalHours'] = saved_interval
    interval = saved_interval or 4
    now = 2_000_000_000
    monkeypatch.setattr('amplifier_web.updates.time.time', lambda: now)
    checks = []
    async def check(**kwargs): checks.append('check')
    app.update_manager.check = check
    state = app.state['updates']
    state.update(lastCheck=now-interval*3600+1, lastAttempt=0)
    await app.update_manager.tick()
    assert checks == []
    state['lastCheck'] -= 1
    await app.update_manager.tick()
    assert checks == ['check']
    state['lastAttempt'] = now
    await app.update_manager.tick()
    assert checks == ['check']

async def test_background_policy_preserves_explicit_opt_out(app):
    manager=app.update_manager;events=[]
    async def check(**kwargs):
        events.append('check');app.state['updates'].update(phase='available',available=1,lastCheck=time.time())
    async def install():events.append('install')
    manager.check=check;manager.install=install
    assert app.state['settings']['updates']['autoInstall'] is True
    app.state['settings']['updates']['autoInstall']=False
    await manager.tick();assert events==['check']
    app.state['settings']['updates']['autoInstall']=True
    await manager.tick();assert events==['check','install']


def test_source_labels_omit_credentials():
    assert safe_label('https://user:private@example.org/owner/repo.git?token=secret')=='example.org/owner/repo'

def test_both_release_pointer_fields_reject_path_traversal(tmp_path):
    (tmp_path/'updates').mkdir()
    for field in ('current','previous'):
        (tmp_path/'updates/active.json').write_text(json.dumps({field:'../../outside'}))
        with pytest.raises(ValueError,match='release identity'):active_release(tmp_path)

async def test_repeated_install_reuses_pending_generation(app,repo):
    manager,root=await prepare(app,repo)
    validations=[]
    async def validate(stage,release):validations.append(release)
    manager.validate=validate
    app._session()['status']='working'
    await manager.install()
    pending=app.state['updates']['pendingRelease']
    await manager.install()
    assert app.state['updates']['pendingRelease']==pending
    assert len(validations)==1

async def test_staging_preserves_symlinks_without_copying_external_workspace(app,repo,tmp_path):
    manager,root=await prepare(app,repo)
    outside=tmp_path/'outside';outside.mkdir();(outside/'private.txt').write_text('outside staging ownership')
    (root/'external').symlink_to(outside,target_is_directory=True)
    async def validate(stage,release):
        link=stage/'foundation/cache/repository/external'
        assert link.is_symlink()
        assert link.resolve()==outside
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['phase']=='installed'
    assert (outside/'private.txt').read_text()=='outside staging ownership'


def test_identical_cache_copies_group_without_losing_refs_or_versions():
    from amplifier_web.updates import group_sources
    first={'id':'a','label':'github.com/owner/computer-use','ref':'main','kind':'bundle / module','current':'old','latest':'new','status':'update','path':'private/cache','url':'https://private','eligible':True}
    rows=group_sources([first,{**first,'id':'b','path':'other/cache'},{**first,'id':'c','ref':'dev'},{**first,'id':'d','current':'different'}])
    assert len(rows)==3
    assert rows[0]['cacheCopies']==2
    assert not {'url','path','eligible'} & rows[0].keys()
    assert group_sources(rows)==rows


async def test_grouped_display_still_installs_every_cached_copy(app,repo):
    import shutil
    manager,root=await prepare(app,repo)
    second=root.parent/'second';shutil.copytree(root,second)
    manager.inventory.append({**manager.inventory[0],'id':'second','path':'cache/second'})
    async def validate(stage,release):
        for name in ('repository','second'):
            assert git(stage/'foundation/cache'/name,'rev-parse','HEAD')==repo[2]
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['phase']=='installed'
    assert git(foundation_home(app.data_dir)/'cache/second','rev-parse','HEAD')==repo[2]


async def test_ecosystem_activation_does_not_claim_application_was_installed(app,repo):
    manager,_=await prepare(app,repo)
    release={'id':'application','label':'Amplifier Unified','kind':'app','current':'0.1.0','latest':'v99.0.0','status':'update'}
    app.state['updates'].update(application=release,items=[release,{'id':'module','label':'Fixture module','current':'old','latest':'new','status':'update'}],appAvailable=True)
    # A staged ecosystem can coexist with a subsequently discovered app update.
    app.state['updates']['appAvailable']=False
    async def validate(*args):pass
    manager.validate=validate
    await manager.install()
    assert app.state['updates']['items'][0]==release
    assert app.state['updates']['application']==release
    assert app.state['updates']['available']==1
    assert app.state['updates']['items'][1]['status']=='current'


async def test_runtime_lifecycle_preserves_dispatch_across_update_repair_reset_and_rollback(app, repo, tmp_path, monkeypatch):
    """Every host-continuing lifecycle operation retains AppService admission."""
    import sys
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    script = r'''
import json,sys
from pathlib import Path
for line in sys.stdin:
    request=json.loads(line)
    if request['op']=='start':
        print(json.dumps({'type':'runtime.ready','report':{}}),flush=True)
    elif request['op']=='send':
        with Path(sys.argv[1]).open('a') as stream:
            stream.write(request['input_id']+'\n')
        print(json.dumps({'op':'reply','id':request['id'],'result':{'accepted':True}}),flush=True)
    elif request['op']=='stop':
        break
'''
    log = tmp_path / 'delivered.txt'
    bridge = app.app_bridge
    runtime = RuntimeManager(bridge, command=[sys.executable, '-c', script, str(log)],
                             startup_timeout=3, progress_interval=.01)
    # Exercise the coordinated-restart path for a host without generation adoption.
    runtime.promote_generation = None
    await app.install_runtime(runtime)
    session = app.state['sessions'][0]
    assert (await app.dispatch('conversation.send', {'text': 'before update'}, command_id='before-update'))['delivery'] == 'accepted'
    await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    original_process = runtime.workers[session['id']]['process']
    original_retention = runtime.retention.task
    manager, _ = await prepare(app, repo)
    async def validate(*args): pass
    manager.validate = validate
    from amplifier_web import updates
    original_write = updates.write_private
    def fail_inventory(path, contents):
        if path == manager.directory / 'inventory.json':
            raise OSError('inventory cleanup unavailable')
        return original_write(path, contents)
    monkeypatch.setattr(updates, 'write_private', fail_inventory)
    await manager.install()
    assert app.state['updates']['phase'] == 'installed'
    assert original_process.returncode is not None
    assert original_retention.done()
    assert not runtime.workers
    assert log.read_text().splitlines() == ['before-update']
    assert runtime._closed
    replacement = app.runtime
    assert replacement is not runtime
    assert replacement.app_bridge is bridge
    assert replacement.command == runtime.command
    assert replacement.startup_timeout == runtime.startup_timeout
    assert replacement.progress_interval == runtime.progress_interval
    assert replacement.retention.settings == runtime.retention.settings
    assert (await app.dispatch('conversation.send', {'text': 'after update'}, command_id='after-update'))['delivery'] == 'accepted'
    await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    updated_process = replacement.workers[session['id']]['process']
    updated_retention = replacement.retention.task
    await manager.rollback()
    assert active_release(app.data_dir)['current'] is None
    assert updated_process.returncode is not None
    assert updated_retention.done()
    assert replacement._closed
    restored = app.runtime
    assert restored is not replacement
    assert (await app.dispatch('conversation.send', {'text': 'after rollback'}, command_id='after-rollback'))['delivery'] == 'accepted'
    await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    app.management = Management(app)
    async def repaired(*args, **kwargs):
        assert args[1] == 'sync'
        return ''
    monkeypatch.setattr(updates, 'process', repaired)
    await app.management.perform('maintenance.repair', {})
    repaired_runtime = app.runtime
    assert repaired_runtime is not restored and restored._closed
    assert (await app.dispatch('conversation.send', {'text': 'after repair'}, command_id='after-repair'))['delivery'] == 'accepted'
    await app.on_runtime_event('runtime.status', {'sessionId': session['id'], 'status': 'idle'})
    await app.management.perform('maintenance.reset', {'parts': ['settings'], 'apply': True, 'confirmation': 'RESET'})
    reset_runtime = app.runtime
    assert reset_runtime is not repaired_runtime and repaired_runtime._closed
    assert (await app.dispatch('conversation.send', {'text': 'after reset'}, command_id='after-reset'))['delivery'] == 'accepted'
    assert log.read_text().splitlines() == ['before-update', 'after-update', 'after-rollback', 'after-repair', 'after-reset']


@pytest.mark.parametrize('failure', ['pointer', 'candidate'])
async def test_ecosystem_activation_preflight_failures_leave_live_runtime_and_pointer_untouched(app, repo, monkeypatch, failure):
    manager, _ = await prepare(app, repo)
    release = 'f' * 32
    marker = manager.directory / 'releases' / release / 'validated.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'hostVersion': __import__('amplifier_web').__version__}))
    app.state['updates']['pendingRelease'] = release
    original = app.runtime
    from amplifier_web import updates
    if failure == 'pointer':
        original_write = updates.write_private
        def reject_pointer(path, contents):
            if path == manager.directory / 'active.json':
                raise OSError('pointer unavailable')
            return original_write(path, contents)
        monkeypatch.setattr(updates, 'write_private', reject_pointer)
    else:
        monkeypatch.setattr(app, 'runtime_candidate', lambda: (_ for _ in ()).throw(OSError('candidate unavailable')))
    await manager.activate()
    assert app.runtime is original
    assert original.closed == 0
    assert not active_release(app.data_dir)
    assert app.state['updates']['phase'] == 'error'


async def test_repair_and_ecosystem_activation_are_serialized(app, repo, monkeypatch):
    import asyncio
    from amplifier_web.management import Management
    from amplifier_web import updates

    manager, _ = await prepare(app, repo)
    app.management = Management(app)
    entered, release = asyncio.Event(), asyncio.Event()

    async def process(*args, **kwargs):
        if args[1] == 'sync':
            entered.set()
            await release.wait()
        return ''

    monkeypatch.setattr(updates, 'process', process)
    repair = asyncio.create_task(app.management.perform('maintenance.repair', {}))
    await asyncio.wait_for(entered.wait(), 3)
    generation = 'e' * 32
    marker = manager.directory / 'releases' / generation / 'validated.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'hostVersion': __import__('amplifier_web').__version__}))
    app.state['updates']['pendingRelease'] = generation
    activation = asyncio.create_task(manager.activate())
    await asyncio.sleep(0)
    assert not activation.done()
    release.set()
    await repair
    await activation
    assert active_release(app.data_dir)['current'] == generation
    assert app.state['updates']['phase'] == 'installed'


@pytest.mark.parametrize('locked', [True, False])
async def test_repair_finds_uv_project_without_fixed_command_offsets(app, monkeypatch, locked):
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager
    from amplifier_web import updates

    app.management = Management(app)
    command = ['/fixture/uv', 'run', *( ['--locked'] if locked else []), '--project',
               '/actual/runtime-project', '--python', '3.13', 'python', '/fixture/worker.py']
    monkeypatch.setattr(RuntimeManager, '_command', lambda self, *args, **kwargs: list(command))
    calls = []
    async def process(*args, **kwargs):
        calls.append(args)
        return ''
    monkeypatch.setattr(updates, 'process', process)

    await app.management.perform('maintenance.repair', {})

    assert calls == [('/fixture/uv', 'sync', *(('--locked',) if locked else ()),
                      '--project', '/actual/runtime-project', '--python', '3.13', '--reinstall')]
    assert isinstance(app.runtime, RuntimeManager) and not app.runtime._closed


@pytest.mark.parametrize('failure', ['replacement', 'publication'])
@pytest.mark.parametrize('rollback', [False, True])
async def test_post_commit_promotion_retry_preserves_pointer_identity(app, repo, monkeypatch, failure, rollback):
    manager, _ = await prepare(app, repo)
    async def validate(*args): pass
    manager.validate = validate
    if rollback:
        await manager.install()
    before = dict(active_release(app.data_dir))
    original_replace = app.replace_runtime
    original_publish = manager.publish
    failed = False

    async def replace(candidate):
        nonlocal failed
        if failure == 'replacement' and not failed:
            failed = True
            raise OSError('replacement failed after pointer commit')
        return await original_replace(candidate)

    async def publish(**values):
        nonlocal failed
        if failure == 'publication' and values.get('phase') == 'installed' and not failed:
            failed = True
            raise OSError('final publication failed after pointer commit')
        return await original_publish(**values)

    monkeypatch.setattr(app, 'replace_runtime', replace)
    monkeypatch.setattr(manager, 'publish', publish)
    if rollback:
        await manager.rollback()
    else:
        await manager.install()

    committed = dict(active_release(app.data_dir))
    target = committed.get('current')
    assert committed == {'current': target, 'previous': before.get('current'), 'at': committed['at']}
    assert app.state['updates']['phase'] == 'error'
    assert manager.diagnostics.state['activeFailures']
    if rollback:
        assert 'pendingRollback' in app.state['updates']
    else:
        assert app.state['updates']['pendingRelease'] == target

    if rollback:
        await manager.rollback()
    else:
        await manager.activate()

    assert active_release(app.data_dir) == committed
    assert app.state['updates']['phase'] == 'installed'
    assert not manager.diagnostics.state['activeFailures']
    assert app.state['updates'].get('pendingRelease') is None
    assert 'pendingRollback' not in app.state['updates']


async def test_normal_promotion_supersedes_failed_rollback_target(app, repo, monkeypatch):
    manager, _ = await prepare(app, repo)
    from amplifier_web import updates

    original_write = updates.write_private
    release_a, release_b, release_c = 'a' * 32, 'b' * 32, 'c' * 32
    for release in (release_a, release_b, release_c):
        marker = manager.directory / 'releases' / release / 'validated.json'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'hostVersion': __import__('amplifier_web').__version__}))
    original_write(manager.directory / 'active.json',
                   json.dumps({'current': release_a, 'previous': release_b, 'at': 1}))
    original_candidate = app.runtime_candidate
    failed = True

    def candidate():
        nonlocal failed
        if failed:
            failed = False
            raise OSError('failed rollback preflight')
        return original_candidate()

    monkeypatch.setattr(app, 'runtime_candidate', candidate)
    await manager.rollback()
    assert app.state['updates']['pendingRollback'] == release_b
    assert active_release(app.data_dir)['current'] == release_a

    app.state['updates']['pendingRelease'] = release_c
    await manager.activate()
    assert active_release(app.data_dir)['current'] == release_c
    assert active_release(app.data_dir)['previous'] == release_a
    assert 'pendingRollback' not in app.state['updates']

    await manager.rollback()
    assert active_release(app.data_dir)['current'] == release_a
    assert active_release(app.data_dir)['previous'] == release_c


async def test_committed_normal_promotion_invalidates_stale_rollback_before_final_publication(app, repo, monkeypatch):
    manager, _ = await prepare(app, repo)
    from amplifier_web import updates

    original_write = updates.write_private
    release_a, release_b, release_c = 'a' * 32, 'b' * 32, 'c' * 32
    for release in (release_a, release_b, release_c):
        marker = manager.directory / 'releases' / release / 'validated.json'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'hostVersion': __import__('amplifier_web').__version__}))
    original_write(manager.directory / 'active.json',
                   json.dumps({'current': release_a, 'previous': release_b, 'at': 1}))
    original_candidate = app.runtime_candidate
    failed_preflight = True

    def candidate():
        nonlocal failed_preflight
        if failed_preflight:
            failed_preflight = False
            raise OSError('failed rollback preflight')
        return original_candidate()

    monkeypatch.setattr(app, 'runtime_candidate', candidate)
    await manager.rollback()
    assert app.state['updates']['pendingRollback'] == release_b

    original_publish = manager.publish
    failed_publication = True

    async def publish(**values):
        nonlocal failed_publication
        if values.get('phase') == 'installed' and failed_publication:
            failed_publication = False
            raise OSError('final normal-promotion publication failed')
        return await original_publish(**values)

    monkeypatch.setattr(manager, 'publish', publish)
    app.state['updates']['pendingRelease'] = release_c
    await manager.activate()
    assert active_release(app.data_dir)['current'] == release_c
    assert active_release(app.data_dir)['previous'] == release_a
    assert app.state['updates']['phase'] == 'error'
    assert 'pendingRollback' not in app.state['updates']

    await manager.rollback()
    assert active_release(app.data_dir)['current'] == release_a
    assert active_release(app.data_dir)['previous'] == release_c


async def test_normal_promotion_supersedes_failed_base_runtime_rollback(app, repo, monkeypatch):
    manager, _ = await prepare(app, repo)
    from amplifier_web import updates

    original_write = updates.write_private
    release_a, release_c = 'a' * 32, 'c' * 32
    for release in (release_a, release_c):
        marker = manager.directory / 'releases' / release / 'validated.json'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({'hostVersion': __import__('amplifier_web').__version__}))
    original_write(manager.directory / 'active.json',
                   json.dumps({'current': release_a, 'previous': None, 'at': 1}))
    original_candidate = app.runtime_candidate
    failed = True

    def candidate():
        nonlocal failed
        if failed:
            failed = False
            raise OSError('failed base-runtime rollback preflight')
        return original_candidate()

    monkeypatch.setattr(app, 'runtime_candidate', candidate)
    await manager.rollback()
    assert 'pendingRollback' in app.state['updates']
    assert app.state['updates']['pendingRollback'] is None
    assert active_release(app.data_dir)['current'] == release_a

    app.state['updates']['pendingRelease'] = release_c
    await manager.activate()
    assert active_release(app.data_dir)['current'] == release_c
    assert active_release(app.data_dir)['previous'] == release_a
    assert 'pendingRollback' not in app.state['updates']

    await manager.rollback()
    assert active_release(app.data_dir)['current'] == release_a
    assert active_release(app.data_dir)['previous'] == release_c


async def test_phased_install_waits_for_idle_then_continues_to_other_sources(app, repo, monkeypatch):
    import shutil
    manager, root = await prepare(app, repo)
    second = root.parent/'optional'
    shutil.copytree(root, second)
    included = {**manager.inventory[0], 'updateTier':'included'}
    other = {**manager.inventory[0], 'id':'optional','path':'cache/optional','updateTier':'other'}
    manager.inventory = [included]
    app.state['updates'].update(sequence={'stage':'included','install':False,'included':{'status':'available','available':1},'other':{'status':'waiting'}},items=[included])
    validations=[]
    async def validate(stage, release):
        validations.append(release)
        assert git(stage/'foundation/cache/repository','rev-parse','HEAD') == repo[2]
        assert git(stage/'foundation/cache/optional','rev-parse','HEAD') == (repo[1] if len(validations)==1 else repo[2])
    manager.validate=validate
    app.state['sessions'][0]['status']='working'
    await manager.install()
    assert app.state['updates']['phase']=='staged'
    assert not active_release(app.data_dir)
    await manager.tick()
    assert len(validations)==1 and not active_release(app.data_dir)
    app.state['sessions'][0]['status']='idle'
    await manager.tick()
    assert app.state['updates']['sequence']['nextStage']=='other'
    assert git(foundation_home(app.data_dir)/'cache/optional','rev-parse','HEAD')==repo[1]
    async def inventory(**kwargs):return [other.copy()]
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    from amplifier_web import updates
    original=updates.process
    async def process(*args,**kwargs):
        if args[1]=='ls-remote':return repo[2]+' refs/heads/main'
        return await original(*args,**kwargs)
    monkeypatch.setattr('amplifier_web.updates.process',process)
    await manager.tick()
    assert len(validations)==2
    assert git(foundation_home(app.data_dir)/'cache/optional','rev-parse','HEAD')==repo[2]
    assert app.state['updates']['sequence']['stage']=='complete'
    assert not app.state['updates']['sequence']['install']
    assert git(root,'rev-parse','HEAD')==repo[1]
    assert git(second,'rev-parse','HEAD')==repo[1]


async def test_new_automatic_default_installs_eligible_updates_but_not_managed_preview(app):
    manager=app.update_manager;events=[]
    async def check(**kwargs):
        events.append('check');app.state['updates'].update(phase='available',available=1,lastCheck=time.time())
    async def install():events.append('install')
    manager.check=check;manager.install=install
    await manager.tick();assert events==['check','install']
    app.state['updates'].update(appAvailable=True,application={'canInstall':False})
    await manager.tick();assert events==['check','install']

async def test_smart_tool_update_waits_for_idle_and_keeps_worker_runtime(app, monkeypatch):
    manager = app.update_manager
    called=[]
    async def activate(previous, target): called.append((previous,target))
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools=SmartToolsManager(app)
    monkeypatch.setattr(app.smart_tools,'activate_update',activate)
    app.state['updates'].update(pendingSmartTools=[{'previous':'old','target':'new'}], phase='staged')
    app.state['sessions'][0]['status']='working'
    await manager.activateSmartTools()
    assert not called and app.runtime.closed == 0
    app.state['sessions'][0]['status']='idle'
    await manager.activateSmartTools()
    assert called == [('old','new')] and app.runtime.closed == 0
    assert app.state['updates']['pendingSmartTools'] == []

async def test_smart_tool_install_recovers_saved_inventory(app, monkeypatch):
    manager=app.update_manager
    rows=[{'id':'smart-tool:old','installationId':'old','kind':'smart tool','status':'update','eligible':True}]
    (manager.directory/'inventory.json').write_text(json.dumps(rows))
    called=[]
    async def install(): called.append(True)
    monkeypatch.setattr(manager,'installSmartTools',install)
    await manager.install()
    assert called and manager.inventory == rows


async def test_smart_tool_failure_does_not_block_healthy_updates_or_repeat_on_tick(app, monkeypatch):
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools = SmartToolsManager(app)
    manager = app.update_manager
    called = []
    async def activate(previous, target):
        called.append(previous)
        if previous == 'bad':
            raise ValueError('private command output secret=do-not-publish')
    monkeypatch.setattr(app.smart_tools, 'activate_update', activate)
    app.state['updates'].update(phase='staged', pendingSmartTools=[
        {'previous': name, 'target': name + '-new', 'component': name}
        for name in ('bad', 'healthy-a', 'healthy-b')])
    await manager.activateSmartTools()
    assert called == ['bad', 'healthy-a', 'healthy-b']
    pending = app.state['updates']['pendingSmartTools']
    assert [row['previous'] for row in pending] == ['bad']
    assert pending[0]['failure']['phase'] == 'activate'
    assert app.state['updates']['phase'] == 'error'
    receipt = manager.diagnostics.state['lastFailure']
    assert receipt['installationId'] == receipt['component'] == 'bad'
    assert 'do-not-publish' not in json.dumps(app.state['updates'])
    await manager.tick()
    assert called == ['bad', 'healthy-a', 'healthy-b']


async def test_smart_tool_staging_failure_keeps_other_tools_and_can_retry_one(app, monkeypatch):
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools = SmartToolsManager(app)
    manager = app.update_manager
    manager.inventory = [{'installationId': name, 'label': name, 'kind': 'smart tool',
                          'status': 'update', 'eligible': True, 'latest': 'b' * 40}
                         for name in ('bad', 'healthy')]
    stage_calls, activated = [], []
    async def stage(source):
        stage_calls.append(source['installationId'])
        if stage_calls == ['bad']:
            raise ValueError('private install output')
        return {'id': source['installationId'] + '-new'}
    async def activate(previous, target): activated.append(previous)
    monkeypatch.setattr(app.smart_tools, 'stage_update', stage)
    monkeypatch.setattr(app.smart_tools, 'activate_update', activate)
    await manager.installSmartTools()
    assert stage_calls == ['bad', 'healthy'] and activated == ['healthy']
    assert app.state['updates']['pendingSmartTools'][0]['failure']['phase'] == 'stage'
    await manager.smartToolRetry('bad')
    assert stage_calls == ['bad', 'healthy', 'bad'] and activated == ['healthy', 'bad']
    assert not app.state['updates']['pendingSmartTools']


async def test_smart_tool_cancellation_never_continues_the_batch(app, monkeypatch):
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools = SmartToolsManager(app)
    called = []
    async def activate(previous, target):
        called.append(previous)
        raise asyncio.CancelledError()
    monkeypatch.setattr(app.smart_tools, 'activate_update', activate)
    app.state['updates'].update(phase='staged', pendingSmartTools=[
        {'previous': name, 'target': name + '-new'} for name in ('first', 'second')])
    try:
        await app.update_manager.activateSmartTools()
    except asyncio.CancelledError:
        pass
    else:
        pytest.fail(f"Cancellation was lost: calls={called}, pending={app.state['updates']['pendingSmartTools']}")
    assert called == ['first']
    assert len(app.state['updates']['pendingSmartTools']) == 2


async def test_skipped_smart_tool_revision_stays_skipped_until_new_revision(app, monkeypatch):
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools = SmartToolsManager(app)
    manager = app.update_manager
    revision = 'b' * 40
    app.state['updates'].update(phase='error', pendingSmartTools=[{
        'previous': 'old', 'revision': revision,
        'failure': {'phase': 'stage', 'reason': 'smart-tool-update-failed'}}])
    await manager.smartToolSkip('old')
    assert not app.state['updates']['pendingSmartTools']
    assert app.state['updates']['skippedSmartTools'] == {'old': revision}
    async def inventory(**kwargs):
        return [{'id': 'smart-tool:old', 'installationId': 'old', 'label': 'example',
                 'kind': 'smart tool', 'status': 'not_checked', 'eligible': True,
                 'url': 'https://example.invalid/tool', 'ref': 'main', 'current': 'a' * 40}]
    async def remote(*args): return revision
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr('amplifier_web.update_checks.git_revision', remote)
    await manager.check(tier='other')
    assert manager.inventory[0]['status'] == 'skipped'
    assert app.state['updates']['available'] == 0
    revision = 'c' * 40
    await manager.check(tier='other')
    assert manager.inventory[0]['status'] == 'update'
    assert app.state['updates']['available'] == 1

@pytest.mark.parametrize('unrelated_failure', [False, True])
async def test_smart_tool_rollback_disables_reinstallation(app, monkeypatch, unrelated_failure):
    manager=app.update_manager
    from amplifier_web.smart_tools import SmartToolsManager
    app.smart_tools=SmartToolsManager(app)
    app.smart_tools.state['installations']=[{'id':'new','previousInstallationId':'old'}]
    app.state['updates']['sequence']={'stage':'other','install':True}
    failed = [{'previous': 'unrelated', 'failure': {'phase': 'stage'}}] if unrelated_failure else []
    app.state['updates']['pendingSmartTools'] = list(failed)
    activated = asyncio.Event()
    async def activate(): activated.set()
    monkeypatch.setattr(manager,'activateSmartTools',activate)
    # Exercise the actual UI dispatcher: it must forward the installation ID.
    await app.dispatch('updates.smartToolRollback', {'id': 'new'}, command_id='rollback-tool')
    await asyncio.wait_for(activated.wait(), 2)
    assert app.state['updates']['pendingSmartTools'] == [*failed, {'previous':'new','target':'old'}]
    assert app.state['updates']['sequence']['install'] is False
    assert app.state['settings']['updates']['autoInstall'] is False
