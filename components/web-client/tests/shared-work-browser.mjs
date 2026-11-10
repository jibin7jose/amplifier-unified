// Real-host Shared Work journey. Run in the owned isolated guest after build.
// Preserve baseline failures separately; results identify the exact candidate.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile,mkdtemp,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect as baseExpect,request as httpRequest} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {assertPassiveActions,assertWorkWaitTermination,bounded,createWorkObserver,observeCompletion,passiveWorkReads,requireCompletion,unknownCleanupValue,workWaitProbeIds} from './shared-work-test-helpers.mjs';

const actionTimeoutMs=7000,navigationTimeoutMs=15000;
const expect=baseExpect.configure({timeout:actionTimeoutMs});
const observer=createWorkObserver();
const observePage=(page,client)=>{
 page.setDefaultTimeout(actionTimeoutMs);
 page.setDefaultNavigationTimeout(navigationTimeoutMs);
 page.on('pageerror',error=>observer.pageError(client,error));
 page.on('request',request=>observer.request(client,request));
};
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-shared-work';
// Capture the unchanged source once for the approved-design comparison. This
// mode is evidence collection only, never the corrected fidelity verdict.
const fidelityBaseline=process.env.AMPLIFIER_TEST_FIDELITY_BASELINE==='1';
await mkdir(output,{recursive:true});
const installed=process.env.AMPLIFIER_TEST_INSTALLED==='1';
const coldTaskRequested=process.env.AMPLIFIER_TEST_COLD_TASK==='1';
const fixturePath=process.env.AMPLIFIER_TEST_FIXTURE||root+'tests/fixtures/empty_host_ui_server.py';
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python';
if(installed)assert.ok(process.env.AMPLIFIER_TEST_PYTHON&&process.env.AMPLIFIER_TEST_FIXTURE&&!fixturePath.startsWith(root),'Installed qualification requires an external fixture and explicit interpreter');
const home=await mkdtemp(path.join(output,'cold-home-'));
const fixtureEnv={...process.env,AMPLIFIER_TEST_SHARED_WORK_HOME:home,
 AMPLIFIER_SHELL_NODE_MODULES:process.env.AMPLIFIER_SHELL_NODE_MODULES||root+'frontend/node_modules'};
const rendererToolchain={modulesPath:fixtureEnv.AMPLIFIER_SHELL_NODE_MODULES,
 candidateLockSha256:createHash('sha256').update(await readFile(root+'frontend/package-lock.json')).digest('hex'),
 installedLockSha256:createHash('sha256').update(await readFile(path.join(fixtureEnv.AMPLIFIER_SHELL_NODE_MODULES,'.package-lock.json'))).digest('hex')};
