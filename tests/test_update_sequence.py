"""Update phases follow the running app's declarations, without touching live work."""
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from amplifier_web import app_updates, updates, update_sequence
from amplifier_web.service import AppService
from amplifier_web.updates import UpdateManager


class Runtime:
    async def close(self):pass


@pytest.fixture
async def manager(tmp_path, monkeypatch):
    service = AppService(tmp_path/'app', Runtime(), workspace=tmp_path)
    manager = UpdateManager(service)
    service.update_manager = manager
    async def application():return {'id':'application','kind':'app','status':'current'}
    monkeypatch.setattr(app_updates, 'check', application)
    yield manager
    await service.close()


def source(manager, tier, current='a'*40):
    return {'id':tier,'kind':'bundle / module','updateTier':tier,'label':tier,
            'url':'https://example.invalid/'+tier,'ref':'main','current':current,
            'status':'not_checked','eligible':True,
            'appSelection':update_sequence.app_selection(manager.home)['witness']}


@pytest.mark.parametrize('status', ['update','unrecognized'])
async def test_app_is_checked_before_any_component_inventory(manager, monkeypatch, status):
    events=[]
    async def application():events.append('app');return {'id':'application','status':status}
    async def inventory(**kwargs):pytest.fail('The old app must not inspect component declarations')
    monkeypatch.setattr(app_updates,'check',application)
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    await manager.check()
    assert events==['app']
    assert manager.service.state['updates']['sequence']['other']['status']=='waiting'
    assert manager.inventory==[]


@pytest.mark.parametrize('included_changed',[False, True])
async def test_check_skips_current_tiers_but_stops_at_included_updates(manager, monkeypatch, included_changed):
    calls=[]
    async def inventory(**kwargs):return [source(manager,'included'),source(manager,'other')]
    async def process(*args,**kwargs):
        calls.append(args[2].rsplit('/',1)[-1])
        return ('b' if included_changed or calls[-1]=='other' else 'a')*40+' refs/heads/main'
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr(updates,'process',process)
    await manager.check()
    assert calls==(['included'] if included_changed else ['included','other'])
    state=manager.service.state['updates']
    assert state['sequence']['stage']==('included' if included_changed else 'other')
    assert state['available']==1
    assert not state['sequence']['install']


async def test_included_check_error_does_not_install_or_check_optional_sources(manager,monkeypatch):
    calls=[]
    async def inventory(**kwargs):return [source(manager,'included'),source(manager,'other')]
    async def process(*args,**kwargs):calls.append(args[2]);raise RuntimeError('offline')
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr(updates,'process',process)
    await manager.check()
    await manager.install()
    assert calls==['https://example.invalid/included']
    assert manager.service.state['updates']['phase']=='error'
    assert not manager.service.state['updates']['sequence']['install']


async def test_manual_install_intent_survives_app_staging(manager,monkeypatch):
    manager.service.state['updates'].update(appAvailable=True,sequence={'stage':'application','install':False})
    calls=[]
    async def app():calls.append('app')
    monkeypatch.setattr(manager,'app',app)
    await manager.install()
    assert calls==['app']
    assert manager.service.state['updates']['sequence']['install'] is True
    restored=UpdateManager(manager.service)
    assert restored.service.state['updates']['sequence']['install'] is True


@pytest.mark.parametrize('install',[False,True])
@pytest.mark.parametrize('tier',['included','other'])
async def test_after_activation_tick_checks_next_tier_without_waiting_for_interval(manager,monkeypatch,install,tier):
    manager.service.state['settings']['updates'].update(autoCheck=False,autoInstall=False)
    manager.service.state['updates'].update(phase='installed',sequence={'stage':'application' if tier=='included' else 'included','nextStage':tier,'install':install})
    calls=[]
    async def check(**kwargs):
        calls.append(kwargs)
        manager.service.state['updates'].update(phase='available',available=1)
    async def apply(*, automatic):
        assert automatic
        calls.append('install')
    monkeypatch.setattr(manager,'check',check)
    monkeypatch.setattr(manager,'install',apply)
    await manager.tick()
    assert calls==[{'tier':tier,'install':install,'fresh':True,'automatic':True}]+(['install'] if install else [])


