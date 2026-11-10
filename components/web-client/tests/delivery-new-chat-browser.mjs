// Real Vite UI, synthetic HTTP/SSE only. No model, runtime, or provider execution.
// Intentional first sends belong to this fixture, not the passive-recovery counts.
// Run from frontend: node tests/delivery-new-chat-browser.mjs
// Focus: AMPLIFIER_DELIVERY_NEW_CHAT_CASE=pending-next-draft node tests/delivery-new-chat-browser.mjs
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
import {shellScope,sameScope} from '../src/shell/resources.js';
import './composer-test-helpers.mjs';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const artifact=process.env.AMPLIFIER_TEST_ARTIFACTS||'/artifacts/delivery-new-chat';
const outboxKey='amplifier.messageOutbox.v1';
const sourceA={historyScope:'synthetic-host/account-a',hostInstanceId:'fixture-host-a'};
const sourceB={historyScope:'synthetic-host/account-b',hostInstanceId:'fixture-host-b'};
const workspace='/fixture',createdId='created',nativeId='native-created';
const createdScope=JSON.stringify([sourceA.historyScope,workspace,nativeId]);
const newScope=JSON.stringify([sourceA.historyScope,null,null]);
const allCases=['accepted','late-success','late-session-busy','unconfirmed-creation','late-creation','pending-next-draft'];
const selectedCases=process.env.AMPLIFIER_DELIVERY_NEW_CHAT_CASE?[process.env.AMPLIFIER_DELIVERY_NEW_CHAT_CASE]:allCases;
assert.ok(selectedCases.every(kind=>allCases.includes(kind)),`Unknown AMPLIFIER_DELIVERY_NEW_CHAT_CASE; choose ${allCases.join(', ')}`);
const cases=[],reports=[];let vite,browser;

function initialState(){
 return {
  revision:1,settings:{workspace},runtime:{available:true},
  view:{navPinned:true,workSurface:'chat',setupJourney:'done',newSessionDraft:{workspace}},
  // A previous chat keeps this fixture on the ordinary New chat surface rather
  // than the empty-install onboarding journey; no conversation is selected.
  sessions:[{id:'previous',title:'Previous conversation',sessionKind:'root',workspace,workspaceId:'project',
   status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],
  workspaces:[{id:'project',name:'Fixture',path:workspace,available:true}],
  selectedSessionId:null,selectedWorkspaceId:'project',
  setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],
   providersLoadedAt:1,providersWorkspace:workspace},
  registeredBundles:[{value:'work',label:'Work'}],
  draftDefaults:{[JSON.stringify([workspace,''])]:{
   phase:'ready',bundle:'work',effective:{instance:'fixture',model:'fixture-model'},providers:[{id:'fixture'}],
  }},
  canvas:{open:false},
 };
}

// The shared shell fixture has one fixed host. Adapt only its public envelope
// identity so HTTP shell resources and SSE frames agree when this test switches
// sources; no production hook or action implementation is replaced.
function identify(value,source){
 if(!value||typeof value!=='object')return value;
 return {...value,
  ...(value.hostInstanceId?{hostInstanceId:source.hostInstanceId}:{}),
  ...(value.client?{client:{...value.client,...source}}:{}),
  ...(value.shellResources?{shellResources:{...value.shellResources,hostInstanceId:source.hostInstanceId}}:{}),
 };
}

