import {readComposerDraft} from './composer-test-helpers.mjs';
import {openSettingsDialog} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/live_clients_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  const timeout=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url)}}catch{}}});
 });
 const headers={Authorization:'Bearer fixture-browser-control-token'};
 const inspect=async()=>await (await fetch(url+'/fixture',{headers})).json();
 const finish=async()=>await fetch(url+'/fixture/finish',{method:'POST',headers});
 const {first,second}=await inspect();
 browser=await chromium.launch({headless:true});
 const contexts=await Promise.all([browser.newContext({extraHTTPHeaders:headers}),browser.newContext({extraHTTPHeaders:headers})]);
 const [a,b]=await Promise.all(contexts.map(context=>context.newPage()));const errors=[];
 for(const page of [a,b])page.on('pageerror',error=>errors.push(error.message));
 await Promise.all([a.goto(url),b.goto(url)]);
 for(const page of [a,b])await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 const act=(page,action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const selected=page=>page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const composer=page=>page.getByRole('textbox',{name:'Message Amplifier'});
 assert.notEqual(await a.evaluate(()=>window.amplifier.getState().client.id),await b.evaluate(()=>window.amplifier.getState().client.id));
 await act(a,'session.select',{id:first});await act(b,'session.select',{id:first});
 await composer(a).fill('Draft belonging to A');await composer(b).fill('Draft belonging to B');
 await openSettingsDialog(b);
 await b.getByRole('heading',{name:'Settings',exact:true}).waitFor();
 assert.equal(await a.locator('[role=dialog]').count(),0);
 await b.getByRole('button',{name:'Close panel',exact:true}).click();
 await composer(a).fill('Shared input from A');
 await a.getByRole('button',{name:'Send message',exact:true}).click();
 for(const page of [a,b])await page.getByText('A live partial response',{exact:true}).waitFor();
 assert.equal(await readComposerDraft(composer(b)),'Draft belonging to B');
 await act(b,'session.select',{id:second});await composer(b).fill('Second conversation draft');
 assert.equal(await selected(a),first);assert.equal(await selected(b),second);
 await contexts[0].setOffline(true);
 await finish();
 const restored=await act(b,'session.select',{id:first});
 assert.equal(restored.accepted,true);assert.equal(restored.state,undefined);
 const restoredClientId=await b.evaluate(()=>window.amplifier.getState().client.id);
 const saved=await b.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':restoredClientId}});assert.ok(saved.ok());
 const savedState=await saved.json();assert.equal(savedState.selectedSessionId,first);
 assert.equal(savedState.view.draft,'Draft belonging to B');
 await b.getByText('Finished: Shared input from A',{exact:true}).waitFor();
 // The action receipt can precede React's composer synchronization effect.
 await expect(composer(b)).toHaveDraft('Draft belonging to B');
 await contexts[0].setOffline(false);
 await a.getByText('Finished: Shared input from A',{exact:true}).waitFor({timeout:15000});
 assert.equal((await inspect()).sent.length,1);assert.deepEqual((await inspect()).stopped,[]);
 // Reload restores a copy of this client's presentation, not another window's.
 await composer(a).fill('Survives reload');
 await a.waitForFunction(()=>window.amplifier.getState().view.draft==='Survives reload');
 await a.waitForTimeout(500);
 const before=await a.evaluate(()=>window.amplifier.getState().client.id);
 await a.reload();await a.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
 assert.notEqual(await a.evaluate(()=>window.amplifier.getState().client.id),before);
 assert.equal(await selected(a),first);assert.equal(await readComposerDraft(composer(a)),'Survives reload');
 assert.equal(await readComposerDraft(composer(b)),'Draft belonging to B');
 // An independent protocol client joins the same session and safely retries input.
 await fetch(url+'/api/clients/attach',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({clientId:'terminal-fixture',kind:'tui',protocolVersion:1})});
 const command={id:'terminal-input',action:'conversation.send',args:{text:'Shared input from terminal'}};
 for(let i=0;i<2;i++){
  const response=await fetch(`${url}/api/sessions/${first}/commands`,{method:'POST',headers:{...headers,'Content-Type':'application/json','X-Amplifier-Client':'terminal-fixture'},body:JSON.stringify(command)});
  assert.equal(response.status,200);if(i===1)assert.equal((await response.json()).duplicate,true);
 }
 for(const page of [a,b])await page.getByText('Shared input from terminal',{exact:true}).waitFor();
 await finish();for(const page of [a,b])await page.getByText('Finished: Shared input from terminal',{exact:true}).waitFor();
 assert.equal((await inspect()).sent.length,2);assert.deepEqual((await inspect()).stopped,[]);
 assert.equal(await readComposerDraft(composer(a)),'Survives reload');assert.equal(await readComposerDraft(composer(b)),'Draft belonging to B');
 assert.deepEqual(errors,[]);
 console.log('Live clients passed: independent selections/drafts/panels; shared input and partial/final replies; offline reconnect; reload; terminal protocol command retry; no work stopped on disconnect.');
}finally{await browser?.close();fixture.kill();}