async def test_completed_sequence_has_no_repeated_background_check(manager,monkeypatch):
    import time
    manager.service.state['updates'].update(phase='checked',lastCheck=time.time(),sequence={'stage':'complete','install':False})
    async def check(**kwargs):pytest.fail('completed sequence should wait for the configured interval')
    monkeypatch.setattr(manager,'check',check)
    await manager.tick()


@pytest.mark.parametrize('action', ['app', 'install'])
async def test_explicit_app_scope_replaces_old_whole_install_without_changing_policy(manager, monkeypatch, action):
    import copy
    import time
    state = manager.service.state['updates']
    settings = manager.service.state['settings']['updates']
    before = copy.deepcopy(settings)
    state.update(phase='available', appAvailable=True,
                 pendingApp={'revision': 'a'*40, 'latest': 'v99.0.0'},
                 sequence={'stage': 'included', 'nextStage': 'other', 'install': True},
                 lastCheck=time.time(), available=2)
    called = []
    async def app(): called.append(copy.deepcopy(state.get('applicationIntent')))
    monkeypatch.setattr(manager, 'app', app)
    await manager.command(action, command_id='exact-'+action)
    assert called == [{'id': 'exact-'+action, 'scope': 'app' if action == 'app' else 'whole'}]
    assert state['pendingApp']['updateIntent'] == called[0]
    assert state['sequence']['install'] is (action == 'install')
    assert ('nextStage' in state['sequence']) is (action == 'install')
    assert settings == before
    restored = UpdateManager(manager.service)
    assert restored.service.state['updates']['applicationIntent'] == called[0]


async def test_app_only_tick_cannot_consume_stale_component_intent(manager, monkeypatch):
    import time
    state = manager.service.state['updates']
    state.update(applicationIntent={'id': 'app-only', 'scope': 'app'}, phase='installed',
                 sequence={'stage': 'included', 'install': True, 'nextStage': 'included'},
                 available=3, lastCheck=time.time(), lastAttempt=time.time(),
                 pendingSmartTools=[{'previous': 'old', 'target': 'new'}])
    async def forbidden(*args, **kwargs): pytest.fail('App-only must not continue old component installation')
    monkeypatch.setattr(manager, 'check', forbidden)
    monkeypatch.setattr(manager, 'install', forbidden)
    monkeypatch.setattr(manager, 'activateSmartTools', forbidden)
    await manager.tick()


@pytest.mark.parametrize('phase', ['error', 'interrupted'])
async def test_app_only_scope_does_not_retry_failed_preparation(manager, monkeypatch, phase):
    import time
    state = manager.service.state['updates']
    manager.service.state['settings']['updates']['autoInstall'] = False
    state.update(applicationIntent={'id': 'failed-app', 'scope': 'app'}, phase=phase,
                 appAvailable=True, lastCheck=time.time())
    async def forbidden(): pytest.fail('Failed preparation requires an explicit new request')
    monkeypatch.setattr(manager, 'app', forbidden)
    for _ in range(3):
        await manager.tick()


async def test_busy_app_only_candidate_still_refreshes_due_release_channel(manager, monkeypatch):
    state = manager.service.state['updates']
    intent = {'id': 'app-only', 'scope': 'app'}
    state.update(applicationIntent=intent, phase='app-staged',
                 pendingApp={'revision': 'a'*40, 'latest': 'v99.0.0', 'updateIntent': intent},
                 sequence={'stage': 'application', 'install': False}, lastCheck=0, lastAttempt=0)
    calls = []
    async def check(**kwargs): calls.append(('check', kwargs))
    async def app(): calls.append(('app', None))
    monkeypatch.setattr(manager, 'check', check)
    monkeypatch.setattr(manager, 'app', app)
    await manager.tick()
    assert calls == [('check', {'fresh': True, 'automatic': True}), ('app', None)]
    assert state['applicationIntent'] == intent


def test_missing_included_sources_and_cold_worker_are_installable_without_writes(tmp_path,monkeypatch):
    monkeypatch.setattr(update_sequence,'included_sources',lambda:({'declared-provider'},[{'url':'https://example.invalid/skills','ref':'main'}]))
    rows=update_sequence.classify(tmp_path,[])
    assert {row['kind'] for row in rows}=={'runtime environment','included source'}
    assert all(row['missing'] and row['updateTier']=='included' for row in rows)
    assert not list(tmp_path.iterdir())


