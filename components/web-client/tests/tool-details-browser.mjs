import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const outputDir=process.env.AMPLIFIER_TEST_OUTPUT_DIR||await mkdtemp(join(tmpdir(),'amplifier-execution-actions-'));
await mkdir(outputDir,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let log='';fixture.stderr.on('data',chunk=>log+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});if(!response.ok)throw Error(await response.text()+' '+log);return response.json()};
await control({op:'bounded-tool-records'});
const before=await control({op:'inspect-work-source'});
const browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
const legacyReads=[],legacyResponses=[];
try{
 const page=await browser.newPage({viewport:{width:1300,height:1050},extraHTTPHeaders:headers}),errors=[],reads=[];
 page.on('pageerror',error=>{errors.push(error.message);console.error(error.message)});
 page.on('request',request=>{const url=new URL(request.url());if(url.pathname==='/api/conversation/detail'&&['tool','field'].includes(url.searchParams.get('part')))reads.push(url)});
 page.on('response',response=>{
  const url=new URL(response.url());
  if(url.pathname!=='/api/conversation/detail'||url.searchParams.get('part')!=='tool'||!url.searchParams.get('id')?.endsWith(':older-legacy'))return;
  const record={at:Date.now(),query:Object.fromEntries(url.searchParams),status:response.status()};legacyReads.push(record);
  legacyResponses.push(response.json().then(value=>{record.response={lookupKey:value.lookupKey,before:value.before,coverage:value.coverage,error:value.error,
   contentAvailability:value.item?.contentAvailability,observations:value.item?.savedObservations?.map(row=>({id:row.id,event:row.event,createdAt:row.createdAt,toolCallId:row.toolCallId}))}})
   .catch(error=>{record.readError=error.message}));
 });
 await page.goto(base);await showFixtureConversation(page);
 const turn=page.locator('[data-group-id="tool-details@tool-detail-question"]');
 const turnButton=turn.locator(':scope > button.a-execution-turn-line');await turnButton.waitFor();
 if(await turnButton.getAttribute('aria-expanded')==='false')await turnButton.click();
 const row=id=>turn.locator(`[data-node-id$=":${id}"]`);
 const open=async id=>{const node=row(id),button=node.locator(':scope > button.a-execution-action-line');await button.waitFor();if(await button.getAttribute('aria-expanded')==='false')await button.click();return node};
 await expect(row('older-exact').locator(':scope > button')).toContainText('older-exact-command');
 const source=before.sessions.find(session=>session.execution?.turns?.some(turn=>turn.id==='tool-details'));
 const delegateId=source.execution.nodes.find(node=>node.toolCallId==='older-delegate').id;
 const olderId=source.execution.nodes.find(node=>node.toolCallId==='older-exact').id;
 const delegateRequest=turn.locator(`[data-work-entry="${delegateId}:delegateRequest"]`);
 const delegateReply=turn.locator(`[data-work-entry="${delegateId}:delegateResponse"]`);
 await expect(delegateRequest).toBeVisible();
 // Opening Work admits the visible delegate preview. Collapsed operations and
 // full fields still must not fetch; no unrelated or duplicate read is allowed.
 const visibleDelegateRead=url=>url.searchParams.get('part')==='tool'&&url.searchParams.get('sessionId')===source.id&&
  url.searchParams.get('group')==='tool-details@tool-detail-question'&&url.searchParams.get('id')===delegateId;
 assert.equal(reads.filter(url=>!visibleDelegateRead(url)).length,0,'Collapsed host hints and full fields do not fetch saved payloads');
 assert.ok(reads.length<=1,'The one visible delegate preview shares one bounded lookup');
 let failNext=true;
 await page.route('**/api/conversation/detail?**',async route=>{if(new URL(route.request().url()).searchParams.get('part')==='tool'&&new URL(route.request().url()).searchParams.get('id')===olderId&&failNext){failNext=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Saved details temporarily unavailable'})})}return route.continue()});
 const older=await open('older-exact');
 await expect(older.getByRole('button',{name:'Retry saved details'})).toBeVisible();
 await expect(older.locator(':scope > button')).toContainText('older-exact-command');
 await older.getByRole('button',{name:'Retry saved details'}).click();
 await expect(older.getByRole('button',{name:'Search older saved details'})).toBeVisible();
 await older.getByRole('button',{name:'Search older saved details'}).click();
 await expect(older).toContainText('OLDER-EXACT-RESULT-END');
 await older.getByRole('button',{name:/Show all .* lines/}).first().click();
 await expect(older).toContainText('OLDER-EXACT-INPUT-END');
 assert.equal(await older.locator('.a-related-saved-records').count(),0,'Exact observation has directly associated fields');
 await expect.poll(async()=>await delegateRequest.getByRole('button',{name:'Search older saved details'}).count()>0||(await delegateRequest.innerText()).includes('Review retained request')).toBe(true);
 assert.ok(!(await delegateReply.innerText()).includes('Response not recorded'),'Unavailable loaded page is not proof of an absent recording');
 if(await delegateRequest.getByRole('button',{name:'Search older saved details'}).count())await delegateRequest.getByRole('button',{name:'Search older saved details'}).click();
 await expect(delegateRequest).toContainText('Review retained request.');
 for(const [entry,label] of [[delegateRequest,'Show full request'],[delegateReply,'Show full response']]){const button=entry.getByRole('button',{name:label,exact:true});if(await button.count())await button.click()}
 await expect(delegateRequest).toContainText('DELEGATE-REQUEST-END');await expect(delegateReply).toContainText('DELEGATE-RESPONSE-END');
 // Child operations are now directly interleaved with the exchange.
 await expect(turn.getByRole('button',{name:/^Worker activity/})).toHaveCount(0);
 const child=await open('child-exact');
 await expect(child.locator(':scope > button')).toContainText('Child validation failed');
 await expect(child.locator(':scope > button')).toContainText('Exit 2');
 await expect(child.getByRole('button',{name:/Show all .* lines/}).first()).toBeVisible();
 await child.getByRole('button',{name:/Show all .* lines/}).first().click();
 await expect(child).toContainText('CHILD-CODE-END');await expect(child).toContainText('CHILD-OUTPUT-END');
 const legacy=await open('older-legacy');
 await expect(legacy.getByRole('button',{name:'Search older saved details'})).toBeVisible();
 if(await legacy.getByRole('button',{name:'Search older saved details'}).count())await legacy.getByRole('button',{name:'Search older saved details'}).click();
 await expect(legacy).toContainText('The app cannot confirm which attempt they belong to.');
 await expect(legacy.locator('.a-related-saved-records > .a-execution-node')).toHaveCount(3);
 for(const button of await legacy.locator('.a-related-saved-records .a-execution-node > button.a-execution-action-line').all())await button.click();
 await expect(legacy).toContainText('LEGACY-INPUT-END');await expect(legacy).toContainText('LEGACY-OUTPUT-END');
 const after=await control({op:'inspect-work-source'});
 assert.deepEqual(after,before,'Inspecting saved actions does not start work or mutate source records');
 assert.deepEqual(errors,[]);
 await older.scrollIntoViewIfNeeded();await page.screenshot({path:join(outputDir,'older-tool-details.png')});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:join(outputDir,'older-tool-details-mobile.png')});
 // Keep exact open fields while newer events cross the latest-100 cutoff
 // and retire all eight original recent-window receipts.
 await page.setViewportSize({width:1300,height:1050});
 await control({op:'bounded-provider-records',retention:true});
 await page.reload();await showFixtureConversation(page);
 const retainedTurn=page.locator('[data-group-id="recordings@recording-question"]');
 await retainedTurn.locator(':scope > button.a-execution-turn-line').click();
 const retainedTool=retainedTurn.locator('[data-node-id$=":retained-tool"]');
 await retainedTool.locator(':scope > button.a-execution-action-line').click();
 await expect(retainedTool.getByRole('button',{name:/Show all .* lines/})).toHaveCount(2);
 for(let index=0;index<2;index++)await retainedTool.getByRole('button',{name:/Show all .* lines/}).first().click();
 await expect(retainedTool).toContainText('TOOL-INPUT-END');
 await expect(retainedTool).toContainText('TOOL-OUTPUT-END');
 const retainedModel=retainedTurn.locator('[data-kind="llm"]').filter({hasText:'bounded-recording'});
 await retainedModel.locator(':scope > button.a-execution-action-line').click();
 for(const field of ['request','response']){
  await retainedModel.getByRole('button',{name:new RegExp('^View '+field)}).click();
  await retainedModel.getByRole('button',{name:new RegExp('^'+field[0].toUpperCase()+field.slice(1)+' Object')}).click();
 }
 await expect(retainedModel).toContainText('RECORDED-REQUEST');
 await expect(retainedModel).toContainText('RECORDED-RESPONSE');
 const retainedBefore=await control({op:'inspect-work-source'});
 const fieldReadsBefore=reads.length;
 for(const count of [99,101,...Array(9).fill(1)]){
  console.log('Retention rollover',count);
  const synced=count===99?page.waitForResponse(response=>new URL(response.url()).pathname==='/api/conversation/work-sync'&&response.request().postDataJSON()?.group==='recordings@recording-question'):null;
  const changed=await control({op:'append-retention-records',count});
  await page.waitForFunction(revision=>window.amplifier.getState().revision>=revision,changed.revision);
  if(synced){const update=await synced;assert.ok(update.ok(),await update.text());}
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  // Reconciliation has reached the open view, with no toggle or reload.
  await expect(retainedTool).toContainText('TOOL-INPUT-END');
  await expect(retainedModel).toContainText('RECORDED-REQUEST');
 }
 await expect(retainedTool).toContainText('TOOL-OUTPUT-END');
 await expect(retainedModel).toContainText('RECORDED-RESPONSE');
 await expect(retainedModel.getByRole('link',{name:'Download full recorded request (JSON)',exact:true})).toBeVisible();
 assert.equal(reads.length,fieldReadsBefore,'Rollover must not discard and re-fetch already loaded fields');
 assert.deepEqual(await control({op:'inspect-work-source'}),retainedBefore,'Inspection and rollover do not replay work');
 await page.screenshot({path:join(outputDir,'open-fields-after-rollover.png')});
 console.log('Bounded older exact, delegate and child fields render; legacy observations remain separate; inspection preserves runtime and source state.');
}catch(error){
 console.error(log);
 // Preserve the failed lookup and visible records without retrying a read or
 // weakening the three-observation assertion. Do not capture payload bodies.
 const diagnostic={error:String(error),reads:legacyReads};
 try{
  await Promise.allSettled(legacyResponses);
  const page=browser.contexts()[0]?.pages()[0];
  diagnostic.dom=page?await page.locator('[data-node-id$=":older-legacy"]').evaluateAll(rows=>rows.map(row=>({
   expanded:row.querySelector(':scope > button')?.getAttribute('aria-expanded'),
   observations:[...row.querySelectorAll('.a-related-saved-records > .a-execution-node')].map(node=>node.dataset.nodeId),
   controls:[...row.querySelectorAll('button')].map(button=>({label:button.getAttribute('aria-label')||button.textContent,disabled:button.disabled})),
   alerts:[...row.querySelectorAll('[role=alert]')].map(alert=>alert.textContent)}))):[];
  await writeFile(join(outputDir,'legacy-tool-lookup-failure.json'),JSON.stringify(diagnostic,null,2));
 }catch(captureError){console.error('Could not capture legacy lookup diagnostics:',captureError.message)}
 throw error;
}finally{await browser.close();fixture.kill()}
