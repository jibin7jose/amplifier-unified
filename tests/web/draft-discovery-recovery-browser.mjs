// Production UI/host, synthetic providers; hold one read acknowledgement to
// reproduce the stalled new-chat controls without slowing the host itself.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--disable-gpu']});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let reads=0;const held=new Promise(resolve=>{release=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().method()==='POST'?route.request().postDataJSON():{};
  if(body.action==='configuration.defaults'&&++reads===1)await held;
  await route.continue();
 });
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 const model=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 await expect.poll(()=>reads).toBe(1);
 await expect(model).toContainText('Loading model');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep this draft while settings recover');
 // The pending default read must not prevent later draft writes reaching host.
 await expect.poll(async()=>{
  const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
  const response=await page.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':clientId}});
  return (await response.json()).view?.draft;
 }).toBe('Keep this draft while settings recover');
 await model.click();
 const retry=page.getByRole('button',{name:'Retry model settings',exact:true});
 await expect(retry).toBeVisible({timeout:20000});
 await retry.click();
 await expect(model).toContainText('first');
 assert.ok(reads>=2);
 await expect(composer).toHaveDraft('Keep this draft while settings recover');
 release();
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.screenshot({path:'../evidence/recovered-draft.png'});
 const inspection=await(await page.request.get(url+'/fixture')).json();
 assert.deepEqual(inspection.started,[]);assert.deepEqual(inspection.sent,[]);
 assert.deepEqual(errors,[]);
 console.log('Stalled default receipt: later draft persisted; delayed state and explicit retry recovered the model without reload; draft preserved, zero chats/workers/model calls.');
}finally{release?.();await browser?.close();fixture.kill()}
