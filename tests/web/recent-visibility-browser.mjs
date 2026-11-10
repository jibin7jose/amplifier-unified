// Real installed-package HTTP/SSE regression. One fixture and browser context.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/recent_visibility_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,context,page;
const receipts=[];
const evidence=process.env.AMPLIFIER_RECENT_EVIDENCE;
try{
 const boot=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Recent fixture first-state timeout')),45000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Recent fixture exited '+code))});
  let output='';
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});
 });
 const url=boot.url;
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-recent-visibility-token'},viewport:{width:1280,height:1000},hasTouch:true});
 page=await context.newPage();
 const other=await context.newPage(),errors=[],previewReads=[];
 for(const target of [page,other]){
  target.on('pageerror',error=>errors.push(error.message));
  target.on('request',request=>{
   if(new URL(request.url()).pathname==='/api/actions'&&request.postDataJSON()?.action==='session.titlePreview')
    previewReads.push(request.postDataJSON());
  });
  target.on('response',async response=>{
   if(new URL(response.url()).pathname==='/api/actions'){
    try{receipts.push({request:response.request().postDataJSON(),status:response.status(),receipt:await response.json()})}catch{}
   }
  });
 }
 const dispatch=(target,action,args)=>target.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const shell=target=>target.evaluate(()=>window.amplifier.getShellState());
 const sidebar=(target=page,instance='chats')=>target.locator(`[data-shell-instance="${instance}"] .a-quiet-sidebar [data-sidebar-section="recent"]`);
 const rows=(target=page,instance='chats')=>sidebar(target,instance).locator('.a-nav-chat[data-session-id]');
 const order=(target=page,instance='chats')=>rows(target,instance).evaluateAll(nodes=>nodes.map(node=>node.dataset.sessionId));
 const metrics=async()=>{const response=await page.request.get(url+'/fixture/metrics');assert.equal(response.status(),200);return response.json()};
 const expectDraft=async(target,text)=>{
  const composer=target.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await expect.poll(()=>target.evaluate(()=>window.amplifier.getState().view.draft)).toBe(text);
  await expect.poll(()=>composer.textContent()).toBe(text);
  const identity=await target.evaluate(()=>window.amplifier.getState().client.id);
  const sid=await target.evaluate(()=>window.amplifier.getState().selectedSessionId);
  await expect.poll(async()=>(await metrics()).durableClients[identity]?.drafts?.[sid]).toBe(text);
 };
 const control=async data=>{const response=await page.request.post(url+'/fixture/control',{data});assert.equal(response.status(),200)};
 const projection=async(target=page,instance='chats')=>(await shell(target)).snapshots[instance].recentNavigation;
 const showAgentCreated=async(value,instance='chats')=>dispatch(page,'shell.view.update',{clientId:(await shell(page)).clientId,instanceId:instance,patch:{navShowAgentCreated:value}});
 const waitForRows=async(count,target=page,instance='chats')=>expect(rows(target,instance)).toHaveCount(count);
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentNavigation);
 await waitForRows(20);
 const initial=await metrics(),firstOrder=await order();
 const titleButton=rows().first().locator('.a-nav-chat-select'),titleViewport=titleButton.locator('.a-nav-chat-title');
 await expect(titleButton).toHaveAttribute('data-tooltip','off');
 await titleViewport.evaluate(node=>node.style.maxWidth='60px');
 await titleButton.hover();
 await expect(titleViewport).toHaveAttribute('data-overflow','true');
 await expect(titleViewport.locator('span')).toHaveCSS('animation-name','a-chat-title-pan');
 await expect(page.getByRole('tooltip')).toHaveCount(0);
 await page.emulateMedia({reducedMotion:'reduce'});
 await expect(titleViewport.locator('span')).toHaveCSS('animation-name','none');
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.mouse.move(0,0);await titleButton.focus();
 await expect(titleViewport.locator('span')).toHaveCSS('animation-name','a-chat-title-pan');
 await titleViewport.evaluate(node=>node.style.removeProperty('max-width'));
 await page.keyboard.press('Escape');await titleButton.evaluate(node=>node.blur());

 if(evidence)await writeFile(evidence+'.initial-metrics.json',JSON.stringify(initial,null,2)+'\n');
 assert.equal(initial.sessionCount,136);assert.equal(initial.sessionsUnchanged,true);
 assert.ok(!firstOrder.includes(initial.commissioned));assert.ok(!firstOrder.includes(initial.pinned));
 assert.ok(firstOrder.includes(initial.fork)&&firstOrder.includes(initial.legacy));
 assert.ok(initial.excluded.every(id=>!firstOrder.includes(id)));
 await expect(page.locator(`[data-shell-instance="chats"] [data-sidebar-section="pinned"] [data-session-id="${initial.pinned}"]`)).toHaveCount(1);
 const total=(await projection()).total;assert.equal(total,132);
 assert.equal(initial.mutations.filter(row=>row.action==='shell.view.update'&&row.args.patch?.navRecentLimit).length,0);
 await expect(sidebar().getByRole('button',{name:'All chats',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Review chats',exact:true})).toHaveCount(0);
 // Tall viewport cannot drain a library on mount/resize or section-open.
 await page.setViewportSize({width:1280,height:4000});
 await waitForRows(20);
 const fallback=sidebar().getByRole('button',{name:'Load older chats',exact:true});
 await expect(fallback).toBeVisible();
 assert.ok((await fallback.boundingBox()).height>=44);
 assert.equal((await projection()).limit,20);
 await page.setViewportSize({width:1280,height:1000});
 await showAgentCreated(!(await projection()).scope.showAgentCreated);
 await expect(sidebar().getByRole('button',{name:'Show agent-created',exact:true})).toHaveCount(0);
 await expect.poll(async()=>(await projection())?.total).toBe(134);
 await waitForRows(20);
 assert.ok((await order()).includes(initial.pinned));
 await expect(page.locator(`[data-shell-instance="chats"] [data-sidebar-section="pinned"] [data-session-id="${initial.pinned}"]`)).toHaveCount(1);
 await showAgentCreated(!(await projection()).scope.showAgentCreated);
 await expect.poll(async()=>(await projection())?.total).toBe(total);
 assert.deepEqual(await order(),firstOrder);
 // Passive browse setup: real current draft, attachment, Canvas and model/bundle.
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Keep original draft');
 await expectDraft(page,'Keep original draft');
 await dispatch(page,'attachment.add',{sessionId:initial.selected,name:'retained.txt',base64:'a2VlcA=='});
 await dispatch(page,'canvas.show',{kind:'text',title:'Kept fixture Canvas',content:'Keep Canvas'});
 const canvasId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 const canonicalBefore=(await dispatch(page,'canvas.versions.inspect',{id:canvasId,version:1,includeSource:true})).result;
 await dispatch(page,'canvas.visibility',{open:false,sessionId:initial.selected,canvasId});
 const preservation=target=>target.evaluate(()=>{
  const state=window.amplifier.getState(),current=state.sessions.find(row=>row.id===state.selectedSessionId);
  return {selected:state.selectedSessionId,workspace:state.selectedWorkspaceId,draft:state.view.draft,
   attachments:current?.draftAttachments,canvas:state.canvas,bundle:current?.bundle,selection:current?.selection,
   messages:current?.messages,newSessionDraft:state.view.newSessionDraft};
 });
 const before=await preservation(page);
 assert.equal(before.draft,'Keep original draft');
 await other.goto(url);await other.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentNavigation);
 await waitForRows(20,other);
 await other.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Keep second client');
 await expectDraft(other,'Keep second client');
 const otherBefore=await preservation(other);
 assert.equal(otherBefore.draft,'Keep second client');
 for(const count of [40,60,80,100]){
  const load=sidebar().getByRole('button',{name:'Load older chats',exact:true});
  await load.scrollIntoViewIfNeeded();await load.focus();await page.keyboard.press('Enter');
  await waitForRows(count);
  const more=sidebar().getByRole('button',{name:'Load older chats',exact:true});
  await expect(more).toBeFocused();
  assert.equal((await projection()).remaining,total-count);
 }
 // Repositioning at the bottom is not input. Only a real downward wheel loads.
 const writes=async()=>(await metrics()).mutations.filter(row=>row.action==='shell.view.update'&&row.args.patch?.navRecentLimit).length;
 const rail=page.locator('.a-nav-content');
 const beforeScroll=await writes();
 await rail.evaluate(node=>{
  node.scrollTop=0;node.dispatchEvent(new Event('scroll'));
  node.dispatchEvent(new WheelEvent('wheel',{deltaY:-100,bubbles:true}));
  document.querySelector('.ProseMirror').focus({preventScroll:true});
  node.scrollTop=node.scrollHeight-node.clientHeight-10;node.dispatchEvent(new Event('scroll'));
 });
 assert.equal(await writes(),beforeScroll,'upward-at-top cannot authorize later focus restoration');
 await rail.evaluate(node=>{
  node.scrollTop=node.scrollHeight-node.clientHeight-20;node.dispatchEvent(new Event('scroll'));
  node.dispatchEvent(new WheelEvent('wheel',{deltaY:100,bubbles:true}));
  document.querySelector('.ProseMirror').focus({preventScroll:true});
  document.dispatchEvent(new FocusEvent('focusin'));
  node.scrollTop+=10;node.dispatchEvent(new Event('scroll'));
 });
 assert.equal(await writes(),beforeScroll,'unused wheel at bottom expires across unrelated focus');
 await rail.evaluate(node=>{node.scrollTop=node.scrollHeight-node.clientHeight-10});
 await expect.poll(()=>writes()).toBe(beforeScroll);
 await rail.hover();await page.mouse.wheel(0,100);
 await waitForRows(120);
 assert.equal(await writes(),beforeScroll+1);
 // Appended content and anchor restoration must not recursively request 140.
 await expect.poll(async()=>(await projection())?.limit).toBe(120);
 assert.equal(await writes(),beforeScroll+1);
 assert.equal((await order()).length,new Set(await order()).size);
 // Hidden/inert/collapsed Recent remains passive even with scroll intent.
 await sidebar().getByRole('button',{name:'Recent',exact:true}).click();
 await rail.evaluate(node=>{
  node.dispatchEvent(new WheelEvent('wheel',{deltaY:200,bubbles:true}));
  node.scrollTop=node.scrollHeight;node.dispatchEvent(new Event('scroll'));
 });
 assert.equal(await writes(),beforeScroll+1);
 await sidebar().getByRole('button',{name:'Recent',exact:true}).click();
 const recentRoot=sidebar().locator('.a-recent-shortcuts');
 for(const attribute of ['hidden','inert']){
  await recentRoot.evaluate((node,attribute)=>node.setAttribute(attribute,''),attribute);
  await rail.evaluate(node=>{
   node.scrollTop-=10;node.dispatchEvent(new Event('scroll'));
   node.dispatchEvent(new WheelEvent('wheel',{deltaY:200,bubbles:true}));
   node.scrollTop+=10;node.dispatchEvent(new Event('scroll'));
  });
  assert.equal(await writes(),beforeScroll+1);
  await recentRoot.evaluate((node,attribute)=>node.removeAttribute(attribute),attribute);
 }
 await sidebar().getByRole('button',{name:'Load older chats',exact:true}).click();
 await waitForRows(total);
 assert.equal((await projection()).limit,140);
 await expect(sidebar().getByRole('button',{name:'Load older chats',exact:true})).toHaveCount(0);
 await expect(sidebar().getByRole('button',{name:'View all chats',exact:true})).toHaveCount(0);
 await expect(sidebar().getByRole('button',{name:'All chats',exact:true})).toHaveCount(0);
 assert.equal(await sidebar().locator('.a-recent-controls').count(),0);
 const exhaustedWrites=await writes();
 await rail.evaluate(node=>{
  node.scrollTop-=10;node.dispatchEvent(new Event('scroll'));
  node.dispatchEvent(new WheelEvent('wheel',{deltaY:200,bubbles:true}));
  node.scrollTop+=10;node.dispatchEvent(new Event('scroll'));
 });
 assert.equal(await writes(),exhaustedWrites,'remaining zero prevents any further query/write');
 assert.deepEqual(previewReads,[],'named summaries never hydrate previews/transcripts, including offscreen rows');
 assert.deepEqual(await preservation(page),before);
 // Reload from this expanded session is bounded at the durable setting.
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.chats?.recentNavigation);
 await waitForRows(100);
 await showAgentCreated(true);
 await expect.poll(async()=>(await projection())?.scope.showAgentCreated).toBe(true);
 await waitForRows(100);
 assert.ok((await order()).includes(initial.commissioned));
 await showAgentCreated(!(await projection()).scope.showAgentCreated);
 await expect.poll(async()=>(await projection())?.scope.showAgentCreated).toBe(false);
 await waitForRows(100);assert.ok(!(await order()).includes(initial.commissioned));
 assert.deepEqual(await preservation(page),before);
 assert.deepEqual(await preservation(other),otherBefore);
 await waitForRows(20,other);
 // Add another real mounted module through the existing guarded composition.
 const inspected=await shell(page),clientId=inspected.clientId;
 const composition=structuredClone(inspected.composition);
 composition.instances.push({id:'recent-second',package:'builtin.chats',slot:'navigation'});
 const prepared=await dispatch(page,'shell.changes.prepare',{clientId,expectedRevision:inspected.revision,composition});
 await dispatch(page,'shell.changes.apply',{clientId,expectedRevision:inspected.revision,changeId:prepared.result.id});
 await waitForRows(20,page,'recent-second');await waitForRows(100);
 await showAgentCreated(true,'recent-second');
 await expect.poll(async()=>(await projection(page,'recent-second'))?.scope.showAgentCreated).toBe(true);
 assert.equal((await projection()).scope.showAgentCreated,false);
 await page.reload();await page.waitForFunction(()=>window.amplifier?.getShellState()?.snapshots?.['recent-second']?.recentNavigation);
 await waitForRows(100);await waitForRows(20,page,'recent-second');
 assert.equal((await projection(page,'recent-second')).scope.showAgentCreated,true);
 assert.deepEqual(await preservation(page),before);
 // Pin/current exceptions are eligibility only: current commissioned rank 25
 // stays open at limit 20 without stealing a row; explicit fallback reveals it.
 const preColdView=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary'));
 await dispatch(page,'session.select',{id:initial.commissioned});
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,initial.commissioned);
 await dispatch(page,'shell.view.update',{clientId:(await shell(page)).clientId,instanceId:'chats',patch:{navRecentLimit:20}});
 await waitForRows(20);assert.ok(!(await order()).includes(initial.commissioned));
 await sidebar().getByRole('button',{name:'Load older chats',exact:true}).click();
 await waitForRows(40);assert.ok((await order()).includes(initial.commissioned));
 await dispatch(page,'session.select',{id:initial.selected});
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,initial.selected);
 await expectDraft(page,'Keep original draft');
 // A cold scope restore must discard only the old mount's render report.
 const coldExpected=structuredClone(before);coldExpected.canvas.renderReports={};
 assert.deepEqual(await preservation(page),coldExpected);
 assert.deepEqual((await dispatch(page,'canvas.versions.inspect',{id:canvasId,version:1,includeSource:true})).result,canonicalBefore);
 const currentView=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary'));
 await dispatch(page,'canvas.visibility',{open:true,sessionId:initial.selected,canvasId});
 await page.waitForFunction(id=>window.amplifier.getState().canvas.id===id&&window.amplifier.getState().canvas.renderReports?.preview?.status==='ready',canvasId);
 assert.deepEqual((await dispatch(page,'canvas.versions.inspect',{id:canvasId,version:1,includeSource:true})).result,canonicalBefore);
 const freshView=await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views.find(row=>row.viewId==='primary'));
 assert.ok(currentView.generation>preColdView.generation);
 assert.equal(freshView.generation,currentView.generation);
 await assert.rejects(async()=>dispatch(page,'canvas.views.command',{clientId:(await shell(page)).clientId,
  viewId:'primary',resourceId:preColdView.resourceId,resourceRevision:preColdView.resourceRevision,
  generation:preColdView.generation,action:'canvas.report',args:{id:canvasId,part:'stale-fixture',status:'ready',message:'Must not be accepted'}}));
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.renderReports?.['stale-fixture']),undefined);
 await dispatch(page,'canvas.visibility',{open:false,sessionId:initial.selected,canvasId});
 // A committed view without its requested streamed page keeps the old rows.
 // Retry is a read-only resource POST; a failed read must not become success.
 const kept=await order();let failReads=true,retryReads=0;
 await control({holdRecentFor:(await shell(page)).clientId});
 await page.route('**/api/shell/resources',async route=>{
  retryReads++;
  if(failReads)return route.abort('failed');
  await control({holdRecentFor:null});
  await route.continue();
 });
 await sidebar().getByRole('button',{name:'Load older chats',exact:true}).click();
 await expect(sidebar().getByRole('alert')).toContainText('previous chats are kept',{timeout:15000});
 assert.deepEqual(await order(),kept);assert.ok((await metrics()).withheldRecentProjections>0);
 const countBeforeRetry=(await metrics()).mutations.filter(row=>row.action==='shell.view.update').length;
 await sidebar().getByRole('button',{name:'Retry',exact:true}).click();
 await expect.poll(()=>retryReads).toBe(1);
 await expect(sidebar().getByRole('alert')).toContainText('Navigation resources could not be refreshed.');
 assert.deepEqual(await order(),kept);
 failReads=false;
 await sidebar().getByRole('button',{name:'Retry',exact:true}).click();
 await waitForRows(60);assert.equal(retryReads,2);
 assert.equal((await metrics()).mutations.filter(row=>row.action==='shell.view.update').length,countBeforeRetry);
 await page.unroute('**/api/shell/resources');
 // An unknown write is read back, never automatically replayed.
 let lost=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(lost&&body?.action==='shell.view.update'&&body.args?.instanceId==='chats'&&body.args.patch?.navRecentLimit===80){
   lost=false;await route.fetch();await route.abort('failed');
  }else await route.continue();
 });
 await sidebar().getByRole('button',{name:'Load older chats',exact:true}).click();
 // The independent SSE query may already have reconciled to 80. Either way,
 // only an explicit read is permitted; check server mutation identities.
 await expect.poll(async()=>(await projection())?.limit).toBe(80);
 if(await sidebar().getByRole('button',{name:'Retry',exact:true}).count())await sidebar().getByRole('button',{name:'Retry',exact:true}).click();
 await waitForRows(80);await page.unroute('**/api/actions');
 const writes80=(await metrics()).mutations.filter(row=>row.action==='shell.view.update'&&row.args?.instanceId==='chats'&&row.args.patch?.navRecentLimit===80);
 assert.equal(writes80.length,2); // One initial 60->80, one later reconciled 60->80.
 // Steady progress retains the loaded order, scroll and active control.
 const steady=await order(),toggle=sidebar().getByRole('button',{name:'Load older chats',exact:true});
 await toggle.scrollIntoViewIfNeeded();await toggle.focus();
 const geometry=()=>page.evaluate(()=>{const rail=document.querySelector('.a-nav-content');const box=n=>{const r=n.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}};return {scrollTop:rail.scrollTop,scrollHeight:rail.scrollHeight,clientHeight:rail.clientHeight,rail:box(rail),focus:document.activeElement?.textContent,sections:[...rail.querySelectorAll('[data-sidebar-section]')].map(n=>({section:n.dataset.sidebarSection,...box(n)})),statuses:[...rail.querySelectorAll('[role=status],[role=alert]')].map(n=>({text:n.textContent,...box(n)})),rows:[...rail.querySelectorAll('[data-sidebar-section=recent] [data-session-id]')].map(n=>({id:n.dataset.sessionId,...box(n)}))}});
 const beforeGeometry=await geometry();
 const scrollBefore=await page.locator('.a-nav-content').evaluate(node=>node.scrollTop);
 await control({progress:true});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.status)).toBe('working');
 assert.deepEqual(await order(),steady);await expect(toggle).toBeFocused();
 if(evidence)await writeFile(evidence+'.progress-geometry.json',JSON.stringify({before:beforeGeometry,after:await geometry()},null,2)+'\n');
 assert.equal(await page.locator('.a-nav-content').evaluate(node=>node.scrollTop),scrollBefore);
 await control({ready:true});await control({attention:true});
 await expect.poll(async()=>(await order())[0]).toBe(initial.selected);
 assert.deepEqual((await metrics()).runtimeCalls,[]);
 // Discriminating source-window case: an unnamed SUMMARY is already mounted,
 // but below the visible rail. Route only that projection/title read; never
 // add messages or hydrate a transcript to arrange the scenario.
 const unnamedId=(await projection()).items[70].id;
 await rail.evaluate(node=>{node.scrollTop=0;node.dispatchEvent(new Event('scroll'))});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='session.titlePreview'&&body.args.sessionId===unnamedId)
   return route.fulfill({json:{accepted:true,result:{title:'Lazy first user message',source:'first-message'}}});
  await route.continue();
 });
 await control({unnamedSummary:unnamedId,clientId:(await shell(page)).clientId});
 const unnamedRow=sidebar().locator(`[data-session-id="${unnamedId}"]`);
 await expect(unnamedRow).toContainText('Untitled chat');
 assert.equal(await unnamedRow.evaluate(node=>{
  const box=node.getBoundingClientRect(),rail=node.closest('.a-nav-content').getBoundingClientRect();
  return box.top>=rail.bottom;
 }),true,'test requires a mounted, genuinely offscreen unnamed row');
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.deepEqual(previewReads,[],'an unnamed offscreen source-window summary must not eagerly read');
 await unnamedRow.scrollIntoViewIfNeeded();
 await expect(unnamedRow).toContainText('Lazy first user message');
 assert.equal(previewReads.filter(body=>body.args.sessionId===unnamedId).length,1);
 await rail.evaluate(node=>{node.scrollTop=0;node.dispatchEvent(new Event('scroll'))});
 await unnamedRow.scrollIntoViewIfNeeded();
 assert.equal(previewReads.filter(body=>body.args.sessionId===unnamedId).length,1,'revisiting a visible row reuses the bounded preview');
 await page.unroute('**/api/actions');
 // Narrow/coarse touch: unchanged finite limit, 44px controls, same one context.
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>matchMedia('(pointer:coarse)').matches),true);
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await waitForRows(80);
 const target=sidebar().getByRole('button',{name:'Load older chats',exact:true});
 await target.scrollIntoViewIfNeeded();const box=await target.boundingBox();assert.ok(box.height>=44);
 await target.tap();await waitForRows(100);
 if(evidence)await page.screenshot({path:evidence+'.narrow.png'});
 await page.setViewportSize({width:1280,height:1000});
 await sidebar().getByRole('button',{name:'Load older chats',exact:true}).focus();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.focus();
 await control({shrink:true});await expect(rows()).toHaveCount(5);
 await expect(composer).toBeFocused();
 await expect(sidebar().getByRole('button',{name:'Load older chats',exact:true})).toHaveCount(0);
 await expect(sidebar().getByRole('button',{name:'All chats',exact:true})).toHaveCount(0);
 assert.equal((await projection()).limit,100);
 const finalMetrics=await metrics();
 assert.deepEqual(finalMetrics.runtimeCalls,[]);
 assert.equal(finalMetrics.mutations.filter(row=>row.action==='conversation.send').length,0);
 assert.deepEqual(errors,[]);
 const result={applicationModule:boot.applicationModule,python:boot.python,expectedPackageChecked:boot.expectedPackageChecked,
  limits:[20,40,60,80,100,120,140],naturalExhaustion:true,noMountResizeFill:true,userDownwardOnly:true,
  programmaticAnchorPassive:true,staleFocusIntentPassive:true,hiddenInertCollapsedPassive:true,namedPreviewReads:0,
  offscreenUnnamedSummaryLazy:true,visibleUnnamedReadCached:true,visibilityAt20And100:true,currentRank25NotForced:true,
  pinsRetainedInEligibleRecent:true,legacyForkVisible:true,queryFailureRetainedRows:true,streamProjectionTimeout:true,retryFailureRetainedRows:true,retryResourceReads:retryReads,retryReadOnly:true,unknownWriteNotReplayed:true,
  twoClientsTwoModulesReload:true,keyboardFocus:true,narrowCoarseTouch:true,progressAnchor:true,countShrink:true,
  draftAttachmentsCanvasModelBundleRetained:true,modelCalls:0};
 if(evidence){
  await page.screenshot({path:evidence+'.png',fullPage:true});
  await writeFile(evidence+'.receipts.json',JSON.stringify(receipts,null,2)+'\n');
  await writeFile(evidence+'.dom.html',await page.content());
  await writeFile(evidence,JSON.stringify(result,null,2)+'\n');
  await writeFile(evidence+'.junit.xml','<testsuite name="recent-visibility" tests="1" failures="0"><testcase name="http-sse-recent"/></testsuite>\n');
 }
 console.log(JSON.stringify(result,null,2));
}catch(error){
 if(evidence){
  await writeFile(evidence+'.receipts.json',JSON.stringify(receipts,null,2)+'\n');
  if(page){
   await writeFile(evidence+'.dom.html',await page.content()).catch(()=>{});
   await page.screenshot({path:evidence+'.failure.png',fullPage:true}).catch(()=>{});
  }
  await writeFile(evidence+'.junit.xml','<testsuite name="recent-visibility" tests="1" failures="1"><testcase name="http-sse-recent"><failure>Browser regression failed; see process stderr and retained evidence.</failure></testcase></testsuite>\n');
 }
 throw error;
}finally{
 await context?.close();await browser?.close();fixture.kill('SIGTERM');
}