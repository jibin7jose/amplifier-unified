import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public local-file path, exact server location, copy and Canvas lifecycle.
import {spawn} from 'node:child_process';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {boundaryRead,BOUNDARY_OBSERVATION_MS} from './browser-boundary-evidence.mjs';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';
import {mkdir,writeFile,lstat} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory is required');
await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/outputs_ui_server.py','--canvas-reading'],{stdio:['ignore','pipe','pipe']});
const stopFixture=ownFixture(fixture,{label:'Canvas files fixture',timeoutMs:5000});
let browser,page,ready,focusScope,focusInvoker,logs='',failure,uiFailure,uiPassed=false,browserClosed=null,fixtureExit=null,fixtureCleanup=null,homeRemoved=null,fixtureStopSucceeded=false;
const errors=[],checks=[],cleanupFailures=[],evidenceFailures=[];
const focusTraversal={complete:false,initial:null,steps:[]};
const focusReturn={complete:false,before:null,after:null,observations:[],observationDeadlineMs:BOUNDARY_OBSERVATION_MS};
const narrowDismiss={complete:false,observations:[],observationDeadlineMs:BOUNDARY_OBSERVATION_MS};
const errorRecord=error=>({name:error?.name||'Error',message:String(error?.message||error),...(error instanceof AggregateError?{errors:error.errors.map(errorRecord)}:{})});
async function focusEvidence(key,read){
 const observation={key,...await boundaryRead(read)};focusReturn.observations.push(observation);
 // Only this bounded result can publish evidence. The pending read never owns
 // the record, so a late reply/rejection cannot rewrite a timeout or escape.
 if(observation.status==='observed')focusReturn[key]=observation.value;
 else evidenceFailures.push(Error(`Focus-return ${key} evidence ${observation.status}`));
}
async function narrowEvidence(key,read){
 const observation={key,...await boundaryRead(read)};narrowDismiss.observations.push(observation);
 if(observation.status==='observed')narrowDismiss[key]=observation.value;
 else evidenceFailures.push(Error(`Narrow dismissal ${key} evidence ${observation.status}`));
}
fixture.once('close',(code,signal)=>{fixtureExit={pid:fixture.pid,code,signal,settled:true}});
let fixtureLines='';
fixture.stdout.on('data',chunk=>{
 fixtureLines+=chunk;let newline;
 while((newline=fixtureLines.indexOf('\n'))!==-1){
  const line=fixtureLines.slice(0,newline);fixtureLines=fixtureLines.slice(newline+1);
  try{const receipt=JSON.parse(line).outputsFixtureCleanup;if(receipt)fixtureCleanup=receipt}catch{}
 }
});
fixture.stderr.on('data',chunk=>logs+=chunk);
try{
 ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('File fixture timed out')),15000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('File fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:950},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep private file-inspection draft');
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();const canvas=page.locator('#workspace-canvas');await expect(canvas).toBeVisible();
 await canvas.getByRole('button',{name:'Open file',exact:true}).click();const inspector=canvas.locator('.a-canvas-inspector'),form=inspector.locator('.a-canvas-file-form');
 await form.getByLabel('File in this workspace',{exact:true}).fill('report.txt');await form.getByRole('combobox',{name:'Canvas file format',exact:true}).selectOption('text');await form.getByRole('button',{name:'Open file',exact:true}).click();
 await inspector.getByRole('button',{name:'Close canvas details',exact:true}).click();await expect(inspector).toBeHidden();
 await expect(canvas.getByRole('region',{name:'Artifact view',exact:true})).toContainText('Original report bytes.');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas?.path)).toBe(ready.workspace+'/report.txt');
 const resourceId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await canvas.getByRole('button',{name:'Canvas details',exact:true}).click();await expect(inspector).toBeVisible();
 const paths=inspector.locator('details.a-canvas-file-path');await paths.getByText('File details',{exact:true}).click();
 await expect(paths.locator('code')).toHaveText([ready.workspace+'/report.txt',ready.workspace,'report.txt']);
 const owner=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary').fileReveal);
 assert.ok(owner.host.id);assert.ok(owner.host.label);await expect(paths.getByText('On '+owner.host.label,{exact:true})).toBeVisible();assert.equal(owner.available,false);
 await expect(paths.getByText('No supported desktop file manager is available on this host.',{exact:true})).toBeVisible();
 await paths.getByRole('button',{name:'Copy full path',exact:true}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(ready.workspace+'/report.txt');
 await paths.getByRole('button',{name:'Copy relative path',exact:true}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('report.txt');
 checks.push('Public file form opens exact text bytes and the named server path; both copies match fixture ownership; missing server file manager is explicit.');
 await inspector.getByRole('button',{name:'Close canvas details',exact:true}).click();await expect(inspector).toBeHidden();
 const focusButton=canvas.getByRole('button',{name:'Focus canvas',exact:true});
 const pendingInvoker=focusButton.elementHandle(),invoker=await boundaryRead(()=>pendingInvoker);
 focusReturn.invokerAcquisition={status:invoker.status};
 if(invoker.status!=='observed'){
  // A deadline is not cancellation. Dispose a late-owned handle once, but do
  // not let that completion mutate this already-final acquisition receipt.
  void pendingInvoker.then(handle=>handle&&boundaryRead(()=>handle.dispose()),()=>{}).catch(()=>{});
  throw Error(`Focus-return invoker acquisition ${invoker.status}`);
 }
 focusInvoker=invoker.value;assert.ok(focusInvoker);
 await focusButton.click();await expect(canvas).toHaveAttribute('role','dialog');await expect(canvas.getByRole('button',{name:'Exit canvas focus',exact:true})).toBeFocused();
 // useModalFocus includes the container-header toggle before the panel, and
 // CanvasTabs uses roving tabindex. Neither negative tabs nor inert controls
 // belong to sequential focus. Retain nodes; only real keyboard input moves focus.
 focusScope=await canvas.evaluateHandle(panel=>[document.getElementById('canvas-toggle'),
  ...panel.querySelectorAll('button,input,textarea,select,summary,a[href],[tabindex]')]
  .filter(el=>el&&el.tabIndex>=0&&!el.matches(':disabled')&&!el.closest('[inert],[hidden]')
   &&el.getClientRects().length&&getComputedStyle(el).visibility==='visible'));
 const focusState=()=>focusScope.evaluate(nodes=>{
  const describe=el=>el?{tag:el.tagName,id:el.id,label:el.getAttribute('aria-label')||el.textContent.trim().slice(0,120),
   role:el.getAttribute('role'),tabIndex:el.tabIndex,connected:el.isConnected,disabled:el.matches(':disabled'),
   inert:!!el.closest('[inert]'),visible:!!el.getClientRects().length&&!el.closest('[hidden]')&&getComputedStyle(el).visibility==='visible'}:null;
  return {nodes:nodes.map(describe),activeIndex:nodes.indexOf(document.activeElement),active:describe(document.activeElement)};
 });
 focusTraversal.initial=await focusState();
 const initial=focusTraversal.initial,count=initial.nodes.length,start=initial.activeIndex;
 assert.ok(count>1&&count<=64,'Unexpected focused Canvas scope size');
 assert.equal(initial.nodes[0].id,'canvas-toggle');
 assert.ok(start>0);assert.equal(initial.active.label,'Exit canvas focus');
 assert.ok(initial.nodes.every(node=>node.tabIndex===0),'Fixture expects ordinary sequential tab order, not positive tabindex');
 const pressTab=async(key,expectedIndex)=>{
  const step={key,expectedIndex,before:await focusState(),after:null};focusTraversal.steps.push(step);
  await page.keyboard.press(key);step.after=await focusState();
  assert.deepEqual(step.after.nodes,initial.nodes,'Focus-scope node identity/state changed during keyboard traversal');
  assert.equal(step.after.activeIndex,expectedIndex,'Native sequential focus did not reach the expected scoped control');
 };
 // Walk to the boundary without locator.focus(), then exercise both wraps.
 for(let index=start+1;index<count;index++)await pressTab('Tab',index);
 await pressTab('Tab',0);await pressTab('Shift+Tab',count-1);
 focusTraversal.complete=true;
 await focusEvidence('before',focusState);
 let returnFailure;
 try{
  await page.keyboard.press('Escape');await expect(canvas).not.toHaveAttribute('role','dialog');
  await expect(canvas.getByRole('button',{name:'Focus canvas',exact:true})).toBeFocused();
 }catch(error){
  // Publish the original assertion before optional observations can time out.
  returnFailure=error;uiFailure=error;focusReturn.primaryFailure=errorRecord(error);
 }
 await focusEvidence('after',focusState);
 await focusEvidence('invoker',()=>focusInvoker.evaluate(node=>({connected:node.isConnected,tag:node.tagName,
   label:node.getAttribute('aria-label'),focused:document.activeElement===node,
   sameControl:node===document.querySelector('#workspace-canvas [aria-label="Focus canvas"]'),
   inert:!!node.closest('[inert]'),visible:!!node.getClientRects().length})));
 await focusEvidence('canvas',()=>canvas.evaluate(node=>({role:node.getAttribute('role'),focused:node.dataset.focused,hidden:node.hidden})));
 if(returnFailure)throw returnFailure;
 assert.equal(evidenceFailures.length,0,'Focus-return evidence is incomplete; not a pass');
 assert.deepEqual(focusReturn.invoker,{connected:true,tag:'BUTTON',label:'Focus canvas',focused:true,sameControl:true,inert:false,visible:true});
 assert.deepEqual(focusReturn.canvas,{role:null,focused:'false',hidden:false});focusReturn.complete=true;
 const anchor=page.locator('.a-messages [data-message-id]').filter({hasText:'Canvas reading marker 55'});await anchor.scrollIntoViewIfNeeded();
 const anchorTop=()=>anchor.evaluate(el=>el.getBoundingClientRect().top-el.closest('.a-messages').getBoundingClientRect().top);
 const readingTop=await anchorTop();const readingId=await anchor.getAttribute('data-message-id');
 await canvas.getByRole('button',{name:'Close canvas panel',exact:true}).click();await expect(canvas).toBeHidden();await expect(page.getByRole('button',{name:'Open canvas',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();await expect(canvas).toBeVisible();assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),resourceId);await expect(canvas.getByRole('region',{name:'Artifact view',exact:true})).toContainText('Original report bytes.');
 await expect.poll(async()=>Math.abs(await anchorTop()-readingTop)).toBeLessThan(3);assert.equal(await anchor.getAttribute('data-message-id'),readingId);
 checks.push('Visible saved-message anchor remains within 3px after closing/reopening Canvas; exact message identity retained.');
 checks.push('Focus/Escape and close/reopen retain resource identity and return focus to the invoking control.');
 // The current canonical return-to-chat control is Close Canvas on every
 // layout (docs/canvas.md); no product branch renders the former Back to chat.
 const dismiss=canvas.getByRole('button',{name:'Close canvas panel',exact:true});
 const workspace=page.locator('[data-part="workspace"][data-pane-layout]');
 const narrowState=()=>page.evaluate(()=>{
  const state=window.amplifier.getState(),panel=document.querySelector('#workspace-canvas'),layout=panel.closest('.a-layout');
  const button=panel.querySelector('[aria-label="Close canvas panel"]'),box=button?.getBoundingClientRect();
  return {viewport:{width:innerWidth,height:innerHeight},mediaNarrow:matchMedia('(max-width:900px)').matches,
   narrow:layout.dataset.narrow,overlay:layout.dataset.canvasOverlay,canvasHidden:panel.hidden,
   canvasOpen:state.canvas.open,resourceId:state.canvas.id,clientId:state.client.id,sessionId:state.selectedSessionId,
   draft:state.view.draft,overflow:panel.scrollWidth>panel.clientWidth,
   button:button?{tag:button.tagName,label:button.getAttribute('aria-label'),text:button.textContent.trim(),
    action:button.dataset.action,visible:!!button.getClientRects().length&&!button.closest('[hidden],[inert]'),
    width:box.width,height:box.height,left:box.left,right:box.right,top:box.top,bottom:box.bottom}:null};
 });
 try{
  await page.setViewportSize({width:390,height:844});
  await expect(workspace).toHaveAttribute('data-narrow','true');
  await expect(workspace).toHaveAttribute('data-canvas-overlay','true');
  await expect(dismiss).toBeVisible();await expect(dismiss).toHaveText('Close Canvas');
  await narrowEvidence('before',narrowState);
  assert.equal(evidenceFailures.length,0,'Narrow dismissal evidence incomplete');
  const before=narrowDismiss.before;
  assert.deepEqual(before.viewport,{width:390,height:844});assert.equal(before.mediaNarrow,true);
  assert.equal(before.narrow,'true');assert.equal(before.overlay,'true');assert.equal(before.canvasHidden,false);
  assert.equal(before.canvasOpen,true);assert.equal(before.resourceId,resourceId);
  assert.equal(before.sessionId,ready.sessionId);assert.equal(before.draft,'Keep private file-inspection draft');
  assert.equal(before.overflow,false);
  assert.equal(before.button.tag,'BUTTON');assert.equal(before.button.action,'canvas.visibility');assert.equal(before.button.visible,true);
  assert.ok(before.button.width>=44&&before.button.height>=44&&before.button.left>=0&&before.button.right<=390&&before.button.top>=0&&before.button.bottom<=844);
  await dismiss.click();await expect(canvas).toBeHidden();await expect(composer).toBeVisible();
  // Reuse the canonical close journey's readback boundary: optimistic hiding
  // alone is not proof that the host accepted the visibility change.
  // Poll deadlines do not cancel callbacks. Keep their receipts local, and
  // publish a detached immutable value only after the awaited poll succeeds.
  let hostReceipt;
  narrowDismiss.host=null;
  await expect.poll(async()=>{
   const response=await page.request.get(ready.url+'/api/state?clientId='+encodeURIComponent(before.clientId),{timeout:5000});
   assert.equal(response.status(),200);const body=await boundaryRead(()=>response.json());
   assert.equal(body.status,'observed','Host visibility readback unavailable');const state=body.value;
   hostReceipt={requestClientId:before.clientId,httpStatus:response.status(),canvasOpen:state.canvas.open,
    resourceId:state.canvas.id,sessionId:state.selectedSessionId};
   return state.canvas.open;
  }).toBe(false);
  narrowDismiss.host=Object.freeze({...hostReceipt});
 }catch(error){uiFailure??=error;narrowDismiss.primaryFailure=errorRecord(error)}
 await narrowEvidence('after',narrowState);
 if(uiFailure)throw uiFailure;
 assert.equal(evidenceFailures.length,0,'Narrow dismissal evidence incomplete');
 assert.equal(narrowDismiss.after.canvasHidden,true);assert.equal(narrowDismiss.after.canvasOpen,false);
 for(const key of ['resourceId','clientId','sessionId','draft'])assert.equal(narrowDismiss.after[key],narrowDismiss.before[key]);
 assert.equal(narrowDismiss.host.resourceId,resourceId);assert.equal(narrowDismiss.host.sessionId,ready.sessionId);
 narrowDismiss.hostHiddenConfirmed=true;narrowDismiss.complete=true;
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep private file-inspection draft');
 const final=await page.request.get(ready.url+'/fixture').then(response=>response.json());assert.equal(final.selected,ready.sessionId);assert.equal(final.message,ready.original);assert.equal(final.file,'Original report bytes.');assert.deepEqual(final.outputs,[]);assert.deepEqual(final.sent,[]);assert.deepEqual(errors,[]);
 checks.push('Narrow Close Canvas returns to chat and preserves the exact draft, selection, original message and original file; no output or model input created.');
 await page.screenshot({path:out+'/canvas-files.png'});uiPassed=true;
}catch(error){uiFailure??=error}
finally{
 try{
  await finishFixture({label:'Canvas files fixture',close:[
   ...(uiFailure&&page?[['Canvas files failure screenshot',()=>page.screenshot({path:out+'/failure.png'})]]:[]),
   ...(focusScope?[['Canvas files retained focus scope',()=>focusScope.dispose()]]:[]),
   ...(focusInvoker?[['Canvas files retained focus invoker',async()=>{
    const disposal=await boundaryRead(()=>focusInvoker.dispose());focusReturn.invokerDisposal={status:disposal.status};
    assert.equal(disposal.status,'observed','Focus-return invoker disposal did not settle');
   }]]:[]),
   ['Canvas files browser',async()=>{if(browser){try{await browser.close()}finally{browserClosed=!browser.isConnected()}assert.equal(browserClosed,true)}}]
  ],stop:async()=>{await stopFixture();fixtureStopSucceeded=true}});
 }catch(error){cleanupFailures.push(error)}
 const expectedHome=ready?.workspace?dirname(ready.workspace):null;
 if(expectedHome){
  try{await lstat(expectedHome);homeRemoved=false}catch(error){if(error.code==='ENOENT')homeRemoved=true;else cleanupFailures.push(error)}
 }
 try{
  assert.equal(fixtureExit?.settled,true);assert.equal(fixtureExit?.code,0);assert.equal(fixtureExit?.signal,null);
  assert.equal(fixtureStopSucceeded,true);assert.equal(browserClosed,true);
  assert.ok(expectedHome);assert.equal(fixtureCleanup?.pid,fixture.pid);assert.equal(fixtureCleanup?.home,expectedHome);
  assert.equal(fixtureCleanup?.signal,'SIGTERM');assert.equal(fixtureCleanup?.runnerCleanup,true);assert.equal(fixtureCleanup?.homeRemoved,true);
  assert.equal(homeRemoved,true);
 }catch(error){cleanupFailures.push(error)}
 try{await writeFile(out+'/fixture.log',logs)}catch(error){cleanupFailures.push(error)}
 const failures=[...(uiFailure?[uiFailure]:[]),...evidenceFailures,...cleanupFailures];
 failure=failures.length>1?new AggregateError(failures,'Canvas files UI and/or cleanup failed'):failures[0];
 const observations={schemaVersion:1,passed:uiPassed&&!failure,uiPassed,checks,focusTraversal,focusReturn,narrowDismiss,browserClosed,fixtureExit,fixtureStopSucceeded,fixtureCleanup,homeRemoved,uiFailure:uiFailure?errorRecord(uiFailure):null,evidenceFailures:evidenceFailures.map(errorRecord),cleanupFailures:cleanupFailures.map(errorRecord),failure:failure?errorRecord(failure):null,limits:['Synthetic local Linux host with no file manager; no actual OS reveal action or remote browser/server pair.','Native selectOption exercises form state, not native picker gesture.','Desktop ordinary path plus keyboard focus/Escape and narrow Close Canvas; not complete four-mode repetition.','No Canvas resize or physical user-scroll gesture claim; anchor positioned through locator before public close/reopen.','Optional focus/narrow evidence has 500ms observation deadlines, not cancellation; late results never rewrite saved observations.']};
 try{await writeFile(out+'/observations.json',JSON.stringify(observations,null,2)+'\n')}
 catch(error){
  failure=failure?new AggregateError([failure,error],'Canvas files observations could not be saved'):error;
  observations.passed=false;observations.reportWriteFailure=errorRecord(error);observations.failure=errorRecord(failure);
 }finally{console.log(JSON.stringify({canvasFilesQualification:observations}))}
}
if(failure)throw failure;
console.log('Canvas file path passed: exact local bytes and owner, absolute/relative copy, unavailable reveal, focus return, narrow Close Canvas and unchanged chat/draft/file.');