async function runCase(kind){
 const report={case:kind,passed:false,checkpoints:[],errors:[],externalRequests:[],providerExecution:[],
  intentionalSendCount:0,retryCount:0,duplicateExecution:0,screenshots:[],staleShellDemands:[]};
 reports.push(report);
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 const page=await context.newPage();
 let state=initialState(),source=sourceA,creation,send,nextDraftSave,nextDraftText,nextDraftEvidence;
 const actions=[],seenSends=new Set(),shell=createShellHttpFixture(()=>state);
 const wire=()=>identify(shell.state(),source);
 const current=()=>page.evaluate(()=>window.amplifier.getState());
 const outbox=()=>page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)||'[]'),outboxKey);
 const journal=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.pendingDrafts.v1')||'null'));
 const resumeJournal=()=>page.evaluate(async()=>{
  const key='amplifier.pendingDrafts.v1',before=sessionStorage.getItem(key),saved=JSON.parse(before||'null');
  // Exercise the exported production parser against a copy. resume() persists
  // ownership, so never run the probe against the live composer's storage.
  const values=new Map(before===null?[]:[[key,before]]);
  const storage={getItem:name=>values.get(name)??null,setItem:(name,value)=>values.set(name,value),removeItem:name=>values.delete(name)};
  const {createDraftJournal}=await import('/src/draft-journal.js');
  const resumed=createDraftJournal('synthetic-resume-probe',()=>storage);
  resumed.resume(saved?.owner);
  return {entries:resumed.entries(),liveUnchanged:sessionStorage.getItem(key)===before};
 });
 const count=action=>actions.filter(row=>row.action===action).length;
 const screenshot=async name=>{
  const path=`${artifact}/${kind}-${name}.png`;
  await page.screenshot({path});report.screenshots.push(path);
 };
 const emit=async()=>{
  const revision=++state.revision;
  await page.evaluate(value=>window.emitState(value),wire());
  await expect.poll(async()=>(await current()).revision).toBeGreaterThanOrEqual(revision);
 };
 const reply=async(route,extra={})=>{
  state.revision++;
  await route.fulfill({json:{accepted:true,state:wire(),...extra}});
 };
 const assertNoReplay=()=>{
  assert.equal(count('session.create'),1);
  assert.equal(count('conversation.send'),['unconfirmed-creation','late-creation'].includes(kind)?0:1);
  assert.equal(count('conversation.retry'),0);
  assert.equal(report.duplicateExecution,0);
  assert.deepEqual(report.providerExecution,[]);
  assert.deepEqual(report.externalRequests,[]);
 };
 page.on('pageerror',error=>report.errors.push(error.message));
 await page.addInitScript(installFixtureEvents);
 try{
  const url=vite.resolvedUrls.local[0],origin=new URL(url).origin;
  await page.route('**/*',async route=>{
   const request=route.request(),address=new URL(request.url());
   if(address.origin!==origin){
    report.externalRequests.push(request.url());return route.abort('blockedbyclient');
   }
   if(!address.pathname.startsWith('/api/'))return route.continue();
   if(['/api/shell','/api/shell/resources'].includes(address.pathname)){
    const demand=address.pathname==='/api/shell/resources'?request.postDataJSON():{
     clientId:address.searchParams.get('clientId'),demandId:Number(address.searchParams.get('demandId')),
     resources:JSON.parse(address.searchParams.get('navigationResources')),scope:JSON.parse(address.searchParams.get('scope')),
    };
    // Scope can legitimately change between request dispatch and this fixture
    // handling it. Match shell_resources.register's bounded 409, rather than
    // letting the simpler shared fixture's sameScope assertion obscure the
    // delivery regression. Do not change state, grant stale coverage, or weaken
    // any source-adoption/journal assertions below.
    if(Array.isArray(demand.scope)&&demand.scope.length===5&&
     demand.scope.every(value=>value===null||typeof value==='string')&&!sameScope(demand.scope,shellScope(state))){
     assert.equal(demand.clientId,shell.state().client.id);
     assert.ok(Number.isSafeInteger(demand.demandId)&&demand.demandId>0);
     assert.ok(Array.isArray(demand.resources));
     report.staleShellDemands.push({demandId:demand.demandId,scope:demand.scope,currentScope:shellScope(state)});
     return route.fulfill({status:409,json:{error:'Shell scope changed; request the current client scope.'}});
    }
   }
   const response=shell.response(request);
   if(response!==undefined)return route.fulfill({json:identify(response,source)});
   if(address.pathname==='/api/state')return route.fulfill({json:wire()});
   if(address.pathname==='/api/actions'&&request.method()==='GET')return route.fulfill({json:[]});
   if(address.pathname!=='/api/actions')return route.fulfill({json:{ok:true}});
   const raw=request.postDataJSON();
   const body=raw.action==='shell.command'?{...raw,action:raw.args.action,args:raw.args.args}:
    raw.action==='shell.view.update'?{...raw,action:'view.update'}:raw;
   actions.push(body);
   if(body.action==='session.create'){
    assert.equal(request.method(),'POST');
    assert.equal(creation,undefined,'The first submission must create only one chat');
    creation={route,body};return;
   }
   if(body.action==='conversation.send'){
    assert.equal(request.method(),'POST');
    report.intentionalSendCount++;
    if(seenSends.has(body.id))report.duplicateExecution++;
    seenSends.add(body.id);
    if(send)return route.fulfill({status:409,json:{accepted:false,error:'Duplicate first send refused by fixture.'}});
    send={route,body,frame:structuredClone(wire())};return;
   }
   if(body.action==='conversation.retry'){
    report.retryCount++;
    return route.fulfill({status:409,json:{accepted:false,error:'Fixture forbids all retry execution.'}});
   }
   if(/^(worker\.spawn|call\.start|probe\.spawn|runtime\.start)$/.test(body.action)){
    report.providerExecution.push(body.action);
    return route.fulfill({status:409,json:{accepted:false,error:'Fixture has no provider or runtime.'}});
   }
   if(body.action==='conversation.delivery'){
    const target=state.sessions.find(session=>session.id===body.args.sessionId);
    const deliverySource={workspace:target.workspace||null,nativeIdentity:target.runtimeSessionId||target.nativeIdentity||target.id};
    return reply(route,{result:{sessionId:body.args.sessionId,inputId:body.args.inputId,
     source:deliverySource,delivery:'unknown',message:'Synthetic passive read only. Nothing was resent.'}});
   }
   if(kind==='pending-next-draft'&&nextDraftText!==undefined&&body.action==='view.update'&&body.args.patch?.draft===nextDraftText){
    assert.equal(nextDraftSave,undefined,'One newer draft autosave must have one pending owner');
    nextDraftSave={route,body};return;
   }
   if(body.action==='workspace.list')return reply(route,{result:{items:state.workspaces,nextOffset:null}});
   if(body.action==='view.update')state.view={...state.view,...body.args.patch};
   if(body.action==='session.select')state.selectedSessionId=body.args.id;
   return reply(route);
  });
  await page.goto(url);
  const composer=page.getByRole('textbox',{name:'Message Amplifier'});
  await expect(composer).toBeVisible();
  await expect(page.getByRole('banner')).toContainText('New chat');
  assert.equal((await current()).selectedSessionId,null);
  assert.deepEqual(await outbox(),[]);
  const submitted=`First new-chat request: ${kind}`;
  await composer.fill(submitted);
  const sendButton=page.getByRole('button',{name:'Send message',exact:true});
  await expect(sendButton).toBeEnabled();await sendButton.click();
  await expect.poll(()=>!!creation).toBe(true);
  const beforeCreation=await outbox();
  assert.equal(beforeCreation.length,1);
  const original=beforeCreation[0];
  assert.equal(original.text,submitted);
  assert.equal(original.sessionId,null);
  assert.equal(original.scope,newScope);
  assert.equal(original.creation.id,creation.body.id);
  assert.equal(creation.body.args.workspace,workspace);
  assert.equal(count('conversation.send'),0,'Send waits for a canonical created chat');
  report.checkpoints.push('rendered Send retains initial scoped payload and issues one session.create');

  if(kind==='pending-next-draft'){
   // Establish the baseline and matching shell demand before timing the real
   // composer edit. The fixture does not advance clocks or invoke saveDraft.
   await emit();
   await expect.poll(()=>page.evaluate(()=>{
    const current=window.amplifier.getState(),shell=window.amplifier.getShellState();
    return !!shell&&shell.hostInstanceId===current.client.hostInstanceId&&shell.shellResources?.scope?.[0]===null;
   })).toBe(true);
   await expect(composer).toHaveDraft('');
   nextDraftText=submitted; // Deliberately identical text, but a newer revision.
   await page.evaluate(expectedText=>{
    const nativeSetTimeout=window.setTimeout,watch={scheduled:[],fired:[],armed:true};
    const read=()=>JSON.parse(sessionStorage.getItem('amplifier.pendingDrafts.v1')||'null');
    window.nextDraftAutosaveEvidence=watch;
    window.setTimeout=function(callback,delay,...args){
     const staged=read(),row=staged?.rows?.find(row=>row.text===expectedText);
     if(!watch.armed||delay!==220||typeof callback!=='function'||!row)
      return nativeSetTimeout.call(this,callback,delay,...args);
     watch.armed=false;
     watch.scheduled.push({at:performance.now(),journal:staged});
     // Observe the actual registered callback at its actual browser deadline.
     // One microtask after it runs also exposes await(nonPromise)'s premature
     // bind; the fixed callback instead remains waiting on owner.promise.
     return nativeSetTimeout.call(this,function(...values){
      try{return callback.apply(this,values)}
      finally{queueMicrotask(()=>{
       watch.fired.push({at:performance.now(),journal:read()});
       window.setTimeout=nativeSetTimeout;
      })}
     },delay,...args);
    };
   },nextDraftText);
   await composer.fill(nextDraftText);
   await page.waitForFunction(()=>window.nextDraftAutosaveEvidence.fired.length===1,null,{timeout:5000});
   nextDraftEvidence=await page.evaluate(()=>window.nextDraftAutosaveEvidence);
   report.autosaveCallback=nextDraftEvidence;
   const staged=nextDraftEvidence.scheduled[0].journal;
   const stagedRow=staged.rows.find(row=>row.text===nextDraftText);
   assert.ok(stagedRow&&typeof stagedRow.revision==='string');
   assert.equal(stagedRow.sessionId,null);
   const saved=await journal(),pendingRow=saved?.rows?.find(row=>row.revision===stagedRow.revision);
   report.pendingDraftJournal=saved;
   assert.ok(pendingRow,'The newer draft revision must remain journaled while creation is pending');
   assert.ok(Object.hasOwn(pendingRow,'sessionId'),
    'AUTOSAVE CAUSAL REGRESSION: pending draft lost sessionId after the real callback; awaiting the owner object bound undefined instead of waiting for owner.promise');
   assert.equal(pendingRow.sessionId,null,'No canonical conversation exists yet; autosave must retain explicit null sessionId');
   assert.equal(pendingRow.text,nextDraftText);
   assert.deepEqual(saved,staged,'Pending creation cannot rewrite the newer journal revision or payload');
   const resumed=await resumeJournal();
   assert.equal(resumed.liveUnchanged,true);
   assert.deepEqual(resumed.entries,[stagedRow],'Actual createDraftJournal.resume must recover the explicit null-session draft');
   report.pendingDraftResume=resumed.entries;
   assert.equal(nextDraftSave,undefined,'Autosave cannot post a draft until the creation owner resolves');
   assert.equal(count('conversation.send'),0);
   assert.deepEqual(await outbox(),beforeCreation);
   await expect(composer).toHaveDraft(nextDraftText);
   report.checkpoints.push('real 220ms autosave fired while creation held; explicit null sessionId/revision/text remain resumable without premature draft POST');
  }

  if(kind==='late-creation'){
   // The initial Send is still waiting on creation, not conversation.send.
   // Supply a complete canonical A chat in the late receipt so a bare-id or
   // fallback lookup cannot accidentally make this test pass by being absent.
   const late=structuredClone(wire());
   late.sessions.unshift({
    id:createdId,title:'STALE SOURCE A CREATED CHAT',runtimeSessionId:nativeId,
    nativeIdentity:'fallback-not-the-owner',workspace,workspaceId:'project',sessionKind:'root',
    status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[],
    creationCommandId:creation.body.id,
   });
   late.selectedSessionId=createdId;delete late.view.newSessionDraft;
   await page.evaluate(()=>{
    window.creationSourceAudit=[];
    window.stopCreationSourceAudit=window.amplifier.subscribe(state=>{
     window.creationSourceAudit.push({
      historyScope:state.client?.historyScope,hostInstanceId:state.client?.hostInstanceId,
      selectedSessionId:state.selectedSessionId,sessionIds:state.sessions.map(session=>session.id),
     });
    });
   });
   // Keep B on its own new-chat draft. No action follows the source change.
   source=sourceB;
   state.sessions[0].title='Source B previous conversation';
   await emit();
   const baseline=await current();
   assert.equal(baseline.client.historyScope,sourceB.historyScope);
   assert.equal(baseline.client.hostInstanceId,sourceB.hostInstanceId);
   assert.equal(baseline.selectedSessionId,null);
   assert.equal(baseline.sessions.some(session=>session.id===createdId),false);
   await expect(page.locator(`[data-input-id="${original.commandId}"]`)).toHaveCount(0);
   assert.deepEqual(await outbox(),beforeCreation,'B baseline must not rebind the pending A creation payload');
   assertNoReplay();
   report.checkpoints.push('source B new-chat baseline adopted while source A session.create is still held');

   late.revision=state.revision+100; // Higher revision cannot legitimize an old source.
   report.heldResponse={action:'session.create',status:200,sessionId:createdId,
    historyScope:late.client.historyScope,hostInstanceId:late.client.hostInstanceId,revision:late.revision};
   await creation.route.fulfill({json:{accepted:true,sessionId:createdId,state:late}});
   // This persisted transition proves the real creation/send chain settled;
   // neither route.fulfill nor an unchanged B DOM proves completion.
   await expect.poll(async()=>(await outbox()).find(entry=>entry.id===original.id)?.status).toBe('unknown');
   const retained=await outbox();
   assert.equal(retained.length,1);
   assert.deepEqual(retained[0],{...original,status:'unknown',error:retained[0].error},
    'Only uncertainty/error may change; original A draft scope, identity and payload stay intact');
   assert.equal(retained[0].scope,newScope);
   assert.equal(retained[0].sessionId,null);
   assert.match(retained[0].error,/target changed.*original submission remains saved/i);
   const selected=await current();
   assert.equal(selected.client.historyScope,sourceB.historyScope);
   assert.equal(selected.client.hostInstanceId,sourceB.hostInstanceId);
   assert.equal(selected.selectedSessionId,null);
   assert.equal(selected.sessions.some(session=>session.id===createdId),false);
   await expect(page.getByRole('banner')).toContainText('New chat');
   await expect(page.getByText(submitted,{exact:true})).toHaveCount(0);
   await expect(page.getByText('STALE SOURCE A CREATED CHAT',{exact:true})).toHaveCount(0);
   for(let i=0;i<2;i++)await emit();
   const adopted=await page.evaluate(()=>window.creationSourceAudit);
   assert.ok(adopted.length>=3,'Record B baseline and subsequent actual state adoptions, not only unchanged DOM');
   assert.ok(adopted.every(frame=>frame.historyScope===sourceB.historyScope&&frame.hostInstanceId===sourceB.hostInstanceId&&
    frame.selectedSessionId===null&&!frame.sessionIds.includes(createdId)),
   'Late creation success must never transiently adopt A or select its created chat');
   report.stateAdoptions={count:adopted.length,frames:adopted};
   await page.evaluate(()=>window.stopCreationSourceAudit());
   assert.deepEqual(await outbox(),retained);
   assert.equal(count('conversation.delivery'),0,'Unbound-to-chat A payload cannot reconcile against B');
   assertNoReplay();
   await screenshot('source-b-creation-fenced');
   report.checkpoints.push('higher-revision complete A creation receipt fenced; A new-chat payload stays unknown; zero conversation.send POSTs');
  }else if(kind==='unconfirmed-creation'){
   // A success-shaped id is not the canonical workspace/native identity record.
   await reply(creation.route,{sessionId:createdId});
   await expect.poll(async()=>(await outbox())[0]?.status).toBe('unknown');
   const retained=await outbox();
   assert.equal(retained.length,1);
   assert.equal(retained[0].scope,newScope);
   assert.equal(retained[0].text,submitted);
   assert.match(retained[0].error,/new conversation is not confirmed.*original submission remains saved/i);
   await expect(page.locator('[data-input-id="'+original.commandId+'"]').getByText(/new conversation is not confirmed/i)).toBeVisible();
   assert.equal((await current()).selectedSessionId,null);
   assertNoReplay();
   await screenshot('honest-no-canonical-chat');
   report.checkpoints.push('missing canonical created record retains payload and reports refusal; zero sends');
  }else{
   const created={
    id:createdId,title:'Source A created conversation',runtimeSessionId:nativeId,nativeIdentity:'fallback-not-the-owner',
    workspace,workspaceId:'project',sessionKind:'root',status:'idle',historyManaged:true,historyLoaded:true,
    messages:[],workers:[],creationCommandId:creation.body.id,
   };
   state.sessions.unshift(created);state.selectedSessionId=createdId;delete state.view.newSessionDraft;
   // The receipt gives only the id as its direct result. The full record lives
   // in canonical state and carries fields missing from the old {id} shortcut.
   await reply(creation.route,{sessionId:createdId});
   await expect.poll(()=>!!send).toBe(true);
   assert.equal(send.body.args.sessionId,createdId);
   assert.equal(send.body.args.text,submitted);
   assert.equal(send.body.id,original.commandId);
   await expect.poll(async()=>(await outbox())[0]?.scope).toBe(createdScope);
   const pending=await outbox();
   assert.equal(pending.length,1);
   assert.equal(pending[0].sessionId,createdId);
   assert.equal(pending[0].scope,createdScope);
   assert.equal(pending[0].status,'sending');
   assert.equal(pending[0].text,submitted);
   await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
   for(let i=0;i<2;i++)await emit();
   assertNoReplay();
   report.checkpoints.push('canonical workspace/native identity binds outbox; exact first conversation.send posts once');

   if(kind==='pending-next-draft'){
    await expect.poll(()=>!!nextDraftSave).toBe(true);
    assert.equal(nextDraftSave.body.args.sessionId,createdId);
    assert.equal(nextDraftSave.body.args.patch.draft,nextDraftText);
    assert.equal(actions.filter(row=>row.action==='view.update'&&row.args.patch?.draft===nextDraftText&&row.args.sessionId===createdId).length,1);
    const stagedRow=nextDraftEvidence.scheduled[0].journal.rows.find(row=>row.text===nextDraftText);
    const bound=await journal(),boundRow=bound?.rows?.find(row=>row.revision===stagedRow.revision);
    assert.ok(boundRow,'The same newer draft revision must remain journaled until its own autosave ACK');
    assert.equal(boundRow.sessionId,createdId);
    assert.equal(boundRow.text,nextDraftText);
    assert.equal(boundRow.revision,stagedRow.revision);
    const resumed=await resumeJournal();
    assert.equal(resumed.liveUnchanged,true);
    assert.equal(resumed.entries.length,1);
    assert.deepEqual(resumed.entries[0],boundRow);
    report.boundDraftResume=resumed.entries;
    await expect(composer).toHaveDraft(nextDraftText);
    assert.equal(send.body.args.text,submitted);
    assertNoReplay();
    report.checkpoints.push('canonical creation binds the same newer draft revision to created chat; only the original submission posts');

    // Hold the newer autosave ACK through the original send ACK. The server's
    // older empty draft must not clear the newer identical local draft.
    assert.equal(state.view.draft,'');
    created.messages=[{id:'received',inputId:send.body.id,role:'user',text:submitted,delivery:{status:'accepted'}}];
    await reply(send.route,{delivery:'accepted'});
    await expect.poll(async()=>(await outbox()).length).toBe(0);
    await expect(composer).toHaveDraft(nextDraftText);
    assert.deepEqual(await journal(),bound);
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
    assertNoReplay();
    await screenshot('newer-draft-after-first-ack');
    state.view={...state.view,...nextDraftSave.body.args.patch};
    await reply(nextDraftSave.route);
    await expect.poll(async()=>(await journal())?.rows?.length??0).toBe(0);
    await expect(composer).toHaveDraft(nextDraftText);
    await page.reload();await expect(composer).toHaveDraft(nextDraftText);
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
    assert.deepEqual(await outbox(),[]);
    assertNoReplay();
    report.checkpoints.push('late first ACK leaves newer identical draft intact; its own autosave ACK and reload preserve it without a second send');
   }else if(kind==='accepted'){
    created.messages=[{id:'received',inputId:send.body.id,role:'user',text:submitted,delivery:{status:'accepted'}}];
    await reply(send.route,{delivery:'accepted'});
    await expect.poll(async()=>(await outbox()).length).toBe(0);
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
    await expect(page.getByText(submitted,{exact:true})).toBeVisible();
    await page.reload();await expect(composer).toBeVisible();
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(1);
    assert.deepEqual(await outbox(),[]);
    assertNoReplay();
    await screenshot('accepted-once');
    report.checkpoints.push('first send accepted once; reload keeps one saved message and no replay');
   }else{
    // Install B first with identical public/native chat ids so only source
    // identity prevents an A reply from being accepted into B.
    source=sourceB;
    state.sessions=[{...created,title:'Source B conversation',messages:[
     {id:'b-baseline',role:'assistant',text:'Source B baseline stays selected.',displayOnly:true},
    ]}];
    state.view={...state.view,draft:''};
    await emit();
    await expect(page.getByText('Source B baseline stays selected.',{exact:true})).toBeVisible();
    await expect(page.locator(`[data-input-id="${send.body.id}"]`)).toHaveCount(0);
    assert.deepEqual((await current()).client.historyScope,sourceB.historyScope);
    await page.evaluate(()=>{
     window.deliverySourceAudit=[];
     window.stopDeliverySourceAudit=window.amplifier.subscribe(state=>{
      window.deliverySourceAudit.push({historyScope:state.client?.historyScope,hostInstanceId:state.client?.hostInstanceId});
     });
    });
    const late=structuredClone(send.frame);
    // Make this newer numerically than B. Revision ordering alone cannot pass.
    late.revision=state.revision+100;
    const oldChat=late.sessions.find(row=>row.id===createdId);
    oldChat.messages=[{id:'old-send-receipt',inputId:send.body.id,role:'user',text:submitted,
     delivery:{status:kind==='late-success'?'accepted':'unknown'}}];
    oldChat.title='STALE SOURCE A RECEIPT';
    const response=kind==='late-success'
     ?{accepted:true,delivery:'accepted',state:late}
     :{accepted:false,code:'session_busy',error:'Synthetic old-source session_busy',status:409,state:late};
    report.heldResponse={status:kind==='late-success'?200:409,code:response.code,
     historyScope:late.client.historyScope,hostInstanceId:late.client.hostInstanceId,revision:late.revision};
    await send.route.fulfill({status:kind==='late-success'?200:409,json:response});
    // Persisted status changes only after the real send promise/catch settles.
    // Do not infer completion from route.fulfill or an unchanged B screenshot.
    await expect.poll(async()=>(await outbox()).find(entry=>entry.id===original.id)?.status).toBe(kind==='late-success'?'unknown':'failed');
    const retained=await outbox();
    assert.equal(retained.length,1);
    assert.equal(retained[0].scope,createdScope);
    assert.equal(retained[0].sessionId,createdId);
    assert.equal(retained[0].commandId,send.body.id);
    assert.equal(retained[0].text,submitted);
    assert.deepEqual(retained[0].attachmentIds,original.attachmentIds);
    if(kind==='late-success')assert.match(retained[0].error,/target changed|source changed|original submission remains saved/i);
    // session_busy deliberately suppresses the generic error string: its
    // conversation notice is owned elsewhere. The regression is source/state
    // isolation and retained payload, not a new error presentation contract.
    const selected=await current();
    assert.equal(selected.client.historyScope,sourceB.historyScope);
    assert.equal(selected.client.hostInstanceId,sourceB.hostInstanceId);
    assert.equal(selected.selectedSessionId,createdId);
    assert.equal(selected.sessions.find(row=>row.id===createdId).title,'Source B conversation');
    assert.ok(!selected.sessions.some(row=>row.messages?.some(message=>message.inputId===send.body.id)));
    await expect(page.getByText('Source B baseline stays selected.',{exact:true})).toBeVisible();
    await expect(page.getByText(submitted,{exact:true})).toHaveCount(0);
    await expect(page.getByText('STALE SOURCE A RECEIPT',{exact:true})).toHaveCount(0);
    for(let i=0;i<2;i++)await emit();
    const adopted=await page.evaluate(()=>window.deliverySourceAudit);
    assert.ok(adopted.length>0,'Observe real state adoption after the held response settles');
    assert.ok(adopted.every(client=>client.historyScope===sourceB.historyScope&&client.hostInstanceId===sourceB.hostInstanceId),
     'Neither success nor error may transiently adopt an old source frame');
    await page.evaluate(()=>window.stopDeliverySourceAudit());
    assert.deepEqual(await outbox(),retained);
    assert.equal(count('conversation.delivery'),0,'Old scoped outbox never projects or automatically checks against B');
    assertNoReplay();
    await screenshot('source-b-retained');
    report.checkpoints.push('source B baseline precedes held A reply; higher-revision old frame never adopted; A payload retained without projection or replay');
   }
  }
  assert.deepEqual(report.errors,[]);
  assertNoReplay();
  report.actions=actions.map(({action,id,args})=>({action,id,args}));
  report.passed=true;cases.push(kind);
 }catch(error){
  report.actions=actions.map(({action,id,args})=>({action,id,args}));
  report.failure=String(error.stack||error);
  try{await screenshot('failure')}catch{}
  throw error;
 }finally{
  await context.close();
 }
}

await mkdir(artifact,{recursive:true});
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),
  server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true,
  ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
 });
 for(const kind of selectedCases)await runCase(kind);
 console.log(`New-chat delivery browser passed: ${cases.join(', ')}. Synthetic transport only; no provider execution. Artifacts: ${artifact}`);
}finally{
 await writeFile(`${artifact}/browser-result.json`,JSON.stringify({
  passed:cases.length===selectedCases.length,selectedCases,completedCases:cases,
  evidence:'Actual rendered new-chat Send with synthetic HTTP/SSE; fresh context per case. No live runtime/provider qualification.',
  reports,
 },null,2));
 if(browser)await browser.close();
 if(vite)await vite.close();
}