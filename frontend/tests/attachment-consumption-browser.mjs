// Real built client + host action/event/private-draft paths; synthetic runtime.
// No mocked consumed-ID selector, invented state revisions, model or live account.
import './composer-test-helpers.mjs';
import {installAttachmentObserver} from './attachment-consumption-observer.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {bounded,withActionRoute,observed,resultOf,combinedFailure} from './chat-reference-harness.mjs';
import {boundaryRead,openBoundaryHandle,closeBoundaryHandle,boundaryIdentity,boundaryRequests,BOUNDARY_OBSERVATION_MS} from './browser-boundary-evidence.mjs';
import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks/attachment-consumption');
const fixtureRoot=process.env.AMPLIFIER_TEST_FIXTURE_ROOT||join(root,'tests/fixtures');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDioAAAAASUVORK5CYII=','base64');
const names=['detector-countercheck','accepted-two-cycle','response-before-frame','failed-recovery','unknown-reload'];
const report={schemaVersion:1,scenario:'attachment-consumption',runtime:'synthetic',liveProvider:false,
 negativeControl:process.env.AMPLIFIER_TEST_PRECOMMIT_NOTIFY==='1'?'precommit-work-notification':null,
 cases:names.map(id=>({id,status:'unrun'})),errors:[],routeLifecycle:[],traces:[],observations:[]};
await mkdir(output,{recursive:true});
const save=()=>writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
await save();
let browser,base,primary,stderr='',fixtureExit;
const secondaryErrors=[],seenFailures=new Set();
function rememberFailure(error,phase,caseId){
 const failures=error instanceof AggregateError?[...(error.cause?[error.cause]:[]),...error.errors]:[error];
 for(const failure of failures){
  if(failure instanceof AggregateError){rememberFailure(failure,phase,caseId);continue;}
  if(seenFailures.has(failure))continue;seenFailures.add(failure);
  const detail={phase,caseId,name:failure.name,message:failure.message,stack:failure.stack};
  if(!primary){primary=failure;report.primary=detail;}
  else {secondaryErrors.push(failure);(report.secondaryErrors??=[]).push(detail);}
 }
}
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(fixtureRoot,'attachment_consumption_ui_server.py')],{stdio:['ignore','pipe','pipe']});
const ownedStop=ownFixture(fixture,{label:'Attachment consumption fixture'});
const stop=async()=>{fixtureExit=await ownedStop();report.fixtureExit=fixtureExit;};
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});

