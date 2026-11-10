import './composer-test-helpers.mjs';
// Packaged UI and a restarted real host; no personal data or provider requests.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/restored_history_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await page.addInitScript(()=>sessionStorage.setItem('amplifier.clientId','previous-browser'));
 const errors=[],actions=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
 const inspect=async()=>await(await page.request.get(url+'/fixture')).json();
 await page.goto(url);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'}),send=page.getByRole('button',{name:'Send message',exact:true});
 await expect(composer).toHaveDraft('Saved unsent draft');
 await composer.fill('New draft while the restored chat loads');
 await expect(composer).toBeEditable();await expect(send).toBeDisabled();
 assert.deepEqual((await inspect()).started,[]);assert.deepEqual((await inspect()).sent,[]);
 assert.equal((await page.request.post(url+'/fixture/release')).status(),200);
 await expect(send).toBeEnabled();
 await expect(composer).toHaveDraft('New draft while the restored chat loads');
 await expect(page.getByText('Saved CLI answer',{exact:true})).toBeVisible();
 const ready=await inspect();assert.equal(ready.originalsUnchanged,true);assert.deepEqual(ready.started,[]);assert.deepEqual(ready.sent,[]);
 assert.equal(actions.filter(row=>row.action==='session.select').length,0,'No navigation was needed to restore Send');
 await send.click();await expect(page.getByText('Synthetic first response',{exact:true})).toBeVisible();
 const sent=await inspect();assert.equal(sent.sent.length,1);assert.equal(sent.sent[0].text,'New draft while the restored chat loads');
 assert.equal(actions.filter(row=>row.action==='conversation.send').length,1);assert.deepEqual(errors,[]);
 console.log('Composer readiness passed: restored selection, editable draft during loading, automatic Send recovery, original files retained, zero model work before explicit submission, exactly one send.');
}finally{await browser?.close();fixture.kill()}
