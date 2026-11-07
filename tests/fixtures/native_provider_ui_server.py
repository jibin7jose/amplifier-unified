"""Real app + controls + provider checkpoint, synthetic SDK response, no network."""
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
import settings_ui_server as base
from amplifier_core.message_models import ChatRequest, Message
from amplifier_module_provider_openai import OpenAIProvider
from amplifier_module_provider_openai.native import NativeResponsesProvider
from amplifier_web.native_provider import NativeProviderHost
from amplifier_web.runtime_controls import RuntimeControls
from amplifier_web.host.config import app_home


class Coordinator:
    session_state = {}
    config = {'providers': []}
    def __init__(self, sid):
        self.session_id = sid
        self.provider = NativeResponsesProvider.wrap(OpenAIProvider(api_key='fixture',
            config={'default_model':'gpt-6-astra'}), owner_getter=lambda:None)
        request=ChatRequest(messages=[Message(role='user',content='fixture original')],max_output_tokens=32)
        self.messages=[m.model_dump() for m in request.messages]
        self.provider._last_native_request=(request,{})
        compact=AsyncMock(return_value=SimpleNamespace(content=json.dumps({'output':[
            {'type':'compaction','id':'private-id','encrypted_content':'OPAQUE_PRIVATE_FIXTURE'}]})))
        client=SimpleNamespace(base_url='https://api.openai.com/v1',
                               responses=SimpleNamespace(with_raw_response=SimpleNamespace(compact=compact)))
        client.with_options=lambda **kwargs:client
        self.provider._client=client
        self.providers={'fixture-selected':self.provider}
        self.loop=SimpleNamespace(config={'native_provider':True},jobs={},root_provider=None,
                                  _select_provider=lambda providers:providers['fixture-selected'])
        self.context=SimpleNamespace(get_messages=self.get_messages)
        self.caps={'live.checkpoint':AsyncMock()}
        from amplifier_web.execution_events import ExecutionEvents
        self.telemetry=ExecutionEvents(sid, lambda event:None)
        self.caps['web.provider_call']=lambda provider,request,invoke,**kwargs: self.telemetry.provider_call(sid,provider,request,invoke,**kwargs)
        self.host=NativeProviderHost(self,self.loop,app_home()/'sessions'/sid)
        self.host.provider=self.provider
        self.host.identity=self.host.selected_identity()
    async def get_messages(self):return copy.deepcopy(self.messages)
    def get(self,name):return {'providers':self.providers,'orchestrator':self.loop,'context':self.context}.get(name)
    def get_capability(self,name):return self.caps.get(name)
    def register_capability(self,name,value):self.caps[name]=value


class Runtime(base.Runtime):
    async def control(self,sid,operation,args):
        if operation.startswith('native.'):
            if not hasattr(self,'controls'):
                c=Coordinator(sid)
                self.controls=RuntimeControls(SimpleNamespace(session_id=sid,coordinator=c),SimpleNamespace(generation=None,queued_inputs=0))
                self.controls.checkpoint=AsyncMock()
            return await self.controls.perform(operation,args)
        return await super().control(sid,operation,args)
    async def close(self):
        if hasattr(self,'controls'):await self.controls.close()


async def main(home):
    base.Runtime=Runtime
    app=await base.main(home)
    app['allowed_origins']=app['allowed_origins']|{'http://127.0.0.1:8967'}
    async def agent(request):
        service=app['service']
        return web.json_response(await service.app_bridge('dispatch',{'action':'runtime.control',
            'args':{'sessionId':service.state['selectedSessionId'],'operation':'native.status'}},service.state['selectedSessionId']))
    app.router.add_post('/fixture/agent-native',agent)
    return app


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='amplifier-native-ui-') as tmp:
        web.run_app(main(Path(tmp)),host='127.0.0.1',port=8967,print=None)