def test_included_classification_keeps_explicit_pins_and_local_sources_protected(tmp_path,monkeypatch):
    monkeypatch.setattr(update_sequence,'included_sources',lambda:({'declared-provider'},[]))
    row={'id':'runtime:declared-provider','package':'declared-provider','kind':'runtime dependency','eligible':False,'status':'pinned','ref':'v1.0.0','url':'https://example.invalid/provider'}
    rows=update_sequence.classify(tmp_path,[row])
    assert rows[-1]['updateTier']=='included'
    assert not rows[-1]['eligible'] and rows[-1]['status']=='pinned'


async def test_missing_source_staging_verifies_exact_checked_revision(tmp_path,monkeypatch):
    from amplifier_foundation.sources.git import GitSourceHandler
    destination=tmp_path/'foundation'
    cached=destination/'cache/source';cached.mkdir(parents=True)
    async def resolve(self,parsed,cache_dir):
        assert cache_dir==destination/'cache' and parsed.ref=='main'
        return SimpleNamespace(active_path=cached)
    async def process(*args,**kwargs):return 'b'*40
    monkeypatch.setattr(GitSourceHandler,'_resolve_locked',resolve)
    monkeypatch.setattr(updates,'process',process)
    row={'url':'https://example.invalid/skills','ref':'main','latest':'a'*40}
    with pytest.raises(ValueError,match='moved after checking'):
        await update_sequence.stage_missing(None,destination,row)


@pytest.mark.parametrize('case', ['pin', 'exact', 'transport', 'interrupted'])
@pytest.mark.parametrize('wake', ['check', 'timer'])
async def test_checked_source_failure_retry_disposition_survives_checks_and_restart(manager, monkeypatch, case, wake):
    """Real check/install/tick/receipts; only remote I/O and costly qualification
    are fixtures. The actual source guard, not an injected typed error, rejects.
    This is scheduling proof, not installed-package qualification.
    """
    import asyncio
    import copy
    import time
    from amplifier_web import update_health, update_plan, session_files
    from amplifier_web.runtime_source_alignment import verify_checked_selection
    from amplifier_web.update_diagnostics import UpdateDiagnostics

    latest = ['b' * 40]
    row = {**source(manager, 'included'), 'kind': 'runtime dependency',
           'package': 'amplifier-foundation'}
    async def inventory(**kw): return [dict(row)]
    async def remote(*args, **kw): return latest[0] + ' refs/heads/main'
    monkeypatch.setattr(manager, 'inventory_sources', inventory)
    monkeypatch.setattr(updates, 'process', remote)
    monkeypatch.setattr(updates, 'foundation_home', lambda home: home/'absent')
    monkeypatch.setattr(session_files, 'amplifier_home', lambda: manager.home/'absent-shared')
    async def build(*args):
        return {'mode': 'worker', 'reason': 'fixture', 'activation': 'per-worker'}
    monkeypatch.setattr(update_plan, 'build', build)
    calls = []
    async def preflight():
        if case == 'transport':
            raise TimeoutError('private endpoint must not enter diagnostics')
        if case == 'interrupted':
            raise asyncio.CancelledError()
        selected = {'url': row['url'], 'ref': 'main', 'revision': 'a'*40,
                    'status': 'explicit-pin' if case == 'pin' else 'selected'}
        verify_checked_selection(
            {'amplifier-foundation': {**selected, 'revision': latest[0]}},
            {'packages': {'amplifier-foundation': selected},
             'batchPaths': {'amplifier-foundation': '/synthetic/private/path'}},
            exact=case == 'exact')
    async def validate(stage, revision):
        calls.append(revision)
        await manager.diagnostics.run('ecosystem-source-preflight', preflight)
    monkeypatch.setattr(manager, 'validate', validate)
    # Keep unrelated periodic storage work out of this scheduler fixture.
    manager._storage_last_pass = time.time()
    manager._storage_policy_due = False
    manager._update_health_initial = False
    await manager.check()
    if case == 'interrupted':
        with pytest.raises(asyncio.CancelledError):
            await manager.install(automatic=True)
    else:
        await manager.install(automatic=True)
    assert len(calls) == 1
    assert not updates.active_release(manager.home)
    diagnostic = manager.diagnostics.state
    failure = copy.deepcopy(diagnostic['lastFailure'])
    mode = 'inspect' if case == 'interrupted' else 'backoff' if case == 'transport' else 'changed-inputs'
    assert diagnostic['batch']['retry']['mode'] == mode
    if case in {'pin', 'exact'}:
        assert failure['package'] == 'amplifier-foundation'
        assert failure['reason'] == ('runtime-source-constraint' if case == 'pin' else 'runtime-resolution-mismatch')
        status = update_health.current(manager.service.state['updates'], {})
        assert status['status'] == 'protected'
        assert status['component'] == 'amplifier-foundation'
    assert 'private' not in json.dumps(failure)
    # The persisted diagnostic reconstruction must retain the same disposition.
    await manager.publish()
    manager.diagnostics = UpdateDiagnostics(manager)
    for _ in range(3):
        if wake == 'check':
            await manager.check()
        await manager.tick()
    assert len(calls) == 1
    assert manager.diagnostics.state['lastFailure'] == failure
    if case == 'interrupted':
        latest[0] = 'c'*40
        await manager.check()
        await manager.tick()
        assert len(calls) == 1
        with pytest.raises(ValueError, match='not replayed'):
            await manager.install()
        return
    manager.diagnostics.state['batch']['retry']['retryAt'] = 0
    if case == 'transport' and wake == 'timer':
        settings = manager.service.state['settings']['updates']
        sequence = manager.service.state['updates']['sequence']
        settings['autoInstall'] = False
        sequence['install'] = False
        await manager.tick()
        assert len(calls) == 1  # A timer cannot create installation intent.
        settings['autoInstall'] = True
        sequence['manualHandoff'] = True
        await manager.tick()
        assert len(calls) == 1  # A manual app handoff also needs component intent.
        sequence.pop('manualHandoff')
    if wake == 'check':
        await manager.check()
    await manager.tick()
    assert len(calls) == (2 if case == 'transport' else 1)
    if case == 'transport':
        assert manager.diagnostics.state['batch']['retry']['attempts'] == 2
        return
    # Unrelated publication/credential settings do not change the source graph.
    manager.service.state['configurationRevision'] = manager.service.state.get('configurationRevision', 0) + 1
    await manager.check()
    await manager.tick()
    assert len(calls) == 1
    latest[0] = 'c'*40
    await manager.check()
    await manager.tick()
    assert len(calls) == 2
    await manager.install()  # Deliberate retry is still allowed; pin stays protected.
    assert len(calls) == 3
    assert not updates.active_release(manager.home)


