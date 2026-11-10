// Real Vite UI with synthetic HTTP/SSE; no live server, provider, or runtime.
// Run from frontend: node tests/delivery-reconciliation-browser.mjs
import {createShellHttpFixture} from './shell-http-fixture.mjs';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/delivery-reconciliation';
await mkdir(artifact,{recursive:true});
const outboxKey='amplifier.messageOutbox.v1',originalText='Keep this historical request and its reference.';
const message=(inputId='original',delivery={status:'unknown',error:'Transport interrupted. Delivery is uncertain.'})=>({
 id:'saved-'+inputId,inputId,role:'user',text:inputId==='original'?originalText:`Synthetic saved request: ${inputId}`,
 attachments:[{id:'a',name:'reference.txt',size:12}],delivery,
});
const session=id=>({
 id,title:id,sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',
 runtimeSessionId:null,nativeIdentity:id,
 historyManaged:true,historyLoaded:true,messages:[message()],workers:[],
});
let state={
 revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},
 sessions:[session('chat'),session('other')],
 workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],
 selectedSessionId:'chat',selectedWorkspaceId:'project',
 setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},
 canvas:{open:false},
};
let historyScope='synthetic-host/account-one';
const shell=createShellHttpFixture(()=>state);
const wireState=()=>{const value=shell.state();return {...value,client:{...value.client,historyScope}}};
const scopeFor=sessionId=>JSON.stringify([historyScope,'/fixture',sessionId]);
const seed=[
 {id:'local-original',commandId:'original',sessionId:'chat',scope:scopeFor('chat'),text:originalText,attachmentIds:['a'],attachments:[{id:'a',name:'reference.txt',size:12}],status:'unknown',error:'Transport interrupted. Delivery is uncertain.'},
 {id:'other-unsent',commandId:'unrelated-input',sessionId:'other',scope:scopeFor('other'),text:'Keep this separate failed input.',attachmentIds:[],status:'failed',error:'Known failure; not eligible for automatic reconciliation.'},
 {id:'old-source-outbox',commandId:'source-race',sessionId:'other',scope:scopeFor('other'),text:'Private retained payload from account one.',attachmentIds:['private-reference'],attachments:[{id:'private-reference',name:'private-reference.txt',size:27}],status:'unknown'},
 // Deliberately the same chat/input as the first accepted read: lack of an origin
 // must not cause adoption into that conversation or automatic retirement.
 {id:'legacy-unbound',commandId:'original',sessionId:'chat',text:'Legacy payload with no verified source.',attachmentIds:[],status:'unknown'},
];
const kept=seed.filter(entry=>entry.id!=='local-original');
const legacyNotice='This tab retains earlier message copies with no verified host identity. Their delivery is unknown. Nothing was sent again.';
const calls=[],pending=[],errors=[],screenshots=[],completed=[],stops=[],responseSourceCases=[];
const executions=new Map([['chat/original',1]]);
let senderCounter=0,duplicateExecution=0,vite,browser,page;
const recoveryCalls=()=>calls.filter(call=>call.action==='conversation.delivery');
const effects=()=>calls.filter(call=>['conversation.send','conversation.retry','message.edit','session.delete'].includes(call.action));

// Extend the small shell fixture's EventSource sequencing locally. Exercise the
// production stream onopen -> amplifier-reconnected path; never stub React hooks
// or invoke the production dispatch API from the fixture.
function installSequencedTransport({seed,outboxKey}){
 const sources=new Set();let online=false;
 Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>online});
 window.EventSource=class extends EventTarget{
  constructor(){super();this.readyState=0;sources.add(this);queueMicrotask(async()=>{
   const state=await fetch('/api/state').then(response=>response.json());
   if(this.readyState===2)return;
   this.readyState=1;this.onopen?.();
   this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
  });}
  close(){this.readyState=2;sources.delete(this);}
 };
 window.emitState=state=>sources.forEach(source=>{
  if(source.readyState!==2)source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
 });
 window.fixtureTransport={
  disconnect(){online=false;for(const source of sources){source.readyState=0;source.onerror?.(new Event('error'));}},
  reconnect(state){
   online=true;
   for(const source of sources){
    source.readyState=1;source.onopen?.();
    source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
   }
  },
 };
 // This context is created by this test. Seed once, not on reload: a reload must
 // inspect the actual persisted outbox, not silently restore the original seed.
 if(!sessionStorage.getItem('delivery-reconciliation-seeded')){
  sessionStorage.setItem('delivery-reconciliation-seeded','1');
  sessionStorage.setItem(outboxKey,JSON.stringify(seed));
 }
}

