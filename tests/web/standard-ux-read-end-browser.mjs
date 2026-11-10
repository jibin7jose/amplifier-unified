import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

// Exercises production read acknowledgement with synthetic completion events.
// Visibility/focus are explicitly emulated: headless Chromium treats independent
// pages as focused, so this does not claim OS notification or desktop-tab proof.
const root=fileURLToPath(new URL('../../',import.meta.url)),output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/standard-ux-read-end';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/review_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,peerBrowser,page,stderr='';fixture.stderr.on('data',b=>stderr=(stderr+b).slice(-16000));
const evidence=new StandardUxEvidence(output,{state:'synthetic-standard',runtime:'synthetic completed events; no provider',visibility:'browser visibility/focus events emulated explicitly',timings:'shared-load observations'});
try{
 await mkdir(output,{recursive:true});const port=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout '+stderr)),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+' '+stderr))});fixture.stdout.on('data',b=>{text+=b;const m=text.match(/\{"port":\s*(\d+)\}/);if(m){clearTimeout(timer);resolve(Number(m[1]))}})});
 const base='http://127.0.0.1:'+port,args={headless:true,args:['--single-process','--no-zygote','--disable-gpu']},headers={Authorization:'Bearer fixture-browser-control-token'};
 browser=await chromium.launch(args);page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});page.setDefaultTimeout(8000);evidence.page=page;
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const act=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const probe=async(id,run)=>{const result=await evidence.probe(id,['notifications.read-end'],async()=>{await run();return await evidence.snapshot(page,id,'#amp-one')});if(result.status!=='passed')throw Error('Stopped after '+id)};
 await page.goto(base);await showFixtureConversation(page,{navPinned:true});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();const first=(await state()).selectedSessionId;
 await act('session.pin',{id:first,pinned:true});const second=(await act('session.create',{title:'Other visible chat'})).result.sessionId;await act('session.pin',{id:second,pinned:true});await act('view.update',{patch:{navPinned:true}});
 const pin=id=>page.locator(`[data-sidebar-section="pinned"] [data-session-id="${id}"] [data-navigation-select]`);
 let lastGeneration;
 const complete=async id=>{lastGeneration=id;const response=await page.request.post(base+'/api/fixture/complete',{data:{sessionId:first,generationId:id}});evidence.assertion('Synthetic completion accepted '+id,response.ok());};
 const unread=()=>page.evaluate(id=>!!window.amplifier.getState().attention.sessions[id],first);
 const remainUnread=async()=>{await expect.poll(unread).toBe(true);await page.waitForTimeout(1150);evidence.assertion('Completion remains unread past acknowledgement dwell',await unread());};
 const latest=async()=>{
  await page.bringToFront();
  await expect.poll(async()=>(await state()).selectedSessionId).toBe(first);
  await expect(page.locator('.a-messages').getByText('Completed '+lastGeneration,{exact:true})).toHaveCount(1);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const jump=page.getByRole('button',{name:'Jump to latest messages',exact:true});if(await jump.isVisible())await jump.click();
  await page.locator('.a-messages').evaluate(el=>{el.scrollTop=el.scrollHeight;el.dispatchEvent(new Event('scroll'))});
  await expect.poll(()=>page.locator('.a-messages').evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight)).toBeLessThan(40);
  evidence.assertion('Positive acknowledgement window is visible and focused',await page.evaluate(()=>!document.hidden&&document.hasFocus()));
 };
 await probe('other-selection-and-hidden-reader',async()=>{
  peerBrowser=await chromium.launch(args);const peer=await peerBrowser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});
  await peer.addInitScript(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.hasFocus=()=>false});await peer.goto(base);await peer.waitForFunction(()=>window.amplifier?.getState());
  await peer.evaluate(id=>window.amplifier.dispatch('session.select',{id}),first);
  await complete('other-visible-and-hidden-peer');await remainUnread();evidence.assertion('Primary stays on other chat',(await state()).selectedSessionId,second);
  await peerBrowser.close();peerBrowser=null;
  await evidence.action('Select exact completed chat from pinned row',()=>pin(first).click());await latest();await expect.poll(unread,{timeout:6000}).toBe(false);
 });
 await probe('scrolled-away-completion',async()=>{
  await page.locator('.a-messages').evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-800,bubbles:true}));el.scrollTop=0;el.dispatchEvent(new Event('scroll'))});
  await complete('scrolled-away');await remainUnread();
  await latest();await expect.poll(unread,{timeout:6000}).toBe(false);
 });
 await probe('dialog-blocks-read',async()=>{
  await page.getByRole('button',{name:'Chat',exact:true}).click();await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}).click();
  await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeVisible();
  await complete('while-info-open');await remainUnread();
  await evidence.action('Close Chat info before acknowledging visible end',()=>page.getByRole('button',{name:'Close panel',exact:true}).click());await latest();await expect.poll(unread,{timeout:6000}).toBe(false);
 });
 await probe('hidden-and-blurred-completion',async()=>{
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
  await complete('hidden');await remainUnread();
  await page.evaluate(()=>{document.hasFocus=()=>false;delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('blur'))});await remainUnread();
  await page.evaluate(()=>{delete document.hasFocus;window.dispatchEvent(new Event('focus'))});await latest();await expect.poll(unread,{timeout:6000}).toBe(false);
  evidence.assertion('No page errors',errors,[]);
 });
 console.log('PASS read-end: other selection/hidden reader, scroll-away, modal and hidden/blurred boundaries; visible focused end acknowledges exact completion.');
}finally{
 if(page)await page.evaluate(()=>{const state=window.amplifier?.getState(),pane=document.querySelector('.a-messages');return {hidden:document.hidden,focused:document.hasFocus(),selected:state?.selectedSessionId,view:state?.view,attention:state?.attention,pane:pane&&{top:pane.scrollTop,height:pane.scrollHeight,viewport:pane.clientHeight,inert:!!pane.closest('[inert]')}}}).then(value=>writeFile(output+'/read-guard.json',JSON.stringify(value,null,2))).catch(()=>{});
 await peerBrowser?.close();await browser?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await new Promise(resolve=>{const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})})}
 await writeFile(output+'/fixture.stderr',stderr);await evidence.save();
}
