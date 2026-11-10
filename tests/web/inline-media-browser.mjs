import {showFixtureConversation} from './browser-chat-pages.mjs';
// Authored acceptance, not qualification. Only synthetic/public sample bytes.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {hostname,homedir} from 'node:os';
import {chromium,expect,request} from '@playwright/test';
import assert from 'node:assert/strict';
import {setTimeout as delay} from 'node:timers/promises';
import './composer-test-helpers.mjs';
import {launchNativeBrowser,nativeKey,nativeControl,nativeTab,nativeInput,zoomMetrics} from './inline-media-native-input.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const evidenceDir=process.env.INLINE_MEDIA_EVIDENCE_DIR;
if(!evidenceDir)throw Error('Supply INLINE_MEDIA_EVIDENCE_DIR owned by this verification task');
const python=process.env.PYTHON||process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python');
const mode=process.env.INLINE_MEDIA_MODE||'source';
const headed=process.env.INLINE_MEDIA_HEADED==='1';
if(process.env.INLINE_MEDIA_HEADED&&!['0','1'].includes(process.env.INLINE_MEDIA_HEADED))
 throw Error('INLINE_MEDIA_HEADED must be 0 or 1');
const selectedPhases=process.env.INLINE_MEDIA_PHASES?new Set(JSON.parse(process.env.INLINE_MEDIA_PHASES)):null;
const guestNative=process.env.INLINE_MEDIA_NATIVE_INPUT==='x11';
if(process.env.INLINE_MEDIA_NATIVE_INPUT&&!guestNative)throw Error('Unsupported native input route');
if(guestNative&&(!headed||!selectedPhases||[...selectedPhases].some(name=>
 !['gif-live-attach','mp4-native','mp4-native-controls','visibility','keyboard-200-percent'].includes(name))))
 throw Error('Guest native input requires headed, explicitly selected media interaction phases');
if(selectedPhases&&![...selectedPhases].every(name=>typeof name==='string'))
 throw Error('INLINE_MEDIA_PHASES must contain phase names');
const browserArgs=process.env.CHROMIUM_ARGS?JSON.parse(process.env.CHROMIUM_ARGS):
 process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[];
if(!Array.isArray(browserArgs)||browserArgs.some(arg=>typeof arg!=='string'||arg.startsWith('--disable-web-security')))
 throw Error('CHROMIUM_ARGS must be an array of existing executor arguments, without disabling web security');
const receipts=[],contexts=new Set(),pages=new Set(),fixtureRows=[],pageErrors=[];
const blockedApplicationCleanup=new WeakSet();
const physicalNames=['mp4-native-controls','visibility','keyboard-200-percent'];
const scenarioPhases=['gif-live-attach','metadata-malformed-retry','gif-resume','gif-report-cadence','report-readback-negative-control',
 'mp4-native','mp4-native-controls','mp4-decode-error','mp4-explicit-cleanup','partial-disposal','large-native',
 'reduced-motion','visibility','offscreen','decoder-error','unsupported-decoder','decoder-late-cleanup',
 'preservation','worker-history-descriptor','gif-canvas','gif-canvas-errors','endpoints','layout','keyboard-200-percent'];
let browser,nativeBrowser,fixture,boot,sharedStorage,sharedContext,exit,forcedKill=false;
await mkdir(resolve(evidenceDir),{recursive:true});
const privateHeaders={'X-Inline-Media-Fixture':'synthetic-control'};
const phasePath=name=>join(evidenceDir,name+'.json');
const privateStrings=[root,resolve(evidenceDir),homedir(),hostname(),python,
 process.env.CHROMIUM_EXECUTABLE,process.env.INLINE_MEDIA_GIF,process.env.INLINE_MEDIA_MP4,
 process.env.INLINE_MEDIA_LARGE_GIF].filter(value=>typeof value==='string'&&value.length>3)
 .sort((a,b)=>b.length-a.length);
function publicEvidence(value){
 if(typeof value==='string'){
  for(const item of privateStrings)value=value.replaceAll(item,'<private>');
  return value.replace(/(?:\/(?:home|Users)\/[^\s"'<>]+|[A-Za-z]:\\Users\\[^\s"'<>]+)/g,'<private-path>');
 }
 if(Array.isArray(value))return value.map(publicEvidence);
 if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,value])=>[publicEvidence(key),publicEvidence(value)]));
 return value;
}
const writeEvidence=(path,value)=>writeFile(path,JSON.stringify(publicEvidence(value),null,2));
async function record(name,status,evidence={},required=true){
 const row={phase:name,status,required,evidence:publicEvidence(evidence)};
 receipts.push(row);await writeEvidence(phasePath(name),row);
 console.log(JSON.stringify({phase:name,status,required}));
 return row;
}
class CantCheck extends Error{}
const errorEvidence=error=>({name:error.name||'Error',reason:String(error.message||error),
 stack:typeof error.stack==='string'?error.stack:null,
 ...(error.fixtureStderr?{fixtureStderr:error.fixtureStderr}:{}),
 ...(error.cleanupErrors?{cleanupErrors:error.cleanupErrors}:{})});
async function phase(name,work,{required=true}={}){
 if(selectedPhases&&!selectedPhases.has(name)&&!['shared-setup','cleanup'].includes(name))
  return record(name,'NOT_RUN',{reason:'Explicit partial verification selection'},false);
 console.log(JSON.stringify({phase:name,status:'RUNNING',required}));
 try{return await record(name,'PASS',await work(),required)}
 catch(error){
  const snapshots=[];
  for(const [index,page] of [...pages].filter(page=>!page.isClosed()).slice(-5).entries()){
   const filename=`${name}-terminal-${index}.png`;
   try{await page.screenshot({path:join(evidenceDir,filename)});snapshots.push({filename,saved:true})}
   catch(capture){snapshots.push({filename,saved:false,reason:String(capture.message)})}
  }
  return await record(name,error instanceof CantCheck?(required?'CANT_CHECK':'NOT_QUALIFIED'):'FAIL',
   {...errorEvidence(error),snapshots},required);
 }
}
async function receiptDeadline(label,work){
 // Playwright action timeouts do not bound promises returned by evaluate.
 let timer;
 const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(`${label} did not settle within 30 seconds`)),30000)});
 try{return await Promise.race([work(),timeout])}
 finally{clearTimeout(timer)}
}
async function action(page,name,args={}){
 return receiptDeadline(`App action ${name}`,()=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]));
}
function gifCanvasCleanupAllowed(clickAttempted,primaryFailed,clickSettled){
 return !clickAttempted||(!primaryFailed&&clickSettled===true);
}
const canvasPixels=figure=>figure.locator('canvas').evaluate(node=>{
 const data=node.getContext('2d').getImageData(0,0,node.width,node.height).data;
 // Small synthetic pixels are exact; large proof is sampled, not a memory copy.
 const points=[0,Math.floor(node.width/2),node.width*node.height-1];
 return {width:node.width,height:node.height,pixels:data.length<=128?Array.from(data):points.map(p=>Array.from(data.slice(p*4,p*4+4)))};
});

// Observe REAL native decoder work and application draws. Do not substitute
// decoded frames or clocks. Test-only gates delay delivery for late cleanup.
function installProbe({unsupported=false,corrupt=false,hold=false,captureTinyPixels=false}={}){
 const fetch=window.fetch,posts=window.__mediaReportPosts=[],actions=window.__actionPosts=[];
 window.fetch=function(input,options){
  if(options?.method==='POST'&&new URL(typeof input==='string'?input:input.url,location.href).pathname==='/api/actions'){
   const body=typeof options.body==='string'?JSON.parse(options.body):null;
   if(body)actions.push({action:body.action,id:body.id});
   if(body?.action==='outputs.mediaReport')posts.push({at:performance.now(),...body.args});
  }
  return fetch.call(this,input,options);
 };
 // Instrument the actual application-owned native callbacks/methods before
 // React mounts. Browser auto-pause/DOM removal cannot satisfy these counters.
 const native=window.__nativeMediaProbe={frames:[],nodes:new WeakMap()};
 const row=node=>{
  if(!native.nodes.has(node))native.nodes.set(node,{pause:0,cancel:[],cancelledPending:0,removeSrc:0,load:0,requests:0,presented:0,pending:null});
  return native.nodes.get(node);
 };
 const proto=HTMLVideoElement.prototype;
 const requestFrame=proto.requestVideoFrameCallback,cancelFrame=proto.cancelVideoFrameCallback;
 if(requestFrame)proto.requestVideoFrameCallback=function(callback){
  const node=this,owned=row(node);owned.requests++;
  const id=requestFrame.call(node,(at,metadata)=>{
   owned.pending=null;owned.presented++;
   const canvas=document.createElement('canvas');canvas.width=16;canvas.height=12;
   const context=canvas.getContext('2d');context.drawImage(node,0,0,16,12);
   native.frames.push({id:node.closest('[data-media-id]')?.dataset.mediaId,
    presentedFrames:owned.presented,nativePresentedFrames:metadata.presentedFrames,
    mediaTime:metadata.mediaTime,pixels:Array.from(context.getImageData(0,0,16,12).data),at});
   callback(at,metadata);
  });
  owned.pending=id;return id;
 };
 if(cancelFrame)proto.cancelVideoFrameCallback=function(id){
  const owned=row(this);owned.cancel.push(id);
  if(owned.pending===id){owned.cancelledPending++;owned.pending=null}
  return cancelFrame.call(this,id);
 };
 const pause=proto.pause,load=proto.load,remove=Element.prototype.removeAttribute;
 proto.pause=function(){row(this).pause++;return pause.call(this)};
 proto.load=function(){row(this).load++;return load.call(this)};
 Element.prototype.removeAttribute=function(name){
  if(this instanceof HTMLVideoElement&&name==='src')row(this).removeSrc++;
  return remove.call(this,name);
 };
 const Native=globalThis.ImageDecoder;
 const probe=window.__mediaProbe={decoders:0,frames:0,active:0,peakActive:0,peakFrames:0,
  draws:[],decoded:[],created:0,closed:0,frameClosed:0,held:[],hold,perDecoder:{}};
 if(unsupported){Object.defineProperty(globalThis,'ImageDecoder',{value:undefined});return}
 if(!Native)return;
 const frames=new WeakMap();
 globalThis.ImageDecoder=class extends Native{
  constructor(args){super(args);this.probeId=++probe.created;probe.decoders++;probe.perDecoder[this.probeId]={live:0,peak:0}}
  async decode(args){
   probe.peakActive=Math.max(probe.peakActive,++probe.active);
   try{
    if(corrupt)throw new Error('Synthetic decoder failure');
    const result=await super.decode(args),frame=result.image,owner=probe.perDecoder[this.probeId];
    frames.set(frame,{index:args.frameIndex,decoderId:this.probeId});
    probe.frames++;probe.peakFrames=Math.max(probe.peakFrames,probe.frames);
    owner.peak=Math.max(owner.peak,++owner.live);
    const close=frame.close.bind(frame);let closed=false;
    frame.close=()=>{if(!closed){closed=true;probe.frames--;owner.live--;probe.frameClosed++}return close()};
    probe.decoded.push({index:args.frameIndex,decoderId:this.probeId,timestamp:frame.timestamp,duration:frame.duration});
    if(probe.hold)await new Promise(done=>probe.held.push(done));
    return result;
   }finally{probe.active--}
  }
  close(){if(!this.probeClosed){this.probeClosed=true;probe.decoders--;probe.closed++;probe.perDecoder[this.probeId].closed=true}return super.close()}
 };
 const draw=CanvasRenderingContext2D.prototype.drawImage;
 CanvasRenderingContext2D.prototype.drawImage=function(frame,...args){
  const result=draw.call(this,frame,...args),info=frames.get(frame);
  if(info)probe.draws.push({...info,id:this.canvas.closest('[data-media-id]')?.dataset.mediaId,
   viewId:this.canvas.closest('[data-canvas-view]')?.dataset.canvasView||'inline',
   at:performance.now(),timestamp:frame.timestamp,duration:frame.duration,
   // Capture the tiny disposal fixture in the same actual draw as its index.
   // Later browser round trips may already observe a subsequent playing frame.
   ...(captureTinyPixels&&this.canvas.width*this.canvas.height<=32?
    {pixels:Array.from(this.getImageData(0,0,this.canvas.width,this.canvas.height).data)}:{})});
  return result;
 };
}
const probeState=page=>page.evaluate(()=>{
 const {held,...data}=window.__mediaProbe;return {...data,heldCount:held.length};
});
const draws=(page,id)=>page.evaluate(id=>window.__mediaProbe.draws.filter(row=>row.id===id),id);
const nativeFrames=(page,id)=>page.evaluate(id=>window.__nativeMediaProbe.frames.filter(row=>row.id===id),id);
const dispatchedReports=(page,id)=>page.evaluate(id=>window.__mediaReportPosts.filter(row=>row.id===id),id);
const retainedVideoState=page=>page.evaluate(()=>{
 const node=window.__retainedNativeVideo,owned=window.__nativeMediaProbe.nodes.get(node);
 return {connected:node.isConnected,hasSrc:node.hasAttribute('src'),paused:node.paused,...owned,cancel:[...owned.cancel]};
});
// This measures an interval during which draws must remain stopped. A paused
// or background page may have no animation frames at all; waiting for its next
// frame would deadlock the very pause/visibility behavior being checked.
const clockWait=async(page,milliseconds)=>{
 const before=await page.evaluate(()=>performance.now());
 await delay(milliseconds);
 assert.ok(await page.evaluate(before=>performance.now()-before,before)>=milliseconds-1);
};
async function control(page,path,data){
 const response=await page.request.post(boot.url+'/api/fixture/'+path,{headers:privateHeaders,data});
 assert.equal(response.status(),200,await response.text());return response.json();
}
async function attach(page,name,instance=0){
 const result=await control(page,'media',{name,instance});
 assert.equal(result.accepted,true);
 return result.result;
}
async function newClient(options={}){
 // Distinct pages have distinct host client identities. One browser context
 // preserves that discriminator without unsupported single-process multi-contexts.
 if(!sharedContext){
  sharedContext=nativeBrowser?.context||await browser.newContext({baseURL:boot.url,viewport:{width:1280,height:950},storageState:sharedStorage});
  // Library-mode Playwright otherwise waits forever for actions/events. Keep
  // failures local so phase evidence and cleanup survive a missing control.
  await sharedContext.tracing.start({screenshots:true,snapshots:true});
  sharedContext.setDefaultTimeout(30000);
  sharedContext.setDefaultNavigationTimeout(30000);
 }
 const context=sharedContext;
 contexts.add(context);
 const page=await context.newPage();pages.add(page);
 const metadataRequests=[];
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/outputs/media')metadataRequests.push(request.url())});
 page.on('pageerror',error=>pageErrors.push(String(error.stack||error.message)));
 await page.addInitScript(installProbe,options);
 if(options.resumeClientId)await page.addInitScript(id=>sessionStorage.setItem('amplifier.clientId',id),options.resumeClientId);
 if(options.suppressReports){
  // Negative control: real pixels still advance, but the host must not acquire
  // an observation. Never create a synthetic report to make readback green.
  await page.route('**/api/actions',async route=>{
   const body=route.request().postDataJSON();
   if(body?.action==='outputs.mediaReport'){
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:true,result:{}})});
   }else await route.continue();
  });
 }
 if(options.corruptVideoId){
  await page.route('**/api/outputs/*/media?*',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname!==`/api/outputs/${options.corruptVideoId}/media`||url.searchParams.has('download'))
    return route.continue();
   // Fetch through normal auth/tuple/Range guards first. Only this client's
   // inline response is test-corrupted; original downloads remain untouched.
   const response=await route.fetch();assert.ok([200,206].includes(response.status()));
   await route.fulfill({status:200,contentType:'video/mp4',body:Buffer.from('synthetic undecodable MP4')});
  });
 }
 if(!sharedStorage){
  await page.goto(boot.url+'/login');
  await page.getByLabel('Username').fill('media-fixture');
  await page.getByLabel('Password').fill('fixture-password');
  await page.getByRole('button',{name:'Sign in',exact:true}).click();await showFixtureConversation(page);
  await expect(page.locator(`[data-message-id="${boot.messageId}"]`)).toBeVisible();
  const selectedAvailability=()=>page.evaluate(()=>{
   const state=window.amplifier.getState();return state.sessions.find(row=>row.id===state.selectedSessionId)?.linkedMediaAvailable;
  });
  await expect.poll(selectedAvailability).toBe(false);
  const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await settled();assert.equal(metadataRequests.length,0,'short empty chat does not request metadata');
  // Explicit readers still receive the original validated empty descriptor.
  const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
  const response=await page.request.get(boot.url+'/api/outputs/media?'+new URLSearchParams({sessionId:boot.sessionId,messageId:boot.messageId}),
   {headers:{'X-Amplifier-Client':clientId}});
  assert.equal(response.status(),200);assert.deepEqual((await response.json()).items,[]);
  await action(page,'session.select',{id:boot.emptyLongSessionId});
  await expect(page.locator('article.a-assistant').filter({hasText:'Empty native assistant 999.'})).toBeVisible();
  await expect.poll(selectedAvailability).toBe(false);await settled();
  assert.equal(metadataRequests.length,0,'long native empty chat does not request metadata');
  await restoreSession(page);await expect.poll(selectedAvailability).toBe(false);await settled();
  assert.equal(metadataRequests.length,0,'returning to the empty chat does not request metadata');
  sharedStorage=await context.storageState();
  await record('empty-descriptor-completed','PASS',{status:200,items:[],completed:true,explicitRead:true,
   automaticRequests:metadataRequests.length,emptyLongMessages:boot.emptyLongMessageCount});
 }else{await page.goto(boot.url);await showFixtureConversation(page)}
 await page.waitForSelector('#amp-one');
 const article=page.locator(`[data-message-id="${boot.messageId}"]`);
 await expect(article).toBeVisible();
 return {page,context,article};
}
async function unmountAndClose(client,dispatchAction=action){
 const {page,context}=client;
 await dispatchAction(page,'session.create',{title:'Synthetic cleanup conversation'});
 await expect.poll(()=>page.locator('[data-media-id]').count()).toBe(0);
 await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
 const evidence=await probeState(page);
 await page.close();assert.equal(page.isClosed(),true);pages.delete(page);
 if(context.pages().length===0){await context.tracing.stop({path:join(evidenceDir,`browser-trace-${Date.now()}.zip`)});await context.close();contexts.delete(context);sharedContext=null}
 return evidence;
}
async function restoreSession(page){
 await action(page,'session.select',{id:boot.sessionId});
 await expect(page.locator(`[data-message-id="${boot.messageId}"]`)).toBeVisible();
}
const figureFor=(page,id)=>page.locator(`[data-media-id="${id}"]`);
async function readyFigure(page,id){
 const figure=figureFor(page,id);
 // Restored reading positions can keep the assistant offscreen. Its visible
 // article owns metadata demand; an unfetched figure cannot be scrolled yet.
 if(await figure.count()===0)await page.locator(`[data-message-id="${boot.messageId}"]`).scrollIntoViewIfNeeded();
 await figure.scrollIntoViewIfNeeded();
 await expect(figure).toHaveAttribute('data-media-state','ready');
 return figure;
}

