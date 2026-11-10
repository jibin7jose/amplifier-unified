// SOURCE-ONLY authoring: run once, serially, in the manager's qualified isolated
// environment AFTER packaging this exact candidate. No Vite or mock Diagram.
// All path arguments except --root/--browser are relative to the owned --root.
// Required: --python --env --source --static --static-manifest --wheel
// --source-sha --base-sha --wheel-sha --css-sha --viewer-sha --harness-sha
// --version --readiness --baseline --tmp-root --proof --browser.
// --baseline is the immutable measured geometry directory (not a combined
// million-line report). --proof must not exist. No automatic retry/overwrite.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {existsSync,mkdirSync,readFileSync,realpathSync,writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

const flags={};
for(let i=2;i<process.argv.length;i+=2){
 assert.ok(process.argv[i]?.startsWith('--')&&process.argv[i+1],'Use --name value pairs');
 const name=process.argv[i].slice(2);assert.ok(!(name in flags),'Duplicate --'+name);flags[name]=process.argv[i+1];
}
for(const name of ['root','python','env','source','static','static-manifest','wheel','source-sha','base-sha','wheel-sha',
 'css-sha','viewer-sha','harness-sha','version','readiness','baseline','tmp-root','proof','browser'])assert.ok(flags[name],'Missing --'+name);
assert.ok(path.isAbsolute(flags.root),'--root must be absolute');
const root=realpathSync(flags.root),digest=value=>createHash('sha256').update(value).digest('hex');
function beneath(value){
 assert.ok(!path.isAbsolute(value)&&!value.split(/[\\/]/).includes('..'),'Require relative paths inside --root');
 const result=path.resolve(root,value);assert.ok(result.startsWith(root+path.sep),'Path escapes root');
 let parent=result;while(!existsSync(parent))parent=path.dirname(parent);
 assert.ok(realpathSync(parent).startsWith(root+path.sep),'Symlink escapes root');return result;
}
const source=beneath(flags.source),out=beneath(flags.proof),tmp=beneath(flags['tmp-root']),baseline=beneath(flags.baseline);
// A qualified venv's interpreter normally symlinks to its selected Python
// installation. Allow that executable link, NOT escaping data/state paths.
assert.ok(!path.isAbsolute(flags.python)&&!flags.python.split(/[\\/]/).includes('..'));
const python=path.resolve(root,flags.python),env=beneath(flags.env);
assert.ok(python.startsWith(env+path.sep)&&realpathSync(path.dirname(python)).startsWith(env+path.sep)&&existsSync(python));
assert.ok(!existsSync(out),'Existing proof is immutable; do not replay');
assert.ok(existsSync(tmp)&&realpathSync(tmp).length<=64,'Require existing qualified short owned TMPDIR');
assert.ok(/^[0-9a-f]{40}$/.test(flags['source-sha'])&&/^[0-9a-f]{40}$/.test(flags['base-sha']));
for(const name of ['wheel-sha','css-sha','viewer-sha','harness-sha'])assert.match(flags[name],/^[0-9a-f]{64}$/);
for(const [file,hash] of [['frontend/src/unified.css',flags['css-sha']],['frontend/src/canvas-viewer.jsx',flags['viewer-sha']],
 ['frontend/tests/embedded-diagram-browser.mjs',flags['harness-sha']]])assert.equal(digest(readFileSync(path.join(source,file))),hash,'Source byte mismatch: '+file);
assert.equal(digest(readFileSync(fileURLToPath(import.meta.url))),flags['harness-sha'],'Executing a different harness');
assert.equal(digest(readFileSync(beneath(flags.wheel))),flags['wheel-sha']);
const readiness=JSON.parse(readFileSync(beneath(flags.readiness),'utf8'));
assert.equal(readiness.sourceHEAD,flags['base-sha'],'Readiness must qualify the admitted current base');
assert.equal(readiness.version,flags.version);
assert.ok(readiness.status==='READY'||readiness.realInstalledPMReadiness?.startsWith('READY '),'A wheel alone is not READY');
const budget=Number(flags['budget-seconds']||600);assert.ok(budget>0&&budget<=600);
mkdirSync(out,{mode:0o700});
const receipt={status:'STARTING',scope:'Synthetic packaged embedded-stage invariants, NOT original #417 closure',attempts:1,
 sourceSHA:flags['source-sha'],baseSHA:flags['base-sha'],version:flags.version,wheelSHA:flags['wheel-sha'],
 sourceHashes:{css:flags['css-sha'],viewer:flags['viewer-sha'],harness:flags['harness-sha']},
 readinessSHA:digest(readFileSync(beneath(flags.readiness))),budgetSeconds:budget,contextCount:0,pageCount:0,
 models:0,pageErrors:[],blocked:[],actions:[],reports:[],streams:[],cells:[],lifecycle:[],standalone:[],
 pixelJudgment:'PENDING_MANAGER: inspect retained top/bottom and dense 390px PNGs; bounds do not prove legibility',
 originalCase:'NOT_CHECKED: original reporter Markdown and platform unavailable'};
