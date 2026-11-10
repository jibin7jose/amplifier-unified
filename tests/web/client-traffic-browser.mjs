// Real server and built client; synthetic history, no provider or live data.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/client-traffic');
const report={status:'running',scope:'Synthetic real-server/built-client traffic; not provider or native-startup qualification',
 phases:[],frames:[],errors:[],dropped:0};
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 [join(root,'tests/fixtures/client_traffic_server.py')],{stdio:['ignore','pipe','pipe']});
let browser,context,page,url,sid,phase='startup',stderr='',viewPosts=0,expectedPanel=null;
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-32_768)});
const bytes=value=>Buffer.byteLength(JSON.stringify(value));
const sha=text=>createHash('sha256').update(text).digest('hex');
const persist=async()=>{await mkdir(output,{recursive:true});await writeFile(join(output,'report.json'),JSON.stringify(report,null,2)+'\n')};
try{
 const ready=await new Promise((resolve,reject)=>{
  let buffer='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+stderr)),30_000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr))});
  fixture.stdout.on('data',chunk=>{buffer+=chunk;if(buffer.length>65_536){clearTimeout(timer);reject(Error('Fixture startup output exceeded diagnostic budget'));return}let end;while((end=buffer.indexOf('\n'))>=0){
   const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
   try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}
  }});
 });
 ({url,sessionId:sid}=ready);report.loaded=ready.loaded;
 browser=await chromium.launch({headless:true});
 context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-client-traffic-token'}});
 page=await context.newPage();page.setDefaultTimeout(12_000);
 page.on('pageerror',error=>{if(report.errors.length<20)report.errors.push(error.message.slice(0,2000));else report.dropped++});
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/view'&&request.method()==='POST')viewPosts++});
 await page.addInitScript(()=>{
  const Native=window.WebSocket;window.trafficSockets=[];
  window.WebSocket=class extends Native{constructor(...args){super(...args);window.trafficSockets.push(this)}};
  // Observe committed App work, not child ticks or aborted render attempts.
  // React PerformedWork is bit1; main.jsx mounts App directly. Calibrate with
  // the remove-revision-guard negative control when the renderer changes.
  window.trafficCommits=0;window.trafficAllCommits=0;
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__={supportsFiber:true,inject:()=>1,
   onCommitFiberRoot:(_id,root)=>{window.trafficAllCommits++;if(root.current.child?.flags&1)window.trafficCommits++},onCommitFiberUnmount:()=>{},checkDCE:()=>{}};
 });
 const cdp=await context.newCDPSession(page);await cdp.send('Network.enable');
 cdp.on('Network.webSocketFrameReceived',event=>{
  try{
   const frame=JSON.parse(event.response.payloadData),changes=frame.data?.changes||[];
   const row={phase,event:frame.event,bytes:Buffer.byteLength(event.response.payloadData),revision:frame.data?.revision,
    paths:changes.slice(0,20).map(c=>c.path),
    revisionOnly:frame.event==='state-delta'&&changes.length>0&&changes.every(c=>c.path.length===1&&c.path[0]==='revision')};
   if(report.frames.length<512)report.frames.push(row);else report.dropped++;
  }catch{}
 });
 const act=(action,args)=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const facts=async()=>{const response=await context.request.get(url+'/fixture/traffic');assert.equal(response.status(),200);return response.json()};
 const mutate=async operation=>{const response=await context.request.post(url+'/fixture/traffic',{data:{operation}});assert.equal(response.status(),200);return response.json()};
 const observed=revision=>expect.poll(()=>page.evaluate(()=>window.amplifier.getState().revision)).toBeGreaterThanOrEqual(revision);
 const shape=()=>page.evaluate(sid=>{
  const state=window.amplifier.getState(),task=state.sessions.find(row=>row.id===sid)?.task;
  const control=state.runtimeControl?.[sid]?.['task.get'];
  return {task,control,panel:state.view.panel??null,revision:state.revision,commits:window.trafficCommits,allCommits:window.trafficAllCommits};
 },sid);
 const settled=async()=>{
  // Shell requests and selected work history settle on independent lanes.
  // A causal-only sample must wait for the selected work receipt as well.
  await expect.poll(()=>page.evaluate(sid=>{
   const state=window.amplifier.getState();
   const work=state.sessions.find(row=>row.id===sid)?.workProjection;
   return Object.values(state.shellResources?.instances||{})
    .every(value=>Object.keys(value.pending||{}).length===0)&&
    work?.status==='ready'&&work.current===true&&work.receipt?.sessionId===sid;
  },sid)).toBe(true);
  // Let the existing debounced view report settle, outside measurement.
  await page.waitForTimeout(450);
 };
 const panel=async value=>{
  const receipt=await act('view.update',{patch:{panel:value,runtimeDraft:{tab:'direction'}}});assert.equal(receipt.accepted,true);
  expectedPanel=value;await expect.poll(async()=>(await shape()).panel).toBe(value);await settled();
 };
 const compact=async label=>{
  const state=await shape();assert.equal(state.panel,expectedPanel);assert.equal(state.task?.id,'traffic-task');
  if(expectedPanel!==null)assert.equal(state.control?.task?.id,state.task.id,'Open task controls need actual coverage');
  const taskBytes=bytes(state.task),controlBytes=state.control?.task?bytes(state.control.task):0;
  assert.ok(!Object.hasOwn(state.task,'changes'),label+' must use historical detail reads');
  assert.ok(!state.control?.task||!Object.hasOwn(state.control.task,'changes'));
  // Fixture current fields are <2 KiB; allow schema overhead, not the 1 MiB history.
  assert.ok(taskBytes<4096&&controlBytes<4096,JSON.stringify({label,taskBytes,controlBytes}));
  report.phases.push({label,taskBytes,controlBytes});return {taskBytes,controlBytes};
 };
 const pulse=async label=>{
  phase=label+'-settle';await settled();
  // A prior rendered-view acknowledgement updates device.updatedAt without publishing.
  // Flush that material state before measuring strictly revision-only publications.
  await observed((await mutate('settle')).revision);await settled();
  phase=label;const before=await shape(),posts=viewPosts,index=report.frames.length,start=performance.now();
  const end=await mutate('pulse');await observed(end.revision);await page.waitForTimeout(450);
  const after=await shape(),frames=report.frames.slice(index);
  report.phases.push({label,beforeCommits:before.commits,afterCommits:after.commits,childInclusiveCommits:after.allCommits-before.allCommits,viewPosts:viewPosts-posts,frames});
  assert.ok(frames.length>0&&frames.every(row=>row.revisionOnly),'Only causal revisions belong to this measurement');
  assert.ok(frames.reduce((sum,row)=>sum+row.bytes,0)<4096,'Eight empty publications need only revision metadata');
  assert.ok(after.revision>before.revision);assert.equal(after.commits,before.commits,'Revision-only publications must not render the application root again');
  assert.equal(viewPosts,posts,'Revision observations must not feed back view reports');
  report.phases.push({label,publications:8,frames:frames.length,commits:after.commits-before.commits,
   viewPosts:viewPosts-posts,wallMs:performance.now()-start});
 };
 const reconnect=async label=>{
  phase=label;const index=report.frames.length;
  await page.evaluate(()=>{for(const socket of window.trafficSockets)socket.close()});
  await expect.poll(()=>report.frames.slice(index).filter(row=>row.event==='state').length).toBeGreaterThan(0);
  await settled();await compact(label);
  const baselines=report.frames.slice(index).filter(row=>row.event==='state');
  // This fixture's complete shell baseline is ~170 KiB, independent of retained task evidence.
  assert.ok(baselines.every(row=>row.bytes<256_000),JSON.stringify(baselines));
 };

 await page.goto(url);await showFixtureConversation(page,{navPinned:true});
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();await settled();
 assert.ok((await shape()).commits>0,'Production React commit observer must be installed');
 const initial=await facts();const small={};
 for(const value of ['chat-work','session-details','runtime']){
  phase='small-'+value;await panel(value);small[value]=await compact(phase);
 }
 phase='grow-history';const grown=await mutate('grow-history');await observed(grown.revision);await settled();
 assert.ok(grown.historyBytes>100*initial.historyBytes,'History grows while current state remains fixed');
 assert.equal(grown.taskRevision,initial.taskRevision);
 const large={};
 for(const value of ['chat-work','session-details','runtime']){
  phase='large-'+value;await panel(value);large[value]=await compact(phase);
  // Count/digest/reference metadata may grow slightly. Historical bodies may not.
  assert.ok(large[value].taskBytes-small[value].taskBytes<256);
  assert.ok(large[value].controlBytes-small[value].controlBytes<256);
  await pulse(phase+'-causal-pulse');
 }
 await reconnect('runtime-reconnect');
 await panel(null);await pulse('chat-causal-pulse');await reconnect('chat-reconnect');
 const preserved=await facts();assert.equal(preserved.taskSha256,grown.taskSha256);assert.equal(preserved.controlTaskSha256,grown.taskSha256);
 phase='append-history';await panel('runtime');const frameStart=report.frames.length;
 const appended=await mutate('append');await observed(appended.revision);await settled();
 const appendFrames=report.frames.slice(frameStart).filter(row=>row.event==='state-delta');
 assert.ok(appendFrames.length>0);assert.ok(appendFrames.every(row=>row.bytes<16_000),JSON.stringify(appendFrames));
 await compact('appended-history');

 phase='current-fields';const updated=await mutate('current');await observed(updated.revision);await settled();
 const current=await shape();
 for(const task of [current.task,current.control.task]){
  assert.equal(task.objective,'Current objective '+'o'.repeat(3000));
  assert.equal(task.corrections[0].text,'Current correction '+'c'.repeat(5000));
  assert.deepEqual(task.constraints,['Preserve historical evidence.']);
  assert.deepEqual(task.completionEvidence,['Saved evidence']);
  assert.deepEqual(task.questionIds,['question-current']);assert.deepEqual(task.operationIds,['operation-current']);
  assert.deepEqual(task.artifactRefs,['artifact-current']);assert.equal(task.revision,updated.taskRevision);
  assert.equal(task.appliedRevision,updated.taskRevision);
 }
 assert.equal(current.control.continuationSupported,true);assert.deepEqual(current.control.continuity,{supported:true,status:'ready'});
 const saved=page.locator('[data-part=task-continuity]');
 await saved.getByText('Record a blocker or completion',{exact:true}).click();
 await saved.locator('#task-evidence').fill('Observed current controls and historical evidence');
 await expect(saved.locator('[data-action="task.complete"]')).toBeEnabled();

 phase='historical-detail';const history=await act('task.history',{sessionId:sid,taskId:current.task.id});
 assert.equal(history.accepted,true);const item=history.result.items.find(row=>row.kind==='change');assert.ok(item?.detailRef);
 let text='',offset=0,digest,pages=0;
 do{
  const result=await act(item.detailRef.action,{...item.detailRef.args,offset});assert.equal(result.accepted,true);
  digest??=result.result.sha256;assert.equal(result.result.sha256,digest);assert.equal(result.result.historyRevision,item.revision);
  text+=result.result.text;offset=result.result.nextOffset;assert.ok(++pages<20,'Detail pagination must make bounded progress');
 }while(offset!==null);
 assert.ok(pages>1,'Historical body must exercise real paging');assert.equal(sha(text),digest);
 assert.equal(digest,grown.firstDetailSha256);
 const original=await act('task.detail',{sessionId:sid,taskId:current.task.id,expectedRevision:current.task.revision,field:'originalObjective'});
 assert.equal(original.accepted,true);assert.equal(original.result.text,'Observe traffic without sending work.');
 const final=await facts();assert.equal(final.sent,0);assert.equal(final.started,0);
 assert.equal(final.taskSha256,final.controlTaskSha256);assert.equal(final.historyCount,129);
 assert.deepEqual(report.errors,[]);assert.equal(report.dropped,0);
 Object.assign(report,{status:'passed',history:{smallBytes:initial.historyBytes,largeBytes:grown.historyBytes},small,large,
  semantics:{historyPreserved:true,currentFieldsRetained:true,completionEnabled:true,continuationRetained:true,
   pagedHistoricalDetail:true,providerCalls:0,runtimeStarts:0},browser:browser.version()});
 console.log('Client traffic passed: historical growth, causal idle updates, panels, reconnect and exact detail reads.');
}catch(error){report.status='failed';report.failure={phase,message:error.message.slice(0,2000)};throw error}
finally{
 try{await browser?.close()}finally{
  let forced=false,confirmed=fixture.exitCode!==null||fixture.signalCode!==null;
  if(!confirmed)await new Promise(resolve=>{
   let forceTimer,exitTimer;
   fixture.once('exit',()=>{confirmed=true;clearTimeout(forceTimer);clearTimeout(exitTimer);resolve()});
   fixture.kill('SIGTERM');
   forceTimer=setTimeout(()=>{forced=true;fixture.kill('SIGKILL');exitTimer=setTimeout(resolve,2000)},10_000);
  });
  report.fixtureExit={code:fixture.exitCode,signal:fixture.signalCode,forced,confirmed};
  const cleanupFailed=!confirmed||forced||fixture.exitCode!==0;
  const wasPassed=report.status==='passed';
  if(cleanupFailed)report.status='failed';
  await persist();await writeFile(join(output,'fixture.log'),stderr);
  if(cleanupFailed&&wasPassed)throw Error('Traffic fixture did not exit cleanly; see retained report');
 }
}
