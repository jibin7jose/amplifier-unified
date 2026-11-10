// Reproducible real-service latency sweep. No user data, runtimes or providers.
// --app-root can target an immutable source snapshot for before/after comparisons.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {writeFile,realpath} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const options=new Map(process.argv.slice(2).map(arg=>{const index=arg.indexOf('=');return index<0?[arg,true]:[arg.slice(0,index),arg.slice(index+1)]}));
const repository=fileURLToPath(new URL('../../',import.meta.url));
const appRoot=await realpath(resolve(options.get('--app-root')||repository));
const samples=Number(options.get('--samples')||3);
const cases=String(options.get('--cases')||'50:10:50,3000:50:3000,5000:100:5000,22500:4000:4000').split(',').map(value=>{const [summaries,workspaces,roots]=value.split(':').map(Number);return {summaries,workspaces,roots}});
const maxBytes=Number(options.get('--max-state-bytes')||2_000_000);
const round=value=>Math.round(value*100)/100;
const stats=values=>{const sorted=[...values].sort((a,b)=>a-b);return {min:round(sorted[0]),median:round(sorted[Math.floor(sorted.length/2)]),max:round(sorted.at(-1)),samples:values.map(round)}};
const result={label:options.get('--label')||'working-tree',frontend:options.has('--dev')?'development':'production',node:process.version,platform:process.platform,arch:process.arch,samples,cases:[]};
const browser=await chromium.launch({headless:true});
try{
 for(const scenario of cases){
  let fixtureLog='',vite,page,fixture;
  try{
   fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||resolve(repository,'.venv/bin/python'),['-u',resolve(repository,'tests/fixtures/library_performance_server.py'),'--summaries',String(scenario.summaries),'--workspaces',String(scenario.workspaces),'--roots',String(scenario.roots)],{
    env:{...process.env,AMPLIFIER_PERF_APP_ROOT:appRoot},stdio:['ignore','pipe','pipe']});
   fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
   const port=await new Promise((resolve,reject)=>{
    let output='';const timer=setTimeout(()=>reject(Error('Performance fixture startup timeout')),180000);
    fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});
    fixture.once('error',error=>{clearTimeout(timer);reject(error)});
    fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exited ${code}: ${fixtureLog}`))});
   });
   const target=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-library-performance-token','Content-Type':'application/json'};
   const api=async(path,body)=>{const response=await fetch(target+path,{headers,...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});if(!response.ok)throw Error(await response.text());return response.json()};
   const measure=async(path,body)=>{
    const start=performance.now(),response=await fetch(target+path,{headers,...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
    const first=performance.now(),buffer=Buffer.from(await response.arrayBuffer()),read=performance.now();
    if(!response.ok)throw Error(buffer.toString());
    const value=JSON.parse(buffer.toString()),decoded=performance.now();
    return {ttfbMs:first-start,bodyMs:read-first,decodeMs:decoded-read,totalMs:decoded-start,bytes:buffer.length,value};
   };
   const metadata=await api('/api/fixture/metrics');
   await api('/api/fixture/reset',{});
   const stateSamples=[],actionSamples=[];
   let stateSessionCount,stateWorkspaceCount;
   for(let sample=0;sample<samples;sample++){
    const measured=await measure('/api/state');stateSamples.push(measured);
    stateSessionCount=measured.value.sessions.length;stateWorkspaceCount=measured.value.workspaces.length;
    assert.equal(measured.value.chatNavigation.total,scenario.roots);
    delete measured.value;
   }
   const readMetrics=await api('/api/fixture/metrics');
   await api('/api/fixture/reset',{});
   for(let sample=0;sample<samples;sample++){
    const measured=await measure('/api/actions',{action:'view.update',args:{patch:{navExpanded:sample%2===0}}});
    assert.equal(measured.value.accepted,true);actionSamples.push(measured);delete measured.value;
   }
   const actionMetrics=await api('/api/fixture/metrics');
   assert.equal(actionMetrics.publications,samples,'one lightweight command must not multiply publications: '+JSON.stringify(actionMetrics));

   // Agents retain the entire navigation catalog even when browser transport is bounded.
   const agent=async path=>(await api('/api/fixture/agent',{args:{path}})).value;
   assert.equal(await agent('/chatNavigation/total'),scenario.roots);
   const boundedNavigation=(await api('/api/state')).headerChatNavigation;
   if(boundedNavigation)assert.equal(boundedNavigation.total,Math.ceil(scenario.roots/scenario.workspaces));
   await api('/api/actions',{action:'view.update',args:{patch:{navFilter:metadata.offPageRootTitle}}});
   assert.equal(await agent('/chatNavigation/total'),1);
   assert.equal(await agent('/chatNavigation/items/0/id'),metadata.offPageRootId);
   if(boundedNavigation)assert.equal((await api('/api/state')).headerChatNavigation.total,boundedNavigation.total,'header workspace choices must be independent of the All chats filter');
   await api('/api/actions',{action:'view.update',args:{patch:{navFilter:''}}});
   assert.equal(await agent('/chatNavigation/total'),scenario.roots);
   await api('/api/actions',{action:'session.pin',args:{id:metadata.offPageRootId,pinned:true}});
   assert.equal(await agent('/chatNavigation/items/0/id'),metadata.offPageRootId,'an agent must be able to pin a chat absent from the browser page');
   assert.equal(await agent('/selectedSessionId'),metadata.selectedId,'pinning must not change the active conversation');
   await api('/api/actions',{action:'session.pin',args:{id:metadata.offPageRootId,pinned:false}});

   let browserTarget=target;
   if(options.has('--dev')){
    vite=await createServer({configFile:false,root:resolve(appRoot,'frontend'),server:{host:'127.0.0.1',port:0,hmr:false,fs:{allow:[appRoot,repository]},proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
    await vite.listen();browserTarget=vite.resolvedUrls.local[0];
   }
   page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:headers.Authorization}});
   const errors=[];page.on('pageerror',error=>errors.push(error.message));
   const pendingRequests=new Set();let lastRequestAt=performance.now();
   page.on('request',request=>{if(/\/api\/(actions|view)(?:$|\?)/.test(request.url())){pendingRequests.add(request);lastRequestAt=performance.now()}});
   const requestFinished=request=>{if(pendingRequests.delete(request))lastRequestAt=performance.now()};
   page.on('requestfinished',requestFinished);page.on('requestfailed',requestFinished);
   const settled=async()=>{
    const deadline=performance.now()+30000;
    while(pendingRequests.size||performance.now()-lastRequestAt<250){
     assert.ok(performance.now()<deadline,'browser view/action requests must settle');
     await new Promise(resolve=>setTimeout(resolve,30));
    }
   };
   await page.addInitScript(()=>{
    window.performanceFixture={streamFrames:0,streamBytes:0,streamMaxBytes:0,longTasks:[]};
    const record=data=>{window.performanceFixture.streamFrames++;const bytes=new TextEncoder().encode(data).length;window.performanceFixture.streamBytes+=bytes;window.performanceFixture.streamMaxBytes=Math.max(window.performanceFixture.streamMaxBytes,bytes)};
    const Source=window.EventSource,Socket=window.WebSocket;
    window.EventSource=class extends Source{constructor(...args){super(...args);for(const kind of ['state','state-delta'])this.addEventListener(kind,event=>record(event.data))}};
    window.WebSocket=class extends Socket{constructor(...args){super(...args);this.addEventListener('message',event=>{const frame=JSON.parse(event.data);if(['state','state-delta'].includes(frame.event))record(event.data)})}};
    try{new PerformanceObserver(entries=>{window.performanceFixture.longTasks.push(...entries.getEntries().map(entry=>entry.duration))}).observe({entryTypes:['longtask']})}catch{}
   });
   const navigationStart=performance.now();await page.goto(browserTarget);await page.locator('#amp-one').waitFor();
   await page.waitForFunction(()=>window.amplifier?.getState().sharedHistory?.loading===false);
   await page.waitForFunction(expected=>document.querySelectorAll('.a-nav-chat').length===expected,Math.min(100,scenario.roots));
   const initialVisibleMs=performance.now()-navigationStart;
   assert.equal(await page.locator('.a-nav-chat').count(),Math.min(100,scenario.roots));
   const domNodes=await page.locator('*').count();
   await page.waitForTimeout(600); // Let initial observational /api/view settle; outside measured window.
   await api('/api/fixture/reset',{});
   await page.evaluate(()=>{window.performanceFixture={streamFrames:0,streamBytes:0,streamMaxBytes:0,longTasks:[]}});
   const settings=[],capabilities=[],maintenance=[],maintenanceSettled=[];
   const painted=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.now())))));
   for(let sample=0;sample<samples;sample++){
    const start=await page.getByRole('button',{name:'Settings',exact:true}).evaluate(button=>{const start=performance.now();button.click();return start});
    await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();settings.push((await painted())-start);
    // Capabilities automatically reads bundles. Exercise that real background
    // work before leaving: optimistic paint alone can conceal server stalls.
    const catalogStart=await page.locator('[data-settings-section=bundles]').evaluate(button=>{const start=performance.now();button.click();return start});
    await page.locator('[data-settings-destination=add-bundles]').waitFor();capabilities.push((await painted())-catalogStart);
    const next=await page.locator('[data-settings-section=history]').evaluate(button=>{const start=performance.now();button.click();return start});
    await page.locator('[data-settings-destination=history]').waitFor();maintenance.push((await painted())-next);
    await settled(); // Includes the 250ms quiet window defined above.
    maintenanceSettled.push((await page.evaluate(()=>performance.now()))-next);
    await page.locator('[data-settings-section=overview]').click();
    await page.getByRole('button',{name:'Close panel',exact:true}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
   }
   await settled(); // Include late server work when optimistic UI paints before acknowledgment.
   const browserMetrics=await api('/api/fixture/metrics'),observations=await page.evaluate(()=>window.performanceFixture);
   assert.deepEqual(browserMetrics.runtimeCalls,[],'settings and navigation must never mount/call a model');
   assert.deepEqual(errors,[]);
   const row={...scenario,stateSessionCount,stateWorkspaceCount,stateBytes:stateSamples[0].bytes,
    stateTtfbMs:stats(stateSamples.map(row=>row.ttfbMs)),stateTotalMs:stats(stateSamples.map(row=>row.totalMs)),
    stateDecodeMs:stats(stateSamples.map(row=>row.decodeMs)),stateServer:readMetrics,
    viewRoundtripMs:stats(actionSamples.map(row=>row.totalMs)),viewResponseBytes:actionSamples.map(row=>row.bytes),viewServer:actionMetrics,
    initialVisibleMs:round(initialVisibleMs),settingsClickToPaintMs:stats(settings),capabilitiesClickToPaintMs:stats(capabilities),maintenanceClickToPaintMs:stats(maintenance),maintenanceSettledMs:stats(maintenanceSettled),
    domNodes,browserServer:browserMetrics,streamFrames:observations.streamFrames,streamBytes:observations.streamBytes,streamMaxBytes:observations.streamMaxBytes,
    longTaskCount:observations.longTasks.length,maxLongTaskMs:round(Math.max(0,...observations.longTasks)),agentNavigationParity:true};
   result.cases.push(row);
   console.log(JSON.stringify({label:result.label,summaries:row.summaries,workspaces:row.workspaces,roots:row.roots,stateBytes:row.stateBytes,stateMedianMs:row.stateTotalMs.median,
    viewMedianMs:row.viewRoundtripMs.median,settingsMedianMs:row.settingsClickToPaintMs.median,maintenanceMedianMs:row.maintenanceClickToPaintMs.median,
    publications:row.viewServer.publications,streamBytes:row.streamBytes,domNodes}));
   if(options.has('--assert-bounded')){
    assert.ok(row.stateBytes<=maxBytes,`state payload ${row.stateBytes} exceeds ${maxBytes}`);
    assert.ok(row.viewResponseBytes.every(value=>value<=maxBytes),'view commands must not return the unbounded library');
    assert.ok(row.stateSessionCount<=250,`wire contains ${row.stateSessionCount} session rows`);
    assert.ok(row.stateWorkspaceCount<=250,`wire contains ${row.stateWorkspaceCount} workspace rows`);
    assert.ok(row.streamMaxBytes<=maxBytes,`SSE frame ${row.streamMaxBytes} exceeds ${maxBytes}`);
   }
  }catch(error){
   await page?.screenshot({path:`/tmp/library-performance-${scenario.summaries}-failure.png`}).catch(()=>{});
   if(fixtureLog)console.error(fixtureLog);throw error;
  }finally{
   await page?.close();await vite?.close();
   if(fixture&&fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
  }
 }
}finally{
 await browser.close();
 if(options.get('--output'))await writeFile(String(options.get('--output')),JSON.stringify(result,null,2)+'\n');
}