async def test_preparation_witness_tracks_source_policy_not_unrelated_settings(manager, monkeypatch):
    from amplifier_web.update_health import preparation_key
    from amplifier_web.host.config import write_private
    from amplifier_web.host import config as host_config
    shared = manager.home/'fixture-shared'
    shared.mkdir()
    monkeypatch.setattr(host_config, 'amplifier_home', lambda: shared)
    rows = [{**source(manager, 'included'), 'latest': 'b'*40}]
    config = shared/'settings.yaml'
    write_private(config, 'updates:\n  intervalHours: 4\n')
    original = preparation_key(manager, rows)
    write_private(config, 'updates:\n  intervalHours: 8\n')
    manager.service.state['configurationRevision'] = 5
    assert preparation_key(manager, rows) == original
    write_private(config, 'sources:\n  bundles:\n    routing-matrix: git+https://example.invalid/custom@v1\n')
    assert preparation_key(manager, rows) != original


async def test_deterministic_disposition_survives_service_reload(manager, monkeypatch):
    from amplifier_web.update_health import preparation_key, record_retry
    manager.inventory=[{**source(manager, 'included'), 'latest':'b'*40, 'status':'update'}]
    (manager.directory/'inventory.json').write_text(json.dumps(manager.inventory))
    manager.diagnostics.begin('ecosystem', 'b'*32, components=['amplifier-foundation'])
    failure=manager.diagnostics.record('ecosystem-source-preflight','failed',
        reason='runtime-source-constraint',package='amplifier-foundation')
    record_retry(manager.diagnostics.state['batch'],preparation_key(manager,manager.inventory),failure)
    await manager.publish(phase='error', error='Source policy conflict')
    home=manager.home
    await manager.service.close()
    service=AppService(home,Runtime(),workspace=home.parent)
    try:
        restored=UpdateManager(service);service.update_manager=restored
        assert restored.diagnostics.state['lastFailure']==failure
        assert restored.diagnostics.state['batch']['retry']['mode']=='changed-inputs'
        async def forbidden(*args,**kwargs):pytest.fail('reloaded conflict must not prepare')
        monkeypatch.setattr(restored,'stage_cache_sources',forbidden)
        before=set((home/'updates/releases').glob('*'))
        await restored.install(automatic=True)
        assert set((home/'updates/releases').glob('*'))==before
        assert not updates.active_release(home)
    finally:
        await service.close()


