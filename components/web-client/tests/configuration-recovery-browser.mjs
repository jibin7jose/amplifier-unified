import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {startSettingsFixture} from './settings-fixture.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';
import {recoveryRequestObserver} from './configuration-recovery-observer.mjs';

const output=resolve(process.env.AMPLIFIER_TEST_ARTIFACTS||'../output/configuration-recovery');
await mkdir(output,{recursive:true});
const {base,stop}=await startSettingsFixture({SETTINGS_RECOVERY_FIXTURE:'1'});
let browser,failure;const browsers=[],pages=[],errors=[],actions=[];
const requestObserver=recoveryRequestObserver(base,actions);
const observe=async page=>{const response=await page.request.get(base+'/fixture/recovery-observations');assert.equal(response.status(),200);return response.json()};
const panel=page=>page.getByRole('region',{name:'Configuration recovery',exact:true});
async function open(page){
 await openChatAdvancedPage(page,'Model & tools');
 const detail=page.getByRole('dialog',{name:'Model & tools',exact:true}).locator('details').filter({has:page.locator('summary').filter({hasText:/^Conversation modules$/})}).first();
 if(!await detail.evaluate(el=>el.open))await detail.locator(':scope > summary').click();
 await expect(panel(page)).toBeVisible();
 await expect(panel(page).getByRole('button',{name:'Refresh status',exact:true})).toBeEnabled();
}
try{
 const launch={headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?
  {args:['--no-zygote','--single-process','--disable-gpu']}:{}),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?
  {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})};
 browser=await chromium.launch(launch);browsers.push(browser);
 for(let index=0;index<2;index++){
  // Chromium's single-process mode needs a separate owner for each private
  // context. Keep both actual clients and all isolation assertions in DTUs.
  if(index&&process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'){
   browser=await chromium.launch(launch);browsers.push(browser);
  }
  const context=await browser.newContext({viewport:{width:index?390:1200,height:900}});
  // Only fixture bootstrap uses a synthetic bearer. Every application/UI request
  // below uses the ordinary authenticated browser cookie and real action route.
  const auth=await context.request.post(base+'/fixture/recovery-cookie',{headers:{Authorization:'Bearer fixture-browser-control-token'}});
  assert.equal(auth.status(),200);
  const page=await context.newPage();pages.push(page);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>requestObserver.record(request,index));
  await page.goto(base+'/');await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
  requestObserver.assertHealthy();
 }
 const [owner,other]=pages,initial=await observe(owner),sid=initial.sessions['Recoverable owner'].sessionId;
 const draft='{"uncommitted_recovery_draft":"keep this exact editor text"}';
 await owner.evaluate(({sid,draft})=>window.amplifier.dispatch('view.update',{patch:{moduleEditor:{sessionId:sid,text:draft}}}),{sid,draft});
 await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),initial.sessions['Unknown owner'].sessionId);
 await showFixtureConversation(owner);await open(owner);await open(other);
 await expect(panel(owner).getByRole('button',{name:'Check saved outcome',exact:true})).toBeEnabled();
 await expect(panel(owner).getByRole('button',{name:'Resume original change',exact:true})).toBeDisabled();
 assert.equal((await observe(owner)).calls.length,0,'Opening recovery must not warm or inspect any runtime.');
 assert.equal(actions.filter(a=>a.action==='configuration.inspect').length,0);
 assert.ok(!await panel(owner).innerText().then(text=>text.includes('PRIVATE_RECOVERY_SENTINEL')));
 // Check is a genuine keyboard click. It classifies evidence but performs no save.
 const check=panel(owner).getByRole('button',{name:'Check saved outcome',exact:true});
 await check.focus();await owner.keyboard.press('Enter');
 await expect(panel(owner).getByRole('button',{name:'Resume original change',exact:true})).toBeEnabled();
 assert.equal((await observe(owner)).calls.length,0);
 // Both browsers retain their own owner and outcome; no global action-status borrowing.
 await panel(other).getByRole('button',{name:'Check saved outcome',exact:true}).click();
 await expect(panel(other).getByRole('button',{name:'Resume original change',exact:true})).toBeDisabled();
 await expect(panel(other)).toContainText('outcome is not confirmed');
 // Lose an accepted resume acknowledgement. Server proceeds only once; client
 // must read the same operation, not resubmit or claim mounted on acceptance.
 let lost=false;
 await owner.route('**/api/actions',async route=>{
  if(!lost&&route.request().postDataJSON()?.action==='configuration.operation.resume'){
   lost=true;await route.fetch();await route.abort('failed');
  }else await route.continue();
 });
 await panel(owner).getByRole('button',{name:'Resume original change',exact:true}).click();
 await expect.poll(async()=>{const value=await observe(owner);return value.calls.filter(c=>c.operation==='configuration.apply').length}).toBe(1);
 await expect(panel(owner)).not.toContainText('Changes are applied to the loaded session.');
 await expect(panel(owner).getByRole('button',{name:'Refresh status',exact:true})).toBeEnabled();
 await panel(owner).getByRole('button',{name:'Refresh status',exact:true}).click();
 await expect(panel(owner).getByRole('button',{name:'Resume original change',exact:true})).toBeDisabled();
 assert.equal((await observe(owner)).sessions['Unknown owner'].attempts,1);
 assert.equal(actions.filter(a=>a.action==='configuration.operation.resume').length,1);
 const release=await owner.request.post(base+'/fixture/recovery-release');assert.equal(release.status(),200);
 await expect(panel(owner)).toContainText('Changes are applied to the loaded session.');
 const settled=await observe(owner);
 assert.equal(settled.sessions['Recoverable owner'].effect,'mounted');assert.equal(settled.loadedLimits[sid],4096);
 assert.equal(settled.sessions['Recoverable owner'].attempts,2);
 assert.ok(Object.values(settled.sessions).every(s=>s.requestUnchanged));
 assert.equal(await owner.evaluate(()=>window.amplifier.getState().view.moduleEditor.text),draft);
 await expect(panel(other)).not.toContainText('Changes are applied to the loaded session.');
 // Opening held legacy/saved views remains observational and visibly unavailable.
 for(const label of ['Saved owner','Legacy owner']){
  await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),initial.sessions[label].sessionId);
  await open(other);
  await expect(panel(other).getByRole('button',{name:'Resume original change',exact:true})).toBeDisabled();
  if(label==='Saved owner')await panel(other).getByRole('button',{name:'Check saved outcome',exact:true}).click();
  else await expect(panel(other)).toContainText('cannot be verified');
  await expect(panel(other).getByRole('button',{name:'Resume original change',exact:true})).toBeDisabled();
 }
 const final=await observe(owner);
 assert.equal(final.calls.filter(c=>c.operation==='configuration.apply').length,1);
 assert.equal(actions.filter(a=>a.action==='configuration.inspect').length,0);
 assert.equal(actions.filter(a=>a.action==='configuration.operation.resume').length,1);
 const captureFrames=[];
 for(const [index,page] of pages.entries()){
  await expect(panel(page)).toBeVisible();
  assert.equal(await panel(page).evaluate(el=>el.scrollWidth<=el.clientWidth),true);
  const target=panel(page);
  await target.scrollIntoViewIfNeeded();
  const required=[
   ['heading',target.getByRole('heading',{name:'Original configuration change',exact:true})],
   ['status',target.getByText(index?'The original change remains held until its evidence can be verified.':'Changes are applied to the loaded session.',{exact:true})],
   ...['Refresh status','Check saved outcome','Resume original change'].map(name=>[name,target.getByRole('button',{name,exact:true})]),
  ];
  if(index)required.push(['heldReason',target.getByText('The original operation or its evidence cannot be verified. Changes remain held; nothing was retried.',{exact:true})]);
  const rectangles={};
  for(const [name,locator] of required)rectangles[name]=await locator.boundingBox();
  captureFrames.push({browser:index,viewport:page.viewportSize(),target:await target.boundingBox(),rectangles});
  await writeFile(resolve(output,'capture-framing.json'),JSON.stringify(captureFrames,null,2));
  for(const [,locator] of required){
   await expect(locator).toBeVisible();
   await expect(locator).toBeInViewport({ratio:1});
  }
  await page.screenshot({animations:'disabled',path:resolve(output,index?'recovery-held-mobile.png':'recovery-mounted-desktop.png')});
 }
 assert.deepEqual(errors,[]);
 requestObserver.assertHealthy();
 await writeFile(resolve(output,'report.json'),JSON.stringify({cookieUi:true,genuineButtons:true,keyboardCheck:true,coldNoInspect:true,explicitOriginalResume:true,lostAckNoReplay:true,acceptedNotMounted:true,loadedReadbackRequired:true,draftPreserved:true,twoBrowserIsolation:true,heldUnknownSavedLegacy:true,requestUnchanged:true,resumeCount:1,nativeRuntimeSimulated:true,priorRealNativeProofRequired:true,observations:final,actions,errors},null,2));
}catch(error){
 failure=error;console.error('Recovery request observer:',JSON.stringify(requestObserver.snapshot()));throw error;
}finally{
 let closingFailure=failure;
 try{await finishFixture({label:'Configuration recovery UI fixture',close:browsers.map((owned,index)=>['Recovery browser '+index,()=>owned.close()]),stop,originalError:failure})}
 catch(error){closingFailure??=error;throw error}
 finally{
  // Always retain observer outcomes, including malformed submissions and late
  // events during close. Retention failure must not prevent owned cleanup.
  try{await writeFile(resolve(output,'request-observer.json'),JSON.stringify(requestObserver.snapshot(),null,2))}
  catch(error){if(closingFailure)console.error('Request observer evidence could not be retained:',error);else throw error}
  if(!closingFailure)requestObserver.assertHealthy();
 }
}
