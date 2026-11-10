import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
// Packaged UI + real scoped HTTP/WebSocket; synthetic progress only, no model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=process.env.AMPLIFIER_HUMAN_POST_ONLY?null:spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/active_client_performance_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
const launchBrowser=()=>chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
async function settleHumanFixture(child){
 if(child.exitCode!==null||child.signalCode!==null){
  assert.equal(child.exitCode,0);assert.equal(child.signalCode,null);return;
 }
 const exit=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>finish(Error('Human fixture did not exit within 10 seconds')),10000);
  const exited=(code,signal)=>finish(null,{code,signal});
  const failed=error=>finish(error);
  function finish(error,receipt){
   clearTimeout(timer);child.removeListener('exit',exited);child.removeListener('error',failed);
   if(error)reject(error);else resolve(receipt);
  }
  child.once('exit',exited);child.once('error',failed);
  if(!child.kill('SIGTERM'))finish(Error('Human fixture TERM could not be delivered'));
 });
 assert.deepEqual(exit,{code:0,signal:null},'Human fixture must settle cleanly before browser shutdown');
}
async function humanPostRecency(){
 const held=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/human_post_recency_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
 let context,bodyError;
 try{
  const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Human post fixture timeout')),45000);held.once('exit',code=>{clearTimeout(timer);reject(Error('Human post fixture exited '+code))});let output='';held.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const d=JSON.parse(line);if(d.url){clearTimeout(timer);resolve(d.url)}}catch{}})});
  context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-human-post-token'},viewport:{width:1280,height:1000}});
  const page=await context.newPage(),other=await context.newPage();
  const errors=[],traffic=[];let measuring=false,sendPayload;
  page.on('pageerror',e=>errors.push(e.message));
  other.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{const path=new URL(r.url()).pathname;if(measuring&&['/api/shell','/api/view'].includes(path))traffic.push(r.method()+' '+path);if(path==='/api/actions'&&r.postDataJSON()?.action==='conversation.send')sendPayload=r.postDataJSON()});
  const metrics=async()=>{const r=await page.request.get(url+'/fixture/metrics');assert.equal(r.status(),200);return r.json()};
  const expectDraft=async(target,text)=>{
   const composer=target.getByRole('textbox',{name:'Message Amplifier',exact:true});
   await expect.poll(()=>target.evaluate(()=>window.amplifier.getState().view.draft)).toBe(text);
   await expect.poll(()=>composer.textContent()).toBe(text);
   const identity=await target.evaluate(()=>window.amplifier.getState().client.id);
   const sid=await target.evaluate(()=>window.amplifier.getState().selectedSessionId);
   await expect.poll(async()=>(await metrics()).durableClients[identity]?.drafts?.[sid]).toBe(text);
  };
  const control=async data=>{const r=await page.request.post(url+'/fixture/control',{data});assert.equal(r.status(),200);return r.json()};
  const dispatch=(p,action,args)=>p.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
  const showChat=async target=>{
   if(await target.locator('.a-settings-experience').isVisible())await target.getByRole('button',{name:'Close panel',exact:true}).click();
   await expect(target.locator('[data-part="overlay"]')).toHaveCount(0);
  };
  const order=()=>page.locator('.a-quiet-sidebar [data-sidebar-section=recent] .a-nav-chat[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId));
  await page.goto(url);await showFixtureConversation(page,{navPinned:true});await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentShortcuts);
  await expect(page.locator('.a-quiet-sidebar [data-sidebar-section=recent] .a-nav-chat[data-session-id]')).toHaveCount(20);
  await showChat(page);
  const initial=await metrics(),target=initial.target;
  assert.equal(initial.sessions.length,24);
  await expect(page.locator('.a-quiet-sidebar')).toBeVisible();
  const before=await order();assert.equal(before.length,20);assert.ok(!before.includes(target));
  await other.goto(url);await showFixtureConversation(other,{navPinned:true});await other.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
  await showChat(other);
  await dispatch(other,'session.select',{id:initial.sessions[1]});
  await other.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep other-client draft');
  await expectDraft(other,'Keep other-client draft');
  await dispatch(other,'attachment.add',{sessionId:initial.sessions[1],name:'other-reference.txt',base64:'aGVsbG8='});
  await dispatch(page,'attachment.add',{sessionId:target,name:'posted-reference.txt',base64:'aGVsbG8='});
  const attachmentId=await page.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id).draftAttachments[0].id,target);
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Explicit human post');
  await expectDraft(page,'Explicit human post');
  // Start the actual shared HTTP action without awaiting its held acknowledgement.
  await page.evaluate(([sessionId,attachmentId])=>{window.heldPostReceipt=window.amplifier.dispatch('conversation.send',{sessionId,text:'Explicit human post',attachmentIds:[attachmentId],preserveDraft:true})},[target,attachmentId]);
  await expect.poll(async()=>(await metrics()).inputs.length).toBe(1);
  await expect.poll(order).toEqual([target,...before.slice(0,19)]);
  const admitted=await metrics(),stable=admitted.navigationActivityAt;
  assert.ok(stable>initial.navigationActivityAt);
  assert.equal(admitted.assistantCount,0);
  assert.equal(admitted.posts[0].navigationPost.disposition,'pending');
  assert.equal(admitted.posts[0].attachments[0].id,attachmentId);
  assert.equal(admitted.posts[0].navigationPost.inputId,sendPayload.id);
  const duplicate=await page.request.post(url+'/api/actions',{data:sendPayload});
  assert.equal(duplicate.status(),200);assert.equal((await duplicate.json()).duplicate,true);
  assert.equal((await metrics()).navigationActivityAt,stable);
  const promotedOrder=await order();
  await control({running:true,history:true});
  await expect.poll(async()=>(await metrics()).ticks).toBeGreaterThan(3);
  // Drain the initial working presentation before measuring steady progress.
  // The fixture leaves gaps longer than the report debounce, so repeated
  // elapsed-label changes cannot hide behind continuous event traffic.
  await control({running:false});
  await page.waitForTimeout(1000);
  const key=await page.evaluate(()=>window.amplifier.getState().shellDataKey);
  const workLabel=page.locator('.a-execution-turn-line .a-execution-label');
  await expect(page.locator('[data-part="overlay"]')).toHaveCount(0);await expect(workLabel).toBeVisible();
  const elapsedBefore=await workLabel.innerText();assert.match(elapsedBefore,/^Working · /);
  traffic.length=0;measuring=true;
  await control({running:true});
  await page.waitForTimeout(2200);measuring=false;
  assert.deepEqual(await order(),promotedOrder);
  assert.equal(await page.evaluate(()=>window.amplifier.getState().shellDataKey),key);
  assert.notEqual(await workLabel.innerText(),elapsedBefore,'Visible elapsed time must keep advancing without reporting it back');
  assert.deepEqual(traffic,[],'posted progress must not cause steady shell/view refetches');
  assert.equal((await metrics()).navigationActivityAt,stable);
  await expectDraft(other,'Keep other-client draft');
  assert.equal(await other.evaluate(()=>window.amplifier.getState().selectedSessionId),initial.sessions[1]);
  assert.equal(await other.evaluate(id=>window.amplifier.getState().sessions.find(row=>row.id===id).draftAttachments[0].name,initial.sessions[1]),'other-reference.txt');
  await control({running:false,ack:true});
  await page.evaluate(()=>window.heldPostReceipt);
  const accepted=await metrics();assert.equal(accepted.posts.length,1);assert.equal(accepted.posts[0].navigationPost.disposition,'accepted');
  assert.equal(accepted.navigationActivityAt,stable);assert.equal(accepted.assistantCount,0);
  await control({ready:true});
  await expect.poll(async()=>(await metrics()).navigationActivityAt).toBeGreaterThan(stable);
  const ready=(await metrics()).navigationActivityAt;
  await control({attention:true});
  await expect.poll(async()=>(await metrics()).navigationActivityAt).toBeGreaterThan(ready);
  await page.reload();await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentShortcuts);
  await showChat(page);
  await expect.poll(order).toEqual(promotedOrder);
  await expectDraft(page,'Explicit human post');
  assert.equal((await metrics()).posts[0].navigationPost.fence,admitted.posts[0].navigationPost.fence);
  assert.deepEqual((await metrics()).runtimeCalls,[]);
  assert.deepEqual(errors,[]);
  if(process.env.AMPLIFIER_SIDEBAR_EVIDENCE)await page.screenshot({path:process.env.AMPLIFIER_SIDEBAR_EVIDENCE+'.human-post.png'});
  return {humanPostBeforeAckAndAnswer:true,heldAckRoots:24,oncePerInput:true,postedProgressStable:true,otherClientDraftAndReferencePreserved:true,modelCalls:0};
 }catch(error){bodyError=error;throw error}
 finally{
  const cleanupErrors=[];
  try{await context?.close()}catch(error){cleanupErrors.push(error)}
  try{await settleHumanFixture(held)}catch(error){cleanupErrors.push(error)}
  if(cleanupErrors.length)throw new AggregateError(
   [...(bodyError?[bodyError]:[]),...cleanupErrors],'Human fixture/context cleanup failed');
 }
}
try{
 browser=await launchBrowser();
 if(process.env.AMPLIFIER_HUMAN_POST_ONLY){
  const result=await humanPostRecency();
  if(process.env.AMPLIFIER_SIDEBAR_EVIDENCE)await writeFile(process.env.AMPLIFIER_SIDEBAR_EVIDENCE,JSON.stringify(result,null,2)+'\n');
  console.log(JSON.stringify(result,null,2));
 }else{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout')),45000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});let output='';fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const d=JSON.parse(line);if(d.url){clearTimeout(timer);resolve(d.url)}}catch{}})});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-active-client-token'},viewport:{width:1280,height:1000}});
 const errors=[],traffic=[],reports=[];let measuring=false,bootstrapReads=0,resourceReads=0,stateBaselines=0;
 await page.addInitScript(()=>{const Native=window.WebSocket;window.sidebarProofSockets=[];window.WebSocket=class extends Native{constructor(...args){super(...args);if(String(args[0]).includes('/api/events/'))window.sidebarProofSockets.push(this)}}});
 page.on('websocket',socket=>socket.on('framereceived',frame=>{try{if(JSON.parse(String(frame.payload)).event==='state')stateBaselines++}catch{}}));
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{const path=new URL(r.url()).pathname;if(path==='/api/view')reports.push(r.postDataJSON());if(path==='/api/shell')bootstrapReads++;if(path==='/api/shell/resources')resourceReads++;if(measuring&&['/api/shell','/api/shell/resources','/api/view'].includes(path))traffic.push(r.method()+' '+path)});
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats);
 const api=async(path,data)=>{const r=await page.request.post(url+path,{data});assert.equal(r.status(),200);return r.json()};
 const {sessions}=await (await page.request.get(url+'/fixture/metrics')).json();
 const dispatch=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const order=()=>page.locator('.a-nav-chat[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId));
 const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 // The shared scale fixture now seeds pins for transport qualification. This
 // journey explicitly creates and reorders its own two pins below.
 for(const id of (await shell()).pinnedSessionIds)await dispatch('session.pin',{id,pinned:false});
 await expect.poll(async()=>(await shell()).pinnedSessionIds).toEqual([]);
 await page.waitForFunction(()=>window.amplifier.getShellState().snapshots.chats.recentNavigation);
 await expect(page.locator('.a-nav-chat[data-session-id]')).toHaveCount(20);
 const before=await order();
 // Progress in a different selected conversation must not reshuffle the sidebar.
 await dispatch('session.select',{id:sessions[1]});await api('/fixture/progress',{running:true});await wait(1500);
 // Cold navigation may still be enabling its paging control. Observe that
 // legitimate initial report before measuring steady, unchanged progress.
 await expect.poll(()=>{const last=reports.at(-1);return last?.sessionId===sessions[1]&&last.controls?.some(control=>control.label==='Load older chats'&&!control.disabled)}).toBe(true);
 measuring=true;await wait(4000);measuring=false;
 assert.deepEqual(await order(),before);assert.deepEqual(traffic,[],'steady streaming must not refetch shell or echo conversation text');
 await api('/fixture/progress',{running:false});await page.waitForFunction(id=>window.amplifier.getShellState().snapshots.chats.recentNavigation?.items[0]?.id===id,sessions[0]);
 assert.equal((await order())[0],sessions[0]);
 // Explicit user preferences run through the same shared actions as agents.
 // The credential-free scale fixture can show initial connection onboarding.
 if(await page.locator('.a-settings-experience').isVisible())await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(page.locator('[data-part="overlay"]')).toHaveCount(0);
 await page.getByRole('button',{name:'Search chats',exact:true}).click();
 const search=page.getByRole('region',{name:'Chat search',exact:true});await search.locator('summary').click();
 await search.getByRole('combobox',{name:'Sort conversations'}).selectOption('name');
 await page.waitForFunction(()=>window.amplifier.getShellState().snapshots.chats.sidebarNavigation?.search?.scope.sort==='name');
 const names=(await shell()).sidebarNavigation.search.items.map(r=>r.title.toLowerCase());assert.deepEqual(names,[...names].sort());
 await dispatch('session.select',{id:sessions[1]});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await dispatch('session.pin',{id:sessions[1],pinned:true});await dispatch('session.pin',{id:sessions[0],pinned:true});
 await page.waitForFunction(ids=>JSON.stringify(window.amplifier.getShellState().snapshots.chats.pinnedSessionIds)===JSON.stringify(ids),[sessions[1],sessions[0]]);
 await expect(page.locator('.a-reorder-grip')).toHaveCount(2);
 const grip=page.locator(`[data-sidebar-section=pinned] [data-session-id="${sessions[0]}"] .a-reorder-grip`);await grip.scrollIntoViewIfNeeded();const from=await grip.boundingBox(),to=await page.locator(`[data-sidebar-section=pinned] [data-session-id="${sessions[1]}"]`).boundingBox();await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();await page.mouse.move(from.x+from.width/2,to.y+to.height/2,{steps:10});await page.mouse.up();
 await page.waitForFunction(id=>window.amplifier.getShellState().snapshots.chats.pinnedSessionIds[0]===id,sessions[0]);
 let heldReorder=false,releaseReorder;
 const holdReorder=async route=>{const value=route.request().postDataJSON(),action=value?.action==='shell.command'?value.args?.action:value?.action;if(action!=='navigation.pinOrder')return route.continue();const response=await route.fetch();await new Promise(resolve=>{const timer=setTimeout(resolve,10000);releaseReorder=()=>{clearTimeout(timer);resolve()};heldReorder=true});await route.fulfill({response})};
 await page.route('**/api/actions',holdReorder);
 await expect(grip).not.toHaveAttribute('aria-disabled','true');await grip.focus();await page.keyboard.press('Alt+ArrowDown');
 await expect.poll(()=>heldReorder).toBe(true);
 await page.waitForFunction(id=>window.amplifier.getShellState().snapshots.chats.pinnedSessionIds[0]===id,sessions[1]);
 // Focus and custom content remain observable while private draft bytes stay
 // redacted. Full explicit readback still includes saved conversation messages.
 // display:contents must not lose slot text.
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this private draft');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeFocused();
 releaseReorder();await expect(grip).not.toHaveAttribute('aria-disabled','true');
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeFocused();
 await page.unroute('**/api/actions',holdReorder);
 await page.evaluate(()=>{const div=document.createElement('div');div.dataset.shellComponent='fixture.custom';div.style.display='contents';div.textContent='Custom component observation';document.querySelector('#amp-one').append(div);document.dispatchEvent(new Event('selectionchange'))});
 await page.waitForTimeout(700);
 const latest=reports.at(-1);assert.equal(latest.visibleTextScope,'interface');assert.ok(latest.visibleText.includes('Custom component observation'));assert.ok(!latest.visibleText.includes('Synthetic saved reply'));
 if(!latest.controls.some(c=>c.label==='Message Amplifier'&&c.value==='[redacted]'&&c.focused))console.error('Composer observation diagnostic',JSON.stringify({reported:latest.controls.filter(c=>c.label==='Message Amplifier'||c.focused),current:await page.evaluate(()=>({active:{tag:document.activeElement?.tagName,label:document.activeElement?.getAttribute('aria-label'),className:document.activeElement?.className},controls:window.amplifier.getState().renderedView.controls.filter(c=>c.label==='Message Amplifier'||c.focused)}))}));
 assert.ok(latest.controls.some(c=>c.label==='Message Amplifier'&&c.value==='[redacted]'&&c.focused));
 assert.ok(!JSON.stringify(latest).includes('Keep this private draft'));
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep this private draft');
 assert.ok(await page.evaluate(()=>!JSON.stringify(window.amplifier.getState().renderedView).includes('Keep this private draft')));
 assert.ok(await page.evaluate(()=>window.amplifier.getState().renderedView.visibleText.includes('Synthetic saved reply')));
 // A real reconnect receives a fresh state baseline on the existing scoped
 // registration; it does not bootstrap the catalog again or replay edits.
 const priorConnection=await page.evaluate(()=>window.sidebarProofSockets.length),priorBaselines=stateBaselines,reads={bootstrapReads,resourceReads};
 assert.ok(priorConnection>0,'The normal WebSocket lane must be active');
 await page.evaluate(()=>window.sidebarProofSockets.at(-1).close());
 await expect.poll(()=>page.evaluate(()=>window.sidebarProofSockets.length)).toBeGreaterThan(priorConnection);
 await expect.poll(()=>stateBaselines).toBeGreaterThan(priorBaselines);
 await expect.poll(()=>page.evaluate(()=>JSON.parse(document.documentElement.dataset.amplifierShellDiagnostics||'{}').counts?.reconnect||0)).toBeGreaterThanOrEqual(2);
 assert.deepEqual({bootstrapReads,resourceReads},reads);
 assert.deepEqual((await shell()).pinnedSessionIds,[sessions[1],sessions[0]]);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this private draft');
 // Hidden clients defer shell reads/reports, then catch up on visibility restore.
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))});
 await wait(500);traffic.length=0;measuring=true;
 await api('/fixture/progress',{running:true});await wait(1000);await api('/fixture/progress',{running:false});await wait(500);measuring=false;
 assert.deepEqual(traffic,[]);
 await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});await wait(700);
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.view?.navRecentView?.navSort==='name');
 assert.deepEqual((await shell()).pinnedSessionIds,[sessions[1],sessions[0]]);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this private draft');
 assert.deepEqual(errors,[]);
 if(process.env.AMPLIFIER_SIDEBAR_EVIDENCE)await page.screenshot({path:process.env.AMPLIFIER_SIDEBAR_EVIDENCE+'.png'});
 // These are independent fixture hosts. Finish the scale browser before the
 // two-client post journey, avoiding cross-context lifetime coupling on ARM.
 await browser.close();browser=await launchBrowser();
 const humanPost=await humanPostRecency();
 const result={steadyStreamRequests:0,activityOrderStable:true,sortChoices:true,dragAndKeyboardPins:true,reloadPersistence:true,privateDraftRetained:true,customContentObserved:true,hiddenReportingDeferred:true,reconnectBaselineWithoutCatalogRead:true,lateReorderKeepsNewFocus:true,...humanPost,modelCalls:0};
 if(process.env.AMPLIFIER_SIDEBAR_EVIDENCE)await writeFile(process.env.AMPLIFIER_SIDEBAR_EVIDENCE,JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result,null,2));
 }
}finally{try{await browser?.close()}finally{fixture?.kill('SIGTERM')}}
