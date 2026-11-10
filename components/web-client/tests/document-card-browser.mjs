import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/main_refresh_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const patch=values=>action('view.update',{patch:values});
 const panel=page.locator('#workspace-canvas'),overview=panel.getByRole('tab',{name:'Chat overview',exact:true});
 await page.goto(url);await showFixtureConversation(page);await expect(page.getByText('Lead with what someone can do here.',{exact:true})).toBeVisible();

 const identity=await page.evaluate(()=>window.amplifier.getState().canvasArtifacts.find(row=>row.title==='Website refresh brief').id);
 await action('canvas.versions.revise',{id:identity,expectedRevision:1,content:'# Revised second version'});
 await action('canvas.visibility',{open:false});
 const card=version=>page.locator('.a-document-output-open').filter({hasText:'Document · Version '+version});
 const selectCalls=[];let release;
 const gate=new Promise(resolve=>{release=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body.action==='canvas.select'){selectCalls.push(body.args);await gate}
  await route.continue();
 });
 await card(1).click();
 await expect(card(1)).toHaveAttribute('aria-busy','true');
 await card(1).dispatchEvent('click');
 assert.equal(selectCalls.length,1,'repeat activation during loading does not enqueue a second open');
 release();
 await expect(panel).toBeVisible();
 await expect(panel.getByRole('heading',{name:'A clearer first impression',exact:true})).toBeVisible();
 await expect(card(1)).not.toHaveAttribute('aria-busy','true');
 await page.unroute('**/api/actions');
 await card(2).click();
 await expect(panel.getByRole('heading',{name:'Revised second version',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.selectedVersion),2);
 await card(1).click();
 await expect(panel.getByRole('heading',{name:'A clearer first impression',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.selectedVersion),1);
 assert.deepEqual(errors,[]);
 console.log('PASS: closed Canvas direct card open, visible pending feedback, duplicate click suppression, exact version switching in open Canvas.');
}finally{await browser?.close();fixture.kill()}
