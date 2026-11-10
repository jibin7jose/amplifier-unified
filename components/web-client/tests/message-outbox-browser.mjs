import {readComposerDraft} from './composer-test-helpers.mjs';
import {createShellHttpFixture,installFixtureEvents} from './shell-http-fixture.mjs';
// Real UI with explicitly controlled admission, lost replies, and server updates.
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
let state={revision:1,settings:{workspace:'/fixture'},runtime:{available:true},view:{navPinned:true},sessions:[{id:'chat',title:'Saved conversation',sessionKind:'root',workspace:'/fixture',workspaceId:'project',status:'idle',historyManaged:true,historyLoaded:true,messages:[],workers:[]}],workspaces:[{id:'project',name:'Fixture',path:'/fixture',available:true}],selectedSessionId:'chat',selectedWorkspaceId:'project',setup:{providers:[{id:'fixture',module:'provider-fixture',config:{default_model:'fixture-model'}}],providersLoadedAt:1,providersWorkspace:'/fixture'},canvas:{open:false}};
const shell=createShellHttpFixture(()=>state);
const waiting=[],calls=[],errors=[];let browser,vite,page,blockedDraft;let blockNextDraft=false;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(check,label){for(let i=0;i<150&&!check();i++)await sleep(10);assert.ok(check(),label)}
async function next(){await until(()=>waiting.length,'Expected pending operation');return waiting.shift()}
const chat=()=>state.sessions.find(s=>s.id===state.selectedSessionId);
async function emit(){state.revision++;await page.evaluate(state=>window.emitState(state),shell.state())}
async function received(item,text=item.body.args.text){const message={id:'server-'+item.body.id,inputId:item.body.id,role:'user',text,createdAt:Date.now()/1000,delivery:{status:'accepted'}};chat().messages.push(message);state.revision++;await item.route.fulfill({json:{accepted:true,delivery:'accepted',state:shell.state()}})}
try{
 vite=await createServer({configFile:false,root:fileURLToPath(new URL('../',import.meta.url)),cacheDir:fileURLToPath(new URL('../.vite-outbox-fixture',import.meta.url)),server:{host:'127.0.0.1',port:0,hmr:false},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});page=await browser.newPage({viewport:{width:1280,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.fixtureClipboard=navigator.clipboard});
 await page.addInitScript(installFixtureEvents);
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  const shellResponse=shell.response(route.request());
  if(shellResponse!==undefined)return route.fulfill({json:shellResponse});
  if(path==='/api/state')return route.fulfill({json:shell.state()});
  if(path==='/api/actions'&&route.request().method()==='GET')return route.fulfill({json:[]});
  if(path!=='/api/actions')return route.fulfill({json:{ok:true}});
  const body=route.request().postDataJSON();calls.push(body);
  if(['conversation.send','conversation.delivery','conversation.retry','message.edit'].includes(body.action)){waiting.push({route,body,client:route.request().headers()['x-amplifier-client']});return}
  if(body.action==='view.update'&&blockNextDraft&&body.args.patch?.draft===''){blockNextDraft=false;blockedDraft={route,body};return}
  if(body.action==='view.update')state.view={...state.view,...body.args.patch};
  state.revision++;return route.fulfill({json:{accepted:true,state:shell.state()}});
 });
 const composer=()=>page.getByRole('textbox',{name:'Message Amplifier'});
 const editor=()=>page.getByRole('textbox',{name:'Edit your message'});
 const forkMode=()=>page.getByLabel('Create a separate chat (fork)');
 const outbox=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')));
 const exactText=async text=>assert.deepEqual(Buffer.from(await editor().inputValue()),Buffer.from(text),'Editor retains byte-exact submitted text');
 // Exercise the mounted callback itself as well as native gestures: a disabled
 // DOM control suppresses click() before the component's guard can be tested.
 const invokeHandler=(locator,name,event={})=>locator.evaluate((node,{name,event})=>{
  const propsKey=Object.keys(node).find(key=>key.startsWith('__reactProps$'));
  if(!propsKey||typeof node[propsKey][name]!=='function')throw Error('Expected mounted React handler: '+name);
  node[propsKey][name]({...event,currentTarget:node,preventDefault(){}});
 },{name,event});
 async function submitTwiceBeforeLock(){
  const locks=await page.locator('.a-message-editor').evaluate(node=>{
   const field=node.querySelector('textarea'),before=field.readOnly;
   node.requestSubmit();const between=field.readOnly;node.requestSubmit();
   return {before,between};
  });
  assert.deepEqual(locks,{before:false,between:false},'Both requestSubmit calls precede the rendered read-only lock');
 }
 async function closedPendingRetry(item,text){
  await editor().waitFor({state:'detached'});await until(()=>state.view.messageEdit===null,'Pending retry editor clear saves');
  const rows=await outbox(),row=rows.find(row=>row.commandId===item.body.id),bubble=page.locator(`[data-input-id="${item.body.id}"]`);
  assert.ok(row);assert.equal(row.status,'sending');assert.deepEqual(Buffer.from(row.text),Buffer.from(text));assert.equal(rows.length,1);
  await bubble.waitFor();assert.equal(await bubble.count(),1);
  assert.equal(await bubble.getByRole('button',{name:'Edit message',exact:true}).isDisabled(),true);
  assert.equal(await bubble.getByRole('button',{name:'Retry',exact:true}).count(),0);
  const before=calls.length;
  await bubble.getByRole('button',{name:'Edit message',exact:true}).evaluate(node=>node.click());
  await invokeHandler(bubble.getByRole('button',{name:'Edit message',exact:true}),'onClick');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await editor().count(),0,'Sending retry cannot be reopened');
  assert.equal(state.view.messageEdit,null);assert.deepEqual(await outbox(),rows);
  assert.deepEqual(calls.slice(before).filter(c=>['view.update','message.edit','conversation.send','conversation.retry'].includes(c.action)),[]);
  assert.equal(calls.filter(c=>c.id===item.body.id).length,1,'Pending retry has exactly one POST');assert.equal(waiting.length,0);
 }
 async function lockedEdit(text,fork=null,pending=true){
  const form=page.locator('.a-message-editor');
  await page.waitForFunction(()=>document.querySelector('.a-message-editor textarea')?.readOnly===true);
  assert.equal(await editor().isEditable(),false);assert.equal(await editor().isDisabled(),false,'Read-only text keeps focus and selection available');
  assert.equal(await editor().getAttribute('aria-busy'),pending?'true':null);assert.equal(await form.getAttribute('aria-busy'),pending?'true':null);
  assert.equal(await form.getByRole('button',{name:'Cancel',exact:true}).isDisabled(),true);assert.equal(await form.locator('button[type=submit]').isDisabled(),true);
  const before=calls.length,editBefore=JSON.stringify(state.view.messageEdit),draftBefore=state.view.draft,outboxBefore=await outbox();
  await editor().focus();assert.equal(await editor().evaluate(node=>node===document.activeElement),true);
  await page.keyboard.type('Pending typing must not replace the submission');
  // Use the native clipboard, retained before the controlled copy fixture.
  await page.evaluate(()=>window.fixtureClipboard.writeText('Pending pasted text — e\u0301\n'));
  await page.keyboard.press('Control+V');await exactText(text);
  if(fork===null)assert.equal(await forkMode().count(),0);
  else {
   assert.equal(await forkMode().isDisabled(),true);assert.equal(await forkMode().getAttribute('aria-busy'),pending?'true':null);
   await forkMode().evaluate(node=>node.click());assert.equal(await forkMode().isChecked(),fork);
   await invokeHandler(forkMode(),'onChange',{target:{checked:!fork}});
  }
  await invokeHandler(editor(),'onChange',{target:{value:'Programmatic mutation must also be guarded'}});
  await editor().focus();await page.keyboard.press('Escape');await page.keyboard.press('Control+Enter');await page.keyboard.press('Meta+Enter');
  await form.getByRole('button',{name:'Cancel',exact:true}).evaluate(node=>node.click());
  await invokeHandler(form.getByRole('button',{name:'Cancel',exact:true}),'onClick');
  // Bypass the disabled submit control to exercise the handler's own guard.
  await form.evaluate(node=>{node.requestSubmit();node.requestSubmit()});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await exactText(text);assert.equal(JSON.stringify(state.view.messageEdit),editBefore);assert.equal(state.view.draft,draftBefore);assert.equal(await readComposerDraft(composer()),draftBefore);
  assert.deepEqual(await outbox(),outboxBefore,'Locked gestures do not mutate the pending outbox');
  assert.deepEqual(calls.slice(before).filter(c=>['view.update','message.edit','conversation.send','conversation.retry'].includes(c.action)),[],'Locked typing/paste/fork/Escape/submit emit no additional writes or admission');
 }
 async function editableAgain(text){
  await page.waitForFunction(()=>document.querySelector('.a-message-editor textarea')?.readOnly===false);
  await exactText(text);assert.equal(await editor().isEditable(),true);assert.equal(await editor().getAttribute('aria-busy'),null);
  assert.equal(await page.locator('.a-message-editor').getAttribute('aria-busy'),null);
  // Measure recovery rather than just inspecting disabled/readOnly attributes.
  await editor().fill(text+'Editable again');
  await until(()=>state.view.messageEdit?.text===text+'Editable again','Rejected edit accepts new typing');
  await editor().fill(text);await until(()=>state.view.messageEdit?.text===text,'Original submitted edit can be restored');
 }
 const send=async text=>{await composer().fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();assert.equal(await readComposerDraft(composer()),'');await page.locator('.a-user').filter({hasText:text}).waitFor();return next()};
 await page.goto(vite.resolvedUrls.local[0]);await composer().waitFor();
 // A lost draft-autosave reply must not prevent a durable outbox send.
 blockNextDraft=true;
 const independent=await send('Send while draft autosave is stalled');
 assert.ok(blockedDraft,'The draft clear is still waiting for its HTTP receipt');
 await received(independent);
 await composer().fill('New draft while old clear waits');
 await blockedDraft.route.fulfill({json:{accepted:true,state:shell.state()}});
 await until(()=>state.view.draft==='New draft while old clear waits','Newer draft saves after the stalled clear');
 assert.equal(await readComposerDraft(composer()),'New draft while old clear waits');
 assert.equal(calls.filter(c=>c.id===independent.body.id).length,1,'Late draft acknowledgement does not resend');
 chat().messages=[];await emit();
 const first=await send('Same text twice intentionally');
 await composer().fill('Same text twice intentionally');await until(()=>state.view.draft==='Same text twice intentionally','New typing saves while admission waits');
 chat().messages.push({id:'first',inputId:first.body.id,role:'user',text:first.body.args.text,delivery:{status:'sending'}});await emit();
 assert.equal(await page.locator('.a-user').count(),1,'Tentative shared bubble replaces optimistic copy');assert.equal(await readComposerDraft(composer()),'Same text twice intentionally');
 chat().messages[0].delivery.status='accepted';state.revision++;await first.route.fulfill({json:{accepted:true,delivery:'accepted',state:shell.state()}});
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);assert.equal(await readComposerDraft(composer()),'Same text twice intentionally','Late acknowledgement cannot clear the next identical draft');
 const failed=await send('Rejected input');await failed.route.fulfill({status:409,json:{accepted:false,error:'Fixture rejection',code:'invalid_input'}});
 const rejected=page.locator('.a-user').filter({hasText:'Rejected input'});await rejected.getByRole('button',{name:'Retry',exact:true}).waitFor();
 await page.context().grantPermissions(['clipboard-read','clipboard-write']);
 await page.evaluate(()=>{window.fixtureClipboard=navigator.clipboard;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>new Promise(resolve=>window.finishCopy=resolve)}})});
 await rejected.getByRole('button',{name:'Copy message as Markdown',exact:true}).click();assert.equal(await rejected.getByRole('button',{name:'Copy message as Markdown',exact:true}).getAttribute('aria-busy'),'true');await page.evaluate(()=>window.finishCopy());
 await rejected.getByRole('button',{name:'Edit message',exact:true}).click();
 const retryText='  Corrected input — e\u0301\nKeep\tspacing.  \n';
 await editor().fill(retryText);assert.equal(await forkMode().count(),0);
 await until(()=>state.view.messageEdit?.text===retryText,'Unsent edit draft saves before admission');
 await composer().fill('Neighbor draft before unsent retry');await until(()=>state.view.draft==='Neighbor draft before unsent retry','Neighbor draft saved');
 await submitTwiceBeforeLock();const corrected=await next();assert.notEqual(corrected.body.id,failed.body.id);assert.equal(corrected.body.action,'conversation.send');assert.equal(corrected.body.args.text,retryText);
 await until(()=>state.view.messageEdit===null,'Retry clears shared editor before admission');await closedPendingRetry(corrected,retryText);
 assert.equal((await outbox()).find(row=>row.commandId===corrected.body.id)?.status,'sending','Retry is attempted, not yet accepted');assert.equal(chat().messages.some(m=>m.inputId===corrected.body.id),false,'Held retry has no confirmed saved message');
 const optimisticRetryId=await page.locator(`[data-input-id="${corrected.body.id}"]`).getAttribute('data-message-id');
 const tentativeRetry={id:'tentative-corrected',inputId:corrected.body.id,role:'user',text:retryText,delivery:{status:'sending'}};
 chat().messages.push(tentativeRetry);await emit();
 await page.locator('[data-message-id="tentative-corrected"]').waitFor();assert.notEqual(tentativeRetry.id,optimisticRetryId,'Shared tentative identity remounts the bubble');await closedPendingRetry(corrected,retryText);
 await composer().fill('Newer draft while unsent retry waits');await until(()=>state.view.draft==='Newer draft while unsent retry waits','Ordinary composer remains editable during retry');
 chat().messages=chat().messages.filter(m=>m.id!==tentativeRetry.id);state.revision++;
 await corrected.route.fulfill({status:409,json:{accepted:false,error:'Fixture corrected-input rejection',code:'invalid_input',state:shell.state()}});
 const correctedBubble=page.locator(`[data-input-id="${corrected.body.id}"]`);
 await correctedBubble.getByRole('button',{name:'Retry',exact:true}).waitFor();assert.equal(await editor().count(),0,'Rejected retry is available through Edit, not an automatic restoration');
 assert.equal(state.view.messageEdit,null);assert.equal(await correctedBubble.getAttribute('data-message-id'),optimisticRetryId);
 await correctedBubble.getByRole('button',{name:'Edit message',exact:true}).click();assert.equal(await forkMode().count(),0);
 await editableAgain(retryText);
 assert.equal((await outbox()).find(row=>row.commandId===corrected.body.id)?.status,'failed');assert.equal((await outbox()).find(row=>row.commandId===corrected.body.id)?.text,retryText);assert.equal(chat().messages.some(m=>m.inputId===corrected.body.id),false,'Rejected attempt is not saved or accepted');
 assert.equal(await readComposerDraft(composer()),'Newer draft while unsent retry waits');assert.equal(waiting.length,0,'Pending gestures did not queue another retry');assert.equal(calls.filter(c=>c.id===corrected.body.id).length,1);
 await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();const correctedAgain=await next();assert.notEqual(correctedAgain.body.id,corrected.body.id);assert.equal(correctedAgain.body.args.text,retryText);await received(correctedAgain);
 await page.locator(`[data-input-id="${correctedAgain.body.id}"]`).waitFor();await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);
 assert.equal(chat().messages.at(-1).text,retryText);assert.equal(chat().messages.at(-1).delivery.status,'accepted');assert.equal(state.sessions.length,1);assert.equal(calls.filter(c=>c.action==='message.edit').length,0,'Unsent edit retries delivery without forking or rewinding');assert.equal(await readComposerDraft(composer()),'Newer draft while unsent retry waits');
 // Starting from a shared failed bubble changes message identity as well as
 // command identity on retry; the outbox, not component state, owns new text.
 const sharedFailed=await send('Old shared tentative input');
 const sharedBubble={id:'shared-failed-retry',inputId:sharedFailed.body.id,role:'user',text:sharedFailed.body.args.text,delivery:{status:'failed'}};
 chat().messages.push(sharedBubble);await emit();
 await sharedFailed.route.fulfill({status:409,json:{accepted:false,error:'Fixture shared rejection',code:'invalid_input'}});
 const sharedEntry=page.locator('[data-message-id="shared-failed-retry"]');
 await sharedEntry.getByRole('button',{name:'Retry',exact:true}).waitFor();await sharedEntry.getByRole('button',{name:'Edit message',exact:true}).click();
 const sharedRetryText='  NEW shared retry — e\u0301\nKeep\tthis text, not the old bubble.  \n';
 await editor().fill(sharedRetryText);await until(()=>state.view.messageEdit?.text===sharedRetryText,'Shared failed edit saves');assert.equal(await forkMode().count(),0);
 await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();const sharedRetry=await next();
 assert.notEqual(sharedRetry.body.id,sharedFailed.body.id);assert.equal(sharedRetry.body.action,'conversation.send');assert.equal(sharedRetry.body.args.text,sharedRetryText);
 await closedPendingRetry(sharedRetry,sharedRetryText);assert.notEqual(await page.locator(`[data-input-id="${sharedRetry.body.id}"]`).getAttribute('data-message-id'),sharedBubble.id);
 chat().messages=chat().messages.filter(m=>m.id!==sharedBubble.id);await emit();await closedPendingRetry(sharedRetry,sharedRetryText);
 await sharedRetry.route.fulfill({status:409,json:{accepted:false,error:'Fixture shared retry rejection',code:'invalid_input'}});
 const sharedRetryBubble=page.locator(`[data-input-id="${sharedRetry.body.id}"]`);
 await sharedRetryBubble.getByRole('button',{name:'Retry',exact:true}).waitFor();assert.equal(await editor().count(),0);
 assert.deepEqual(Buffer.from((await outbox()).find(row=>row.commandId===sharedRetry.body.id).text),Buffer.from(sharedRetryText));
 await sharedRetryBubble.getByRole('button',{name:'Edit message',exact:true}).click();await editableAgain(sharedRetryText);assert.equal(await forkMode().count(),0);
 await page.getByRole('button',{name:'Save & regenerate',exact:true}).click();const sharedRetryAccepted=await next();assert.notEqual(sharedRetryAccepted.body.id,sharedRetry.body.id);assert.equal(sharedRetryAccepted.body.args.text,sharedRetryText);
 await received(sharedRetryAccepted);await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);
 assert.equal(chat().messages.at(-1).text,sharedRetryText);assert.equal(state.sessions.length,1);assert.equal(calls.filter(c=>c.action==='message.edit').length,0);
 const lostRejection=await send('Rejection reply lost');await lostRejection.route.abort('failed');await page.getByRole('button',{name:'Check delivery',exact:true}).click();
 const rejectionCheck=await next();assert.equal(rejectionCheck.body.action,'conversation.delivery');assert.equal(rejectionCheck.body.args.inputId,lostRejection.body.id);await rejectionCheck.route.fulfill({json:{accepted:true,result:{delivery:'not_saved',message:'No saved copy; retry the original submission.'},state:shell.state()}});
 assert.equal(calls.filter(c=>c.action==='conversation.send'&&c.id===lostRejection.body.id).length,1,'Check never resends');
 await page.getByRole('button',{name:'Retry original submission',exact:true}).click();
 const rejectionReceipt=await next();assert.equal(rejectionReceipt.body.action,'conversation.send');assert.equal(rejectionReceipt.body.id,lostRejection.body.id,'Original-submission retry keeps its deduplication identity');assert.equal(calls.filter(c=>c.action==='conversation.send'&&c.id===lostRejection.body.id).length,2,'Only the explicit click retries the original submission');await rejectionReceipt.route.fulfill({json:{accepted:false,duplicate:true,status:409,error:'Saved rejection',state:shell.state()}});
 await page.getByRole('button',{name:'Retry',exact:true}).click();const rejectedRetry=await next();assert.notEqual(rejectedRetry.body.id,lostRejection.body.id);await received(rejectedRetry);
 const delayed=await send('Acknowledgement delayed');chat().status='working';chat().messages.push({id:'delayed',inputId:delayed.body.id,role:'user',text:delayed.body.args.text,delivery:{status:'sending'}});await emit();
 await composer().fill('Keep this next draft');await until(()=>state.view.draft==='Keep this next draft','Draft saves while acknowledgement waits');
 await delayed.route.fulfill({status:504,json:{error:'The runtime operation has not returned yet. It may still be running; do not automatically repeat it.',code:'runtime_pending',delivery:'unknown'}});
 await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Retry',exact:true}).count(),0,'Unknown delivery does not offer a fresh retry');
 assert.equal(await readComposerDraft(composer()),'Keep this next draft');assert.equal(calls.filter(c=>c.id===delayed.body.id).length,1,'Timeout does not automatically resend');
 chat().messages.at(-1).delivery.status='accepted';chat().status='idle';await emit();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);assert.equal(await page.getByText('Acknowledgement delayed',{exact:true}).count(),1);assert.equal(await readComposerDraft(composer()),'Keep this next draft');assert.equal(calls.filter(c=>c.id===delayed.body.id).length,1,'Late acknowledgement does not resend');
 const lost=await send('Delivery uncertain');await lost.route.abort('failed');await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();
 await page.reload();await composer().waitFor();await page.getByRole('button',{name:'Check delivery',exact:true}).click();const checked=await next();assert.equal(checked.body.action,'conversation.delivery');assert.equal(checked.body.args.inputId,lost.body.id);assert.notEqual(checked.client,lost.client,'Reload has a new client identity');
 await checked.route.fulfill({json:{accepted:true,result:{delivery:'sending',message:'The original send is still in progress.'},state:shell.state()}});await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();
 chat().messages.push({id:'late',inputId:lost.body.id,role:'user',text:lost.body.args.text,delivery:{status:'accepted'}});await emit();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')).length===0);assert.equal(await page.getByText('Delivery uncertain',{exact:true}).count(),1);assert.equal(calls.filter(c=>c.action==='conversation.send'&&c.id===lost.body.id).length,1,'Check did not resubmit');
 const editText='  Edit the last input — e\u0301\nKeep\tspacing.  \n',savedHistory=JSON.stringify(chat().messages);
 await page.locator('.a-user').last().getByRole('button',{name:'Edit message',exact:true}).click();await editor().fill(editText);assert.equal(await forkMode().isChecked(),false);
 await until(()=>state.view.messageEdit?.text===editText,'Current-mode edit saved');
 await composer().fill('Neighbor draft before current edit');await until(()=>state.view.draft==='Neighbor draft before current edit','Neighbor draft saved');
 await submitTwiceBeforeLock();const edit=await next();assert.equal(edit.body.action,'message.edit');assert.equal(edit.body.args.mode,'current');assert.equal(edit.body.args.sessionId,'chat');assert.equal(edit.body.args.text,editText);
 await lockedEdit(editText,false);assert.equal(JSON.stringify(chat().messages),savedHistory,'Held edit is only an admission attempt, not changed saved history');
 await composer().fill('Newer draft during current edit');await until(()=>state.view.draft==='Newer draft during current edit','Ordinary composer remains editable during current edit');
 await edit.route.fulfill({status:409,json:{accepted:false,error:'Fixture safe-boundary failure'}});await page.getByText('Fixture safe-boundary failure',{exact:true}).waitFor();await editableAgain(editText);
 assert.equal(await forkMode().isChecked(),false);assert.equal(await forkMode().isEnabled(),true);assert.equal(JSON.stringify(chat().messages),savedHistory);assert.equal(await readComposerDraft(composer()),'Newer draft during current edit');assert.equal(waiting.length,0);assert.equal(calls.filter(c=>c.action==='message.edit').length,1,'Rejected current edit was attempted once');
 await forkMode().check();await until(()=>state.view.messageEdit?.fork===true,'Fork choice saved');await page.getByRole('button',{name:'Create edited chat',exact:true}).click();const fork=await next();
 const retainedMessages=chat().messages;chat().messages=[];await emit();await editor().waitFor({state:'detached'});
 chat().messages=retainedMessages;await emit();await editor().waitFor();await lockedEdit(editText,true);
