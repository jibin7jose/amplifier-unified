import json
import sys
import pytest
from amplifier_web.provider_health import generation,record,issue
from amplifier_web.attention import snapshot


def test_auth_failure_is_bound_to_configuration_and_credential_file(tmp_path):
    token=tmp_path/'tokens.json';token.write_text('expired-private-token')
    config={'token_file_path':str(token),'api_key':'private-value'}
    key=generation('provider-openai-chatgpt',config)
    record(tmp_path,'one',key,authentication_required=True)
    assert issue(tmp_path,'one','provider-openai-chatgpt',config)
    assert 'private-value' not in next((tmp_path/'provider-health').glob('*.json')).read_text()
    assert not issue(tmp_path,'one','provider-openai-chatgpt',{**config,'auth_mode':'chatgpt_plan'})
    token.write_text('renewed-private-token')
    assert not issue(tmp_path,'one','provider-openai-chatgpt',config)


def test_attention_trail_remains_actionable_until_account_recovers():
    state={'setup':{'providers':[{'id':'one','enabled':True,'authenticationRequired':True}]}}
    report=snapshot(state); item=report['items'][0]
    assert report['settingsUnread']==1 and report['pages']['ai-connections']==1
    assert item['providerId']=='one'
    state['attentionRead']={item['id']:item['fingerprint']}
    assert snapshot(state)['settingsUnread']==1
    state['setup']['providers'][0]['authenticationRequired']=False
    assert snapshot(state)['settingsUnread']==0


@pytest.mark.parametrize('source', ['configured', 'static', 'simulated', 'unsupported'])
async def test_local_catalog_cannot_clear_account_failure(tmp_path, monkeypatch, source):
    from amplifier_web.preferences import SettingsStore
    from amplifier_web.setup import SetupManager
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'shared'))
    home=tmp_path/'app'
    config={'api_key':'synthetic-not-a-live-credential'}
    module='provider-fixture'
    SettingsStore(home).update(tmp_path,'global',lambda values:values.update(config={'providers':[
        {'id':'one','module':module,'config':config}]}))
    record(home,'one',generation(module,config),authentication_required=True)
    def manager(origin):
        result={'info':{},'configSchema':{'fields':[]},'models':[{'id':'local'}],
                'modelsSupported':origin!='unsupported','modelsProvenance':origin}
        return SetupManager(home,probe_command=[sys.executable,'-c',
            'import json,sys;json.load(sys.stdin);print('+repr(json.dumps(result))+')'])
    await manager(source).probe('providers.models',{'id':'one'},tmp_path)
    assert issue(home,'one',module,config)
    await manager('provider-api').probe('providers.models',{'id':'one'},tmp_path)
    assert not issue(home,'one',module,config)