@pytest.mark.parametrize('repair', ['module-config', 'override-config', 'override-disabled', 'key-file', 'bundle-key-file'])
async def test_qualification_configuration_repair_rechecks_same_sources(manager, monkeypatch, repair):
    """Use real host setting application and probe classification inside install.
    Installer/network/profile mounting are not qualified by this scheduling test.
    """
    import time
    import yaml
    from amplifier_web import update_plan, session_files, update_health
    from amplifier_web.host import config as host_config
    from amplifier_web.host.session import _apply_settings
    from amplifier_web.update_diagnostics import CommandFailure, probe_failure
    shared=manager.home/'shared';shared.mkdir()
    monkeypatch.setattr(host_config, 'amplifier_home', lambda: shared)
    monkeypatch.setattr(session_files, 'amplifier_home', lambda: shared)
    monkeypatch.delenv('UNIFIED_MISSING_FIXTURE_VALUE', raising=False)
    # Each validation boundary simulates a fresh worker loading its copied keys.
    import os
    monkeypatch.setattr(host_config, '_KEY_FILE_VALUES', {})
    settings={'config': {'tools': [{'module':'tool-fixture','id':'fixture',
        'config':{'value':'${UNIFIED_MISSING_FIXTURE_VALUE}'}}]}}
    if repair=='bundle-key-file':
        settings={}
    def save():host_config.write_private(shared/'settings.yaml',yaml.safe_dump(settings))
    save()
    row={**source(manager, 'included'),'kind':'runtime dependency','package':'amplifier-foundation'}
    async def inventory(**kw):return [dict(row)]
    async def remote(*args,**kw):return 'b'*40+' refs/heads/main'
    async def build(*args):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr(updates,'process',remote)
    monkeypatch.setattr(updates,'foundation_home',lambda home:home/'absent')
    monkeypatch.setattr(update_plan,'build',build)
    attempts=[];prepared=[]
    async def validate(stage, revision):
        attempts.append(revision)
        config=host_config.read_config(stage,home=stage,shared_home=stage/'shared-config',global_only=True)
        bundle=SimpleNamespace(providers=[],tools=[],hooks=[],session={},agents={})
        if repair=='bundle-key-file':
            from amplifier_foundation import Bundle
            bundle=Bundle(name='authored-fixture',tools=[{'module':'tool-fixture',
                'config':{'value':'${UNIFIED_MISSING_FIXTURE_VALUE}'}}])
        try:
            with monkeypatch.context() as worker:
                worker.setattr(host_config.os, 'environ', dict(os.environ))
                worker.setattr(host_config, '_KEY_FILE_VALUES', {})
                host_config._load_keys(stage/'shared-config/keys.env')
                _apply_settings(bundle,config)
        except ValueError as error:
            update_health.retain_environment_failure(error,stage,manager.diagnostics.state['attemptId'])
            raise CommandFailure({'probe':probe_failure(error,'prepare')}) from error
        prepared.append(bundle.tools)
        # Stop before mounting a synthetic module: reaching this point proves
        # the repaired configuration got a new ordinary qualification attempt.
        raise TimeoutError('fixture stops after configuration preparation')
    monkeypatch.setattr(manager,'validate',validate)
    manager._storage_last_pass=time.time();manager._storage_policy_due=False;manager._update_health_initial=False
    await manager.check();await manager.install(automatic=True)
    first=manager.diagnostics.state['lastFailure']
    assert first['probe']['reason']=='missing-configured-environment'
    assert manager.diagnostics.state['batch']['retry']['mode']=='changed-inputs'
    before=update_health.preparation_key(manager,manager.inventory)
    await manager.check();await manager.tick()
    assert len(attempts)==1 and not prepared
    settings['updates']={'intervalHours':8};save()
    assert update_health.preparation_key(manager,manager.inventory)==before
    if repair=='module-config':
        settings['config']['tools'][0]['config']['value']='repaired'
    elif repair=='override-config':
        settings['overrides']={'fixture':{'config':{'value':'repaired'}}}
    elif repair=='override-disabled':
        settings['overrides']={'fixture':{'enabled':False}}
    else:
        host_config.write_private(shared/'keys.env', 'UNRELATED_CREDENTIAL=not-a-runtime-input\n')
        assert update_health.preparation_key(manager,manager.inventory)==before
        await manager.check();await manager.tick()
        assert len(attempts)==1
        host_config.write_private(shared/'keys.env', 'UNIFIED_MISSING_FIXTURE_VALUE=repaired\n')
    save()
    await manager.check();await manager.tick()
    assert len(attempts)==2 and len(prepared)==1
    assert prepared[0]==[] if repair=='override-disabled' else prepared[0][0]['config']['value']=='repaired'
    assert manager.diagnostics.state['batch']['retry']['mode']=='backoff'
    assert first in manager.diagnostics.state['events']
    assert not updates.active_release(manager.home)


