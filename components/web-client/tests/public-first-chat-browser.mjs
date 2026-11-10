// Empty production host, packaged UI, real API/storage and deterministic runtime.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py','--chat-controls','--first-use'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(15000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const inspect=async()=>await(await page.request.get(url+'/fixture')).json();
 await page.goto(url);
 await page.getByRole('button',{name:'Start a chat',exact:true}).click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(composer).toBeVisible();
 assert.equal((await inspect()).sent.length,0);
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  const starters=page.locator('.a-chat-starters');
  await expect(starters).toBeVisible();
  assert.equal(await starters.getByRole('button').count(),3);
  assert.ok(await starters.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Starter choices must not overflow');
  await page.screenshot({path:`/tmp/first-chat-starters-${width}.png`});
 }
 await page.setViewportSize({width:1280,height:900});
 const emailStarter=page.getByRole('button',{name:'Draft an email',exact:false});
 await emailStarter.focus();await emailStarter.press('Enter');
 await expect(composer).toHaveDraft('Help me draft an email. Ask me who it is for, what I want to say, and the tone I would like.');
 await expect(composer).toBeFocused();
 assert.equal((await inspect()).sent.length,0,'Choosing a starter must not send a message');
 await expect(page.getByRole('button',{name:'Summarize a file',exact:false})).toHaveCount(0);
 const savedDraft=page.waitForResponse(response=>{if(response.request().method()!=='POST'||!response.url().includes('/api/actions'))return false;const body=response.request().postDataJSON();return body.action==='view.update'&&body.args?.patch?.draft==='Please keep my first message'&&response.ok()});
 await composer.fill('Please keep my first message');
 await expect(composer).toHaveDraft('Please keep my first message');
 await savedDraft;
 await page.reload();await expect(composer).toHaveDraft('Please keep my first message');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'first-note.txt',mimeType:'text/plain',buffer:Buffer.from('Synthetic first attachment')});
 await expect(page.getByRole('button',{name:'Remove first-note.txt'})).toBeVisible();
 let rejected=0;
 await page.route('**/api/actions',async route=>{
  if(route.request().method()==='POST'){
   const original=route.request().postDataJSON();
   const body=original.action==='shell.command'?original.args:original;
   if(body.action==='session.create'&&rejected===0){rejected++;return route.fulfill({status:409,json:{accepted:false,error:'Fixture: workspace temporarily unavailable'}})}
  }
  return route.continue();
 });
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Fixture: workspace temporarily unavailable',{exact:true}).first()).toBeVisible();
 assert.equal((await inspect()).sent.length,0);
 await page.getByRole('button',{name:'Retry',exact:true}).click();
 await expect(page.getByText('Synthetic first response',{exact:true})).toBeVisible();
 let result=await inspect();
 assert.equal(rejected,1);assert.equal(result.sent.length,1);
 assert.equal(result.sent[0].text,'Please keep my first message');
 assert.equal(result.sent[0].attachments.length,1);assert.equal(result.sent[0].attachments[0].name,'first-note.txt');
 await page.reload();await expect(page.getByText('Synthetic first response',{exact:true})).toBeVisible();
 assert.equal((await inspect()).sent.length,1,'Reload must not replay the recovered submission');
 await expect(composer).toHaveDraft('');
 assert.deepEqual(errors,[]);
 console.log('First chat passed: desktop/mobile starters, keyboard choice without sending, empty host, persisted draft, attachment, failed creation, explicit retry, exactly one delivery, saved response and reload without replay.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