async function readiness(){
 return new Promise((resolveReady,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',fail);};
  const fail=error=>{cleanup();reject(error);};
  const exited=(code,signal)=>fail(Error('Fixture exited before readiness: '+(code??signal)+' '+stderr));
  const data=chunk=>{pending+=chunk;for(let end;(end=pending.indexOf('\n'))>=0;){
   const line=pending.slice(0,end);pending=pending.slice(end+1);let row;try{row=JSON.parse(line)}catch{continue}
   if(row.fixture!=='attachment-consumption')continue;
   if(row.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(row.base))return fail(Error('Wrong fixture identity'));
   cleanup();resolveReady(row);return;
  }if(pending.length>8000)fail(Error('Readiness output exceeded bound'));};
  const timer=setTimeout(()=>fail(Error('Fixture startup deadline: '+stderr)),30000);
  fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',fail);
 });
}
const call=(page,action,args={})=>bounded(page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]),15000,'App action deadline: '+action);
const state=page=>bounded(page.evaluate(()=>window.amplifier.getState()),5000,'State read deadline');
const trace=page=>bounded(page.evaluate(()=>window.attachmentTrace.read()),5000,'Trace read deadline');
async function control(page,args){const response=await page.request.post(base+'/fixture/attachment-consumption',{data:args,timeout:7000});assert.equal(response.status(),200);return response.json();}
async function inspect(page){const current=await state(page);const response=await page.request.get(base+'/fixture/attachment-consumption?clientId='+encodeURIComponent(current.client.id),{timeout:5000});assert.equal(response.status(),200);return response.json();}
async function observation(page,label){const data=await inspect(page);report.observations.push({label,...data});return data;}
async function frames(page,count=3){await bounded(page.evaluate(count=>new Promise(resolveFrames=>{const next=()=>--count<=0?resolveFrames():requestAnimationFrame(next);requestAnimationFrame(next);}),count),5000,'Frame observation deadline');}
async function cleanTrace(page,label){await frames(page);const data=await trace(page);report.traces.push({label,...data});assert.equal(data.capped,false,'Trace must never silently truncate');assert.deepEqual(data.violations,[],label+': consumed attachments reappeared at DOM/render boundary');}
async function upload(page,name){
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name,mimeType:'image/png',buffer:png});
 await expect.poll(async()=>{const s=await state(page);return s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments?.find(x=>x.name===name)?.id;},{timeout:10000}).toMatch(/^[a-f0-9]{32}$/);
 const s=await state(page);return s.sessions.find(x=>x.id===s.selectedSessionId).draftAttachments.find(x=>x.name===name);
}
async function original(page,row){const response=await page.request.get(base+row.url,{timeout:5000});assert.equal(response.status(),200);assert.equal(hash(await response.body()),hash(png),'Original attachment bytes unchanged');}
async function watch(page,owner,ids){await page.evaluate(([owner,ids])=>window.attachmentTrace.watch(owner,ids),[owner,ids]);}
async function waitRuntimeSend(page,owner,previous){
 await expect.poll(async()=>{const data=await inspect(page);return data.sends.filter(x=>x.sessionId===owner).length;},{timeout:10000}).toBe(previous+1);
 return (await inspect(page)).sends.filter(x=>x.sessionId===owner).at(-1).inputId;
}
// Observe only cycle A's existing click and existing ten-second runtime wait.
// No event is cancelled, validity method invoked, action dispatched or retried.
async function observeFirstSend(page,owner,attachmentId){
 const data={attempt:'accepted-two-cycle-A',owner,attachmentId,stage:'setup',network:[],snapshots:[],errors:[],omitted:0,
  limits:{requests:8,events:24,snapshots:4,responseBytes:65536,outboxBytes:65536,optionalReadMs:BOUNDARY_OBSERVATION_MS},
  limitation:'Passive reads can affect timing. Deadlines are not cancellation; late setup gets bounded cleanup without rewriting its unknown receipt. A lost handle reply may leave listener removal unconfirmed until page close.'};
 report.firstSendBoundary=data;
 const id=value=>typeof value==='string'&&/^[a-zA-Z0-9:_-]{1,200}$/.test(value)?value:null;
 const receipt=row=>{
  if(!row||typeof row!=='object')return null;
  return {accepted:typeof row.accepted==='boolean'?row.accepted:null,
   inputId:id(row.inputId),sessionId:id(row.sessionId),
   revision:Number.isSafeInteger(row.revision)?row.revision:null,
   delivery:['sending','accepted','failed','unknown','queued_for_update','held'].includes(row.delivery)?row.delivery:null,
   code:['invalid_response','delivery_uncertain','stale_revision','runtime_unavailable'].includes(row.code)?row.code:row.code?'other':null,
   errorPresent:!!row.error};
 };
 const fault=phase=>{if(data.errors.length<8)data.errors.push(phase);else data.omitted++};
 let active=false,closed=false,sequence=0,dom,finished;
 const requests=new WeakMap();
 const onRequest=request=>{
  if(!active||closed)return;
  try{
   const url=new URL(request.url());
   if(url.origin!==new URL(page.url()).origin||url.pathname!=='/api/actions'||request.method()!=='POST')return;
   const text=request.postData();if(!text||text.length>32768){fault('action-body-unavailable');return}
   const raw=JSON.parse(text),body=raw?.action==='shell.command'?raw.args:raw;
   if(body?.action!=='conversation.send')return;
   if(data.network.length>=data.limits.requests){data.omitted++;return}
   const args=body.args??{},row={sequence:++sequence,stage:data.stage,action:body.action,commandId:id(raw.id??body.id),
    sessionId:id(args.sessionId),identityMatch:boundaryIdentity({sessionId:id(args.sessionId)},{sessionId:owner}),
    exactFixtureText:args.text==='Submit A',
    exactAttachmentBatch:Array.isArray(args.attachmentIds)&&args.attachmentIds.length===1&&args.attachmentIds[0]===attachmentId,
    attachmentCount:Array.isArray(args.attachmentIds)?args.attachmentIds.length:null,responseState:'not-observed'};
   requests.set(request,row);data.network.push(row);
  }catch{fault('request-observation-unavailable')}
 };
 const onResponse=response=>{
  try{
  const row=requests.get(response.request());if(!row||closed)return;
  row.status=response.status();row.responseSequence=++sequence;
  const length=response.headers()['content-length'];
  if(!/^\d+$/.test(length??'')||Number(length)>data.limits.responseBytes){row.responseState='body-size-unavailable-or-over-limit';return}
  row.responseState='body-pending';
  void response.body().then(bytes=>{
   if(closed)return;
   if(bytes.length>data.limits.responseBytes){row.responseState='body-over-limit';return}
   const body=JSON.parse(bytes.toString('utf8'));
   row.response={...receipt(body),result:receipt(body?.result),receipt:receipt(body?.receipt)};
   row.responseState='observed';row.bodySequence=++sequence;
  }).catch(()=>{if(!closed){row.responseState='body-unreadable';fault('response-body-unavailable')}});
  }catch{fault('response-observation-unavailable')}
 };
 const onFailed=request=>{const row=requests.get(request);if(row&&!closed){row.responseState='request-failed';row.failureSequence=++sequence}};
 const snapshot=async stage=>{
  if(closed)return;
  data.stage=stage;const row={sequence:++sequence,stage};
  const observedDom=dom?await boundaryRead(()=>dom.evaluate(value=>value.read())):{status:'observer-unavailable'};
  row.dom=observedDom.status==='observed'?observedDom.value:{status:observedDom.status};
  if(observedDom.status!=='observed')fault(stage+'-dom-'+observedDom.status);
  const observedFixture=await boundaryRead(async()=>{
   const state=await inspect(page),sends=state.sends.filter(value=>value.sessionId===owner);
   return {selectedSessionId:id(state.selectedSessionId),ownerSendCount:sends.length,
    inputIds:sends.slice(0,8).map(value=>id(value.inputId)),inputsOmitted:Math.max(0,sends.length-8)};
  });
  row.fixture=observedFixture.status==='observed'?observedFixture.value:{status:observedFixture.status};
  if(observedFixture.status!=='observed')fault(stage+'-fixture-'+observedFixture.status);
  if(closed)return;
  if(data.snapshots.length<data.limits.snapshots)data.snapshots.push(row);else data.omitted++;
 };
 const finish=failed=>finished??=(async()=>{
  data.endedStage=data.stage;
  active=false;page.off('request',onRequest);page.off('response',onResponse);page.off('requestfailed',onFailed);
  await snapshot(failed?'original-wait-or-click-failed':'runtime-send-observed');
  if(dom){
   const cleanup=await closeBoundaryHandle(dom);
   data.events=cleanup.events.status==='observed'?cleanup.events.value:{status:cleanup.events.status};
   data.handleDisposal=cleanup.disposal;
   if(cleanup.events.status!=='observed')fault('event-cleanup-'+cleanup.events.status);
   if(cleanup.disposal.status!=='observed')fault('handle-disposal-'+cleanup.disposal.status);
  }else{
   data.events={status:'observer-unavailable'};
   data.handleDisposal={status:'unknown'};
  }
  closed=true;
  data.requestObservation=boundaryRequests(data.network,'conversation.send',!!(data.errors.length||data.omitted));
  data.pendingResponseReads=data.network.filter(row=>row.responseState==='body-pending').length;
 })();
 page.on('request',onRequest);page.on('response',onResponse);page.on('requestfailed',onFailed);
 const setup=await openBoundaryHandle(()=>page.evaluateHandle(({owner,attachmentId})=>{
   const id=value=>typeof value==='string'&&/^[a-zA-Z0-9:_-]{1,200}$/.test(value)?value:null;
   const form=document.querySelector('.a-composer'),events=[];let omitted=0,sequence=0;
   const category=node=>node===form?'form':node?.matches?.('[data-part="composer-primary-action"]')?'primary-action':
    node?.matches?.('[role="textbox"]')?'editor':node?.form===form?'form-control':'other';
   const listen=event=>{
    if(event.target!==form&&!form?.contains(event.target))return;
    const row={sequence:++sequence,type:event.type,phase:event.eventPhase===1?'capture':'bubble',
     trusted:event.isTrusted,defaultPrevented:event.defaultPrevented,target:category(event.target),
     submitter:category(event.submitter)};
    if(events.length===24){events.shift();omitted++}events.push(row);
   };
   for(const name of ['click','submit','invalid']){
    document.addEventListener(name,listen,{capture:true,passive:true});
    document.addEventListener(name,listen,{capture:false,passive:true});
   }
   const read=()=>{
    const state=window.amplifier?.getState(),current=document.querySelector('.a-composer');
    const button=current?.querySelector('[data-part="composer-primary-action"]');
    const editor=current?.querySelector('[role="textbox"][aria-label="Message Amplifier"]');
    let outbox={status:'unavailable'};
    try{
     const raw=sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]';
     if(raw.length>65536)outbox={status:'over-limit'};
     else{
      const rows=JSON.parse(raw);if(!Array.isArray(rows))throw Error();
      outbox={status:'observed',count:rows.length,omitted:Math.max(0,rows.length-8),
       identities:rows.slice(0,8).map(row=>({commandId:id(row.commandId),sessionId:id(row.sessionId),
        ownerMatches:row.sessionId===owner,containsAttachment:Array.isArray(row.attachmentIds)&&row.attachmentIds.includes(attachmentId),
        status:['sending','accepted','failed','unknown','queued','queued_for_update'].includes(row.status)?row.status:'other'}))};
     }
    }catch{outbox={status:'unreadable'}}
    return {status:'observed',selectedSessionId:id(state?.selectedSessionId),ownerSelected:state?.selectedSessionId===owner,
     formPresent:!!current,sameForm:current===form,originalFormConnected:!!form?.isConnected,
     formInvalid:current?.matches(':invalid')??null,noValidate:current?.noValidate??null,
     buttonPresent:!!button,buttonDisabled:button?.disabled??null,buttonType:['submit','button','reset'].includes(button?.type)?button.type:null,
     sendAction:button?.dataset.action==='conversation.send',editorPresent:!!editor,
     editorDisabled:editor?.disabled??null,editorReadOnly:editor?.readOnly??null,editorContentEditable:editor?.isContentEditable??null,
     draftLength:(editor?.value??editor?.textContent??'').length,activeCategory:category(document.activeElement),outbox};
   };
   return {read,finish(){
    for(const name of ['click','submit','invalid']){
     document.removeEventListener(name,listen,true);document.removeEventListener(name,listen,false);
    }
    return {records:events,omitted};
   }};
  },{owner,attachmentId}));
 data.setup={status:setup.status};
 if(setup.status==='observed')dom=setup.value;
 else fault('event-observer-setup-'+setup.status);
 await snapshot('before-click');active=true;data.stage='click-pending';
 return {finish,clickResolved(){if(!closed){data.stage='runtime-wait';data.clickResolvedSequence=++sequence}}};
}
async function consumed(page,ids){
 await expect.poll(async()=>{const data=await trace(page);return ids.every(id=>data.rules.find(r=>r.id===id)?.armed);},{timeout:10000}).toBe(true);
}
async function settled(page,command){
 await page.waitForFunction(command=>!JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').some(r=>r.commandId===command),command,{timeout:10000});
}
async function setupPage(){
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.route(url=>url.origin!==base,route=>route.abort('blockedbyclient'));
 await context.addInitScript(installAttachmentObserver);
 await context.addInitScript(()=>{
  const Native=window.WebSocket;
  window.attachmentSockets=[];
  window.attachmentStateRecovery=[];
  const nativeFetch=window.fetch;
  window.fetch=async(...args)=>{
   const url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
   const row=url.pathname==='/api/state'?{url:url.pathname,beforeRevision:window.amplifier?.getState()?.revision}:null;
   if(row)window.attachmentStateRecovery.push(row);
   const response=await nativeFetch(...args);
   if(row){
    const body=await response.clone().json();Object.assign(row,{status:response.status,revision:body.revision,clientId:body.client?.id});
    const gate=window.attachmentFrameGate;
    if(gate?.active){
     row.held=true;
     await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(Error('Recovery response release deadline')),10000);
      gate.recoveries.push(()=>{clearTimeout(timer);row.released=true;resolve();});
     });
    }
   }
   return response;
  };
  // Hold actual state delivery, including HTTP recovery. No payload is edited.
  window.attachmentFrameGate={arm:false,active:false,held:[],events:[],recoveries:[],
   release(){this.arm=false;this.active=false;for(const [handler,event] of this.held.splice(0))handler(event);for(const resolve of this.recoveries.splice(0))resolve();}};
  window.WebSocket=class extends Native{constructor(...args){super(...args);window.attachmentSockets.push(this);
   Object.defineProperty(this,'onmessage',{set(handler){this.addEventListener('message',event=>{
    const gate=window.attachmentFrameGate,frame=JSON.parse(event.data);
    const beforeRevision=window.amplifier?.getState()?.revision;
    if(gate.active&&['state','state-delta'].includes(frame.event)){
     gate.held.push([handler,event]);gate.events.push({kind:'held',frameKind:frame.event,revision:frame.data.revision,beforeRevision});return;
    }
    handler(event);
    const state=window.amplifier?.getState(),session=state?.sessions.find(row=>row.id===state.selectedSessionId);
    const sent=session?.messages.find(row=>row.text==='Response-first submission');
    if(gate.arm&&!gate.identity&&sent?.delivery?.status==='sending'){
     gate.active=true;gate.admission=state.revision;gate.command=sent.inputId;
     gate.identity={clientId:state.client.id,sessionId:session.id,inputId:sent.inputId,
      hostInstanceId:state.client.hostInstanceId,frameKind:frame.event,frameRevision:frame.data.revision,
      beforeRevision,afterRevision:state.revision,rawFrame:frame.data};
    }
   });}});
  }};
 });
 const page=await context.newPage();page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(15000);
 page.on('pageerror',error=>{report.errors.push(error.message);rememberFailure(error,'pageerror');});
 try{
  await page.goto(base);
  await page.waitForFunction(()=>!!window.amplifier?.getState()?.client?.id,null,{timeout:15000});
  // Current release starts a fresh view at adaptive Home. Explicit UI actions
  // select test chats rather than assuming an obsolete initial composer.
  const ownerResult=await call(page,'session.create',{title:'Attachment owner',bundle:'anchors'});
  const owner=ownerResult.sessionId||ownerResult.result?.sessionId||(await state(page)).selectedSessionId;
  const otherResult=await call(page,'session.create',{title:'Other attachment draft',bundle:'anchors'});
  const other=otherResult.sessionId||otherResult.result?.sessionId||(await state(page)).selectedSessionId;
  await call(page,'session.select',{id:owner});
  await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
  return {context,page,owner,other};
 }catch(error){
  const cleanup=[];
  try{await bounded(context.close(),10000,'Startup context close');}catch(closeError){cleanup.push(closeError);}
  throw combinedFailure(error,cleanup);
 }
}

