// Synthetic real-host regression for different pins moved in adjacent sequences.
// No model, live history, user browser, reconnect action or mutation replay.
import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/recent_visibility_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-pin-rapid-reorder';
const checks=[],commands=[],errors=[];
const negative=process.argv.includes('--negative-control');
let browser,page,releaseReceipt;
try{
 const boot=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture timeout')),45000);let text='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});
 });
 browser=await chromium.launch({headless:true});
 const options={extraHTTPHeaders:{Authorization:'Bearer fixture-recent-visibility-token'},viewport:{width:1440,height:1000}};
 const context=await browser.newContext(options);page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  if(!request.url().endsWith('/api/actions')||request.method()!=='POST')return;
  const body=request.postDataJSON();
  if(body.action==='navigation.pinOrder'||body.action==='shell.command'&&body.args.action==='navigation.pinOrder')commands.push(body);
 });
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const shellPins=()=>page.evaluate(()=>{
  const snapshot=window.amplifier.getShellState()?.snapshots?.chats;
  return {ids:snapshot?.pinnedItemIds,items:snapshot?.pinnedNavigation?.items?.map(item=>item.id),pending:snapshot?.navigationResourcePending?.pinnedNavigation||null};
 });
 const pinned=p=>p.locator('[data-shell-instance=chats] [data-sidebar-section=pinned]');
 const order=p=>pinned(p).locator('[data-order-id]').evaluateAll(rows=>rows.map(row=>row.dataset.orderId));
 const grip=(p,id)=>pinned(p).locator(`[data-order-id="${id}"] .a-reorder-grip`);
 const alerts=()=>pinned(page).getByRole('alert');
 await page.goto(boot.url);await showFixtureConversation(page,{navPinned:true});
 const metrics=async()=>{const response=await page.request.get(boot.url+'/fixture/metrics');assert.ok(response.ok());return response.json()};
 const control=async data=>{const response=await page.request.post(boot.url+'/fixture/control',{data});assert.ok(response.ok());return response.json()};
 const initial=await metrics();
 const recent=page.locator('[data-shell-instance=chats] [data-sidebar-section=recent]');
 await expect.poll(()=>recent.locator('.a-nav-chat[data-session-id]').count()).toBeGreaterThanOrEqual(3);
 const ordinary=(await recent.locator('.a-nav-chat[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId))).filter(id=>id!==initial.pinned).slice(0,2);
 assert.equal(ordinary.length,2);
 for(const id of ordinary)assert.notEqual((await action('session.pin',{id,pinned:true})).accepted,false);
 const workspace=(await state()).selectedWorkspaceId;
 assert.notEqual((await action('workspace.pin',{id:workspace,pinned:true})).accepted,false);
 const start=['session:'+initial.pinned,...ordinary.map(id=>'session:'+id),'workspace:'+workspace];
 await expect.poll(()=>order(page)).toEqual(start);
 const selection=(await state()).selectedSessionId;
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill('Keep this draft through adjacent pin moves');
 const otherContext=await browser.newContext(options),other=await otherContext.newPage();
 await other.goto(boot.url);await showFixtureConversation(other,{navPinned:true});
 await expect.poll(()=>order(other)).toEqual(start);
 const expectedMove=(ids,source,target)=>{const result=ids.filter(id=>id!==source);result.splice(ids.indexOf(target),0,source);return result};
 const settled=async(expected,id)=>{
  await expect.poll(async()=>(await state()).pinnedItemIds).toEqual(expected);
  await expect.poll(()=>order(page)).toEqual(expected);
  await expect(grip(page,id)).not.toHaveAttribute('aria-disabled','true');
  await expect(alerts()).toHaveCount(0);
  await expect.poll(()=>order(other)).toEqual(expected);
 };
 const startDrag=async(source,target)=>{
  await expect(grip(page,source)).not.toHaveAttribute('aria-disabled','true');
  const from=await grip(page,source).boundingBox(),to=await pinned(page).locator(`[data-order-id="${target}"]`).boundingBox();
  assert.ok(from&&to);await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();
  await page.mouse.move(from.x+from.width/2,to.y+to.height/2,{steps:3});
 };
 const pointerMove=async(source,target)=>{await startDrag(source,target);await page.mouse.up()};
 let expected=start;
 // Two different items, then a second adjacent sequence. No arbitrary sleeps.
 for(const [source,target] of [[start[1],start[0]],[start[2],start[1]],[start[0],start[2]],[start[1],start[0]]]){
  expected=expectedMove(expected,source,target);await pointerMove(source,target);await settled(expected,source);
  checks.push({gesture:'pointer',source,target,order:expected});
 }
 assert.equal(commands.length,4);
 // A new canonical order from another client can arrive with the old page.
 // Moving a different row against those mismatched inputs is the old race.
 const raceBefore=expected,clientId=(await state()).client.id,raceCount=commands.length;
 const concurrent=[raceBefore[2],raceBefore[0],raceBefore[1],...raceBefore.slice(3)];
 await control({holdPinsFor:clientId});
 await other.evaluate(ids=>window.amplifier.dispatch('navigation.pinOrder',{ids}),concurrent);
 await expect.poll(async()=>(await shellPins()).ids).toEqual(concurrent);
 await expect.poll(async()=>(await shellPins()).pending?.status).toBe('pending');
 assert.deepEqual((await shellPins()).items,raceBefore);
 const raceSource=raceBefore[0],raceTarget=raceBefore[1],raceFinal=expectedMove(concurrent,raceSource,raceTarget);
 if(negative){
  await expect(grip(page,raceSource)).not.toHaveAttribute('aria-disabled','true');
  await grip(page,raceSource).focus();await page.keyboard.press('ArrowDown');
  await expect.poll(()=>commands.length).toBe(raceCount+1);
  await expect.poll(async()=>(await state()).pinnedItemIds).toEqual(raceFinal);
  await control({holdPinsFor:null});
  await expect.poll(shellPins).toEqual({ids:raceFinal,items:raceFinal,pending:null});
  const reported='Pin order was saved, but its display has not arrived. Reconnect to read the current order.';
  await expect(alerts()).toHaveText(reported,{timeout:15000});
  assert.equal(commands.length,raceCount+1);await expect.poll(()=>order(other)).toEqual(raceFinal);
  await mkdir(output,{recursive:true});await page.screenshot({path:output+'/pin-race-negative.png'});
  const result={status:'negative-control-reproduced',error:reported,canonicalOrder:raceFinal,displayOrder:(await shellPins()).items,mutations:commands.length-raceCount,checks};
  await writeFile(output+'/pin-rapid-result.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
 }else{
 await expect(grip(page,raceSource)).toHaveAttribute('aria-disabled','true');
 await grip(page,raceSource).focus();await page.keyboard.press('ArrowDown');assert.equal(commands.length,raceCount);
 await control({holdPinsFor:null});await expect(grip(page,raceSource)).not.toHaveAttribute('aria-disabled','true');
 await expect.poll(()=>order(page)).toEqual(concurrent);
 await grip(page,raceSource).focus();await page.keyboard.press('ArrowDown');
 expected=raceFinal;await settled(expected,raceSource);
 checks.push({gesture:'keyboard',concurrentOrder:true,stalePageBlocked:true,order:expected});
 // Pending state alone (same IDs/order key) must cancel a held pointer preview.
 const dragCount=commands.length,dragSource=expected[1];
 const dragFrom=await grip(page,dragSource).boundingBox(),dragTo=await pinned(page).locator(`[data-order-id="${expected[0]}"]`).boundingBox();
 await page.mouse.move(dragFrom.x+dragFrom.width/2,dragFrom.y+dragFrom.height/2);await page.mouse.down();
 await page.mouse.move(dragFrom.x+dragFrom.width/2,dragTo.y+dragTo.height/2,{steps:3});
 await expect(pinned(page).locator('[data-reordering=true]')).toHaveCount(1);
 await control({holdPinsFor:clientId,invalidatePins:true});
 await expect.poll(async()=>(await shellPins()).pending?.status).toBe('pending');
 await expect(pinned(page).locator('[data-reordering=true]')).toHaveCount(0);
 await page.mouse.up();assert.equal(commands.length,dragCount);
 await control({holdPinsFor:null});await settled(expected,dragSource);
 checks.push({heldDragCancelledOnPending:true,mutations:0});
 // Hold only the projected page. Receipt/context can arrive with old rows;
 // releasing the page wakes the existing owner at the same app revision.
 const heldPageOrder=expected;
 await control({holdPinsFor:clientId});
 const heldSource=expected[1],heldTarget=expected[0],heldCount=commands.length;
 expected=expectedMove(expected,heldSource,heldTarget);
 await pointerMove(heldSource,heldTarget);
 await expect.poll(async()=>(await shellPins()).ids).toEqual(expected);
 await expect.poll(async()=>(await shellPins()).pending?.status).toBe('pending');
 assert.deepEqual((await shellPins()).items,heldPageOrder);
 const different=expected[2],afterHeld=expectedMove(expected,different,expected[1]);
 await expect(grip(page,different)).not.toHaveAttribute('aria-disabled','true');
 await grip(page,different).focus();await page.keyboard.press('ArrowUp');assert.equal(commands.length,heldCount+1);
 await expect.poll(()=>order(page)).toEqual(afterHeld);
 await control({holdPinsFor:null});
 expected=afterHeld;await settled(expected,different);
 checks.push({gesture:'keyboard',projection:'delayed',otherItemQueued:true,order:expected});
 // Let real persistence/stream publication finish, but hold one HTTP receipt.
 // Another item's gesture must not sneak in while that outcome is outstanding.
 let receiptHeld=false;
 const held=new Promise(resolve=>{releaseReceipt=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(!receiptHeld&&body.action==='shell.command'&&body.args.action==='navigation.pinOrder'){
   const response=await route.fetch();receiptHeld=true;await held;await route.fulfill({response});
  }else await route.continue();
 });
 const source=expected[2],target=expected[1],beforeCount=commands.length;
 expected=expectedMove(expected,source,target);
 await grip(page,source).focus();await page.keyboard.press('ArrowUp');
 await expect.poll(()=>receiptHeld).toBe(true);
 await expect.poll(shellPins).toEqual({ids:expected,items:expected,pending:null});
 const otherId=expected[0],afterReceipt=expectedMove(expected,otherId,expected[1]);
 await expect(grip(page,otherId)).not.toHaveAttribute('aria-disabled','true');
 await grip(page,otherId).focus();await page.keyboard.press('ArrowDown');assert.equal(commands.length,beforeCount+1);
 await expect.poll(()=>order(page)).toEqual(afterReceipt);
 releaseReceipt();releaseReceipt=null;expected=afterReceipt;await settled(expected,otherId);await page.unroute('**/api/actions');
 checks.push({gesture:'keyboard',receipt:'delayed',otherItemQueued:true,order:expected});
 const next=expected[0];expected=expectedMove(expected,next,expected[1]);
 await grip(page,next).focus();await page.keyboard.press('Alt+ArrowDown');await settled(expected,next);
 checks.push({gesture:'keyboard',source:next,order:expected});
 // Two adjacent cycles: two DIFFERENT pointer moves before A reaches the host,
 // so neither its HTTP response nor authoritative readback can arrive first.
 for(let cycle=0;cycle<2;cycle++){
  let intercepted=false;
  const before=expected,count=commands.length,hold=new Promise(resolve=>{releaseReceipt=resolve});
  await page.route('**/api/actions',async route=>{
   const body=route.request().postDataJSON();
   if(!intercepted&&body.action==='shell.command'&&body.args.action==='navigation.pinOrder'){
    intercepted=true;await hold;
   }
   await route.continue();
  });
  const a=before[1],b=before[2],first=expectedMove(before,a,before[0]),final=expectedMove(first,b,first[0]);
  await pointerMove(a,before[0]);await expect.poll(()=>intercepted).toBe(true);
  await pointerMove(b,first[0]);await expect.poll(()=>order(page)).toEqual(final);
  assert.deepEqual((await state()).pinnedItemIds,before);assert.equal(commands.length,count+1);
  releaseReceipt();releaseReceipt=null;expected=final;await settled(expected,b);await page.unroute('**/api/actions');
  assert.equal(commands.length,count+2);checks.push({cycle,twoDistinctMovesBeforeResponseAndReadback:true,order:expected});
 }
 // B stays pointer-held across A's successful receipt and React publication.
 let crossing=false;
 const crossCount=commands.length,crossHold=new Promise(resolve=>{releaseReceipt=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(!crossing&&body.action==='shell.command'&&body.args.action==='navigation.pinOrder'){crossing=true;await crossHold}
  await route.continue();
 });
 const crossA=expected[1],crossB=expected[2],crossFirst=expectedMove(expected,crossA,expected[0]),crossFinal=expectedMove(crossFirst,crossB,crossFirst[0]);
 const crossAnnouncement=(await grip(page,crossA).getAttribute('aria-label')).slice('Reorder '.length)+' moved to position 1.';
 await pointerMove(crossA,expected[0]);await expect.poll(()=>crossing).toBe(true);
 await startDrag(crossB,crossFirst[0]);await expect(pinned(page).locator('[data-reordering=true]')).toHaveCount(1);
 releaseReceipt();releaseReceipt=null;
 await expect.poll(shellPins).toEqual({ids:crossFirst,items:crossFirst,pending:null});
 await expect(pinned(page).locator('.a-sr-only[role=status]')).toHaveText(crossAnnouncement);
 await expect(pinned(page).locator('[data-reordering=true]')).toHaveCount(1);
 await page.mouse.up();expected=crossFinal;await settled(expected,crossB);await page.unroute('**/api/actions');
 assert.equal(commands.length,crossCount+2);checks.push({heldDifferentDragAcrossReceipt:true,order:expected});
 // Repeated explicit same-order intent is semantically idempotent.
 await action('navigation.pinOrder',{ids:expected});await settled(expected,next);
 // A saved order with a genuinely missing page stays honest, and an explicit
 // bounded read repairs display without a reconnect or replaying the mutation.
 await control({holdPinsFor:clientId});
 const missingSource=expected[1],missingCount=commands.length;
 expected=expectedMove(expected,missingSource,expected[0]);
 await grip(page,missingSource).focus();await page.keyboard.press('ArrowUp');
 await expect.poll(async()=>(await shellPins()).pending?.status).toBe('pending');
 await expect(pinned(page).getByRole('button',{name:'Refresh pinned list',exact:true})).toBeVisible({timeout:15000});
 assert.equal(commands.length,missingCount+1);
 const readResponse=page.waitForResponse(response=>response.url().endsWith('/api/shell/resources')&&response.request().method()==='POST');
 await pinned(page).getByRole('button',{name:'Refresh pinned list',exact:true}).click();
 await readResponse;
 await expect(pinned(page).getByRole('button',{name:'Reading pins…',exact:true})).toBeVisible();
 await control({holdPinsFor:null});
 await settled(expected,missingSource);assert.equal(commands.length,missingCount+1);
 checks.push({displayReadRecovery:true,coldPageAfterReadResponse:true,reconnect:false,replayed:false,order:expected});
 assert.deepEqual([...new Set(expected)].sort(),[...start].sort());
 assert.equal((await state()).selectedSessionId,selection);await expect(composer).toHaveDraft('Keep this draft through adjacent pin moves');
 await mkdir(output,{recursive:true});await page.mouse.move(1400,990);await page.keyboard.press('Escape');await page.screenshot({path:output+'/pin-rapid-desktop.png'});
 await page.reload();await expect.poll(()=>order(page)).toEqual(expected);await expect.poll(()=>order(other)).toEqual(expected);
 await expect(composer).toHaveDraft('Keep this draft through adjacent pin moves');
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await expect(grip(page,expected[1])).toBeVisible();
 const narrowSource=expected[1],narrowTarget=expected[0];expected=expectedMove(expected,narrowSource,narrowTarget);
 await pointerMove(narrowSource,narrowTarget);await settled(expected,narrowSource);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.mouse.move(380,830);await page.screenshot({path:output+'/pin-rapid-narrow.png'});
 // Lose a mutation's final HTTP acknowledgment after the server commits it.
 // The UI must not invent a saved receipt or retry the command.
 let lost=false;const loseReceipt=new Promise(resolve=>{releaseReceipt=resolve});
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(!lost&&body.action==='shell.command'&&body.args.action==='navigation.pinOrder'){
   await route.fetch();lost=true;await loseReceipt;await route.abort('failed');
  }else await route.continue();
 });
 const lostCount=commands.length,lostSource=expected[1];expected=expectedMove(expected,lostSource,expected[0]);
 await grip(page,lostSource).focus();await page.keyboard.press('ArrowUp');
 await expect.poll(()=>lost).toBe(true);await expect.poll(shellPins).toEqual({ids:expected,items:expected,pending:null});
 await startDrag(expected[2],expected[0]);await expect(pinned(page).locator('[data-reordering=true]')).toHaveCount(1);
 releaseReceipt();releaseReceipt=null;
 await expect(alerts()).toHaveCount(1);assert.doesNotMatch(await alerts().innerText(),/Pin order was saved/);
 await expect(alerts()).toContainText('Later queued moves were not sent.');
 await expect(pinned(page).locator('[data-reordering=true]')).toHaveCount(0);await page.mouse.up();
 await expect.poll(async()=>(await state()).pinnedItemIds).toEqual(expected);
 assert.equal(commands.length,lostCount+1);await page.unroute('**/api/actions');
 await expect.poll(()=>order(other)).toEqual(expected);
 checks.push({lostAcknowledgment:true,heldDifferentDragCancelled:true,unknownReported:true,replayed:false,order:expected});
 assert.deepEqual((await metrics()).runtimeCalls,[]);assert.deepEqual(errors,[]);
 const result={status:'passed',applicationModule:boot.applicationModule,checks,order:expected,commandCount:commands.length,runtimeCalls:[],limits:'Synthetic source host; no inference or native desktop acceptance.'};
 await writeFile(output+'/pin-rapid-result.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
 }
}catch(error){
 await mkdir(output,{recursive:true});if(page)await page.screenshot({path:output+'/pin-rapid-failure.png'});
 await writeFile(output+'/pin-rapid-result.json',JSON.stringify({status:'failed',error:String(error),checks,commands,errors},null,2)+'\n');throw error;
}finally{releaseReceipt?.();await browser?.close();fixture.kill('SIGTERM')}
