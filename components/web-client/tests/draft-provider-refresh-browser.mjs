// Packaged UI and real state transport; isolated host with a synthetic runtime.
import {showComposerOption} from './composer-options-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const preference=async model=>assert.equal((await page.request.post(url+'/fixture/provider-preference',{data:{model}})).status(),200);
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 await expect(model).toContainText('first');
 await preference('chosen-model');
 await expect(model).toContainText('chosen-model');
 assert.equal((await state()).configurationRevision,1);
 assert.equal((await state()).sessions.length,0);
 const before=await(await page.request.get(url+'/fixture')).json();
 assert.deepEqual(before.started,[]);assert.deepEqual(before.sent,[]);
 // An explicit chat choice survives subsequent changes to the default.
 await model.click();await showComposerOption(page,'model');await page.locator('select#chat-model').selectOption('first');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await preference('chosen-model');
 await expect.poll(async()=>(await state()).configurationRevision).toBe(2);
 await expect(model).toContainText('first');
 assert.equal((await state()).view.newSessionDraft.selection.model,'first');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Use my explicit choice');await composer.press('Enter');
 await expect.poll(async()=>(await(await page.request.get(url+'/fixture')).json()).sent.length).toBe(1);
 const after=await(await page.request.get(url+'/fixture')).json();
 assert.equal(after.sent[0].selection.model,'first');
 assert.deepEqual(errors,[]);
 console.log('Draft provider refresh browser passed: default refresh without reload, no premature runtime, explicit choice preserved on first send.');
}finally{await browser?.close();fixture.kill()}