assert.equal(fork.body.action,'message.edit');assert.equal(fork.body.args.mode,'fork');assert.equal(fork.body.args.text,editText);
 await lockedEdit(editText,true);
 await composer().fill('Newer draft during fork edit');await until(()=>state.view.draft==='Newer draft during fork edit','Ordinary composer remains editable during fork edit');
 await fork.route.fulfill({status:409,json:{accepted:false,error:'Fixture fork safe-boundary failure'}});await page.getByText('Fixture fork safe-boundary failure',{exact:true}).waitFor();await editableAgain(editText);
 assert.equal(await forkMode().isChecked(),true);assert.equal(await forkMode().isEnabled(),true);assert.equal(state.view.messageEdit.fork,true);assert.equal(state.sessions.length,1,'Rejected fork did not create a conversation');assert.equal(JSON.stringify(chat().messages),savedHistory);assert.equal(await readComposerDraft(composer()),'Newer draft during fork edit');assert.deepEqual(await outbox(),[]);assert.equal(waiting.length,0);assert.equal(calls.filter(c=>c.action==='message.edit').length,2,'Each deliberate edit was attempted once, with no queued replay');
 // A blocker arriving while an editor is open also locks every mutation path.
 for(const blocker of [{configurationBusy:true},{workspaceAvailable:false},{historyReadOnlyReason:'Fixture read-only history'},{status:'working'}]){
  const previous={...chat()};Object.assign(chat(),blocker);await emit();await lockedEdit(editText,true,false);
  for(const key of Object.keys(blocker)){if(Object.hasOwn(previous,key))chat()[key]=previous[key];else delete chat()[key]}
  await emit();await page.waitForFunction(()=>document.querySelector('.a-message-editor textarea')?.readOnly===false);
 }
 await forkMode().uncheck();await until(()=>state.view.messageEdit?.fork===false,'Mode editable after rejection and unblock');await forkMode().check();await until(()=>state.view.messageEdit?.fork===true,'Fork choice restored');
 await page.getByRole('button',{name:'Create edited chat',exact:true}).click();const acceptedFork=await next();assert.equal(acceptedFork.body.args.mode,'fork');assert.equal(acceptedFork.body.args.text,editText);assert.notEqual(acceptedFork.body.id,fork.body.id);state.view.messageEdit=null;state.revision++;await acceptedFork.route.fulfill({json:{accepted:true,state:shell.state()}});
 await editor().waitFor({state:'detached'});assert.equal(calls.filter(c=>c.action==='message.edit').length,3,'No hidden admission beyond the three deliberate edit attempts');
 // Choosing another editor while admission waits does not cancel admitted
 // work, and an old rejection must not issue any global restoration write.
 for(const mode of ['current','fork']){
  const oldMessage=chat().messages.at(-1),newMessage=chat().messages[0],historyBefore=JSON.stringify(chat().messages);
  const oldEntry=page.locator(`[data-message-id="${oldMessage.id}"]`),newEntry=page.locator(`[data-message-id="${newMessage.id}"]`);
  await oldEntry.getByRole('button',{name:'Edit message',exact:true}).click();await editor().fill(editText);
  if(mode==='fork')await forkMode().check();
  await until(()=>state.view.messageEdit?.text===editText&&state.view.messageEdit?.fork===(mode==='fork'),'Old editor choice saves');
  await page.getByRole('button',{name:mode==='fork'?'Create edited chat':'Save & regenerate',exact:true}).click();const oldEdit=await next();assert.equal(oldEdit.body.args.mode,mode);await lockedEdit(editText,mode==='fork');
  await newEntry.getByRole('button',{name:'Edit message',exact:true}).click();
  const newerText='  Newer editor while '+mode+' waits — e\u0301\nKeep\tmy choice.  \n';
  await editor().fill(newerText);await forkMode().check();
  await until(()=>state.view.messageEdit?.messageId===newMessage.id&&state.view.messageEdit?.text===newerText&&state.view.messageEdit?.fork===true,'Newer editor selection saves');
  const newerEdit=JSON.stringify(state.view.messageEdit),writesBefore=calls.length;
  await oldEdit.route.fulfill({status:409,json:{accepted:false,error:'Fixture old '+mode+' rejection'}});
  await oldEntry.getByText('Fixture old '+mode+' rejection',{exact:true}).waitFor();
  await page.waitForFunction(id=>document.querySelector(`[data-message-id="${id}"] button[aria-label="Edit message"]`)?.getAttribute('aria-busy')===null,oldMessage.id);
  assert.equal(await editor().count(),1);assert.equal(await newEntry.getByRole('textbox',{name:'Edit your message'}).isEditable(),true);await exactText(newerText);
  assert.equal(JSON.stringify(state.view.messageEdit),newerEdit);assert.equal(await forkMode().isChecked(),true);
  assert.deepEqual(calls.slice(writesBefore).filter(c=>c.action==='view.update'),[],'Old rejection needs no restoration write');
  assert.equal(JSON.stringify(chat().messages),historyBefore);assert.deepEqual(await outbox(),[]);assert.equal(calls.filter(c=>c.id===oldEdit.body.id).length,1);assert.equal(waiting.length,0);
  await page.locator('.a-message-editor').getByRole('button',{name:'Cancel',exact:true}).click();await editor().waitFor({state:'detached'});await until(()=>state.view.messageEdit===null,'Settled editor can be canceled');
 }
 chat().status='working';chat().collaborationGeneration={id:'original-run',terminal:false};await emit();
 await composer().fill('Please find a good pause point.');await page.getByRole('button',{name:'Send message',exact:true}).click();
 const correction=await next();assert.equal(correction.body.args.expectedGenerationId,'original-run','Composer binds steering to the observed run');assert.equal(correction.body.args.allowNextTurn,true,'Ordinary messages may cross normal completion');
 const steered={id:'steered',inputId:correction.body.id,role:'user',text:correction.body.args.text,delivery:{status:'accepted'},steering:{generationId:'original-run',disposition:'queued'}};
 chat().messages.push(steered);state.revision++;await correction.route.fulfill({json:{accepted:true,delivery:'accepted',steering:steered.steering,state:shell.state()}});
 await page.getByText('Received by the active run; waiting for its next step…',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Check delivery',exact:true}).click();const steeringCheck=await next();assert.equal(steeringCheck.body.action,'conversation.delivery');
 await steeringCheck.route.fulfill({json:{accepted:true,result:{delivery:'accepted',steering:steered.steering},state:shell.state()}});
 steered.steering.disposition='applied';await emit();await page.getByText('Added to the active run’s context',{exact:true}).waitFor();
 assert.equal(calls.filter(c=>c.id===correction.body.id).length,1,'Steering updates and checks never resend');
 await page.screenshot({path:'/tmp/amplifier-steering-applied.png'});

 // A rejected send keeps its text, but an explicit Retry must not reuse the expired run.
 const boundary=await send('This is another message, not a correction.');
 assert.equal(boundary.body.args.expectedGenerationId,'original-run');
 chat().status='idle';chat().collaborationGeneration.terminal=true;await emit();
 await boundary.route.fulfill({status:409,json:{accepted:false,code:'steering_target_changed',error:'The run changed. Your message was not sent.'}});
 const boundaryRejected=page.locator(`[data-input-id="${boundary.body.id}"]`);
 await boundaryRejected.getByRole('button',{name:'Retry',exact:true}).click();
 const retried=await next();assert.notEqual(retried.body.id,boundary.body.id);
 assert.equal(retried.body.args.expectedGenerationId,undefined,'Explicit retry refreshes the expired generation');
 assert.equal(retried.body.args.allowNextTurn,true);assert.equal(retried.body.args.text,boundary.body.args.text);
 await received(retried);
 chat().status='idle';chat().collaborationGeneration.terminal=true;await emit();
 await page.screenshot({path:'/tmp/amplifier-optimistic-messages.png'});await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{const save=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key==='amplifier.messageOutbox.v1')throw new DOMException('Fixture quota','QuotaExceededError');return save.call(this,key,value)}});const mobile=await send('Mobile failed message');await mobile.route.fulfill({status:409,json:{accepted:false,error:'Fixture rejection'}});await page.getByRole('button',{name:'Retry',exact:true}).waitFor();await page.getByText('This browser could not save the pending message. Keep this tab open until delivery is confirmed.',{exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/amplifier-message-retry-mobile.png'});
 await page.addInitScript(()=>Object.defineProperty(window,'sessionStorage',{get(){throw new DOMException('Fixture storage blocked','SecurityError')}}));await page.reload();await composer().waitFor();
 const blockedStorage=await send('Storage unavailable');await blockedStorage.route.fulfill({status:409,json:{accepted:false,error:'Fixture rejection'}});
 await page.getByRole('button',{name:'Retry',exact:true}).waitFor();await page.getByText('This browser could not save the pending message. Keep this tab open until delivery is confirmed.',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log('Outbox browser passed: immediate clear/bubble, late identical draft, server dedup, held current/fork locks typing/paste/mode/Escape/submit and direct handlers, pre-render double requestSubmit, closed pending retry with byte-exact outbox text across local/shared identities and explicit Edit after rejection, old rejection preserves newer editor without restoration writes, blocked editor guards, neighboring drafts/outbox, delayed504/late acknowledgement without replay, uncertain reload/check, mobile, blocked storage. Controlled transport measures admission/saved/accepted states, not model execution.');
}finally{await browser?.close();await vite?.close()}
