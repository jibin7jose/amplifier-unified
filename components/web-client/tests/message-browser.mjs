import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const forkRequests=[],editRequests=[];page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const body=request.postDataJSON();if(body.action==='session.fork')forkRequests.push(body);if(body.action==='message.edit')editRequests.push(body)}});
const source=async()=>{const response=await page.request.get('http://127.0.0.1:8958/fixture/message-source');assert.equal(response.status(),200);return response.json()};
const state=()=>page.evaluate(()=>window.amplifier.getState());
const idle=()=>page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId)?.status==='idle'});
async function send(text){await page.getByRole('textbox',{name:'Message Amplifier'}).fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();await page.waitForFunction(text=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.status==='idle'&&c.messages.at(-1)?.role==='assistant'&&c.messages.filter(m=>m.role==='user').at(-1)?.text===text},text)}
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 await send('First question');await send('Second question');
 const original=(await state()).selectedSessionId;
 // Declared prerequisites only: actual fork/edit controls below perform the
 // tested transitions. Inspect raw saved rows/pins independently of the UI.
 await page.evaluate(async id=>{await window.amplifier.dispatch('session.rename',{id,title:'Preserve original title'});await window.amplifier.dispatch('session.pin',{id,pinned:true})},original);
 const originalSource=await source(),originalRow=originalSource.sessions.find(row=>row.id===original);
 assert.ok(originalSource.pins.includes(original));assert.equal(originalRow.title,'Preserve original title');
 const markdown=(await state()).sessions.find(s=>s.id===original).messages.at(-1).text;
 await page.locator('.a-assistant').last().getByRole('button',{name:'Copy message as Markdown'}).click();await page.getByText('Copied Markdown',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),markdown);assert.match(markdown,/\*\*Markdown\*\*/);
 const bounded=(await state()).sessions.find(s=>s.id===original);
 assert.equal(bounded.messageWindow.mode,'cursor');assert.equal(bounded.sharedHistoryUserTurnOffset,null);
 assert.equal(await page.getByRole('button',{name:'Fork conversation after this turn',exact:true}).count(),2);
 await page.locator('.a-assistant').first().getByRole('button',{name:'Fork conversation after this turn',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId!==id,original);
 assert.equal(forkRequests.length,1);assert.deepEqual(forkRequests[0].args,{id:original,afterMessageId:bounded.messages.find(m=>m.role==='user').id});
 let s=await state(),fork=s.sessions.find(x=>x.id===s.selectedSessionId);assert.equal(fork.messages.length,2);assert.equal(fork.parentId,original);assert.equal(fork.messages[0].text,'First question');
 assert.deepEqual((await source()).sessions.find(row=>row.id===original),originalRow);assert.deepEqual((await source()).pins,originalSource.pins);
 await page.getByRole('button',{name:'Open original chat',exact:true}).click();await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,original);
 await page.locator('.a-user').first().getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message',exact:true}).fill('Edited first question');
 await page.getByLabel('Create a separate chat (fork)').check();
 await expect(page.locator('.a-message-editor')).toContainText('This original chat and its pin stay unchanged.');
 await page.getByRole('button',{name:'Create edited chat',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId!==id,original);await idle();
 s=await state();const edited=s.sessions.find(x=>x.id===s.selectedSessionId);assert.equal(edited.messages.length,2);assert.equal(edited.messages[0].text,'Edited first question');assert.equal(edited.editOrigin.sessionId,original);
 const retained=await page.evaluate(async id=>(await (await fetch('/api/state?sessionId='+encodeURIComponent(id))).json()).sessions.find(row=>row.id===id),original);assert.equal(retained.messages.length,4);assert.equal(await page.getByText('Second question',{exact:true}).count(),0);
 assert.deepEqual((await source()).sessions.find(row=>row.id===original),originalRow);assert.deepEqual((await source()).pins,originalSource.pins);
 await page.getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message',exact:true}).fill('');assert.ok(await page.getByRole('button',{name:'Save & regenerate',exact:true}).isDisabled());await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.screenshot({path:'/tmp/amplifier-message-actions.png'});
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Edit message',exact:true}).click();await page.getByRole('textbox',{name:'Edit your message',exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));await page.screenshot({path:'/tmp/amplifier-message-edit-mobile.png'});
 await page.getByRole('button',{name:'Cancel',exact:true}).click();await page.setViewportSize({width:1280,height:900});
 await page.getByRole('button',{name:'Open original chat',exact:true}).click();await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,original);
 await page.locator('.a-user').first().getByRole('button',{name:'Edit message',exact:true}).click();
 const editor=page.locator('.a-message-editor');await expect(page.getByLabel('Create a separate chat (fork)')).not.toBeChecked();
 await expect(editor).toContainText('Continue in this chat, keeping its name, workspace and pin.');await expect(editor).toContainText('Later messages leave the active conversation; saved event history and earlier tool effects remain.');
 await editor.getByRole('textbox',{name:'Edit your message',exact:true}).fill('One accepted current edit');
 let held,received;const arrival=new Promise(resolve=>received=resolve);await page.route('**/api/actions',route=>{if(route.request().postDataJSON()?.action==='message.edit'){held=route;received();return}return route.continue()});
 const beforeEdit=await source(),beforeRequests=editRequests.length;
 await editor.getByRole('button',{name:'Save & regenerate',exact:true}).focus();await page.keyboard.press('Enter');await page.keyboard.press('Enter');await arrival;
 await expect(editor.getByRole('textbox',{name:'Edit your message',exact:true})).toHaveJSProperty('readOnly',true);await expect(editor.locator('button[type="submit"]')).toBeDisabled();await expect(editor.locator('button[type="submit"]')).toHaveText('Starting…');assert.equal(editRequests.length,beforeRequests+1);assert.deepEqual(await source(),beforeEdit);
 await held.continue();await expect(editor).toHaveCount(0);await idle();
 const afterEdit=await source(),current=afterEdit.sessions.find(row=>row.id===original);
 assert.equal(afterEdit.edits.length,beforeEdit.edits.length+1,'one actual runtime edit admission');
 assert.equal(afterEdit.sessions.length,beforeEdit.sessions.length);assert.equal(current.title,originalRow.title);assert.equal(current.workspaceId,originalRow.workspaceId);assert.deepEqual(afterEdit.pins,originalSource.pins);assert.equal(current.messages.length,2);assert.equal(current.messages[0].text,'One accepted current edit');assert.equal(afterEdit.sends.length,beforeEdit.sends.length+1);assert.equal(afterEdit.sends.at(-1).sessionId,original);assert.equal(editRequests.length,beforeRequests+1);assert.equal((await state()).selectedSessionId,original);
 assert.deepEqual(errors,[]);console.log('Message controls verified: raw Markdown clipboard, per-turn fork boundaries, original preservation, edit/regenerate, empty edit, cancel, mobile layout.');
}finally{try{await browser.close()}finally{fixture.kill()}}
