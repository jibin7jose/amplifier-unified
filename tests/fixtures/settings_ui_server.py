"""Isolated settings UI fixture: synthetic providers and runtime, no account calls."""
import asyncio,copy,json,os,signal,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from aiohttp import web
from amplifier_web.server import create_app
from amplifier_web.host.config import write_private
from amplifier_web.setup import SetupManager
from amplifier_web.bundles import BundleManager
from amplifier_web.updates import group_sources
from amplifier_web.smart_tools import SmartToolsManager
import yaml
if os.environ.get('AMPLIFIER_TEST_PACKAGE_ROOT'):
 import amplifier_web
 actual_package = Path(amplifier_web.__file__).resolve().parent
 assert actual_package == Path(os.environ['AMPLIFIER_TEST_PACKAGE_ROOT']).resolve(), 'Fixture must import the exact installed package'
 # Mobile harnesses consume stdout for the URL; stderr is inherited into the
 # scenario evidence log so successful origin checks cannot disappear.
 print(json.dumps({'installedPackageVerified':True,'packageRoot':str(actual_package)}),file=sys.stderr,flush=True)
PLAN={'session':{'orchestrator':{'module':'loop-live','config':{'max_iterations':10}},'context':{'module':'context-simple','config':{'max_tokens':1000}}},'providers':[{'module':'provider-openai','id':'openai','config':{'default_model':'fixture-model'}}],'tools':[{'module':'tool-filesystem','config':{'read_only':False,'max_bytes':2000}}],'hooks':[{'module':'hooks-logging','config':{'enabled':True}}]}
class Runtime:
 def __init__(self):self.plan=copy.deepcopy(PLAN)
 async def start(self,session,emit,*,preserve_emit=False,guard=None):
  if guard:guard()
  await emit('runtime.status',{'sessionId':session['id'],'status':'ready'})
 async def stop(self,*args,reason="stopped"):pass
 async def close(self):pass
 async def control(self,sid,operation,args,*,guard=None):
  if guard:guard()
  if operation=='configuration.apply':
   from amplifier_web.configuration_recovery import receipt_path,seal
   self.plan=copy.deepcopy(args['config'])
   attempt=args['_configurationAttempt']
   # This UI fixture simulates persistence; native writes have separate tests.
   write_private(receipt_path(attempt),json.dumps(seal(attempt,'saved_not_mounted',terminal=True)))
   return {'requiresRestart':True}
  if operation=='configuration.inspect':return {'plan':self.plan,'capabilities':{'configuration':True}}
  return {}
async def probe(self,action,args,workspace):
 await asyncio.sleep(.25)
 result={'providerMetadata':{'module':args.get('module','provider-openai'),'info':{'config_fields':[{'id':'reasoning_effort','display_name':'Reasoning effort','choices':['low','high'],'field_type':'choice'}]},'configSchema':{}}}
 if action=='providers.models':result.update(models=[{'id':'fixture-model'},{'id':'fixture-alternative'}],modelsProviderId=args['id'])
 if action=='providers.test':result['test']={'reachable':True,'modelCount':1,'providerId':args['id'],'method':'provider.list_models'}
 return result
SetupManager.probe=probe
async def cached_routing(self,workspace):pass
SetupManager.ensure_routing_catalog=cached_routing
async def discover(self,url):
 return {'candidates':[{'name':name,'path':name+'.yaml','uri':url+'#'+name+'.yaml','kind':'behavior'} for name in ['base','dev-tools','research']]}
BundleManager.discover=discover
# Exercise real operation receipts and connection persistence without fetching
# or executing third-party packages during settings acceptance.
async def catalog(self):
 await asyncio.sleep(.2)
 rows=[{'id':'fixture-tool','name':'Fixture catalog tool','description':'A discoverable specialist tool','repository':'https://example.com/fixture-tool','ref':'main','path':'.'}]
 await self._change(lambda state:state.update(catalog=rows))
 return {'items':rows,'commit':'fixture-revision'}
async def inspect_tool(self,args):
 await asyncio.sleep(.2)
 return {**args,'name':'Fixture catalog tool','description':'A discoverable specialist tool','commit':'fixture-revision','manifest':{'name':'Fixture catalog tool','version':'1.0','requires':'Python 3.13'},'pythonSupported':True}
async def install_tool(self,args):
 await asyncio.sleep(.2)
 result={**args,'id':'fixture-tool','name':'Fixture catalog tool','commit':'fixture-revision','binDir':'/fixture/bin','status':'installed','guidance':'Configure the documented MCP executable.'}
 await self._change(lambda state:state.update(installations=[result]))
 return result
