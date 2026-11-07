"""Actual Core session, Foundation configurator, and app controls. No model calls."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from amplifier_core import HookResult, ToolResult
from amplifier_core.models import ProviderInfo
from amplifier_core.message_models import ChatRequest
from amplifier_foundation import SessionConfigurator
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_loop_live.runtime import Runtime
from amplifier_web.runtime_controls import RuntimeControls


async def run():
    plan={'session':{'orchestrator':{'module':'loop-live'},'context':{'module':'context-simple'}},
          'agents':{'fixture':{'instruction':'Fixture worker'}}}
    paths={name:Path(importlib.util.find_spec('amplifier_module_'+name.replace('-','_')).origin).parent for name in ('loop-live','context-simple')}
    bundle=Bundle(name='fixture',session=plan['session'],agents=plan['agents'])
    prepared=PreparedBundle(plan,BundleModuleResolver(paths),bundle)
    session=await prepared.create_session(session_id='controls-fixture')
    coordinator=session.coordinator
    configurator=SessionConfigurator(session,prepared)
    coordinator.register_capability('web.configurator',configurator)
    coordinator.register_capability('web.prepared',prepared)
    runtime=Runtime('controls-fixture')
    controls=RuntimeControls(session,runtime)
    class Provider:
        request=None
        def get_info(self):
            return ProviderInfo(id='fixture',display_name='Fixture',defaults={'model':'fixture-model'})
        async def complete(self,request,**kwargs):
            self.request=request
            return None
        async def list_models(self):
            return [{'id':'fixture-model'}]
        def get_model_list_info(self):
            return {'version':1,'source':'provider-api'}
    provider=Provider()
    await coordinator.mount('providers',provider,name='fixture-provider')
    class Echo:
        name='fixture_echo'
        description='Return fixture input'
        input_schema={'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}
        calls=0
        async def execute(self,args):
            self.calls+=1
            return ToolResult(success=True,output=args['text'])
    tool=Echo()
    await coordinator.mount('tools',tool,name=tool.name)
    deny=True
    async def gate(event,data):
        return HookResult(action='deny',reason='Fixture denial') if deny else HookResult()
    coordinator.hooks.register('tool:pre',gate,name='fixture-deny')
    with tempfile.TemporaryDirectory() as home:
        os.environ['AMPLIFIER_WEB_HOME']=home
        # context-simple uses None for its automatic context limit. Startup
        # must round-trip that default, including snapshots from older builds.
        context=coordinator.get('context')
        original_limit=context.max_tokens
        context.max_tokens=None
        controls.persist()
        assert 'contextTokens' not in json.loads(controls.state_path().read_text())['budget']
        saved=json.loads(controls.state_path().read_text())
        saved['budget']={'maxIterations':-1,'contextTokens':None}
        controls.state_path().write_text(json.dumps(saved))
        await controls.restore()
        assert context.max_tokens is None
        assert coordinator.get('orchestrator').max_iterations==-1
        # Null from an older snapshot also must not replace a bundle's newly
        # configured numeric limit during preparation.
        context.max_tokens=12345
        controls.state_path().write_text(json.dumps(saved))
        await controls.restore()
        assert context.max_tokens==12345
        # Explicit edits still reject null, zero, negatives and booleans.
        for invalid_limit in (None,0,-1,False):
            try:
                await controls.perform('budget.set',{'contextTokens':invalid_limit})
                raise AssertionError('Invalid explicit context token limit accepted')
            except ValueError:pass
        context.max_tokens=original_limit
        inspected=await controls.perform('configuration.inspect')
        assert inspected['plan']['session']['orchestrator']['module']=='loop-live'
        catalog=await controls.perform('catalog.inspect')
        assert any(row['name']=='fixture_echo' for row in catalog['tools'])
        denied=await controls.perform('tool.invoke',{'name':tool.name,'arguments':{'text':'hidden'}})
        assert denied['success'] is False and tool.calls==0
        deny=False
        completed=await controls.perform('tool.invoke',{'name':tool.name,'arguments':{'text':'visible'}})
        assert completed['result']['success'] is True and tool.calls==1
        await controls.perform('configuration.toggle',{'section':'agents','name':'fixture','enabled':False})
        assert 'fixture' not in coordinator.config['agents']
        await controls.perform('configuration.toggle',{'section':'agents','name':'fixture','enabled':True})
        assert 'fixture' in coordinator.config['agents']
        await controls.perform('budget.set',{'maxIterations':8,'contextTokens':32000})
        assert coordinator.get('orchestrator').max_iterations==8
        assert coordinator.get('context').max_tokens==32000
        await controls.perform('provider.select',{'provider':'fixture-provider','model':'fixture-model'})
        await controls.perform('budget.set',{'maxOutputTokens':321})
        await coordinator.get('orchestrator').root_provider.complete(ChatRequest(messages=[]))
        assert provider.request.max_output_tokens==321
        assert provider.request.model=='fixture-model'
        await controls.perform('provider.reset')
        assert controls.selection is None
        assert coordinator.get('orchestrator').root_provider.selection == {'max_output_tokens':321}
        await coordinator.get('orchestrator').root_provider.complete(ChatRequest(messages=[]))
        assert provider.request.max_output_tokens == 321
        assert json.loads(controls.state_path().read_text())['selection'] is None
        summary = await controls.perform('configuration.providers')
        assert summary['effective']['model'] == 'fixture-model' and summary['pinned'] is False
        assert (await controls.perform('configuration.providerTest',{'provider':'fixture-provider'}))['reachable']
        assert (await controls.perform('configuration.providerModels',{'provider':'fixture-provider'}))['models']==[{'id':'fixture-model'}]
        await controls.perform('goals.set',{'condition':'finish fixture','maxTurns':3})
        assert coordinator.session_state['goal']['cap']==3
        await coordinator.get('context').add_message({'role':'user','content':'Clear this test'})
        await controls.perform('context.clear')
        assert not await coordinator.get('context').get_messages()
        assert coordinator.session_state['goal'] is None
        runtime.generation={'id':'active'}
        try:
            await controls.perform('context.clear')
            raise AssertionError('Active turn allowed destructive clear')
        except ValueError:pass
        runtime.generation=None
        invalid=dict(plan,providers=[{'module':'provider-fixture','enabled':False}])
        try:
            await controls.perform('configuration.apply',{'config':invalid})
            raise AssertionError('Disabled all providers')
        except ValueError:pass
        assert not (Path(home)/'sessions/controls-fixture/configuration.json').exists()
        applied=await controls.perform('configuration.apply',{'config':dict(plan,providers=[{'module':'provider-fixture'}])})
        assert applied['requiresRestart']
        # A disabled provider is absent from the mounted coordinator but its
        # private configuration must survive turning it back on in the editor.
        stored=Path(home)/'sessions/controls-fixture/configuration.json'
        stored.write_text(json.dumps(dict(plan,providers=[{'module':'provider-fixture'},{'module':'provider-disabled','enabled':False,'config':{'api_key':'fixture-private'}}])))
        await controls.perform('configuration.apply',{'config':dict(plan,providers=[{'module':'provider-fixture'},{'module':'provider-disabled','enabled':True,'config':{'api_key':'[REDACTED]'}}])})
        restored=json.loads(stored.read_text())
        assert restored['providers'][1]['config']['api_key']=='fixture-private'
        assert restored['providers'][1]['enabled'] is True
    await session.cleanup()
    assert not any(name.startswith(('amplifier_app_cli','amplifier_loop_live_cli')) for name in sys.modules)
    print(json.dumps({'approval_enforced':True,'public_configurator':True,'budget_applied':True,'cli_imports':False}))

asyncio.run(run())
