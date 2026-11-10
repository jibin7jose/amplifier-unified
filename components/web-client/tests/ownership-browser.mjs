import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsDialog} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/ownership_ui_server.py',import.meta.url))],{stdio:'inherit'});
const url='http://127.0.0.1:8967';
let browser;
try{
 for(let i=0;i<100;i++){try{if((await fetch(url+'/api/health')).ok)break}catch{}await new Promise(resolve=>setTimeout(resolve,100))}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const configure=async options=>{const response=await page.request.post(url+'/fixture/ownership',{data:options});assert.ok(response.ok())};
 const finish=()=>page.request.post(url+'/fixture/finish');
 const stats=async()=>(await page.request.get(url+'/fixture')).json();
 await page.goto(url);await showFixtureConversation(page);await page.waitForSelector('#amp-one');
 const draft=page.locator('[role="textbox"][aria-label="Message Amplifier"]');
 const access=page.getByRole('region',{name:'Conversation access'});
 const takeover=access.getByRole('button',{name:'Continue here',exact:true});
 const fields=page.locator('.a-composer-fields');
 await draft.fill('Keep this draft');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(access).toBeVisible();await expect(takeover).toBeEnabled();
 await expect(draft).toHaveDraft('');await expect(page.locator('.a-user').filter({hasText:'Keep this draft'})).toBeVisible();
 await expect(draft).toBeDisabled();await expect(fields).toHaveAttribute('inert','');
 await expect(page.getByText('Saved history stays readable.',{exact:true})).toBeVisible();
 assert.equal(await page.locator('.a-alert.a-ownership').count(),0,'no separate top ownership banner');
 assert.equal(await page.getByText('Amplifier needs a little help.',{exact:true}).count(),0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().attention.items.filter(i=>i.title==='Conversation needs attention').length),0);
 const bounds=await access.boundingBox(),composer=await page.locator('form.a-composer').boundingBox();
 assert.ok(bounds.y>=composer.y&&bounds.y+bounds.height<=composer.y+composer.height,'overlay covers the composer');
 assert.ok(bounds.y+bounds.height<=900,'takeover stays in the visible composer');
 await takeover.focus();
 for(let i=0;i<25;i++){
  await page.keyboard.press('Tab');
  assert.equal(await fields.evaluate(el=>el.contains(document.activeElement)),false,'keyboard cannot reach covered controls');
 }
 await page.keyboard.press('Escape');await expect(access).toBeVisible();
 const before=await stats();
 await page.locator('form.a-composer').evaluate(form=>{
  form.requestSubmit();
  const clipboardData=new DataTransfer();clipboardData.items.add(new File(['content'],'blocked.txt',{type:'text/plain'}));clipboardData.setData('text/plain','Must not replace the draft');
  form.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData}));
 });
 await expect(draft).toHaveDraft('');await expect(page.locator('.a-user').filter({hasText:'Keep this draft'})).toBeVisible();
 assert.equal((await stats()).sends,before.sends,'form submission is also guarded');
 const visibleControls=await page.evaluate(()=>window.amplifier.getState().renderedView.controls);
 assert.equal(visibleControls.find(control=>control.label==='Message Amplifier').disabled,true,'agent view reflects the disabled composer');
 await page.screenshot({path:'/tmp/amplifier-composer-takeover-desktop.png'});
 await openSettingsDialog(page);
 await page.locator('[role=dialog]').waitFor();
 assert.equal(await page.getByText('Conversation needs attention',{exact:false}).count(),0);
 await page.getByRole('button',{name:'Close panel'}).click();
 await configure({hold:true});
 await takeover.click();
 await expect(access).toHaveAttribute('aria-busy','true');
 await expect(access.locator('strong')).toHaveText('Taking over…');
 await expect(draft).toBeDisabled();await expect(draft).toHaveDraft('');await expect(page.locator('.a-user').filter({hasText:'Keep this draft'})).toBeVisible();
 await expect.poll(async()=>(await stats()).takeovers).toBe(1);
 await finish();
 await expect(access).toHaveCount(0);await expect(draft).toBeEnabled();await expect(draft).toBeFocused();
 await expect(draft).toHaveDraft('');await expect(page.locator('.a-user').filter({hasText:'Keep this draft'})).toBeVisible();
 await page.getByRole('button',{name:'Retry',exact:true}).click();
 await page.getByText('Continued successfully.',{exact:true}).waitFor();
 await draft.fill('Keep this too');
 await configure({result:'error'});
 await takeover.click();
 await expect(access.getByText('Could not continue here',{exact:true})).toBeVisible();
 await expect(access.getByText('Could not reach the session owner.',{exact:true})).toBeVisible();
 await expect(draft).toBeDisabled();await expect(draft).toHaveDraft('Keep this too');
 await expect(access.getByRole('button',{name:'Try again',exact:true})).toBeEnabled();
 // A failed HTTP request is displayed beside the retry action, not only above the chat.
 await page.route('**/api/actions',async route=>{
  if(route.request().method()==='POST'&&route.request().postDataJSON()?.action==='session.takeover')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Connection temporarily unavailable'})});
  await route.continue();
 });
 await access.getByRole('button',{name:'Try again',exact:true}).click();
 await expect(access.getByRole('alert')).toContainText('Connection temporarily unavailable');
 await expect(draft).toBeDisabled();
 await page.unroute('**/api/actions');
 // Unsupported owners, in-progress yield, and failed cleanup all stay honest.
 await configure({supportsTakeover:false});
 await expect(access).toContainText('does not support takeover requests');
 await expect(access.getByRole('alert'),'old connection failure clears when host ownership changes').toHaveCount(0);
 await expect(takeover).toBeEnabled();
 for(const status of ['yielding','yield-failed']){
  await configure({status,detail:status==='yield-failed'?'Cleanup needs attention.':undefined});
  await expect(access).toBeVisible();await expect(access.getByRole('button')).toHaveCount(0);await expect(draft).toBeDisabled();
 }
 await configure({status:'yielded'});await expect(takeover).toBeEnabled();
 await configure({runtimeAvailable:false});
 await expect(access).toContainText('runtime is unavailable');await expect(takeover).toBeDisabled();
 await configure({});
 await page.setViewportSize({width:390,height:844});
 await expect(takeover).toBeEnabled();
 const mobile=await access.boundingBox();
 assert.ok(mobile.x>=0&&mobile.x+mobile.width<=390&&mobile.y+mobile.height<=844,'mobile overlay remains visible without horizontal overflow');
 await page.screenshot({path:'/tmp/amplifier-composer-takeover-mobile.png'});
 await takeover.click();await expect(access).toHaveCount(0);await expect(draft).toHaveDraft('Keep this too');
 // A stale CLI conflict cannot turn saved child history into a resumable root.
 const reason='Worker sessions are read-only; continuing them as root chats would lose their worker configuration.';
 await configure({sessionKind:'worker',readOnlyReason:reason});
 await expect(access.locator('strong')).toHaveText('Worker history');
 await expect(access.getByText(reason,{exact:true})).toHaveCount(1);
 await expect(access.getByRole('button')).toHaveCount(0);
 await expect(access).not.toContainText('In use by Amplifier CLI');
 await expect(draft).toBeDisabled();await expect(draft).toHaveDraft('Keep this too');
 await page.screenshot({path:'/tmp/readiness100-worker-history.png'});
 assert.deepEqual(errors,[]);
 console.log('Ownership browser flow passed: composer overlay, keyboard/pointer guards, history access, saved drafts, pending ownership, success, retry, failure, unavailable/legacy owners, agent view, and mobile layout.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