async function detector(page,owner){
 const id='f'.repeat(32);
 await page.locator('.a-composer').evaluate((form,id)=>{const marker=document.createElement('a');marker.href='/api/attachments/'+id;marker.textContent='Synthetic detector control';marker.id='attachment-detector-control';form.append(marker);},id);
 await watch(page,owner,[id]);
 // Initial clear, uncached wrapper insertion, then empty/remove the wrapper in
 // ONE task. Final DOM/live addedNodes alone cannot recover the intermediate ID.
 await page.locator('.a-composer').evaluate((form,id)=>{
  form.querySelector('#attachment-detector-control').remove();
  const wrapper=document.createElement('span'),marker=document.createElement('a');
  marker.href='/api/attachments/'+id;wrapper.append(marker);form.append(wrapper);
  marker.remove();wrapper.remove();
 },id);
 await consumed(page,[id]);
 const synchronous=await trace(page);
 assert.ok(synchronous.violations.some(row=>row.id===id&&row.owner===owner&&row.kind==='added-node'),'Detector must reject initial-clear/same-task emptied-wrapper reappearance');
 const firstClear=synchronous.events.findIndex(row=>row.kind==='optimistic-clear-observed'&&row.id===id);
 const firstReturn=synchronous.events.findIndex(row=>row.kind==='added-node'&&row.id===id);
 assert.ok(firstClear>=0&&firstReturn>firstClear,'Removal arms before later addition in the same batch');
 const foreignId='e'.repeat(32);
 await page.evaluate(([owner,id])=>window.attachmentTrace.watch(owner,[id],true),['different-fixture-owner',foreignId]);
 await page.locator('.a-composer').evaluate((form,id)=>{const marker=document.createElement('a');marker.href='/api/attachments/'+id;form.append(marker);marker.remove();},foreignId);
 assert.equal((await trace(page)).violations.some(row=>row.id===foreignId),false,'Different-chat rule cannot create a violation in this owner');
 const beforeEmpty=(await trace(page)).violations.length;
 await page.locator('.a-composer').evaluate(form=>{const wrapper=document.createElement('span');form.append(wrapper);wrapper.remove();});
 assert.equal((await trace(page)).violations.length,beforeEmpty,'An empty wrapper cannot inherit stale cached IDs');
 await page.locator('.a-composer').evaluate((form,id)=>{const marker=document.createElement('a');marker.href='/api/attachments/'+id;marker.textContent='One-frame detector control';form.append(marker);requestAnimationFrame(()=>requestAnimationFrame(()=>marker.remove()));},id);
 await frames(page,4);
 const data=await trace(page);assert.equal(data.capped,false);assert.ok(data.violations.some(row=>row.id===id&&row.kind==='animation-frame'),'Detector must reject a presented-frame candidate');
 assert.equal(await page.locator('.a-composer a[href$="'+id+'"]').count(),0);
 report.traces.push({label:'deliberate-detector-countercheck-not-product-reproduction',...data});
}

