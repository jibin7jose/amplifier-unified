import os
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from amplifier_web.setup import SetupManager,validate_matrix
from amplifier_web.host.config import HostConfig, merge

@pytest.fixture
def manager(tmp_path,monkeypatch):
    manager=SetupManager(tmp_path/'home')
    def config(workspace):
        value={}
        for scope in ('global','project','local'):value=merge(value,manager.store.read(workspace,scope))
        result=HostConfig(home=manager.home,workspace=Path(workspace),
                          settings=value,registry_home=manager.home/'foundation')
        result.resolve_source=lambda reference:None
        return result
    monkeypatch.setattr(manager,'config',config)
    async def cached_catalog(workspace):pass
    monkeypatch.setattr(manager,'ensure_routing_catalog',cached_catalog)
    return manager

@pytest.mark.asyncio
async def test_keys_are_private_and_redacted_across_scopes(manager,tmp_path,monkeypatch):
    monkeypatch.delenv('AMPLIFIER_FIRST_API_KEY',raising=False)
    result=await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'first','config':{'model':'example','api_key':'secret-test'},'scope':'project'})
    assert result['providers'][0]['config']['api_key']=='[REDACTED]'
    assert 'secret-test' not in str(result)
    assert result['providers'][0]['credentialsConfigured']
    settings=manager.store.read(tmp_path,'project')
    assert settings['config']['providers'][0]['config']['api_key']=='${AMPLIFIER_FIRST_API_KEY}'
    keyfile=manager.store.shared_home/'keys.env'
    assert keyfile.stat().st_mode & 0o777 == 0o600
    assert 'secret-test' in keyfile.read_text()
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'first','config':{'model':'edited','api_key':'[REDACTED]'},'scope':'project'})
    assert manager.store.read(tmp_path,'project')['config']['providers'][0]['config']['api_key']=='${AMPLIFIER_FIRST_API_KEY}'


@pytest.fixture
def windows_key_storage(tmp_path, monkeypatch):
    """Model only the native boundary; no Win32 calls, disk secrets or real env."""
    from contextlib import contextmanager
    from amplifier_web import configuration_authority, setup, windows_files
    from amplifier_web.host import config as host_config

    shared = tmp_path / 'shared'
    state = SimpleNamespace(
        files={}, events=[], pinned=False, locked=False, unsafe=set(),
        fail_publish=False, fail_after_publish=False, fail_lock=False,
        concurrent_content=None, cache={}, environment={},
    )
    manager = object.__new__(SetupManager)
    manager.store = SimpleNamespace(shared_home=shared)
    state.manager = manager

    class Pinned:
        def read_private(self, name, *, limit):
            assert state.pinned and state.locked and name == 'keys.env'
            state.events.append(('read', name, limit))
            if name in state.unsafe:
                raise PermissionError('unsafe existing private object')
            if name not in state.files:
                raise FileNotFoundError(name)
            content = state.files[name]
            if len(content) > limit:
                raise ValueError('private-file read limit')
            return content

        def publish_private(self, name, content, *, replace=False):
            assert state.pinned
            assert name in {'keys.env', 'keys.env.lock'}
            assert state.locked is (name == 'keys.env')
            state.events.append(('publish', name, replace))
            if name in state.unsafe:
                raise PermissionError('unsafe existing private object')
            if name in state.files and not replace:
                raise FileExistsError(name)
            assert isinstance(content, bytes)
            if len(content) > 1024 * 1024:
                raise ValueError('private-file publication limit')
            if name == 'keys.env' and state.fail_publish:
                raise OSError('injected pre-publication failure')
            state.files[name] = content
            if name == 'keys.env' and state.fail_after_publish:
                raise OSError('private file was published; post-publication verification failed')

    @contextmanager
    def directory(path, *, owned_root, create=False):
        assert path == owned_root == shared and create is True
        state.events.append('directory-open')
        if 'directory' in state.unsafe:
            raise PermissionError('unsafe existing private directory')
        state.pinned = True
        try:
            yield Pinned()
        finally:
            assert not state.locked, 'directory unpinned before FileLock release'
            state.pinned = False
            state.events.append('directory-close')

    class Lock:
        def __init__(self, path, *, timeout):
            assert state.pinned and not state.locked
            assert path == str(shared / 'keys.env') + '.lock' and timeout == 10
            assert 'keys.env.lock' in state.files, 'FileLock created the lock first'
            state.events.append('lock-open')

        def __enter__(self):
            state.events.append('lock-enter')
            if state.fail_lock:
                raise TimeoutError('injected FileLock timeout')
            state.locked = True
            # Another writer can finish between preparation and acquisition.
            if state.concurrent_content is not None:
                state.files['keys.env'] = state.concurrent_content
            return self

        def __exit__(self, *exc):
            assert state.pinned and state.locked
            state.locked = False
            state.events.append('lock-exit')

    def no_atomic_write(*args, **kwargs):
        pytest.fail('Windows keys must not use the chmod-only atomic writer')

    # Deny ordinary path access only within this model's storage root.
    def guarded(original):
        def check(path, *args, **kwargs):
            if path == shared or shared in path.parents:
                pytest.fail('ordinary path access bypassed the Windows private boundary')
            return original(path, *args, **kwargs)
        return check

    for method in ('mkdir', 'exists', 'read_text', 'read_bytes', 'open', 'chmod', 'resolve', 'unlink'):
        monkeypatch.setattr(Path, method, guarded(getattr(Path, method)))
    monkeypatch.setattr(configuration_authority, 'check_current', lambda: state.events.append('authority'))
    monkeypatch.setattr(setup, '_WINDOWS', True)
    monkeypatch.setattr(setup, 'os', SimpleNamespace(environ=state.environment))
    monkeypatch.setattr(setup, 'FileLock', Lock)
    monkeypatch.setattr(setup, 'atomic_write', no_atomic_write)
    monkeypatch.setattr(windows_files, 'directory', directory)
    monkeypatch.setattr(host_config, '_KEY_FILE_VALUES', state.cache)
    return state


