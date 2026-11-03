// Focused production-host regression for question cards inside the shared
// transcript scroller. Uses the prepared browser-test contract or ordinary
// background runner; it builds/installs nothing and uses no live provider.
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdtempSync,readFileSync,realpathSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {questionFadeExecution} from './question-fade-runtime.mjs';
import {boundaryRead,boundaryIdentity,boundaryRequests,boundaryLease,BOUNDARY_OBSERVATION_MS} from './browser-boundary-evidence.mjs';

const root=realpathSync(fileURLToPath(new URL('../../',import.meta.url)));
const {python,artifactRoot,evidence:executionEvidence}=questionFadeExecution({root});

const artifacts=mkdtempSync(join(artifactRoot,'question-transcript-fade-'));
const hash=value=>createHash('sha256').update(value).digest('hex');
const sourceFiles=[
 'frontend/src/conversation-rail.css',
 'frontend/src/questions.jsx',
 'frontend/src/main.jsx',
 'frontend/src/chat-scroll.js',
 'frontend/tests/transcript-overflow.test.mjs',
 'frontend/tests/question-transcript-fade-browser.mjs',
 'frontend/tests/question-fade-runtime.mjs',
 'frontend/tests/fixture-lifecycle.mjs',
 'tests/fixtures/questions_ui_server.py',
];
const sourceHashes=()=>Object.fromEntries(sourceFiles.map(file=>
 [file,hash(readFileSync(join(root,file)))]));
const buildBytes=readFileSync(join(root,'amplifier_web/static/build.json'));
const report={
 status:'running',
 runtime:'synthetic-question-fixture',
 liveProvider:false,
 ...executionEvidence,
 observedRuntime:{node:process.version,platform:process.platform,arch:process.arch,selectedPython:python,
  pythonIdentity:'Selected executable; not an installed-distribution attestation'},
 sourceSha256:sourceHashes(),
 build:{
  path:'amplifier_web/static/build.json',
  diskSha256:hash(buildBytes),
  diskMetadata:JSON.parse(buildBytes),
  servedSha256:null,
  servedMetadata:null,
  stylesheetAssets:[],
  stylesheetAssetsDropped:0,
 },
 fixture:{started:false,ready:false,close:null,shutdown:null,stderrTail:'',stderrCharactersObserved:0,stderrTruncated:false},
 browser:{started:false,closed:false},
 capabilities:null,
 question:{id:null,required:false,answerSubmitted:false,answerRequests:[]},
 actions:[],
 focus:[],
 geometry:[],
 screenshots:[],
 browserErrors:[],
 httpErrors:[],
 externalRequests:[],
 instrumentationErrors:[],
 evidenceDropped:0,
 limits:{
  successScreenshots:6,
  failureScreenshots:1,
  stderrTailCharacters:24000,
  retainedEntriesPerArray:32,
  stylesheetAssets:12,
  actionRecords:80,
 },
 limitations:[
  'One synthetic chat message uses the fixture runtime; no live provider is configured or contacted.',
  'The optional question is cancelled after inspection; no question answer is submitted.',
  'Saved screenshots require separate pixel review; this script records browser state and geometry.',
  'Chromium forced-colors is emulated and is not a native operating-system check.',
  'The report keeps source and served-build identities separate; matching source-to-build provenance remains the runner owner’s responsibility.',
 ],
};
const writeReport=()=>writeFileSync(join(artifacts,'report.json'),JSON.stringify(report,null,2)+'\n');
const boundedPush=(array,value,limit=report.limits.retainedEntriesPerArray)=>{
 if(array.length<limit)array.push(value);
 else report.evidenceDropped++;
};
let fixture,stopFixture,browser,page,pane,failure,stderr='',stderrCharacters=0;
let failureScreenshotAttempted=false;

