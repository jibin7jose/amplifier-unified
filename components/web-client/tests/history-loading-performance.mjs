import {fixturePython} from './fixture-python.mjs';
// Production UI, real saved JSONL, cold application cache and warm reopen.
// The OS file cache is warm from fixture creation; remote conditions are emulated.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/history_loading_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
const result={scenario:'Synthetic saved JSONL; warm OS cache; production UI; no providers',cases:[]};
try{
 const {url,temporaryRoot}=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('History fixture timeout')),60000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{out+=chunk;for(const line of out.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 result.temporaryRoot=temporaryRoot;
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-history-loading'}});
 const cdp=await page.context().newCDPSession(page);await cdp.send('Network.enable');
 const apiRequests=new Set(),streamFrames=[];let apiBytes=0;
 cdp.on('Network.requestWillBeSent',event=>{const path=new URL(event.request.url).pathname;if(path.startsWith('/api/')&&!path.startsWith('/api/fixture/'))apiRequests.add(event.requestId)});
 cdp.on('Network.dataReceived',event=>{if(apiRequests.has(event.requestId))apiBytes+=event.dataLength});
 cdp.on('Network.eventSourceMessageReceived',event=>{if(apiRequests.has(event.requestId))streamFrames.push(Buffer.byteLength(event.data))});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url+'/login');await page.getByLabel('Username').fill('history-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');
 const metrics=async()=>{const response=await page.request.get(url+'/api/fixture/history-metrics');assert.equal(response.status(),200);return response.json()};
 const initial=await metrics();
 for(const scenario of initial.cases){
  const network=scenario.profile==='remote'?{latency:100,downloadThroughput:1250000,uploadThroughput:250000}:{latency:0,downloadThroughput:-1,uploadThroughput:-1};
  await cdp.send('Network.emulateNetworkConditions',{offline:false,...network});
  for(const cache of ['cold-app','warm','warm']){
   await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),initial.home);
   await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,initial.home);
   const before=(await metrics()).loads.length,bytesBefore=apiBytes,framesBefore=streamFrames.length;
   const started=performance.now();
   const action=page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),scenario.id);
   await page.waitForFunction(({id,marker})=>window.amplifier.getState().selectedSessionId===id && Array.from(document.querySelectorAll('[data-message-id]')).some(n=>n.textContent.includes(marker)),{id:scenario.id,marker:'END-'+scenario.identity},{timeout:30000});
   await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
   const paintedMs=performance.now()-started;
   await action;
   const transport={decodedApiBytes:apiBytes-bytesBefore,sseFrameBytes:streamFrames.slice(framesBefore)};
   const after=await metrics();
   const domRows=await page.locator('[data-message-id]').count();
   const row={...scenario,cache,network,paintedMs,domRows,transport,serverLoads:after.loads.slice(before)};
   result.cases.push(row);console.log(JSON.stringify(row));
   assert.ok(domRows<=100,'Initial rendering must remain bounded');
   assert.ok(transport.decodedApiBytes>0,'Measure real API delivery, not an empty sample');
   assert.ok(transport.sseFrameBytes.length>0,'Include actual streaming state delivery');
   assert.ok(transport.sseFrameBytes.every(n=>n<2000000),'Every streaming projection must remain bounded');
   assert.deepEqual(after.runtimeCalls,[]);assert.equal(after.unchanged,true);
  }
 }
 assert.deepEqual(errors,[]);
}finally{
 if(process.env.AMPLIFIER_PERF_EVIDENCE)await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(result,null,2)+'\n');
 await browser?.close();fixture.kill('SIGTERM');
 await new Promise(resolve=>{if(fixture.exitCode!==null)resolve();else{fixture.once('exit',resolve);const timer=setTimeout(resolve,5000);timer.unref()}});
}