async def test_active_profile_repair_rechecks_same_registered_candidates(manager, monkeypatch):
    import time
    import yaml
    from unittest.mock import AsyncMock
    from amplifier_foundation import Bundle
    from amplifier_web import update_plan, session_files
    from amplifier_web.host import config as host_config, session
    from amplifier_web.update_diagnostics import CommandFailure, probe_failure
    monkeypatch.chdir(manager.home)
    shared=manager.home/'shared';shared.mkdir()
    monkeypatch.setattr(host_config,'amplifier_home',lambda:shared)
    monkeypatch.setattr(session_files,'amplifier_home',lambda:shared)
    settings={'bundle':{'active':'unsupported','added':{'unsupported':'fixture:unsupported'}}}
    def save():host_config.write_private(shared/'settings.yaml',yaml.safe_dump(settings))
    save()
    row={**source(manager, 'included'),'kind':'runtime dependency','package':'amplifier-foundation'}
    async def inventory(**kw):return [dict(row)]
    async def remote(*args,**kw):return 'b'*40+' refs/heads/main'
    async def build(*args):return {'mode':'worker','reason':'fixture','activation':'per-worker'}
    monkeypatch.setattr(manager,'inventory_sources',inventory)
    monkeypatch.setattr(updates,'process',remote)
    monkeypatch.setattr(updates,'foundation_home',lambda home:home/'absent')
    monkeypatch.setattr(update_plan,'build',build)
    async def load(*args,**kw):
        loaded=Bundle(name='unsupported',session={'orchestrator':{'module':'custom-loop'},
            'context':{'module':'context-simple'}},providers=[{'module':'provider-fixture'}])
        loaded.prepare=AsyncMock(side_effect=AssertionError('Unsupported profile must not mount'))
        return None,loaded,'unsupported'
    monkeypatch.setattr(session,'load_root_bundle',load)
    monkeypatch.setattr(host_config,'prepare_registry',lambda config:None)
    calls=[];prepared=[]
    async def validate(stage, revision):
        calls.append(revision)
        config=host_config.read_config(stage,home=stage,shared_home=stage/'shared-config',global_only=True)
        with monkeypatch.context() as probe:
            probe.setattr(session,'load_config',lambda *a,**kw:config)
            try:
                catalog={}
                await session.prepare_dependencies(stage,bundle='unsupported',global_only=True,profile_catalog=catalog)
            except ValueError as error:
                raise CommandFailure({'probe':probe_failure(error,'prepare')}) from error
        prepared.append(catalog)
        raise TimeoutError('fixture stops after profile selection')
    monkeypatch.setattr(manager,'validate',validate)
    manager._storage_last_pass=time.time();manager._storage_policy_due=False;manager._update_health_initial=False
    await manager.check();await manager.install(automatic=True)
    failure=manager.diagnostics.state['lastFailure']
    assert failure['probe']['reason']=='incompatible-conversation-loop'
    assert manager.diagnostics.state['batch']['retry']['mode']=='changed-inputs'
    await manager.check();await manager.tick();assert len(calls)==1
    settings['bundle']['active']='work';save()
    await manager.check();await manager.tick()
    assert len(calls)==2 and len(prepared)==1
    assert prepared[0]['unsupported']['supportedLoop'] is False
    assert failure in manager.diagnostics.state['events']
    assert not updates.active_release(manager.home)


