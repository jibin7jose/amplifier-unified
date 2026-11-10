import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {ownFixture} from './fixture-lifecycle.mjs';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {appendFileSync,writeFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {acceptedCanvasReceipt,acceptedCanvasVisibility,settleCanvasReceipts} from './canvas-test-receipts.mjs';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const base=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(base,{recursive:true});
const output=await mkdtemp(join(base,'approval-lifecycle-'));
// Append as events happen, not only in finally: an outer kill must not erase
// the distinction between queued dispatch, outstanding HTTP and state recovery.
let diagnosticSequence=0;
const diagnostic=(event,detail={})=>{
 const row={sequence:++diagnosticSequence,at:Date.now(),event,...detail};
 appendFileSync(join(output,'diagnostics.jsonl'),JSON.stringify(row)+'\n');
 return row;
};
const stateIdentity=value=>({revision:value?.revision,clientId:value?.client?.id,hostInstanceId:value?.client?.hostInstanceId,
 sessionId:value?.selectedSessionId,canvasId:value?.canvas?.id,canvasOpen:value?.canvas?.open,
 visibilityPending:value?.canvas?.visibilityPending,library:value?.view?.canvasDraft?.library,delivery:value?.view?.canvasDraft?.delivery});
const bounded=async(label,work,milliseconds=15000)=>{
 let timer;
 try{return await Promise.race([work,new Promise((_,reject)=>{timer=setTimeout(()=>{
  diagnostic('watchdog-timeout',{label,milliseconds});
  reject(Error(`${label} exceeded ${milliseconds}ms; original operation was not replayed`));
 },milliseconds)})])}finally{clearTimeout(timer)}
};
writeFileSync(join(output,'launch.json'),JSON.stringify({
 node:process.execPath,argv:process.argv,cwd:process.cwd(),fixture:[process.env.AMPLIFIER_TEST_PYTHON||'python',root+'/tests/fixtures/approval_lifecycle_server.py'],
 environment:Object.fromEntries(['AMPLIFIER_TEST_PYTHON','AMPLIFIER_TEST_ROOT','AMPLIFIER_TEST_PACKAGE_ROOT','AMPLIFIER_TEST_OUTPUT_DIR','AMPLIFIER_SHELL_NODE_MODULES','PLAYWRIGHT_BROWSERS_PATH','PYTHONPATH','UV_OFFLINE','PIP_NO_INDEX'].map(key=>[key,process.env[key]??null])),
 // Do not dump arbitrary environment values or request Authorization headers.
},null,2));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||'python',[root+'/tests/fixtures/approval_lifecycle_server.py'],{
 stdio:['ignore','pipe','pipe'],env:{...process.env,AMPLIFIER_TEST_OUTPUT_DIR:output}});
