// Removed-invoker regression through the real bounded peer fixture. The same
// source fails on predecessor static with BODY focus; no fake revoke/unmount.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {combinedFailure} from './chat-reference-harness.mjs';
import {mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const output=process.env.AMPLIFIER_PROOF_DIR;
assert.ok(output,'Retained artifact directory required');mkdirSync(output,{recursive:true});
const evidence=[];
async function exercise(width,dismiss){
 const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[
  fileURLToPath(new URL('../../tests/fixtures/peer_preview_ui_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))
 ],{stdio:['ignore','pipe','inherit']});
 const stopFixture=ownFixture(fixture,{label:'Peer preview focus fixture'});
 const cleanup={width,dismiss,browserClosed:false,fixtureExit:null};cleanupEvidence.push(cleanup);
 let browser,page,failure;
 try{
  const target=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture readiness timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n')){try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}}})});
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-peer-preview'}});
  page=await context.newPage();const errors=[],commands=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions')commands.push(r.postDataJSON().action)});
  await page.goto(target);await showFixtureConversation(page,{navPinned:false});
  const facts=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();
  const group=page.locator('.a-peer-run-summary');await expect(group).toHaveAttribute('aria-expanded','false');await group.press('Enter');
  const article=page.locator(`[data-message-id="${facts.originals[0]}"]`),source=article.getByRole('button',{name:'Preview source chat: Navigation design',exact:true}),disclosure=article.locator('.a-peer-summary');
  const pane=page.getByRole('log',{name:'Conversation messages',exact:true}),composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
  await page.evaluate(async()=>{const s=window.amplifier.getState();const r=await window.amplifier.dispatch('attachment.add',{sessionId:s.selectedSessionId,name:'focus-retained.txt',base64:btoa('Synthetic draft attachment')});if(!r.accepted)throw Error('Attachment not accepted');window.peerFocusDocument=crypto.randomUUID()});
  await composer.focus();await composer.press('Home');await composer.press('ArrowRight');await composer.press('ArrowRight');await composer.press('ArrowRight');await composer.press('Shift+ArrowRight');
  const selection=()=>composer.evaluate(n=>{
   const selected=getSelection();if(!selected?.rangeCount||!n.contains(selected.anchorNode)||!n.contains(selected.focusNode))return null;
   const offset=(node,pos)=>{const r=document.createRange();r.selectNodeContents(n);r.setEnd(node,pos);return r.toString().length};
   return {anchor:offset(selected.anchorNode,selected.anchorOffset),focus:offset(selected.focusNode,selected.focusOffset),text:selected.toString()};
  });
  const caret=await selection();assert.ok(caret&&caret.anchor!==caret.focus,'Real editor selection is required');
  const paneNode=await pane.elementHandle(),composerNode=await composer.elementHandle();
  const state=()=>page.evaluate(()=>{const s=window.amplifier.getState();return {id:s.selectedSessionId,draft:s.view.draft,attachments:s.sessions.find(x=>x.id===s.selectedSessionId)?.draftAttachments||s.draftAttachments||[],document:window.peerFocusDocument}});
  const before=await state();assert.equal(before.attachments.length,1);
  await source.scrollIntoViewIfNeeded();const top=await pane.evaluate(n=>n.scrollTop);
  await source.click();const panel=page.getByRole('dialog',{name:'Read-only source chat preview',exact:true});
  await expect(panel).toBeVisible();await expect(panel.getByRole('button',{name:'Open chat',exact:true})).toBeEnabled();
  assert.equal(await composerNode.evaluate(n=>n.isConnected&&!!n.closest('[inert]')),true);
  const response=await page.request.post(new URL('/api/fixture/change',target).href,{data:{kind:'revoke'},headers:{Origin:target}});assert.equal(response.ok(),true);
  await expect(panel.getByText('Source chat unavailable',{exact:true})).toBeVisible();
  // Parent is inert: observe removal without requesting an interaction there.
  await expect(article.locator('.a-peer-source')).toHaveCount(0);
  await expect(panel.locator('[data-preview-message-id]')).toHaveCount(0);
  await expect(panel.getByRole('button',{name:'Open chat',exact:true})).toBeDisabled();
  if(dismiss==='Escape')await panel.getByRole('button',{name:'Close source preview',exact:true}).press('Escape');
  else await panel.getByRole('button',{name:'Close source preview',exact:true}).click();
  await expect(panel).toHaveCount(0);
  await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
  const focus=await page.evaluate(()=>({tag:document.activeElement.tagName,label:document.activeElement.getAttribute('aria-label'),messageId:document.activeElement.closest('[data-message-id]')?.dataset.messageId||null,inert:!!document.activeElement.closest('[inert]')}));
  const row={width,dismiss,focus,sourceRemoved:true,caretBefore:caret};evidence.push(row);
  await page.screenshot({path:join(output,`peer-focus-${width}-${dismiss}.png`)});
  assert.equal(focus.tag,'BUTTON','PEER_FOCUS_RETURN_FAILED: dismissal must not leave BODY');
  await expect(disclosure).toBeFocused();assert.equal(focus.messageId,facts.originals[0]);assert.equal(focus.inert,false);
  assert.deepEqual(await state(),before);assert.equal(await pane.evaluate((n,old)=>n===old,paneNode),true);assert.equal(await composer.evaluate((n,old)=>n===old,composerNode),true);
  assert.ok(Math.abs(await pane.evaluate(n=>n.scrollTop)-top)<2);
  // Refocus without placing the caret: the rich editor's prior selection must
  // remain, rather than being reconstructed or moved by dismissal.
  await composer.evaluate(n=>n.focus({preventScroll:true}));row.caretAfter=await selection();assert.deepEqual(row.caretAfter,caret);
  if(width===390){
   // Successful engagement is not explicit dismissal: it must keep its chosen
   // destination, not focus a fallback in the prior receiving thread.
   const other=page.locator(`[data-message-id="${facts.originals[1]}"]`).getByRole('button',{name:/^Preview source chat: Accessibility review/});
   await other.click();await expect(panel.getByRole('button',{name:'Open chat',exact:true})).toBeEnabled();
   await panel.getByRole('button',{name:'Open chat',exact:true}).click();
   await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId)).toBe(facts.sourceB);
   await expect(panel).toHaveCount(0);
   await expect(page.getByRole('log',{name:'Conversation messages',exact:true})).toHaveAttribute('data-session-id',facts.sourceB);
   assert.equal(await page.evaluate(()=>window.peerFocusDocument),before.document);
   assert.notEqual(await page.evaluate(()=>document.activeElement.closest('[data-message-id]')?.dataset.messageId),facts.originals[0]);
   row.explicitOpenNotRedirected=true;
  }
  const final=await (await page.request.get(new URL('/api/fixture/peerFacts',target).href)).json();assert.deepEqual(final.effects,[]);assert.deepEqual(final.subprocesses,[]);assert.deepEqual(errors,[]);
  assert.equal(commands.filter(n=>n==='session.select').length,width===390?1:0);
  assert.ok(!commands.some(n=>['conversation.send','worker.spawn','session.warm','approval.respond'].includes(n)));
  row.passed=true;row.parentContinuity=true;row.noExecution=true;
 }catch(error){failure=error}
 finally{
  try{
   await finishFixture({label:'Peer preview focus fixture',close:[['Peer preview focus browser',async()=>{
    if(browser){await browser.close();cleanup.browserClosed=!browser.isConnected();assert.equal(cleanup.browserClosed,true)}
   }]],stop:async()=>{cleanup.fixtureExit=await stopFixture()}});
  }catch(error){failure=combinedFailure(failure,[error])}
 }
 if(failure)throw failure;
}
const cleanupEvidence=[];
try{
 await exercise(390,'Escape');await exercise(320,'Close');
 writeFileSync(join(output,'focus-result.json'),JSON.stringify({passed:true,evidence,cleanup:cleanupEvidence},null,2));
 console.log('PEER_FOCUS_RETURN_PASSED');
}catch(error){writeFileSync(join(output,'focus-result.json'),JSON.stringify({passed:false,error:String(error),evidence,cleanup:cleanupEvidence},null,2));throw error;}
