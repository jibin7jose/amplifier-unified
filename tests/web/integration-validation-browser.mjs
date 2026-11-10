import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_OUTPUT_DIR||'/tmp/unified-integration-validation';await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/integration_validation_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(ready.url);const panel=page.getByRole('region',{name:'Task worktrees'});await panel.waitFor();
 const facts=()=>page.request.get(ready.url+'/fixture/integration').then(row=>row.json()),observed=await facts();
 await panel.getByText('Select a repository within this workspace',{exact:true}).click();
 await panel.getByText('Combine and check work',{exact:true}).click();
 const evidence=panel.locator('[data-part=integration-evidence]'),saved=evidence.locator('[data-part=integration-result]');
 for(const root of observed.roots){await panel.getByLabel('Repository folder',{exact:true}).fill(root);await panel.getByRole('button',{name:'Inspect repository',exact:true}).click();await expect(panel.locator('[data-part=selected-repository]')).toHaveText(root);await evidence.getByRole('button',{name:'Add inspected repository',exact:true}).click()}
 await evidence.getByRole('button',{name:'Save candidate observation',exact:true}).click();await expect(saved.getByRole('status')).toHaveText('Candidate: matched');
 const capture=await evidence.locator('[data-candidate-id]').getAttribute('data-candidate-id');
 const validation=evidence.locator('[data-part=integration-validation]');await validation.getByText('Run checks and collect results',{exact:true}).click();
 const writes=[];let dropped=false;
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='integration.validate'){writes.push(body);if(!dropped){dropped=true;await route.fetch();await route.abort('connectionreset');return}}await route.continue()});
 await validation.getByLabel('Validation command',{exact:true}).fill('test "$(cat source.txt)" = original && printf browser-native-result > result.txt');
 await validation.getByRole('button',{name:'Run validation command',exact:true}).click();await expect(validation.getByRole('alert')).toBeVisible();
 await page.reload();await panel.waitFor();await evidence.getByText('Combine and check work',{exact:true}).click();await validation.getByText('Run checks and collect results',{exact:true}).click();
 await validation.getByRole('button',{name:'Check the same integration request',exact:true}).click();await expect(validation.locator('[data-part=validation-result]')).toBeVisible();
 assert.equal(writes.length,2);assert.equal(writes[0].id,writes[1].id);
 await evidence.getByRole('button',{name:'Load saved observations',exact:true}).click();await evidence.locator('[data-observation-id="'+capture+'"]').getByRole('button',{name:'Select candidate',exact:true}).click();
 await expect.poll(async()=>(await facts()).operations.filter(row=>row.state==='completed').length).toBe(1);
 await validation.getByRole('button',{name:'Collect validation results',exact:true}).click();await expect(validation.locator('[data-part=validation-result]').getByRole('status')).toHaveText('Combined result: partial');
 await validation.getByLabel('Repository to validate',{exact:true}).selectOption(observed.roots[1]);await validation.getByLabel('Validation command',{exact:true}).fill('test "$(cat source.txt)" = original && printf browser-native-result > result.txt');
 await validation.getByRole('button',{name:'Run validation command',exact:true}).click();await expect.poll(async()=>(await facts()).operations.filter(row=>row.state==='completed').length).toBe(2);
 await validation.getByText('Advanced collection inputs',{exact:true}).click();await validation.getByText('Result files and publication reports',{exact:true}).click();
 const results=(await facts()).results;results[0].publication={status:'published',reference:'https://example.test/reported-only'};
 await validation.getByLabel('Integration result declarations',{exact:true}).fill(JSON.stringify(results));await validation.getByRole('button',{name:'Collect validation results',exact:true}).click();
 const combined=validation.locator('[data-part=validation-result]');await expect(combined.getByRole('status')).toHaveText('Combined result: passed');await expect(combined).toContainText('Publication: published (reported)');await expect(combined).toContainText('Publication: unknown (not observed)');
 await page.request.post(ready.url+'/fixture/remove-result');const href=await combined.getByRole('link',{name:'Download saved result'}).first().getAttribute('href');assert.equal(await page.request.get(ready.url+href).then(row=>row.text()),'browser-native-result');
 await combined.scrollIntoViewIfNeeded();await page.screenshot({path:output+'/integration-validation-desktop.png'});
 await page.setViewportSize({width:390,height:844});await combined.scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:output+'/integration-validation-mobile.png'});
 await page.request.post(ready.url+'/fixture/change-dependency');await validation.getByLabel('Integration result declarations').fill('[]');await validation.getByRole('button',{name:'Collect validation results',exact:true}).click();await expect(combined.getByRole('status')).toHaveText('Combined result: mismatch');
 assert.equal((await facts()).operations.length,2);assert.deepEqual(errors,[]);
 await writeFile(output+'/browser-report.json',JSON.stringify({passed:true,actualManagedShell:true,syntheticApprovalHooks:true,providerCalls:false,partialThenPassedThenDrift:true,lostAckAndReloadNoReplay:true,retainedDownloadAfterDeletion:true,mobileNoOverflow:true,errors},null,2));
}catch(error){if(page)await page.screenshot({path:output+'/integration-validation-failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();fixture.kill()}