@pytest.mark.parametrize('existing_keys', [False, True])
@pytest.mark.parametrize('existing_lock', [False, True])
def test_windows_keys_private_create_read_utf8_and_lock_order(windows_key_storage, existing_keys, existing_lock):
    import shlex
    state = windows_key_storage
    prefix = "# kept comment\nOTHER='kept café'\n" if existing_keys else ''
    if existing_keys:
        state.files['keys.env'] = (prefix + "TARGET=old\nexport TARGET=duplicate\n").encode('utf-8')
    if existing_lock:
        state.files['keys.env.lock'] = b'previous lock'
    updates = {'TARGET': "fixture clé 'quote' 東京", 'ADDED': 'other fixture'}

    state.manager._keys(updates)

    expected = prefix + ''.join(name + '=' + shlex.quote(value) + '\n' for name, value in updates.items())
    assert state.files['keys.env'] == expected.encode('utf-8')
    assert state.files['keys.env.lock'] == (b'previous lock' if existing_lock else b'')
    assert state.cache == state.environment == updates
    assert state.events == [
        'authority', 'directory-open', ('publish', 'keys.env.lock', False),
        'lock-open', 'lock-enter', ('read', 'keys.env', 1024 * 1024),
        ('publish', 'keys.env', True), 'lock-exit', 'directory-close',
    ]
    assert not state.pinned and not state.locked


def test_windows_keys_merge_reads_after_lock_acquisition(windows_key_storage):
    state = windows_key_storage
    state.files['keys.env'] = b'OTHER=stale\nTARGET=old\n'
    state.concurrent_content = b'OTHER=concurrent-writer\nTARGET=old\n'
    state.manager._keys({'TARGET': 'replacement'})
    assert state.files['keys.env'] == b'OTHER=concurrent-writer\nTARGET=replacement\n'


def test_windows_keys_invalid_environment_batch_cannot_publish_or_partially_apply(windows_key_storage):
    state = windows_key_storage
    state.files['keys.env'] = b'TARGET=previous\n'
    class Environment(dict):
        def __setitem__(self, name, value):
            if '\x00' in value:
                raise ValueError('embedded null character')
            super().__setitem__(name, value)
        def update(self, values):
            for name, value in values.items():
                self[name] = value
    state.environment = Environment(TARGET='previous')
    from amplifier_web import setup
    setup.os.environ = state.environment
    state.cache['TARGET'] = 'previous'
    with pytest.raises(ValueError, match='Invalid Windows'):
        state.manager._keys({'ADDED': 'valid-first', 'TARGET': 'invalid\x00value'})
    assert state.files == {'keys.env': b'TARGET=previous\n'}
    assert state.environment == state.cache == {'TARGET': 'previous'}
    assert state.events == ['authority']


@pytest.mark.parametrize('unsafe', ['directory', 'keys.env.lock', 'keys.env'])
def test_windows_keys_refuse_unsafe_existing_without_repair_or_cache_update(windows_key_storage, unsafe):
    state = windows_key_storage
    state.files.update({'keys.env': b'TARGET=previous\n', 'keys.env.lock': b'previous lock'})
    state.cache['TARGET'] = state.environment['TARGET'] = 'previous'
    before = dict(state.files)
    state.unsafe.add(unsafe)

    with pytest.raises(PermissionError, match='unsafe existing'):
        state.manager._keys({'TARGET': 'replacement'})

    assert state.files == before
    assert state.cache == state.environment == {'TARGET': 'previous'}
    assert ('publish', 'keys.env', True) not in state.events
    if unsafe != 'keys.env':
        assert 'lock-open' not in state.events
    assert not state.pinned and not state.locked


@pytest.mark.parametrize('failure', ['publish', 'decode', 'read-limit', 'write-limit', 'lock'])
def test_windows_keys_failure_preserves_previous_secret_and_cache(windows_key_storage, failure):
    state = windows_key_storage
    previous = b'TARGET=previous\n'
    if failure == 'decode':
        previous += b'# invalid UTF-8: \xff\n'
    elif failure == 'read-limit':
        previous += b'#' * (1024 * 1024)
    state.files.update({'keys.env': previous, 'keys.env.lock': b'previous lock'})
    state.cache['TARGET'] = state.environment['TARGET'] = 'previous'
    state.fail_publish = failure == 'publish'
    state.fail_lock = failure == 'lock'
    replacement = 'x' * (1024 * 1024) if failure == 'write-limit' else 'replacement'
    error = {
        'publish': OSError, 'decode': UnicodeDecodeError, 'read-limit': ValueError,
        'write-limit': ValueError, 'lock': TimeoutError,
    }[failure]

    with pytest.raises(error):
        state.manager._keys({'TARGET': replacement, 'ADDED': 'must-not-appear'})

    assert state.files == {'keys.env': previous, 'keys.env.lock': b'previous lock'}
    assert state.cache == state.environment == {'TARGET': 'previous'}
    if failure in {'decode', 'read-limit', 'lock'}:
        assert ('publish', 'keys.env', True) not in state.events
    assert not state.pinned and not state.locked


def test_windows_keys_report_post_publication_failure_without_retry_or_cache_update(windows_key_storage):
    state = windows_key_storage
    state.files['keys.env'] = b'TARGET=previous\n'
    state.cache['TARGET'] = state.environment['TARGET'] = 'previous'
    state.fail_after_publish = True

    with pytest.raises(OSError, match='was published'):
        state.manager._keys({'TARGET': 'replacement'})

    # The existing helper can report a failure after rename. Do not roll back,
    # retry, or claim the prior file survived that distinct outcome.
    assert state.files['keys.env'] == b'TARGET=replacement\n'
    assert state.cache == state.environment == {'TARGET': 'previous'}
    assert state.events.count(('publish', 'keys.env', True)) == 1
    assert not state.pinned and not state.locked


@pytest.mark.asyncio
async def test_nested_image_configuration_roundtrips_without_replacing_chat_instance(manager,tmp_path,monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config={'default_model':'kept-chat-model','reasoning_effort':'high','image_generation':{'enabled':True,'id':'images','model':'chosen-image-model'}}
    result=await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'kept-instance','config':config,'scope':'project'})
    row=next(row for row in result['providers'] if row['id']=='kept-instance')
    assert row['config']==config
    saved=manager.store.read(tmp_path,'project')['config']['providers'][0]
    assert saved['id']=='kept-instance' and saved['config']==config


