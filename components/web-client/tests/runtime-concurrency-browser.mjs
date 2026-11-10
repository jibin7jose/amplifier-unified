import {fixturePython} from './fixture-python.mjs';
// Owned-DTU real-worker benchmark. External provider latency and runtime
// qualification/install are explicitly excluded, never mocked as accepted.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const options=Object.fromEntries(process.argv.slice(2).map(value=>value.replace(/^--/,'').split(/=(.*)/s)));
assert.ok(options.home&&options.out&&options['worker-python'],'Require --home= --out= --worker-python=');
mkdirSync(options.out,{recursive:true});
const hash=path=>createHash('sha256').update(readFileSync(root+path)).digest('hex');
const quantiles=values=>{const sorted=values.toSorted((a,b)=>a-b);return {count:sorted.length,p50:sorted[Math.floor(sorted.length*.5)]??null,p95:sorted[Math.min(sorted.length-1,Math.floor(sorted.length*.95))]??null,max:sorted.at(-1)??null}};
const result={schemaVersion:1,startedAt:new Date().toISOString(),source:options.source||'unspecified',
 fixtureSha256:hash('tests/fixtures/runtime_concurrency_server.py'),providerSha256:hash('tests/fixtures/amplifier_module_provider_concurrency/__init__.py'),harnessSha256:hash('frontend/tests/runtime-concurrency-browser.mjs'),
 scope:{abruptStopFaultInjection:options['abrupt-stop']==='1',realRuntimeManager:true,realSubprocessWorkers:true,realLoopLive:true,provider:'local deterministic 250ms deltas',runtimeQualification:'bypassed explicit qualified interpreter',externalProvider:false,history:'14 synthetic top-level chats; scale replica acceptance is separate',timingContext:options.context||'functional calibration; shared host load'},
 desired:{healthP95Ms:100,selectionP95Ms:500,sendAckWarmMs:500,sendAckColdMs:3000,firstVisibleDeltaWarmMs:1000,firstVisibleDeltaColdMs:3000,streamLagP95Ms:250,stopIdleMs:1000},stages:[],failures:[]};
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/runtime_concurrency_server.py','--home',options.home,'--worker-python',options['worker-python'],...(options['abrupt-stop']==='1'?['--abrupt-stop']:[]),...(options.trace==='1'?['--trace']:[])],{cwd:root,stdio:['ignore','pipe','pipe']});
let stderr='',stdout='',browser,page,ready;
fixture.stderr.on('data',chunk=>{stderr+=chunk;});fixture.stdout.on('data',chunk=>{stdout+=chunk;});
const fixtureExited=new Promise(resolve=>fixture.once('exit',(code,signal)=>resolve({code,signal})));
const isAction=(response,action)=>{try {const body=response.request().postDataJSON();return new URL(response.url()).pathname==='/api/actions'&&(body.action==='shell.command'?body.args.action:body.action)===action}catch{return false}};
try{
 ready=await new Promise((resolve,reject)=>{let data='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{data+=chunk;for(const line of data.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 result.runtimePython=ready.runtimePython;result.runtimeVersions=ready.runtimeVersions;
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 result.browserVersion=browser.version();
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-real-worker-benchmark'}});
 const pageErrors=[];page.on('pageerror',error=>pageErrors.push(error.message));
 await page.addInitScript(()=>{
  window.__bench={after:Date.now(),seen:new Set(),samples:[]};
  const scan=()=>{const root=document.querySelector('.a-messages'),now=Date.now();if(!root)return;for(const match of (root.textContent||'').matchAll(/\[BENCH:(\d+):(\d+):(\d+)\]/g)){const key=match[0],at=Number(match[3]);if(at<window.__bench.after||window.__bench.seen.has(key))continue;window.__bench.seen.add(key);window.__bench.samples.push({pid:Number(match[1]),sequence:Number(match[2]),emittedAt:at,renderedAt:now,lagMs:now-at,sessionId:root.dataset.sessionId});}};
  let queued=false;new MutationObserver(()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;scan()})}}).observe(document,{subtree:true,childList:true,characterData:true});
 });
 const cdp=await page.context().newCDPSession(page);await cdp.send('Network.enable');
 let phase=null;const requests=new Map();
 cdp.on('Network.requestWillBeSent',event=>requests.set(event.requestId,{url:event.request.url,start:event.timestamp,phase}));
 cdp.on('Network.loadingFinished',event=>{const r=requests.get(event.requestId);requests.delete(event.requestId);if(r?.phase&&r.url.startsWith(ready.url)){const path=new URL(r.url).pathname;r.phase.network.push({path,bytes:event.encodedDataLength,elapsedMs:(event.timestamp-r.start)*1000});}});
 cdp.on('Network.webSocketFrameReceived',event=>{if(phase){phase.socketFrames++;phase.socketBytes+=Buffer.byteLength(event.response.payloadData);if(options.trace==='1'){const at=Date.now();for(const marker of event.response.payloadData.match(/\[BENCH:\d+:\d+:\d+\]/g)||[])phase.socketMarkers[marker]??=at}}});
 const inspect=async(reset=false)=>{const response=await page.request.get(ready.url+'/fixture/runtime'+(reset?'?reset=1':''));assert.equal(response.status(),200);return response.json()};
 const action=async(action,args)=>{const response=await page.request.post(ready.url+'/api/actions',{headers:{'X-Amplifier-State-Transport':'delta-v1'},data:{action,args}});const body=await response.json();assert.equal(response.status(),200,JSON.stringify(body));assert.equal(body.accepted,true);if(action==='conversation.send')assert.equal(body.delivery,'accepted',JSON.stringify(body));return body};
 const row=id=>page.locator(`.a-nav-chat[data-session-id="${id}"]`).first();
 async function choose(id){await page.evaluate(()=>{window.__bench.after=Date.now()});const started=performance.now();await row(id).locator('.a-nav-chat-select').click();await expect(page.locator('.a-messages')).toHaveAttribute('data-session-id',id);await expect(row(id).locator('.a-nav-chat-select')).toHaveAttribute('aria-current','page');return performance.now()-started;}
 async function health(){const times=[];for(let i=0;i<10;i++){const started=performance.now(),response=await page.request.get(ready.url+'/api/health');assert.equal(response.status(),200);times.push(performance.now()-started)}return quantiles(times)}
 async function send(id,label){await choose(id);const before=await inspect();await page.getByRole('textbox',{name:'Message Amplifier'}).fill(label);await expect(page.getByRole('button',{name:'Send message',exact:true})).toBeEnabled({timeout:20000});await page.evaluate(()=>{window.__bench.after=Date.now();window.__bench.samples=[];});const started=performance.now(),receiptPromise=page.waitForResponse(r=>isAction(r,'conversation.send'),{timeout:60000});await page.getByRole('button',{name:'Send message',exact:true}).click();const receipt=await receiptPromise;const ackMs=performance.now()-started;assert.equal(receipt.status(),200,await receipt.text());const delivery=await receipt.json();assert.equal(delivery.delivery,'accepted',JSON.stringify(delivery));await page.waitForFunction(()=>window.__bench.samples.length>0,{},{timeout:30000});const firstVisibleMs=performance.now()-started;await expect(page.getByRole('button',{name:'Stop response',exact:true})).toBeVisible();return {ackMs,firstVisibleMs,beforeWorkers:before.workers,afterWorkers:(await inspect()).workers};}
 async function stop(id){await choose(id);const started=performance.now(),responsePromise=page.waitForResponse(r=>isAction(r,'conversation.stop'));await page.getByRole('button',{name:'Stop response',exact:true}).click();const response=await responsePromise;assert.equal(response.status(),200);const ackMs=performance.now()-started;await expect(page.getByRole('button',{name:'Stop response',exact:true})).toHaveCount(0,{timeout:20000});await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeEditable();await expect.poll(async()=>((await inspect()).sessions.find(s=>s.id===id)?.status),{timeout:20000}).toBe('stopped');const uiReadyMs=performance.now()-started;await expect.poll(async()=>(await inspect()).workers.some(w=>w.id===id&&w.returncode===null),{timeout:20000,message:'Stopped worker process must have exited'}).toBe(false);const stopped=await inspect();assert.equal(stopped.sessions.find(s=>s.id===id)?.generation?.terminal,true,'Confirmed exit must retire the stopped run before new ordinary input');return {ackMs,uiReadyMs,idleMs:performance.now()-started};}
 await page.goto(ready.url);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();await expect(row(ready.ids[13])).toBeVisible();
 result.bootstrap=await inspect();
 for(const count of (options.stages?options.stages.split(',').map(Number):[0,6,12])){
  console.log('Starting real-worker stage',count);
  const stage={activeTarget:count,baselineActive:count?count-1:0,measuredActive:Math.max(1,count),network:[],socketFrames:0,socketBytes:0,socketMarkers:{},selectionMs:[],sidebar:[],foreground:[],streamSamples:[]};result.stages.push(stage);phase=stage;
  const fg=count?ready.ids[count-1]:ready.ids[13],background=count?ready.ids.slice(0,count-1):[];
  if(count){await Promise.all(background.map((id,i)=>action('conversation.send',{sessionId:id,text:`Background stream ${count}/${i}`})));await expect.poll(async()=>{const s=await inspect();return background.filter(id=>s.workers.some(w=>w.id===id&&w.returncode===null)&&s.sessions.some(r=>r.id===id&&r.status==='working'&&r.streamingBytes>0)).length},{timeout:60000}).toBe(background.length);}
  stage.before=await inspect(true);stage.healthBefore=await health();
  if(!count){for(const id of [ready.ids[0],ready.ids[1],fg])stage.selectionMs.push(await choose(id));stage.idle=await inspect();assert.equal(stage.idle.workers.filter(w=>w.returncode===null).length,0);}
  stage.foreground.push({kind:'cold worker first turn',...await send(fg,`Foreground ${count}: stream until I stop.`)});
  const expected=Math.max(1,count);
  await expect.poll(async()=>{const state=await inspect();return state.workers.filter(w=>w.returncode===null&&state.sessions.some(s=>s.id===w.id&&s.status==='working')).length},{timeout:20000}).toBe(expected);
  stage.loaded=await inspect();
  for(const id of [...background,fg]){await expect(row(id).locator('.a-navigation-status')).toHaveAttribute('data-kind','working',{timeout:15000});const status=await row(id).locator('.a-navigation-status').getAttribute('aria-label');stage.sidebar.push({id,status,html:await row(id).innerHTML()});}
  for(let i=0;i<6;i++){stage.selectionMs.push(await choose(i%2&&background.length?background[i%background.length]:fg));await page.waitForTimeout(500);}
  await choose(fg);await page.waitForTimeout(options.quick==='1'?1500:6000);stage.healthLoaded=await health();stage.streamSamples=await page.evaluate(()=>window.__bench.samples);stage.streamLagMs=quantiles(stage.streamSamples.map(s=>s.lagMs));stage.loopAndResidency=await inspect();
  stage.stop=await stop(fg);
  stage.restartAfterStop=await send(fg,`BENCH_FINISH_FAST ${count}`);
  await expect.poll(async()=>((await inspect()).sessions.find(s=>s.id===fg)?.status),{timeout:15000}).toBe('idle');
  const completedWorker=(await inspect()).workers.find(w=>w.id===fg&&w.returncode===null);
  const warm=await send(fg,`Retained worker foreground ${count}.`);
  const warmWorker=warm.afterWorkers.find(w=>w.id===fg&&w.returncode===null);
  assert.ok(completedWorker&&warmWorker&&completedWorker.pid===warmWorker.pid,'Warm sample requires same real subprocess after completed turn');
  stage.foreground.push({kind:'same worker after completed turn',workerPid:completedWorker.pid,...warm});
  await page.waitForTimeout(options.quick==='1'?1000:3000);stage.warmStreamSamples=await page.evaluate(()=>window.__bench.samples);stage.warmStreamLagMs=quantiles(stage.warmStreamSamples.map(s=>s.lagMs));stage.warmStop=await stop(fg);
  for(const id of background)await action('conversation.stop',{sessionId:id});
  await expect.poll(async()=>{const s=await inspect();return s.workers.filter(w=>w.returncode===null).length+s.sessions.filter(r=>['working','starting','stopping'].includes(r.status)).length},{timeout:20000}).toBe(0);
  stage.afterStop=await inspect();stage.selection=quantiles(stage.selectionMs);phase=null;
  console.log(JSON.stringify({stage:count,foreground:stage.foreground.map(r=>({kind:r.kind,ackMs:r.ackMs,firstVisibleMs:r.firstVisibleMs})),stream:stage.streamLagMs,health:stage.healthLoaded,selection:stage.selection,stop:stage.stop}));
 }
 assert.deepEqual(pageErrors,[]);result.pageErrors=pageErrors;result.status='completed';
}catch(error){result.status='failed';result.failures.push({message:error.message,stack:error.stack});try{if(page&&ready)result.failureRuntime=await(await page.request.get(ready.url+'/fixture/runtime')).json()}catch{};try{await page?.screenshot({path:options.out+'/failure.png'});result.failureText=await page?.locator('body').innerText()}catch{};throw error;
}finally{
 await browser?.close();fixture.kill('SIGTERM');
 let cleanupTimer;result.cleanup=await Promise.race([fixtureExited,new Promise(resolve=>{cleanupTimer=setTimeout(()=>resolve({timeout:true}),30000)})]);clearTimeout(cleanupTimer);
 if(result.cleanup.timeout){fixture.kill('SIGKILL');result.cleanup=await fixtureExited;result.cleanup.forced=true;}
 result.finishedAt=new Date().toISOString();
 writeFileSync(options.out+'/fixture.stderr',stderr);writeFileSync(options.out+'/fixture.stdout',stdout.split('\n').map(line=>{try{const r=JSON.parse(line);delete r.cookie;return JSON.stringify(r)}catch{return line}}).join('\n'));
 writeFileSync(options.out+'/results.json',JSON.stringify(result,null,2));
 const rows=[['activeTarget','metric','currentMs','desiredMs']];
 for(const s of result.stages){for(const [metric,current,desired] of [['healthP95',s.healthLoaded?.p95,100],['selectionP95',s.selection?.p95,500],['streamLagP95',s.streamLagMs?.p95,250],['sendAckFirst',s.foreground[0]?.ackMs,3000],['firstVisibleFirst',s.foreground[0]?.firstVisibleMs,3000],['sendAckWarm',s.foreground[1]?.ackMs,500],['firstVisibleWarm',s.foreground[1]?.firstVisibleMs,1000],['stopIdle',s.stop?.idleMs,1000]])rows.push([s.activeTarget,metric,current??'',desired]);}
 writeFileSync(options.out+'/current-desired.csv',rows.map(r=>r.join(',')).join('\n')+'\n');
}
