import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';
const fixture = spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python', import.meta.url))),
 [fileURLToPath(new URL('../../tests/fixtures/companion_ui_server.py', import.meta.url))],
 {stdio:['ignore','pipe','inherit']});
let browser;
try {
 const ready = await new Promise((resolve,reject) => {
  let text=''; const timeout=setTimeout(()=>reject(Error('Fixture timeout')),15000);
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{
   const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value)}
  }catch{}});
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exit '+code))});
 });
 browser = await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'
  ? ['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage();
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(ready.url);
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const result=await page.evaluate(async()=>{
  const state=window.amplifier.getState();
  const headers={'X-Amplifier-Client':state.client.id};
  const get=async url=>{const r=await fetch(url,{headers});if(!r.ok)throw Error(await r.text());return r.json()};
  await window.amplifier.dispatch('session.markUnread',{id:state.selectedSessionId});
  const summary=await get('/api/activity');
  const sessions=await get('/api/sessions?view=observation');
  const notices=await get('/api/attention');
  const notice=notices.items.find(i=>i.kind==='manual-unread'&&i.target.sessionId===state.selectedSessionId);
  if(!notice)throw Error('Missing web-created unread marker');
  await window.amplifier.dispatch('attention.read',{ids:[notice.id],fingerprints:{[notice.id]:notice.fingerprint}});
  const reviewed=await get('/api/attention');
  return {summary,sessions,notice,reviewed,selected:window.amplifier.getState().selectedSessionId,before:state.selectedSessionId};
 });
 assert.equal(result.selected,result.before);
 assert.equal(result.summary.status,'ready');
 assert.ok(result.summary.attentionCount>=1);
 assert.ok(result.sessions.items.length>=2);
 assert.equal(result.notice.requiresAction,false);
 assert.ok(result.reviewed.items.every(i=>i.id!==result.notice.id));
 assert.ok(result.sessions.items.every(i=>!('messages' in i)));
 assert.deepEqual(errors,[]);
 console.log('Generic web observations passed: shared unread/read fingerprints, lightweight catalog/counts, selection preserved.');
}finally{await browser?.close();fixture.kill()}