@pytest.mark.asyncio
async def test_provider_removal_tombstones_inherited_instance(manager,tmp_path):
    args={'workspace':str(tmp_path),'module':'provider-test','id':'one','config':{}}
    await manager.perform('providers.save',args)
    result=await manager.perform('providers.remove',{'workspace':str(tmp_path),'id':'one','scope':'local'})
    assert result['providers'][0]['enabled'] is False
    assert 'one' not in manager.store.read(tmp_path,'global')['configurator']['disabled']['providers']
    assert manager.store.read(tmp_path,'local')['configurator']['disabled']['providers'] == ['one']


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['providers.finishSetup', 'providers.save'])
async def test_model_edit_retains_existing_local_provider_source(manager,tmp_path,action):
    source=str(tmp_path/'local-provider')
    original={'id':'one','module':'provider-local','source':source,
              'config':{'default_model':'before','custom':{'kept':True}}}
    manager.store.update(tmp_path,'global',lambda settings:settings.update(config={'providers':[original]}))
    args={'workspace':str(tmp_path),'id':'one','model':'after'} if action=='providers.finishSetup' else {
        'workspace':str(tmp_path),'id':'one','module':'provider-local','source':source,
        'config':{'default_model':'after','custom':{'kept':True}}}
    await manager.perform(action,args)
    saved=manager.store.read(tmp_path,'global')['config']['providers'][0]
    assert saved=={**original,'config':{'default_model':'after','custom':{'kept':True}}}


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['source','module'])
async def test_existing_local_provider_does_not_bypass_new_source_admission(manager,tmp_path,change):
    original={'id':'one','module':'provider-local','source':str(tmp_path/'local-provider'),'config':{}}
    manager.store.update(tmp_path,'global',lambda settings:settings.update(config={'providers':[original]}))
    before=manager.store.read(tmp_path,'global')
    args={'workspace':str(tmp_path),**original}
    args[change]=str(tmp_path/'different-provider') if change=='source' else 'provider-different'
    with pytest.raises(ValueError,match='HTTPS Git bundle URI'):
        await manager.perform('providers.save',args)
    assert manager.store.read(tmp_path,'global')==before

@pytest.mark.asyncio
async def test_models_and_test_use_isolated_provider_without_starting_session(manager,tmp_path,monkeypatch):
    import sys
    monkeypatch.setenv('TEAM_CHECK_KEY','secret-fixture-value')
    await manager.perform('providers.save',{'id':'one','module':'provider-openai','config':{'base_url':'https://example.test/v1'},'apiKeyEnv':'TEAM_CHECK_KEY','workspace':str(tmp_path)})
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']['api_key']=='${TEAM_CHECK_KEY}'; assert r['config']['base_url']=='https://example.test/v1'; print(json.dumps({'info':{},'configSchema':{'fields':[]},'models':[{'id':'discovered'}],'test':{'reachable':True,'modelCount':1}}))")
    manager.probe_command=[sys.executable,str(child)]
    async def runtime(*args):raise AssertionError('Must not start a conversation')
    manager.runtime_operation=runtime
    result=await manager.perform('providers.models',{'id':'one','workspace':str(tmp_path),'sessionId':'s'})
    assert result['models'][0]['id']=='discovered' and result['modelsProviderId']=='one'
    assert 'secret-fixture-value' not in str(result)
    result=await manager.perform('providers.test',{'id':'one','workspace':str(tmp_path),'sessionId':'s'})
    assert result['test']['reachable'] and result['test']['providerId']=='one'
    assert manager.store.read(tmp_path)['config']['providers'][0]['config']['api_key']=='${TEAM_CHECK_KEY}'

@pytest.mark.asyncio
async def test_schema_does_not_need_configured_credentials(manager,tmp_path):
    import sys
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']=={}; print(json.dumps({'info':{},'configSchema':{'fields':[{'id':'effort','choices':['low','high']}]}}))")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.perform('providers.schema',{'module':'provider-openai','workspace':str(tmp_path)})
    assert result['providerMetadata']['configSchema']['fields'][0]['choices']==['low','high']


@pytest.mark.asyncio
async def test_schema_uses_only_requested_instances_endpoint_and_no_saved_secrets(manager,tmp_path):
    import sys
    for identity in ('one', 'two'):
        await manager.perform('providers.save', {'id': identity, 'module': 'provider-vllm',
            'config': {'base_url': f'https://{identity}.test/v1/', 'custom_secret': 'private-value'},
            'apiKeyEnv': 'UNSET_SCHEMA_KEY', 'workspace': str(tmp_path)})
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']=={'base_url':'https://two.test/v1/'}; print(json.dumps({'info':{},'configSchema':{'fields':[]}}))")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.perform('providers.schema', {'module': 'provider-vllm', 'id': 'two', 'workspace': str(tmp_path)})
    assert result['providerMetadata']['configSchema']=={'fields':[]}
    # Requesting another module cannot borrow the saved account's endpoint.
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']=={}; print(json.dumps({'info':{},'configSchema':{'fields':[]}}))")
    await manager.perform('providers.schema', {'module': 'provider-openai', 'id': 'two', 'workspace': str(tmp_path)})

@pytest.mark.asyncio
async def test_provider_probe_errors_are_visible(manager,tmp_path):
    import sys
    child=tmp_path/'probe.py';child.write_text("import json; print(json.dumps({'error':'private-provider-body', 'diagnostic':{'stage':'metadata','category':'AuthenticationError'}}))")
    manager.probe_command=[sys.executable,str(child)]
    with pytest.raises(ValueError,match='AuthenticationError'):
        await manager.perform('providers.schema',{'module':'provider-openai','workspace':str(tmp_path)})


@pytest.mark.parametrize('exit_code,output', [(1, ''), (0, 'not-json')])
async def test_provider_probe_missing_result_does_not_claim_installation_is_running(manager,tmp_path,exit_code,output):
    import sys
    child=tmp_path/'failed-probe.py'
    child.write_text(f'import sys; print({output!r}, end=""); print("credential-must-not-leak", file=sys.stderr); sys.exit({exit_code})')
    manager.probe_command=[sys.executable,str(child)]
    with pytest.raises(ValueError) as failure:
        await manager.perform('providers.schema',{'module':'provider-openai','workspace':str(tmp_path)})
    message=str(failure.value)
    assert f'exit code {exit_code}' in message
    assert 'update the app' in message
    assert 'dependencies finish installing' not in message
    assert 'credential-must-not-leak' not in message


MATRIX={'description':'Custom policy','roles':{'general':{'description':'General','candidates':[{'provider':'one','model':'gpt-*','config':{'reasoning_effort':'high'}}]},'fast':{'description':'Quick','candidates':[{'provider':'one','model':'small'}]}}}