SmartToolsManager.catalog=catalog
SmartToolsManager.inspect=inspect_tool
SmartToolsManager.install=install_tool
class RecoveryRuntime(Runtime):
 def __init__(self):
  super().__init__();self.calls=[];self.saved={};self.loaded={};self.release=asyncio.Event()
 async def start(self,session,emit,*,preserve_emit=False,guard=None):
  if guard:guard()
  sid=session['id'];self.calls.append({'sessionId':sid,'operation':'start'})
  if sid in self.saved:
   await asyncio.wait_for(self.release.wait(),30)
   if guard:guard()
   self.loaded[sid]=copy.deepcopy(self.saved[sid])
  await emit('runtime.status',{'sessionId':sid,'status':'ready'})
 async def stop(self,sid,*,reason='stopped'):
  self.calls.append({'sessionId':sid,'operation':'stop'})
 async def close(self):self.release.set()
 async def control(self,sid,operation,args,*,guard=None):
  if guard:guard()
  self.calls.append({'sessionId':sid,'operation':operation})
  if operation=='configuration.apply':
   from amplifier_web.configuration_recovery import receipt_path,seal
   self.saved[sid]=copy.deepcopy(args['config'])
   write_private(receipt_path(args['_configurationAttempt']),json.dumps(seal(args['_configurationAttempt'],'saved_not_mounted',terminal=True)))
   return {'requiresRestart':True,'configuration':copy.deepcopy(self.saved[sid])}
  if operation=='configuration.inspect':return {'plan':copy.deepcopy(self.loaded.get(sid,PLAN)),'capabilities':{'configuration':True}}
  return {}

async def add_recovery_fixture(app):
 from amplifier_web.auth import SESSION_COOKIE,new_session
 from amplifier_web.configuration_recovery import binding,receipt_path,seal
 service=app['service'];runtime=service.runtime;originals={};ids={}
 for label,effect in [('Recoverable owner','none'),('Unknown owner','unknown'),('Saved owner','saved_not_mounted'),('Legacy owner',None)]:
  session=service._new_session({'title':label,'bundle':'anchors'})
  service.state['sessions'].append(session);sid=session['id'];ids[label]=sid
  session['collaborationGeneration']={'id':'completed-fixture-turn','inputIds':['synthetic-input'],'terminal':True}
  session['configurationBusy']=True
  if effect is None:
   session['pendingConfiguration']={'phase':'error','error':'PRIVATE_RECOVERY_SENTINEL'}
   continue
  desired=copy.deepcopy(PLAN);desired['tools'][0]['config']['max_bytes']=4096
  record=service.management.create_configuration_operation(session,desired,'fixture-'+label,None,
   {'origin':'ui','target':service.management.configuration_target_binding(session)})
  attempt=binding(record);attempt.update(nativeSessionId=sid,workerPid=None)
  record['attempts']=[attempt]
  write_private(service.management.configuration_record_path(sid,record['id']),json.dumps(record))
  service.management.configuration_transition(session,record['id'],phase='error',stage='dispatching',effect='unknown',attemptId=attempt['attemptId'],error='PRIVATE_RECOVERY_SENTINEL')
  write_private(receipt_path(attempt),json.dumps(seal(attempt,effect,terminal=True)))
  session['configurationOperationFence']=record['id']
  originals[sid]=copy.deepcopy(record['request'])
 service.state['selectedSessionId']=ids['Recoverable owner'];service._publish()
 async def cookie(request):
  response=web.json_response({'ready':True});response.set_cookie(SESSION_COOKIE,new_session(app['session_secret']),httponly=True,samesite='Strict');return response
 async def observe(request):
  rows={}
  for label,sid in ids.items():
   session=service._session(sid);pending=session.get('pendingConfiguration',{});record=None
   if pending.get('operationId'):record=service.management.configuration_record(sid,pending['operationId'])
   rows[label]={'sessionId':sid,'phase':pending.get('phase'),'effect':pending.get('effect'),
    'requestUnchanged':record is None or record['request']==originals[sid],
    'attempts':len(record.get('attempts',[])) if record else 0}
  return web.json_response({'sessions':rows,'calls':runtime.calls,'loadedLimits':{sid:plan['tools'][0]['config']['max_bytes'] for sid,plan in runtime.loaded.items()}})
 async def release(request):
  runtime.release.set();return web.json_response({'released':True})
 app.router.add_post('/fixture/recovery-cookie',cookie)
 app.router.add_get('/fixture/recovery-observations',observe)
 app.router.add_post('/fixture/recovery-release',release)

