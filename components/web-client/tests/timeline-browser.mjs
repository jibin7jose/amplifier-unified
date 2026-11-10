import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(process.env.UNIFIED_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/timeline_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const order=()=>page.locator('.a-messages').evaluate(el=>[...el.children].map(n=>n.dataset.messageId||n.dataset.groupId?.split('@')[0]||n.dataset.turnId).filter(Boolean));
const voiceWork=id=>page.locator(`[data-part="execution-group"][data-group-id^="${id}@"]`);
const expected=['voice-user','voice:first','voice-ack','voice:second','voice-result'];
try{
 await page.goto('http://127.0.0.1:8958/');await showFixtureConversation(page);await voiceWork('voice:second').waitFor();assert.deepEqual(await order(),expected);
 const second=voiceWork('voice:second');assert.match(await second.innerText(),/Working/);
 const toggle=second.locator(':scope > button.a-execution-turn-line');assert.equal(await toggle.getAttribute('aria-expanded'),'false');
 assert.equal(await second.locator('[data-turn-id="voice:second"]').count(),0);
 await toggle.click();await second.locator('[data-turn-id="voice:second"]').waitFor();
 assert.deepEqual(await second.locator('[data-turn-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.turnId)),['voice:second','voice:third']);
 await second.getByText('Tool 1',{exact:true}).waitFor();
 await second.locator('[data-node-id="tool-1"] button.a-execution-action-line').first().click();
 const worker=second.getByRole('complementary',{name:'Fixture worker status'});
 await worker.getByRole('button',{name:'Fixture worker delegation details',exact:true}).click();
 assert.match(await worker.innerText(),/anthropic · child-model/);
 assert.match(await worker.innerText(),/delegation provider preferences/);
 assert.match(await worker.innerText(),/fixture-routing.*user settings/);
 // The current flat delegate stream keeps parent provenance in its canonical
 // receipt; the visible details describe the worker's selected provider.
 assert.equal(await page.evaluate(async group=>{const state=window.amplifier.getState(),query=new URLSearchParams({sessionId:state.selectedSessionId,part:'nodes',group});const response=await fetch('/api/conversation/detail?'+query);if(!response.ok)throw Error('Could not read the fixture work receipt');return (await response.json()).items.find(node=>node.id==='worker:fixture').parentProvider},await second.getAttribute('data-group-id')),'subscription');
 assert.doesNotMatch(await worker.innerText(),/tokens unavailable|cost unavailable|usage pending/);
 assert.equal(await second.locator('[data-node-id="llm:fixture"][data-owner-id="worker:fixture"]').count(),1);
 await toggle.click();assert.equal(await second.locator('.a-execution-roots').count(),0);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('A later question');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.messages.length===5&&c.status==='idle'});
 assert.deepEqual((await order()).slice(0,expected.length),expected);assert.match(await second.innerText(),/Worked for/);
 assert.equal(await toggle.getAttribute('aria-expanded'),'false');await toggle.click();await second.getByText('Tool 1',{exact:true}).waitFor();assert.deepEqual((await order()).slice(0,expected.length),expected);
 await page.reload();await voiceWork('voice:second').waitFor();assert.deepEqual((await order()).slice(0,expected.length),expected);
 await second.locator('[data-turn-id="voice:third"]').waitFor();
 assert.deepEqual(await second.locator('[data-turn-id]').evaluateAll(nodes=>nodes.map(node=>node.dataset.turnId)),['voice:second','voice:third']);
 assert.deepEqual(errors,[]);console.log('Worker routing/provider/usage disclosure stays nested; voice Work groups remain between their original messages during completion, later turns, expansion and reload; multiple turns retain order within their shared message gap.');
}finally{await browser.close();fixture.kill()}