async def test_environment_recovery_is_relevant_passive_and_uses_worker_precedence(manager, monkeypatch):
    import os
    from amplifier_web.host import config as host_config
    from amplifier_web.update_health import preparation_key
    shared=manager.home/'shared';shared.mkdir()
    monkeypatch.setattr(host_config,'amplifier_home',lambda:shared)
    monkeypatch.setattr(host_config,'_KEY_FILE_VALUES',{})
    monkeypatch.delenv('FIXTURE_INPUT',raising=False)
    host_config.write_private(shared/'settings.yaml',
        'config:\n  tools:\n  - module: tool-fixture\n    config: {value: \"${FIXTURE_INPUT}\"}\n')
    rows=[{**source(manager, 'included'),'latest':'b'*40}]
    absent=preparation_key(manager,rows)
    before=dict(os.environ)
    host_config.write_private(shared/'keys.env','FIXTURE_INPUT=first-secret\n')
    first=preparation_key(manager,rows)
    assert first==absent and dict(os.environ)==before  # Source-policy holds ignore credentials.
    assert host_config.missing_environment_available('FIXTURE_INPUT')
    host_config.write_private(shared/'keys.env','FIXTURE_INPUT=second-secret\nUNRELATED=rotated\n')
    second=preparation_key(manager,rows)
    assert second==first and len(second)==64 and 'secret' not in second
    monkeypatch.setenv('FIXTURE_INPUT','explicit-launch')
    explicit=preparation_key(manager,rows)
    host_config.write_private(shared/'keys.env','FIXTURE_INPUT=third-secret\nUNRELATED=rotated-again\n')
    assert preparation_key(manager,rows)==explicit
    monkeypatch.setattr(host_config,'_KEY_FILE_VALUES',{'FIXTURE_INPUT':'explicit-launch'})
    assert preparation_key(manager,rows)==explicit
    assert os.environ['FIXTURE_INPUT']=='explicit-launch'  # Scheduling never loads keys.


@pytest.mark.parametrize('bad', ['missing','malformed','attempt','candidate','unknown','source-policy','pointer','permissions','generic'])
async def test_environment_recovery_rejects_unbound_evidence(manager, monkeypatch, bad):
    from amplifier_web import update_health
    from amplifier_web.host import config as host_config
    shared=manager.home/'shared';shared.mkdir()
    monkeypatch.setattr(host_config,'amplifier_home',lambda:shared)
    host_config.write_private(shared/'keys.env','FIXTURE_MISSING=repaired\n')
    candidate='e'*32;attempt='f'*32
    stage=manager.directory/'releases'/candidate;stage.mkdir(parents=True,mode=0o700)
    batch={'attemptId':attempt,'retry':{'mode':'changed-inputs'},'preparation':{
        'candidate':candidate,'directoryIdentity':[stage.stat().st_dev,stage.stat().st_ino],
        'activeBefore':update_health.pointer_witness(manager)}}
    manager.diagnostics.state['lastFailure']={'attemptId':attempt,'reason':'missing-configured-environment'}
    error=host_config.MissingConfiguredEnvironment('FIXTURE_MISSING')
    if bad=='generic':error=ValueError('Configured environment variable FIXTURE_MISSING is not set')
    update_health.retain_environment_failure(error,stage,attempt)
    path=stage/'missing-environment.json'
    if bad=='missing':path.unlink()
    elif bad=='malformed':path.write_text('null')
    elif bad in {'attempt','candidate'}:
        data=json.loads(path.read_text());data['attemptId' if bad=='attempt' else 'candidate']='a'*32
        path.write_text(json.dumps(data))
    elif bad=='unknown':batch['retry']['mode']='inspect'
    elif bad=='source-policy':manager.diagnostics.state['lastFailure']['reason']='runtime-source-constraint'
    elif bad=='pointer':batch['preparation']['activeBefore']='changed'
    elif bad=='permissions':path.chmod(0o644)
    assert not update_health.environment_repaired(manager,batch)