@pytest.mark.asyncio
async def test_routing_roundtrip_and_dynamic_roles(manager,tmp_path):
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'personal','matrix':MATRIX})
    await manager.perform('routing.use',{'workspace':str(tmp_path),'name':'personal'})
    result=await manager.perform('routing.show',{'workspace':str(tmp_path),'name':'personal'})
    assert result['matrix']['roles']==MATRIX['roles']
    listing=await manager.perform('routing.list',{'workspace':str(tmp_path)})
    assert listing['active']=='personal'
    assert listing['roles']==['fast','general']

@pytest.mark.parametrize('value',[{}, {'roles':{'general':{}}}, {'roles':{'general':{'description':'x','candidates':['base']},'fast':{'description':'x','candidates':[]}}}])
def test_matrix_validation_rejects_invalid_community_contract(value):
    with pytest.raises(ValueError):validate_matrix(value)

@pytest.mark.asyncio
async def test_login_progress_and_cancel_preserve_owned_path(manager,tmp_path):
    import asyncio,sys
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai-chatgpt','id':'chatgpt','config':{}})
    script=tmp_path/'auth-fixture.py'
    script.write_text('import sys,json,time\nr=json.loads(sys.stdin.readline())\nassert "/config/chatgpt-sign-in/chatgpt/" in r["tokenFile"]\nprint(json.dumps({"status":"waiting","instruction":"Open https://auth.openai.com/codex/device and enter TEST-CODE"}),flush=True)\ntime.sleep(30)\n')
    manager.auth_command=[sys.executable,str(script)]
    updates=[]
    async def progress(value):updates.append(value)
    manager.progress=progress
    initial=await manager.perform('providers.login',{'id':'chatgpt','workspace':str(tmp_path)})
    assert initial['login']['status']=='starting'
    for _ in range(100):
        if any(row['login'].get('url') for row in updates):break
        await asyncio.sleep(.01)
    assert updates[-1]['login']['url']=='https://auth.openai.com/codex/device'
    cancelled=await manager.perform('providers.loginCancel',{'id':'chatgpt','workspace':str(tmp_path)})
    assert cancelled['login']['status']=='cancelled'
    assert manager.logins['chatgpt']['process'].returncode is not None
    await manager.close()


@pytest.mark.asyncio
async def test_login_missing_helper_is_actionable_without_exposing_provider_errors(manager,tmp_path,monkeypatch):
    from amplifier_web import host_dependencies
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai-chatgpt','id':'chatgpt','config':{}})
    monkeypatch.setenv('HOME',str(tmp_path/'empty-home'))
    monkeypatch.setenv('PATH',str(tmp_path/'empty-bin'))
    monkeypatch.setattr(host_dependencies.sys,'platform','linux')
    await manager.perform('providers.login',{'id':'chatgpt','workspace':str(tmp_path)})
    await manager.logins['chatgpt']['task']
    result=manager.login_state('chatgpt')
    assert result['status']=='failed' and 'setup helper (uv)' in result['error']
    assert 'same user' in result['error'] and 'process' not in manager.logins['chatgpt']
    assert not list((manager.home/'config/chatgpt-sign-in').rglob('*.json'))
    await manager.close()


@pytest.mark.asyncio
async def test_api_key_setup_reports_same_missing_host_helper(manager,tmp_path,monkeypatch):
    from amplifier_web.host_dependencies import MissingHostDependency
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'api','config':{'api_key':'private-fixture-key'}})
    monkeypatch.setenv('HOME',str(tmp_path/'empty-home'))
    monkeypatch.setenv('PATH',str(tmp_path/'empty-bin'))
    monkeypatch.setattr('sys.platform','linux')
    with pytest.raises(MissingHostDependency,match='setup helper') as error:
        await manager.probe('providers.models',{'id':'api'},str(tmp_path))
    assert 'private-fixture-key' not in str(error.value)
    await manager.close()

@pytest.mark.asyncio
async def test_local_routing_shadows_project_without_mutating_it(manager,tmp_path):
    import copy
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','scope':'project','matrix':MATRIX})
    local=copy.deepcopy(MATRIX);local['description']='Local only'
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','scope':'local','matrix':local})
    result=await manager.perform('routing.show',{'workspace':str(tmp_path),'name':'custom'})
    assert result['matrix']['description']=='Local only'
    assert yaml.safe_load((tmp_path/'.amplifier/routing/custom.yaml').read_text())['description']=='Custom policy'

