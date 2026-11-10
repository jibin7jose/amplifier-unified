import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
// Real HTTP/SSE and saved JSONL, with a synthetic runtime and temporary home.
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',['-u',root+'/tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','pipe']});
let browser,log='';fixture.stderr.on('data',chunk=>log+=chunk);
try{
 const port=await new Promise((resolve,reject)=>{
  let output='';const timeout=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);
  fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timeout);resolve(JSON.parse(line).port)}});
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code+': '+log))});
 });
 const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(response.status,200);return response.json()};
 const prepared=await control({op:'native-history'});
 const native=prepared.state.sessions.find(row=>row.nativeIdentity==='paging-fixture');assert.ok(native);
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers}),errors=[],pages=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/conversation/detail'&&url.searchParams.get('part')==='messages'&&url.searchParams.has('before'))pages.push(Object.fromEntries(url.searchParams))});
 await page.goto(base);await showFixtureConversation(page);
 await expect(page.locator('[data-message-id]')).toHaveCount(100);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this draft');
 const pane=page.locator('.a-messages');
 await page.waitForFunction(()=>{const p=document.querySelector('.a-messages');return p.scrollHeight-p.scrollTop-p.clientHeight<3});
 // Keep the manual click from first causing a scroll-triggered request.
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).evaluate(button=>button.click());
 await expect(page.locator('[data-message-id]')).toHaveCount(200);
 assert.equal(pages.length,1);assert.ok(pages[0].before,'Older pages use an explicit cursor');
 // Restoration alone must not request another page; only a real reader gesture
 // authorizes it. Hold that page receipt to capture the post-gesture anchor.
 await pane.evaluate(element=>element.scrollTop=60);
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(pages.length,1);
 let releasePage;const pageGate=new Promise(resolve=>releasePage=resolve);
 const holdPage=async route=>{if(new URL(route.request().url()).searchParams.has('before'))await pageGate;await route.continue()};
 await page.route('**/api/conversation/detail?*',holdPage);
 await pane.hover();await page.mouse.wheel(0,-60);
 await expect.poll(()=>pages.length).toBe(2);
 const anchor=await pane.evaluate(element=>{const first=element.querySelector('[data-message-id]');return {id:first.dataset.messageId,y:first.getBoundingClientRect().y}});
 releasePage();
 await expect(page.locator('[data-message-id]')).toHaveCount(300);
 await page.waitForTimeout(100);
 const after=await page.locator(`[data-message-id="${anchor.id}"]`).boundingBox();
 assert.ok(Math.abs(after.y-anchor.y)<3,`Scroll anchor moved ${after.y-anchor.y}px`);
 await page.unroute('**/api/conversation/detail?*',holdPage);
 assert.equal(pages.length,2);assert.notEqual(pages[1].before,pages[0].before,'Each page advances the private reader cursor');
 // A deliberately paged reader is a stable snapshot. Live status continues,
 // and the explicit latest control reveals newly accepted output without replay.
 await control({op:'live-response',id:native.id});
 await expect(page.getByText('Still writing...',{exact:true})).toBeVisible();
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(row=>row.id===window.amplifier.getState().selectedSessionId)?.status==='working');
 await expect(page.getByText('A new live response.',{exact:true})).toHaveCount(0);
 await expect(page.locator('[data-message-id]')).toHaveCount(300);
 await pane.evaluate(el=>el.scrollTop=el.scrollHeight);
 await page.waitForTimeout(150);
 const frozenBottom=await page.evaluate(()=>{const pane=document.querySelector('.a-messages'),r=pane.getBoundingClientRect(),latest=[...document.querySelectorAll('button')].filter(el=>/Return to latest|Jump to latest messages/.test(el.getAttribute('aria-label')||el.textContent));return {atBottom:pane.scrollHeight-pane.clientHeight-pane.scrollTop<3,controls:latest.map(el=>{const b=el.getBoundingClientRect();return {label:el.getAttribute('aria-label')||el.textContent,inViewport:b.width>0&&b.bottom>r.top&&b.top<r.bottom}})}});
 console.log(JSON.stringify({frozenBottom}));
 const latest=page.getByRole('button',{name:'Jump to latest messages',exact:true});
 await expect(latest).toBeInViewport();await latest.click();
 await expect(page.getByText('A new live response.',{exact:true})).toBeVisible();
 const current=await control({op:'inspect'}),saved=current.state.sessions.find(row=>row.id===native.id);
 assert.equal(saved.sharedHistoryOffset,205,'Private browser paging does not expand the resident runtime history');
 assert.equal(saved.status,'working');assert.equal(saved.streaming,'Still writing...');
 assert.equal(saved.messages.at(-1).text,'A new live response.');
 assert.equal(current.runtimeSends,prepared.runtimeSends,'Paging must not submit work');
 assert.equal(current.runtimeStarts,prepared.runtimeStarts,'Paging must not start a runtime');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this draft');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,manualPageWhileReady:true,stableReaderWhileWorking:true,explicitLatest:true,liveResponsePreserved:true,scrollAnchorDelta:after.y-anchor.y}));
}finally{await browser?.close();fixture.kill()}
