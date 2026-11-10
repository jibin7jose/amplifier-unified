import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+fixtureLog)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+fixtureLog))})});
const base=`http://127.0.0.1:${port}`;
const headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>(await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)})).json();
const identities=await control({op:'heavy',active:true,messages:520,otherMessages:549,chars:8000,nodes:400,nodeBytes:10000});
assert.ok(identities.alpha);const initial=await fetch(base+'/api/state',{headers});assert.equal(initial.status,200);const body=await initial.text();assert.ok(body.length<950000);
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const start=Date.now();await page.goto(base);await page.locator('[data-message-id]').first().waitFor();const shellMs=Date.now()-start;
 assert.equal(await page.locator('[data-message-id]').count(),60);
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-message-id]').length>=120);
 const first=page.locator('[data-message-id]').first();const before=await first.innerText();assert.ok(before.includes('Show full text'));
 await first.getByRole('button',{name:'Show full text',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-message-id] p')?.textContent.length>5000);assert.ok((await first.innerText()).length>before.length);
 await page.locator('.a-execution-turn-line').first().click();
 await page.getByRole('button',{name:'Load earlier steps',exact:true}).click();await page.locator('.a-execution-node').first().waitFor();assert.equal(await page.locator('.a-execution-node').count(),200);
 const node=page.locator('.a-execution-node').first();await node.locator('.a-execution-line').first().click();await node.getByRole('button',{name:'Show all 10,000 characters',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.a-execution-node .a-execution-body')?.textContent.includes('x'.repeat(10000)));assert.ok((await node.innerText()).length>10000);
 // New live revisions preserve explicitly requested earlier history.
 const retained=await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>row.dataset.messageId));
 await page.evaluate(async()=>window.amplifier.dispatch('view.update',{patch:{notice:'fixture update'}}));
 const afterRefresh=await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>row.dataset.messageId));
 assert.ok(retained.every(id=>afterRefresh.includes(id)));assert.ok(afterRefresh.length>=120);
 await page.screenshot({path:'/tmp/amplifier-browser-detail.png',fullPage:false});
 // Exercise packaged build identity in real feedback UI; intercept submission.
 await control({op:'reset'});await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'feedback'}}));
 await page.getByLabel('Title',{exact:true}).fill('Fixture report - never posted');await page.getByLabel('Details',{exact:true}).fill('Synthetic browser test only.');
 assert.equal(await page.getByLabel('Include reproduction diagnostics').isChecked(),true);
 await page.locator('[data-part="feedback-diagnostics"] summary').click();
 await page.screenshot({path:'/tmp/amplifier-feedback-diagnostics.png',fullPage:false});
 let submitted;
 await page.route('**/api/actions',route=>{const value=route.request().method()==='POST'?route.request().postDataJSON():null;if(value?.action==='feedback.submit'){submitted=value.args;return route.fulfill({json:{accepted:true}})}return route.continue()});
 await page.getByRole('dialog').getByRole('button',{name:'Send feedback',exact:true}).click();
 for(let i=0;i<100&&!submitted;i++)await new Promise(resolve=>setTimeout(resolve,10));assert.ok(submitted);
 const facts=await (await fetch(base+'/build.json',{headers})).json();assert.equal(submitted.deviceDiagnostics.frontendVersion,facts.version);assert.equal(submitted.deviceDiagnostics.frontendBuild,facts.id);assert.equal(submitted.includeDiagnostics,true);
 assert.equal(submitted.deviceDiagnostics.browser,'Chrome');assert.equal(submitted.deviceDiagnostics.width,1400);
 assert.deepEqual(errors,[]);
 const result={bytes:body.length,shellMs,initialMessages:60,loadedMessages:afterRefresh.length,loadedNodes:200,fullTextVerified:true,feedbackDiagnosticsVerified:true};await writeFile('/tmp/amplifier-browser-detail-results.json',JSON.stringify(result,null,2));console.log(result);
}finally{await browser.close();fixture.kill()}
