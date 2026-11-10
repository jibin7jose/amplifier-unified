import {fixturePython} from './fixture-python.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/empty_host_ui_server.py','--retention'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}});fixture.once('error',reject)});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();

 await openSettingsPage(page,'ready-conversations');
 const count=page.getByLabel('Conversations to keep ready',{exact:true}),hours=page.getByLabel('Hours to keep an idle conversation ready',{exact:true});
 await expect(count).toHaveValue('32');await expect(hours).toHaveValue('12');
 await count.fill('100');await hours.fill('336');
 await count.focus(); // Commit hours through its normal blur autosave.
 await expect(page.getByRole('checkbox',{name:'Prepare a conversation in the background when I select it'})).toHaveCount(0);
 await page.waitForFunction(()=>window.amplifier.getState().runtime.retention.idle_timeout_hours===336);
 await page.waitForFunction(()=>window.amplifier.getState().runtime.retention.max_warm_workers===100);
 const inspect=await page.request.get(url+'/fixture').then(response=>response.json());
 assert.deepEqual(inspect.retention,{max_warm_workers:100,idle_timeout_hours:336,max_background_starts:2});
 assert.equal(inspect.workerCount,0);
 await page.reload();await expect(count).toHaveValue('100');await expect(hours).toHaveValue('336');
 await page.screenshot({path:'/tmp/amplifier-worker-retention-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.locator('.a-dialog').evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:'/tmp/amplifier-worker-retention-mobile.png',animations:'disabled'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,sharedAction:true,appliedToRunningManager:true,reloadPreserved:true,noWorkersStarted:true,mobileNoOverflow:true}));
}finally{await browser?.close();fixture.kill()}
