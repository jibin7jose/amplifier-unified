import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(process.env.UNIFIED_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/demand_ui_server.py',import.meta.url))],{stdio:'inherit'});
const stopFixture=ownFixture(fixture,{label:'Demand fixture'});
let browser,primary;
try{
 for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),reads=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/conversation/detail'))reads.push(r.url())});
 await page.goto('http://127.0.0.1:8958/');await showFixtureConversation(page);
 // Voice Work owns the disclosure; group IDs include a message-gap suffix,
 // while the input attribute retains the exact owner across its segments.
 const third=page.locator('[data-voice-work-group="true"][data-work-input-id="voice:third"]'),toggle=third.locator(':scope > button.a-execution-turn-line');await toggle.waitFor();
 assert.equal(await third.count(),1,'one disclosure belongs to this exact voice owner');
 assert.equal(reads.length,0,'collapsed groups do not read steps');
 const execution=await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId).execution});
 assert.deepEqual(execution.nodes,[]);assert.equal(execution.detailsDeferred,true);
 let reject=true;
 await page.route('**/api/conversation/detail?**',async route=>{
  if(reject&&new URL(route.request().url()).searchParams.has('group')){reject=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture detail unavailable'})})}else await route.continue();
 });
 const transcriptBefore=await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>({id:row.dataset.messageId,text:row.innerText})));
 await toggle.click();await third.getByRole('alert').waitFor();
 assert.deepEqual(await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>({id:row.dataset.messageId,text:row.innerText}))),transcriptBefore,'Work projection failure must leave the readable transcript unchanged');
 await third.getByRole('button',{name:'Retry loading work'}).click();
 await third.locator('[data-node-id="bulk-124"]').waitFor();assert.equal(await third.locator('.a-execution-node').count(),100);
 assert.equal(reads.filter(url=>new URL(url).searchParams.has('field')).length,0,'large bodies remain unloaded');
 await third.getByRole('button',{name:'Load earlier steps'}).click();await third.locator('[data-node-id="bulk-0"]').waitFor();
 assert.equal(await third.locator('.a-execution-node').count(),126);
 await third.locator('[data-node-id="bulk-0"] button.a-execution-action-line').click();
 await page.waitForFunction(()=>document.querySelector('[data-node-id="bulk-0"]')?.textContent.includes('large result '.repeat(30)));
 assert.ok(reads.some(url=>new URL(url).searchParams.get('field')==='output'));
 await toggle.click();assert.equal(await third.locator('.a-execution-node').count(),0);
 // A late response after closing must neither render nor retain hidden steps.
 await page.unroute('**/api/conversation/detail?**');let release,started;
 const gate=new Promise(resolve=>release=resolve),entered=new Promise(resolve=>started=resolve);
 await page.route('**/api/conversation/detail?**',async route=>{started();await gate;try{await route.continue()}catch{}});
 await toggle.click();await entered;await toggle.click();release();await page.waitForTimeout(200);
 assert.equal(await third.locator('.a-execution-node').count(),0);await page.unroute('**/api/conversation/detail?**');
 await toggle.click();await third.locator('[data-node-id="bulk-124"]').waitFor();assert.equal(await third.locator('.a-execution-node').count(),100,'reopening reads a bounded page');
 await third.getByRole('button',{name:'Load earlier steps'}).click();
 await third.locator('[data-node-id="bulk-0"]').waitFor();
 assert.equal(await third.locator('.a-execution-node').count(),126,'explicitly loaded older page is present before sync');
 const loadedStepIds=await third.locator('.a-execution-node').evaluateAll(nodes=>nodes.map(node=>node.dataset.nodeId));
 // Hold a delta response while two new revisions arrive. Existing DOM and
 // expanded tool details must remain mounted throughout, then catch up.
 await third.locator('[data-node-id="bulk-124"] button.a-execution-action-line').click();
 await page.evaluate(()=>{window.savedWorkRow=document.querySelector('[data-node-id="bulk-124"]')});
 let releaseSync,enteredSync,firstSync=true;
 const syncGate=new Promise(resolve=>releaseSync=resolve),syncEntered=new Promise(resolve=>enteredSync=resolve);
 await page.route('**/api/conversation/work-sync',async route=>{
  if(!firstSync){await route.continue();return}firstSync=false;
  const response=await route.fetch(),body=await response.json();
  assert.equal(body.items.length,1,'delta only includes the changed step');enteredSync();await syncGate;
  await route.fulfill({response});
 });
 const identities=await page.evaluate(async()=> (await fetch('/api/fixture/work',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'update-step'})})).json());
 await syncEntered;
 assert.equal(await third.getByText('Loading work details…',{exact:true}).count(),0);
 assert.equal(await third.locator('.a-execution-node').count(),126);
 assert.ok(await page.evaluate(()=>window.savedWorkRow.isConnected));
 await page.evaluate(async()=>fetch('/api/fixture/work',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'update-step',label:'Newest live step',phase:'running'})}));
 releaseSync();await third.getByText('Newest live step',{exact:true}).waitFor();
 assert.ok(await page.evaluate(()=>window.savedWorkRow===document.querySelector('[data-node-id="bulk-124"]')));
 assert.equal(await third.locator('[data-node-id="bulk-124"] button.a-execution-action-line').getAttribute('aria-expanded'),'true');
 assert.deepEqual(await third.locator('.a-execution-node').evaluateAll(nodes=>nodes.map(node=>node.dataset.nodeId)),loadedStepIds,'live sync retains all explicitly loaded steps in order');
 await page.unroute('**/api/conversation/work-sync');
 // Visit a second chat, then delay returning to the cached first chat.
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),identities.second);
 await page.getByText('Second chat content',{exact:true}).waitFor();
 let releaseNavigation,enteredNavigation;
 const navigationGate=new Promise(resolve=>releaseNavigation=resolve),navigationEntered=new Promise(resolve=>enteredNavigation=resolve);
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='session.select'){enteredNavigation();await navigationGate}await route.continue()});
 await page.evaluate(id=>{window.pendingNavigation=window.amplifier.dispatch('session.select',{id})},identities.first);
 await navigationEntered;await page.getByRole('status').filter({hasText:'Loading conversation…'}).waitFor();
 assert.equal(await page.locator('[data-message-id="voice-user"]').count(),0,'cached transcript is not displayed');
 assert.equal(await page.getByText('Second chat content',{exact:true}).count(),0,'previous chat is not displayed');
 releaseNavigation();await page.evaluate(()=>window.pendingNavigation);
 await page.locator('[data-message-id="voice-user"]').waitFor();await page.unroute('**/api/actions');
 // An ordinary failure offers an explicit new continuation, preserving draft.
 await page.evaluate(async()=>fetch('/api/fixture/work',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op:'fail'})}));
 const continueButton=page.getByRole('button',{name:'Continue conversation',exact:true});await continueButton.waitFor();
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep my unsent draft');
 let continuation;const continuations=[];
 await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(body?.action==='conversation.send'){continuation=body;continuations.push(body);await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Temporary fixture failure'})})}else await route.continue()});
 await continueButton.click();await page.getByText('Temporary fixture failure',{exact:true}).first().waitFor();
 assert.equal(continuation.args.preserveDraft,true);assert.equal(continuation.args.sessionId,identities.first);
 assert.match(continuation.args.text,/Check what has already completed/);
 assert.equal(await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'})),'Keep my unsent draft');
 assert.equal(await continueButton.isEnabled(),true);
 await page.waitForTimeout(1500);
 assert.equal(continuations.length,1,'No automatic continuation retry during the observed failure interval');
 await page.reload();await continueButton.waitFor();
 assert.equal(await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'})),'Keep my unsent draft');
 assert.equal(continuations.length,1,'Reload must not replay the rejected continuation');
 await page.unroute('**/api/actions');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,collapsedStepReads:0,initialNodes:execution.nodes.length,summaryBytes:JSON.stringify(execution).length,checks:['lazy steps','bounded paging','lazy bodies','error retry','collapse race','bounded reopen','delta-only live refresh','no blanking under overlapping revisions','expanded DOM retained','fresh navigation','explicit continuation and retry']}));
}catch(error){primary=error;throw error}
finally{await finishFixture({label:'Demand fixture',close:[['browser',()=>browser?.close()]],stop:stopFixture,originalError:primary})}
