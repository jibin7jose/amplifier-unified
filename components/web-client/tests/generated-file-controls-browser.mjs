// Focused #1228/#1210 proof. Authoring this file is not execution evidence.
// Run only against an already built candidate, with AMPLIFIER_TEST_ARTIFACTS
// (or FILE_CONTROLS_EVIDENCE) and AMPLIFIER_TEST_PYTHON set by the owned runner.
// Installed runs: copy the Python fixture + configured_browser.py beneath
// /artifacts. Set FILE_CONTROLS_FIXTURE, AMPLIFIER_TEST_INSTALLED=1, and exact
// AMPLIFIER_TEST_PACKAGE_ROOT. The fixture rejects /workspace import leakage.
// This browser harness may use the runner's existing locked Playwright install.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {mkdir,mkdtemp,readFile,readdir,writeFile} from 'node:fs/promises';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {expect} from '@playwright/test';
import {launchOwner,ownerPage,closeOwner,desktopOptions,touchOptions,launchOptions} from './file-controls-browser-topology.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const cornerDiagnosticMode=process.env.FILE_CONTROLS_CORNER_DIAGNOSTIC==='1';
const touchEventDiagnosticMode=process.env.FILE_CONTROLS_TOUCH_EVENT_DIAGNOSTIC==='1';
const nativeStateDiagnosticMode=process.env.FILE_CONTROLS_NATIVE_STATE_DIAGNOSTIC==='1';
const portalHostExperimentMode=process.env.FILE_CONTROLS_PORTAL_HOST_EXPERIMENT==='1';
const stateReductionMode=process.env.FILE_CONTROLS_STATE_REDUCTION==='1';
const focusContrastMode=process.env.FILE_CONTROLS_FOCUS_CONTRAST==='1';
const coarseToolbarQualificationMode=process.env.FILE_CONTROLS_COARSE_TOOLBAR_QUALIFICATION==='1';
assert.ok([cornerDiagnosticMode,touchEventDiagnosticMode,nativeStateDiagnosticMode,portalHostExperimentMode,stateReductionMode,focusContrastMode,coarseToolbarQualificationMode].filter(Boolean).length<=1,
 'Conflicting file-controls diagnostic/qualification modes');
const fullJourney=![cornerDiagnosticMode,touchEventDiagnosticMode,nativeStateDiagnosticMode,portalHostExperimentMode,
 stateReductionMode,focusContrastMode,coarseToolbarQualificationMode].some(Boolean);
// A reviewed current product composition, not a diagnostic or an old geometry
// replay. The native contour algorithm/event/bytes oracles below are unchanged.
const currentLayout=process.env.FILE_CONTROLS_CURRENT_LAYOUT;
assert.ok(currentLayout===undefined||currentLayout==='e15e3745d826cbbce0735a582c14b79779e890b2','Unknown current layout binding');
assert.ok(!currentLayout||fullJourney,'Current layout is only available to the full journey');
// Fixed binding to the retained failed first-390 gesture, NOT a new measurement
// chosen to fit this candidate. No predecessor report is modified or rerun.
const coarseToolbarBaseline={
 source:{head:'8ac98b80826b136432aa45e263bebd98e0a04407',tree:'f1cb13af2c6d0281e589f7703ab1368d3e0835c3',
  report:'output/files1318-touch-event-v1-result/generated-source/evidence/generated-file-controls-3W6LLV/observations.json',
  sha256:'365b1b690f2735f799186bd5170ce82d93b5014eb18c55e0f745af4a8bf8e8bb',
  checkpoint:'first-390-primary-download',path:'touchEventDiagnostic.before'},
 before:{rect:{x:292,y:207.5,width:44,height:44,top:207.5,right:336,bottom:251.5,left:292},
  radii:['9px','9px','9px','9px'],borders:{left:0,top:0,right:0,bottom:0},
  viewport:{width:390,height:844,scale:1,offsetLeft:0,offsetTop:0},
  intended:{x:331.24264068711926,y:246.7426406871193},
  position:{x:39.24264068711926,y:39.24264068711929}},
 bodyTop:254,toolbarRect:{x:0,y:206,width:390,height:48,top:206,right:390,bottom:254,left:0},
 html:{bytes:426,sha256:'93229c6d39952ff4ab7b89ec66ae6d92e49da7a0d4da4269b736b8430caabfb0'}
};
// The driver derives this from immutable check-17 observations, not candidate
// geometry. A missing binding stops before fixture startup.
const portalHostBaseline=portalHostExperimentMode?JSON.parse(await readFile(
 '/artifacts/files1318-portal-host-experiment/portal-host-baseline.json','utf8')):null;
const stateReductionBaseline=stateReductionMode||focusContrastMode?JSON.parse(await readFile(focusContrastMode?
 '/artifacts/files1318-focus-contrast/focus-contrast-baseline.json':
 '/artifacts/files1318-state-reduction/state-reduction-baseline.json','utf8')):null;
const focusContrast={version:'focus-contrast-v1',baseline:stateReductionBaseline,arms:[],outcome:'inconclusive',
 productAcceptance:false,unfinished:focusContrastMode?`Unsubmitted touch note — ${randomUUID()}`:null,
 limits:['Two fresh sequential browser contexts over one immutable synthetic v1/v2/deleted-source fixture.',
  'Only the outside arm traverses Shift+Tab to the existing visible header Canvas toggle; no activation or focus repair.',
  'One original primary tap per context; no card/pointer rehearsal. A gate/capture/cleanup gap is inconclusive.',
  'Focus dependence is not engine cause or a product fix; both misses only show iframe focus was not necessary here.']};
const stateReduction={version:'state-reduction-v1',baseline:stateReductionBaseline,gates:[],card:null,primary:null,
 outcome:'inconclusive',productAcceptance:false,
 limits:['Two real product surfaces in one touch context; card geometry is intentionally not a causal match.',
  'Omitted Copy/320/desktop exercises, clipboard faults and metadata races; data setup uses the existing one-shot fixture actions.',
  'No DOM clone, trace, target listener, focus repair, alternate coordinates or product CSS change.',
  'Non-reproduction leaves omitted history/state/timing unresolved; neither result establishes cause or product acceptance.']};
const output=process.env.FILE_CONTROLS_EVIDENCE||process.env.AMPLIFIER_TEST_ARTIFACTS;
assert.ok(output,'Set an owned AMPLIFIER_TEST_ARTIFACTS or FILE_CONTROLS_EVIDENCE directory');
await mkdir(output,{recursive:true});
const evidence=await mkdtemp(join(resolve(output),'generated-file-controls-'));
const fixturePath=resolve(process.env.FILE_CONTROLS_FIXTURE||join(root,'tests/fixtures/generated_file_controls_server.py'));
const python=process.env.AMPLIFIER_TEST_PYTHON||process.env.FIXTURE_PYTHON||join(root,'.venv/bin/python');
const fixture=spawn(python,['-u',fixturePath],{cwd:dirname(fixturePath),stdio:['ignore','pipe','pipe']});
const stop=ownFixture(fixture,{label:'Generated file controls fixture'});
const records=[],checks=[],geometry=[],downloads=[],requests=[],commands=[],errors=[],screenshotErrors=[];
const fileFrameCaptures=[];
const compactPrimaryGestures=[],downloadDecorations=[],downloadNeighbors=[];
const downloadSettlements=[],tapSettlements=[],tapPending=new Set(),tapDrain={};
const metadataExchanges=[],detailsReadiness=[],preCleanupSnapshots=[],metadataPending=new Set();
const metadataByRequest=new WeakMap();
const pageLabels=new WeakMap(),routeCleanups=new Set(),browserOwners=[];
const cornerDiagnostics=[];
let touchEventDiagnostic=null,touchEventDiagnosticComplete=false;
const coarseToolbarQualification={version:'coarse-toolbar-v1',baseline:coarseToolbarBaseline,
 separationCssPx:14,contactRadiusDip:16,layoutChecks:[],gesture:null,complete:false};
const contourOracle={version:'circular-contour-v1',formula:'d=max(3,r-(r-3)/sqrt(2))',
 logicChecks:[],cornerShapeChecks:[],cornerShapeObservations:[],cornerShapeCaptureErrors:[],browserPreflight:null,
 negativeCounts:{size:0,regions:0,obstruction:0,paddingOrigin:0}};
const cornerShapeLogPrefix='FILE_CONTROLS_CORNER_SHAPES ';
let cornerDiagnosticComplete=false;
let logs='',lineBuffer='',page,ready,seed,failure,cleanup,timer,running;
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const errorEvidence=error=>error&&({name:error.name,message:error.message,stack:error.stack,
 ...(error.errors?{causes:error.errors.map(errorEvidence)}:{})});
fixture.stderr.on('data',chunk=>{logs=(logs+chunk).slice(-65536)});
const startup=new Promise((resolveReady,reject)=>{
 const timeout=setTimeout(()=>reject(Error('File controls fixture startup exceeded 15 seconds')),15000);
 fixture.once('error',error=>{clearTimeout(timeout);reject(error)});
 fixture.once('exit',(code,signal)=>{clearTimeout(timeout);reject(Error(`Fixture exited before readiness: ${code??signal}`))});
 fixture.stdout.on('data',chunk=>{
  lineBuffer+=chunk;
  let newline;
  while((newline=lineBuffer.indexOf('\n'))>=0){
   const line=lineBuffer.slice(0,newline);lineBuffer=lineBuffer.slice(newline+1);
   let value;try{value=JSON.parse(line)}catch{logs=(logs+line+'\n').slice(-65536);continue}
   records.push(value);
   if(value.url){clearTimeout(timeout);resolveReady(value)}
  }
 });
});

async function action(target,name,args={}){
 const result=await target.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 assert.equal(result.accepted,true,`${name} refused: ${JSON.stringify(result)}`);
 return result.result;
}
async function identity(target){
 return target.evaluate(()=>{const s=window.amplifier.getState();return {clientId:s.client.id,sessionId:s.selectedSessionId}});
}
async function fixturePost(target,suffix){
 const response=await target.request.post(ready.url+'/fixture/file-controls/'+suffix,{data:await identity(target),timeout:5000});
 assert.equal(response.status(),200,`Fixture ${suffix}: ${await response.text()}`);
 return response.json();
}
async function fixtureRead(target){
 const response=await target.request.get(ready.url+'/fixture/file-controls',{timeout:5000});
 assert.equal(response.status(),200);
 return response.json();
}
function card(target,row){
 return target.locator(`section[data-artifact-id="${row.id}"][data-artifact-version="${row.version}"]`);
}
function detailsFor(target,row){return card(target,row).locator('details.a-canvas-file-path')}
const button=(details,name)=>details.getByRole('button',{name,exact:true});
const names=['Copy full path','Copy relative path','Download saved file'];

async function bounded(promise,timeout,message){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(message)),timeout)})])}
 finally{clearTimeout(timer)}
}
// Observe rejection immediately, even before the driver reaches wait(). Keep the
// error as a tagged outcome and rethrow it from the awaited gate, never ignore it.
function observedGate(label){
 let resolve,reject;
 const outcome=new Promise((yes,no)=>{resolve=yes;reject=no}).then(value=>({value}),error=>({error}));
 return {resolve,reject,async wait(){
  const result=await bounded(outcome,8000,`${label} exceeded 8 seconds`);
  if(result.error)throw result.error;
  return result.value;
 }};
}
async function placement(target,restore=false){
 const response=await target.request.post(ready.url+'/fixture/file-controls/placement',{
  data:{...await identity(target),restore},timeout:5000});
 assert.equal(response.status(),200,`Fixture placement: ${await response.text()}`);
 return response.json();
}

// Runs in the page before application code. This is observation only: no app
// state or controls are substituted. Each snapshot is one synchronous DOM read.
function installDetailsProbe(){
 const ids=new WeakMap();let nextId=0;
 const id=node=>{if(!ids.has(node))ids.set(node,++nextId);return ids.get(node)};
 const visible=node=>node.checkVisibility({checkVisibilityCSS:true})&&node.getClientRects().length>0;
 const snapshot=node=>({
  at:Date.now(),nodeId:id(node),tag:node.tagName,open:node.open,visible:visible(node),
  viewport:{width:innerWidth,height:innerHeight},scroll:{x:scrollX,y:scrollY},
  loading:[...node.querySelectorAll('[role="status"]')].some(item=>item.textContent==='Loading file details…'),
  errors:[...node.querySelectorAll('[role="alert"]')].map(item=>item.textContent),
  paragraphs:[...node.querySelectorAll('p')].map(item=>({id:id(item),text:item.textContent,visible:visible(item)})),
  controls:[...node.querySelectorAll('button,[role="button"]')].map(item=>({
   tag:item.tagName,label:item.getAttribute('aria-label')||item.textContent.trim(),visible:visible(item)})),
  codes:[...node.querySelectorAll('code')].map(item=>({text:item.textContent,visible:visible(item)})),
  text:node.textContent,iframes:node.querySelectorAll('iframe').length
 });
 window.__fileDetailsSnapshot=snapshot;
 window.__fileControlsNodeId=id;
 window.__fileDetailsProbe=node=>{
  const events=[];let overflow=false;
  const record=kind=>{
   if(events.length===100){overflow=true;return}
   events.push({kind,...snapshot(node)});
  };
  const observer=new MutationObserver(()=>record('mutation')),toggle=()=>record('toggle');
  record('armed');
  observer.observe(node,{childList:true,subtree:true,characterData:true,attributes:true});
  node.addEventListener('toggle',toggle);
  return {read:()=>({events:[...events],overflow,snapshot:snapshot(node)}),
   markOpening:()=>{record('opening');return events.length},
   stop:()=>{observer.disconnect();node.removeEventListener('toggle',toggle)}};
 };
}
const detailsSnapshot=details=>details.evaluate(node=>window.__fileDetailsSnapshot(node));

function observeMetadata(request,payload,client,requestId){
 const args=payload.args||{},gate=observedGate('Exact metadata response completion');
 const record={client,requestedAt:Date.now(),requestId,
  target:{id:args.id,version:args.version,sessionId:args.sessionId},
  ...(fullJourney?{commandIndex:commands.length-1,clientId:request.headers()['x-amplifier-client']}:{})};
 metadataExchanges.push(record);
 const observed={record,wait:()=>gate.wait()};metadataByRequest.set(request,observed);
 const pending=(async()=>{
  try{
   const response=await request.response();assert.ok(response,'Metadata request has no response');
   record.responseAt=Date.now();record.httpStatus=response.status();
   const body=await response.json(),value=body.result;
   const finished=await response.finished();assert.equal(finished,null,'Metadata response did not complete');
   record.completedAt=Date.now();
   record.response={accepted:body.accepted,id:value?.id,version:value?.version,
    filePaths:value?.filePaths,download:value?.download,error:body.error};
   gate.resolve(record);
  }catch(error){record.failedAt=Date.now();record.error=errorEvidence(error);gate.reject(error)}
 })();
 observed.completion=pending; // Original response/body completion, also drainable after owned-browser close.
 metadataPending.add(pending);pending.finally(()=>metadataPending.delete(pending));
 return observed;
}
function metadataOracle(record,row){
 assert.equal(record.httpStatus,200,'Fresh metadata HTTP response refused');
 assert.ok(record.completedAt,'Fresh metadata response must finish');
 assert.equal(record.response.accepted,true,'Fresh metadata action refused');
 assert.equal(record.response.id,row.id,'Fresh metadata artifact changed');
 assert.equal(record.response.version,row.version,'Fresh metadata version changed');
 assert.deepEqual(record.response.filePaths,row.filePaths,'Fresh metadata paths differ from fixture provenance');
}
function freshRenderReady(state,openingIndex,version){
 const current=state.snapshot;
 const loadedThisOpening=state.events.slice(openingIndex).some(event=>event.open&&event.loading);
 return loadedThisOpening&&current.open&&!current.loading&&(current.errors.length>0||
  current.paragraphs.some(item=>item.text===`Saved version ${version}`&&item.visible));
}
// Poll only lifecycle readiness, NEVER the expected button/path counts. Once a
// fresh result is rendered, the exact oracle below runs once and fails on a
// settled missing control. An unchanged old version label is not fresh evidence.
async function freshDetails(target,details,row,trigger){
 const trace={client:pageLabels.get(target),expected:{id:row.id,version:row.version,sessionId:seed.sessionId},
  armedAt:Date.now(),matches:[]};detailsReadiness.push(trace);
 const probe=await bounded(details.evaluateHandle(node=>window.__fileDetailsProbe(node)),2000,'Details probe setup timed out');
 const readProbe=()=>bounded(probe.evaluate(value=>value.read()),1000,'Details snapshot timed out');
 const failures=[];
 let matched;
 const requestGate=observedGate('Fresh opening metadata request');
 const onRequest=request=>{
  const observed=metadataByRequest.get(request);
  if(!observed||JSON.stringify(observed.record.target)!==JSON.stringify(trace.expected))return;
  trace.matches.push(observed.record);if(!matched){matched=observed;requestGate.resolve(observed)}
 };
 try{
  let before=await readProbe();trace.before=before;
  // The rail can retain an open disclosure when its panel closes. Exercise a
  // real close/reopen rather than accepting that prior result as a fresh load.
  if(before.snapshot.open){
   await trigger();
   await expect.poll(async()=>{
    before=await readProbe();
    return !before.snapshot.open&&before.events.some(event=>event.kind==='toggle'&&!event.open);
   },{timeout:4000}).toBe(true);
  }
  trace.closed=before.snapshot;
  assert.equal(metadataExchanges.filter(record=>record.client===trace.client&&
   JSON.stringify(record.target)===JSON.stringify(trace.expected)&&!record.completedAt&&!record.failedAt).length,0,
  'An earlier matching metadata request is still pending');
  target.on('request',onRequest); // Armed BEFORE the native opening gesture.
  const openingIndex=await bounded(probe.evaluate(value=>value.markOpening()),1000,'Opening marker timed out');
  // Synthetic predicate controls only, never evidence of application rendering.
  // Both use the SAME open, visible result; only post-opening loading differs.
  const staleVisibleSnapshot={open:true,visible:true,loading:false,errors:[],
   paragraphs:[{text:`Saved version ${row.version}`,visible:true}]};
  assert.equal(freshRenderReady({events:[],snapshot:staleVisibleSnapshot},0,row.version),false);
  assert.equal(freshRenderReady({events:[{open:true,loading:true}],snapshot:staleVisibleSnapshot},0,row.version),true);
  trace.openingAt=Date.now();await trigger();
  const observed=await requestGate.wait();await observed.wait();
  metadataOracle(observed.record,row);
  let state;
  await expect.poll(async()=>{
   state=await readProbe();trace.latest=state;
   // Require this opening's observed loading -> result transition, even when
   // React reuses its paragraph. If no transition was observed, fail readiness;
   // do not infer a new render from receipt completion or a retained label.
   return freshRenderReady(state,openingIndex,row.version);
  },{timeout:4000}).toBe(true);
  assert.equal(trace.matches.length,1,'More than one matching metadata request during this opening');
  assert.equal(state.overflow,false,'Details transition evidence exceeded its bound');
  controlsSnapshotOracle(state.snapshot,row);
  trace.settledAt=Date.now();trace.passed=true;
 }catch(error){trace.error=errorEvidence(error);failures.push(error)}
 finally{
  target.off('request',onRequest);
  try{trace.final=await bounded(probe.evaluate(value=>{const result=value.read();value.stop();return result}),
   1500,'Final details capture timed out')}
  catch(error){trace.captureError=errorEvidence(error);failures.push(error)}
  try{await bounded(probe.dispose(),1000,'Details probe disposal timed out')}
  catch(error){trace.disposeError=errorEvidence(error);failures.push(error)}
 }
 if(failures.length===1)throw failures[0];
 if(failures.length)throw new AggregateError(failures,'Details readiness and diagnostic capture failed');
}
async function openDetails(target,row,mode='click'){
 const details=detailsFor(target,row),summary=details.locator('summary');
 await expect(summary).toHaveText('File details');
 if(mode==='keyboard'){
  await card(target,row).getByRole('button',{name:'Preview in chat',exact:true}).focus();
  await target.keyboard.press('Tab');await expect(summary).toBeFocused();
 }
 await freshDetails(target,details,row,()=>mode==='keyboard'?target.keyboard.press('Enter'):
  mode==='touch'?summary.tap():summary.click());
 return details;
}

// Positive and deliberate missing-button cases consume the SAME atomic snapshot
// predicate. No sequential locator counts, retries or synthesized replacements.
function controlsSnapshotOracle(snapshot,row){
 assert.equal(snapshot.tag,'DETAILS');assert.equal(snapshot.open,true);
 assert.equal(snapshot.visible,true);assert.equal(snapshot.loading,false);assert.deepEqual(snapshot.errors,[]);
 const expected=row.filePaths;
 for(const [name,present] of [
  ['Copy full path',!!expected.absolute],['Copy relative path',!!expected.relative],['Download saved file',true]]){
  const controls=snapshot.controls.filter(item=>item.label===name);
  assert.equal(controls.length,present?1:0,`Required control mismatch: ${name}`);
  if(present){assert.equal(controls[0].tag,'BUTTON',`${name} must be a native button`);assert.equal(controls[0].visible,true)}
 }
 const codes=snapshot.codes.filter(item=>item.visible).map(item=>item.text);
 for(const value of Object.values(expected))assert.ok(codes.includes(value),`Missing displayed path/base: ${value}`);
 if(!expected.absolute)assert.ok(snapshot.text.includes('Saved in this chat; no backing filesystem path.'));
 else if(!expected.relative)assert.ok(snapshot.text.includes('No relative path:'));
 assert.equal(snapshot.iframes,0,'File details must not mount a preview');
}
async function controlsOracle(details,row){
 const snapshot=await detailsSnapshot(details);
 detailsReadiness.push({kind:'atomic-oracle',at:Date.now(),expected:{id:row.id,version:row.version},snapshot});
 controlsSnapshotOracle(snapshot,row);
 if(fullJourney)await downloadDecorationCheck(button(details,names[2]),'file-details',false);
}

// Read-only component invariant: compact decorations are not independent pointer
// targets. Noncompact card/inspector descendants retain their normal hit behavior.
async function downloadDecorationCheck(control,label,compact){
 const sample=await control.evaluate(node=>{
  const id=window.__fileControlsNodeId,wrapper=node.parentElement,children=[...node.querySelectorAll('*')];
  if(children.length>16)throw Error('Download decoration evidence exceeds 16 descendants');
  return {nodeId:id(node),tag:node.tagName,connected:node.isConnected,label:node.getAttribute('aria-label'),
   compact:wrapper.getAttribute('data-compact'),pointerEvents:getComputedStyle(node).pointerEvents,
   statusPointerEvents:getComputedStyle(wrapper.querySelector('[role="status"]')).pointerEvents,
   children:children.map(child=>{
    const r=child.getBoundingClientRect(),point={x:r.left+r.width/2,y:r.top+r.height/2};
    const visible=child.checkVisibility({checkVisibilityCSS:true})&&r.width>0&&r.height>0;
    const hit=visible?document.elementFromPoint(point.x,point.y):null;
    return {tag:child.tagName.toLowerCase(),pointerEvents:getComputedStyle(child).pointerEvents,
     ariaHidden:child.getAttribute('aria-hidden'),focusable:child.getAttribute('focusable'),
     rect:r.toJSON(),visible,point,hitNodeId:hit?id(hit):null,hitIsButton:hit===node};
   })};
 });
 const record={label,compact,sample,passed:false};downloadDecorations.push(record);
 assert.equal(sample.tag,'BUTTON');assert.equal(sample.connected,true);
 assert.equal(sample.label,compact?'Download canvas source':'Download saved file');
 assert.equal(sample.compact,compact?'true':null);
 assert.notEqual(sample.pointerEvents,'none');assert.notEqual(sample.statusPointerEvents,'none');
 const svg=sample.children.filter(child=>child.tag==='svg');
 assert.equal(svg.length,1);assert.equal(svg[0].ariaHidden,'true');assert.equal(svg[0].focusable,'false');
 assert.ok(sample.children.some(child=>child.tag==='span'),'Native button label missing');
 for(const child of sample.children){
  if(compact){assert.equal(child.pointerEvents,'none');if(child.visible)assert.equal(child.hitIsButton,true)}
  else assert.notEqual(child.pointerEvents,'none','Noncompact controls must not inherit the compact rule');
 }
 if(compact)assert.ok(svg[0].visible&&svg[0].hitIsButton,'Visible compact icon must hit its native button');
 record.passed=true;return record;
}

async function copy(target,details,name,expected,trigger){
 const sentinel=`clipboard-sentinel:${name}:${randomUUID()}`;
 await target.evaluate(value=>navigator.clipboard.writeText(value),sentinel);
 assert.equal(await target.evaluate(()=>navigator.clipboard.readText()),sentinel);
 await trigger(button(details,name));
 const status=button(details,name).locator('..').getByRole('status');
 await expect(status).toHaveText('Copied.');
 await expect.poll(()=>target.evaluate(()=>navigator.clipboard.readText()),{timeout:3000}).toBe(expected);
 checks.push({check:'real clipboard + Copied.',control:name,expected,sentinel});
}