async function twoCycles(page,owner,other){
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const a=await upload(page,'cycle-A.png');await original(page,a);await composer.fill('Submit A');
 await watch(page,owner,[a.id]);
 const sendBoundary=await observeFirstSend(page,owner,a.id);
 let first,sendFailed=true;
 try{
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  sendBoundary.clickResolved(); // No extra read between click and the original runtime wait.
  first=await waitRuntimeSend(page,owner,0);sendFailed=false;
 }finally{await sendBoundary.finish(sendFailed)}
 await consumed(page,[a.id]);
 await composer.fill('Keep next draft B');
 // Hold an exact real attachment-add HTTP reply, after real host admission.
 // The service and state stream remain active; no content/revision is forged.
 let release,seen;const gate=new Promise(r=>{release=r}),arrived=new Promise(r=>{seen=r});
 let held=false;
 await withActionRoute(page,{label:'late-B-upload-response',trace:report.routeLifecycle,release,
  handle:async route=>{
   const body=route.request().method()==='POST'?route.request().postDataJSON():null;
   if(!held&&body?.action==='attachment.add'&&body.args.name==='cycle-B.png'){
    held=true;const response=await route.fetch({timeout:10000});assert.equal(response.status(),200);seen();
    await bounded(gate,15000,'B upload reply release deadline');await route.fulfill({response});
   }else await route.continue();
  }},async()=>{
   await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'cycle-B.png',mimeType:'image/png',buffer:png});
   await bounded(arrived,10000,'Expected real B upload response');
   await call(page,'session.select',{id:other});
   await composer.fill('Other chat text stays here');release();
  });
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.some(x=>x.name==='cycle-B.png'),{timeout:10000}).toBe(true);
 const otherState=await state(page);assert.equal(otherState.selectedSessionId,other);
 assert.equal(otherState.sessions.find(x=>x.id===other)?.draftAttachments?.length||0,0,'Late upload never retargets other chat');
 await call(page,'session.select',{id:owner});await expect(composer).toHaveDraft('Keep next draft B');
 const b=(await inspect(page)).attachments[owner].find(x=>x.name==='cycle-B.png');await original(page,b);
 await observation(page,'A send RPC held; B attached to original owner');
 await control(page,{op:'accept',inputId:first});await settled(page,first);
 const beforeReturn=await observation(page,'A positive input.delivered before send return');
 assert.equal(beforeReturn.sends.find(x=>x.inputId===first).returned,false,'Controlled RPC return is still held');
 await frames(page);report.traces.push({label:'A accepted before RPC return',...await trace(page)});
 // Release even on a baseline failure so the fixture does not need forced teardown.
 await control(page,{op:'finish',inputId:first});
 await expect.poll(async()=>(await inspect(page)).sends.find(x=>x.inputId===first).returned,{timeout:10000}).toBe(true);
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.map(x=>x.id),{timeout:10000}).toEqual([b.id]);
 await expect(composer).toHaveDraft('Keep next draft B');await expect(composer).toBeEditable();
 await expect(page.locator('.a-composer img[alt="cycle-B.png"]')).toBeVisible();
 await cleanTrace(page,'A submission through acceptance/outbox retirement/re-enable');
 await original(page,a);await original(page,b);

 await watch(page,owner,[b.id]);await page.getByRole('button',{name:'Send message',exact:true}).click();
 const second=await waitRuntimeSend(page,owner,1);await consumed(page,[b.id]);
 const c=await upload(page,'cycle-C.png');await composer.fill('Keep draft C after B');
 await control(page,{op:'accept',inputId:second});await settled(page,second);
 await frames(page);report.traces.push({label:'B accepted before RPC return',...await trace(page)});
 await control(page,{op:'finish',inputId:second});
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.map(x=>x.id),{timeout:10000}).toEqual([c.id]);
 await expect(page.locator('.a-composer img[alt="cycle-C.png"]')).toBeVisible();
 await expect(composer).toHaveDraft('Keep draft C after B');
 await cleanTrace(page,'Second B-to-C cycle');
 const saved=await observation(page,'two-cycle originals and owner');
 const messages=saved.sessions.find(x=>x.id===owner).messages.filter(x=>[first,second].includes(x.inputId)&&x.role==='user');
 assert.deepEqual(messages.map(x=>x.attachments.map(f=>f.id)),[[a.id],[b.id]],'Second send must not reuse first batch');
 assert.equal(saved.sends.filter(x=>x.sessionId===owner).length,2,'Exactly two explicit runtime sends');
 for(const row of [a,b,c])await original(page,row);
 await call(page,'session.select',{id:other});await expect(composer).toHaveDraft('Other chat text stays here');
 await call(page,'session.select',{id:owner});await expect(composer).toHaveDraft('Keep draft C after B');
 await cleanTrace(page,'Chat switch preserves consumed and new identities');
 try{
  const connected=page.locator('.a-footer-status');
  await expect(connected).toContainText('Connected');
  const open=await page.evaluate(()=>window.attachmentSockets.filter(socket=>socket.readyState===WebSocket.OPEN).length);
  assert.ok(open>0,'Observe a real open transport before disconnecting');
  await bounded(page.context().setOffline(true),5000,'Enter offline mode');
  // Network emulation does not close an already-open WebSocket. Use the same
  // retained real-socket close path as reconnect-retention, not forged app state.
  await bounded(page.evaluate(()=>window.attachmentSockets.forEach(socket=>socket.close())),5000,'Close owned state transports');
  await expect(connected).toContainText('Reconnecting');
  await expect(page.locator('[data-part="connection-notice"]')).toBeVisible({timeout:15000});
  await expect(composer).toHaveDraft('Keep draft C after B');
  report.transport={observedOpenSockets:open,disconnectObserved:true};
 }finally{
  await bounded(page.context().setOffline(false),5000,'Restore fixture network');
  await bounded(page.evaluate(()=>window.dispatchEvent(new Event('online'))),5000,'Wake existing reconnect handler');
 }
 await expect(page.locator('.a-footer-status')).toContainText('Connected',{timeout:15000});
 await expect(page.locator('[data-part="connection-notice"]')).toHaveCount(0,{timeout:15000});
 report.transport.reconnectObserved=true;
 await expect(page.locator('.a-composer img[alt="cycle-C.png"]')).toBeVisible();
 await cleanTrace(page,'Actual state transport reconnect retains draft C');
 const oldClient=(await state(page)).client.id;
 // New document observer watches accepted IDs from its first tick, rather than
 // taking only a settled screenshot after client reattachment.
 await page.addInitScript(({owner,ids})=>window.addEventListener('DOMContentLoaded',()=>window.attachmentTrace.watch(owner,ids,true),{once:true}),{owner,ids:[a.id,b.id]});
 await page.reload();await expect(composer).toHaveDraft('Keep draft C after B');
 await expect(page.locator('.a-composer img[alt="cycle-C.png"]')).toBeVisible();
 assert.notEqual((await state(page)).client.id,oldClient);
 await expect.poll(async()=>(await inspect(page)).attachments[owner]?.map(x=>x.id),{timeout:10000}).toEqual([c.id]);
 await cleanTrace(page,'Reload/client reconnect does not restore consumed IDs');
 report.transport.reloadObserved=true;
 assert.equal((await inspect(page)).sends.filter(x=>x.sessionId===owner).length,2,'Reattachment never resends');
}