const stopFixture=ownFixture(fixture,{label:'Approval lifecycle fixture'});
let browser,page,fixtureLog='',releaseHeld,completedReport,terminalError;
const http=[],observedReceipts=new Map(),checks=[];
const requestStarts=new Map(),responseBodies=[];
const unwrap=raw=>raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
const isAction=request=>request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions';
const observedRequest=request=>isAction(request)||new URL(request.url()).pathname==='/api/state';
const requestIdentity=request=>{
 const url=new URL(request.url());
 return {method:request.method(),path:url.pathname,clientId:request.headers()['x-amplifier-client']??url.searchParams.get('clientId'),
  ...(isAction(request)?{command:unwrap(request.postDataJSON())}:{})};
};
const saveFailure=async error=>{
 diagnostic('failure',{error:error.stack||String(error)});
 writeFileSync(join(output,'failure.txt'),error.stack||String(error));
 if(!page)return;
 await bounded('failure diagnostics',Promise.allSettled([
  page.screenshot({path:join(output,'failure.png'),fullPage:true,timeout:3000}),
  page.locator('body').innerText({timeout:3000}).then(text=>writeFileSync(join(output,'failure-page.txt'),text)),
  page.evaluate(()=>{const s=window.amplifier?.getState();return {state:{revision:s?.revision,client:s?.client,sessionId:s?.selectedSessionId,
   canvasId:s?.canvas?.id,canvasOpen:s?.canvas?.open,visibilityPending:s?.canvas?.visibilityPending,library:s?.view?.canvasDraft?.library},
   visibility:document.visibilityState,inspect:window.__permissionInspectDiagnostic};})
   .then(value=>diagnostic('failure-browser-state',value))
 ]),4000).catch(captureError=>diagnostic('failure-capture-incomplete',{error:captureError.message}));
};
let handlingSignal=false;
const onTermination=signal=>{
 if(handlingSignal)return;handlingSignal=true;process.exitCode=1;
 terminalError=Error(`Harness received ${signal}; pending operations remain uncertain`);
 diagnostic('process-signal',{signal});
 // Synchronous evidence + exit: no callback window for further test actions.
 // Normal screenshot/drain cleanup cannot be claimed on a terminated harness.
 writeFileSync(join(output,'failure.txt'),terminalError.message);
 writeFileSync(join(output,'http.json'),JSON.stringify(http,null,2));
 writeFileSync(join(output,'checks.json'),JSON.stringify(checks,null,2));
 writeFileSync(join(output,'fixture.log'),fixtureLog);
 writeFileSync(join(output,'report.json'),JSON.stringify({passed:false,incomplete:true,error:terminalError.message}));
 writeFileSync(join(output,'cleanup.json'),JSON.stringify({status:'unconfirmed',signal,pendingOperations:'not replayed',screenshot:'not captured after termination; prior frames retained'}));
 fixture.kill('SIGTERM');
 process.exit(signal==='SIGINT'?130:143);
};
const onTerm=()=>onTermination('SIGTERM'),onInterrupt=()=>onTermination('SIGINT');
process.once('SIGTERM',onTerm);process.once('SIGINT',onInterrupt);
fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timeout=setTimeout(()=>reject(Error('Fixture startup')),15000);
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}});
  fixture.once('exit',code=>reject(Error(`Fixture exited: ${code}`)));fixture.once('error',reject);
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-approval-control-token'}});
 page=await context.newPage();const other=await context.newPage();
 page.setDefaultTimeout(8000);
 await page.exposeFunction('__recordPermissionDiagnostic',detail=>diagnostic('browser-dispatch',detail));
 page.on('request',request=>{
  if(!observedRequest(request))return;
  const entry=diagnostic('request-start',{...requestIdentity(request),page:request.frame().url()});
  requestStarts.set(request,entry);
 });
 page.on('response',response=>{
  const request=response.request();if(!observedRequest(request))return;
  const start=requestStarts.get(request);
  diagnostic('response-headers',{requestSequence:start?.sequence,...requestIdentity(request),status:response.status()});
  const row=isAction(request)?{request:unwrap(request.postDataJSON()),status:response.status()}:null;
  if(row)http.push(row);
  const settled=response.json().then(body=>{
   if(row)row.body=body;
   diagnostic('response-body',{requestSequence:start?.sequence,...requestIdentity(request),status:response.status(),
    accepted:body?.accepted,revision:body?.stateRevision??body?.revision,hostInstanceId:body?.hostInstanceId,
    ...(row?{error:body?.error,state:body?.state?stateIdentity(body.state):undefined}:{state:stateIdentity(body)})});
  },error=>{
   if(row)row.readError=error.message;
   diagnostic('response-body-failed',{requestSequence:start?.sequence,...requestIdentity(request),error:error.message});
  });
  responseBodies.push(settled);
  if(row)observedReceipts.set(request,{row,settled});
 });
 page.on('requestfailed',request=>{
  if(!observedRequest(request))return;
  diagnostic('request-failed',{requestSequence:requestStarts.get(request)?.sequence,...requestIdentity(request),failure:request.failure()});
  if(isAction(request))http.push({request:unwrap(request.postDataJSON()),transportError:request.failure()});
 });
 page.on('close',()=>diagnostic('page-closed'));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const serverSnapshot=async clientId=>{
  const entry=diagnostic('request-start',{channel:'node-api',path:'/api/state',clientId});
  try{
   const response=await page.request.get(url+'/api/state?clientId='+encodeURIComponent(clientId),{timeout:8000});
   diagnostic('response-headers',{channel:'node-api',requestSequence:entry.sequence,status:response.status()});
   assert.equal(response.status(),200);const snapshot=await response.json();
   diagnostic('response-body',{channel:'node-api',requestSequence:entry.sequence,state:stateIdentity(snapshot)});return snapshot;
  }catch(error){diagnostic('request-failed',{channel:'node-api',requestSequence:entry.sequence,error:error.message});throw error}
 };
 const responseFor=predicate=>page.waitForResponse(response=>isAction(response.request())&&predicate(unwrap(response.request().postDataJSON())),{timeout:8000}).then(response=>({response}),error=>({error}));
 const acceptedResponse=async waiting=>{
  const result=await waiting;if(result.error)throw result.error;
  const request=result.response.request(),command=unwrap(request.postDataJSON());
  return acceptedCanvasReceipt(observedReceipts.get(request),command.id);
 };
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const inspect=()=>page.request.get(url+'/fixture').then(r=>r.json());
 const call=async(action,args,id)=>{
  const response=await page.request.post(url+'/api/actions',{data:{action,args,id}});
  assert.equal(response.status(),200,await response.text());return response.json();
 };
 await page.goto(url);
 await showFixtureConversation(page);
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
 const initial=await inspect(),sid=initial.sessionId;
 assert.deepEqual(initial.starts,[]);
 assert.equal(initial.attention.length,1);assert.equal(initial.navigation.label,'Approval requested');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep this private draft');
 const old=page.locator('[data-approval-id="capability:old"]'),current=page.locator('[data-approval-id="capability:current"]');
 await expect(old).toContainText('stopped');
 await expect(old.getByRole('button',{name:'Allow',exact:true})).toHaveCount(0);
 await expect(old.getByRole('button',{name:'Deny',exact:true})).toHaveCount(0);
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 await old.getByRole('button',{name:'Review current scope'}).click();
 await expect(old.getByRole('region',{name:'Current configuration scope'})).toBeVisible();
 assert.deepEqual((await inspect()).starts,[]);
 assert.deepEqual((await inspect()).controls,[]);
 assert.deepEqual((await inspect()).inputs,[]);
 await page.screenshot({path:join(output,'desktop.png'),animations:'disabled'});
 // Another tab sees the same owner and cannot revive the dismissed request.
 await other.goto(url);
 await showFixtureConversation(other);
 await other.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
 const observed=(await call('capability.read',{sessionId:sid,id:'capability:old'},'read-old')).result.request;
 await old.getByRole('button',{name:'Dismiss old request'}).focus();
 await page.keyboard.press('Enter');
 await expect(old).toHaveCount(0);
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 await expect(other.locator('[data-approval-id="capability:old"]')).toHaveCount(0);
 const duplicate=await page.request.post(url+'/api/actions',{data:{action:'capability.revoke',args:{sessionId:sid,id:'capability:old',dismissRequest:true,expectedRevision:observed.revision,expectedRequestHash:observed.requestHash},id:'stale-second-tab'}});
 assert.equal(duplicate.status(),409);
 const late=await page.request.post(url+'/api/actions',{data:{action:'approval.respond',args:{sessionId:sid,id:'capability:old',decision:'allow'},id:'late-allow'}});
 assert.equal(late.status(),409,await late.text());
 const dismissed=(await call('capability.read',{sessionId:sid,id:'capability:old'},'check-dismissed')).result.request;
 assert.equal(dismissed.status,'revoked');assert.equal(dismissed.dismissed,true);
 assert.equal((await inspect()).attention.length,1);
 const historyEntry=()=>page.getByRole('button',{name:/^Permissions history \(/});
 const panel=page.locator('#workspace-canvas'),history=panel.locator('[data-overview-section="permissions"]');
 const closeHistory=async()=>{
  const before=await state(),response=responseFor(command=>command.action==='canvas.visibility'&&command.args.open===false);
  await panel.getByRole('button',{name:'Close canvas panel',exact:true}).click();
  await expect(panel).toBeHidden();
  const received=await response;if(received.error)throw received.error;
  const request=received.response.request();
  const expected={commandId:unwrap(request.postDataJSON()).id,clientId:before.client.id,hostInstanceId:before.client.hostInstanceId,sessionId:before.selectedSessionId,canvasId:before.canvas.id,open:false};
  const confirmed=await acceptedCanvasVisibility(observedReceipts.get(request),expected,()=>serverSnapshot(expected.clientId));
  checks.push({name:'Close accepted',commandId:expected.commandId,revision:confirmed.snapshot.revision});
  await expect(page.locator('#canvas-toggle')).toBeFocused();
 };
 const recordsBefore=(await inspect()).approvals;
 await historyEntry().focus();await page.keyboard.press('Enter');
 await expect(history).toHaveAttribute('open','');
 await expect(history.locator('summary')).toBeFocused();
 await expect(page.locator('[data-permission-history-id="capability:old"]')).toContainText('Dismissed old request');
 await expect(page.locator('[data-part="conversation"] [data-permission-history-id]')).toHaveCount(0);
 await page.screenshot({path:join(output,'history-desktop.png'),animations:'disabled'});
 await closeHistory();
 assert.deepEqual((await inspect()).approvals,recordsBefore,'Passive history keeps exact audit records');
 await page.reload();
 await expect(composer).toHaveDraft('Keep this private draft');
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 // Change actual scope without pushing a new card. Preflight must not approve it.
 await page.request.post(url+'/fixture',{data:{change:'scope'}});
 await current.getByRole('button',{name:'Allow',exact:true}).click();
 await expect(current.getByRole('alert')).toContainText('changed');
 await expect(current).toContainText('Changed scope requiring another review');
 assert.equal((await inspect()).approvals.find(r=>r.id==='capability:current').status,'pending');
 // Expire between review and click; no approval is applied or automatically retried.
 await current.getByRole('textbox',{name:'Note to Amplifier (optional)'}).fill('Keep this note in history');
 await page.request.post(url+'/fixture',{data:{change:'expire'}});
 await current.getByRole('button',{name:'Deny',exact:true}).click();
 await expect(current.getByRole('button',{name:'Dismiss old request'})).toBeVisible();
 await expect(current.getByRole('button',{name:'Allow',exact:true})).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});
 await current.scrollIntoViewIfNeeded();
 assert.equal(await current.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.locator('[data-part="approvals"]').evaluate(element=>{element.scrollTop=0});
 await page.screenshot({path:join(output,'narrow.png'),animations:'disabled'});
 const dismissButton=current.getByRole('button',{name:'Dismiss old request'});
 await dismissButton.scrollIntoViewIfNeeded();
 assert.equal(await dismissButton.evaluate(button=>{
  const bounds=button.getBoundingClientRect(),container=button.closest('[data-part="approvals"]').getBoundingClientRect();
  return bounds.top>=container.top&&bounds.bottom<=container.bottom&&button.contains(document.elementFromPoint(bounds.x+bounds.width/2,bounds.y+bounds.height/2));
 }),true,'Narrow dismissal is visible and hit-testable after scrolling');
 await page.screenshot({path:join(output,'narrow-actions.png'),animations:'disabled'});
 await current.getByRole('button',{name:'Dismiss old request'}).click();
 await expect(current).toHaveCount(0);
 await expect(page.locator('[data-part="approvals"]')).toHaveCount(0);
 await expect(historyEntry()).toBeVisible();
 const compact=await historyEntry().boundingBox();
 assert.ok(compact.height>=44&&compact.height<=60,'Compact entry retains a touch-size target');
 await page.screenshot({path:join(output,'history-only-narrow-chat.png'),animations:'disabled'});
 await historyEntry().click();
 await expect(history).toBeVisible();
 await expect(page.locator('[data-permission-history-id="capability:current"]')).toContainText('Keep this note in history');
 await page.screenshot({path:join(output,'history-narrow.png'),animations:'disabled'});
 await closeHistory();
 await page.reload();
 await expect(composer).toHaveDraft('Keep this private draft');
 await expect(page.locator('[data-part="approvals"]')).toHaveCount(0);
 // A new independent permission still arrives in chat after history-only state.
 await page.request.post(url+'/fixture',{data:{change:'later'}});
 const later=page.locator('[data-approval-id="capability:later"]');
 await expect(later.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
 await expect(later.getByRole('button',{name:'Deny',exact:true})).toBeEnabled();
 await later.getByRole('button',{name:'Deny',exact:true}).click();
 await expect(later).toHaveCount(0);
 await expect(historyEntry()).toHaveText('Permissions history (3)');
 // Touch activation in a real narrow touch context; no synthetic DOM click.
 const touchContext=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,extraHTTPHeaders:{Authorization:'Bearer fixture-approval-control-token'}});
 const touch=await touchContext.newPage();
 await touch.goto(url);await showFixtureConversation(touch);
 await touch.getByRole('button',{name:'Permissions history (3)',exact:true}).tap();
 await expect(touch.locator('#workspace-canvas [data-permission-history-id="capability:later"]')).toContainText('Permission denied');
 assert.equal(await touch.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
 await touch.screenshot({path:join(output,'history-touch.png'),animations:'disabled'});
 await touch.getByRole('button',{name:'Close canvas panel',exact:true}).tap();
 await expect(touch.locator('#workspace-canvas')).toBeHidden();await touchContext.close();
 // Pending cards must complete the ordinary guarded path while a different,
 // read-only history reveal is held. Releasing history cannot make this pass.
 await page.setViewportSize({width:1400,height:900});
 for(const decision of ['approve','deny']){
  const created=await page.request.post(url+'/fixture',{data:{change:'held-decision',decision}});
  assert.equal(created.status(),200);const id=(await created.json()).created;
  const card=page.locator(`[data-approval-id="${id}"]`);
  const button=card.getByRole('button',{name:decision==='approve'?'Allow':'Deny',exact:true});
  await expect(button).toBeEnabled();
  const before=await inspect(),beforeState=await state(),expectedStatus=decision==='approve'?'allow':'deny';
  let heldRequest,released=false;
  const gate=new Promise(resolve=>{releaseHeld=()=>{released=true;resolve()}});
  const hold=async route=>{
   const command=unwrap(route.request().postDataJSON());
   if(command?.action==='view.update'&&command.args?.patch?.canvasDraft?.delivery==='permissions'){heldRequest=route.request();await gate}
   await route.continue();
  };
  await page.route('**/api/actions',hold);
  const opening=responseFor(command=>command.action==='view.update'&&command.args?.patch?.canvasDraft?.delivery==='permissions');
  try{
   await historyEntry().click();await expect.poll(()=>!!heldRequest).toBe(true);
   const preflight=responseFor(command=>command.action==='capability.read'&&command.args.id===id);
   const deciding=responseFor(command=>command.action==='approval.respond'&&command.args.id===id);
   await button.click();
   const reviewed=await acceptedResponse(preflight),applied=await acceptedResponse(deciding);
   assert.equal(released,false,'Decision must finish before history is released');
   assert.equal(observedReceipts.has(heldRequest),false,'Held history has no response yet');
   assert.equal(reviewed.body.result.request.canDecide,true);
   assert.equal(applied.request.args.expectedRequestHash,reviewed.body.result.request.requestHash);
   assert.equal(applied.request.args.sessionId,beforeState.selectedSessionId);
   assert.equal(applied.request.args.decision,decision);
   await expect(card).toHaveCount(0);
   const after=await inspect();
   assert.equal(after.approvals.find(row=>row.id===id).status,expectedStatus);
   assert.deepEqual(after.approvals.filter(row=>row.id!==id),before.approvals.filter(row=>row.id!==id));
   assert.equal((await serverSnapshot(beforeState.client.id)).selectedSessionId,beforeState.selectedSessionId);
   await expect(composer).toHaveDraft('Keep this private draft');
   checks.push({name:`Pending ${decision} completes during held history`,preflight:reviewed,decision:applied,historyUnreleased:true});
   releaseHeld();await acceptedResponse(opening);
   await expect(history).toBeVisible();
   await expect(history.locator(`[data-permission-history-id="${id}"]`)).toContainText(decision==='approve'?'Permission allowed':'Permission denied');
   await closeHistory();
  }finally{releaseHeld?.();await page.unroute('**/api/actions',hold)}
 }
 // Dirty renderer-local edits survive history browsing. Replacement still refuses.
 await page.setViewportSize({width:1400,height:900});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const runtimeBeforeHistory=await inspect();
 await action('canvas.show',{kind:'markdown',title:'Newer document choice',content:'# Keep this newer choice'});
 const newerDocumentId=(await state()).canvas.id;
 await action('canvas.show',{kind:'markdown',title:'Unfinished document',content:'# Retained original'});
 const unfinishedId=(await state()).canvas.id;
 // A genuinely different newer document must win after a held history reveal.
 const beforeSelection=await state();
 let selectionHeld=false;
 const selectionGate=new Promise(resolve=>{releaseHeld=resolve});
 const holdSelectionHistory=async route=>{
  const body=unwrap(route.request().postDataJSON());
  if(body?.action==='view.update'&&body.args?.patch?.canvasDraft?.delivery==='permissions'){selectionHeld=true;await selectionGate}
  await route.continue();
 };
 await page.route('**/api/actions',holdSelectionHistory);
 const historyOpening=responseFor(command=>command.action==='view.update'&&command.args?.patch?.canvasDraft?.delivery==='permissions');
 await historyEntry().click();await expect.poll(()=>selectionHeld).toBe(true);
 const selectionResponse=responseFor(command=>command.action==='canvas.select'&&command.args.id===newerDocumentId);
 const newerTab=panel.getByRole('tab',{name:'Newer document choice',exact:true});
 await newerTab.click();releaseHeld();
 const openedRow=await acceptedResponse(historyOpening),selectedRow=await acceptedResponse(selectionResponse);
 await page.unroute('**/api/actions',holdSelectionHistory);
 await expect(newerTab).toHaveAttribute('aria-selected','true');
 await expect(newerTab).toBeFocused();
 // Allow the presentation frame, then flush queued housekeeping before the host read.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 // One explicit operation. Node bounds the wait; page defaultTimeout does not
 // bound a dispatch promise. No retry, no synthetic success and no forced flush.
 const inspectId='permission-history-post-selection-'+randomUUID();
 diagnostic('dispatch-requested',{id:inspectId,action:'canvas.views.inspect',after:'two animation frames completed',identity:stateIdentity(beforeSelection)});
 const inspectDispatch=page.evaluate(async id=>{
  const snapshot=()=>{const s=window.amplifier.getState();return {revision:s.revision,clientId:s.client?.id,hostInstanceId:s.client?.hostInstanceId,
   sessionId:s.selectedSessionId,canvasId:s.canvas?.id,canvasOpen:s.canvas?.open,visibilityPending:s.canvas?.visibilityPending,
   library:s.view?.canvasDraft?.library,delivery:s.view?.canvasDraft?.delivery,visibility:document.visibilityState}};
  window.__permissionInspectDiagnostic={id,phase:'entered',identity:snapshot()};
  void window.__recordPermissionDiagnostic(window.__permissionInspectDiagnostic).catch(()=>{});
  try{
   const operation=window.amplifier.dispatch('canvas.views.inspect',{}, {id});
   window.__permissionInspectDiagnostic={id,phase:'dispatch-returned-promise',identity:snapshot()};
   void window.__recordPermissionDiagnostic(window.__permissionInspectDiagnostic).catch(()=>{});
   const receipt=await operation;
   window.__permissionInspectDiagnostic={id,phase:'settled',accepted:receipt?.accepted,revision:receipt?.stateRevision??receipt?.revision,identity:snapshot()};
   await window.__recordPermissionDiagnostic(window.__permissionInspectDiagnostic);
   return receipt;
  }catch(error){
   window.__permissionInspectDiagnostic={id,phase:'rejected',error:error.message,identity:snapshot()};
   await window.__recordPermissionDiagnostic(window.__permissionInspectDiagnostic);throw error;
  }
 },inspectId);
 // Observe any eventual settlement even after the watchdog; never call dispatch again.
 const inspected=await bounded(`single ${inspectId}`,inspectDispatch.then(receipt=>{
  diagnostic('dispatch-settled',{id:inspectId,accepted:receipt?.accepted,revision:receipt?.stateRevision??receipt?.revision});return receipt;
 },error=>{diagnostic('dispatch-rejected',{id:inspectId,error:error.message});throw error}),15000);
 assert.equal(inspected?.accepted,true);
 const selected=await serverSnapshot(beforeSelection.client.id);
 assert.equal(selected.client.id,beforeSelection.client.id);
 assert.equal(selected.client.hostInstanceId,beforeSelection.client.hostInstanceId);
 assert.equal(selected.selectedSessionId,sid);assert.equal(selected.canvas.id,newerDocumentId);
 assert.equal(selected.view.canvasDraft.library,false);
 assert.ok(selected.revision>=(selectedRow.body.stateRevision??selectedRow.body.revision));
 await expect(history).toBeHidden();await expect(newerTab).toBeFocused();
 checks.push({name:'Held history/newer document wins',opened:openedRow,selected:selectedRow,server:selected});
 await page.screenshot({path:join(output,'newer-document-retained.png'),animations:'disabled'});
 await action('canvas.select',{id:unfinishedId});
 const manifest={id:'example.permission-editor',label:'Permission history test editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());const [dirty,setDirty]=React.useState("unrequested");return React.createElement("label",null,"Retained local edit",React.createElement("input",{"data-mount":mount,"data-dirty-settlement":dirty,value:text,onChange:event=>{setText(event.target.value);setDirty("pending");host.setDirty(true).then(result=>setDirty(JSON.stringify(result)),error=>setDirty("error: "+error.message))}}))}';
 const staged=await action('shell.packages.stage',{manifest,source}),digest=staged.result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 await panel.getByRole('button',{name:'Canvas details',exact:true}).click();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const edit=page.getByLabel('Retained local edit', {exact:true});
 await panel.getByRole('button',{name:'Close canvas details',exact:true}).click();
 await expect(page.getByRole('region',{name:'Canvas details',exact:true})).toBeHidden();
 assert.equal(await edit.evaluate(el=>!!el.closest('[inert]')),false,'Details must not leave the editor inert');
 await edit.focus();await expect(edit).toBeFocused();
 const dirtyResponse=responseFor(command=>command.action==='canvas.views.dirty'&&command.args.dirty===true);
 await edit.fill('Keep unsaved permission review edit');
 await expect(edit).toHaveValue('Keep unsaved permission review edit');
 await acceptedResponse(dirtyResponse);
 await expect.poll(()=>edit.getAttribute('data-dirty-settlement')).not.toBe('pending');
 const views=async()=>(await action('canvas.views.inspect')).result.views;
 await expect.poll(async()=>(await views()).find(row=>row.viewId==='primary').dirty).toBe(true);
 const beforeView=(await views()).find(row=>row.viewId==='primary'),mount=await edit.getAttribute('data-mount');
 await historyEntry().click();await expect(history).toBeVisible();await expect(edit).toBeHidden();
 const refusal=await action('canvas.show',{kind:'text',title:'Must not replace',content:'not accepted'}).then(value=>({value}),error=>({error:error.message}));
 assert.match(refusal.error,/viewer edit/);
 await panel.getByRole('tab',{name:'Unfinished document',exact:true}).click();
 await expect(edit).toHaveValue('Keep unsaved permission review edit');
 assert.equal(await edit.getAttribute('data-mount'),mount);
 const afterView=(await views()).find(row=>row.viewId==='primary');
 for(const key of ['viewId','resourceId','resourceRevision','generation','dirty'])assert.equal(afterView[key],beforeView[key],key);
 checks.push({name:'Dirty edit retained and replacement refused',beforeView,afterView,mount,refusal,dirtySettlement:await edit.getAttribute('data-dirty-settlement')});
 await expect(composer).toHaveDraft('Keep this private draft');
 await page.screenshot({path:join(output,'retained-dirty-editor.png'),animations:'disabled'});
 // Newer Close wins even when the history opening request is slow.
 let historyHeld=false;
 const gate=new Promise(resolve=>{releaseHeld=resolve});
 const holdHistory=async route=>{
  const raw=route.request().postDataJSON(),body=raw?.action==='shell.command'?(raw.args.command||raw.args):raw;
  if(body?.action==='view.update'&&body.args?.patch?.canvasDraft?.delivery==='permissions'){historyHeld=true;await gate}
  await route.continue();
 };
 await page.route('**/api/actions',holdHistory);
 const beforeClose=await state(),opening=responseFor(command=>command.action==='view.update'&&command.args?.patch?.canvasDraft?.delivery==='permissions');
 await historyEntry().click();
 await expect.poll(()=>historyHeld).toBe(true);
 const closing=responseFor(command=>command.action==='canvas.visibility'&&command.args.open===false);
 await panel.getByRole('button',{name:'Close canvas panel',exact:true}).click();
 await expect(panel).toBeHidden();
 checks.push({name:'Newer Close hides immediately before either receipt',serverStillOpen:(await serverSnapshot(beforeClose.client.id)).canvas.open});
 releaseHeld();
 await acceptedResponse(opening);
 const closeResult=await closing;if(closeResult.error)throw closeResult.error;
 const closeRequest=closeResult.response.request();
 const closeExpected={commandId:unwrap(closeRequest.postDataJSON()).id,clientId:beforeClose.client.id,hostInstanceId:beforeClose.client.hostInstanceId,sessionId:beforeClose.selectedSessionId,canvasId:beforeClose.canvas.id,open:false};
 const closed=await acceptedCanvasVisibility(observedReceipts.get(closeRequest),closeExpected,()=>serverSnapshot(closeExpected.clientId));
 checks.push({name:'Held history/newer Close accepted',receipt:closed.row,server:closed.snapshot});
 await expect(historyEntry()).toBeEnabled();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.visibilityPending!==true);
 await expect(panel).toBeHidden();
 await page.unroute('**/api/actions',holdHistory);
 assert.equal((await views()).find(row=>row.viewId==='primary').dirty,true);
 const final=await inspect();
 for(const key of ['starts','inputs','controls','decisions'])assert.deepEqual(final[key],runtimeBeforeHistory[key],`History browsing creates no extra runtime ${key}`);
 assert.equal(final.approvals.filter(r=>r.status==='pending').length,0);
 assert.equal(final.attention.length,0);assert.notEqual(final.navigation.label,'Approval requested');
 assert.deepEqual(errors,[]);
 await settleCanvasReceipts(observedReceipts.values());
 // Separate synthetic chats keep Stop from altering the already-qualified
 // history/dirty document fixture. These cases exercise the actual dispatch path.
 for(const scenario of ['later-allow','pending-preflight']){
  const created=await page.request.post(url+'/fixture',{data:{change:'stop-case',case:scenario}});
  assert.equal(created.status(),200);const target=await created.json();
  const stopContext=await browser.newContext({viewport:{width:1400,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-approval-control-token'}});
  const stopPage=await stopContext.newPage(),mutations=[],stopRequests=[];
  stopPage.setDefaultTimeout(8000);
  stopPage.on('request',request=>{
   if(!isAction(request))return;
   const command=unwrap(request.postDataJSON());diagnostic('stop-case-request',{scenario,command});
   if(command.action==='approval.respond'&&command.args?.id===target.pendingId)mutations.push(command);
   if(command.action==='conversation.stop')stopRequests.push(command);
  });
  let heldHistory=false,heldPreflight=false,releasePreflight=()=>{};
  const historyGate=new Promise(resolve=>{releaseHeld=resolve});
  const preflightGate=new Promise(resolve=>{releasePreflight=resolve});
  const hold=async route=>{
   const command=unwrap(route.request().postDataJSON());
   if(command.action==='view.update'&&command.args?.patch?.canvasDraft?.delivery==='permissions'){heldHistory=true;await historyGate}
   if(scenario==='pending-preflight'&&command.action==='capability.read'&&command.args?.id===target.pendingId){heldPreflight=true;await preflightGate}
   await route.continue();
  };
  const inspectTarget=()=>stopPage.request.get(url+'/fixture?sessionId='+encodeURIComponent(target.sessionId)).then(r=>r.json());
  try{
   await stopPage.goto(url);await showFixtureConversation(stopPage);
   await stopPage.evaluate(id=>window.amplifier.dispatch('session.select',{id}),target.sessionId);
   const card=stopPage.locator(`[data-approval-id="${target.pendingId}"]`),allow=card.getByRole('button',{name:'Allow',exact:true});
   await expect(allow).toBeEnabled();
   await stopPage.route('**/api/actions',hold);
   await stopPage.getByRole('button',{name:/^Permissions history \(/}).click();
   await expect.poll(()=>heldHistory).toBe(true);
   if(scenario==='pending-preflight'){await allow.click();await expect.poll(()=>heldPreflight).toBe(true)}
   const stopResponse=stopPage.waitForResponse(response=>isAction(response.request())&&unwrap(response.request().postDataJSON()).action==='conversation.stop')
    .then(response=>({response}),error=>({error}));
   await stopPage.locator('button[data-action="conversation.stop"]').click();
   if(scenario==='later-allow')await allow.click();else releasePreflight();
   await expect(card.getByRole('alert')).toContainText('Stop was requested');
   assert.deepEqual(mutations,[],'Later mutation is refused, not queued for automatic execution');
   assert.deepEqual(stopRequests,[],'Stop is still queued behind the held history');
   const before=(await inspectTarget());
   assert.equal(before.interruptionRevision,0);
   assert.equal(before.approvals.find(row=>row.id===target.pendingId).status,'pending');
   releaseHeld();
   const received=await stopResponse;if(received.error)throw received.error;
   const body=await received.response.json(),command=unwrap(received.response.request().postDataJSON());
   assert.equal(received.response.status(),200);assert.equal(body.accepted,true);
   assert.equal(command.args.sessionId,target.sessionId);
   const after=await inspectTarget();
   assert.equal(after.interruptionRevision,1);
   assert.equal(after.lastInterruption.commandId,command.id);
   assert.notEqual(after.approvals.find(row=>row.id===target.pendingId).status,'allow');
   assert.deepEqual(mutations,[],'Confirmed Stop never replays the rejected Allow');
   checks.push({name:`Stop fences ${scenario}`,before,after,stopReceipt:body,stopCommand:command,decisionRequests:mutations});
  }finally{
   releasePreflight();releaseHeld?.();await stopPage.unroute('**/api/actions',hold);await stopContext.close();
  }
 }
 // The host really accepts each Stop. Only its response is hidden/corrupted.
 // No direct fence settlement, fixture receipt forgery or page reload can recover it.
 for(const scenario of ['lost-ack','malformed-ack']){
  const created=await page.request.post(url+'/fixture',{data:{change:'stop-case',case:scenario}});
  assert.equal(created.status(),200);const target=await created.json();
  const stopContext=await browser.newContext({viewport:{width:1400,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-approval-control-token'}});
  const stopPage=await stopContext.newPage(),mutations=[],stopRequests=[],lookups=[],routeErrors=[],pageErrors=[];
  stopPage.setDefaultTimeout(8000);
  stopPage.on('pageerror',error=>pageErrors.push(error.message));
  stopPage.on('request',request=>{
   if(!isAction(request))return;
   const command=unwrap(request.postDataJSON());diagnostic('stop-recovery-action',{scenario,command});
   if(['approval.respond','capability.revoke'].includes(command.action)&&command.args?.id===target.pendingId)mutations.push(command);
   if(command.action==='conversation.stop')stopRequests.push(command);
  });
  const inspectTarget=()=>stopPage.request.get(url+'/fixture?sessionId='+encodeURIComponent(target.sessionId),{timeout:8000}).then(r=>r.json());
  const nextPermissionRead=()=>bounded('Stop recovery permission read', (async()=>{
   const request=await stopPage.waitForRequest(request=>{
    if(!isAction(request))return false;
    const command=unwrap(request.postDataJSON());
    return command.action==='capability.read'&&command.args?.sessionId===target.sessionId&&command.args?.id===target.pendingId;
   },{timeout:8000});
   // Correlate the body to this exact new request, not an earlier response.
   const response=await request.response();assert.ok(response,'Permission read must receive a response');
   const receipt={command:unwrap(request.postDataJSON()),status:response.status(),body:await response.json()};
   diagnostic('stop-recovery-permission-read',{scenario,...receipt});
   assert.equal(receipt.status,200);assert.equal(receipt.body.accepted,true);
   assert.equal(receipt.body.result.request.id,target.pendingId);
   assert.equal(receipt.body.result.request.canDismiss,true);
   assert.ok(receipt.body.result.request.requestHash);
   return receipt;
  })(),8000).then(receipt=>({receipt}),error=>({error}));
  let acceptedLookup=false,stopCommand,stopClientId,stopReceipt,supersedingStop=null;
  const hideStop=async route=>{
   try{
    // Reconnect also reads the bodyless action catalog. Only POST commands
    // carry JSON, and callback failures must reach the journey's cleanup path.
    if(!isAction(route.request())){await route.continue();return}
    const command=unwrap(route.request().postDataJSON());
    assert.ok(command&&typeof command.action==='string','Action POST must identify its command');
    if(command.action!=='conversation.stop'){await route.continue();return}
    assert.equal(stopCommand,undefined,'Never replay the uncertain Stop');stopCommand=command;
    stopClientId=route.request().headers()['x-amplifier-client'];
    const response=await route.fetch({timeout:8000});
    stopReceipt=await response.json();
    assert.equal(response.status(),200);assert.equal(stopReceipt.accepted,true);
    diagnostic('stop-accepted-response-hidden',{scenario,command,receipt:stopReceipt});
    if(scenario==='lost-ack')await route.abort('failed');
    else await route.fulfill({response,contentType:'application/json',body:'{"accepted":'});
   }catch(error){routeErrors.push(error.message);await route.abort('failed').catch(()=>{})}
  };
  const lookup=async route=>{
   const request=route.request(),row={};
   lookups.push(row);
   try{
    assert.equal(request.method(),'POST');
    const body=request.postDataJSON();row.body=body;
    assert.deepEqual(body,{sessionId:target.sessionId,commandId:stopCommand.id,clientId:stopClientId});
    assert.equal(request.headers()['x-amplifier-client'],stopClientId);
    assert.equal(typeof body.clientId,'string');assert.ok(body.clientId);
    if(!acceptedLookup){
     // Keep the lookup unknown through reconnect and a decision attempt.
     row.response={...body,status:'unknown'};row.injectedUnknown=true;
     await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(row.response)});
    }else{
     const response=await route.fetch({timeout:8000});row.response=await response.json();row.httpStatus=response.status();
     assert.equal(response.status(),200);
     assert.deepEqual(row.response,{...body,status:'accepted'});
     await route.fulfill({response});
    }
    diagnostic('stop-passive-lookup',{scenario,...row});
   }catch(error){row.error=error.message;routeErrors.push(error.message);await route.abort('failed').catch(()=>{})}
  };
  try{
   await stopPage.goto(url);await showFixtureConversation(stopPage);
   await stopPage.evaluate(id=>window.amplifier.dispatch('session.select',{id}),target.sessionId);
   const card=stopPage.locator(`[data-approval-id="${target.pendingId}"]`);
   const review=card.getByRole('button',{name:'Review current scope'}),dismiss=card.getByRole('button',{name:'Dismiss old request'});
   const note=card.getByRole('textbox',{name:'Note to Amplifier (optional)'}),noteText=`Keep ${scenario} note`;
   await expect(card.getByRole('button',{name:'Allow',exact:true})).toBeEnabled();
   await note.fill(noteText);
   const before=await inspectTarget(),clientBefore=await stopPage.evaluate(()=>window.amplifier.getState().client.id);
   await stopPage.route('**/api/actions',hideStop);
   await stopPage.route('**/api/conversation/stop-status',lookup);
   await stopPage.locator('button[data-action="conversation.stop"]').click();
   await expect.poll(()=>lookups.some(row=>row.response?.status==='unknown')).toBe(true);
   assert.equal(stopReceipt.accepted,true);assert.equal(stopRequests.length,1);assert.equal(stopClientId,clientBefore);
   await expect(dismiss).toBeEnabled();await expect(note).toHaveValue(noteText);
   await dismiss.click();
   await expect(card.getByRole('alert')).toContainText('Stop was requested');
   await expect(review).toBeEnabled();
   assert.deepEqual(mutations,[],'Unknown Stop sends no decision or dismissal');
   assert.equal((await inspectTarget()).interruptionRevision,1);
   checks.push({name:`Unknown ${scenario} stays fenced`,stopCommand,stopReceipt,lookups:structuredClone(lookups),decisionRequests:[...mutations]});
   if(scenario==='lost-ack'){
    // A different explicit Stop overwrites lastInterruption. The old command's
    // ledger entry, not this newer snapshot, must be used to recover the fence.
    const id='superseding-stop-'+randomUUID();
    const response=await stopPage.request.post(url+'/api/actions',{data:{action:'conversation.stop',args:{sessionId:target.sessionId},id},timeout:8000});
    supersedingStop={id,receipt:await response.json()};
    assert.equal(response.status(),200);assert.equal(supersedingStop.receipt.accepted,true);
    const overwritten=await inspectTarget();
    assert.equal(overwritten.interruptionRevision,2);assert.equal(overwritten.lastInterruption.commandId,id);
    assert.notEqual(overwritten.lastInterruption.commandId,stopCommand.id);
   }
   // Exercise the real same-page transport reconnect, not a synthetic settlement.
   const priorLookups=lookups.length;
   await stopPage.evaluate(()=>window.dispatchEvent(new Event('online')));
   await expect.poll(()=>lookups.length).toBeGreaterThan(priorLookups);
   await expect(review).toBeEnabled();await expect(note).toHaveValue(noteText);
   assert.equal(await stopPage.evaluate(()=>window.amplifier.getState().client.id),clientBefore);
   // A newer Stop changes requestHash even when the card still looks expired.
   // Reconnect lookup arrival is not completion of the card's passive review.
   // Clear the earlier alert through the real UI handler before the one decision
   // attempt: a stale-hash rejection must never stand in for Stop-fence proof.
   const scopeRead=nextPermissionRead();
   await review.click();
   const scoped=await scopeRead;if(scoped.error)throw scoped.error;
   await expect(card.getByRole('region',{name:'Current configuration scope'})).toBeVisible();
   await expect(card.getByRole('alert')).toHaveCount(0);
   await expect(review).toBeEnabled();await expect(note).toHaveValue(noteText);
   assert.equal(acceptedLookup,false);assert.deepEqual(mutations,[]);
   const guardedRead=nextPermissionRead();
   await dismiss.click();await expect(card.getByRole('alert')).toContainText('Stop was requested');
   const guarded=await guardedRead;if(guarded.error)throw guarded.error;
   assert.equal(guarded.receipt.body.result.request.requestHash,scoped.receipt.body.result.request.requestHash,
    'The fenced decision preflight must match the freshly reviewed scope');
   diagnostic('stop-recovery-reviewed-fence',{scenario,review:scoped.receipt,preflight:guarded.receipt,
    decisionRequests:[...mutations],acceptedLookup});
   await expect(review).toBeEnabled();assert.deepEqual(mutations,[]);
   acceptedLookup=true;
   if(scenario==='malformed-ack'){
    // A decision preflight that obtains exact acceptance is still ineligible.
    // Recovery is not permission to continue that already-started decision.
    await dismiss.click();await expect(card.getByRole('alert')).toContainText('Stop was requested');
    await expect(review).toBeEnabled();assert.deepEqual(mutations,[]);
    checks.push({name:'Recovering preflight cannot send its decision',lookups:structuredClone(lookups),decisionRequests:[...mutations]});
   }
   await review.click();
   await expect(card.getByRole('region',{name:'Current configuration scope'})).toBeVisible();
   await expect(review).toBeEnabled();
   await expect(card.getByRole('alert')).toHaveCount(0);
   assert.ok(lookups.some(row=>row.httpStatus===200&&row.response?.status==='accepted'),'Production ledger lookup confirmed the original command');
   assert.deepEqual(mutations,[],'Passive recovery never replays a prior decision');
   await expect(note).toHaveValue(noteText);
   const dismissal=stopPage.waitForResponse(response=>isAction(response.request())&&unwrap(response.request().postDataJSON()).action==='capability.revoke',{timeout:8000})
    .then(response=>({response}),error=>({error}));
   await dismiss.click();
   const received=await dismissal;if(received.error)throw received.error;
   const dismissalReceipt=await received.response.json();
   assert.equal(received.response.status(),200);assert.equal(dismissalReceipt.accepted,true);
   await expect(card).toHaveCount(0);
   assert.equal(mutations.length,1);assert.equal(mutations[0].action,'capability.revoke');
   assert.equal(mutations[0].args.sessionId,target.sessionId);assert.equal(mutations[0].args.comment,noteText);
   assert.ok(mutations[0].args.expectedRequestHash);
   assert.equal(stopRequests.length,1,'Original browser sent Stop exactly once, including across reconnect/review');
   const expectedStops=supersedingStop?2:1;
   await expect.poll(async()=>(await inspectTarget()).stops.filter(id=>id===target.sessionId).length).toBe(expectedStops);
   const after=await inspectTarget(),saved=after.approvals.find(row=>row.id===target.pendingId);
   assert.ok(saved.dismissedAt);assert.equal(saved.dismissalComment,noteText);
   assert.deepEqual(after.approvals.filter(row=>row.id!==target.pendingId),before.approvals.filter(row=>row.id!==target.pendingId));
   for(const key of ['starts','inputs','controls','decisions'])assert.deepEqual(after[key],before[key],`No extra runtime ${key}`);
   assert.equal(after.interruptionRevision,expectedStops);assert.deepEqual(routeErrors,[]);assert.deepEqual(pageErrors,[]);
   await stopPage.screenshot({path:join(output,`stop-${scenario}-recovered.png`),animations:'disabled'});
   checks.push({name:`Passive ${scenario} recovery permits only fresh dismissal`,before,after,clientId:clientBefore,
    stopCommand,stopReceipt,supersedingStop,lookups,decisionRequests:mutations,dismissalReceipt,samePage:true});
  }catch(error){
   diagnostic('stop-recovery-failure',{scenario,error:error.message,routeErrors,lookups,stopRequests,mutations});
   await stopPage.screenshot({path:join(output,`stop-${scenario}-failure.png`),timeout:3000}).catch(()=>{});
   throw error;
  }finally{
   await stopPage.unroute('**/api/actions',hideStop);
   await stopPage.unroute('**/api/conversation/stop-status',lookup);
   await stopContext.close();
  }
 }
 completedReport={source:final.source,authoritySha256:final.authoritySha256,packageIdentity:final.packageIdentity,coldState:true,
  passiveReview:true,twoTabs:true,scopeRace:true,expiryRace:true,exactDismiss:true,adjacentCycle:true,
  keyboard:true,narrow:true,touch:true,reload:true,privateDraft:true,canvasHistory:true,laterPermission:true,heldHistoryPendingDecisions:true,stopIntentOrdering:true,stopAcknowledgementRecovery:true,overwrittenStopRecovery:true,dirtyRefusal:true,retainedEditor:true,lateClose:true,newerDocument:true,scriptedRuntime:true,realProvider:false,checks};
}catch(error){
 terminalError=error;
 await saveFailure(error);
}finally{
 releaseHeld?.();
 try{await settleCanvasReceipts(observedReceipts.values())}catch(error){
  checks.push({name:'Receipt drain failed',error:error.message});terminalError??=error;
 }
 await bounded('state response evidence drain',Promise.allSettled(responseBodies),8000).catch(error=>{
  checks.push({name:'State response evidence drain failed',error:error.message});terminalError??=error;
 });
 await writeFile(join(output,'http.json'),JSON.stringify(http,null,2));
 await writeFile(join(output,'checks.json'),JSON.stringify(checks,null,2));
 await writeFile(join(output,'fixture.log'),fixtureLog);
 await bounded('browser cleanup',browser?.close(),8000).catch(error=>{
  checks.push({name:'Browser cleanup failed',error:error.message});terminalError??=error;
 });
 let cleanupError;
 try{await stopFixture()}catch(error){cleanupError=error;terminalError??=error}
 const cleanup={exitCode:fixture.exitCode,signal:fixture.signalCode,observed:fixture.exitCode!==null||fixture.signalCode!==null,
  graceful:!cleanupError,error:cleanupError?.message??null};
 if(cleanup.exitCode!==0||cleanup.signal!==null)terminalError??=Error('Fixture did not exit normally');
 await writeFile(join(output,'fixture.log'),fixtureLog);
 await writeFile(join(output,'cleanup.json'),JSON.stringify(cleanup));
 process.removeListener('SIGTERM',onTerm);process.removeListener('SIGINT',onInterrupt);
 const report={...completedReport,passed:!!completedReport&&!terminalError,incomplete:!completedReport,error:terminalError?.message??null,checks,cleanup};
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({output,passed:report.passed,incomplete:report.incomplete,error:report.error}));
 if(!report.passed)process.exitCode=1;
}