@pytest.mark.parametrize('explicit', [False, True])
async def test_validate_probe_fresh_process_refreshes_file_managed_environment(manager, monkeypatch, explicit):
    import asyncio
    import os
    import sys
    from amplifier_web import runtime_environment, runtime_qualification, bundles, update_sources
    from amplifier_web.host import config as host_config
    stage=manager.directory/'releases'/('b'*32);stage.mkdir(parents=True)
    shared=stage/'shared-config';shared.mkdir()
    host_config.write_private(shared/'keys.env','FIXTURE_INPUT=new-file\n')
    (stage/'runtime.lock').write_text('fixture')
    project=stage/'project';project.mkdir()
    manager.inventory=[]
    manager.diagnostics.begin('ecosystem',stage.name)
    monkeypatch.setenv('FIXTURE_INPUT','explicit-launch' if explicit else 'old-file')
    monkeypatch.setattr(host_config,'_KEY_FILE_VALUES',{'FIXTURE_INPUT':'old-file'})
    async def staged(*args,**kw):return project
    monkeypatch.setattr(runtime_environment,'stage',staged)
    monkeypatch.setattr(runtime_environment,'receipt_directory',lambda *args:stage)
    monkeypatch.setattr(runtime_qualification,'verify_recorded',lambda *args:None)
    monkeypatch.setattr(bundles,'offered_profiles',lambda config:['work'])
    monkeypatch.setattr(update_sources,'worker_supports_shared',lambda project:False)
    seen=[]
    async def process(*args,env=None,**kwargs):
        assert any(str(arg).endswith('update_probe.py') for arg in args)
        # Use the actual validate() handoff environment in a fresh interpreter:
        # it starts with no knowledge of the parent's _KEY_FILE_VALUES.
        code=("import os,json; from pathlib import Path; from amplifier_web.host.config import _load_keys,expand_environment;"
              "_load_keys(Path(os.environ['AMPLIFIER_HOME'])/'keys.env');"
              "print(json.dumps(expand_environment('${FIXTURE_INPUT}')))")
        child=await asyncio.create_subprocess_exec(sys.executable,'-c',code,env=env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await child.communicate()
        assert child.returncode==0,err.decode()
        seen.append(json.loads(out))
        return ''
    monkeypatch.setattr(updates,'process',process)
    await manager.validate(stage,stage.name)
    assert seen==['explicit-launch' if explicit else 'new-file']
    assert os.environ['FIXTURE_INPUT']==('explicit-launch' if explicit else 'old-file')


async def test_actual_probe_keeps_bundle_missing_reference_private(manager, monkeypatch):
    import asyncio
    import os
    import sys
    from amplifier_web import update_diagnostics
    from amplifier_web.host.config import worker_environment
    import amplifier_web
    stage=manager.directory/'releases'/('d'*32);stage.mkdir(parents=True,mode=0o700)
    (stage/'profiles.json').write_text('["fixture"]')
    probe=Path(amplifier_web.__file__).with_name('update_probe.py')
    code = """
import asyncio,runpy,sys
from pathlib import Path
from types import SimpleNamespace
from amplifier_foundation import Bundle
from amplifier_web.host import session
from amplifier_web.host.config import read_config
async def prepare(workspace, **kwargs):
    bundle=Bundle(name='fixture',tools=[{'module':'tool-fixture','config':{'endpoint':'${BUNDLE_ENDPOINT}'}}])
    config=read_config(workspace,home=workspace,shared_home=Path(workspace)/'shared-config',global_only=True)
    session._apply_settings(bundle,config)
session.prepare_dependencies=prepare
probe,stage=sys.argv[1:]
sys.path.insert(0,str(Path(probe).parent))
sys.argv=[probe,stage,'fixture','--profiles',str(Path(stage)/'profiles.json'),'--global-only']
runpy.run_path(probe,run_name='__main__')
"""
    env={**worker_environment(),'AMPLIFIER_WEB_HOME':str(stage),'AMPLIFIER_HOME':str(stage/'shared-config'),
         'AMPLIFIER_UPDATE_ATTEMPT':'e'*32}
    env.pop('BUNDLE_ENDPOINT',None)
    child=await asyncio.create_subprocess_exec(sys.executable,'-c',code,str(probe),str(stage),
        env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await child.communicate()
    assert child.returncode==1,err.decode()
    facts=update_diagnostics.probe_record(out.decode())
    assert facts is not None,(out.decode(),err.decode())
    assert facts['reason']=='missing-configured-environment'
    assert b'BUNDLE_ENDPOINT' not in out
    private=json.loads((stage/'missing-environment.json').read_text())
    assert private=={'candidate':stage.name,'attemptId':'e'*32,'reference':'BUNDLE_ENDPOINT'}