async function responseFirst(page,owner){
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const file=await upload(page,'response-first.png');await watch(page,owner,[file.id]);
 await composer.fill('Response-first submission');
 // Reproduce the publication boundary: a real current Work window must be
 // invalidated by admission, not merely absent when the send happens to run.
 await withActionRoute(page,{label:'work-ready-before-admission',trace:report.routeLifecycle,handle:async route=>{
  const body=route.request().method()==='POST'?route.request().postDataJSON():null;
  if(body?.action==='conversation.send'&&body.args.text==='Response-first submission'){
   await expect.poll(async()=>(await inspect(page)).drafts[owner]).toBe('');
   await control(page,{op:'read-work',sessionId:owner});
   assert.equal((await inspect(page)).workStatus[owner].current,true);
  }
  await route.continue();
 }},async()=>{
 await page.evaluate(()=>{window.attachmentFrameGate.arm=true;});
 const responsePromise=page.waitForResponse(response=>{
  try{return response.request().postDataJSON()?.action==='conversation.send';}catch{return false;}
 });
 try{
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await page.waitForFunction(()=>window.attachmentFrameGate.active);
  const inputId=await waitRuntimeSend(page,owner,0);
  await control(page,{op:'accept',inputId});await control(page,{op:'finish',inputId});
  const response=await responsePromise,receipt=await response.json();assert.equal(response.status(),200);
  const gate=await page.evaluate(()=>({admission:window.attachmentFrameGate.admission,identity:window.attachmentFrameGate.identity,
   events:window.attachmentFrameGate.events,recovery:window.attachmentStateRecovery,browserRevision:window.amplifier.getState().revision}));
  const saved=(await observation(page,'Response-first exact saved receipt evidence')).sends.find(row=>row.inputId===inputId);
  report.observations.push({label:'Response before attachment-consumption frame',receipt,gate,saved});
  assert.equal(receipt.delivery,'accepted');
  assert.equal(saved.initial.receipt.inputId,inputId);
  assert.equal(saved.initial.receipt.sessionId,owner);
  assert.equal(saved.initial.receipt.delivery,'sending');
  assert.equal(saved.initial.receipt.revision,saved.accepted.receipt.revision,'Acceptance preserves exact saved admission');
  assert.equal(saved.initial.receipt.revision,saved.final.receipt.revision,'Runtime return preserves exact saved admission');
  assert.equal(receipt.revision,saved.initial.receipt.revision,'HTTP preserves exact saved admission');
  assert.equal(receipt.stateRevision,saved.accepted.receipt._deliveryStateRevision,'HTTP observes exact consumption marker');
  assert.equal(receipt.stateRevision,saved.accepted.stateRevision,'Consumption marker names acceptance publication');
  assert.equal(gate.identity.inputId,inputId);
  assert.equal(gate.identity.sessionId,owner);
  assert.equal(gate.identity.clientId,(await state(page)).client.id);
  assert.equal(gate.identity.hostInstanceId,receipt.hostInstanceId);
  assert.ok(['state','state-delta'].includes(gate.identity.frameKind),'Gate is an actual app-state frame');
  assert.equal(gate.identity.frameRevision,receipt.revision,'Admission frame has the exact command revision');
  assert.equal(receipt.revision,gate.admission,'Durable command retains its admission revision');
  assert.ok(receipt.stateRevision>gate.admission,'Accepted response observes later attachment consumption');
  assert.ok(gate.browserRevision<receipt.stateRevision,'HTTP response precedes browser consumption state');
  // Exercise the real recovery fetch too; do not let it silently satisfy this
  // response-before-state scenario while only WebSocket delivery is gated.
  await page.waitForFunction(()=>window.attachmentStateRecovery.some(row=>row.held&&!row.released));
  const protectedOutbox=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
  assert.deepEqual(protectedOutbox.find(row=>row.commandId===inputId)?.attachmentIds,[file.id],
   'Accepted receipt retains attachment protection until consumption state arrives');
  // Queue a normal second send while the first receipt awaits its state frame.
  // Its optimistic outbox must already exclude the first consumed upload.
  await composer.fill('Adjacent cycle');await page.getByRole('button',{name:'Send message',exact:true}).click();
  const next=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').find(row=>row.text==='Adjacent cycle'));
  assert.ok(next);assert.deepEqual(next.attachmentIds,[],'Adjacent send never reuses the first attachment');
  assert.ok((await state(page)).revision<receipt.stateRevision,'Adjacent send was checked before consumption state');
  report.observations.push({label:'Response-first recovery held through adjacent send',
   recovery:await page.evaluate(()=>window.attachmentStateRecovery),protectedOutbox,next});
  await cleanTrace(page,'Accepted HTTP response while later frames are held');
  await page.evaluate(()=>window.attachmentFrameGate.release());
  const second=await waitRuntimeSend(page,owner,1);
  await control(page,{op:'accept',inputId:second});await control(page,{op:'finish',inputId:second});await settled(page,second);
  const actual=await inspect(page),messages=actual.sessions.find(row=>row.id===owner).messages.filter(row=>row.role==='user'&&[inputId,second].includes(row.inputId));
  assert.deepEqual(messages.map(row=>row.attachments.map(file=>file.id)),[[file.id],[]]);
  assert.equal(actual.sends.filter(row=>row.sessionId===owner).length,2,'Two explicit sends, no replay');
  await cleanTrace(page,'Response-first two-cycle settlement');await original(page,file);
 }finally{await page.evaluate(()=>window.attachmentFrameGate.release());}
 });
}