const save=()=>writeFileSync(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
save();

// The test-owned Python fixture imports ONLY the selected noneditable installed
// package. Process-local catalog gates are synthetic; all renderers, storage,
// authentication, HTTP and SSE remain the real installed app.
const pythonFixture=String.raw`
import asyncio, hashlib, importlib.metadata as md, json, os, signal, socket, sys, traceback, zipfile
from pathlib import Path

args=json.loads(sys.argv[1]);root=Path(args['root']).resolve()
def owned(name):
 p=(root/args[name]).resolve();assert p.is_relative_to(root) and p!=root;return p
out=owned('proof');state=out/'private';state.mkdir(mode=0o700)
report={'status':'STARTING','runtimeCalls':[],'dispatches':[],'refusedActions':[],'audit':[],'models':0}
def save():(out/'server-receipt.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert sys.flags.isolated and sys.flags.dont_write_bytecode and sys.version_info>=(3,13)
assert Path(sys.prefix).resolve()==owned('env')
for k in list(os.environ):
 if k.startswith('AMPLIFIER') or k.endswith(('_API_KEY','_TOKEN','_SECRET')) or k in ('GH_TOKEN','GITHUB_TOKEN','AZURE_OPENAI_KEY'):
  os.environ.pop(k,None)
os.environ.update(HOME=str(state/'home'),XDG_CONFIG_HOME=str(state/'config'),XDG_CACHE_HOME=str(state/'cache'),
 TMPDIR=str(owned('tmp-root')),PYTHONPATH='',PYTHONDONTWRITEBYTECODE='1',
 AMPLIFIER_HOME=str(state/'native'),AMPLIFIER_WEB_HOME=str(state/'app'),
 AMPLIFIER_SESSION_STATE_HOME=str(state/'shared'),AMPLIFIER_UNIFIED_IMPORT_HOME=str(state/'legacy'))
for name in ('home','config','cache','native','shared','legacy','workspace'):(state/name).mkdir()
from aiohttp import web
import amplifier_web.server as server
import amplifier_web.service as service_module
import amplifier_web.auth as auth
import amplifier_web.draft_defaults as draft_defaults
from amplifier_web.setup import SetupManager
from amplifier_web.deployment import validate_server
package=Path(server.__file__).resolve().parent;dist=md.distribution('amplifier-unified')
assert package.is_relative_to(owned('env')) and 'site-packages' in str(package)
assert dist.version==args['version']
direct=dist.read_text('direct_url.json')
assert not direct or not json.loads(direct).get('dir_info',{}).get('editable')
assert sha(owned('wheel'))==args['wheel-sha']
static=json.loads(owned('static-manifest').read_text());assert static and all(k.startswith('/') for k in static)
installed={};backend={}
with zipfile.ZipFile(owned('wheel')) as wheel:
 for name in wheel.namelist():
  if not name.startswith('amplifier_web/') or name.endswith('/'):continue
  rel=name.removeprefix('amplifier_web/');expected=hashlib.sha256(wheel.read(name)).hexdigest()
  assert sha(package/rel)==expected,'Wheel/install mismatch '+rel
  if rel.startswith('static/'):
   key='/'+rel.removeprefix('static/');installed[key]=expected
   assert sha(owned('static')/key.lstrip('/'))==expected,'Built source/static mismatch '+key
  elif rel.endswith('.py'):
   assert sha(owned('source')/'amplifier_web'/rel)==expected,'Source/backend mismatch '+rel
   backend[rel]=expected
assert static==installed,'Static manifest is not the exact complete installed wheel inventory'
report['provenance']={'version':dist.version,'sourceSHAAttribution':args['source-sha'],
 'attributionLimit':'Parent supplies commit/cleanliness; source/backend, owned source and static bytes checked separately',
 'wheelSHA':args['wheel-sha'],'backend':backend,'static':installed,'serverImport':server.__file__,'serviceImport':service_module.__file__}
provider={'id':'synthetic-diagram-fixture','module':'provider-synthetic-no-inference',
 'info':{'display_name':'Synthetic fixture','defaults':{'model':'no-model'},'config_fields':[]},'configSchema':{'fields':[]}}
catalog={'configurationRevision':0,'providers':[provider],'effective':{'instance':provider['id'],'model':'no-model'}}
patches=[]
def patch(obj,name,value):patches.append((obj,name,getattr(obj,name)));setattr(obj,name,value)
async def probe(*a,**kw):return {**catalog,'models':[]}
async def defaults(*a,**kw):return {'bundle':'work',**catalog}
patch(SetupManager,'provider_rows',lambda self,workspace:[provider])
patch(SetupManager,'probe',probe);patch(SetupManager,'cached_probe',probe);patch(draft_defaults,'resolve_defaults',defaults)
class SyntheticNoInferenceRuntime:
 def refuse(self,op):
  report['runtimeCalls'].append(op);save();raise RuntimeError('No inference or native runtime admitted: '+op)
 async def control(self,*a,**kw):return self.refuse('control')
 async def start(self,*a,**kw):return self.refuse('start')
 async def send(self,*a,**kw):return self.refuse('send')
 async def close(self):report['runtimeClosed']=True;save()
runtime=SyntheticNoInferenceRuntime()
original=service_module.AppService.dispatch
allowed={'session.create','view.update','view.report','canvas.show','canvas.select','canvas.visibility','canvas.close','canvas.reopen',
 'canvas.view','canvas.report','canvas.reference','canvas.copy','canvas.views.command','canvas.views.update',
 'canvas.views.status','canvas.views.inspect','canvas.versions.inspect','shell.report'}
inner={'canvas.view','canvas.report','canvas.copy','canvas.reference'}
async def dispatch(self,action,values=None,*a,**kw):
 values=values or {};report['dispatches'].append({'action':action});save()
 synthetic=None
 if action=='providers.list':synthetic={'providers':[provider]}
 elif action=='configuration.defaults':synthetic={'bundle':'work',**catalog}
 elif action=='bundles.list':synthetic={'bundles':[{'value':'work','label':'Work'}]}
 elif action=='runtime.control' and values.get('operation')=='configuration.catalog':synthetic=catalog
 if synthetic is not None:return {'accepted':True,'result':synthetic,'state':self.browser_state()}
 if action not in allowed or action=='canvas.views.command' and values.get('action') not in inner:
  report['refusedActions'].append(action);save();raise RuntimeError('Fixture does not admit '+action)
 return await original(self,action,values,*a,**kw)
patch(service_module.AppService,'dispatch',dispatch)
def audit(event,values):
 if event in ('subprocess.Popen','os.system','os.posix_spawn'):
  report['audit'].append(event);save();raise RuntimeError('No SDK/runtime subprocess admitted')
 if event=='socket.connect':
  address=values[1]
  if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):
   report['audit'].append(event);save();raise RuntimeError('No external or Unix socket admitted')
sys.addaudithook(audit)
async def main():
 runner=None;svc=None;sock=None
 try:
  done=asyncio.Event()
  for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,done.set)
  sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128);sock.setblocking(False);port=sock.getsockname()[1]
  assert port not in (8088,8089);origin='http://127.0.0.1:'+str(port);workspace=state/'workspace'
  app=await server.create_app(state/'app',workspace=workspace,runtime=runtime,voice=False,background_updates=False,
   preload_providers=False,server_config=validate_server({'bind':['127.0.0.1'],'port':port,'public_origins':[origin],
   'runtime':{'max_warm_workers':0,'prewarm_on_select':False,'max_background_starts':1}}))
  svc=app['service'];await svc.dispatch('session.create',{'workspace':str(workspace),'title':'Synthetic embedded diagrams'})
  sid=svc.state['selectedSessionId']
  svc.state.setdefault('runtimeControl',{})[sid]={'configuration.catalog':catalog}
  svc.state.setdefault('setup',{}).update(providers=[provider],providersLoadedAt=1,providersWorkspace=str(workspace))
  svc.state['registeredBundles']=[{'value':'work','label':'Work'}]
  svc.state['view'].update(navPinned=False,navExpanded=False,workSurface='chat')
  svc.state['runtime']['available']=True;svc._publish_full(reason='Synthetic no-inference catalog fixture')
  runner=web.AppRunner(app);await runner.setup();await web.SockSite(runner,sock).start()
  cookie={'name':auth.SESSION_COOKIE,'value':auth.new_session(app['session_secret']),'url':origin,
   'httpOnly':True,'secure':False,'sameSite':'Strict'}
  report['status']='SERVING';save()
  print(json.dumps({'origin':origin,'cookie':cookie,'sessionId':sid,'runtime':type(runtime).__name__,
   'provenance':report['provenance']}),flush=True)
  await done.wait()
 except BaseException:
  report['status']='CANT_CHECK';report['failure']=traceback.format_exc();save();raise
 finally:
  try:
   if runner is not None:await runner.cleanup()
   elif svc is not None:await svc.close()
  finally:
   if not report.get('runtimeClosed'):await runtime.close()
   if sock is not None:sock.close()
   for obj,name,old in reversed(patches):setattr(obj,name,old)
   report['cleanup']={'serviceClosed':getattr(svc,'closed',None),'runtimeClosed':report.get('runtimeClosed'),
    'socketClosed':sock is None or sock.fileno()==-1,'patchesRestored':True};save()
asyncio.run(main())
`;
writeFileSync(path.join(out,'server.py'),pythonFixture,{flag:'wx',mode:0o600});

const chain='flowchart TD\n  T[TOP_SENTINEL] --> N01[Stage 01]\n'+
 Array.from({length:23},(_,i)=>`  N${String(i+1).padStart(2,'0')} --> N${String(i+2).padStart(2,'0')}[Stage ${String(i+2).padStart(2,'0')}]`).join('\n')+
 '\n  N24 --> B[BOTTOM_SENTINEL]';
const fence=(kind,text)=>'```'+kind+'\n'+text+'\n```';
const docs=[
 {name:'tall-chain',content:'# Synthetic tall-chain\n\nOwned synthetic source; not reporter Markdown.\n\n'+fence('mermaid',chain)+'\n\nAFTER_TALL_SENTINEL: following prose.\n',
  fences:1,sentinels:['TOP_SENTINEL','BOTTOM_SENTINEL']},
 {name:'wide-labels',content:'# Synthetic wide labels\n\n'+fence('mermaid','flowchart LR\n  L[LEFT_SENTINEL] --> A["Fixed long multiword intake label with deliberate width"]\n  A --> B["Fixed long multiword planning label with deliberate width"]\n  B --> C["Fixed long multiword validation label with deliberate width"]\n  C --> D["Fixed long multiword delivery label with deliberate width"]\n  D --> R[RIGHT_SENTINEL]\n  B --> X[BRANCH_SENTINEL]')+'\n\nAFTER_WIDE_SENTINEL.\n',
  fences:1,sentinels:['LEFT_SENTINEL','RIGHT_SENTINEL','BRANCH_SENTINEL']},
 {name:'mixed-fences',content:'# Synthetic mixed fences\n\nProse, table and ordinary code precede diagrams.\n\n| Key | Value |\n| --- | --- |\n| synthetic | retained |\n\n'+fence('text','not a diagram\n  exact whitespace')+'\n\n'+
  fence('mermaid','flowchart TD\n  A[MIX_TOP_SENTINEL] --> B[MIX_LEFT_SENTINEL]\n  A --> C[MIX_RIGHT_SENTINEL]')+'\n\n'+
  fence('dot','digraph G { rankdir=LR; a [label="DOT_LEFT_SENTINEL"]; b [label="DOT_RIGHT_SENTINEL"]; a -> b; }')+'\n\n'+fence('mermaid',chain)+'\n\nAFTER_MIXED_SENTINEL: following prose.\n',
  fences:3,sentinels:['MIX_TOP_SENTINEL','MIX_LEFT_SENTINEL','MIX_RIGHT_SENTINEL','DOT_LEFT_SENTINEL','DOT_RIGHT_SENTINEL','TOP_SENTINEL','BOTTOM_SENTINEL']},
 {name:'long-label-small',content:'# Synthetic long label / small graph\n\n'+fence('mermaid','flowchart TD\n  A[TOP_SENTINEL] --> B["A deliberately long valid fixed label distinguishes text and viewBox clipping from the embedded height cap"]\n  B --> C[BOTTOM_SENTINEL]')+'\n\nAFTER_LONG_SENTINEL.\n',
  fences:1,sentinels:['TOP_SENTINEL','BOTTOM_SENTINEL']}
];
const cells=[{width:1280,height:900,focused:false},{width:1280,height:900,focused:true},
 {width:390,height:844,focused:false},{width:390,height:844,focused:true}];
writeFileSync(path.join(out,'synthetic-documents.json'),JSON.stringify(docs,null,2)+'\n',{flag:'wx'});
const stageSelector='[data-canvas-view="primary"] .a-diagram.embedded .a-diagram-stage';
let server,browser,context,page,cdp,ready,secret='',stderr='',deadline,serverExit,clipboardBefore;
const started=Date.now();
const clean=value=>String(value).replaceAll(secret||'__no_cookie__','[fixture-cookie-redacted]');
const timeout=max=>{const remaining=budget*1000-(Date.now()-started);assert.ok(remaining>0,'Finite probe budget exhausted');return Math.max(1,Math.min(max,remaining))};
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const state=()=>page.evaluate(()=>window.amplifier.getState());
const primary=async()=>{const s=await state();return {canvas:s.canvas,view:s.canvasWorkspace?.views?.find(v=>v.viewId==='primary'),presentation:s.view,sessionId:s.selectedSessionId}};
async function settled(count,embedded=true){
 await page.waitForFunction(({count,embedded})=>{
  const s=window.amplifier?.getState(),v=s?.canvasWorkspace?.views?.find(v=>v.viewId==='primary');
  const reports=Object.entries(v?.renderReports||{}).filter(([key])=>embedded?key.startsWith('fence-'):key==='preview');
  return reports.length===count&&reports.every(([,r])=>r.status==='ready')&&
   document.querySelectorAll('[data-canvas-view="primary"] .a-diagram'+(embedded?'.embedded':'')+' .a-diagram-stage img').length===count;
 },{count,embedded},{timeout:timeout(20000)});
 await page.evaluate(async()=>{
  await document.fonts.ready;
  await Promise.all([...document.querySelectorAll('[data-canvas-view="primary"] .a-diagram-stage img')].map(i=>i.decode()));
  await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 });
}
const tick=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
async function focus(value){
 const current=await primary();if(!!current.presentation.canvasFocused===value)return;
 const button=page.getByRole('button',{name:value?'Focus canvas':'Exit canvas focus',exact:true});
 if(await button.isVisible())await button.click();
 else{receipt.focusFallbacks??=[];receipt.focusFallbacks.push({width:(await page.viewportSize()).width,value,reason:'Control not exposed; public view.update'});await action('view.update',{patch:{canvasFocused:value}})}
 await page.waitForFunction(value=>!!window.amplifier.getState().view.canvasFocused===value,value,{timeout:timeout(4000)});
}
async function snapshot(index){
 return page.locator(stageSelector).nth(index).evaluate(stage=>{
  const image=stage.querySelector('img'),r=image.getBoundingClientRect(),s=getComputedStyle(stage),ir=getComputedStyle(image);
  return {top:stage.scrollTop,max:stage.scrollHeight-stage.clientHeight,clientHeight:stage.clientHeight,scrollHeight:stage.scrollHeight,
   label:stage.getAttribute('aria-label'),tabIndex:stage.tabIndex,touchAction:s.touchAction,overflow:s.overflowY,
   overscroll:s.overscrollBehaviorY,transform:ir.transform,src:image.currentSrc,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
   focusVisible:stage.matches(':focus-visible'),outline:{style:s.outlineStyle,width:s.outlineWidth,color:s.outlineColor}};
 });
}

// Measure only the SAME decoded sanitized SVG img. The inert off-screen clone
// supplies SVG bboxes/CTMs, not a replacement renderer or a visual witness.
async function measure(index){
 return page.locator(stageSelector).nth(index).evaluate(async stage=>{
  const image=stage.querySelector('img'),uri=image.currentSrc;
  if(!uri.startsWith('data:image/svg+xml;charset=utf-8,'))throw Error('Not the real sanitized SVG image');
  const xml=decodeURIComponent(uri.slice(uri.indexOf(',')+1)),parsed=new DOMParser().parseFromString(xml,'image/svg+xml');
  if(parsed.querySelector('parsererror')||parsed.documentElement.localName!=='svg')throw Error('Invalid sanitized SVG');
  for(const e of [parsed.documentElement,...parsed.querySelectorAll('*')]){
   if(['script','foreignObject','image','a','use','animate','set'].includes(e.localName))throw Error('Unexpected executable/reference tag');
   for(const a of e.attributes)if(/^on/i.test(a.name)||/href$/i.test(a.name))throw Error('Unexpected reference/event attribute');
  }
  if(/@import/i.test(xml))throw Error('Unexpected external SVG stylesheet');
  for(const m of xml.matchAll(/url\(\s*([^)]*)\)/gi))if(!m[1].trim().replace(/^["']|["']$/g,'').startsWith('#'))throw Error('External SVG asset');
  const holder=document.createElement('div');holder.inert=true;
  Object.assign(holder.style,{position:'fixed',left:'-100000px',top:'0',visibility:'hidden',pointerEvents:'none'});
  const svg=document.importNode(parsed.documentElement,true),vb=svg.viewBox.baseVal;
  const viewBox={x:vb.x,y:vb.y,width:vb.width,height:vb.height};
  if(!(vb.width>0&&vb.height>0))throw Error('No positive viewBox');
  svg.setAttribute('width',String(vb.width));svg.setAttribute('height',String(vb.height));
  Object.assign(svg.style,{maxWidth:'none',width:vb.width+'px',height:vb.height+'px'});holder.append(svg);document.body.append(holder);
  const extents=[];
  try{
   await document.fonts.ready;const matrix=svg.getCTM().inverse();
   for(const e of svg.querySelectorAll('text,rect,circle,ellipse,polygon,polyline,path,line')){
    if(e.closest('defs,clipPath,marker,mask'))continue;
    const b=e.getBBox(),m=matrix.multiply(e.getCTM()),style=getComputedStyle(e),pad=style.stroke!=='none'?(parseFloat(style.strokeWidth)||0)/2:0;
    const points=[[b.x-pad,b.y-pad],[b.x+b.width+pad,b.y-pad],[b.x-pad,b.y+b.height+pad],[b.x+b.width+pad,b.y+b.height+pad]]
     .map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));
    const x=Math.min(...points.map(p=>p.x)),y=Math.min(...points.map(p=>p.y));
    const width=Math.max(...points.map(p=>p.x))-x,height=Math.max(...points.map(p=>p.y))-y;
    if(![x,y,width,height].every(Number.isFinite))throw Error('Nonfinite extent');
    extents.push({tag:e.localName,text:e.localName==='text'?e.textContent.trim():null,svgBox:{x,y,width,height},
     font:e.localName==='text'?{family:style.fontFamily,size:style.fontSize,weight:style.fontWeight,available:document.fonts.check(style.fontSize+' '+style.fontFamily)}:null,
     insideViewBox:x>=vb.x-1&&y>=vb.y-1&&x+width<=vb.x+vb.width+1&&y+height<=vb.y+vb.height+1});
    if(extents.length>5000)throw Error('Synthetic geometry budget exceeded');
   }
  }finally{holder.remove()}
  const ancestors=[];for(let e=stage;e;e=e.parentElement){ancestors.push(e);if(e.id==='amp-one')break}
  if(ancestors.at(-1)?.id!=='amp-one')throw Error('Missing host boundary');
  for(const e of ancestors){const s=getComputedStyle(e);if(s.transform!=='none'||s.clipPath!=='none'||/paint/.test(s.contain))throw Error('Unsupported ancestor clipping')}
  const r=image.getBoundingClientRect(),s=getComputedStyle(image);
  if(s.transform!=='none'||s.objectFit!=='contain'||s.objectPosition!=='50% 50%'||!(r.width>0&&r.height>0))throw Error('Unsupported image mapping');
  const scale=Math.min(r.width/viewBox.width,r.height/viewBox.height);
  return {xml,viewBox,extents,scale,renderedLabelHeights:extents.filter(e=>e.tag==='text').map(e=>e.svgBox.height*scale)};
 });
}
async function witness(index,measurement){
 return page.locator(stageSelector).nth(index).evaluate((stage,{viewBox,extents})=>{
  const image=stage.querySelector('img'),r=image.getBoundingClientRect(),scale=Math.min(r.width/viewBox.width,r.height/viewBox.height);
  const ox=r.x+(r.width-viewBox.width*scale)/2,oy=r.y+(r.height-viewBox.height*scale)/2;
  const clips=[{x:0,y:0,width:innerWidth,height:innerHeight}];
  for(let e=stage;e;e=e.parentElement){
   const s=getComputedStyle(e),r=e.getBoundingClientRect();
   if(s.overflowX!=='visible'||s.overflowY!=='visible')clips.push({x:r.x+e.clientLeft,y:r.y+e.clientTop,width:e.clientWidth,height:e.clientHeight,
    axisX:s.overflowX!=='visible',axisY:s.overflowY!=='visible'});
   if(e.id==='amp-one')break;
  }
  const visible=(x,y)=>clips.every(c=>(c.axisX===false||x>=c.x-1&&x<=c.x+c.width+1)&&(c.axisY===false||y>=c.y-1&&y<=c.y+c.height+1));
  return {scrollTop:stage.scrollTop,previewTop:stage.closest('.a-canvas-preview').scrollTop,
   corners:extents.map(e=>{const b=e.svgBox;return [[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]]
    .map(([x,y])=>visible(ox+(x-viewBox.x)*scale,oy+(y-viewBox.y)*scale))})};
 },measurement);
}
async function wheel(index,delta){
 const point=await page.locator(stageSelector).nth(index).evaluate(stage=>{
  let left=0,top=0,right=innerWidth,bottom=innerHeight;
  for(let e=stage;e;e=e.parentElement){
   const r=e.getBoundingClientRect(),s=getComputedStyle(e);
   if(e===stage||s.overflowX!=='visible'){left=Math.max(left,r.x+e.clientLeft);right=Math.min(right,r.x+e.clientLeft+e.clientWidth)}
   if(e===stage||s.overflowY!=='visible'){top=Math.max(top,r.y+e.clientTop);bottom=Math.min(bottom,r.y+e.clientTop+e.clientHeight)}
   if(e.id==='amp-one')break;
  }
  if(right-left<4||bottom-top<4)throw Error('Stage has no visible wheel target');
  return {x:(left+right)/2,y:(top+bottom)/2};
 });
 await page.mouse.move(point.x,point.y);await page.mouse.wheel(0,delta);await tick();
}
async function nativeReach(index,measurement,stem){
 const stage=page.locator(stageSelector).nth(index),seen=measurement.extents.map(()=>[false,false,false,false]),scrolls=[],screenshots=[],sentinelEnds=[];
 const collect=async()=>{const value=await witness(index,measurement);value.corners.forEach((corners,i)=>corners.forEach((yes,j)=>seen[i][j]||=yes));
  scrolls.push({top:value.scrollTop,previewTop:value.previewTop});return value};
 for(const alignment of ['start','end']){
  await stage.evaluate((e,block)=>e.scrollIntoView({block}),alignment);
  await stage.focus();await stage.press('Home');await wheel(index,-50000);
  await page.waitForFunction(({selector,index})=>document.querySelectorAll(selector)[index].scrollTop<=1,
   {selector:stageSelector,index},{timeout:timeout(3000)});
  await stage.evaluate((e,block)=>e.scrollIntoView({block}),alignment);
  assert.ok((await snapshot(index)).top<=1,'Native top is unreachable');
  const topWitness=await collect();
  if(alignment==='start'){
   const matches=measurement.extents.map((e,i)=>({e,i})).filter(({e})=>e.tag==='text'&&e.text.includes('TOP_SENTINEL'));
   assert.ok(matches.every(({i})=>topWitness.corners[i].every(Boolean)),'TOP not visible at actual scrollTop 0');
   sentinelEnds.push({end:'top',top:topWitness.scrollTop,labels:matches.map(({e})=>e.text),visible:matches.every(({i})=>topWitness.corners[i].every(Boolean))});
   const file=stem+'-top.png';await page.screenshot({path:path.join(out,file)});screenshots.push({file,sha256:digest(readFileSync(path.join(out,file)))});
  }
  for(let step=0;step<64;step++){
   const before=await snapshot(index);if(before.top>=before.max-1)break;
   await wheel(index,Math.min(300,Math.max(1,before.clientHeight/2)));
   await page.waitForFunction(({selector,index,top})=>document.querySelectorAll(selector)[index].scrollTop>top,
    {selector:stageSelector,index,top:before.top},{timeout:timeout(3000)});
   await tick();await collect();timeout(1);
  }
  const last=await snapshot(index);assert.ok(last.top>=last.max-1,'Native bottom not reached within finite scroll budget');
  if(alignment==='end'){
   const bottomWitness=await collect(),matches=measurement.extents.map((e,i)=>({e,i})).filter(({e})=>e.tag==='text'&&e.text.includes('BOTTOM_SENTINEL'));
   assert.ok(matches.every(({i})=>bottomWitness.corners[i].every(Boolean)),'BOTTOM not visible at actual maximum scrollTop');
   sentinelEnds.push({end:'bottom',top:bottomWitness.scrollTop,max:last.max,labels:matches.map(({e})=>e.text),visible:matches.every(({i})=>bottomWitness.corners[i].every(Boolean))});
   const file=stem+'-bottom.png';await page.screenshot({path:path.join(out,file)});screenshots.push({file,sha256:digest(readFileSync(path.join(out,file)))});
  }
 }
 return {corners:seen,allGeometryReachable:seen.length>0&&seen.every(c=>c.every(Boolean)),
  allLabelsReachable:measurement.extents.some(e=>e.tag==='text')&&measurement.extents.every((e,i)=>e.tag!=='text'||seen[i].every(Boolean)),
  scrolls,screenshots,sentinelEnds,rule:'Union of witnessed rectangle corners during overlapping ACTUAL native wheel scroll states; not simultaneous 650px visibility or glyph-pixel proof'};
}
function canonical(measurement){
 return {viewBox:measurement.viewBox,extents:measurement.extents.map(e=>({tag:e.tag,text:e.text,svgBox:e.svgBox,font:e.font}))};
}
function equivalent(current,previous){
 assert.equal(current.extents.length,previous.extents.length,'Geometry/content element count changed');
 for(const key of ['x','y','width','height'])assert.ok(Math.abs(current.viewBox[key]-previous.viewBox[key])<=.1,'ViewBox changed');
 current.extents.forEach((e,i)=>{
  const p=previous.extents[i];assert.equal(e.tag,p.tag);assert.equal(e.text,p.text);assert.deepEqual(e.font,p.font);
  for(const key of ['x','y','width','height'])assert.ok(Math.abs(e.svgBox[key]-p.svgBox[key])<=.1,'Canonical geometry changed at '+i+'/'+key);
 });
}
async function preservation(doc){
 const current=await primary(),id=current.canvas.id;
 const versions=(await action('canvas.versions.inspect',{id,includeSource:true,version:1})).result;
 assert.equal(current.canvas.content,doc.content);assert.equal(versions.source.content,doc.content);
 return {id,sessionId:current.sessionId,content:current.canvas.content,revision:current.canvas.revision,
  selectedVersion:current.view.selectedVersion,resourceRevision:current.view.resourceRevision,versions,
  sourceSpans:await page.locator('[data-canvas-view="primary"] [data-canvas-source-start]').evaluateAll(elements=>elements.map(e=>({
   start:e.dataset.canvasSourceStart,end:e.dataset.canvasSourceEnd,literal:e.dataset.canvasLiteral||null,text:e.textContent})))};
}
async function observe(doc,cell,tag='candidate'){
 receipt.activeCheck={doc:doc.name,cell,tag};save();
 await settled(doc.fences);
 assert.equal((await primary()).view.renderer,'builtin.canvas.markdown');
 assert.equal(await page.locator('#workspace-canvas').getAttribute('data-focused'),String(cell.focused));
 const stem=`${doc.name}-${cell.width}-${cell.focused?'focus':'split'}`,entry={doc:doc.name,cell,tag,sourceSHA:digest(doc.content),diagrams:[]};
 const oldFile=path.join(baseline,stem+'-baseline.geometry.json'),old=JSON.parse(readFileSync(oldFile,'utf8'));
 const diagnostic=path.join(baseline,stem+'-diagnostic-bounded.geometry.json');
 const matched=old.diagrams.some(d=>d.stageClipObserved)?JSON.parse(readFileSync(diagnostic,'utf8')):old;
 entry.baseline={sha256:digest(readFileSync(oldFile)),priorStageFailure:old.diagrams.some(d=>d.stageClipObserved),
  matchedSHA:digest(readFileSync(old.diagrams.some(d=>d.stageClipObserved)?diagnostic:oldFile))};
 for(let i=0;i<doc.fences;i++){
  const measurement=await measure(i),initial=await snapshot(i);
  const svgFile=stem+'-'+tag+'-diagram'+i+'.sanitized.svg';writeFileSync(path.join(out,svgFile),measurement.xml,{flag:'wx'});
  writeFileSync(path.join(out,stem+'-'+tag+'-diagram'+i+'.raw-geometry.json'),
   JSON.stringify({measurement:{...measurement,xml:undefined},initial:{...initial,src:undefined},svgFile,svgSHA:digest(measurement.xml)},null,2)+'\n',{flag:'wx'});
  assert.equal(initial.label,'Diagram. Scroll to explore; arrow keys scroll.');assert.equal(initial.tabIndex,0);
  assert.equal(initial.touchAction,'auto');assert.equal(initial.overflow,'auto');assert.equal(initial.overscroll,'auto');assert.equal(initial.transform,'none');
  assert.ok(initial.clientHeight<=650,'Stage exceeds page-growth bound');
  assert.ok(measurement.extents.every(e=>e.insideViewBox),'Separate INTERNAL SVG clipping failure; scrolling is not a repair');
  equivalent(canonical(measurement),canonical(matched.diagrams[i]));
  const heights=matched.diagrams[i].extents.filter(e=>e.tag==='text').map(e=>e.initialMapped.height);
  assert.equal(heights.length,measurement.renderedLabelHeights.length);
  measurement.renderedLabelHeights.forEach((height,j)=>assert.ok(height>=heights[j]*.98,'Label shrink relative to matched diagnostic baseline'));
  const reach=await nativeReach(i,measurement,stem+'-'+tag+'-diagram'+i);
  assert.ok(reach.allLabelsReachable,'Native label reachability failed');assert.ok(reach.allGeometryReachable,'Native geometry reachability failed');
  assert.equal((await snapshot(i)).src,initial.src,'Native scrolling changed SAME-render sanitized SVG');
  entry.diagrams.push({index:i,svgFile,svgSHA:digest(measurement.xml),canonical:canonical(measurement),
   labelHeights:measurement.renderedLabelHeights,initial:{...initial,src:undefined},reach});
 }
 const labels=entry.diagrams.flatMap(d=>d.canonical.extents.filter(e=>e.tag==='text').map(e=>e.text));
 assert.ok(doc.sentinels.every(s=>labels.some(t=>t.includes(s))),'Missing source sentinels');
 entry.noHorizontalOverflow=await page.evaluate(()=>{
  const p=document.querySelector('[data-canvas-view="primary"] .a-canvas-preview'),c=document.querySelector('#workspace-canvas');
  return document.documentElement.scrollWidth<=innerWidth+1&&p.scrollWidth<=p.clientWidth+1&&c.scrollWidth<=c.clientWidth+1;
 });
 if(cell.width===390&&cell.focused)assert.ok(entry.noHorizontalOverflow,'390px focus page/Canvas horizontal overflow');
 entry.preservation=await preservation(doc);entry.passed=true;receipt.activeCheck=null;
 writeFileSync(path.join(out,stem+'-'+tag+'.geometry.json'),JSON.stringify(entry,null,2)+'\n',{flag:'wx'});return entry;
}
async function interactions(){
 const doc=docs[0];await page.setViewportSize({width:390,height:844});await focus(true);
 await action('canvas.show',{kind:'markdown',title:'Native interactions',content:doc.content});await settled(1);
 const stage=page.locator(stageSelector).first(),before=await primary(),pan=before.canvas.view||{};
 await stage.evaluate(e=>e.scrollIntoView({block:'start'}));await stage.focus();await stage.press('Home');await wheel(0,-50000);
 await stage.press('ArrowDown');
 await page.waitForFunction(selector=>document.querySelector(selector).scrollTop>0,stageSelector,{timeout:timeout(3000)});
 await tick();const arrow=await snapshot(0);
 assert.ok(arrow.focusVisible&&arrow.outline.style!=='none'&&parseFloat(arrow.outline.width)>0,'Existing keyboard focus indication is not visible');
 await page.screenshot({path:path.join(out,'native-keyboard-focus.png')});
 const wheelBefore=arrow.top;await wheel(0,180);
 await page.waitForFunction(({selector,top})=>document.querySelector(selector).scrollTop>top,{selector:stageSelector,top:wheelBefore},{timeout:timeout(3000)});
 const wheelAfter=await snapshot(0);
 assert.deepEqual((await primary()).canvas.view||{},pan,'Embedded native keys/wheel mutated Canvas pan/zoom');
 await stage.press('Home');await wheel(0,-50000);await stage.evaluate(e=>e.scrollIntoView({block:'start'}));
 assert.ok(await page.evaluate(()=>matchMedia('(pointer: coarse)').matches&&matchMedia('(hover: none)').matches),'Touch trial is not coarse/no-hover');
 const r=await stage.boundingBox(),preview=await page.locator('[data-canvas-view="primary"] .a-canvas-preview').boundingBox();
 const top=Math.max(0,r.y,preview.y)+12,bottom=Math.min(844,r.y+r.height,preview.y+preview.height)-12,x=r.x+r.width/2;
 assert.ok(bottom-top>80,'No visible native touch gesture span');
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y:bottom}]});
 for(let step=1;step<=6;step++){await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:bottom-(bottom-top)*step/6}]});await tick()}
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await page.waitForFunction(selector=>document.querySelector(selector).scrollTop>0,stageSelector,{timeout:timeout(4000)});
 const touchAfter=await snapshot(0);assert.deepEqual((await primary()).canvas.view||{},pan);
 await wheel(0,50000);
 await page.waitForFunction(selector=>{const e=document.querySelector(selector);return e.scrollTop>=e.scrollHeight-e.clientHeight-1},stageSelector,{timeout:timeout(3000)});
 await stage.evaluate(e=>e.scrollIntoView({block:'start'}));
 const parentBefore=await stage.evaluate(e=>e.closest('.a-canvas-preview').scrollTop);await wheel(0,300);
 await page.waitForFunction(({selector,top})=>document.querySelector(selector).closest('.a-canvas-preview').scrollTop>top,
  {selector:stageSelector,top:parentBefore},{timeout:timeout(3000)});
 const parentAfter=await stage.evaluate(e=>e.closest('.a-canvas-preview').scrollTop);
 await stage.press('Home');await wheel(0,-50000);await stage.evaluate(e=>e.scrollIntoView({block:'end'}));
 const topParentBefore=await stage.evaluate(e=>e.closest('.a-canvas-preview').scrollTop);assert.ok(topParentBefore>0);
 await wheel(0,-300);
 await page.waitForFunction(({selector,top})=>document.querySelector(selector).closest('.a-canvas-preview').scrollTop<top,
  {selector:stageSelector,top:topParentBefore},{timeout:timeout(3000)});
 receipt.native={arrowTop:arrow.top,wheelBefore,wheelAfter:wheelAfter.top,touchAfter:touchAfter.top,parentBefore,parentAfter,
  topParentBefore,topParentAfter:await stage.evaluate(e=>e.closest('.a-canvas-preview').scrollTop),focus:arrow.outline,
  canvasViewUnchanged:true,touchLimit:'Chromium emulated native touch, not physical reporter device',passed:true};save();
}
async function copySource(doc){
 // The released viewer has a full-source toolbar action. Per-block controls
 // belong to the separate, unqualified #298 candidate, not this #417 baseline.
 const fenceCount=[...doc.content.matchAll(/```(?:mermaid|dot)\n([\s\S]*?)\n```/g)].length;
 await page.getByRole('button',{name:'Copy canvas source',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.renderReports?.clipboard?.status==='ready',null,{timeout:timeout(4000)});
 await page.waitForFunction(async expected=>(await navigator.clipboard.readText())===expected,doc.content,{timeout:timeout(4000)});
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),doc.content);
 receipt.sourceCopies??=[];receipt.sourceCopies.push({doc:doc.name,fenceCount,exactFullSource:true,
  perBlockCopyControls:'Not present in the 0.20.81 baseline; #298 remains separate and unqualified'});save();
 return fenceCount;
}
async function copyAndReference(doc){
 const count=await copySource(doc);
 const passage='Owned synthetic source; not reporter Markdown.',offset=doc.content.indexOf(passage);assert.ok(offset>=0);
 const paragraph=page.locator('[data-canvas-view="primary"] p').filter({hasText:passage});
 await paragraph.evaluate(e=>{e.closest('.a-canvas-preview').focus();const range=document.createRange();range.selectNodeContents(e);
  const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'))});
 const previous=receipt.actions.length;await page.getByRole('button',{name:'Reference in chat',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().view.draft.includes('amplifier-canvas://artifact/'),null,{timeout:timeout(4000)});
 const request=receipt.actions.slice(previous).find(a=>a.action==='canvas.views.command'&&a.args.action==='canvas.reference');
 assert.ok(request);assert.equal(request.args.args.excerpt,passage);assert.equal(request.args.args.version,1);
 assert.deepEqual(request.args.args.spans,[{start:[...doc.content.slice(0,offset)].length,end:[...doc.content.slice(0,offset+passage.length)].length}]);
 const versions=(await action('canvas.versions.inspect',{id:(await primary()).canvas.id,version:1})).result;
 assert.ok((await primary()).presentation.draft.includes(versions.reference),'Saved exact-version link lost');
 receipt.copyReference={fenceCount:count,exactFullSource:true,reference:versions.reference,spans:request.args.args.spans,passed:true};save();
}
async function lifecycle(){
 const doc=docs[0];await page.setViewportSize({width:1280,height:900});await focus(false);
 await action('canvas.show',{kind:'markdown',title:'Synthetic lifecycle tall-chain',content:doc.content});await settled(1);
 await copyAndReference(doc);const original=await preservation(doc),draft=(await primary()).presentation.draft;
 let previous=await primary(),svg=(await snapshot(0)).src,geometry=canonical(await measure(0));
 for(const step of ['focus','resize','collapse-reopen','reload']){
  const reportAt=receipt.reports.length;
  if(step==='focus')await focus(true);
  if(step==='resize')await page.setViewportSize({width:390,height:844});
  if(step==='collapse-reopen'){await action('canvas.visibility',{open:false});await action('canvas.visibility',{open:true})}
  if(step==='reload')await page.reload({waitUntil:'domcontentloaded',timeout:timeout(15000)});
  await settled(1);
  const current=await primary(),now=await snapshot(0),measured=canonical(await measure(0));
  assert.deepEqual(await preservation(doc),original,'Lifecycle changed source/version/resource/saved links/reference spans');
  assert.equal(current.presentation.draft,draft);equivalent(measured,geometry);
  const fresh=receipt.reports.slice(reportAt),generationChanged=current.view.generation!==previous.view.generation,svgChanged=now.src!==svg;
  if(generationChanged||svgChanged||step==='reload'){
   assert.ok(fresh.some(r=>r.args?.args?.status==='pending'),'Fresh generation/render lacks actual pending report');
   assert.ok(fresh.some(r=>r.args?.args?.status==='ready'),'Fresh generation/render lacks actual ready report');
  }
  // Resize may only reflow this same img: do not invent a fresh renderer pass.
  const cell={...(await page.viewportSize()),focused:!!current.presentation.canvasFocused};
  const observation=await observe(doc,cell,'lifecycle-'+step);
  receipt.lifecycle.push({step,generationBefore:previous.view.generation,generationAfter:current.view.generation,
   svgBefore:digest(svg),svgAfter:digest(now.src),sameRender:now.src===svg,freshReports:fresh,
   canonicalUnchanged:true,sourcePreserved:true,numericalPass:observation.passed});
  previous=current;svg=now.src;geometry=measured;save();
 }
}
async function standalone(){
 await page.setViewportSize({width:1280,height:900});await focus(true);
 const small='flowchart TD\n  A[SMALL_TOP] --> B[SMALL_BOTTOM]';
 await action('canvas.show',{kind:'mermaid',title:'Standalone small Mermaid',content:small});await settled(1,false);
 const stage=page.locator('[data-canvas-view="primary"] .a-diagram:not(.embedded) .a-diagram-stage'),img=stage.locator('img');
 const transform=()=>img.evaluate(e=>getComputedStyle(e).transform),view=async()=>(await primary()).canvas.view||{};
 const initial=await transform();assert.equal(await stage.evaluate(e=>getComputedStyle(e).touchAction),'none');
 const r=await stage.boundingBox();await page.mouse.move(r.x+r.width/2,r.y+r.height/2);await page.mouse.down();
 await page.mouse.move(r.x+r.width/2+45,r.y+r.height/2+20);await page.mouse.up();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.view?.panX===45,null,{timeout:timeout(4000)});
 assert.notEqual(await transform(),initial);receipt.standalone.push({behavior:'pointer drag',view:await view(),transform:await transform()});
 const dragTransform=await transform();await page.getByRole('button',{name:'Zoom in',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.view?.zoom===1.2,null,{timeout:timeout(4000)});
 assert.notEqual(await transform(),dragTransform);receipt.standalone.push({behavior:'Zoom in',view:await view(),transform:await transform()});
 await page.getByRole('button',{name:'Zoom out',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.view?.zoom===1,null,{timeout:timeout(4000)});
 await page.getByRole('button',{name:'Fit',exact:true}).click();
 await page.waitForFunction(()=>{const v=window.amplifier.getState().canvas.view;return v.zoom===1&&v.panX===0&&v.panY===0},null,{timeout:timeout(4000)});
 receipt.standalone.push({behavior:'Fit',view:await view(),transform:await transform()});
 assert.ok(await stage.evaluate(e=>{const r=e.getBoundingClientRect(),i=e.querySelector('img').getBoundingClientRect();
  return i.x>=r.x-1&&i.y>=r.y-1&&i.right<=r.right+1&&i.bottom<=r.bottom+1}),'Ordinary small Mermaid bounds regressed');
 await stage.focus();await stage.press('ArrowDown');
 await page.waitForFunction(()=>window.amplifier.getState().canvas.view?.panY===30,null,{timeout:timeout(4000)});
 receipt.standalone.push({behavior:'arrow pan / touch-action:none',view:await view(),transform:await transform()});
 await action('canvas.show',{kind:'dot',title:'Standalone DOT',
  content:'digraph G { a [label="DOT_LEFT_SENTINEL"]; b [label="DOT_RIGHT_SENTINEL"]; a -> b; }'});await settled(1,false);
 const oldSVG=await img.getAttribute('src'),reportAt=receipt.reports.length;
 await page.getByRole('combobox',{name:'Graph layout',exact:true}).selectOption('neato');
 await page.waitForFunction(()=>window.amplifier.getState().canvas.view?.engine==='neato',null,{timeout:timeout(4000)});await settled(1,false);
 await page.waitForFunction(old=>document.querySelector('[data-canvas-view="primary"] .a-diagram-stage img')?.src!==old,oldSVG,{timeout:timeout(15000)});
 const fresh=receipt.reports.slice(reportAt);assert.ok(fresh.some(r=>r.args.args.status==='pending')&&fresh.some(r=>r.args.args.status==='ready'));
 await page.getByText(/^Node details ·/).click();
 await page.getByRole('combobox',{name:'Inspect graph node',exact:true}).selectOption('a');
 await page.locator('.a-node-inspector dl').waitFor();assert.match(await page.locator('.a-node-inspector dl').innerText(),/DOT_LEFT_SENTINEL/);
 receipt.standalone.push({behavior:'Graphviz layout/node inspection',engine:'neato',node:'a',freshReports:fresh,svgChanged:true});
 await page.screenshot({path:path.join(out,'standalone-dot.png')});save();
}
async function run(){
 const require=createRequire(path.join(source,'frontend/package.json')),{chromium,expect}=require('@playwright/test');
 const childEnv=Object.fromEntries(Object.entries(process.env).filter(([k])=>!k.startsWith('AMPLIFIER')&&!k.endsWith('_API_KEY')&&!k.endsWith('_TOKEN')&&!k.endsWith('_SECRET')));
 childEnv.PYTHONPATH='';childEnv.PYTHONDONTWRITEBYTECODE='1';childEnv.TMPDIR=tmp;
 server=spawn(python,['-I','-B','-u',path.join(out,'server.py'),JSON.stringify(flags)],{cwd:out,env:childEnv,stdio:['ignore','pipe','pipe']});
 receipt.serverPID=server.pid;server.on('exit',(code,signal)=>{serverExit={code,signal};save()});
 server.stderr.on('data',c=>stderr=(stderr+c.toString()).slice(-16000));
 ready=await new Promise((resolve,reject)=>{
  const lines=createInterface({input:server.stdout}),timer=setTimeout(()=>{lines.close();reject(Error('Fixture startup timeout '+clean(stderr)))},timeout(30000));
  const fail=e=>{clearTimeout(timer);lines.close();reject(e)};server.once('error',fail);server.once('exit',(code,signal)=>fail(Error('Fixture exit '+code+'/'+signal+' '+clean(stderr))));
  lines.on('line',line=>{try{const value=JSON.parse(line);if(value.cookie){secret=value.cookie.value;clearTimeout(timer);lines.close();resolve(value)}}catch{}});
 });
 assert.equal(ready.runtime,'SyntheticNoInferenceRuntime');assert.equal(ready.provenance.version,flags.version);
 receipt.server={...ready,cookie:'[fixture-cookie-redacted]'};save();
 const browserHome=path.join(out,'browser-home');mkdirSync(browserHome);
 browser=await chromium.launch({executablePath:flags.browser,headless:true,args:['--single-process','--no-zygote','--disable-gpu'],
  env:{...childEnv,HOME:browserHome,XDG_CONFIG_HOME:browserHome,XDG_CACHE_HOME:browserHome},timeout:timeout(15000)});
 receipt.chromium=browser.version();
 context=await browser.newContext({viewport:{width:1280,height:900},hasTouch:true,serviceWorkers:'block'});receipt.contextCount++;
 assert.equal((await fetch(ready.origin+'/api/state',{signal:AbortSignal.timeout(timeout(3000))})).status,401);
 await context.addCookies([ready.cookie]);await context.grantPermissions(['clipboard-read','clipboard-write'],{origin:ready.origin});
 page=await context.newPage();receipt.pageCount++;cdp=await context.newCDPSession(page);
 page.setDefaultTimeout(8000);await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 page.on('pageerror',e=>{receipt.pageErrors.push(clean(e.message));save()});
 page.on('response',r=>{if(new URL(r.url()).pathname==='/api/events')receipt.streams.push({transport:'sse',status:r.status(),contentType:r.headers()['content-type']})});
 page.on('websocket',socket=>{if(new URL(socket.url()).pathname==='/api/events/ws')socket.on('framereceived',frame=>{try{if(JSON.parse(String(frame.payload)).event==='state')receipt.streams.push({transport:'websocket',status:101})}catch{}})});
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==ready.origin){receipt.blocked.push({url:request.url()});save();return route.abort()}
  if(request.method()==='POST'&&url.pathname==='/api/actions'){
   const body=request.postDataJSON();receipt.actions.push(body);
   if(body.action==='canvas.views.command'&&body.args?.action==='canvas.report')receipt.reports.push(body);
   if(/^(conversation\.(send|retry)|worker\.|call\.|session\.(warm|resume)|runtime\.restart)/.test(body.action)){
    receipt.blocked.push({action:body.action});save();return route.abort();
   }
  }
  return route.continue();
 });
 assert.equal((await page.goto(ready.origin,{waitUntil:'domcontentloaded',timeout:timeout(15000)})).status(),200);
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).waitFor();
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id,null,{timeout:timeout(15000)});
 await page.waitForFunction(async()=>{const r=await fetch('/api/state');return r.status===200},null,{timeout:timeout(5000)});
 await expect.poll(()=>receipt.streams.some(r=>r.transport==='websocket'&&r.status===101||r.status===200&&r.contentType?.includes('text/event-stream')),{timeout:timeout(10000)}).toBe(true);
 receipt.realCookieHTTPAndStream=true;
 receipt.servedCSS=[];
 for(const url of await page.evaluate(()=>[...document.querySelectorAll('link[rel="stylesheet"]')].map(e=>e.href))){
  assert.equal(new URL(url).origin,ready.origin);const response=await context.request.get(url);assert.ok(response.ok());
  const key=new URL(url).pathname,hash=digest(await response.body());assert.equal(hash,ready.provenance.static[key]);
  receipt.servedCSS.push({path:key,sha256:hash});
 }
 assert.ok(receipt.servedCSS.length);
 clipboardBefore=await page.evaluate(()=>navigator.clipboard.readText());receipt.clipboardBeforeSHA=digest(clipboardBefore);
 await action('view.update',{patch:{canvasWidth:480,canvasControlsPinned:true,canvasControlsExpanded:true,navPinned:false,navExpanded:false}});
 for(const doc of docs){
  await action('canvas.show',{kind:'markdown',title:'Synthetic '+doc.name,content:doc.content});
  let original;
  for(const cell of cells){
   await page.setViewportSize({width:cell.width,height:cell.height});await focus(cell.focused);
   const observation=await observe(doc,cell);original??=observation.preservation;
   assert.deepEqual(observation.preservation,original,'Focus/resize changed source/version/link/spans');
   if(cell===cells[0])await copySource(doc);
   receipt.cells.push(observation);save();
  }
 }
 assert.equal(receipt.cells.length,16);assert.equal(receipt.cells.filter(c=>c.baseline.priorStageFailure).length,9);
 receipt.recoveredPriorFailures=9;receipt.retainedPriorPasses=7;
 receipt.activeCheck='native interactions';save();await interactions();
 receipt.activeCheck='lifecycle';save();await lifecycle();
 receipt.activeCheck='standalone';save();await standalone();receipt.standalonePassed=true;receipt.activeCheck=null;
 assert.deepEqual(receipt.pageErrors,[]);assert.deepEqual(receipt.blocked,[]);
 receipt.status='SYNTHETIC_NUMERICAL_CHECKS_PASSED_PIXEL_REVIEW_PENDING';save();
}
try{
 deadline=setTimeout(()=>{receipt.deadlineExceeded=true;receipt.status='CANT_CHECK_DEADLINE';save();server?.kill('SIGTERM');browser?.close().catch(()=>{})},budget*1000);
 await run();
}catch(e){
 receipt.status=receipt.realCookieHTTPAndStream?'FAILED_SYNTHETIC_CHECK':'CANT_CHECK_PREREQUISITE';
 receipt.failure=clean(e.stack||e);process.exitCode=1;save();
}finally{
 clearTimeout(deadline);
 const bounded=async(promise,label)=>{
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(label+' cleanup timeout')),5000)})])}
  finally{clearTimeout(timer)}
 };
 if(page&&!page.isClosed()&&clipboardBefore!==undefined){
  try{await bounded(page.evaluate(text=>navigator.clipboard.writeText(text),clipboardBefore),'Clipboard write');
   receipt.clipboardRestored=(await bounded(page.evaluate(()=>navigator.clipboard.readText()),'Clipboard read'))===clipboardBefore;
   if(!receipt.clipboardRestored)throw Error('Clipboard restoration mismatch');
  }catch(e){receipt.clipboardRestoreFailure=clean(e);receipt.status='CANT_CHECK_CLIPBOARD_PRESERVATION';process.exitCode=1}
 }
 try{if(context)await bounded(context.tracing.stop({path:path.join(out,'trace.zip')}),'Trace')}catch(e){receipt.traceFailure=clean(e)}
 try{if(browser){await bounded(browser.close(),'Browser');receipt.browserClosed=true}}
 catch(e){receipt.browserCloseFailure=clean(e);receipt.status='CANT_CHECK_BROWSER_CLEANUP';process.exitCode=1}
 if(server&&!serverExit){
  server.kill('SIGTERM');
  await new Promise(resolve=>{const timer=setTimeout(()=>{server.kill('SIGKILL');resolve()},5000);
   server.once('exit',()=>{clearTimeout(timer);resolve()})});
 }
 receipt.serverExit=serverExit||null;
 if(existsSync(path.join(out,'server-receipt.json'))){
  const final=JSON.parse(readFileSync(path.join(out,'server-receipt.json'),'utf8'));receipt.serverCleanup=final.cleanup;
  receipt.runtimeCalls=final.runtimeCalls;receipt.serverRefusedActions=final.refusedActions;receipt.serverAudit=final.audit;
  if(final.runtimeCalls.length||final.refusedActions.length||final.audit.length||!final.cleanup?.serviceClosed||
   !final.cleanup?.runtimeClosed||!final.cleanup?.socketClosed){receipt.status='CANT_CHECK_FIXTURE_OR_CLEANUP';process.exitCode=1}
 }
 receipt.elapsedSeconds=(Date.now()-started)/1000;receipt.unexecutedCells=16-receipt.cells.length;
 receipt.unfinishedGroups={native:!receipt.native?.passed,lifecycleSteps:4-receipt.lifecycle.length,standalone:!receipt.standalonePassed};save();
 console.log(JSON.stringify({status:receipt.status,cells:receipt.cells.length,unexecutedCells:receipt.unexecutedCells,
  contexts:receipt.contextCount,pages:receipt.pageCount,proof:flags.proof,pixelJudgment:receipt.pixelJudgment,originalCase:receipt.originalCase}));
}
