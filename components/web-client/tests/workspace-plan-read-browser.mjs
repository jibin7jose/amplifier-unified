import {showFixtureConversation} from './browser-chat-pages.mjs';
// A delayed optional read must not hold public workspace paging or filtering.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[
 '-u',root+'tests/fixtures/workspace_plan_read_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,releasePlan,releaseMutation;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[],plans=[],mutations=[];
 page.on('pageerror',error=>errors.push(error.message));
 const inspect=()=>page.request.get(url+'/fixture/plan-read').then(r=>r.json());
 let holdPlan=true,holdMutation=false;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON()||{};
  if(body.action==='session.plans'){
   const response=await route.fetch(),receipt=await response.json();
   assert.equal(receipt.accepted,true);assert.equal('stateRevision' in receipt,false);assert.equal('_stateIndependent' in receipt,false);
   plans.push(receipt);
   if(holdPlan){holdPlan=false;releasePlan=()=>route.fulfill({response});return}
   await route.fulfill({response});return;
  }
  if(body.action==='view.update'&&body.args.patch.navWidth){
   mutations.push(body.args.patch.navWidth);
   if(holdMutation){holdMutation=false;const response=await route.fetch();releaseMutation=()=>route.fulfill({response});return}
  }
  await route.continue();
 });
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});await expect(page.getByRole('textbox',{name:'Message Amplifier',exact:true})).toBeEditable();
 const before=await inspect();
 await page.locator('[data-part="header"] .a-work-crumb:not(.a-work-home-crumb)').click();
 const rows=page.locator('.a-work-browser .a-nav-chats [data-session-id]');
 await expect(rows).toHaveCount(40);await expect.poll(()=>!!releasePlan).toBe(true);
 const firstIds=await rows.evaluateAll(elements=>elements.map(el=>el.dataset.sessionId));
 // Keep the first real plan response held through both public gestures.
 await page.getByRole('button',{name:'Show more conversations',exact:true}).click();
 await expect(rows).toHaveCount(16);
 const secondIds=await rows.evaluateAll(elements=>elements.map(el=>el.dataset.sessionId));
 assert.ok(secondIds.every(id=>!firstIds.includes(id)));
 await page.getByRole('searchbox',{name:'Filter conversations',exact:true}).fill(secondIds[0]);
 await expect(rows).toHaveCount(1);await expect(rows.first()).toHaveAttribute('data-session-id',secondIds[0]);
 await expect(page.locator('.a-workspace-plan')).toContainText('1 of 1 steps done');
 assert.ok(releasePlan,'The held response was not released to allow navigation');
 await releasePlan();releasePlan=null;
 await expect(rows).toHaveCount(1);await expect(rows.first()).toHaveAttribute('data-session-id',secondIds[0]);
 await page.locator('.a-workspace-plan > summary').click();
 await expect(page.locator('.a-workspace-plan ol')).toContainText('Accepted task');
 assert.ok(plans.some(receipt=>receipt.result.items.some(row=>row.sessionId===secondIds[0]&&row.plan?.items?.[0]?.content.startsWith('Accepted task'))));
 // Control: ordinary mutations still wait for the preceding accepted receipt.
 holdMutation=true;
 await page.evaluate(()=>{window.__firstMutation=window.amplifier.dispatch('view.update',{patch:{navWidth:301}})});
 await expect.poll(()=>!!releaseMutation).toBe(true);
 await page.evaluate(()=>{window.__secondMutation=window.amplifier.dispatch('view.update',{patch:{navWidth:302}})});
 // A browser roundtrip flushes runnable promise work; no arbitrary timeout.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.deepEqual(mutations,[301]);
 await releaseMutation();releaseMutation=null;
 await page.evaluate(()=>Promise.all([window.__firstMutation,window.__secondMutation]));
 assert.deepEqual(mutations,[301,302]);
 const after=await inspect();assert.deepEqual(after.canonical,before.canonical);assert.deepEqual(after.admittedIds,before.admittedIds);assert.equal(before.admittedIds.length,1);assert.deepEqual(after.runtimeCalls,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,heldReadDidNotBlockPagingOrFiltering:true,staleReadDidNotReplaceScope:true,canonicalPlanRetained:true,mutationReceiptsRemainOrdered:true,admittedSessions:1,runtimeEffects:0}));
}finally{
 await releasePlan?.().catch(()=>{});await releaseMutation?.().catch(()=>{});await browser?.close();
 if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch{}finally{clearTimeout(timer)}}
}