// One exact second-review attempt, following questions-browser's request-object
// correlation. Diagnostic failures never replace the journey's original error.
async function observeReviewBoundary(page,target,artifactReceipt){
 const data={target,stage:'setup',snapshots:[],network:[],errors:[],omitted:0,
  limits:{requests:12,snapshots:6,responseBytes:131072,optionalReadMs:BOUNDARY_OBSERVATION_MS},
  limitation:'Passive reads can affect timing. Deadlines need a responsive Node event loop and do not cancel evaluation. Response bodies are Playwright observations, not independent wire captures.'};
 report.reviewBoundary=data;
 let active=false,closed=false,sequence=0,finished;
 const requests=new WeakMap();
 const id=value=>typeof value==='string'&&/^[a-zA-Z0-9:_-]{1,200}$/.test(value)?value:null;
 const number=value=>Number.isSafeInteger(value)&&value>=0?value:null;
 const pick=row=>{
  if(!row||typeof row!=='object')return null;
  const result={};
  for(const key of ['id','viewId','resourceId','resourceRevision','previewId','sessionId','questionId','hostInstanceId'])
   if(Object.hasOwn(row,key))result[key]=id(row[key]);
  for(const key of ['version','selectedVersion','questionContentRevision','revision','stateRevision','generation','status'])
   if(Object.hasOwn(row,key))result[key]=number(row[key]);
  for(const key of ['accepted','resetPreview','presentationRequest'])
   if(Object.hasOwn(row,key))result[key]=typeof row[key]==='boolean'?row[key]:null;
  if(Object.hasOwn(row,'presentation'))result.presentation=['canvas','inline','saved'].includes(row.presentation)?row.presentation:'other';
  if(Object.hasOwn(row,'code'))result.code=['canvas_preview_reset_required','canvas_preview_dirty','invalid_response','stale_revision','canvas_view_stale'].includes(row.code)?row.code:'other';
  result.errorPresent=!!row.error;
  return result;
 };
 const fault=phase=>{if(data.errors.length<8)data.errors.push(phase);else data.omitted++};
 data.fixtureArtifactReceipt=pick(artifactReceipt);
 const onRequest=request=>{
  if(!active||closed)return;
  try{
   const url=new URL(request.url());if(url.origin!==new URL(page.url()).origin)return;
   let action,args,commandId=null;
   if(request.method()==='POST'&&url.pathname==='/api/actions'){
    const text=request.postData();if(!text||text.length>32768){fault('action-body-unavailable');return}
    const raw=JSON.parse(text),body=raw?.action==='shell.command'?raw.args:raw;
    action=body?.action;args=body?.args;
    if(!['canvas.select','canvas.views.inline'].includes(action))return;
    commandId=id(raw.id??body.id);
   }else if(request.method()==='GET'&&/^\/api\/canvas\/views\/[^/]+\/resource$/.test(url.pathname)){
    action='inline-resource';args={viewId:url.pathname.split('/')[4],resourceId:url.searchParams.get('resourceId'),
     resourceRevision:url.searchParams.get('resourceRevision'),
     generation:url.searchParams.has('generation')?Number(url.searchParams.get('generation')):null};
   }else return;
   if(data.network.length>=data.limits.requests){data.omitted++;return}
   const identityMatch=action==='inline-resource'?boundaryIdentity({resourceId:id(args.resourceId)},{resourceId:target.resourceId}):
    boundaryIdentity({resourceId:id(action==='canvas.select'?args?.id:args?.resourceId),version:number(args?.version),
     questionId:id(args?.questionId),questionContentRevision:number(args?.questionContentRevision)},
    {resourceId:target.resourceId,version:target.version,questionId:target.questionId,questionContentRevision:target.questionContentRevision});
   const row={sequence:++sequence,stage:data.stage,action,commandId,args:pick(args),identityMatch,responseState:'not-observed'};
   data.network.push(row);requests.set(request,row);
  }catch{fault('request-observation-unavailable')}
 };
 const onResponse=response=>{
  try{
  const row=requests.get(response.request());if(!row||closed)return;
  row.status=response.status();row.responseSequence=++sequence;
  if(row.action==='inline-resource'){row.responseState='headers-observed';return}
  const length=response.headers()['content-length'];
  if(!/^\d+$/.test(length??'')||Number(length)>data.limits.responseBytes){row.responseState='body-size-unavailable-or-over-limit';return}
  row.responseState='body-pending';
  void response.body().then(bytes=>{
   if(closed)return;
   if(bytes.length>data.limits.responseBytes){row.responseState='body-over-limit';return}
   const body=JSON.parse(bytes.toString('utf8'));
   row.response={...pick(body),result:pick(body?.result),receipt:pick(body?.receipt)};
   row.responseState='observed';row.bodySequence=++sequence;
  }).catch(()=>{if(!closed){row.responseState='body-unreadable';fault('response-body-unavailable')}});
  }catch{fault('response-observation-unavailable')}
 };
 const onFailed=request=>{const row=requests.get(request);if(row&&!closed){row.responseState='request-failed';row.failureSequence=++sequence}};
 const snapshot=async stage=>{
  if(closed)return;
  data.stage=stage;
   const result=await boundaryRead(()=>page.evaluate(target=>{
    const id=value=>typeof value==='string'&&/^[a-zA-Z0-9:_-]{1,200}$/.test(value)?value:null;
    const scalar=(row,keys)=>row?Object.fromEntries(keys.map(key=>[key,
     typeof row[key]==='boolean'||Number.isSafeInteger(row[key])?row[key]:id(row[key])])):null;
    const state=window.amplifier?.getState();
    const question=document.querySelector(`[data-question-id="${CSS.escape(target.questionId)}"]`);
    const card=question?.querySelectorAll('.a-compact-artifact')[1],button=card?.querySelector('.a-document-preview-toggle');
    const notice=card?.querySelector('.a-compact-artifact-notice');
    const controls=notice?[...notice.querySelectorAll('button')].slice(0,4):[];
    const keep=controls.some(node=>node.textContent==='Keep current preview');
    const reset=controls.some(node=>node.textContent?.startsWith('Reset preview and '));
    const session=state?.sessions?.find(row=>row.id===target.sessionId);
    const savedQuestion=session?.questions?.find(row=>row.id===target.questionId);
    const presentations=state?.canvasWorkspace?.presentations||[],views=state?.canvasWorkspace?.views||[];
    const exact=presentations.filter(row=>row.id===target.resourceId&&row.version===target.version);
    const leases=views.filter(row=>row.resourceId===target.resourceId);
    return {selectedSessionId:id(state?.selectedSessionId),stateRevision:Number.isSafeInteger(state?.revision)?state.revision:null,
     question:scalar(savedQuestion,['id','revision','contentRevision','status']),
     cardPresent:!!card,buttonPresent:!!button,expanded:button?.getAttribute('aria-expanded')==='true',
     buttonDisabled:button?.disabled??null,iframeCount:card?.querySelectorAll('iframe').length??0,
     notice:!notice?'absent':keep&&reset?'keep-or-reset':notice.textContent?.startsWith('Shown in Canvas')?'shown-in-canvas':'other',
     keepControl:keep,resetControl:reset,inlineErrorPresent:!!card?.querySelector('.a-inline-artifact-notice'),
     primary:scalar(state?.canvas,['id','selectedVersion','revision','open','_presentationDisposed']),
     presentations:exact.slice(0,4).map(row=>scalar(row,['id','version','presentation','questionId','questionContentRevision','sessionId'])),
     presentationsOmitted:Math.max(0,exact.length-4),
     leases:leases.slice(0,8).map(row=>scalar(row,['viewId','resourceId','resourceRevision','generation','selectedVersion','dirty'])),
     leasesOmitted:Math.max(0,leases.length-8)};
   },target));
   if(closed)return;
   const facts=result.status==='observed'?result.value:{status:result.status};
   if(result.status!=='observed')fault(stage+'-state-'+result.status);
   if(data.snapshots.length<data.limits.snapshots)data.snapshots.push({sequence:++sequence,stage,...facts});else data.omitted++;
 };
 const finish=failed=>finished??=(async()=>{
  data.endedStage=data.stage;
  active=false;page.off('request',onRequest);page.off('response',onResponse);page.off('requestfailed',onFailed);
  await snapshot(failed?'failure-final':'preview-mounted');
  closed=true;
  data.selectionRequest=boundaryRequests(data.network,'canvas.select',!!(data.omitted||data.errors.length));
  data.inlineRequest=boundaryRequests(data.network,'canvas.views.inline',!!(data.omitted||data.errors.length));
  data.pendingResponseReads=data.network.filter(row=>row.responseState==='body-pending').length;
  for(const row of data.network.filter(row=>row.action==='inline-resource'))
   row.matchesAttemptLease=boundaryLease(row,data.network,!!(data.omitted||data.errors.length));
  // An unfinished read stays explicitly pending; do not extend the iframe wait.
 })();
 page.on('request',onRequest);page.on('response',onResponse);page.on('requestfailed',onFailed);
 await snapshot('before-click');active=true;data.stage='click-pending';
 return {snapshot,finish,waiting(){if(!closed)data.stage='iframe-wait'}};
}

