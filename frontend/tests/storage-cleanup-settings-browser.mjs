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
const outputDir=await mkdtemp(join(outputBase,'storage-cleanup-browser-'));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(root,'tests/fixtures/storage_cleanup_ui_server.py')],
 {stdio:['ignore','pipe','pipe'],env:process.env});
const stop=ownFixture(fixture,{label:'Storage cleanup settings fixture'});
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
    if(ready.fixture!=='storage-cleanup-ui')continue;
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
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 const context=await browser.newContext({viewport:{width:1100,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage();page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const observe=async()=>{const response=await page.request.get(base+'/fixture/storage-cleanup-observations');assert.equal(response.ok(),true);return response.json()};
 await page.goto(base);await page.waitForSelector('#amp-one');
 const before=await observe();
 const evidenceRequests=[];let loseEvidence=false;
 const evidenceRoute=async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='storage.cleanup.evidence'){
   evidenceRequests.push(body);
   if(loseEvidence){loseEvidence=false;await route.fetch();await route.abort('connectionreset');return}
  }
  await route.fallback();
 };
 await page.route('**/api/actions',evidenceRoute);
 await openSettingsPage(page,'storage');
 const evidence=page.getByRole('region',{name:'Saved test evidence',exact:true});
 await expect(evidence.getByRole('button',{name:'Review saved test sizes',exact:true})).toBeVisible();
 assert.equal(evidenceRequests.length,0,'Opening Storage does not read saved-test pages');
 await evidence.getByRole('button',{name:'Review saved test sizes',exact:true}).click();
 await expect(evidence.getByRole('listitem')).toHaveCount(32);
 await expect(evidence).toContainText('191.0 KB recorded across 31 tests on this page.');
 await expect(evidence).toContainText('1 other test has incomplete size records.');
 await expect(evidence).toContainText('not a current disk measurement');
 await expect(evidence).toContainText('not an all-workspace total');
 const saved=identity=>evidence.getByRole('listitem').filter({hasText:`Test reference: ${identity}`});
 const complete=saved('saved-test-33');await complete.locator('summary').click();
 await expect(complete).toContainText('Original source: 1.0 KB.');
 await expect(complete).toContainText('Final workspace: 3.0 KB. Test results: 2.0 KB.');
 const full=saved('saved-test-32');await full.locator('summary').click();
 await expect(full).toContainText('Final workspace and results are included in a full environment archive: 10.0 KB. Their separate sizes were not recorded.');
 const remote=saved('saved-test-31');await remote.locator('summary').click();
 await expect(remote).toContainText('Remote results downloaded; additional copies may remain on the test machine.');
 const unknown=saved('saved-test-34');await unknown.locator('summary').click();
 await expect(unknown.locator('summary')).toContainText('Unavailable');
 await expect(unknown).toContainText('Complete saved-size accounting is unavailable. Its files remain protected.');
 await expect(unknown).toContainText('Original source: Unavailable.');
 await expect(unknown).toContainText('Final workspace: Unavailable. Test results: Unavailable.');
 const passive=await observe();assert.deepEqual(passive.policy,before.policy);
 assert.deepEqual(passive.writes,before.writes);assert.deepEqual(passive.runtimeCalls,before.runtimeCalls);
 assert.equal(passive.savedLedgerUnchanged,true);assert.equal(passive.savedWorkIntact,true);
 assert.equal(passive.unusedExists,true);assert.equal(passive.selectedSessionId,before.selectedSessionId);
 await evidence.scrollIntoViewIfNeeded();
 await page.screenshot({path:join(outputDir,'saved-test-sizes-desktop.png'),animations:'disabled'});
 loseEvidence=true;
 await evidence.getByRole('button',{name:'Show earlier tests',exact:true}).click();
 await expect(evidence.getByRole('alert')).toBeVisible();
 await expect(evidence.getByRole('listitem')).toHaveCount(32);
 await expect(complete).toContainText('Final workspace: 3.0 KB.');
 await expect(evidence.getByRole('button',{name:'Show earlier tests',exact:true})).toBeEnabled();
 await evidence.getByRole('button',{name:'Show earlier tests',exact:true}).click();
 await expect(evidence.getByRole('listitem')).toHaveCount(3);
 await expect(evidence).toContainText('18.0 KB recorded across 3 tests on this page.');
 await expect(saved('saved-test-02')).toHaveCount(1);await expect(saved('saved-test-00')).toHaveCount(1);
 await expect(saved('saved-test-33')).toHaveCount(0);
 await expect(evidence.getByRole('button',{name:'Show earlier tests',exact:true})).toHaveCount(0);
 await expect(evidence.getByRole('alert')).toHaveCount(0);
 assert.equal(evidenceRequests.length,3);
 assert.deepEqual(evidenceRequests[1].args,evidenceRequests[2].args,'Retry repeats the passive page cursor');
 assert.ok(Number.isInteger(evidenceRequests[1].args.before));
 await evidence.getByRole('button',{name:'Refresh recent test sizes',exact:true}).click();
 await expect(evidence.getByRole('listitem')).toHaveCount(32);
 assert.deepEqual(evidenceRequests[3].args,{});
 await page.setViewportSize({width:390,height:844});
 await unknown.locator('summary').click();await unknown.scrollIntoViewIfNeeded();
 assert.equal(await evidence.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 assert.equal(await unknown.evaluate(element=>element.getBoundingClientRect().right<=innerWidth),true);
 await page.screenshot({path:join(outputDir,'saved-test-sizes-mobile.png'),animations:'disabled'});
 const afterReads=await observe();assert.deepEqual(afterReads.policy,before.policy);
 assert.deepEqual(afterReads.writes,before.writes);assert.deepEqual(afterReads.runtimeCalls,before.runtimeCalls);
 assert.equal(afterReads.savedLedgerUnchanged,true);assert.equal(afterReads.savedWorkIntact,true);
 await page.setViewportSize({width:1100,height:900});await page.unroute('**/api/actions',evidenceRoute);
 const section=page.getByRole('region',{name:'Automatic cleanup',exact:true});
 await section.getByText('Retention and size',{exact:true}).click();
 const days=section.getByLabel('Keep unused copies for days',{exact:true});
 const size=section.getByLabel('Size target for unused copies (GiB)',{exact:true});
 const save=section.getByRole('button',{name:'Save cleanup settings',exact:true});
 await expect(days).toHaveValue('7');await expect(size).toHaveValue('10');
 await days.fill('3');await size.fill('0');await save.click();
 await expect(section.getByRole('status').filter({hasText:'saved for all workspaces'})).toBeVisible();
 assert.deepEqual((await observe()).policy,{revision:1,enabled:true,keepDays:3,maxUnusedBytes:0});
 await days.fill('4');
 const other=await context.newPage();await other.goto(base);await other.waitForSelector('#amp-one');
 const concurrent=await other.request.post(base+'/fixture/storage-cleanup-concurrent-save',{data:{keepDays:2}});
 assert.equal(concurrent.ok(),true);
 await save.click();await expect(section.getByRole('alert').filter({hasText:'changed'})).toBeVisible();
 await expect(days).toHaveValue('4');assert.equal((await observe()).policy.keepDays,2);
 await section.getByRole('button',{name:'Reload cleanup settings',exact:true}).click();
 await expect(days).toHaveValue('4');await expect(save).toBeDisabled();
 await section.getByRole('button',{name:'Discard edits and use saved settings',exact:true}).click();
 await expect(days).toHaveValue('2');await other.close();
 const attempts=[];let lose=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='storage.cleanup.save'){attempts.push(body);if(lose){lose=false;await route.fetch();await route.abort('connectionreset');return}}
  await route.continue();
 });
 await days.fill('0');await save.click();
 await expect(section.getByRole('button',{name:'Check the same request',exact:true})).toBeEnabled();
 assert.equal((await observe()).policy.revision,3);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'storage');
 await section.getByRole('button',{name:'Check the same request',exact:true}).click();
 await expect(section.getByRole('status').filter({hasText:'saved for all workspaces'})).toBeVisible();
 assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1]);
 assert.equal((await observe()).writes.length,3);
 assert.equal((await observe()).unusedExists,true);
 await section.getByRole('button',{name:'Review unused copies',exact:true}).click();
 await expect(section.getByLabel('Cleanup preview')).toBeVisible();
 await expect(section.getByLabel('Cleanup preview')).toContainText('This inspection found');
 await expect(section.getByLabel('Cleanup preview')).toContainText('Across cleanup batches:');
 await expect(section.getByLabel('Cleanup preview')).toContainText('Earlier measurements can be out of date');
 await expect(section.getByLabel('Cleanup preview')).toContainText('not a complete inventory');
 await page.screenshot({path:join(outputDir,'cleanup-budget-preview-desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await section.getByLabel('Cleanup preview').scrollIntoViewIfNeeded();
 assert.equal(await section.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:join(outputDir,'cleanup-budget-preview-mobile.png'),animations:'disabled'});
 await page.setViewportSize({width:1100,height:900});
 await expect(section.getByRole('button',{name:'Clean unused copies now',exact:true})).toBeEnabled();
 assert.equal((await observe()).unusedExists,true);
 const cleanupAttempts=[];let loseCleanup=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='storage.cleanup.run'){cleanupAttempts.push(body);if(loseCleanup){loseCleanup=false;await route.fetch();await route.abort('connectionreset');return}}
  await route.fallback();
 });
 await section.getByRole('button',{name:'Clean unused copies now',exact:true}).click();
 await expect(section.getByRole('button',{name:'Check the same request',exact:true})).toBeEnabled();
 assert.equal((await observe()).unusedExists,false);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'storage');
 await section.getByRole('button',{name:'Check the same request',exact:true}).click();
 await expect(section.getByRole('status').filter({hasText:'Cleanup completed'})).toBeVisible();
 assert.equal(cleanupAttempts.length,2);assert.deepEqual(cleanupAttempts[0],cleanupAttempts[1]);
 const after=await observe();assert.equal(after.savedWorkIntact,true);assert.equal(after.savedLedgerUnchanged,true);
 assert.equal(after.lastCleanup.status,'completed');assert.ok(after.lastCleanup.removedFileBytes>=4096);
 await expect(section.getByRole('status').filter({hasText:'Cleanup completed'})).toContainText('4.2 KB');
 assert.deepEqual(after.runtimeCalls,before.runtimeCalls);assert.equal(after.selectedSessionId,before.selectedSessionId);
 await page.screenshot({path:join(outputDir,'storage-cleanup-desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await section.getByText('Retention and size',{exact:true}).click();
 await days.fill('5');await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();await expect(days).toHaveValue('5');
 await section.getByRole('button',{name:'Discard edits and use saved settings',exact:true}).click();
 await expect(days).toHaveValue('0');await days.scrollIntoViewIfNeeded();
 assert.equal(await section.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:join(outputDir,'storage-cleanup-mobile.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);
 const result={passed:true,productionBuiltUI:true,productionService:true,syntheticRuntime:true,syntheticInstalledGraph:true,
  providerCalls:false,removedFileBytes:after.lastCleanup.removedFileBytes,checks:[
   'global retention and size settings','stale save retains private draft','lost save acknowledgment exact retry after reopen',
   'read-only preview keeps files','actual canonical unused generation retirement','lost cleanup acknowledgment reobserves same receipt',
   'source/result archives and project work remain','no worker starts or stops','desktop and narrow draft protection',
   'saved test sizes load only on explicit read','canonical receipt roles stay distinct from current disk use',
   'bounded saved-test pagination and refresh','unknown accounting remains unavailable and protected',
   'lost passive page response preserves last page and supports retry','saved-test reads preserve ledger and policy',
   'saved test sizes fit desktop and mobile']};
 await writeFile(join(outputDir,'result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify({outputDir,...result}));
}catch(error){failure=error;await writeFile(join(outputDir,'fixture-stderr.log'),stderr);console.error(stderr);throw error}
finally{await finishFixture({label:'Storage cleanup settings fixture',
 close:[['Storage cleanup browser',()=>browser?.close()]],stop,originalError:failure})}
