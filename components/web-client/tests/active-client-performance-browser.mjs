import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/active_client_performance_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,terminal,stopCompanion=true,companionTask,api,result,primaryError,diagnosticsBefore,diagnosticsAfter;
const clientRequests=[],samples=[],companionSamples=[],diagnosticErrors=[],cleanupErrors=[];
const retain=(rows,row)=>{rows.push(row);if(rows.length>128)rows.shift()};
const evidence=async()=>{
 const value={...(result||{}),samples,clientRequests,companionSamples,diagnosticsBefore,diagnosticsAfter,
  failure:primaryError?.stack?.slice(0,16000),diagnosticErrors,cleanupErrors,
  timingLimits:'Client/server wall timestamps share the fixture host clock. Capacity includes lock wait; save spans are nested. Do not add overlapping durations. Client elapsed minus server duration includes scheduling/transport, not a proven queue cause.'};
 if(process.env.AMPLIFIER_PERF_EVIDENCE){await mkdir(dirname(process.env.AMPLIFIER_PERF_EVIDENCE),{recursive:true});await writeFile(process.env.AMPLIFIER_PERF_EVIDENCE,JSON.stringify(value,null,2)+'\n')}
};
const stats=values=>{const s=[...values].sort((a,b)=>a-b);return {samples:s.length,medianMs:Math.round(s[Math.floor(s.length/2)]),p95Ms:Math.round(s[Math.min(s.length-1,Math.floor(s.length*.95))]),maxMs:Math.round(s.at(-1))}};
try{
 const url=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),45000);let output='';fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const r=JSON.parse(line);if(r.url){clearTimeout(timer);resolve(r.url)}}catch{}})});
 const headers={Authorization:'Bearer fixture-active-client-token','Content-Type':'application/json'};
 api=async(path,body)=>{
  const started=performance.now(),row={path,startedAtMs:Date.now(),...(path==='/fixture/admit'?{callId:body.id}:{})};
  try{const response=await fetch(url+path,{headers,signal:AbortSignal.timeout(15000),...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
   Object.assign(row,{status:response.status,serverTiming:response.headers.get('server-timing'),requestId:response.headers.get('x-fixture-request-id')||response.headers.get('x-amplifier-request-id')});
   assert.equal(response.status,200,path);return await response.json();
  }catch(error){row.errorType=error.name;throw error}
  finally{Object.assign(row,{elapsedMs:performance.now()-started,endedAtMs:Date.now()});retain(clientRequests,row)}
 };
 const {sessions}=await api('/fixture/metrics');
 browser=await chromium.launch({headless:true});
 const pages=[],errors=[],viewCounts=[],stateReads=[];
 for(let i=0;i<4;i++){
  const context=await browser.newContext({extraHTTPHeaders:{Authorization:headers.Authorization},viewport:{width:1280,height:900}}),page=await context.newPage();pages.push(page);
  page.on('pageerror',error=>errors.push(error.message));
  stateReads.push(0);
  const counts={pending:0,max:0};viewCounts.push(counts);const views=new Set();
  page.on('request',r=>{const path=new URL(r.url()).pathname;if(path==='/api/state')stateReads[i]++;if(path==='/api/view'){views.add(r);counts.pending++;counts.max=Math.max(counts.max,counts.pending)}});
  const done=r=>{if(views.delete(r))counts.pending--};page.on('requestfinished',done);page.on('requestfailed',done);
  await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
  await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sessions[i<2?0:1]);
  await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Retained draft '+i);
  await page.waitForFunction(text=>window.amplifier.getState().view.draft===text,'Retained draft '+i);
 }
 await api('/api/clients/attach',{clientId:'performance-terminal',kind:'tui',protocolVersion:1});
 terminal=new AbortController();
 const stream=await fetch(url+'/api/sessions/'+sessions[0]+'/events',{headers:{...headers,'X-Amplifier-Client':'performance-terminal'},signal:terminal.signal});assert.equal(stream.status,200);
 let terminalFrames=0;const drain=(async()=>{try{for await(const chunk of stream.body)terminalFrames+=new TextDecoder().decode(chunk).split('event: snapshot').length-1}catch(error){if(error.name!=='AbortError')throw error}})();
 assert.equal((await api('/fixture/metrics')).subscriptions,5);
 await api('/api/clients/attach',{clientId:'performance-companion',kind:'native',protocolVersion:1});
 const companionBefore=(await api('/fixture/metrics')).companion;
 assert.equal(companionBefore.pins.length,14);assert.equal(companionBefore.readReceipts,153);assert.equal(companionBefore.noticeOwners,100);
 const companionDurations=[],companionStatuses=[],companionErrors=[];
 let companionPending=0,companionMax=0,companionEtag,lastCompanion;
 const pollCompanion=async working=>{
  const start=performance.now(),sample={startedAtMs:Date.now(),working};companionPending++;companionMax=Math.max(companionMax,companionPending);
  try{
   const response=await fetch(url+'/api/companion',{headers:{...headers,'X-Amplifier-Client':'performance-companion',...(companionEtag?{'If-None-Match':companionEtag}:{})},signal:AbortSignal.timeout(15000)});
   Object.assign(sample,{status:response.status,serverTiming:response.headers.get('server-timing'),requestId:response.headers.get('x-amplifier-request-id')});
   assert.ok([200,304].includes(response.status),'Companion HTTP '+response.status);
   if(response.status===200)lastCompanion=await response.json();
   else assert.ok(companionEtag&&lastCompanion,'Conditional response needs a prior representation');
   companionEtag=response.headers.get('etag');assert.ok(companionEtag);
   assert.equal(lastCompanion.workingCount,working);assert.equal(lastCompanion.attentionCount,50);
   assert.equal(lastCompanion.sessionsTruncated,false);assert.equal(lastCompanion.itemsTruncated,false);
   assert.deepEqual(lastCompanion.sessions.map(row=>row.id).sort(),[...companionBefore.pins].sort());
   assert.ok(lastCompanion.sessions.every(row=>row.pinned));
   assert.deepEqual(lastCompanion.items.map(row=>row.id).sort(),[...companionBefore.noticeIds].sort());
   companionStatuses.push(response.status);
   return performance.now()-start;
  }finally{companionPending--;Object.assign(sample,{elapsedMs:performance.now()-start,endedAtMs:Date.now()});retain(companionSamples,sample)}
 };
 diagnosticsBefore=await api('/fixture/diagnostics');
 await api('/fixture/progress',{running:true});
 // Representative one-second stress cadence, not a measured Tempo interval.
 // A slow response delays the next poll; never queue or overlap observations.
 stopCompanion=false;
 companionTask=(async()=>{try{while(!stopCompanion){const start=performance.now();companionDurations.push(await pollCompanion(1));if(!stopCompanion)await new Promise(resolve=>setTimeout(resolve,Math.max(0,1000-(performance.now()-start))))}}catch(error){companionErrors.push(error.stack||String(error))}})();
 const health=[],admissions=[],actions=[];
 for(let i=0;i<12;i++){
  const sample={index:i,callId:'synthetic-admission-'+i,startedAtMs:Date.now()};
  let start=performance.now();await api('/api/health');health.push(performance.now()-start);
  start=performance.now();assert.equal((await api('/fixture/admit',{id:'synthetic-admission-'+i})).allowed,true);admissions.push(performance.now()-start);
  const readsBefore=stateReads[i%4];
  start=performance.now();const receipt=await pages[i%4].evaluate(n=>window.amplifier.dispatch('view.update',{patch:{navExpanded:n%2===0}}),i);actions.push(performance.now()-start);
  Object.assign(sample,{healthMs:health.at(-1),admissionMs:admissions.at(-1),actionMs:actions.at(-1),endedAtMs:Date.now(),stateAcknowledgement:Object.hasOwn(receipt,'stateRevision'),stateRecoveryReads:stateReads[i%4]-readsBefore});retain(samples,sample);
  // Each client's value repeats after its first sample. No-op receipts must
  // not wait for unrelated streaming revisions or trigger the 1.5s recovery.
  if(i>=4){assert.equal(sample.stateAcknowledgement,false,JSON.stringify(sample));assert.equal(sample.stateRecoveryReads,0,JSON.stringify(sample))}
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 stopCompanion=true;await companionTask;
 assert.deepEqual(companionErrors,[]);assert.ok(companionDurations.length>=2,'Companion must overlap the progress workload');
 await api('/fixture/progress',{running:false});
 const companionQuietMs=await pollCompanion(0);
 for(let i=0;i<pages.length;i++){
  await expect(pages[i].getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Retained draft '+i);
  assert.equal(await pages[i].evaluate(()=>window.amplifier.getState().selectedSessionId),sessions[i<2?0:1]);
 }
 const metrics=await api('/fixture/metrics');assert.deepEqual(metrics.runtimeCalls,[]);assert.ok(metrics.ticks>=5);assert.ok(terminalFrames>=3);assert.deepEqual(errors,[]);
 assert.deepEqual(metrics.companion.selection,companionBefore.selection);
 assert.equal(metrics.subscriptions,5,'Polling must not attach an SSE subscription');
 assert.equal(metrics.companion.snapshots,0);assert.equal(metrics.companion.leases,0);assert.equal(metrics.companion.readers,0);
 assert.equal(companionMax,1);assert.equal(companionPending,0);
 // Generous end-to-end ceilings catch seconds-long queues without fragile CPU microbenchmarks.
 result={catalogSessions:22915,workspaces:3932,browsers:4,terminalStreams:1,health:stats(health),admission:stats(admissions),actions:stats(actions),viewMaxInFlight:viewCounts.map(r=>r.max),ticks:metrics.ticks,terminalFrames,draftsRetained:true,modelCalls:0,
  companion:{clients:1,cadenceMs:1000,cadence:'representative stress workload',...stats(companionDurations),quietMs:Math.round(companionQuietMs),statuses:companionStatuses,maxInFlight:companionMax,pins:14,noticeOwners:100,readReceipts:153,selectionRetained:true,snapshots:metrics.companion.snapshots,leases:metrics.companion.leases,readers:metrics.companion.readers}};
 diagnosticsAfter=await api('/fixture/diagnostics');
 await evidence().catch(error=>diagnosticErrors.push(error.message.slice(0,1000)));
 console.log(JSON.stringify(result,null,2));
 assert.ok(result.health.p95Ms<1500,JSON.stringify(result));assert.ok(result.admission.p95Ms<1500,JSON.stringify(result));assert.ok(result.actions.p95Ms<2000,JSON.stringify(result));
 assert.ok(result.viewMaxInFlight.every(n=>n<=1),JSON.stringify(result));
 terminal.abort();await drain;
}catch(error){primaryError=error;throw error}
finally{
 stopCompanion=true;
 for(const cleanup of [()=>companionTask,async()=>{if(api)diagnosticsAfter=await api('/fixture/diagnostics')},()=>terminal?.abort(),()=>browser?.close(),()=>fixture.kill()]){
  try{await cleanup()}catch(error){cleanupErrors.push({name:error.name,message:error.message.slice(0,1000)})}
 }
 await evidence().catch(error=>diagnosticErrors.push(error.message.slice(0,1000)));
 if(diagnosticErrors.length||cleanupErrors.length)console.error(JSON.stringify({diagnosticErrors,cleanupErrors}));
 if(!primaryError&&(diagnosticErrors.length||cleanupErrors.length))throw Error('Performance evidence or cleanup failed; see diagnostic errors');
}