@pytest.mark.asyncio
async def test_default_env_is_detected_and_saved_as_reference_without_copying_secret(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','existing-private-key')
    status=await manager.perform('providers.credentials',{'module':'provider-openai','workspace':str(tmp_path)})
    assert status['credentialCheck']['defaultEnvVar']=='OPENAI_API_KEY'
    assert status['credentialCheck']['available']
    result=await manager.perform('providers.save',{'module':'provider-openai','config':{},'workspace':str(tmp_path)})
    row=result['providers'][0]
    assert row['credentialsConfigured'] and row['credential']['envVar']=='OPENAI_API_KEY'
    assert manager.store.read(tmp_path)['config']['providers'][0]['config']['api_key']=='${OPENAI_API_KEY}'
    assert not (manager.store.shared_home/'keys.env').exists()
    assert 'existing-private-key' not in str(status)+str(result)

@pytest.mark.asyncio
async def test_custom_env_overrides_saved_key_and_missing_values_are_honest(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('TEAM_MODEL_KEY','private-team-key')
    monkeypatch.delenv('MISSING_MODEL_KEY',raising=False)
    args={'module':'provider-anthropic','id':'team','config':{},'workspace':str(tmp_path)}
    await manager.perform('providers.save',{**args,'apiKey':'old-private-key'})
    result=await manager.perform('providers.save',{**args,'apiKeyEnv':'TEAM_MODEL_KEY'})
    assert result['providers'][0]['credential']['available']
    assert manager.store.read(tmp_path)['config']['providers'][0]['config']['api_key']=='${TEAM_MODEL_KEY}'
    from amplifier_web.host.config import expand_environment
    assert expand_environment(manager.store.read(tmp_path)['config']['providers'][0]['config'])['api_key']=='private-team-key'
    result=await manager.perform('providers.save',{**args,'apiKeyEnv':'MISSING_MODEL_KEY'})
    assert not result['providers'][0]['credentialsConfigured']
    assert 'private-team-key' not in str(result)
    with pytest.raises(ValueError):await manager.perform('providers.save',{**args,'apiKeyEnv':'BAD-NAME'})
    with pytest.raises(ValueError):await manager.perform('providers.save',{**args,'apiKeyEnv':'TEAM_MODEL_KEY','apiKey':'ambiguous'})

@pytest.mark.asyncio
async def test_implicit_environment_and_provider_fallback_are_reported_without_values(manager,tmp_path,monkeypatch):
    monkeypatch.delenv('GOOGLE_API_KEY',raising=False)
    monkeypatch.setenv('GEMINI_API_KEY','private-google-key')
    manager.store.update(tmp_path,'global',lambda s:s.update(config={'providers':[{'module':'provider-gemini','config':{}}]}))
    row=manager.provider_rows(tmp_path)[0]
    assert row['credentialsConfigured'] and row['credential']['envVar']=='GEMINI_API_KEY'
    assert row['credential']['defaultEnvVar']=='GOOGLE_API_KEY'
    assert 'private-google-key' not in str(row)

@pytest.mark.asyncio
async def test_probe_references_implicit_environment_credential_without_serializing_its_value(manager,tmp_path,monkeypatch):
    import sys
    monkeypatch.setenv('OPENAI_API_KEY','private-openai-key')
    manager.store.update(tmp_path,'global',lambda s:s.update(config={'providers':[{'id':'one','module':'provider-openai','config':{}}]}))
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['config']['api_key']=='${OPENAI_API_KEY}'; print(json.dumps({'info':{},'configSchema':{'fields':[]},'models':[{'id':'discovered'}]}))")
    manager.probe_command=[sys.executable,str(child)]
    result=await manager.perform('providers.models',{'id':'one','workspace':str(tmp_path)})
    assert result['models']==[{'id':'discovered'}]


def test_explicit_copilot_environment_choice_wins_over_ambient_default(monkeypatch):
    from amplifier_web.host import session
    monkeypatch.setattr(session,'_copilot_credential',None)
    monkeypatch.setenv('COPILOT_AGENT_TOKEN','ambient')
    session.apply_provider_environment({'providers':[{'module':'provider-github-copilot','config':{'github_token':'selected'}}]})
    assert os.environ['COPILOT_AGENT_TOKEN']=='selected'
    with pytest.raises(ValueError,match='separate conversations'):
        session.apply_provider_environment({'providers':[{'module':'provider-github-copilot','config':{'github_token':'different'}}]})

def test_copilot_credentials_include_nested_agent_providers(monkeypatch):
    from amplifier_web.host import session
    monkeypatch.setattr(session,'_copilot_credential',None)
    with pytest.raises(ValueError,match='separate conversations'):
        session.apply_provider_environment({
            'providers':[{'module':'provider-github-copilot','config':{'github_token':'root'}}],
            'agents':{'worker':{'providers':[{'module':'provider-github-copilot','config':{'github_token':'nested'}}]}},
        })


@pytest.mark.asyncio
async def test_shared_key_file_is_detected_without_returning_value(tmp_path,monkeypatch):
    name='UNIFIED_TEST_SAVED_KEY'
    monkeypatch.delenv(name,raising=False)
    home=tmp_path/'home';(home/'config').mkdir(parents=True)
    shared=Path(os.environ['AMPLIFIER_HOME']);shared.mkdir()
    (shared/'keys.env').write_text(name+'=private-file-value\n')
    manager=SetupManager(home)
    result=await manager.perform('providers.credentials',{'module':'provider-openai','envVar':name,'workspace':str(tmp_path)})
    assert result['credentialCheck']['available'] and 'private-file-value' not in str(result)
    monkeypatch.delenv(name)

@pytest.mark.asyncio
async def test_provider_order_persists_without_copying_inherited_credentials(tmp_path):
    from amplifier_web.host.config import write_private
    home=tmp_path/'home'
    write_private(Path(os.environ['AMPLIFIER_HOME'])/'settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':name,'module':'provider-openai','config':{'api_key':'${PRIVATE_KEY}'}} for name in ['one','two','three']]}}))
    manager=SetupManager(home)
    result=await manager.perform('providers.move',{'workspace':str(tmp_path),'scope':'project','id':'one','beforeId':None})
    assert [row['id'] for row in result['providers']]==['two','three','one']
    saved=manager.store.read(tmp_path,'project')
    assert 'provider_order' not in saved
    assert [row['config'] for row in saved['config']['providers']]==[{'priority':1},{'priority':2},{'priority':3}]
    assert 'PRIVATE_KEY' not in str(saved)
    with pytest.raises(ValueError):await manager.perform('providers.move',{'workspace':str(tmp_path),'id':'missing'})

@pytest.mark.asyncio
async def test_active_routing_can_be_loaded_edited_saved_and_used(manager,tmp_path):
    await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','matrix':MATRIX,'activate':True})
    active=await manager.perform('routing.use',{'workspace':str(tmp_path),'name':'custom'})
    assert active['active']=='custom' and active['matrix']['name']=='custom'
    edited=active['matrix'];edited['roles']['general']['candidates'][0]['model']='edited-model'
    result=await manager.perform('routing.save',{'workspace':str(tmp_path),'name':'custom','matrix':edited,'activate':True})
    assert result['matrix']['roles']['general']['candidates'][0]['model']=='edited-model'
    assert manager.store.read(tmp_path)['routing']['matrix']=='custom'


async def test_complete_provider_order_is_atomic_and_rejects_stale_list(tmp_path):
    manager=SetupManager(tmp_path)
    args={'workspace':str(tmp_path)}
    for name in ('one','two','three'):
        await manager.perform('providers.save',{**args,'id':name,'module':'provider-test','config':{'custom':name}})
    result=await manager.perform('providers.reorder',{**args,'ids':['three','one','two'],'expectedIds':['one','two','three']})
    assert [row['id'] for row in result['providers']]==['three','one','two']
    assert result['providers'][0]['config']['custom']=='three'
    before=manager.store.read(tmp_path)
    with pytest.raises(ValueError,match='changed'):
        await manager.perform('providers.reorder',{**args,'ids':['two','one','three'],'expectedIds':['one','two','three']})
    assert manager.store.read(tmp_path)==before
    with pytest.raises(ValueError):
        await manager.perform('providers.reorder',{**args,'ids':['three','three','two'],'expectedIds':['three','one','two']})


