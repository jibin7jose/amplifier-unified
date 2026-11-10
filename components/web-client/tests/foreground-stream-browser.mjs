import {showFixtureConversation} from './browser-chat-pages.mjs';
// Real API/SSE and packaged UI; deterministic transport interruption, no models.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try {
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}});
 });
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const context=await browser.newContext({viewport:{width:1500,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await context.addInitScript(()=>{
  const Native=window.WebSocket;window.fixtureStreams=[];
  window.WebSocket=class extends Native{constructor(...args){super(...args);if(String(args[0]).includes('/api/events')){window.fixtureStreams.push(this);if(window.fixtureRejectNext){window.fixtureRejectNext=false;this.addEventListener('open',()=>this.close(),{once:true})}}}};
 });
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create');
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,draft:'Saved draft'}});
 // Use the built-in sandboxed Canvas app; this input has no saved-state handler.
 await action('canvas.apps.create',{title:'Editable artifact',manifest:{version:1,theme:'inherit',stateSchema:{type:'object'},events:{},requests:{}},content:'<!doctype html><html><body><label>Unsaved renderer edit<input type="text"></label></body></html>',initialState:{}});
 const editor=page.locator('[data-canvas-view="primary"]').frameLocator('iframe').frameLocator('iframe').getByLabel('Unsaved renderer edit');
 await editor.fill('LOCAL CANVAS EDIT');
 await expect.poll(async()=>(await action('canvas.views.inspect')).result.views.find(v=>v.viewId==='primary')?.dirty).toBe(true);
 const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
 const mutations=[];page.on('request',request=>{if(request.method()==='POST'&&request.url().includes('/api/actions')){const raw=request.postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;if(['conversation.send','message.edit','session.create','session.select'].includes(body.action))mutations.push(body)}});
 // Keep a composer save pending to prove baseline recovery preserves the overlay.
 let held;await page.route('**/api/actions',route=>{const request=route.request(),body=request.method()==='POST'?request.postDataJSON():{};if(body.action==='view.update'&&body.args?.patch?.draft==='UNSAVED COMPOSER EDIT'){held=route;return}return route.continue()});
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.fill('UNSAVED COMPOSER EDIT');await expect.poll(()=>!!held).toBe(true);
 const count=()=>page.evaluate(()=>window.fixtureStreams.length);
 const assertRecovered=async before=>{
  await expect.poll(count,{timeout:10000}).toBeGreaterThan(before);
  await page.waitForFunction(()=>window.fixtureStreams.at(-1).readyState===WebSocket.OPEN);
  await expect(composer).toHaveDraft('UNSAVED COMPOSER EDIT');await expect(editor).toHaveValue('LOCAL CANVAS EDIT');
  assert.equal(await page.evaluate(()=>window.amplifier.shellClientId),clientId,'No page reload or new client');assert.deepEqual(mutations,[]);
 };
 // A permanently CLOSED native stream cannot retry itself.
 let before=await count();await page.evaluate(()=>{window.fixtureRejectNext=true;window.fixtureStreams.at(-1).close()});
 await expect.poll(count,{timeout:15000}).toBeGreaterThan(before+1);await assertRecovered(before);
 // A foreground wake also replaces an apparently OPEN but stale connection.
 before=await count();await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await assertRecovered(before);
 // A visibility event while already visible is not a new foreground episode.
 const visibleCount=await count();await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));assert.equal(await count(),visibleCount);
 // Several wake signals from one episode must not create several transports.
 const after=await count();await page.evaluate(()=>{window.dispatchEvent(new Event('online'));document.dispatchEvent(new Event('visibilitychange'))});assert.equal(await count(),after);
 await held.continue();await page.unroute('**/api/actions');
 await expect.poll(async()=>(await page.evaluate(()=>window.amplifier.getState())).view.draft).toBe('UNSAVED COMPOSER EDIT');
 await expect(editor).toHaveValue('LOCAL CANVAS EDIT');
 // Confirm live delivery on the replacement stream (no navigation action).
 await action('session.rename',{id:await page.evaluate(()=>window.amplifier.getState().selectedSessionId),title:'Reconnected chat'});
 await expect(page).toHaveTitle('Reconnected chat - Amplifier');
 assert.deepEqual(mutations,[]);assert.deepEqual(errors,[]);
 console.log('Foreground recovery passed: CLOSED native stream retry, stale OPEN wake, coalesced wake signals, pending composer draft, dirty Canvas mount, stable client, live replacement stream and no replay.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
