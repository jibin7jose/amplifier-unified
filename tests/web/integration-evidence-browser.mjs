import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/worktrees_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(ready.url);const panel=page.getByRole('region',{name:'Task worktrees'});await panel.waitFor();
 await panel.getByRole('button',{name:'Inspect repository',exact:true}).click();await expect(panel.getByText('main · Checkout clean',{exact:true})).toBeVisible();
 await panel.getByText('Combine and check work',{exact:true}).click();
 const evidence=panel.locator('[data-part=integration-evidence]'),result=evidence.locator('[data-part=integration-result]');
 await evidence.getByRole('button',{name:'Add inspected repository',exact:true}).click();await evidence.getByLabel('What are you checking?').fill('Combined source check');
 await evidence.getByText('Advanced source and import expectations',{exact:true}).click();await evidence.getByRole('button',{name:'Inspect loaded sources',exact:true}).click();await expect(result.getByRole('status')).toHaveText('Loaded sources: unknown');
 await evidence.getByRole('button',{name:'Save candidate observation',exact:true}).click();await expect(result.getByRole('status')).toHaveText('Candidate: matched');
 const receipt=await evidence.locator('[data-candidate-id]').getAttribute('data-candidate-id');
 await page.request.post(ready.url+'/fixture/change-source');await evidence.getByRole('button',{name:'Recheck this candidate'}).click();await expect(result.getByRole('status')).toHaveText('Recheck: mismatch');
 await evidence.getByRole('button',{name:'Load saved observations',exact:true}).click();await evidence.locator('[data-observation-id="'+receipt+'"]').getByRole('button',{name:'Select candidate',exact:true}).click();await expect(result.getByRole('status')).toHaveText('Candidate: matched');
 await page.reload();await panel.waitFor();await panel.getByText('Combine and check work',{exact:true}).click();await evidence.getByRole('button',{name:'Load saved observations',exact:true}).click();await evidence.locator('[data-observation-id="'+receipt+'"]').getByRole('button',{name:'Select candidate',exact:true}).click();await expect(result.getByRole('status')).toHaveText('Candidate: matched');
 await result.scrollIntoViewIfNeeded();await page.screenshot({path:process.env.AMPLIFIER_EVIDENCE_SCREENSHOT||'/tmp/unified-integration-evidence.png'});
 await page.setViewportSize({width:390,height:844});await result.scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);console.log('Integration evidence browser passed: passive unknown worker, explicit repository capture, real source drift mismatch, immutable retained observation, reload/list/read and mobile.');
}finally{await browser?.close();fixture.kill()}