const inspect=async(pane,questionId)=>pane.evaluate((el,id)=>{
 const box=node=>{
  if(!node)return null;
  const r=node.getBoundingClientRect();
  return {x:r.x,y:r.y,width:r.width,height:r.height,top:r.top,bottom:r.bottom};
 };
 const section=el.querySelector('[data-part="questions"]');
 const question=id?el.querySelector(`[data-question-id="${CSS.escape(id)}"]`):null;
 const conversation=el.closest('[data-part="conversation"]');
 const state=window.amplifier?.getState();
 const session=state?.sessions?.find(row=>row.id===el.dataset.sessionId);
 const layout=selector=>{
  const node=conversation?.querySelector(selector);
  if(!node)return null;
  const style=getComputedStyle(node);
  return {box:box(node),display:style.display,visibility:style.visibility,
   marginTop:style.marginTop,marginBottom:style.marginBottom};
 };
 const composer=conversation?.querySelector('[data-part="composer"]');
 const editor=composer?.querySelector('[role="textbox"][aria-label="Message Amplifier"]');
 const primary=composer?.querySelector('[data-part="composer-primary-action"]');
 const anchor=el.querySelector('.a-message.a-user'),anchorBox=box(anchor);
 const active=document.activeElement;
 const style=getComputedStyle(el);
 const paneBox=box(el),activeName=active?.getAttribute('aria-label')||active?.textContent?.trim()||'';
 return {
  runtime:{
   sessionId:session?.id??null,selectedSessionId:state?.selectedSessionId??null,
   status:session?.status??null,streaming:!!session?.streaming,
   activityPhase:session?.activity?.phase??null,
   activeToolCount:session?.activity?.activeTools?.length??0,workerCount:session?.workers?.length??0,
  },
  geometry:{
   pane:paneBox,
   anchor:anchorBox?{id:anchor.dataset.messageId,offset:anchorBox.top-paneBox.top,
    paneTop:paneBox.top,scrollTop:el.scrollTop}:null,
   surroundings:{
    activityCount:conversation?.querySelectorAll('[data-part="activity"]').length??0,
    activity:layout('[data-part="activity"]'),composer:layout('[data-part="composer"]'),
    editor:editor?{box:box(editor),textLength:(editor.value??editor.textContent??'').length}:null,
    primaryAction:primary?{action:primary.dataset.action,label:primary.getAttribute('aria-label'),disabled:primary.disabled}:null,
    notice:layout('.a-question-notice'),latest:layout('.a-chat-jump'),
   },
   scrollTop:el.scrollTop,
   scrollHeight:el.scrollHeight,
   clientHeight:el.clientHeight,
   maxScrollTop:Math.max(0,el.scrollHeight-el.clientHeight),
   remaining:Math.max(0,el.scrollHeight-el.clientHeight-el.scrollTop),
   question:box(question),
  },
  overflow:{above:el.dataset.overflowAbove??null,below:el.dataset.overflowBelow??null},
  mask:{
   standard:style.maskImage,
   webkit:style.webkitMaskImage,
   composite:style.maskComposite,
   edge:style.getPropertyValue('--_a-transcript-edge-mask').slice(0,600),
  },
  focus:{
   activeTag:active?.tagName?.toLowerCase()||null,
   activeRole:active?.getAttribute('role')||null,
   activeName:activeName.slice(0,180),
   activeClass:String(active?.className||'').slice(0,180),
   activeFocusVisible:!!active?.matches?.(':focus-visible'),
   activeInsidePane:!!active&&el.contains(active),
   activeTextarea:active instanceof HTMLTextAreaElement,
   activeIframe:active instanceof HTMLIFrameElement,
   activeIframeCssFocus:active instanceof HTMLIFrameElement&&active.matches(':focus'),
   transcriptIframeFocus:el.dataset.transcriptIframeFocus??null,
   activeEditable:!!active?.matches?.('input:focus, textarea:focus, select:focus, [contenteditable]:focus, iframe:focus'),
   paneFocused:document.activeElement===el,
   paneFocusVisible:el.matches(':focus-visible'),
   paneFocusWithin:el.matches(':focus-within'),
   questionSectionFocused:!!section&&document.activeElement===section,
   questionSectionFocusVisible:!!section&&section.matches(':focus-visible'),
   forcedColors:matchMedia('(forced-colors: active)').matches,
  },
 };
},questionId);

