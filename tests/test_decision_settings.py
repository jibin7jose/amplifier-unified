import copy
from types import SimpleNamespace
import pytest
from amplifier_foundation import Bundle
from amplifier_web import decision_settings as decisions
from amplifier_web.setup import SetupManager
from amplifier_web.host.session import _apply_settings

@pytest.fixture
def manager(tmp_path,monkeypatch):
    def validate(row):
        if set(row)-{'model','endpoint'} or not row.get('model'):raise ValueError('Invalid decision config')
        return row
    monkeypatch.setattr(decisions,'catalogs',lambda:{'provider-fixture':({'kind':'fixture','label':'Fixture','default_model':'test','configuration_key':'decisions'},validate)})
    result=SetupManager(tmp_path/'app')
    result.store.update(tmp_path,'global',lambda settings:settings.update({'config':{'providers':[{'module':'provider-fixture','config':{'api_key':'${FIXTURE_KEY}','default_model':'chat'}}]}}))
    return result

async def select(manager,workspace,**patch):
    return await manager.perform('providers.configureDecisions',{'workspace':str(workspace),'scope':'global','purpose':'browser','mode':'decision','backend':{'provider':'fixture','model':'test'},'allowExternalState':True,**patch})

async def test_absence_ambient_credentials_and_registration_do_not_enable_selection(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('FIXTURE_KEY','not-real')
    result=await manager.perform('providers.decisionModels',{'workspace':str(tmp_path)})
    assert result['decisionModels']['selection']=={}
    assert 'api_key' not in repr(result) and 'not-real' not in repr(result)
    bundle=Bundle(name='ordinary',tools=[{'module':'tool-existing'}]);before=copy.deepcopy(bundle.to_mount_plan())
    decisions.apply(bundle,{'decision_models':{'selection':{'browser':{'mode':'decision'}}}})
    assert bundle.to_mount_plan()==before


async def test_connection_save_and_removal_publish_matching_decision_choices(manager,tmp_path,monkeypatch):
    monkeypatch.setenv('FIXTURE_KEY','synthetic-account-key')
    before=copy.deepcopy(manager.config(tmp_path).providers)
    result=await manager.perform('providers.save',{'workspace':str(tmp_path),'scope':'global',
        'id':'new-account','module':'provider-fixture','apiKeyEnv':'FIXTURE_KEY','config':{'chat_enabled':False}})
    assert {row['id'] for row in result['decisionModels']['catalog']}=={'fixture','new-account'}
    assert result['decisionModels']['selection']=={} and result['decisionModels']['backends']=={}
    assert result['decisionModelsError'] is None
    assert manager.config(tmp_path).providers[0]==before[0]
    assert 'synthetic-account-key' not in repr(result)
    result=await manager.perform('providers.remove',{'workspace':str(tmp_path),'scope':'global','id':'new-account'})
    assert [row['id'] for row in result['decisionModels']['catalog']]==['fixture']
    assert result['decisionModels']['selection']=={}


async def test_optional_metadata_failure_does_not_report_committed_account_as_failed(manager,tmp_path,monkeypatch):
    def broken(*args):raise RuntimeError('private provider detail')
    with monkeypatch.context() as patch:
        patch.setattr(decisions,'describe',broken)
        result=await manager.perform('providers.save',{'workspace':str(tmp_path),'scope':'global',
            'id':'new-account','module':'provider-fixture','config':{'default_model':'ordinary'}})
    assert any(row['id']=='new-account' for row in result['providers'])
    assert result['decisionModelsError'] and 'private provider detail' not in repr(result)
    assert 'decisionModels' not in result
    refreshed=await manager.perform('providers.decisionModels',{'workspace':str(tmp_path)})
    assert refreshed['decisionModelsError'] is None
    assert {row['id'] for row in refreshed['decisionModels']['catalog']}=={'fixture','new-account'}

async def test_selection_reuses_account_and_keeps_surface_models_independent(manager,tmp_path):
    await select(manager,tmp_path)
    await select(manager,tmp_path,purpose='computer',backend={'provider':'fixture','model':'other'})
    settings=manager.config(tmp_path).settings
    provider=manager.config(tmp_path).providers[0]['config']
    assert provider['api_key']=='${FIXTURE_KEY}' and provider['default_model']=='chat'
    assert provider['decisions']['backends']['ui-browser-fixture']['model']=='test'
    assert provider['decisions']['backends']['ui-computer-fixture']['model']=='other'
    await select(manager,tmp_path,scope='local',mode='standard')
    settings=manager.config(tmp_path).settings
    assert settings['decision_models']['selection']['browser']=={'mode':'standard','backend':None,'allow_external_state':False}
    assert settings['decision_models']['selection']['computer']['mode']=='decision'
    with pytest.raises(ValueError,match='workspace overrides'):await select(manager,tmp_path)


async def test_both_saves_independent_bindings_without_changing_chat_or_credentials(manager,tmp_path):
    result = await select(manager, tmp_path, purpose='both')
    assert result['decisionSetupCompletion']['purpose'] == 'both'
    selection = result['decisionModels']['selection']
    assert {name: row['backend'] for name, row in selection.items()} == {
        'browser': 'ui-browser-fixture', 'computer': 'ui-computer-fixture'}
    config = manager.config(tmp_path).providers[0]['config']
    assert config['api_key'] == '${FIXTURE_KEY}' and config['default_model'] == 'chat'
    assert {row['model'] for row in config['decisions']['backends'].values()} == {'test'}
    await select(manager, tmp_path, purpose='browser', backend={'provider':'fixture','model':'changed'})
    assert manager.config(tmp_path).providers[0]['config']['decisions']['backends']['ui-computer-fixture']['model'] == 'test'
    result = await select(manager, tmp_path, purpose='both', mode='standard')
    assert all(row == {'mode':'standard','backend':None,'allow_external_state':False}
               for row in result['decisionModels']['selection'].values())


@pytest.mark.parametrize('conflict', ['selection', 'backend'])
async def test_both_rejects_entire_save_when_one_purpose_has_a_narrower_override(manager,tmp_path,conflict):
    await select(manager, tmp_path)
    if conflict == 'selection':
        await select(manager, tmp_path, purpose='computer', scope='local', mode='standard')
    else:
        await select(manager, tmp_path, purpose='computer', scope='local', backend={'provider':'fixture','model':'local'})
    before = {scope: manager.store.read(tmp_path, scope) for scope in ('global','project','local')}
    with pytest.raises(ValueError, match='workspace overrides'):
        await select(manager, tmp_path, purpose='both', backend={'provider':'fixture','model':'new'})
    assert {scope: manager.store.read(tmp_path, scope) for scope in before} == before


async def test_both_still_requires_explicit_consent_and_scope_owned_account(manager,tmp_path):
    before = manager.store.read(tmp_path, 'global')
    with pytest.raises(ValueError, match='observations'):
        await select(manager, tmp_path, purpose='both', allowExternalState=False)
    assert manager.store.read(tmp_path, 'global') == before
    manager.store.update(tmp_path,'local',lambda settings:settings.update({'config':{'providers':[{'id':'local','module':'provider-fixture','config':{'api_key':'local-secret'}}]}}))
    with pytest.raises(ValueError, match='workspace scope'):
        await select(manager, tmp_path, purpose='both', backend={'provider':'local','model':'test'})
    assert manager.store.read(tmp_path, 'global') == before

async def test_unavailable_no_consent_and_inline_secrets_leave_settings_unchanged(manager,tmp_path):
    before=manager.store.read(tmp_path,'global')
    for patch in ({'backend':{'provider':'missing','model':'x'}},{'allowExternalState':False},{'backend':{'provider':'fixture','model':'x','api_key':'secret'}}):
        with pytest.raises(ValueError):await select(manager,tmp_path,**patch)
        assert manager.store.read(tmp_path,'global')==before

async def test_narrow_connection_cannot_be_promoted_by_preferences(manager,tmp_path):
    manager.store.update(tmp_path,'local',lambda settings:settings.update({'config':{'providers':[{'id':'local','module':'provider-fixture','config':{'api_key':'local-secret'}}]}}))
    before=manager.store.read(tmp_path,'global')
    with pytest.raises(ValueError,match='workspace scope'):await select(manager,tmp_path,backend={'provider':'local','model':'test'})
    assert manager.store.read(tmp_path,'global')==before
    await select(manager,tmp_path,backend={'provider':'local','model':'test'},scope='local')
    assert 'local-secret' not in repr(manager.store.read(tmp_path,'global'))

async def test_readback_uses_provider_metadata_and_never_credentials(manager,tmp_path):
    await select(manager,tmp_path,backend={'provider':'fixture','model':'deployment','endpoint':'https://fixture.test'})
    value=(await manager.perform('providers.decisionModels',{'workspace':str(tmp_path)}))['decisionModels']
    assert value['backends']['ui-browser-fixture']['provider']=='fixture'
    assert value['catalog'][0]['id']=='fixture'
    assert 'api_key' not in repr(value)


def test_explicit_module_only_and_disabled_host_override_wins():
    settings={'decision_models':{'selection':{'browser':{'mode':'decision','backend':'one'}}}}
    bundle=Bundle(name='explicit',tools=[{'module':'tool-ui-use','config':{'browser':{'read_only':True}}}],session={'orchestrator':{'module':'loop-live','config':{'programmatic_dispatch':False}}})
    result=_apply_settings(bundle,SimpleNamespace(settings=settings,providers=[]))
    assert result.tools[0]['config']['selection']['browser']['mode']=='decision'
    assert result.tools[0]['config']['browser']['read_only'] is True
    assert result.session['orchestrator']['config']['programmatic_dispatch'] is False
    settings['overrides']={'tool-ui-use':{'enabled':False}}
    assert _apply_settings(bundle,SimpleNamespace(settings=settings,providers=[])).tools==[]


def test_optional_catalog_failure_isolated(monkeypatch):
    class Broken:
        def load(self):raise ImportError('removed')
    monkeypatch.setattr(decisions,'entry_points',lambda **kwargs:[Broken()])
    assert decisions.describe({})['catalog']==[]


def test_installed_decision_services_can_be_connected_without_enabling_them(monkeypatch):
    definition={'label':'Specialist','configuration_key':'decisions','decision_only':True,
                'connection':{'auth':'key','endpoint':True,'description':'Connect your service.'}}
    monkeypatch.setattr(decisions,'catalogs',lambda:{'provider-specialist':(definition,lambda value:value)})
    value=decisions.describe({})
    assert value['services']==[{'module':'provider-specialist','name':'Specialist','decisionOnly':True,
                               'auth':'key','endpoint':True,'description':'Connect your service.'}]
    assert value['catalog']==[] and value['selection']=={} and value['backends']=={}
    value['services'][0]['auth']='changed'
    assert definition['connection']['auth']=='key'


def test_configuration_requires_explicit_scope():
    from amplifier_web.configuration_authority import requirement
    with pytest.raises(ValueError,match='explicit configuration scope'):requirement('providers.configureDecisions',{})
    assert requirement('providers.configureDecisions',{'scope':'local'})==('connections','local')


def test_conditional_hook_catalog_offers_a_decision_connection_without_changing_chat_metadata(monkeypatch):
    definition={'schema_version':2,'provider_module':'provider-mixed','validate':lambda value:value,
        'runtime':{'kind':'hook','module':'hooks-mixed-decisions','when':{'chat_enabled':False}},
        'backends':[{'label':'Mixed decisions','configuration_key':'decisions',
                     'connection':{'auth':'key','endpoint':True}}]}
    class Entry:
        def load(self):return lambda:definition
    monkeypatch.setattr(decisions,'entry_points',lambda **kwargs:[Entry()])
    value=decisions.describe({})
    assert value['services']==[{'module':'provider-mixed','name':'Mixed decisions','decisionOnly':True,
        'decisionOnlyWhen':{'chat_enabled':False},'auth':'key','endpoint':True}]
    assert 'decision_only' not in definition['backends'][0]
    assert value['selection']=={} and value['backends']=={}
