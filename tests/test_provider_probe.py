import pytest
from amplifier_web import provider_probe


async def test_native_metadata_and_catalog_materialize_config_close_providers_and_hide_secrets(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key,config):
            if config:
                assert api_key=='private' and config=={'api_key':'private','base_url':''}
            else:
                assert api_key is None and config=={}
        def get_info(self):
            return {'config_fields':[
                {'id':'api_key','field_type':'secret','required':True},
                {'id':'base_url','field_type':'text','required':False},
                {'id':'reasoning_effort','field_type':'choice','required':False,'choices':['low','high']},
            ]}
        async def list_models(self):return [{'id':'model','api_key':'private'}]
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    monkeypatch.setenv('TEST_PROBE_KEY','private')
    monkeypatch.delenv('TEST_PROBE_BASE_URL',raising=False)
    result=await provider_probe.query({'module':'test','action':'providers.models','config':{'api_key':'${TEST_PROBE_KEY}','base_url':'${TEST_PROBE_BASE_URL}'}})
    assert result['models']==[{'id':'model'}] and closed==[True,True]
    assert result['configSchema']['fields'][2]['choices']==['low','high']
    assert 'private' not in str(result)


async def test_schema_probe_uses_empty_config_without_listing_models_and_closes_provider(monkeypatch):
    closed=[]
    metadata_calls=[]
    class Provider:
        def __init__(self,*,api_key,config):
            assert api_key is None and config=={}
        def get_info(self):
            metadata_calls.append(True)
            return {'config_fields':[]}
        async def list_models(self):raise AssertionError('schema discovery must not list models')
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    result=await provider_probe.query({'module':'test','action':'providers.schema','config':{'ignored':'${MISSING}'}})
    assert result['configSchema']=={'fields':[]} and closed==[True]
    assert metadata_calls == [True]


async def test_failed_catalog_still_closes_provider(monkeypatch):
    closed=[]
    class Provider:
        def __init__(self,*,api_key=None,config=None):pass
        def get_info(self):return {'config_fields':[]}
        async def list_models(self):raise ValueError('private exception')
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
        async def close(self):closed.append(True)
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    with pytest.raises(ValueError):await provider_probe.query({'module':'test','action':'providers.models'})
    assert closed==[True,True]


@pytest.mark.parametrize('action', ['providers.schema', 'providers.models', 'providers.test'])
async def test_endpoint_provider_uses_explicit_unmodified_endpoint_and_metadata_has_no_secrets(monkeypatch, action):
    endpoint = 'https://configured.test/v1/?route=%2F'
    calls, closed = [], []
    class Provider:
        def __init__(self, base_url=None, *, api_key=None, config=None):
            assert base_url == endpoint
            assert api_key == ('private' if config else None)
            self.config = config
            calls.append(('construct', bool(config)))
        def get_info(self):
            return {'config_fields': [
                {'id': 'base_url', 'field_type': 'text', 'required': True},
                {'id': 'api_key', 'field_type': 'secret', 'required': True},
            ]}
        async def list_models(self):
            assert self.config and action != 'providers.schema'
            calls.append(('models', True))
            return [{'id': 'configured-model'}]
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
        async def close(self):
            closed.append(True)
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    monkeypatch.setenv('EXACT_PROVIDER_ENDPOINT', endpoint)
    monkeypatch.setenv('EXACT_PROVIDER_KEY', 'private')
    result = await provider_probe.query({'module': 'provider-endpoint', 'action': action,
        'config': {'base_url': '${EXACT_PROVIDER_ENDPOINT}', 'api_key': '${EXACT_PROVIDER_KEY}'}})
    assert 'private' not in str(result)
    assert calls == ([('construct', False)] if action == 'providers.schema' else
                     [('construct', False), ('construct', True), ('models', True)])
    assert len(closed) == (1 if action == 'providers.schema' else 2)