const maskActive=state=>[state.mask.standard,state.mask.webkit].some(value=>Boolean(value)&&value!=='none');
const record=async(label,pane,questionId=null)=>{
 const state=await inspect(pane,questionId);
 boundedPush(report.focus,{label,...state.focus});
 boundedPush(report.geometry,{label,...state.geometry,runtime:state.runtime,overflow:state.overflow,mask:state.mask});
 return state;
};
const settled=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
const successShot=async name=>{
 const count=report.screenshots.filter(row=>row.kind==='success').length;
 assert.ok(count<report.limits.successScreenshots,'Success screenshot cap');
 const file=`${name}.png`,path=join(artifacts,file);
 const bytes=await page.screenshot({path,timeout:8000,animations:'disabled'});
 report.screenshots.push({kind:'success',file,sha256:hash(bytes),bytes:bytes.length});
};
const failureShot=async()=>{
 if(failureScreenshotAttempted||!page||page.isClosed())return;
 failureScreenshotAttempted=true;
 const path=join(artifacts,'failure.png');
 const result=await boundaryRead(()=>page.screenshot({path,timeout:2000,animations:'disabled'}));
 report.failureScreenshotObservation={status:result.status,budgetMs:BOUNDARY_OBSERVATION_MS};
 if(result.status==='observed'){
  const bytes=result.value;
  report.screenshots.push({kind:'failure',file:'failure.png',sha256:hash(bytes),bytes:bytes.length});
 }
};

