import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',()=>reject(Error('Fixture exited')));fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1500,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await action('session.create',{});
 const row=(await action('canvas.apps.create',{title:'Live sketch',manifest:{version:1,stateSchema:{type:'object'}},initialState:{strokes:Array.from({length:7},(_,i)=>({points:[[i,0],[i,1]]})),note:''},content:`<h1>Live sketch</h1><canvas id="drawing" style="width:400px;height:240px"></canvas><label>Sketch note<input id="note"></label><script>
 const draft=canvasApp.createDraft({delay:5000});canvasApp.observeCanvas(document.querySelector('canvas'),({context:c,width:w,height:h})=>{c.fillStyle='#fff';c.fillRect(0,0,w,h);c.strokeStyle='#246';for(let n=0;n<7;n++){c.beginPath();c.moveTo(n*40+10,10);c.lineTo(n*40+10,200);c.stroke()}});
 document.querySelector('input').oninput=e=>draft.update({note:e.target.value});
 </script>`})).result;
 const frame=()=>page.locator('[data-canvas-view="primary"]').frameLocator('iframe').frameLocator('iframe');
 await frame().getByRole('heading',{name:'Live sketch'}).waitFor();
 await frame().getByLabel('Sketch note').fill('Saved before the question');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('What is in the sketch?');await page.getByRole('button',{name:'Send message',exact:true}).click();
 const sent=()=>page.request.get(url+'/fixture').then(r=>r.json()).then(r=>r.sent);
 await expect.poll(async()=>(await sent()).length).toBe(1);
 const first=(await sent())[0].surfaceContext.surfaces[0];assert.equal(first.facts.note,'Saved before the question');assert.equal(first.facts.strokes.count,7);assert.equal(first.pendingLocalEdits,false);assert.equal(first.image.available,true);
 // Observation updates do not call the model. A rejected checkpoint retains the edit.
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='canvas.apps.state')return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Synthetic save conflict'})});await route.continue()});
 await frame().getByLabel('Sketch note').fill('Unsaved work survives');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('And now?');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(async()=>(await sent()).length).toBe(2);
 const pending=(await sent())[1].surfaceContext.surfaces[0];assert.equal(pending.pendingLocalEdits,true);assert.equal(pending.facts.note,'Saved before the question');
 await expect(frame().getByLabel('Sketch note')).toHaveValue('Unsaved work survives');
 const inspected=(await action('canvas.apps.inspect',{id:row.id})).result;assert.ok(inspected.views.some(v=>v.dirty));assert.equal((await sent()).length,2);assert.deepEqual(errors,[]);
 console.log('Live context acceptance passed: seven strokes, image available, send checkpoint, failed-save preservation, no edit-triggered model work');
}finally{await browser?.close();fixture.kill('SIGTERM')}
