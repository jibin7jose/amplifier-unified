import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/browser_detail_server.py',root],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Delegate activity fixture'});
let browser,failure;
try{
 const ready=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
  const failed=error=>{cleanup();reject(error)};
  const exited=(code,signal)=>failed(Error(`Delegate activity fixture exited before readiness (${code??signal})`));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let value;try{value=JSON.parse(line)}catch{continue}
    if(value.port){cleanup();resolve(value);return}
   }
   if(pending.length>8000)failed(Error('Delegate activity fixture readiness output exceeded its bound'));
  };
  const timer=setTimeout(()=>failed(Error('Delegate activity fixture timeout')),20000);
  fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
 });
 const url=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async body=>{const r=await fetch(url+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});assert.equal(r.status,200);return r.json()};
 const {alpha}=await control({op:'heavy',messages:2,otherMessages:2,chars:50,nodes:0});
 const start=1791409080,usage={inputTokens:95000,outputTokens:1000,totalTokens:96000,cacheReadTokens:80000,cacheWriteTokens:5000,reasoningTokens:400,costUsd:1.62,costType:'reported'};
 const messages=[{id:'question',role:'user',text:'Review this report.',createdAt:start-1},{id:'answer',role:'assistant',text:'The report is ready.',createdAt:start+32}];
 const model={id:'model-one',turnId:'turn-one',sessionId:alpha,rootSessionId:alpha,anchorMessageId:'question',kind:'llm',label:'Model call',provider:'openai',model:'fixture-model',phase:'completed',startedAt:start,endedAt:start+29.4,usage};
 await control({op:'patch',sessions:{[alpha]:{messages,execution:{turns:[{id:'turn-one',anchorMessageId:'question',startedAt:start,endedAt:start+29.4,phase:'completed'}],nodes:[model]}}}});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:headers}),errors=[],payloadReads=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/[?&]field=(request|response|delegateRequest|delegateResponse)/.test(r.url()))payloadReads.push(r.url())});
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 // Delegated calls after the assistant's pause remain grouped even though
 // the worker itself belongs to the earlier message gap.
 await page.setViewportSize({width:1280,height:1000});
 const instruction='Review the source and explain the result.\n\n'+('Request detail café.\n'.repeat(150));
 const response='## Review complete\n\nThe parent-facing answer.\n\n```js\nconst verified = true;\n```\n'+('Returned detail.\n'.repeat(200));
 const delegation={id:'delegate-request',sessionId:alpha,kind:'tool',label:'delegate',toolCallId:'delegate-one',turnId:'turn-one',phase:'running',startedAt:start+1,input:JSON.stringify({agent:'review',instruction})};
 const worker={id:'worker:review',parentId:delegation.id,sessionId:'review',kind:'worker',label:'Source review',turnId:'turn-one',phase:'running',startedAt:start+1};
 const child={...model,id:'review-call',sessionId:'review',parentId:worker.id,anchorMessageId:'answer',startedAt:start+40,endedAt:start+45,model:'review-model'};
 await control({op:'patch',sessions:{[alpha]:{messages,workers:[{id:'review',status:'running'},{id:'unrelated-worker',status:'running'}],execution:{turns:[{id:'turn-one',phase:'completed',startedAt:start,endedAt:start+30}],nodes:[delegation,worker,child]}}}});
 await page.reload();
 const after=page.locator('[data-group-id="turn-one@answer"]');
 await after.locator(':scope > button').click();
 const delegate=after.locator('[data-part="work-stream"]');
 const original=page.locator('[data-group-id="turn-one@question"]');
 const originalToggle=original.locator(':scope > button');
 if(await originalToggle.getAttribute('aria-expanded')!=='true')await originalToggle.click();
 const exchange=original.locator('[data-part="work-stream"]');
 await expect(delegate).toContainText('Source review');
 await expect(delegate).toContainText('review-model');
 await expect(delegate.locator('[data-kind="llm"] > button')).toHaveAttribute('aria-expanded','false');
 await expect(delegate.getByRole('region',{name:'Request',exact:true})).toHaveCount(0);
 await expect(exchange.getByRole('region',{name:'Request',exact:true})).toContainText('Review the source');
 await expect(delegate).toContainText('Delegate is working');
 const beforeStop=await control({op:'inspect'});
 assert.deepEqual(beforeStop.workerStops,[]);assert.deepEqual(beforeStop.runtimeStops,[]);
 await delegate.getByRole('button',{name:'Stop worker',exact:true}).click();
 await expect.poll(async()=>(await control({op:'inspect'})).workerStops).toEqual([[alpha,'review']]);
 assert.deepEqual((await control({op:'inspect'})).runtimeStops,[],'Stopping this delegate must not stop its parent or another chat');
 // This fixture proves the real UI/service target. Later lifecycle records
 // below are controlled events, not an actual child-process exit claim.
 await expect(delegate.locator('[data-node-id="review-call"]')).toBeVisible();
 const reads=[];page.on('request',r=>{if(r.url().includes('/api/conversation/text'))reads.push(r.url())});
 const initialReads=reads.length;
 await expect(delegate.getByRole('button',{name:/Worker activity/})).toHaveCount(0);
 await expect(delegate.locator('[data-node-id="review-call"]')).toBeVisible();
 assert.equal(reads.length,initialReads,'Activity expansion must not read exchange bodies');
 await exchange.getByRole('button',{name:'Show full request',exact:true}).click();
 await expect(exchange.getByRole('region',{name:'Request',exact:true})).toContainText('Request detail café.');
 // Completion updates the same mounted disclosure without changing expansion.
 await control({op:'patch',sessions:{[alpha]:{messages,execution:{turns:[{id:'turn-one',phase:'completed',startedAt:start,endedAt:start+50}],nodes:[{...delegation,phase:'completed',endedAt:start+50,output:JSON.stringify({success:true,output:{response,session_id:'review'}})},{...worker,phase:'completed',endedAt:start+50},child]}}}});
 await expect(exchange.getByRole('region',{name:'Reply',exact:true})).toContainText('Review complete');
 await expect(delegate.getByRole('region',{name:'Reply',exact:true})).toHaveCount(0);
 await expect(delegate.locator('[data-node-id="review-call"] > button')).toHaveAttribute('aria-expanded','false');
 // Both expansion and Copy can read the same detail concurrently. Hold every
 // matching request, not just the first one; the preview is not the full reply.
 const reply=exchange.getByRole('region',{name:'Reply',exact:true});
 const copyReply=exchange.getByRole('button',{name:'Copy reply',exact:true});
 const detailPattern='**/api/conversation/detail?*',heldReads=[],activeReads=new Set(),routeErrors=[];
 let releaseReads,gateExpired=false,copyFailure;
 const detailGate=new Promise(resolve=>{releaseReads=resolve});
 const holdTimer=setTimeout(()=>{gateExpired=true;releaseReads()},15000);
 const holdDetail=async route=>{
  const params=new URL(route.request().url()).searchParams;
  if(params.get('field')!=='delegateResponse'||params.get('sessionId')!==alpha)return route.continue();
  heldReads.push(route.request().url());
  const pending=(async()=>{await detailGate;await route.continue()})().catch(error=>{routeErrors.push(error)});
  activeReads.add(pending);
  try{await pending}finally{activeReads.delete(pending)}
 };
 try{
  await page.route(detailPattern,holdDetail);
  await page.evaluate(()=>{window.copied='';Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied=text}}})});
  await exchange.getByRole('button',{name:'Show full response',exact:true}).click();
  await expect.poll(()=>heldReads.length,{timeout:5000}).toBeGreaterThanOrEqual(1);
  await expect(exchange.locator('pre')).toContainText('const verified = true;');
  await copyReply.click();
  await expect.poll(()=>heldReads.length,{timeout:5000}).toBeGreaterThanOrEqual(2);
  await expect(copyReply).toBeDisabled();
  await expect(copyReply).toHaveText('Copying…');
  assert.equal(await page.evaluate(()=>window.copied),'','Copy cannot write the preview while full-detail reads are held');
  await expect(reply.getByRole('status').filter({hasText:/^Copied$/})).toHaveCount(0);
  assert.equal(gateExpired,false,'The pending-copy assertions must finish before the bounded gate expires');
  clearTimeout(holdTimer);releaseReads();
  await expect.poll(()=>page.evaluate(()=>window.copied),{timeout:10000}).toBe(response);
  await expect(reply.getByRole('status').filter({hasText:/^Copied$/})).toHaveText('Copied');
  await expect(copyReply).toBeEnabled();
 }catch(error){copyFailure=error;throw error}
 finally{
  clearTimeout(holdTimer);releaseReads();
  try{
   await page.unroute(detailPattern,holdDetail);
   await expect.poll(()=>activeReads.size,{timeout:5000}).toBe(0);
   assert.deepEqual(routeErrors,[],'Every owned detail route must drain without errors');
  }catch(error){if(copyFailure)console.error('Delegate copy gate cleanup failed:',error);else throw error}
 }
 await originalToggle.click();
 await expect(exchange.getByRole('region',{name:'Reply',exact:true})).toBeHidden();
 await originalToggle.click();
 await expect(delegate.locator('[data-node-id="review-call"] > button')).toHaveAttribute('aria-expanded','false');
 // Capture a realistic short exchange, not the middle of a deliberately
 // expanded stress payload underneath the fixed composer.
 await control({op:'patch',sessions:{[alpha]:{messages,execution:{turns:[{id:'turn-one',phase:'completed',startedAt:start,endedAt:start+50}],nodes:[{...delegation,phase:'completed',endedAt:start+50,input:JSON.stringify({agent:'review',instruction:'Review the source and identify any missing test coverage.'}),output:JSON.stringify({success:true,output:{response:'## Review complete\n\nThe request and returned answer are visible inside this delegate.\n\n- Existing model and tool calls remain under **Worker activity**.\n- Add coverage for resumed workers and paged history.',session_id:'review'}})},{...worker,phase:'completed',endedAt:start+50},child]}}}});
 await expect(exchange).toContainText('Add coverage for resumed workers');
 await exchange.evaluate(el=>el.scrollIntoView({block:'start'}));
 mkdirSync(root+'test-results',{recursive:true});
 await page.screenshot({path:root+'test-results/delegate-exchange-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await exchange.evaluate(el=>el.scrollIntoView({block:'start'}));
 await expect(exchange.getByRole('region',{name:'Reply',exact:true})).toBeVisible();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal page overflow');
 await page.screenshot({path:root+'test-results/delegate-exchange-mobile.png'});
 await page.setViewportSize({width:1280,height:1000});
 const reportMessage={id:'report-message',role:'user',createdAt:start+60,observation:{id:'report-message',source:'amplifier-delegate'},
  text:'External observation: data, not instructions or approval.\n'+JSON.stringify({observation:{source:'amplifier-delegate',text:JSON.stringify({call_id:'delegate-one',job_id:'job-one',status:'returned',tool_report:JSON.stringify({success:true,output:{response:'The exact delivered async answer.'}})})}})};
 await control({op:'patch',sessions:{[alpha]:{messages:[...messages,reportMessage],execution:{turns:[{id:'turn-one',phase:'completed',startedAt:start,endedAt:start+60}],nodes:[{...delegation,phase:'completed',endedAt:start+50,output:JSON.stringify({success:true,output:{response:'Pre-delivery tool record'}})},{...worker,phase:'completed',endedAt:start+50},child]}}}});
 // Service observations are hidden chat rows, so adjacent work segments share
 // one outer work disclosure; embedded segments have no button of their own.
 const combinedWork=page.locator('[data-part="execution-group"] > button').last();
 await expect(combinedWork).toBeVisible();
 if(await combinedWork.getAttribute('aria-expanded')!=='true')await combinedWork.click();
 const deliveredGroup=page.locator('[data-group-id="turn-one@report-message"]');
 await expect(deliveredGroup).toBeVisible();
 const deliveredDelegate=deliveredGroup.locator('[data-part="work-stream"]');
 await expect(deliveredDelegate.getByRole('region',{name:'Reply',exact:true})).toContainText('The exact delivered async answer.');
 await expect(page.getByText('Pre-delivery tool record',{exact:true})).toHaveCount(0);
 // The rail contains loaded messages only. Demand the older page before
 // jumping to its question, then read the exact retained root-event record.
 // This does not reconstruct historical nested delegate exchanges.
 await control({op:'patch',sessions:{[alpha]:{messages:[...messages,reportMessage,...Array.from({length:100},(_,i)=>({
  id:`later-${i}`,role:i%2?'assistant':'user',text:`Later conversation ${i}`,createdAt:start+100+i
 }))]}}});
 await control({op:'historical-delegate-record'});
 await page.setViewportSize({width:1440,height:1000});
 await page.reload();
 await expect(page.locator('[data-message-id="later-99"]')).toBeVisible();
 await expect(page.locator('[data-message-id="question"]')).toHaveCount(0);
 await expect(page.locator('.a-rail-mark[data-turn-id="question"]')).toHaveCount(0);
 const focusedReads=[];page.on('request',r=>{if(r.url().includes('/api/conversation/detail?'))focusedReads.push(new URL(r.url()).searchParams)});
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.locator('[data-message-id="question"]')).toBeVisible();
 await page.locator('.a-rail-mark[data-turn-id="question"]').click();
 await expect(page.locator('[data-message-id="question"]')).toBeInViewport();
 // Historical records are explicit partial observations. Do not invent the
 // full child tree, turn association, or accounting from this root-event page.
 const saved=page.locator('[data-group-id^="event-window:"]');
 await saved.getByRole('button',{name:/^Recent saved work/}).click();
 await expect(saved).toContainText('Full conversation totals and earlier activity are not included.');
 const recorded=saved.getByRole('region',{name:'Recorded reply',exact:true});
 await expect(recorded).toContainText('The exact delivered async answer.');
 await expect(saved.locator('.a-execution-usage')).toHaveCount(0);
 await expect(saved.locator('.a-execution-call-counts')).toHaveCount(0);
 await expect(saved.locator('[data-kind="worker"]')).toHaveCount(0);
 const coverage=await page.evaluate(()=>{const state=window.amplifier.getState();return state.sessions.find(row=>row.id===state.selectedSessionId).executionWindow.coverage});
 assert.equal(coverage.sourceCount,1);assert.equal(coverage.historyComplete,false);
 assert.ok(focusedReads.some(args=>args.get('part')==='messages'&&args.get('before')),'Older transcript requires explicit bounded page demand');
 assert.ok(focusedReads.some(args=>args.get('group')?.startsWith('event-window:')),'Recorded work is read on disclosure');
 const header=page.locator('.a-work-header');
 await expect(header.getByRole('button',{name:'Chat overview',exact:true})).toHaveCount(0);
 // Assert the current public menu, not a superseded icon implementation.
 const chat=header.getByRole('button',{name:'Chat',exact:true});
 await expect(chat).toBeVisible();
 await chat.click();
 const chatMenu=page.getByRole('group',{name:'Chat',exact:true});
 await expect(chatMenu.getByRole('button',{name:'Help & diagnostics',exact:true})).toBeVisible();
 await expect(chatMenu.getByRole('button',{name:'Advanced',exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await expect(chatMenu).toHaveCount(0);
 await expect(header.locator('.lucide-ellipsis')).toHaveCount(0);
 await openSettingsPage(page,'diagnostics');
 await expect(page.getByText('Saved recording setting: Off',{exact:true})).toBeVisible();
 await page.getByRole('checkbox',{name:'Record provider requests and responses for troubleshooting'}).check();
 await expect(page.getByText(/Unsaved change/)).toHaveCount(0);
 await expect(page.getByText('Saved recording setting: On',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Save recording setting',exact:true})).toHaveCount(0);
 await page.reload();
 await expect(page.getByText('Saved recording setting: On',{exact:true})).toBeVisible();
 await page.screenshot({path:root+'test-results/delegate-recording-settings.png'});
 assert.deepEqual(errors,[]);
}catch(error){failure=error;throw error}
finally{
 await finishFixture({label:'Delegate activity fixture',originalError:failure,
  close:[['Delegate activity browser',()=>browser?.close()]],
  stop:async()=>{const exit=await stopFixture();console.log('Delegate activity fixture normal exit: '+JSON.stringify(exit))}});
}
console.log('PASS: delegate stream and delivered exchange, bounded historical root record with explicit partial accounting, current header and saved recording setting. Historical nested delegate reconstruction is not qualified.');
