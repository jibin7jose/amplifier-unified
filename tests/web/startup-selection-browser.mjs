import {fixturePython} from './fixture-python.mjs';
// Real host/transport with a gated, failing worker. No model calls.
import {showComposerOption} from './composer-options-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls','--startup-failure'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const bundle=page.getByLabel('Registered root bundles',{exact:true});
 const bundleLabel=async()=>{await model.click();await showComposerOption(page,'bundle');await expect(bundle.locator('option:checked')).toHaveText('Work');const label=await bundle.locator('option:checked').innerText();await page.getByRole('button',{name:'Close chat options',exact:true}).click();return label};
 await expect(model).toContainText('first');
 const before={model:await model.innerText(),bundle:await bundleLabel()};
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Preserve this unsent request');await composer.press('Enter');
 await expect.poll(async()=>(await state()).selectedSessionId).toBeTruthy();
 await expect(model).toHaveText(before.model);
 assert.equal(await bundleLabel(),before.bundle);
 let s=await state(),session=s.sessions.find(row=>row.id===s.selectedSessionId);
 assert.ok(!session.selection?.model,'An inherited model must not become an explicit pin');
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,0);
 await page.request.post(url+'/fixture/fail-startup');
 await expect(page.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
 await expect(model).toHaveText(before.model);assert.equal(await bundleLabel(),before.bundle);
 const id=session.id;
 await page.reload();
 await expect(model).toHaveText(before.model);assert.equal(await bundleLabel(),before.bundle);
 await expect(page.getByRole('button',{name:'Retry',exact:true})).toBeVisible();
 s=await state();assert.equal(s.selectedSessionId,id);
 assert.equal(s.sessions.find(row=>row.id===id).messages.filter(row=>row.role==='user').length,1);
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,0);
 // A real runtime report must supersede the retained draft label.
 await page.request.post(url+'/fixture/report-selection',{data:{sessionId:id}});
 await expect(model).toContainText('reported-model');
 await expect(model).not.toContainText('first');
 assert.deepEqual(errors,[]);
 console.log('Startup selection browser passed: inherited model and bundle retained during start, failure and reload; saved request not replayed.');
}finally{await browser?.close();fixture.kill()}