async def test_model_discovery_for_a_future_workspace_does_not_create_it(manager,tmp_path):
    import sys
    await manager.perform('providers.save',{'workspace':str(tmp_path),'module':'provider-openai','id':'one','config':{}})
    child=tmp_path/'draft-probe.py'
    child.write_text("import os,json; print(json.dumps({'info':{},'configSchema':{'fields':[]},'models':[{'id':os.getcwd()}]}))")
    manager.probe_command=[sys.executable,str(child)]
    future=tmp_path/'not-created'/'project'
    result=await manager.perform('providers.models',{'workspace':str(future),'id':'one'})
    assert result['models']==[{'id':str(tmp_path)}]
    assert not future.parent.exists()

@pytest.mark.asyncio
async def test_guided_setup_selects_balanced_once_and_preserves_private_config(manager,tmp_path):
    workspace=str(tmp_path)
    await manager.perform('providers.save',{'workspace':workspace,'id':'first','module':'provider-openai','apiKey':'private-guided-key','config':{'opaque':{'keep':True}}})
    args={'workspace':workspace,'id':'first','model':'chosen-model','initializeRouting':True}
    result=await manager.perform('providers.finishSetup',args)
    assert result['setupCompletion']['routingCreated'] is False
    assert result['setupCompletion']['routingSelected']=='balanced'
    assert result['takesEffect']=='new_sessions'
    active=result['active']
    assert active=='balanced'
    assert not list((manager.store.shared_home/'routing').glob('my-ai-*.yaml'))
    saved=manager.store.read(workspace)['config']['providers'][0]['config']
    assert saved['opaque']=={'keep':True}
    assert saved['api_key']=='${AMPLIFIER_FIRST_API_KEY}'
    assert saved['default_model']=='chosen-model'
    assert 'private-guided-key' not in str(result)
    # A retry or later connection edit cannot reset a user's model rules.
    retry=await manager.perform('providers.finishSetup',{**args,'model':'another-model'})
    assert retry['setupCompletion']['routingCreated'] is False
    assert manager.routing(workspace)['active']==active

@pytest.mark.asyncio
@pytest.mark.parametrize('protection',['custom-balanced','explicit-profile','multiple-providers'])
async def test_guided_setup_never_replaces_existing_routing(manager,tmp_path,protection):
    workspace=str(tmp_path)
    await manager.perform('providers.save',{'workspace':workspace,'id':'one','module':'provider-openai','config':{'default_model':'before'}})
    if protection=='multiple-providers':
        await manager.perform('providers.save',{'workspace':workspace,'id':'two','module':'provider-openai','config':{}})
    else:
        await manager.perform('routing.save',{'workspace':workspace,'name':'balanced' if protection=='custom-balanced' else 'personal','matrix':MATRIX,'activate':protection=='explicit-profile'})
    before=manager.routing(workspace)
    result=await manager.perform('providers.finishSetup',{'workspace':workspace,'id':'one','model':'after','initializeRouting':True})
    assert not result['setupCompletion']['routingCreated']
    assert manager.routing(workspace)==before

@pytest.mark.asyncio
async def test_guided_setup_rejects_blank_or_missing_connection_without_writes(manager,tmp_path):
    for args,message in [({'id':'missing','model':'chosen'},'connection changed'),({'id':'missing','model':'  '},'Choose a model')]:
        with pytest.raises(ValueError,match=message):
            await manager.perform('providers.finishSetup',{'workspace':str(tmp_path),**args})
    assert manager.store.read(str(tmp_path))=={}

@pytest.mark.asyncio
async def test_credential_discovery_and_explicit_account_choice(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','environment-account')
    result=await manager.perform('providers.credentials',{'workspace':str(tmp_path),'module':'provider-anthropic'})
    assert result['credentialCheck']['available'] and 'environment-account' not in str(result)
    args={'workspace':str(tmp_path),'module':'provider-anthropic','config':{}}
    await manager.perform('providers.save',{**args,'id':'env-account','apiKeyEnv':'ANTHROPIC_API_KEY'})
    assert not (manager.store.shared_home/'keys.env').exists()
    await manager.perform('providers.save',{**args,'id':'other-account','apiKey':'different-account'})
    rows=manager.config(tmp_path).providers
    assert rows[0]['config']['api_key']=='${ANTHROPIC_API_KEY}'
    assert rows[1]['config']['api_key']=='${AMPLIFIER_OTHER_ACCOUNT_API_KEY}'
    assert 'different-account' not in str(manager.provider_rows(tmp_path))
    assert os.environ['ANTHROPIC_API_KEY']=='environment-account'

@pytest.mark.asyncio
async def test_github_cli_discovery_is_metadata_only_and_save_is_explicit(manager,tmp_path,monkeypatch):
    monkeypatch.setattr('amplifier_web.setup.github_cli_token',lambda:'cli-account-token')
    args={'workspace':str(tmp_path),'module':'provider-github-copilot'}
    result=await manager.perform('providers.credentials',args)
    assert result['credentialCheck']['githubCliAvailable']
    assert 'cli-account-token' not in str(result)
    assert not (manager.store.shared_home/'keys.env').exists()
    result=await manager.perform('providers.save',{**args,'id':'copilot','config':{},'useGitHubCli':True})
    assert 'cli-account-token' not in str(result)
    assert manager.config(tmp_path).providers[0]['config']['github_token']=='${AMPLIFIER_COPILOT_GITHUB_TOKEN}'
    monkeypatch.setattr('amplifier_web.setup.github_cli_token',lambda:None)
    with pytest.raises(ValueError,match='no longer available'):
        await manager.perform('providers.save',{**args,'id':'missing','config':{},'useGitHubCli':True})

def test_account_status_checks_saved_provider_tokens_without_exposing_them(tmp_path):
    from amplifier_web.setup import account_connected
    path=tmp_path/'oauth.json';config={'token_file_path':str(path)}
    assert not account_connected(config)
    path.write_text('{"access_token":"private","refresh_token":"private-refresh"}')
    assert account_connected(config)
    path.write_text('{"auth_mode":"oauth","access_token":"private","refresh_token":"private-refresh"}')
    assert account_connected(config)
    assert not account_connected({**config,'auth_mode':'chatgpt_plan'})
    path.write_text('{"access_token":"expired","expires_at":"2000-01-01T00:00:00+00:00"}')
    assert not account_connected(config)
    path.write_text('[]');assert not account_connected(config)


def test_plan_account_uses_provider_default_profile(tmp_path,monkeypatch):
    from amplifier_web.setup import account_status,chatgpt_token_path
    import json
    monkeypatch.setenv('HOME',str(tmp_path))
    path=tmp_path/'.amplifier/chatgpt-plan/default.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'auth_mode':'chatgpt_plan','access_token':'private','refresh_token':'private-refresh',
        'subject':'subject','client_id':'client','scopes':['chatgpt.tokens.use.direct']}))
    assert chatgpt_token_path({'auth_mode':'chatgpt_plan'})=='~/.amplifier/chatgpt-plan/default.json'
    assert account_status({'auth_mode':'chatgpt_plan'})['planEnabled']
    assert not account_status({})['connected']

