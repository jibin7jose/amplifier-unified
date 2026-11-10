import {readComposerDraft} from './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
const url=`http://127.0.0.1:${port}`;
const proof=process.env.AMPLIFIER_PROOF_DIR;if(proof)mkdirSync(proof,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/message_interactions_ui_server.py',import.meta.url)),String(port)],{stdio:'inherit'});
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const state=()=>page.evaluate(()=>window.amplifier.getState());
const control=async(op)=>{const response=await page.request.post(url+'/fixture/reactions',{data:{op}});assert.equal(response.status(),200);return response.json()};
const pendingRoutes=new Set(),measurements=[];
try{
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
 await page.goto(url);await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 const draft=page.getByRole('textbox',{name:'Message Amplifier'});await draft.fill('Preserve this draft');
 const original=page.locator('.a-message').filter({hasText:'Saved message 20:'});
 await original.getByRole('button',{name:'Reply to message',exact:true}).click();
 await page.getByLabel('Quoted reply',{exact:true}).waitFor();assert.ok((await readComposerDraft(draft)).includes('Preserve this draft'));
 await page.reload();await page.getByLabel('Quoted reply',{exact:true}).waitFor();assert.ok((await readComposerDraft(draft)).includes('Preserve this draft'));
 await page.getByRole('button',{name:'Remove quote',exact:true}).click();await page.getByLabel('Quoted reply',{exact:true}).waitFor({state:'hidden'});assert.equal((await readComposerDraft(draft)).trim(),'Preserve this draft');
 await original.getByRole('button',{name:'Reply to message',exact:true}).click();
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.status==='idle'&&c.messages.some(m=>m.text.includes('Preserve this draft')&&m.replyTo?.version===2)});
 const sent=page.locator('.a-message').filter({hasText:'Preserve this draft'});await sent.locator('.a-reply-quote').waitFor();
 if(proof)await sent.screenshot({path:join(proof,'desktop-reply.png')});
 await page.getByLabel('Quoted reply',{exact:true}).waitFor({state:'hidden'});
 await sent.getByRole('button',{name:'React to message',exact:true}).click();await sent.getByRole('button',{name:'Heart',exact:true}).click();await sent.getByRole('button',{name:'Remove Heart reaction',exact:true}).waitFor();
 // A separate client receives the same reaction, but not another client's draft.
 const second=await context.newPage();await second.goto(url);await second.waitForSelector('#amp-one');await showFixtureConversation(second);
 const sent2=second.locator('.a-message').filter({hasText:'Preserve this draft'});await sent2.getByRole('button',{name:'Remove Heart reaction',exact:true}).waitFor();
 await sent2.getByRole('button',{name:'Remove Heart reaction',exact:true}).click();await sent.getByRole('button',{name:'Remove Heart reaction',exact:true}).waitFor({state:'hidden'});
 for(let i=0;i<4;i++){await draft.fill('Later message '+i);await page.getByRole('button',{name:'Send message',exact:true}).click();await page.waitForFunction(text=>{const s=window.amplifier.getState(),c=s.sessions.find(x=>x.id===s.selectedSessionId);return c?.status==='idle'&&c.messages.some(m=>m.text===text)},'Later message '+i)}
 // Reload discards locally expanded history. The quote opens its original by loading older presentation pages.
 await page.reload();await page.locator('.a-reply-quote').waitFor();await sent.locator('.a-quoted-source').click();
 await page.waitForFunction(()=>document.activeElement?.dataset?.messageId&&document.activeElement.textContent.includes('Saved message 20:'));
 // Quote an assistant message at phone width and verify touch picker and draft removal.
 await page.setViewportSize({width:390,height:844});
 await sent.getByRole('button',{name:'React to message',exact:true}).click();
 const picker=sent.getByRole('group',{name:'Choose reaction'});await picker.waitFor();
 const bounds=await picker.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);
 await picker.getByRole('button',{name:'Heart',exact:true}).press('Escape');await picker.waitFor({state:'hidden'});
 const assistant=page.locator('.a-assistant').last();
 await assistant.getByRole('button',{name:'Reply to message',exact:true}).click();await page.getByLabel('Quoted reply',{exact:true}).waitFor();
 if(proof)await page.locator('.a-composer').screenshot({path:join(proof,'mobile-reply-preview.png')});
 await page.getByRole('button',{name:'Remove quote',exact:true}).click();
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();await assistant.getByRole('button',{name:'Celebrate',exact:true}).click();await assistant.getByRole('button',{name:'Remove Celebrate reaction',exact:true}).waitFor();
 if(proof)await assistant.locator('.a-message-actions').screenshot({path:join(proof,'mobile-reactions.png')});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // Feedback is local: deliberately hold the write before it can reach the host.
 await page.setViewportSize({width:1280,height:900});
 let heldResolve,heldCommand,holdNextReaction=true;const held=new Promise(resolve=>heldResolve=resolve);
 const holdReaction=route=>{
  if(route.request().postDataJSON()?.action==='session.rename'){pendingRoutes.add(route);heldCommand=route;return}
  if(route.request().postDataJSON()?.action==='message.reaction'&&holdNextReaction){holdNextReaction=false;pendingRoutes.add(route);heldResolve(route);return}
  return route.continue();
 };
 await page.route('**/api/actions',holdReaction);
 await page.evaluate(()=>{const s=window.amplifier.getState();window.heldRename=window.amplifier.dispatch('session.rename',{id:s.selectedSessionId,title:'Synthetic queue independence'}).catch(()=>{})});
 await expect.poll(()=>!!heldCommand).toBe(true);
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();
 const heart=assistant.getByRole('button',{name:'Heart',exact:true});
 await heart.evaluate(button=>{
  window.reactionFrame=null;
  button.addEventListener('click',()=>{
   const message=button.closest('[data-message-id]'),pane=message.closest('.a-messages');
   const body=message.querySelector('.a-message-body');
   const original=body.textContent,top=message.getBoundingClientRect().top,scroll=pane.scrollTop,started=performance.now();
   requestAnimationFrame(()=>window.reactionFrame={
    messageId:message.dataset.messageId,
    feedbackMs:performance.now()-started,selected:!!message.querySelector('[aria-label="Remove Heart reaction"][aria-pressed="true"]'),
    messageStable:message.isConnected&&message.querySelector('.a-message-body')===body&&body.textContent===original,scrollDelta:pane.scrollTop-scroll,
    topDelta:message.getBoundingClientRect().top-top
   });
  },{once:true,capture:true});
 });
 await heart.click();
 await page.waitForFunction(()=>window.reactionFrame!==null);
 const nextFrame=await page.evaluate(()=>window.reactionFrame);
 measurements.push({check:'held-response-first-frame',...nextFrame});
 assert.equal(nextFrame.selected,true,'The next animation frame must show the optimistic reaction before HTTP proceeds');
 assert.equal(nextFrame.messageStable,true,'Local feedback must keep the original message body and its DOM node');
 assert.equal(nextFrame.scrollDelta,0,'Reaction feedback must not move the reading position');
 assert.equal(nextFrame.topDelta,0,'The original message must stay in place');
 const heldRoute=await Promise.race([held,new Promise((_,reject)=>setTimeout(()=>reject(Error('Reaction request did not reach HTTP within 3 seconds')),3000))]);
 assert.ok(heldCommand,'Reaction reached HTTP independently of the held general command');
 if(proof)await assistant.screenshot({path:join(proof,'optimistic-reaction-held.png')});
 await assistant.getByRole('button',{name:'Remove Heart reaction',exact:true}).click();
 await expect(assistant.getByRole('button',{name:'Remove Heart reaction',exact:true})).toHaveCount(0);
 await expect(assistant.getByRole('button',{name:'Remove Celebrate reaction',exact:true})).toBeVisible();
 pendingRoutes.delete(heldRoute);await heldRoute.continue();
 pendingRoutes.delete(heldCommand);await heldCommand.continue();
 await page.unroute('**/api/actions',holdReaction);
 await expect.poll(async()=>(await control('inspect')).admissions.filter(row=>row.args.messageId===nextFrame.messageId&&row.args.emoji==='❤️'&&row.settledAt).length).toBe(2);
 await expect.poll(async()=>(await control('inspect')).annotations[nextFrame.messageId]?.reactions||[]).not.toContain('❤️');
 // A definite rejection rolls back only this emoji, not another reaction/draft.
 await draft.fill('Draft survives reaction recovery');
 const rejectReaction=route=>route.request().postDataJSON()?.action==='message.reaction'
  ?route.fulfill({status:409,json:{accepted:false,error:'Synthetic unavailable message'}}):route.continue();
 await page.route('**/api/actions',rejectReaction);
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();
 await assistant.getByRole('button',{name:'Heart',exact:true}).click();
 await expect(assistant.getByRole('alert')).toContainText('Synthetic unavailable message');
 await expect(assistant.getByRole('button',{name:'Remove Heart reaction',exact:true})).toHaveCount(0);
 await expect(assistant.getByRole('button',{name:'Remove Celebrate reaction',exact:true})).toBeVisible();
 assert.equal(await readComposerDraft(draft),'Draft survives reaction recovery');
 await page.unroute('**/api/actions',rejectReaction);
 // Lose a real committed response, not its mutation. Recovery is a read only.
 const beforeLost=(await control('inspect')).admissions.length;
 const loseReceipt=async route=>{
  if(route.request().postDataJSON()?.action!=='message.reaction')return route.continue();
  await route.fetch();await route.abort('connectionfailed');
 };
 await page.route('**/api/actions',loseReceipt);
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();
 await assistant.getByRole('button',{name:'Thumbs up',exact:true}).click();
 await expect(assistant.getByRole('alert')).toContainText('not confirmed');
 await page.unroute('**/api/actions',loseReceipt);
 await assistant.getByRole('button',{name:'Check saved reaction',exact:true}).click();
 await expect(assistant.getByRole('alert')).toHaveCount(0);
 await expect(assistant.getByRole('button',{name:'Remove Thumbs up reaction',exact:true})).not.toHaveAttribute('data-pending','true');
 assert.equal((await control('inspect')).admissions.length,beforeLost+1,'Unknown recovery must not replay the write');
 assert.equal(await readComposerDraft(draft),'Draft survives reaction recovery');
 // Navigation away/back reuses one in-page flight owner. A late response must
 // not dispatch a superseded removal after the newer click.
 let late;const holdAccepted=async route=>{
  if(route.request().postDataJSON()?.action!=='message.reaction')return route.continue();
  const response=await route.fetch();pendingRoutes.add(route);late={route,response};
 };
 await page.route('**/api/actions',holdAccepted);
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();
 await assistant.getByRole('button',{name:'Heart',exact:true}).click();
 await expect.poll(()=>!!late).toBe(true);
 await assistant.getByRole('button',{name:'Remove Heart reaction',exact:true}).click();
 await page.evaluate(async()=>{window.reactionSource=window.amplifier.getState().selectedSessionId;await window.amplifier.dispatch('session.draft',{})});
 await expect(page.locator('.a-message')).toHaveCount(0);
 await page.evaluate(()=>window.amplifier.dispatch('session.select',{id:window.reactionSource}));
 await showFixtureConversation(page);
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();
 await assistant.getByRole('button',{name:'Heart',exact:true}).click();
 const beforeLate=(await control('inspect')).admissions.length;
 pendingRoutes.delete(late.route);await late.route.fulfill({response:late.response});await page.unroute('**/api/actions',holdAccepted);
 await expect(assistant.getByRole('button',{name:'Remove Heart reaction',exact:true})).not.toHaveAttribute('data-pending','true');
 assert.equal((await control('inspect')).admissions.length,beforeLate,'Discarded row must not issue an old follow-up');
 // A real transport outage is uncertain, not rejected, and reconnect never
 // resubmits a write whose server outcome is unknown.
 const beforeOffline=(await control('inspect')).admissions.length;
 await context.setOffline(true);
 await assistant.getByRole('button',{name:'React to message',exact:true}).click();
 await assistant.getByRole('button',{name:'Sad',exact:true}).click();
 await expect(assistant.getByRole('button',{name:'Check saved reaction',exact:true})).toBeVisible();
 await assistant.getByRole('button',{name:'Remove Sad reaction',exact:true}).click();
 await expect(assistant.getByRole('button',{name:'Check saved reaction',exact:true})).toBeVisible();
 await context.setOffline(false);
 await expect(assistant.getByRole('alert')).toContainText('unknown');
 await page.reload();await showFixtureConversation(page);
 await expect(assistant.getByRole('button',{name:'Check saved reaction',exact:true})).toBeVisible();
 assert.equal((await control('inspect')).admissions.length,beforeOffline,'Reconnect/reload must not repeat uncertain writes');
 // The real host must persist/broadcast a reaction while a synthetic send is
 // blocked. The deliberately broken REACTION_TEST_TURN_GATE control fails here.
 const before=await control('hold');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Synthetic response held for reaction test');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(async()=>(await control('inspect')).blocked).toBe(true);
 const stable=page.locator('.a-message').filter({hasText:'Saved message 74:'});
 const messageId=await stable.getAttribute('data-message-id');
 await stable.getByRole('button',{name:'React to message',exact:true}).click();
 const started=Date.now();
 await stable.getByRole('button',{name:'Check',exact:true}).focus();
 await page.keyboard.press('Enter');
 await expect.poll(async()=>(await control('inspect')).annotations[messageId]?.reactions||[],{
  timeout:3000,message:'Reaction must commit before the synthetic generation is released'
 }).toContain('✅');
 const admitted=await control('inspect'),admission=admitted.admissions.findLast(row=>row.args.messageId===messageId&&row.args.emoji==='✅');
 assert.equal(admitted.blocked,true);assert.equal(admitted.sends,before.sends);
 assert.equal(admission.generationBlocked,true);assert.ok(admission.settledAt);
 measurements.push({check:'commit-during-blocked-generation',clickToHostArrivalMs:admission.arrivedAt*1000-started,hostDispatchMs:(admission.settledAt-admission.arrivedAt)*1000});
 await second.reload();
 await expect(second.locator(`[data-message-id="${messageId}"]`).getByRole('button',{name:'Remove Check reaction',exact:true})).toBeVisible();
 // Stop is a non-reaction command: a pending reaction must not hold its admission
 // or clear the durable interruption revision when its late response arrives.
 let stopHeld,stopHeldResponse,stopHeldReceipt;
 const holdAtStop=async route=>{
  if(route.request().postDataJSON()?.action==='message.reaction'){
   pendingRoutes.add(route);stopHeld=route;
   stopHeldResponse=await route.fetch();stopHeldReceipt=await stopHeldResponse.json();return;
  }
  return route.continue();
 };
 await page.route('**/api/actions',holdAtStop);
 await stable.getByRole('button',{name:'React to message',exact:true}).click();
 await stable.getByRole('button',{name:'Heart',exact:true}).click();
 await expect.poll(()=>!!stopHeldReceipt).toBe(true);
 assert.equal(stopHeldReceipt.accepted,true);
 const stopState=await state(),preStop=stopState.sessions.find(row=>row.id===before.sessionId)?.interruptionRevision||0;
 const stopResponse=page.waitForResponse(response=>response.request().method()==='POST'&&
  new URL(response.url()).pathname==='/api/actions'&&response.request().postDataJSON()?.action==='conversation.stop'&&
  response.request().postDataJSON()?.args?.sessionId===before.sessionId&&response.request().headers()['x-amplifier-client']===stopState.client.id);
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 const stopReceipt=await (await stopResponse).json();assert.equal(stopReceipt.accepted,true);
 assert.ok(stopReceipt.stateRevision>stopHeldReceipt.stateRevision);
 await expect.poll(async()=>(await state()).revision).toBeGreaterThanOrEqual(stopReceipt.stateRevision);
 assert.equal((await state()).sessions.find(row=>row.id===before.sessionId).interruptionRevision,preStop+1);
 pendingRoutes.delete(stopHeld);await stopHeld.fulfill({response:stopHeldResponse});await page.unroute('**/api/actions',holdAtStop);
 await expect(stable.getByRole('button',{name:'Remove Heart reaction',exact:true})).not.toHaveAttribute('data-pending','true');
 assert.equal((await state()).sessions.find(row=>row.id===before.sessionId).interruptionRevision,preStop+1);
 measurements.push({check:'stop-before-held-reaction-response',reactionRevision:stopHeldReceipt.stateRevision,
  stopRevision:stopReceipt.stateRevision,interruptionRevision:preStop+1});
 await control('release');
 await page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(row=>row.id===s.selectedSessionId)?.status==='idle'});
 assert.deepEqual(errors,[]);console.log('Verified: quoted draft preservation/reload/removal/send, source navigation, assistant replies, persistent cross-client emoji add/remove, mobile affordances.');
}finally{
 if(proof)writeFileSync(join(proof,'reaction-latency.json'),JSON.stringify(measurements,null,2));
 for(const route of pendingRoutes)await route.abort().catch(()=>{});
 await control('release').catch(()=>{});
 await browser.close();fixture.kill();
}
