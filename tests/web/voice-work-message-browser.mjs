import {showFixtureConversation} from './browser-chat-pages.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/voice_work_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),30000);fixture.once('exit',()=>{clearTimeout(timer);reject(Error('Fixture exited'))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const pane=page.locator('.a-messages'),row=id=>pane.locator(`[data-message-id="${id}"]`);
 const group=input=>pane.locator(`[data-voice-work-group="true"][data-work-input-id="${input}"]`);
 const first=group('voice:fixture-call:request'),second=group('voice:fixture-call:second');
 const summary=first.locator(':scope > button').first();
 const inspect=()=>page.evaluate(async()=>await(await fetch('/fixture/voice-work')).json());
 const fixturePost=(path,body={})=>page.evaluate(async({path,body})=>{const response=await fetch('/fixture/voice-work/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw Error('Fixture mutation failed: '+response.status);return response.json();},{path,body});
 const standalone=id=>row(id).evaluate(element=>!element.closest('[data-voice-work-group]'));
 const workReady=()=>page.waitForFunction(()=>{try{const receipt=JSON.parse(document.querySelector('.a-messages')?.dataset.workProjection);return receipt.status==='ready'&&receipt.current===true;}catch{return false;}});
 await expect(first).toHaveCount(1);await expect(second).toHaveCount(1);
 await expect(summary).toHaveAttribute('aria-expanded','false');
 await expect(summary).toContainText(/Worked for|Work details/);
 await expect(row('backend-result')).toHaveCount(0);
 await expect(row('backend-update')).toHaveCount(0);
 await expect(row('spoken-result')).toContainText('The requested change is complete.');
 await expect(row('spoken-result')).toHaveClass(/a-assistant/);
 assert.equal(await standalone('spoken-result'),true);
 await expect(row('spoken-progress-two')).toHaveClass(/a-assistant/);
 assert.equal(await standalone('spoken-progress-two'),true);
 await expect(row('typed-result')).toContainText('The ordinary typed answer remains visible.');
 assert.equal(await standalone('typed-result'),true);
 await expect(row('unknown-origin')).toContainText('A retained answer with unknown provenance.');
 assert.equal(await standalone('unknown-origin'),true);
 // Missing old work gets a neutral disclosure with no fabricated timing,
 // never attachment to an unrelated loaded turn or a second kind of card.
 const orphan=group('voice:older-call:unloaded'),orphanSummary=orphan.locator(':scope > button').first();
 await expect(orphan).toHaveCount(1);await expect(orphanSummary).toHaveText(/Work details/);
 await expect(orphanSummary).not.toContainText(/Worked for|Working|tokens|seconds|\d+\s*[ms]/);
 await expect(row('orphan-backend')).toHaveCount(0);
 await orphanSummary.click();await expect(orphan.locator('[data-message-id="orphan-backend"]')).toHaveCount(1);
 await expect(orphan.getByRole('button',{name:/^Amplifier (result|work update)/})).toHaveCount(0);
 await orphanSummary.click();
 await summary.focus();await page.keyboard.press('Enter');
 await expect(summary).toHaveAttribute('aria-expanded','true');
 await expect(first.locator('[data-message-id="backend-update"]')).toHaveCount(1);
 await expect(first.locator('[data-message-id="backend-result"]')).toHaveCount(1);
 await expect(first.locator('[data-message-id="backend-second"]')).toHaveCount(0);
 await expect(first.getByRole('button',{name:/^Amplifier (result|work update)/})).toHaveCount(0);
 await expect(row('backend-result').locator('strong')).toHaveText('The requested change is complete.');
 await expect(row('backend-result')).not.toContainText('via call');
 await row('backend-result').getByRole('button',{name:'Copy message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('**The requested change is complete.**');
 // Main quotes directly from rendered text; nested work uses the same action.
 await expect(row('backend-result').getByRole('button',{name:'Choose passage',exact:true})).toHaveCount(0);
 const passage=row('backend-result').locator('strong');
 await passage.dispatchEvent('pointerdown');
 await passage.evaluate(element=>{
  document.activeElement?.blur();
  const range=document.createRange();range.selectNodeContents(element);
  const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
  document.dispatchEvent(new Event('selectionchange'));
 });
 await page.getByRole('button',{name:'Quote selection',exact:true}).click();
 const quote=page.getByRole('textbox',{name:'Message Amplifier'}).locator('[data-quote-instance]');
 await expect(quote.locator('pre')).toHaveText('The requested change is complete.');
 await quote.getByRole('button',{name:'Remove quote',exact:true}).click();
 await expect(quote).toHaveCount(0);
 await second.locator(':scope > button').first().click();
 await expect(second.locator('[data-message-id="backend-second"]')).toHaveCount(1);
 await expect(second.locator('[data-message-id="backend-result"]')).toHaveCount(0);
 for(const id of ['backend-update','backend-result','backend-second'])await expect(row(id)).toHaveCount(1);
 if(process.env.EVIDENCE_DIR){await mkdir(process.env.EVIDENCE_DIR,{recursive:true});await summary.evaluate(element=>element.scrollIntoView({block:'start'}));await page.screenshot({path:process.env.EVIDENCE_DIR+'/voice-work-desktop.png'});}
 await summary.click();await page.reload();await showFixtureConversation(page);await expect(summary).toHaveAttribute('aria-expanded','false');
 const before=await inspect();
 await page.evaluate(sessionId=>window.amplifier.dispatch('message.reveal',{sessionId,messageId:'backend-result'}),before.sessionId);
 await expect(summary).toHaveAttribute('aria-expanded','true');
 await expect(row('backend-result')).toBeFocused();

 // Observe actual React commits before paint, including the first delta. This
 // fails if a voice stream flashes as an ordinary reply or a separate card.
 await pane.evaluate(element=>{
  window.voiceWorkFrames=[];
  const sample=()=>{const outer=[...element.children].filter(node=>node.matches('article'));
   window.voiceWorkFrames.push({ordinary:outer.some(node=>node.matches('.a-assistant')&&node.textContent.includes('VOICE_STREAM_')),
    standalone:outer.some(node=>node.matches('.a-voice-work')&&(node.dataset.inputId==='voice:fixture-call:stream'||node.textContent.includes('VOICE_STREAM_'))),
    groups:element.querySelectorAll('[data-voice-work-group="true"][data-work-input-id="voice:fixture-call:stream"]').length});};
  window.voiceWorkObserver=new MutationObserver(sample);window.voiceWorkObserver.observe(element,{subtree:true,childList:true,characterData:true});sample();
 });
 const voiceStart=await fixturePost('stream',{kind:'voice',part:'start'});
 assert.equal(voiceStart.streamingInputId,'voice:fixture-call:stream','The real normalization boundary retains exact delivered input on the first chunk');
 const live=group('voice:fixture-call:stream'),liveSummary=live.locator(':scope > button').first();
 await expect(live).toHaveCount(1);await expect(liveSummary).toContainText('Working');
 await expect(liveSummary).toHaveAttribute('aria-expanded','false');
 await expect(pane).not.toContainText('VOICE_STREAM_FIRST_CHUNK');
 await liveSummary.click();
 await expect(live.locator(`[data-stream-id="${voiceStart.streamId}"]`)).toContainText('VOICE_STREAM_FIRST_CHUNK');
 await fixturePost('stream',{kind:'voice',part:'grow'});
 await expect(live).toContainText('VOICE_STREAM_SECOND_CHUNK');
 const completed=await fixturePost('stream',{kind:'voice',part:'finish'});
 await expect(liveSummary).toContainText(/Worked for|Work details/);
 await expect(live.locator(`[data-message-id="${completed.messageId}"]`)).toHaveCount(1);
 await expect(pane.locator(`[data-stream-id="${voiceStart.streamId}"]`)).toHaveCount(0);
 await expect(row(completed.messageId)).toHaveCount(1);
 const frames=await page.evaluate(()=>{window.voiceWorkObserver.disconnect();return window.voiceWorkFrames});
 assert.ok(frames.length>3,'Observe first chunk, growth, and completion mutations');
 assert.equal(frames.some(frame=>frame.ordinary||frame.standalone),false,'No voice chunk is ever painted outside its work group: '+JSON.stringify(frames));
 assert.ok(frames.every(frame=>frame.groups<=1),'One owning group, without transient duplicates');
 await row(completed.messageId).getByRole('button',{name:'Copy message as Markdown'}).click();
 await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('VOICE_STREAM_FIRST_CHUNK\n\nVOICE_STREAM_SECOND_CHUNK');

 // A connected call cannot change the presentation of typed or unknown work.
 for(const kind of ['typed','unknown']){
  const start=await fixturePost('stream',{kind,part:'start'});
  assert.equal(start.streamingInputId??null,kind==='typed'?'typed-live-request':null);
  await expect(pane.locator(':scope > .a-assistant').filter({hasText:kind.toUpperCase()+'_STREAM_FIRST_CHUNK'})).toHaveCount(1);
  const final=await fixturePost('stream',{kind,part:'finish'});
  await expect(row(final.messageId)).toHaveCount(1);assert.equal(await standalone(final.messageId),true);
 }

 // Exercise a real older browser-detail page, not just scrolling a short DOM.
 await fixturePost('scrollback');await page.reload();await showFixtureConversation(page);
 // The earlier explicit reveal restores a focused window across reload.
 // Return through the real control before testing recent→older paging.
 const latest=page.getByRole('button',{name:'Return to latest',exact:true});
 await expect(latest).toBeVisible();await latest.click();await workReady();
 await expect(row('voice-user')).toHaveCount(0);
 for(let attempt=0;attempt<5&&await row('voice-user').count()===0;attempt++){
  const older=page.getByRole('button',{name:'Load earlier conversation',exact:true});
  await expect(pane).toHaveAttribute('aria-busy','false');
  await Promise.all([page.waitForResponse(response=>new URL(response.url()).pathname==='/api/conversation/detail'&&response.ok()),older.click()]);
  await expect(pane).toHaveAttribute('aria-busy','false');
 }
 await expect(row('voice-user')).toHaveCount(1);
 await expect(first).toHaveCount(1);
 await page.evaluate(sessionId=>window.amplifier.dispatch('message.reveal',{sessionId,messageId:'backend-result'}),before.sessionId);
 await expect(row('backend-result')).toBeFocused();await expect(first.locator('[data-message-id="backend-result"]')).toHaveCount(1);
 await expect(first.locator('[data-message-id="backend-update"]')).toHaveCount(1);
 await expect(row('spoken-result')).toHaveClass(/a-assistant/);
 await summary.click();await expect(summary).toHaveAttribute('aria-expanded','false');
 await page.setViewportSize({width:390,height:844});await summary.focus();await page.keyboard.press('Space');
 await expect(summary).toHaveAttribute('aria-expanded','true');await expect(row('backend-result').locator('strong')).toBeVisible();
 await row('backend-result').scrollIntoViewIfNeeded();await expect(row('backend-result').locator('strong')).toBeInViewport();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 if(process.env.EVIDENCE_DIR)await page.screenshot({path:process.env.EVIDENCE_DIR+'/voice-work-mobile.png'});
 const after=await inspect();assert.equal(after.historyUnchanged,true);assert.equal(after.runtimeSends,0);assert.deepEqual(errors,[]);
 console.log('PASS: first-chunk voice grouping before paint, exact owning work groups, nested final text/actions, no duplicate/orphan drops, spoken/typed/unknown separation, keyboard reveal/reload/paged scrollback/mobile, unchanged original history and zero model sends.');
}catch(error){
 if(page)console.error('Synthetic fixture diagnostic: '+JSON.stringify(await page.evaluate(()=>{
  const state=window.amplifier?.getState(),session=state?.sessions?.find(row=>row.id===state.selectedSessionId),pane=document.querySelector('.a-messages');
  return {projection:pane?.dataset.workProjection,busy:pane?.getAttribute('aria-busy'),messageWindow:session?.messageWindow,
   executionWindow:session?.executionWindow,messageCount:session?.messages?.length,focus:state?.view?.messageFocus,
   historyControls:document.querySelector('.a-session-history')?.textContent,workGroups:pane?.querySelectorAll('[data-voice-work-group]').length};
 }).catch(()=>({pageUnavailable:true}))));
 throw error;
}finally{await browser?.close();fixture.kill('SIGTERM');}
