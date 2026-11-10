import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const stalledBody=process.env.CANVAS_RENDERER_STALLED_BODY==='1',mode=stalledBody?'stalled-body':'normal';
const reportDirectory=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/canvas-proof';
const proof={schemaVersion:1,mode,request:null,observedHeaders:null,hostAcceptance:null,fixtureEvents:[],browserErrors:[],optimisticPanel:false,bodyWaitStarted:false,bodyWaitCompleted:false,acceptedCompleteResponse:false,reloadStarted:false,reloadNavigationCount:0,watchdog:{budgetMs:25000,fired:false},cleanup:{budgetMs:5000,pageStopRequested:false,pageClosed:false,drained:false,failures:[]},fixtureExit:null};
const unhandledRejections=[],onUnhandled=reason=>unhandledRejections.push({source:'node',message:String(reason?.stack||reason)});
process.on('unhandledRejection',onUnhandled);
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Canvas renderer fixture'});
fixture.once('close',(code,signal)=>{proof.fixtureExit={pid:fixture.pid,code,signal,settled:true}});
let fixtureLines='';
fixture.stdout.on('data',chunk=>{
 fixtureLines+=chunk;let newline;
 while((newline=fixtureLines.indexOf('\n'))!==-1){
  const line=fixtureLines.slice(0,newline);fixtureLines=fixtureLines.slice(newline+1);
  try{const event=JSON.parse(line).canvasRendererStalledBody;if(event){proof.fixtureEvents.push(event);if(event.event==='accepted')proof.hostAcceptance=event}}catch{}
 }
});
const errorRecord=error=>({name:error?.name||'Error',message:String(error?.message||error),...(error?.code?{code:error.code}:{}),...(error instanceof AggregateError?{errors:error.errors.map(errorRecord)}:{})});
let browser,page,failure,detailsDrain,finalDetailsEvidence=()=>{};
proof.startup={phase:'fixture',complete:false};
const startupState=()=>page.evaluate(()=>{
 const s=window.amplifier?.getState?.(),textbox=document.querySelector('[aria-label="Message Amplifier"]');
 const text=document.body?.innerText||'';
 return {clientId:s?.client?.id??null,selectedSessionId:s?.selectedSessionId??null,
  sessionCount:s?.sessions?.length??null,workSurface:s?.view?.workSurface??null,panel:s?.view?.panel??null,
  composer:{attached:!!textbox,visible:!!textbox?.checkVisibility({checkVisibilityCSS:true})},
  text:text.slice(0,4096),textTruncated:text.length>4096};
});
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.addInitScript(()=>window.addEventListener('unhandledrejection',event=>console.error('canvas-renderer-unhandled:'+String(event.reason?.stack||event.reason))));
 page=await context.newPage();const errors=proof.browserErrors;let streams=0,resourceReads=0;
 page.on('console',message=>{if(message.type()==='error'&&message.text().startsWith('canvas-renderer-unhandled:'))unhandledRejections.push({source:'browser',message:message.text()})});
 page.on('pageerror',error=>errors.push(error.message));
 page.on('websocket',socket=>{if(new URL(socket.url()).pathname==='/api/events/ws')streams++});page.on('request',request=>{if(new URL(request.url()).pathname==='/api/events')streams++;if(request.url().includes('/api/canvas/views/'))resourceReads++});
 proof.startup.phase='app-ready';
 await page.goto(url);await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 proof.startup.arrival=await startupState();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=()=>action('canvas.views.inspect').then(result=>result.result);
 const view=async id=>(await inspect()).views.find(view=>view.viewId===id);
 const target=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 const canvas=page.locator('#workspace-canvas'),inspector=canvas.locator('.a-canvas-inspector'),reader=canvas.locator('.a-canvas-reader');
 const openDetails=()=>canvas.getByRole('button',{name:'Canvas details',exact:true}).click();
 // At these pane widths Details covers the reader; use the UI to expose it.
 const closeDetails=async()=>{
  await inspector.getByRole('button',{name:'Close canvas details',exact:true}).click();
  await expect(inspector).toBeHidden();
  await expect(reader).toHaveJSProperty('inert',false);
  await expect(reader).not.toHaveAttribute('aria-hidden','true');
  await expect(reader.getByRole('region',{name:'Artifact view',exact:true})).toBeVisible();
 };
 proof.startup.phase='create-session';
 const created=await action('session.create',{});assert.equal(created.accepted,true);
 proof.startup.created={accepted:created.accepted,selectedSessionId:(await state()).selectedSessionId};
 assert.ok(proof.startup.created.selectedSessionId);
 proof.startup.phase='conversation-surface';
 await showFixtureConversation(page);
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 proof.startup.conversation=await startupState();
 assert.equal(proof.startup.conversation.selectedSessionId,proof.startup.created.selectedSessionId);
 assert.equal(proof.startup.conversation.workSurface,'chat');proof.startup.complete=true;
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:640}});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this draft through viewer changes');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='Keep this draft through viewer changes');
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 const initial=await state(),workspace=initial.workspaces.find(row=>row.id===initial.selectedWorkspaceId).path;
 const filePath=workspace+'/copy path proof.md';await writeFile(filePath,'# File path proof');
 await action('canvas.show',{kind:'markdown',path:filePath});
 const fileDetails=page.locator('#workspace-canvas .a-canvas-inspector details.a-canvas-file-path');
 await fileDetails.getByText('File details',{exact:true}).click();
 await expect(fileDetails.locator('code')).toHaveText([filePath,workspace,'copy path proof.md']);
 await fileDetails.getByRole('button',{name:'Copy full path',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(filePath);
 await fileDetails.getByRole('button',{name:'Copy relative path',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('copy path proof.md');
 await fileDetails.getByText('File details',{exact:true}).click();
 await action('canvas.show',{kind:'html',title:'Live HTML view',content:'<h1>Persistent viewer</h1><input aria-label="Viewer note" value="Retained input">'});
 const html=await view('primary');
 await closeDetails();
 await expect(page.getByRole('button',{name:'Open a second view'})).toHaveCount(0);
 await page.evaluate(()=>{window.proof={root:document.getElementById('amp-one'),time:performance.timeOrigin,host:window.amplifier.getState().client.hostInstanceId,session:window.amplifier.getState().selectedSessionId}});
 await action('canvas.show',{kind:'markdown',title:'Reading notes',content:'# Reading notes\n\nA live document for the reader.\n\n## Next section\n\nKeep the source unchanged.'});
 const markdown=await view('primary');
 const sourceBefore=(await state()).canvas.content;
 const clientId=(await state()).client.id;
 const sourceURL='/api/canvas/views/primary/source?'+new URLSearchParams({...target(markdown),clientId});
 const sourceResponse=await page.request.get(url+sourceURL);
 assert.equal(sourceResponse.status(),200);assert.equal((await sourceResponse.json()).content,sourceBefore);
 const staleResponse=await page.request.get(url+'/api/canvas/views/primary/source?'+new URLSearchParams({...target(html),clientId}));
 assert.equal(staleResponse.status(),409,'HTTP source reads must reject an old artifact binding');
 // Author/build the separate package only after the production app is open.
 await import('../../scripts/examples/build-reader-example.mjs');
 const manifest=JSON.parse(await readFile(root+'examples/shell-reader/dist/manifest.json','utf8'));
 const source=await readFile(root+'examples/shell-reader/dist/module.mjs','utf8');
 const staged=(await action('shell.packages.stage',{manifest,source})).result;
 await assert.rejects(()=>action('canvas.views.renderer',{...target(markdown),renderer:staged.digest}),/validation/);
 assert.equal((await action('shell.packages.validate',{digest:staged.digest})).result.status,'passed');
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(staged.digest);
 await closeDetails();
 await page.getByRole('article',{name:'Reading view'}).waitFor();
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('ready');
 await page.getByRole('combobox',{name:'Reader text size'}).selectOption('22');
 await expect.poll(async()=>(await view('primary')).view.readerScale).toBe(22);
 await action('view.update',{patch:{canvasWidth:620}});
 assert.deepEqual(await page.evaluate(()=>({root:window.proof.root===document.getElementById('amp-one'),time:window.proof.time===performance.timeOrigin,host:window.proof.host===window.amplifier.getState().client.hostInstanceId,session:window.proof.session===window.amplifier.getState().selectedSessionId})),{root:true,time:true,host:true,session:true});
 assert.equal((await state()).canvas.content,sourceBefore);
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption('builtin.canvas.markdown');
 await closeDetails();
 await expect(reader.getByRole('heading',{name:'Reading notes',exact:true})).toBeVisible();
 await expect(page.getByRole('article',{name:'Reading view'})).toHaveCount(0);
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(staged.digest);
 await closeDetails();
 await page.getByRole('article',{name:'Reading view'}).waitFor();
 await expect(page.getByRole('combobox',{name:'Reader text size'})).toHaveValue('22');
 const readsBefore=resourceReads;
 await page.getByRole('combobox',{name:'Reader text size'}).selectOption('26');
 await expect.poll(async()=>(await view('primary')).view.readerScale).toBe(26);
 assert.equal(resourceReads,readsBefore,'View preferences must not refetch the artifact body');
 await mkdir(root+'output/canvas-proof',{recursive:true});
 await page.screenshot({path:root+'output/canvas-proof/single-view.png'});
 const oldPrimary=await view('primary');
 await action('canvas.show',{kind:'markdown',title:'Other document',content:'# Another document'});
 await assert.rejects(()=>action('canvas.views.command',{...target(oldPrimary),action:'canvas.view',args:{patch:{readerScale:99}}}),/changed/);
 await action('canvas.select',{id:markdown.resourceId});
 await expect(page.locator('[data-canvas-view="primary"]').getByRole('combobox',{name:'Reader text size'})).toHaveValue('26');
 const beforeClose=await view('primary');
 await action('canvas.close');await action('canvas.reopen');
 await assert.rejects(()=>action('canvas.views.command',{...target(beforeClose),action:'canvas.view',args:{patch:{readerScale:99}}}),/changed/);
 const reloadState=await state(),beforeReload=reloadState.client.id;
 // The visible inspector is optimistic. Hold only this client's exact opening
 // update and exercise the receipt barrier while that panel is already visible.
 const detailsArgs={sessionId:reloadState.selectedSessionId,patch:{canvasControlsExpanded:true}};
 const actionsURL=new URL('/api/actions',url).href;
 const openingDetails=request=>{
  if(request.url()!==actionsURL||request.method()!=='POST'||request.headers()['x-amplifier-client']!==beforeReload)return false;
  let body;try{body=request.postDataJSON()}catch(error){failDetails(error);return false}
  return body?.action==='view.update'&&body.args?.sessionId===detailsArgs.sessionId&&
   body.args?.patch?.canvasControlsExpanded===true&&Object.keys(body.args.patch).length===1&&Object.keys(body.args).length===2;
 };
 let releaseDetails,markHeld,heldRequest,forwarded,requestedDetails,reloadAfterReceipt,acceptedDetails=false,reloadStarted=false,cancelReload=false;
 let rejectDetails,detailsTimer,drainTimer,detailsFailure,pageStop;
 const detailsStarted=performance.now();let detailsPhase='capture';
 const observeReload=request=>{if(request.isNavigationRequest()&&request.frame()===page.mainFrame())proof.reloadNavigationCount++};
 page.on('request',observeReload);
 const pendingDetails=[],detailsFailures=[],cleanupFailures=[];
 const heldDetails=new Promise(resolve=>{releaseDetails=resolve});
 const capturedDetails=new Promise(resolve=>{markHeld=resolve});
 // One lifecycle watchdog includes capture, forwarding, the complete response
 // body and reload. Reserve five seconds for cleanup: at most 30 seconds total.
 const detailsDeadline=new Promise((_,reject)=>{rejectDetails=reject});
 const failDetails=error=>{detailsFailures.push(error);cancelReload=true;releaseDetails();rejectDetails(error)};
 const retainDetails=promise=>{
  pendingDetails.push(promise);promise.catch(failDetails);return promise;
 };
 const holdDetails=route=>{
  const operation=(async()=>{
   const request=route.request();
   if(heldRequest||!openingDetails(request))return route.continue();
   heldRequest=request;markHeld(request);
   forwarded=heldDetails.then(()=>route.continue(stalledBody?{headers:{...request.headers(),'x-canvas-renderer-stall':request.postDataJSON().id}}:undefined));
   return forwarded;
  })();
  // The handler must not leak a rejection; retain it and fail the lifecycle.
  return retainDetails(operation).catch(()=>{});
 };
 detailsTimer=setTimeout(()=>{
  proof.watchdog={...proof.watchdog,fired:true,phase:detailsPhase,elapsedMs:performance.now()-detailsStarted};
  failDetails(Object.assign(Error('Details reload lifecycle exceeded 25 seconds'),{code:'CANVAS_DETAILS_LIFECYCLE_TIMEOUT'}));
 },25000);
 const detailsLifecycle=(async()=>{
  await page.route(actionsURL,holdDetails);
  if(cancelReload)return;
  requestedDetails=retainDetails(page.waitForRequest(openingDetails));
  await openDetails();
  const requested=await requestedDetails;
  const detailsRequest=await capturedDetails;
  if(cancelReload)return;
  assert.equal(detailsRequest,requested);
  const detailsPayload=detailsRequest.postDataJSON();
  assert.equal(detailsPayload.action,'view.update');assert.deepEqual(detailsPayload.args,detailsArgs);
  assert.equal(typeof detailsPayload.id,'string');assert.ok(detailsPayload.id);
  proof.request={...detailsPayload,clientId:beforeReload,hostInstanceId:reloadState.client.hostInstanceId,url:detailsRequest.url(),method:detailsRequest.method()};
  // Correlate by the captured Request object, not another view.update response.
  reloadAfterReceipt=retainDetails((async()=>{
   const response=await page.waitForResponse(response=>response.request()===detailsRequest);
   assert.equal(response.request(),detailsRequest);assert.equal(response.status(),200);
   proof.observedHeaders={status:response.status(),requestId:detailsPayload.id,stallRequestId:response.headers()['x-canvas-renderer-stalled-request']||null,contentLength:response.headers()['content-length']||null,elapsedMs:performance.now()-detailsStarted};
   if(stalledBody)assert.equal(proof.observedHeaders.stallRequestId,detailsPayload.id);
   detailsPhase='response.finished';proof.bodyWaitStarted=true;
   assert.equal(await response.finished(),null);
   proof.bodyWaitCompleted=true;detailsPhase='response.json';
   const receipt=await response.json();
   assert.equal(receipt.accepted,true);assert.equal(receipt.hostInstanceId,reloadState.client.hostInstanceId);
   assert.deepEqual(response.request().postDataJSON(),detailsPayload);
   acceptedDetails=true;proof.acceptedCompleteResponse=true;
   if(cancelReload)return;
   reloadStarted=true;proof.reloadStarted=true;detailsPhase='reload';
   await page.reload();
  })());
  await expect(inspector).toBeVisible();await expect(reader).toHaveJSProperty('inert',true);
  assert.equal(acceptedDetails,false,'Optimistic Details is not an accepted update');
  assert.equal(reloadStarted,false,'Visible Details must not release the reload barrier');
  assert.equal((await state()).client.id,beforeReload);
  proof.optimisticPanel=true;detailsPhase='forward/headers';
  releaseDetails();await forwarded;
  await reloadAfterReceipt;
  assert.equal(acceptedDetails,true);assert.equal(reloadStarted,true);
 })();
 try{await Promise.race([detailsLifecycle,detailsDeadline])}catch(error){detailsFailure=error}
 finally{
  clearTimeout(detailsTimer);cancelReload=true;releaseDetails();markHeld(null);
  proof.lifecycleFailure=detailsFailure?errorRecord(detailsFailure):null;
  proof.timeoutCause=proof.watchdog.fired?proof.lifecycleFailure:null;
  const drainStarted=performance.now();
  // This test owns this page. Stop its I/O before draining failed work, never
  // close another page/context or wait indefinitely for a response body.
  const stopDetailsIO=()=>pageStop??=(async()=>{
   proof.cleanup.pageStopRequested=true;
   proof.cleanup.pageStopStartedAt=Date.now();
   try{await page.close({runBeforeUnload:false});proof.cleanup.pageClosed=page.isClosed();
    proof.cleanup.pageStopCompletedAt=Date.now()}catch(error){cleanupFailures.push(error)}
  })();
  const drainDetails=detailsDrain=(async()=>{
   // Remove the interceptor while its owner is still live. Closing the page
   // first cancels response I/O but also makes page.unroute itself fail.
   const disposal=proof.cleanup.routeDisposal={startedAt:Date.now(),pageOpenBefore:!page.isClosed(),completed:false};
   try{
    assert.equal(disposal.pageOpenBefore,true,'Owned page closed before route disposal');
    await page.unroute(actionsURL,holdDetails);
    disposal.completed=true;disposal.completedAt=Date.now();disposal.pageOpenAfter=!page.isClosed();
   }catch(error){disposal.error=errorRecord(error);cleanupFailures.push(error)}
   if(detailsFailure||detailsFailures.length)await stopDetailsIO();
   else if(cleanupFailures.length)await stopDetailsIO();
   let settled,count,stop;
   do{
    count=pendingDetails.length;stop=pageStop;
    settled=await Promise.allSettled([detailsLifecycle,...pendingDetails,...(stop?[stop]:[])]);
   }while(count!==pendingDetails.length||stop!==pageStop);
   for(const result of settled)if(result.status==='rejected')detailsFailures.push(result.reason);
   proof.cleanup.settlements=settled.map(result=>({status:result.status,
    ...(result.status==='rejected'?{error:errorRecord(result.reason)}:{})}));
   proof.cleanup.pendingCount=count;proof.cleanup.pageStopJoined=!!stop;
   proof.cleanup.drained=true;
  })();
  finalDetailsEvidence=()=>{proof.cleanup.failures=cleanupFailures.map(errorRecord)};
  try{
   await Promise.race([drainDetails,new Promise((_,reject)=>{
    drainTimer=setTimeout(()=>{stopDetailsIO();reject(Error('Details reload cleanup exceeded 5 seconds; owned page stop requested'))},5000);
   })]);
  }catch(error){cleanupFailures.push(error)}finally{
   clearTimeout(drainTimer);proof.cleanup.elapsedMs=performance.now()-drainStarted;
   finalDetailsEvidence();
   page.off('request',observeReload);
  }
 }
 const detailsErrors=[...new Set([detailsFailure,...detailsFailures,...cleanupFailures].filter(error=>error!==undefined))];
 if(detailsErrors.length)throw detailsErrors.length===1?detailsErrors[0]:new AggregateError(detailsErrors,'Details reload failed, including retained I/O/cleanup failures');
 await closeDetails();await page.getByRole('article',{name:'Reading view'}).first().waitFor();
 assert.notEqual((await state()).client.id,beforeReload);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this draft through viewer changes');
 // A module can pass fixtures but still fail on live data: contain and recover.
 const brokenSource='export default ({React})=>function Broken({host}){const s=React.useSyncExternalStore(host.subscribe,host.getSnapshot,host.getSnapshot);if(s.resource?.content?.includes("live document"))throw Error("Live reader failure");return React.createElement("p",null,"Fixture reader")}';
 const broken=(await action('shell.packages.stage',{manifest:{...manifest,id:'example.live-failure'},source:brokenSource})).result;
 assert.equal((await action('shell.packages.validate',{digest:broken.digest})).result.status,'passed');
 await openDetails();
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(broken.digest);
 await closeDetails();
 await page.getByText(/This renderer needs attention: Live reader failure/).waitFor();
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('error');
 assert.equal((await view('primary')).resourceId,markdown.resourceId);
 await page.getByRole('button',{name:'Use standard viewer'}).click();
 await openDetails();
 await expect(page.getByRole('combobox',{name:'Open with',exact:true})).toHaveValue('builtin.canvas.markdown');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this draft through viewer changes');
 await page.setViewportSize({width:390,height:844});
 await action('view.update',{patch:{canvasFocused:true,canvasControlsPinned:true,canvasControlsExpanded:true}});
 await expect(page.getByRole('combobox',{name:'Open with',exact:true})).toBeVisible();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'The single-view shell must fit a narrow display');
 await page.screenshot({path:root+'output/canvas-proof/single-view-mobile.png'});
 // Recovery startup must skip the optional renderer, preserving its saved
 // selection until the user explicitly restores the standard viewer.
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(staged.digest);
 await page.goto(url+'/?shell=recovery');
 await page.locator('[aria-label="Message Amplifier"]').waitFor({state:'attached'});
 await closeDetails();
 await expect(page.getByText('Recovery mode uses standard viewers.')).toBeVisible();
 await expect(reader.getByRole('heading',{name:'Reading notes',exact:true})).toBeVisible();
 assert.equal(await page.getByRole('article',{name:'Reading view'}).count(),0);
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('error');
 assert.equal((await view('primary')).renderer,staged.digest);
 await page.getByRole('button',{name:'Use standard viewer'}).click();
 await expect.poll(async()=>(await view('primary')).renderer).toBe('builtin.canvas.markdown');
 await expect.poll(async()=>(await view('primary')).activation?.status).toBe('ready');
 assert.deepEqual(errors,[]);
 assert.equal(streams,3,'One event stream per page load');
 await writeFile(root+'output/canvas-proof/evidence.json',JSON.stringify({externalDigest:staged.digest,resourceId:markdown.resourceId,streams,resourceReads,checks:['production hot loading after independent build','single viewer and retained saved artifacts','draft and conversation retention','renderer choices and state after reload','stale action rejection','failed live renderer fallback and recovery']},null,2));
 console.log('Canvas renderer proof passed: external validated hot loading, single viewer and preserved draft, reload retention, stale actions, fallback/recovery.');
}catch(error){
 failure=error;
 if(!proof.startup.complete&&page&&!page.isClosed()){
  let timer;
  try{proof.startup.failureState=await Promise.race([startupState(),new Promise((_,reject)=>{
   timer=setTimeout(()=>reject(Error('Startup failure evidence exceeded 1500ms')),1500);
  })])}
  catch(captureError){proof.startup.captureError=errorRecord(captureError)}
  finally{clearTimeout(timer)}
 }
}
finally{
 try{
  await finishFixture({label:'Canvas renderer fixture',close:[
   ['Canvas renderer browser',()=>browser?.close()],
   ['Canvas renderer Details drain',()=>detailsDrain]
  ],stop:async()=>{
   await stopFixture();proof.fixtureStopSucceeded=true;
  }});
 }catch(error){
  proof.finalCleanupFailure=errorRecord(error);
  failure=failure?new AggregateError([failure,error],'Canvas renderer scenario and cleanup failed'):error;
 }
 finalDetailsEvidence(); // Include late cleanup results after owned-browser cancellation/join.
 proof.noReload=!proof.reloadStarted&&proof.reloadNavigationCount===0;
 proof.unhandledRejectionCount=unhandledRejections.length;proof.unhandledRejections=unhandledRejections;
 if(unhandledRejections.length){
  const error=Error('Canvas renderer had unhandled rejections');
  failure=failure?new AggregateError([failure,error],'Canvas renderer failed with unhandled rejections'):error;
 }
 proof.expectedFailureVerified=false;
 if(stalledBody){
  try{
   assert.ok(failure);assert.ok(proof.request?.id);
   assert.equal(proof.watchdog.fired,true);assert.equal(proof.watchdog.phase,'response.finished');
   assert.equal(proof.timeoutCause?.code,'CANVAS_DETAILS_LIFECYCLE_TIMEOUT');
   assert.equal(proof.observedHeaders?.status,200);assert.equal(proof.observedHeaders?.stallRequestId,proof.request.id);
   assert.equal(proof.hostAcceptance?.id,proof.request.id);assert.equal(proof.hostAcceptance?.clientId,proof.request.clientId);
   assert.equal(proof.hostAcceptance?.hostInstanceId,proof.request.hostInstanceId);
   assert.deepEqual(proof.hostAcceptance?.args,proof.request.args);assert.equal(proof.hostAcceptance?.accepted,true);
   assert.equal(proof.optimisticPanel,true);assert.equal(proof.bodyWaitStarted,true);assert.equal(proof.bodyWaitCompleted,false);
   assert.equal(proof.acceptedCompleteResponse,false);assert.equal(proof.noReload,true);
   assert.equal(proof.cleanup.pageStopRequested,true);assert.equal(proof.cleanup.pageClosed,true);assert.equal(proof.cleanup.drained,true);
   assert.equal(proof.cleanup.routeDisposal?.completed,true);
   assert.equal(proof.cleanup.routeDisposal.pageOpenBefore,true);assert.equal(proof.cleanup.routeDisposal.pageOpenAfter,true);
   assert.ok(proof.cleanup.routeDisposal.completedAt<=proof.cleanup.pageStopStartedAt);
   assert.equal(proof.cleanup.pageStopJoined,true);
   assert.equal(proof.cleanup.settlements.length,proof.cleanup.pendingCount+2);
   assert.deepEqual(proof.cleanup.failures,[]);assert.equal(proof.finalCleanupFailure,undefined);
   assert.equal(proof.unhandledRejectionCount,0);
   assert.deepEqual(proof.browserErrors,[]);
   assert.equal(proof.fixtureExit?.settled,true);assert.equal(proof.fixtureExit?.code,0);assert.equal(proof.fixtureExit?.signal,null);
   assert.equal(proof.fixtureStopSucceeded,true);
   assert.ok(proof.fixtureEvents.some(event=>event.event==='headers-sent'&&event.id===proof.request.id&&event.bodyBytesSent===1&&event.bodyBytesTotal>1));
   assert.ok(proof.fixtureEvents.some(event=>event.event==='cleanup-complete'&&event.stalledResponses===1));
   proof.expectedFailureVerified=true;
  }catch(error){
   proof.negativeVerificationFailure=errorRecord(error);
   failure=failure?new AggregateError([failure,error],'Stalled-body qualification did not meet its evidence requirements'):error;
  }
 }
 proof.outcome=proof.expectedFailureVerified?'expected-failure':failure?'failed':'passed';
 proof.failure=failure?errorRecord(failure):null;
 try{
  await mkdir(reportDirectory,{recursive:true});
  await writeFile(reportDirectory+'/canvas-renderers-'+mode+'.json',JSON.stringify(proof,null,2)+'\n');
 }catch(error){
  proof.reportWriteFailure=errorRecord(error);proof.outcome='failed';proof.expectedFailureVerified=false;
  failure=failure?new AggregateError([failure,error],'Canvas renderer proof could not be saved'):error;
  proof.failure=errorRecord(failure);
 }finally{
  console.log(JSON.stringify({canvasRendererQualification:proof}));
  process.off('unhandledRejection',onUnhandled);
 }
}
if(failure)throw failure; // A verified negative is still a nonzero watchdog failure.