@pytest.mark.asyncio
async def test_removed_connections_are_excluded_from_preference_order(manager,tmp_path):
    args={'workspace':str(tmp_path),'module':'provider-test','config':{}}
    for identity in ('a','b','c'):await manager.perform('providers.save',{**args,'id':identity})
    await manager.perform('providers.remove',{'workspace':str(tmp_path),'id':'b'})
    result=await manager.perform('providers.reorder',{'workspace':str(tmp_path),'ids':['c','a'],'expectedIds':['a','c']})
    assert not next(row for row in result['providers'] if row['id']=='b')['enabled']


async def test_explicit_test_retries_failed_probe_without_waiting_for_catalog_ttl(manager, tmp_path):
    import sys
    await manager.perform('providers.save', {'id': 'retry', 'module': 'provider-openai', 'config': {}, 'workspace': str(tmp_path)})
    child = tmp_path / 'probe-retry.py'
    child.write_text("import json; print(json.dumps({'error':'First attempt failed'}))")
    manager.probe_command = [sys.executable, str(child)]
    args = {'id': 'retry', 'workspace': str(tmp_path)}
    with pytest.raises(ValueError, match='First attempt'):
        await manager.perform('providers.test', args)
    child.write_text("import json; print(json.dumps({'info':{},'configSchema':{},'test':{'reachable':True,'modelCount':2}}))")
    assert (await manager.perform('providers.test', args))['test']['reachable']


async def test_probe_drains_large_stderr_and_reports_safe_build_evidence(manager, tmp_path):
    import sys
    child = tmp_path / 'probe-build.py'
    child.write_text("import sys; sys.stderr.write('secret-do-not-copy' * 100000); sys.stderr.write('rustc 1.91.1 is not supported; private-dependency requires rustc 1.92.0'); sys.exit(1)")
    manager.probe_command = [sys.executable, str(child)]
    with pytest.raises(ValueError) as failure:
        await manager.perform('providers.schema', {'module': 'provider-openai', 'workspace': str(tmp_path)})
    message = str(failure.value)
    assert 'Rust 1.91.1 is unsupported' in message
    assert 'requires Rust 1.92.0' in message
    assert 'secret-do-not-copy' not in message and 'private-dependency' not in message
    assert len(message) < 500

@pytest.mark.parametrize('mode,stored_mode,scopes,ready',[
    ('chatgpt_plan','chatgpt_plan',['chatgpt.tokens.use.direct'],True),
    ('chatgpt_plan','chatgpt_plan',[],False),
    ('chatgpt_plan','legacy_codex',['chatgpt.tokens.use.direct'],False),
    ('legacy_codex','chatgpt_plan',['chatgpt.tokens.use.direct'],False),
])
def test_chatgpt_account_distinguishes_identity_from_plan_permission(tmp_path,mode,stored_mode,scopes,ready):
    import json,time
    from amplifier_web.setup import account_status
    path=tmp_path/'profile.json'
    path.write_text(json.dumps({'auth_mode':stored_mode,'access_token':'private-access','refresh_token':'private-refresh',
        'id_token':'private-jwt','subject':'subject','client_id':'client','email':'account@example.test',
        'expires_at':time.time()+300,'scopes':scopes}))
    status=account_status({'auth_mode':mode,'token_file_path':str(path)})
    assert status['planEnabled'] is ready
    if mode==stored_mode=='chatgpt_plan':assert status['connected']
    else:assert not status['connected']
    assert 'private-' not in str(status)
    assert 'subject' not in status and 'client_id' not in status


def test_chatgpt_authorization_url_never_exposes_identity_hints_or_tokens():
    from amplifier_web.setup import login_url
    allowed='https://auth.openai.com/api/accounts/authorize?state=s&code_challenge=c'
    assert login_url(allowed)==allowed
    for suffix in ('&id_token_hint=private','&access_token=private','&code=private','#token','&ID_TOKEN=private'):
        assert login_url(allowed+suffix) is None
    for url in ('https://auth.openai.com.evil.test/','https://name:private@auth.openai.com/','http://auth.openai.com/'):
        assert login_url(url) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['success','failure','removed','edited','permission-missing','exit-failure','cancel','timeout','adopted','adopted-disabled'])
