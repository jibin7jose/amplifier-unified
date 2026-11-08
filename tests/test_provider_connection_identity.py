"""Connection admission is cross-process; discovery reuse needs stronger provenance."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_web.preferences import SettingsStore
from amplifier_web.provider_catalog import ProviderCatalog, model_key
from amplifier_web.setup import SetupManager, chatgpt_token_path


@pytest.mark.parametrize('module,filters', [
    ('provider-openai', {'include':['custom*']}),
    ('provider-fixture', {'supported_generation_methods':['generateContent']}),
    ('provider-fixture', {'chat_only':True,'include_preview':False}),
])
def test_recommendation_scope_invalidates_catalog_not_saved_account_binding(tmp_path, monkeypatch, module, filters):
    from amplifier_web.provider_catalog import connection_key, configuration_key
    base={'api_key':'synthetic-account','base_url':'https://fixture.invalid'}
    scopes=[base, {**base,'model_filters':{}},
            {**base,'model_filters':filters},
            {**base,'model_filters':{'include':[]}}]
    assert len({configuration_key(tmp_path,module,row) for row in scopes})==4
    assert len({connection_key(tmp_path,module,row) for row in scopes})==1
    assert connection_key(tmp_path,module,base)!=connection_key(
        tmp_path,module,{**base,'api_key':'different-account'})
    indirect={**base,'model_filters':{'include':['${LIST_PATTERN}']}}
    monkeypatch.setenv('LIST_PATTERN','first*')
    before=configuration_key(tmp_path,module,indirect)
    monkeypatch.setenv('LIST_PATTERN','second*')
    assert configuration_key(tmp_path,module,indirect)!=before


_PROCESS = r'''
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace
from amplifier_web.host.config import load_config, expand_environment
from amplifier_web.host.session import apply_provider_environment, _apply_settings
from amplifier_web.provider_catalog import configured_binding, mounted_catalog_keys
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.setup import SetupManager
from amplifier_web.provider_environment import materialize_provider_config

request = json.load(sys.stdin)
os.environ.update(request.get('environment', {}))
home, workspace = Path(request['home']), Path(request['workspace'])
manager = SetupManager(home)
if request['mode'] == 'host':
    snapshot = manager.probe_snapshot('providers.models', {'id':'one'}, workspace)
    probe_config = materialize_provider_config(snapshot['config'], {'fields':[]}, environment=snapshot['env'])
    expected_token = request.get('expectedToken')
    print(json.dumps({'binding':configured_binding(home, workspace, 'one'),
                      'catalog':manager.catalog_key({'id':'one'}, workspace),
                      'probeCredentialMatches':not expected_token or probe_config.get('github_token')==expected_token}))
else:
    config = load_config(workspace, home=home)
    if request['mode'] == 'composed_worker':
        bundle = SimpleNamespace(providers=[], tools=[], hooks=[], agents={}, session={})
        _apply_settings(bundle, config)
        rows = bundle.providers
    else:
        rows = config.configured_connections
    rows = expand_environment(rows)
    row = rows[0]
    row['config'].update(request.get('change', {}))
    if request.get('source'):
        row['source'] = request['source']
    plan = {'providers':rows}
    apply_provider_environment(plan)
    sources = {'one':row['source']}
    captured = {'home':str(home), 'workspace':str(workspace), 'sources':sources,
        'keys':mounted_catalog_keys(home, workspace, rows, sources),
        'connectionKeys':mounted_catalog_keys(home, workspace, rows, sources, binding=True)}
    capabilities = {'web.provider_catalog':captured}
    coordinator = SimpleNamespace(config=plan, session_state={},
        get=lambda name: {}, get_capability=capabilities.get,
        register_capability=capabilities.__setitem__)
    controls = RuntimeControls(SimpleNamespace(coordinator=coordinator, config=plan, session_id='fixture'),
                               SimpleNamespace(generation=None, queued_inputs=0))
    async def check():
        try:
            return await controls.perform('provider.verifyConnection', {
                'instance':'one', 'expectedConnectionKey':request['expected']})
        except ValueError as exc:
            return {'verified':False, 'error':str(exc)}
        finally:
            await controls.close()
    result = asyncio.run(check())
    print(json.dumps({**result, 'binding':captured['connectionKeys']['one'],
        'catalog':captured['keys']['one'],
        'promoted':os.environ.get('COPILOT_AGENT_TOKEN') == row['config'].get('github_token'),
        'effectiveCredentialMatches':not request.get('expectedToken') or os.environ.get('COPILOT_AGENT_TOKEN')==request['expectedToken']}))
'''


def process_identity(tmp_path, mode, **args):
    # No inherited credentials, live home or provider imports. Both independent
    # processes use actual settings composition/identity and the worker control.
    env = {'PATH':os.defpath, 'HOME':str(tmp_path),
           'PYTHONPATH':str(Path(__file__).resolve().parents[1]),
           'AMPLIFIER_HOME':str(tmp_path/'shared'),
           'AMPLIFIER_WEB_HOME':str(tmp_path/'app'),
           'AMPLIFIER_SESSION_STATE_HOME':str(tmp_path/'session-state')}
    completed = subprocess.run([sys.executable, '-c', _PROCESS], env=env,
        input=json.dumps({'mode':mode,'home':str(tmp_path/'app'),
                          'workspace':str(tmp_path),**args}),
        text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert 'synthetic-account' not in completed.stdout
    return json.loads(completed.stdout)


@pytest.mark.parametrize('qualified', [False, True])
@pytest.mark.parametrize('module,field', [('provider-github-copilot','github_token'),
                                        ('provider-fixture','api_key')])
def test_saved_binding_survives_separate_host_worker_processes(tmp_path, monkeypatch, qualified, module, field):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'shared'))
    home=tmp_path/'app'
    source='git+https://fixture.invalid/provider@'+'a'*40
    SettingsStore(home).update(tmp_path, 'global', lambda values: values.update(config={'providers':[
        {'id':'one','module':module,'source':source,
         'config':{field:'synthetic-account-a','base_url':'https://endpoint-a.invalid'}}]}))
    if qualified:
        receipt=home/'updates/baseline-runtime'
        receipt.mkdir(parents=True)
        (receipt/'runtime-installed.json').write_text('[]')
    host=process_identity(tmp_path,'host')
    worker=process_identity(tmp_path,'worker',expected=host['binding'])
    assert worker['verified'] is True
    assert worker['binding'] == host['binding']
    if module=='provider-github-copilot':
        assert worker['promoted'] is True  # Real apply_provider_environment path.
    assert (worker['catalog'] == host['catalog']) is qualified
    changed_selection=process_identity(tmp_path,'worker',expected=host['binding'],
        change={'model':'new-default','default_model':'other-default'})
    assert changed_selection['verified'] is True
    assert changed_selection['binding']==host['binding']
    assert changed_selection['catalog']!=host['catalog']
    restarted_host=process_identity(tmp_path,'host')
    assert restarted_host['binding'] == host['binding']
    assert (restarted_host['catalog'] == host['catalog']) is qualified
    for change in ({'change':{field:'synthetic-account-b'}},
                   {'change':{'base_url':'https://endpoint-b.invalid'}},
                   {'source':'git+https://fixture.invalid/provider@'+'b'*40}):
        different=process_identity(tmp_path,'worker',expected=host['binding'],**change)
        assert different['verified'] is False
        assert 'mounted connection differs' in different['error']


@pytest.mark.parametrize('config,environment,expected', [
    ({'github_token':'${COPILOT_AGENT_TOKEN}'}, {'COPILOT_AGENT_TOKEN':'synthetic-account-b'}, 'synthetic-account-b'),
    ({}, {'COPILOT_AGENT_TOKEN':'synthetic-account-b'}, 'synthetic-account-b'),
    ({}, {'GITHUB_TOKEN':'synthetic-account-a','COPILOT_AGENT_TOKEN':'synthetic-account-b'}, 'synthetic-account-b'),
    ({'github_token':'synthetic-account-a'}, {'COPILOT_AGENT_TOKEN':'synthetic-account-b'}, 'synthetic-account-a'),
])
def test_copilot_probe_and_worker_resolve_same_effective_token(tmp_path, monkeypatch, config, environment, expected):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'shared'))
    SettingsStore(tmp_path/'app').update(tmp_path,'global',lambda values: values.update(config={'providers':[
        {'id':'one','module':'provider-github-copilot','source':'fixture-source','config':config}]}))
    host=process_identity(tmp_path,'host',environment=environment,expectedToken=expected)
    assert host['probeCredentialMatches']
    worker=process_identity(tmp_path,'worker',expected=host['binding'],environment=environment,expectedToken=expected)
    assert worker['verified'] and worker['effectiveCredentialMatches']


@pytest.mark.parametrize('auth_mode', ['chatgpt_codex','chatgpt_plan'])
@pytest.mark.parametrize('failed', [False, True])
async def test_implicit_chatgpt_credential_rotation_invalidates_positive_and_negative_cache(
        tmp_path, monkeypatch, auth_mode, failed):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'shared'))
    home=tmp_path/'app'
    raw={'auth_mode':auth_mode}  # Valid existing setup without token_file_path.
    SettingsStore(home).update(tmp_path,'global',lambda values: values.update(config={'providers':[
        {'id':'one','module':'provider-openai-chatgpt','source':'fixture-source','config':raw}]}))
    path=Path(chatgpt_token_path(raw)).expanduser()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'access_token':'synthetic-account-a'}))
    calls=[]
    async def probe(self,action,args,workspace):
        calls.append(path.read_text() if path.exists() else 'missing')
        if failed:
            raise ValueError('Synthetic discovery failed')
        return {'models':[{'id':'actual-api-model-'+str(len(calls))}],
                'modelsSupported':True,'modelsProvenance':'provider-api'}
    monkeypatch.setattr(SetupManager,'probe',probe)
    cache_path=home/'fixture-catalog.json'
    keys=[]
    for state in ('account-a','account-b','missing'):
        if state=='account-b':
            path.write_text(json.dumps({'access_token':'synthetic-account-b'}))
        elif state=='missing':
            path.unlink()
        manager=SetupManager(home,catalog=ProviderCatalog(cache_path))
        key=manager.catalog_key({'id':'one'},tmp_path)
        assert key not in keys
        keys.append(key)
        assert manager.catalog.peek(model_key(key)) is None
        for _ in range(2):
            if failed:
                with pytest.raises(ValueError,match='discovery failed'):
                    await manager.cached_probe('providers.models',{'id':'one'},tmp_path)
            else:
                result=await manager.cached_probe('providers.models',{'id':'one'},tmp_path)
                assert result['models']==[{'id':'actual-api-model-'+str(len(keys))}]
        assert len(calls)==len(keys)  # Cache reuse until the exact credential changes.
        await manager.catalog.close()
    assert calls[-1]=='missing'


@pytest.mark.parametrize('module,raw', [
    ('provider-openai', {'api_key': 'synthetic-account-a'}),
    ('provider-openai-chatgpt', {'auth_mode': 'chatgpt_codex', 'login_on_mount': False}),
])
def test_source_less_saved_connection_matches_actual_host_composition(tmp_path, monkeypatch, module, raw):
    """Settings created by the normal UI need no qualified runtime receipt."""
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    SettingsStore(tmp_path / 'app').update(tmp_path, 'global', lambda values: values.update(config={
        'providers': [{'id': 'one', 'module': module, 'config': raw}]}))
    host = process_identity(tmp_path, 'host')
    worker = process_identity(tmp_path, 'composed_worker', expected=host['binding'])
    assert worker['verified'], worker
    assert worker['binding'] == host['binding']
    # Source provenance for discovery is NOT inferred from intended mounting.
    assert worker['catalog'] != host['catalog']
    from amplifier_web.host.config import load_config
    from amplifier_web.host.session import _apply_settings
    from amplifier_web.provider_catalog import conversation_bindings
    from types import SimpleNamespace
    config = load_config(tmp_path, home=tmp_path / 'app')
    bundle = SimpleNamespace(providers=[], tools=[], hooks=[], agents={}, session={})
    _apply_settings(bundle, config)
    saved = tmp_path / 'app' / 'sessions' / 'fixture' / 'configuration.json'
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({'providers': bundle.providers}))
    providers = [{'id': 'one', 'connectionKey': host['binding']}]
    assert conversation_bindings(tmp_path / 'app', tmp_path, 'fixture', providers) == {}
    bundle.providers[0]['config']['base_url'] = 'https://override.invalid'
    saved.write_text(json.dumps({'providers': bundle.providers}))
    assert conversation_bindings(tmp_path / 'app', tmp_path, 'fixture', providers)['one']['eligible'] is False
    for change in (
        {'change': {'base_url': 'https://different.invalid'}},
        {'source': 'git+https://fixture.invalid/different@' + 'b' * 40},
    ):
        other = process_identity(tmp_path, 'composed_worker', expected=host['binding'], **change)
        assert not other['verified']


@pytest.mark.parametrize('module', ['provider-openai', 'provider-openai-chatgpt'])
def test_disabled_source_does_not_override_enabled_connection_without_snapshot(tmp_path, monkeypatch, module):
    from amplifier_web.provider_catalog import configured_binding, conversation_bindings
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path / 'shared'))
    home = tmp_path / 'app'
    store = SettingsStore(home)
    store.update(tmp_path, 'global', lambda values: values.update(
        config={'providers': [
            {'id': 'one', 'module': module, 'config': {}},
            {'id': 'disabled', 'module': module,
             'source': 'git+https://fixture.invalid/other-implementation@' + 'b' * 40,
             'config': {}},
        ]},
        configurator={'disabled': {'providers': ['disabled']}},
    ))
    path = home / 'sessions' / 'no-snapshot' / 'configuration.json'
    assert not path.exists()
    expected = configured_binding(home, tmp_path, 'one')
    assert expected and configured_binding(home, tmp_path, 'disabled') is None
    assert conversation_bindings(home, tmp_path, 'no-snapshot',
                                 [{'id': 'one', 'connectionKey': expected}]) == {}
    # A disabled selected connection remains refused, not silently re-enabled.
    disabled = conversation_bindings(home, tmp_path, 'no-snapshot',
                                     [{'id': 'disabled', 'connectionKey': expected}])
    assert disabled['disabled']['eligible'] is False
    # An actual saved snapshot is authoritative: a different saved source,
    # endpoint or account is still a real override, not this absent-snapshot bug.
    path.parent.mkdir(parents=True)
    for patch in [
        {'source': 'git+https://fixture.invalid/saved-implementation@' + 'c' * 40},
        {'config': {'base_url': 'https://different.invalid'}},
        {'config': {'api_key': 'synthetic-other-account'}},
        {'enabled': False},
    ]:
        path.write_text(json.dumps({'providers': [{'id': 'one', 'module': module,
                                                 'config': {}, **patch}]}))
        result = conversation_bindings(home, tmp_path, 'no-snapshot',
                                       [{'id': 'one', 'connectionKey': expected}])
        assert result['one']['eligible'] is False, patch