try{
 const fixturePath=join(root,'tests/fixtures/questions_ui_server.py');
 fixture=spawn(python,[fixturePath],{cwd:root,stdio:['ignore','pipe','pipe']});
 stopFixture=ownFixture(fixture,{label:'Question transcript fade fixture'});
 report.fixture.started=true;
 fixture.on('close',(code,signal)=>{report.fixture.close={observed:true,code,signal}});
 fixture.stderr.on('data',chunk=>{
  const text=chunk.toString('utf8');
  stderrCharacters+=text.length;
  stderr=(stderr+text).slice(-report.limits.stderrTailCharacters);
 });
 const ready=await new Promise((resolve,reject)=>{
  let pending='',settledStartup=false;
  const cleanup=()=>{
   clearTimeout(timer);fixture.stdout.off('data',onData);fixture.off('error',onError);fixture.off('exit',onExit);
  };
  const finish=(error,value)=>{
   if(settledStartup)return;
   settledStartup=true;cleanup();error?reject(error):resolve(value);
  };
  const onError=error=>finish(error);
  const onExit=(code,signal)=>finish(Error(`Question fixture exited before readiness (${code??signal})`));
  const onData=chunk=>{
   pending=(pending+chunk.toString('utf8')).slice(-24000);
   const lines=pending.split('\n');pending=lines.pop()||'';
   for(const line of lines){
    let row;try{row=JSON.parse(line)}catch{continue}
    if(row.url){finish(null,row);return}
   }
  };
  const timer=setTimeout(()=>finish(Error('Question fixture startup timeout')),20000);
  fixture.stdout.on('data',onData);fixture.once('error',onError);fixture.once('exit',onExit);
 });
 report.fixture.ready=true;
 const origin=new URL(ready.url).origin;
 browser=await chromium.launch({
  headless:true,
  ignoreDefaultArgs:['--hide-scrollbars'],
  args:[
   '--disable-features=OverlayScrollbar,FluentOverlayScrollbar',
   ...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]),
   ...JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]'),
  ],
  ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{}),
 });
 report.browser.started=true;
 report.observedRuntime.chromium=browser.version();
 const context=await browser.newContext({
  viewport:{width:1280,height:900},deviceScaleFactor:1,colorScheme:'light',reducedMotion:'no-preference',
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},
 });
 page=await context.newPage();
 page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(20000);
 await page.route('**/*',route=>{
  const target=new URL(route.request().url());
  if(['http:','https:'].includes(target.protocol)&&target.origin!==origin){
   boundedPush(report.externalRequests,target.origin+target.pathname);
   return route.abort('blockedbyclient');
  }
  return route.continue();
 });
 page.on('pageerror',error=>boundedPush(report.browserErrors,String(error?.stack||error).slice(0,1500)));
 page.on('response',response=>{
  if(response.status()>=400)boundedPush(report.httpErrors,{path:new URL(response.url()).pathname,status:response.status()});
 });
 page.on('request',request=>{
  if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
  try{
   const raw=request.postDataJSON(),body=raw?.action==='shell.command'?raw.args:raw;
   const action=body?.action;
   if(action==='question.answer')boundedPush(report.question.answerRequests,{action,requestId:raw.id??null});
   boundedPush(report.actions,{action:action||'unknown',requestId:raw.id??body?.id??null},report.limits.actionRecords);
  }catch(error){boundedPush(report.instrumentationErrors,`Action observer: ${String(error).slice(0,1000)}`)}
 });
 await page.goto(ready.url);
 await showFixtureConversation(page);
 await expect(page.locator('#amp-one')).toBeVisible();
 pane=page.locator('[data-part="messages"]');
 await expect(pane).toBeVisible();
 const support=await page.evaluate(()=>({
  hasFocusVisible:CSS.supports('selector(:focus-visible)'),
  hasHasFocusVisible:CSS.supports('selector(:has(:focus-visible))'),
 }));
 report.capabilities=support;
 assert.equal(support.hasFocusVisible,true,'Browser supports keyboard-visible focus selectors');
 assert.equal(support.hasHasFocusVisible,true,'Browser supports the shared :has() focus guard');

 const buildResponse=await page.request.get(ready.url+'/build.json');
 assert.ok(buildResponse.ok(),'Production fixture serves build identity');
 const servedBuildBytes=await buildResponse.body();
 report.build.servedSha256=hash(servedBuildBytes);
 report.build.servedMetadata=JSON.parse(servedBuildBytes);
 assert.equal(report.build.servedSha256,report.build.diskSha256,'Fixture serves the exact admitted static build marker');
 const stylesheetUrls=await page.evaluate(()=>performance.getEntriesByType('resource')
  .filter(entry=>/\.css(?:[?#]|$)/i.test(entry.name))
  .map(entry=>entry.name).slice(0,13));
 report.build.stylesheetAssetsDropped=Math.max(0,stylesheetUrls.length-12);
 for(const rawUrl of stylesheetUrls.slice(0,12)){
  const asset=new URL(rawUrl,ready.url);
  if(asset.origin!==origin)continue;
  const response=await page.request.get(asset.href);
  assert.ok(response.ok(),`Production stylesheet responds: ${asset.pathname}`);
  const bytes=await response.body();
  report.build.stylesheetAssets.push({path:asset.pathname,sha256:hash(bytes),bytes:bytes.length});
 }

 const fixtureCheck=async()=>await (await page.request.get(ready.url+'/fixture/check')).json();
 const readState=(questionId=null)=>inspect(pane,questionId);
 const waitForIdleLayout=async label=>{
  let previous=null;
  // sent[] is recorded before the fixture emits idle. Require the browser's
  // terminal state AND unmounted activity, then compare frame-separated layout
  // samples (including composer, question notice and Latest) within the existing
  // expect.poll budget. Do not substitute a delay or relax the insertion checks.
  await expect.poll(async()=>{
   await settled();
   const state=await readState();
   const signature=JSON.stringify({runtime:state.runtime,geometry:state.geometry,overflow:state.overflow});
   const stable=signature===previous;previous=signature;
   return state.runtime.sessionId===ready.sessionId&&state.runtime.selectedSessionId===ready.sessionId
    &&state.runtime.status==='idle'&&state.geometry.surroundings.activityCount===0&&stable;
  },{message:`${label}: idle runtime, absent activity and settled surrounding layout`}).toBe(true);
 };
 const wheelAtPane=async delta=>{
  const box=await pane.boundingBox();assert.ok(box,'Transcript pane has a rendered box');
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await page.mouse.wheel(0,delta);await settled();
 };
 const wheelToEdge=async edge=>{
  for(let index=0;index<24;index++){
   const current=await readState();
   if(edge==='top'&&current.geometry.scrollTop<=0.5)return current;
   if(edge==='bottom'&&current.geometry.remaining<=1)return current;
   await wheelAtPane(edge==='top'?-1100:1100);
  }
  return readState();
 };
 const assertPaneFrameUnchanged=(before,after,label)=>{
  for(const key of ['x','y','width','height'])
   assert.ok(Math.abs(before.geometry.pane[key]-after.geometry.pane[key])<=1,`${label}: pane ${key} changed`);
 };
 const assertMasked=(state,label)=>assert.ok(maskActive(state),`${label}: overflow mask is active`);

 await page.setViewportSize({width:1280,height:2800});await settled();
 const short=await record('short-content',pane);
 assert.ok(short.geometry.scrollHeight<=short.geometry.clientHeight+1,'Short transcript content fits the pane');
 assert.equal(short.overflow.above,'false');assert.equal(short.overflow.below,'false');
 assert.equal(maskActive(short),false,'Short content has no unnecessary mask');

 await page.setViewportSize({width:1280,height:900});await settled();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const ordinaryText=Array.from({length:28},(_,index)=>
  `Synthetic ordinary chat paragraph ${index+1}. ${'This fixture message stays in the ordinary conversation. '.repeat(3)}`
 ).join('\n\n');
 await composer.fill(ordinaryText);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(pane.locator('.a-message.a-user')).toContainText('Synthetic ordinary chat paragraph 1');
 await expect.poll(async()=>(await fixtureCheck()).sent.length).toBe(1);
 await record('ordinary-chat-send-observed',pane);
 await waitForIdleLayout('Ordinary chat completed');
 const ordinaryTop=await wheelToEdge('top');
 assert.ok(ordinaryTop.geometry.remaining>1,'Ordinary chat has content below the viewport');
 assert.equal(ordinaryTop.overflow.below,'true');
 assertMasked(ordinaryTop,'Ordinary chat at the top edge');
 await record('ordinary-chat-top',pane);
 await successShot('ordinary-chat-top');

 const questionMarker='Synthetic transcript fade decision';
 const questionPrompt=Array.from({length:32},(_,index)=>
  `${questionMarker}, paragraph ${index+1}. ${'Keep this pending question readable in the shared conversation. '.repeat(3)}`
 ).join('\n\n');
 await waitForIdleLayout('Before question insertion');
 // Capture pane and reading anchor in ONE DOM evaluation immediately before
 // insertion, not on opposite sides of screenshot capture or layout changes.
 const ordinary=await record('before-question-insertion',pane);
 const anchorBefore=ordinary.geometry.anchor;
 assert.ok(anchorBefore,'Question insertion has an existing message anchor');
 const created=await page.evaluate(async({sessionId,prompt})=>{
  const receipt=await window.amplifier.dispatch('question.create',{
   sessionId,prompt,required:false,dependency:'Inspect this synthetic question layout',
  });
  if(!receipt?.accepted)throw Error(`Synthetic optional question was refused: ${JSON.stringify(receipt?.error||receipt)}`);
  return receipt.result;
 },{sessionId:ready.sessionId,prompt:questionPrompt});
 assert.ok(created?.id,'The fixture returns the new question identity');
 report.question.id=created.id;
 const question=pane.locator(`[data-question-id="${created.id}"]`);
 const questionSection=pane.locator('[data-part="questions"]');
 await expect(question).toBeVisible();
 await expect(question.locator('.a-question-prompt')).toContainText(`${questionMarker}, paragraph 32`);
 await settled();
 const afterQuestion=await record('ordinary-chat-plus-question-same-pane',pane,created.id);
 const anchorAfter=afterQuestion.geometry.anchor;
 assert.ok(anchorAfter,'Question insertion retains a rendered message anchor');
 assertPaneFrameUnchanged(ordinary,afterQuestion,'Question insertion');
 assert.equal(anchorAfter.id,anchorBefore.id,'Question insertion retains the existing message anchor');
 assert.ok(Math.abs(anchorAfter.offset-anchorBefore.offset)<=1,'Question insertion does not move the existing reading anchor');
 assert.ok(Math.abs(anchorAfter.scrollTop-anchorBefore.scrollTop)<=1,'Question insertion does not change the reader scroll position');
 assert.ok(afterQuestion.geometry.remaining>1,'Question card contributes real overflow to the same pane');

 const review=page.getByRole('button',{name:'Review questions',exact:true});
 await review.scrollIntoViewIfNeeded();await settled();
 const beforePointer=await record('before-pointer-review',pane,created.id);
 assert.equal(beforePointer.focus.questionSectionFocused,false,'Pointer journey begins without manually focusing the question section');
 report.question.pointerActivation='Playwright locator.click on the production Review questions button';
 await review.click();
 await expect(questionSection).toBeFocused();
 await settled();
 const afterPointer=await record('after-pointer-review',pane,created.id);
 assert.equal(afterPointer.focus.questionSectionFocused,true,'Review questions focuses the real question section');
 assert.equal(afterPointer.focus.paneFocusWithin,true,'Question section remains inside the transcript scroller');
 assert.ok(afterPointer.geometry.remaining>1,'Overflow remains after pointer Review questions');
 assert.equal(afterPointer.overflow.below,'true','Question content continues below the viewport after pointer review');
 assert.equal(afterPointer.focus.questionSectionFocusVisible,false,
  'This regression must exercise passive pointer-triggered focus; visible-focus-only coverage is insufficient');
 assertMasked(afterPointer,'Non-visible pointer-triggered section focus');

 const scrollQuestionAcrossBottom=async label=>{
  let current=await readState(created.id);
  for(let index=0;index<12;index++){
   const paneBottom=current.geometry.pane.bottom,questionBounds=current.geometry.question;
   if(questionBounds&&questionBounds.top<paneBottom-1&&questionBounds.bottom>paneBottom+1&&current.geometry.remaining>1)break;
   await wheelAtPane(260);current=await readState(created.id);
  }
  current=await record(label,pane,created.id);
  assert.ok(current.geometry.remaining>1,`${label}: more than one pixel remains below the viewport`);
  assert.equal(current.overflow.below,'true',`${label}: bottom overflow is reported`);
  assert.ok(current.geometry.question.top<current.geometry.pane.bottom-1,`${label}: question enters the viewport`);
  assert.ok(current.geometry.question.bottom>current.geometry.pane.bottom+1,`${label}: question crosses the pane bottom`);
  return current;
 };
 const pointerCrossing=await scrollQuestionAcrossBottom('pointer-review-question-crosses-bottom');
 assert.equal(pointerCrossing.focus.questionSectionFocusVisible,false,
  'Passive question-section focus remains distinct from the keyboard journey');
 assertMasked(pointerCrossing,'Pointer review at the question bottom edge');
 await successShot('question-review-pointer');

 await review.focus();
 await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 await expect(review).toBeFocused();
 assert.equal(await review.evaluate(element=>element.matches(':focus-visible')),true,
  'Keyboard traversal gives the real Review questions button visible focus');
 await page.keyboard.press('Enter');
 await expect(questionSection).toBeFocused();await settled();
 const keyboardStart=await record('after-keyboard-review',pane,created.id);
 assert.equal(keyboardStart.focus.questionSectionFocused,true);
 assert.equal(keyboardStart.focus.questionSectionFocusVisible,true,
  'Keyboard activation transfers visible focus to the question section');
 assert.ok(keyboardStart.geometry.remaining>1);
 assert.equal(maskActive(keyboardStart),false,'Keyboard-visible question focus suppresses the mask');
 const keyboardCrossing=await scrollQuestionAcrossBottom('keyboard-review-question-crosses-bottom');
 assert.equal(keyboardCrossing.focus.questionSectionFocusVisible,true);
 assert.equal(maskActive(keyboardCrossing),false,'Keyboard-visible focus stays fully unmasked during scrolling');
 await successShot('question-review-keyboard');

 const editor=question.locator('textarea');
 await editor.click();
 await editor.fill('Synthetic local draft. Do not submit this question.');
 await expect(editor).toHaveValue('Synthetic local draft. Do not submit this question.');
 const editing=await record('question-textarea-editing',pane,created.id);
 assert.equal(editing.focus.activeTextarea,true);
 assert.equal(editing.focus.activeInsidePane,true);
 assert.equal(maskActive(editing),false,'Active question editing remains fully readable');
 await successShot('question-editor-focused');
 const scrollBeforeBlur=editing.geometry.scrollTop;
 await composer.click();
 await expect(composer).toBeFocused();await settled();
 const blurred=await record('question-editor-blurred-to-composer',pane,created.id);
 assert.ok(Math.abs(blurred.geometry.scrollTop-scrollBeforeBlur)<=1,'Blurring to the composer does not move the transcript');
 assert.equal(blurred.focus.activeInsidePane,false);
 assertMasked(blurred,'Focus outside the transcript restores the overflow fade');

 await page.emulateMedia({forcedColors:'active'});
 const forcedColors=await record('forced-colors',pane,created.id);
 assert.equal(forcedColors.focus.forcedColors,true);
 assert.equal(maskActive(forcedColors),false,'Forced colors removes the transcript mask');
 await page.emulateMedia({forcedColors:'none'});
 await expect.poll(async()=>maskActive(await readState())).toBe(true);

 const bottom=await wheelToEdge('bottom');
 assert.ok(bottom.geometry.remaining<=1,'Real wheel scrolling reaches the transcript end');
 assert.equal(bottom.overflow.below,'false','The bottom fade is absent at the end');
 assert.ok(bottom.overflow.above==='true'||bottom.overflow.above==='false');
 if(bottom.overflow.above==='true')assertMasked(bottom,'Top overflow remains visible at the transcript end');
 else assert.equal(maskActive(bottom),false,'A transcript with no remaining overflow has no mask');
 await record('transcript-bottom',pane,created.id);

 await question.getByRole('button',{name:'Skip question',exact:true}).click();
 await expect(question).toHaveCount(0,'Optional question leaves the active transcript after cancellation');
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}).click();
 await page.getByText('Questions & answers',{exact:true}).click();
 const history=page.locator('.a-question-history');
 await expect(history).toBeVisible();
 await history.locator(':scope > summary').click();
 const historyItem=page.locator('.a-question-history .a-question-item').filter({hasText:questionMarker});
 await expect(historyItem).toHaveCount(1);
 const historySummary=historyItem.locator(':scope > summary');
 await expect(historySummary).toBeVisible();
 const historyMaskBefore=await inspect(pane,created.id);
 await historySummary.focus();
 await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 await expect(historySummary).toBeFocused();
 const historyFocus=await record('question-history-summary-keyboard',pane,created.id);
 const outline=await historySummary.evaluate(element=>{
  const style=getComputedStyle(element);
  return {outlineStyle:style.outlineStyle,outlineWidth:parseFloat(style.outlineWidth),focusVisible:element.matches(':focus-visible')};
 });
 report.focus.at(-1).outline=outline;
 assert.equal(outline.focusVisible,true,'Closed-question summary retains keyboard-visible focus');
 assert.notEqual(outline.outlineStyle,'none','Closed-question summary has a visible focus outline');
 assert.ok(outline.outlineWidth>=2,'Closed-question summary focus outline remains at least 2px');
 assert.equal(historyFocus.focus.activeInsidePane,false,'History disclosure focus is outside the transcript scroller');
 assert.equal(historyFocus.mask.standard,historyMaskBefore.mask.standard,'History-summary focus does not alter transcript masking');

 // Use the fixture's real saved HTML artifact and pinned question review, not
 // an injected frame. Its sandbox has a single interactive button.
 await page.locator('[data-part="dialog"]').getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(page.locator('[data-part="dialog"]')).toHaveCount(0);
 const reviewResponse=await page.request.post(ready.url+'/fixture/reviews');
 assert.equal(reviewResponse.status(),200,await reviewResponse.text());
 const reviewFixture=await reviewResponse.json();
 const reviewQuestion=pane.locator(`[data-question-id="${reviewFixture.question.id}"]`);
 const htmlReview=reviewQuestion.locator('.a-compact-artifact').nth(1);
 const iframe=htmlReview.locator('iframe'),inner=htmlReview.frameLocator('iframe');
 const reviewBoundary=await observeReviewBoundary(page,{attempt:'second-html-review',
  sessionId:ready.sessionId,questionId:reviewFixture.question.id,
  questionRevision:reviewFixture.question.revision,questionContentRevision:reviewFixture.question.contentRevision,
  resourceId:reviewFixture.html.id,version:reviewFixture.question.artifacts?.find(row=>row.id===reviewFixture.html.id)?.version??null},reviewFixture.html);
 let reviewFailed=true;
 try{
  await htmlReview.getByRole('button',{name:/^Preview here:/}).click();
  await reviewBoundary.snapshot('click-resolved');
  reviewBoundary.waiting();
  await expect(iframe).toHaveAttribute('sandbox','allow-scripts');
  await expect(inner.getByRole('heading',{name:'HTML mockup v1',exact:true})).toBeVisible();
  reviewFailed=false;
 }finally{await reviewBoundary.finish(reviewFailed)}
 const innerControl=inner.getByRole('button',{name:'Try mockup',exact:true});
 // Merely mounting a review must not disable the overflow cue.
 await composer.evaluate(element=>element.focus({preventScroll:true}));await expect(composer).toBeFocused();
 await expect(pane).not.toHaveAttribute('data-transcript-iframe-focus','true');
 for(let index=0;index<4&&(await readState()).geometry.remaining<=1;index++)await wheelAtPane(-400);
 const iframeInactive=await record('question-review-iframe-inactive',pane,reviewFixture.question.id);
 assert.equal(iframeInactive.focus.activeIframe,false);
 assert.ok(iframeInactive.geometry.remaining>1,'Inactive iframe negative control has actual overflow');
 assertMasked(iframeInactive,'An inactive mounted iframe does not suppress the shared overflow mask');
 await innerControl.click();
 await expect(inner.locator('#value')).toHaveText('1');
 assert.equal(await iframe.evaluate(element=>element.ownerDocument.activeElement===element),true,
  'The host active element is this real sandboxed question review');
 await expect(pane).toHaveAttribute('data-transcript-iframe-focus','true');
 const iframePointer=await record('question-review-iframe-pointer',pane,reviewFixture.question.id);
 assert.equal(iframePointer.focus.activeIframe,true,'Inner pointer focus is represented by the real host iframe');
 assert.equal(iframePointer.focus.activeInsidePane,true);
 assert.equal(iframePointer.focus.transcriptIframeFocus,'true','Host active iframe identity protects the review independently of CSS :focus');
 assert.ok(iframePointer.geometry.remaining>1,'Iframe pointer focus check exercises actual bottom overflow');
 assert.equal(maskActive(iframePointer),false,'Interactive iframe pointer focus suppresses transcript masking');
 await successShot('question-review-iframe-pointer');
 // Traverse out and back with the keyboard; host focus-visible alone cannot
 // describe focus on a control in an opaque-origin sandbox.
 await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 await expect(innerControl).toBeFocused();
 assert.equal(await iframe.evaluate(element=>element.ownerDocument.activeElement===element),true,
  'Inner keyboard focus retains this real review as the host active element');
 await expect(pane).toHaveAttribute('data-transcript-iframe-focus','true');
 const innerFocus=await innerControl.evaluate(element=>({
  active:document.activeElement===element,focusVisible:element.matches(':focus-visible'),
  outlineStyle:getComputedStyle(element).outlineStyle,
  outlineWidth:parseFloat(getComputedStyle(element).outlineWidth),
 }));
 assert.equal(innerFocus.active,true);assert.equal(innerFocus.focusVisible,true);
 assert.notEqual(innerFocus.outlineStyle,'none');assert.ok(innerFocus.outlineWidth>0);
 // Retain actual overflow while the inner control remains focused, so an
 // unmasked result cannot pass merely because the transcript reached its end.
 for(let index=0;index<4&&(await readState()).geometry.remaining<=1;index++)await wheelAtPane(-400);
 const iframeKeyboard=await record('question-review-iframe-keyboard',pane,reviewFixture.question.id);
 report.focus.at(-1).inner=innerFocus;
 assert.equal(iframeKeyboard.focus.activeIframe,true);
 assert.equal(iframeKeyboard.focus.activeInsidePane,true);
 assert.equal(iframeKeyboard.focus.transcriptIframeFocus,'true');
 assert.ok(iframeKeyboard.geometry.remaining>1,'Iframe focus check exercises actual bottom overflow');
 assert.equal(iframeKeyboard.overflow.below,'true');
 assert.equal(maskActive(iframeKeyboard),false,'Inner keyboard controls remain fully unmasked');
 const iframeScroll=iframeKeyboard.geometry.scrollTop;
 await composer.evaluate(element=>element.focus({preventScroll:true}));
 await expect(composer).toBeFocused();await expect(pane).not.toHaveAttribute('data-transcript-iframe-focus','true');await settled();
 const iframeBlurred=await record('question-review-iframe-blurred',pane,reviewFixture.question.id);
 assert.equal(iframeBlurred.focus.activeInsidePane,false);
 assert.notEqual(iframeBlurred.focus.transcriptIframeFocus,'true');
 assert.ok(Math.abs(iframeBlurred.geometry.scrollTop-iframeScroll)<=1,'Leaving the iframe preserves the transcript position');
 assert.equal(iframeBlurred.overflow.below,'true');
 assertMasked(iframeBlurred,'Leaving the real iframe restores the shared overflow mask');
 await successShot('question-review-iframe-blurred');
 await expect(inner.locator('#value')).toHaveText('1','The iframe retains its local state after focus traversal');

 const final=await fixtureCheck();
 report.question.answerSubmitted=report.question.answerRequests.length>0;
 assert.deepEqual(report.question.answerRequests,[],'The regression never submits a question answer');
 assert.equal(final.sent.length,1,'Only the synthetic ordinary chat message reaches the fixture runtime');
 assert.equal(final.sent[0].text,ordinaryText);
 assert.equal(final.retries.length,0,'The synthetic fixture performs no retries');
 assert.equal(final.networkAttempts,0,'The real HTML review sandbox does not reach its network probe');
 assert.equal(final.questions.find(row=>row.id===created.id)?.status,'cancelled',
  'The optional synthetic question is cancelled without an answer');
 assert.deepEqual(report.browserErrors,[],'Production page has no uncaught errors');
 assert.deepEqual(report.httpErrors,[],'Production page has no failed local responses');
 assert.deepEqual(report.externalRequests,[],'Fixture browser contacts no external origin');
 assert.deepEqual(report.instrumentationErrors,[],'Bounded evidence observers remain healthy');
 assert.equal(report.evidenceDropped,0,'No bounded evidence was dropped');
 assert.equal(report.screenshots.filter(row=>row.kind==='success').length,6,'All six visual states were retained');
 assert.deepEqual(sourceHashes(),report.sourceSha256,'Authored source remains unchanged during the browser journey');
 const servedBuild=await page.request.get(ready.url+'/build.json');
 assert.equal(hash(await servedBuild.body()),report.build.servedSha256,'Served build identity remains stable');
}catch(error){
 failure=error;
 report.failure={name:error?.name||'Error',message:String(error?.message||error),stack:String(error?.stack||error).slice(0,8000)};
 if(pane&&page&&!page.isClosed()){
  // Commit only an on-time read. The old record() continuation could append
  // late facts after cleanup; this optional context never owns the failure.
  const context=await boundaryRead(async()=>await pane.count()===1?inspect(pane,report.question.id):null);
  report.failureContextObservation={status:context.status,budgetMs:BOUNDARY_OBSERVATION_MS};
  if(context.status==='observed'&&context.value){
   const state=context.value,label='failure-context';
   boundedPush(report.focus,{label,...state.focus});
   boundedPush(report.geometry,{label,...state.geometry,runtime:state.runtime,overflow:state.overflow,mask:state.mask});
  }
 }
 await failureShot();
}finally{
 report.question.answerSubmitted=report.question.answerRequests.length>0;
 report.fixture.stderrTail=stderr;
 report.fixture.stderrCharactersObserved=stderrCharacters;
 report.fixture.stderrTruncated=stderrCharacters>report.limits.stderrTailCharacters;
 if(stopFixture){
  try{
   await finishFixture({
    label:'Question transcript fade fixture',
    originalError:failure,
    close:[['Question transcript fade browser',async()=>{
     if(browser){await browser.close();report.browser.closed=true}
    }]],
    stop:async()=>{
     const result=await stopFixture();
     report.fixture.shutdown={code:result.code,signal:result.signal,closeObserved:!!report.fixture.close?.observed};
    },
   });
  }catch(error){
   report.cleanupError=String(error?.stack||error).slice(0,4000);
   failure??=error;
  }
 }
 if(report.fixture.started&&!report.fixture.close?.observed){
  report.cleanupError??='Fixture close event was not observed.';
  failure??=Error(report.cleanupError);
 }
 if(report.browser.started&&!report.browser.closed){
  report.cleanupError??='Browser close did not resolve.';
  failure??=Error(report.cleanupError);
 }
 report.status=failure?'failed':'passed';
 if(failure&&!report.failure)report.failure={name:failure.name||'Error',message:String(failure.message||failure),stack:String(failure.stack||failure).slice(0,8000)};
 writeReport();
 console.log(JSON.stringify({
  status:report.status,artifacts,report:join(artifacts,'report.json'),
  checks:{questionPointerFocus:report.focus.some(row=>row.label==='after-pointer-review'),
   keyboardFocus:report.focus.some(row=>row.label==='after-keyboard-review'),
   editorFocus:report.focus.some(row=>row.label==='question-textarea-editing'),
   iframeFocus:report.focus.some(row=>row.label==='question-review-iframe-keyboard'),
   historySummary:report.focus.some(row=>row.label==='question-history-summary-keyboard')},
  screenshots:report.screenshots.map(row=>row.file),
  fixtureClose:report.fixture.close,fixtureShutdown:report.fixture.shutdown,
 }));
}
if(failure)throw failure;