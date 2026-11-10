import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/session_health_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});let browser;
const image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9mQAAAAASUVORK5CYII=';
try{
 const {url}=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const pages=[],errors=[];
 for(let i=0;i<8;i++){
  const page=await context.newPage();pages.push(page);page.on('pageerror',error=>errors.push(error.message));
  await page.goto(url,{waitUntil:'domcontentloaded'});await showFixtureConversation(page);
  await page.getByRole('button',{name:'View error details',exact:true}).waitFor({timeout:15000});
  await expect(page.getByText('Connected',{exact:true})).toHaveCount(1);
 }
 const identities=await Promise.all(pages.map(p=>p.evaluate(()=>window.amplifier.shellClientId)));
 assert.equal(new Set(identities).size,8);
 const results=[];
 for(const page of pages){
  results.push(await page.evaluate(async()=>{const started=performance.now();const result=await fetch('/api/health',{signal:AbortSignal.timeout(3000)});await window.amplifier.dispatch('view.update',{patch:{navWidth:290}});return {status:result.status,ms:performance.now()-started}}));
 }
 await pages[0].getByRole('textbox',{name:'Message Amplifier'}).fill('Private first-window draft');
 assert.equal(await readComposerDraft(pages[7].getByRole('textbox',{name:'Message Amplifier'})),'');
 const loaded=await pages[7].evaluate(async image=>{
  await window.amplifier.dispatch('canvas.show',{kind:'image',title:'Saved test image',content:image});
  const views=(await window.amplifier.dispatch('canvas.views.inspect')).result.views;
  const target=views.find(v=>v.viewId==='primary');
  const args=Object.fromEntries(['resourceId','resourceRevision','generation'].map(k=>[k,target[k]]));args.clientId=window.amplifier.shellClientId;
  const response=await fetch('/api/canvas/views/primary/resource?'+new URLSearchParams(args),{signal:AbortSignal.timeout(3000)});
  return {status:response.status,canvas:await response.json()};
 },image);
 assert.equal(loaded.status,200);assert.equal(loaded.canvas.content,image);
 await pages[0].close();await pages[7].reload();await pages[7].getByRole('button',{name:'View error details',exact:true}).waitFor();
 const retry=await context.newPage();let first=true;
 await retry.routeWebSocket('**/api/events/ws?**',socket=>{if(first){first=false;socket.close()}else socket.connectToServer()});
 await retry.goto(url);await retry.getByRole('button',{name:'Retry connection',exact:true}).waitFor({timeout:15000});
 await retry.getByRole('button',{name:'Retry connection',exact:true}).click();
 // This fresh client arrives at Home, just like the initial eight windows.
 await showFixtureConversation(retry);
 await expect(retry.getByText('Connected',{exact:true})).toHaveCount(1);
 await retry.getByRole('button',{name:'View error details',exact:true}).waitFor();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({windows:8,privateClients:true,imageLoaded:true,reconnected:true,startupRetry:true,requests:results}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