function watchReports(page,id){
 const rows=[];
 const listener=request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  const body=request.postDataJSON();
  if(body?.action==='outputs.mediaReport'&&(!id||body.args.id===id))
   rows.push({at:performance.now(),...body.args});
 };
 page.on('request',listener);
 return {rows,stop:()=>page.off('request',listener)};
}
async function readOutput(page,item){
 const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const response=await page.request.post(boot.url+'/api/actions',{headers:{'X-Amplifier-Client':clientId},
  data:{action:'outputs.read',args:{sessionId:item.sessionId,id:item.id}}});
 assert.equal(response.status(),200,await response.text());
 const body=await response.json();assert.equal(body.accepted,true);
 const result=body.result;
 for(const key of ['id','sessionId','version','sha256'])assert.equal(result[key],item[key],`Exact read tuple: ${key}`);
 return {clientId,result};
}
function requireObservation({clientId,result},{playback,renderState='ready',frames,mediaTime,since}){
 const observation=result.browserObservation;
 assert.equal(observation.status,'observed','Real reports required: pixels alone cannot qualify readback');
 assert.equal(observation.clientId,clientId);
 assert.equal(observation.evidence,'authenticated-ui-report');assert.equal(observation.modelAttestation,false);
 assert.equal(observation.renderState,renderState);assert.equal(observation.playback,playback);
 assert.equal(result.playback,playback);assert.equal(result.saved,true);assert.equal(result.attached,true);
 assert.ok(Number.isFinite(observation.serverTimestamp));
 assert.ok(observation.serverTimestamp>=since/1000&&Math.abs(Date.now()/1000-observation.serverTimestamp)<10,
  'Fresh server timestamp, not cached/synthetic evidence');
 assert.equal(observation.presentedFrames,frames);assert.equal(observation.mediaTime,mediaTime);
 assert.ok(observation.generation>0&&observation.sequence>0);
 return observation;
}
async function observeLatest(page,item,expected){
 // Wait only for REAL app transport completion. Readback is exact-client and
 // never POSTs outputs.mediaReport itself.
 await expect.poll(async()=>{
  const {result}=await readOutput(page,item),o=result.browserObservation;
  return o.status==='observed'&&o.playback===expected.playback&&o.renderState===(expected.renderState||'ready')&&
   o.presentedFrames===expected.frames&&o.mediaTime===expected.mediaTime;
 }).toBe(true);
 return requireObservation(await readOutput(page,item),expected);
}

async function workerHistoryDescriptor(primary,peer,saved){
 const {page}=primary,clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const peerClientId=await peer.page.evaluate(()=>window.amplifier.getState().client.id);
 assert.ok(boot.workerSessionId);assert.notEqual(boot.workerSessionId,boot.sessionId);
 assert.notEqual(clientId,peerClientId);
 // Ordinary known-empty rendering elides discovery. The explicit endpoint
 // still proves the original scoped worker exception below.
 let worker;
 try{
  await action(page,'session.select',{id:boot.workerSessionId});
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(boot.workerSessionId);
  const article=page.locator('article.a-assistant[data-message-id]');
  await expect(article).toHaveCount(1);await article.scrollIntoViewIfNeeded();
  await expect(article).toBeVisible();await expect(article).toContainText('Synthetic child saved response.');
  const messageId=await article.getAttribute('data-message-id');
  const response=await page.request.get(boot.url+'/api/outputs/media?'+new URLSearchParams({sessionId:boot.workerSessionId,messageId}),
   {headers:{'X-Amplifier-Client':clientId}});
  assert.equal(response.status(),200);
  const descriptor=await response.json();
  assert.deepEqual(descriptor.items,[]);assert.equal(descriptor.overflow,false);
  assert.ok(Number.isInteger(descriptor.mediaRevision));
  assert.equal(new URL(response.url()).searchParams.get('messageId'),messageId);
  assert.equal(await page.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id).sessionKind,
   boot.workerSessionId),'worker');
  await expect(article.locator('.a-inline-media [role="alert"]')).toHaveCount(0);
  await expect(article.getByRole('button',{name:'Retry media metadata',exact:true})).toHaveCount(0);
  await expect(article.locator('[data-media-id]')).toHaveCount(0);
  await article.screenshot({path:join(evidenceDir,'worker-history-article.png')});
  assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
  assert.equal(await peer.page.evaluate(()=>window.amplifier.getState().selectedSessionId),boot.sessionId);
  await expect(peer.page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent draft B');
  worker={workerSessionId:boot.workerSessionId,messageId,status:response.status(),descriptor,
   renderedAssistant:true,mediaAlerts:0,retryRows:0};
 }finally{
  // Return before the original endpoint phase unlinks the GIF. Both drafts,
  // client identities and the exact original media remain usable, even if the
  // worker assertion fails. That failure must not replace the original phases.
  await restoreSession(page);await readyFigure(page,saved.id);
 }
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent draft A');
 await expect(peer.page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent draft B');
 for(const p of [page,peer.page])assert.equal(await p.evaluate(()=>window.amplifier.getState().canvas.open),false);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().client.id),clientId);
 assert.equal(await peer.page.evaluate(()=>window.amplifier.getState().client.id),peerClientId);
 await expect.poll(async()=>{
  const {result}=await readOutput(page,saved);
  return result.browserObservation.status==='observed'&&result.browserObservation.renderState==='ready';
 }).toBe(true);
 const readback=await readOutput(page,saved);
 assert.equal(readback.clientId,clientId);assert.equal(readback.result.browserObservation.clientId,clientId);
 assert.equal(readback.result.saved,true);assert.equal(readback.result.attached,true);
 return {...worker,clientId,peerClientId,rootRestored:true,
  drafts:['Unsent draft A','Unsent draft B'],canvasClosed:true,readback};
}

