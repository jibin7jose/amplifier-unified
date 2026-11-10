// Production host/assets, synthetic runtime, real browser input. Run only in the
// owner's isolated qualification environment after building the exact candidate.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {acceptedCanvasReceipt,settleCanvasReceipts,assertCanvasVisibility,acceptedCanvasVisibility} from './canvas-test-receipts.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const base=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks');
await mkdir(base,{recursive:true});
const output=await mkdtemp(join(base,'canvas-chrome-'));
const report={status:'running',checks:[],actions:[],http:[],browserErrors:[],screenshots:[],evidenceErrors:[],
 limits:['Synthetic runtime; no provider calls.','Screenshots and trace are saved, not a claim of pixel review.',
  'Reduced motion uses the OS media preference; no app-level Less motion setting exists.']};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','pipe']});
let browser,context,page,failure,stdout='',stderr='',phase='exercise';
const pendingReceipts=[];
const observedReceipts=new Map();
fixture.stderr.on('data',chunk=>stderr+=chunk);
const unwrap=body=>body?.action==='shell.command'?(body.args.command||body.args):body;
const isAction=request=>request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions';
const errorText=error=>error?.stack||String(error);
async function check(name,run){
 const row={name,status:'running'};report.checks.push(row);
 try{row.observation=await run();row.status='passed'}catch(error){row.status='failed';row.error=errorText(error);throw error}
}
async function capture(name){
 const file=name+'.png';await page.screenshot({path:join(output,file)});
 report.screenshots.push({file,visualReview:'not-performed'});
}
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{stdout+=chunk;for(const line of stdout.split('\n'))try{
   const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 context=await browser.newContext({viewport:{width:1500,height:850},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.tracing.start({screenshots:true,snapshots:true,sources:true});
 page=await context.newPage();page.setDefaultTimeout(8000);
 page.on('pageerror',error=>report.browserErrors.push(error.message));
 page.on('response',response=>{
  if(!isAction(response.request()))return;
  const row={phase,request:unwrap(response.request().postDataJSON()),status:response.status()};report.http.push(row);
  const settled=response.json().then(body=>{row.body=body},error=>{row.readError=errorText(error)});
  observedReceipts.set(response.request(),{row,settled});pendingReceipts.push(settled);
 });
 page.on('requestfailed',request=>{if(isAction(request))report.http.push({phase,request:unwrap(request.postDataJSON()),transportError:request.failure()})});
 const action=async(name,args={})=>{
  const row={name,args};report.actions.push(row);
  try{
   row.receipt=await page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
   assert.equal(row.receipt?.accepted,true,name+': explicit accepted receipt required');
   return row.receipt;
  }catch(error){row.error=errorText(error);throw error}
 };
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('session.create');
 const sessionId=(await state()).selectedSessionId;
 await action('view.update',{patch:{canvasWidth:720,canvasOverviewWidth:720,canvasControlsPinned:false,canvasControlsExpanded:false,navExpanded:false,draft:'Retain the Canvas test draft'}});
 const panel=page.locator('#workspace-canvas'),overview=page.getByRole('region',{name:'Chat overview',exact:true});
 const overviewTab=panel.getByRole('tab',{name:'Chat overview',exact:true}),strip=panel.locator('.a-canvas-document-tabs');
 const toggle=page.locator('#canvas-toggle'),dialog=page.getByRole('dialog',{name:'Open documents',exact:true});
 const section=id=>overview.locator(`details[data-overview-section="${id}"]`);
 const summary=id=>section(id).locator(':scope > summary');
 const documents=()=>panel.locator('button.a-open-documents');
 const serverSnapshot=async clientId=>{
  const response=await page.request.get(url+'/api/state?clientId='+encodeURIComponent(clientId),{timeout:8000});
  assert.equal(response.status(),200,'Read the same client directly from the host');
  return response.json();
 };
 const visibility=async(open,{holdBeforeAdmission=false}={})=>{
  const before=await state(),expected={clientId:before.client.id,hostInstanceId:before.client.hostInstanceId,sessionId,canvasId:before.canvas.id,open};
  assert.equal(before.canvas.open,!open);assert.notEqual(before.canvas.visibilityPending,true);
  const matches=request=>{
   if(!isAction(request))return false;
   const command=unwrap(request.postDataJSON());
   return command?.action==='canvas.visibility'&&command.args?.open===open
    &&command.args.sessionId===sessionId&&command.args.canvasId===expected.canvasId
    &&request.headers()['x-amplifier-client']===expected.clientId;
  };
  let heldRoute,release;
  const gate=new Promise(resolve=>{release=resolve});
  const hold=async route=>{if(matches(route.request())){heldRoute=route;await gate}await route.continue()};
  if(holdBeforeAdmission)await page.route('**/api/actions',hold);
  // Observe this click's request before it can finish; no synthetic dispatch or replay.
  const responseReady=page.waitForResponse(response=>matches(response.request()),{timeout:8000})
   .then(response=>({response}),error=>({error}));
  try{
   const [request]=await Promise.all([page.waitForRequest(matches,{timeout:8000}),toggle.click()]);
   expected.commandId=unwrap(request.postDataJSON()).id;
   if(holdBeforeAdmission){
    await expect.poll(()=>heldRoute?.request()===request,{timeout:8000}).toBe(true);
    await expect(overview).toBeVisible();
    const optimistic=await state(),unaccepted=await serverSnapshot(expected.clientId);
    assert.equal(optimistic.canvas.open,true);assert.equal(optimistic.canvas.visibilityPending,true);
    assert.throws(()=>assertCanvasVisibility(optimistic,expected),/still awaits acceptance/);
    assert.equal(unaccepted.canvas.open,false,'Held request has not changed server visibility');
    assert.throws(()=>assertCanvasVisibility(unaccepted,expected),/Server visibility/);
    assert.equal(observedReceipts.has(request),false,'No response has been supplied or fabricated');
    report.visibilityNegative={commandId:expected.commandId,optimisticOpen:true,serverOpen:false,receiptAbsent:true};
   }
   release();
   const result=await responseReady;if(result.error)throw result.error;
   assert.equal(result.response.request(),request,'The receipt is for this exact click');
   const accepted=await acceptedCanvasVisibility(observedReceipts.get(request),expected,()=>serverSnapshot(expected.clientId));
   await page.waitForFunction(()=>window.amplifier.getState().canvas.visibilityPending!==true,{},{timeout:8000});
   if(open)await expect(overview).toBeVisible();else await expect(panel).toBeHidden();
   (report.visibilityReceipts??=[]).push({commandId:expected.commandId,open,accepted:accepted.row.body,serverRevision:accepted.snapshot.revision,serverOpen:accepted.snapshot.canvas.open});
   return {expected,snapshot:accepted.snapshot};
  }finally{release();if(holdBeforeAdmission)await page.unroute('**/api/actions',hold)}
 };
 const artifacts=async()=>{const s=await state();return s.canvasArtifacts.filter(row=>row.sessionId===sessionId&&row.workspaceId===s.selectedWorkspaceId)};
 const counts=async(open)=>{
  await expect(documents()).toHaveAccessibleName(`Open documents, ${open} open`);
  // Native modal dialogs make the surrounding page inert. Count those retained
  // DOM tabs, not the dialog's accessible tree, while asserting their owner/name.
  await expect(strip.locator('[role="tab"]')).toHaveCount(open);
  const rows=await artifacts();assert.equal(rows.length,30);assert.equal(rows.filter(row=>row.tabOpen).length,open);
 };
 // Full containment and a hit test: a locator existing below clipped content is
 // not reachability. All positive scroll movement below comes from native input.
 const reachable=locator=>locator.evaluate(el=>{
  const r=el.getBoundingClientRect(),owner=el.closest('.a-canvas-overview-page'),s=owner?.getBoundingClientRect();
  return r.width>0&&r.height>0&&!el.closest('[inert]')&&r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1
   &&(!s||r.top>=s.top-1&&r.bottom<=s.bottom+1)&&el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));
 });
 const requireReachable=async(locator,label)=>expect.poll(()=>reachable(locator),{message:label+' must be unclipped and hit-testable',timeout:3000}).toBe(true);
 // Resolve screenshot-review claims at the actual overview header boundary.
 // Partially visible document tabs are intentional; fixed controls are not.
 const navigationGeometry=()=>panel.evaluate(el=>{
  const box=node=>{const r=node.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
  const header=el.querySelector('.a-canvas-head'),reader=el.querySelector('.a-canvas-overview-page');
  const controls=[el.querySelector('.a-chat-overview-trigger'),el.querySelector('.a-open-documents'),header.querySelector('[aria-label="Focus canvas"]')].map(node=>{
   const r=box(node),points=[[r.left+4,r.top+4],[r.right-4,r.top+4],[r.left+4,r.bottom-4],[r.right-4,r.bottom-4]];
   return {name:node.getAttribute('aria-label'),box:r,hit:points.every(([x,y])=>node.contains(document.elementFromPoint(x,y))),
    children:[...node.querySelectorAll('svg,span,small')].filter(child=>{const s=getComputedStyle(child),r=box(child);return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>.99&&r.width>0&&r.height>0}).map(child=>({tag:child.tagName,text:child.textContent,box:box(child)}))};
  });
  return {header:box(header),reader:box(reader),viewport:{width:innerWidth,height:innerHeight},controls};
 });
 const navigationViolations=g=>{
  const inside=(a,b)=>a.left>=b.left-1&&a.right<=b.right+1&&a.top>=b.top-1&&a.bottom<=b.bottom+1;
  return [
   g.reader.top>=g.header.bottom-1?null:'header overlaps overview reading region',
   ...g.controls.flatMap(c=>[
    inside(c.box,g.header)?null:c.name+' escapes header',
    inside(c.box,{left:0,top:0,right:g.viewport.width,bottom:g.viewport.height})?null:c.name+' escapes viewport',
    c.hit?null:c.name+' has covered hit targets',
    ...c.children.map(child=>inside(child.box,c.box)?null:c.name+' clips '+child.tag)
   ])
  ].filter(Boolean);
 };
 const assertNavigation=async()=>{const g=await navigationGeometry();report.lastNavigation=g;assert.deepEqual(navigationViolations(g),[],'FIXED_CANVAS_NAVIGATION');return g};
 const inspectorGeometry=()=>panel.evaluate(el=>{
  const box=node=>{const r=node.getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
  const content=el.querySelector('.a-canvas-content'),reader=el.querySelector('.a-canvas-reader'),inspector=el.querySelector('#canvas-options');
  return {panel:box(el),content:box(content),reader:box(reader),body:box(el.querySelector('.a-canvas-body')),inspector:box(inspector),
   navigation:box(el.querySelector('.a-canvas-head')),overlay:content.dataset.detailsOverlay==='true',readerInert:reader.inert,
   inspectorScrollWidth:inspector.scrollWidth,inspectorClientWidth:inspector.clientWidth,overflow:getComputedStyle(inspector).overflowY,
   viewport:{width:innerWidth,height:innerHeight}};
 });
 const inspectorViolations=(g,closed)=>{
  const near=(a,b)=>Math.abs(a-b)<=2;
  return [
   near(g.body.top,closed.body.top)&&near(g.body.height,closed.body.height)?null:'Details consumes reader height',
   near(g.inspector.top,g.content.top)&&near(g.inspector.bottom,g.content.bottom)?null:'Details is not bounded to the content row',
   g.inspector.top>=g.navigation.bottom-1&&g.inspector.bottom<=Math.min(g.panel.bottom,g.viewport.height)+1?null:'Details covers navigation or escapes Canvas',
   g.inspectorScrollWidth<=g.inspectorClientWidth+1?null:'Details has horizontal overflow',
   /auto|scroll/.test(g.overflow)?null:'Details cannot scroll independently',
   g.overlay
    ?near(g.reader.width,closed.reader.width)&&g.readerInert&&g.inspector.width<=344+1&&near(g.inspector.right,g.content.right)?null:'Narrow sheet changes reader width or leaves covered content active'
    :g.reader.width>=320&&near(g.inspector.width,264)&&g.reader.right<=g.inspector.left+1&&!g.readerInert?null:'Wide Details is not beside a usable reader'
  ].filter(Boolean);
 };
 const assertInspector=async closed=>{
  const sample=await inspectorGeometry();report.lastInspector=sample;
  assert.deepEqual(inspectorViolations(sample,closed),[],'SIDE_CANVAS_INSPECTOR');return sample;
 };
 const wheel=async(locator,x,y)=>{
  const b=await locator.boundingBox();assert.ok(b?.width>0&&b.height>0,'Wheel target must be rendered');
  await page.mouse.move(b.x+b.width*.8,b.y+b.height/2);await page.mouse.wheel(x,y);
 };
 const top=async()=>{
  await overview.focus();await expect(overview).toBeFocused();await page.keyboard.press('Home');
  await expect.poll(()=>overview.evaluate(el=>el.scrollTop)).toBeLessThanOrEqual(1);
 };
 const revealByWheel=async locator=>{
  for(let attempt=0;attempt<30;attempt++){
   if(await reachable(locator))return;
   const r=await locator.boundingBox(),s=await overview.boundingBox(),before=await overview.evaluate(el=>el.scrollTop);
   assert.ok(r&&s,'The requested section must have real layout');
   await wheel(overview,0,(r.y<s.y?-1:1)*s.height*.7);
   await expect.poll(()=>overview.evaluate(el=>el.scrollTop),{message:'Native wheel must advance toward section',timeout:1500}).not.toBe(before);
  }
  assert.fail('Section did not become reachable after 30 native wheel inputs');
 };
 const geometry=()=>overview.evaluate(el=>{
  const box=node=>{const b=node.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,right:b.right,bottom:b.bottom}};
  const panel=el.closest('#workspace-canvas'),wrapper=el.closest('.a-canvas-session-body');
  return {viewport:{width:innerWidth,height:innerHeight},panel:box(panel),wrapper:box(wrapper),overview:box(el),
   clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,scrollTop:el.scrollTop,clientWidth:el.clientWidth,scrollWidth:el.scrollWidth,
   pageWidth:document.documentElement.scrollWidth,pageHeight:document.documentElement.scrollHeight,
   overflowY:getComputedStyle(el).overflowY,wrapperDisplay:getComputedStyle(wrapper).display};
 });
 const boundedViolations=g=>[
  g.overview.height>80?null:'overview has no usable height',
  g.scrollHeight>g.clientHeight+100?null:'long content does not overflow its own scrollport',
  /auto|scroll/.test(g.overflowY)?null:'overview does not own vertical scrolling',
  g.overview.bottom<=Math.min(g.panel.bottom,g.viewport.height)+1?null:'overview extends below the panel/viewport',
  g.wrapper.bottom<=g.panel.bottom+1?null:'session wrapper escapes the panel',
  g.scrollWidth<=g.clientWidth+1?null:'overview has horizontal overflow',
  g.pageWidth<=g.viewport.width+1&&g.pageHeight<=g.viewport.height+1?null:'document escapes the viewport'
 ].filter(Boolean);
 const assertBoundedOverflow=async(timeout=3000)=>{
  let sample;
  await expect.poll(async()=>{sample=await geometry();return boundedViolations(sample)},
   {message:'BOUNDED_OVERVIEW_OVERFLOW',timeout,intervals:[32,64,100]}).toEqual([]);
  return sample;
 };
 const titles=Array.from({length:30},(_,i)=>`Document ${String(i+1).padStart(2,'0')} — retained research and review notes`),ids=[];
 await check('30 saved outputs and 12 genuinely open documents',async()=>{
  // A real attachment/send creates Sources without fabricating app state or DOM.
  await action('attachment.add',{sessionId,name:'Canvas reference.txt',base64:Buffer.from('Synthetic Canvas reference').toString('base64')});
  const attachment=(await state()).sessions.find(row=>row.id===sessionId).draftAttachments[0];
  await action('conversation.send',{sessionId,text:'Canvas fixture reference',attachmentIds:[attachment.id]});
  await expect.poll(async()=>(await state()).sessions.find(row=>row.id===sessionId).status).toBe('idle');
  await action('view.update',{patch:{draft:'Retain the Canvas test draft'}});
  const response=await page.request.post(url+'/fixture/activity',{data:{sessionId,status:'idle',
   plan:{items:[{content:'Review retained sources',status:'completed'},{content:'Inspect the Canvas result',status:'pending'}]},
   workers:Array.from({length:35},(_,i)=>({id:'worker-'+i,name:'Completed worker '+i,status:'completed'}))}});
  assert.equal(response.ok(),true);assert.equal((await response.json()).ok,true);
  for(const [index,title] of titles.entries()){
   await action('canvas.show',{kind:'markdown',title,content:`# Document ${index+1}\n\nRetained source ${index+1}.`});
   ids.push((await state()).canvas.id);
  }
  assert.equal(new Set(ids).size,30);
  for(const id of ids.slice(0,18))await action('canvas.tabClose',{id});
  await action('canvas.versions.revise',{id:ids[29],expectedRevision:1,content:'# Second version\n\nRetained source 30.'});
  await counts(12);await overviewTab.click();await expect(overviewTab).toHaveAttribute('aria-selected','true');
  await expect(overview.locator('.a-saved-artifact')).toHaveCount(30);
  await expect(summary('outputs')).toContainText('30 saved');
  await expect(summary('agents')).toContainText('35 finished');
  await expect(section('sources')).toContainText('Canvas reference.txt');
  return {saved:30,open:12,ids};
 });
 await check('scroll containment and negative control use the same assertion',async()=>{
  const good=await assertBoundedOverflow();
  // Reintroduce the missing flex sizing link, not replacement content. A mere
  // "scrollHeight > clientHeight" check used to miss this clipped-wrapper defect.
  const broken=await page.addStyleTag({content:'#amp-one .a-canvas-session-body{display:block!important;flex:none!important;min-height:auto!important;overflow:visible!important}'});
  let negative;
  try{
   await assert.rejects(async()=>{try{await assertBoundedOverflow(350)}catch(error){negative={sample:await geometry(),error:errorText(error)};throw error}},/BOUNDED_OVERVIEW_OVERFLOW/);
   assert.ok(boundedViolations(negative.sample).length,'Negative control must fail for measured overflow, not a missing selector');
   await capture('negative-broken-wrapper');
  }finally{await broken.evaluate(el=>el.remove())}
  await assertBoundedOverflow();return {good,negative};
 });
 await check('delivery navigation reaches and focuses its real lower section',async()=>{
  const draft=(await state()).view.canvasDraft||{};
  await action('view.update',{patch:{canvasDraft:{...draft,library:true,delivery:'publishing'}}});
  await expect(section('publishing')).toHaveAttribute('open','');
  await expect(summary('publishing')).toBeFocused();
  await requireReachable(summary('publishing'),'Publishing navigation destination');
  await summary('publishing').click();
  return {destination:'publishing',focused:true};
 });
 await check('all populated top-level sections collapse, reopen and retain choices',async()=>{
  const sectionIds=await overview.locator('details[data-overview-section]').evaluateAll(nodes=>nodes.map(el=>el.dataset.overviewSection));
  assert.deepEqual(sectionIds,['plan','outputs','sources','agents','workspace','activity','permissions','review','publishing']);
  for(const id of sectionIds){
   await revealByWheel(summary(id));
   if(!await section(id).evaluate(el=>el.open)){await summary(id).click();await expect(section(id)).toHaveAttribute('open','')}
   await summary(id).focus();await page.keyboard.press('Space');
   await expect(section(id)).not.toHaveAttribute('open','');await expect(summary(id)).toBeFocused();
   await page.keyboard.press('Enter');await expect(section(id)).toHaveAttribute('open','');
   // Leave each closed, then verify the exact choices across hide and reload.
   await summary(id).click();await expect(section(id)).not.toHaveAttribute('open','');
  }
  await visibility(false);await expect(panel).toBeHidden();await expect(toggle).toHaveAccessibleName('Open canvas');
  const reopened=await visibility(true,{holdBeforeAdmission:true});await expect(overview).toBeVisible();
  await settleCanvasReceipts(observedReceipts.values());
  await page.reload();await showFixtureConversation(page,{navPinned:false});await expect(overview).toBeVisible();
  const reloaded=await serverSnapshot((await state()).client.id);
  assertCanvasVisibility(reloaded,{...reopened.expected,clientId:reloaded.client.id},reopened.snapshot.revision);
  assert.notEqual(reloaded.client.id,reopened.expected.clientId,'Reload resumes a new private client copy');
  assert.equal(reloaded.canvas.selectedVersion,reopened.snapshot.canvas.selectedVersion);
  assert.equal(reloaded.view.canvasDraft.library,true);assert.equal(reloaded.view.draft,'Retain the Canvas test draft');
  report.visibilityReload={acceptedCommand:reopened.expected.commandId,beforeClient:reopened.expected.clientId,afterClient:reloaded.client.id,canvasId:reloaded.canvas.id,open:reloaded.canvas.open,revision:reloaded.revision};
  for(const id of sectionIds)await expect(section(id)).not.toHaveAttribute('open','');
  for(const id of sectionIds){await revealByWheel(summary(id));await summary(id).click();await expect(section(id)).toHaveAttribute('open','')}
  // The expensive lower panels were exercised; leave their compact defaults.
  for(const id of ['review','publishing']){await revealByWheel(summary(id));await summary(id).click();await expect(section(id)).not.toHaveAttribute('open','')}
  await assertBoundedOverflow();return {sectionIds,retainedAfterReload:true};
 });
 await check('pinned Overview, native horizontal wheel, reading retention and runtime reduced motion',async()=>{
  await page.emulateMedia({reducedMotion:'no-preference'});
  await wheel(strip,-100000,0);await expect.poll(()=>strip.evaluate(el=>el.scrollLeft)).toBeLessThanOrEqual(1);
  const transition=()=>overviewTab.evaluate(el=>({width:getComputedStyle(el).transitionDuration,label:getComputedStyle(el.querySelector('span')).transitionDuration,property:getComputedStyle(el).transitionProperty}));
  const milliseconds=value=>value.split(',').map(part=>parseFloat(part)*(part.trim().endsWith('ms')?1:1000));
  const normal=await transition();assert.ok(milliseconds(normal.width).some(ms=>ms>0));assert.match(normal.property,/width|all/);
  await expect.poll(()=>overviewTab.evaluate(el=>el.getBoundingClientRect().width)).toBeGreaterThan(100);
  await top();await wheel(overview,0,600);await expect.poll(()=>overview.evaluate(el=>el.scrollTop)).toBeGreaterThan(100);
  const vertical=await overview.evaluate(el=>el.scrollTop);
  await wheel(strip,100000,0);
  await expect.poll(()=>strip.evaluate(el=>el.scrollLeft/(el.scrollWidth-el.clientWidth))).toBeGreaterThan(.95);
  await expect.poll(()=>overviewTab.evaluate(el=>el.getBoundingClientRect().width)).toBeLessThanOrEqual(45);
  await requireReachable(overviewTab,'Pinned Overview');
  const horizontal=await strip.evaluate(el=>el.scrollLeft);
  await page.emulateMedia({reducedMotion:'reduce'});
  await expect.poll(async()=>milliseconds((await transition()).width).every(ms=>ms===0)).toBe(true);
  const reduced=await transition();assert.ok(milliseconds(reduced.label).every(ms=>ms===0));
  const compactNavigation=await assertNavigation(),alignment=await overviewTab.evaluate(el=>el.style.justifyContent);
  try{
   await overviewTab.evaluate(el=>{el.style.justifyContent='center'});
   const broken=await navigationGeometry();
   assert.ok(navigationViolations(broken).includes('Chat overview clips svg'));
   await assert.rejects(assertNavigation(),/FIXED_CANVAS_NAVIGATION/);
   report.compactIconNegative={sample:broken,rejected:true};
  }finally{await overviewTab.evaluate((el,value)=>{el.style.justifyContent=value},alignment)}
  await assertNavigation();
  const lastTab=strip.getByRole('tab',{name:titles[29],exact:true});
  await requireReachable(lastTab,'Far document');await lastTab.click();await expect(lastTab).toHaveAttribute('aria-selected','true');
  await overviewTab.click();await expect(overviewTab).toHaveAttribute('aria-selected','true');
  for(let cycle=0;cycle<2;cycle++){
   await expect.poll(()=>strip.evaluate(el=>el.scrollLeft)).toBeCloseTo(horizontal,0);
   await expect.poll(()=>overview.evaluate(el=>el.scrollTop)).toBeCloseTo(vertical,0);
   await toggle.click();await expect(panel).toBeHidden();await toggle.click();await expect(overview).toBeVisible();
  }
  await expect.poll(()=>strip.evaluate(el=>el.scrollLeft)).toBeCloseTo(horizontal,0);
  await expect.poll(()=>overview.evaluate(el=>el.scrollTop)).toBeCloseTo(vertical,0);
  await page.emulateMedia({reducedMotion:'no-preference'});
  await expect.poll(async()=>milliseconds((await transition()).width).some(ms=>ms>0)).toBe(true);
  await capture('pinned-overview-far-strip');
  // Arrow/Home/End focus tabs; Enter selects. No scrollIntoView helper.
  await panel.evaluate(el=>{
   const events=[],describe=node=>({tag:node?.tagName,role:node?.getAttribute?.('role'),label:node?.getAttribute?.('aria-label'),artifactId:node?.dataset?.artifactId});
   const observe=event=>events.push({event:event.type,key:event.key,...describe(event.target)});
   el.addEventListener('focusin',observe);el.addEventListener('keydown',observe);
   window.canvasKeyboardObservation={read:()=>({events,active:describe(document.activeElement)}),stop:()=>{el.removeEventListener('focusin',observe);el.removeEventListener('keydown',observe)}};
  });
  try{
   await overviewTab.focus();await page.keyboard.press('End');await expect(lastTab).toBeFocused();
   await page.keyboard.press('Enter');await expect(lastTab).toHaveAttribute('aria-selected','true');
   await page.keyboard.press('Home');await expect(overviewTab).toBeFocused();await page.keyboard.press('Enter');
   await expect(overviewTab).toHaveAttribute('aria-selected','true');
  }finally{report.keyboardOrder=await page.evaluate(()=>{const o=window.canvasKeyboardObservation,r=o.read();o.stop();delete window.canvasKeyboardObservation;return r})}
  return {horizontal,vertical,normal,reduced,compactNavigation};
 });
 const presentation=async scheme=>{
  await page.waitForFunction(()=>window.amplifier.getShellState()?.effectiveComposition);
  const current=await page.evaluate(()=>({shell:window.amplifier.getShellState(),clientId:window.amplifier.shellClientId}));
  const composition={...current.shell.effectiveComposition,presentation:{...current.shell.effectiveComposition.presentation,scheme}};
  const prepared=await action('shell.changes.prepare',{clientId:current.clientId,expectedRevision:current.shell.revision,composition});
  await action('shell.changes.apply',{clientId:current.clientId,expectedRevision:current.shell.revision,changeId:prepared.result.id});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
 };
 for(const scheme of ['light','dark']){
  await presentation(scheme);
  for(const size of [{name:'desktop-1500',width:1500,pane:720},{name:'split-390',width:1500,pane:390},{name:'mobile-390',width:390,pane:390},{name:'mobile-320',width:320,pane:320}]){
   await check(scheme+' '+size.name+' real wheel/keyboard reachability, toolbar and focused toggle',async()=>{
    await page.setViewportSize({width:size.width,height:850});
    await action('view.update',{patch:{canvasWidth:size.pane,canvasOverviewWidth:size.pane,canvasFocused:false}});
    await overviewTab.click();await expect(overview).toBeVisible();await top();
    const bounds=await assertBoundedOverflow();
    if(size.width===1500)assert.ok(Math.abs(bounds.panel.width-size.pane)<=2,'Requested split width must actually be rendered');
    await requireReachable(summary('outputs'),'Upper Outputs section');
    const navigation=await assertNavigation();
    if(scheme==='light'&&size.name==='mobile-320'){
     const prior=await documents().evaluate(el=>el.style.transform);
     try{
      await documents().evaluate(el=>{el.style.transform='translateY(52px)'});
      const broken=await navigationGeometry();
      assert.ok(navigationViolations(broken).includes('Open documents, 12 open escapes header'));
      await assert.rejects(assertNavigation(),/FIXED_CANVAS_NAVIGATION/);
      report.navigationNegative={sample:broken,rejected:true};
     }finally{await documents().evaluate((el,value)=>{el.style.transform=value},prior)}
     await assertNavigation();
    }
    await capture(scheme+'-'+size.name+'-upper');
    await wheel(overview,0,bounds.scrollHeight);
    await expect.poll(()=>overview.evaluate(el=>el.scrollTop+el.clientHeight>=el.scrollHeight-2)).toBe(true);
    await requireReachable(summary('publishing'),'Lower Publishing section by wheel');
    await capture(scheme+'-'+size.name+'-lower');
    await top();await requireReachable(summary('outputs'),'Upper section by Home');
    await page.keyboard.press('PageDown');await expect.poll(()=>overview.evaluate(el=>el.scrollTop)).toBeGreaterThan(100);
    await page.keyboard.press('End');await requireReachable(summary('publishing'),'Lower section by End');
    await action('canvas.select',{id:ids[29]});
    // Reset only a displaced strip. A boundary-only horizontal gesture can
    // invoke browser Back; it does not exercise document-strip scrolling.
    const remaining=await strip.evaluate(el=>el.scrollLeft);
    if(remaining>1)await wheel(strip,-remaining,0);
    await expect.poll(()=>strip.evaluate(el=>el.scrollLeft)).toBeLessThanOrEqual(1);
    // The named tab stays reachable; its label collapses by Canvas container
    // width, not viewport width (a desktop split can be only 390px wide).
    const paneWidth=await panel.evaluate(el=>{const s=getComputedStyle(el);return el.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight)});
    const overviewLabel=overviewTab.locator('span');
    await expect(overviewTab).toHaveAccessibleName('Chat overview');
    await requireReachable(overviewTab,'Overview tab at the start of the strip');
    await expect(overviewTab.locator('svg')).toBeVisible();
    const overviewBox=await overviewTab.boundingBox();
    assert.ok(overviewBox.width>=44&&overviewBox.height>=44,'Overview retains a 44px target');
    if(paneWidth<=600){
     await expect(overviewLabel).toBeHidden();
    }else{
     await expect(overviewLabel).toBeVisible();
     await expect.poll(()=>overviewLabel.evaluate(el=>getComputedStyle(el).opacity)).toBe('1');
    }
    (report.overviewPresentation??=[]).push({scheme,layout:size.name,paneWidth,labelVisible:await overviewLabel.isVisible(),target:overviewBox});
    await expect(panel.getByRole('heading',{name:'Second version',exact:true})).toBeVisible();
    await expect(page.locator('#canvas-options')).toBeHidden();
    for(const name of ['Choose artifact version','Copy canvas source','Download canvas source','Canvas details','Focus canvas']){
     const button=panel.getByRole('button',{name,exact:true});await requireReachable(button,name);
     const box=await button.boundingBox();assert.ok(box.width>=44&&box.height>=44,`${name}: at least 44px target`);
    }
    const closedInspector=await inspectorGeometry();
    const stableControls=await panel.locator('[data-part="canvas-view-controls"]').elementHandle();
    const stableRenderer=await panel.locator('.a-resource-renderer').elementHandle();
    await panel.getByRole('button',{name:'Choose artifact version'}).click();
    await expect(panel.getByRole('combobox',{name:'Artifact version',exact:true})).toBeFocused();
    const details=page.getByRole('region',{name:'Canvas details',exact:true}),closeDetails=details.getByRole('button',{name:'Close canvas details',exact:true});
    await expect(details).toBeVisible();
    const openInspector=await assertInspector(closedInspector);
    assert.equal(openInspector.overlay,size.pane<720,'Wide and narrow Canvas use the intended inspector modes');
    await requireReachable(closeDetails,'Details close');await requireReachable(toggle,'Header toggle with Details open');await requireReachable(overviewTab,'Overview with Details open');
    await capture(scheme+'-'+size.name+'-details');
    if(scheme==='light'&&size.name==='desktop-1500'){
     // Restore the old above-reader geometry on the real nodes. The SAME
     // positive assertion must reject reader-height loss, not a missing element.
     const broken=await page.addStyleTag({content:'#amp-one .a-canvas-content{flex-direction:column!important}#amp-one .a-canvas-content>.a-canvas-inspector{position:static!important;order:-1!important;flex:0 0 35dvh!important;width:100%!important;height:35dvh!important;max-height:35dvh!important}'});
     try{
      const sample=await inspectorGeometry();assert.ok(inspectorViolations(sample,closedInspector).includes('Details consumes reader height'));
      await assert.rejects(assertInspector(closedInspector),/SIDE_CANVAS_INSPECTOR/);
      report.inspectorTopBandNegative={sample,rejected:true};await capture('negative-details-top-band');
     }finally{await broken.evaluate(el=>el.remove())}
     await assertInspector(closedInspector);
    }
    await documents().click();await expect(dialog).toBeVisible();await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();await expect(details).toBeVisible();
    await closeDetails.click();await expect(page.locator('#canvas-options')).toBeHidden();
    await expect(panel.getByRole('button',{name:'Choose artifact version'})).toBeFocused();
    assert.equal(await panel.locator('[data-part="canvas-view-controls"]').evaluate((el,original)=>el===original,stableControls),true);
    assert.equal(await panel.locator('.a-resource-renderer').evaluate((el,original)=>el===original,stableRenderer),true);
    await stableControls.dispose();await stableRenderer.dispose();
    await capture(scheme+'-'+size.name+'-artifact');
    await panel.getByRole('button',{name:'Focus canvas',exact:true}).click();await expect(panel).toHaveAttribute('data-focused','true');
    await requireReachable(toggle,'Header Hide canvas in focus mode');
    await capture(scheme+'-'+size.name+'-focused');
    await panel.getByRole('button',{name:'Canvas details',exact:true}).click();await expect(details).toBeVisible();
    await page.keyboard.press('Escape');await expect(details).toBeHidden();
    await expect(panel,'Escape closes Details before leaving focused Canvas').toHaveAttribute('data-focused','true');
    await panel.getByRole('button',{name:'Canvas details',exact:true}).click();await expect(details).toBeVisible();
    // The native dialog still owns the first Escape. Then exercise the real
    // document listener from the shared header, outside the Canvas aside.
    await documents().click();await expect(dialog).toBeVisible();await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();await expect(details).toBeVisible();await expect(documents()).toBeFocused();
    await toggle.focus();await expect(toggle).toBeFocused();await page.keyboard.press('Escape');
    await expect(details,'Header Escape closes Details first').toBeHidden();
    await expect(panel,'Header Escape must retain focused Canvas').toHaveAttribute('data-focused','true');
    await expect(toggle,'Closing Details must not steal the header focus').toBeFocused();
    (report.headerEscape??=[]).push({scheme,layout:size.name,detailsClosed:true,canvasFocused:true,headerFocusRetained:true});
    // Start on the included header control and traverse both modal boundaries.
    await toggle.focus();await page.keyboard.press('Shift+Tab');await page.keyboard.press('Tab');await expect(toggle).toBeFocused();
    await page.keyboard.press('Enter');await expect(panel).toBeHidden();await expect(toggle).toHaveAccessibleName('Open canvas');
    await expect(toggle).toBeFocused();await page.keyboard.press('Enter');await expect(panel).toBeVisible();
    // Hiding ends focus mode by contract; reopening is the normal split/overlay.
    await expect(panel).toHaveAttribute('data-focused','false');
    await panel.getByRole('button',{name:'Focus canvas',exact:true}).click();
    await expect(panel).toHaveAttribute('data-focused','true');await page.keyboard.press('Escape');
    await expect(panel).toHaveAttribute('data-focused','false');
    await expect(panel.getByRole('button',{name:'Close canvas panel',exact:true})).toBeVisible();
    await expect(panel.getByRole('button',{name:'Back to canvas',exact:true})).toHaveCount(0);
    return {...bounds,navigation,details:{closed:closedInspector,open:openInspector}};
   });
  }
 }
 await check('200 percent equivalent layout viewport retains navigation and lower content',async()=>{
  await page.setViewportSize({width:1280,height:900});
  const overviewResponse=page.waitForResponse(response=>{
   const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
   const command=request.postDataJSON();return command.action==='view.update'&&command.args?.patch?.canvasDraft?.library===true;
  },{timeout:8000}).then(value=>({value}),error=>({error}));
  await overviewTab.click();
  const opened=await overviewResponse;if(opened.error)throw opened.error;
  await acceptedCanvasReceipt(observedReceipts.get(opened.value.request()),opened.value.request().postDataJSON().id);
  await expect(overview).toBeVisible();await expect(panel).toHaveAttribute('data-focused','false');
  // Browser zoom reduces the CSS layout viewport. CSS zoom on #amp-one alone
  // instead doubles its 100dvh box and is not a faithful browser-zoom emulation.
  // CDP emulates the half-size layout viewport and 2x pixel density; this is not
  // evidence of an OS/browser-toolbar zoom action or a physical device.
  const metrics=await context.newCDPSession(page);
  await metrics.send('Emulation.setDeviceMetricsOverride',{width:640,height:450,deviceScaleFactor:2,mobile:false});
  try{
   await expect.poll(()=>page.evaluate(()=>[innerWidth,innerHeight,devicePixelRatio])).toEqual([640,450,2]);
   await requireReachable(toggle,'Header toggle at 200 percent equivalent layout');
   await top();await expect(overview).toBeFocused();await page.keyboard.press('End');
   await requireReachable(summary('publishing'),'Publishing at 200 percent equivalent layout');
   await capture('emulated-layout-zoom-200');
  }catch(error){
   report.zoomFailure=await summary('publishing').evaluate(el=>{
    const box=node=>{const r=node?.getBoundingClientRect();return r?{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}:null};
    const owner=el.closest('.a-canvas-overview-page'),r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2),active=document.activeElement;
    return {viewport:[innerWidth,innerHeight,devicePixelRatio],section:box(el),owner:box(owner),scrollTop:owner.scrollTop,scrollHeight:owner.scrollHeight,clientHeight:owner.clientHeight,active:{tag:active?.tagName,label:active?.getAttribute('aria-label')},hit:{tag:hit?.tagName,label:hit?.getAttribute('aria-label')},inert:!!el.closest('[inert]')};
   });
   await capture('emulated-layout-zoom-failure');throw error;
  }finally{await metrics.send('Emulation.clearDeviceMetricsOverride');await metrics.detach();await page.setViewportSize({width:1280,height:900})}
  return {scale:2,layout:{width:640,height:450},method:'CDP layout viewport and pixel density, not physical/browser-toolbar zoom'};
 });
 await check('held Delete preserves newer Home while an owned close restores focus',async()=>{
  await page.setViewportSize({width:1500,height:850});await action('view.update',{patch:{canvasWidth:720,canvasOverviewWidth:720}});
  const closingId=ids[18],closingTab=()=>strip.getByRole('tab',{name:titles[18],exact:true}),successor=strip.getByRole('tab',{name:titles[29],exact:true});
  const observed=promise=>promise.then(value=>({value}),error=>({error}));
  const received=async promise=>{const result=await promise;if(result.error)throw result.error;return result.value};
  const selectByClick=async()=>{
   const clientId=(await state()).client.id;
   const selected=observed(page.waitForResponse(response=>{
    const request=response.request();if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions'||request.headers()['x-amplifier-client']!==clientId)return false;
    const command=request.postDataJSON();return command.action==='canvas.select'&&command.args.id===closingId;
   },{timeout:8000}));
   await closingTab().click();
   const response=await received(selected);await acceptedCanvasReceipt(observedReceipts.get(response.request()),response.request().postDataJSON().id);
   await expect(closingTab()).toHaveAttribute('aria-selected','true');await expect(closingTab()).toBeFocused();
   // Actual rendered pending state, not an immediately-true selection assertion.
   await expect(page.getByRole('dialog',{name:'Open documents',exact:true,includeHidden:true}).getByRole('button',{name:'Close tab '+titles[18],exact:true,includeHidden:true})).toBeEnabled();
  };
  await selectByClick();
  const start=await state(),events=[];
  await page.evaluate(()=>{
   const events=[],record=event=>events.push({type:event.type,key:event.key||null,id:event.target.dataset?.artifactId||event.target.getAttribute?.('aria-label')||event.target.tagName});
   document.addEventListener('focusin',record);document.addEventListener('keydown',record,true);
   window.canvasCloseObservation={events,stop(){document.removeEventListener('focusin',record);document.removeEventListener('keydown',record,true)}};
  });
  let release,heldRequest;
  const gate=new Promise(resolve=>release=resolve);
  const matches=request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions'||request.headers()['x-amplifier-client']!==start.client.id)return false;
   const command=request.postDataJSON();return command.action==='canvas.tabClose'&&command.args.id===closingId;
  };
  const intercept=async route=>{if(!matches(route.request()))return route.continue();heldRequest=route.request();await gate;await route.continue()};
  await page.route('**/api/actions',intercept);
  const held=observed(page.waitForRequest(matches,{timeout:8000}));
  const response=observed(page.waitForResponse(value=>matches(value.request()),{timeout:8000}));
  try{
   await page.keyboard.press('Delete');const request=await received(held);
   await expect.poll(()=>heldRequest===request).toBe(true);
   assert.equal(observedReceipts.has(heldRequest),false,'Held close has no acceptance yet');
   await page.keyboard.press('Home');await expect(overviewTab).toBeFocused();
   release();const receiptResponse=await received(response),command=receiptResponse.request().postDataJSON();
   assert.equal(receiptResponse.request(),heldRequest);
   await acceptedCanvasReceipt(observedReceipts.get(heldRequest),command.id);
   await counts(11);await expect(closingTab()).toHaveCount(0);await expect(successor).toHaveAttribute('aria-selected','true');
   // Let the actual deferred frame run; never force focus or retry a close.
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await expect(overviewTab).toBeFocused();
   events.push({phase:'held-close-newer-Home',commandId:command.id,accepted:true,focused:'Chat overview'});
   await capture('delayed-close-newer-home');
  }finally{
   release();await page.unroute('**/api/actions',intercept);
   report.removeFocus={checks:events,events:await page.evaluate(()=>{const record=window.canvasCloseObservation;record.stop();delete window.canvasCloseObservation;return record.events})};
  }
  // Reopen the saved item, then prove the same close without newer input still
  // recovers to the selected surviving tab. No explicit focus() is used.
  await action('canvas.select',{id:closingId});await counts(12);
  await selectByClick();
  const ownResponse=observed(page.waitForResponse(value=>matches(value.request()),{timeout:8000}));
  await page.keyboard.press('Delete');
  const own=await received(ownResponse),command=own.request().postDataJSON();
  await acceptedCanvasReceipt(observedReceipts.get(own.request()),command.id);
  await counts(11);await expect(successor).toBeFocused();
  report.removeFocus.checks.push({phase:'owned-close',commandId:command.id,accepted:true,focused:ids[29]});
  await action('canvas.select',{id:closingId});await counts(12);
  // Restore the original following scenario's distinct selected-document setup:
  // it selects Document 19 from a list where Document 30 is current.
  await action('canvas.select',{id:ids[29]});
  await expect(successor).toHaveAttribute('aria-selected','true');
  return report.removeFocus;
 });
 await check('native all-open list, exact counts, close-active/last and saved recovery',async()=>{
  await page.setViewportSize({width:1500,height:850});await action('view.update',{patch:{canvasWidth:720,canvasOverviewWidth:720}});
  await counts(12);await documents().click();await expect(dialog).toBeVisible();
  assert.equal(await dialog.evaluate(el=>el instanceof HTMLDialogElement&&el.open&&el.matches(':modal')),true);
  await expect(dialog.locator('li')).toHaveCount(12);await expect(dialog).toContainText('12 open · 30 saved outputs');
  const search=dialog.getByRole('searchbox',{name:'Find an open document'});
  await search.fill('Document 19');await expect(dialog.locator('li')).toHaveCount(1);
  // Observe actual application ordering without moving focus from the test.
  await dialog.evaluate((element,id)=>{
   const events=[],nativeClose=element.close;
   window.canvasFocusOrder={events,restore:()=>{element.close=nativeClose;document.removeEventListener('focusin',focused)}};
   const focused=event=>{if(event.target.dataset?.artifactId===id)events.push({event:'selected-tab-focus',modal:element.matches(':modal')})};
   element.close=function(...args){const result=nativeClose.apply(this,args);events.push({event:'native-close',modal:this.matches(':modal')});return result};
   document.addEventListener('focusin',focused);
  },ids[18]);
  await dialog.locator('li').getByRole('button',{name:titles[18]+' markdown',exact:true}).click();
  await expect(dialog).not.toBeVisible();await expect(strip.getByRole('tab',{name:titles[18],exact:true})).toBeFocused();
  const order=await page.evaluate(()=>{const value=window.canvasFocusOrder;value.restore();delete window.canvasFocusOrder;return value.events});
  assert.deepEqual(order.slice(0,2),[{event:'native-close',modal:false},{event:'selected-tab-focus',modal:false}]);
  report.focusOrder=order;
  assert.equal((await state()).canvas.id,ids[18]);
  await documents().click();await search.fill('');await page.keyboard.press('Escape');await expect(documents()).toBeFocused();
  // Close the selected tab through the native dialog; the other 29 saved bodies remain.
  await documents().click();await dialog.getByRole('button',{name:'Close tab '+titles[18],exact:true}).click();
  await counts(11);assert.equal((await state()).canvas.id,ids[29]);
  await expect(dialog.locator('li[aria-current="true"]')).toContainText(titles[29]);
  for(let index=19;index<30;index++){
   await dialog.getByRole('button',{name:'Close tab '+titles[index],exact:true}).click();
   await counts(29-index);
  }
  await expect(dialog).toContainText('No documents are open. Saved outputs remain in Overview.');
  await page.keyboard.press('Escape');await expect(documents()).toBeFocused();
  await expect(overviewTab).toHaveAttribute('aria-selected','true');await expect(overview).toBeVisible();
  await top();const saved=overview.locator('.a-saved-artifact').filter({hasText:titles[29]});
  await requireReachable(saved,'Saved document recovery');await saved.click();await counts(1);
  assert.equal((await state()).canvas.id,ids[29]);await expect(panel.getByRole('heading',{name:'Second version',exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'Choose artifact version'}).click();
  await panel.getByRole('combobox',{name:'Artifact version',exact:true}).selectOption('1');
  await expect(panel.getByRole('heading',{name:'Document 30',exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'Go to Latest',exact:true}).click();
  await expect(panel.getByRole('heading',{name:'Second version',exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Canvas details',exact:true})).toHaveAttribute('aria-expanded','true');
  const [closedResponse]=await Promise.all([page.waitForResponse(response=>{
   if(!isAction(response.request()))return false;
   const command=unwrap(response.request().postDataJSON());
   return command.action==='view.update'&&command.args.sessionId===sessionId
    &&JSON.stringify(command.args.patch)===JSON.stringify({canvasControlsExpanded:false});
  },{timeout:8000}),panel.getByRole('button',{name:'Canvas details',exact:true}).click()]);
  const command=unwrap(closedResponse.request().postDataJSON());
  const closedReceipt=await acceptedCanvasReceipt(observedReceipts.get(closedResponse.request()),command.id);
  await expect(panel.getByRole('button',{name:'Canvas details',exact:true})).toHaveAttribute('aria-expanded','false');
  await expect(page.locator('#canvas-options')).toBeHidden();
  report.detailsCloseBeforeReload={commandId:command.id,status:closedReceipt.status,body:closedReceipt.body,bodyReadBeforeReload:true,closed:true};
  await settleCanvasReceipts(observedReceipts.values());
  await page.reload();await showFixtureConversation(page,{navPinned:false});
  await expect(panel.getByRole('heading',{name:'Second version',exact:true})).toBeVisible();await expect(page.locator('#canvas-options')).toBeHidden();
  await overviewTab.click();await top();await revealByWheel(overview.getByRole('button',{name:'Open file',exact:true}));
  await overview.getByRole('button',{name:'Open file',exact:true}).click();
  await expect(panel.getByLabel('File in this workspace')).toBeVisible();
  await capture('recovered-file-controls');
  assert.equal((await state()).view.draft,'Retain the Canvas test draft');return {saved:30,open:1,recoveredVersion:2};
 });
 await action('canvas.views.inspect');await settleCanvasReceipts(observedReceipts.values());
 assert.deepEqual(report.http.filter(row=>row.transportError||row.readError||row.receiptTimeout||row.status>=400||row.body?.accepted===false),[],'Unexpected action receipt failures');
 assert.deepEqual(report.browserErrors,[]);report.status='passed';
}catch(error){
 failure=error;report.status='failed';report.error=errorText(error);
 if(page&&!page.isClosed())try{await capture('failure')}catch(error){report.evidenceErrors.push(errorText(error))}
}finally{
 phase='teardown';
 if(context)try{await context.tracing.stop({path:join(output,'trace.zip')})}catch(error){report.evidenceErrors.push(errorText(error));failure||=error}
 try{await browser?.close()}catch(error){report.evidenceErrors.push(errorText(error));failure||=error}
 if(fixture.pid&&fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit'),timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);
  fixture.kill('SIGTERM');try{await exited}finally{clearTimeout(timer)}
 }
 try{await settleCanvasReceipts(observedReceipts.values())}catch(error){report.evidenceErrors.push(errorText(error));failure||=error}
 if(failure)report.status='failed';
 await writeFile(join(output,'fixture.stdout.log'),stdout);await writeFile(join(output,'fixture.stderr.log'),stderr);
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({test:'canvas-chrome',status:report.status,output,checks:report.checks.map(({name,status})=>({name,status})),visualReview:'not-performed'}));
}
if(failure)throw failure;