// Save the browser's actual download before checking the independently seeded
// bytes/hash/name. This same oracle must reject the corrupted-download control.
async function saveAndVerify(target,details,row,label,trigger=control=>control.click()){
 return saveControlAndVerify(target,button(details,'Download saved file'),row,label,trigger);
}
async function saveControlAndVerify(target,control,row,label,trigger=control=>control.click(),deferObservation=false){
 assert.equal(await control.evaluate(node=>node.tagName),'BUTTON','Exercise the rendered native download control');
 const settlement={label,trigger:{state:'pending'},observation:{state:'pending'},settled:false};
 downloadSettlements.push(settlement);
 let resolveEvent,eventTimer;
 // Own this observer so a failed gesture can REMOVE it, clear its deadline and
 // settle its promise. A rejected Promise.all does none of those things.
 const event=new Promise(resolve=>{resolveEvent=resolve});
 const finish=outcome=>{
  if(settlement.observation.state!=='pending')return;
  clearTimeout(eventTimer);
  target.off('download',onDownload);target.off('close',onClose);
  Object.assign(settlement.observation,{state:outcome.state,at:Date.now(),listenerRemoved:true,timerCleared:true,
   ...(outcome.error?{error:errorEvidence(outcome.error)}:{}),
   ...(outcome.download?{filename:outcome.download.suggestedFilename(),url:outcome.download.url()}:{}),
   ...(outcome.reason?{reason:outcome.reason}:{})});
  resolveEvent(outcome);
 };
 const onDownload=download=>finish({state:'received',download});
 const onClose=()=>finish({state:'closed',error:Error('Page closed before the download observation settled')});
 const arm=()=>{
  assert.ok(!settlement.observation.armedAt&&settlement.observation.state==='pending','Download observer may only arm once');
  settlement.observation.armedAt=Date.now();
  target.on('download',onDownload);target.on('close',onClose);
  eventTimer=setTimeout(()=>finish({state:'timed-out',error:Error('Download observation exceeded 5000ms')}),5000);
 };
 if(!deferObservation)arm();
 let triggerFailure;
 try{
  await trigger(control,arm);
  assert.ok(settlement.observation.armedAt,'Gesture did not arm its download observation');
  settlement.trigger.state='fulfilled';
 }
 catch(error){
  triggerFailure=error;
  Object.assign(settlement.trigger,{state:'rejected',error:errorEvidence(error)});
 }finally{
  settlement.trigger.settledAt=Date.now();
  if(triggerFailure)finish({state:'cancelled',reason:'gesture-failed'});
 }
 // The gesture (including its finally) is already settled. Its error takes
 // precedence over a concurrent timeout/close, but neither error is discarded.
 const outcome=await event;
 settlement.settled=true;
 settlement.primaryFailure=errorEvidence(triggerFailure||outcome.error);
 const failures=[triggerFailure,outcome.error].filter(Boolean);
 if(failures.length===1)throw failures[0];
 if(failures.length)throw new AggregateError(failures,'Gesture failed; concurrent download observation also failed');
 const download=outcome.download;
 assert.equal(await download.failure(),null);
 assert.equal(download.suggestedFilename(),row.filename,'Filename must describe the saved source');
 const path=join(evidence,label+'-'+row.filename);
 await download.saveAs(path);
 const bytes=await readFile(path);
 const observed={label,filename:download.suggestedFilename(),bytes:bytes.length,sha256:digest(bytes)};
 downloads.push(observed);
 assert.equal(observed.sha256,row.sha256,'Saved file SHA-256 mismatch');
 assert.equal(bytes.length,row.byteLength,'Saved file byte length mismatch');
 assert.deepEqual(bytes,Buffer.from(row.body,'utf8'),'Saved file changed original Unicode/CRLF bytes');
 await expect(control.locator('..').getByRole('status')).toHaveText('Download started. Check your browser’s downloads.');
 return observed;
}

async function preserved(target,draft){
 const value=await target.evaluate(()=>{
  const s=window.amplifier.getState(),c=s.canvas;
  return {selected:s.selectedSessionId,draft:s.view.draft,canvas:{id:c.id,version:c.selectedVersion,readOnly:c.readOnlyVersion},
   views:s.canvasWorkspace?.views||[],iframeSightings:window.__fileControlsFrames||[]};
 });
 assert.equal(value.selected,seed.sessionId);
 assert.equal(value.draft,draft,'File controls changed the private unsent draft');
 assert.deepEqual(value.canvas,{id:seed.unrelated.id,version:1,readOnly:true},'Unrelated historical Canvas selection changed');
 const ids=new Set(Object.values(seed.artifacts).map(row=>row.id));
 assert.deepEqual(value.views.filter(view=>ids.has(view.resourceId)),[],'Details/download acquired a generated-artifact renderer lease');
 assert.deepEqual(value.iframeSightings,[],'Details/download mounted an HTML iframe, even transiently');
 assert.equal(await target.locator('iframe').count(),0);
 assert.equal(await target.getByRole('textbox',{name:'Message Amplifier',exact:true}).innerText(),draft);
 return value;
}

// One deterministic circular-contour definition, also installed verbatim in the
// page. No passing-point search, CSS radius rescaling or Chromium pixel snapping.
// Reject geometry outside this deliberately small supported domain.
function circularContour({rect,radii,transforms,fragments,viewport}){
 const require=(condition,message)=>{if(!condition)throw Error(`Unsupported contour geometry: ${message}`)};
 require(['left','top','right','bottom','width','height'].every(key=>Number.isFinite(rect[key])),'nonfinite rect');
 require(rect.width>0&&rect.height>0&&rect.right-rect.left===rect.width&&rect.bottom-rect.top===rect.height,
  'inconsistent border box');
 require(fragments===1,'fragmented border box');
 require(Number.isFinite(viewport.width)&&viewport.width>0&&Number.isFinite(viewport.height)&&viewport.height>0&&
  viewport.scale===1&&viewport.offsetLeft===0&&viewport.offsetTop===0,'visual viewport mapping');
 require(Array.isArray(transforms)&&transforms.length>0&&transforms.every(style=>
  ['transform','perspective','translate','rotate','scale','offsetPath'].every(key=>style[key]==='none')&&
  style.transformStyle==='flat'&&['1','normal'].includes(style.zoom)),'transform/perspective/zoom');
 const px=value=>{
  require(typeof value==='string'&&/^(?:\d+(?:\.\d+)?|\.\d+)px$/.test(value),'radius must be finite computed px');
  const number=Number(value.slice(0,-2));require(Number.isFinite(number)&&number>=0,'nonfinite radius');return number;
 };
 require(Array.isArray(radii)&&radii.length===4,'four circular radii required');
 const values=radii.map(value=>{
  require(typeof value==='string','radius must be computed px');
  const pair=value.trim().split(/\s+/);
  require(pair.length===1||pair.length===2,'radius syntax');
  const radius=px(pair[0]);
  if(pair.length===2)require(px(pair[1])===radius,'elliptical radius');
  return radius;
 });
 const [tl,tr,bl,br]=values;
 require(tl+tr<=rect.width&&bl+br<=rect.width&&tl+bl<=rect.height&&tr+br<=rect.height,
  'overlapping radii require unsupported used-radius scaling');
 return values.map((radius,index)=>{
  const inward=Math.max(3,radius-(radius-3)/Math.sqrt(2));
  require(inward<rect.width/2&&inward<rect.height/2,'four distinct corner regions');
  const right=index%2===1,bottom=index>=2;
  return {corner:['top-left','top-right','bottom-left','bottom-right'][index],radius,inward,
   x:right?rect.right-inward:rect.left+inward,y:bottom?rect.bottom-inward:rect.top+inward};
 });
}
// An unsupported corner-*-shape property returns ''. In that case the browser
// has only the border-radius contour. Chromium 153 serializes round as
// superellipse(1). Accept ONLY these exact computed forms, without normalization.
function supportedCircularCornerShape(value){
 return value===''||value==='round'||value==='superellipse(1)';
}
function installContourSampler(logPrefix){
 const px=value=>{
  if(!/^(?:\d+(?:\.\d+)?|\.\d+)px$/.test(value)||!Number.isFinite(Number(value.slice(0,-2))))
   throw Error('Unsupported contour geometry: border must be finite computed px');
  return Number(value.slice(0,-2));
 };
 window.__fileControlsContourSample=node=>{
  if(window!==window.top||!node.isConnected)throw Error('Unsupported contour geometry: detached or framed control');
  const rect=node.getBoundingClientRect().toJSON(),style=getComputedStyle(node),transforms=[],ancestors=[];
  const cornerShapes=['top-left','top-right','bottom-left','bottom-right'].map(corner=>{
   const raw=style.getPropertyValue(`corner-${corner}-shape`);
   return {corner,raw,quoted:JSON.stringify(raw)};
  });
  // Synchronous console events are collected in Node, not in app state/UI. Emit
  // ALL raw values before validation, and again on rejection: a failed evaluate
  // must not discard the computed value that explains the failed guard.
  const record=stage=>console.debug(logPrefix+JSON.stringify({stage,at:Date.now(),
   nodeId:window.__fileControlsNodeId(node),tag:node.tagName,cornerShapes}));
  record('before-validation');
  for(const {corner,raw} of cornerShapes){
   if(!window.__fileControlsSupportedCircularCornerShape(raw)){
    record('rejected');
    throw Error(`Unsupported contour geometry: noncircular corner shape ${corner}=${JSON.stringify(raw)}; `+
     `cornerShapes=${JSON.stringify(cornerShapes)}`);
   }
  }
  for(let ancestor=node;ancestor;ancestor=ancestor.parentElement){
   const computed=getComputedStyle(ancestor);
   transforms.push(Object.fromEntries(['transform','perspective','translate','rotate','scale','offsetPath','transformStyle','zoom']
    .map(key=>[key,computed[key]])));
   ancestors.push({nodeId:window.__fileControlsNodeId(ancestor),x:ancestor.scrollLeft,y:ancestor.scrollTop});
  }
  const radii=[style.borderTopLeftRadius,style.borderTopRightRadius,style.borderBottomLeftRadius,style.borderBottomRightRadius];
  const viewport={width:innerWidth,height:innerHeight,scale:visualViewport?.scale??1,
   offsetLeft:visualViewport?.offsetLeft??0,offsetTop:visualViewport?.offsetTop??0};
  const points=window.__fileControlsCircularContour({rect,radii,transforms,fragments:node.getClientRects().length,viewport});
  const borders={left:px(style.borderLeftWidth),top:px(style.borderTopWidth),
   right:px(style.borderRightWidth),bottom:px(style.borderBottomWidth)};
  if(borders.left+borders.right>=rect.width||borders.top+borders.bottom>=rect.height)
   throw Error('Unsupported contour geometry: empty padding box');
  const hit=point=>{
   const element=document.elementFromPoint(point.x,point.y);
   return {...point,reachable:point.x>=0&&point.y>=0&&point.x<innerWidth&&point.y<innerHeight&&
    !!element&&node.contains(element),hit:element&&{nodeId:window.__fileControlsNodeId(element),tag:element.tagName}};
  };
  return {nodeId:window.__fileControlsNodeId(node),name:node.getAttribute('aria-label')||node.textContent.trim(),
   tag:node.tagName,rect,radii,cornerShapes,borders,transforms,viewport,scroll:{x:scrollX,y:scrollY,ancestors},
   basis:'four contour-relative native hit probes',corners:points.map(hit),
   legacyCorners:[[rect.left+3,rect.top+3],[rect.right-3,rect.top+3],[rect.left+3,rect.bottom-3],[rect.right-3,rect.bottom-3]]
    .map(([x,y],index)=>hit({corner:points[index].corner,x,y}))};
 };
}
function nativeSizeOracle(measured,label){
 assert.ok(measured.rect.width>=44&&measured.rect.height>=44,`${label}: target below 44px`);
}
function nativeHitOracle(measured,label){
 assert.ok(['BUTTON','SUMMARY'].includes(measured.tag),`${label}: native control required`);
 nativeSizeOracle(measured,label);
 assert.deepEqual(measured.corners.map(point=>point.corner),['top-left','top-right','bottom-left','bottom-right'],
  `${label}: four distinct contour regions required`);
 assert.ok(measured.corners.every(point=>point.reachable===true),`${label}: obscured/offscreen contour probe`);
}
function contourLogicChecks(){
 const rect={left:0,top:0,right:100,bottom:44,width:100,height:44};
 const plain={transform:'none',perspective:'none',translate:'none',rotate:'none',scale:'none',
  offsetPath:'none',transformStyle:'flat',zoom:'1'};
 const base={rect,radii:['0px','0px','0px','0px'],transforms:[plain],fragments:1,
  viewport:{width:320,height:740,scale:1,offsetLeft:0,offsetTop:0}};
 const square=circularContour(base);
 assert.deepEqual(square.map(({x,y})=>({x,y})),[{x:3,y:3},{x:97,y:3},{x:3,y:41},{x:97,y:41}]);
 contourOracle.logicChecks.push({check:'square radius 0 uses 3px',passed:true});
 const circle=circularContour({...base,radii:['10px','10px 10px','10px','10px']});
 assert.ok(circle.every(point=>Math.abs(point.inward-5.050252531694167)<1e-12));
 assert.deepEqual(circle.map(point=>point.corner),square.map(point=>point.corner));
 contourOracle.logicChecks.push({check:'circular radius 10 uses 5.050252531694167px',passed:true});
 const invalid=[
  ['nonfinite radius',{radii:['Infinitypx','0px','0px','0px']},/finite computed px/],
  ['nonfinite rect',{rect:{...rect,width:Infinity}},/nonfinite rect/],
  ['elliptical radius',{radii:['10px 11px','0px','0px','0px']},/elliptical radius/],
  ['percentage radius',{radii:['10%','0px','0px','0px']},/finite computed px/],
  ['negative radius',{radii:['-1px','0px','0px','0px']},/finite computed px/],
  ['overlapping radii',{radii:['30px','30px','30px','30px']},/overlapping radii/],
  ['transform',{transforms:[{...plain,transform:'matrix(1, 0, 0, 1, 0, 0)'}]},/transform\/perspective/],
  ['perspective',{transforms:[{...plain,perspective:'100px'}]},/transform\/perspective/],
  ['fragmented box',{fragments:2},/fragmented border box/],
  ['inconsistent box',{rect:{...rect,right:101}},/inconsistent border box/],
  ['coincident regions',{rect:{...rect,right:6,width:6}},/distinct corner regions/],
  ['scaled viewport',{viewport:{...base.viewport,scale:2}},/visual viewport mapping/]
 ];
 for(const [name,change,message] of invalid){
  assert.throws(()=>circularContour({...base,...change}),message);
  contourOracle.logicChecks.push({check:`reject ${name}`,passed:true});
 }
 checks.push({check:'contour formula and unsupported geometry logic checks',count:contourOracle.logicChecks.length});
}
function cornerShapeLogicChecks(){
 for(const [raw,expected] of [
  ['',true],['round',true],['superellipse(1)',true],
  ['bevel',false],['scoop',false],['superellipse(0)',false],['superellipse(2)',false],
  ['unknown',false],['Round',false],[' round',false],['round ',false],['superellipse(1.0)',false],[null,false]
 ]){
  const actual=supportedCircularCornerShape(raw);
  const row={raw,quoted:JSON.stringify(raw),expected,actual,passed:actual===expected};
  contourOracle.cornerShapeChecks.push(row);
  assert.equal(actual,expected,`Computed corner-shape predicate: ${row.quoted}`);
 }
 checks.push({check:'computed corner-shape exact-value predicate checks',count:contourOracle.cornerShapeChecks.length});
}
async function computedCornerPreflight(target,owner){
 const preflight={passed:false,client:owner.label,timeoutMs:2000,
  browser:{name:owner.browser.browserType().name(),version:owner.browser.version()},
  observationRange:{start:contourOracle.cornerShapeObservations.length,end:null}};
 contourOracle.browserPreflight=preflight;
 try{
  // Same browser/context and installed sampler as the journey; no second launch,
  // retry, app dispatch or product CSS mutation. Both source and installed runs
  // execute this gate before session creation. The temporary native control is
  // removed synchronously in finally, including when either sampler call fails.
  preflight.result=await bounded(target.evaluate(()=>{
   const result={userAgent:navigator.userAgent,platform:navigator.platform,removed:false,
    supported:{round:CSS.supports('corner-top-left-shape','round'),bevel:CSS.supports('corner-top-left-shape','bevel')},
    positive:null,negative:null,error:null};
   const control=document.createElement('button');
   control.type='button';control.textContent='Contour preflight';
   control.style.cssText='all:initial;position:fixed;left:8px;top:8px;display:block;box-sizing:border-box;'+
    'width:120px;height:44px;margin:0;padding:0;border:2px solid black;border-radius:10px;corner-shape:round;'+
    'background:white;color:black;font:12px sans-serif;pointer-events:auto;z-index:2147483647;';
   try{
    document.documentElement.append(control);
    result.positive=window.__fileControlsContourSample(control);
    control.style.setProperty('corner-top-left-shape','bevel');
    result.negative={corner:'top-left',authored:'bevel',
     raw:getComputedStyle(control).getPropertyValue('corner-top-left-shape'),accepted:null,error:null};
    result.negative.quoted=JSON.stringify(result.negative.raw);
    try{result.negative.sample=window.__fileControlsContourSample(control);result.negative.accepted=true}
    catch(error){result.negative.accepted=false;result.negative.error={name:error.name,message:error.message,stack:error.stack}}
   }catch(error){result.error={name:error.name,message:error.message,stack:error.stack}}
   finally{control.remove();result.removed=!control.isConnected}
   return result;
  }),preflight.timeoutMs,'Computed corner-shape browser preflight exceeded 2 seconds');
  preflight.observationRange.end=contourOracle.cornerShapeObservations.length;
  const {result}=preflight;
  assert.equal(result.removed,true,'Preflight native button was not removed');
  assert.equal(result.error,null,`Browser preflight failed: ${JSON.stringify(result.error)}`);
  assert.deepEqual(result.supported,{round:true,bevel:true},'Browser cannot demonstrate actual corner-shape discrimination');
  assert.equal(result.positive.tag,'BUTTON');
  assert.ok(result.positive.cornerShapes.every(({raw})=>raw!==''&&supportedCircularCornerShape(raw)),
   'Preflight must observe a supported, computed round form, not an unsupported-property empty value');
  nativeHitOracle(result.positive,'computed-round preflight');
  assert.ok(['bevel','superellipse(0)'].includes(result.negative.raw),'Preflight did not compute the authored bevel');
  assert.equal(supportedCircularCornerShape(result.negative.raw),false);
  assert.equal(result.negative.accepted,false,'Same sampler accepted a noncircular corner');
  assert.match(result.negative.error?.message||'',/Unsupported contour geometry: noncircular corner shape top-left=/);
  const observed=contourOracle.cornerShapeObservations.slice(preflight.observationRange.start,preflight.observationRange.end);
  assert.deepEqual(contourOracle.cornerShapeCaptureErrors,[],'Corner-shape console evidence capture failed');
  assert.deepEqual(observed.map(row=>row.stage),['before-validation','before-validation','rejected'],
   'Preflight must retain raw computed values before both validations and on rejection');
  assert.deepEqual(observed[0].cornerShapes,result.positive.cornerShapes);
  assert.deepEqual(observed[1].cornerShapes,observed[2].cornerShapes);
  assert.equal(observed[2].cornerShapes[0].raw,result.negative.raw);
  preflight.passed=true;
  checks.push({check:'actual browser computed round accepted and noncircular corner rejected by same sampler'});
 }catch(error){preflight.error=errorEvidence(error);throw error}
 finally{preflight.observationRange.end=contourOracle.cornerShapeObservations.length}
}
async function contourNegativeChecks(control){
 await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
 const before=await control.evaluate(node=>window.__fileControlsContourSample(node));
 nativeHitOracle(before,'negative baseline');
 for(const dimension of ['width','height']){
  assert.throws(()=>nativeHitOracle({...before,rect:{...before.rect,[dimension]:43.99}},'negative size'),/target below 44px/);
  contourOracle.negativeCounts.size++;
  checks.push({check:'negative control: native size below 44px rejected',dimension});
 }
 for(const [index,point] of before.corners.entries()){
  const variant={...before,corners:before.corners.map((value,i)=>i===index?{...value,reachable:false}:value)};
  assert.throws(()=>nativeHitOracle(variant,'negative region'),/obscured\/offscreen contour probe/);
  contourOracle.negativeCounts.regions++;
  checks.push({check:'negative control: individual contour region rejected',corner:point.corner});
 }
 // A real pointer-intercepting 2x2px DOM overlay, not just a falsified sample.
 // It covers exactly the first probe; no app CSS, layout, or event is changed.
 const negative={label:'negative-contour-overlay',before,removed:false};
 geometry.push(negative);
 const overlay=await control.evaluateHandle((node,point)=>{
  const overlay=document.createElement('div');overlay.dataset.fileControlsNegative='one-contour-probe';
  overlay.style.cssText=`all:initial;position:fixed;left:${point.x-1}px;top:${point.y-1}px;width:2px;height:2px;`+
   'box-sizing:border-box;margin:0;padding:0;border:0;border-radius:0;pointer-events:auto;z-index:2147483647;';
  document.documentElement.append(overlay);return overlay;
 },before.corners[0]);
 try{
  const blocked=await control.evaluate(node=>window.__fileControlsContourSample(node));
  // Do not mix a deliberately failed sample into positive geometry.corners.
  negative.sample=blocked;
  assert.deepEqual(blocked.rect,before.rect,'Overlay must not change control layout');
  assert.deepEqual(blocked.corners.map(({x,y})=>({x,y})),before.corners.map(({x,y})=>({x,y})));
  assert.deepEqual(blocked.corners.map(point=>point.reachable),[false,true,true,true],'Overlay must obstruct exactly ONE probe');
  negative.hitOverlay=await overlay.evaluate((node,point)=>document.elementFromPoint(point.x,point.y)===node,before.corners[0]);
  assert.equal(negative.hitOverlay,true);
  assert.throws(()=>nativeHitOracle(blocked,'negative overlay'),/obscured\/offscreen contour probe/);
  negative.rejected=true;
  contourOracle.negativeCounts.obstruction++;
  checks.push({check:'negative control: real one-probe pointer overlay rejected by same contour oracle',corner:before.corners[0].corner});
 }finally{
  try{
   negative.removed=await overlay.evaluate(node=>{node.remove();return !node.isConnected});
   assert.equal(negative.removed,true,'Negative overlay was not removed');
  }
  finally{await overlay.dispose()}
 }
 const restored=await control.evaluate(node=>window.__fileControlsContourSample(node));
 negative.restored=restored;
 nativeHitOracle(restored,'overlay removed');
 assert.deepEqual(restored.corners,before.corners,'Removing overlay must restore the original hit targets');
}