async function recovery(page,owner,mode){
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const row=await upload(page,mode+'.png');await composer.fill('Retain '+mode+' submission');
 await control(page,{op:'next-mode',mode});
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const input=await waitRuntimeSend(page,owner,0);
 const expected=mode==='failed'?'failed':'unknown';
 await page.waitForFunction(([id,status])=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]').some(x=>x.commandId===id&&x.status===status),[input,expected],{timeout:10000});
 await composer.fill('New '+mode+' draft');
 await expect.poll(async()=>(await inspect(page)).drafts[owner],{timeout:10000}).toBe('New '+mode+' draft');
 const before=await observation(page,mode+' recovery before reload');
 assert.ok(before.attachments[owner].some(x=>x.id===row.id),'Unconfirmed/rejected original is not silently consumed');
 await original(page,row);
 if(mode==='unknown'){
  await page.reload();await expect(composer).toHaveDraft('New unknown draft');
  const check=page.getByRole('button',{name:'Check delivery',exact:true});await expect(check).toBeVisible();
  const result=observed(call(page,'conversation.delivery',{sessionId:owner,inputId:input}));
  const delivery=await resultOf(result);
  assert.equal(delivery.result?.delivery,'unknown','Explicit inspection must retain UNKNOWN without positive evidence');
 }else await expect(page.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
 await frames(page);
 const after=await observation(page,mode+' recovery retained');
 await expect(composer).toHaveDraft('New '+mode+' draft');
 assert.equal(after.selectedSessionId,owner,'Recovery remains bound to the original chat');
 assert.equal(after.drafts[owner],'New '+mode+' draft','Final saved newer draft is retained');
 assert.ok(after.attachments[owner]?.some(x=>x.id===row.id),'Final original attachment association remains with its exact owner');
 assert.equal(after.sends.filter(x=>x.sessionId===owner).length,1,'Recovery inspection/reload must not replay');
 const rows=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')||'[]'));
 const retained=rows.find(x=>x.commandId===input);assert.ok(retained);
 assert.equal(retained.sessionId,owner);assert.equal(retained.status,expected);
 assert.deepEqual(retained.attachmentIds,[row.id]);assert.equal(retained.text,'Retain '+mode+' submission');
 await original(page,row);
}

try{
 const ready=await readiness();base=ready.base;
 const built=await readFile(join(root,'amplifier_web/static/build.json'));
 assert.equal(ready.buildSha256,hash(built),'Fixture imports the exact candidate web build');
 report.build={sha256:ready.buildSha256,metadata:JSON.parse(built),fixturePackageRoot:ready.packageRoot,
  installedFixture:resolve(fixtureRoot)!==resolve(join(root,'tests/fixtures'))};
 browser=await chromium.launch({headless:true,timeout:20000});
 for(const row of report.cases){
  row.status='running';await save();let context,page;
  try{
   const env=await setupPage();({context,page}=env);
   const delivered=await page.request.get(base+'/build.json',{timeout:5000});assert.equal(delivered.status(),200);assert.equal(hash(await delivered.body()),ready.buildSha256,'HTTP build metadata matches fixture import');
   if(row.id==='detector-countercheck')await detector(page,env.owner);
   else if(row.id==='accepted-two-cycle')await twoCycles(page,env.owner,env.other);
   else if(row.id==='response-before-frame')await responseFirst(page,env.owner);
   else await recovery(page,env.owner,row.id==='failed-recovery'?'failed':'unknown');
   row.status='passed';
  }catch(error){row.status='failed';row.error=error.message;rememberFailure(error,'case',row.id);}
  finally{
   if(page&&!page.isClosed()){
    try{report.traces.push({label:row.id+' terminal',...await trace(page)});}
    catch(error){row.status='failed';(row.cleanupErrors??=[]).push(error.message);rememberFailure(error,'terminal-trace',row.id);}
    try{await control(page,{op:'release-all'});}
    catch(error){row.status='failed';(row.cleanupErrors??=[]).push(error.message);rememberFailure(error,'release-all',row.id);}
   }
   if(context)try{await bounded(context.close(),10000,'Case browser-context cleanup');}
   catch(error){row.status='failed';(row.cleanupErrors??=[]).push(error.message);rememberFailure(error,'context-close',row.id);}
   await save();
  }
 }
 assert.deepEqual(report.errors,[],'Browser page errors');
 if(primary)throw primary;
}catch(error){rememberFailure(error,'outer');report.error=primary.message;}
finally{
 // Ask the existing helper to throw its complete cleanup aggregate, so failures
 // are retained here rather than only printed behind an earlier primary.
 try{await finishFixture({label:'Attachment consumption',close:[['Chromium',()=>browser?.close()]],stop});}
 catch(error){report.cleanupError=error.message;rememberFailure(error,'fixture-browser-cleanup');}
 report.fixtureExit=fixtureExit||null;report.complete=report.cases.every(row=>['passed','failed'].includes(row.status));
 report.passed=!primary&&report.complete&&report.cases.every(row=>row.status==='passed')&&fixtureExit?.code===0;
 report.fixtureStderr=stderr;await save();
}
if(!report.passed)throw combinedFailure(primary,secondaryErrors)||Error('Attachment consumption evidence incomplete');
console.log('Attachment consumption: actual host/browser two-cycle journey and recovery passed; synthetic runtime, no native/provider claim.');