const row=inputId=>page.locator(`[data-input-id="${inputId}"]`);
const check=inputId=>row(inputId).getByRole('button',{name:'Check delivery',exact:true});
const checking=inputId=>row(inputId).getByRole('button',{name:'Checking…',exact:true});
const warning=inputId=>row(inputId).locator('.a-message-delivery').filter({hasText:'Delivery not confirmed'});
const outbox=()=>page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)||'[]'),outboxKey);
async function emit(){
 const revision=++state.revision;
 await page.evaluate(value=>window.emitState(value),wireState());
 await expect.poll(()=>page.evaluate(()=>window.amplifier?.getState().revision)).toBeGreaterThanOrEqual(revision);
}
async function readFor(sessionId,inputId,scope=historyScope){
 await expect.poll(()=>pending.filter(request=>request.body.args.sessionId===sessionId&&request.body.args.inputId===inputId&&request.scope===scope).length).toBe(1);
 return pending.find(request=>request.body.args.sessionId===sessionId&&request.body.args.inputId===inputId&&request.scope===scope);
}
async function answer(request,result,responseState=wireState(),status=200){
 assert.ok(request&&!request.settled,'Each deferred HTTP request is completed exactly once');
 request.settled=true;
 pending.splice(pending.indexOf(request),1);
 // Keep the server message unknown. Receipt reconciliation, not a conveniently
 // changed snapshot, must remove the warning and exact outbox entry.
 // null deliberately models the production compact receipt: no state field.
 await request.route.fulfill({status,json:{...result,...(responseState===null?{}:{state:responseState})}});
}
const receiptSource=request=>{
 const target=request.baseline.sessions.find(session=>session.id===request.body.args.sessionId);
 assert.ok(target,'Synthetic delivery receipt requires the request baseline canonical chat');
 return {workspace:target.workspace||null,nativeIdentity:target.runtimeSessionId||target.nativeIdentity||target.id};
};
const receipt=(request,delivery='accepted',extra={})=>({
 accepted:true,result:{
  sessionId:request.body.args.sessionId,inputId:request.body.args.inputId,delivery,source:receiptSource(request),
  message:delivery==='accepted'?'Received. Nothing was resent.':'Still uncertain. Nothing was resent.',...extra,
 },
});
async function screenshot(name){
 const path=`${artifact}/${name}.png`;
 await page.screenshot({path});screenshots.push(path);
}
async function assertRetained(){
 assert.deepEqual(await outbox(),kept,'Foreign, unrelated and unbound payloads stay byte-for-byte unchanged');
 await expect(page.getByText(legacyNotice,{exact:true})).toBeVisible();
 await expect(page.getByText('Legacy payload with no verified source.',{exact:true})).toHaveCount(0);
}
function assertPassive(){
 assert.equal(senderCounter,0,'No conversation.send or conversation.retry may run');
 assert.equal(duplicateExecution,0,'No synthetic accepted input may execute again');
 assert.deepEqual(effects(),[]);
 for(const call of recoveryCalls())assert.deepEqual(Object.keys(call.args).sort(),['inputId','sessionId'],'No recovery scope or payload is promoted into action authority');
}
async function saveResult(passed,error){
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({
  passed,evidence:'Real bundled Vite UI; synthetic HTTP/SSE and synthetic execution counters only.',
  viewports:[{width:1280,height:900},{width:390,height:844}],completed,
  senderCounter,duplicateExecution,calls:recoveryCalls(),stops,responseSourceCases,effects:effects(),errors,screenshots,
  ...(error?{failure:String(error.stack||error)}:{}),
 },null,2));
}