async function measureControls(target,controls,label){
 const diagnostic=cornerDiagnosticMode&&label==='desktop';
 for(const [controlIndex,control] of controls.entries()){
  await control.scrollIntoViewIfNeeded();
  // The inspector has a sticky heading. Center within real scroll containers
  // before hit testing rather than accepting a button covered by that heading.
  await control.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
  const sample=(node,diagnostic)=>{
   const contour=diagnostic?null:window.__fileControlsContourSample(node);
   const r=node.getBoundingClientRect(),inset=3,style=getComputedStyle(node);
   const points=[[r.left+inset,r.top+inset],[r.right-inset,r.top+inset],
    [r.left+inset,r.bottom-inset],[r.right-inset,r.bottom-inset]];
   const hits=[];
   const measured={name:node.getAttribute('aria-label')||node.textContent.trim(),tag:node.tagName,className:node.className,
    rect:r.toJSON(),computed:{height:style.height,minHeight:style.minHeight,minWidth:style.minWidth,
     padding:style.padding,borderWidth:style.borderWidth,boxSizing:style.boxSizing,
     whiteSpace:style.whiteSpace,overflowWrap:style.overflowWrap},
    context:{inspector:!!node.closest('.a-canvas-inspector'),viewTools:!!node.closest('.a-canvas-view-tools')},
    ...(contour||{}),viewport:contour?.viewport||{width:innerWidth,height:innerHeight},corners:contour?.corners||points.map(([x,y],index)=>({x,y,
     reachable:x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&node.contains(
      diagnostic?(hits[index]=document.elementFromPoint(x,y)):document.elementFromPoint(x,y))}))};
   if(diagnostic){
    // No await/RAF, repeated oracle, UI write or text capture. The oracle above
    // and this diagnostic share the FIRST rect and exact elementFromPoint hits.
    const capture={complete:false,first:{rect:measured.rect,corners:measured.corners}};
    measured.cornerDiagnostic=capture;
    try{
     const id=window.__fileControlsNodeId;
     const describe=element=>element?{nodeId:id(element),tag:element.tagName,connected:element.isConnected}:null;
     const properties=['borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius',
      'borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth',
      'borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle',
      'pointerEvents','display','visibility','opacity','position','zIndex','transform','transformOrigin',
      'clip','clipPath','overflowX','overflowY','overflowClipMargin'];
     const computed=value=>Object.fromEntries(properties.map(key=>[key,value[key]]));
     const corners=measured.corners.map((point,index)=>{
      const stack=document.elementsFromPoint(point.x,point.y);
      return {...point,corner:['top-left','top-right','bottom-left','bottom-right'][index],
       hitTested:index in hits,hit:describe(hits[index]),stack:stack.slice(0,8).map(describe),
       stackTruncated:stack.length>8};
     });
     const ancestors=[];let parent=node.parentElement;
     for(;parent&&ancestors.length<16;parent=parent.parentElement){
      ancestors.push({...describe(parent),rect:parent.getBoundingClientRect().toJSON(),
       computed:computed(getComputedStyle(parent)),scroll:{x:parent.scrollLeft,y:parent.scrollTop,
        clientWidth:parent.clientWidth,clientHeight:parent.clientHeight,
        scrollWidth:parent.scrollWidth,scrollHeight:parent.scrollHeight}});
     }
     Object.assign(capture.first,{at:Date.now(),node:describe(node),corners,computed:computed(style),
      controlRadius:style.getPropertyValue('--a-control-radius'),ancestors,ancestorsTruncated:!!parent,
      viewport:{...measured.viewport,dpr:devicePixelRatio,visual:visualViewport&&{
       width:visualViewport.width,height:visualViewport.height,offsetLeft:visualViewport.offsetLeft,
       offsetTop:visualViewport.offsetTop,pageLeft:visualViewport.pageLeft,pageTop:visualViewport.pageTop,scale:visualViewport.scale}},
      scroll:{x:scrollX,y:scrollY},fonts:{status:document.fonts.status,ready:document.fonts.status==='loaded'}});
     capture.complete=true;
    }catch{
     // Missing diagnostics must fail, but never replace an original false corner.
     capture.captureError='Synchronous corner diagnostic capture failed';
    }
   }
   return measured;
  };
  let result,disposeError;
  if(diagnostic){
   const handle=await control.elementHandle();
   assert.ok(handle,'Corner diagnostic control handle missing');
   try{result=await handle.evaluate(sample,true)}
   finally{try{await handle.dispose()}catch(error){disposeError=error}}
  }else result=await control.evaluate(sample,false);
  const {cornerDiagnostic,...measured}=result;
  if(diagnostic)cornerDiagnostics.push({label,controlIndex,...cornerDiagnostic,
   ...(disposeError?{disposeError:errorEvidence(disposeError)}:{})});
  geometry.push({label,...measured});
  if(diagnostic){
   nativeSizeOracle(measured,`${label} ${measured.name}`);
   assert.ok(measured.corners.every(point=>point.reachable),`${label} ${measured.name}: obscured/offscreen legacy corner`);
  }else nativeHitOracle(measured,`${label} ${measured.name}`);
  if(diagnostic)assert.equal(cornerDiagnostic?.complete,true,'Corner diagnostic capture missing or incomplete');
  if(disposeError)throw disposeError;
 }
 assert.ok(await target.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Document horizontal overflow');
}
// One synchronous, RAF-aligned page read per sample: include OFFSCREEN siblings,
// not only the path just centered by the existing journey. No scroll or UI writes.
function fileFrameSnapshot(anchor){
 return new Promise((resolve,reject)=>requestAnimationFrame(rafAt=>{try{
  const id=window.__fileControlsNodeId,rect=node=>node.getBoundingClientRect().toJSON();
  const describe=node=>({nodeId:id(node),tag:node.tagName,id:node.id,className:node.getAttribute('class'),
   connected:node.isConnected});
  const properties=['display','visibility','opacity','position','zIndex','top','transform','clipPath',
   'width','height','minWidth','minHeight','maxWidth','maxHeight','boxSizing','margin','padding',
   'borderWidth','overflowX','overflowY','whiteSpace','overflowWrap','wordBreak','textOverflow',
   'fontSize','lineHeight','color','backgroundColor','pointerEvents','gridTemplateColumns','gap'];
  const computed=node=>{const style=getComputedStyle(node);return Object.fromEntries(properties.map(key=>[key,style[key]]))};
  const details=anchor.matches('details.a-canvas-file-path')?[anchor]:
   [...anchor.closest('#workspace-canvas').querySelectorAll('details.a-canvas-file-path')];
  const headers=[...document.querySelectorAll('header,.a-canvas-artifact-toolbar')];
  const toolbars=[...document.querySelectorAll('.a-canvas-artifact-toolbar')];
  const selector='code,p,summary,button,label,strong,[role="status"],[role="alert"]';
  const nodes=[...new Set([anchor,...details.flatMap(node=>[node,...node.querySelectorAll(selector)]),
   ...headers,...toolbars.flatMap(node=>[...node.querySelectorAll(selector)])])];
  const textRects=node=>{
   // Paragraphs can contain code blocks. Keep their OWN label fragments separate
   // from the code's lines rather than calling the whole paragraph a label line.
   const contents=node.tagName==='CODE'?[node]:[...node.childNodes].filter(child=>child.nodeType===3&&child.textContent.trim());
   return contents.flatMap(child=>{const range=document.createRange();range.selectNodeContents(child);
    return [...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>r.toJSON())});
  };
  const elements=nodes.map(node=>({...describe(node),label:node.getAttribute('aria-label'),text:node.textContent,rect:rect(node),
   rects:[...node.getClientRects()].map(r=>r.toJSON()),textRects:textRects(node),computed:computed(node),
   path:node.tagName==='CODE',header:headers.includes(node),open:node.open??null}));
  const ancestors=new Set();
  for(const node of nodes)for(let parent=node;parent;parent=parent.parentElement)ancestors.add(parent);
  const scrollContainers=[...ancestors].filter(node=>{
   const style=getComputedStyle(node);
   return node===document.scrollingElement||/(auto|scroll|hidden|clip)/.test(style.overflowX+' '+style.overflowY)||
    node.scrollWidth>node.clientWidth||node.scrollHeight>node.clientHeight;
  }).map(node=>({...describe(node),rect:rect(node),x:node.scrollLeft,y:node.scrollTop,
   clientWidth:node.clientWidth,clientHeight:node.clientHeight,scrollWidth:node.scrollWidth,scrollHeight:node.scrollHeight,
   overflowX:getComputedStyle(node).overflowX,overflowY:getComputedStyle(node).overflowY}));
  const scrollIds=new Set(scrollContainers.map(node=>node.nodeId));
  nodes.forEach((node,index)=>{
   const ids=[];for(let parent=node;parent;parent=parent.parentElement)if(scrollIds.has(id(parent)))ids.push(id(parent));
   elements[index].scrollContainerIds=ids;
  });
  const overlap=(a,b)=>{
   const left=Math.max(a.left,b.left),top=Math.max(a.top,b.top),right=Math.min(a.right,b.right),bottom=Math.min(a.bottom,b.bottom);
   return right>left&&bottom>top?{left,top,right,bottom,width:right-left,height:bottom-top}:null;
  };
  const intersections=[],headerIntersections=[];
  for(let a=0;a<elements.length;a++)for(let b=a+1;b<elements.length;b++){
   const first=elements[a],second=elements[b],box=overlap(first.rect,second.rect);
   if(!box)continue;
   const text=[];
   first.textRects.forEach((r,i)=>second.textRects.forEach((s,j)=>{const intersection=overlap(r,s);
    if(intersection)text.push({firstLine:i,secondLine:j,rect:intersection})}));
   intersections.push({first:first.nodeId,second:second.nodeId,rect:box,text,
    nested:nodes[a].contains(nodes[b])||nodes[b].contains(nodes[a])});
  }
  for(const [index,item] of elements.entries())for(const [line,r] of item.textRects.entries())for(const header of headers){
   if(header.contains(nodes[index]))continue;
   const intersection=overlap(r,rect(header));if(!intersection)continue;
   const x=intersection.left+intersection.width/2,y=intersection.top+intersection.height/2;
   const inViewport=x>=0&&y>=0&&x<innerWidth&&y<innerHeight,hit=inViewport?document.elementFromPoint(x,y):null;
   headerIntersections.push({nodeId:item.nodeId,line,headerId:id(header),rect:intersection,
    sample:{x,y,inViewport,hit:hit&&{nodeId:id(hit),tag:hit.tagName,className:hit.getAttribute('class')},
     headerOnTop:!!hit&&header.contains(hit)}});
  }
  resolve({at:Date.now(),rafAt,state:{documentId:id(document),url:location.href,
   anchorId:id(anchor),anchorConnected:anchor.isConnected,
   viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio,visual:visualViewport&&{
    width:visualViewport.width,height:visualViewport.height,offsetLeft:visualViewport.offsetLeft,
    offsetTop:visualViewport.offsetTop,pageLeft:visualViewport.pageLeft,pageTop:visualViewport.pageTop,scale:visualViewport.scale}},
   scroll:{x:scrollX,y:scrollY,containers:scrollContainers},elements,intersections,headerIntersections}});
 }catch(error){reject(error)}}));
}
async function captureFileFrame(target,anchor,label){
 const path=join(evidence,label+'.png');
 if(target.viewportSize()?.width!==320){await target.screenshot({path});return}
 const capture={label,png:{path},visualJudgment:'pending',
  binding:{atomic:false,status:'not-matched',basis:'Exact before/after RAF samples only; no continuous or atomic pixel-stability claim.'}};
 fileFrameCaptures.push(capture);
 let handle;
 try{
  handle=await anchor.elementHandle();
  capture.before=await target.evaluate(fileFrameSnapshot,handle);
  const png=await target.screenshot({path}); // Immediate next browser operation; same existing shot.
  capture.png.sha256=digest(png);capture.png.bytes=png.length;
  capture.after=await target.evaluate(fileFrameSnapshot,handle);
  const before=capture.before.state,after=capture.after.state,same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  const scroll=state=>({x:state.scroll.x,y:state.scroll.y,
   containers:state.scroll.containers.map(({nodeId,x,y})=>({nodeId,x,y}))});
  Object.assign(capture.binding,{
   domIdentityUnchanged:before.anchorConnected&&after.anchorConnected&&before.documentId===after.documentId&&
    before.anchorId===after.anchorId&&same(before.elements.map(item=>item.nodeId),after.elements.map(item=>item.nodeId)),
   viewportUnchanged:same(before.viewport,after.viewport),scrollUnchanged:same(scroll(before),scroll(after)),
   layoutUnchanged:same(before,after)});
  if(before.viewport.width===320&&after.viewport.width===320&&capture.binding.domIdentityUnchanged&&
   capture.binding.viewportUnchanged&&capture.binding.scrollUnchanged&&capture.binding.layoutUnchanged)capture.binding.status='matched-before-after-only';
 }catch(error){capture.error=errorEvidence(error);throw error}
 finally{await handle?.dispose()}
}
async function measure(target,details,label){
 await measureControls(target,[details.locator('summary'),...names.map(name=>button(details,name))],label);
 assert.ok(await details.evaluate(node=>node.scrollWidth<=node.clientWidth),'File-details horizontal overflow');
 await captureFileFrame(target,details,label+'-controls');
 await details.locator('summary').scrollIntoViewIfNeeded();
 await captureFileFrame(target,details,label+'-paths');
 // Inspect the text itself, separately from button visibility. Center each path
 // in the real scroll container; 320px shots additionally retain sibling geometry
 // and explicit PNG brackets, including raw occlusion under the sticky heading.
 // This investigates the earlier narrow path-overlap observation without
 // assuming that reachable buttons imply unobscured path text.
 const codes=details.locator('code');
 for(let index=0;index<await codes.count();index++){
  const code=codes.nth(index);
  await code.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
  const textGeometry=await code.evaluate(node=>{
   const range=document.createRange();range.selectNodeContents(node);
   const style=getComputedStyle(node),rect=node.getBoundingClientRect();
   const lines=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>{
    const y=r.top+r.height/2,inset=Math.min(1,r.width/4);
    return {rect:r.toJSON(),points:[r.left+inset,r.left+r.width/2,r.right-inset].map(x=>{
     const hit=document.elementFromPoint(x,y);
     return {x,y,reachable:x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&node.contains(hit),
      hit:hit&&{tag:hit.tagName,className:hit.className}};
    })};
   });
   return {text:node.textContent,rect:rect.toJSON(),clientWidth:node.clientWidth,scrollWidth:node.scrollWidth,
    computed:{whiteSpace:style.whiteSpace,overflowWrap:style.overflowWrap,fontSize:style.fontSize,lineHeight:style.lineHeight},
    viewport:{width:innerWidth,height:innerHeight},lines};
  });
  geometry.push({label:label+'-path-text',index,...textGeometry});
  await captureFileFrame(target,details,`${label}-path-${index}`);
  assert.ok(textGeometry.lines.length>0,`${label} path ${index}: no rendered text lines`);
  assert.ok(textGeometry.scrollWidth<=textGeometry.clientWidth,`${label} path ${index}: horizontal text overflow`);
  assert.ok(textGeometry.lines.every(line=>line.points.every(point=>point.reachable)),
   `${label} path ${index}: text line is covered or outside viewport`);
 }
}
// Read-only geometry for the real reader -> toolbar + body seam. Installed only
// in qualification mode; no synthetic toolbar, extra gesture or layout mutation.
function coarseToolbarSnapshot(node){
 const toolbar=node.closest('.a-canvas-artifact-toolbar'),body=toolbar?.nextElementSibling;
 const reader=toolbar?.parentElement,panel=toolbar?.closest('#workspace-canvas');
 if(!body?.matches('.a-canvas-body')||!reader?.matches('.a-canvas-reader')||!panel)
  throw Error('Saved-file toolbar is not followed by the whole Canvas body in its reader');
 const properties=['display','visibility','marginTop','padding','minWidth','minHeight','overflowX','overflowY',
  'pointerEvents','touchAction','userSelect','flexDirection','borderTopLeftRadius','borderTopRightRadius',
  'borderBottomLeftRadius','borderBottomRightRadius'];
 const describe=element=>{
  const style=getComputedStyle(element);
  return {nodeId:window.__fileControlsNodeId(element),tag:element.tagName,className:element.className,
   rect:element.getBoundingClientRect().toJSON(),connected:element.isConnected,hidden:element.hidden,
   visible:element.checkVisibility({checkVisibilityCSS:true})&&element.getClientRects().length>0,
   computed:Object.fromEntries(properties.map(key=>[key,style[key]])),
   clientWidth:element.clientWidth,scrollWidth:element.scrollWidth};
 };
 const notices=[...body.querySelectorAll('.a-renderer-notice')],style=getComputedStyle(node);
 return {at:Date.now(),queries:{coarse:matchMedia('(pointer:coarse)').matches,fine:matchMedia('(pointer:fine)').matches},
  viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio,scale:visualViewport?.scale??1,
   offsetLeft:visualViewport?.offsetLeft??0,offsetTop:visualViewport?.offsetTop??0},
  scroll:{x:scrollX,y:scrollY},documentWidth:document.documentElement.scrollWidth,
  panelMode:panel.dataset.mode,settings:!!body.querySelector(':scope > .a-settings-canvas'),
  adjacent:toolbar.nextElementSibling===body&&body.parentElement===reader,
  reader:describe(reader),toolbar:describe(toolbar),body:describe(body),button:describe(node),
  radii:[style.borderTopLeftRadius,style.borderTopRightRadius,style.borderBottomLeftRadius,style.borderBottomRightRadius],
  wrappers:['.a-canvas-primary-actions','.a-canvas-common-actions','.a-saved-file-download'].map(selector=>{
   const element=node.closest(selector);return {selector,node:element?describe(element):null};
  }),
  notices:notices.slice(0,4).map(describe),noticesTruncated:notices.length>4,
  contactEnvelope:{radiusDip:16,radiusCssPx:16,bottom:node.getBoundingClientRect().bottom+16,
   basis:'Maximum 16 DIP contact radius; qualified only at DPR=1 and visual scale=1.'}};
}
function coarseToolbarLayoutOracle(layout,coarse){
 assert.deepEqual(layout.queries,{coarse,fine:!coarse},'Expected primary pointer query did not match');
 assert.equal(layout.adjacent,true);assert.equal(layout.panelMode,'artifact');assert.equal(layout.settings,false);
 assert.equal(layout.reader.computed.display,'flex');assert.equal(layout.reader.computed.flexDirection,'column');
 assert.equal(layout.toolbar.hidden,false);assert.equal(layout.toolbar.visible,true);
 assert.equal(layout.body.visible,true);assert.equal(layout.button.visible,true);
 const margin=coarse?14:0;
 assert.equal(layout.body.computed.marginTop,`${margin}px`,'Wrong external Canvas body margin');
 assert.equal(layout.body.rect.top,layout.toolbar.rect.bottom+margin,'Gap must precede the entire Canvas body');
 for(const box of [layout.toolbar,layout.body,layout.button]){
  assert.ok(box.connected&&box.rect.width>0&&box.rect.height>0,'Toolbar/body/control must remain rendered');
  assert.ok(box.rect.left>=0&&box.rect.right<=layout.viewport.width&&box.rect.top>=0&&
   box.rect.bottom<=layout.viewport.height,'Toolbar/body/control clipped by viewport');
  assert.ok(box.rect.left>=layout.reader.rect.left&&box.rect.right<=layout.reader.rect.right&&
   box.rect.top>=layout.reader.rect.top&&box.rect.bottom<=layout.reader.rect.bottom,'Toolbar/body/control clipped by reader');
  assert.ok(box.scrollWidth<=box.clientWidth,'Toolbar/body/control horizontal overflow');
  assert.notEqual(box.computed.pointerEvents,'none','Separation must not suppress hit testing');
  assert.notEqual(box.computed.touchAction,'none','Separation must not disable touch action');
 }
 assert.ok(layout.documentWidth<=layout.viewport.width,'Document horizontal overflow');
 assert.equal(layout.button.computed.minWidth,'44px');assert.equal(layout.button.computed.minHeight,'44px');
 assert.ok(layout.button.rect.width>=44&&layout.button.rect.height>=44,'Native button below 44px');
 assert.ok(layout.wrappers.every(row=>row.node?.computed.display==='contents'),'Plain toolbar wrappers must stay display:contents');
 assert.equal(layout.noticesTruncated,false,'Notice geometry exceeded four records');
 assert.ok(layout.notices.some(notice=>notice.visible),'Saved-version notice must remain visible');
 for(const notice of layout.notices.filter(notice=>notice.visible)){
  assert.ok(notice.rect.top>=layout.body.rect.top&&notice.rect.bottom<=layout.body.rect.bottom&&
   notice.rect.left>=layout.body.rect.left&&notice.rect.right<=layout.body.rect.right,'Notice clipped by body');
  assert.notEqual(notice.computed.userSelect,'none','Saved-version text must remain selectable');
  assert.notEqual(notice.computed.pointerEvents,'none','Saved-version text must retain hit testing');
 }
 if(coarse){
  assert.equal(layout.viewport.dpr,1);assert.equal(layout.viewport.scale,1);
  assert.equal(layout.viewport.offsetLeft,0);assert.equal(layout.viewport.offsetTop,0);
  assert.equal(layout.contactEnvelope.bottom,layout.button.rect.bottom+16);
  assert.ok(layout.body.rect.top>layout.contactEnvelope.bottom,'Whole following body intersects the maximum contact envelope');
 }
}
async function qualifyToolbarLayout(control,label,phase,coarse){
 const row={check:'saved-file toolbar external separation and clipping',label,phase,passed:false};
 coarseToolbarQualification.layoutChecks.push(row);
 row.snapshot=await control.evaluate(node=>window.__fileControlsToolbarSnapshot(node));
 coarseToolbarLayoutOracle(row.snapshot,coarse);row.passed=true;
}
function qualifyToolbarBaseline(gesture){
 const row={check:'first-390 baseline geometry and contour point unchanged',passed:false,
  fields:Object.keys(coarseToolbarBaseline.before)};
 gesture.baselineCheck=row;
 for(const field of row.fields)assert.deepEqual(gesture.before[field],coarseToolbarBaseline.before[field],
  `First-390 ${field} changed from the bound failed gesture; do not move or retry the tap`);
 coarseToolbarLayoutOracle(gesture.beforeLayout,true);
 assert.deepEqual(gesture.beforeLayout.toolbar.rect,coarseToolbarBaseline.toolbarRect,'Candidate moved or resized toolbar');
 assert.deepEqual(gesture.beforeLayout.button.rect,gesture.before.rect);
 assert.deepEqual(gesture.beforeLayout.radii,gesture.before.radii);
 assert.equal(gesture.beforeLayout.body.rect.top,coarseToolbarBaseline.bodyTop+14);
 row.bodyGap=gesture.beforeLayout.body.rect.top-gesture.before.rect.bottom;
 assert.equal(row.bodyGap,16.5);row.passed=true;
}
function qualifyToolbarEvents(gesture){
 const capture=gesture.capture,events=capture.events;
 const row={check:'first-390 trusted pointerdown and click on original button',passed:false};
 gesture.eventCheck=row;
 assert.equal(gesture.tapAttempts,1);assert.equal(gesture.checkpointObserved,true);
 assert.deepEqual(gesture.captureErrors,[]);assert.deepEqual(capture.errors,[]);
 assert.ok(Object.values(capture.truncations).every(count=>count===0),'Truncated event capture cannot qualify');
 const hasTruncatedField=value=>!!value&&typeof value==='object'&&Object.entries(value).some(([key,child])=>
  (key.endsWith('Truncated')&&child===true)||hasTruncatedField(child));
 assert.equal(hasTruncatedField(events),false,'Truncated descriptor or layout cannot qualify');
 const types=['pointerdown','pointerup','mousedown','mouseup','click'];
 const pair=types.map(type=>{
  const matches=events.filter(event=>event.type===type);
  assert.equal(matches.length,1,`Require exactly one ${type}; never retry input`);
  return matches[0];
 });
 assert.ok(pair.every((event,index)=>index===0||event.index>pair[index-1].index),'Gesture event order changed');
 assert.equal(events.filter(event=>['pointercancel','touchcancel'].includes(event.type)).length,0);
 const pointerdown=pair[0],mousedown=pair[2],click=pair[4];
 assert.equal(pointerdown.pointerType,'touch');assert.equal(mousedown.sourceCapabilities?.firesTouchEvents,true);
 for(const event of pair){
  assert.equal(event.isTrusted,true);assert.equal(event.sameNode,true,`${event.type} must target the original Download button`);
  assert.equal(event.intendedButton.nodeId,gesture.before.nodeId);assert.equal(event.intendedButton.connected,true);
  assert.ok(event.composedPath.some(node=>node.nodeId===gesture.before.nodeId),'Original button missing from event path');
  assert.deepEqual(event.intendedButton.rect,gesture.before.rect,`${event.type} button rectangle changed`);
  assert.deepEqual(event.intended,gesture.before.intended);
  assert.ok(event.actual&&event.actual.x>=gesture.before.rect.left&&event.actual.x<gesture.before.rect.right&&
   event.actual.y>=gesture.before.rect.top&&event.actual.y<gesture.before.rect.bottom,'Actual event outside original button');
  coarseToolbarLayoutOracle(event.toolbarLayout,true);
  assert.deepEqual(event.toolbarLayout.button.rect,gesture.before.rect);assert.deepEqual(event.toolbarLayout.radii,gesture.before.radii);
 }
 Object.assign(row,{passed:true,buttonNodeId:gesture.before.nodeId,pointerdownIndex:pointerdown.index,
  firstMousedownIndex:mousedown.index,clickIndex:click.index,
  actual:{pointerdown:pointerdown.actual,mousedown:mousedown.actual,click:click.actual},
  mouseDelta:{x:mousedown.actual.x-pointerdown.actual.x,y:mousedown.actual.y-pointerdown.actual.y}});
}
// Read-only topology falsifier. All geometry expectations come from check 17.
// Identity numbers are local to this browser; compare them within this gesture,
// not across runs. No CDP trace or added target listener is needed in this mode.
function portalHostSnapshot(node){
 const toolbar=node.closest('.a-canvas-artifact-toolbar'),host=node.closest('.a-canvas-primary-actions');
 const details=toolbar.querySelector(':scope > .a-canvas-details'),body=toolbar.nextElementSibling;
 const button=value=>{
  const sample=window.__fileControlsContourSample(value),style=getComputedStyle(value);
  return {nodeId:sample.nodeId,connected:value.isConnected,geometry:{
   ...Object.fromEntries(['rect','radii','borders','viewport','transforms','cornerShapes'].map(key=>[key,sample[key]])),
   scroll:{x:sample.scroll.x,y:sample.scroll.y,ancestors:sample.scroll.ancestors.map(({x,y})=>({x,y}))},
   computed:Object.fromEntries(['height','minHeight','minWidth','padding','borderWidth','boxSizing','whiteSpace','overflowWrap']
    .map(key=>[key,style[key]]))},corners:sample.corners};
 };
 const box=value=>({nodeId:window.__fileControlsNodeId(value),connected:value.isConnected,
  rect:value.getBoundingClientRect().toJSON(),rectCount:value.getClientRects().length,
  computed:Object.fromEntries(['display','position','margin','padding','borderWidth','columnGap','rowGap','pointerEvents','touchAction']
   .map(key=>[key,getComputedStyle(value)[key]]))});
 const copy=host.querySelector('.a-canvas-common-actions > button');
 return {download:button(node),details:button(details),host:box(host),copy:box(copy),
  toolbar:box(toolbar),body:box(body),coarse:matchMedia('(pointer:coarse)').matches,dpr:devicePixelRatio,
  innerWrappers:[node.closest('.a-saved-file-download'),node.closest('.a-canvas-common-actions')].map(box)};
}
function portalHostOracle(snapshot,baseline){
 assert.equal(snapshot.coarse,true);assert.equal(snapshot.dpr,1);
 for(const key of ['download','details']){
  assert.deepEqual(snapshot[key].geometry,baseline[key],`${key} changed from native17; do not change the tap or baseline`);
  assert.equal(snapshot[key].connected,true);
  nativeHitOracle({tag:'BUTTON',rect:snapshot[key].geometry.rect,corners:snapshot[key].corners},`portal-host ${key}`);
 }
 assert.deepEqual(snapshot.toolbar.rect,baseline.toolbarRect);assert.deepEqual(snapshot.body.rect,baseline.bodyRect);
 assert.equal(snapshot.body.computed.margin,'0px','No body separation is permitted');
 const host=snapshot.host,copy=snapshot.copy,download=snapshot.download.geometry.rect;
 assert.equal(host.connected,true);assert.equal(host.rectCount,1);assert.equal(host.computed.display,'grid');
 for(const key of ['margin','padding','borderWidth'])assert.equal(host.computed[key],'0px');
 assert.equal(host.computed.position,'static');assert.notEqual(host.computed.pointerEvents,'none');
 assert.notEqual(host.computed.touchAction,'none');
 assert.deepEqual(copy.rect,baseline.copyRect);
 assert.deepEqual(host.rect,{...copy.rect,width:download.right-copy.rect.left,right:download.right},
  'Portal host must box only the existing Copy/Download tracks, not shield other controls');
 assert.equal(download.left-copy.rect.right,4);assert.equal(snapshot.details.geometry.rect.left-download.right,4);
 assert.ok(snapshot.innerWrappers.every(row=>row.connected&&row.computed.display==='contents'));
}
// Bounded, passive structure/focus reads for BOTH reduction gestures. This does
// not infer native clickability from handlers or reconstruct a DOM snapshot.
function reductionSnapshot(node){
 const id=window.__fileControlsNodeId,limits={descendants:24,ancestors:20},truncations=[];
 const describe=value=>{
  if(!value)return null;
  const style=getComputedStyle(value);
  const className=value.getAttribute('class')||'',label=value.getAttribute('aria-label')||'';
  if(className.length>160||label.length>160)truncations.push('descriptor');
  return {nodeId:id(value),tag:value.tagName,className:className.slice(0,160),label:label.slice(0,160),
   connected:value.isConnected,rect:value.getBoundingClientRect().toJSON(),rectCount:value.getClientRects().length,
   computed:Object.fromEntries(['display','visibility','pointerEvents','touchAction','userSelect','padding','margin',
    'borderWidth','borderRadius','position','overflowX','overflowY'].map(key=>[key,style[key]])),
   scroll:{x:value.scrollLeft,y:value.scrollTop},inert:value.inert,ariaHidden:value.getAttribute('aria-hidden')};
 };
 const descendants=[],walker=document.createTreeWalker(node,NodeFilter.SHOW_ELEMENT);
 let child;
 while((child=walker.nextNode())&&descendants.length<limits.descendants){
  const text=[...child.childNodes].filter(part=>part.nodeType===Node.TEXT_NODE).map(part=>part.textContent).join('');
  if(text.length>160)truncations.push('descendant-text');
  descendants.push({...describe(child),parentNodeId:id(child.parentElement),text:text.slice(0,160)});
 }
 if(child)truncations.push('descendants');
 const ancestors=[];let ancestor=node.parentElement;
 while(ancestor&&ancestors.length<limits.ancestors){ancestors.push(describe(ancestor));ancestor=ancestor.parentElement}
 if(ancestor)truncations.push('ancestors');
 const sample=value=>{
  const c=window.__fileControlsContourSample(value),style=getComputedStyle(value);
  return {nodeId:c.nodeId,geometry:{...Object.fromEntries(['rect','radii','borders','viewport','transforms','cornerShapes']
   .map(key=>[key,c[key]])),scroll:{x:c.scroll.x,y:c.scroll.y,ancestors:c.scroll.ancestors.map(({x,y})=>({x,y}))},
   computed:Object.fromEntries(['height','minHeight','minWidth','padding','borderWidth','boxSizing','whiteSpace','overflowWrap']
    .map(key=>[key,style[key]]))},corners:c.corners};
 };
 const toolbar=node.closest('.a-canvas-artifact-toolbar');
 return {at:Date.now(),limits,truncations,button:describe(node),descendants,ancestors,focus:describe(document.activeElement),
  context:{coarse:matchMedia('(pointer:coarse)').matches,dpr:devicePixelRatio,maxTouchPoints:navigator.maxTouchPoints},
  primary:toolbar?{download:sample(node),details:sample(toolbar.querySelector(':scope > .a-canvas-details')),
   toolbar:describe(toolbar),body:describe(toolbar.nextElementSibling),
   notice:describe(toolbar.nextElementSibling.querySelector('.a-renderer-notice'))}:null};
}
function reductionSnapshotOracle(snapshot,primary=false){
 assert.deepEqual(snapshot.truncations,[],'Reduction structure capture truncated');
 assert.equal(snapshot.context.coarse,true);assert.equal(snapshot.context.dpr,1);
 assert.ok(snapshot.context.maxTouchPoints>0);
 assert.equal(snapshot.button.connected,true);
 if(primary){
  const actual=snapshot.primary,expected=stateReductionBaseline;
  assert.ok(actual,'Primary reduction snapshot missing');
  for(const key of ['download','details']){
   assert.deepEqual(actual[key].geometry,expected[key],`Reduction ${key} differs from original failed geometry`);
   nativeHitOracle({tag:'BUTTON',rect:actual[key].geometry.rect,corners:actual[key].corners},`reduction ${key}`);
  }
  for(const key of ['toolbar','body','notice'])assert.deepEqual(actual[key].rect,expected[key+'Rect']);
  assert.equal(actual.body.computed.margin,'0px');
  const host=snapshot.ancestors.find(row=>row.className==='a-canvas-primary-actions');
  assert.equal(host?.computed.display,'contents','Reduction must use rolled-back product topology');
 }
}
function reductionCaptureTruncated(value){
 return !!value&&typeof value==='object'&&Object.entries(value).some(([key,child])=>
  (key.endsWith('Truncated')&&child===true)||reductionCaptureTruncated(child));
}
function reductionEventsComplete(gesture){
 const capture=gesture?.capture,events=capture?.events||[];
 const sequence=events.filter(event=>event.isTrusted&&['pointerdown','pointerup','mousedown','mouseup','click'].includes(event.type));
 return !!(gesture?.captureValid&&gesture.checkpointObserved&&gesture.originalListenerRemoved&&gesture.handleDisposed&&
  gesture.captureErrors?.length===0&&gesture.cleanupErrors?.length===0&&capture.errors.length===0&&
  Object.values(capture.truncations).every(n=>n===0)&&!reductionCaptureTruncated(events)&&
  JSON.stringify(sequence.map(event=>event.type))===JSON.stringify(['pointerdown','pointerup','mousedown','mouseup','click'])&&
  sequence.every(event=>event.isTrusted&&event.reductionSnapshot&&event.reductionSnapshot.truncations.length===0)&&
  gesture.settlement?.settled&&gesture.settlement.otherFailures.length===0&&
  gesture.settlement.operations.every(row=>row.state==='fulfilled'));
}
// Installed only at the first 390px primary Download, after the existing contour
// measurement/oracles and immediately before its unchanged locator.tap. All
// listeners are passive document capture observers. No event or DOM is changed.
function armTouchEventCapture(node,{intended,compactDecoration=false}){
 const types=['pointerover','pointerenter','pointerdown','pointermove','pointerup','pointercancel',
  'pointerout','pointerleave','gotpointercapture','lostpointercapture',
  'touchstart','touchmove','touchend','touchcancel','mouseover','mouseenter','mousemove',
  'mousedown','mouseup','click','mouseout','mouseleave','auxclick','contextmenu'];
 const limits={windowMs:2000,records:compactDecoration||window.__fileControlsReductionSnapshot?128:64,
  stack:16,path:24,ancestors:16,notices:4,touches:4,errors:8};
 const events=[],errors=[],truncations={records:0,stack:0,path:0,ancestors:0,notices:0,touches:0,errors:0};
 const id=window.__fileControlsNodeId,armedAt=performance.now(),deadline=armedAt+limits.windowMs;
 let timer,finished,resolveDone,lastPointerId=null;
 const done=new Promise(resolve=>{resolveDone=resolve});
 const short=element=>element?{nodeId:id(element),tag:element.tagName||element.nodeName||
  (element===window?'WINDOW':'UNKNOWN')}:null;
 const describe=element=>element?{...short(element),connected:element.isConnected??null,
  rect:element.getBoundingClientRect?.().toJSON()??null,
  className:typeof element.className==='string'?element.className.slice(0,160):null,
  classNameTruncated:typeof element.className==='string'&&element.className.length>160}:null;
 const take=(values,key)=>{const list=Array.from(values);truncations[key]+=Math.max(0,list.length-limits[key]);return list.slice(0,limits[key])};
 const hit=point=>({point,element:describe(document.elementFromPoint(point.x,point.y)),
  elements:take(document.elementsFromPoint(point.x,point.y),'stack').map(describe)});
 const stop=reason=>{
  if(!finished){
   clearTimeout(timer);
   for(const type of types)document.removeEventListener(type,listener,true);
   finished={reason,at:performance.now(),listenersRemoved:true,timerCleared:true};
   resolveDone();
  }
 };
 const listener=event=>{
  if(finished)return;
  const at=performance.now();
  if(at>=deadline){stop('deadline-observed');return}
  if(events.length>=limits.records){truncations.records++;return}
  const entry={index:events.length,type:event.type,at,wallTime:Date.now(),
   timeStamp:event.timeStamp,isTrusted:event.isTrusted};
  events.push(entry); // Keep a partial record if any synchronous read fails.
  try{
   const changed=event.changedTouches?take(event.changedTouches,'touches'):null;
   const coordinate=changed?changed[0]:event;
   const actual=coordinate&&Number.isFinite(coordinate.clientX)&&Number.isFinite(coordinate.clientY)?
    {x:coordinate.clientX,y:coordinate.clientY}:null;
   if(Number.isInteger(event.pointerId))lastPointerId=event.pointerId;
   const path=take(event.composedPath(),'path'),ancestors=[];
   let ancestor=event.target instanceof Element?event.target:null;
   while(ancestor&&ancestors.length<limits.ancestors){
    ancestors.push({...describe(ancestor),scroll:{x:ancestor.scrollLeft,y:ancestor.scrollTop}});
    ancestor=ancestor.parentElement;
   }
   if(ancestor)truncations.ancestors++;
   const notices=take((node.closest('#workspace-canvas')||document).querySelectorAll('.a-renderer-notice'),'notices');
   Object.assign(entry,{
    actual,coordinateSource:changed?'changedTouches[0]':'event.clientXY',intended,
    pointerType:event.pointerType??null,pointerId:event.pointerId??null,
    button:event.button??null,buttons:event.buttons??null,detail:event.detail??null,
    sourceCapabilities:event.sourceCapabilities?{firesTouchEvents:event.sourceCapabilities.firesTouchEvents}:null,
    changedTouches:changed?.map(touch=>({identifier:touch.identifier,x:touch.clientX,y:touch.clientY,target:short(touch.target)}))??null,
    touchesLength:event.touches?.length??null,changedTouchesLength:event.changedTouches?.length??null,
    target:describe(event.target),sameNode:node.contains(event.target),intendedButton:describe(node),
    notices:notices.map(describe),composedPath:path.map(short),ancestors,
    capture:{phase:event.eventPhase,currentTarget:short(event.currentTarget),defaultPrevented:event.defaultPrevented,
     cancelBubble:event.cancelBubble,cancelable:event.cancelable,pointerIdQueried:lastPointerId,
     queried:[...new Set([node,event.target,...path])].filter(item=>item instanceof Element).map(element=>({
      ...short(element),hasPointerCapture:lastPointerId===null?null:element.hasPointerCapture(lastPointerId)})),
     limitation:'Only these elements at document capture; pending capture, global capture owner and UA target adjustment are not exposed.'},
    activeElement:short(document.activeElement),scroll:{x:scrollX,y:scrollY},
    viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
     visual:visualViewport?{width:visualViewport.width,height:visualViewport.height,scale:visualViewport.scale,
      offsetLeft:visualViewport.offsetLeft,offsetTop:visualViewport.offsetTop,pageLeft:visualViewport.pageLeft,pageTop:visualViewport.pageTop}:null},
    atActual:actual?hit(actual):null,atIntended:hit(intended)
   });
   if(window.__fileControlsToolbarSnapshot)entry.toolbarLayout=window.__fileControlsToolbarSnapshot(node);
   if(window.__fileControlsPortalHostSnapshot&&['pointerdown','pointerup','mousedown','mouseup','click'].includes(event.type))
    entry.portalHost=window.__fileControlsPortalHostSnapshot(node);
   if(window.__fileControlsReductionSnapshot&&event.isTrusted&&['pointerdown','pointerup','mousedown','mouseup','click'].includes(event.type))
    entry.reductionSnapshot=window.__fileControlsReductionSnapshot(node);
  }catch(error){
   entry.captureError={name:error.name,message:String(error.message).slice(0,1024),
    messageTruncated:String(error.message).length>1024};
   if(errors.length<limits.errors)errors.push({index:entry.index,...entry.captureError});
   else truncations.errors++;
  }
 };
 const read=()=>({limits,types,armedAt,deadline,finished,events,errors,truncations});
 try{
  for(const type of types)document.addEventListener(type,listener,{capture:true,passive:true});
  timer=setTimeout(()=>stop('deadline'),limits.windowMs);
 }catch(error){stop('arm-error');throw error}
 return {wait:async()=>{await done;return read()},stop:()=>{stop('finally');return read()}};
}
// CDP exposes current DOM responsiveness/quads, not touch-adjustment candidates,
// pruning or scores. Observe the synthetic fixture only; do not add target
// listeners, DOM attributes, layout writes, input, launch flags or breakpoints.
async function prepareNativeState(target,original,measured,diagnostic,tap){
 const state={version:'native-state-v1',complete:false,errors:[],
  limits:{snapshotBytes:4*1024*1024,snapshotNodes:12000,documents:8,candidateNodes:64,
   descendantDepth:8,ancestorDepth:24,quadsPerNode:32,traceBytes:4*1024*1024,traceRecords:10000,
   hitTests:128,traceWindowMs:3000},
  styles:['display','visibility','pointer-events','touch-action','user-select','position',
   'overflow-x','overflow-y','transform','border-radius','padding'],
  limitations:[
   'isClickable is WillRespondToMouseClickEvents, not membership in a touch candidate list.',
   'Content quads are fresh AbsoluteQuads; display:contents wrappers may recurse into children, not retained decision geometry.',
   'HitTest EndData deliberately omits nodes for listBased tests. Point node/coordinates/flags cannot establish pruning or scoring.',
   'Clock correlation is a measured interval, not exact event identity. CDP reads and tracing may affect timing.']};
 diagnostic.nativeState=state;
 let session,traceStarted=false,endPromise,traceTimer,resolveComplete;
 const trace={records:[],bytes:0,recordCount:0,overflow:false,windowExpired:false,hitTests:[],
  config:{transferMode:'ReportEvents',traceConfig:{recordMode:'recordUntilFull',traceBufferSizeInKb:1024,
   includedCategories:['devtools.timeline','input']}}};state.trace=trace;
 const traceComplete=new Promise(resolve=>{resolveComplete=resolve});
 const send=(method,params={})=>tapOperation(tap,method,()=>session.send(method,params),2000);
 const data=({value})=>{
  for(const event of value){
   trace.recordCount++;
   const bytes=Buffer.byteLength(JSON.stringify(event));
   trace.bytes+=bytes;
   if(trace.recordCount>state.limits.traceRecords||trace.bytes>state.limits.traceBytes){
    trace.overflow=true;void end('capture-limit');continue; // Never call a truncated trace complete.
   }
   trace.records.push(event);
  }
 };
 const completed=value=>{trace.completion=value;resolveComplete(value)};
 const end=reason=>{
  if(!endPromise){
   clearTimeout(traceTimer);trace.endReason=reason;trace.endRequestedAt=Date.now();
   // End is submitted once. A timeout never replays this protocol mutation.
   endPromise=send('Tracing.end').then(()=>({}),error=>({error}));
  }
  return endPromise;
 };
 const saveJson=async(name,value,maxBytes)=>{
  const bytes=Buffer.from(JSON.stringify(value));
  assert.ok(bytes.length<=maxBytes,`${name} exceeds retained byte bound`);
  await writeFile(join(evidence,name),bytes,{flag:'wx'});
  return {file:name,bytes:bytes.length,sha256:digest(bytes)};
 };
 const finish=async()=>{
  const failures=[];
  if(traceStarted){
   const ended=await end('gesture-finally');if(ended.error)failures.push(ended.error);
   try{await tapOperation(tap,'trace-complete',()=>traceComplete,2000)}
   catch(error){failures.push(error)}
  }
  clearTimeout(traceTimer);state.timerCleared=true;
  if(!trace.completion){
   trace.observerCancelled=true;
   resolveComplete({cancelled:true}); // Settle the local observer, NOT a CDP completion receipt.
  }
  if(session){
   session.off('Tracing.dataCollected',data);session.off('Tracing.tracingComplete',completed);
   state.listenersRemoved=true;
   try{await tapOperation(tap,'release-native-objects',()=>session.send('Runtime.releaseObjectGroup',
    {objectGroup:'file-controls-native-state'}),1000);state.objectsReleased=true}
   catch(error){failures.push(error)}
   // Detach is attempted even after an owned-browser abort; rejection is evidence,
   // not permission to substitute another session or retry the tap.
   state.detachAttempted=true;
   const detached=session.detach().then(()=>{state.detached=true;return {}},error=>{
    state.detachError=errorEvidence(error);return {error};
   });
   tapPending.add(detached);detached.then(()=>tapPending.delete(detached));
   try{const result=await bounded(detached,1000,'Native CDP detach did not settle');if(result.error)failures.push(result.error)}
   catch(error){failures.push(error)}
  }
  try{
   if(traceStarted){
    trace.hitTests=trace.records.flatMap((event,index)=>event.name==='HitTest'?[{index,event}]:[]);
    assert.ok(trace.hitTests.length<=state.limits.hitTests,'HitTest subset exceeds bound');
    trace.artifact=await saveJson('native-gesture-trace.json',trace.records,state.limits.traceBytes);
    delete trace.records;
    assert.equal(trace.overflow,false,'Native trace records/bytes were lost');
    assert.equal(trace.windowExpired,false,'Native trace window exceeded 3000ms');
    assert.equal(trace.completion?.dataLossOccurred,false,'Native trace loss/missing completion');
    assert.ok(trace.hitTests.length>0,'Native trace contains no HitTest evidence');
   }
  }catch(error){failures.push(error)}
  state.errors.push(...failures.map(errorEvidence));
  state.complete=!!(state.snapshot&&state.candidates&&trace.artifact&&trace.completion&&
   state.detached&&state.objectsReleased&&!state.errors.length);
  return failures;
 };
 try{
  for(const field of Object.keys(coarseToolbarBaseline.before))
   assert.deepEqual(measured[field],coarseToolbarBaseline.before[field],`Native diagnostic changed baseline ${field}`);
  session=await tapOperation(tap,'native-cdp-session',()=>target.context().newCDPSession(target),2000);
  session.on('Tracing.dataCollected',data);session.on('Tracing.tracingComplete',completed);
  await send('DOM.enable');
  const document=await send('DOM.getDocument',{depth:0});
  const roots=[];state.roots=roots;
  for(const [kind,selector] of [
   ['download','#workspace-canvas .a-canvas-artifact-toolbar .a-saved-file-download>button'],
   ['details','#workspace-canvas .a-canvas-artifact-toolbar>.a-canvas-details'],
   ['notice','#workspace-canvas .a-canvas-body .a-renderer-notice'],
   ['toolbar','#workspace-canvas .a-canvas-artifact-toolbar']]){
   const {nodeIds}=await send('DOM.querySelectorAll',{nodeId:document.root.nodeId,selector});
   assert.equal(nodeIds.length,1,`Require exactly one native ${kind}`);
   const {node}=await send('DOM.describeNode',{nodeId:nodeIds[0],depth:0});
   const {object}=await send('DOM.resolveNode',{backendNodeId:node.backendNodeId,objectGroup:'file-controls-native-state'});
   const identity=await send('Runtime.callFunctionOn',{objectId:object.objectId,returnByValue:true,
    functionDeclaration:'function(){return {nodeId:window.__fileControlsNodeId(this),connected:this.isConnected,rect:this.getBoundingClientRect().toJSON()}}'});
   assert.ok(!identity.exceptionDetails&&identity.result.value?.connected,`Native ${kind} identity unavailable`);
   roots.push({kind,backendNodeId:node.backendNodeId,...identity.result.value});
  }
  assert.equal(roots[0].nodeId,measured.nodeId,'CDP Download is not the retained native control');
  assert.equal(await tapOperation(tap,'native-retained-identity',()=>original.evaluate(node=>window.__fileControlsNodeId(node))),roots[0].nodeId);
  assert.deepEqual(roots[0].rect,measured.rect);
  // Exactly ONE full snapshot; reject oversized capture rather than publish a
  // partial DOM as complete. Only bounded structural subsets go in observations.
  state.snapshotStartedAt=Date.now();
  const snapshot=await send('DOMSnapshot.captureSnapshot',{computedStyles:state.styles,
   includePaintOrder:true,includeDOMRects:true});
  assert.ok(snapshot.documents.length<=state.limits.documents&&snapshot.documents.reduce(
   (sum,doc)=>sum+doc.nodes.nodeName.length,0)<=state.limits.snapshotNodes,'DOM snapshot node/document bound exceeded');
  state.snapshot=await saveJson('native-dom-snapshot.json',snapshot,state.limits.snapshotBytes);
  state.snapshotFinishedAt=Date.now();
  const docIndex=snapshot.documents.findIndex(doc=>doc.nodes.backendNodeId.includes(roots[0].backendNodeId));
  assert.ok(docIndex>=0,'Native Download absent from snapshot');
  const doc=snapshot.documents[docIndex],nodes=doc.nodes,layout=doc.layout,strings=snapshot.strings;
  const rootIndexes=roots.map(root=>nodes.backendNodeId.indexOf(root.backendNodeId));
  assert.ok(rootIndexes.every(index=>index>=0),'Native target absent from same-document snapshot');
  const selected=[];
  for(let index=0;index<nodes.nodeName.length;index++){
   let parent=index,depth=0;
   while(parent>=0&&!rootIndexes.includes(parent)&&depth<=state.limits.descendantDepth){
    parent=nodes.parentIndex[parent];depth++;
   }
   if(parent>=0&&rootIndexes.includes(parent)){
    assert.ok(depth<=state.limits.descendantDepth,'Native descendant depth exceeded');
    selected.push(index);
   }else{
    // Detect a deeper descendant, not silently exclude it from the subset.
    while(parent>=0&&!rootIndexes.includes(parent))parent=nodes.parentIndex[parent];
    assert.ok(parent<0,'Native descendant omitted by depth bound');
   }
  }
  assert.ok(selected.length<=state.limits.candidateNodes,'Native candidate subset exceeds bound');
  const clickable=new Set(nodes.isClickable?.index||[]);
  state.candidates=[];
  for(const index of selected){
   const ancestry=[];let parent=nodes.parentIndex[index];
   while(parent>=0&&ancestry.length<state.limits.ancestorDepth){
    ancestry.push({index:parent,backendNodeId:nodes.backendNodeId[parent],name:strings[nodes.nodeName[parent]]});
    parent=nodes.parentIndex[parent];
   }
   assert.ok(parent<0,'Native ancestor depth exceeded');
   const layoutIndexes=layout.nodeIndex.flatMap((nodeIndex,i)=>nodeIndex===index?[i]:[]);
   const attributes=Object.fromEntries((nodes.attributes[index]||[]).reduce((pairs,key,i,list)=>
    i%2===0?[...pairs,[strings[key],strings[list[i+1]]]]:pairs,[]));
   const row={documentIndex:docIndex,index,backendNodeId:nodes.backendNodeId[index],
    parentIndex:nodes.parentIndex[index],nodeType:nodes.nodeType[index],name:strings[nodes.nodeName[index]],
    attributes:Object.fromEntries(['id','class','role','aria-label'].filter(key=>key in attributes).map(key=>[key,attributes[key]])),
    isClickable:clickable.has(index),ancestry,layout:layoutIndexes.map(i=>({index:i,bounds:layout.bounds[i],
     styles:Object.fromEntries(state.styles.map((name,j)=>[name,strings[layout.styles[i][j]]??null]))}))};
   const wrapper=(attributes.class||'').split(/\s+/).some(name=>
    ['a-canvas-primary-actions','a-canvas-common-actions','a-saved-file-download'].includes(name));
   state.candidates.push(row);
   if(row.nodeType===1&&(layoutIndexes.length||wrapper||rootIndexes.includes(index))){
    // Chromium may recurse into wrapper children; cap our explicit requests and
    // returned quads separately. An empty quad is data, not an invented box.
    let result;
    try{result=await send('DOM.getContentQuads',{backendNodeId:row.backendNodeId})}
    catch(error){row.quadsError=errorEvidence(error);throw error}
    assert.ok(result.quads.length<=state.limits.quadsPerNode,'Native quads exceed bound');
    row.quads=result.quads;row.quadsAt=Date.now();
   }else row.quadsNotRequested='non-element or no snapshot layout and not an action wrapper/root';
  }
  await send('Performance.enable');
  const before=await send('Performance.getMetrics');
  const clock=await send('Runtime.evaluate',{expression:'({now:performance.now(),timeOrigin:performance.timeOrigin})',returnByValue:true});
  const after=await send('Performance.getMetrics');
  assert.ok(!clock.exceptionDetails,'Native clock sample failed');
  const metric=value=>value.metrics.find(row=>row.name==='Timestamp')?.value;
  state.clock={before:metric(before),page:clock.result.value,after:metric(after),
   basis:'CDP monotonic Timestamp seconds brackets page performance.now milliseconds; trace ts is microseconds.'};
  assert.ok(Number.isFinite(state.clock.before)&&Number.isFinite(state.clock.after)&&
   Number.isFinite(state.clock.page.now)&&state.clock.after>=state.clock.before,'Native clock mapping unavailable');
  state.clock.offsetUs={min:state.clock.before*1e6-state.clock.page.now*1000,
   max:state.clock.after*1e6-state.clock.page.now*1000};
  state.prepared=true;
 }catch(error){
  state.errors.push(errorEvidence(error));const failures=await finish();
  throw new AggregateError([error,...failures],'Native-state preparation failed; no gesture admitted');
 }
 return {state,async start(){
  trace.startRequestedAt=Date.now();
  traceStarted=true; // Even an unknown start response must attempt end + detach.
  await send('Tracing.start',trace.config);
  trace.startedAt=Date.now();
  traceTimer=setTimeout(()=>{trace.windowExpired=true;void end('window-limit')},state.limits.traceWindowMs);
 },end,finish};
}
// Only the native-tap lifecycle uses this boundary. A timer is not cancellation:
// on an RPC deadline, close that tap's owned browser, then collect the ORIGINAL
// operation's outcome. A failed close/drain remains explicit, never "settled".
async function tapOperation(tap,name,perform,timeout=1000){
 const row={name,timeoutMs:timeout,state:'pending',startedAt:Date.now()};tap.record.operations.push(row);
 if(tap.abort){
  row.state='not-started';row.reason='tap browser shutdown already requested';
  throw Error(`${name}: browser shutdown already requested`);
 }
 const pending=Promise.resolve().then(perform).then(value=>{
  row.state='fulfilled';row.settledAt=Date.now();return {value};
 },error=>{row.state='rejected';row.settledAt=Date.now();row.error=errorEvidence(error);return {error}});
 tapPending.add(pending);pending.then(()=>tapPending.delete(pending));
 let outcome;
 try{outcome=await bounded(pending,timeout,`${name} exceeded ${timeout}ms`)}
 catch(deadline){
  row.deadlineExceeded=true;
  const failures=[deadline],abort={reason:name,state:'pending'};tap.record.browserAbort=abort;
  tap.abort=Promise.resolve().then(()=>{
   assert.ok(tap.owner,'Tap has no owned browser to shut down');
   return closeOwner(tap.owner);
  }).then(()=>{abort.state='fulfilled';return {}},error=>{
   abort.state='rejected';abort.error=errorEvidence(error);return {error};
  });
  tapPending.add(tap.abort);tap.abort.then(()=>tapPending.delete(tap.abort));
  try{
   const closed=await bounded(tap.abort,3000,'Owned tap browser shutdown did not settle');
   if(closed.error)failures.push(closed.error);
  }catch(error){abort.waitError=errorEvidence(error);failures.push(error)}
  try{
   const drained=await bounded(pending,1000,`${name} remained in flight after shutdown`);
   row.drainedAfterAbort=true;if(drained.error)failures.push(drained.error);
  }catch(error){row.drainError=errorEvidence(error);failures.push(error)}
  throw new AggregateError(failures,`${name} deadline; owned-browser shutdown and drain required`);
 }
 if(outcome.error)throw outcome.error;
 return outcome.value;
}
async function collectTouchEvents(probe,diagnostic,tap){
 const failures=[];
 try{diagnostic.capture=await tapOperation(tap,'passive-capture-wait',()=>probe.evaluate(value=>value.wait()),2500)}
 catch(error){failures.push(error)}
 finally{
  try{diagnostic.capture=await tapOperation(tap,'passive-capture-stop',()=>probe.evaluate(value=>value.stop()))}
  catch(error){failures.push(error)}
  try{await tapOperation(tap,'passive-capture-dispose',()=>probe.dispose());diagnostic.handleDisposed=true}
  catch(error){failures.push(error)}
 }
 diagnostic.captureErrors=failures.map(errorEvidence);
 const capture=diagnostic.capture,events=capture?.events||[];
 const first=type=>events.findIndex(event=>event.type===type);
 diagnostic.sequence=events.map(event=>event.type);
 diagnostic.pairPresence={pointerdown:first('pointerdown'),firstMousedown:first('mousedown')};
 diagnostic.checkpointObserved=!!(capture?.finished?.listenersRemoved&&capture.finished.timerCleared&&
  diagnostic.handleDisposed&&!failures.length&&!capture.errors.length&&
  Object.values(capture.truncations).every(count=>count===0)&&
  diagnostic.pairPresence.pointerdown>=0&&diagnostic.pairPresence.firstMousedown>diagnostic.pairPresence.pointerdown&&
  events[diagnostic.pairPresence.pointerdown].pointerType==='touch'&&
  ['pointerdown','mousedown'].every(type=>{const event=events[first(type)];return event?.isTrusted&&event.actual&&event.atActual}));
 diagnostic.outcome=diagnostic.checkpointObserved?'checkpoint_observed_cause_unknown':'unknown';
 return failures;
}
async function cornerTap(control,corner,diagnostic=null,beforeTap=null,precondition=null){
 const target=control.page(),record={corner,client:pageLabels.get(target),operations:[],settled:false,
  listenerCleanup:{state:'not-armed'},handleDisposed:false};
 tapSettlements.push(record);if(diagnostic)diagnostic.settlement=record;
 const tap={record,owner:browserOwners.find(owner=>owner.label===record.client),abort:null};
 let original,measured,originalFailure,listenerArmed=false,native;
 const diagnosticFailures=[];
 try{
 await tapOperation(tap,'scroll-control',()=>control.scrollIntoViewIfNeeded(),4500);
 await tapOperation(tap,'center-control',()=>control.evaluate(node=>
  node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'})),4500);
 assert.ok(['top-left','top-right','bottom-left','bottom-right'].includes(corner));
 // Retain ONCE while the role is visible. Later reads/cleanup never resolve the
 // role again: a wrong-target click can make this same node inert/aria-hidden.
 original=await tapOperation(tap,'retain-native-control',()=>control.elementHandle({timeout:4000}),4500);
 assert.ok(original,'Native control handle missing');
 record.listenerCleanup={state:'arm-unconfirmed'};
 measured=await tapOperation(tap,'measure-and-arm-pointer',()=>original.evaluate((node,corner)=>{
  const describe=element=>element&&({tag:element.tagName,id:element.id,className:element.className,
   label:element.getAttribute('aria-label')||element.textContent.trim().slice(0,120)});
  const sample=()=>{
   const contour=window.__fileControlsContourSample(node),r=contour.rect,borders=contour.borders;
   const point=contour.corners.find(point=>point.corner===corner);
   const intended={x:point.x,y:point.y};
   const oldPosition={x:intended.x-r.left,y:intended.y-r.top};
   // Playwright's explicit positions start at the padding box, not the border
   // box used by getBoundingClientRect and the shared contour oracle. Preserve
   // the old WRONG conversion as a negative even when it still hits this button.
   const position={x:oldPosition.x-borders.left,y:oldPosition.y-borders.top};
   const hit=point=>({point,reachable:point.x>=0&&point.y>=0&&point.x<innerWidth&&point.y<innerHeight&&
    node.contains(document.elementFromPoint(point.x,point.y)),
    elements:document.elementsFromPoint(point.x,point.y).map(describe)});
   const fromPosition=offset=>({x:r.left+borders.left+offset.x,y:r.top+borders.top+offset.y});
   return {...contour,corner,node:describe(node),
    intended,position,oldPosition,corrected:hit(fromPosition(position)),old:hit(fromPosition(oldPosition))};
  };
  const measured=sample();
  window.__fileControlsLastTap=null;
  const listener=event=>{
   const raw={x:event.clientX,y:event.clientY,pointerType:event.pointerType,isTrusted:event.isTrusted,
    target:describe(event.target),sameNode:node.contains(event.target)};
   // Save the raw target even if subsequent geometry sampling fails.
   window.__fileControlsLastTap={event:raw};
   try{
    window.__fileControlsLastTap={...sample(),event:raw};
   }catch(error){window.__fileControlsLastTap.captureError=error.message}
  };
  // Observe the delivered event even if it lands on a different native target.
  document.addEventListener('pointerdown',listener,{capture:true,once:true});
  window.__fileControlsCancelTap=()=>{
   document.removeEventListener('pointerdown',listener,true);
   return {nodeId:window.__fileControlsNodeId(node),listenerRemoved:true};
  };
  return measured;
 },corner));
 listenerArmed=true;record.nodeId=measured.nodeId;record.listenerCleanup={state:'armed',nodeId:measured.nodeId};
  const {corners,...tapBefore}=measured;
  geometry.push({label:'corner-tap-before',...tapBefore,acceptance:{basis:measured.basis,corners}});
  nativeHitOracle(measured,`touch ${corner}`);
  const exactCorner=sample=>{
   assert.deepEqual(sample.point,measured.intended,'Tap must preserve the measured contour viewport point');
   assert.ok(sample.reachable,'Exact contour probe must hit the native control');
  };
  exactCorner(measured.corrected);
  if(measured.borders.left||measured.borders.top){
   assert.throws(()=>exactCorner(measured.old),/Tap must preserve the measured contour viewport point/);
   contourOracle.negativeCounts.paddingOrigin++;
   checks.push({check:'old padding-origin conversion rejected by same exact-contour oracle',corner,
    oldPoint:measured.old.point,intended:measured.intended,oldReachable:measured.old.reachable});
  }else assert.deepEqual(measured.old.point,measured.corrected.point,'Zero border has no conversion difference');
  let probe;
  if(diagnostic){
   diagnostic.before=measured;
   if(diagnostic.qualification){
    diagnostic.beforeLayout=await tapOperation(tap,'baseline-layout',()=>original.evaluate(node=>window.__fileControlsToolbarSnapshot(node)));
    qualifyToolbarBaseline(diagnostic);
   }
   if(diagnostic.nativeStateRequested)native=await prepareNativeState(target,original,measured,diagnostic,tap);
   if(diagnostic.portalHostRequested){
    diagnostic.portalHost={version:'portal-host-v1',baseline:portalHostBaseline,baselinePassed:false};
    const snapshot=await tapOperation(tap,'portal-host-before',()=>original.evaluate(node=>window.__fileControlsPortalHostSnapshot(node)));
    diagnostic.portalHost.before=snapshot;
    for(const key of ['rect','radii','borders','viewport','intended','position'])
     assert.deepEqual(measured[key],portalHostBaseline.tap[key],`Portal-host original tap ${key} changed`);
    assert.equal(snapshot.download.nodeId,measured.nodeId);
    portalHostOracle(snapshot,portalHostBaseline);
    diagnostic.portalHost.baselinePassed=true;
   }
   if(diagnostic.reductionRequested){
    diagnostic.beforeStructure=await tapOperation(tap,'reduction-before',()=>original.evaluate(node=>window.__fileControlsReductionSnapshot(node)));
    reductionSnapshotOracle(diagnostic.beforeStructure,diagnostic.reductionPhase==='primary');
    assert.equal(diagnostic.beforeStructure.button.nodeId,measured.nodeId);
    if(diagnostic.reductionPhase==='primary'){
     for(const key of ['rect','radii','borders','viewport','intended','position'])
      assert.deepEqual(measured[key],stateReductionBaseline.tap[key],`Original reduction tap ${key} changed`);
    }
    if(precondition)await tapOperation(tap,'reduction-state-and-focus',()=>precondition(diagnostic),4000);
    diagnostic.preconditionsPassed=true;
   }
   if(diagnostic.compactDecorationRequested&&diagnostic.label==='touch-390-preview'){
    if(currentLayout){
     // The reviewed Refresh/full-width rows move the control. Do not claim the
     // old rectangle matched. The same exact contour was already checked above.
     assert.deepEqual(measured.viewport,coarseToolbarBaseline.before.viewport);
     diagnostic.layoutBinding=currentLayout;
     diagnostic.baselineMatched=false;
    }else{
     // Historical modes retain the fixed original failed contour.
     for(const [key,value] of Object.entries(coarseToolbarBaseline.before))
      assert.deepEqual(measured[key],value,`Original first-390 ${key} changed`);
     diagnostic.baselineMatched=true;
    }
   }
   probe=await tapOperation(tap,'arm-passive-capture',()=>original.evaluateHandle(armTouchEventCapture,
    {intended:measured.intended,compactDecoration:!!diagnostic.compactDecorationRequested}));
  }
  try{
   if(native)await native.start();
   if(beforeTap)beforeTap();
   if(diagnostic)diagnostic.tapAttempts=1;
   await tapOperation(tap,'native-tap',()=>control.tap({position:measured.position}),4500);
  }
  catch(error){diagnosticFailures.push(error)}
  finally{
   if(native){const ended=await native.end('tap-settled');if(ended.error)diagnosticFailures.push(ended.error)}
   // Wait only for passive observation, never repeat or reroute input. Capture
   // failures cannot suppress the original sameNode/geometry assertion below.
   if(probe)diagnosticFailures.push(...await collectTouchEvents(probe,diagnostic,tap));
  }
  if(native&&diagnostic.capture){
   native.state.correlation=diagnostic.capture.events.filter(event=>
    ['pointerdown','pointerup','mousedown','mouseup','click'].includes(event.type)).map(event=>({
     eventIndex:event.index,type:event.type,at:event.at,timeStamp:event.timeStamp,wallTime:event.wallTime,
     traceCallbackIntervalUs:{min:event.at*1000+native.state.clock.offsetUs.min,
      max:event.at*1000+native.state.clock.offsetUs.max}}));
  }
  if(diagnostic?.reductionRequested)try{
   // SavedFileDownload intentionally calls link.click() after fetching bytes.
   // Retain that untrusted event raw, but never count it as native touch delivery.
   const sequence=diagnostic.capture.events.filter(event=>event.isTrusted&&['pointerdown','pointerup','mousedown','mouseup','click'].includes(event.type));
   assert.equal(reductionCaptureTruncated(diagnostic.capture.events),false,'Reduction descriptors truncated');
   assert.deepEqual(sequence.map(event=>event.type),['pointerdown','pointerup','mousedown','mouseup','click']);
   assert.equal(diagnostic.capture.events.some(event=>['pointercancel','touchcancel'].includes(event.type)),false);
   for(const event of sequence){
    assert.equal(event.isTrusted,true);assert.equal(event.intendedButton.nodeId,measured.nodeId);
    assert.equal(event.intendedButton.connected,true);
    assert.deepEqual(event.intendedButton.rect,measured.rect);assert.deepEqual(event.intended,measured.intended);
    reductionSnapshotOracle(event.reductionSnapshot,diagnostic.reductionPhase==='primary');
    assert.equal(event.reductionSnapshot.button.nodeId,measured.nodeId);
   }
   assert.equal(sequence[0].pointerType,'touch');
   assert.equal(sequence[2].sourceCapabilities?.firesTouchEvents,true);
   if(diagnostic.reductionPhase==='primary')
    assert.equal(sequence[0].reductionSnapshot.focus?.nodeId,
     diagnostic.focusContrastArm?diagnostic.focus.expectedNodeId:diagnostic.focus.outer.nodeId,
     'Accepted focus changed before pointerdown');
   diagnostic.captureValid=true;
  }catch(error){diagnosticFailures.push(error)}
  // Evaluate trusted raw event targets BEFORE any later geometry/cleanup error.
  // Retain both events even on failure; do not let an absent download mask them.
  record.rawTargets=(diagnostic?.capture?.events||[]).filter(event=>['pointerdown','click'].includes(event.type))
   .map(({index,type,isTrusted,sameNode,target,actual,intendedButton})=>
    ({index,type,isTrusted,sameNode,target,actual,intendedButton}));
  const wrongTarget=record.rawTargets.find(event=>event.isTrusted&&event.sameNode===false);
  if(wrongTarget){
   try{assert.ok(false,wrongTarget.type==='pointerdown'?'Touch must reach the native control':
    'Trusted click must reach the native control')}
   catch(error){originalFailure=error;record.rawFailureAt=Date.now()}
  }
  let delivered,retained;
  try{
   const readback=await tapOperation(tap,'read-retained-native-control',()=>original.evaluate(node=>({
    delivered:window.__fileControlsLastTap,
    retained:{nodeId:window.__fileControlsNodeId(node),connected:node.isConnected,tag:node.tagName,
     rect:node.getBoundingClientRect().toJSON(),inert:!!node.closest('[inert]'),
     ariaHidden:!!node.closest('[aria-hidden="true"]')}
   })));
   ({delivered,retained}=readback);record.retained=retained;
  }catch(error){diagnosticFailures.push(error)}
  if(diagnostic)diagnostic.delivered=delivered;
  const {corners:deliveredCorners,...tapDelivered}=delivered||{};
  geometry.push({label:'corner-tap-delivered',...tapDelivered,
   acceptance:{basis:delivered?.basis,corners:deliveredCorners}});
  if(originalFailure)throw originalFailure;
  if(!retained)throw diagnosticFailures.shift()||Error('Retained native control read is missing');
  assert.ok(delivered?.event?.sameNode,'Touch must reach the native control');
  assert.equal(retained.connected,true,'Original native control was detached after touch');
  assert.equal(retained.nodeId,measured.nodeId,'Retained native control identity changed');
  assert.equal(retained.tag,measured.tag,'Retained native control tag changed');
  assert.equal(delivered.nodeId,measured.nodeId,'Native control was replaced before touch delivery');
  for(const field of ['rect','radii','borders','transforms','viewport','scroll','intended','position'])
   assert.deepEqual(delivered[field],measured[field],`Control ${field} changed between measurement and gesture`);
  nativeHitOracle(delivered,`delivered touch ${corner}`);
  exactCorner(delivered.corrected);
  assert.equal(delivered.event.pointerType,'touch');assert.equal(delivered.event.isTrusted,true);
  // Chromium may quantize fractional CSS coordinates; retain actual delivery.
  // This tolerance is ONLY for event delivery, never for padding reconstruction.
  assert.ok(Math.abs(delivered.event.x-measured.intended.x)<1&&
   Math.abs(delivered.event.y-measured.intended.y)<1,'Delivered touch missed the measured contour probe');
  if(diagnostic){
   diagnostic.originalOraclePassed=true;
   assert.equal(diagnostic.checkpointObserved,true,'Native touch event pair is UNKNOWN: missing event, truncation or capture loss; no retry');
   if(diagnostic.qualification)qualifyToolbarEvents(diagnostic);
   if(diagnostic.nativeStateRequested||diagnostic.portalHostRequested||diagnostic.reductionRequested||diagnostic.compactDecorationRequested){
    const events=diagnostic.capture.events;
    if(diagnostic.compactDecorationRequested){
     assert.equal(reductionCaptureTruncated(events),false,'Compact native sequence capture truncated');
     assert.equal(events.some(event=>['pointercancel','touchcancel'].includes(event.type)),false);
    }
    for(const type of ['pointerdown','pointerup','mousedown','mouseup','click']){
     const matches=events.filter(event=>event.type===type&&
      (!(diagnostic.reductionRequested||diagnostic.compactDecorationRequested)||event.isTrusted));
     assert.equal(matches.length,1,`Native-state diagnostic requires exactly one ${type}`);
     assert.equal(matches[0].isTrusted,true);assert.equal(matches[0].sameNode,true,`${type} must reach the native control`);
     if(diagnostic.compactDecorationRequested){
      assert.equal(matches[0].target.nodeId,measured.nodeId,'Compact decoration intercepted native input');
      assert.equal(matches[0].target.tag,'BUTTON');
     }
     if(diagnostic.portalHostRequested){
      const snapshot=matches[0].portalHost;
      portalHostOracle(snapshot,portalHostBaseline);
      for(const key of ['download','details','host'])
       assert.equal(snapshot[key].nodeId,diagnostic.portalHost.before[key].nodeId,`Portal ${key} replaced during gesture`);
     }
     if(diagnostic.reductionRequested){
      reductionSnapshotOracle(matches[0].reductionSnapshot,diagnostic.reductionPhase==='primary');
      assert.equal(matches[0].reductionSnapshot.button.nodeId,measured.nodeId);
     }
    }
    diagnostic.strictEventsPassed=true;
   }
  }
 }catch(error){
  originalFailure=error;if(diagnostic)diagnostic.failure=errorEvidence(error);
 }finally{
  if(native){
   diagnosticFailures.push(...await native.finish());
   if(!native.state.complete)diagnosticFailures.push(Error('Native-state capture incomplete; no retry'));
  }
  if(original&&listenerArmed)try{
   const removed=await tapOperation(tap,'remove-original-pointer-listener',()=>original.evaluate(()=>{
    const result=window.__fileControlsCancelTap?.();delete window.__fileControlsCancelTap;return result;
   }));
   record.listenerCleanup={state:'removal-unconfirmed',result:removed};
   assert.deepEqual(removed,{nodeId:measured.nodeId,listenerRemoved:true},'Original pointer listener cleanup was not confirmed');
   record.listenerCleanup.state='removed';
   if(diagnostic)diagnostic.originalListenerRemoved=true;
  }catch(error){record.listenerCleanup.error=errorEvidence(error);diagnosticFailures.push(error)}
  if(original)try{await tapOperation(tap,'dispose-native-control',()=>original.dispose());record.handleDisposed=true}
  catch(error){diagnosticFailures.push(error)}
 }
 record.primaryFailure=errorEvidence(originalFailure);
 record.otherFailures=diagnosticFailures.map(errorEvidence);
 record.settled=record.operations.every(row=>row.state!=='pending')&&record.browserAbort?.state!=='pending';
 if(diagnostic)diagnostic.cleanupErrors=record.otherFailures;
 const failures=[...(originalFailure?[originalFailure]:[]),...diagnosticFailures];
 if(failures.length===1)throw failures[0];
 if(failures.length)throw new AggregateError(failures,'Native touch assertion and event-pair capture/cleanup failed');
}

async function connect(owner,options){
 const label=owner.label,target=await ownerPage(owner,ready.url,options);pageLabels.set(target,label);
 target.on('pageerror',error=>errors.push(error.message));
 target.on('console',message=>{
  const text=message.text();if(!text.startsWith(cornerShapeLogPrefix))return;
  try{
   assert.ok(contourOracle.cornerShapeObservations.length<512,'Corner-shape evidence exceeded 512 observations');
   contourOracle.cornerShapeObservations.push({client:label,...JSON.parse(text.slice(cornerShapeLogPrefix.length))});
  }catch(error){
   if(!contourOracle.cornerShapeCaptureErrors.length)contourOracle.cornerShapeCaptureErrors.push(errorEvidence(error));
  }
 });
 target.on('request',request=>{
  const pathname=new URL(request.url()).pathname;
  if(pathname.startsWith('/api/canvas/'))requests.push({client:label,path:pathname,query:new URL(request.url()).search});
  if(request.method()==='POST'&&pathname==='/api/actions'){
   const payload=request.postDataJSON(),command=payload?.action==='shell.command'?(payload.args.command||payload.args):payload;
   commands.push(command);
   if(command?.action==='canvas.versions.inspect')observeMetadata(request,command,label,payload.id);
  }
 });
 await target.addInitScript(installDetailsProbe);
 if(coarseToolbarQualificationMode)
  await target.addInitScript({content:`window.__fileControlsToolbarSnapshot=(${coarseToolbarSnapshot.toString()});`});
 if(portalHostExperimentMode)
  await target.addInitScript({content:`window.__fileControlsPortalHostSnapshot=(${portalHostSnapshot.toString()});`});
 if(stateReductionMode||focusContrastMode)
  await target.addInitScript({content:`window.__fileControlsReductionSnapshot=(${reductionSnapshot.toString()});`});
 await target.addInitScript({content:`window.__fileControlsCircularContour=(${circularContour.toString()});`+
  `window.__fileControlsSupportedCircularCornerShape=(${supportedCircularCornerShape.toString()});`+
  `(${installContourSampler.toString()})(${JSON.stringify(cornerShapeLogPrefix)});`});
 await target.addInitScript(()=>{
  window.__fileControlsFrames=[];
  new MutationObserver(records=>{
   for(const record of records){
    if(record.type==='attributes'&&record.target.tagName==='IFRAME')window.__fileControlsFrames.push(record.target.getAttribute('src')||'inline iframe');
    for(const node of record.addedNodes||[])if(node.nodeType===1){
     if(node.tagName==='IFRAME')window.__fileControlsFrames.push(node.getAttribute('src')||'inline iframe');
     for(const frame of node.querySelectorAll('iframe'))window.__fileControlsFrames.push(frame.getAttribute('src')||'inline iframe');
    }
   }
  }).observe(document,{childList:true,subtree:true,attributes:true,attributeFilter:['src','srcdoc']});
 });
 await target.goto(ready.url);
 await target.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 return target;
}

const documentRequests=target=>requests.filter(row=>row.path.endsWith('/document')&&row.client===pageLabels.get(target));
async function previewState(target,row){
 return target.evaluate(id=>{
  const s=window.amplifier.getState(),c=s.canvas;
  return {selected:s.selectedSessionId,draft:s.view.draft,
   canvas:{id:c.id,version:c.selectedVersion,readOnly:c.readOnlyVersion},
   views:(s.canvasWorkspace?.views||[]).filter(view=>view.resourceId===id).map(view=>({
    viewId:view.viewId,resourceId:view.resourceId,resourceRevision:view.resourceRevision,generation:view.generation,
    selectedVersion:view.selectedVersion
   })),iframeSightings:window.__fileControlsFrames||[]};
 },row.id);
}
const executionMarker=frame=>frame.evaluate(()=>({
 count:window.fileControlsExecutions,executed:document.body.dataset.fileControlsExecuted,
 token:document.body.dataset.fileControlsToken
}));

async function startPreview(target,row,draft,label,unfinishedNote=null,focusSetup=null){
 // A separate, deliberate preview SETUP, after the card-only no-document oracle.
 // Never classify this canvas.select or its document request as a download.
 const before=documentRequests(target).length;
 await action(target,'view.update',{patch:{canvasWidth:900,canvasControlsExpanded:false,canvasFocused:false}});
 await action(target,'canvas.select',{id:row.id,sessionId:seed.sessionId,version:row.version,presentation:'canvas'});
 const panel=target.locator('#workspace-canvas');
 await expect(panel).toBeVisible();
 const locator=panel.locator('.a-canvas-workspace iframe.a-canvas-html');
 await expect(locator).toHaveCount(1);
 const element=await locator.elementHandle();
 try{
  const frame=await element.contentFrame();assert.ok(frame,'Primary HTML frame must be attached');
  await expect(frame.getByRole('heading',{name:'Original résumé α — 雪',exact:true})).toBeVisible();
  await frame.waitForFunction(()=>window.fileControlsExecutions===1&&document.body.dataset.fileControlsToken,undefined,{timeout:4000});
  const input=frame.getByRole('textbox',{name:'Unfinished preview note',exact:true});
  const unfinished=unfinishedNote??`Unsubmitted ${label} note — ${randomUUID()}`;
  if(focusSetup)await settleFocusSetup({target,row,panel,locator,element,frame,input,unfinished},focusSetup);
  else await input.fill(unfinished);
  const marker=await executionMarker(frame);
  assert.equal(marker.count,1);assert.equal(marker.executed,'1');
  assert.match(marker.token,/^\d+-\d+-\d+-\d+$/,'Fixture script must have generated a fresh execution token');
  const state=await previewState(target,row);
  assert.equal(state.selected,seed.sessionId);assert.equal(state.draft,draft);
  assert.deepEqual(state.canvas,{id:row.id,version:row.version,readOnly:true});
  assert.equal(state.views.length,1);assert.equal(state.views[0].viewId,'primary');
  const documents=documentRequests(target);
  assert.equal(documents.length,before+1,'Preview setup must load exactly one document');
  assert.equal(documents.at(-1).path,`/api/canvas/${row.id}/document`);
  const retained={target,row,panel,locator,element,frame,input,unfinished,marker,state,documents,commandStart:commands.length,label};
  checks.push({check:'deliberate saved-v1 preview setup, not a download',label,marker,state,documents});
  await previewPreserved(retained);
  return retained;
 }catch(error){
  if(focusSetup){
   try{await tapOperation(focusSetup.lifecycle,'setup-preview-dispose',()=>element.dispose())}
   catch(cleanupError){throw new AggregateError([error,cleanupError],'Preview setup failed; retained frame cleanup also failed')}
  }else await element.dispose();
  throw error;
 }
}
async function previewPreserved(retained){
 const {target,row,locator,element,frame,input,unfinished,marker,state,documents}=retained;
 const observed={iframeConnected:await element.evaluate(node=>node.isConnected)};
 assert.equal(observed.iframeConnected,true,'Running preview iframe was removed');
 observed.sameIframe=await locator.evaluate((node,original)=>node===original,element);
 assert.equal(observed.sameIframe,true,'Running preview iframe was replaced');
 observed.iframeCount=await target.locator('iframe').count();
 assert.equal(observed.iframeCount,1,'A second preview frame was mounted');
 observed.marker=await executionMarker(frame);
 assert.deepEqual(observed.marker,marker,'Preview execution marker changed: script reran or frame reloaded');
 observed.unfinished=await input.inputValue();
 assert.equal(observed.unfinished,unfinished,'Unfinished preview input changed');
 observed.state=await previewState(target,row);
 assert.deepEqual(observed.state,state,'File controls changed selection, draft, renderer lease or frame attributes');
 observed.documents=documentRequests(target);
 assert.deepEqual(observed.documents,documents,'File controls issued an extra HTML document request');
 observed.prohibitedCommands=commands.slice(retained.commandStart).filter(row=>
  ['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action));
 assert.deepEqual(observed.prohibitedCommands,[],
 'File controls selected/mounted a preview or sent input');
 return observed;
}
async function tabTo(target,control){
 // Traverse the real tab order; do not turn a missing/unreachable control into
 // a pass by clicking via evaluate or directly focusing the destination.
 for(let step=0;step<16;step++){
  if(await control.evaluate(node=>node===document.activeElement))return;
  await target.keyboard.press('Tab');
 }
 throw Error(`Native keyboard traversal did not reach ${await control.getAttribute('aria-label')||await control.innerText()}`);
}
async function observeNeighborDownloads(target,primary,row,label){
 const record={version:'neighbor-request-boundary-v1',label,client:pageLabels.get(target),downloads:0,
  commandStart:commands.length,requestStart:requests.length,metadataStart:metadataExchanges.length,
  inspectionRequests:[],downloadRequests:[],observerErrors:[],listenerRemoved:false,passed:false,
  settlement:{operations:[],otherFailures:[],settled:false}};
 downloadNeighbors.push(record);
 const owner=browserOwners.find(o=>o.label===record.client),lifecycle={record:record.settlement,owner,abort:null};
 record.clientIdentity=await tapOperation(lifecycle,'neighbor-client-identity',()=>identity(target));
 const observed=[];
 let original;
 const snapshot=()=>({
  commandEnd:commands.length,requestEnd:requests.length,metadataEnd:metadataExchanges.length,
  inspections:record.inspectionRequests.map(r=>r.requestId),downloads:record.downloads,
  attachments:record.downloadRequests.length
 });
 const onRequest=request=>{
  try{
   const metadata=metadataByRequest.get(request);
   if(metadata){
    assert.ok(observed.length<16,'Neighbor metadata evidence exceeds 16 requests');
    observed.push(metadata);
    record.inspectionRequests.push({commandIndex:commands.length-1,metadataIndex:metadataExchanges.indexOf(metadata.record),
     requestId:metadata.record.requestId,target:metadata.record.target,client:metadata.record.client,
     clientId:request.headers()['x-amplifier-client'],command:commands.at(-1)});
    assert.equal(record.inspectionRequests.at(-1).clientId,record.clientIdentity.clientId);
   }
   const url=new URL(request.url());
   if(url.pathname.startsWith('/api/canvas/')&&url.pathname.endsWith('/download')){
    assert.ok(record.downloadRequests.length<16,'Neighbor attachment evidence exceeds 16 requests');
    record.downloadRequests.push({requestIndex:requests.length-1,url:request.url(),method:request.method(),
     path:url.pathname,query:url.search,client:record.client});
   }
  }catch(error){if(!record.observerErrors.length)record.observerErrors.push(errorEvidence(error))}
 };
 const onDownload=()=>{record.downloads++};
 const assertBoundary=()=>{
  assert.deepEqual(record.observerErrors,[]);
  const expected=record.expectedInspect;
  assert.ok(expected?.length,'Missing fresh Details metadata identity');
  assert.deepEqual(record.inspectionRequests.map(r=>r.requestId),expected.map(r=>r.requestId),
   'Details/Copy issued an inspect outside its fresh Details lifecycle');
  assert.deepEqual(record.inspectionRequests.map(r=>r.metadataIndex),expected.map(r=>r.metadataIndex));
  assert.equal(record.downloads,0,'Details/Copy emitted an unintended browser download');
  assert.deepEqual(record.downloadRequests,[],'Details/Copy started an attachment fetch');
 };
 const drain=()=>tapOperation(lifecycle,'neighbor-metadata-settlement',async()=>{
  // Join originals, including a response already completed before this wait.
  // A newly observed request is collected too; the enclosing deadline is finite.
  let count=-1;
  while(count!==observed.length){count=observed.length;await Promise.all(observed.map(o=>o.completion))}
  record.metadataSettlement=record.inspectionRequests.map(r=>({requestId:r.requestId,metadataIndex:r.metadataIndex,
   record:metadataExchanges[r.metadataIndex]}));
  for(const o of observed){if(o.record.error)throw Error(`Neighbor metadata failed: ${o.record.error.message}`);metadataOracle(o.record,row)}
 },8000);
 record.pendingAtStart=metadataExchanges.filter(r=>r.client===record.client&&!r.completedAt&&!r.failedAt).map(r=>r.requestId);
 assert.deepEqual(record.pendingAtStart,[],'Previous metadata remains pending before Details negative');
 target.on('request',onRequest);target.on('download',onDownload);record.armedAt=Date.now();
 return {
  record,
  async retain(){original=await tapOperation(lifecycle,'neighbor-retain-primary',()=>primary.elementHandle({timeout:1000}),1500);
   assert.ok(original,'Original primary Download missing')},
  async settle(traceIndex,details){
   const trace=detailsReadiness[traceIndex];record.detailsTraceIndex=traceIndex;
   assert.ok(trace?.passed&&!trace.error&&!trace.captureError&&!trace.disposeError,'Fresh Details lifecycle incomplete');
   record.expectedInspect=trace.matches.map(r=>({requestId:r.requestId,metadataIndex:metadataExchanges.indexOf(r)}));
   assert.equal(trace.client,record.client);assert.deepEqual(trace.expected,{id:row.id,version:row.version,sessionId:seed.sessionId});
   assertBoundary(); // An extra inspect fails even if its attachment is still delayed.
   await drain();assertBoundary();
   record.afterSettlement=snapshot();record.metadataSettledAt=Date.now();
   // save() sets busy synchronously before queuing inspect. This catches a
   // queued invocation whose HTTP request has not yet reached the observer.
   const busy=node=>({nodeId:window.__fileControlsNodeId(node),connected:node.isConnected,tag:node.tagName,
    label:node.getAttribute('aria-label'),disabled:node.disabled,busy:node.getAttribute('aria-busy')});
   record.controls={primary:await tapOperation(lifecycle,'neighbor-primary-idle',()=>original.evaluate(busy)),
    rail:await tapOperation(lifecycle,'neighbor-rail-idle',()=>button(details,names[2]).evaluate(busy))};
   for(const control of Object.values(record.controls)){
    assert.equal(control.connected,true);assert.equal(control.tag,'BUTTON');
    assert.equal(control.disabled,false);assert.equal(control.busy,'false','Download is queued or in flight during negative');
   }
   assert.equal(record.controls.primary.label,'Download canvas source');assert.equal(record.controls.rail.label,'Download saved file');
   record.controlsReadAt=Date.now();
   assertBoundary();record.readyForPositive=true;
  },
  beforePositive(){
   assert.equal(record.readyForPositive,true);
   assertBoundary();
   record.boundary=snapshot();record.positiveStartedAt=Date.now();
   // Synchronous boundary immediately before the existing key/tap. Its positive
   // observer cannot consume a negative event that this guard has already seen.
   target.off('request',onRequest);target.off('download',onDownload);record.listenerRemoved=true;
   record.passed=true;
  },
  async abort(error){
   record.failure=errorEvidence(error);const failures=[error];
   // An assertion failure must not leave a queued save/fetch for the next positive.
   // Close this owner FIRST, then collect original metadata promises; no retry.
   try{await tapOperation(lifecycle,'neighbor-failure-close',()=>closeOwner(owner),4000);
    record.settlement.ownerClosed=owner.closed&&!owner.browser.isConnected()}
   catch(closeError){failures.push(closeError)}
   try{await drain()}catch(drainError){failures.push(drainError)}
   throw new AggregateError(failures,error.message);
  },
  async dispose(){
   target.off('request',onRequest);target.off('download',onDownload);record.listenerRemoved=true;
   record.observersRemovedAt=Date.now();
   try{if(original)await tapOperation(lifecycle,'neighbor-primary-dispose',()=>original.dispose())}
   finally{record.settlement.settled=record.settlement.operations.every(op=>op.state!=='pending')&&
    record.settlement.browserAbort?.state!=='pending'}
  }
 };
}
async function usePreviewControls(retained,label,mode){
 const {target,panel,row}=retained;
 const primary=panel.locator('.a-canvas-primary-actions').getByRole('button',{name:'Download canvas source',exact:true});
 const toggle=panel.getByRole('button',{name:'Canvas details',exact:true});
 const inspector=panel.getByRole('region',{name:'Canvas details',exact:true});
 await expect(inspector).toBeHidden();
 await expect(primary).toBeVisible();
 assert.equal(await primary.evaluate(node=>node.tagName),'BUTTON');
 await measureControls(target,[primary,toggle],label+'-primary-before');
 if(fullJourney)await downloadDecorationCheck(primary,label+'-primary-before',true);
 if(coarseToolbarQualificationMode)await qualifyToolbarLayout(primary,label,'before',mode==='touch');
 if((nativeStateDiagnosticMode||portalHostExperimentMode)&&mode==='touch'){
  assert.equal(label,'touch-390-preview','Native-state diagnostic may only observe the first 390px primary Download');
  assert.equal(touchEventDiagnostic,null,'Only one native-state diagnostic tap is permitted');
  touchEventDiagnostic={nativeStateRequested:nativeStateDiagnosticMode,portalHostRequested:portalHostExperimentMode,
   checkpoint:'first-390-primary-download',label,control:'Download canvas source',
   browser:browserOwners.find(owner=>owner.label==='touch').browser.version(),options:touchOptions,
   retained:{marker:retained.marker,unfinished:retained.unfinished,state:retained.state,documents:retained.documents},
   tapAttempts:0,checkpointObserved:false,originalOraclePassed:false,strictEventsPassed:false,
   outcome:'unknown',cause:'unknown',productAcceptance:false};
  // CDP preparation precedes arming the unchanged 5s download observer. Its
  // timer covers the actual gesture, not the read-only native-state collection.
  touchEventDiagnostic.download=await saveControlAndVerify(target,primary,row,label+'-primary-v1',
   (control,arm)=>cornerTap(control,'bottom-right',touchEventDiagnostic,arm),true);
  touchEventDiagnostic.preservation=await previewPreserved(retained);
  if(portalHostExperimentMode){
   assert.equal(touchEventDiagnostic.download.bytes,426);
   assert.equal(touchEventDiagnostic.download.sha256,portalHostBaseline.html.sha256);
   touchEventDiagnostic.portalHost.outcome='same-geometry-gesture-passed-topology-dependence-supported-not-pruning-proof';
  }
  touchEventDiagnosticComplete=true;
  return; // No next control, gesture or viewport.
 }
 if(touchEventDiagnosticMode&&mode==='touch'){
  assert.equal(label,'touch-390-preview','Diagnostic may only observe the first 390px primary Download');
  assert.equal(touchEventDiagnostic,null,'Only one diagnostic tap is permitted');
  touchEventDiagnostic={checkpoint:'first-390-primary-download',label,control:'Download canvas source',
   browser:browserOwners.find(owner=>owner.label==='touch').browser.version(),options:touchOptions,
   retained:{marker:retained.marker,unfinished:retained.unfinished,state:retained.state,documents:retained.documents},
   tapAttempts:0,checkpointObserved:false,originalOraclePassed:false,outcome:'unknown',cause:'unknown',productAcceptance:false};
  // Diagnostic ends at delivery, not download/feedback acceptance. The normal
  // journey's download verifier and all preceding state-changing steps are intact.
  await cornerTap(primary,'bottom-right',touchEventDiagnostic);
  touchEventDiagnosticComplete=true;
  return;
 }
 let qualificationGesture=null;
 if(coarseToolbarQualificationMode&&label==='touch-390-preview'){
  assert.equal(mode,'touch');assert.equal(coarseToolbarQualification.gesture,null,'Only one first-390 event capture is permitted');
  qualificationGesture={qualification:true,checkpoint:'first-390-primary-download',label,control:'Download canvas source',
   browser:browserOwners.find(owner=>owner.label==='touch').browser.version(),options:touchOptions,
   retained:{marker:retained.marker,unfinished:retained.unfinished,state:retained.state,documents:retained.documents},
   tapAttempts:0,checkpointObserved:false,originalOraclePassed:false,outcome:'unknown'};
  coarseToolbarQualification.gesture=qualificationGesture;
 }
 if(fullJourney&&mode==='touch'){
  qualificationGesture={compactDecorationRequested:true,label,tapAttempts:0,checkpointObserved:false,
   originalOraclePassed:false,strictEventsPassed:false,
   retained:{marker:retained.marker,unfinished:retained.unfinished,state:retained.state,documents:retained.documents}};
  compactPrimaryGestures.push(qualificationGesture);
 }
 let primaryDownload;
 if(mode==='keyboard'){
  // Copy precedes Download in the actual primary action row.
  await panel.locator('.a-canvas-primary-actions').getByRole('button',{name:'Copy canvas source',exact:true}).focus();
  await target.keyboard.press('Tab');await expect(primary).toBeFocused();
  primaryDownload=await saveControlAndVerify(target,primary,row,label+'-primary-v1',()=>target.keyboard.press('Enter'));
 }else primaryDownload=await saveControlAndVerify(target,primary,row,label+'-primary-v1',
  control=>cornerTap(control,'bottom-right',qualificationGesture));
 if(qualificationGesture)qualificationGesture.download=primaryDownload;
 const preservation=await previewPreserved(retained);
 if(qualificationGesture){
  assert.equal(primaryDownload.bytes,coarseToolbarBaseline.html.bytes);
  assert.equal(primaryDownload.sha256,coarseToolbarBaseline.html.sha256);
  qualificationGesture.postGesture={check:qualificationGesture.compactDecorationRequested?
   'compact primary exact saved HTML download and running preview preserved':'first-390 exact saved HTML download and running preview preserved',
   passed:true,download:primaryDownload,preservation};
  qualificationGesture.outcome='qualified-gesture-download-and-preservation';
 }
 await measureControls(target,[primary,toggle],label+'-primary-after');
 if(fullJourney)await downloadDecorationCheck(primary,label+'-primary-after',true);
 const notice=primary.locator('..').getByRole('status');
 await expect(notice).toBeVisible();
 const feedback=await notice.evaluate(node=>{
  const r=node.getBoundingClientRect(),bar=node.closest('.a-canvas-artifact-toolbar').getBoundingClientRect();
  const range=document.createRange();range.selectNodeContents(node);
  const lines=[...range.getClientRects()].map(rect=>rect.toJSON());
  return {rect:r.toJSON(),bar:bar.toJSON(),lines,contained:r.left>=bar.left&&r.right<=bar.right&&r.bottom<=bar.bottom&&
   r.right<=innerWidth&&r.bottom<=innerHeight&&lines.every(line=>line.left>=r.left-1&&line.right<=r.right+1&&line.bottom<=r.bottom+1)};
 });
 geometry.push({label:label+'-visible-feedback',...feedback});
 assert.ok(feedback.contained&&feedback.lines.length>0,'Download completion feedback must remain visible and bounded');
 if(coarseToolbarQualificationMode){
  await qualifyToolbarLayout(primary,label,'after',mode==='touch');
  coarseToolbarQualification.layoutChecks.at(-1).feedback=feedback;
 }
 await captureFileFrame(target,primary,label+'-primary');
 const neighbor=fullJourney?await observeNeighborDownloads(target,primary,row,label):null;
 const neighborErrors=[];
 try{
 if(neighbor)await neighbor.retain();
 if(mode==='keyboard'){
  // Download temporarily disables its button. Re-anchor after completion so a
  // browser's native disabled-control blur is not mistaken for wrong tab order.
  await expect(primary).toBeEnabled();await primary.focus();
  await target.keyboard.press('Tab');await expect(toggle).toBeFocused();await target.keyboard.press('Enter');
 }else await toggle.tap();
 await expect(inspector).toBeVisible();
 await expect(panel.locator('.a-canvas-content')).toHaveAttribute('data-details-overlay',mode==='keyboard'?'false':'true');
 const details=inspector.locator('details.a-canvas-file-path'),summary=details.locator('summary');
 await expect(summary).toHaveText('File details');
 // This is the existing right-rail portal, not the similarly named chat card.
 assert.equal(await details.count(),1);
 if(mode==='keyboard'){
  await expect(inspector.getByRole('button',{name:'Close canvas details',exact:true})).toBeFocused();
  await tabTo(target,summary);
 }
 const detailsTraceIndex=detailsReadiness.length;
 await freshDetails(target,details,row,()=>mode==='keyboard'?target.keyboard.press('Enter'):summary.tap());
 if(neighbor)neighbor.record.copyCommandStart=commands.length;
 await controlsOracle(details,row);
 for(const [name,value] of [[names[0],row.filePaths.absolute],[names[1],row.filePaths.relative]]){
  if(mode==='keyboard'){
   await tabTo(target,button(details,name));await expect(button(details,name)).toBeFocused();
   await copy(target,details,name,value,()=>target.keyboard.press('Enter'));
  }else await copy(target,details,name,value,control=>cornerTap(control,name===names[0]?'top-left':'bottom-right'));
  await previewPreserved(retained);
 }
 if(neighbor)await neighbor.settle(detailsTraceIndex,details);
 if(mode==='keyboard'){
  await tabTo(target,button(details,names[2]));await expect(button(details,names[2])).toBeFocused();
  await saveAndVerify(target,details,row,label+'-rail-v1',()=>{
   neighbor?.beforePositive();return target.keyboard.press('Enter');
  });
 }else await saveAndVerify(target,details,row,label+'-rail-v1',
  control=>cornerTap(control,'bottom-right',null,()=>neighbor?.beforePositive()));
 if(neighbor)neighbor.record.positive={commandEnd:commands.length,requestEnd:requests.length,
  metadataEnd:metadataExchanges.length,completedAt:Date.now(),downloadLabel:label+'-rail-v1'};
 }catch(error){
  if(neighbor)try{await neighbor.abort(error)}catch(failure){neighborErrors.push(failure)}
  else neighborErrors.push(error);
 }finally{
  if(neighbor)try{await neighbor.dispose()}catch(error){neighborErrors.push(error)}
 }
 if(neighborErrors.length)throw new AggregateError(neighborErrors,'Details/Copy exclusion or following positive failed');
 if(neighbor)checks.push({check:'ordinary Details and inspector Copy do not activate primary Download',...neighbor.record});
 const details=inspector.locator('details.a-canvas-file-path');
 await previewPreserved(retained);
 await measure(target,details,label+'-rail');
 const close=inspector.getByRole('button',{name:'Close canvas details',exact:true});
 if(mode==='keyboard'){
  await button(details,names[2]).focus();await target.keyboard.press('Escape');
 }else await close.tap();
 await expect(inspector).toBeHidden();
 await previewPreserved(retained);
 if(fullJourney&&mode==='keyboard'){
  // Escape restores the ordinary Details invoker; reverse the real tab order.
  // This is a separate keyboard activation, never a retry of the touch falsifier.
  await expect(toggle).toBeFocused();
  await target.keyboard.press('Shift+Tab');await expect(primary).toBeFocused();
  const download=await saveControlAndVerify(target,primary,row,label+'-primary-space-v1',()=>target.keyboard.press('Space'));
  const preservation=await previewPreserved(retained);
  checks.push({check:'native primary Space downloads exact saved bytes and preserves preview',label,download,preservation});
 }
 await target.screenshot({path:join(evidence,label+'-retained-preview.png')});
 checks.push({check:'right-rail paths/download and primary Download preserve running saved-v1 HTML',label,mode,
  marker:retained.marker,unfinished:retained.unfinished,documents:documentRequests(target),state:await previewState(target,row)});
}

async function reductionDataGate(target,phase,gates=stateReduction.gates){
 const fixtureState=await fixtureRead(target);
 const client=await target.evaluate(()=>{
  const s=window.amplifier.getState(),session=s.sessions.find(row=>row.id===s.selectedSessionId);
  return {selected:s.selectedSessionId,workingDirectory:session?.workingDirectory,workspace:session?.workspace,
   draft:s.view.draft,canvas:{id:s.canvas.id,version:s.canvas.selectedVersion,readOnly:s.canvas.readOnlyVersion}};
 });
 const gate={phase,fixture:fixtureState,client,passed:false};gates.push(gate);
 assert.equal(fixtureState.seed.sessionId,seed.sessionId);
 assert.equal(fixtureState.seed.artifacts.html.id,seed.artifacts.html.id);
 assert.equal(fixtureState.newer.id,seed.artifacts.html.id);assert.equal(fixtureState.newer.revision,2);
 assert.equal(fixtureState.sourceDeleted,true);assert.equal(fixtureState.sourceExists,false);assert.deepEqual(fixtureState.sent,[]);
 assert.equal(client.selected,seed.sessionId);assert.equal(client.workingDirectory,seed.workingDirectory);
 assert.equal(client.workspace,seed.workspace);assert.notEqual(seed.workingDirectory,seed.workspace);
 assert.equal(client.draft,stateReductionBaseline.draft);
 const expectedId=phase.startsWith('card')||phase==='unrelated'?seed.unrelated.id:seed.artifacts.html.id;
 assert.deepEqual(client.canvas,{id:expectedId,version:1,readOnly:true});
 gate.passed=true;return gate;
}
async function runStateReduction(){
 // Same real fixture/components, one touch owner, no alternate DOM/React root.
 ready=await startup;assert.notEqual(process.env.AMPLIFIER_TEST_INSTALLED,'1');
 contourLogicChecks();cornerShapeLogicChecks();
 const owner=await launchOwner('touch',browserOwners);page=await connect(owner,touchOptions);
 stateReduction.browser={version:owner.browser.version(),options:touchOptions,launch:launchOptions,client:await identity(page)};
 assert.equal(stateReduction.browser.version,stateReductionBaseline.browser);
 await computedCornerPreflight(page,owner);
 await action(page,'session.create',{title:'Generated file controls'});
 stateReduction.browser.client=await identity(page);
 await showFixtureConversation(page,{navPinned:false});
 seed=await fixturePost(page,'seed');
 const html=seed.artifacts.html,draft=stateReductionBaseline.draft;
 assert.equal(html.version,1);assert.equal(html.byteLength,426);assert.equal(html.sha256,stateReductionBaseline.html.sha256);
 assert.equal(seed.unrelated.version,1);
 stateReduction.newer=await fixturePost(page,'newer');
 stateReduction.deleted=await fixturePost(page,'delete-source');
 await action(page,'canvas.select',{id:seed.unrelated.id,sessionId:seed.sessionId,version:1,presentation:'canvas'});
 await action(page,'canvas.visibility',{open:false,sessionId:seed.sessionId,canvasId:seed.unrelated.id});
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill(draft);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(draft);
 await preserved(page,draft);
 const details=await openDetails(page,html,'touch');await controlsOracle(details,html);
 const gesture=phase=>({reductionRequested:true,reductionPhase:phase,tapAttempts:0,preconditionsPassed:false,
  checkpointObserved:false,originalOraclePassed:false,strictEventsPassed:false});
 const cardGesture=stateReduction.card=gesture('card');
 const cardCommandStart=commands.length;
 cardGesture.download=await saveControlAndVerify(page,button(details,names[2]),html,'reduction-card-v1',
  (control,arm)=>cornerTap(control,'bottom-right',cardGesture,arm,async()=>{
   await preserved(page,draft);await reductionDataGate(page,'card-before');
  }),true);
 cardGesture.preservation=await preserved(page,draft);await reductionDataGate(page,'card-after');
 assert.deepEqual(commands.slice(cardCommandStart).filter(row=>
  ['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[]);
 assert.deepEqual(documentRequests(page),[]);
 assert.equal(reductionEventsComplete(cardGesture),true,'Card passive evidence incomplete');
 cardGesture.passed=true;

 const retained=await startPreview(page,html,draft,'touch');
 const primary=retained.panel.locator('.a-canvas-primary-actions').getByRole('button',{name:'Download canvas source',exact:true});
 const primaryGesture=stateReduction.primary=gesture('primary');
 primaryGesture.retained={marker:retained.marker,unfinished:retained.unfinished,state:retained.state,documents:retained.documents};
 let primaryError;
 try{
  await expect(retained.panel.getByRole('region',{name:'Canvas details',exact:true})).toBeHidden();
  // Preserve the existing primary-before measurement order (Download, Details).
  await measureControls(page,[primary,retained.panel.getByRole('button',{name:'Canvas details',exact:true})],
   'reduction-primary-before');
  primaryGesture.download=await saveControlAndVerify(page,primary,html,'reduction-primary-v1',
   (control,arm)=>cornerTap(control,'bottom-right',primaryGesture,arm,async diagnostic=>{
    diagnostic.preGesturePreservation=await previewPreserved(retained);
    assert.deepEqual(retained.state.views.map(({viewId,generation,selectedVersion})=>({viewId,generation,selectedVersion})),
     stateReductionBaseline.views,'Reduction renderer lease generation/selection differs');
    await reductionDataGate(page,'primary-before');
    const outer=await retained.element.evaluate(node=>({at:Date.now(),nodeId:window.__fileControlsNodeId(node),
     connected:node.isConnected,focused:document.activeElement===node}));
    const inner=await retained.input.evaluate(node=>({at:Date.now(),connected:node.isConnected,focused:document.activeElement===node,
     documentFocused:document.hasFocus(),tag:node.tagName,id:node.id,value:node.value}));
    diagnostic.focus={outer,inner};
    assert.equal(outer.connected,true);assert.equal(outer.focused,true);
    assert.equal(inner.connected,true);assert.equal(inner.focused,true);assert.equal(inner.documentFocused,true);
    assert.equal(inner.id,'preview-note');assert.equal(inner.value,retained.unfinished);
    assert.equal(diagnostic.beforeStructure.focus.nodeId,outer.nodeId,'Pre-tap focus snapshot differs');
   }),true);
 }catch(error){primaryError=error}
 finally{
  // A wrong target stays primary even if independent state readback/cleanup fails.
  const failures=[];
  const settlement=primaryGesture.settlement||{operations:[],otherFailures:[],settled:false};
  const postTap={record:settlement,owner,abort:null};
  try{
   primaryGesture.postGesturePreservation=await tapOperation(postTap,'reduction-post-preservation',()=>previewPreserved(retained),3000);
   await tapOperation(postTap,'reduction-post-state',()=>reductionDataGate(page,'primary-after'),4000);
  }catch(error){failures.push(error)}
  try{await tapOperation(postTap,'reduction-preview-dispose',()=>retained.element.dispose())}
  catch(error){failures.push(error)}
  settlement.settled=settlement.operations.every(row=>row.state!=='pending')&&settlement.browserAbort?.state!=='pending';
  primaryGesture.postErrors=failures.map(errorEvidence);
  if(primaryError||failures.length)throw new AggregateError([primaryError,...failures].filter(Boolean),
   primaryError?.message||'Reduction preservation/cleanup failed');
 }
 primaryGesture.passed=true;
 assert.deepEqual(errors,[]);assert.deepEqual(contourOracle.cornerShapeCaptureErrors,[]);
}
// Existing header Canvas toggle is outside the narrow pane; the composer is
// hidden there. Read visibility/occlusion, never focus/activate a hidden control.
async function focusBoundarySnapshot(retained){
 const host=await retained.element.evaluate(frame=>{
  const toggle=document.querySelector('#canvas-toggle'),panel=document.querySelector('#workspace-canvas');
  const id=window.__fileControlsNodeId,active=document.activeElement,s=window.amplifier.getState();
  if(!toggle||!panel)throw Error('Required outer Canvas toggle/pane absent');
  const rect=toggle.getBoundingClientRect(),style=getComputedStyle(toggle);
  const point={x:rect.left+rect.width/2,y:rect.top+rect.height/2},hit=document.elementFromPoint(point.x,point.y);
  const selected=getSelection();
  if((selected?.toString().length||0)>4096)throw Error('Focus selection evidence exceeds 4096 characters');
  return {at:Date.now(),active:{nodeId:id(active),tag:active.tagName,id:active.id,label:active.getAttribute('aria-label'),
    inCanvas:panel.contains(active)},frame:{nodeId:id(frame),connected:frame.isConnected,focused:active===frame},
   toggle:{nodeId:id(toggle),tag:toggle.tagName,id:toggle.id,label:toggle.getAttribute('aria-label'),
    rect:rect.toJSON(),outsideCanvas:!panel.contains(toggle),connected:toggle.isConnected,
    visible:toggle.checkVisibility({checkVisibilityCSS:true})&&toggle.getClientRects().length>0,
    disabled:toggle.disabled,tabIndex:toggle.tabIndex,blocked:!!toggle.closest('[hidden],[inert],[aria-hidden="true"]'),
    pointerEvents:style.pointerEvents,inViewport:rect.left>=0&&rect.top>=0&&rect.right<=innerWidth&&rect.bottom<=innerHeight,
    point,hit:hit&&{nodeId:id(hit),tag:hit.tagName},reachable:!!hit&&toggle.contains(hit)},
   canvasDocument:s.canvas.document??null,
   pane:{open:s.canvas.open,focused:!!s.view.canvasFocused,controls:!!s.view.canvasControlsExpanded,
    hidden:panel.hidden,inert:panel.inert,rect:panel.getBoundingClientRect().toJSON()},
   scroll:{x:scrollX,y:scrollY},selection:selected?{text:selected.toString(),anchorOffset:selected.anchorOffset,
    focusOffset:selected.focusOffset,anchorNodeId:selected.anchorNode?id(selected.anchorNode):null,
    focusNodeId:selected.focusNode?id(selected.focusNode):null}:null};
 });
 const inner=await retained.input.evaluate(node=>({at:Date.now(),connected:node.isConnected,focused:document.activeElement===node,
  documentFocused:document.hasFocus(),tag:node.tagName,id:node.id,value:node.value,
  selectionStart:node.selectionStart,selectionEnd:node.selectionEnd,selectionDirection:node.selectionDirection,
  scroll:{x:scrollX,y:scrollY}}));
 return {host,inner};
}
function focusBoundaryOracle(value,arm){
 const {host,inner}=value,t=host.toggle;
 assert.ok(t.connected&&t.visible&&t.outsideCanvas&&!t.disabled&&!t.blocked&&t.tabIndex>=0&&t.inViewport&&t.reachable,
  'Design invalid: outer Canvas toggle is not an ordinary visible keyboard target');
 assert.equal(t.tag,'BUTTON');assert.equal(t.id,'canvas-toggle');assert.equal(t.label,'Hide canvas');
 assert.notEqual(t.pointerEvents,'none');
 assert.equal(host.pane.open,true);assert.equal(host.pane.focused,false);assert.equal(host.pane.controls,false);
 assert.equal(host.pane.hidden,false);assert.equal(host.pane.inert,false);
 assert.equal(host.frame.connected,true);assert.equal(inner.connected,true);
 assert.equal(inner.tag,'INPUT');assert.equal(inner.id,'preview-note');assert.equal(inner.value,focusContrast.unfinished);
 if(arm==='inside'){
  assert.equal(host.frame.focused,true);assert.equal(inner.focused,true);assert.equal(inner.documentFocused,true);
  assert.equal(host.active.nodeId,host.frame.nodeId);
 }else{
  assert.equal(host.active.nodeId,t.nodeId);assert.equal(host.active.inCanvas,false);
  assert.equal(host.frame.focused,false);assert.equal(inner.documentFocused,false);
  // A blurred child document can retain input as activeElement. documentFocused
  // and the actual parent activeElement, not that retained pointer, own focus.
 }
}
function sameFocusContext(before,after){
 const input=value=>Object.fromEntries(['connected','tag','id','value','selectionStart','selectionEnd','selectionDirection','scroll']
  .map(key=>[key,value.inner[key]]));
 assert.deepEqual(input(after),input(before),'Keyboard traversal mutated input/selection/scroll');
 for(const key of ['pane','scroll','selection','toggle'])
  assert.deepEqual(after.host[key],before.host[key],`Keyboard traversal changed host ${key}`);
}
// The ordinary input bridge reports asynchronously (canvas_bridge.js). Observe
// its real request before fill, then its retained response and applied document.
// No postMessage, synthetic report, DOM listener, focus change or quiet-period sleep.
async function settleFocusSetup(preview,{arm,lifecycle}){
 const {target,row,element,frame,input,unfinished}=preview;
 const record=arm.setupSnapshot={version:'focus-setup-snapshot-v1',passed:false,requests:[],cleanup:{},
  limits:{requestMs:4000,responseMs:3000,readbackMs:3000,requests:4,requestBytes:32768,responseBytes:1048576}};
 let native,stopObservation=()=>{},primaryError;const cleanupErrors=[];
 try{
  const state=await tapOperation(lifecycle,'setup-renderer-identity',()=>previewState(target,row));
  assert.equal(state.views.length,1);
  const {viewId,resourceId,resourceRevision,generation}=state.views[0];
  const lease={viewId,resourceId,resourceRevision,generation};
  record.expected={client:await tapOperation(lifecycle,'setup-client-identity',()=>identity(target)),lease,unfinished};
  assert.deepEqual(record.expected.client,arm.client);
  native=await tapOperation(lifecycle,'setup-retain-input',()=>input.elementHandle({timeout:1000}),1500);
  assert.ok(native,'Setup input handle missing');
  const nativeRead=()=>native.evaluate(node=>({connected:node.isConnected,exactInput:node===document.querySelector('#preview-note'),
   onlyControl:document.querySelectorAll('button,input,select,textarea,[role="button"]').length===1,
   tag:node.tagName,id:node.id,type:node.type,label:node.getAttribute('aria-label')||node.labels?.[0]?.textContent.trim(),
   value:node.value,disabled:node.disabled,focused:document.activeElement===node,documentFocused:document.hasFocus()}));
  record.inputBefore=await tapOperation(lifecycle,'setup-input-before',nativeRead);
  assert.equal(record.inputBefore.exactInput,true);assert.equal(record.inputBefore.onlyControl,true);
  assert.equal(record.inputBefore.connected,true);assert.equal(record.inputBefore.disabled,false);
  assert.equal(record.inputBefore.tag,'INPUT');assert.equal(record.inputBefore.type,'text');
  assert.equal(record.inputBefore.id,'preview-note');assert.equal(record.inputBefore.value,'');
  assert.equal(record.inputBefore.label,'Unfinished preview note');
  let resolveRequest,requestTimer,finished=false;
  const observed=new Promise(resolve=>{resolveRequest=resolve});
  const finish=outcome=>{
   if(finished)return;finished=true;clearTimeout(requestTimer);target.off('request',onRequest);
   record.cleanup.listenerRemoved=true;record.cleanup.timerCleared=true;record.observerFinishedAt=Date.now();
   resolveRequest(outcome);
  };
  const onRequest=request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
   try{
    const payload=request.postDataJSON(),command=payload?.action==='shell.command'?(payload.args.command||payload.args):payload;
    if(command?.action!=='canvas.views.command'||command.args?.action!=='canvas.snapshot')return;
    const bytes=Buffer.byteLength(request.postData()||'');
    assert.ok(bytes<=record.limits.requestBytes&&record.requests.length<record.limits.requests,'Setup snapshot request evidence limit');
    const requestRecord={at:Date.now(),clientId:request.headers()['x-amplifier-client'],payload,command,
     commandIndex:commands.findLastIndex(c=>c.id===command.id),bytes};
    record.requests.push(requestRecord);
    const controls=command.args.args?.document?.controls;
    if(!controls?.some(c=>c.value===unfinished))return; // Initial empty snapshot is retained, not mistaken for fill.
    assert.equal(requestRecord.clientId,arm.client.clientId);
    for(const key of Object.keys(lease))assert.equal(command.args[key],lease[key],`Setup snapshot ${key} changed`);
    assert.equal(command.args.args.id,row.id);assert.equal(controls.length,1);
    const control=controls[0];
    assert.deepEqual({...control,id:null},{id:null,tag:'input',type:'text',label:'Unfinished preview note',value:unfinished,disabled:false});
    assert.match(control.id,/^control-1-[a-z0-9]+$/);
    assert.ok(requestRecord.commandIndex>=0);
    record.matchedIndex=record.requests.length-1;record.controlId=control.id;
    finish({request,requestRecord});
   }catch(error){finish({error})}
  };
  stopObservation=()=>finish({cancelled:true});
  record.armedAt=Date.now();target.on('request',onRequest);
  requestTimer=setTimeout(()=>finish({error:Error('Current setup input snapshot request exceeded 4000ms')}),record.limits.requestMs);
  record.fillStartedAt=Date.now();
  await tapOperation(lifecycle,'setup-fill-input',()=>input.fill(unfinished),3000);
  record.fillCompletedAt=Date.now();
  record.afterFill=await tapOperation(lifecycle,'setup-after-fill',()=>focusBoundarySnapshot(preview),3000);
  focusBoundaryOracle(record.afterFill,'inside');
  const primary=()=>element.evaluate((node,expectedId)=>{
   const button=node.closest('#workspace-canvas')?.querySelector('.a-canvas-primary-actions .a-saved-file-download > button');
   if(!node.isConnected||!button?.isConnected||button.tagName!=='BUTTON'||
    button.getAttribute('aria-label')!=='Download canvas source')
    throw Error('Setup geometry requires the connected native primary Download button');
   if(expectedId!==null&&window.__fileControlsNodeId(button)!==expectedId)
    throw Error('Setup primary Download identity changed during snapshot settlement');
   return window.__fileControlsReductionSnapshot(button);
  },record.geometryAfterFill?.button.nodeId??null);
  record.geometryAfterFill=await tapOperation(lifecycle,'setup-geometry-after-fill',primary);
  const provisional={...preview,marker:await tapOperation(lifecycle,'setup-execution-marker',()=>executionMarker(frame)),
   state,documents:documentRequests(target),commandStart:commands.length};
  const match=await observed; // Resolver retains a match even if its response completed during fill/readbacks.
  if(match.error)throw match.error;
  assert.ok(match.request&&!match.cancelled,'Setup snapshot observation cancelled');
  await tapOperation(lifecycle,'setup-snapshot-response',async()=>{
   const response=await match.request.response();assert.ok(response,'Setup snapshot response missing');
   record.response={httpStatus:response.status(),at:Date.now()};
   const bytes=await response.body();record.response.bytes=bytes.length;
   assert.ok(bytes.length<=record.limits.responseBytes,'Setup snapshot response evidence limit');
   record.response.body=JSON.parse(bytes.toString('utf8'));
   record.response.finished=await response.finished();record.response.completedAt=Date.now();
   assert.equal(record.response.httpStatus,200);assert.equal(record.response.finished,null);
   assert.equal(record.response.body.accepted,true);assert.deepEqual(record.response.body.result,{status:'updated'});
  },record.limits.responseMs);
  const document=match.requestRecord.command.args.args.document;
  let readback;
  try{
   readback=await tapOperation(lifecycle,'setup-document-readback',()=>target.waitForFunction(({client,lease,document})=>{
    const s=window.amplifier.getState(),v=s.canvasWorkspace?.views?.find(v=>v.viewId===lease.viewId);
    const sameDocument=d=>d?.text===document.text&&d.controls?.length===document.controls.length&&
     d.controls.every((c,i)=>Object.keys(c).length===Object.keys(document.controls[i]).length&&
      Object.keys(document.controls[i]).every(k=>c[k]===document.controls[i][k]));
    if(s.client.id!==client.clientId||s.selectedSessionId!==client.sessionId||s.canvas.id!==lease.resourceId||
     !v||Object.keys(lease).some(k=>v[k]!==lease[k])||
     !sameDocument(s.canvas.document)||!sameDocument(v.document))return false;
    return {client:{clientId:s.client.id,sessionId:s.selectedSessionId},
     lease:Object.fromEntries(Object.keys(lease).map(k=>[k,v[k]])),document:s.canvas.document,viewDocument:v.document};
   },{client:arm.client,lease,document},{timeout:record.limits.readbackMs}),record.limits.readbackMs+500);
   record.readback=await tapOperation(lifecycle,'setup-document-readback-value',()=>readback.jsonValue());
  }finally{if(readback)try{
   await tapOperation(lifecycle,'setup-document-readback-dispose',()=>readback.dispose());record.cleanup.readbackDisposed=true;
  }catch(error){cleanupErrors.push(error)}}
  record.readbackAt=Date.now();
  record.inputAfter=await tapOperation(lifecycle,'setup-input-after',nativeRead);
  assert.deepEqual(record.inputAfter,{...record.inputBefore,value:unfinished,focused:true,documentFocused:true});
  record.afterSettlement=await tapOperation(lifecycle,'setup-after-settlement',()=>focusBoundarySnapshot(preview),3000);
  focusBoundaryOracle(record.afterSettlement,'inside');sameFocusContext(record.afterFill,record.afterSettlement);
  assert.equal(record.afterFill.host.frame.nodeId,record.afterSettlement.host.frame.nodeId);
  assert.deepEqual(record.afterSettlement.host.canvasDocument,document); // Observed document DID update.
  record.geometryAfterSettlement=await tapOperation(lifecycle,'setup-geometry-after-settlement',primary);
  assert.deepEqual(record.geometryAfterSettlement.primary,record.geometryAfterFill.primary);
  record.preservation=await tapOperation(lifecycle,'setup-preservation',()=>previewPreserved(provisional),3000);
 }catch(error){primaryError=error;record.error=errorEvidence(error)}
 finally{
  stopObservation();
  if(native)try{await tapOperation(lifecycle,'setup-input-dispose',()=>native.dispose());record.cleanup.inputDisposed=true}
  catch(error){cleanupErrors.push(error)}
  record.cleanup.errors=cleanupErrors.map(errorEvidence);
 }
 if(primaryError||cleanupErrors.length)throw new AggregateError([primaryError,...cleanupErrors].filter(Boolean),
  primaryError?.message||'Setup snapshot cleanup failed');
 record.passed=true;record.completedAt=Date.now();
}
function focusArmVerdict(arm){
 const g=arm.gesture,d=downloadSettlements.find(row=>row.label===`focus-${arm.name}-primary-v1`);
 if(!arm.setupSnapshot?.passed||!arm.focusEstablished||!g?.preconditionsPassed||!reductionEventsComplete(g)||
  !g.postGesturePreservation||g.postErrors?.length||arm.gates.length!==3||!arm.gates.every(gate=>gate.passed)||
  !arm.closed||arm.closeError||arm.lifecycle.operations.some(op=>op.state!=='fulfilled')||
  !d?.settled||!d.observation.listenerRemoved||!d.observation.timerCleared||d.observation.error||
  !['received','cancelled'].includes(d.observation.state))return 'inconclusive';
 const sequence=g.capture.events.filter(e=>e.isTrusted&&['pointerdown','pointerup','mousedown','mouseup','click'].includes(e.type));
 if(sequence.some(e=>!e.sameNode)&&/must reach the native control/.test(g.settlement.primaryFailure?.message||''))
  return 'miss';
 return g.strictEventsPassed&&g.download&&!arm.error?'hit':'inconclusive';
}
async function runFocusContrast(){
 ready=await startup;assert.notEqual(process.env.AMPLIFIER_TEST_INSTALLED,'1');
 contourLogicChecks();cornerShapeLogicChecks();
 const failures=[];
 for(const name of ['inside','outside']){
  const arm={name,createdAt:Date.now(),gates:[],keyboard:[],focusEstablished:false,closed:false};
  focusContrast.arms.push(arm);
  const owner=await launchOwner(`focus-${name}`,browserOwners);
  page=await connect(owner,touchOptions);
  const lifecycle={record:{operations:[],otherFailures:[]},owner,abort:null};
  arm.lifecycle=lifecycle.record;
  let retained;
  try{
   arm.browser={version:owner.browser.version(),options:touchOptions,launch:launchOptions};
   assert.equal(arm.browser.version,stateReductionBaseline.browser);
   await computedCornerPreflight(page,owner);
   arm.preflight=structuredClone(contourOracle.browserPreflight);
   if(name==='inside'){
    await action(page,'session.create',{title:'Generated file controls'});
    await showFixtureConversation(page,{navPinned:false});seed=await fixturePost(page,'seed');
    focusContrast.newer=await fixturePost(page,'newer');
    focusContrast.deleted=await fixturePost(page,'delete-source');
   }else{
    // One immutable server dataset, distinct fresh browser/client-local state.
    // Do not replay the seed/newer/delete one-shot effects for the second client.
    await action(page,'session.select',{id:seed.sessionId});
    await showFixtureConversation(page,{navPinned:false});
   }
   arm.client=await identity(page);
   if(name==='outside')assert.notEqual(arm.client.clientId,focusContrast.arms[0].client.clientId);
   const html=seed.artifacts.html,draft=stateReductionBaseline.draft;
   assert.equal(html.version,1);assert.equal(html.byteLength,426);assert.equal(html.sha256,stateReductionBaseline.html.sha256);
   await action(page,'canvas.select',{id:seed.unrelated.id,sessionId:seed.sessionId,version:1,presentation:'canvas'});
   await action(page,'canvas.visibility',{open:false,sessionId:seed.sessionId,canvasId:seed.unrelated.id});
   await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill(draft);
   await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(draft);
   arm.unrelatedPreservation=await preserved(page,draft);
   await reductionDataGate(page,'unrelated',arm.gates);
   retained=await startPreview(page,html,draft,'touch',focusContrast.unfinished,{arm,lifecycle});
   arm.retained={marker:retained.marker,unfinished:retained.unfinished,state:retained.state,documents:retained.documents};
   const primary=retained.panel.locator('.a-canvas-primary-actions').getByRole('button',{name:'Download canvas source',exact:true});
   await expect(retained.panel.getByRole('region',{name:'Canvas details',exact:true})).toBeHidden();
   await measureControls(page,[primary,retained.panel.getByRole('button',{name:'Canvas details',exact:true})],
    `focus-${name}-primary-before`);
   const geometryBefore=await primary.evaluate(node=>window.__fileControlsReductionSnapshot(node));
   reductionSnapshotOracle(geometryBefore,true);arm.geometryBeforeFocus=geometryBefore;
   arm.focusBefore=await tapOperation(lifecycle,'focus-boundary-before',()=>focusBoundarySnapshot(retained),3000);
   focusBoundaryOracle(arm.focusBefore,'inside');
   assert.deepEqual(arm.focusBefore.host.canvasDocument,arm.setupSnapshot.readback.document);
   const commandStart=commands.length;
   arm.commandStart=commandStart;arm.baselineAt=Date.now();
   if(name==='outside'){
    for(let index=0;index<24;index++){
     // No click, Enter, Space, .focus() or fallback. Deadline closes the owner
     // and drains the original RPC, not merely a rejected timeout race.
     await tapOperation(lifecycle,'keyboard-shift-tab',()=>page.keyboard.press('Shift+Tab'),1500);
     const snapshot=await tapOperation(lifecycle,'keyboard-focus-read',()=>focusBoundarySnapshot(retained),3000);
     const step={key:'Shift+Tab',snapshot};arm.keyboard.push(step);
     sameFocusContext(arm.focusBefore,snapshot);
     assert.deepEqual(snapshot.host.canvasDocument,arm.focusBefore.host.canvasDocument);
     step.geometry=await tapOperation(lifecycle,'keyboard-geometry',()=>
      primary.evaluate(node=>window.__fileControlsReductionSnapshot(node)),2000);
     reductionSnapshotOracle(step.geometry,true);
     step.preservation=await tapOperation(lifecycle,'keyboard-preservation',()=>previewPreserved(retained),3000);
     step.commandEnd=commands.length;
     step.commands=commands.slice(commandStart);
     assert.deepEqual(step.commands,[],'Application command arrived during strict keyboard interval');
     if(snapshot.host.active.nodeId===snapshot.host.toggle.nodeId)break;
     assert.ok(snapshot.host.active.inCanvas,'Design invalid: keyboard left Canvas somewhere other than its header toggle');
    }
   }
   arm.focusAfter=await tapOperation(lifecycle,'focus-boundary-after',()=>focusBoundarySnapshot(retained),3000);
   sameFocusContext(arm.focusBefore,arm.focusAfter);focusBoundaryOracle(arm.focusAfter,name);
   assert.deepEqual(arm.focusAfter.host.canvasDocument,arm.focusBefore.host.canvasDocument);
   arm.focusEstablished=true;
   const g=arm.gesture={reductionRequested:true,reductionPhase:'primary',focusContrastArm:name,tapAttempts:0,
    preconditionsPassed:false,checkpointObserved:false,originalOraclePassed:false,strictEventsPassed:false};
   let gestureError;
   try{
    g.download=await saveControlAndVerify(page,primary,html,`focus-${name}-primary-v1`,
     (control,armDownload)=>cornerTap(control,'bottom-right',g,armDownload,async diagnostic=>{
      diagnostic.preGesturePreservation=await previewPreserved(retained);
      assert.deepEqual(retained.state.views.map(({viewId,generation,selectedVersion})=>({viewId,generation,selectedVersion})),
       stateReductionBaseline.views);
      await reductionDataGate(page,'primary-before',arm.gates);
      const focus=await focusBoundarySnapshot(retained);
      sameFocusContext(arm.focusBefore,focus);focusBoundaryOracle(focus,name);
      assert.deepEqual(focus.host.canvasDocument,arm.focusBefore.host.canvasDocument);
      diagnostic.focusIntervalEnd=commands.length;
      diagnostic.focusIntervalCommands=commands.slice(commandStart);
      assert.deepEqual(diagnostic.focusIntervalCommands,[],'Application command arrived before the primary tap');
      diagnostic.focus={...focus,expectedNodeId:focus.host.active.nodeId};
      assert.equal(diagnostic.beforeStructure.focus.nodeId,diagnostic.focus.expectedNodeId);
     }),true);
   }catch(error){gestureError=error}
   finally{
    const postErrors=[],post={record:g.settlement||lifecycle.record,owner,abort:null};
    try{
     g.postGesturePreservation=await tapOperation(post,'focus-post-preservation',()=>previewPreserved(retained),3000);
     await tapOperation(post,'focus-post-state',()=>reductionDataGate(page,'primary-after',arm.gates),4000);
    }catch(error){postErrors.push(error)}
    g.postErrors=postErrors.map(errorEvidence);
    if(gestureError||postErrors.length)throw new AggregateError([gestureError,...postErrors].filter(Boolean),
     gestureError?.message||'Focus post-gesture preservation failed');
   }
  }catch(error){arm.error=errorEvidence(error);failures.push(error)}
  finally{
   if(retained)try{await tapOperation(lifecycle,'focus-preview-dispose',()=>retained.element.dispose())}
   catch(error){arm.disposeError=errorEvidence(error);failures.push(error)}
   try{await tapOperation(lifecycle,'focus-owner-close',()=>closeOwner(owner),4000);
    arm.closed=owner.closed&&!owner.browser.isConnected();arm.closedAt=Date.now()}
   catch(error){arm.closeError=errorEvidence(error);failures.push(error)}
  }
  arm.verdict=focusArmVerdict(arm);
  if(arm.verdict==='inconclusive'||arm.disposeError||errors.length||tapPending.size||
   metadataExchanges.some(row=>row.error))break; // Invalid arm is not repaired or replayed.
 }
 if(failures.length)throw new AggregateError(failures,'Focus contrast retained original failed gesture(s)');
 assert.equal(focusContrast.arms.length,2,'Focus contrast incomplete');
}
function focusContrastOutcome(){
 const arms=focusContrast.arms;
 if(arms.length!==2||arms.some(a=>focusArmVerdict(a)==='inconclusive'||a.disposeError)||
  !tapDrain.settled||tapDrain.pendingAfter!==0||errors.length||screenshotErrors.length||
  metadataExchanges.some(r=>r.error)||records.find(r=>r.cleanup)?.cleanup.homeRemoved!==true||
  cleanup?.code!==0||cleanup?.signal!==null)return 'inconclusive';
 const [inside,outside]=arms.map(focusArmVerdict);
 focusContrast.pattern=`inside-${inside}-outside-${outside}`;
 if(inside==='miss')return outside==='miss'?'both-miss-iframe-focus-not-necessary':'inside-miss-outside-hit-focus-dependence-only';
 return 'inconclusive'; // Retain both raw hits/misses; the known failing control did not reproduce.
}
function reductionOutcome(){
 const card=stateReduction.card,primary=stateReduction.primary;
 if(!card?.passed||!primary?.preconditionsPassed||!reductionEventsComplete(card)||!reductionEventsComplete(primary)||
  !primary.postGesturePreservation||primary.postErrors?.length||stateReduction.gates.length!==4||
  !stateReduction.gates.every(gate=>gate.passed)||!tapDrain.settled||tapDrain.pendingAfter!==0||
  errors.length||screenshotErrors.length||metadataExchanges.some(row=>row.error)||
  browserOwners.some(owner=>!owner.closed||owner.browser?.isConnected())||
  records.find(row=>row.cleanup)?.cleanup.homeRemoved!==true||cleanup?.code!==0||cleanup?.signal!==null)
  return 'inconclusive';
 const sequence=primary.capture.events.filter(event=>event.isTrusted&&['pointerdown','pointerup','mousedown','mouseup','click'].includes(event.type));
 if(sequence.some(event=>!event.sameNode)&&primary.settlement.primaryFailure&&
  /must reach the native control/.test(primary.settlement.primaryFailure.message))
  return 'smaller-reproduction-not-cause';
 return !failure&&primary.passed&&primary.strictEventsPassed&&primary.download?'non-reproduction-not-product-acceptance':'inconclusive';
}
async function run(){
 if(focusContrastMode)return runFocusContrast();
 if(stateReductionMode)return runStateReduction();
 ready=await startup;
 if(process.env.AMPLIFIER_TEST_INSTALLED==='1'||process.env.AMPLIFIER_TEST_PACKAGE_ROOT)assert.equal(ready.installedVerified,true);
 if(!cornerDiagnosticMode){contourLogicChecks();cornerShapeLogicChecks()}
 const desktopOwner=await launchOwner('desktop',browserOwners);
 page=await connect(desktopOwner,desktopOptions);
 if(!cornerDiagnosticMode)await computedCornerPreflight(page,desktopOwner);
 await action(page,'session.create',{title:'Generated file controls'});
 await showFixtureConversation(page,{navPinned:false});
 seed=await fixturePost(page,'seed');
 assert.notEqual(seed.workspace,seed.workingDirectory);
 const {html,adjacent,saved,outside}=seed.artifacts;
 const draft='Keep this unsent file-inspection draft — α.';
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill(draft);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe(draft);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.readOnlyVersion)).toBe(true);
 const controlsStart=commands.length;
 let details=await openDetails(page,html,'keyboard');
 await controlsOracle(details,html);
 const metadata=await action(page,'canvas.versions.inspect',{id:html.id,sessionId:seed.sessionId,version:1});
 assert.equal(metadata.version,1);assert.deepEqual(metadata.filePaths,html.filePaths);
 assert.equal(metadata.download.filename,html.filename);
 assert.equal(new URL(metadata.download.url,ready.url).searchParams.get('version'),'1');
 assert.equal(metadata.source,undefined,'Metadata inspect must not include the document body');

 // No second fixture or build: remove the real buttons, run the exact positive
 // oracle, then restore the same DOM nodes before any React state changes.
 await details.evaluate(node=>{node.__removed=[...node.querySelectorAll('button')].map(button=>({button,parent:button.parentNode,next:button.nextSibling}));for(const row of node.__removed)row.button.remove()});
 try{await assert.rejects(()=>controlsOracle(details,html),/Required control mismatch/);checks.push({check:'negative control: missing buttons rejected by the same oracle'})}
 finally{await details.evaluate(node=>{for(const row of node.__removed)row.parent.insertBefore(row.button,row.next);delete node.__removed})}
 await controlsOracle(details,html);
 await details.locator('summary').focus();
 for(const [name,value] of [[names[0],html.filePaths.absolute],[names[1],html.filePaths.relative]]){
  await page.keyboard.press('Tab');await expect(button(details,name)).toBeFocused();
  await copy(page,details,name,value,()=>page.keyboard.press('Enter'));
 }
 // Delay the saved URL itself, after the click has captured v1. Publish v2
 // through the maintained action before allowing the original GET to resolve.
 const downloadUrl=url=>url.pathname===new URL(metadata.download.url,ready.url).pathname&&url.searchParams.get('version')==='1';
 let raceObserved=false,raceError;
 await page.route(downloadUrl,async route=>{
  try{
   assert.equal(new URL(route.request().url()).searchParams.get('version'),'1');
   const revision=await fixturePost(page,'newer');assert.equal(revision.revision,2);
   raceObserved=true;await route.continue();
  }catch(error){raceError=error;await route.abort()}
 },{times:1});
 await page.keyboard.press('Tab');await expect(button(details,names[2])).toBeFocused();
 try{await saveAndVerify(page,details,html,'desktop-race-v1',()=>page.keyboard.press('Enter'))}
 catch(error){throw raceError||error}
 assert.equal(raceObserved,true,'Revision race did not run');
 checks.push({check:'v1 browser download remains original after v2 is saved before URL resolution'});
 await preserved(page,draft);
 await measure(page,details,'desktop');
 if(cornerDiagnosticMode){
  assert.equal(cornerDiagnostics.length,4,'Corner diagnostic must retain summary and all three controls');
  assert.ok(cornerDiagnostics.every(row=>row.complete),'Corner diagnostic capture missing or incomplete');
  cornerDiagnosticComplete=true;
  return; // Diagnostic checkpoint only, never a full-journey qualification.
 }
 await contourNegativeChecks(button(details,names[2]));

 // Inject only clipboard write failure; readText remains the real browser API.
 const deniedSentinel='denied-write:'+randomUUID();
 await page.evaluate(value=>navigator.clipboard.writeText(value),deniedSentinel);
 await page.evaluate(()=>{window.__clipboardDescriptor=Object.getOwnPropertyDescriptor(navigator.clipboard,'writeText');Object.defineProperty(navigator.clipboard,'writeText',{configurable:true,value:()=>Promise.reject(new DOMException('Synthetic denied write','NotAllowedError'))})});
 try{
  await button(details,names[0]).click();
  await expect(button(details,names[0]).locator('..').getByRole('status')).toHaveText('Clipboard access was denied. Select the block text to copy it.');
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),deniedSentinel);
  checks.push({check:'clipboard refusal reports failure and leaves real clipboard unchanged'});
 }finally{await page.evaluate(()=>{if(window.__clipboardDescriptor)Object.defineProperty(navigator.clipboard,'writeText',window.__clipboardDescriptor);else delete navigator.clipboard.writeText;delete window.__clipboardDescriptor})}
 await preserved(page,draft);

 let observedDownloads=0;const countDownload=()=>observedDownloads++;page.on('download',countDownload);
 await page.route(downloadUrl,route=>route.fulfill({status:403,contentType:'text/plain',body:'Synthetic guarded refusal'}),{times:1});
 await button(details,names[2]).click();
 await expect(details.locator('.a-saved-file-download [role="status"]')).toHaveText('The saved file could not be downloaded (403).');
 assert.equal(observedDownloads,0,'403 must not create an error-page download');
 await expect(button(details,names[2])).toBeEnabled();
 await saveAndVerify(page,details,html,'desktop-after-403');
 assert.equal(observedDownloads,1,'A legitimate subsequent click must download once');
 page.off('download',countDownload);
 checks.push({check:'403 has visible feedback and zero downloads, then a legitimate click succeeds'});
 await preserved(page,draft);

 // The real UI saves this deliberately normalized response. The SAME byte/hash
 // oracle must fail, rather than accepting a download event or a .html filename.
 await page.route(downloadUrl,route=>route.fulfill({status:200,headers:{'Content-Type':'text/html; charset=utf-8',
  'Content-Disposition':'attachment; filename="fixture.html"'},body:Buffer.from(html.body.replaceAll('\r\n','\n'))}),{times:1});
 await assert.rejects(()=>saveAndVerify(page,details,html,'negative-wrong-bytes'),/Saved file SHA-256 mismatch/);
 checks.push({check:'negative control: real wrong-byte download rejected by the same SHA-256 oracle'});
 await preserved(page,draft);

 const deleted=await fixturePost(page,'delete-source');assert.equal(deleted.sourceExists,false);
 await page.reload();await showFixtureConversation(page,{navPinned:false});
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(seed.sessionId);
 details=await openDetails(page,html);
 await controlsOracle(details,html);
 for(const [name,value] of [[names[0],html.filePaths.absolute],[names[1],html.filePaths.relative]])await copy(page,details,name,value,control=>control.click());
 await saveAndVerify(page,details,html,'reload-deleted-source-v1');
 await preserved(page,draft);
 checks.push({check:'reload after source deletion retains paths, exact saved v1 download and unrelated read-only Canvas/draft'});

 for(const row of [adjacent,saved,outside]){
  const other=await openDetails(page,row);await controlsOracle(other,row);
  if(row.filePaths.absolute)await copy(page,other,names[0],row.filePaths.absolute,control=>control.click());
  if(row.filePaths.relative)await copy(page,other,names[1],row.filePaths.relative,control=>control.click());
  if(row!==outside)await saveAndVerify(page,other,row,row===adjacent?'adjacent-markdown':'saved-only');
  await preserved(page,draft);
  await other.locator('summary').click();
 }
 assert.notEqual(adjacent.sha256,html.sha256,'Adjacent specimen must detect cross-file confusion');
 checks.push({check:'adjacent Markdown stays distinct; direct saved-only has no paths; outside-current-base has no relative-copy control'});
 // Open disclosure survives placement changes, but never retains a stale base.
 await placement(page);
 await expect(button(details,'Copy relative path')).toHaveCount(0);
 await expect(details.getByText(html.filePaths.relative,{exact:true})).toHaveCount(0);
 await expect(details.getByText(html.filePaths.absolute,{exact:true})).toBeVisible();
 await placement(page,true);
 await expect(button(details,'Copy relative path')).toBeVisible();
 await controlsOracle(details,html);
 // Hold only the FIRST matching request. Playwright 1.63.0 auto-continues a held
 // route if unroute runs early, so fulfill and await delivery BEFORE unregistering.
 // Later metadata requests must continue while the old receipt is held.
 await details.locator('summary').click();
 let releaseMetadata,metadataHeld=false,metadataClosing=false,metadataCleanup;
 const metadataCallbacks=new Set(),metadataErrors=[];
 const metadataGate=new Promise(resolve=>{releaseMetadata=resolve});
 const acceptedGate=observedGate('Old metadata acceptance'),deliveredGate=observedGate('Old metadata delivery');
 const delayMetadata=route=>{
  const pending=(async()=>{
   try{
    const data=route.request().postDataJSON();
    if(metadataClosing||metadataHeld||data?.action!=='canvas.versions.inspect'||data.args?.id!==html.id){
     await route.continue();return;
    }
    metadataHeld=true; // Synchronous, before route.fetch yields.
    const response=await route.fetch({timeout:5000});
    assert.equal(response.status(),200,'Held metadata request failed');
    acceptedGate.resolve();await metadataGate;
    await route.fulfill({response});deliveredGate.resolve();
   }catch(error){
    acceptedGate.reject(error);
    let deliveredError=error;
    try{await route.abort()}catch(abortError){deliveredError=new AggregateError([error,abortError],'Metadata route and abort failed')}
    // Even a later continue failure, after a gate settled, is retained and
    // thrown by drainMetadata. No rejected callback is left unobserved.
    metadataErrors.push(deliveredError);deliveredGate.reject(deliveredError);
   }
  })();
  metadataCallbacks.add(pending);
  return pending;
 };
 // Idempotent: the local finally and the overall deadline cleanup share this
 // release. A callback failure is rethrown by an awaited gate, including cleanup.
 const drainMetadata=()=>metadataCleanup??=(async()=>{
  metadataClosing=true;releaseMetadata();
  const failures=[];
  try{if(metadataHeld)await deliveredGate.wait()}catch(error){failures.push(error)}
  try{await page.unroute('**/api/actions',delayMetadata)}catch(error){failures.push(error)}
  try{await bounded(Promise.all([...metadataCallbacks]),8000,'Metadata callbacks did not settle')}catch(error){failures.push(error)}
  failures.push(...metadataErrors);
  if(failures.length)throw new AggregateError([...new Set(failures)],'Metadata interceptor failed');
 })();
 await page.route('**/api/actions',delayMetadata);
 routeCleanups.add(drainMetadata);
 try{
  await details.locator('summary').click();await acceptedGate.wait();
  assert.equal(metadataHeld,true);
  await placement(page);
  await expect.poll(()=>page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(row=>row.id===s.selectedSessionId)?.workingDirectory})).toBe(seed.workspace);
  await expect(button(details,'Copy relative path')).toHaveCount(0);
  // The app serializes actions; release the old receipt before expecting the
  // replacement metadata request to finish. Assert after both deliveries.
  releaseMetadata();await deliveredGate.wait();
  await expect(details).toContainText('No relative path:');
  await expect(details.getByText(seed.workspace,{exact:true})).toBeVisible();
  await expect(button(details,'Copy relative path')).toHaveCount(0);
  await expect(details.getByText(html.filePaths.relative,{exact:true})).toHaveCount(0);
 }finally{
  try{await drainMetadata()}finally{routeCleanups.delete(drainMetadata)}
 }
 await placement(page,true);
 await expect(button(details,'Copy relative path')).toBeVisible();
 checks.push({check:'open details refreshes current working folder and rejects delayed prior-placement metadata'});
 const desktopCommands=commands.slice(controlsStart);
 assert.deepEqual(desktopCommands.filter(row=>['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[],
  'File actions must not select/mount previews or send messages');

 // A real touch-enabled context, not just a resized desktop. Reuse the same
 // retained artifact and positive oracles at both narrow widths.
 const touchOwner=await launchOwner('touch',browserOwners);
 const mobile=await connect(touchOwner,touchOptions);
 await action(mobile,'session.select',{id:seed.sessionId});
 await showFixtureConversation(mobile,{navPinned:false});
 await action(mobile,'canvas.select',{id:seed.unrelated.id,sessionId:seed.sessionId,version:1,presentation:'canvas'});
 await action(mobile,'canvas.visibility',{open:false,sessionId:seed.sessionId,canvasId:seed.unrelated.id});
 const mobileDraft='Keep touch draft — unsubmitted.';
 await mobile.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill(mobileDraft);
 await expect.poll(()=>mobile.evaluate(()=>window.amplifier.getState().view.draft)).toBe(mobileDraft);
 const touchStart=commands.length;
 for(const width of [390,320]){
  await mobile.setViewportSize({width,height:width===390?844:740});
  assert.ok(await mobile.evaluate(()=>navigator.maxTouchPoints>0));
  const touchDetails=await openDetails(mobile,html,'touch');
  await controlsOracle(touchDetails,html);
  await measure(mobile,touchDetails,`touch-${width}`);
  await copy(mobile,touchDetails,names[0],html.filePaths.absolute,control=>cornerTap(control,'top-left'));
  await copy(mobile,touchDetails,names[1],html.filePaths.relative,control=>cornerTap(control,'bottom-right'));
  await saveAndVerify(mobile,touchDetails,html,`touch-${width}-deleted-v1`,control=>cornerTap(control,'bottom-right'));
  await preserved(mobile,mobileDraft);
  // Leave it open. On the next width freshDetails owns BOTH the close fence and
  // the fresh opening, instead of racing an external close's native toggle.
 }
 assert.deepEqual(commands.slice(touchStart).filter(row=>['canvas.select','canvas.views.inline','conversation.send','session.send'].includes(row?.action)),[]);
 checks.push({check:'390px/320px emulated touch, actual contour taps, 44px targets, four contour-relative native hit probes and saved PNGs'});
 // End the card-only phase here. The unrelated read-only Canvas and no-document
 // assertions above remain strict; the next phase intentionally opens our HTML.
 assert.deepEqual(requests.filter(row=>row.path.endsWith('/document')),[],'Card details/download must not request an HTML document');
 checks.push({check:'card-only phase completed with zero document requests and unrelated historical Canvas retained'});
 const desktopPreview=await startPreview(page,html,draft,'desktop');
 try{
  // Meaningful negatives for the same retained-preview oracle, without a second
  // renderer or a deliberate reload that would invalidate the original handle.
  await desktopPreview.input.evaluate(node=>{node.value='Synthetic lost input'});
  try{await assert.rejects(()=>previewPreserved(desktopPreview),/Unfinished preview input changed/)}
  finally{await desktopPreview.input.evaluate((node,value)=>{node.value=value},desktopPreview.unfinished)}
  await desktopPreview.frame.evaluate(()=>{document.body.dataset.fileControlsToken='Synthetic rerun token'});
  try{await assert.rejects(()=>previewPreserved(desktopPreview),/Preview execution marker changed/)}
  finally{await desktopPreview.frame.evaluate(value=>{document.body.dataset.fileControlsToken=value},desktopPreview.marker.token)}
  await previewPreserved(desktopPreview);
  checks.push({check:'negative controls: lost input and changed execution token rejected by the same retained-preview oracle'});
  await usePreviewControls(desktopPreview,'desktop-preview','keyboard');
 }finally{await desktopPreview.element.dispose()}
 await mobile.setViewportSize({width:390,height:844});
 const touchPreview=await startPreview(mobile,html,mobileDraft,'touch');
 try{
  for(const width of [390,320]){
   await mobile.setViewportSize({width,height:width===390?844:740});
   assert.ok(await mobile.evaluate(()=>navigator.maxTouchPoints>0));
   await previewPreserved(touchPreview);
   await usePreviewControls(touchPreview,`touch-${width}-preview`,'touch');
   if(touchEventDiagnosticMode||nativeStateDiagnosticMode||portalHostExperimentMode)return; // No next gesture, control, width or full-journey verdict.
  }
 }finally{await touchPreview.element.dispose()}
 const final=await fixtureRead(page);
 assert.deepEqual(final.sent,[]);assert.equal(final.sourceDeleted,true);assert.equal(final.sourceExists,false);
 assert.equal(final.newer.revision,2);
 assert.equal(requests.filter(row=>row.path.endsWith('/document')).length,2,
  'Only the two deliberate preview setups may request HTML; all copy/download phases must retain them');
 const {paddingOrigin,...contourNegatives}=contourOracle.negativeCounts;
 assert.deepEqual(contourNegatives,{size:2,regions:4,obstruction:1},'Required contour negatives did not complete');
 assert.ok(paddingOrigin>0,'No nonzero-border padding conversion negative ran');
 assert.equal(paddingOrigin,geometry.filter(row=>row.label==='corner-tap-before'&&(row.borders.left||row.borders.top)).length,
  'Every nonzero-border touch must reject the old padding conversion');
 assert.deepEqual(contourOracle.cornerShapeCaptureErrors,[],'Corner-shape console evidence capture failed');
 assert.deepEqual(errors,[]);
 if(fullJourney){
  assert.deepEqual(compactPrimaryGestures.map(g=>g.label),['touch-390-preview','touch-320-preview']);
  assert.equal(compactPrimaryGestures[0].baselineMatched,!currentLayout);
  if(currentLayout)assert.equal(compactPrimaryGestures[0].layoutBinding,currentLayout);
  for(const gesture of compactPrimaryGestures){
   assert.equal(gesture.tapAttempts,1);assert.equal(gesture.strictEventsPassed,true);
   assert.equal(gesture.originalOraclePassed,true);assert.equal(gesture.originalListenerRemoved,true);
   assert.equal(gesture.handleDisposed,true);assert.deepEqual(gesture.captureErrors,[]);assert.deepEqual(gesture.cleanupErrors,[]);
   assert.equal(gesture.postGesture.passed,true);
  }
  assert.ok(downloadDecorations.length>6&&downloadDecorations.every(r=>r.passed));
  assert.deepEqual(downloadDecorations.filter(r=>r.compact).map(r=>r.label),
   ['desktop-preview','touch-390-preview','touch-320-preview'].flatMap(label=>[label+'-primary-before',label+'-primary-after']));
 }
 if(coarseToolbarQualificationMode){
  const {layoutChecks,gesture}=coarseToolbarQualification;
  assert.deepEqual(layoutChecks.map(({label,phase,passed})=>({label,phase,passed})),
   ['desktop-preview','touch-390-preview','touch-320-preview'].flatMap(label=>
    ['before','after'].map(phase=>({label,phase,passed:true}))), 'All six fine/coarse toolbar layout checks must complete');
  assert.ok(layoutChecks.filter(row=>row.phase==='after').every(row=>row.feedback?.contained&&row.feedback.lines.length>0),
   'All three toolbar feedback checks must remain visible');
  assert.equal(gesture?.baselineCheck?.passed,true);assert.equal(gesture?.eventCheck?.passed,true);
  assert.equal(gesture?.postGesture?.passed,true);assert.equal(gesture.tapAttempts,1);
  assert.equal(gesture.originalOraclePassed,true);assert.equal(gesture.originalListenerRemoved,true);
  assert.equal(gesture.handleDisposed,true);assert.deepEqual(gesture.cleanupErrors,[]);
  assert.deepEqual(gesture.captureErrors,[]);
  coarseToolbarQualification.complete=true;
 }
}

try{
 running=run();
 await Promise.race([running,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Focused file-controls proof exceeded 120 seconds')),120000)})]);
}catch(error){
 failure=error;
 for(const target of browserOwners.flatMap(owner=>owner.browser?.contexts()||[]).flatMap(context=>context.pages()).filter(target=>!target.isClosed())){
  const label=pageLabels.get(target)||'unknown';
  try{preCleanupSnapshots.push({client:label,...await bounded(target.evaluate(()=>({
   at:Date.now(),viewport:{width:innerWidth,height:innerHeight},
   details:[...document.querySelectorAll('details.a-canvas-file-path')].map(window.__fileDetailsSnapshot)
  })),1500,`${label} pre-cleanup details capture timed out`)})}
  catch(error){preCleanupSnapshots.push({client:label,error:errorEvidence(error)})}
  try{await target.screenshot({path:join(evidence,`failure-${label}.png`),timeout:3000})}
  catch(error){screenshotErrors.push({client:label,error:errorEvidence(error)})}
 }
}finally{
 clearTimeout(timer);browserOwners.closing=true;
 try{
  await finishFixture({label:'Generated file controls fixture',
   close:[...Array.from(routeCleanups,release=>['held metadata response',release]),...browserOwners.map(owner=>[`${owner.label} browser`,()=>closeOwner(owner)])],
   stop:async()=>{cleanup=await stop()}});
  assert.equal(records.find(row=>row.cleanup)?.cleanup.homeRemoved,true,'Fixture-owned state/socket directory was not removed');
 }catch(error){failure=failure?new AggregateError([failure,error],'File controls proof and cleanup failed'):error}
 // A deadline is not cancellation. Closing the browser settles outstanding
 // driver calls; await the run before freezing evidence, including its finally.
 if(running)try{await bounded(running,10000,'Driver did not settle after fixture cleanup')}
 catch(error){if(error!==failure)failure=failure?new AggregateError([failure,error],'File controls proof did not settle cleanly'):error}
 try{
  // These are the original tagged RPC/close promises, not a second await of the
  // rejected journey aggregate. Browser shutdown above is the cancellation.
  tapDrain.pendingBefore=tapPending.size;
  await bounded(Promise.all([...tapPending]),3000,'Tap operations remain in flight after browser cleanup');
  tapDrain.pendingAfter=tapPending.size;tapDrain.settled=tapPending.size===0;
  assert.equal(tapDrain.settled,true);
  assert.ok(downloadSettlements.every(row=>row.settled&&row.observation.listenerRemoved&&row.observation.timerCleared),
   'Download observation/gesture branch did not settle');
 }catch(error){
  Object.assign(tapDrain,{settled:false,pendingAfter:tapPending.size,error:errorEvidence(error)});
  failure=failure?new AggregateError([failure,error],'File controls proof and tap/download settlement failed'):error;
 }
 try{
  await bounded(Promise.all([...metadataPending]),5000,'Metadata observations did not settle after cleanup');
  assert.deepEqual(metadataExchanges.filter(record=>record.error),[],'Metadata response capture failed');
 }
 catch(error){failure=failure?new AggregateError([failure,error],'Metadata observation cleanup failed'):error}
 if(stateReductionMode)stateReduction.outcome=reductionOutcome();
 if(focusContrastMode)focusContrast.outcome=focusContrastOutcome();
 const writes=await Promise.allSettled([
  writeFile(join(evidence,'fixture.log'),logs),
  writeFile(join(evidence,'observations.json'),JSON.stringify({passed:!failure,error:failure?.stack,failure:errorEvidence(failure),screenshotErrors,
  ...(stateReductionMode?{mode:'state-reduction',productAcceptance:false,stateReduction}:{}),
  ...(focusContrastMode?{mode:'focus-contrast',productAcceptance:false,focusContrast}:{}),
  ...(cornerDiagnosticMode?{mode:'corner-diagnostic',diagnosticComplete:cornerDiagnosticComplete,
   stoppedAfter:cornerDiagnosticComplete?'first-desktop-measure':null,cornerDiagnostics}:{}),
  ...(touchEventDiagnosticMode||nativeStateDiagnosticMode||portalHostExperimentMode?{
   mode:portalHostExperimentMode?'portal-host-experiment':nativeStateDiagnosticMode?'native-state-diagnostic':'touch-event-diagnostic',
   diagnosticComplete:touchEventDiagnosticComplete,
   productAcceptance:false,stoppedAfter:touchEventDiagnosticComplete?'first-390-primary-download':null,
   touchEventDiagnostic,diagnosticLimits:[
    'One unchanged locator.tap; passive document capture for at most 2000ms from arm, not a device or product pass.',
    'Missing compatibility mousedown, capture errors and truncation are UNKNOWN, never a request to repeat input.',
    'Synchronous DOM reads may affect timing. Each event snapshot is atomic, not proof of unchanged layout between events.',
    'Capture state is limited to queried nodes; UA target adjustment and pending/global capture ownership are not observable.']}:{}),
  ...(coarseToolbarQualificationMode?{mode:'coarse-toolbar-qualification',
   qualificationComplete:!failure&&coarseToolbarQualification.complete,coarseToolbarQualification,
   qualificationLimits:[
    'One fixed 14px coarse-pointer external body margin is a mitigation hypothesis, not a root-cause guarantee.',
    'First-390 button geometry and intended contour point are bound to the retained failed report; no passing-point search or gesture retry.',
    'The first-390 gesture retains passive capture plus the full download/preservation verifier; missing click, wrong target, truncation and capture/cleanup loss fail closed.',
    'The contact-envelope check assumes DPR=1 and visual scale=1; fine desktop and coarse 390/320 layouts are separate observations.',
    'Existing screenshots and all 13 narrow PNG brackets remain evidence awaiting independent visual judgment.']}:{}),
  contourOracle,
  ...(currentLayout?{currentLayout}:{}),
  ...(fullJourney?{downloadDecorations,compactPrimaryGestures,downloadNeighbors}:{}),
  installed:ready&&{packageRoot:ready.packageRoot,verified:ready.installedVerified,staticSha256:ready.staticSha256},
  seed,checks,geometry,fileFrameCaptures,downloads,downloadSettlements,tapSettlements,tapDrain,
  requests,commands,metadataExchanges,detailsReadiness,preCleanupSnapshots,
  errors,cleanup,fixtureCleanup:records.find(row=>row.cleanup)?.cleanup,
  browserCleanup:browserOwners.map(owner=>({label:owner.label,closed:owner.closed,connected:owner.browser?.isConnected()??false})),
  limits:focusContrastMode?focusContrast.limits:stateReductionMode?stateReduction.limits:['Synthetic runtime and fixture bearer authentication; no provider, login, remote server or physical touch-device claim.',
   'Clipboard/403/wrong-byte faults are deliberate test injections; positive clipboard reads and downloaded files are real browser results.',
   'The two later HTML previews execute deliberately as setup; copy/download must retain each original frame and execution token.',
   'Exact-byte identity covers ordinary saved UTF-8 HTML/Markdown, not Babylon engine-prepending exports.',
   '320px fileFrameCaptures preserve raw intersections/occlusion at existing scroll positions; sampling is not a visual pass.',
   'Geometry reads are atomic within each RAF callback, but PNGs are separate. matched-before-after-only is bracket equality, not proof of unchanged intervening frames. Visual judgment remains pending.',
   'PNG capture and measured reachability do not claim independent human visual review.']},null,2)+'\n')]);
 const writeErrors=writes.filter(row=>row.status==='rejected').map(row=>row.reason);
 if(writeErrors.length)failure=new AggregateError([...(failure?[failure]:[]),...writeErrors],'File controls evidence write failed');
 const entries=await readdir(evidence,{withFileTypes:true});
 assert.ok(entries.every(entry=>entry.isFile()),'Export only ordinary evidence files, never fixture sockets/profile/state');
}
if(failure)throw failure;
console.log(focusContrastMode?`Focus contrast: ${focusContrast.outcome}; not product acceptance; evidence: ${evidence}`:
 stateReductionMode?`State/history reduction: ${stateReduction.outcome}; not product acceptance; evidence: ${evidence}`:
 touchEventDiagnosticMode||nativeStateDiagnosticMode||portalHostExperimentMode?`Generated file controls touch event checkpoint observed (cause unknown; not qualification); evidence: ${evidence}`:
 cornerDiagnosticMode?`Generated file controls corner diagnostic complete (not full qualification); evidence: ${evidence}`:
 `Generated file controls passed; evidence: ${evidence}`);
