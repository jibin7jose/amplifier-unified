"""Opt-in offline candidate provider through the actual host/worker admission seam.

No production methods are replaced. Only HTTPX network transport is synthetic;
profiling records entry/source witnesses without changing results.
"""
import asyncio
import copy
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))


def record(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value)+'\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def worker(root):
    import httpx
    from catalog_candidate_transports import install
    os.environ.update(UNIFIED_CATALOG_CANDIDATES='1',UNIFIED_CATALOG_PROVIDER='openai',
                      UNIFIED_CATALOG_MODE='success',UNIFIED_CATALOG_LOG=str(root/'requests.jsonl'))
    install()
    # Native admission additionally executes one canned response at the HTTP
    # transport boundary, never an inference service or a replacement provider.
    from catalog_candidate_transports import _response, SYNTHETIC_KEY
    def response(http,request):
        if request.method == 'POST' and request.url.host == 'catalog.invalid' and request.url.path.endswith('/responses'):
            assert request.headers['authorization']=='Bearer '+SYNTHETIC_KEY
            body=json.loads(request.content)
            assert not body.get('stream')
            record(root/'requests.jsonl',{'event':'completion','model':body['model']})
            return http.Response(200,json={'id':'fixture-response','object':'response',
                'created_at':1,'model':body['model'],'status':'completed',
                'output':[{'type':'message','id':'fixture-message','role':'assistant','status':'completed',
                           'content':[{'type':'output_text','text':'Fixture response','annotations':[]}]}],
                'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}})
        return _response(http,request)
    http_modules=[httpx]
    if importlib.util.find_spec('httpx2') is not None:
        http_modules.append(importlib.import_module('httpx2'))
    for http in http_modules:
        original=http.AsyncClient.__init__
        def initialize(self,*args,_original=original,_http=http,**kwargs):
            _original(self,*args,**kwargs)
            self._transport=_http.MockTransport(lambda request:response(_http,request))
        http.AsyncClient.__init__=initialize
    from amplifier_web.runtime_worker import Worker, configure_protocol
    trace=root/'worker-witness.jsonl'
    seen=set()
    def profile(frame,event,arg):
        if event!='call':return
        name=frame.f_code.co_name
        file=Path(frame.f_code.co_filename)
        if name=='prepare_manager' and file.name=='session.py' and file.parent.name=='host':
            if name not in seen:record(trace,{'stage':name,'sourceSha256':digest(file)});seen.add(name)
        if name=='require_mounted_binding' and file.name=='provider_catalog.py' and not frame.f_locals.get('catalog'):
            co=frame.f_locals['coordinator']
            providers=co.get('providers') or {}
            for instance, provider in providers.items():
                kind=type(provider);path=inspect.getfile(kind)
                record(trace,{'stage':'mounted-binding','instance':instance,
                    'class':kind.__name__,'module':kind.__module__,
                    'file':path,'sha256':digest(path),'expectedPresent':bool(frame.f_locals.get('expected'))})
    configure_protocol()
    sys.setprofile(profile)
    try:await Worker().run()
    finally:sys.setprofile(None)


async def run(root, mismatch=False):
    import yaml
    from catalog_candidate_transports import CANDIDATES, SYNTHETIC_KEY, MODEL_IDS, OPENAI_DEFAULT_IDS
    root.mkdir(parents=True,exist_ok=False)
    home, workspace, shared=(root/name for name in ('app','workspace','shared'))
    workspace.mkdir();shared.mkdir()
    for key in list(os.environ):
        if key.endswith(('_API_KEY','_ACCESS_TOKEN','_AUTH_TOKEN')) or key in {'OPENAI_KEY','GITHUB_TOKEN','GH_TOKEN','COPILOT_AGENT_TOKEN','COPILOT_GITHUB_TOKEN'}:
            os.environ.pop(key,None)
    os.environ.update(HOME=str(root),AMPLIFIER_HOME=str(shared),AMPLIFIER_WEB_HOME=str(home),
        AMPLIFIER_UNIFIED_IMPORT_HOME=str(root/'legacy'),AMPLIFIER_SESSION_STATE_HOME=str(root/'ownership'),
        AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH=str(root/'capture'),AMPLIFIER_RUNTIME_IMMUTABLE='1',
        UV_OFFLINE='true',PYTHONPATH=str(SOURCE),FIXTURE_OPENAI_KEY=SYNTHETIC_KEY)
    from amplifier_web.bundles import SNAPSHOT_VERSION
    from amplifier_web.builtin_behaviors import APPENDED_DEFAULTS
    from amplifier_web.management import Management
    from amplifier_web.runtime import RuntimeManager, RuntimeStartupError
    from amplifier_web.runtime_controls import override_path
    from amplifier_web.service import AppService
    paths={name:str(Path(importlib.import_module('amplifier_module_'+name.replace('-','_')).__file__).parent)
           for name in ('loop-live','context-simple','provider-openai')}
    candidate=CANDIDATES['providers']['openai']
    package=Path(paths['provider-openai'])
    for name,sha in candidate['files'].items():
        assert digest(package/name)==sha,(name,'candidate source mismatch')
    row={'id':'candidate-openai','module':'provider-openai','source':paths['provider-openai'],
         'config':{'api_key':'${FIXTURE_OPENAI_KEY}','base_url':'https://catalog.invalid/openai/v1',
                   'default_model':MODEL_IDS['openai'][0],'max_retries':0,'use_streaming':False}}
    plan={'session':{'orchestrator':{'module':'loop-live','config':{'use_streaming':False}},
                     'context':{'module':'context-simple','source':paths['context-simple']}},'providers':[row]}
    bundle=root/'bundle.yaml'
    bundle.write_text(yaml.safe_dump({'bundle':{'name':'candidate-binding','version':SNAPSHOT_VERSION},**plan}))
    (shared/'settings.yaml').write_text(yaml.safe_dump({'bundle':{'active':str(bundle),'app':[]},
        'web_bundles':{'excluded':list(APPENDED_DEFAULTS)},'sources':{'modules':paths},
        'config':{'providers':[row]}}))
    runtime=RuntimeManager(command=[sys.executable,'-u',str(Path(__file__).resolve()),'--worker',str(root)],
        startup_timeout=60,retention={'prewarm_on_select':False})
    app=AppService(home,runtime,workspace=workspace);app.management=Management(app)
    runtime.home,runtime.app_bridge=home,app.app_bridge
    events=[];process=None;result={}
    async def emit(event,data):
        events.append(event)
        await app.on_runtime_event(event,data)
    try:
        response=await app.dispatch('session.create',{'bundle':str(bundle),'title':'Candidate binding',
            'selection':{'instance':'candidate-openai','model':'gpt-4o'}},
            command_id='candidate-binding-create',include_state=False)
        assert response['accepted'],response
        session=app._session()
        assert session['modelConnectionKey']
        await app.dispatch('session.naming',{'id':session['id'],'automatic':False},
                           command_id='candidate-naming-disable',include_state=False)
        if mismatch:
            # Existing conversation override: inventory/account A remains unchanged.
            changed=copy.deepcopy(plan);changed['providers'][0]['config']['api_key']='synthetic-account-b'
            native=session.get('runtimeSessionId') or session.get('nativeIdentity') or session['id']
            saved=override_path(native);saved.parent.mkdir(parents=True,exist_ok=True)
            saved.write_text(json.dumps(changed))
        try:
            receipt=await runtime.send(session,'Synthetic admission fixture','candidate-input',emit)
        except RuntimeStartupError as exc:
            assert mismatch, str(exc)
            assert 'mounted connection differs' in str(exc.__cause__),repr(exc.__cause__)
            result['admission']='rejected-account-change'
        else:
            assert not mismatch,'Different account admitted'
            assert receipt['accepted'] is True and receipt['inputId']=='candidate-input',receipt
            result['sendReceipt']=receipt
            async with asyncio.timeout(30):
                while 'assistant.message' not in events:
                    await asyncio.sleep(.05)
            summary=await runtime.control(session['id'],'configuration.providers',{})
            mounted=next(row for row in summary['providers'] if row['id']=='candidate-openai')
            assert mounted['connectionKey']==session['modelConnectionKey']
            discovered=await runtime.control(session['id'],'configuration.providerModels',{'instance':'candidate-openai'})
            assert [m['id'] for m in discovered['models']]==OPENAI_DEFAULT_IDS
            assert summary['effective']['model']=='gpt-4o', 'Recommendations must not reset the hidden exact choice'
            result.update(admission='accepted',models=discovered['models'])
        process=runtime.workers[session['id']]['process']
        witnesses=[json.loads(line) for line in (root/'worker-witness.jsonl').read_text().splitlines()]
        assert any(w['stage']=='prepare_manager' for w in witnesses)
        binding=[w for w in witnesses if w['stage']=='mounted-binding']
        assert binding and all(w['expectedPresent'] for w in binding)
        assert all(w['module']=='amplifier_module_provider_openai' for w in binding),binding
        assert all(w['sha256']==candidate['files']['__init__.py'] for w in binding)
        requests=[json.loads(line) for line in (root/'requests.jsonl').read_text().splitlines()] if (root/'requests.jsonl').exists() else []
        assert not any(row['event']=='forbidden' for row in requests)
        completions=[row for row in requests if row['event']=='completion']
        assert len(completions)==(0 if mismatch else 1),completions
        if completions:assert completions[0]['model']=='gpt-4o'
        requests=[row for row in requests if row['event']=='request']
        assert len(requests)==(0 if mismatch else 1),requests
        result.update(candidate=candidate['commit'],qualification='focused installed native fixture; no full runtime qualification',
                      mismatch=mismatch,transportRequests=requests,syntheticCompletions=completions,
                      productionWitnesses=witnesses,events=events)
    finally:
        await app.close()
        result['cleanup']={'workersRemaining':len(runtime.workers),
                           'workerReturncode':process.returncode if process else None}
        (root/'result.json').write_text(json.dumps(result,indent=2))
    assert not runtime.workers
    assert process is not None and process.returncode is not None
    print(json.dumps(result))


if __name__=='__main__':
    if sys.argv[1]=='--worker':asyncio.run(worker(Path(sys.argv[2])))
    else:asyncio.run(run(Path(sys.argv[1]).resolve(),mismatch='--mismatch' in sys.argv))