async def test_naming_probe_makes_one_call_and_closes_the_provider(monkeypatch):
    from types import SimpleNamespace
    closed, requests = [], []
    class Provider:
        def __init__(self, *, api_key=None, config=None): pass
        def get_info(self): return {'config_fields': [{'id': 'api_key', 'field_type': 'secret', 'required': True}]}
        async def complete(self, request, **kwargs):
            requests.append(request.messages[0].content)
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='{"action":"set","name":"N"}')])
        async def close(self): closed.append(True)
    monkeypatch.setattr(provider_probe, 'provider_class', lambda module: Provider)
    result = await provider_probe.query({'module': 'test', 'action': 'naming.complete', 'prompt': 'Name this',
                                         'config': {'api_key': 'private'}})
    assert result['naming'] == {'text': '{"action":"set","name":"N"}'}
    assert requests == ['Name this'] and closed == [True, True]


async def test_legacy_static_list_is_never_promoted_to_discovery():
    class StaticProvider:
        def list_models(self):
            raise AssertionError('Legacy API must not be called for discovery')
    assert await provider_probe.listed_models(StaticProvider()) == {
        'models':[], 'modelsSupported':False, 'modelsProvenance':'unsupported'}


async def test_legacy_static_list_cannot_claim_a_reachable_connection(monkeypatch):
    class StaticProvider:
        def __init__(self, config=None):pass
        def get_info(self):return {'config_fields':[]}
        async def list_models(self):raise AssertionError('Unverified listing must not be called')
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:StaticProvider)
    from amplifier_web.probe_diagnostics import HostProbeFailure
    with pytest.raises(HostProbeFailure) as raised:
        await provider_probe.query({'module':'fixture','action':'providers.test'})
    assert raised.value.diagnostic['code']=='not-verifiable'


@pytest.mark.parametrize('info', [
    {'version':True,'source':'provider-api'},
    {'source':'provider-api'},
    {'version':2,'source':'provider-api'},
    {'version':1,'source':'cache'},
    {'version':1,'source':'unsupported'},
    {'version':1,'source':'deployment'},
    {'version':1,'filters_keyword':'filters'},
])
async def test_unqualified_listing_is_not_promoted_to_strict(info):
    class Provider:
        def get_model_list_info(self):return info
        async def list_models(self):raise AssertionError('unverified listing')
        async def discover_models(self):raise AssertionError('superseded API')
    assert await provider_probe.listed_models(Provider()) == {
        'models':[], 'modelsSupported':False, 'modelsProvenance':'unsupported'}


@pytest.mark.parametrize('payload', [None, {}, 'bad', [{'name':'no id'}], [{'id':''}]])
async def test_strict_listing_rejects_malformed_results(payload):
    class Provider:
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
        async def list_models(self):return payload
    with pytest.raises(ValueError):
        await provider_probe.listed_models(Provider())


async def test_strict_successful_empty_is_not_failure_or_unsupported():
    class Provider:
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
        async def list_models(self):return []
    assert await provider_probe.listed_models(Provider()) == {
        'models':[], 'modelsSupported':True, 'modelsProvenance':'provider-api',
        'modelListInfo':{'version':1,'source':'provider-api'}}


async def test_provider_owns_default_all_custom_and_empty_filter_scopes():
    calls=[]
    class Provider:
        async def get_model_list_info(self):
            return {'version':1,'source':'provider-api','filters_keyword':'filters',
                    'default_filters':{'include':['provider-recommendation']}}
        async def list_models(self, **kwargs):
            calls.append(kwargs)
            # Deliberately not an implementation of the recommendation logic.
            return [] if kwargs.get('filters') == {'include':[]} else [{'id':'Exact-ID'}]
        async def discover_models(self):raise AssertionError('superseded API')
    provider=Provider()
    for filters in (None, {}, {'include':['Custom*']}, {'include':[]}):
        result=await provider_probe.listed_models(provider,filters=filters)
        assert result['modelsSupported'] is True
        assert result['models'] == ([] if filters=={'include':[]} else [{'id':'Exact-ID'}])
    assert calls == [{}, {'filters':{}}, {'filters':{'include':['Custom*']}}, {'filters':{'include':[]}}]