@pytest.mark.parametrize('existing_mode',['legacy_codex','chatgpt_plan'])
async def test_plan_login_commits_only_successful_unchanged_connection(manager,tmp_path,outcome,existing_mode,monkeypatch):
    import asyncio,json,sys
    original=tmp_path/'original-custom-tokens.json'
    original_tokens={'access_token':'legacy-private','refresh_token':'legacy-refresh'}
    if existing_mode=='chatgpt_plan':original_tokens.update(auth_mode='chatgpt_plan',subject='subject',client_id='client',scopes=['chatgpt.tokens.use.direct'])
    original.write_text(json.dumps(original_tokens))
    if outcome=='timeout':
        real_timeout=asyncio.timeout
        monkeypatch.setattr(asyncio,'timeout',lambda delay:real_timeout(.2 if delay==900 else delay))
    args={'workspace':str(tmp_path),'module':'provider-openai-chatgpt','id':'chatgpt','scope':'local',
        'config':{'auth_mode':existing_mode,'token_file_path':str(original),'default_model':'kept'}}
    if existing_mode=='chatgpt_plan':args['config']['host_file_path']=str(tmp_path/'import-host.json')
    await manager.perform('providers.save',args)
    before=manager.store.read(tmp_path,'local')
    script=tmp_path/'plan-fixture.py';gate=tmp_path/'continue'
    script.write_text('''import sys,json,time,pathlib
r=json.loads(sys.stdin.readline())
assert r['authMode']=='chatgpt_plan'
p=pathlib.Path(r['tokenFile']);p.parent.mkdir(parents=True,exist_ok=True)
p.write_text(pathlib.Path(r['sourceTokenFile']).read_text() if r.get('sourceTokenFile') else '{}')
for key in ('registrationFile','hostFile'):
 f=pathlib.Path(r[key]);f.parent.mkdir(parents=True,exist_ok=True);f.write_text('{"client_id":"stable-registration"}')
print(json.dumps({'status':'waiting','instruction':'Open https://auth.openai.com/api/accounts/authorize?state=s&code_challenge=c'}),flush=True)
print(json.dumps({'status':'waiting','instruction':'identity private-jwt https://auth.openai.com/api/accounts/authorize?id_token_hint=private-jwt'}),flush=True)
while not pathlib.Path(%r).exists():time.sleep(.01)
if %r=='failure':print(json.dumps({'status':'failed','error':'private failure body'}),flush=True)
else:
 p=pathlib.Path(r['tokenFile']);p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'auth_mode':'chatgpt_plan','access_token':'private-plan','refresh_token':'private-refresh','subject':'subject','client_id':'client','scopes':[] if %r=='permission-missing' else ['chatgpt.tokens.use.direct']}))
 print(json.dumps({'status':'completed'}),flush=True)
 if %r=='exit-failure':sys.exit(1)
'''%(str(gate),outcome,outcome,outcome))
    manager.auth_command=[sys.executable,str(script)]
    updates=[]
    async def progress(value):updates.append(value)
    manager.progress=progress
    await manager.perform('providers.login',{'id':'chatgpt','workspace':str(tmp_path),'scope':'local','authMode':'chatgpt_plan'})
    for _ in range(100):
        if manager.login_state('chatgpt').get('url'):break
        await asyncio.sleep(.01)
    assert manager.store.read(tmp_path,'local')==before
    candidate=manager.home/'config'/'chatgpt-sign-in'/'chatgpt'/(manager.login_state('chatgpt')['loginId']+'.json')
    assert candidate.exists()
    assert 'private' not in str(updates)
    assert 'id_token_hint' not in str(updates)
    if outcome=='removed':await manager.perform('providers.remove',{'id':'chatgpt','workspace':str(tmp_path),'scope':'local'})
    if outcome=='edited':await manager.perform('providers.save',{**args,'config':{**args['config'],'default_model':'changed'}})
    if outcome=='adopted':await manager.perform('providers.save',{**args,'config':{**args['config'],'token_file_path':str(candidate)}})
    if outcome=='adopted-disabled':
        # Disabled persisted rows still own their referenced credentials and
        # can be re-enabled later. They must prevent abandoned-file cleanup.
        await manager.perform('providers.save',{**args,'id':'another','scope':'global','config':{**args['config'],'token_file_path':str(candidate)}})
        await manager.perform('providers.remove',{**args,'id':'another'})
        await manager.perform('providers.save',{**args,'config':{**args['config'],'default_model':'changed'}})
    if outcome=='cancel':await manager.cancel_login('chatgpt')
    else:
        if outcome!='timeout':gate.touch()
        await asyncio.wait_for(manager.logins['chatgpt']['task'],3)
    login=manager.login_state('chatgpt')
    saved=manager.store.read(tmp_path,'local')['config']['providers'][0]
    assert json.loads(original.read_text())['access_token']=='legacy-private'
    if outcome in {'success','permission-missing'}:
        assert login['status']=='completed'
        assert saved['config']['auth_mode']=='chatgpt_plan'
        assert saved['config']['default_model']=='kept'
        assert saved['config']['host_file_path']==str(tmp_path/'import-host.json' if existing_mode=='chatgpt_plan' else manager.home/'config'/'chatgpt-host.json')
        assert login['account']['planEnabled'] is (outcome=='success')
        assert login['showPlanWelcome'] is (outcome=='success' and existing_mode=='legacy_codex')
        assert any(row.get('loginConfigurationChanged') for row in updates)
    else:
        assert login['status']==('cancelled' if outcome=='cancel' else 'expired' if outcome=='timeout' else 'failed')
        if outcome not in {'removed','adopted'}:assert saved['config']['token_file_path']==str(original)
        if outcome=='failure':assert manager.store.read(tmp_path,'local')==before
        if outcome=='edited':assert saved['config']['default_model']=='changed'
        if outcome=='removed':assert 'chatgpt' in manager.store.read(tmp_path,'local')['configurator']['disabled']['providers']
    assert candidate.exists() is (outcome in {'success','permission-missing','adopted','adopted-disabled'})
    assert list(candidate.parent.glob('registration-*.json'))
    assert (tmp_path/'import-host.json' if existing_mode=='chatgpt_plan' else manager.home/'config'/'chatgpt-host.json').exists()
    assert json.loads(original.read_text())==original_tokens
    global_providers=manager.store.read(tmp_path,'global').get('config',{}).get('providers',[])
    if outcome=='adopted-disabled':assert global_providers[0]['config']['token_file_path']==str(candidate)
    else:assert not global_providers
    await manager.close()


@pytest.mark.asyncio
async def test_naming_completion_uses_isolated_default_provider(manager,tmp_path,monkeypatch):
    import sys
    from amplifier_web.naming_backfill import default_provider
    monkeypatch.setenv('TEAM_CHECK_KEY','secret-fixture-value')
    await manager.perform('providers.save',{'id':'one','module':'provider-openai','config':{'base_url':'https://example.test/v1','default_model':'small'},'apiKeyEnv':'TEAM_CHECK_KEY','workspace':str(tmp_path)})
    assert default_provider(manager,str(tmp_path))==('one','small')
    child=tmp_path/'probe.py'
    child.write_text("import sys,json; r=json.load(sys.stdin); assert r['action']=='naming.complete' and r['prompt']=='Name this' and r['model']=='small'; assert r['config']['api_key']=='${TEAM_CHECK_KEY}'; print(json.dumps({'info':{},'configSchema':{'fields':[]},'naming':{'text':'{\"action\":\"defer\"}'}}))")
    manager.probe_command=[sys.executable,str(child)]
    assert await manager.probe('naming.complete',{'id':'one','model':'small','prompt':'Name this'},str(tmp_path))=={'text':'{"action":"defer"}'}
    child.write_text("import json; print(json.dumps({'info':{},'configSchema':{'fields':[]},'naming':{'error':'Model not found'}}))")
    with pytest.raises(ValueError,match='Model not found'):
        await manager.probe('naming.complete',{'id':'one','prompt':'Name this'},str(tmp_path))
