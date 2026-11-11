"""Actual Worker.start wiring with local acquisition and offline SDK transport."""
import asyncio
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from amplifier_foundation.bundle import Bundle, PreparedBundle, BundleModuleResolver
from amplifier_module_provider_openai import OpenAIProvider
from amplifier_module_loop_live.runtime import Input
from amplifier_web import runtime_worker
from amplifier_web.host.session import SelectedProvider
from amplifier_web.host.children import StandaloneHostAdapter
from amplifier_foundation.session.activation import ActivationGate
from test_context_inspection import config, evidence


@pytest.mark.parametrize('revocation,image,computer_hook,restore,model', [
    ('none',False,False,False,'gpt-6-astra'), ('none',False,False,True,'gpt-6-astra'),
    ('none',False,True,False,'gpt-6-astra'), ('none',False,True,True,'gpt-6-astra'),
    ('none',True,True,True,'gpt-6-astra'), ('metadata-file',True,True,True,'gpt-6-astra'),
    ('admission-file',True,True,True,'gpt-6-astra'),
    ('none',False,True,True,'unknown-deployment'),
])
async def test_full_worker_start_capture(tmp_path,monkeypatch,restore,computer_hook,image,revocation,model):
    import socket
    def forbidden(*args,**kwargs):raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    home=tmp_path/'home';workspace=tmp_path/'workspace';workspace.mkdir();config(home,recording=False)
    monkeypatch.setenv('AMPLIFIER_WEB_HOME',str(home))
    monkeypatch.setenv('AMPLIFIER_HOME',str(tmp_path/'community'))
    # Actual provider serialization/count/complete, no sockets/accounts.
    paths=[];image_path=None;image_digest=None;revoked=False
    def transport(req):
        body=json.loads(req.content)
        blocks=[block for item in body.get('input',[]) if isinstance(item,dict)
                for key in ('content','output')
                for block in (item.get(key) if isinstance(item.get(key),list) else [])]
        images=[block for block in blocks if block.get('type')=='input_image']
        digests=[]
        for block in images:
            url=block.get('image_url','')
            assert url.startswith('data:image/png;base64,')
            digests.append(hashlib.sha256(base64.b64decode(url.split(',',1)[1])).hexdigest())
        encoded=json.dumps(body.get('input'),sort_keys=True)
        paths.append({'path':req.url.path,'bytes':len(req.content),'model':body.get('model'),
            'inputHash':hashlib.sha256(encoded.encode()).hexdigest(),
            'imageDigests':digests,'imageTypes':[b['type'] for b in images],
            'referenceRetained':'retained appended context' in encoded,
            'toolCallRetained':'capture' in encoded})
        if req.url.path.endswith('/input_tokens'):return httpx.Response(200,json={'input_tokens':100})
        assert req.url.path=='/v1/responses'
        return httpx.Response(200,json={'id':'r','object':'response','created_at':1,'model':'gpt-6-astra','status':'completed',
            'output':[{'type':'message','id':'m','role':'assistant','content':[{'type':'output_text','text':'Done','annotations':[]}]}],
            'usage':{'input_tokens':100,'output_tokens':1,'total_tokens':101}})
    client=AsyncOpenAI(api_key='fixture',max_retries=0,http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    raw=OpenAIProvider(api_key='fixture',client=client,config={'default_model':'gpt-6-astra','use_streaming':False,'raw':False,'max_retries':0})
    if revocation == 'metadata-file':
        info=raw.get_info
        def get_info():
            from amplifier_web.request_projection import _CURRENT
            if _CURRENT.get() is not None and image_path is not None and not revoked:
                async def pending():
                    nonlocal revoked
                    await asyncio.sleep(0)
                    image_path.unlink();revoked=True
                    return info()
                return pending()
            return info()
        raw.get_info=get_info
    modules={n:Path(importlib.util.find_spec('amplifier_module_'+n.replace('-','_')).origin).parent for n in ('loop-live','context-managed')}
    plan={'session':{'orchestrator':{'module':'loop-live','config':{'max_iterations':2}},'context':{'module':'context-managed','config':{'token_meter':'actual'}}},'providers':[]}
    sid='worker-capture';prepared=PreparedBundle(plan,BundleModuleResolver(modules),Bundle(name='fixture',session=plan['session']))
    session=await prepared.create_session(session_id=sid);c=session.coordinator
    failures=[]
    execute=c.get('orchestrator').execute
    async def recorded_execute(*args,**kwargs):
        try:return await execute(*args,**kwargs)
        except Exception as error:
            import traceback
            failures.append(''.join(traceback.format_exception(error,limit=20))[-6000:])
            raise
    c.get('orchestrator').execute=recorded_execute
    await c.mount('providers',raw,name='configured')
    hook_cleanup=None
    computer=None
    if computer_hook:
        from amplifier_module_hook_computer_use import mount
        class ScreenshotAuthority:
            name='fixture_screenshot'
            description='Fixture capture authority; never invoked'
            input_schema={'type':'object','properties':{}}
            computer_use_screenshots=True
            def _take_issued_capture_result(self,*args,**kwargs):
                raise AssertionError('No screenshot was requested')
            async def execute(self,*args,**kwargs):
                raise AssertionError('No desktop operation is permitted')
        if image:
            import io
            from PIL import Image
            import amplifier_module_tool_computer_use as tool_module
            from amplifier_module_tool_computer_use.backend import ScreenGeometry, BackendError
            class Backend:
                name='fixture';is_remote=False
                def screen_geometry(self):return ScreenGeometry(32,32,0,0)
                def list_monitors(self):raise BackendError('Fixture has no monitor enumeration')
                def capture(self,region=None):
                    output=io.BytesIO();Image.new('RGB',(32,32),'navy').save(output,format='PNG')
                    return output.getvalue()
                def close(self):pass
            monkeypatch.setattr(tool_module,'SHOT_DIR',tmp_path/'shots')
            computer=tool_module.ComputerTool(Backend(),{'max_edge':32})
            computer.resolve_display()
        await c.mount('tools',computer or ScreenshotAuthority(),name='fixture_screenshot')
        hook_cleanup=await mount(c)
    async def capture_image():
        nonlocal image_path,image_digest
        if computer:
            result=await computer.execute({'action':'screenshot'})
            assert result.success, result.error
            from amplifier_module_hook_computer_use import _parse_marker
            image_path=Path(_parse_marker(result.model_dump())['images'][0])
            image_digest=hashlib.sha256(image_path.read_bytes()).hexdigest()
            await c.hooks.emit('tool:post',{'tool_name':'fixture_screenshot',
                'tool_call_id':'capture','result':result.model_dump()})
            await c.get('context').set_messages([
                {'role':'assistant','content':'','tool_calls':[{'id':'capture','type':'function',
                 'function':{'name':'fixture_screenshot','arguments':'{"action":"screenshot"}'}}]},
                {'role':'tool','name':'fixture_screenshot','tool_call_id':'capture',
                 'content':result.model_dump_json()+'\\nretained appended context'}])
    selection={'instance':'configured','model':model,'effort':'high'}
    c.get('orchestrator').root_provider=SelectedProvider(raw,selection)
    async def instructions():return 'W'*1_000_100
    await c.get('context').set_system_prompt_factory(instructions)
    finished=asyncio.Event();published=[]
    def publish(row):
        published.append(row)
        if row.get('type') in {'generation.finished','generation.failed','generation.detached','runtime.error'}:finished.set()
    monkeypatch.setattr(runtime_worker,'publish',publish)
    async def acquire(workspace,*,runtime,**kw):
        c.register_capability('live.runtime',runtime)
        c.register_capability('live.host',StandaloneHostAdapter())
        c.register_capability('live.checkpoint',AsyncMock())
        return session,runtime,{'history_source':'native','config_inputs':[]}
    monkeypatch.setattr('amplifier_web.host.session.prepare_manager',acquire)
    monkeypatch.setattr('amplifier_web.runtime_qualification.active_install_overrides',lambda *a:None)
    monkeypatch.setattr('amplifier_web.history_revision.recover_pending',lambda *a:None)
    monkeypatch.setattr('amplifier_web.host.naming.LiveSessionNaming',lambda *a:None)
    monkeypatch.setattr('amplifier_web.shared_state.configuration_stamp',lambda *a,**kw:())
    monkeypatch.setattr('amplifier_web.support_diagnostics.runtime_versions',lambda:{})
    worker=runtime_worker.Worker();worker.shared_store=worker.shared_handle=SimpleNamespace()
    worker.shared_store_stamp=lambda *a:();worker.activation_gate=ActivationGate()
    worker.preparation_progress=AsyncMock();worker.history_stamp=lambda:();worker.ownership.register=AsyncMock()
    worker.park=AsyncMock()
    async def bridge(operation,args):
        nonlocal revoked
        if operation=='capacity.admit':
            if revocation == 'admission-file' and image_path is not None and not revoked:
                await asyncio.sleep(0)
                image_path.unlink();revoked=True
                # Validation cannot depend on an enabled numeric observer.
                worker.telemetry.context_inspectors.clear()
            return {'allowed':True,'budgetRevision':0,'admittedAt':1}
        if operation=='context.prepare':return {}
        if operation=='context.manifest':return {'surfaces':[],'inputIds':[]}
        return {}
    worker.bridge=bridge
    if restore:
        path=home/'sessions'/sid/'control-state.json';path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'selection':selection,'budget':{'maxOutputTokens':321}}))
    try:
        await worker.start({'id':sid,'workspace':str(workspace),'selection':selection},raise_errors=True,recover_bundle=False)
        await capture_image()
        await worker.runtime.submit(Input('user','Complete without tools.',activation=worker.activation))
        await asyncio.wait_for(finished.wait(),20)
        if revocation != 'none':
            assert revoked
            assert not any(p['path']=='/v1/responses' for p in paths)
            errors=[row['event'] for row in published
                    if row.get('type')=='execution.event' and row.get('event',{}).get('kind')=='llm'
                    and row['event'].get('phase')=='error']
            assert len(errors)==1 and errors[0].get('admittedAt')==1
            return
        assert any(row.get('type')=='generation.finished' for row in published), published
        await worker.runtime.submit(Input('stop',activation=worker.activation))
        await worker.execution
        observer=worker.telemetry.context_inspectors[sid];await observer.drain();row=observer.store.read()['selected']
        evidence('full-worker-'+str(restore)+'-'+str(computer_hook)+'-'+str(image)+'-'+revocation+'-'+model,{
            'header':row,'requests':paths,'workerStart':True,
            'rootType':type(c.get('orchestrator').root_provider).__name__,
            'computerHookMounted':computer_hook,'issuedImageSha256':image_digest,
            'computerHookWrapped':getattr(raw,'_amplifier_computer_use_wrapped',False)})
        assert row['budgetStatus']=='captured'
        assert row['budget']['measurement']['input_tokens']==100
        expected_window=1_050_000 if model=='gpt-6-astra' else None
        assert row['budget'].get('context_window_tokens')==expected_window
        assert row['limits']['modelWindow']==expected_window
        if restore:
            assert row['budget']['max_output_tokens']==321
        assert len({p['inputHash'] for p in paths})==1
        for p in paths:
            assert p['model']==model
            assert p['imageDigests']==([image_digest] if image and computer_hook else [])
            if image and computer_hook:
                assert p['imageTypes']==['input_image']
                assert p['referenceRetained'] and p['toolCallRetained']
    finally:
        try:
            if worker.execution and not worker.execution.done():
                if not worker.runtime.closed:
                    await worker.runtime.submit(Input('stop',activation=worker.activation))
                await asyncio.wait_for(worker.execution,5)
        except Exception as error:
            import traceback
            failures.append(''.join(traceback.format_exception(error,limit=20))[-6000:])
        finally:
            evidence('full-worker-lifecycle-'+str(restore)+'-'+str(computer_hook)+'-'+str(image)+'-'+revocation+'-'+model,{'events':published,'requests':paths,'revoked':revoked,'failures':failures})
        if worker.controls:await worker.controls.close()
        if hook_cleanup:await hook_cleanup()
        if computer:computer._backend.close()
        await session.cleanup();await client.close()