async def test_selector_keyword_is_never_guessed_and_failures_keep_their_type():
    class Provider:
        def get_model_list_info(self):return {'version':1,'source':'provider-api'}
        async def list_models(self):raise TimeoutError('synthetic failure')
    with pytest.raises(ValueError,match='advertise'):
        await provider_probe.listed_models(Provider(),filters={})
    with pytest.raises(TimeoutError):
        await provider_probe.listed_models(Provider())


async def test_provider_specific_selector_schema_and_fields_are_not_openai_policy():
    from copy import deepcopy
    schema={'type':'object','properties':{
        'supported_generation_methods':{'type':'array','items':{'type':'string'}},
        'chat_only':{'type':'boolean'},'include_preview':{'type':'boolean'}}}
    defaults={'supported_generation_methods':['generateContent'],'chat_only':True}
    info={'version':1,'source':'provider-api','filters_keyword':'filters',
          'filters_schema':schema,'default_filters':defaults,'unfiltered':{},'override':'replace'}
    before=deepcopy(info);calls=[]
    class Provider:
        def get_model_list_info(self):return info
        async def list_models(self, **kwargs):
            calls.append(deepcopy(kwargs))
            return [{'id':'Provider/Exact-ID'}]
    provider=Provider()
    assert (await provider_probe.listed_models(provider))['modelListInfo']==before
    override={'include_preview':True,'supported_generation_methods':[]}
    assert (await provider_probe.listed_models(provider,filters=override))['models']==[
        {'id':'Provider/Exact-ID'}]
    await provider_probe.listed_models(provider,filters={})
    assert calls==[{}, {'filters':override}, {'filters':{}}]
    assert info==before, 'Inspection does not rewrite provider schema/defaults'


@pytest.mark.parametrize('source', ['configured', 'static', 'simulated'])
@pytest.mark.parametrize('models', [[], [{'id':'Exact-Customer/Alias'}]])
async def test_explicit_origin_is_visible_but_never_connection_verification(source, models, monkeypatch, tmp_path):
    from amplifier_web.provider_catalog import ProviderCatalog, model_key
    calls=[]
    class Provider:
        def __init__(self, config=None):pass
        def get_info(self):return {'config_fields':[]}
        def get_model_list_info(self):return {'version':1,'source':source,'filters_keyword':'filters'}
        async def list_models(self, **kwargs):
            calls.append(kwargs)
            return models
        async def login(self):raise AssertionError('Browsing never logs in')
        async def complete(self, request):raise AssertionError('Browsing never infers')
    monkeypatch.setattr(provider_probe,'provider_class',lambda module:Provider)
    result=await provider_probe.query({'module':'fixture','action':'providers.models'})
    assert result['models']==models and result['modelsSupported'] is True
    assert result['modelsProvenance']==source and calls==[{}]
    from amplifier_web.probe_diagnostics import HostProbeFailure
    with pytest.raises(HostProbeFailure,match='API model listing'):
        await provider_probe.query({'module':'fixture','action':'providers.test'})
    assert calls==[{}], 'Connection testing rejects local-origin metadata before listing'
    cache=ProviderCatalog(tmp_path/'catalog.json')
    key=model_key(source)
    cache.put(key,result)
    before=cache.entry(key)
    async def failed():raise TimeoutError('failure is not an alternate inventory')
    with pytest.raises(TimeoutError):await cache.get(key,failed,refresh=True)
    stale=ProviderCatalog(cache.path).entry(key)
    assert stale['models']==models and stale['provenance']==source
    assert stale['loadedAt']==before['loadedAt'] and stale['stale'] and stale['phase']=='error'
    await cache.close()
