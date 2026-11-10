import {fixturePython} from './fixture-python.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 // Hold only the composer debounce to deterministically model two edits inside220ms.
 await page.addInitScript(()=>{
  const nativeSet=window.setTimeout.bind(window),nativeClear=window.clearTimeout.bind(window),pending=new Map();
  window.setTimeout=(fn,delay,...args)=>{if(delay!==220)return nativeSet(fn,delay,...args);const id=nativeSet(()=>{},60000);pending.set(id,()=>fn(...args));return id};
  window.clearTimeout=id=>{pending.delete(id);nativeClear(id)};
  window.flushComposerDebounce=()=>{const entries=[...pending];pending.clear();for(const [id,fn] of entries){nativeClear(id);fn()}return entries.length};
 });
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await act('session.create');const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('session.create');const second=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 // Compact action receipts synchronize through SSE; saved drafts are read from
 // the authoritative state endpoint under the same client identity.
 const selectSaved=async id=>{
  const receipt=await act('session.select',{id});assert.equal(receipt.accepted,true);assert.equal(receipt.state,undefined);
  const clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
  const response=await page.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':clientId}});assert.ok(response.ok());
  const state=await response.json();assert.equal(state.selectedSessionId,id);return state.view.draft;
 };
 await act('session.select',{id:first});await composer.fill('Unsent draft in first chat');
 const firstTyped=await readComposerDraft(composer);
 await act('session.select',{id:second});await composer.fill('Unsent draft in second chat');
 const secondTyped=await readComposerDraft(composer);
 const completed=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().method()==='POST'&&response.request().postDataJSON().action==='view.update'&&response.request().postDataJSON().args.patch?.draft==='Unsent draft in second chat');
 const pending=await page.evaluate(()=>window.flushComposerDebounce());await completed;
 const restored=await selectSaved(first);
 assert.equal(restored,firstTyped);
 await expect(composer).toHaveDraft(firstTyped);
 const secondRestored=await selectSaved(second);
 assert.equal(secondRestored,secondTyped);
 await expect(composer).toHaveDraft(secondTyped);
 // Sending the second chat's existing draft can also cancel a staged first draft.
 await act('session.select',{id:first});await composer.fill('First chat edit retained through another chat send');
 const beforeSend=await readComposerDraft(composer);
 await act('session.select',{id:second});await expect(composer).toHaveDraft(secondTyped);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.getByText('Synthetic first response',{exact:true}).waitFor();
 await expect(composer).toHaveDraft('');
 const afterSend=await selectSaved(first);
 assert.equal(afterSend,beforeSend);
 await expect(composer).toHaveDraft(beforeSend);
 assert.equal(await page.evaluate(()=>window.flushComposerDebounce()),0);
 const sentChat=await selectSaved(second);
 assert.equal(sentChat,'','An old debounce must not resurrect a sent draft');
 await expect(composer).toHaveDraft('');
 const sent=await (await page.request.get(url+'/fixture')).json();
 assert.deepEqual(sent.sent,[{sessionId:second,text:secondTyped}]);
 console.log(JSON.stringify({passed:true,pendingDebounces:pending,firstTyped,firstSaved:restored,secondTyped,secondSaved:secondRestored,beforeSend,afterSendSaved:afterSend,checks:['rapid chat edit retains both authoritative drafts','send in another chat preserves staged draft','send stays bound to intended chat exactly once']}));
}finally{await browser?.close();fixture.kill()}
