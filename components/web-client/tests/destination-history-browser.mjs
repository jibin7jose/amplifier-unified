// Owned synthetic host + production bundle. No model calls or shared application.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {destinationCommand} from '../src/destination-history.js';
import {observeSettingsProviderPreservation,privateWorkspaceText} from './settings-provider-preservation.mjs';
import {chromium,expect} from '@playwright/test';
const out=process.env.AMPLIFIER_TEST_OUTPUT;
if(!out)throw Error('Set AMPLIFIER_TEST_OUTPUT to an owned evidence directory.');
const launch={headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
 ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls','--navigation-history'],{stdio:['ignore','pipe','inherit']});
let browser,page,scenarioFailure,settingsDiagnostic,providerDiagnostic;
// Observation only: no dispatch replacement, state mutation, retry or changed assertion.
function observeSettingsClose(page){
 const started=performance.now(),events=[],requests=new WeakMap();
 let dropped=0,inflight=0,parsed=0,stopped=false,initialization='pending',binding=null,pinnedReceipt=null;
 const token=value=>typeof value==='string'&&/^[\w.:-]{1,160}$/.test(value)?value:null;
 const number=value=>Number.isSafeInteger(value)?value:null;
 const flag=value=>typeof value==='boolean'?value:null;
 const record=(kind,data={})=>{if(stopped)return;if(events.length<128)events.push({atMs:performance.now()-started,wallTimeMs:Date.now(),kind,...data});else dropped++};
 const receipt=value=>({accepted:flag(value?.accepted),revision:number(value?.revision),
  stateRevision:number(value?.stateRevision),hostInstanceId:token(value?.hostInstanceId),hasEmbeddedState:!!value?.state});
 const deadline=async promise=>{
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Diagnostic read deadline')),2000)})])}
  finally{clearTimeout(timer)}
 };
 const requested=request=>{
  try{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
   const command=request.postDataJSON(),name=command?.action;
   if(!['canvas.tabClose','canvas.select','canvas.show','canvas.close','canvas.visibility','view.update','session.select'].includes(name))return;
   const args=command.args||{},patch=args.patch||{};
   const row={id:token(command.id),action:name,clientId:token(request.headers()['x-amplifier-client']),
    args:{id:token(args.id),canvasId:token(args.canvasId),sessionId:token(args.sessionId),kind:token(args.kind),open:flag(args.open)},
    patch:{panel:token(patch.panel),panelPresent:Object.hasOwn(patch,'panel'),settingsCanvas:flag(patch.settingsCanvas),
     workSurface:token(patch.workSurface),settingsSection:token(patch.settingsSection),library:flag(patch.canvasDraft?.library),
     settingsExpanded:Array.isArray(patch.settingsExpanded)?patch.settingsExpanded.slice(0,4).map(token):null}};
   requests.set(request,row);record('request',row);
  }catch{record('request-observation-unavailable')}
 };
 const responded=async response=>{
  const row=requests.get(response.request());if(!row||stopped)return;
  record('response-headers',{id:row.id,status:response.status()});
  if(inflight>=4||parsed>=24){record('receipt-not-read',{id:row.id,reason:'observer-cap'});return}
  const length=Number(response.headers()['content-length']);
  if(length>524288){record('receipt-not-read',{id:row.id,reason:'declared-size'});return}
  inflight++;parsed++;
  try{
   const bytes=await response.body();
   if(bytes.length>524288){record('receipt-not-read',{id:row.id,reason:'actual-size'});return}
   record('receipt-parsed',{id:row.id,status:response.status(),...receipt(JSON.parse(bytes.toString()))});
  }catch{record('receipt-unavailable',{id:row.id})}finally{inflight--}
 };
 const finished=request=>{const row=requests.get(request);if(row)record('request-finished',{id:row.id})};
 const failed=request=>{const row=requests.get(request);if(row)record('request-failed',{id:row.id})};
 const pageError=error=>record('page-error',{name:token(error.name),messageOmitted:true});
 page.on('request',requested);page.on('response',responded);page.on('requestfinished',finished);
 page.on('requestfailed',failed);page.on('pageerror',pageError);
 return {
  async start(){
   try{
    await deadline(page.evaluate(()=>{
     const started=performance.now(),records=[],initialUrl=new URL(location.href);let dropped=0,expired=false,last='',binding={},observer,unsubscribe;
     const token=value=>typeof value==='string'&&/^[\w.:-]{1,160}$/.test(value)?value:null;
     const title=value=>typeof value==='string'?value.slice(0,128):null;
     const marker=()=>({token:token(history.state?.amplifierSettings?.token),index:Number.isInteger(history.state?.amplifierSettings?.index)?history.state.amplifierSettings.index:null});
     const snapshot=state=>{
      const s=state||window.amplifier.getState(),canvas=s.canvas||{},view=s.view||{};
      const rows=(s.canvasArtifacts||[]).filter(row=>row.sessionId===s.selectedSessionId&&row.workspaceId===s.selectedWorkspaceId);
      const artifacts=rows.filter(row=>row.kind==='settings'||row.title==='Settings'||[binding.closedId,binding.successorId,canvas.id].includes(row.id));
      const tabs=[...document.querySelectorAll('#workspace-canvas .a-canvas-document-tabs > [role="tab"]')];
      const openRows=rows.filter(row=>row.tabOpen),currentUrl=new URL(location.href),container=document.querySelector('#workspace-canvas');
      const metadata=row=>({id:token(row.id),title:title(row.title),kind:token(row.kind),tabOpen:row.tabOpen===true});
      return {atMs:performance.now()-started,wallTimeMs:Date.now(),revision:s.revision,clientId:token(s.client?.id),hostInstanceId:token(s.client?.hostInstanceId),
       sessionId:token(s.selectedSessionId),workspaceId:token(s.selectedWorkspaceId),hidden:document.hidden,
       canvas:{id:token(canvas.id),kind:token(canvas.kind),open:canvas.open===true,visibilityPending:!!canvas.visibilityPending},
       view:{panel:token(view.panel),settingsCanvas:view.settingsCanvas===true,workSurface:token(view.workSurface),settingsSection:token(view.settingsSection),library:view.canvasDraft?.library===true,
        settingsExpanded:Array.isArray(view.settingsExpanded)?view.settingsExpanded.slice(0,4).map(token):null},
       navigationPending:!!s.navigationPending,settingsHistory:marker(),historyLength:history.length,
       url:{sameOrigin:currentUrl.origin===initialUrl.origin,samePath:currentUrl.pathname===initialUrl.pathname,chat:token(currentUrl.searchParams.get('chat')),shell:token(currentUrl.searchParams.get('shell'))},
       artifacts:artifacts.slice(0,32).map(metadata),openTabs:openRows.slice(0,32).map(metadata),openTabsOmitted:Math.max(0,openRows.length-32),
       artifactsOmitted:Math.max(0,artifacts.length-32),
       dom:{tabs:tabs.slice(0,32).map((node,index)=>({index,label:title(node.querySelector('[role="tab"] span')?.textContent),
         selected:node.querySelector('[role="tab"]')?.getAttribute('aria-selected'),closeLabel:title(node.querySelector('[data-action="canvas.tabClose"]')?.getAttribute('aria-label')),
         visible:!!node.getClientRects().length})),tabsOmitted:Math.max(0,tabs.length-32),
        closeSettingsCount:document.querySelectorAll('button[aria-label="Close tab Settings"]').length,
        canvasContainer:container?{hidden:container.hidden,inert:container.inert,ariaHidden:container.getAttribute('aria-hidden'),mode:token(container.getAttribute('data-mode'))}:null,
        settingsSurfaces:[...document.querySelectorAll('.a-settings-experience')].slice(0,8).map(node=>({page:token(node.getAttribute('data-settings-page')),compact:token(node.getAttribute('data-compact')),inCanvas:!!node.closest('#workspace-canvas'),visible:!!node.getClientRects().length})),
        unsavedDialogs:document.querySelectorAll('[role="alertdialog"]').length},
       identityLimit:'DOM tabs have no artifact ID attribute; state IDs and ordered labels are separate observations.'};
     };
     const capture=(label,state,force=false)=>{
      try{
       const value=snapshot(state),key=JSON.stringify({...value,atMs:0,wallTimeMs:0});
       if(!force&&key===last)return;last=key;
       if(performance.now()-started>60000)expired=true;
       if(!expired&&records.length<128)records.push({label,value});else dropped++;
       return value;
      }catch{if(records.length<128)records.push({label,unavailable:true});else dropped++;return null}
     };
     const pop=event=>capture(event.isTrusted?'popstate-trusted':'popstate-untrusted',null,true);
     const click=event=>{
      const button=event.target.closest?.('button'),label=button?.getAttribute('aria-label')||button?.textContent?.trim();
      if(['Discard changes','Keep settings open','Close tab Settings'].includes(label))capture('click:'+label,null,true);
     };
     observer=new MutationObserver(()=>capture('DOM-mutation'));
     observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-selected','aria-label','data-settings-page']});
     unsubscribe=window.amplifier.subscribe(state=>capture('state-publication',state));
     window.addEventListener('popstate',pop,true);document.addEventListener('click',click,true);
     window.__settingsCloseObservation={
      mark(label,next){if(next)binding=next;return capture(label,null,true)},
      finish(label){const final=capture(label,null,true);observer.disconnect();unsubscribe();
       window.removeEventListener('popstate',pop,true);document.removeEventListener('click',click,true);
       return {records,dropped,expired,binding,final,clock:'browser performance.now relative to observer installation'}},
     };
     capture('installed',null,true);
    }));initialization='ready';
   }catch{initialization='unavailable';record('initialization-unavailable')}
  },
  async bind(before,closedId,successorId){
   binding={closedId,successorId,clientId:before.client.id,sessionId:before.selectedSessionId,workspaceId:before.selectedWorkspaceId,beforeRevision:before.revision};
   try{await deadline(page.evaluate(binding=>window.__settingsCloseObservation?.mark('before-close',binding),binding))}catch{record('binding-snapshot-unavailable')}
  },
  closeReceipt(id,value){pinnedReceipt={id:token(id),...receipt(value)};record('original-close-receipt',pinnedReceipt)},
  async mark(label){
   try{await deadline(page.evaluate(label=>window.__settingsCloseObservation?.mark(label),label))}catch{record('checkpoint-unavailable',{label})}
  },
  async save(label){
   let observation={unavailable:true};
   try{observation=await deadline(page.evaluate(label=>window.__settingsCloseObservation?.finish(label),label))||observation}catch{record('final-snapshot-unavailable')}
   const pendingReceiptObservers=inflight;stopped=true;
   page.off('request',requested);page.off('response',responded);page.off('requestfinished',finished);
   page.off('requestfailed',failed);page.off('pageerror',pageError);
   await writeFile(path.join(out,'settings-close-diagnostic.json'),JSON.stringify({schema:1,label,initialization,binding,pinnedReceipt,events,dropped,pendingReceiptObservers,observation,
    limits:{nodeClock:'performance.now relative to listener attachment; not the browser clock',maxEvents:128,maxSnapshots:128,bodyObservers:4,bodyReads:24,
     receiptBodyLimit:'524288 bytes checked after acquisition and Content-Length when present; not a universal acquisition bound',
     privateBodiesRetained:false,dispatchesAdded:0,diagnosticOnly:true}},null,2));
  },
 };
}
const pendingGates=new Set();
const placeKey=row=>JSON.stringify([row.kind,row.id||null]);
const menuKeys=(menu,attribute)=>menu.getByRole('menuitem').evaluateAll((rows,attribute)=>rows.map(row=>row.getAttribute(attribute)),attribute);
const placeRow=(page,row)=>page.getByRole('menu',{name:'Recent places',exact:true}).locator(`[data-place-key=${JSON.stringify(placeKey(row))}]`);
const visitRow=(menu,key)=>menu.locator(`[data-visit-key=${JSON.stringify(key)}]`);
async function keyboardSteps(page,button,direction){
 await expect(button).toBeEnabled();await button.focus();await expect(button).toBeFocused();await page.keyboard.press('ArrowDown');
 const menu=page.getByRole('menu',{name:direction+' steps',exact:true});
 await expect(menu).toBeVisible();assert.equal(await menu.locator('[role=menuitem]:focus').count(),1);
 return menu;
}
async function holdSteps(page,button,direction,pointerType){
 // Observe the actual menu insertion, not the time of a later polling assertion.
 // The deadline is only a bound; it cannot make an early-opening menu pass.
 await expect(button).toBeEnabled();
 const elapsed=await button.evaluate((button,{direction,pointerType})=>new Promise((resolve,reject)=>{
  const started=performance.now(),label=direction+' steps';
  const observer=new MutationObserver(()=>{
   if(![...document.querySelectorAll('[role=menu]')].some(node=>node.getAttribute('aria-label')===label))return;
   clearTimeout(deadline);observer.disconnect();resolve(performance.now()-started);
  });
  const deadline=setTimeout(()=>{observer.disconnect();reject(Error('Directional hold menu did not open'))},4000);
  observer.observe(document.body,{subtree:true,childList:true});
  button.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType,button:0,clientX:10,clientY:10}));
 }),{direction,pointerType});
 assert.ok(elapsed>=450,`${pointerType} ${direction} hold opened before 450 ms: ${elapsed}`);
 await expect(page.getByRole('menu',{name:direction+' steps',exact:true})).toBeVisible();
 await button.dispatchEvent('pointerup',{pointerType,button:0});await button.dispatchEvent('click');
 return elapsed;
}
async function wideTouchHome(browser,url,sessionId){
 const isolated=process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?await chromium.launch(launch):null;
 let context;
 try{
 context=await (isolated||browser).newContext({viewport:{width:1024,height:900},hasTouch:true,
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
  const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
  const state=()=>page.evaluate(()=>window.amplifier.getState());
  await action('session.select',{id:sessionId});
  const input=await page.evaluate(()=>({width:innerWidth,coarse:matchMedia('(pointer:coarse)').matches}));
  assert.equal(input.width,1024);assert.equal(input.coarse,true,'Use a real coarse-pointer context, not a resized mouse context');
  const home=page.locator('[data-part="header"] .a-work-home-nav');
  await expect(home).toBeVisible();await expect(home).toBeEnabled();
  await expect(home).toHaveAccessibleName('Home');
  assert.equal(await home.evaluate(node=>node.closest('[aria-label="Breadcrumb"]')),null,'Home is site navigation, not a breadcrumb');
  const visible=async()=>assert.equal(await home.isVisible(),true,'Wide-touch header Home must remain visible');
  await visible();
  const box=await home.boundingBox();assert.ok(box.width>=44&&box.height>=44);
  // The old breadcrumb-hiding regression no longer matches site Home. Retain
  // that adversarial rule, then prove the visibility oracle still detects a hide.
  const regression=await page.addStyleTag({content:'@media(pointer:coarse),(max-width:760px){#amp-one.a-approachable-shell .a-work-heading .a-work-crumb,#amp-one.a-approachable-shell .a-work-heading>.a-work-crumb+svg{display:none}}'});
  try{await visible()}
  finally{await regression.evaluate(node=>node.remove())}
  const hiddenHome=await page.addStyleTag({content:'#amp-one.a-approachable-shell .a-work-home-nav{display:none!important}'});
  try{await assert.rejects(visible,/Wide-touch header Home must remain visible/)}
  finally{await hiddenHome.evaluate(node=>node.remove())}
  await expect(home).toBeVisible();await visible();
  await home.focus();await expect(home).toBeFocused();await page.keyboard.press('Enter');
  await expect.poll(async()=>(await state()).view.workSurface).toBe('home');
  await action('session.select',{id:sessionId});await home.tap();
  await expect.poll(async()=>(await state()).view.workSurface).toBe('home');
  await action('session.select',{id:sessionId});
  const brand=page.getByRole('button',{name:'Amplifier Home',exact:true});
  assert.equal(await brand.evaluate(node=>node.tagName),'BUTTON');
  await brand.focus();await page.keyboard.press('Space');
  await expect.poll(async()=>(await state()).view.workSurface).toBe('home');
  const touchBack=page.getByRole('button',{name:'Back in navigation history',exact:true});
  await expect(touchBack).toBeEnabled();
  await expect.poll(()=>page.evaluate(()=>{
   const trail=JSON.parse(sessionStorage.getItem('amplifier.destinationHistory'));
   return trail.entries[trail.cursor]?.kind;
  })).toBe('home');
  const beforeTouch=await page.evaluate(()=>sessionStorage.getItem('amplifier.destinationHistory'));
  const openedDuringTouch=await touchBack.evaluate(button=>new Promise(resolve=>{
   let opened=false;
   const observer=new MutationObserver(()=>{opened||=!!document.querySelector('.a-destination-menu [role=menu]')});
   observer.observe(document.body,{subtree:true,childList:true});
   button.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',button:0}));
   // A bounded negative observation must span the 450 ms mouse/pen threshold.
   setTimeout(()=>{button.dispatchEvent(new PointerEvent('contextmenu',{bubbles:true,pointerType:'touch',button:2}));button.dispatchEvent(new PointerEvent('pointercancel',{bubbles:true,pointerType:'touch'}));setTimeout(()=>{observer.disconnect();resolve(opened)},0)},550);
  }));
  assert.equal(openedDuringTouch,false,'Touch hold must not open a step menu');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.destinationHistory')),beforeTouch);
  await action('view.update',{patch:{workSurface:'workspaces'}});
  await page.getByRole('button',{name:'Recent places',exact:true}).click();
  await expect(page.getByRole('menuitem').filter({hasText:'All workspaces'})).toHaveCount(1);
  await expect(page.getByRole('menuitem').filter({hasText:'All projects'})).toHaveCount(0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:path.join(out,'history-wide-touch.png')});
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'wide-touch-home.json'),JSON.stringify({passed:true,input,box,
   oldBreadcrumbSelectorHarmless:true,hiddenHomeRejected:true,restoredVisible:true,keyboardHome:true,tapHome:true,
   nativeBrandKeyboardHome:true,touchHoldExcluded:true,
   allWorkspacesLabel:true,pageErrors:errors},null,2));
 }finally{await context.close()}
 }finally{await isolated?.close()}
}
function gate(){
 let resolve;const wait=new Promise(done=>{resolve=done});
 const release=()=>{pendingGates.delete(release);resolve()};
 pendingGates.add(release);return {wait,release};
}
function stopFixture(){
 if(fixture.exitCode!==null||fixture.signalCode!==null||!fixture.pid)return Promise.resolve();
 return new Promise((resolve,reject)=>{
  let escalation,deadline,settled=false;
  const finish=error=>{
   if(settled)return;settled=true;
   clearTimeout(escalation);clearTimeout(deadline);
   fixture.off('exit',exited);fixture.off('error',failed);
   if(error)reject(error);else resolve();
  };
  const exited=()=>finish(),failed=error=>finish(error);
  const signal=value=>{try{fixture.kill(value)}catch(error){finish(error)}};
  fixture.once('exit',exited);fixture.once('error',failed);
  console.error(`Stopping owned fixture PID ${fixture.pid}`);
  escalation=setTimeout(()=>{
   if(fixture.exitCode!==null||fixture.signalCode!==null){exited();return}
   console.error(`Escalating owned fixture PID ${fixture.pid} to SIGKILL`);
   deadline=setTimeout(()=>finish(Error(`Could not confirm exit of owned fixture PID ${fixture.pid}`)),5000);
   signal('SIGKILL');
  },5000);
  signal('SIGTERM');
 });
}
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('error',error=>{clearTimeout(timer);reject(error)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});
 browser=await chromium.launch(launch);
 const context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 let delayedFrames=null;
 await page.routeWebSocket(/\/api\/events\/ws(?:\?|$)/,socket=>{
  const server=socket.connectToServer();
  server.onMessage(message=>{if(delayedFrames)delayedFrames.push({frame:JSON.parse(message),deliver:()=>socket.send(message)});else socket.send(message)});
 });
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const saved=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.destinationHistory')));
 const back=page.getByRole('button',{name:'Back in navigation history',exact:true});
 const forward=page.getByRole('button',{name:'Forward in navigation history',exact:true});
 const menu=page.getByRole('button',{name:'Recent places',exact:true});
 const recentMenu=page.getByRole('menu',{name:'Recent places',exact:true});
 const headerHome=page.locator('[data-part="header"] .a-work-home-nav');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 await expect(menu).toBeEnabled();
 const home=path.dirname((await state()).workspaceDefaults.root),folder=path.join(home,'History project');await mkdir(folder);await writeFile(path.join(folder,'preview.txt'),'Synthetic preview');
 await action('workspace.add',{path:folder,name:'History project'});
 const workspace=(await action('workspace.list',{query:'History project'})).result.items.find(row=>row.path===folder);assert.ok(workspace);
 const ids=[];for(const title of ['Alpha','Beta','Gamma'])ids.push((await action('session.create',{workspace:folder,title})).result.sessionId);
 const [a,b,c]=ids;await action('session.select',{id:a});
 await composer.fill('Unsent Alpha draft');await action('attachment.add',{sessionId:a,name:'note.txt',base64:'cHJlc2VydmU='});
 await action('session.select',{id:b});await action('session.select',{id:c});
 const baseline=await saved();await back.click();await expect.poll(async()=>(await state()).selectedSessionId).toBe(b);
 await expect.poll(async()=>(await saved()).cursor).toBe(baseline.cursor-1);
 assert.equal((await saved()).version,1);assert.equal((await saved()).recent[0].id,b,'Confirmed Back refreshes MRU');
 await expect(back).toBeEnabled();await expect(back).toBeFocused();
 await forward.click();await expect.poll(async()=>(await state()).selectedSessionId).toBe(c);
 // Selection is optimistic; keyboard focus requires the confirmed history move.
 await expect.poll(async()=>(await saved()).cursor).toBe(baseline.cursor);
 assert.equal((await saved()).recent[0].id,c,'Confirmed Forward refreshes MRU');
 await expect(menu).toBeEnabled();
 assert.equal((await saved()).entries.length,baseline.entries.length);
 // Recent places is separate from the directional menus; keyboard dismissal
 // returns to its own opener and never performs a navigation.
 await menu.focus();await page.keyboard.press('ArrowDown');await expect(recentMenu).toBeVisible();
 assert.equal(await page.locator('[role=menuitem]:focus').count(),1);
 assert.deepEqual(await menuKeys(recentMenu,'data-place-key'),(await saved()).recent.map(placeKey));
 assert.ok((await menuKeys(recentMenu,'data-visit-key')).every(key=>key===null));
 await page.keyboard.press('End');await page.keyboard.press('Home');await page.keyboard.press('Escape');await expect(menu).toBeFocused();
 // A -> B -> A remains two distinct visits, while Recent has one row per place.
 for(const id of [a,b,a,c])await action('session.select',{id});
 const repeated=await saved(),olderAlphaIndex=repeated.cursor-3;
 assert.deepEqual(repeated.entries.slice(-4).map(row=>row.id),[a,b,a,c]);
 const olderAlpha=repeated.entries[olderAlphaIndex],newerAlpha=repeated.entries[repeated.cursor-1];
 const originalAlpha=baseline.entries.findLast(row=>row.kind==='chat'&&row.id===a);
 assert.ok(originalAlpha);
 const originalAlphaIndex=repeated.entries.findIndex(row=>row.key===originalAlpha.key);
 assert.notEqual(olderAlpha.key,newerAlpha.key);
 assert.deepEqual(repeated.recent.slice(0,3).map(row=>row.id),[c,a,b]);
 assert.equal(new Set(repeated.recent.map(placeKey)).size,repeated.recent.length);
 const mouseHoldMs=await holdSteps(page,back,'Back','mouse');
 const backMenu=page.getByRole('menu',{name:'Back steps',exact:true});
 assert.deepEqual(await menuKeys(backMenu,'data-visit-key'),repeated.entries.slice(0,repeated.cursor).reverse().map(row=>row.key));
 assert.deepEqual(await saved(),repeated,'Hold release must not also execute Back or change recency');
 await page.screenshot({path:path.join(out,'history-back-repeated-visits.png')});
 await visitRow(backMenu,olderAlpha.key).click();
 await expect.poll(async()=>(await saved()).cursor).toBe(olderAlphaIndex);
 assert.deepEqual((await saved()).entries,repeated.entries);
 assert.equal((await saved()).recent[0].id,a);
 await keyboardSteps(page,back,'Back');await visitRow(backMenu,originalAlpha.key).click();
 await expect.poll(async()=>(await saved()).cursor).toBe(originalAlphaIndex);
 const atOriginalAlpha=await saved(),penHoldMs=await holdSteps(page,forward,'Forward','pen');
 const forwardMenu=page.getByRole('menu',{name:'Forward steps',exact:true});
 const futureVisits=repeated.entries.slice(originalAlphaIndex+1);
 assert.ok(futureVisits.filter(row=>row.id===a).length>=2,'Forward oracle must include repeated Alpha visits');
 assert.deepEqual(await menuKeys(forwardMenu,'data-visit-key'),futureVisits.map(row=>row.key));
 assert.deepEqual(await saved(),atOriginalAlpha,'Pen hold release must not also execute Forward');
 await page.screenshot({path:path.join(out,'history-forward-repeated-visits.png')});
 await page.keyboard.press('Escape');await expect(forward).toBeFocused();
 await keyboardSteps(page,forward,'Forward');await visitRow(forwardMenu,newerAlpha.key).click();
 await expect.poll(async()=>(await saved()).cursor).toBe(repeated.cursor-1);
 assert.equal((await state()).selectedSessionId,a,'Exact Forward visits must work even when both visits name Alpha');
 await keyboardSteps(page,back,'Back');await visitRow(backMenu,olderAlpha.key).click();
 await expect.poll(async()=>(await saved()).cursor).toBe(olderAlphaIndex);
 const beforeBranch=await saved();
 await menu.click();await expect(recentMenu).toBeVisible();
 assert.deepEqual(await menuKeys(recentMenu,'data-place-key'),beforeBranch.recent.map(placeKey));
 assert.equal((await menuKeys(recentMenu,'data-place-key')).filter(key=>key===placeKey(olderAlpha)).length,1);
 await expect(placeRow(page,olderAlpha)).toHaveAttribute('aria-current','page');
 await expect(placeRow(page,olderAlpha)).toBeDisabled();
 await page.screenshot({path:path.join(out,'history-recent-unique-places.png')});
 await placeRow(page,{kind:'chat',id:c}).click();
 await expect.poll(async()=>{
  const trail=await saved();
  return trail.cursor===beforeBranch.cursor+1&&trail.entries.length===beforeBranch.cursor+2&&
   trail.entries.at(-1)?.id===c&&!beforeBranch.entries.some(row=>row.key===trail.entries.at(-1)?.key);
 }).toBe(true);
 await expect(menu).toBeEnabled();
 const branched=await saved();
 assert.deepEqual(branched.entries.slice(0,-1),beforeBranch.entries.slice(0,beforeBranch.cursor+1));
 assert.equal(branched.cursor,beforeBranch.cursor+1);
 assert.ok(!beforeBranch.entries.some(row=>row.key===branched.entries.at(-1).key),'Recent selects a normal destination, not a previous visit key');
 assert.deepEqual(branched.recent.slice(0,3).map(row=>row.id),[c,a,b]);
 await expect(forward).toBeDisabled();
 // The destination trail consumes projected metadata, not a hidden full catalog.
 // Visit each target before renaming so its authoritative metadata is observed.
 // Renaming that selected target must not add a visit or bump MRU.
 await action('session.select',{id:a});
 const beforeRenameA=await saved();
 await action('session.rename',{id:a,title:'Shared navigation name'});
 assert.deepEqual((await saved()).entries.map(row=>row.key),beforeRenameA.entries.map(row=>row.key));
 assert.deepEqual((await saved()).recent.map(placeKey),beforeRenameA.recent.map(placeKey));
 await action('session.select',{id:b});
 const beforeRenameB=await saved();
 await action('session.rename',{id:b,title:'Shared navigation name'});
 const renamed=await saved();
 assert.deepEqual(renamed.entries.map(row=>row.key),beforeRenameB.entries.map(row=>row.key));
 assert.equal(renamed.cursor,beforeRenameB.cursor);assert.deepEqual(renamed.recent.map(placeKey),beforeRenameB.recent.map(placeKey));
 await menu.click();
 const sameNames=recentMenu.getByRole('menuitem').filter({hasText:'Shared navigation name'});
 await expect(sameNames).toHaveCount(2);
 assert.equal(new Set(await sameNames.allTextContents()).size,2,'Same-title destinations need visible identity disambiguation');
 await expect(placeRow(page,{kind:'chat',id:a})).toContainText(a);
 await expect(placeRow(page,{kind:'chat',id:b})).toContainText(b);
 await placeRow(page,{kind:'chat',id:a}).click();
 await expect.poll(async()=>(await saved()).recent[0].id).toBe(a);
 assert.equal((await state()).selectedSessionId,a);
 await action('session.rename',{id:a,title:'Alpha renamed'});
 const afterRename=await saved();
 assert.ok(afterRename.entries.filter(row=>row.id===a).every(row=>row.label==='Alpha renamed'));
 assert.equal(afterRename.recent.find(row=>row.id===a).label,'Alpha renamed');
 assert.equal(afterRename.recent[0].id,a,'Renaming the projected target is not another visit');
 await action('session.rename',{id:a,title:'Alpha'});await action('session.select',{id:b});await action('session.rename',{id:b,title:'Beta'});
 await writeFile(path.join(out,'history-menu-semantics.json'),JSON.stringify({mouseHoldMs,penHoldMs,
  repeatedVisitKeys:[olderAlpha.key,newerAlpha.key],backNearestFirst:true,forwardNearestFirst:true,
  uniqueRecent:true,normalRecentBranch:true,sameTitleIdentity:true,renameWithoutVisit:true},null,2));
 // Select the original saved Alpha visit, not a new Recent activation; all
 // draft ownership and attachments remain intact.
 await keyboardSteps(page,back,'Back');await visitRow(backMenu,originalAlpha.key).click();
 await expect.poll(async()=>{const trail=await saved();return trail.entries[trail.cursor]?.key}).toBe(originalAlpha.key);
 await expect(composer).toHaveText('Unsent Alpha draft');await expect(page.getByRole('button',{name:'Remove note.txt',exact:true})).toBeVisible();
 const alpha=await saved();await action('session.draft',{});await expect.poll(async()=>(await state()).selectedSessionId).toBeNull();
 assert.equal((await saved()).entries.length,alpha.entries.length);await back.click();await expect.poll(async()=>(await state()).selectedSessionId).toBe(a);
 await expect(composer).toHaveText('Unsent Alpha draft');
 // Home is a destination, not a visit for every view/filter or reading change.
 await headerHome.click();
 await expect.poll(async()=>(await saved()).entries.at(-1).kind).toBe('home');
 const homeTrail=await saved();
 await action('view.update',{patch:{workHomeView:'focus',workHomeFilter:'working'}});
 assert.deepEqual(await saved(),homeTrail);
 await action('session.select',{id:a});await back.click();
 await expect.poll(async()=>(await saved()).cursor).toBe(homeTrail.cursor);
 assert.equal((await state()).view.workSurface,'home');
 assert.equal((await state()).view.workHomeView,'focus');assert.equal((await state()).view.workHomeFilter,'working');
 await forward.click();await expect.poll(async()=>(await saved()).cursor).toBe(homeTrail.cursor+1);
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
 assert.equal((await state()).selectedSessionId,a);
 await expect(composer).toHaveText('Unsent Alpha draft');
 // A real canonical chat card records an accepted destination, not its command.
 const messageResponse=await page.request.post(url+'/fixture/history-message',{data:{sessionId:a}});
 assert.ok(messageResponse.ok());const {messageId}=await messageResponse.json();
 const attached=await action('outputs.attachChatReference',{sessionId:a,messageId,targetSessionId:b});
 assert.equal(attached.accepted,true);
 const card=page.locator(`[data-message-id="${messageId}"]`).getByRole('button',{name:/^Open chat.*Beta/});
 const beforeCard=await saved();await card.click();
 await expect.poll(async()=>(await saved()).entries.at(-1).id).toBe(b);
 await back.click();await expect.poll(async()=>(await saved()).cursor).toBe(beforeCard.cursor);
 await expect(card).toBeVisible();await expect(composer).toHaveText('Unsent Alpha draft');
 // A rejected card leaves its source mounted and cannot add a phantom visit.
 const cardTrail=await saved();
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='session.select'&&body.args.chatReference)await route.fulfill({status:409,contentType:'application/json',body:'{"error":"Synthetic card rejection"}'});
  else await route.continue();
 });
 await card.click();await expect(page.getByRole('alert').filter({hasText:'Synthetic card rejection'})).toBeVisible();
 assert.deepEqual(await saved(),cardTrail);assert.equal((await state()).selectedSessionId,a);
 await page.unroute('**/api/actions');
 // Browsing retains chat identity but creates a workspace destination; tabs update it.
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'chats'}});
 const workspaceVisit=await saved();await action('view.update',{patch:{workWorkspaceTab:'files'}});
 assert.equal((await saved()).entries.length,workspaceVisit.entries.length);
 await expect.poll(async()=>(await saved()).entries.at(-1).tab).toBe('files');
 await page.getByRole('button',{name:'preview.txt Open in “Alpha”',exact:true}).click();
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
 await expect.poll(async()=>{const trail=await saved();return trail.entries[trail.cursor]?.kind}).toBe('chat');
 const beforeWorkspaceBack=await state(),beforeWorkspaceTrail=await saved(),workspaceIndex=beforeWorkspaceTrail.cursor-1;
 assert.equal(beforeWorkspaceTrail.entries[workspaceIndex].kind,'workspace');
 assert.equal(beforeWorkspaceTrail.entries[workspaceIndex].id,workspace.id);
 const workspaceResponse=page.waitForResponse(response=>{
  const request=response.request();if(request.method()!=='POST'||new URL(response.url()).pathname!=='/api/actions'||
   request.headers()['x-amplifier-client']!==beforeWorkspaceBack.client.id)return false;
  const command=request.postDataJSON(),patch=command.args?.patch;
  return command.action==='view.update'&&patch?.workSurface==='workspace'&&patch.workWorkspaceId===workspace.id&&patch.workWorkspaceTab==='files';
 });
 await back.click();await expect.poll(async()=>(await state()).view.workSurface).toBe('workspace');
 const workspaceAccepted=await workspaceResponse,workspaceReceipt=await workspaceAccepted.json();
 assert.ok(workspaceAccepted.request().postDataJSON().id);assert.equal(workspaceReceipt.accepted,true);
 assert.equal(workspaceReceipt.hostInstanceId,beforeWorkspaceBack.client.hostInstanceId);
 assert.ok(Number.isSafeInteger(workspaceReceipt.stateRevision));
 // Optimistic workSurface is not a completed history visit. Keep the exact
 // reload oracle, but capture its baseline after this original Back settles.
 await expect.poll(async()=>(await saved()).cursor).toBe(workspaceIndex);
 await expect(menu).toBeEnabled();
 const serverWorkspace=await page.request.get(url+'/api/state?clientId='+encodeURIComponent(beforeWorkspaceBack.client.id));
 assert.equal(serverWorkspace.status(),200);const authoritativeWorkspace=await serverWorkspace.json();
 assert.equal(authoritativeWorkspace.client.id,beforeWorkspaceBack.client.id);
 assert.equal(authoritativeWorkspace.client.hostInstanceId,workspaceReceipt.hostInstanceId);
 assert.ok(authoritativeWorkspace.revision>=workspaceReceipt.stateRevision);
 assert.equal(authoritativeWorkspace.view.workSurface,'workspace');
 assert.equal(authoritativeWorkspace.view.workWorkspaceId,workspace.id);
 assert.equal(authoritativeWorkspace.view.workWorkspaceTab,'files');
 await writeFile(path.join(out,'workspace-back-before-reload.json'),JSON.stringify({
  commandId:workspaceAccepted.request().postDataJSON().id,receipt:workspaceReceipt,
  serverRevision:authoritativeWorkspace.revision,cursor:workspaceIndex,originalBackOnly:true
 },null,2));
 assert.equal((await state()).view.workWorkspaceTab,'files');
 // The workspace paints optimistically; capture the trail only after this
 // exact accepted Back visit has committed, not while its receipt is pending.
 const workspaceVisitKey=workspaceVisit.entries[workspaceVisit.cursor].key;
 await expect.poll(async()=>{const trail=await saved();return trail.entries[trail.cursor]?.key}).toBe(workspaceVisitKey);
 // Reload retains the exact trail but does not navigate or append on bootstrap.
 const preReload=await saved();await page.reload();await expect(menu).toBeEnabled();
 await expect.poll(saved).toEqual(preReload);
 assert.equal((await state()).view.workSurface,'workspace');
 // A failed Recent activation preserves both the trail and MRU, even though
 // the destination still exists in the chooser. It cannot fall back elsewhere.
 const preRecentReject=await saved(),recentRejectState=await state();let recentRejectRequests=0;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='session.select'&&body.args.id===a){
   recentRejectRequests++;await route.fulfill({status:404,contentType:'application/json',body:'{"error":"Synthetic Recent destination unavailable"}'});
  }else await route.continue();
 });
 await menu.click();await placeRow(page,{kind:'chat',id:a}).click();
 await expect(page.getByRole('alert').filter({hasText:'Synthetic Recent destination unavailable'})).toBeVisible();
 await expect(menu).toBeEnabled();assert.equal(recentRejectRequests,1);
 assert.deepEqual(await saved(),preRecentReject);
 const recentRejectedState=await state();
 for(const key of ['selectedSessionId','selectedWorkspaceId'])assert.equal(recentRejectedState[key],recentRejectState[key]);
 for(const key of ['workSurface','workWorkspaceId','workWorkspaceTab'])assert.equal(recentRejectedState.view[key],recentRejectState.view[key]);
 await page.unroute('**/api/actions');
 // Explicit traversal rejection leaves cursor and entries unchanged; no missing-target fallback.
 const preReject=await saved();let rejected=false;
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(!rejected&&body?.action==='session.select'){rejected=true;await route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:'Synthetic destination unavailable'})})}else await route.continue()});
 await back.click();await expect(page.getByRole('alert').filter({hasText:'Synthetic destination unavailable'})).toBeVisible();
 assert.deepEqual(await saved(),preReject);await page.unroute('**/api/actions');
 // A held selection cannot beat newer browsing: the existing shared intent queue owns order.
 await action('session.select',{id:b});await action('session.select',{id:c});let entered=false;
 const selectionGate=gate();
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='session.select'){entered=true;await selectionGate.wait}await route.continue()});
 await back.click();await expect.poll(()=>entered).toBe(true);
 const newer=action('view.update',{patch:{workSurface:'chats'}});selectionGate.release();await newer;await page.unroute('**/api/actions');
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chats');
 await expect.poll(async()=>(await saved()).entries.at(-1).kind).toBe('chats');
 assert.equal((await saved()).entries.at(-2).id,b,'Back must commit before the newer browse replaces Forward');
 // Optimistic selections are not visits, even during unrelated state publication.
 await action('session.select',{id:c});const beforeOptimistic=await saved(),optimisticGate=gate();let optimisticHeld=false;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='session.select'&&body.args.id===b){optimisticHeld=true;await optimisticGate.wait;await route.fulfill({status:409,contentType:'application/json',body:'{"error":"Synthetic selection rejection"}'})}
  else await route.continue();
 });
 await page.evaluate(id=>{window.optimisticSelection=window.amplifier.dispatch('session.select',{id}).catch(error=>({error:error.message}))},b);
 await expect.poll(()=>optimisticHeld).toBe(true);
 // Publish the unrelated change through the authenticated host boundary. Do not
 // make this history assertion depend on settlement of another UI dispatch while
 // selection is deliberately held. Join its exact receipt to observed state.
 const beforePin=await state(),pinCommandId=crypto.randomUUID();
 const pinResponse=await page.request.post(url+'/api/actions',{headers:{
  'X-Amplifier-Client':beforePin.client.id,'X-Amplifier-State-Transport':'delta-v1'},
  data:{id:pinCommandId,action:'session.pin',args:{id:c,pinned:true}}});
 assert.ok(pinResponse.ok());const pinReceipt=await pinResponse.json();
 assert.equal(pinReceipt.accepted,true);assert.equal(pinReceipt.hostInstanceId,beforePin.client.hostInstanceId);
 assert.ok(Number.isSafeInteger(pinReceipt.stateRevision)&&pinReceipt.stateRevision>beforePin.revision);
 await expect.poll(async()=>{const s=await state();return s.client.id===beforePin.client.id&&
  s.client.hostInstanceId===pinReceipt.hostInstanceId&&s.revision>=pinReceipt.stateRevision&&s.navigationPending===true;}).toBe(true);
 await writeFile(path.join(out,'optimistic-publication-boundary.json'),JSON.stringify({
  commandId:pinCommandId,action:'session.pin',clientId:beforePin.client.id,
  hostInstanceId:pinReceipt.hostInstanceId,receiptRevision:pinReceipt.stateRevision,
  observedRevision:(await state()).revision,selectionStillHeld:optimisticHeld,
  before:beforeOptimistic,after:await saved()},null,2));
 assert.deepEqual(await saved(),beforeOptimistic,'Unrelated authoritative state must not record an optimistic selection');
 optimisticGate.release();await page.evaluate(()=>window.optimisticSelection);await page.unroute('**/api/actions');
 assert.deepEqual(await saved(),beforeOptimistic);
 // Back alone supersedes an older archive browse; Undo/Dismiss must not mask this race.
 await action('session.select',{id:b});await action('session.select',{id:c});
 const beforeArchive=await saved(),archiveGate=gate();let archiveHeld=false,workspaceRequests=0;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='view.update'&&body.args.patch?.workSurface==='workspace')workspaceRequests++;
  if(body?.action==='view.update'&&body.args.patch?.workSurface==='chat'){archiveHeld=true;await archiveGate.wait}
  await route.continue();
 });
 await page.evaluate(()=>{window.archiveQueueBarrier=window.amplifier.dispatch('view.update',{patch:{workSurface:'chat'}})});
 await expect.poll(()=>archiveHeld).toBe(true);
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Archive',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 await expect(page.locator('#amp-one')).toHaveAttribute('data-work-surface','workspace');
 await back.click();archiveGate.release();await page.evaluate(()=>window.archiveQueueBarrier);
 await expect.poll(async()=>(await saved()).cursor).toBe(beforeArchive.cursor-1);
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(b);
 assert.equal(workspaceRequests,0);assert.deepEqual((await saved()).entries,beforeArchive.entries);
 await page.unroute('**/api/actions');
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal((await state()).selectedSessionId,b);
 // Raw archive changes organization only, not selection, workspace or visits.
 const rawState=await state(),rawTrail=await saved(),rawArchive=await action('session.archive',{id:b});
 assert.equal(rawArchive.accepted,true);assert.equal(rawArchive.result.id,b);assert.equal(rawArchive.result.archived,true);
 assert.equal((await state()).selectedSessionId,rawState.selectedSessionId);
 assert.equal((await state()).selectedWorkspaceId,rawState.selectedWorkspaceId);
 assert.equal((await state()).view.workSurface,rawState.view.workSurface);assert.deepEqual(await saved(),rawTrail);
 await action('session.restore',{id:b});
 // Dirty Canvas must reject actual history traversal, keeping the exact mounted editor.
 await action('session.select',{id:a});await action('session.select',{id:b});
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'History dirty guard',content:'# Retained source'});
 const manifest={id:'example.history-dirty',label:'History unsaved editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"History unsaved input",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 assert.equal((await action('shell.packages.validate',{digest})).result.status,'passed');
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const editor=page.getByLabel('History unsaved input');await editor.fill('UNSAVED HISTORY EDIT');
 const inspect=async()=>(await action('canvas.views.inspect')).result.views.find(row=>row.viewId==='primary');
 await expect.poll(async()=>(await inspect()).dirty).toBe(true);
 const mount=await editor.getAttribute('data-mount'),dirtyTrail=await saved();
 await back.click();await expect(page.getByRole('alert').filter({hasText:/viewer edit/})).toBeVisible();
 assert.equal((await state()).selectedSessionId,b);assert.deepEqual(await saved(),dirtyTrail);
 await expect(editor).toHaveValue('UNSAVED HISTORY EDIT');assert.equal(await editor.getAttribute('data-mount'),mount);
 await menu.click();await expect(placeRow(page,{kind:'chat',id:a})).toBeEnabled();await placeRow(page,{kind:'chat',id:a}).click();
 await expect(recentMenu).toHaveCount(0);await expect(menu).toBeEnabled();
 assert.equal((await state()).selectedSessionId,b);assert.deepEqual(await saved(),dirtyTrail,'Recent must honor the same dirty Canvas guard');
 await expect(editor).toHaveValue('UNSAVED HISTORY EDIT');assert.equal(await editor.getAttribute('data-mount'),mount);
 await action('view.update',{patch:{workSurface:'workspaces'}});await expect(editor).toBeHidden();
 const browsedDirtyTrail=await saved();
 await back.click();await expect(editor).toBeVisible();await expect(editor).toHaveValue('UNSAVED HISTORY EDIT');
 await expect.poll(async()=>(await saved()).cursor).toBe(dirtyTrail.cursor);
 assert.deepEqual((await saved()).entries,browsedDirtyTrail.entries,'Returning must keep the workspace visit available through Forward');
 assert.equal(await editor.getAttribute('data-mount'),mount);
 const recovering=await inspect();
 await action('canvas.views.recover',Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,recovering[key]])));
 await action('canvas.close');
 // Settings in Canvas leaves the header usable; cancelling Back preserves private edits.
 await action('session.select',{id:a});await action('session.select',{id:b});
 settingsDiagnostic=observeSettingsClose(page);await settingsDiagnostic.start();
 await action('canvas.show',{kind:'settings'});
 await action('view.update',{patch:{settingsExpanded:['providers'],settingsCanvas:true,panel:null}});
 const settings=page.locator('.a-settings-experience[data-settings-page="providers"]');
 await expect(settings).toBeVisible();
 // This fixture has a saved provider. Settle its initial hydration before
 // choosing a blank editor, so a fill/setup race cannot masquerade as lost
 // private input after cancelling history navigation.
 await expect.poll(async()=>{
  const s=await state(),setup=s.setup||{},draft=s.view.providerEditor||{};
  const provider=(setup.providers||[]).find(row=>row.enabled!==false&&row.id===draft.id);
  return setup.operations?.['providers.list:']?.phase==='ready'&&!!setup.providersLoadedAt&&
   !!provider&&draft.selectionKey===provider.id&&draft.config===JSON.stringify(provider.config||{},null,2);
 }).toBe(true);
 await expect(settings.getByRole('button',{name:'Refresh providers',exact:true})).toBeEnabled();
 await settings.getByRole('button',{name:'Add provider',exact:true}).click();
 await expect.poll(async()=>{
  const draft=(await state()).view.providerEditor||{};
  return {id:draft.id,selectionKey:draft.selectionKey,module:draft.module,config:draft.config,detailOpen:draft.detailOpen};
 }).toEqual({id:'',selectionKey:'',module:'',config:'{}',detailOpen:true});
 await settings.getByRole('button',{name:'Show advanced configuration',exact:true}).click();
 const privateEdit=settings.locator('[id$="provider-config"]');
 await expect(privateEdit).toHaveValue('{}');
 await privateEdit.fill('{"unsaved-history-input":');
 await expect(privateEdit,'Establish exact private input before Back; do not accept a prefixed fill').toHaveValue('{"unsaved-history-input":');
 const settingsTrail=await saved();await back.click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 assert.equal((await state()).selectedSessionId,b);assert.deepEqual(await saved(),settingsTrail);
 await expect(privateEdit).toHaveValue('{"unsaved-history-input":');
 await menu.click();await placeRow(page,{kind:'chat',id:a}).click();
 await expect(page.getByRole('alertdialog',{name:'Unsaved settings changes',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 assert.equal((await state()).selectedSessionId,b);assert.deepEqual(await saved(),settingsTrail,'Cancelling Recent must retain private Settings edits and MRU');
 await expect(privateEdit).toHaveValue('{"unsaved-history-input":');
 // Discard resolves the guard, not the asynchronous tab-close command. Observe
 // that original command and its authoritative successor before hiding Canvas.
 const beforeClose=await state(),closingId=beforeClose.canvas.id;
 const successor=beforeClose.canvasArtifacts.filter(row=>row.sessionId===b&&
  row.workspaceId===beforeClose.selectedWorkspaceId&&row.tabOpen&&row.id!==closingId).at(-1);
 assert.equal(beforeClose.selectedSessionId,b);assert.equal(beforeClose.canvas.kind,'settings');
 assert.ok(successor,'The earlier retained Canvas tab must remain');
 await settingsDiagnostic.bind(beforeClose,closingId,successor.id);
 const closedResponse=page.waitForResponse(response=>{
  const request=response.request();
  if(request.method()!=='POST'||new URL(response.url()).pathname!=='/api/actions'||
   request.headers()['x-amplifier-client']!==beforeClose.client.id)return false;
  const command=request.postDataJSON();return command.action==='canvas.tabClose'&&command.args.id===closingId;
 });
 // Canvas now exposes close through its all-documents list and Delete on tabs.
 // Use the actual keyboard control, keeping the exact close receipt/guard oracle.
 await page.getByRole('tab',{name:'Settings',exact:true}).press('Delete');
 await page.getByRole('button',{name:'Discard changes',exact:true}).click();
 const closeResponse=await closedResponse,closeReceipt=await closeResponse.json();
 const closeCommandId=closeResponse.request().postDataJSON().id;
 settingsDiagnostic.closeReceipt(closeCommandId,closeReceipt);
 assert.ok(closeCommandId);assert.ok(closeResponse.ok());assert.equal(closeReceipt.accepted,true);
 assert.equal(closeReceipt.hostInstanceId,beforeClose.client.hostInstanceId);
 assert.ok(Number.isSafeInteger(closeReceipt.stateRevision));
 assert.equal(closeReceipt.stateRevision,closeReceipt.revision);
 assert.ok(closeReceipt.stateRevision>beforeClose.revision);
 await expect.poll(async()=>{
  const s=await state();
  return s.client.id===beforeClose.client.id&&s.client.hostInstanceId===closeReceipt.hostInstanceId&&
   s.revision>=closeReceipt.stateRevision&&s.selectedSessionId===b&&
   s.selectedWorkspaceId===beforeClose.selectedWorkspaceId&&!s.navigationPending&&!s.canvas.visibilityPending&&
   s.canvas.id===successor.id&&s.canvas.open===true&&
   s.canvasArtifacts.find(row=>row.id===closingId)?.tabOpen===false;
 }).toBe(true);
 await settingsDiagnostic.mark('close-state-poll-completed');
 await expect(page.getByRole('alertdialog',{name:'Unsaved settings changes',exact:true})).toHaveCount(0);
 await expect(page.getByRole('tab',{name:'Settings',exact:true})).toHaveCount(0);
 await expect(settings).toHaveCount(0);assert.deepEqual(await saved(),settingsTrail);
 await settingsDiagnostic.mark('rendered-close-assertions-completed');
 await writeFile(path.join(out,'settings-close-boundary.json'),JSON.stringify({
  commandId:closeCommandId,closedId:closingId,successorId:successor.id,
  clientId:beforeClose.client.id,hostInstanceId:closeReceipt.hostInstanceId,
  beforeRevision:beforeClose.revision,receiptRevision:closeReceipt.stateRevision,
  observedRevision:(await state()).revision,originalClickOnly:true,historyUnchanged:true,
 },null,2));
 // A deliberate fresh reopen is valid; suppressing every Settings navigation
 // would hide the measured close race while breaking this separate user intent.
 assert.equal((await action('canvas.select',{id:closingId})).accepted,true);
 await expect.poll(async()=>(await state()).canvas.id).toBe(closingId);
 await expect(page.getByRole('tab',{name:'Settings',exact:true})).toBeVisible();
 const reopened=await state();assert.equal(reopened.canvas.kind,'settings');
 assert.equal(reopened.canvasArtifacts.find(row=>row.id===closingId).tabOpen,true);
 assert.equal((await action('canvas.tabClose',{id:closingId})).accepted,true);
 await expect(page.getByRole('tab',{name:'Settings',exact:true})).toHaveCount(0);
 await expect.poll(async()=>(await state()).canvas.id).toBe(successor.id);
 assert.deepEqual(await saved(),settingsTrail);
 await action('canvas.visibility',{open:false});
 // Workspace settings has its own private editor registry. Local navigation
 // must consult it before the host changes the route or history branches.
 await headerHome.click();
 const providerClient=(await state()).client;
 providerDiagnostic=observeSettingsProviderPreservation(page,{workspaceId:workspace.id,
  clientId:providerClient.id,hostInstanceId:providerClient.hostInstanceId,gate:gate()});
 await providerDiagnostic.start();
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'details',workWorkspaceSettings:'providers'}});
 await providerDiagnostic.waitForHeld(1);
 await action('session.select',{id:b});await back.click();
 await expect.poll(async()=>(await state()).view.workSurface).toBe('workspace');
 const workspaceSettings=page.getByRole('region',{name:'Workspace configuration'});
 await expect(workspaceSettings).toBeVisible();
 // The provider-read queue serializes the returning mount behind this first
 // held read. Do not wait for that queued request before releasing its predecessor.
 // Neither can pre-hydrate this editor before the private edit and Keep decision.
 await workspaceSettings.getByRole('button',{name:'Add provider',exact:true}).click();
 await workspaceSettings.getByRole('button',{name:'Show advanced configuration',exact:true}).click();
 const workspaceEdit=workspaceSettings.locator('[id$="provider-config"]');
 await workspaceEdit.fill(privateWorkspaceText);
 await providerDiagnostic.bind();
 const workspaceDirtyTrail=await saved(),workspaceCommands=[];
 assert.ok(workspaceDirtyTrail.cursor<workspaceDirtyTrail.entries.length-1,'Retain a real Forward branch');
 const recordWorkspaceCommand=request=>{
  if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')workspaceCommands.push(request.postDataJSON());
 };
 page.on('request',recordWorkspaceCommand);
 let cancelledWorkspaceRoutes=0;
 for(const trigger of [
  ()=>back.click(),()=>forward.click(),
  async()=>{await menu.click();await placeRow(page,{kind:'home'}).click()},
  ()=>headerHome.click(),()=>page.getByRole('button',{name:'Amplifier Home',exact:true}).click(),
  ()=>page.getByRole('group',{name:'Workspace view',exact:true}).getByRole('button',{name:'Files',exact:true}).click(),
 ]){
  await providerDiagnostic.mark(cancelledWorkspaceRoutes===0?'before-first-back':'before-trigger-'+cancelledWorkspaceRoutes);
  await trigger();
  const prompt=page.getByRole('alertdialog',{name:'Unsaved settings changes',exact:true});
  await expect(prompt).toBeVisible();
  assert.equal((await state()).view.workSurface,'workspace','Prompt must precede shared navigation');
  assert.deepEqual(await saved(),workspaceDirtyTrail,'Prompt must precede trail/MRU mutation');
  await providerDiagnostic.mark('prompt-'+cancelledWorkspaceRoutes);
  await prompt.getByRole('button',{name:'Keep settings open',exact:true}).click();
  await expect(prompt).toHaveCount(0);
  await providerDiagnostic.mark('after-keep-'+cancelledWorkspaceRoutes);
  if(cancelledWorkspaceRoutes===0)await providerDiagnostic.releaseAndVerify();
  await expect(workspaceEdit).toHaveValue('{"unsaved-workspace-navigation":');
  assert.deepEqual(await saved(),workspaceDirtyTrail,'Cancellation retains visit keys/cursor/Forward/MRU');
  cancelledWorkspaceRoutes++;
 }
 assert.equal(workspaceCommands.some(command=>['session.select','session.draft','workspace.select'].includes(command.action)||command.action==='view.update'&&['workSurface','workWorkspaceTab'].some(key=>Object.hasOwn(command.args.patch||{},key))),false,'Cancelled local navigation never reached the host');
 await providerDiagnostic.save('six-cancellations-before-deliberate-discard',path.join(out,'settings-provider-preservation.json'));
 await menu.click();await placeRow(page,{kind:'home'}).click();
 await page.getByRole('alertdialog').getByRole('button',{name:'Discard changes',exact:true}).click();
 await expect.poll(async()=>(await state()).view.workSurface).toBe('home');
 await expect.poll(async()=>(await saved()).recent[0].kind).toBe('home');
 page.off('request',recordWorkspaceCommand);
 await writeFile(path.join(out,'workspace-settings-history-guard.json'),JSON.stringify({cancelledRoutes:6,sharedRouteHeld:true,historyAndForwardHeld:true,discardNavigated:true,privateDraftSent:workspaceCommands.some(command=>JSON.stringify(command).includes('unsaved-workspace-navigation'))},null,2));
 assert.equal(workspaceCommands.some(command=>JSON.stringify(command).includes('unsaved-workspace-navigation')),false);
 await action('session.select',{id:b});
 // Keep the existing chrome spacing and New chat geometry; do not mistake
 // deduplication for permission to redesign the sidebar or shrink touch targets.
 const layoutMeasurements=[],longTitle='Beta — a deliberately long chat title for reviewing navigation without losing the current working context',
  longWorkspace='History project — a deliberately long workspace name that must not consume the current chat title';
 const originalScheme=await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme);
 const scheme=async value=>{
  await page.evaluate(async scheme=>{
   const s=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
   const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:s.revision,
    composition:{...s.effectiveComposition,presentation:{...s.effectiveComposition.presentation,scheme}}});
   if(!prepared.accepted)throw Error('Could not prepare test appearance');
   const applied=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:s.revision,changeId:prepared.result.id});
   if(!applied.accepted)throw Error('Could not apply test appearance');
  },value);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme)).toBe(value);
  if(value==='light'||value==='dark')await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',value);
 };
 const newChatGeometry=async(width,screenshot)=>{
  await action('view.update',{patch:{navExpanded:true,navPinned:width>900}});
  const newChat=page.locator('#workspace-navigation').getByRole('button',{name:'New chat',exact:true});
  await expect(newChat).toBeVisible();await expect(newChat).toHaveAttribute('data-action','session.draft');
  const measurement=await newChat.evaluate(button=>{
   const style=getComputedStyle(button),box=button.getBoundingClientRect(),rail=button.closest('.a-nav-rail').getBoundingClientRect(),
    modes=button.previousElementSibling,icon=button.querySelector('svg').getBoundingClientRect(),round=n=>Math.round(n*100)/100;
   return {text:button.textContent.trim(),modesImmediatelyBefore:modes.classList.contains('a-sidebar-modes'),
    box:{x:round(box.x-rail.x),y:round(box.y-rail.y),width:round(box.width),height:round(box.height)},
    afterModes:round(box.y-modes.getBoundingClientRect().bottom),icon:{width:round(icon.width),height:round(icon.height)},
    style:Object.fromEntries(['minHeight','paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginBottom','borderRadius','gap','fontSize','lineHeight'].map(key=>[key,style[key]]))};
  });
  assert.equal(measurement.text,'New chat');assert.equal(measurement.modesImmediatelyBefore,true);
  // Existing main's authored dimensions, not measurements inferred from the
  // proposed mockup. The full measured box is also compared across conditions.
  assert.equal(measurement.style.minHeight,width<=760?'44px':'38px');
  assert.equal(measurement.style.borderRadius,'8px');
  assert.equal(measurement.style.marginTop,'4px');assert.equal(measurement.style.marginBottom,'4px');
  assert.equal(measurement.style.paddingTop,'9px');assert.equal(measurement.style.paddingBottom,'9px');
  assert.equal(measurement.style.paddingLeft,'10px');assert.equal(measurement.style.paddingRight,'10px');
  assert.equal(measurement.style.gap,'12px');assert.deepEqual(measurement.icon,{width:18,height:18});
  assert.ok(measurement.box.height>=(width<=760?44:38));
  await expect(page.locator('#workspace-navigation .a-sidebar-home')).toHaveCount(1);
  await expect(page.locator('#workspace-navigation .a-sidebar-home')).toBeVisible();
  if(screenshot)await page.screenshot({path:path.join(out,screenshot)});
  await action('view.update',{patch:{navExpanded:false,navPinned:false}});
  return measurement;
 };
 for(const width of [1440,390,360]){
  await page.setViewportSize({width,height:900});
  await action('session.rename',{id:b,title:'Beta'});await action('workspace.rename',{id:workspace.id,name:'History project'});
  await scheme('light');
  const shortNewChat=await newChatGeometry(width);
  await action('session.rename',{id:b,title:longTitle});await action('workspace.rename',{id:workspace.id,name:longWorkspace});
  for(const appearance of ['light','dark']){
   await scheme(appearance);
   await expect(headerHome).toBeVisible();await expect(headerHome).toHaveAccessibleName('Home');
   await expect(page.getByRole('navigation',{name:'Breadcrumb'}).getByRole('button',{name:'Home',exact:true})).toHaveCount(0);
   const title=page.locator('[data-part="header"] .a-work-heading>strong');
   await expect(title).toHaveText(longTitle);await expect(title).toHaveAttribute('title',longTitle);
   const homeBox=await headerHome.boundingBox(),heading=await title.boundingBox(),backBox=await back.boundingBox();
   assert.ok(heading.width>=100,'History controls must leave a readable context title');
   assert.ok(Math.abs((homeBox.y+homeBox.height/2)-(backBox.y+backBox.height/2))<1,'Home belongs in the top navigation row, not the context row');
   const chrome=await page.locator('[data-part="header"]').evaluate(header=>{
    const style=getComputedStyle(header);
    return {gap:style.gap,padding:style.padding,buttons:[...header.querySelectorAll('button')].filter(button=>button.getClientRects().length).map(button=>{
     const r=button.getBoundingClientRect();return {label:button.getAttribute('aria-label')||button.textContent.trim(),x:r.x,y:r.y,width:r.width,height:r.height};
    })};
   });
   assert.equal(chrome.gap,width<=760?'4px':'14px');assert.equal(chrome.padding,width<=760?'6px 8px':'7px 16px');
   for(let i=0;i<chrome.buttons.length;i++)for(const other of chrome.buttons.slice(i+1)){
    const first=chrome.buttons[i],overlapX=Math.min(first.x+first.width,other.x+other.width)-Math.max(first.x,other.x),
     overlapY=Math.min(first.y+first.height,other.y+other.height)-Math.max(first.y,other.y);
    assert.ok(overlapX<=1||overlapY<=1,`Header controls overlap: ${first.label} / ${other.label}`);
   }
   if(width<=390){
    for(const control of [headerHome,back,forward,menu]){const box=await control.boundingBox();assert.ok(box.width>=44&&box.height>=44)}
    assert.ok(heading.y>=homeBox.y+homeBox.height-1,'Narrow context title stays below top-row site navigation');
   }else{
    const brand=page.getByRole('button',{name:'Amplifier Home',exact:true});await expect(brand).toBeVisible();
    assert.equal(await brand.evaluate(node=>node.tagName),'BUTTON','Brand Home must be a native keyboard control');
   }
   await menu.click();await expect(recentMenu).toBeVisible();
   const popup=await page.locator('.a-destination-menu').boundingBox(),header=await page.locator('[data-part="header"]').boundingBox();
   assert.ok(popup.x>=0&&popup.x+popup.width<=width,'Recent places must fit the viewport');
   if(width<=390)assert.ok(popup.y>=header.y+header.height-1,'Menu must fit below the complete narrow header');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.screenshot({path:path.join(out,appearance==='light'?`history-${width}.png`:`history-${width}-dark.png`)});
   await page.keyboard.press('Escape');
   const longNewChat=await newChatGeometry(width,`history-sidebar-${width}-${appearance}.png`);
   assert.deepEqual(longNewChat,shortNewChat,'Long names and color scheme must not restyle or reposition New chat');
   layoutMeasurements.push({width,appearance,home:homeBox,heading,popup,chrome,newChat:longNewChat});
  }
 }
 await action('session.rename',{id:b,title:'Beta'});await action('workspace.rename',{id:workspace.id,name:'History project'});
 if(originalScheme)await scheme(originalScheme);
 await writeFile(path.join(out,'history-layout-measurements.json'),JSON.stringify({schema:1,
  baseline:'Existing authored New chat dimensions and same-run short-title geometry; not a pre-change screenshot comparison',
  conditions:layoutMeasurements},null,2));
 // The separate mobile Canvas overlay close is deliberately retained by Scope A.
 await expect(page.locator('[data-part="header"]').getByRole('button',{name:'Back to chat',exact:true})).toHaveCount(0);
 const working=await page.request.post(url+'/fixture/activity',{data:{sessionId:b,status:'working'}});
 assert.ok(working.ok());
 await action('view.update',{patch:{workSurface:'chats'}});
 await expect(page.locator('[data-part="work-browser"]')).toBeVisible();
 await expect(page.locator('[data-part="header"]').getByRole('button',{name:/^(Back|Back to chat)$/})).toHaveCount(0);
 await expect(page.locator('.a-work-live-controls').getByRole('button',{name:'Back to chat',exact:true})).toHaveCount(0);
 await expect(page.locator('.a-work-live-controls .a-call-strip[role="status"]')).toHaveCount(0);
 await expect(page.locator('[data-part="work-browser"]').getByRole('button',{name:'Back to chat',exact:true})).toHaveCount(0);
 const captured=await state();assert.equal(Object.hasOwn(captured,'destinationHistory'),false);
 // An accepted receipt alone cannot confirm a traversal. Withhold actual
 // stream frames and return a deliberately stale recovery snapshot; the
 // existing dispatch owner must reject confirmation without moving its cursor.
 const unconfirmedTrail=await saved(),staleFrame=await state();delayedFrames=[];
 const [expectedAction,expectedArgs]=destinationCommand(unconfirmedTrail.entries[unconfirmedTrail.cursor-1]);
 let recoveryRequests=0;
 const recoveryRoute=/\/api\/state(?:\?|$)/;
 await page.route(recoveryRoute,route=>{
  assert.equal(route.request().headers()['x-amplifier-client'],staleFrame.client.id);recoveryRequests++;
  return route.fulfill({json:staleFrame});
 });
 const traversalReceipt=page.waitForResponse(response=>response.request().method()==='POST'&&
  new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action===expectedAction&&
  JSON.stringify(response.request().postDataJSON()?.args)===JSON.stringify(expectedArgs)&&
  response.request().headers()['x-amplifier-client']===staleFrame.client.id);
 await back.click();
 const traversalResponse=await traversalReceipt,acceptedTraversal=await traversalResponse.json();
 assert.equal(acceptedTraversal.accepted,true);assert.equal(acceptedTraversal.hostInstanceId,staleFrame.client.hostInstanceId);
 assert.ok(acceptedTraversal.stateRevision>staleFrame.revision);
 await expect(back).toBeDisabled();assert.deepEqual(await saved(),unconfirmedTrail);
 await expect(page.getByRole('alert').filter({hasText:'Navigation was not confirmed'})).toBeVisible();
 assert.deepEqual(await saved(),unconfirmedTrail);assert.equal(recoveryRequests,1);
 assert.ok(delayedFrames.some(({frame})=>frame.data?.revision>=acceptedTraversal.stateRevision),'The confirming state frame was actually held');
 const delayed=delayedFrames;delayedFrames=null;await page.unroute(recoveryRoute);for(const {deliver} of delayed)deliver();
 await expect.poll(async()=>(await state()).revision).toBeGreaterThanOrEqual(acceptedTraversal.stateRevision);
 await writeFile(path.join(out,'receipt-without-frame.json'),JSON.stringify({accepted:true,commandId:traversalResponse.request().postDataJSON().id,
  receiptRevision:acceptedTraversal.stateRevision,staleRevision:staleFrame.revision,recoveryRequests,delayedFrames:delayed.length,
  historyUnchangedBeforeFrame:true,confirmationRejected:true},null,2));
 // Restore this test's original destination for the independent touch-client
 // assertion; no failed input, file open or other effect is replayed.
 await action('session.select',{id:captured.selectedSessionId});
 await action('view.update',{patch:{workSurface:captured.view.workSurface}});
 const originalTrail=await saved();await wideTouchHome(browser,url,b);
 assert.equal((await state()).selectedSessionId,captured.selectedSessionId);
 assert.deepEqual(await saved(),originalTrail,'A separate touch client must not change this tab history');
 const runtime=await (await page.request.get(url+'/fixture')).json();
 assert.deepEqual(runtime.started,[]);assert.deepEqual(runtime.sent,[]);
 const serialized=JSON.stringify(await saved());assert.doesNotMatch(serialized,/Unsent Alpha draft|Synthetic preview|cHJlc2VydmU=/);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',checks:['confirmed traversal recency','repeated directional visits and unique Recent places','normal Recent forward branching','same-title and renamed identities','draft and attachment retention','detached New chat','Home filters','canonical card acceptance and rejection','workspace tabs','file preview','trail and Recent reload','traversal and Recent rejection','ordered navigation','optimistic isolation','Back supersedes archive','raw archive unchanged','dirty Canvas and Settings for both menus','keyboard and mouse/pen hold','1440/390/360 light/dark long-name layout','unchanged New chat dimensions','1024 coarse-pointer Home, native brand, touch-hold exclusion and hide negative','All workspaces label','no runtime start or input'],pageErrors:errors}));
}catch(error){scenarioFailure=error;throw error}finally{
 if(providerDiagnostic)try{await providerDiagnostic.save(scenarioFailure?'failure-before-teardown':'completed-before-teardown',path.join(out,'settings-provider-preservation.json'))}
 catch(error){console.error('Provider preservation diagnostic retention failed:',error.name)}
 if(settingsDiagnostic)try{await settingsDiagnostic.save(scenarioFailure?'failure-before-teardown':'completed-before-teardown')}
 catch(error){console.error('Settings diagnostic retention failed:',error.name)}
 for(const release of pendingGates)release();
 // Start fixture termination even if browser cleanup itself stalls. Keep the
 // original assertion and a separate cleanup failure instead of replacing it.
 const cleanup=await Promise.allSettled([Promise.resolve().then(()=>browser?.close()),stopFixture()]);
 const failures=cleanup.filter(row=>row.status==='rejected').map(row=>row.reason);
 if(failures.length){
  if(scenarioFailure)for(const error of failures)console.error('Cleanup also failed:',error);
  else throw new AggregateError(failures,'Owned browser fixture cleanup failed');
 }
}
