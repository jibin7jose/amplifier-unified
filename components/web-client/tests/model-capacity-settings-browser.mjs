import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {finishFixture,ownFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from './browser-settings.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const outputBase=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(outputBase,{recursive:true});
const outputDir=await mkdtemp(join(outputBase,'model-capacity-browser-'));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(root,'tests/fixtures/model_capacity_ui_server.py')],
 {stdio:['ignore','pipe','pipe'],env:process.env});
const stop=ownFixture(fixture,{label:'Model capacity settings fixture'});
let browser,failure,stderr='';
fixture.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-8000)});
try{
 const base=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
  const failed=error=>{cleanup();reject(error)};
  const exited=(code,signal)=>failed(Error(`Fixture exited before readiness (${code??signal}): ${stderr}`));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let ready;try{ready=JSON.parse(line)}catch{continue}
    if(ready.fixture!=='model-capacity-ui')continue;
    if(ready.pid!==fixture.pid||!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(ready.base)){
     failed(Error('Invalid owned fixture readiness'));return;
    }
    cleanup();resolve(ready.base);return;
   }
   if(pending.length>8000)failed(Error('Fixture readiness output exceeded bound'));
  };
  const timer=setTimeout(()=>failed(Error('Fixture startup timed out: '+stderr)),30000);
  fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1100,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage();page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const observe=async()=>{const response=await page.request.get(base+'/fixture/model-capacity-observations');assert.equal(response.ok(),true);return response.json()};
 await page.goto(base);await page.waitForSelector('#amp-one');
 const before=await observe();
 await openSettingsPage(page,'model-capacity');
 const section=page.getByRole('region',{name:'Model capacity',exact:true});
 const waiting=section.getByLabel('Maximum waiting model calls (0 means no count limit)',{exact:true});
 const timeout=section.getByLabel('Maximum wait in seconds (0 means no time limit)',{exact:true});
 const save=section.getByRole('button',{name:'Save model-capacity settings',exact:true});
 await expect(waiting).toHaveValue('0');await expect(timeout).toHaveValue('0');
 await waiting.fill('300');await timeout.fill('120');await save.click();
 await expect(section.getByRole('status').filter({hasText:'Saved for all chats'})).toBeVisible();
 assert.deepEqual((await observe()).policy,{revision:1,maxWaitingCalls:300,waitTimeoutSeconds:120});

 // A second client changes the same host policy; stale private edits survive
 // the rejection and cannot overwrite the new saved revision.
 await waiting.fill('350');
 const other=await context.newPage();await other.goto(base);await other.waitForSelector('#amp-one');
 const concurrent=await other.request.post(base+'/fixture/model-capacity-concurrent-save',
  {data:{maxWaitingCalls:400,waitTimeoutSeconds:45}});
 assert.equal(concurrent.ok(),true);assert.equal((await concurrent.json()).accepted,true);
 await save.click();
 await expect(section.getByRole('alert').filter({hasText:'changed'})).toBeVisible();
 await expect(waiting).toHaveValue('350');
 assert.equal((await observe()).policy.maxWaitingCalls,400);
 await section.getByRole('button',{name:'Reload saved settings',exact:true}).click();
 await expect(waiting).toHaveValue('350');await expect(save).toBeDisabled();
 await section.getByRole('button',{name:'Discard edits and use saved settings',exact:true}).click();
 await expect(waiting).toHaveValue('400');await expect(timeout).toHaveValue('45');
 await other.close();

 // Commit the setting but lose its response. Reopening must retain the exact
 // command identity, and checking that save must not create a second mutation.
 const attempts=[];let lose=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='modelCapacity.set'){
   attempts.push(body);
   if(lose){lose=false;await route.fetch();await route.abort('connectionreset');return}
  }
  await route.continue();
 });
 await waiting.fill('450');await timeout.fill('0');await save.click();
 await expect(section.getByRole('button',{name:'Check the same save',exact:true})).toBeVisible();
 assert.equal((await observe()).policy.revision,3);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'model-capacity');
 await expect(section.getByRole('button',{name:'Check the same save',exact:true})).toBeVisible();
 await section.getByRole('button',{name:'Check the same save',exact:true}).click();
 await expect(section.getByRole('status').filter({hasText:'Saved for all chats'})).toBeVisible();
 assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1]);
 const after=await observe();
 assert.deepEqual(after.policy,{revision:3,maxWaitingCalls:450,waitTimeoutSeconds:0});
 assert.equal(after.writes.length,3);
 assert.deepEqual(after.runtimeCalls,before.runtimeCalls);
 assert.equal(after.selectedSessionId,before.selectedSessionId);
 assert.equal(after.waiters,0);
 await section.getByRole('button',{name:'Use unlimited waiting',exact:true}).click();
 await expect(waiting).toHaveValue('0');await expect(timeout).toHaveValue('0');
 await save.click();
 await expect(section.getByRole('status').filter({hasText:'no waiting-call count limit'})).toBeVisible();
 assert.deepEqual((await observe()).policy,{revision:4,maxWaitingCalls:0,waitTimeoutSeconds:0});
 await expect(section.getByRole('status').filter({hasText:'wait until capacity is available or you stop work'})).toBeVisible();
 await page.screenshot({path:join(outputDir,'model-capacity-desktop.png'),animations:'disabled'});

 // Narrow settings preserve a private draft when the user declines to close;
 // cold reopen keeps the saved global preference rather than that discarded edit.
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
 await waiting.fill('500');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(waiting).toHaveValue('500');
 await section.getByRole('button',{name:'Discard edits and use saved settings',exact:true}).click();
 await expect(waiting).toHaveValue('0');
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'model-capacity');
 await expect(waiting).toHaveValue('0');await expect(timeout).toHaveValue('0');
 await waiting.scrollIntoViewIfNeeded();
 assert.equal(await section.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:join(outputDir,'model-capacity-mobile.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);
 const result={passed:true,productionBuiltUI:true,productionService:true,
  syntheticRuntime:true,providerCalls:false,checks:[
   'global finite and unlimited wait settings','two-client stale writer rejected',
   'private edit retained until explicit discard','lost acknowledgement retries same command after reopen',
   'one write per confirmed command','no worker start or stop from changing settings',
   'saved preference survives cold reopen','desktop and narrow layout']};
 await writeFile(join(outputDir,'result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify({outputDir,...result}));
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Model capacity settings fixture',
 close:[['Model capacity browser',()=>browser?.close()]],stop,originalError:failure})}
