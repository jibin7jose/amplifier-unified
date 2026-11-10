// Actual standard Settings clicks, HTTP receipts and SSE state; fake installers.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/smart_tool_update_server.py',import.meta.url))]);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/smart-tool-updates';
let browser,page,log='';const result={status:'pending',realHTTPAndSSE:true,syntheticInstallers:true,externalCalls:0};
fixture.stderr.on('data',data=>log+=data);
try{
 await mkdir(out,{recursive:true});
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error(log||'Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exited ${code}: ${log}`))});fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 assert.equal(boot.package,fileURLToPath(new URL('../../amplifier_web/__init__.py',import.meta.url)));
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=result.pageErrors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const results=page.getByRole('region',{name:'Smart Tool update results'});
 await expect(results).toBeVisible();await expect(results).toContainText('Updated: healthy');
 await expect(results).toContainText('unavailable');
 assert.ok(!(await page.locator('body').innerText()).includes('private-provider-secret'));
 const evidence=async()=>{const response=await page.request.get(boot.url+'/fixture/tool-updates');assert.ok(response.ok());return response.json()};
 assert.deepEqual((await evidence()).calls,{stage:['unavailable','healthy'],activate:['healthy']});
 await page.screenshot({path:out+'/partial-desktop.png'});
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await results.isVisible())await openSettingsPage(page,'updates');
 await results.getByRole('button',{name:'Retry this tool',exact:true}).click();
 await expect(results).toHaveCount(0);
 assert.deepEqual((await evidence()).calls,{stage:['unavailable','healthy','unavailable'],activate:['healthy','unavailable']});
 assert.ok((await page.request.post(boot.url+'/fixture/tool-updates')).ok());
 await page.setViewportSize({width:390,height:844});
 await expect(results).toContainText('skip-example');
 const before=(await evidence()).calls;
 await page.screenshot({path:out+'/failure-narrow.png'});
 await results.getByRole('button',{name:'Skip this version',exact:true}).click();
 await expect(results).toHaveCount(0);
 assert.deepEqual((await evidence()).calls,before);assert.deepEqual((await evidence()).pending,[]);
 // A retained/unknown runner qualification cannot hide the app's install action.
 assert.ok((await page.request.post(boot.url+'/fixture/runner-update')).ok());
 const updates=page.locator('[data-part="updates"]');
 const install=updates.locator('[data-action="updates.install"]');
 await expect(updates.getByRole('heading',{name:'Updates available',exact:true})).toBeVisible();
 await expect(install).toBeEnabled();await expect(results).toBeVisible();
 await expect(updates).toContainText('New app remains available while the runner waits.');
 const runnerAttempt=(await evidence()).pending;
 await page.screenshot({path:out+'/runner-app-narrow.png'});
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await results.isVisible())await openSettingsPage(page,'updates');
 await page.setViewportSize({width:1280,height:950});
 await expect(install).toBeEnabled();await expect(results).toBeVisible();
 await page.screenshot({path:out+'/runner-app-desktop.png'});
 await install.click();
 await expect(updates).toContainText('Waiting for your work to finish');
 await expect(install).toHaveCount(0);
 assert.deepEqual((await evidence()).calls.application,['stage']);
 assert.deepEqual((await evidence()).pending,runnerAttempt);
 assert.equal((await evidence()).pendingApp.latest,'v9.9.9');
 // A click during the runner's observation queues instead of disappearing.
 assert.ok((await page.request.post(boot.url+'/fixture/runner-observation/hold')).ok());
 await updates.locator('[data-action="updates.check"]').click();
 await expect.poll(async()=>(await evidence()).checkQueued).toBe(true);
 assert.deepEqual((await evidence()).calls.checks,undefined);
 assert.ok((await page.request.post(boot.url+'/fixture/runner-observation/release')).ok());
 await writeFile(out+'/released-observation.json',JSON.stringify(await evidence(),null,2));
 await expect(updates.getByRole('heading',{name:'Updates available',exact:true})).toBeVisible();
 await expect(install).toBeEnabled();
 assert.deepEqual((await evidence()).calls.checks,['available']);
 assert.deepEqual((await evidence()).pending,runnerAttempt);
 assert.deepEqual(errors,[]);
 result.status='passed';result.checks={partialSuccess:true,namedFailure:true,noSecretOutput:true,reloadRetainsFailure:true,retryOnlyFailed:true,skipNoInstall:true,desktopAndNarrow:true,runnerWaitAllowsApp:true,runnerReceiptRetained:true,appIdleWaitRetained:true,refreshQueuesDuringObservation:true};
}catch(error){result.status='failed';result.error=String(error);if(page)await page.screenshot({path:out+'/failed.png'});throw error}
finally{
 await browser?.close();fixture.kill('SIGTERM');
 await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});
 result.fixtureLog=log;await writeFile(out+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}