try{
 vite=await createServer({
  configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},
  optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']},
 });await vite.listen();
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(installSequencedTransport,{seed,outboxKey});
 await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,response=shell.response(request);
  if(response!==undefined)return route.fulfill({json:response});
  if(path==='/api/state')return route.fulfill({json:wireState()});
  if(path==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=request.postDataJSON();calls.push(body);
  if(body.action==='conversation.delivery'){
   pending.push({route,body,scope:historyScope,baseline:structuredClone(wireState()),settled:false});return;
  }
  if(['conversation.send','conversation.retry'].includes(body.action)){
   senderCounter++;
   const identity=`${body.args.sessionId}/${body.args.inputId||body.id}`;
   if(executions.has(identity))duplicateExecution++;
   executions.set(identity,(executions.get(identity)||0)+1);
   return route.fulfill({json:{accepted:false,error:'This passive fixture refuses all send and retry effects.',state:wireState()}});
  }
  if(body.action==='conversation.stop'){
   assert.equal(request.method(),'POST');
   stops.push({args:body.args,pendingDelivery:pending.map(read=>({sessionId:read.body.args.sessionId,inputId:read.body.args.inputId})),actionIndex:calls.length-1});
   const target=state.sessions.find(session=>session.id===body.args.sessionId);
   assert.ok(target,'Stop must target an existing synthetic conversation');
   target.status='stopped';target.execution={...target.execution,currentTurnId:null};
  }
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  if(body.action==='session.select')state.selectedSessionId=body.args.id;
  state.revision++;
  return route.fulfill({json:{accepted:true,state:wireState()}});
 });
 await page.goto(vite.resolvedUrls.local[0]);
 await expect(warning('original')).toBeVisible();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep the next unsent thought');
 const retained=await outbox();
 assert.deepEqual(retained,seed);
 await expect(page.getByText(legacyNotice,{exact:true})).toBeVisible();
 await expect(page.getByText('Legacy payload with no verified source.',{exact:true})).toHaveCount(0);
 assert.equal(recoveryCalls().length,0,'Offline mount must not request delivery');
 for(let i=0;i<3;i++){state.sessions[0].title=`Offline state revision ${i}`;await emit();}
 assert.equal(recoveryCalls().length,0);
 completed.push('offline mount and unrelated frames: no delivery requests');
 await page.reload();
 await expect(warning('original')).toBeVisible();
 await expect(row('original')).toHaveCount(1);
 await expect(composer).toHaveDraft('Keep the next unsent thought');
 assert.deepEqual(await outbox(),seed,'Reload preserves original origin and unbound copies without duplication');
 assert.equal(recoveryCalls().length,0);
 await expect(page.getByText(legacyNotice,{exact:true})).toBeVisible();
 assertPassive();completed.push('offline reload: same persisted scoped outbox, no duplicate projection or submission');

 // Lost ACK recovery: no Check click or retry, and exactly one read after SSE open.
 await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
 const first=await readFor('chat','original');
 await expect(checking('original')).toBeDisabled();
 await expect(row('original').getByRole('button',{name:'Dismiss warning',exact:true})).toBeDisabled();
 for(let i=0;i<3;i++){state.sessions[0].title=`Pending read revision ${i}`;await emit();}
 assert.equal(recoveryCalls().length,1,'State updates must not poll');
 await screenshot('desktop-rechecking');
 const unchangedResponse=wireState(),unchangedTarget=unchangedResponse.sessions.find(session=>session.id==='chat');
 assert.equal(JSON.stringify([unchangedResponse.client.historyScope,unchangedTarget.workspace,
  unchangedTarget.runtimeSessionId||unchangedTarget.nativeIdentity||unchangedTarget.id]),seed[0].scope);
 const compactPositive={...receipt(first),hostInstanceId:first.baseline.client.hostInstanceId};
 assert.deepEqual(compactPositive.result.source,{workspace:unchangedTarget.workspace,nativeIdentity:unchangedTarget.nativeIdentity});
 await answer(first,compactPositive,null);
 await expect(warning('original')).toHaveCount(0);
 await expect.poll(async()=>(await outbox()).map(entry=>entry.id)).toEqual(kept.map(entry=>entry.id));
 await assertRetained();
 await expect(row('original').getByText(originalText,{exact:true})).toBeVisible();
 await expect(row('original').getByText(/reference.txt/)).toBeVisible();
 await expect(composer).toHaveDraft('Keep the next unsent thought');
 await emit();
 await expect(warning('original')).toHaveCount(0);
 assert.equal(recoveryCalls().length,1);
 assert.deepEqual(state.sessions[0].messages[0].delivery.status,'unknown','Fixture did not hide the warning through a snapshot');
 responseSourceCases.push({case:'compact-unchanged-source-positive-control',sessionId:'chat',inputId:'original',
  scope:seed[0].scope,source:compactPositive.result.source,stateOmitted:true,retiredExactEntry:true,warningCleared:true});
 assertPassive();await screenshot('desktop-reconciled');
 completed.push('lost ACK: compact exact positive automatic receipt without state; original history, attachment and draft retained; unrelated outbox preserved');
 // A later authoritative accepted snapshot also cannot clear the legacy entry
 // with identical chat/input IDs. Reload must not recreate the retired copy.
 state.sessions[0].messages[0].delivery={status:'accepted'};await emit();
 await assertRetained();
 await page.reload();
 await expect(row('original')).toHaveCount(1);
 await expect(warning('original')).toHaveCount(0);
 await expect(composer).toHaveDraft('Keep the next unsent thought');
 await assertRetained();
 assert.equal(recoveryCalls().length,1);
 await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
 assert.equal(recoveryCalls().length,1);
 completed.push('accepted snapshot and reload: scoped retirement persists; same-ID legacy payload and notice remain');

 // The no-local-copy case represents another browser with only a saved input.
 state.sessions[0].messages=[message('saved-only')];await emit();
 const saved=await readFor('chat','saved-only');
 assert.ok(!(await outbox()).some(entry=>entry.commandId==='saved-only'));
 await answer(saved,receipt(saved));
 await expect(warning('saved-only')).toHaveCount(0);
 await expect(row('saved-only').getByText('Synthetic saved request: saved-only',{exact:true})).toBeVisible();
 await expect(row('saved-only').getByText(/reference.txt/)).toBeVisible();
 await expect(row('original'),'Unbound legacy payload must not project after server original leaves view').toHaveCount(0);
 responseSourceCases.push({case:'full-frame-unchanged-source-positive-control',sessionId:'chat',inputId:'saved-only',
  source:receiptSource(saved),warningCleared:true});
 completed.push('saved exact input reconciles without local copy');

 // A stalled read for an old input must not occupy the queue needed by Stop.
 // Exercise the rendered button, not window.amplifier.dispatch or a handler stub.
 state.sessions[0].status='working';
 state.sessions[0].execution={currentTurnId:'newer-running-input'};
 state.sessions[0].messages=[message('older-stop-read'),message('newer-running-input',{status:'accepted'})];
 await emit();
 const stopRead=await readFor('chat','older-stop-read');
 const stopButton=page.getByRole('button',{name:'Stop response',exact:true});
 await expect(stopButton).toBeVisible();await expect(stopButton).toBeEnabled();
 await expect(checking('older-stop-read')).toBeDisabled();
 await stopButton.click();
 await expect.poll(()=>stops.length).toBe(1);
 assert.equal(stopRead.settled,false,'Stop POST reached the server before the held delivery response');
 assert.ok(pending.includes(stopRead));
 assert.deepEqual(stops[0].args,{sessionId:'chat'});
 assert.ok(stops[0].pendingDelivery.some(read=>read.sessionId==='chat'&&read.inputId==='older-stop-read'));
 assert.ok(stops[0].actionIndex>calls.indexOf(stopRead.body));
 await expect(composer).toHaveDraft('Keep the next unsent thought');
 await answer(stopRead,receipt(stopRead,'unknown'));
 await expect(check('older-stop-read')).toBeEnabled();
 await expect(warning('older-stop-read')).toBeVisible();
 assertPassive();await screenshot('desktop-stop-not-blocked');
 completed.push('rendered Stop posts once for newer work while old delivery read remains unresolved');
 state.sessions[0].status='idle';state.sessions[0].execution={currentTurnId:null};

 // A historical sending report must not be mistaken for current execution.
 state.sessions[0].messages=[message('stale-sending')];await emit();
 const sendingRead=await readFor('chat','stale-sending');
 await answer(sendingRead,receipt(sendingRead,'sending',{message:'The prior read observed sending.'}));
 await expect(row('stale-sending').getByText('The prior read observed sending.',{exact:true})).toBeVisible();
 state.sessions[0].messages[0].delivery={status:'unknown'};await emit();
 const sendingCount=recoveryCalls().length;
 await page.evaluate(()=>window.fixtureTransport.disconnect());
 await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
 const refreshed=await readFor('chat','stale-sending');
 assert.equal(recoveryCalls().length,sendingCount+1);
 await answer(refreshed,receipt(refreshed));
 await expect(warning('stale-sending')).toHaveCount(0);
 assertPassive();completed.push('sending report followed by idle/unknown state permits one reconnect check');

 // Absence, malformed/generic acceptance, and conflicting identity never imply
 // success. Each is a new exact input, not a retry loop against one fixture row.
 const negativeCases=[
  ['unknown',request=>receipt(request,'unknown')],
  ['not-saved',request=>receipt(request,'not_saved',{message:'No saved receipt was found. Nothing was resent.'})],
  ['absent',()=>({accepted:true})],
  ['unbound',()=>({accepted:true,result:{delivery:'accepted'}})],
  ['wrong-input',request=>receipt(request,'accepted',{inputId:'not-this-input'})],
  ['conflict',request=>receipt(request,'accepted',{accepted:false})],
 ];
 for(const [name,result] of negativeCases){
  const inputId='negative-'+name;
  state.sessions[0].messages=[message(inputId)];await emit();
  const request=await readFor('chat',inputId),count=recoveryCalls().length;
  await answer(request,result(request));
  await expect(warning(inputId)).toBeVisible();
  await expect(check(inputId)).toBeEnabled();
  for(let revision=0;revision<3;revision++){state.sessions[0].title=`Unrelated ${name} ${revision}`;await emit();}
  assert.equal(recoveryCalls().length,count,'Settled uncertainty must not poll');
  await assertRetained();
  if(name==='not-saved'){
   await expect(row(inputId).getByRole('button',{name:/^(Send again|Retry original submission|Send this message again)$/})).toHaveCount(0);
   await page.setViewportSize({width:390,height:844});
   await expect(warning(inputId)).toBeVisible();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile recovery must not overflow');
   await screenshot('mobile-no-evidence');
  }
  assertPassive();completed.push(`honest negative result: ${name}`);
 }

 // Chat navigation remounts the row without replacing its pending operation.
 // Same input ID in another chat must not share that operation or its result.
 state.sessions[0].messages=[message('race-input')];
 state.sessions[1].messages=[message('race-input')];await emit();
 const chatRead=await readFor('chat','race-input');
 state.selectedSessionId='other';await emit();
 const otherRead=await readFor('other','race-input');
 state.selectedSessionId='chat';await emit();
 await expect(checking('race-input')).toBeDisabled();
 assert.equal(recoveryCalls().filter(call=>call.args.sessionId==='chat'&&call.args.inputId==='race-input').length,1,'Pending remount must not duplicate the read');
 // Native disabled click is deliberately inert; the component test also invokes
 // the handler directly to cover automatic/manual ownership independent of DOM.
 await checking('race-input').evaluate(button=>button.click());
 assert.equal(recoveryCalls().filter(call=>call.args.sessionId==='chat'&&call.args.inputId==='race-input').length,1);
 state.selectedSessionId='other';await emit();
 await answer(chatRead,receipt(chatRead));
 await expect(warning('race-input')).toBeVisible();
 await expect(checking('race-input')).toBeDisabled();
 await answer(otherRead,receipt(otherRead,'unknown',{message:'Other chat is still uncertain. Nothing was resent.'}));
 await expect(row('race-input').getByText('Other chat is still uncertain. Nothing was resent.',{exact:true})).toBeVisible();
 await expect(check('race-input')).toBeEnabled();
 completed.push('pending remount/manual dedup; late positive for another chat does not clear selected warning');

 // Source identity is a nonsecret namespace supplied by state.client, not action
 // authority. The old scope owns a real persisted local payload with these IDs.
 state.sessions[1].messages=[message('source-race')];await emit();
 const oldScope=historyScope;
 let oldSourceRead=await readFor('other','source-race',oldScope);
 assert.deepEqual((await outbox()).find(entry=>entry.id==='old-source-outbox'),seed.find(entry=>entry.id==='old-source-outbox'));
 // The replacement host may answer HTTP before its first SSE baseline. Both
 // returned identity forms must be checked before acknowledging the old outbox.
 // No new-host stream frame is injected in these two bounded cases.
 for(const identityForm of ['top-level','state']){
  const previousHost=wireState().client.hostInstanceId,responseState=structuredClone(wireState());
  const reply=receipt(oldSourceRead);
  if(identityForm==='top-level')reply.hostInstanceId='replacement-host-before-baseline';
  else responseState.client.hostInstanceId='replacement-host-before-baseline';
  await answer(oldSourceRead,reply,responseState);
  await expect(warning('source-race')).toBeVisible();
  await expect(check('source-race')).toBeEnabled();
  assert.equal(await page.evaluate(()=>window.amplifier.getState().client.hostInstanceId),previousHost,'A mismatched HTTP reply must not become the new authoritative baseline');
  await assertRetained();
  await check('source-race').click();
  oldSourceRead=await readFor('other','source-race',oldScope);
  completed.push(`${identityForm} returned host before baseline: old scoped payload retained`);
 }
 // A response can keep host/account/chat/input fixed yet name a different
 // history source for that chat. No SSE frame is emitted between capturing the
 // canonical source and answering any of these six held reads.
 const changedSources=[
  ['workspace',target=>{target.workspace='/different-workspace'}],
  ['runtimeSessionId',target=>{target.runtimeSessionId='different-runtime-session'}],
  ['nativeIdentity-fallback',target=>{target.nativeIdentity='different-native-identity'}],
 ];
 for(const outcome of ['accepted','error']){
  for(const [field,changeTarget] of changedSources){
   const baseline=await page.evaluate(()=>window.amplifier.getState());
   const canonical=baseline.sessions.find(session=>session.id==='other');
   const beforeBytes=await page.evaluate(key=>sessionStorage.getItem(key),outboxKey);
   const originalEntry=(await outbox()).find(entry=>entry.id==='old-source-outbox');
   assert.deepEqual(originalEntry,seed.find(entry=>entry.id==='old-source-outbox'));
   assert.equal(canonical.runtimeSessionId,null,'Native fallback case must exercise the effective identity');
   assert.equal(canonical.nativeIdentity,'other');
   const originalScope=JSON.stringify([baseline.client.historyScope,canonical.workspace,
    canonical.runtimeSessionId||canonical.nativeIdentity||canonical.id]);
   assert.equal(originalScope,originalEntry.scope,'The caller has the complete canonical target, not an id-only stub');
   assert.deepEqual(oldSourceRead.body.args,{sessionId:'other',inputId:'source-race'});

   const responseState=structuredClone(wireState());
   const returned=responseState.sessions.find(session=>session.id==='other');
   changeTarget(returned);
   responseState.revision=baseline.revision+1000;
   const returnedScope=JSON.stringify([responseState.client.historyScope,returned.workspace,
    returned.runtimeSessionId||returned.nativeIdentity||returned.id]);
   assert.notEqual(returnedScope,originalScope);
   assert.equal(responseState.client.hostInstanceId,baseline.client.hostInstanceId);
   assert.equal(responseState.client.historyScope,baseline.client.historyScope);
   assert.equal(returned.id,canonical.id);
   assert.equal(returned.messages.find(message=>message.inputId==='source-race').inputId,'source-race');
   const evidence={case:`${outcome}-${field}-before-baseline`,sessionId:'other',inputId:'source-race',
    hostInstanceId:baseline.client.hostInstanceId,historyScope:baseline.client.historyScope,
    originalScope,returnedScope,responseRevision:responseState.revision,
    status:outcome==='accepted'?200:409,settlement:null,adoptions:[],outboxRetained:false};
   responseSourceCases.push(evidence);
   await page.evaluate(()=>{
    window.responseSourceAudit=[];
    window.stopResponseSourceAudit=window.amplifier.subscribe(state=>{
     const target=state.sessions.find(session=>session.id==='other');
     window.responseSourceAudit.push({
      revision:state.revision,hostInstanceId:state.client?.hostInstanceId,historyScope:state.client?.historyScope,
      selectedSessionId:state.selectedSessionId,
      scope:JSON.stringify([state.client?.historyScope||state.client?.hostInstanceId,target?.workspace||null,
       target?.runtimeSessionId||target?.nativeIdentity||target?.id||null]),
     });
    });
   });
   const response=outcome==='accepted'?receipt(oldSourceRead):{
    ...receipt(oldSourceRead,'unknown'),accepted:false,status:409,code:'session_busy',error:'Synthetic rejected read from a different history source.',
   };
   assert.deepEqual(response.result.source,{workspace:canonical.workspace||null,
    nativeIdentity:canonical.runtimeSessionId||canonical.nativeIdentity||canonical.id},
   'Full-frame contradiction must keep the valid original source witness; it must not fail for absent compact evidence');
   evidence.sourceWitness=response.result.source;
   const readsBefore=recoveryCalls().length;
   await answer(oldSourceRead,response,responseState,evidence.status);
   // Resolve either the correct settlement or the causal failure. The broken
   // implementation removes the outbox before hiding/remounting the warning;
   // waiting only for Check to be enabled would turn that into a vague timeout.
   let observed;
   await expect.poll(async()=>{
    observed=await page.evaluate(({key,originalScope,responseRevision})=>{
     const entries=JSON.parse(sessionStorage.getItem(key)||'[]'),state=window.amplifier.getState();
     const target=state.sessions.find(session=>session.id==='other');
     const scope=JSON.stringify([state.client?.historyScope||state.client?.hostInstanceId,target?.workspace||null,
      target?.runtimeSessionId||target?.nativeIdentity||target?.id||null]);
     const control=document.querySelector('[data-input-id="source-race"] button[data-action="conversation.delivery"]');
     const adoptions=window.responseSourceAudit||[];
     return {
      settlement:!entries.some(entry=>entry.id==='old-source-outbox')?'outbox-retired':
       scope!==originalScope||adoptions.some(frame=>frame.scope!==originalScope||frame.revision===responseRevision)?'wrong-source-adopted':
       control&&!control.disabled?'read-settled':'pending',
      scope,revision:state.revision,adoptions,
     };
    },{key:outboxKey,originalScope,responseRevision:responseState.revision});
    return observed.settlement;
   },{message:`Wait for ${evidence.case} settlement or a causal source violation`,timeout:5000}).not.toBe('pending');
   evidence.settlement=observed.settlement;evidence.adoptions=observed.adoptions;
   const after=await outbox(),retainedEntry=after.find(entry=>entry.id==='old-source-outbox');
   assert.ok(retainedEntry,`RESPONSE SOURCE CAUSAL REGRESSION: ${evidence.case} retired old-source-outbox despite changed ${field}`);
   assert.deepEqual(retainedEntry,originalEntry,`RESPONSE SOURCE CAUSAL REGRESSION: ${evidence.case} changed original text, attachment, identity or scope`);
   assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),outboxKey),beforeBytes,
    `RESPONSE SOURCE CAUSAL REGRESSION: ${evidence.case} rewrote persisted outbox bytes`);
   evidence.outboxRetained=true;
   assert.equal(observed.scope,originalScope,`RESPONSE SOURCE CAUSAL REGRESSION: ${evidence.case} adopted returned target source before baseline`);
   assert.ok(observed.adoptions.every(frame=>frame.scope===originalScope&&frame.revision!==responseState.revision&&
    frame.hostInstanceId===baseline.client.hostInstanceId&&frame.historyScope===baseline.client.historyScope),
   `RESPONSE SOURCE CAUSAL REGRESSION: ${evidence.case} transiently adopted the mismatched response frame`);
   await expect(warning('source-race')).toBeVisible();
   await expect(check('source-race')).toBeEnabled();
   await page.evaluate(()=>window.stopResponseSourceAudit());
   assert.equal(recoveryCalls().length,readsBefore,'Rejecting a changed response must not start an automatic check loop');
   await assertRetained();assertPassive();
   await check('source-race').click();
   oldSourceRead=await readFor('other','source-race',oldScope);
   completed.push(`${outcome} ${field} before baseline: unchanged original scoped outbox and no response-state adoption`);
  }
 }
 // Compact acknowledgements have no state from which to infer or repair source.
 // Keep the same real outbox and exact request target; only the witness varies.
 const compactCases=[
  ['changed-workspace',response=>{response.result.source.workspace='/compact-other-workspace'}],
  ['changed-nativeIdentity',response=>{response.result.source.nativeIdentity='compact-other-native'}],
  ['absent-source',response=>{delete response.result.source}],
  ['null-source',response=>{response.result.source=null}],
  ['string-source',response=>{response.result.source='other'}],
  ['missing-fields',response=>{response.result.source={}}],
  ['numeric-workspace',response=>{response.result.source.workspace=42}],
  ['null-nativeIdentity',response=>{response.result.source.nativeIdentity=null}],
 ];
 for(const [name,changeWitness] of compactCases){
  const baseline=await page.evaluate(()=>window.amplifier.getState());
  const beforeBytes=await page.evaluate(key=>sessionStorage.getItem(key),outboxKey);
  const originalEntry=(await outbox()).find(entry=>entry.id==='old-source-outbox');
  assert.deepEqual(originalEntry,seed.find(entry=>entry.id==='old-source-outbox'));
  const originalScope=originalEntry.scope;
  const response={...receipt(oldSourceRead),hostInstanceId:oldSourceRead.baseline.client.hostInstanceId};
  changeWitness(response);
  assert.equal(response.hostInstanceId,baseline.client.hostInstanceId);
  assert.deepEqual(oldSourceRead.body.args,{sessionId:'other',inputId:'source-race'});
  const evidence={case:`compact-${name}`,stateOmitted:true,hostInstanceId:response.hostInstanceId,
   originalScope,sourceWitness:response.result.source??null,sourcePresent:Object.hasOwn(response.result,'source'),
   settlement:null,outboxRetained:false};
  responseSourceCases.push(evidence);
  await page.evaluate(()=>{
   window.compactSourceAudit=[];
   window.stopCompactSourceAudit=window.amplifier.subscribe(state=>{
    const target=state.sessions.find(session=>session.id==='other');
    window.compactSourceAudit.push({revision:state.revision,scope:JSON.stringify([
     state.client.historyScope||state.client.hostInstanceId,target?.workspace||null,
     target?.runtimeSessionId||target?.nativeIdentity||target?.id||null,
    ])});
   });
  });
  const readsBefore=recoveryCalls().length;
  await answer(oldSourceRead,response,null);
  let observed;
  await expect.poll(async()=>{
   observed=await page.evaluate(key=>{
    const entries=JSON.parse(sessionStorage.getItem(key)||'[]');
    const control=document.querySelector('[data-input-id="source-race"] button[data-action="conversation.delivery"]');
    return !entries.some(entry=>entry.id==='old-source-outbox')?'outbox-retired':
     control&&!control.disabled?'read-settled':'pending';
   },outboxKey);
   return observed;
  },{message:`Wait for compact ${name} settlement or causal retirement`,timeout:5000}).not.toBe('pending');
  evidence.settlement=observed;
  assert.ok((await outbox()).some(entry=>entry.id==='old-source-outbox'),
   `RESPONSE SOURCE CAUSAL REGRESSION: compact ${name} retired old-source-outbox without an exact valid witness`);
  assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),outboxKey),beforeBytes,
   `RESPONSE SOURCE CAUSAL REGRESSION: compact ${name} changed persisted original payload bytes`);
  evidence.outboxRetained=true;
  const after=await page.evaluate(()=>window.amplifier.getState()),target=after.sessions.find(session=>session.id==='other');
  assert.equal(JSON.stringify([after.client.historyScope,target.workspace||null,
   target.runtimeSessionId||target.nativeIdentity||target.id]),originalScope);
  evidence.adoptions=await page.evaluate(()=>window.compactSourceAudit);
  assert.ok(evidence.adoptions.every(frame=>frame.scope===originalScope));
  await page.evaluate(()=>window.stopCompactSourceAudit());
  await expect(warning('source-race')).toBeVisible();
  await expect(check('source-race')).toBeEnabled();
  assert.equal(recoveryCalls().length,readsBefore);
  await assertRetained();assertPassive();
  await check('source-race').click();
  oldSourceRead=await readFor('other','source-race',oldScope);
  completed.push(`compact ${name}: original scoped outbox retained; no state fallback, resend, or warning clearance`);
 }
 historyScope='synthetic-host/account-two';await emit();
 const newSourceRead=await readFor('other','source-race');
 assert.notEqual(oldSourceRead,newSourceRead);
 await answer(oldSourceRead,receipt(oldSourceRead),oldSourceRead.baseline);
 await expect(warning('source-race')).toBeVisible();
 await expect(checking('source-race')).toBeDisabled();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().client.historyScope),historyScope,'Late old state cannot restore its source');
 await assertRetained();
 await answer(newSourceRead,receipt(newSourceRead));
 await expect(warning('source-race')).toHaveCount(0);
 await assertRetained();
 // New-source acceptance is not authority to clean up an old-source payload,
 // whether it arrives in a receipt or a subsequent accepted snapshot.
 state.sessions[1].messages[0].delivery={status:'accepted'};await emit();
 await assertRetained();
 state.sessions[1].messages=[];await emit();
 await expect(row('source-race')).toHaveCount(0);
 await expect(page.getByText('Private retained payload from account one.',{exact:true})).toHaveCount(0);
 await assertRetained();
 assertPassive();await screenshot('mobile-source-isolation');
 completed.push('late old-source reply fenced; new positive receipt/snapshot cannot retire or project old scoped payload');

 // Exact active input, Stop-held, known failure, queued/sending and steering are
 // not automatically recovered or resumed, even after another SSE open.
 const excludedCount=recoveryCalls().length;
 state.sessions[1].status='working';
 state.sessions[1].execution={currentTurnId:'active-input'};
 state.sessions[1].messages=[
  message('active-input'),
  message('stop-held',{status:'held',reason:'Message retained after Stop. Nothing was sent.'}),
  message('known-failure',{status:'failed',error:'Known startup failure.'}),
  message('sending',{status:'sending'}),message('queued',{status:'queued'}),
  message('update-queued',{status:'queued_for_update'}),
  {...message('steering'),steering:{generationId:'run',disposition:'unknown'}},
 ];
 await emit();
 assert.equal(recoveryCalls().length,excludedCount,'Excluded states do not check on mount');
 await page.evaluate(()=>window.fixtureTransport.disconnect());
 await page.evaluate(value=>window.fixtureTransport.reconnect(value),wireState());
 await expect(row('stop-held').getByText('Message retained after Stop. Nothing was sent.',{exact:true})).toBeVisible();
 await expect(row('known-failure').locator('.a-message-delivery').filter({hasText:'Not sent'})).toBeVisible();
 await expect(row('steering').getByText('Steering delivery could not be confirmed. Nothing was resent.',{exact:true})).toBeVisible();
 for(let revision=0;revision<3;revision++)await emit();
 assert.equal(recoveryCalls().length,excludedCount);
 await assertRetained();
 assertPassive();await screenshot('mobile-held-and-active');
 completed.push('active, Stop-held, failed, sending, queued, update-queued and steering: no automatic check or resend');
 assert.deepEqual(errors,[]);
 assert.equal(stops.length,1,'Reconnect never replays the explicit Stop');
 assert.equal(pending.length,0,'All synthetic requests settled');
 await saveResult(true);
 console.log(`Delivery reconciliation browser passed: ${completed.length} checkpoints; senderCounter=0, duplicateExecution=0; artifacts ${artifact}`);
}catch(error){
 if(page)try{await screenshot('failure')}catch{}
 await saveResult(false,error);
 throw error;
}finally{
 if(browser)await browser.close();
 if(vite)await vite.close();
}