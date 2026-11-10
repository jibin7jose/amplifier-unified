import {showFixtureConversation} from './browser-chat-pages.mjs';
// Discovered chat names arrive through the real background loop and state stream.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/imported_naming_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const check=async()=>await (await page.request.get(ready.url+'/fixture/check')).json();
 await page.goto(ready.url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navPinned:true,navExpanded:true}}));
 await expect.poll(async()=>(await check()).calls,{timeout:25000}).toBe(1);
 assert.equal((await check()).historyLoaded,false);
 await page.request.post(ready.url+'/fixture/finish');
 await expect(page.getByRole('button',{name:'Quarterly report summary',exact:true}).first()).toBeVisible({timeout:15000});
 const result=await check();assert.deepEqual(result,{calls:1,title:'Quarterly report summary',historyLoaded:false,unchanged:true,cleanPrompt:true});
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await expect(page.getByRole('button',{name:'Quarterly report summary',exact:true}).first()).toBeVisible();
 assert.equal((await check()).calls,1);assert.deepEqual(errors,[]);
 const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/imported-naming-browser';await mkdir(out,{recursive:true});await page.screenshot({path:out+'/named.png'});
 console.log(JSON.stringify({status:'passed',checks:result,screenshots:out}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