if(installed){delete fixtureEnv.PYTHONPATH;delete fixtureEnv.PYTHONHOME}
const fixtureArgs=[...(installed?['-I','-B']:[]),fixturePath,'--chat-controls','--shared-work'];
const fixtureOptions={stdio:['ignore','pipe','inherit'],env:fixtureEnv,cwd:path.dirname(fixturePath)};
// Seed in a different process, close the real service and reopen its persisted
// state. The second process must never replay capture or initialization actions.
const seed=spawn(python,[...fixtureArgs,'--shared-work-seed-only'],fixtureOptions);
let seedOutput='';seed.stdout.on('data',chunk=>{if(seedOutput.length<65536)seedOutput+=String(chunk)});
const seedClosed=new Promise((resolve,reject)=>{seed.once('error',reject);seed.once('close',(code,signal)=>resolve({code,signal,pid:seed.pid}))});
let seedReceipt;
try{seedReceipt=await bounded(seedClosed,30000,'Cold seed process')}
catch(error){if(seed.exitCode===null)seed.kill('SIGKILL');await bounded(seedClosed,2000,'Cold seed reap');throw error}
assert.equal(seedReceipt.code,0);assert.equal(seedReceipt.signal,null);assert.match(seedOutput,/"sharedWorkSeed"/);
await writeFile(path.join(output,'cold-seed.json'),JSON.stringify({receipt:seedReceipt,output:seedOutput})+'\n');
const fixture=spawn(python,fixtureArgs,fixtureOptions);
// Passive observations retire at the real host fence, not at their 30s deadline.
// Keep the ordinary strict 10s shutdown / 2s forced-reap failure semantics.
const stop=ownFixture(fixture,{label:'Shared Work fixture'});
const headers={Authorization:'Bearer fixture-browser-control-token'};
const launch={headless:true,timeout:15000,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]};
let browser,peerBrowser,touchBrowser,originalError,result,page,fixtureUrl,probeClient,shutdownCompletion;
const termination={fixturePid:fixture.pid,browserDisconnectedAt:null,peerBrowserDisconnectedAt:null,touchBrowserDisconnectedAt:null,closeRequestedAt:null,fixtureClose:null,workWaits:[],shutdownSignals:[],shutdownFences:[],wireWaits:[],cleanupComplete:null,observationErrors:[]};
const observationError=message=>{if(termination.observationErrors.length<16)termination.observationErrors.push(message)};
const retain=(target,value,cap)=>{if(target.length<cap)target.push(value);else observationError('Fixture observation capacity exceeded')};
let observationBuffer='';
fixture.stdout.on('data',chunk=>{
 observationBuffer+=String(chunk);
 let end;
 while((end=observationBuffer.indexOf('\n'))>=0){
  const line=observationBuffer.slice(0,end);observationBuffer=observationBuffer.slice(end+1);
  if(!line.trim())continue;
  try{
   assert.ok(line.length<=65536,'Fixture observation line exceeded 64KiB');
   const row=JSON.parse(line);
   if(row.workWait)retain(termination.workWaits,row.workWait,800);
   if(row.sharedWorkSignal)retain(termination.shutdownSignals,row.sharedWorkSignal,4);
   if(row.sharedWorkFence)retain(termination.shutdownFences,row.sharedWorkFence,4);
   if(row.sharedWorkCleanup){
    if(termination.cleanupComplete)observationError('Duplicate cleanup observation');
    termination.cleanupComplete=row.sharedWorkCleanup;
   }
  }catch{observationError('Malformed fixture observation')}
 }
 if(observationBuffer.length>65536){observationError('Fixture observation exceeded 64KiB');observationBuffer=''}
});
fixture.stdout.once('end',()=>{if(observationBuffer.trim())observationError('Incomplete fixture observation at EOF')});
fixture.once('close',(code,signal)=>{termination.fixtureClose={code,signal,at:Date.now()}});
try {
 const url=await new Promise((resolve,reject)=>{
  let body='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),30000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{body+=chunk;for(const line of body.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
 });
 fixtureUrl=url;
 await mkdir(output,{recursive:true});
 browser=await chromium.launch(launch);
 browser.on('disconnected',()=>{termination.browserDisconnectedAt=Date.now()});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 observePage(page,'primary');
 const calls=observer.calls;
 const manifest=await (await page.request.get(url+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
 assert.equal(manifest.coldReopen,true);assert.deepEqual(manifest.coldReadCalls,[]);
 assert.notEqual(seed.pid,fixture.pid);
 let installedIdentity=null;
 if(installed){
  installedIdentity=(await (await page.request.get(url+'/fixture',{timeout:actionTimeoutMs})).json()).installedIdentity;
  assert.equal(installedIdentity?.verified,true);assert.ok(installedIdentity.fileCount>0);
  for(const name of ['index.html','build.json',...Object.keys(installedIdentity.static).filter(name=>/^assets\/app-.*\.(js|css)$/.test(name))]){
   const response=await page.request.get(url+'/'+(name==='index.html'?'':name),{timeout:actionTimeoutMs});
   assert.equal(response.status(),200);
   assert.equal(createHash('sha256').update(await response.body()).digest('hex'),installedIdentity.static[name],name+' served from installed wheel');
  }
 }
 assert.equal(manifest.target.delivery,'reported_published');
 assert.equal(manifest.target.latestOutcome,'unknown');
 assert.equal(manifest.target.verification,'not_verified');
 assert.equal(manifest.target.humanDecision,'not_recorded');
 assert.equal(manifest.target.deliveryReport.hostObserved,false);
 assert.equal(manifest.target.deliveryReport.independentlyVerified,false);
 assert.equal(manifest.coverage.historicalCoverage,'incomplete');
 assert.ok(manifest.coverage.unreviewedInputs>=1);
 assert.deepEqual(manifest.runtimeCallbackCounts,{start:0,send:0,prewarm:0});
 assert.equal(manifest.pendingWarmups,0);
 const action=(name,args={})=>bounded(page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]),actionTimeoutMs,'Primary '+name);
 const state=()=>bounded(page.evaluate(()=>window.amplifier.getState()),actionTimeoutMs,'Primary state read');
 await page.goto(url);
 await page.waitForFunction(()=>window.amplifier);
 // Current Home is a real navigation surface, not an already-open composer.
 // Enter only the exact synthetic owner through its ordinary guarded control.
 const ownerCard=page.locator(`article[data-session-id="${manifest.ownerSessionId}"]`);
 await expect(ownerCard).toBeVisible();
 await expect(ownerCard.getByRole('heading',{name:'Synthetic Work owner',exact:true})).toBeVisible();
 const entryBefore=calls.length;
 await ownerCard.getByRole('button',{name:/Continue chat/}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
 await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const entrySelects=calls.slice(entryBefore).filter(row=>row.action==='session.select');
 assert.equal(entrySelects.length,1);
 assert.equal(entrySelects[0].args.id,manifest.ownerSessionId);
 const entryObservation=await (await page.request.get(url+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
 assert.deepEqual(entryObservation.forbiddenRuntimeCalls,[]);
 assert.deepEqual(entryObservation.runtimeCallbackCounts,{start:0,send:0,prewarm:0});
 assert.equal(entryObservation.pendingWarmups,0);
 observer.assertHealthy();
 // The selected draft belongs to a different chat; reading Work must not touch it.
 const created=await action('session.create',{workspace:manifest.workspace.path,title:'Draft owner'});
 const draftOwner=created.result.sessionId;
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Private draft remains unchanged.');
 await action('attachment.add',{sessionId:draftOwner,name:'private-draft.txt',base64:'cHJpdmF0ZQ=='});
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:manifest.workspace.id,workWorkspaceTab:'chats',navExpanded:false}});
 const before=calls.length;
 await page.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Work',exact:true}).click();
 const work=page.getByRole('region',{name:'Shared work',exact:true});
 await expect(work).toBeVisible();
 const filter=work.getByRole('combobox',{name:'Show work'});
 await expect(filter).toHaveValue('accepted');
 await expect(work.getByText(/historical reconciliation is incomplete/i)).toBeVisible();
 const found=new Set();
 for(let pageNumber=0;pageNumber<5;pageNumber++) {
  await expect(work.locator('[data-work-id]').first()).toBeVisible();
  for(const id of await work.locator('[data-work-id]').evaluateAll(rows=>rows.map(row=>row.dataset.workId)))found.add(id);
  const next=work.getByRole('button',{name:'Next work',exact:true});
  if(await next.isDisabled())break;
  const previous=await work.locator('[data-work-id]').first().getAttribute('data-work-id');
  await next.click();
  await expect(work.locator('[data-work-id]').first()).not.toHaveAttribute('data-work-id',previous);
 }
 assert.deepEqual([...found].sort(),[...manifest.acceptedIds].sort(),'Every accepted-open record reachable through bounded pages');
 assert.ok(!found.has(manifest.proposal.id));assert.ok(!found.has(manifest.closed.id));
 await work.getByRole('searchbox',{name:'Find work'}).fill('Retain the original promise');
 const row=work.locator(`[data-work-id="${manifest.target.id}"]`);
 await expect(row).toBeVisible();
 if(!fidelityBaseline)await expect(row).toContainText('Next: '+manifest.target.nextAction);
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:1000});
  await row.scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Work list fits '+width);
  await page.screenshot({path:path.join(output,`fidelity-list-${width}.png`)});
 }
 await page.setViewportSize({width:1440,height:1000});
 await row.focus();await page.keyboard.press('Enter');
 const detail=work.getByRole('article',{name:'Work details'});
 await expect(detail).toBeVisible();
 for(const fact of ['Commitment','Execution','Verification','Human decision','Delivery','Cleanup'])await expect(detail.getByText(fact,{exact:true})).toBeVisible();
 const cleanupValue=detail.locator('dt').filter({hasText:/^Cleanup$/}).locator('xpath=following-sibling::*[1][self::dd]');
 await expect(cleanupValue).toHaveCount(1);
 await expect(cleanupValue).toHaveText(unknownCleanupValue);
 await expect(detail.getByText(/Returned.*unverified/i)).toBeVisible();
 await expect(detail.getByText('Reported published',{exact:true})).toBeVisible();
 await expect(detail.getByText('Participant reports publication; no host-observed delivery or independent verification.',{exact:true})).toBeVisible();
 await expect(detail.getByText(/Independent checks passed|Cleanup complete|Shipped/)).toHaveCount(0);
 const evidence=detail.getByRole('region',{name:'Evidence and limits',exact:true});
 if(!fidelityBaseline){
  await expect(evidence).toBeVisible();
  await expect(evidence).toContainText('Latest reported outcome: unknown');
  await expect(evidence).toContainText('Participant reports are not independent verification');
  await expect(evidence.getByText('No qualified check recorded for this criterion.',{exact:true})).toHaveCount(manifest.target.criteria?.length||2);
  await expect(evidence.locator('pre')).toHaveCount(0);
 }
 for(const width of [1440,390,320]){
  await page.setViewportSize({width,height:1000});
  await (fidelityBaseline?detail.getByText('Current links and criterion evidence',{exact:true}):evidence).scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Evidence fits '+width);
  await page.screenshot({path:path.join(output,`fidelity-evidence-${width}.png`)});
 }
 await page.setViewportSize({width:1440,height:1000});
 const source=detail.getByRole('region',{name:'Original source'});
 for(let index=0;index<20&&!((await source.innerText()).includes('ORIGINAL-END'));index++) {
  const previous=await source.locator('blockquote').textContent();
  await detail.getByRole('button',{name:'Read more source',exact:true}).click();
  // A DOM click completes before the passive read does. Wait for the exact
  // displayed page to advance before deciding whether another page exists.
  await expect.poll(()=>source.locator('blockquote').textContent()).not.toBe(previous);
 }
 await expect.poll(()=>source.locator('blockquote').textContent()).toBe(manifest.originalText);
 await expect(source.getByText(manifest.originalSha256,{exact:true})).toBeVisible();
 assert.equal(await source.locator('script').count(),0,'Source rendered as text, never executable HTML');
 await detail.getByRole('button',{name:'Read history',exact:true}).click();
 await expect(detail.getByText('Participant reports success. This is not a retained check receipt.',{exact:true})).toBeVisible();
 await detail.getByRole('button',{name:'Read correction source',exact:true}).click();
 await expect(detail.getByText(manifest.correctionText,{exact:true})).toBeVisible();
 await detail.getByRole('button',{name:'Next history',exact:true}).click();
 await expect(detail.getByText('The current cold-reopen outcome is unknown; the old success is retained.',{exact:true})).toBeVisible();
 assert.equal((await state()).selectedSessionId,draftOwner);
 const readCalls=calls.slice(before);
 assert.ok(readCalls.filter(row=>row.action.startsWith('workspace.work.')).every(row=>passiveWorkReads.has(row.action)));
 assertPassiveActions(readCalls);
 observer.assertHealthy();
 assert.ok(readCalls.filter(row=>row.action==='workspace.work.list').every(row=>row.args.limit<=50));
 const waitCalls=readCalls.filter(row=>row.action==='workspace.work.wait');
 assert.ok(waitCalls.length>0,'Visible Work subscribes without a model or worker');
 assert.ok(waitCalls.every(row=>typeof row.args.afterVersion==='string'&&row.args.afterVersion.length===64
  &&row.args.workspaceId===manifest.workspace.id&&(row.args.waitMs??30000)<=30000));
 // New incoming state cannot steal focus; Back restores the explicit item.
 for(const width of [390,320,1440]) {
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal overflow at '+width);
  if(width<720) {
   const back=detail.getByRole('button',{name:'Back to Work',exact:true});await expect(back).toBeVisible();
   const box=await back.boundingBox();assert.ok(box.height>=44&&box.width>=44,'44px Back target');
   await back.click();await expect(row).toBeFocused();
   const rowBox=await row.boundingBox();assert.ok(rowBox.height>=44&&rowBox.width>=44,'44px item target');
   await page.keyboard.press('Enter');
   await expect(detail.getByRole('heading',{name:manifest.target.title,exact:true})).toBeFocused();
  }
  await page.screenshot({path:path.join(output,`shared-work-${width}.png`)});
 }
 // Real domain-produced decisions: absence of counterevidence is zero, while
 // later counterevidence makes the same accepted decision historical.
 const chooseWork=async item=>{
  await work.getByRole('searchbox',{name:'Find work'}).fill(item.title);
  await work.locator(`[data-work-id="${item.id}"]`).click();
  await expect(detail.getByRole('heading',{name:item.title,exact:true})).toBeVisible();
 };
 const decisionValue=detail.locator('dt').filter({hasText:/^Human decision$/}).locator('xpath=following-sibling::*[1][self::dd]');
 await chooseWork(manifest.decisions.fresh);
 await expect(decisionValue).toHaveText('accepted');
 await expect(detail.getByText(/Independent checks passed|Shipped/)).toHaveCount(0);
 await chooseWork(manifest.decisions.later);
 await expect(decisionValue).toHaveText('accepted — no longer current; later counterevidence retained.');
 await expect(detail.getByText(/Independent checks passed|Shipped/)).toHaveCount(0);
 await chooseWork(manifest.target);
 // A second browser client has independent presentation choices.
 // Chromium single-process mode cannot reliably host a second isolated context.
 // A second owned process preserves independent cookies/storage/client identity
 // without weakening the actual two-client assertions.
 peerBrowser=await chromium.launch(launch);
 peerBrowser.on('disconnected',()=>{termination.peerBrowserDisconnectedAt=Date.now()});
 const peer=await peerBrowser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 observePage(peer,'peer');
 await peer.goto(url);await peer.waitForFunction(()=>window.amplifier);
 await bounded(peer.evaluate(id=>window.amplifier.dispatch('view.update',{patch:{workSurface:'workspace',workWorkspaceId:id,workWorkspaceTab:'work'}}),manifest.workspace.id),actionTimeoutMs,'Peer Work navigation');
 const peerWork=peer.getByRole('region',{name:'Shared work',exact:true});
 await peerWork.getByRole('combobox',{name:'Show work'}).selectOption('closed');
 await expect(peerWork.locator(`[data-work-id="${manifest.closed.id}"]`)).toBeVisible();
 await expect(filter).toHaveValue('accepted');
 await expect(work.getByRole('searchbox',{name:'Find work'})).toHaveValue('Retain the original promise');
 assertPassiveActions(calls.slice(before)); // Includes both clients, before explicit Open chat.
 observer.assertHealthy(); // Rejects session.warm across the whole observation.
 // Only explicit Open chat traverses the existing guarded navigation path.
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await page.getByRole('button',{name:'Back in navigation history',exact:true}).click();
 await expect.poll(async()=>(await state()).view.workWorkspaceTab).toBe('work');
 await expect(detail).toBeVisible();
 await expect(work.getByRole('searchbox',{name:'Find work'})).toHaveValue('Retain the original promise');
 await page.getByRole('button',{name:'Forward in navigation history',exact:true}).click();
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await action('session.select',{id:draftOwner});
 await expect(composer).toHaveDraft('Private draft remains unchanged.',{timeout:actionTimeoutMs});
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 // Actual dirty Canvas memory, not a fabricated accepted navigation receipt.
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'Work guard source',content:'# Original guard source'});
 const rendererManifest={id:'example.work-dirty-editor',label:'Work guard editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const rendererSource='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");const [mount]=React.useState(()=>crypto.randomUUID());return React.createElement("label",null,"Work unsaved edit",React.createElement("input",{"data-mount":mount,value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const renderer=(await action('shell.packages.stage',{manifest:rendererManifest,source:rendererSource})).result.digest;
 const rendererValidation=(await action('shell.packages.validate',{digest:renderer})).result;
 await writeFile(path.join(output,'renderer-validation.json'),JSON.stringify(rendererValidation,null,2)+'\n');
 assert.equal(rendererValidation.status,'passed');
 if(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1')assert.deepEqual(rendererValidation.launchOptions.args,launch.args);
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(renderer);
 const editor=page.locator('[data-canvas-view="primary"]').getByLabel('Work unsaved edit');
 await editor.fill('DIRTY WORK CANVAS');
 const canvasView=async()=>(await action('canvas.views.inspect')).result.views.find(view=>view.viewId==='primary');
 await expect.poll(async()=>(await canvasView()).dirty).toBe(true);
 const originalView=await canvasView(),mount=await editor.getAttribute('data-mount');
 const target=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 const browseWork=()=>action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:manifest.workspace.id,workWorkspaceTab:'work'}});
 await browseWork();await expect(editor).toBeHidden();await expect(detail).toBeVisible();
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect(detail.getByRole('alert')).toContainText(/viewer edit/);
 assert.equal((await state()).selectedSessionId,draftOwner);
 assert.deepEqual(target(await canvasView()),target(originalView));
 await action('view.update',{patch:{workSurface:'chat'}});
 await expect(editor).toHaveValue('DIRTY WORK CANVAS');assert.equal(await editor.getAttribute('data-mount'),mount);
 await expect(composer).toHaveDraft('Private draft remains unchanged.');
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 await action('canvas.views.recover',target(await canvasView()));
 assert.equal((await state()).canvas.content,'# Original guard source');
 // A real Settings private draft stays mounted while Work is inspected. The
 // same Open chat path must ask, refuse on Keep, and retain the exact input.
 await action('canvas.show',{kind:'settings',title:'Guarded Settings'});
 await action('view.update',{patch:{settingsExpanded:['ai-connections']}});
 const ai=page.locator('.a-settings-experience [data-part="ai-connections"]');
 await ai.getByRole('button',{name:'Connect another service',exact:true}).click();
 await ai.getByRole('button',{name:/Anthropic API Connect with/}).click();
 const secret=ai.getByRole('textbox',{name:'API key',exact:true}),secretText='synthetic-unsaved-work-secret';
 await secret.fill(secretText);assert.ok(!JSON.stringify(await state()).includes(secretText));
 const providersBefore=(await state()).setup.providers.map(row=>row.id);
 await browseWork();await expect(secret).toBeHidden();
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 const guard=page.getByRole('alertdialog',{name:'Unsaved settings changes',exact:true});
 await expect(guard).toBeVisible();await guard.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(detail.getByRole('alert')).toContainText('Settings changes were kept');
 assert.equal((await state()).selectedSessionId,draftOwner);
 await action('view.update',{patch:{workSurface:'chat'}});
 await expect(secret).toHaveValue(secretText);
 assert.deepEqual((await state()).setup.providers.map(row=>row.id),providersBefore);
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await guard.getByRole('button',{name:'Discard changes',exact:true}).click();
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 await expect(composer).toHaveDraft('Private draft remains unchanged.');
 await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
 await browseWork();
 await detail.getByRole('button',{name:'Open chat: '+manifest.ownerSessionId,exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(manifest.ownerSessionId);
 await action('session.select',{id:draftOwner});
 let coldTaskResult={status:'not_requested',reason:'Requires the combined canonical cold-task producer'};
 if(coldTaskRequested){
  const expected=manifest.coldTask;
  assert.ok(expected,'Cold mode must seed its real persisted task');
  await action('session.select',{id:expected.sessionId});
  await action('view.update',{patch:{panel:'runtime',runtimeDraft:{tab:'direction'}}});
  const saved=page.getByRole('region',{name:'Saved task',exact:true});
  const load=saved.getByRole('button',{name:'Load saved task',exact:true});
  await expect(load).toBeVisible();
  const ordinary=async()=>{
   const current=await state();
   return {runtimeControl:current.runtimeControl?.[expected.sessionId]??null,
    task:current.sessions.find(row=>row.id===expected.sessionId)?.task??null};
  };
  const ordinaryBefore=await ordinary();
  const responsePromise=page.waitForResponse(response=>{
   if(!response.url().endsWith('/api/actions')||response.request().method()!=='POST')return false;
   const body=response.request().postDataJSON();
   return body.action==='task.get'&&body.args.sessionId===expected.sessionId;
  }).then(response=>({response}),error=>({error}));
  await load.focus();await page.keyboard.press('Enter');
  const observed=await responsePromise;if(observed.error)throw observed.error;
  const response=observed.response,body=await response.json();
  assert.equal(response.status(),200);assert.equal(body.accepted,true);
  assert.equal(body.result.status,'available');assert.equal(body.result.task.id,expected.id);
  assert.equal(body.result.task.revision,expected.revision);
  assert.equal(body.result.continuity.status,'not_observed_cold');
  assert.ok(!body.state,'A passive cold task response must not return broad app state');
  assert.ok(Buffer.byteLength(JSON.stringify(body),'utf8')<=10240,'Bounded task response');
  await expect(saved.getByText(expected.objective,{exact:true})).toBeVisible();
  await expect(saved.getByRole('status').filter({hasText:`Task paused · revision ${expected.revision}`})).toBeVisible();
  await expect(saved.getByText(/Some details are not loaded in this bounded view/)).toBeVisible();
  await saved.getByText('Linked questions, operations, and artifacts',{exact:true}).click();
  await expect(saved.getByText('Artifacts: Not loaded (80)',{exact:true})).toBeVisible();
  assert.ok(!JSON.stringify(body).includes(expected.privateMarker));
  assert.ok(!(await saved.innerText()).includes(expected.privateMarker));
  assert.deepEqual(await ordinary(),ordinaryBefore,'Cold read cannot publish private task into ordinary state');
  await saved.getByText('Record a blocker or completion',{exact:true}).click();
  await saved.getByRole('textbox',{name:'Evidence that the objective is complete (one per line)'}).fill('Synthetic evidence, not permission to complete omitted details');
  await expect(saved.getByRole('button',{name:'Complete task with evidence',exact:true})).toBeDisabled();
  for(const width of [390,320,1440]){
   await page.setViewportSize({width,height:900});
   await load.scrollIntoViewIfNeeded();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Cold task no overflow at '+width);
   await page.screenshot({path:path.join(output,`cold-task-${width}.png`)});
  }
  // A narrow viewport is not a touch device. Exercise the real coarse-pointer
  // stylesheet in a separately observed client rather than forcing its CSS.
  touchBrowser=await chromium.launch(launch);
  touchBrowser.on('disconnected',()=>{termination.touchBrowserDisconnectedAt=Date.now()});
  const touch=await touchBrowser.newPage({hasTouch:true,viewport:{width:390,height:900},extraHTTPHeaders:headers});
  observePage(touch,'touch');
  await touch.goto(url);await touch.waitForFunction(()=>window.amplifier);
  assert.equal(await touch.evaluate(()=>matchMedia('(pointer:coarse)').matches),true);
  const touchAction=(name,args)=>bounded(touch.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]),actionTimeoutMs,'Touch '+name);
  await touchAction('session.select',{id:expected.sessionId});
  await touchAction('view.update',{patch:{panel:'runtime',runtimeDraft:{tab:'direction'}}});
  const touchSaved=touch.getByRole('region',{name:'Saved task',exact:true});
  const touchLoad=touchSaved.getByRole('button',{name:'Load saved task',exact:true});
  await touchLoad.tap();
  await expect(touchSaved.getByText(expected.objective,{exact:true})).toBeVisible();
  for(const width of [390,320]){
   await touch.setViewportSize({width,height:900});await touchLoad.scrollIntoViewIfNeeded();
   const box=await touchLoad.boundingBox();
   assert.ok(box.height>=44&&box.width>=44,'44px coarse-pointer Load target at '+width);
   assert.ok(await touch.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Touch task no overflow at '+width);
   await touch.screenshot({path:path.join(output,`cold-task-touch-${width}.png`)});
  }
  await bounded(touch.close(),actionTimeoutMs,'Touch page close');
  await action('session.select',{id:draftOwner});
  await expect(saved.getByText(expected.objective,{exact:true})).toHaveCount(0);
  await load.click();
  await expect(saved.getByRole('alert')).toContainText('Saved task state is unavailable');
  await expect(saved.getByText('No saved task is recorded.',{exact:true})).toHaveCount(0);
  await expect(saved.getByRole('button',{name:'Save task',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
  await expect(composer).toHaveDraft('Private draft remains unchanged.');
  await expect(page.getByRole('button',{name:'Remove private-draft.txt',exact:true})).toBeVisible();
  coldTaskResult={status:'passed',taskId:expected.id,revision:expected.revision,
   responseBytes:Buffer.byteLength(JSON.stringify(body),'utf8'),ordinaryStateUnchanged:true,
   missingDetailsVisible:true,completionDisabled:true,unavailableNotEmpty:true,
   touch:{coarsePointer:true,separateProcess:true,widths:[390,320],minimumLoadTarget:44}};
 }
 const observation=await (await page.request.get(url+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
 assert.deepEqual(observation.forbiddenRuntimeCalls,[]);
 assert.deepEqual(observation.runtimeCallbackCounts,{start:0,send:0,prewarm:0});
 assert.equal(observation.pendingWarmups,0,'No scheduled warm callback may remain unobserved');
 assert.equal(observation.coldReopen,true);assert.deepEqual(observation.coldReadCalls,[]);
 if(coldTaskRequested){
  assert.deepEqual(observation.taskControlCalls,[]);
  assert.deepEqual(observation.taskReadObservations.map(row=>row.status),['available','available','unavailable']);
  assert.ok(observation.taskReadObservations.every(row=>row.accepted&&!row.broadStateReturned));
  coldTaskResult.readStatuses=observation.taskReadObservations.map(row=>row.status);
 }
 const runtime=await (await page.request.get(url+'/fixture',{timeout:actionTimeoutMs})).json();
 assert.deepEqual(runtime.started,[]);assert.deepEqual(runtime.sent,[]);assert.equal(runtime.workerCount,0);
 // Close observed pages before the final awaited assertion: detached listener
 // failures from either client must not arrive after the PASS decision.
 await bounded(peer.close(),actionTimeoutMs,'Peer page close');
 await bounded(page.close(),actionTimeoutMs,'Primary page close');
 observer.assertHealthy();
 // Deliberate, separate HTTP controls after the UI is quiescent. No browser
 // context owns these requests; closing the browsers must not abort the 503 probe.
 probeClient=await httpRequest.newContext({extraHTTPHeaders:{...headers,'X-Amplifier-State-Transport':'delta-v1'}});
 const waitScope={workspaceId:manifest.workspace.id,limit:1};
 const listed=await probeClient.post(url+'/api/actions',{data:{action:'workspace.work.list',args:waitScope},timeout:actionTimeoutMs,maxRetries:0});
 assert.equal(listed.status(),200);
 const listing=await listed.json();assert.equal(listing.accepted,true);
 assert.match(listing.result.observationVersion,/^[a-f0-9]{64}$/);
 const waitArgs={...waitScope,afterVersion:listing.result.observationVersion,waitMs:30000};
 const waitProbe=commandId=>observeCompletion((async()=>{
  const response=await probeClient.post(url+'/api/actions',{
   data:{id:commandId,action:'workspace.work.wait',args:waitArgs},timeout:40000,maxRetries:0});
  const body=await response.body();assert.ok(body.length<=65536,'Bounded control wait response');
  const value=JSON.parse(body.toString());
  const receipt={commandId,status:response.status(),...Object.fromEntries(
   ['accepted','code','changed','timedOut'].map(key=>[key,value[key]??null]))};
  termination.wireWaits.push(receipt);return receipt;
 })());
 const natural=await requireCompletion(waitProbe(workWaitProbeIds.natural));
 assert.equal(natural.status,200);assert.equal(natural.accepted,true);
 assert.equal(natural.changed,false);assert.equal(natural.timedOut,true);
 assert.equal(termination.shutdownFences.length,0);
 // Wait for the actual dispatch record, not just the outgoing request event.
 await expect.poll(()=>termination.workWaits.some(row=>row.commandId===workWaitProbeIds.natural&&row.phase==='returned')).toBe(true);
 shutdownCompletion=waitProbe(workWaitProbeIds.shutdown);
 await expect.poll(()=>termination.workWaits.some(row=>row.commandId===workWaitProbeIds.shutdown&&row.phase==='entered')).toBe(true);
 assert.ok(!termination.workWaits.some(row=>row.commandId===workWaitProbeIds.shutdown&&row.phase!=='entered'),'Shutdown probe must still be pending');
 assert.equal(termination.shutdownFences.length,0);
 result={status:'passed',fidelity:{mode:fidelityBaseline?'before-change-capture':'approved-gap-check',nextAction:!fidelityBaseline,readableEvidence:!fidelityBaseline},sourceFixture:!installed,installedWheel:!!installedIdentity,installedIdentity,launchOptions:launch,
  homeEntry:{via:'ordinary Continue chat',sessionId:manifest.ownerSessionId,selectionActions:entrySelects.length,
   surface:'chat',runtimeCallbackCounts:entryObservation.runtimeCallbackCounts},
  coldServiceReopen:{seedPid:seed.pid,reopenedPid:fixture.pid,readHydrationCalls:observation.coldReadCalls},
  dirtyCanvas:{refusal:true,mountPreserved:true,draftAndAttachmentPreserved:true},
  dirtySettings:{keepRefusedNavigation:true,privateDraftPreserved:true,discardWithoutSave:true},
  decisionCurrentness:{freshAccepted:true,laterCounterevidenceHistorical:true},coldTask:coldTaskResult,
  navigationWorkReturn:{backToWork:true,privateFilterRetained:true,forwardToChat:true},
  acceptedItems:found.size,sourcePagesComplete:true,priorReturnRetained:true,currentUnknown:true,
  widths:[1440,390,320],clients:coldTaskRequested?3:2,forbiddenRuntimeCalls:observation.forbiddenRuntimeCalls,
  peerClient:{separateProcess:true},rendererValidation,rendererToolchain,
  runtimeCallbackCounts:observation.runtimeCallbackCounts,observationErrors:observer.errors,
  browserTimeouts:{actionTimeoutMs,navigationTimeoutMs},
  limits:['No live provider or private history','Synthetic runtime; installed identity requires explicit independently prepared wheel']};
} catch(error) {
 originalError=error;
 // An expected missing-UI baseline still needs observed fixture/runtime facts.
 // Evidence collection never replaces the original assertion or bypasses finally.
 const failure={status:'failed',error:error.message,observationErrors:observer.errors,fixture:null};
 try {
  if(page&&fixtureUrl)failure.fixture=await (await page.request.get(fixtureUrl+'/fixture/shared-work',{timeout:actionTimeoutMs})).json();
  await writeFile(path.join(output,'failure.json'),JSON.stringify(failure,null,2)+'\n');
  if(page&&!page.isClosed())await page.screenshot({path:path.join(output,'failure.png'),timeout:actionTimeoutMs});
 } catch(evidenceError){console.error('Failure evidence unavailable:',evidenceError.message)}
 throw error;
}
finally {
 termination.closeRequestedAt=Date.now();
 let teardownError;
 try{
  await finishFixture({label:'Shared Work fixture',close:[['touch browser',()=>touchBrowser?.close()],['peer browser',()=>peerBrowser?.close()],['browser',()=>browser?.close()]],stop,originalError});
  assert.ok(shutdownCompletion,'Known shutdown request was not started');
  await bounded(requireCompletion(shutdownCompletion),actionTimeoutMs,'Shutdown wire receipt');
  termination.verdict=assertWorkWaitTermination(termination);
 }catch(error){
  teardownError=error;termination.verificationError={name:error.name,message:error.message};
  if(originalError)console.error('Work wait drain verification failed:',error);
 }
 finally{
  try{await bounded(Promise.resolve().then(()=>probeClient?.dispose()),actionTimeoutMs,'Probe client dispose')}
  catch(error){
   termination.probeDisposeError={name:error.name,message:error.message};
   if(originalError||teardownError)console.error('Probe client disposal failed:',error);
   teardownError??=error;
  }
  finally{
   try{await writeFile(path.join(output,'termination.json'),JSON.stringify(termination,null,2)+'\n')}
   catch(error){
    if(originalError||teardownError)console.error('Termination evidence unavailable:',error.message);
    teardownError??=error;
   }
  }
 }
 if(teardownError&&!originalError)throw teardownError;
}
await writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
