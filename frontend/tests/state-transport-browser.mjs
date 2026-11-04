import {showFixtureConversation} from './browser-chat-pages.mjs';
// Production assets, real HTTP/SSE and a disposable Spark-sized catalog.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import {availableParallelism,loadavg} from 'node:os';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const evidencePath=process.env.AMPLIFIER_PERF_EVIDENCE||root+'output/state-transport-browser/evidence.json';
const tracePath=evidencePath.replace(/\.json$/,'')+'.trace.zip';
const errors=[],responses=[],catalogResponses=[],frames=[],initialFrames=[],paint=[],pending=new Map();
const evidence={status:'running',stage:'fixture',thresholdMs:250,paint,diagnostics:[],rendererUnits:'Seconds except LayoutCount and RecalcStyleCount',errors,modelCalls:0,
 runner:{platform:process.platform,arch:process.arch,node:process.version,parallelism:availableParallelism(),ci:process.env.CI==='true',loadAverage:loadavg()}};
const persist=async()=>{await mkdir(dirname(evidencePath),{recursive:true});await writeFile(evidencePath,JSON.stringify(evidence,null,2)+'\n')};
const recordFrame=(kind,bytes)=>{if(measured)frames.push({kind,bytes});else if(!evidence.initialCoverageSettled){initialFrameCount++;initialMaxBytes=Math.max(initialMaxBytes,bytes);if(initialFrames.length<256)initialFrames.push({kind,bytes})}};
const pendingActions=()=>[...pending.values()].slice(0,32);
// Compact transport omits state, not an explicitly requested domain body
// (docs/browser-state-transport.md). This fixture opens seven bundled thumbnails.
// Their measured maximum at c6e04df is 142,211 bytes including JSON escaping;
// 160 KB bounds each complete stylesheet, not arbitrary reads or mutations.
function assertActionResponse(row){
 assert.equal(row.hasState,false,`${row.action} must not carry a state snapshot`);
 if(row.action!=='theme.read'){assert.ok(row.bytes<16_000,JSON.stringify(row));return;}
 assert.equal(row.accepted,true);assert.equal(row.resultId,row.requestId);
 assert.equal(row.source,'built-in');assert.ok(row.cssBytes>0);
 assert.match(row.catalogFingerprint||'',/^[a-f0-9]{64}$/);
 assert.equal(row.fingerprint,row.catalogFingerprint,'Read matches the independently delivered catalog');
 assert.equal(row.fingerprint,row.cssSha256,'The complete requested CSS matches its fingerprint');
 assert.ok(row.envelopeBytes<16_000,JSON.stringify(row));
 assert.ok(row.bytes<160_000,JSON.stringify(row));
}
async function captureResponse(request){
 const response=await request.response();
 assert.ok(response,`No response for ${request.postDataJSON().action}`);
 return {request:request.postDataJSON(),body:await response.body()};
}
function actionResponse({request,body},catalog){
 const value=JSON.parse(body),{action,args}=request;
 const row={action,bytes:body.length,hasState:Object.hasOwn(value,'state')};
 if(action==='theme.read'){
  const {css,...metadata}=value.result||{};
  Object.assign(row,{accepted:value.accepted,requestId:args.id,resultId:metadata.id,source:metadata.source,
   fingerprint:metadata.fingerprint,catalogFingerprint:catalog.get(args.id),cssBytes:typeof css==='string'?Buffer.byteLength(css):0,
   cssSha256:typeof css==='string'?createHash('sha256').update(css).digest('hex'):null,
   envelopeBytes:Buffer.byteLength(JSON.stringify({...value,result:metadata}))});
 }
 return row;
}
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/active_client_performance_server.py'],{stdio:['ignore','pipe','inherit'],env:{...process.env,AMPLIFIER_TRANSPORT_FIXTURE:'1'}});
let browser,context,page,cdp,tracing=false,failed=false,measured=false,heldPrepares=0,persistenceReleased=false,initialFrameCount=0,initialMaxBytes=0;
let releasePersistence;const persistenceGate=new Promise(resolve=>{releasePersistence=()=>{persistenceReleased=true;resolve()}});
const metricNames=['Timestamp','TaskDuration','ScriptDuration','LayoutDuration','RecalcStyleDuration','LayoutCount','RecalcStyleCount'];
const rendererMetrics=async()=>Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.filter(row=>metricNames.includes(row.name)).map(row=>[row.name,row.value]));
async function measurePaint(kind){
 // Runner/renderer reads and JSON writes stay outside the measured interaction.
 const before=await rendererMetrics(),pendingBefore=pendingActions(),loadBefore=loadavg();
 const sample=await page.evaluate(async kind=>{
  const root=document.getElementById('amp-one'),button=kind==='appearance'?document.getElementById('appearance-color-mode'):null;
  const gallery={cards:document.querySelectorAll('.a-appearance-card').length,loading:!![...document.querySelectorAll('[role="status"]')].find(node=>node.textContent==='Loading appearances…')};
  let start,schemeChangeMs=null,firstFrameMs,secondFrameMs;
  const observer=new MutationObserver(()=>{if(schemeChangeMs===null&&root.style.colorScheme==='dark')schemeChangeMs=performance.now()-start});
  if(kind==='appearance')observer.observe(root,{attributes:true,attributeFilter:['style']});
  start=performance.now();
  if(kind==='settings')window.pendingSettings=window.amplifier.dispatch('view.update',{patch:{panel:'settings'}});else {button.value='dark';button.dispatchEvent(new Event('change',{bubbles:true}))};
  const clickMs=performance.now()-start;
  await new Promise(resolve=>requestAnimationFrame(()=>{firstFrameMs=performance.now()-start;requestAnimationFrame(()=>{secondFrameMs=performance.now()-start;resolve()})}));
  const ms=performance.now()-start,visible=kind==='settings'?!!document.querySelector('[role="dialog"]'):root.style.colorScheme==='dark';
  observer.disconnect();
  window.transportLongTasks.record(window.transportLongTasks.observer.takeRecords());
  const longTasks=window.transportLongTasks.rows.filter(row=>row.start<=start+ms&&row.start+row.duration>=start).map(row=>({startMs:row.start-start,durationMs:row.duration}));
  return {kind,ms,visible,clickMs,firstFrameMs,secondFrameMs,schemeChangeMs,gallery,longTasks};
 },kind);
 paint.push(sample);
 const after=await rendererMetrics();
 evidence.diagnostics.push({kind,pendingBefore,pendingAfter:pendingActions(),loadBefore,loadAfter:loadavg(),
  rendererDelta:Object.fromEntries(metricNames.map(name=>[name,after[name]-before[name]]))});
 await persist();
}
try{
 await persist();
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),45000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});evidence.runner.browser=browser.version();
 context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-active-client-token'},viewport:{width:1440,height:1000}});page=await context.newPage();
 // Keep network/action/DOM evidence for failures without continuous screenshots.
 await context.tracing.start({screenshots:false,snapshots:true,sources:false});tracing=true;
 await page.addInitScript(()=>{
  const rows=[],record=entries=>{for(const row of entries)rows.push({start:row.startTime,duration:row.duration});if(rows.length>64)rows.splice(0,rows.length-64)};
  const observer=new PerformanceObserver(list=>record(list.getEntries()));observer.observe({type:'longtask',buffered:true});
  window.transportLongTasks={rows,record,observer};
 });
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){
  pending.set(request,{action:String(request.postDataJSON()?.action||'unknown').slice(0,120)});
  // Bind the window at request admission, including bodies that finish later.
  // Settle only after cold paint measurements; never prewarm the gallery.
  if(measured||request.postDataJSON().action==='theme.list'){
   const captured=captureResponse(request).catch(error=>({error:error.message}));
   if(measured)responses.push(captured);
   if(request.postDataJSON().action==='theme.list')catalogResponses.push(captured);
  }
 }});
 page.on('requestfinished',request=>pending.delete(request));page.on('requestfailed',request=>pending.delete(request));
 cdp=await context.newCDPSession(page);await cdp.send('Network.enable');await cdp.send('Performance.enable');
 cdp.on('Network.eventSourceMessageReceived',event=>{recordFrame(event.eventName,Buffer.byteLength(event.data))});
 cdp.on('Network.webSocketFrameReceived',event=>{try{const frame=JSON.parse(event.response.payloadData);recordFrame(frame.event,Buffer.byteLength(event.response.payloadData))}catch{}});
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});await page.getByRole('button',{name:'App options',exact:true}).waitFor();
 evidence.stage='baseline';const initialBytes=await page.evaluate(()=>JSON.stringify(window.amplifier.getState()).length);evidence.initialBytes=initialBytes;await persist();
 assert.ok(initialBytes<1_000_000,`Chat baseline should omit settings history: ${initialBytes}`);
 assert.equal(await page.evaluate(()=>!!window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId).execution.retiredUsageNodes),false);
 // Initial asynchronous page delivery belongs to the bounded baseline, not
 // ordinary view-only deltas. Wait for actual requested coverage once; the
 // Settings/gallery interactions below remain cold and do not wait for idle.
 await expect.poll(()=>page.evaluate(()=>{
  const instances=window.amplifier.getState().shellResources?.instances;
  return ['chats','workspaces'].every(id=>{
   const entry=instances?.[id];return entry&&Object.keys(entry.pending||{}).length===0
    &&['context','pinnedNavigation','recentNavigation','workspaceShortcuts'].every(name=>entry.resources?.[name]);
  });
 }),{timeout:10000}).toBe(true);
 evidence.settledBaselineBytes=await page.evaluate(()=>JSON.stringify(window.amplifier.getState()).length);
 Object.assign(evidence,{initialFrames,initialFrameCount,initialMaxBytes,initialCoverageSettled:true});
 assert.ok(evidence.settledBaselineBytes<1_000_000,`Settled baseline should remain bounded: ${evidence.settledBaselineBytes}`);
 assert.ok(initialMaxBytes<1_000_000,JSON.stringify({initialFrameCount,initialMaxBytes}));
 measured=true;
 for(let i=0;i<12;i++)await page.evaluate(value=>window.amplifier.dispatch('view.update',{patch:{navWidth:300+value}}),i);
 // Preserve simulated transport latency; the explicit gate proves persistence is still blocked.
 await page.route('**/api/actions',async route=>{
  const action=route.request().postDataJSON()?.action;
  if(['view.update','shell.changes.prepare','shell.changes.apply'].includes(action))await new Promise(resolve=>setTimeout(resolve,500));
  if(action==='shell.changes.prepare'&&!persistenceReleased){heldPrepares++;await persistenceGate}
  await route.continue();
 });
 evidence.stage='settings paint';await measurePaint('settings');
 await page.evaluate(()=>window.pendingSettings);
 await page.locator('[data-settings-section="appearance"]').click();
 const dark=page.getByRole('combobox',{name:'Color mode',exact:true});await dark.waitFor();
 // Do not wait for gallery/fonts/network idle: keep the existing cold-opening coverage.
 evidence.stage='appearance paint';await measurePaint('appearance');
 evidence.stage='held persistence';await expect.poll(()=>heldPrepares).toBe(1);
 evidence.optimisticProof={heldPrepares,persistenceReleased,...await page.evaluate(()=>({localScheme:document.getElementById('amp-one').style.colorScheme,savedScheme:window.amplifier.getShellState().effectiveComposition.presentation.scheme??null}))};
 await persist();assert.ok(!evidence.optimisticProof.persistenceReleased&&evidence.optimisticProof.localScheme==='dark',JSON.stringify(evidence.optimisticProof));
 releasePersistence();
 await expect(dark).toBeEnabled();
 assert.equal(await page.evaluate(()=>window.amplifier.getShellState().effectiveComposition.presentation.scheme),'dark');
 // Failed persistence rolls back the preview and retains the saved appearance.
 evidence.stage='rejection rollback';await page.unroute('**/api/actions');
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='shell.changes.apply'){await new Promise(resolve=>setTimeout(resolve,300));return route.fulfill({status:409,json:{accepted:false,error:'Fixture rejected appearance'}})}await route.continue()});
 await page.getByRole('combobox',{name:'Color mode',exact:true}).selectOption('light');
 await expect(page.getByText('Fixture rejected appearance',{exact:true})).toBeVisible();
 assert.equal(await page.locator('#amp-one').evaluate(node=>node.style.colorScheme),'dark');evidence.rejectionRollback=true;
 await page.unroute('**/api/actions');
 measured=false;
 // Parse/hash only after the measured interaction. Merely deferring Promise.all
 // would not defer work in the asynchronous response callbacks.
 const catalog=new Map();
 for(const captured of await Promise.all(catalogResponses)){
  assert.equal(captured.error,undefined);const value=JSON.parse(captured.body);
  assert.equal(value.accepted,true);
  for(const item of value.result.items)catalog.set(item.id,item.fingerprint);
 }
 const receipts=(await Promise.all(responses)).map(captured=>{assert.equal(captured.error,undefined);return actionResponse(captured,catalog)}),
  deltas=frames.filter(row=>row.kind==='state-delta');
 const reads=receipts.filter(row=>row.action==='theme.read'),compact=receipts.filter(row=>row.action!=='theme.read');
 Object.assign(evidence,{stage:'transport and paint assertions',receipts,actionCount:receipts.length,maxActionBytes:Math.max(...receipts.map(row=>row.bytes)),
  compactCount:compact.length,maxCompactBytes:Math.max(...compact.map(row=>row.bytes)),themeReadCount:reads.length,
  themeReadBytes:reads.reduce((sum,row)=>sum+row.bytes,0),deltaCount:deltas.length,maxDeltaBytes:Math.max(...deltas.map(row=>row.bytes))});await persist();
 assert.ok(deltas.length>=12);
 for(const row of receipts){assert.equal(row.error,undefined);assertActionResponse(row);}
 assert.ok(compact.filter(row=>row.action==='view.update').length>=12);
 assert.ok(reads.some(row=>row.bytes>=16_000),'Exercise real demand bodies larger than a compact receipt');
 assert.ok(reads.length<=7,'Only this seven-entry gallery is demanded');
 assert.equal(new Set(reads.map(row=>row.requestId)).size,reads.length,'No duplicate thumbnail body reads');
 assert.ok(evidence.themeReadBytes<7*160_000,'Aggregate gallery bytes stay bounded');
 // Negative controls prove classification cannot hide receipt/state inflation,
 // oversized demand bodies, a wrong source, or a shortened/mismatched CSS body.
 const read=reads.find(row=>row.bytes>=16_000);
 for(const patch of [{action:'view.update'},{hasState:true},{bytes:160_000},{envelopeBytes:16_000},
  {resultId:'builtin:wrong'},{cssSha256:'wrong'},{fingerprint:'a'.repeat(64),cssSha256:'a'.repeat(64)}])assert.throws(()=>assertActionResponse({...read,...patch}));
 evidence.responseNegativeControls=7;
 assert.ok(deltas.every(row=>row.bytes<30_000),JSON.stringify(deltas));
 assert.ok(paint.every(row=>row.visible&&row.ms<250),JSON.stringify(paint));
 // A fresh page gets an authoritative baseline after reconnect.
 evidence.stage='reconnect';await page.reload();await page.getByRole('button',{name:'App options',exact:true}).waitFor();
 assert.equal(await page.locator('#amp-one').evaluate(node=>node.style.colorScheme),'dark');
 assert.deepEqual(errors,[]);
 Object.assign(evidence,{status:'passed',stage:'complete',reconnect:true});await persist();console.log(JSON.stringify(evidence,null,2));
}catch(error){if(page)console.error((await page.locator('body').innerText()).slice(0,3000));failed=true;evidence.status='failed';evidence.failure={name:error.name,message:error.message.slice(0,8000)};throw error}
finally{
 releasePersistence();
 try{
  if(tracing){await context.tracing.stop(failed?{path:tracePath}:{});if(failed)evidence.failureTrace=tracePath}
 }catch(error){evidence.traceError=error.message}
 try{await persist()}finally{try{await browser?.close()}finally{
  if(fixture.exitCode===null&&fixture.signalCode===null){
   const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
   try{const exited=once(fixture,'exit',{signal:controller.signal});fixture.kill();await exited;}
   catch(error){evidence.status='failed';evidence.fixtureCleanup='unconfirmed';await persist();throw error;}
   finally{clearTimeout(timer)}
  }
 }}
}
