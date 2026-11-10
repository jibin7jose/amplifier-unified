import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

// Observe one explicit preview attempt; never replay the action to obtain evidence.
async function observePreview(page,button,target){
 const report={target,snapshots:[],network:[],errors:[],dropped:0},pending=new Set(),requests=new WeakMap();
 let dom,finished,sequence=0;
 const pick=(row,keys)=>row?Object.fromEntries(keys.filter(key=>Object.hasOwn(row,key)).map(key=>[key,typeof row[key]==='string'?row[key].slice(0,1500):row[key]])):null;
 const fault=error=>{if(report.errors.length<8)report.errors.push(String(error?.stack||error).slice(0,1500));else report.dropped++};
 const onRequest=request=>{
  try{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return;
   const raw=request.postDataJSON(),body=raw?.action==='shell.command'?raw.args:raw,args=body?.args;
   if(body?.action!=='canvas.select'||args?.id!==target.resourceId||args?.version!==target.version||args?.questionId!==target.questionId)return;
   if(sequence>=16){report.dropped++;return}
   const row={sequence:++sequence,at:Date.now(),commandId:raw.id??body.id??null,responseState:'not-observed',
    action:body.action,args:pick(args,['id','version','questionId','questionContentRevision','sessionId','presentation','resetPreview'])};
   requests.set(request,row);report.network.push(row);
  }catch(error){fault(error)}
 };
 const onResponse=response=>{
  try{
   const row=requests.get(response.request());if(!row)return;
   row.status=response.status();row.responseAt=Date.now();row.responseState='body-pending';
   // response.json() observes this response through Playwright's body API;
   // it is not an independent capture/attestation of original wire bytes.
   const read=response.json().then(body=>{row.response={source:'Playwright observed action response.json',
    ...pick(body,['accepted','status','code','error','revision','stateRevision','hostInstanceId']),
    result:pick(body.result,['id','version','presentation','status','code','error'])};row.responseState='observed';
   }).catch(error=>{row.responseState='body-read-failed';fault(error)}).finally(()=>pending.delete(read));
   pending.add(read);
  }catch(error){fault(error)}
 };
 const onFailed=request=>{try{const row=requests.get(request);if(row){row.failure=request.failure()?.errorText||'unknown';row.responseState='request-failed'}}catch(error){fault(error)}};
 const snapshot=async label=>{try{if(dom)report.snapshots.push({label,...await dom.evaluate(value=>value.snapshot())})}catch(error){fault(error)}};
 const finish=error=>finished??=(async()=>{
  page.off('request',onRequest);page.off('response',onResponse);page.off('requestfailed',onFailed);
  await snapshot(error?'failure-finally':'preview-assertions-passed');
  if(dom){
   try{report.dom=await dom.evaluate(value=>value.finish())}catch(error){fault(error)}
   try{await dom.dispose()}catch(error){fault(error)}
  }
  let timer;
  try{await Promise.race([Promise.all([...pending]),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Preview response observation did not drain')),5000)})])}
  catch(error){fault(error)}finally{clearTimeout(timer)}
  report.pendingResponseReads=pending.size;
  report.failure=error?{name:error.name,message:error.message}:null;
  if(output)await writeFile(join(output,'question-first-preview.json'),JSON.stringify(report,null,2)+'\n');
  assert.deepEqual(report.errors,[],'First-preview observer must remain healthy');
  assert.deepEqual(report.dom?.errors,[],'First-preview DOM observer must remain healthy');
 })();
 page.on('request',onRequest);page.on('response',onResponse);page.on('requestfailed',onFailed);
 try{
  dom=await button.evaluateHandle((original,target)=>{
   const cardSelector=`[data-question-id="${CSS.escape(target.questionId)}"]`;
   const wrapperSelector=`[data-full-artifact-preview="${CSS.escape(target.resourceId)}"]`;
   const originalCard=original.closest('.a-compact-artifact'),events=[],errors=[];let dropped=0;
   const ordinal=[...original.closest('[data-question-id]').querySelectorAll('.a-compact-artifact')].indexOf(originalCard);
   const pick=(row,keys)=>row?Object.fromEntries(keys.filter(key=>Object.hasOwn(row,key)).map(key=>[key,row[key]])):null;
   const snapshot=()=>{
    const state=window.amplifier.getState(),question=document.querySelector(cardSelector);
    const card=question?.querySelectorAll('.a-compact-artifact')[ordinal],button=card?.querySelector('.a-document-preview-toggle');
    return JSON.parse(JSON.stringify({at:Date.now(),selectedSessionId:state.selectedSessionId,panel:state.view?.panel,
     navigationPending:state.navigationPending,card:{present:!!card,same:card===originalCard,originalConnected:originalCard.isConnected},
     button:{same:button===original,originalConnected:original.isConnected,expanded:button?.getAttribute('aria-expanded'),disabled:button?.disabled},
     wrapperCount:question?.querySelectorAll(wrapperSelector).length??0,notice:card?.querySelector('.a-compact-artifact-notice')?.textContent?.slice(0,1500)||'',
     question:pick(state.sessions?.find(row=>row.id===target.sessionId)?.questions?.find(row=>row.id===target.questionId),['id','revision','contentRevision','status']),
     primary:pick(state.canvas,['id','selectedVersion','revision','open','_presentationDisposed']),
     presentations:(state.canvasWorkspace?.presentations||[]).filter(row=>row.id===target.resourceId&&row.version===target.version).slice(0,4).map(row=>pick(row,['id','version','presentation','questionId','questionContentRevision','sessionId'])),
     views:(state.canvasWorkspace?.views||[]).filter(row=>row.resourceId===target.resourceId).slice(0,8).map(row=>pick(row,['viewId','resourceId','resourceRevision','generation','selectedVersion','dirty'])),
    }));
   };
   const guard=fn=>{try{fn()}catch(error){if(errors.length<8)errors.push(String(error).slice(0,1500));else dropped++}};
   const record=value=>{if(events.length<32)events.push({...value,...snapshot()});else dropped++};
   const input=event=>guard(()=>{
    const button=event.target.closest?.('.a-document-preview-toggle');
    if(button!==original&&button?.closest('.a-compact-artifact')!==originalCard)return;
    record({kind:event.type,detail:event.detail,pointerType:event.pointerType,isTrusted:event.isTrusted,x:event.clientX,y:event.clientY});
   });
   const mutations=rows=>guard(()=>{for(const row of rows){
    const matches=node=>node.nodeType===1&&(node.matches(wrapperSelector)||node.querySelector(wrapperSelector));
    const added=[...row.addedNodes].filter(matches).length,removed=[...row.removedNodes].filter(matches).length;
    if(added||removed)record({kind:'wrapper-mutation',added,removed});
   }});
   const observer=new MutationObserver(mutations);observer.observe(document.body,{childList:true,subtree:true});
   for(const name of ['pointerdown','pointerup','click'])document.addEventListener(name,input,true);
   return {snapshot,finish(){const queued=observer.takeRecords();observer.disconnect();for(const name of ['pointerdown','pointerup','click'])document.removeEventListener(name,input,true);mutations(queued);return {events,errors,dropped}}};
  },target);
  await snapshot('before-click');return {snapshot,finish};
 }catch(error){try{await finish(error)}catch(reportError){console.error('First-preview setup cleanup failed:',reportError)}throw error}
}

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[process.env.AMPLIFIER_QUESTIONS_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/questions_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Questions fixture'});
let browser,failure,finishReviewEvidence,firstPreview;
const output=process.env.AMPLIFIER_QUESTIONS_OUTPUT,layoutChecks=[];
try{
 const ready=await new Promise((resolve,reject)=>{
  let pending='';
  const cleanup=()=>{clearTimeout(timer);fixture.stdout.off('data',data);fixture.off('exit',exited);fixture.off('error',failed)};
  const failed=error=>{cleanup();reject(error)};
  const exited=(code,signal)=>failed(Error(`Questions fixture exited before readiness (${code??signal})`));
  const data=chunk=>{
   pending+=chunk;
   for(let end;(end=pending.indexOf('\n'))>=0;){
    const line=pending.slice(0,end);pending=pending.slice(end+1);
    let value;try{value=JSON.parse(line)}catch{continue}
    if(value.url){cleanup();resolve(value);return}
   }
   if(pending.length>8000)failed(Error('Questions fixture readiness output exceeded its bound'));
  };
  const timer=setTimeout(()=>failed(Error('Questions fixture timeout')),15000);
  fixture.stdout.on('data',data);fixture.once('exit',exited);fixture.once('error',failed);
 });
 if(output)await mkdir(output,{recursive:true});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const card=id=>page.locator(`[data-question-id="${id}"]`),composer=()=>page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 const usage=()=>page.getByRole('region',{name:'Conversation usage',exact:true});
 const holdUsageRead=async()=>{
  let release,arrived=false;const gate=new Promise(resolve=>release=resolve);
  const route=async pending=>{const raw=pending.request().postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;if(body.action==='capacity.read'){arrived=true;await gate}await pending.continue()};
  await page.route('**/api/actions',route);
  return {release,arrived:()=>arrived,remove:()=>page.unroute('**/api/actions',route)};
 };
 const completeUsageDuringClick=async(questions,held,label)=>{
  await expect.poll(held.arrived).toBe(true);
  await expect(usage().getByRole('button',{name:'Reading usage…',exact:true})).toBeDisabled();
  // Scroll only before pointerdown: a completion must not move the target away.
  await questions.scrollIntoViewIfNeeded();const target=await questions.elementHandle(),before=await questions.boundingBox(),refreshBefore=await usage().getByRole('button',{name:'Reading usage…',exact:true}).boundingBox();
  if(output)await page.screenshot({path:join(output,label.replace(/\W+/g,'-')+'-before.png')});
  await page.mouse.move(before.x+before.width/2,before.y+before.height/2);await page.mouse.down();
  try{
   held.release();await expect(usage().getByRole('button',{name:'Refresh usage',exact:true})).toBeEnabled();
   const after=await questions.boundingBox(),refreshAfter=await usage().getByRole('button',{name:'Refresh usage',exact:true}).boundingBox();layoutChecks.push({label,viewport:page.viewportSize(),before,after,refreshBefore,refreshAfter});
   for(const key of ['x','y','height'])assert.ok(Math.abs(refreshAfter[key]-refreshBefore[key])<=1,`${label}: refresh control ${key} moved from ${refreshBefore[key]} to ${refreshAfter[key]}`);
   assert.equal(await target.evaluate(element=>element.isConnected),true,'Usage completion preserves the history control');
   for(const key of ['x','y','width','height'])assert.ok(Math.abs(after[key]-before[key])<=1,`${label}: history target ${key} moved from ${before[key]} to ${after[key]}`);
  }finally{await page.mouse.up();await held.remove();await target.dispose()}
  await expect(questions.locator('..')).toHaveAttribute('open','');
  if(output)await page.screenshot({path:join(output,label.replace(/\W+/g,'-')+'-after.png')});
 };
 const history=async({stableUsage=false,usageCalls=0,label='history'}={})=>{
  const held=stableUsage?await holdUsageRead():null;
  await page.getByRole('button',{name:'Chat',exact:true}).click();
  await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}).click();
  const questions=page.getByText('Questions & answers',{exact:true});
  if(held){
   await completeUsageDuringClick(questions,held,label+' initial');
   await questions.click();await expect(questions.locator('..')).not.toHaveAttribute('open','');
   // Real canonical receipts make the expanded refreshed snapshot taller.
   await usage().getByText('Model breakdown',{exact:true}).click();
   const seeded=await page.request.post(ready.url+'/fixture/usage',{data:{count:usageCalls}});
   assert.equal(seeded.status(),200,await seeded.text());
   const refresh=await holdUsageRead();await usage().getByRole('button',{name:'Refresh usage',exact:true}).click();
   await completeUsageDuringClick(questions,refresh,label+' expanded refresh');
   await expect(usage().getByText(`${usageCalls} recorded model calls ·`,{exact:false})).toBeVisible();
   await expect(usage().locator('h4')).toHaveCount(usageCalls);
  }else{
   await expect(usage().getByRole('button',{name:'Refresh usage',exact:true})).toBeEnabled();
   await questions.click();await expect(questions.locator('..')).toHaveAttribute('open','');
  }
   await page.locator('.a-question-history > summary').click();
   // Each historical answer stays compact until explicitly opened.
   const entries=page.locator('.a-question-history .a-question-item');
   for(const entry of await entries.all()){
    await expect(entry).not.toHaveAttribute('open','');
    await entry.locator(':scope > summary').click();
   }
 };
 const close=async()=>{
  // Closing is optimistic; do not reload until the closed panel is saved.
  const saved=page.waitForResponse(response=>{
   if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
   const raw=response.request().postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;
   return body.action==='view.update'&&body.args?.patch?.panel===null&&response.ok();
  });
  await page.getByRole('button',{name:'Close panel',exact:true}).click();
  await saved;await expect(page.getByRole('dialog')).toHaveCount(0);
 };
 const check=async()=>await (await page.request.get(ready.url+'/fixture/check')).json();
 await page.goto(ready.url);await showFixtureConversation(page);
 await expect(card(ready.required)).toBeVisible();
 const uniqueQuestions=async()=>{for(const id of [ready.required,ready.optional,ready.skipped])await expect(card(id)).toHaveCount(1)};
 await uniqueQuestions();
 const alternative=card(ready.required).locator('.a-question-alternative');
 if(process.env.AMPLIFIER_QUESTIONS_NEGATIVE==='v2-density'){
  // Deliberately restore the old expanded editor: the approved-density oracle
  // below must fail, independently of Markdown/version/security assertions.
  await alternative.evaluate(element=>{element.open=true});
 }
 await expect(alternative).not.toHaveAttribute('open','');
 await expect(card(ready.required).getByRole('textbox')).toBeHidden();
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  await card(ready.required).scrollIntoViewIfNeeded();
  await expect(card(ready.required).getByRole('textbox')).toBeHidden();
  assert.ok(await card(ready.required).evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Choice question fits narrow view');
  if(output)await page.screenshot({path:join(output,`question-choice-collapsed-${width}.png`)});
 }
 await page.setViewportSize({width:1280,height:900});
 await card(ready.required).getByText('Question context',{exact:true}).click();
 await expect(card(ready.required).getByText('This step waits for your answer. Other work can continue.')).toBeVisible();
 await card(ready.required).getByText('Question context',{exact:true}).click();
 assert.equal(await card(ready.required).getByRole('button',{name:'Skip question'}).count(),0);
 // A neighboring handoff must not reuse the questions component identity.
 // Keep the strict, unscoped question selectors: duplicate cards are a defect.
 const questionDraft='Keep this question draft through neighboring updates';
 await alternative.locator(':scope > summary').click();
 await card(ready.required).getByRole('textbox').fill(questionDraft);
 await alternative.locator(':scope > summary').click();
 await expect(card(ready.required).getByRole('textbox')).toBeHidden();
 await alternative.locator(':scope > summary').click();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 const draftSaved=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='view.update'&&response.request().postDataJSON()?.args?.patch?.draft==='Keep this unsent message');
 await composer().fill('Keep this unsent message');await draftSaved;
 await uniqueQuestions();await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 const response=await page.request.post(ready.url+'/fixture/handoff',{data:{clientId:await page.evaluate(()=>window.amplifier.getState().client.id)}});
 assert.equal(response.status(),200,await response.text());const proposal=await response.json();assert.equal(proposal.accepted,true);
 const handoff=page.locator(`[data-handoff-id="${proposal.result.requestId}"]`);
 await expect(handoff).toHaveCount(1);await expect(handoff).toBeVisible();await uniqueQuestions();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 await page.reload();await expect(card(ready.required)).toBeVisible();await uniqueQuestions();
 await expect(card(ready.required).locator('.a-question-alternative')).toHaveAttribute('open','');
 await expect(card(ready.required).getByRole('textbox')).toHaveValue(questionDraft);
 await expect(handoff).toHaveCount(1);await expect(handoff).toBeVisible();
 await expect(composer()).toHaveDraft('Keep this unsent message');
 await handoff.getByRole('button',{name:'Not now',exact:true}).click();
 await expect(handoff.getByRole('button',{name:'Not now',exact:true})).toHaveCount(0);
 await uniqueQuestions();await expect(composer()).toHaveDraft('Keep this unsent message');
 assert.deepEqual((await check()).sessionIds,[ready.sessionId],'A cancelled review creates no chat');
 assert.equal((await check()).sent.length,0,'Reconnect cannot submit an unanswered question');
 await expect(card(ready.required).getByRole('radio')).toHaveCount(0);
 await expect(card(ready.required).getByRole('button',{name:/Full report/})).toHaveCSS('justify-content','flex-start');
 await page.getByRole('button',{name:'Review questions',exact:true}).click();
 assert.ok(await page.locator('[data-part=questions]').evaluate(el=>el.scrollHeight<=el.clientHeight+1),'Questions use the chat scroll area, without a clipping scroller');
 const questionBox=await card(ready.required).boundingBox(),composerBox=await page.locator('[data-part=composer]').boundingBox();
 assert.ok(questionBox.width<=composerBox.width+2,'Question cards respect the chat column');
 await page.screenshot({path:'/tmp/amplifier-durable-questions-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await card(ready.required).getByRole('button',{name:/Full report/}).scrollIntoViewIfNeeded();
 assert.ok(await card(ready.required).evaluate(element=>element.scrollWidth<=element.clientWidth));
 await page.screenshot({path:'/tmp/amplifier-durable-questions-choice-mobile.png'});
 await page.setViewportSize({width:1280,height:900});
 // A failed request retains the custom draft; retry uses the same command ID.
 const answerRequests=[];let releaseAnswer,heldResolve;
 const held=new Promise(resolve=>{heldResolve=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body.action!=='question.answer')return route.continue();
  answerRequests.push(body);
  if(answerRequests.length===1)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Try this answer again'})});
  await new Promise(resolve=>{releaseAnswer=resolve;heldResolve()});
  return route.continue();
 });
 await card(ready.required).getByRole('textbox').fill('Keep my alternative answer');
 await card(ready.required).getByRole('button',{name:/Full report/}).click();
 await expect(card(ready.required).getByRole('alert')).toBeVisible();
 await expect(card(ready.required).getByRole('textbox')).toHaveValue('Keep my alternative answer');
 await card(ready.required).getByRole('button',{name:/Full report/}).click();
 await held;
 await expect(card(ready.required).getByRole('button',{name:/Full report/})).toBeDisabled();
 await card(ready.required).getByRole('button',{name:/Full report/}).evaluate(button=>{button.click();button.click()});
 assert.equal(answerRequests.length,2,'Repeated clicks during save cannot send another answer');
 assert.equal(answerRequests[0].id,answerRequests[1].id,'Retry reuses the original command ID');
 const saved=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='question.answer'&&response.ok());
 releaseAnswer();await saved;await page.unroute('**/api/actions');
 await expect(page.locator('.a-question-history')).toHaveCount(0);
 await history({stableUsage:true,usageCalls:6,label:'desktop'});
 await expect(card(ready.required).getByText('Answer saved and delivered to Amplifier.')).toBeVisible();
 await close();
 await expect(composer()).toHaveDraft('Keep this unsent message');
 let state=await check();assert.equal(state.sent.length,1);assert.equal(state.sent[0].sessionId,ready.sessionId);
 const duplicate=await page.request.post(ready.url+'/api/actions',{data:{id:'second-device',action:'question.answer',args:{sessionId:ready.sessionId,id:ready.required,expectedRevision:1,optionId:'full'}}});
 assert.equal(duplicate.status(),409);assert.equal((await check()).sent.length,1);
 await card(ready.optional).getByRole('textbox',{name:'Your answer: What title would you like?'}).fill('Project findings');
 await card(ready.optional).getByRole('button',{name:'Submit answer'}).click();
 await expect(card(ready.optional)).toHaveCount(0);
 await history();await expect(card(ready.optional).getByText('Answer saved and delivered to Amplifier.')).toBeVisible();await close();
 await card(ready.skipped).getByRole('button',{name:'Skip question'}).click();
 await history();await expect(card(ready.skipped).getByText('Question cancelled without an answer.')).toBeVisible();await close();
 const voice=await (await page.request.post(ready.url+'/fixture/voice')).json();
 assert.equal(voice.answer.provenance.via,'call');assert.equal(voice.answer.provenance.voiceItemId,'fixture-answer');
 await history();await expect(card(voice.id).getByText('Answer saved and delivered to Amplifier.')).toBeVisible();await close();
 state=await check();assert.equal(state.sent.length,3);assert.equal(new Set(state.sent.map(row=>row.inputId)).size,3);
 await page.reload();await expect(composer()).toHaveDraft('Keep this unsent message');
 await page.setViewportSize({width:390,height:844});
 await history({stableUsage:true,usageCalls:12,label:'narrow'});await expect(card(voice.id).locator('.a-question-result .a-markdown')).toHaveText('Plain language');
 await expect(card(voice.id)).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await card(voice.id).scrollIntoViewIfNeeded();await expect(card(voice.id)).toBeInViewport();
 await page.screenshot({path:'/tmp/amplifier-durable-questions-mobile.png'});
 await close();
 const reviewResponse=await page.request.post(ready.url+'/fixture/reviews');
 assert.equal(reviewResponse.status(),200,await reviewResponse.text());
 const review=await reviewResponse.json(),q=card(review.question.id);
 await page.setViewportSize({width:1280,height:900});
 if(process.env.AMPLIFIER_QUESTIONS_NEGATIVE==='flatten'){
  // Deliberate rendered-boundary fault: the formatting oracle below must fail.
  await q.locator('.a-question-prompt').evaluate(element=>{element.textContent=element.textContent});
 }
 await expect(q.locator('.a-question-prompt h1')).toHaveText('Choose the peer presentation');
 await expect(q.locator('.a-question-prompt li')).toHaveCount(2);
 await expect(q.locator('.a-question-prompt pre code')).toHaveText('exact source\n');
 assert.equal(await q.locator('.a-question-prompt a[href^="javascript:"]').count(),0);
 assert.equal(await page.evaluate(()=>window.bad),undefined);
 assert.equal(await page.evaluate(()=>document.body.dataset.untrusted),undefined);
 const answerDraft='**Proceed**.\n\nKeep the `source` visible.';
 await q.getByRole('textbox').fill(answerDraft);
 await page.reload();await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 const rows=q.locator('.a-compact-artifact'),first=rows.nth(0),second=rows.nth(1);
 await expect(rows).toHaveCount(2);await expect(q.locator('[data-full-artifact-preview]')).toHaveCount(0);
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  await first.scrollIntoViewIfNeeded();
  const geometry=await first.locator('.a-compact-artifact-row').evaluate(row=>{
   const bounds=node=>{const b=node.getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,height:b.height}};
   return {row:bounds(row),buttons:[...row.querySelectorAll('button')].map(bounds),scrollWidth:row.scrollWidth,clientWidth:row.clientWidth};
  });
  for(const b of geometry.buttons){assert.ok(b.width>=44&&b.height>=44,'44px review targets');assert.ok(b.x>=geometry.row.x&&b.x+b.width<=geometry.row.x+geometry.row.width+1,'Review actions fit row')}
  assert.ok(Math.abs(geometry.buttons[0].y-geometry.buttons[1].y)<1,'Review actions stay on one row');
  assert.ok(geometry.scrollWidth<=geometry.clientWidth+1,'No row overflow');
  layoutChecks.push({label:'compact-question-review',width,...geometry});
  if(output)await page.screenshot({path:join(output,`question-review-${width}.png`)});
 }
 await page.setViewportSize({width:1280,height:900});
 if(process.env.AMPLIFIER_QUESTIONS_NEGATIVE==='wrong-version'){
  // Deliberate request fault: bypassing the pinned version must fail the v1 oracle.
  await page.route('**/api/actions',async route=>{
   const body=route.request().postDataJSON();
   if(body?.action==='canvas.views.inline'&&body.args?.questionId===review.question.id)
    return route.continue({postData:JSON.stringify({...body,args:{...body.args,version:2}})});
   return route.continue();
  });
 }
 firstPreview=await observePreview(page,first.getByRole('button',{name:/^Preview here:/}),
  {questionId:review.question.id,resourceId:review.markdown.id,version:1,sessionId:ready.sessionId});
 await first.getByRole('button',{name:/^Preview here:/}).click();
 await firstPreview.snapshot('after-click');
 await first.locator('[data-full-artifact-preview]').scrollIntoViewIfNeeded();
 await expect(first.getByRole('heading',{name:'Pinned document v1'})).toBeVisible();
 await expect(first.getByRole('heading',{name:'Latest document v2'})).toHaveCount(0);
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 await firstPreview.finish();
 await first.getByRole('button',{name:/^Open in Canvas:/}).click();
 await expect(first.locator('[data-full-artifact-preview]')).toHaveCount(0);
 await expect.poll(()=>page.evaluate(()=>({id:window.amplifier.getState().canvas.id,version:window.amplifier.getState().canvas.selectedVersion}))).toEqual({id:review.markdown.id,version:1});
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 await first.getByRole('button',{name:/^Preview here:/}).click();
 await first.locator('[data-full-artifact-preview]').scrollIntoViewIfNeeded();
 await expect(first.getByRole('heading',{name:'Pinned document v1'})).toBeVisible();
 await first.getByRole('button',{name:/^Hide preview:/}).click();
 await expect(first.locator('[data-full-artifact-preview]')).toHaveCount(0);
 await second.getByRole('button',{name:/^Preview here:/}).click();
 const frame=second.frameLocator('iframe');
 await expect(frame.getByRole('heading',{name:'HTML mockup v1'})).toBeVisible();
 await expect(second.locator('iframe')).toHaveAttribute('sandbox','allow-scripts');
 await frame.getByRole('button',{name:'Try mockup'}).click();
 await expect(frame.locator('#value')).toHaveText('1');
 await expect(frame.locator('body')).toHaveAttribute('data-parent-blocked','yes');
 await expect(frame.locator('body')).toHaveAttribute('data-network-blocked','yes');
 assert.equal((await check()).networkAttempts,0,'Sandbox must block a reachable fixture endpoint, not merely fail DNS');
 assert.equal(await page.evaluate(()=>document.body.dataset.untrusted),undefined);
 assert.equal((await check()).sent.length,3,'Review actions never send or answer');
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 if(output)await page.screenshot({path:join(output,'question-html-review.png')});
 const beforeTransfer=await second.locator('iframe').elementHandle();
 await second.getByRole('button',{name:/^Open in Canvas:/}).click();
 await expect(second.getByRole('button',{name:'Keep current preview',exact:true})).toBeVisible();
 await second.getByRole('button',{name:'Keep current preview',exact:true}).click();
 assert.equal(await beforeTransfer.evaluate(element=>element.isConnected),true,'Keep preserves the exact interactive frame');
 await expect(frame.locator('#value')).toHaveText('1');
 await second.getByRole('button',{name:/^Open in Canvas:/}).click();
 await second.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await expect(second.locator('[data-full-artifact-preview]')).toHaveCount(0);
 await expect.poll(()=>beforeTransfer.evaluate(element=>element.isConnected)).toBe(false);
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.id)).toBe(review.html.id);
 // A closed Canvas retains the primary frame: returning it to the question
 // also needs explicit reset, not canvas.close as an implicit disposal.
 await page.evaluate(()=>{const state=window.amplifier.getState();return window.amplifier.dispatch('canvas.visibility',{open:false,sessionId:state.selectedSessionId,canvasId:state.canvas.id})});
 await second.getByRole('button',{name:/^Preview here:/}).click();
 await expect(second.getByRole('button',{name:'Keep current preview',exact:true})).toBeVisible();
 await second.getByRole('button',{name:'Reset preview and preview in chat',exact:true}).click();
 await expect(frame.getByRole('heading',{name:'HTML mockup v1'})).toBeVisible();
 await frame.getByRole('button',{name:'Try mockup'}).click();
 await expect(frame.locator('#value')).toHaveText('1');
 await beforeTransfer.dispose();
 // Synthetic renderer-declared dirty state is a supported contract probe.
 // It does not claim the built-in Markdown/HTML reader has a document editor.
 const reviewTarget=await page.evaluate(id=>{
  const view=window.amplifier.getState().canvasWorkspace.views.find(v=>v.viewId.startsWith('inline-')&&v.resourceId===id);
  return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
 },review.html.id);
 const reviewAction=async(action,args)=>page.evaluate(async({action,args})=>{
  try{return await window.amplifier.dispatch(action,args)}
  catch(error){return {accepted:false,code:error.code,error:error.message}}
 },{action,args});
 assert.equal((await reviewAction('canvas.views.dirty',{...reviewTarget,dirty:true})).accepted,true);
 const mountedFrame=await second.locator('iframe').elementHandle();
 await second.getByRole('button',{name:/^Open in Canvas:/}).click();
 await expect(second.getByRole('status')).toContainText('Finish or cancel');
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Canvas transfer cannot bypass a dirty question preview');
 await q.getByRole('button',{name:'Submit answer'}).click();
 await expect(q.getByRole('alert')).toContainText('Finish or cancel');
 await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
 assert.equal((await check()).questions.find(row=>row.id===review.question.id).status,'pending');
 assert.equal((await check()).sent.length,3,'Rejected dirty answer must not send');
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Dirty rejection preserves the mounted renderer');
 await expect(frame.locator('#value')).toHaveText('1');
 // Failure-local diagnostics only: retain the original nodes, never replace
 // the continuity assertions with a lookup of a newly mounted equivalent.
 const reviewEvidence={questionId:review.question.id,resourceId:review.html.id,sessionId:ready.sessionId,
  initialView:{...reviewTarget},snapshots:[],events:[],errors:[],droppedEvents:0,droppedErrors:0};
 const evidenceError=(where,error)=>{
  const row={where,message:String(error?.stack||error).slice(0,1500)};
  if(reviewEvidence.errors.length<16)reviewEvidence.errors.push(row);else reviewEvidence.droppedErrors++;
 };
 let identityObserver,finishEvidencePromise;
 const networkRequests=new WeakMap();let requestSequence=0;
 const event=row=>{if(reviewEvidence.events.length<128)reviewEvidence.events.push({at:Date.now(),...row});else reviewEvidence.droppedEvents++};
 const onReviewRequest=request=>{
  try{
   const url=new URL(request.url());
   if(request.isNavigationRequest()&&url.pathname===`/api/canvas/${review.html.id}/document`){
    const id=++requestSequence;networkRequests.set(request,id);
    event({kind:'request',request:id,action:'iframe.document',scope:'resource',url:url.href});return;
   }
   if(request.method()!=='POST'||url.pathname!=='/api/actions')return;
   const raw=request.postDataJSON(),body=raw?.action==='shell.command'?raw.args:raw;
   const action=body?.action,args=body?.args||{},patch=args.patch||{};
   const resource=args.resourceId===review.html.id||args.id===review.html.id||args.canvasId===review.html.id||args.viewId===reviewTarget.viewId;
   const question=action==='question.answer'&&args.id===review.question.id;
   // Leaving the watched chat can remove its resource without naming that
   // resource in the destination. Retain only navigation during this window.
   const navigation=['session.select','session.draft','workspace.select','session.create','canvas.reopen','canvas.close'].includes(action)
    ||action==='view.update'&&['panel','workSurface','workWorkspaceId','canvasDraft'].some(key=>Object.hasOwn(patch,key));
   if(!resource&&!question&&!navigation)return;
   const keys=['id','resourceId','canvasId','viewId','resourceRevision','generation','version','sessionId',
    'questionId','questionContentRevision','previewId','presentation','presentationRequest','resetPreview',
    'expectedRevision','dirty','open'];
   const selected=Object.fromEntries(keys.filter(key=>Object.hasOwn(args,key)).map(key=>[key,args[key]]));
   if(navigation&&action==='view.update')selected.patch=Object.fromEntries(
    ['panel','workSurface','workWorkspaceId'].filter(key=>Object.hasOwn(patch,key)).map(key=>[key,patch[key]]));
   if(navigation&&Object.hasOwn(patch,'canvasDraft'))selected.canvasLibrary=patch.canvasDraft?.library??null;
   const record={request:++requestSequence,commandId:raw.id??body.id??null,action,
    scope:resource?'resource':question?'question':'navigation-away-or-within-watched-chat',args:selected};
   // Serialize immediately: later callbacks cannot revise captured arguments.
   networkRequests.set(request,record.request);
   event({kind:'request',...JSON.parse(JSON.stringify(record))});
  }catch(error){evidenceError('request observer',error)}
 };
 const onReviewResponse=response=>{
  try{const request=networkRequests.get(response.request());if(request)event({kind:'response',request,status:response.status()})}
  catch(error){evidenceError('response observer',error)}
 };
 const onReviewRequestFailed=request=>{
  try{const id=networkRequests.get(request);if(id)event({kind:'requestfailed',request:id,error:String(request.failure()?.errorText||'unknown').slice(0,500)})}
  catch(error){evidenceError('requestfailed observer',error)}
 };
 const captureReview=async label=>{
  try{
   if(!identityObserver){evidenceError(label,'Identity observer was not installed');return}
   const snapshot={label,...await identityObserver.evaluate(observer=>observer.snapshot())};
   reviewEvidence.snapshots.push(snapshot);return snapshot;
  }catch(error){evidenceError(label,error)}
 };
 finishReviewEvidence=()=>finishEvidencePromise??=(async()=>{
  await captureReview(failure?'failure-finally':'after-continuity-assertions');
  page.off('request',onReviewRequest);page.off('response',onReviewResponse);page.off('requestfailed',onReviewRequestFailed);
  if(identityObserver){
   try{reviewEvidence.dom=await identityObserver.evaluate(observer=>observer.finish())}
   catch(error){evidenceError('DOM observer finish',error)}
   try{await identityObserver.dispose()}catch(error){evidenceError('DOM observer dispose',error)}
  }
  reviewEvidence.failure=failure?{name:failure.name,message:failure.message}:null;
  if(output)await writeFile(join(output,'question-review-continuity.json'),JSON.stringify(reviewEvidence,null,2)+'\n');
  // Observer faults are awaited test failures, never uncaught callback errors.
  // finishFixture preserves the original assertion when this runs on failure.
  assert.deepEqual(reviewEvidence.errors,[],'Question review instrumentation must remain healthy');
  assert.equal(reviewEvidence.droppedErrors,0);
  assert.deepEqual(reviewEvidence.dom?.errors,[],'Question review DOM observer must remain healthy');
 })();
 page.on('request',onReviewRequest);page.on('response',onReviewResponse);page.on('requestfailed',onReviewRequestFailed);
 try{
  identityObserver=await page.evaluateHandle(({frame,questionId,resourceId,sessionId})=>{
   // The first existing message proves transcript children survive too, not
   // merely the outer log element whose children can be replaced by loading UI.
   const nodes={transcript:frame.closest('[data-part="messages"]').querySelector('[data-message-id]'),
    card:frame.closest('[data-question-id]'),preview:frame.closest('[data-full-artifact-preview]'),frame};
   const ids=new WeakMap();let serial=0,dropped=0;const mutations=[],errors=[];
   const identity=node=>node?(ids.has(node)?ids.get(node):(ids.set(node,++serial),serial)):null;
   for(const node of Object.values(nodes))identity(node);
   const pick=(row,keys)=>row?Object.fromEntries(keys.filter(key=>Object.hasOwn(row,key)).map(key=>[key,row[key]])):null;
   const currentNodes=()=>{
    const card=document.querySelector(`[data-question-id="${CSS.escape(questionId)}"]`);
    const preview=card?.querySelector(`[data-full-artifact-preview="${CSS.escape(resourceId)}"]`);
    return {transcript:document.querySelector('[data-part="messages"] [data-message-id]'),card,preview,frame:preview?.querySelector('iframe')};
   };
   const snapshot=()=>{
    const current=currentNodes(),state=window.amplifier.getState();
    const question=state.sessions?.find(row=>row.id===sessionId)?.questions?.find(row=>row.id===questionId);
    const refs=rows=>(rows||[]).slice(0,4).map(row=>pick(row,['id','version','sessionId','bodyDigest','kind','title']));
    const views=(state.canvasWorkspace?.views||[]).filter(row=>row.resourceId===resourceId);
    const presentations=(state.canvasWorkspace?.presentations||[]).filter(row=>row.id===resourceId);
    return JSON.parse(JSON.stringify({at:Date.now(),browserTime:performance.now(),
     nodes:Object.fromEntries(Object.entries(nodes).map(([name,node])=>[name,{
      original:identity(node),connected:!!node?.isConnected,current:identity(current[name]),
      currentConnected:!!current[name]?.isConnected,same:node===current[name],
      ...(name==='frame'?{originalSrc:node?.getAttribute('src'),currentSrc:current[name]?.getAttribute('src')}:{}),
     }])),
     selectedSessionId:state.selectedSessionId,navigationPending:state.navigationPending,draftNavigationPending:state.draftNavigationPending,
     panel:state.view?.panel,workSurface:state.view?.workSurface,
     question:question?{...pick(question,['id','revision','contentRevision','status']),delivery:pick(question.delivery,['status','inputId']),
      artifacts:refs(question.artifacts),answer:question.answer?{questionRevision:question.answer.questionRevision,artifacts:refs(question.answer.artifacts)}:null}:null,
     primary:pick(state.canvas,['id','selectedVersion','revision','open','_presentationDisposed']),
     viewCount:views.length,views:views.slice(0,8).map(row=>pick(row,['viewId','resourceId','resourceRevision','generation','dirty','renderer','selectedVersion'])),
     presentationCount:presentations.length,presentations:presentations.slice(0,8).map(row=>pick(row,['id','version','presentation','sessionId','questionId','questionContentRevision','messageId'])),
    }));
   };
   const record=records=>{
    try{
     for(const mutation of records){
      const current=currentNodes(),affected=[];
      for(const [name,node] of Object.entries(nodes)){
       const contains=root=>root===node||root.contains(node)||current[name]&&(root===current[name]||root.contains(current[name]));
       if(mutation.type==='attributes'&&(mutation.target===node||mutation.target===current[name]))affected.push(name+':src');
       if([...mutation.removedNodes].some(contains))affected.push(name+':removed');
       if([...mutation.addedNodes].some(contains))affected.push(name+':added');
      }
      if(!affected.length)continue;
      if(mutations.length<64)mutations.push({affected,...snapshot()});else dropped++;
     }
    }catch(error){if(errors.length<16)errors.push(String(error?.stack||error).slice(0,1500));else dropped++}
   };
   const observer=new MutationObserver(record);
   // Observe ancestors too, but retain only changes to the named nodes;
   // a parent removal can detach the review without a mutation on the iframe.
   observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['src']});
   return {snapshot,finish(){record(observer.takeRecords());observer.disconnect();return {mutations,errors,dropped}}};
  },{frame:mountedFrame,questionId:review.question.id,resourceId:review.html.id,sessionId:ready.sessionId});
 }catch(error){evidenceError('DOM observer setup',error)}
 // Hold the real draft request before host handling. A dirty refusal must
 // preserve the mounted subtree both while pending and after reconciliation.
 const originalDraft=await page.evaluate(()=>window.amplifier.getState().view.draft),originalComposer=await composer().innerText();
 assert.ok(originalDraft.trim(),'The Enter probe needs a nonempty, preserved composer draft');
 const modelOptions=page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true});
 const sendMessage=page.getByRole('button',{name:'Send message',exact:true}),originalContent=await mountedFrame.contentFrame();
 assert.ok(originalContent,'Retain the original iframe browsing context');
 const sameReview=async label=>{
  const snapshot=await captureReview(label);assert.ok(snapshot,'Review identity observation is available');
  for(const [name,node] of Object.entries(snapshot.nodes)){
   assert.ok(node.original,`${label}: original ${name} exists`);
   assert.equal(node.connected&&node.same&&node.currentConnected,true,`${label}: preserve the original ${name}`);
  }
  assert.equal(snapshot.selectedSessionId,ready.sessionId);
  assert.equal(snapshot.question?.status,'pending');
  const lease=snapshot.views.find(view=>view.viewId===reviewTarget.viewId);
  assert.ok(lease,'The original dirty preview lease remains available');
  assert.equal(lease.dirty,true);
  for(const [key,value] of Object.entries(reviewTarget))assert.equal(lease[key],value,`${label}: retain ${key}`);
  await expect(originalContent.locator('#value')).toHaveText('1');
  await expect(q.getByRole('textbox')).toHaveValue(answerDraft);
  await expect(composer()).toHaveDraft(originalDraft);
  assert.equal(await composer().innerText(),originalComposer);
 };
 await sameReview('before-draft-navigation');
 await expect(modelOptions).toBeEnabled();await expect(composer()).toHaveAttribute('aria-readonly','false');
 await expect(sendMessage).toBeEnabled();
 let releaseDraft,heldDrafts=0,sendRequests=0,expired=false,draftResult,draftError,probeFailure,attempt;
 const gate=new Promise(resolve=>{releaseDraft=resolve}),routes=new Set(),routeErrors=[];
 const holdDraft=async route=>{
  routes.add(route);
  try{
   if(route.request().method()==='POST'){
    const raw=route.request().postDataJSON(),body=raw.action==='shell.command'?raw.args:raw;
    if(body.action==='session.draft'){heldDrafts++;await gate}
    if(body.action==='conversation.send')sendRequests++;
   }
  }catch(error){routeErrors.push(String(error).slice(0,1000))}
  try{await route.continue()}catch(error){routeErrors.push(String(error).slice(0,1000))}
  finally{routes.delete(route)}
 };
 const beforeDraftSendCount=(await check()).sent.length;
 const gateTimer=setTimeout(()=>{expired=true;releaseDraft()},15000);
 try{
  await page.route('**/api/actions',holdDraft);
  attempt=reviewAction('session.draft',{}).then(result=>{draftResult=result},error=>{draftError=error});
  await expect.poll(()=>heldDrafts).toBe(1);
  await expect.poll(()=>page.evaluate(()=>{const s=window.amplifier.getState();return {
   navigation:!!s.navigationPending,draft:!!s.draftNavigationPending,selected:s.selectedSessionId,
  }})).toEqual({navigation:true,draft:true,selected:ready.sessionId});
  await sameReview('draft-navigation-held');
  await expect(composer()).toHaveAttribute('aria-readonly','true');
  await expect(modelOptions).toBeDisabled();await expect(sendMessage).toBeDisabled();
  await composer().press('Enter');
  assert.equal((await check()).sent.length,beforeDraftSendCount,'Pending navigation cannot send the retained draft');
  await sameReview('draft-navigation-after-enter');
  assert.equal(sendRequests,0,'Enter while navigation is pending cannot dispatch conversation.send');
  assert.equal(expired,false,'Pending assertions must finish while the real draft request is held');
  assert.equal(draftResult,undefined,'The held request has not reached the host');
  assert.deepEqual(routeErrors,[]);
  clearTimeout(gateTimer);releaseDraft();
  await expect.poll(()=>draftResult!==undefined||draftError!==undefined).toBe(true);
  if(draftError)throw draftError;
  assert.equal(draftResult.accepted,false);assert.equal(draftResult.code,'canvas_view_dirty');
  await expect.poll(()=>page.evaluate(()=>{const s=window.amplifier.getState();return {
   navigation:!!s.navigationPending,draft:!!s.draftNavigationPending,selected:s.selectedSessionId,
  }})).toEqual({navigation:false,draft:false,selected:ready.sessionId});
  await sameReview('draft-navigation-refused');
  await expect(composer()).toHaveAttribute('aria-readonly','false');
  await expect(modelOptions).toBeEnabled();await expect(sendMessage).toBeEnabled();
  assert.equal((await check()).sent.length,beforeDraftSendCount);assert.equal(sendRequests,0);
 }catch(error){probeFailure=error;throw error}
 finally{
  clearTimeout(gateTimer);releaseDraft();
  try{
   await page.unroute('**/api/actions',holdDraft);
   await expect.poll(()=>routes.size).toBe(0);
   if(attempt){await expect.poll(()=>draftResult!==undefined||draftError!==undefined).toBe(true);await attempt}
   assert.deepEqual(routeErrors,[]);
  }catch(error){if(probeFailure)console.error('Draft navigation gate cleanup failed:',error);else throw error}
  finally{reviewEvidence.draftNavigation={heldDrafts,sendRequests,expired,
   result:draftResult?{accepted:draftResult.accepted,code:draftResult.code,error:draftResult.error}:null,
   error:draftError?String(draftError):null,routeErrors}}
 }
 if(output)await page.screenshot({path:join(output,'question-dirty-answer-rejected.png')});
 await captureReview('before-dirty-clear');
 assert.equal((await reviewAction('canvas.views.dirty',{...reviewTarget,dirty:false})).accepted,true);
 await captureReview('after-dirty-clear');
 // New explicit click after confirmed rejection; the same draft must work.
 await captureReview('before-answer');
 await q.getByRole('button',{name:'Submit answer'}).click();
 await captureReview('after-answer-click');
 await expect(q.getByText('Answer saved and delivered to Amplifier.')).toBeVisible();
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Status revision and delivery updates preserve the open review');
 await expect(frame.locator('#value')).toHaveText('1');
 await finishReviewEvidence();
 assert.equal((await check()).sent.length,4);
 await history();await expect(q).toHaveCount(1);
 await expect(page.locator('.a-question-history').locator(`[data-question-id="${review.question.id}"]`)).toHaveCount(0);
 await close();
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'History inspection does not duplicate or replace the review');
 await second.getByRole('button',{name:/^Hide preview:/}).click();
 await expect(second.getByRole('button',{name:'Keep current preview',exact:true})).toBeVisible();
 assert.equal(await mountedFrame.evaluate(element=>element.isConnected),true,'Hide does not silently reset interactive state');
 await expect(frame.locator('#value')).toHaveText('1');
 await second.getByRole('button',{name:'Keep current preview',exact:true}).click();
 await expect(frame.locator('#value')).toHaveText('1');
 await second.getByRole('button',{name:/^Hide preview:/}).click();
 await second.getByRole('button',{name:'Reset preview and hide',exact:true}).click();
 await expect(q).toHaveCount(0);
 await mountedFrame.dispose();
 await history();await expect(card(review.question.id).locator('.a-question-result strong').filter({hasText:'Proceed'})).toBeVisible();
 const answeredCard=card(review.question.id);
 await expect(answeredCard.locator('.a-question-original')).not.toHaveAttribute('open','');
 await expect(answeredCard.locator('.a-question-prompt')).toBeHidden();
 const answerFirst=await answeredCard.evaluate(element=>{
  const answer=element.querySelector('.a-question-result'),refs=element.querySelector('.a-question-artifacts');
  return !!(answer.compareDocumentPosition(refs)&Node.DOCUMENT_POSITION_FOLLOWING);
 });
 assert.equal(answerFirst,true,'Saved answer comes before review cards and original prose');
 await expect(answeredCard.locator('.a-compact-artifact')).toHaveCount(2);
 for(const width of [1280,390,320]){
  await page.setViewportSize({width,height:900});
  await answeredCard.scrollIntoViewIfNeeded();
  assert.ok(await answeredCard.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Answered card fits narrow view');
  if(output)await page.screenshot({path:join(output,`question-answer-first-${width}.png`)});
 }
 await answeredCard.getByText('Original question & provenance',{exact:true}).click();
 await expect(answeredCard.locator('.a-question-prompt h1')).toHaveText('Choose the peer presentation');
 await expect(answeredCard.getByText('Answer to question revision 1.',{exact:true})).toBeVisible();
 await answeredCard.getByText('Original question & provenance',{exact:true}).click();
 const savedQuestion=(await check()).questions.find(row=>row.id===review.question.id);
 assert.equal(savedQuestion.answer.text,answerDraft);assert.equal(savedQuestion.answer.questionRevision,1);
 assert.equal(savedQuestion.answer.artifacts[0].version,1);
 assert.equal(savedQuestion.answer.artifacts[0].id,review.markdown.id);
 if(output)await page.screenshot({path:join(output,'question-formatted-answer.png')});
 await close();
 for(const action of ['question.cancel','question.supersede']){
  const seeded=await page.request.post(ready.url+'/fixture/reviews');
  assert.equal(seeded.status(),200,await seeded.text());
  const data=await seeded.json(),item=card(data.question.id),document=item.locator('.a-compact-artifact').first();
  await document.getByRole('button',{name:/^Preview here:/}).click();
  await expect(document.getByRole('heading',{name:'Pinned document v1'})).toBeVisible();
  const body=await document.locator('.a-inline-artifact-body').elementHandle();
  const target=await page.evaluate(id=>{
   const view=window.amplifier.getState().canvasWorkspace.views.find(v=>v.viewId.startsWith('inline-')&&v.resourceId===id);
   return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
  },data.markdown.id);
  const args={sessionId:ready.sessionId,id:data.question.id,expectedRevision:1,
   ...(action==='question.supersede'?{prompt:'Replacement review question',required:false,dependency:'Choose'}:{})};
  assert.equal((await reviewAction('canvas.views.dirty',{...target,dirty:true})).accepted,true);
  assert.equal((await reviewAction(action,args)).code,'canvas_view_dirty');
  assert.equal((await check()).questions.find(row=>row.id===data.question.id).status,'pending');
  assert.equal(await body.evaluate(element=>element.isConnected),true);
  assert.equal((await check()).sent.length,4,'Cancel/supersede cannot send an answer');
  assert.equal((await reviewAction('canvas.views.dirty',{...target,dirty:false})).accepted,true);
  const closed=await reviewAction(action,args);assert.equal(closed.accepted,true);
  await expect(item.getByText(action==='question.cancel'?'Question cancelled without an answer.':'Replaced by a newer question.')).toBeVisible();
  assert.equal(await body.evaluate(element=>element.isConnected),true,'Clean closure keeps the open review reachable');
  await document.getByRole('button',{name:/^Hide preview:/}).click();
  await expect(item).toHaveCount(0);await body.dispose();
  if(action==='question.supersede')assert.equal((await reviewAction('question.cancel',{
   sessionId:ready.sessionId,id:closed.result.id,expectedRevision:closed.result.revision,
  })).accepted,true);
 }
 // Transport timeout is not a failed answer save. Exercise both reconciliation
 // paths through the real service, with fresh chats and client-owned drafts.
 for(const variant of [{mode:'event',width:1280,touch:false},{mode:'passive',width:390,touch:true}]){
  const deliveryContext=await browser.newContext({viewport:{width:variant.width,height:900},hasTouch:variant.touch,
   extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  try{
   const p=await deliveryContext.newPage();p.on('pageerror',error=>errors.push(error.message));
   const seed=await p.request.post(ready.url+'/fixture/delivery-questions',{data:{mode:variant.mode}});
   assert.equal(seed.status(),200,await seed.text());const data=await seed.json();
   const observe=async()=>await (await p.request.get(ready.url+'/fixture/check',{params:{sessionId:data.sessionId}})).json();
   const first=p.locator(`[data-question-id="${data.first.id}"]`),second=p.locator(`[data-question-id="${data.second.id}"]`);
   const editor=p.getByRole('textbox',{name:'Message Amplifier',exact:true});
   const activate=async control=>{
    await control.scrollIntoViewIfNeeded();await expect(control).toBeEnabled();
    if(variant.touch)await control.tap();
    else{await control.focus();await expect(control).toBeFocused();await control.press('Enter')}
   };
   const bodyOf=request=>{const raw=request.postDataJSON();return raw.action==='shell.command'?raw.args:raw};
   const editComposer=async text=>{
    const saved=p.waitForResponse(response=>{
     if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
     const body=bodyOf(response.request());return body.action==='view.update'&&body.args?.patch?.draft===text&&response.ok();
    });
    await editor.fill(text);await saved;
   };
   const screenshot=async label=>{if(output)await p.screenshot({path:join(output,`question-delivery-${variant.mode}-${variant.width}-${label}.png`)})};
   const answer='**Use v1**.\n\nKeep the saved source.',nextAnswer='Keep this second question draft';
   const draft='Keep this independent composer draft',unconfirmed='Answer saved. Delivery is unconfirmed; it has not been sent again.';
   await p.goto(ready.url);await showFixtureConversation(p,{navPinned:false});
   assert.equal((await p.evaluate(id=>window.amplifier.dispatch('session.select',{id}),data.sessionId)).accepted,true);
   await expect(first).toBeVisible();await second.getByRole('textbox').fill(nextAnswer);
   await editComposer(draft);await first.getByRole('textbox').fill(answer);
   const before=await observe();
   await activate(first.getByRole('button',{name:'Submit answer',exact:true}));
   await expect(first.getByText(unconfirmed,{exact:true})).toBeVisible();
   const saved=await observe(),record=saved.questions.find(row=>row.id===data.first.id);
   assert.equal(record.status,'answered');assert.equal(record.delivery.status,'unknown');
   assert.equal(record.delivery.inputId,data.inputId);assert.equal(record.answer.text,answer);
   assert.equal(record.answer.questionRevision,1);assert.equal(record.answer.artifacts[0].version,1);
   assert.equal(record.answer.artifacts[0].id,data.document.id);
   assert.deepEqual(record.answer.artifacts,data.first.artifacts,'Answer freezes the exact original artifact binding');
   assert.equal(saved.sent.length,before.sent.length+1,'One explicit answer makes one runtime send');
   const exactMessage=rows=>rows.messages.filter(row=>row.inputId===data.inputId);
   assert.equal(exactMessage(saved).length,1);assert.equal(exactMessage(saved)[0].delivery.status,'unknown');
   await expect(editor).toHaveDraft(draft);await expect(second.getByRole('textbox')).toHaveValue(nextAnswer);
   await first.scrollIntoViewIfNeeded();await screenshot('unknown');
   await p.reload();await expect(first.getByText(unconfirmed,{exact:true})).toBeVisible();
   await expect(second.getByRole('textbox')).toHaveValue(nextAnswer);await expect(editor).toHaveDraft(draft);
   assert.deepEqual((await observe()).questions.find(row=>row.id===data.first.id),record,'Reload does not rewrite the saved answer or its unknown delivery');
   assert.equal((await observe()).sent.length,saved.sent.length,'Reload cannot resend the saved answer');
   const checkButton=first.getByRole('button',{name:'Check delivery',exact:true});
   const checkDelivery=async()=>{
    const response=p.waitForResponse(response=>{
     if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
     const body=bodyOf(response.request());
     return body.action==='conversation.delivery'&&body.args?.inputId===data.inputId&&body.args?.sessionId===data.sessionId;
    });
    await activate(checkButton);const result=await response;assert.equal(result.status(),200,await result.text());
   };
   const beforeProbe=await observe();await checkDelivery();
   await expect(checkButton).toBeEnabled();await expect(first.getByText(unconfirmed,{exact:true})).toBeVisible();
   const missing=await observe();
   assert.deepEqual(missing.questions.find(row=>row.id===data.first.id),record,'Missing runtime evidence must remain unknown');
   assert.deepEqual(missing.deliveryChecks.slice(beforeProbe.deliveryChecks.length),[{sessionId:data.sessionId,inputId:data.inputId}]);
   assert.deepEqual(missing.sent,beforeProbe.sent);assert.deepEqual(missing.starts,beforeProbe.starts);
   assert.deepEqual(missing.retries,[],'Check delivery is never a retry');
   assert.ok(await first.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Unknown delivery card fits the viewport');
   assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Delivery controls do not create page overflow');
   await screenshot('checked-unknown');
   // The unresolved delivery must not lock navigation or an unrelated message.
   await activate(p.getByRole('button',{name:'Home',exact:true}));
   await expect.poll(()=>p.evaluate(()=>window.amplifier.getState().view.workSurface)).toBe('home');
   await activate(p.getByRole('button',{name:'Back in navigation history',exact:true}));
   await expect(first.getByText(unconfirmed,{exact:true})).toBeVisible();
   await expect(editor).toHaveDraft(draft);await expect(second.getByRole('textbox')).toHaveValue(nextAnswer);
   const independent='Independent message while delivery remains unknown';
   await editComposer(independent);await activate(p.getByRole('button',{name:'Send message',exact:true}));
   await expect(editor).toHaveDraft('');
   await expect.poll(async()=>((await observe()).messages.find(row=>row.text===independent)||{}).delivery?.status).toBe('accepted');
   assert.equal((await observe()).questions.find(row=>row.id===data.first.id).delivery.status,'unknown','Acceptance of a different input cannot confirm this answer');
   await editComposer(draft);
   const pendingBefore=(await observe()).questions.find(row=>row.id===data.second.id);
   const pendingElement=await second.getByRole('textbox').elementHandle();
   await second.getByRole('textbox').focus();
   const beforeEvidence=await observe();
   const evidence=await p.request.post(ready.url+'/fixture/delivery-evidence',{
    data:{mode:variant.mode,sessionId:data.sessionId,inputId:data.inputId}});
   assert.equal(evidence.status(),200,await evidence.text());
   if(variant.mode==='passive'){
    assert.equal((await observe()).questions.find(row=>row.id===data.first.id).delivery.status,'unknown','Seeding runtime evidence alone cannot update the question');
    await checkDelivery();
   }
   await expect(first).toHaveCount(0);
   await expect(second.getByRole('textbox')).toHaveValue(nextAnswer);await expect(editor).toHaveDraft(draft);
   assert.equal(await pendingElement.evaluate(element=>element.isConnected),true,'Confirmation preserves the second question editor');
   if(!variant.touch)await expect(second.getByRole('textbox')).toBeFocused();
   await pendingElement.dispose();
   await expect(p.locator('.a-question-notice')).toContainText('A question needs your answer');
   const confirmed=await observe(),accepted=confirmed.questions.find(row=>row.id===data.first.id);
   assert.equal(accepted.delivery.status,'accepted');assert.equal(accepted.delivery.inputId,data.inputId);
   assert.deepEqual(accepted.answer,record.answer,'Confirmation changes delivery, not answer provenance or version');
   assert.deepEqual(accepted.artifacts,record.artifacts);
   assert.deepEqual(confirmed.questions.find(row=>row.id===data.second.id),pendingBefore);
   assert.equal(exactMessage(confirmed).length,1);assert.equal(exactMessage(confirmed)[0].id,exactMessage(saved)[0].id);
   assert.equal(exactMessage(confirmed)[0].delivery.status,'accepted');
   assert.deepEqual(confirmed.sent,beforeEvidence.sent,'Late acceptance cannot send anything');
   assert.deepEqual(confirmed.starts,beforeEvidence.starts);assert.deepEqual(confirmed.retries,[]);
   assert.deepEqual(confirmed.deliveryChecks.slice(beforeEvidence.deliveryChecks.length),
    variant.mode==='passive'?[{sessionId:data.sessionId,inputId:data.inputId}]:[],
    'Only the explicit passive check probes the exact saved input');
   assert.equal(confirmed.sent.filter(row=>row.inputId===data.inputId).length,1);
   // Accepted answers leave the active warning but retain exact review/history.
   await activate(p.getByRole('button',{name:'Chat',exact:true}));
   await activate(p.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}));
   await expect(p.getByRole('region',{name:'Conversation usage',exact:true}).getByRole('button',{name:'Refresh usage',exact:true})).toBeEnabled();
   await activate(p.getByText('Questions & answers',{exact:true}));
   await activate(p.locator('.a-question-history > summary'));
   await activate(p.locator('.a-question-history .a-question-item > summary'));
   await expect(first.getByText('Answer saved and delivered to Amplifier.',{exact:true})).toBeVisible();
   await expect(first.getByRole('button',{name:'Check delivery',exact:true})).toHaveCount(0);
   await activate(first.getByRole('button',{name:/^Preview here:/}));
   await expect(first.getByRole('heading',{name:'Delivery document v1',exact:true})).toBeVisible();
   await expect(first.getByRole('heading',{name:'Delivery document v2',exact:true})).toHaveCount(0);
   assert.ok(await first.evaluate(element=>element.scrollWidth<=element.clientWidth+1),'Confirmed answer fits the viewport');
   await first.scrollIntoViewIfNeeded();await screenshot('confirmed');
   layoutChecks.push({label:'question-delivery-reconciliation',...variant,inputId:data.inputId,
    answerSends:confirmed.sent.filter(row=>row.inputId===data.inputId).length,delivery:accepted.delivery.status});
   await activate(first.getByRole('button',{name:/^Hide preview:/}));
   const closed=p.waitForResponse(response=>{
    if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
    const body=bodyOf(response.request());return body.action==='view.update'&&body.args?.patch?.panel===null&&response.ok();
   });
   await activate(p.getByRole('button',{name:'Close panel',exact:true}));await closed;
   // Reload a confirmed answer too: the retained history must stay confirmed.
   await p.reload();
   await expect.poll(async()=>(await observe()).questions.find(row=>row.id===data.first.id).delivery.status).toBe('accepted');
   await expect(first).toHaveCount(0);await expect(second).toBeVisible();
   await expect(editor).toHaveDraft(draft);await expect(second.getByRole('textbox')).toHaveValue(nextAnswer);
   assert.deepEqual((await observe()).sent,confirmed.sent,'Confirmed reload cannot replay work');
  }finally{await deliveryContext.close()}
 }
 assert.deepEqual(errors,[]);
}catch(error){failure=error;throw error}
finally{
 await finishFixture({label:'Questions fixture',originalError:failure,
  close:[
   ['First question preview evidence',()=>firstPreview?.finish(failure)],
   ['Question review continuity evidence',()=>finishReviewEvidence?.()],
   ['Questions browser',()=>browser?.close()],
   ['Question layout evidence',async()=>{if(output){await mkdir(output,{recursive:true});await writeFile(join(output,'layout-checks.json'),JSON.stringify(layoutChecks,null,2)+'\n')}}]],
  stop:async()=>{
   const exit=await stopFixture();
   console.log('Questions fixture normal exit: '+JSON.stringify(exit));
   if(output)await writeFile(join(output,'fixture-exit.json'),JSON.stringify(exit,null,2)+'\n');
  }});
}
console.log('Durable questions browser passed: unique cards alongside handoff updates and reload, reconnect, explicit required/optional UI, option and free-text answers, cancellation, duplicate rejection, preserved draft, shared voice provenance, mobile layout, timeout retained across reload, late exact acceptance and passive delivery checks without replay on keyboard/touch.');