async def main(home):
 if os.environ.get('SETTINGS_DECISION_SETUP_FIXTURE'):
  from decision_setup import prepare
  prepare()
 # Never read or change a developer's real machine resource authority.
 from amplifier_web import test_host_resources as resources
 resources.identity=lambda:'fixture-machine'
 resources.directory=lambda:Path(home)/'synthetic-machine-authority'
 resources.observe=lambda:{'cpuCount':16,'memoryMiB':65536,'availableMemoryMiB':50000,'freeBytes':100*1024**3,'loadPercent':10}
 os.environ['AMPLIFIER_HOME']=str(home/'native')
 os.environ['AMPLIFIER_WEB_HOME']=str(home);os.environ['AMPLIFIER_UNIFIED_IMPORT_HOME']=str(home/'legacy');os.environ['FIXTURE_KEY']='fixture-private-key'
 workspace=home/'workspace';workspace.mkdir();(workspace/'project').mkdir();(workspace/'project'/'bundle.yaml').write_text('bundle:\n  name: fixture\n')
 write_private(Path(os.environ['AMPLIFIER_HOME'])/'settings.yaml',yaml.safe_dump({'config':{'providers':[{'id':name,'module':'provider-openai','config':{'api_key':'${FIXTURE_KEY}','default_model':'fixture-model'}} for name in ['one','two','three']]},'routing':{'matrix':'balanced'},'bundle':{'added':{'fixture-root':'foundation:test'}}}))
 matrix={'name':'balanced','roles':{role:{'description':role.title(),'candidates':[{'provider':'one','model':'fixture-model'},{'provider':'two','model':'fallback-model'}]} for role in ['general','fast']}}
 write_private(home/'config/routing/balanced.yaml',yaml.safe_dump(matrix))
 recovery=bool(os.environ.get('SETTINGS_RECOVERY_FIXTURE'))
 app=await create_app(home,workspace=workspace,runtime=RecoveryRuntime() if recovery else Runtime(),voice=False,background_updates=False)
 # The browser fixture uses a synthetic control credential, never local PAM.
 if 'control_token' in app:
  app['control_token']='fixture-browser-control-token'
  app['allowed_origins']=app['allowed_origins']|{'http://127.0.0.1:8957','http://127.0.0.1:8958'}
 service=app['service'];await service.dispatch('session.create',{'title':'Settings test','workspace':str(workspace),'bundle':'anchors'})
 if recovery:await add_recovery_fixture(app)
 if os.environ.get('SETTINGS_DECISION_SETUP_FIXTURE'):
  from decision_setup import attach
  attach(app,workspace)
 async def agent_view(request):
  payload=await request.json()
  result=await service.app_bridge('dispatch',{'action':'view.update','args':{'clientId':payload['clientId'],'patch':payload['patch']}},service._session()['id'])
  return web.json_response(result)
 app.router.add_post('/fixture/agent-view',agent_view)
 async def tool_observations(request):
  # Read-only synthetic evidence: compact frames deliberately omit retained
  # tool operation collections and connection definitions.
  if request.headers.get('Authorization') != 'Bearer fixture-browser-control-token':
   raise web.HTTPForbidden()
  async with service.lock:
   return web.json_response({key:service.smart_tools.state[key] for key in ('operations','servers')})
 app.router.add_get('/fixture/settings-tool-observations',tool_observations)
 service.state['updates']['items']=group_sources([{'id':str(i),'label':f'fixture-source-{i}','status':'current'} for i in range(86)]+[{'id':'copy-'+str(i),'label':'github.com/example/amplifier-bundle-computer-use','kind':'bundle / module','ref':'main','current':'123456789','latest':'abcdefghi','status':'update'} for i in range(2)])
 service.state['updates']['available']=1
 if os.environ.get('SETTINGS_RELEASE_FIXTURE'):
  service.state['releaseNoticeStartVersion']='0.21.0'
  service.state['updates']['application']={'current':'0.21.0','latest':'0.21.0','status':'current','releaseNotes':[
   {'version':'0.21.0','title':'Fixture release','changes':['Settings are simpler.'],'notices':[{'id':key,'title':title,'detail':'Fixture change details.','action':'Review your preferences.'} for key,title in [('settings','Settings scope updated'),('permissions','File access updated')]]},
   {'version':'0.11.0','title':'Earlier fixture release','changes':['Earlier settings release.'],'notices':[{'id':'old-settings','title':'Settings now apply across Amplifier apps','detail':'Earlier change details.','action':'Review earlier guidance.'}]}]}
 return app
async def serve():
 with tempfile.TemporaryDirectory(prefix='amplifier-settings-ui-') as tmp:
  app=await main(Path(tmp));runner=web.AppRunner(app);await runner.setup()
  stopped=asyncio.Event();loop=asyncio.get_running_loop()
  # POSIX browser qualification must unwind app cleanup before the child exits.
  shutdown_signals=(signal.SIGTERM,signal.SIGINT) if os.name=='posix' else ()
  for signum in shutdown_signals:loop.add_signal_handler(signum,stopped.set)
  try:
   site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
   port=site._server.sockets[0].getsockname()[1]
   base=f'http://127.0.0.1:{port}'
   app['allowed_origins']=app['allowed_origins']|{base}
   print(json.dumps({'fixture':'settings-ui','pid':os.getpid(),'base':base}),flush=True)
   await stopped.wait()
  finally:
   try:await runner.cleanup()
   finally:
    for signum in shutdown_signals:loop.remove_signal_handler(signum)
if __name__=='__main__':asyncio.run(serve())
