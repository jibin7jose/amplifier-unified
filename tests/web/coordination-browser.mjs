import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {observeCoordinationDisclosures} from './coordination-disclosure-diagnostics.mjs';
import {startCoordinationFixture} from './coordination-fixture.mjs';
import {createHash} from 'node:crypto';
import {mkdir,mkdtemp,writeFile,unlink} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python';
const outputBase=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(outputBase,{recursive:true});
const outputDir=await mkdtemp(join(outputBase,'coordination-browser-'));
const fixture=await startCoordinationFixture({root,python,output:outputDir});
let browser,page,disclosureFacts,failure,result;
try{
 const url=fixture.base;
 // ARM containers can opt into single-process Chromium for this fixture.
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 disclosureFacts=await observeCoordinationDisclosures(page);
 page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const uiActions=[];
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')uiActions.push(request.postDataJSON())});
 await page.goto(url);
 await showFixtureConversation(page);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Preserve this unsent draft');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 const expandControls=async()=>{for(const name of ['Follow up or watch work','Exact requests, messages and saved results']){const summary=page.getByText(name,{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click()}};
 await expandControls();
 const first=page.getByRole('region',{name:'Worker: First worker'}),second=page.getByRole('region',{name:'Worker: Second worker'});
 await first.getByRole('checkbox').check();
 await second.getByRole('checkbox').check();
 const current=await page.request.get(url+'/fixture').then(response=>response.json());
 const emit=data=>page.request.post(url+'/fixture/emit',{data});
 await emit({id:'worker-b',status:'idle',report:'Second worker finished its first report.',reportId:'b-report-1'});
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await first.getByText('Follow up',{exact:true}).click();
 await first.getByLabel('Follow-up for First worker').fill('Investigate the remaining check');
 await first.getByRole('button',{name:'Send follow-up',exact:true}).click();
 await expect(page.getByText('Follow-up accepted.',{exact:true})).toBeVisible();
 let observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.deepEqual(observed.messages.map(({sessionId,workerId,text})=>({sessionId,workerId,text})),[{sessionId:current.other,workerId:'worker-a',text:'Investigate the remaining check'}]);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 await expect(composer).toHaveDraft('Preserve this unsent draft');
 // Reconnect with retained cursors: one stable DOM report, no repeated command.
 const savedWatch=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier-coordination-v1')));
 const secondKey=JSON.stringify([current.other,'worker-b']);
 assert(savedWatch.selected.some(target=>target.sessionId===current.other&&target.workerId==='worker-b'),'Worker selection must be saved before reload');
 assert(savedWatch.cursors[secondKey],'Worker cursor must be saved before reload');
 assert.equal(savedWatch.results[secondKey].filter(report=>report.id==='b-report-1').length,1,'Report body must be saved before reload');
 await page.reload();await expandControls();
 const restoredWatch=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier-coordination-v1')));
 assert(restoredWatch.selected.some(target=>target.sessionId===current.other&&target.workerId==='worker-b'),'Reload must retain the selected worker');
 assert.equal(restoredWatch.results[secondKey].filter(report=>report.id==='b-report-1').length,1,'Reload must retain the report body independently of the target list');
 await expect(second).toBeVisible();
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await expect(second.getByRole('checkbox')).toBeChecked();
 observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.equal(observed.messages.length,1);assert.equal(observed.stops.length,0);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 let interruptedRead=false;
 await page.route('**/api/actions',async route=>{
  if(!interruptedRead&&route.request().method()==='POST'&&route.request().postDataJSON()?.action==='coordination.wait'){interruptedRead=true;await route.abort('connectionreset')}else await route.continue();
 });
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();await expandControls();
 await expect(page.getByText(/Waiting to reconnect:/)).toBeVisible();
 await emit({id:'worker-b',status:'idle',report:'Second worker finished its first report.',reportId:'b-report-1'});
 await emit({id:'worker-a',status:'idle',report:'First worker follow-up report.',reportId:'a-report-2'});
 await expect(first.locator('[data-report-id="a-report-2"]')).toHaveCount(1);
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 // An active long wait must not occupy the shared command queue.
 await first.getByRole('button',{name:'Interrupt',exact:true}).click();
 await expect(first.getByText('Outcome unknown · Needs attention',{exact:true})).toBeVisible();
 observed=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.equal(observed.messages.length,1);assert.equal(observed.stops.length,1);
 await page.locator('.a-dialog').evaluate(element=>element.scrollTop=0);
 await page.screenshot({path:join(outputDir,'amplifier-coordination-desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:join(outputDir,'amplifier-coordination-mobile.png'),animations:'disabled'});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveDraft('Preserve this unsent draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
 // Production service/DOM, but scripted generations and emulated anchors:
 // this is NOT native/runtime/live-model qualification.
 await page.setViewportSize({width:1100,height:900});
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const readCandidate=(actor,inputId)=>page.request.get(url+'/fixture/artifact?'+new URLSearchParams({actor,...(inputId?{inputId}:{})})).then(response=>response.json());
 const sha256=text=>createHash('sha256').update(text).digest('hex');
 const agent=(actor,action,args,id)=>page.request.post(url+'/fixture/peer',{data:{actor,action,args,id}}).then(async response=>{
  assert.equal(response.ok(),true,await response.text());return response.json();
 });
 const finish=actor=>page.request.post(url+'/fixture/finish',{data:{actor}}).then(async response=>{
  assert.equal(response.ok(),true,await response.text());return response.json();
 });
 const natural='Coordinate with Other conversation on this task';
 await composer.fill(natural);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(composer).toHaveDraft('');
 await expect(page.getByRole('log',{name:'Conversation messages'}).getByText(natural,{exact:true})).toBeVisible();
 // The rendered bubble can be optimistic. Wait for real fixture delivery,
 // not its text or an arbitrary delay, before inspecting transport lineage.
 await expect.poll(async()=>{
  const state=await inspect(),human=state.humanMessages[0];
  assert.deepEqual(state.failures,[]);
  return state.humanMessages.length===1&&state.contexts[current.selected]?.active
   &&state.events.some(row=>row.sessionId===current.selected&&row.event==='input.delivered'&&row.inputId===human.inputId);
 }).toBe(true);
 let peerState=await inspect();
 assert.deepEqual(peerState.failures,[]);
 assert.equal(peerState.humanMessages.length,1);
 const human=peerState.humanMessages[0];
 assert.equal(human.text,natural);assert.equal(human.inputOrigin,'ui');
 assert.equal(human.hostAction,undefined);assert.equal(human.peerEnvelope,undefined);
 assert.equal(human.inputId,peerState.sent.find(row=>row.kind==='human').inputId);
 assert.deepEqual(peerState.contexts[current.selected].inputIds,[human.inputId]);
 const humanContext=peerState.contexts[current.selected];
 assert.equal(humanContext.active,true);
 assert.deepEqual(peerState.approvals,[]);assert.deepEqual(peerState.approvalResponses,[]);
 assert.deepEqual(peerState.coordination.grants,[]);assert.deepEqual(peerState.coordination.proposals,[]);
 assert.equal(peerState.events.filter(row=>row.sessionId===current.selected&&row.event==='input.delivered'&&row.inputId===human.inputId).length,1);
 await composer.fill('Preserve this unsent draft');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
 await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
 await expandControls();
 const related=page.getByRole('region',{name:'Workspace work overview'});
 const preserve=async()=>{
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),current.selected);
  await expect(composer).toHaveDraft('Preserve this unsent draft');
  assert.equal(uiActions.filter(row=>row.action==='session.select').length,0);
 };
 const noForms=async()=>{
  // File intent is an explicitly opened optional form. Ordinary related-work
  // browsing still requires no commissioning or authorization paperwork.
  await expect(related.locator('form,input,textarea,select').filter({visible:true})).toHaveCount(0);
  for(const text of ['Authorize collaboration','Commission durable task','Pending until a human decides']){
   await expect(related.getByText(text,{exact:true})).toHaveCount(0);
  }
  await expect(related.getByRole('button',{name:/Approve exact proposal|Send peer message|Create task chat|Revoke grant/})).toHaveCount(0);
  await expect(page.locator('[data-part="approvals"]').getByRole('button',{name:'Allow',exact:true})).toHaveCount(0);
 };
 const binding=(state,id,sid,context)=>{
  const call=state.bridgeCalls.find(row=>row.id===id);
  assert.equal(call.sessionId,sid);assert.equal(call._runtimeSessionId,sid);
  assert.equal(call._generationId,context.generationId);
  assert.deepEqual(call._inputBindings.map(row=>row.inputId),context.inputIds);
  assert.equal(Object.hasOwn(call.args,'grantId'),false);
 };
 await noForms();await preserve();
 const tasks=[];
 for(let index=0;index<2;index++){
  const id='browser-create-'+index,args={title:'Fixture implementation '+(index+1),text:'Root '+(index+1)+' round one'};
  const created=await agent('source','coordination.create',args,id);
  assert.equal(created.accepted,true);assert.ok(created.sessionId);assert.ok(created.initialInputId);
  assert.equal(created.subscription.status,'reserved');
  assert.equal(created.result.sourceGenerationId,humanContext.generationId);
  assert.deepEqual(created.result.sourceInputIds,[human.inputId]);
  assert.equal(Object.hasOwn(created.result,'grantId'),false);
  await expect.poll(async()=>{
   const state=await inspect();
   return state.contexts[created.sessionId]?.active&&state.coordination.requests.find(row=>row.requestId===created.initialInputId)?.delivery==='accepted';
  }).toBe(true);
  peerState=await inspect();
  binding(peerState,id,current.selected,humanContext);
  const task=peerState.tasks.find(row=>row.id===created.sessionId);
  assert.equal(task.sessionKind,'root');assert.equal(task.workspace,peerState.workspace);
  assert.equal(task.bundle,peerState.bundle);
  assert.equal(task.collaboration.creatorSessionId,current.selected);
  assert.equal(task.collaboration.sourceGenerationId,humanContext.generationId);
  assert.deepEqual(task.collaboration.sourceInputIds,[human.inputId]);
  assert.equal(task.collaboration.configurationHash,created.result.configurationHash);
  assert.equal((await agent('source','coordination.create',args,id)).duplicate,true);
  tasks.push({...task,operation:created.requestId,brief:created.initialInputId,actor:index===0?'recipient':'recipient2'});
 }
 assert.notEqual(tasks[0].id,tasks[1].id);
 assert.notEqual(tasks[0].collaboration.outputNamespace,tasks[1].collaboration.outputNamespace);
 // A peer-woken task can notify a same-workspace peer without a fresh human
 // message. Notify must retain attribution but never start that target.
 peerState=await inspect();
 const recipientContext=peerState.contexts[tasks[0].id],sentBefore=peerState.sent.length;
 const notified=await agent('recipient','coordination.send',{
  sessionId:current.other,mode:'notify',text:'Consult the peer without a human relay',
 },'browser-notify');
 assert.equal(notified.delivery,'notified');
 assert.equal(notified.sourceGenerationId,recipientContext.generationId);
 assert.deepEqual(notified.sourceInputIds,[tasks[0].brief]);
 peerState=await inspect();binding(peerState,'browser-notify',tasks[0].id,recipientContext);
 assert.equal(peerState.sent.length,sentBefore);assert.equal(peerState.contexts[current.other],undefined);
 await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
 await related.getByText('Workspace chat list',{exact:true}).click();
 for(const task of tasks){
  // A working peer also appears in the activity overview. Assert the exact
  // chat-list row instead of matching both legitimate representations.
  const row=related.locator('[data-workspace-chat-id="'+task.id+'"]');
  await expect(row).toBeVisible();
  await expect(row).toContainText(task.title+' · Working · Commissioned chat');
  await expect(row.getByRole('button',{name:'Open chat: '+task.title,exact:true})).toBeEnabled();
 }
 const rounds=[],continuations=new Set();
 for(const task of tasks){
  let firstCandidate,firstGeneration;
  for(let round=0;round<2;round++){
   const requestId=round===0?task.brief:'browser-round-two-'+task.id;
   const text='Root '+(tasks.indexOf(task)+1)+(round===0?' round one':' round two');
   if(round===1){
    const sourceContext=(await inspect()).contexts[current.selected];
    assert.equal(sourceContext.active,true);
    assert.notEqual(sourceContext.generationId,humanContext.generationId);
    assert.equal(sourceContext.inputIds.length,1);
    assert.ok(continuations.has(sourceContext.inputIds[0]));
    const args={sessionId:task.id,mode:'queue',text,returnNotification:true};
    const queued=await agent('source','coordination.send',args,requestId);
    assert.equal(queued.accepted,true);
    assert.equal(queued.returnState.returnPolicy,'requested');
    assert.equal(queued.returnState.subscription.registered,true);
    assert.equal(queued.sourceGenerationId,sourceContext.generationId);
    assert.deepEqual(queued.sourceInputIds,sourceContext.inputIds);
    binding(await inspect(),requestId,current.selected,sourceContext);
    await expect.poll(async()=>(await inspect()).coordination.requests.find(row=>row.requestId===requestId)?.delivery).toBe('accepted');
    assert.equal((await agent('source','coordination.send',args,requestId)).duplicate,true);
   }
   peerState=await inspect();
   const context=peerState.contexts[task.id];
   assert.equal(context.active,true);assert.equal(peerState.statuses[task.id],'working');
   assert.deepEqual(context.inputIds,[requestId]);
   if(round===0)firstGeneration=context.generationId;
   else assert.notEqual(context.generationId,firstGeneration);
   assert.match(peerState.sent.find(row=>row.inputId===requestId).text,/not a new human instruction/);
   const candidate=await readCandidate(task.actor,requestId);
   assert.equal(candidate.text,text);assert.equal(candidate.sha256,sha256(text));
   assert.ok(candidate.path.includes('/'+task.collaboration.outputNamespace+'/'));
   if(round===0)firstCandidate=candidate;
   else{
    assert.notEqual(candidate.path,firstCandidate.path);
    assert.deepEqual(await readCandidate(task.actor,task.brief),firstCandidate);
   }
   const references=[candidate.path+'@sha256:'+candidate.sha256];
   const declaration=await agent(task.actor,'coordination.reply',{
    requestId,kind:'result',outcome:'success',text:'Deterministic artifact retained; independently check the file.',references,
   },requestId+':reply');
   assert.equal(declaration.result.status,'staged');assert.equal(declaration.result.qualified,false);
   assert.equal(declaration.result.artifactVerification,'verified');
   assert.equal(declaration.result.artifacts[0].status,'retained');
   binding(await inspect(),requestId+':reply',task.id,context);
   // Creation and opted-in sends already own their return. No separate
   // subscribe action is needed, even when the result arrives immediately.
   const returning=await agent('source','coordination.result',{requestId:round===0?task.operation:requestId},requestId+':return');
   const wait={accepted:true,result:(returning.result.initialRequest||returning.result.receipt).subscription};
   assert.equal(wait.accepted,true);assert.equal(wait.result.status,'waiting');
   const continuation=wait.result.continuationId;
   assert.ok(continuation);assert.equal(continuations.has(continuation),false);continuations.add(continuation);
   const staged=await agent('source','coordination.result',{requestId},requestId+':staged');
   assert.equal(staged.result.qualified,false);assert.equal(staged.result.qualificationSupported,true);
   assert.equal(staged.result.returnState.response.status,'staged');
   assert.equal(staged.result.returnState.subscription.registered,true);
   assert.equal(staged.result.returnState.notification.status,'not_queued');
   assert.deepEqual(staged.result.results,[]);
   assert.equal((await inspect()).sent.filter(row=>row.inputId===continuation).length,0);
   const terminal=(await finish(task.actor)).terminal,anchor=terminal.nativeTerminal;
   assert.equal(terminal.generation_id,context.generationId);assert.deepEqual(terminal.input_ids,[requestId]);
   assert.equal(terminal.rootSessionId,task.id);assert.equal(terminal.sessionId,task.id);
   assert.equal(terminal.disposition,'manager_turn_finished');assert.deepEqual(terminal.active_job_ids,[]);
   assert.equal(anchor.deterministicEmulation,true);
   assert.equal(anchor.rootSessionId,task.id);assert.equal(anchor.generationId,context.generationId);
   assert.equal(anchor.textDigest,sha256(JSON.stringify(terminal.text)));
   const canonical='['+[task.id,anchor.nativeIndex,'assistant',terminal.text].map(value=>JSON.stringify(value)).join(', ')+']';
   assert.equal(anchor.messageId,sha256(canonical).slice(0,32));
   const qualified=await agent('source','coordination.result',{requestId:round===0?task.operation:requestId},requestId+':result');
   assert.equal(qualified.result.qualified,true);assert.equal(qualified.result.qualificationSupported,true);
   assert.equal(qualified.result.results.length,1);
   const exact=qualified.result.results[0];
   assert.equal(exact.sessionId,task.id);assert.equal(exact.inputId,requestId);assert.equal(exact.messageId,anchor.messageId);
   assert.equal(exact.declaration.terminalMessageId,anchor.messageId);
   assert.equal(exact.declaration.generationId,context.generationId);assert.equal(exact.declaration.status,'sealed');
   assert.deepEqual(exact.declaration.references,references);
   assert.equal(exact.declaration.independentArtifactVerification,false);
   await expect.poll(async()=>{
    const state=await inspect();
    return state.sent.filter(row=>row.inputId===continuation).length===1
     &&state.contexts[current.selected]?.active
     &&state.coordination.requests.find(row=>row.requestId===continuation)?.delivery==='accepted';
   }).toBe(true);
   peerState=await inspect();
   assert.equal(peerState.statuses[task.id],'idle');
   assert.equal(peerState.contexts[task.id].active,false);
   const resumed=peerState.sent.find(row=>row.inputId===continuation);
   assert.equal(resumed.sessionId,current.selected);assert.equal(resumed.kind,'peer');
   assert.equal(resumed.peerEnvelope.senderSessionId,task.id);
   assert.equal(resumed.peerEnvelope.replyToRequestId,requestId);
   assert.equal(Object.hasOwn(resumed.peerEnvelope,'grantId'),false);
   assert.deepEqual(resumed.peerEnvelope.references,[anchor.messageId,...references]);
   assert.match(resumed.text,/Independently check the referenced artifact/);
   assert.deepEqual(peerState.contexts[current.selected].inputIds,[continuation]);
   assert.equal(peerState.statuses[current.selected],'working');
   assert.equal(peerState.events.filter(row=>row.sessionId===current.selected&&row.event==='generation.started'&&row.generation_id===peerState.contexts[current.selected].generationId).length,1);
   assert.equal(peerState.events.filter(row=>row.sessionId===current.selected&&row.event==='input.delivered'&&row.inputId===continuation).length,1);
   if(round!==0)assert.deepEqual(await readCandidate(task.actor,requestId),candidate);
   assert.deepEqual((await finish(task.actor)).terminal,terminal); // Duplicate observation, never another continuation.
   assert.equal((await inspect()).sent.filter(row=>row.inputId===continuation).length,1);
   await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
   const row=related.locator('[data-request-id="'+requestId+'"]');
   await expect(row).toHaveCount(1);await expect(row).toContainText(/Sent to .* · accepted/);
   await row.getByRole('button',{name:'Inspect exact result',exact:true}).click();
   const returnStatus=row.getByLabel('Request return status');
   await expect(returnStatus).toContainText('Automatic return: Registered');
   await expect(returnStatus).toContainText('accepted (input admitted)');
   await expect(returnStatus).toContainText('Delivery does not prove the requester consumed or accepted');
   await expect(row.locator('[data-part="exact-result"]')).toContainText('Result: sealed · success · Qualified terminal evidence');
   await expect(row).toContainText('not independently verified correctness');
   if(round===1&&task===tasks[0]){
    await returnStatus.scrollIntoViewIfNeeded();
    await returnStatus.screenshot({path:join(outputDir,'amplifier-return-status-desktop.png'),animations:'disabled'});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);
    await returnStatus.screenshot({path:join(outputDir,'amplifier-return-status-mobile.png'),animations:'disabled'});
    await page.setViewportSize({width:1100,height:900});
   }
   await preserve();await noForms();
   await expect(row.getByRole('button',{name:'Open exact terminal message',exact:true})).toHaveAttribute('data-message-id',anchor.messageId);
   await row.getByRole('button',{name:'Open exact terminal message',exact:true}).click();
   const original=related.getByRole('article',{name:'Original message'});
   await expect(original.getByText(terminal.text,{exact:true})).toBeVisible();
   await expect(original).toContainText('assistant · '+task.id);
   await preserve();
   const read=uiActions.filter(call=>call.action==='coordination.read').at(-1);
   assert.equal(read.args.sessionId,task.id);assert.equal(read.args.messageId,anchor.messageId);
   await original.getByRole('button',{name:'Close original message',exact:true}).click();
   // Source disappears after reporting but before its first artifact preview.
   if(round===0)await unlink(candidate.path);
   await row.getByRole('button',{name:'Open referenced artifact',exact:true}).click();
   await page.waitForFunction(text=>window.amplifier.getState().canvas?.content===text,candidate.text);
   if(round===0)await writeFile(candidate.path,candidate.text);
   const opened=uiActions.filter(call=>call.action==='canvas.openFile').at(-1);
   assert.equal(opened.args.sessionId,current.selected);assert.equal(opened.args.workspace,peerState.workspace);
   assert.equal(opened.args.path,candidate.path);
   assert.equal(opened.args.expectedSha256,candidate.sha256);
   assert.equal(opened.args.referenceRequestId,requestId);
   await preserve();
   await page.getByRole('button',{name:'Close panel',exact:true}).click();
   await expect(page.locator('[data-part="canvas"] .a-canvas-plain')).toContainText(candidate.text);
   await expect(page.locator('[data-part="canvas"] .a-canvas-plain')).toBeVisible();
   await preserve();
   await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
   await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
   await expandControls();
   rounds.push({task,requestId,candidate,qualified});
  }
 }
 const lastContext=(await inspect()).contexts[current.selected];
 const sourceTerminal=(await finish('source')).terminal;
 assert.equal(sourceTerminal.generation_id,lastContext.generationId);
 assert.deepEqual(sourceTerminal.input_ids,lastContext.inputIds);
 assert.equal(sourceTerminal.disposition,'manager_turn_finished');
 const executionCounts=state=>Object.fromEntries([current.selected,...tasks.map(task=>task.id)].map(sid=>[sid,{
  generations:state.events.filter(row=>row.sessionId===sid&&row.event==='generation.started').length,
  delivered:state.events.filter(row=>row.sessionId===sid&&row.event==='input.delivered').length,
  submitted:state.sent.filter(row=>row.sessionId===sid).length,
 }]));
 const beforePassive=await inspect();
 const beforePassiveCounts=executionCounts(beforePassive);
 for(const sid of Object.keys(beforePassiveCounts))assert.equal(beforePassive.contexts[sid].active,false);
 await page.reload();
 // Either panel can be restored. Re-observe rendered and public view state
 // within one bound if a delayed view update replaces the navigation target.
 // Only navigate from visible Work & automation; never normalize task/input state.
 const coordinationDialog=page.getByRole('dialog',{name:'Chat coordination review',exact:true});
 const detailsDialog=page.getByRole('dialog',{name:'Work & automation',exact:true});
 const reloadObservations=[];let reopenClicks=0;
 await expect(async()=>{
  const publicPanel=await page.evaluate(()=>window.amplifier?.getState()?.view?.panel??null);
  const coordinationVisible=await coordinationDialog.isVisible();
  const detailsVisible=await detailsDialog.isVisible();
  const observation={publicPanel,coordinationVisible,detailsVisible};
  if(JSON.stringify(reloadObservations.at(-1))!==JSON.stringify(observation))reloadObservations.push(observation);
  if(publicPanel==='chat-work'&&detailsVisible){
   await detailsDialog.getByRole('button',{name:'Tasks and workers',exact:true}).click({timeout:1000});
   reopenClicks++;
  }
  await expect(coordinationDialog).toBeVisible({timeout:1000});
  await expect(related.getByRole('heading',{name:'Working here',exact:true})).toBeVisible({timeout:1000});
  assert.equal(await page.evaluate(()=>window.amplifier.getState().view.panel),'coordination');
 }).toPass({timeout:10000,intervals:[100,250,500]});
 await expect(related.getByRole('heading',{name:'Working here',exact:true})).toBeVisible();
 await expandControls();
 await expect(related.locator('[data-request-id="'+rounds[0].requestId+'"]')).toBeVisible();
 console.log(JSON.stringify({kind:'coordination-reload',reloadObservations,reopenClicks,
  postReopen:{publicPanel:await page.evaluate(()=>window.amplifier.getState().view.panel),
   coordinationVisible:await coordinationDialog.isVisible(),relatedVisible:await related.isVisible()},
  beforePassiveCounts,renderedPanel:await coordinationDialog.ariaSnapshot()}));
 await page.screenshot({path:join(outputDir,'amplifier-coordination-reload.png'),animations:'disabled'});
 await noForms();await preserve();
 await expect(second.locator('[data-report-id="b-report-1"]')).toHaveCount(1);
 await expect(first.locator('[data-report-id="a-report-2"]')).toHaveCount(1);
 for(const {task,requestId,candidate,qualified} of rounds){
  const row=related.locator('[data-request-id="'+requestId+'"]');
  await expect(row.locator('[data-part="exact-result"]')).toContainText('Qualified terminal evidence');
  const retained=await agent('source','coordination.result',{requestId},requestId+':reloaded');
  assert.deepEqual(retained.result.results,qualified.result.results);
  assert.deepEqual(await readCandidate(task.actor,requestId),candidate);
  // Reopen earlier evidence after both rounds and reload: the exact original
  // and first artifact bytes must still be available, with no session switch.
  await row.getByRole('button',{name:'Open exact terminal message',exact:true}).click();
  const original=related.getByRole('article',{name:'Original message'});
  await expect(original.getByText(qualified.result.results[0].declaration.nativeTerminal.nativeText,{exact:true})).toBeVisible();
  await preserve();
  await original.getByRole('button',{name:'Close original message',exact:true}).click();
  await row.getByRole('button',{name:'Open referenced artifact',exact:true}).click();
  await page.waitForFunction(text=>window.amplifier.getState().canvas?.content===text,candidate.text);
  await preserve();
 }
 peerState=await inspect();
 await expect(related).toContainText('Other conversation');
 const changed=rounds[0];
 await writeFile(changed.candidate.path,'Changed after the result was reported');
 try{
  await related.locator('[data-request-id="'+changed.requestId+'"]').getByRole('button',{name:'Open referenced artifact',exact:true}).click();
  await page.waitForFunction(text=>window.amplifier.getState().canvas?.content===text,changed.candidate.text);
  const row=related.locator('[data-request-id="'+changed.requestId+'"]');
  await expect(row).toContainText('Exact version saved');
  const url=await row.getByRole('link',{name:'Download saved version',exact:true}).getAttribute('href');
  assert.equal(await page.request.get(new URL(url,page.url()).href).then(response=>response.text()),changed.candidate.text);
  await unlink(changed.candidate.path);
  const saved=await agent('source','coordination.artifact',{sessionId:current.selected,requestId:changed.requestId,reference:changed.qualified.result.results[0].declaration.references[0]},'deleted-source-read');
  assert.equal(saved.result.text,changed.candidate.text);assert.equal(saved.result.status,'retained');
  await row.getByRole('button',{name:'Open referenced artifact',exact:true}).click();
  await page.waitForFunction(text=>window.amplifier.getState().canvas?.content===text,changed.candidate.text);
  await preserve();
 }finally{await writeFile(changed.candidate.path,changed.candidate.text)}
 for(const {requestId} of rounds)assert.equal(peerState.sent.filter(row=>row.inputId===requestId).length,1);
 for(const continuation of continuations)assert.equal(peerState.sent.filter(row=>row.inputId===continuation).length,1);
 assert.equal(continuations.size,4);assert.equal(peerState.tasks.length,2);
 assert.equal(peerState.sent.filter(row=>row.kind==='human').length,1);assert.equal(peerState.humanMessages.length,1);
 assert.deepEqual(peerState.approvals,[]);assert.deepEqual(peerState.approvalResponses,[]);
 assert.deepEqual(peerState.coordination.grants,[]);assert.deepEqual(peerState.coordination.proposals,[]);
 assert.equal(peerState.stops.length,1);assert.equal(peerState.messages.length,1);
 for(const task of tasks){
  assert.equal(peerState.statuses[task.id],'idle');
  assert.equal(peerState.events.filter(row=>row.sessionId===task.id&&row.event==='generation.started').length,2);
 }
 assert.ok(['idle','ready'].includes(peerState.statuses[current.selected]));
 assert.deepEqual(executionCounts(peerState),beforePassiveCounts);
 for(const sid of Object.keys(beforePassiveCounts))assert.equal(peerState.contexts[sid].active,false);
 assert.deepEqual(peerState.failures,[]);
 assert.equal(peerState.nativeRuntime,false);assert.equal(peerState.providerCalls,false);
 assert.equal(peerState.bridgeCalls.filter(row=>['coordination.grant','coordination.decide','coordination.revoke'].includes(row.action)).length,0);
 assert.equal(uiActions.filter(row=>['coordination.grant','coordination.decide','coordination.revoke','coordination.create','coordination.send','coordination.subscribe'].includes(row.action)).length,0);
 // Revise one original request across new generations. Refresh must discard a
 // stale inspection, and an earlier saved file remains reachable from the UI.
 const originalRound=rounds[0],revisedTask=originalRound.task;
 for(const [revision,outcome] of [[2,'failed'],[3,'success'],[4,'malformed'],[5,'opaque'],[6,'success']]){
  const inputId='browser-revision-input-'+revision;
  await page.evaluate(({sender,target,inputId})=>window.amplifier.dispatch('coordination.send',{
   senderSessionId:sender,sessionId:target,mode:'queue',text:'Updated candidate '+inputId,
  },{id:inputId}),{sender:current.selected,target:revisedTask.id,inputId});
  await expect.poll(async()=>(await inspect()).contexts[revisedTask.id]?.inputIds[0]).toBe(inputId);
  const candidate=await readCandidate(revisedTask.actor,inputId);
  const replyText=revision===2?'Needs another correction':revision===4?'Malformed file declaration':revision===5?'Plain path citation':'Latest correction complete';
  const reference=revision===4?'file:'+candidate.path+'#sha256='+candidate.sha256:revision===5?candidate.path:candidate.path+'@sha256:'+candidate.sha256;
  const declaration=await agent(revisedTask.actor,'coordination.reply',{
   requestId:originalRound.requestId,expectedResponseRevision:revision-1,kind:'result',outcome:outcome==='failed'?'failed':'success',
   text:replyText,references:[reference],
  },'browser-revision-'+revision);
  await finish(revisedTask.actor);
  await related.getByRole('button',{name:'Refresh workspace work',exact:true}).click();
  const row=related.locator('[data-request-id="'+originalRound.requestId+'"]');
  await row.getByRole('button',{name:'Inspect exact result',exact:true}).click();
  await expect(row.locator('[data-part="exact-result"]')).toContainText(replyText);
  if(revision===4||revision===5){
   const result=row.locator('[data-part="exact-result"]');
   assert.equal(declaration.result.artifactVerification,revision===4?'incomplete':'not_requested');
   await expect(result).toContainText(revision===4?'Some file versions were not saved':'No file versions saved');
   if(revision===4){
    await expect(result).toContainText('Not qualified');
    await expect(result).toContainText('Unsupported retained-file reference');
    await expect(result).toContainText('path@sha256:');
   }
   await expect(result.getByRole('button',{name:'Open referenced artifact',exact:true})).toHaveCount(0);
   await expect(result.getByRole('link',{name:'Download saved version',exact:true})).toHaveCount(0);
   await result.getByText(revision===4?'Some file versions were not saved. Check the reference details.':'No file versions saved. These references are citations only.',{exact:true}).scrollIntoViewIfNeeded();
   await page.screenshot({path:join(outputDir,'amplifier-coordination-'+outcome+'.png'),animations:'disabled'});
  }
  if(revision===6){
   assert.equal(declaration.result.artifactVerification,'verified');
   await expect(row.locator('[data-part="exact-result"]')).toContainText('Exact version saved');
   await expect(row.locator('[data-part="exact-result"]')).toContainText('Qualified terminal evidence');
   await row.getByText('Earlier replies',{exact:true}).click();
   await expect(row.locator('[data-response-revision="4"]')).toContainText('Unsupported retained-file reference');
   await expect(row.locator('[data-response-revision="4"]')).toContainText('Not qualified');
   await expect(row.locator('[data-response-revision="5"]')).toContainText('No file versions saved');
  }
  if(revision===3){
   await unlink(candidate.path);
   await expect(row.locator('[data-part="exact-result"]')).toContainText('Qualified terminal evidence');
   await row.getByText('Earlier replies',{exact:true}).click();
   await row.getByRole('button',{name:'Open saved reply 1',exact:true}).click();
   await page.waitForFunction(text=>window.amplifier.getState().canvas?.content===text,originalRound.candidate.text);
   const savedUrl=await row.getByRole('link',{name:'Download saved reply 1',exact:true}).getAttribute('href');
   assert.equal(await page.request.get(new URL(savedUrl,page.url()).href).then(response=>response.text()),originalRound.candidate.text);
   await preserve();
  }
 }
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await preserve();
 assert.deepEqual(errors,[]);
 result={passed:true,actualService:true,twoTargets:true,followupExactTarget:true,cursorReconnect:true,failedReadReconnect:true,noDuplicateReports:true,noRepeatedSubmission:true,interruptWhileWaiting:true,selectionAndDraftPreserved:true,mobileNoOverflow:true,noCoordinationForms:true,noGrantRequired:true,runtimeOwnedBindings:true,twoDistinctTaskRoots:true,twoArtifactRoundsPerRoot:true,firstRoundBytesRetained:true,laterReplyVersionsInspectable:true,malformedReferenceRepair:true,opaqueReferenceNotSaved:true,correctedRetentionRevision:true,latestOutcomeAfterRefresh:true,peerWokenGeneration:true,notifyNeverWakes:true,exactResultInspector:true,referencedArtifactOpen:true,qualifiedScriptedTerminal:true,continuationOncePerRequest:true,deterministicRuntime:true,emulatedTerminalAnchor:true,nativeRuntime:false,liveModelEvidence:false,providerCalls:false,outputDir};
}catch(error){
 failure=error;
 try{
  const facts=disclosureFacts?.();
  if(facts){await writeFile(join(outputDir,'coordination-disclosure-failure.json'),JSON.stringify(facts,null,2)+'\n');console.error(JSON.stringify({coordinationDisclosureFailure:facts}))}
 }catch{}
 // Failure-only facts distinguish a replaced disclosure from a closed one or
 // missing retained reports without retrying the interaction that failed.
 try{console.error(JSON.stringify({coordinationFailure:await page?.evaluate(()=>{
  const state=window.amplifier?.getState?.();let saved;try{saved=JSON.parse(sessionStorage.getItem('amplifier-coordination-v1')||'null')}catch{}
  const dialog=document.querySelector('.a-overlay [role="dialog"]');
  return {selectedSessionId:state?.selectedSessionId??null,sessionIds:(state?.sessions||[]).slice(0,12).map(row=>row.id),panel:state?.view?.panel??null,
   bootVisible:!!document.querySelector('.boot'),dialogTitle:dialog?.querySelector('h2')?.textContent?.slice(0,100)??null,
   disclosures:[...(dialog?.querySelectorAll('details')||[])].slice(0,32).map(row=>({summary:row.querySelector(':scope > summary')?.textContent?.slice(0,100),open:row.open,reportCount:row.querySelectorAll('[data-report-id]').length})),
   targetCount:dialog?.querySelectorAll('.a-coordination-target').length??0,
   reportIds:[...(dialog?.querySelectorAll('[data-report-id]')||[])].slice(0,16).map(row=>row.dataset.reportId),
   savedWatchCount:saved?.selected?.length??0,savedCursorCount:Object.keys(saved?.cursors||{}).length,
   savedReportCount:Object.values(saved?.results||{}).reduce((n,rows)=>n+(Array.isArray(rows)?rows.length:0),0)};
 })}))}catch{}
 try{await page?.screenshot({path:join(outputDir,'amplifier-coordination-failure.png')});console.error((await page?.locator("body").innerText())?.slice(-5000))}catch{}
 throw error
}finally{await fixture.finish({close:[['Browser',()=>browser?.close()]],originalError:failure})}
console.log(JSON.stringify(result));
