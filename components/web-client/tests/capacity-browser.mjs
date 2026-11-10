import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/capacity_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exit '+code)))});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const post=async(path)=>{const response=await page.request.post(ready.url+path,{data:{}});assert.equal(response.status(),200,await response.text());return await response.json()};
 await page.goto(ready.url);await showFixtureConversation(page);await openChatAdvancedPage(page,'Model & tools');
 const panel=page.getByRole('region',{name:'Task usage and budget'});
 await panel.getByRole('button',{name:'Inspect task usage'}).click();await expect(panel.getByRole('status')).toHaveText('Budget disabled · 0 recorded model calls');
 await panel.getByLabel('Enable cumulative task budget').check();await panel.getByLabel('Total token limit',{exact:true}).fill('100');await panel.getByRole('button',{name:'Save budget'}).click();await expect(panel.getByRole('status')).toHaveText('Budget active · 0 recorded model calls');
 await panel.getByLabel('Total token limit',{exact:true}).fill('42');
 assert.equal((await post('/fixture/model')).calls,1);const denied=await post('/fixture/model');assert.equal(denied.calls,1);assert.match(denied.error,/budget paused/);
 await expect(panel.getByRole('status')).toHaveText('Budget paused · 1 recorded model calls');await expect(panel.getByLabel('Total token limit',{exact:true})).toHaveValue('42');const total=panel.getByRole('region',{name:'Token usage'}).locator('.a-usage-grid > div').filter({has:page.getByText('Total tokens',{exact:true})});await expect(total.locator('dd')).toHaveText('12,645');
 await panel.getByRole('button',{name:'Inspect task usage'}).click();await expect(panel.getByRole('status')).toHaveText('Budget paused · 1 recorded model calls');await expect(panel.getByText('Session cost (USD)',{exact:true})).toHaveCount(0); // Unknown cost must not be rendered as a numeric total.
await panel.scrollIntoViewIfNeeded();await page.screenshot({path:'/tmp/unified-capacity-desktop.png'});
 const restored=await post('/fixture/restart');assert.equal(restored.budget.maxTotalTokens,100);assert.equal(restored.usage.metrics.totalTokens.value,10);assert.equal(restored.usage.metrics.grossTotalTokens.value,12645);assert.equal(restored.usage.metrics.costUsd.value,null);
 await page.reload();await openChatAdvancedPage(page,'Model & tools');await panel.getByRole('button',{name:'Inspect task usage'}).click();await expect(panel.getByRole('status')).toHaveText('Budget paused · 1 recorded model calls');
 const edited=await post('/fixture/agent');assert.equal(edited.result.budget.maxTotalTokens,20000);assert.equal(edited.draft,'Keep this draft');await panel.getByRole('button',{name:'Inspect task usage'}).click();await expect(panel.getByRole('status')).toHaveText('Budget active · 1 recorded model calls');
 await panel.getByRole('button',{name:'Inspect provider quota'}).click();await expect(panel.getByText('No provider quota capability is available.')).toBeVisible();
 await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/unified-capacity-mobile.png'});
 assert.deepEqual(errors,[]);console.log('Capacity browser passed: usage read, real provider guard, persistent budget pause, restart receipts, agent edit/draft preservation, explicit quota absence, mobile.');
}finally{await browser?.close();fixture.kill()}
