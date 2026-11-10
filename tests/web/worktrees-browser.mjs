import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/worktrees_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(ready.url);await page.getByRole('region',{name:'Task worktrees'}).waitFor();
 const panel=page.getByRole('region',{name:'Task worktrees'});
 await panel.getByRole('button',{name:'Inspect repository',exact:true}).click();await expect(panel.getByText('main · Checkout clean',{exact:true})).toBeVisible();
 await expect(panel.locator('[data-part=execution-host]')).toHaveText(ready.host.label);
 await panel.getByRole('button',{name:'Create managed checkout'}).click();const card=panel.locator('[data-worktree-id]').first();await expect(card.getByRole('status')).toHaveText('Checkout ready');await expect(card.getByText('Local host: '+ready.host.label,{exact:true})).toBeVisible();
 await card.getByRole('button',{name:'Move execution here'}).click();await expect(panel.getByText('Handoff applied',{exact:true})).toBeVisible();await expect(panel.locator('[data-handoff-id]').first().getByText('Local host: '+ready.host.label,{exact:true})).toBeVisible();await expect(panel.locator('[data-part="execution-directory"]')).not.toHaveText(ready.root);
 await card.getByRole('button',{name:'Inspect checkout and manifest'}).click();await expect(panel.getByText('Checkout inspection',{exact:true})).toBeVisible();
 await panel.locator('[data-part="execution-directory"]').scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/unified-worktrees-desktop.png'});
 await page.reload();await page.getByRole('region',{name:'Task worktrees'}).waitFor();await expect(panel.locator('[data-part="execution-directory"]')).not.toHaveText(ready.root);
 await page.request.post(ready.url+'/fixture/fail-release');await panel.getByRole('button',{name:'Return execution to home'}).click();await expect(panel.getByText('Handoff unknown',{exact:true})).toBeVisible();
 await panel.getByLabel('What did you verify?').fill('Inspected the source checkout; retain its files and return to home.');await page.request.post(ready.url+'/fixture/restore-release');await panel.getByRole('button',{name:'Use target folder'}).click();await expect(panel.getByText('Handoff reconciled',{exact:true})).toBeVisible();await expect(panel.locator('[data-part="execution-directory"]')).toHaveText(ready.root);
 await card.getByRole('button',{name:'Remove clean checkout'}).click();await expect(card.getByRole('status')).toHaveText('Checkout removed');
 await page.setViewportSize({width:390,height:844});await panel.locator('[data-part="execution-directory"]').scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/unified-worktrees-mobile.png'});
 assert.deepEqual(errors,[]);console.log('Worktrees browser passed: real Git create/inspect, execution handoff, reload, unknown release, reconcile, safe cleanup and mobile.');
}finally{await browser?.close();fixture.kill()}