try{
 await phase('shared-setup',async()=>{
  fixture=spawn(python,['-u',join(root,'tests/fixtures/inline_media_server.py')],{
   cwd:mode==='installed'?resolve(evidenceDir):root,
   env:{...process.env,INLINE_MEDIA_MODE:mode,PYTHONPATH:mode==='installed'?'':process.env.PYTHONPATH||''},
   stdio:['ignore','pipe','pipe']});
  let buffer='';let resolveBoot,rejectBoot;
  const pendingBoot=new Promise((yes,no)=>{resolveBoot=yes;rejectBoot=no});
  fixture.stdout.on('data',chunk=>{
   buffer+=chunk;let at;
   while((at=buffer.indexOf('\n'))>=0){
    const line=buffer.slice(0,at);buffer=buffer.slice(at+1);
    try{const row=JSON.parse(line);fixtureRows.push(row);if(row.url)resolveBoot(row)}catch{}
   }
  });
  // Bound synthetic fixture diagnostics; evidence writing redacts private paths.
  let stderr='';fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
  exit=new Promise(done=>{
   fixture.once('close',(code,signal)=>{done({code,signal});rejectBoot(Object.assign(
    Error('Fixture closed before boot: '+code),{fixtureStderr:stderr}))});
   fixture.once('error',error=>{done({code:null,signal:null,spawnError:true});rejectBoot(error)});
  });
  const timeout=setTimeout(()=>rejectBoot(Error('Fixture startup deadline exceeded')),120000);
  try{boot=await pendingBoot}finally{clearTimeout(timeout)}
  if(guestNative){nativeBrowser=await launchNativeBrowser();browser=nativeBrowser.browser}
  else browser=await chromium.launch({headless:!headed,
   ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{}),
   args:browserArgs});
  assert.equal(boot.mode,mode);
  assert.ok(boot.frontendAssets.some(row=>row.filename.endsWith('.js')));
  return {mode:boot.mode,python:boot.python,browserVersion:browser.version(),headed,guestNative,samples:boot.samples,
   servedPackageFrontend:boot.frontendAssets,
   security:'production authentication, origin boundaries, CSP and media routes unchanged'};
 });
 if(!browser||!boot){
  for(const name of scenarioPhases){
   const physical=physicalNames.includes(name),notSelected=selectedPhases&&!selectedPhases.has(name);
   await record(name,physical||notSelected?'NOT_RUN':'CANT_CHECK',{reason:'Shared setup blocked'},!physical&&!notSelected);
  }
 }else{
  let primary,saved;
  await phase('gif-live-attach',async()=>{
   primary=await newClient();
   const {page,article}=primary;
   await expect(article.locator('[data-media-id]')).toHaveCount(0);
   const nonce=await page.evaluate(()=>window.__mediaDocumentNonce=crypto.randomUUID());
   let navigations=0;
   const navigated=frame=>{if(frame===page.mainFrame())navigations++};
   page.on('framenavigated',navigated);
   saved=await attach(page,'gif');
   const figure=await readyFigure(page,saved.id);
   assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
   const initial=await canvasPixels(figure);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
   await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
   await figure.locator('canvas').screenshot({path:join(evidenceDir,'gif-pixels.png')});
   const matrix=await control(page,'matrix',{});
   assert.ok(matrix.cases.every(row=>row.rejected&&row.unchanged));
   assert.equal(await page.evaluate(()=>window.__mediaDocumentNonce),nonce);
   assert.equal(navigations,0);page.off('framenavigated',navigated);
   return {saved,initial,changed:await canvasPixels(figure),matrix,navigationUnchanged:true,
    documentNonce:nonce,mainFrameNavigations:navigations,observer:'actual application canvas'};
  });
  await phase('metadata-malformed-retry',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable for metadata recovery');
   const {page,article}=primary,pattern='**/api/outputs/media?*',requests=[];
   const exact=request=>{
    const url=new URL(request.url());
    return request.method()==='GET'&&url.pathname==='/api/outputs/media'&&
     url.searchParams.get('sessionId')===boot.sessionId&&url.searchParams.get('messageId')===boot.messageId;
   };
   let corrupted=0,original,intercepted=false;
   const handler=async route=>{
    const request=route.request();
    if(!exact(request))return route.continue();
    requests.push({sessionId:boot.sessionId,messageId:boot.messageId,
     clientId:request.headers()['x-amplifier-client']});
    if(intercepted)return route.continue();
    intercepted=true;
    // Keep production authentication/client/article guards. Corrupt only one
    // successful metadata response; retries and all media bytes stay real.
    const response=await route.fetch();
    original={status:response.status(),descriptor:await response.json()};
    if(response.status()!==200)return route.fulfill({response});
    corrupted++;
    return route.fulfill({response,json:{ok:true}});
   };
   // Drain the previous document before arming a response waiter. Otherwise
   // an in-flight old-document response can match and lose its body on reload.
   await page.goto('about:blank');
   await page.route(pattern,handler);
   try{
    const malformed=page.waitForResponse(response=>exact(response.request()));
    await page.goto(boot.url);await page.waitForSelector('#amp-one');
    const failed=await malformed;
    assert.equal(failed.status(),200);assert.equal(await failed.finished(),null);
    assert.deepEqual(await failed.json(),{ok:true});assert.equal(corrupted,1);
    assert.equal(original.status,200);
    const item=original.descriptor.items.find(item=>item.id===saved.id);
    assert.ok(item,'Corruption must follow a real descriptor for the saved media');
    for(const key of ['id','sessionId','version','sha256'])assert.equal(item[key],saved[key]);
    await expect(article).toBeVisible();await expect(article).toHaveClass(/a-assistant/);
    await expect(article).toContainText('Saved media belongs inside this assistant article.');
    const alert=article.locator('.a-inline-media [role="alert"]');
    await expect(alert).toContainText('Malformed media metadata:');
    const retry=article.getByRole('button',{name:'Retry media metadata',exact:true});
    await expect(retry).toBeVisible();await expect(article.locator('[data-media-id]')).toHaveCount(0);
    assert.deepEqual(pageErrors,[]);
    const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
    assert.ok(clientId);assert.ok(requests.every(row=>row.clientId===clientId));
    const mounted=await article.elementHandle();
    const nonce=await page.evaluate(()=>window.__metadataRetryNonce=crypto.randomUUID());
    let navigations=0;
    const navigated=frame=>{if(frame===page.mainFrame())navigations++};
    page.on('framenavigated',navigated);
    try{
     await article.screenshot({path:join(evidenceDir,'metadata-malformed.png')});
     const pending=page.waitForResponse(response=>exact(response.request())&&
      response.request().headers()['x-amplifier-client']===clientId);
     await retry.click();
     const recovered=await pending;
     assert.equal(recovered.status(),200);assert.equal(await recovered.finished(),null);
     const descriptor=await recovered.json(),restored=descriptor.items.find(item=>item.id===saved.id);
     assert.ok(restored);assert.equal(descriptor.overflow,false);
     assert.ok(Number.isInteger(descriptor.mediaRevision));
     for(const key of ['id','sessionId','version','sha256'])assert.equal(restored[key],saved[key]);
     const figure=await readyFigure(page,saved.id),pixels=await canvasPixels(figure);
     assert.equal(await figure.evaluate((node,messageId)=>node.closest('article').dataset.messageId===messageId,
      boot.messageId),true);
     await expect(alert).toHaveCount(0);await expect(retry).toHaveCount(0);
     await expect(article).toContainText('Saved media belongs inside this assistant article.');
     assert.equal(await mounted.evaluate((node,messageId)=>node.isConnected&&
      node===document.querySelector(`[data-message-id="${messageId}"]`),boot.messageId),true);
     assert.equal(await page.evaluate(()=>window.__metadataRetryNonce),nonce);
     assert.equal(await page.evaluate(()=>window.amplifier.getState().client.id),clientId);
     assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),boot.sessionId);
     assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false);
     assert.equal(navigations,0);assert.equal(corrupted,1);
     assert.ok(requests.length>=2);assert.ok(requests.every(row=>row.clientId===clientId));
     assert.deepEqual(pageErrors,[]);
     await article.screenshot({path:join(evidenceDir,'metadata-recovered.png')});
     // The fixture hardcodes titles: these are response-shape witnesses, not
     // server-produced custom titles. The genuine malformed/retry proof above
     // and the original-body/auth/full-scenario checks remain untouched.
     const titleCompatibility=[],titleClient=await newClient(),titlePage=titleClient.page;
     try{
      for(const title of ['', '\u{1D400}'.repeat(101)]){
       let replacements=0;
       const titleHandler=async route=>{
        if(!exact(route.request()))return route.continue();
        const response=await route.fetch();assert.equal(response.status(),200);
        const value=await response.json(),item=value.items.find(item=>item.id===saved.id);
        assert.ok(item);
        for(const key of ['id','sessionId','version','sha256'])assert.equal(item[key],saved[key]);
        replacements++;
        await route.fulfill({response,json:{...value,items:value.items.map(item=>
         item.id===saved.id?{...item,title}:item)}});
       };
       await titlePage.goto('about:blank');
       await titlePage.route(pattern,titleHandler);
       try{
        const pending=titlePage.waitForResponse(response=>exact(response.request()));
        await titlePage.goto(boot.url);await titlePage.waitForSelector('#amp-one');
        const response=await pending;
        assert.equal(response.status(),200);assert.equal(await response.finished(),null);
        assert.equal((await response.json()).items.find(item=>item.id===saved.id).title,title);
        assert.equal(response.request().headers()['x-amplifier-client'],
         await titlePage.evaluate(()=>window.amplifier.getState().client.id));
        const titled=await readyFigure(titlePage,saved.id);
        assert.equal(await titled.locator('figcaption').textContent(),title);
        assert.deepEqual(await canvasPixels(titled),pixels);
        await expect(titleClient.article.locator('.a-inline-media [role="alert"]')).toHaveCount(0);
        await expect(titleClient.article.getByRole('button',{name:'Retry media metadata',exact:true})).toHaveCount(0);
        assert.ok(replacements>0);assert.deepEqual(pageErrors,[]);
        titleCompatibility.push({kind:'response-shape-validation',serverProducedTitle:false,
         title,codePoints:[...title].length,utf16Units:title.length,rendered:true,originalPixels:true});
       }finally{await titlePage.unroute(pattern,titleHandler)}
      }
     }finally{await unmountAndClose(titleClient)}
     return {malformed:{ok:true},corruptedResponses:corrupted,requests,descriptor,pixels,
      titleCompatibility,
      explicitAlert:true,retryRemoved:true,sameMountedArticle:true,mainFrameNavigations:navigations,
      pageErrors:[...pageErrors],canvasClosed:true};
    }finally{page.off('framenavigated',navigated);await mounted.dispose()}
   }finally{await page.unroute(pattern,handler)}
  });
  await phase('gif-resume',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup failed; MP4 remains independent');
   const {page}=primary,timing=await attach(page,'timing'),figure=await readyFigure(page,timing.id);
   const baseline=(await draws(page,timing.id)).at(-1).at;
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   // Wait for an ACTUAL nonzero frame with enough nonuniform delay to measure.
   await expect.poll(async()=>{
    const row=(await draws(page,timing.id)).at(-1);
    return !!row&&row.at>baseline&&row.index>0&&row.duration>=700000;
   }).toBe(true);
   const frame=(await draws(page,timing.id)).at(-1);
   await page.waitForFunction(frame=>performance.now()-frame.at>=frame.duration/1000*.3,frame);
   await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
   const pausedAt=await page.evaluate(()=>performance.now()),paused=await canvasPixels(figure);
   const stopped=(await draws(page,timing.id)).at(-1);
   assert.equal(stopped.index,frame.index);assert.ok(stopped.index>0);
   const elapsed=pausedAt-frame.at,remaining=frame.duration/1000-elapsed;
   assert.ok(remaining>150&&elapsed>100,'Pause must occur partway, not at the frame boundary');
   await clockWait(page,frame.duration/1000+100);
   assert.deepEqual(await canvasPixels(figure),paused);
   assert.equal((await draws(page,timing.id)).at(-1).at,frame.at);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   const resumedAt=await page.evaluate(()=>performance.now());
   assert.deepEqual(await canvasPixels(figure),paused,'Resume keeps SAME nonzero frame');
   await expect.poll(async()=>(await draws(page,timing.id)).at(-1).at).toBeGreaterThan(frame.at);
   const next=(await draws(page,timing.id)).at(-1),actual=next.at-resumedAt;
   assert.ok(Math.abs(actual-remaining)<150,JSON.stringify({actual,remaining,elapsed}));
   assert.ok(actual<frame.duration/1000-100,'Resume must not restart full delay');
   await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
   return {sample:timing.sha256,frameIndex:frame.index,elapsedMs:elapsed,remainingMs:remaining,resumedDelayMs:actual,pausedPixels:paused};
  });

  let fast;
  await phase('gif-report-cadence',async()=>{
   const client=await newClient(),{page}=client,watch=watchReports(page);
   try{
    const attachedSince=Date.now();
    fast=await attach(page,'fast');
    const figure=await readyFigure(page,fast.id),initial=await canvasPixels(figure);
    const initialDraws=await draws(page,fast.id);
    await observeLatest(page,fast,{playback:'paused',frames:initialDraws.length,
     mediaTime:initialDraws.at(-1).timestamp/1_000_000,since:attachedSince});
    const since=Date.now(),started=await page.evaluate(()=>performance.now());
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await clockWait(page,2200);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    const ended=await page.evaluate(()=>performance.now()),actual=await draws(page,fast.id),latest=actual.at(-1);
    assert.ok(actual.length>=50,'Real 10 ms GIF must keep advancing; do not meet report cap by slowing frames');
    assert.ok(actual.every(row=>row.duration===10000),'Fixture uses native 10 ms frame durations');
    const observation=await observeLatest(page,fast,{playback:'paused',frames:actual.length,
     mediaTime:latest.timestamp/1_000_000,since});
    const reports=await dispatchedReports(page,fast.id),requests=watch.rows.filter(row=>row.id===fast.id),
     moving=reports.filter(row=>row.playback==='playing');
    assert.equal(requests.length,reports.length,'Dispatch attempts counted against real HTTP POST requests');
    assert.ok(moving.length>=2,'Include at least one real same-state advancing report');
    assert.ok(moving.length<=1+Math.floor((ended-started)/1000));
    for(let n=1;n<moving.length;n++)assert.ok(moving[n].at-moving[n-1].at>=1000,
     'Same-state requests must be spaced by 1 second, not frame modulo bursts');
    // Loading, initial-ready, play and pause are immediate transitions; each
    // contributes separately to the inclusive observation-window bound.
    assert.ok(reports.length<=4+Math.floor((ended-started)/1000));
    const transitions=reports.filter((row,n)=>n===0||
     row.renderState!==reports[n-1].renderState||row.playback!==reports[n-1].playback||row.detail!==reports[n-1].detail);
    assert.deepEqual(transitions.map(row=>[row.renderState,row.playback]),[
     ['loading','paused'],['ready','paused'],['ready','playing'],['ready','paused']]);
    for(let n=0;n<reports.length;n++){
     const row=reports[n];
     assert.equal(row.sessionId,fast.sessionId);assert.equal(row.messageId,boot.messageId);
     assert.equal(row.version,fast.version);assert.equal(row.sha256,fast.sha256);
     assert.equal(row.generation,observation.generation);
     if(n)assert.ok(row.sequence>reports[n-1].sequence);
     if(row.presentedFrames)assert.equal(row.mediaTime,actual[row.presentedFrames-1].timestamp/1_000_000);
    }
    assert.equal(observation.sequence,reports.at(-1).sequence);
    await action(page,'session.create',{title:'Dispose bounded report timer'});
    await expect(page.locator('[data-media-id]')).toHaveCount(0);
    const after=watch.rows.filter(row=>row.id===fast.id).length;
    await clockWait(page,1100);
    assert.equal(watch.rows.filter(row=>row.id===fast.id).length,after,'No delayed reports after unmount');
    return {sample:fast,windowMs:ended-started,actualPresentedFrames:actual.length,actualMediaTime:latest.timestamp/1_000_000,
     reports,requests,transitions,observation,cleanup:await unmountAndClose(client)};
   }finally{watch.stop();if(!page.isClosed())await unmountAndClose(client)}
  });
  await phase('report-readback-negative-control',async()=>{
   const client=await newClient({suppressReports:true}),{page}=client;
   try{
    const item=fast||await attach(page,'fast'),figure=await readyFigure(page,item.id);
    const initial=await canvasPixels(figure),since=Date.now();
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    const actual=await draws(page,item.id),read=await readOutput(page,item);
    assert.equal(read.result.browserObservation.status,'not_observed');
    assert.throws(()=>requireObservation(read,{playback:'paused',frames:actual.length,
     mediaTime:actual.at(-1).timestamp/1_000_000,since}),/Real reports required/);
    return {pixelsAdvanced:true,exactClient:read.clientId,readback:read.result.browserObservation,
     readbackGuardRejected:true,syntheticReportsSubmitted:false,cleanup:await unmountAndClose(client)};
   }finally{if(!page.isClosed())await unmountAndClose(client)}
  });

  // Independent phase: no GIF assertion or decoder capability gates this.
  let videoClient,mp4;
  await phase('mp4-native',async()=>{
   if(!boot.samples.mp4)throw new CantCheck('Supply INLINE_MEDIA_MP4 containing an actual playable synthetic/public MP4');
   videoClient=await newClient();
   const {page}=videoClient;mp4=await attach(page,'mp4');
   const figure=await readyFigure(page,mp4.id),video=figure.locator('video');
   const attrs=await video.evaluate(node=>({controls:node.controls,muted:node.muted,autoplay:node.autoplay,paused:node.paused}));
   assert.deepEqual(attrs,{controls:true,muted:true,autoplay:false,paused:true});
   assert.ok(await video.evaluate(node=>typeof node.requestVideoFrameCallback==='function'));
   const since=Date.now(),watch=watchReports(page,mp4.id);
   await video.evaluate(node=>node.play());
   await expect.poll(async()=>(await nativeFrames(page,mp4.id)).length).toBeGreaterThan(2);
   await expect.poll(async()=>{
    const {result}=await readOutput(page,mp4),o=result.browserObservation;
    return o.status==='observed'&&o.playback==='playing'&&o.presentedFrames>2&&o.mediaTime>0;
   }).toBe(true);
   const playingRead=await readOutput(page,mp4),playing=playingRead.result.browserObservation;
   const frames=await nativeFrames(page,mp4.id);
   const witnessed=frames.find(row=>row.presentedFrames===playing.presentedFrames);
   assert.ok(witnessed,'Readback count must refer to an actual application-owned native callback');
   requireObservation(playingRead,{playback:'playing',frames:witnessed.presentedFrames,mediaTime:witnessed.mediaTime,since});
   assert.ok(frames.at(-1).presentedFrames>frames[0].presentedFrames);
   assert.ok(frames.at(-1).mediaTime>frames[0].mediaTime);
   assert.notDeepEqual(frames.at(-1).pixels,frames[0].pixels);
   await video.evaluate(node=>node.pause());
   // The final native callback can run after pause(). Compare against the LIVE
   // witness inside the poll, not an already-obsolete frozen expected count.
   let pausedPair;
   await expect.poll(async()=>{
    const read=await readOutput(page,mp4),latest=(await nativeFrames(page,mp4.id)).at(-1),o=read.result.browserObservation;
    const matched=o.status==='observed'&&o.playback==='paused'&&o.presentedFrames===latest.presentedFrames&&o.mediaTime===latest.mediaTime;
    if(matched)pausedPair={read,latest};
    return matched;
   }).toBe(true);
   const pausedObservation=requireObservation(pausedPair.read,{playback:'paused',
    frames:pausedPair.latest.presentedFrames,mediaTime:pausedPair.latest.mediaTime,since});
   const nativeReports=await dispatchedReports(page,mp4.id),moving=nativeReports.filter(row=>row.playback==='playing');
   for(let n=1;n<moving.length;n++)assert.ok(moving[n].at-moving[n-1].at>=1000);
   watch.stop();
   const at=await video.evaluate(node=>node.currentTime);
   await clockWait(page,Math.max(100,(frames.at(-1).mediaTime-frames.at(-2).mediaTime)*3000));
   assert.equal(await video.evaluate(node=>node.currentTime),at);
   const seek=await video.evaluate(node=>new Promise((done,no)=>{
    const target=Math.min(node.duration*.65,node.duration-.1);
    if(!Number.isFinite(target)||target<=.1){no(Error('MP4 duration cannot support the seek discriminator'));return}
    const deadline=setTimeout(()=>no(Error('seeked deadline exceeded')),10000);
    node.addEventListener('seeked',()=>{clearTimeout(deadline);done({target,actual:node.currentTime,seeking:node.seeking})},{once:true});
    node.currentTime=target;
   }));
   assert.equal(seek.seeking,false);assert.ok(Math.abs(seek.actual-seek.target)<.08);
   await video.screenshot({path:join(evidenceDir,'mp4-native.png')});
   return {mp4,attrs,frames:frames.map(({pixels,...row})=>({...row,pixelSha256:createHash('sha256').update(Buffer.from(pixels)).digest('hex')})),
    playingObservation:playing,pausedObservation,reportRequests:watch.rows,nativeReports,
    pausedTime:at,seekTarget:seek.target,seekedTime:seek.actual};
  });
  await phase('mp4-native-controls',async()=>{
   if(!videoClient||!mp4)throw new CantCheck('MP4 setup did not reach native controls');
   const {page}=videoClient;
   if(guestNative){
    const inputStart=nativeInput.length,video=figureFor(page,mp4.id).locator('video');
    const selector=`[data-media-id="${mp4.id}"] video`;
    await page.bringToFront();await video.scrollIntoViewIfNeeded();
    assert.equal(await video.evaluate(node=>node.paused),true);
    const fullscreen=await nativeControl(page,selector,'button',/^enter full screen$/i);
    nativeKey('space');
    await expect.poll(()=>video.evaluate(node=>document.fullscreenElement===node)).toBe(true);
    await page.screenshot({path:join(evidenceDir,'mp4-fullscreen.png')});
    nativeKey('Escape');
    await expect.poll(()=>page.evaluate(()=>document.fullscreenElement===null)).toBe(true);
    await nativeControl(page,selector,'button',/^unmute$/i);nativeKey('space');
    await expect.poll(()=>video.evaluate(node=>node.muted)).toBe(false);
    const before=await video.evaluate(node=>node.volume);
    const volume=await nativeControl(page,selector,'slider',/^volume$/i);
    nativeKey(before>.5?'Left':'Right');
    await expect.poll(()=>video.evaluate(node=>node.volume)).not.toBe(before);
    const after=await video.evaluate(node=>({volume:node.volume,paused:node.paused,muted:node.muted}));
    assert.equal(after.paused,true);
    await page.screenshot({path:join(evidenceDir,'mp4-volume.png')});
    await nativeControl(page,selector,'button',/^mute$/i);nativeKey('space');
    await expect.poll(()=>video.evaluate(node=>node.muted)).toBe(true);
    return {fullscreenUI:true,fullscreenVideo:true,fullscreenExitNull:true,volumeUI:true,
     fullscreen,volume,volumeBefore:before,volumeAfter:after,input:nativeInput.slice(inputStart)};
   }
   // UA shadow controls are not portable DOM. Only claim when THIS browser
   // exposes usable accessibility targets; absence is explicit, never PASS.
   const fullscreen=page.getByRole('button',{name:/full.?screen/i}),volume=page.getByRole('slider',{name:/volume/i});
   if(await fullscreen.count()!==1||await volume.count()!==1)
    throw new CantCheck('Browser does not expose native fullscreen/volume controls as actionable accessibility targets');
   await fullscreen.click();
   await expect.poll(()=>page.evaluate(()=>!!document.fullscreenElement)).toBe(true);
   await page.screenshot({path:join(evidenceDir,'mp4-fullscreen.png')});
   await page.keyboard.press('Escape');
   await expect.poll(()=>page.evaluate(()=>document.fullscreenElement===null)).toBe(true);
   const video=figureFor(page,mp4.id).locator('video');
   const before=await video.evaluate(node=>node.volume);
   await volume.focus();await page.keyboard.press(before>.5?'ArrowDown':'ArrowUp');
   await expect.poll(()=>video.evaluate(node=>node.volume)).not.toBe(before);
   return {fullscreenUI:true,volumeUI:true};
  },{required:false});

  await phase('mp4-decode-error',async()=>{
   if(!mp4)throw new CantCheck('MP4 sample has not been attached');
   const client=await newClient({corruptVideoId:mp4.id}),{page}=client;
   try{
    const since=Date.now(),figure=figureFor(page,mp4.id);
    await figure.scrollIntoViewIfNeeded();
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect(figure).toContainText('cannot be decoded');
    const download=figure.getByRole('link',{name:'Download original'});
    await expect(download).toBeVisible();
    const response=await page.request.get(await download.getAttribute('href'));
    assert.equal(response.status(),200);
    assert.equal(createHash('sha256').update(await response.body()).digest('hex'),mp4.sha256);
    const observation=await observeLatest(page,mp4,{playback:'paused',renderState:'error',frames:0,mediaTime:0,
     since:since-5000}); // page load may already have reported the decode error
    return {testOnlyCorruptInlineRead:true,downloadSha256:mp4.sha256,distinctError:true,observation,
     cleanup:await unmountAndClose(client)};
   }finally{if(!page.isClosed())await unmountAndClose(client)}
  });
  await phase('mp4-explicit-cleanup',async()=>{
   if(!videoClient||!mp4)throw new CantCheck('MP4 native phase did not retain a client');
   const {page}=videoClient,video=figureFor(page,mp4.id).locator('video'),watch=watchReports(page,mp4.id);
   try{
    await video.scrollIntoViewIfNeeded();
    await video.evaluate(node=>{window.__retainedNativeVideo=node;return node.play()});
    await expect.poll(()=>video.evaluate(node=>node.paused)).toBe(false);
    const before=await retainedVideoState(page);
    assert.equal(before.connected,true);assert.equal(before.hasSrc,true);assert.notEqual(before.pending,null);
    await action(page,'session.create',{title:'Detach actual native MP4 owner'});
    await expect(figureFor(page,mp4.id)).toHaveCount(0);
    await expect.poll(()=>retainedVideoState(page)).toMatchObject({connected:false,hasSrc:false,paused:true});
    const after=await retainedVideoState(page);
    assert.ok(after.pause>before.pause,'Owned pause() call, not browser auto-pause');
    assert.ok(after.cancel.length>before.cancel.length&&after.cancelledPending>before.cancelledPending,
     'Owned cancellation of an actually pending video frame callback');
    assert.ok(after.removeSrc>before.removeSrc,'Owned removeAttribute(src) call');
    assert.ok(after.load>before.load,'Owned load() call after src removal');
    const reports=watch.rows.length;
    await clockWait(page,1100);
    const settled=await retainedVideoState(page);
    assert.equal(settled.presented,after.presented);assert.equal(settled.requests,after.requests);
    assert.equal(watch.rows.length,reports,'No old-native reports after unmount');
    return {before,after,settled,methodWitnesses:true,browserCloseUsedAsProof:false,
     cleanup:await unmountAndClose(videoClient)};
   }finally{watch.stop()}
  });

  await phase('partial-disposal',async()=>{
   const client=await newClient({captureTinyPixels:true});
   try{
    const saved=await attach(client.page,'partial'),figure=await readyFigure(client.page,saved.id);
    const check=async index=>{
     const row=(await draws(client.page,saved.id)).find(row=>row.index===index);
     assert.ok(row,`Actual disposal frame ${index} was drawn`);
     assert.deepEqual(row.pixels,boot.partialPixels[index].flat());
    };
    await check(0);
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    for(let index=1;index<4;index++){
     await expect.poll(async()=>(await draws(client.page,saved.id)).some(row=>row.index===index)).toBe(true);
     await check(index);
    }
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    await figure.locator('canvas').screenshot({path:join(evidenceDir,'partial-transparent.png')});
    return {disposal:[2,3],expected:boot.partialPixels,draws:await draws(client.page,saved.id),
     cleanup:await unmountAndClose(client)};
   }finally{if(!client.page.isClosed())await unmountAndClose(client)}
  });
  await phase('large-native',async()=>{
   const client=await newClient(),{page}=client;
   try{
    const large=await attach(page,'large');
    assert.equal(large.bytes,boot.samples.large.bytes);
    assert.equal(large.sha256,boot.samples.large.sha256);
    assert.ok(large.bytes>=19_000_000&&large.bytes<=20_100_000);
    assert.deepEqual([large.media.width,large.media.height,large.media.frameCount],[1280,720,26]);
    const figure=await readyFigure(page,large.id),initial=await canvasPixels(figure);
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await expect.poll(async()=>(await draws(page,large.id)).at(-1)?.index||0).toBeGreaterThan(0);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    // Three real application players admit native work concurrently in one
    // realm. Counters observe native decode calls, not mocked source estimates.
    const additional=await Promise.all([attach(page,'large',1),attach(page,'large',2)]);
    const largeIds=[large.id,...additional.map(item=>item.id)];
    assert.equal(new Set(largeIds).size,3);
    for(const id of largeIds){
     await readyFigure(page,id);
     await expect.poll(async()=>(await draws(page,id)).length).toBeGreaterThan(0);
    }
    const decoderIds=await page.evaluate(ids=>ids.map(id=>window.__mediaProbe.draws.find(row=>row.id===id).decoderId),largeIds);
    assert.equal(new Set(decoderIds).size,3,'Three actual application-owned decoders, not an old DOM-count barrier');
    const live=await probeState(page);
    assert.ok(live.peakActive<=2&&live.peakActive>0);
    assert.ok(Object.values(live.perDecoder).every(row=>row.peak<=2));
    const cdp=await client.context.newCDPSession(page);
    const browserMemory=await cdp.send('Runtime.getHeapUsage');
    await cdp.detach();
    const serverMemory=await (await page.request.get(boot.url+'/api/fixture/evidence',{headers:privateHeaders})).json();
    await figure.locator('canvas').screenshot({path:join(evidenceDir,'large-moving-pixels.png')});
    return {sample:{bytes:large.bytes,sha256:large.sha256,width:1280,height:720,frames:26},
     largeIds,decoderIds,observed:live,browserMemory,serverRssBytes:serverMemory.rssBytes,
     nativeCaches:'UNKNOWN: JS/CDP heap and retained VideoFrame counts do not bound browser codec caches or native process heap',
     cleanup:await unmountAndClose(client)};
   }finally{if(!page.isClosed())await unmountAndClose(client)}
  });

  await phase('reduced-motion',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page}=primary,figure=await readyFigure(page,saved.id);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   await page.emulateMedia({reducedMotion:'reduce'});
   await expect(figure).toHaveAttribute('data-playback','paused');
   const pixels=await canvasPixels(figure),frame=(await draws(page,saved.id)).at(-1);
   await clockWait(page,frame.duration/1000+100);
   assert.deepEqual(await canvasPixels(figure),pixels);
   await page.emulateMedia({reducedMotion:'no-preference'});
   return {paused:true,frameIndex:frame.index};
  });
  await phase('visibility',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   if(guestNative)await primary.page.bringToFront();
   const {page,context}=primary,figure=await readyFigure(page,saved.id);
   let other;
   try{
    await page.bringToFront();await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    const foreground=await zoomMetrics(page),drawsBefore=(await draws(page,saved.id)).length;
    await expect.poll(async()=>(await draws(page,saved.id)).length).toBeGreaterThan(drawsBefore);
    other=guestNative?await nativeTab(page):await context.newPage();pages.add(other);
    await other.goto(boot.url+'/api/health');await other.bringToFront();
    if(!await page.evaluate(()=>document.hidden))throw new CantCheck('This browser does not mark background tabs hidden');
    await expect(figure).toHaveAttribute('data-playback','paused');
    const background=await zoomMetrics(page),pixels=await canvasPixels(figure),pausedDraws=await draws(page,saved.id);
    assert.equal(background.visibilityState,'hidden');
    const interval=Math.max(...pausedDraws.map(row=>row.duration))/1000+100;
    await clockWait(page,interval);
    assert.equal((await draws(page,saved.id)).length,pausedDraws.length);
    assert.deepEqual(await canvasPixels(figure),pixels);
    await other.screenshot({path:join(evidenceDir,'visibility-foreground-tab.png')});
    return {actualDocumentHidden:true,paused:true,foreground,background,
     stableIntervalMs:interval,draws:pausedDraws.length,pixels,nativeTab:guestNative};
   }finally{
    if(other){await other.close();pages.delete(other)}await page.bringToFront();
    if(guestNative)await expect.poll(()=>page.evaluate(()=>document.hidden)).toBe(false);
    // An unsupported headless visibility sensor leaves playback running.
    // Settle this phase explicitly; the next phase must not inherit that state.
    const pause=figure.getByRole('button',{name:'Pause GIF',exact:true});
    if(await pause.count())await pause.click();
   }
  },{required:false});
  await phase('offscreen',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page}=primary,figure=await readyFigure(page,saved.id);
   await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
   // Real scrolling, not an injected intersection report.
   await page.locator('.a-messages').evaluate(node=>{node.scrollTop=0});
   const box=await figure.boundingBox(),pane=await page.locator('.a-messages').boundingBox();
   if(box&&box.y+box.height>pane.y&&box.y<pane.y+pane.height)
    throw new CantCheck('Fixture conversation has insufficient scroll range to hide the media');
   await expect.poll(async()=>await figure.count()===0||await figure.getAttribute('data-playback')==='paused').toBe(true);
   const unmounted=await figure.count()===0;
   if(unmounted)await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
   else{
    const pixels=await canvasPixels(figure),frame=(await draws(page,saved.id)).at(-1);
    await clockWait(page,frame.duration/1000+100);
    assert.deepEqual(await canvasPixels(figure),pixels);
   }
   if(await figureFor(page,saved.id).count())await figureFor(page,saved.id).scrollIntoViewIfNeeded();
   return {offscreen:true,pausedOrUnmounted:true,unmounted};
  });
  await phase('decoder-error',async()=>{
   const client=await newClient({corrupt:true});
   try{
    const figure=figureFor(client.page,saved?.id|| (await attach(client.page,'gif')).id);
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect(figure.getByRole('link',{name:'Download original'})).toBeVisible();
    return {distinctError:true,cleanup:await unmountAndClose(client)};
   }finally{if(!client.page.isClosed())await unmountAndClose(client)}
  });
  await phase('unsupported-decoder',async()=>{
   const client=await newClient({unsupported:true});
   try{
    const item=saved||await attach(client.page,'gif'),figure=figureFor(client.page,item.id);
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect(figure).toContainText('unsupported');
    await expect(figure.getByRole('link',{name:'Download original'})).toBeVisible();
    return {unsupportedExplicit:true,cleanup:await unmountAndClose(client)};
   }finally{if(!client.page.isClosed())await unmountAndClose(client)}
  });
  await phase('decoder-late-cleanup',async()=>{
   const client=await newClient({hold:true}),{page}=client;
   try{
    await expect.poll(async()=>(await probeState(page)).heldCount).toBeGreaterThan(0);
    await action(page,'session.create',{title:'Unmount while native decode is pending'});
    await expect(page.locator('[data-media-id]')).toHaveCount(0);
    await page.evaluate(()=>{window.__mediaProbe.hold=false;window.__mediaProbe.held.splice(0).forEach(done=>done())});
    await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
    const state=await probeState(page);assert.equal(state.created,state.closed);
    return {lateFrameClosed:true,cleanup:await unmountAndClose(client)};
   }finally{
    if(!page.isClosed()){
     await page.evaluate(()=>window.__mediaProbe.held.splice(0).forEach(done=>done()));
     await unmountAndClose(client);
    }
   }
  });

  await phase('preservation',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const peer=await newClient(),{page}=primary;
   try{
    await restoreSession(page);
    for(const [client,text] of [[primary,'Unsent draft A'],[peer,'Unsent draft B']]){
     await client.page.getByRole('textbox',{name:'Message Amplifier'}).fill(text);
     await expect(client.page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(text);
    }
    for(const p of [page,peer.page])await readyFigure(p,saved.id);
    const readingPosition=p=>p.locator('.a-messages').evaluate((node,{messageId,mediaId})=>{
     const pane=node.getBoundingClientRect(),message=[...node.querySelectorAll('[data-message-id]')].find(row=>row.dataset.messageId===messageId),
      media=[...node.querySelectorAll('[data-media-id]')].find(row=>row.dataset.mediaId===mediaId);
     return {scrollTop:node.scrollTop,message:message?message.getBoundingClientRect().top-pane.top:null,
      media:media?media.getBoundingClientRect().top-pane.top:null};
    },{messageId:boot.messageId,mediaId:saved.id});
    const positions=await Promise.all([page,peer.page].map(readingPosition)),afterPositions=[];
    const scrolls=positions.map(row=>row.scrollTop);
    const item=await attach(page,'gif',1);
    // Publication/readiness only: do NOT move either reader to the new figure.
    await expect(figureFor(page,item.id)).toHaveAttribute('data-media-state','ready');
    await expect(figureFor(peer.page,item.id)).toHaveAttribute('data-media-state','ready');
    for(const [index,p,text] of [[0,page,'Unsent draft A'],[1,peer.page,'Unsent draft B']]){
     await expect(p.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(text);
     const current=await readingPosition(p);afterPositions.push(current);
     // Preserve the actual reading anchor. Raw scrollTop may compensate for
     // content growth above this message while its screen position stays fixed.
     for(const anchor of ['message','media']){
      assert.equal(typeof positions[index][anchor],'number');assert.equal(typeof current[anchor],'number');
      assert.ok(Math.abs(current[anchor]-positions[index][anchor])<3,JSON.stringify({index,anchor,before:positions[index],after:current}));
     }
     assert.equal(await p.evaluate(()=>window.amplifier.getState().canvas.open),false);
    }
    await phase('worker-history-descriptor',()=>workerHistoryDescriptor(primary,peer,saved));
    await control(page,'mutate',{name:'gif',operation:'remove-source'});
    await page.reload();await page.waitForSelector('#amp-one');
    await readyFigure(page,saved.id);
    await action(page,'session.create',{title:'Reopen test'});await restoreSession(page);
    await readyFigure(page,saved.id);
    const evidence=await (await page.request.get(boot.url+'/api/fixture/evidence',{headers:privateHeaders})).json();
    assert.deepEqual(evidence.errors,[]);
    assert.ok(!Object.keys(evidence.counters).some(key=>key.startsWith('unexpected:')));
    return {twoClients:true,drafts:['Unsent draft A','Unsent draft B'],scrolls,positions,afterPositions,sourceRemoved:true,reloaded:true,reopened:true,
     automaticRuntimeCalls:0,counters:evidence.counters,peerCleanup:await unmountAndClose(peer)};
   }finally{if(!peer.page.isClosed())await unmountAndClose(peer)}
  });
  if(!receipts.some(row=>row.phase==='worker-history-descriptor'))
   await phase('worker-history-descriptor',async()=>{
    throw new CantCheck('Preservation did not reach the native-discovered worker rendering check');
   });
  await phase('gif-canvas',async()=>{
   let client=await newClient(),peer=await newClient();
   let page=client.page;
   const canvasRoot=p=>p.locator('[data-canvas-view="primary"]');
   const canvasFigure=(p,id)=>canvasRoot(p).locator(`[data-media-id="${id}"]`);
   const inlineFigure=(p,id)=>p.locator(`[data-message-id="${boot.messageId}"] [data-media-id="${id}"]`);
   const canvasDraws=(p,id)=>p.evaluate(id=>window.__mediaProbe.draws.filter(row=>row.id===id&&row.viewId==='primary'),id);
   const state=p=>p.evaluate(()=>window.amplifier.getState());
   const originalPeer=await state(peer.page);
   let releaseOpen,openCalls=0,clickEnvelope;
   const openGate=new Promise(done=>{releaseOpen=done});
   const holdOpen=async route=>{
    const body=route.request().postDataJSON();
    if(body?.action!=='canvas.openGif')return route.continue();
    openCalls++;clickEnvelope=body;await openGate;await route.continue();
   };
   let large,identity,reference,peerBefore,primaryError;
   try{
    large=await attach(page,'large');
    assert.equal(large.bytes,boot.samples.large.bytes);
    if(process.env.INLINE_MEDIA_FULL_RESOLUTION==='1'){
     assert.ok(large.bytes>=60_000_000&&large.bytes<=64*1024*1024);
     assert.deepEqual([large.media.width,large.media.height,large.media.frameCount],[1920,802,68]);
    }else assert.ok(large.bytes>5*1024*1024&&large.bytes>=19_000_000&&large.bytes<=20_100_000);
    assert.equal(large.sha256,boot.samples.large.sha256);
    await action(peer.page,'canvas.show',{kind:'text',title:'Peer selected artifact',content:'Keep this peer Canvas'});
    await peer.page.getByRole('textbox',{name:'Message Amplifier'}).fill('GIF peer unsent draft');
    await expect(peer.page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('GIF peer unsent draft');
    await expect.poll(async()=>(await state(peer.page)).canvas.renderReports?.preview?.status).toBe('ready');
    peerBefore=await state(peer.page);
    await page.getByRole('textbox',{name:'Message Amplifier'}).fill('GIF clicker unsent draft');
    await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('GIF clicker unsent draft');
    const inline=inlineFigure(page,large.id);
    await inline.scrollIntoViewIfNeeded();
    await expect(inline).toHaveAttribute('data-media-state','ready');
    await expect(inline.getByRole('button',{name:'Open in Canvas',exact:true})).toHaveCount(1);
    assert.equal((await state(page)).canvas.open,false,'Retained media alone never opens Canvas');
    const inlineBefore=await readOutput(page,large);
    assert.equal(inlineBefore.result.browserObservation.status,'observed');
    const inlineGeneration=inlineBefore.result.browserObservation.generation;
    // Actual visible USER click: never assign Canvas in the fixture.
    const receipt=page.waitForResponse(response=>response.request().method()==='POST'&&
     new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action==='canvas.openGif');
    await page.route('**/api/actions',holdOpen);
    await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();
    await expect.poll(()=>openCalls).toBe(1);
    await expect(inline.getByRole('button',{name:'Opening in Canvas…',exact:true})).toBeDisabled();
    assert.equal(typeof clickEnvelope.id,'string');assert.ok(clickEnvelope.id.length>0);
    assert.equal(clickEnvelope.args.messageId,boot.messageId);
    assert.equal((await state(page)).canvas.open,false,'Pending does not spontaneously select an artifact');
    releaseOpen();
    const opened=await receipt;
    await page.unroute('**/api/actions',holdOpen);
    assert.equal(opened.status(),200);
    const result=await opened.json();assert.equal(result.accepted,true);assert.equal(result.result.status,'opened');
    identity=result.result.id;
    await expect.poll(async()=>(await state(page)).canvas.id).toBe(identity);
    const current=await state(page);
    assert.equal(current.client.id,await page.evaluate(()=>window.amplifier.shellClientId));
    assert.notEqual(current.client.id,peerBefore.client.id,'The visible clicker and preserved peer are distinct clients');
    assert.equal(current.canvas.kind,'gif');reference=current.canvas.mediaReference;
    assert.deepEqual(Object.fromEntries(['id','sessionId','version','sha256'].map(key=>[key,reference.item[key]])),
     Object.fromEntries(['id','sessionId','version','sha256'].map(key=>[key,large[key]])));
    assert.equal(reference.messageId,boot.messageId);
    const rows=current.canvasArtifacts.filter(row=>row.kind==='gif');
    assert.equal(rows.length,1);assert.equal(rows[0].messageId,null);
    assert.deepEqual(rows[0].publications,[]);assert.equal(rows[0].revision,1);assert.equal(rows[0].versions.length,1);
    assert.ok(rows[0].body.bytes<8000);
    const figure=canvasFigure(page,large.id);
    await expect(figure).toBeVisible();await expect(figure).toHaveAttribute('data-media-state','ready');
    await expect(figure).toHaveAttribute('data-playback','paused');
    await expect(canvasRoot(page).getByRole('button',{name:'Open in Canvas',exact:true})).toHaveCount(0);
    await expect(canvasRoot(page).getByRole('button',{name:/Copy canvas source|Download canvas source|Restore as new version/})).toHaveCount(0);
    const initial=await canvasPixels(figure),defaultDraws=(await canvasDraws(page,large.id)).length;
    assert.deepEqual([initial.width,initial.height],process.env.INLINE_MEDIA_FULL_RESOLUTION==='1'?[1920,802]:[1280,720]);
    assert.ok(initial.pixels.some(pixel=>pixel[3]===255),'Actual decoded first-frame pixels');
    await clockWait(page,350);assert.equal((await canvasDraws(page,large.id)).length,defaultDraws);
    await figure.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect.poll(()=>canvasPixels(figure)).not.toEqual(initial);
    await expect.poll(async()=>(await canvasDraws(page,large.id)).length).toBeGreaterThan(defaultDraws+1);
    await figure.getByRole('button',{name:'Pause GIF',exact:true}).click();
    const paused=await canvasPixels(figure),pauseDraws=(await canvasDraws(page,large.id)).length;
    await clockWait(page,450);assert.deepEqual(await canvasPixels(figure),paused);
    assert.equal((await canvasDraws(page,large.id)).length,pauseDraws);
    await figure.locator('canvas').screenshot({path:join(evidenceDir,'gif-canvas-large-pixels.png')});
    await figure.locator('.media-controls').scrollIntoViewIfNeeded();
    await page.screenshot({path:join(evidenceDir,'gif-canvas-controls-viewport.png')});
    const canvasDecoder=(await canvasDraws(page,large.id))[0].decoderId;
    assert.ok((await probeState(page)).perDecoder[canvasDecoder].peak<=2);
    const reportsDuringCanvas=await dispatchedReports(page,large.id);
    assert.ok(reportsDuringCanvas.every(row=>row.generation===inlineGeneration),
     'Canvas local reports must not replace the inline output generation');
    // Inline controls and fresh readback still work after the newer Canvas
    // instance reports real playback. A synthetic Canvas mediaReport breaks it.
    await inline.scrollIntoViewIfNeeded();
    await inline.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect(inline).toHaveAttribute('data-playback','playing');
    await inline.getByRole('button',{name:'Pause GIF',exact:true}).click();
    await expect.poll(async()=>{
     const observed=(await readOutput(page,large)).result.browserObservation;
     return observed.generation===inlineGeneration&&observed.sequence>inlineBefore.result.browserObservation.sequence
      &&observed.playback==='paused';
    }).toBe(true);
    const inlineAfter=await readOutput(page,large);
    assert.equal(inlineAfter.result.browserObservation.generation,inlineGeneration);
    assert.equal(inlineAfter.result.browserObservation.playback,'paused');
    const link=await figure.getByRole('link',{name:'Download original'}).getAttribute('href');
    const original=await page.request.get(link);assert.equal(original.status(),200);
    assert.equal(createHash('sha256').update(await original.body()).digest('hex'),large.sha256);
    // Close must dispose the actual Canvas decoder; inline players remain.
    await action(page,'canvas.close',{});
    await expect(canvasFigure(page,large.id)).toHaveCount(0);
    await expect.poll(async()=>page.evaluate(id=>window.__mediaProbe.perDecoder[id].live,canvasDecoder)).toBe(0);
    await expect.poll(async()=>page.evaluate(id=>window.__mediaProbe.perDecoder[id].closed,canvasDecoder)).toBe(true);
    const closed=(await probeState(page)).closed;
    await action(page,'canvas.reopen');
    await expect(canvasFigure(page,large.id)).toHaveAttribute('data-media-state','ready');
    assert.ok((await probeState(page)).closed>=closed);
    assert.equal((await state(page)).canvas.id,identity);
    const duplicate=page.waitForResponse(response=>response.request().method()==='POST'&&
     new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action==='canvas.openGif');
    await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();
    assert.equal((await (await duplicate).json()).result.id,identity);
    assert.equal((await state(page)).canvasArtifacts.filter(row=>row.kind==='gif').length,1);
    await control(page,'mutate',{name:'large',operation:'remove-source'});
    await page.reload();await page.waitForSelector('#amp-one');
    await expect(canvasFigure(page,large.id)).toHaveAttribute('data-media-state','ready');
    assert.deepEqual((await state(page)).canvas.mediaReference,reference);
    // Close the ordinary app tab and reopen with its saved client presentation.
    // This does not unmount by selecting a fixture-assigned artifact.
    const resumeClientId=(await state(page)).client.id;
    await page.close();pages.delete(page);
    client=await newClient({resumeClientId});page=client.page;
    await expect(canvasFigure(page,large.id)).toHaveAttribute('data-media-state','ready');
    assert.equal((await state(page)).canvas.id,identity);
    assert.deepEqual((await state(page)).canvas.mediaReference,reference);
    await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('GIF clicker unsent draft');
    const restored=canvasFigure(page,large.id);
    await restored.scrollIntoViewIfNeeded();
    const beforeMotionPixels=await canvasPixels(restored),beforeMotionDraws=(await canvasDraws(page,large.id)).length;
    await restored.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect(restored).toHaveAttribute('data-playback','playing');
    await expect.poll(()=>canvasPixels(restored)).not.toEqual(beforeMotionPixels);
    await expect.poll(async()=>(await canvasDraws(page,large.id)).length).toBeGreaterThan(beforeMotionDraws+1);
    await page.emulateMedia({reducedMotion:'reduce'});
    await expect(restored).toHaveAttribute('data-playback','paused');
    const restoredPixels=await canvasPixels(restored),motionDraws=(await canvasDraws(page,large.id)).length;
    await clockWait(page,350);assert.deepEqual(await canvasPixels(restored),restoredPixels);
    assert.equal((await canvasDraws(page,large.id)).length,motionDraws,'Motion reduction stops actual Canvas draws');
    await restored.getByRole('button',{name:'Play GIF',exact:true}).click();
    await expect(restored).toHaveAttribute('data-playback','playing');
    await expect.poll(()=>canvasPixels(restored)).not.toEqual(restoredPixels);
    await expect.poll(async()=>(await canvasDraws(page,large.id)).length).toBeGreaterThan(motionDraws);
    await restored.getByRole('button',{name:'Pause GIF',exact:true}).click();
    await page.emulateMedia({reducedMotion:'no-preference'});
    const after=await state(page),other=await state(peer.page);
    assert.deepEqual(other.canvas,peerBefore.canvas,'Other client Canvas stays exactly unchanged');
    await expect(peer.page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('GIF peer unsent draft');
    assert.equal(after.canvasArtifacts.filter(row=>row.kind==='gif').length,1);
    assert.equal(after.canvasArtifacts.find(row=>row.id===identity).versions.length,1);
    const evidence=await (await page.request.get(boot.url+'/api/fixture/evidence',{headers:privateHeaders})).json();
    assert.deepEqual(evidence.errors,[]);
    assert.ok(!Object.keys(evidence.counters).some(key=>key.startsWith('unexpected:')));
    const automaticRuntimeCalls=Object.entries(evidence.counters)
     .filter(([name])=>/^unexpected:runtime-(start|send|control)$/.test(name))
     .reduce((total,[,count])=>total+count,0);
    assert.equal(automaticRuntimeCalls,0,'Observed NoModel entry counters, not a literal qualification label');
    return {artifactId:identity,tuple:reference,bytes:large.bytes,originalSha256:large.sha256,
     userClick:true,physicalPixels:true,pausedFreeze:true,defaultPaused:true,reducedMotion:true,
     clientId:current.client.id,presentation:{initial,paused,defaultDraws,pauseDraws,
      beforeMotionDraws,motionDraws,pausedMotionPixels:restoredPixels},
     inlineGeneration,inlineReadback: inlineAfter.result.browserObservation,sourceRemoved:true,
     reload:true,appCloseReopen:true,peerPreserved:true,versions:1,automaticRuntimeCalls,counters:evidence.counters};
   }catch(error){
    primaryError=error;
    // Save the assertion before cleanup, which may itself wait for a receipt.
    try{await writeEvidence(join(evidenceDir,'gif-canvas-primary-error.json'),errorEvidence(error))}
    catch(writeError){error.cleanupErrors=[{step:'save primary error evidence',...errorEvidence(writeError)}]}
    throw error;
   }finally{
    const cleanupErrors=[...(primaryError?.cleanupErrors||[])];
    const cleanup=async(label,work)=>{
     try{await work()}catch(error){cleanupErrors.push({step:label,...errorEvidence(error)})}
    };
    releaseOpen();
    if(!page.isClosed())await cleanup('release action route',()=>page.unroute('**/api/actions',holdOpen));
    if(!page.isClosed())await cleanup('clicker unmount and close',()=>unmountAndClose(client));
    if(!peer.page.isClosed()){
     // Restore the peer through the normal action before disposing the test client.
     if(originalPeer.canvas?.kind)await cleanup('restore peer Canvas',()=>action(peer.page,'canvas.select',{id:originalPeer.canvas.id}));
     await cleanup('peer unmount and close',()=>unmountAndClose(peer));
    }
    if(cleanupErrors.length){
     try{await writeEvidence(join(evidenceDir,'gif-canvas-cleanup-errors.json'),cleanupErrors)}
     catch(writeError){cleanupErrors.push({step:'save cleanup error evidence',...errorEvidence(writeError)})}
     if(primaryError)primaryError.cleanupErrors=cleanupErrors;
     else throw Object.assign(new Error('GIF Canvas cleanup failed'),{cleanupErrors});
    }
   }
  });
  await phase('gif-canvas-errors',async()=>{
   if(!saved)throw new CantCheck('GIF setup unavailable for Canvas failure checks');
   const client=await newClient(),{page}=client;
   const inline=page.locator(`[data-message-id="${boot.messageId}"] [data-media-id="${saved.id}"]`);
   const canvasRoot=page.locator('[data-canvas-view="primary"]');
   let releaseEarlier,dirtyHostAdmitted=false,dirtyReceipt,dirtyTarget,primaryError;
   let clickAttempted=false,clickSettled=null;
   const earlierGate=new Promise(done=>{releaseEarlier=done});
   const commandOrder=[],blockerId='gif-canvas-earlier-command';
   const dirtyTrueId=randomUUID(),ownedCommandIds=new Set([blockerId,dirtyTrueId]);
   // Phase-local diagnostics observe the real queue, never replace its effects.
   // Only identifiers, lifecycle flags and revisions leave the synthetic page.
   const network=[],requests=new WeakMap(),started=Date.now();
   let droppedNetwork=0,pinnedRequest=null,pinnedId=null,bodyObservers=0;
   const token=value=>typeof value==='string'&&/^[a-zA-Z0-9_.:-]{1,120}$/.test(value)?value:null;
   const number=value=>Number.isFinite(value)?value:null;
   const onRequest=request=>{
    const url=new URL(request.url()),method=request.method();
    if(!(url.pathname==='/api/actions'&&method==='POST'||url.pathname==='/api/state'&&method==='GET'))return;
    let body;try{body=method==='POST'?request.postDataJSON():null}catch{}
    const row={atMs:Date.now()-started,route:url.pathname,method,id:token(body?.id),action:token(body?.action),request:'issued'};
    requests.set(request,row);network.push(row);
    if(row.id===pinnedId)pinnedRequest=row;
    if(network.length>64){network.shift();droppedNetwork++}
   };
   const onResponse=response=>{
    const row=requests.get(response.request());if(!row)return;
    row.status=response.status();row.headersAtMs=Date.now()-started;
   };
   const onFinished=request=>{
    const row=requests.get(request);if(!row)return;
    row.request='body-finished';row.finishedAtMs=Date.now()-started;
    if(row.method!=='POST'||!ownedCommandIds.has(row.id))return;
    if(bodyObservers>=8){row.receipt='observer-cap';return}
    bodyObservers++;
    // This observer is not awaited by application dispatch or failure capture.
    request.response().then(async response=>{
     const length=await response.headerValue('content-length');
     if(length!==null&&Number(length)>1024*1024){row.receipt='omitted-large';return}
     const body=await response.body();
     if(body.length>1024*1024){row.receipt='omitted-large';return}
     let value;try{value=JSON.parse(body.toString('utf8'))}catch{row.receipt='invalid-json';return}
     row.receipt='observer-parsed';row.accepted=typeof value.accepted==='boolean'?value.accepted:null;
     row.stateRevision=number(value.stateRevision);row.hostInstanceId=token(value.hostInstanceId);
     row.embeddedState=!!value.state;row.parsedAtMs=Date.now()-started;
    }).catch(()=>{row.receipt='unavailable'}).finally(()=>{bodyObservers--});
   };
   const onFailed=request=>{
    const row=requests.get(request);if(row){row.request='failed';row.failedAtMs=Date.now()-started}
   };
   page.on('request',onRequest);page.on('response',onResponse);
   page.on('requestfinished',onFinished);page.on('requestfailed',onFailed);
   const initializeCommands=()=>receiptDeadline('Initialize command diagnostic',()=>page.evaluate(()=>{
    const rows=[],pending=new Map();let dropped=0;
    const token=value=>typeof value==='string'&&/^[a-zA-Z0-9_.:-]{1,120}$/.test(value)?value:null;
    const revision=value=>Number.isFinite(value)?value:null;
    const observed=()=>{
     try{const state=window.amplifier.getState();return {status:'observed',revision:revision(state.revision),hostInstanceId:token(state.client?.hostInstanceId)}}
     catch{return {status:'unavailable',revision:null,hostInstanceId:null}}
    };
    window.__gifCommandDiagnostic={rows,pending,get dropped(){return dropped},observed,token};
    window.__gifDispatch=(name,args={},id=crypto.randomUUID())=>{
     const row={id,action:name,dirty:typeof args.dirty==='boolean'?args.dirty:null,calledAt:performance.now(),settlement:'pending',before:observed()};
     if(rows.length<64)rows.push(row);else dropped++;
     let promise;
     try{promise=window.amplifier.dispatch(name,args,{id})}
     catch(error){row.settlement='threw';row.after=observed();throw error}
     pending.set(id,promise);
     const settled=()=>{pending.delete(id);row.settledAt=performance.now();row.after=observed()};
     promise.then(value=>{
      row.settlement='fulfilled';row.accepted=typeof value.accepted==='boolean'?value.accepted:null;
      row.stateRevision=revision(value.stateRevision);row.hostInstanceId=token(value.hostInstanceId);row.embeddedState=!!value.state;
      settled();
     },()=>{row.settlement='rejected';settled()}).catch(()=>{row.observerError=true});
     return promise; // Retain the original, not the deadline race or observer.
    };
   }));
   const phaseAction=async(_page,name,args={})=>{
    const id=randomUUID();
    ownedCommandIds.add(id);
    if(name==='canvas.views.dirty'&&args.dirty===false&&!pinnedId)pinnedId=id;
    return receiptDeadline(`App action ${name}`,()=>page.evaluate(([name,args,id])=>window.__gifDispatch(name,args,id),[name,args,id]));
   };
   const captureCommands=async label=>{
    const captureStartedAtMs=Date.now()-started;
    let observed={status:'unavailable'};
    try{
     observed=await receiptDeadline('Read bounded command diagnostic',()=>page.evaluate(()=>{
      const d=window.__gifCommandDiagnostic,state=window.amplifier.getState();
      const view=state.canvasWorkspace?.views?.find(row=>row.viewId==='primary');
      return {status:'observed',state:d.observed(),commands:d.rows,droppedCommands:d.dropped,
       pendingIds:[...d.pending.keys()].slice(-64),
       fetchIssued:window.__actionPosts.slice(-64).map(row=>({id:d.token(row.id),action:d.token(row.action)})),
       primary:{dirty:typeof view?.dirty==='boolean'?view.dirty:null,
        resourceRevision:Number.isFinite(view?.resourceRevision)?view.resourceRevision:null,
        generation:Number.isFinite(view?.generation)?view.generation:null}};
     }));
    }catch{}
    const snapshot=JSON.parse(JSON.stringify({label,captureStartedAtMs,capturedAtMs:Date.now()-started,
     clickOwned:{attempted:clickAttempted,settled:clickSettled,primaryFailed:!!primaryError},
     pinnedId,pinnedRequest,network,droppedNetwork,bodyObservers,observed}));
    await writeEvidence(join(evidenceDir,`gif-canvas-commands-${label}.json`),snapshot);
   };
   const pendingSettled=()=>page.evaluate(async()=>{
    const d=window.__gifCommandDiagnostic;
    if(!d)throw Error('Command diagnostic was not initialized');
    await Promise.allSettled([...d.pending.values(),window.__gifEarlier,window.__gifDirty]);
    if(d.pending.size||d.dropped)throw Error('Tracked command settlement remains unknown');
   });
   const holdEarlier=async route=>{
    const body=route.request().postDataJSON();
    if(body?.id===blockerId){
     commandOrder.push('earlier-issued');await earlierGate;return route.continue();
    }
    if(body?.action==='canvas.views.dirty'&&body.args.dirty){
     commandOrder.push('dirty-issued');
     const response=await route.fetch(),receipt=await response.json();
     dirtyReceipt={status:response.status(),accepted:receipt.accepted};
     dirtyHostAdmitted=dirtyReceipt.status===200&&dirtyReceipt.accepted===true;
     commandOrder.push(dirtyHostAdmitted?'dirty-admitted':'dirty-refused');
     return route.fulfill({response});
    }
    if(body?.action==='canvas.openGif'){
     commandOrder.push(dirtyHostAdmitted?'open-after-dirty':'open-before-dirty');
     await earlierGate;
    }
    return route.continue();
   };
   try{
    await initializeCommands();
    await readyFigure(page,saved.id);
    await expect(inline).toHaveAttribute('data-media-state','ready');
    for(const figure of await page.locator('[data-message-id] [data-media-id]').all()){
     if(await figure.locator('canvas').count())await expect(figure).toHaveAttribute('data-media-state','ready');
    }
    await phaseAction(page,'canvas.show',{kind:'text',title:'Dirty primary',content:'Keep the mounted dirty view'});
    const current=await page.evaluate(()=>window.amplifier.getState());
    const view=current.canvasWorkspace.views.find(view=>view.viewId==='primary');
    const target=Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
    dirtyTarget=target;
    await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Queued GIF unsent draft');
    await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Queued GIF unsent draft');
    const before=await page.evaluate(()=>window.amplifier.getState());
    await inline.scrollIntoViewIfNeeded();
    await page.route('**/api/actions',holdEarlier);
    // Hold an ACTUAL earlier command in App's commandQueue. Do not await dirty
    // before the click: its declaration must remain queued behind that command.
    await page.evaluate(blockerId=>{
     window.__gifEarlier=window.__gifDispatch('canvas.views.inspect',{},blockerId);
     window.__gifEarlier.catch(()=>{});
    },blockerId);
    await expect.poll(()=>commandOrder[0]).toBe('earlier-issued');
    const postStart=await page.evaluate(()=>window.__actionPosts.length);
    await page.evaluate(([target,id])=>{
     window.__gifDirty=window.__gifDispatch('canvas.views.dirty',{...target,dirty:true},id);
     window.__gifDirty.catch(()=>{});
    },[target,dirtyTrueId]);
    const refused=page.waitForResponse(response=>response.request().method()==='POST'&&
     new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action==='canvas.openGif');
    refused.catch(()=>{}); // Preserve its eventual rejection until the ordered receipt checks await it.
    clickAttempted=true;clickSettled=null;
    await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();
    await expect(inline.getByRole('button',{name:'Opening in Canvas…',exact:true})).toBeDisabled();
    // fetch is observed synchronously at issuance. No sleep or absence timeout
    // is used as FIFO proof, and no fixture assigns a Canvas or relaxes its guard.
    assert.deepEqual(await page.evaluate(start=>window.__actionPosts.slice(start)
     .filter(row=>['canvas.views.dirty','canvas.openGif'].includes(row.action)),postStart),[]);
    assert.deepEqual(commandOrder,['earlier-issued']);
    assert.equal((await page.evaluate(()=>window.amplifier.getState())).canvas.id,before.canvas.id);
    releaseEarlier();
    await receiptDeadline('Queued Canvas edit receipts',()=>page.evaluate(()=>Promise.all([window.__gifEarlier,window.__gifDirty])));
    const rejected=await refused;
    assert.equal(rejected.status(),409);assert.equal((await rejected.json()).accepted,false);
    assert.deepEqual(dirtyReceipt,{status:200,accepted:true});
    assert.deepEqual(commandOrder,['earlier-issued','dirty-issued','dirty-admitted','open-after-dirty']);
    await page.unroute('**/api/actions',holdEarlier);
    await expect(inline.getByRole('alert')).toContainText('Finish or cancel');
    const after=await page.evaluate(()=>window.amplifier.getState());
    assert.equal(after.canvas.id,before.canvas.id);assert.deepEqual(after.canvasArtifacts,before.canvasArtifacts);
    assert.equal(after.canvas.content,before.canvas.content);assert.equal(after.canvas.open,before.canvas.open);
    assert.deepEqual(after.view.canvasDraft,before.view.canvasDraft,'No Library or draft-view selection was changed');
    assert.equal(after.canvasWorkspace.views.find(view=>view.viewId==='primary').dirty,true);
    await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Queued GIF unsent draft');
    await phaseAction(page,'canvas.views.dirty',{...target,dirty:false});
    // Hold the REAL native first decode. Mounted is not ready; closing owns
    // pending-frame cleanup and may not leak a late frame into a replacement.
    await page.evaluate(()=>{window.__mediaProbe.hold=true});
    clickAttempted=true;clickSettled=null;
    await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();
    const figure=canvasRoot.locator(`[data-media-id="${saved.id}"]`);
    await expect(figure).toHaveAttribute('data-media-state','loading');
    await expect.poll(async()=>(await probeState(page)).heldCount).toBeGreaterThan(0);
    const pending=await page.evaluate(()=>window.amplifier.getState());
    assert.equal(pending.canvas.kind,'gif');
    assert.notEqual(pending.canvasWorkspace.views.find(view=>view.viewId==='primary').activation?.status,'ready');
    const heldDecoder=(await probeState(page)).created;
    await phaseAction(page,'canvas.show',{kind:'text',title:'Replacement',content:'No late GIF draw here'});
    await page.evaluate(()=>{window.__mediaProbe.hold=false;window.__mediaProbe.held.splice(0).forEach(done=>done())});
    await expect(figure).toHaveCount(0);
    await expect.poll(async()=>page.evaluate(id=>window.__mediaProbe.perDecoder[id].live,heldDecoder)).toBe(0);
    await expect.poll(async()=>page.evaluate(id=>window.__mediaProbe.perDecoder[id].closed,heldDecoder)).toBe(true);
    assert.equal(await page.evaluate(id=>window.__mediaProbe.draws.filter(row=>row.decoderId===id).length,heldDecoder),0);
    // Decoder failure in the same shared player must become Canvas error,
    // never a generic mounted-ready report or an outputs.mediaReport.
    await page.route('**/api/outputs/*/media?*',async route=>{
     const url=new URL(route.request().url());
     if(url.pathname!==`/api/outputs/${saved.id}/media`||url.searchParams.has('download'))return route.continue();
     const response=await route.fetch();assert.equal(response.status(),200);
     await route.fulfill({response,body:Buffer.from('synthetic undecodable GIF')});
    });
    clickAttempted=true;clickSettled=null;
    await inline.getByRole('button',{name:'Open in Canvas',exact:true}).click();
    await expect(figure).toHaveAttribute('data-media-state','error');
    await expect.poll(async()=>page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views
     .find(view=>view.viewId==='primary').activation?.status)).toBe('error');
    await expect(figure.getByRole('link',{name:'Download original'})).toHaveCount(1);
    await page.unroute('**/api/outputs/*/media?*');
    await captureCommands('completed');
    return {commandOrder,queuedDirtyAdmission:true,dirtyRejectedBeforeMutation:true,visibleError:true,actualDecodeReadiness:true,
     replacementCleanup:true,lateFramesOwned:true,decoderError:'error',mountedReady:false};
   }catch(error){
    primaryError=error;
    // A cleanup receipt can itself be unsettled; retain the first failure now.
    try{await writeEvidence(join(evidenceDir,'gif-canvas-errors-primary.json'),errorEvidence(error))}
    catch(writeError){error.cleanupErrors=[{step:'save primary error evidence',...errorEvidence(writeError)}]}
    try{await captureCommands('before-cleanup')}
    catch{error.cleanupErrors=[...(error.cleanupErrors||[]),{step:'save command diagnostic',reason:'Diagnostic write failed'}]}
    throw error;
   }finally{
    const cleanupErrors=[...(primaryError?.cleanupErrors||[])];
    const cleanup=async(label,work)=>{
     try{await receiptDeadline(label,work);return true}
     catch(error){cleanupErrors.push({step:label,...errorEvidence(error)});return false}
    };
    releaseEarlier();
    if(!page.isClosed()){
     // Let the released route finish before unregistering its handler.
     let settled=await cleanup('settle latest Canvas receipts',pendingSettled);
     await cleanup('release action route',()=>page.unroute('**/api/actions',holdEarlier));
     // UI clicks use the component's dispatch, not __gifDispatch. A primary
     // failure after any click leaves that command conservatively unknown.
     // Only on the successful path observe MediaFigure's post-dispatch finally
     // binding; HTTP/body completion alone is never application settlement.
     if(clickAttempted&&!primaryError)clickSettled=await cleanup('confirm UI-owned Canvas open settlement',async()=>{
      await expect(inline.getByRole('button',{name:'Open in Canvas',exact:true})).toBeEnabled();
     });
     if(!gifCanvasCleanupAllowed(clickAttempted,!!primaryError,clickSettled)){
      settled=false;
      blockedApplicationCleanup.add(page);
      cleanupErrors.push({step:'click-owned Canvas open',reason:'Settlement unconfirmed; application cleanup not attempted'});
     }
     // Do not enqueue more app commands behind a receipt that never settled.
     if(settled)settled=await cleanup('clear owned dirty view',()=>page.evaluate(async target=>{
      const view=window.amplifier.getState().canvasWorkspace.views.find(view=>view.viewId==='primary');
      if(target&&view?.dirty&&['resourceId','resourceRevision','generation'].every(key=>view[key]===target[key]))
       await window.__gifDispatch('canvas.views.dirty',{...target,dirty:false});
     },dirtyTarget));
     await cleanup('release held decoder frames',()=>page.evaluate(()=>{window.__mediaProbe.hold=false;window.__mediaProbe.held.splice(0).forEach(done=>done())}));
     if(settled)settled=await cleanup('confirm latest receipt settlement',pendingSettled);
     if(settled)settled=await cleanup('unmount and close owned client',()=>unmountAndClose(client,phaseAction));
     if(!settled){
      blockedApplicationCleanup.add(page);
      cleanupErrors.push({step:'application cleanup',reason:'Not attempted or not confirmed; no further mutations behind unresolved dispatch'});
     }
     if(!page.isClosed())await cleanup('save pre-close command diagnostic',()=>captureCommands('after-cleanup'));
     if(!page.isClosed())await cleanup('close unsettled owned page',()=>page.close());
     if(page.isClosed())pages.delete(page);
    }
    page.off('request',onRequest);page.off('response',onResponse);
    page.off('requestfinished',onFinished);page.off('requestfailed',onFailed);
    if(cleanupErrors.length){
     try{await writeEvidence(join(evidenceDir,'gif-canvas-errors-cleanup.json'),{errors:cleanupErrors,pageClosed:page.isClosed()})}
     catch(writeError){cleanupErrors.push({step:'save cleanup evidence',...errorEvidence(writeError)})}
     if(primaryError)primaryError.cleanupErrors=cleanupErrors;
     else throw Object.assign(new Error('GIF Canvas failure-check cleanup failed'),{cleanupErrors});
    }
   }
  });
  await phase('endpoints',async()=>{
   if(!primary||!saved)throw new CantCheck('GIF setup unavailable');
   const {page}=primary,figure=await readyFigure(page,saved.id);
   const link=await figure.getByRole('link',{name:'Download original'}).getAttribute('href');
   const url=new URL(link,boot.url);url.searchParams.delete('download');
   const download=await page.request.get(link);
   assert.equal(download.status(),200);
   const data=await download.body();assert.equal(createHash('sha256').update(data).digest('hex'),saved.sha256);
   assert.equal(data.length,saved.bytes);
   assert.ok(download.headers()['content-disposition'].startsWith('attachment;'));
   const get=await page.request.get(url.href);assert.equal(get.status(),200);assert.deepEqual(await get.body(),data);
   const head=await page.request.head(url.href);assert.equal(head.status(),200);assert.equal((await head.body()).length,0);
   assert.equal(Number(head.headers()['content-length']),data.length);
   for(const [range,start,end] of [['bytes=2-4',2,4],['bytes=2-',2,data.length-1],['bytes=-3',data.length-3,data.length-1]]){
    const response=await page.request.get(url.href,{headers:{Range:range}});
    assert.equal(response.status(),206);assert.deepEqual(await response.body(),data.subarray(start,end+1));
    assert.equal(response.headers()['content-range'],`bytes ${start}-${end}/${data.length}`);
   }
   assert.equal((await page.request.get(url.href,{headers:{Range:'bytes=0-1,3-4'}})).status(),400);
   assert.equal((await page.request.get(url.href,{headers:{Range:`bytes=${data.length}-`}})).status(),416);
   for(const [key,value] of [['sessionId','foreign'],['messageId','foreign'],['version','99'],['sha256','0'.repeat(64)],['clientId','foreign']]){
    const wrong=new URL(url);wrong.searchParams.set(key,value);
    const rejected=await page.request.get(wrong.href,{headers:{Range:'malformed'}});
    assert.equal(rejected.status(),key==='clientId'?409:400);
    const payload=await rejected.json();assert.equal(payload.accepted,false);
    assert.equal(typeof payload.error,'string');
    if(key!=='clientId')assert.ok(payload.error.startsWith('Invalid request: '));
   }
   const anonymous=await request.newContext();
   try{assert.ok([401,403].includes((await anonymous.get(url.href)).status()))}finally{await anonymous.dispose()}
   const unlinked=await control(page,'mutate',{name:'gif',operation:'unlink'});
   assert.equal(unlinked.accepted,true);
   const rejected=await page.request.get(url.href);assert.equal(rejected.status(),400);
   assert.deepEqual(await rejected.json(),{accepted:false,error:'Invalid request: Choose the exact linked assistant media version and hash.'});
   await expect(figure).toHaveCount(0);
   return {exactOriginalSha256:saved.sha256,bytes:data.length,GET:200,HEAD:200,Range:206,authRejected:true,
    wrongTuplesRejected:true,unlinkRejected:true};
  });
  await phase('layout',async()=>{
   if(!primary)throw new CantCheck('Browser setup unavailable');
   const {page}=primary;
   // PNG output registry remains unchanged; gallery/pixel read stays usable.
   const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
   const png=await page.request.post(boot.url+'/api/actions',{headers:{'X-Amplifier-Client':clientId},
    data:{action:'outputs.image',args:{sessionId:boot.sessionId,id:boot.pngId,sha256:boot.pngSha256}}});
   assert.equal(png.status(),200);
   const gallery=page.locator('.a-inline-gallery').first();
   await gallery.scrollIntoViewIfNeeded();
   await expect(gallery.locator('.a-inline-image img')).toBeVisible();
   assert.ok(await gallery.locator('.a-inline-image img').evaluate(node=>node.complete&&node.naturalWidth===4));
   const boxes=[];
   for(const width of [1280,390])for(const scheme of ['light','dark']){
    await page.setViewportSize({width,height:width===390?844:950});
    await action(page,'view.update',{patch:{scheme}});
    await page.locator('[data-media-id]').first().scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    const overflow=await page.locator('[data-media-id]').evaluateAll(nodes=>nodes.some(node=>{
     const r=node.getBoundingClientRect();return r.left<0||r.right>innerWidth||node.scrollWidth>node.clientWidth+1;
    }));
    assert.equal(overflow,false);
    await page.screenshot({path:join(evidenceDir,`layout-${width}-${scheme}.png`)});
    boxes.push({width,scheme,overflow});
   }
   await page.setViewportSize({width:1280,height:950});
   const play=page.getByRole('button',{name:'Play GIF',exact:true}).first();
   await play.scrollIntoViewIfNeeded();
   const id=await play.evaluate(node=>node.closest('[data-media-id]').dataset.mediaId),figure=figureFor(page,id);
   await play.focus();await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','playing');
   await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','paused');
   return {boxes,keyboard:true,pngGallery:true};
  });
  await phase('keyboard-200-percent',async()=>{
   if(!primary)throw new CantCheck('Browser setup unavailable');
   const {page}=primary;
   const inputStart=nativeInput.length,proof={};
   await page.bringToFront();
   const reset=async()=>{
    if(guestNative){nativeKey('Control_L','0');await expect.poll(()=>page.evaluate(()=>devicePixelRatio)).toBe(1)}
    else await page.keyboard.press('Control+0');
   };
   try{
   // Browser keyboard zoom, not CSS transform masquerading as zoom.
   await reset();proof.baseline=await zoomMetrics(page);
   const before=await page.evaluate(()=>devicePixelRatio);
   let after=before;
   for(let n=0;n<8&&after/before<1.99;n++){
    if(guestNative){
     nativeKey('Control_L','equal');
     await expect.poll(()=>page.evaluate(()=>devicePixelRatio)).not.toBe(after);
    }else await page.keyboard.press('Control++');
    after=await page.evaluate(()=>devicePixelRatio);
   }
   if(after/before<1.99||after/before>2.01)throw new CantCheck('This browser does not apply true 200% keyboard page zoom');
   proof.zoomed=await zoomMetrics(page);
   if(guestNative){
    assert.equal(before,1);assert.equal(after,2);assert.equal(proof.zoomed.visualScale,1);
    assert.equal(proof.zoomed.outerWidth,proof.baseline.outerWidth);
    assert.equal(proof.zoomed.screenWidth,proof.baseline.screenWidth);
   }
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const play=page.getByRole('button',{name:'Play GIF',exact:true}).first();
   await play.scrollIntoViewIfNeeded();
   const id=await play.evaluate(node=>node.closest('[data-media-id]').dataset.mediaId),figure=figureFor(page,id);
   await play.focus();
   if(guestNative)nativeKey('space');else await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','playing');
   if(guestNative)nativeKey('space');else await page.keyboard.press('Space');
   await expect(figure).toHaveAttribute('data-playback','paused');
   await page.screenshot({path:join(evidenceDir,'keyboard-200-percent.png')});
   Object.assign(proof,{zoomRatio:after/before,keyboard:true,tabTraversalQualified:false});
   return proof;
   }finally{await reset();proof.reset=await zoomMetrics(page);proof.input=nativeInput.slice(inputStart)}
  },{required:false});
 }
}catch(error){
 await record('harness','FAIL',{reason:String(error.message).replaceAll(root,'<source>/')});
}finally{
 await phase('cleanup',async()=>{
  const decoderEvidence=[],errors=[];
  // Inspect/unmount every remaining live realm BEFORE closing it. Closing the
  // browser without observing frame disposal is not cleanup proof.
  for(const context of [...contexts]){
   for(const page of context.pages()){
    // A real Chrome exits when its last tab closes. Keep its initial blank tab
    // alive until trace retention and the explicit observed Browser.close.
    if(page===nativeBrowser?.keepAlive)continue;
    if(page.isClosed())continue;
    try{
     if(blockedApplicationCleanup.has(page))throw Error('Application cleanup remains incomplete; final sweep must not enqueue behind unresolved dispatch');
     if(await page.evaluate(()=>!!window.__mediaProbe)){
      await page.evaluate(()=>{window.__mediaProbe.hold=false;window.__mediaProbe.held.splice(0).forEach(done=>done())});
      await action(page,'session.create',{title:'Final synthetic cleanup'});
      await expect.poll(async()=>{const p=await probeState(page);return p.frames+p.decoders+p.active}).toBe(0);
      decoderEvidence.push(await probeState(page));
     }
    }catch(error){errors.push('Decoder cleanup: '+error.message)}
    finally{
     try{await page.close();assert.equal(page.isClosed(),true);pages.delete(page)}
     catch(error){errors.push('Page close: '+error.message)}
    }
   }
   try{
    await context.tracing.stop({path:join(evidenceDir,`browser-trace-${Date.now()}.zip`)});
    if(!nativeBrowser)await context.close();contexts.delete(context);
   }catch(error){errors.push('Context close: '+error.message)}
  }
  let nativeCleanup;
  if(browser)try{
   if(nativeBrowser)nativeCleanup=await nativeBrowser.close();else await browser.close();
   assert.equal(browser.isConnected(),false);
  }catch(error){errors.push('Browser close: '+error.message)}
  if(guestNative)try{await writeEvidence(join(evidenceDir,'native-input.json'),nativeInput)}
  catch(error){errors.push('Native input evidence: '+error.message)}
  if(fixture&&fixture.exitCode===null){
   fixture.kill('SIGTERM');
   const timer=setTimeout(()=>{forcedKill=true;fixture.kill('SIGKILL')},10000);
   try{await exit}finally{clearTimeout(timer)}
  }
  const processResult=exit?await exit:null;
  const evidence={decoderEvidence,errors,pageErrors,nativeCleanup,pageClosed:pages.size===0,contextClosed:contexts.size===0,
   browserDisconnected:!browser?.isConnected(),fixture:fixtureRows.filter(row=>row.phase),process:processResult,forcedKill};
  await writeEvidence(join(evidenceDir,'cleanup-observations.json'),evidence);
  assert.equal(forcedKill,false,'Forced kill is a cleanup failure, never success');
  if(fixture){
   assert.equal(processResult.code,0);
   assert.equal(fixtureRows.find(row=>row.phase==='fixture-cleanup')?.closed,true);
   assert.equal(fixtureRows.find(row=>row.phase==='fixture-process')?.temporaryRemoved,true);
  }
  assert.equal(contexts.size,0);assert.equal(pages.size,0);
  assert.deepEqual(errors,[]);
  assert.deepEqual(pageErrors,[]);
  return evidence;
 });
 const failed=receipts.some(row=>row.status==='FAIL'||row.required&&row.status==='CANT_CHECK');
 const remainingPhysical=receipts.filter(row=>physicalNames.includes(row.phase)&&row.status!=='PASS');
 const fullScenario=selectedPhases===null;
 const corePassed=receipts.filter(row=>row.required).every(row=>row.status==='PASS');
 await writeEvidence(join(evidenceDir,'result.json'),{qualified:fullScenario&&!failed&&remainingPhysical.length===0,fullScenario,
  coreChecks:fullScenario?(corePassed?'PASS':'NOT_QUALIFIED'):'NOT_RUN',
  physicalChecks:fullScenario?(remainingPhysical.length?'NOT_QUALIFIED':'PASS'):'NOT_RUN',
  remainingPhysical:remainingPhysical.map(({phase,status,evidence})=>({
   phase,status:'NOT_QUALIFIED',observedStatus:status,reason:evidence.reason})),receipts,
  nativeMemoryGuarantee:false});
 process.exitCode=failed?1:0;
}
