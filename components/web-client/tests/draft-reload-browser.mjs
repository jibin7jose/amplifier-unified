import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage();
 // A deterministic immediate reload: no debounced composer request may leave.
 await page.addInitScript(()=>{const timer=window.setTimeout.bind(window);window.setTimeout=(fn,delay,...args)=>timer(fn,delay===220?60000:delay,...args)});
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const client=()=>page.evaluate(()=>window.amplifier.getState().client.id);
 await composer.fill('New chat draft before autosave');const firstClient=await client();
 await page.reload();await expect(composer).toHaveDraft('New chat draft before autosave');assert.notEqual(await client(),firstClient);
 await act('session.create');const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('session.create');const second=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('session.select',{id:first});await composer.fill('Draft A before autosave');
 await act('session.select',{id:second});await composer.fill('Draft B before autosave');
 await page.reload();await expect(composer).toHaveDraft('Draft B before autosave');await act('session.select',{id:first});await expect(composer).toHaveDraft('Draft A before autosave');
 // Offline pending edits are still recoverable once connectivity returns.
 await context.setOffline(true);await composer.fill('Offline edit retained');await context.setOffline(false);await page.reload();await expect(composer).toHaveDraft('Offline edit retained');
 // User clearing text must replace the older saved server draft after reload.
 await composer.fill('');await page.reload();await expect(composer).toHaveDraft('');
 // Pending and acknowledged sends never resurrect text or run again on reload.
 await page.route('**/api/actions',route=>{const body=route.request().postDataJSON();return body?.action==='view.update'&&body.args?.patch?.draft===''?route.abort('failed'):route.continue()});
 await composer.fill('Send exactly once');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();await page.unroute('**/api/actions');await page.reload();await expect(composer).toHaveDraft('');
 const sent=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(sent.sent,[{sessionId:first,text:'Send exactly once'}]);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('amplifier.pendingDrafts.v1')),null);
 // An unsaved edit left for an already removed chat cannot block bootstrap.
 await page.evaluate(()=>sessionStorage.setItem('amplifier.pendingDrafts.v1',JSON.stringify({owner:sessionStorage.getItem('amplifier.clientId'),rows:[{sessionId:'removed-chat',text:'Deleted chat draft',revision:'deleted'}]})));
 await page.reload();await expect(composer).toHaveDraft('');assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.pendingDrafts.v1')).rows[0].text),'Deleted chat draft');
 console.log(JSON.stringify({passed:true,checks:['immediate new-chat reload','new private client identity','multiple chat drafts','offline edit reload','cleared draft reload','send not restored or replayed']}));
}finally{await browser?.close();fixture.kill()}
