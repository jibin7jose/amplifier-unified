import {fixturePython} from './fixture-python.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,release,releaseShell;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--no-sandbox','--single-process','--disable-dev-shm-usage']:[]});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await act('session.create',{title:'First chat'});const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('conversation.send',{sessionId:first,text:'First history'});
 await act('view.update',{sessionId:first,patch:{draft:'First private draft'}});
 await act('session.create',{title:'Second chat'});const second=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('conversation.send',{sessionId:second,text:'Second history'});
 await act('view.update',{sessionId:second,patch:{draft:'Second private draft'}});
 await act('session.pin',{id:first,pinned:true});
 await act('session.pin',{id:second,pinned:true});
 await act('view.update',{patch:{navPinned:true}});
 await act('session.select',{id:first});
 await act('view.update',{patch:{workSurface:'workspace',workWorkspaceId:await page.evaluate(()=>window.amplifier.getState().selectedWorkspaceId)}});
 const blocked=new Promise(resolve=>{release=resolve});
 await page.route('**/api/actions',async route=>{
  const request=route.request();
  if(request.method()==='POST'&&['session.select','shell.command'].includes(request.postDataJSON()?.action))await blocked;
  await route.continue();
 });
 await page.getByRole('button',{name:'Second chat',exact:true}).first().waitFor();
 await page.evaluate(()=>{window.switchStarted=performance.now()});
 // Exercise the reported workspace-list entry path, not only the sidebar.
 await page.locator('.a-work-browser').getByRole('button',{name:'Second chat',exact:true}).click();
 await expect(page.getByRole('button',{name:'Second chat',exact:true}).first()).toHaveAttribute('aria-current','page',{timeout:1000});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Second private draft',{timeout:1000});
 await expect(page.getByText('Second history',{exact:true})).toHaveCount(0);
 await expect(page.getByText('First history',{exact:true})).toHaveCount(0);
 const painted=await page.evaluate(()=>({milliseconds:performance.now()-window.switchStarted,settled:!window.amplifier.getState().navigationPending,session:window.amplifier.getState().selectedSessionId}));
 assert.equal(painted.settled,false,'selection must paint before the blocked server request');
 assert.equal(painted.session,second);
 // A user can type while navigation is pending, with a stable target.
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Typed while selection was pending');
 release();await page.waitForFunction(()=>!window.amplifier.getState().navigationPending);
 await expect(page.getByText('Second history',{exact:true})).toBeVisible();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Typed while selection was pending');
 await act('session.select',{id:first});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('First private draft');
 await act('session.select',{id:second});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Typed while selection was pending');
 // A failed selection restores the confirmed chat and its private draft.
 await page.unroute('**/api/actions');
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='shell.command'&&body.args?.action==='session.select'){
   await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Fixture selection rejected'})});return;
  }
  await route.continue();
 });
 await page.getByRole('button',{name:'First chat',exact:true}).first().click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id&&!window.amplifier.getState().navigationPending,second);
 await expect(page.getByRole('button',{name:'Second chat',exact:true}).first()).toHaveAttribute('aria-current','page');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Typed while selection was pending');
 await page.unroute('**/api/actions');
 // Three rapid choices while the first request is blocked retain the latest
 // intent even when the earlier authoritative response arrives first.
 await act('session.create',{title:'Third chat'});const third=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await act('conversation.send',{sessionId:third,text:'Third history'});
 await act('session.pin',{id:third,pinned:true});
 await act('session.select',{id:second});
 let releaseRapid;const rapidGate=new Promise(resolve=>releaseRapid=resolve);
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='shell.command'&&body.args?.action==='session.select')await rapidGate;
  await route.continue();
 });
 await page.getByRole('button',{name:'First chat',exact:true}).first().click();
 await page.getByRole('button',{name:'Third chat',exact:true}).first().click();
 await expect(page.getByRole('button',{name:'Third chat',exact:true}).first()).toHaveAttribute('aria-current','page',{timeout:1000});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),third);
 releaseRapid();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id&&!window.amplifier.getState().navigationPending,third);
 await expect(page.getByText('Third history',{exact:true})).toBeVisible();
 await page.unroute('**/api/actions');
 // A derived navigation refresh must not own the command queue. Keep the
 // shell request pending while two real selections commit through the host.
 const shellGate=new Promise(resolve=>releaseShell=resolve);let shellHeld=false;
 await page.route('**/api/shell?**',async route=>{shellHeld=true;await shellGate;await route.continue()});
 const accepted=[];
 page.on('response',response=>{
  const request=response.request();if(!request.url().endsWith('/api/actions')||request.method()!=='POST')return;
  const body=request.postDataJSON();
  if(response.ok()&&body.action==='shell.command'&&body.args.action==='session.select')accepted.push(body.args.args.id);
 });
 await page.getByRole('button',{name:'First chat',exact:true}).first().click();
 await expect.poll(()=>accepted.includes(first)).toBe(true);
 await expect.poll(()=>shellHeld).toBe(true);
 await page.getByRole('button',{name:'Second chat',exact:true}).first().click();
 await expect.poll(()=>accepted.includes(second),{timeout:3000}).toBe(true);
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id&&!window.amplifier.getState().navigationPending,second);
 await expect(page.getByText('Second history',{exact:true})).toBeVisible();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Typed while selection was pending');
 releaseShell();
 console.log(JSON.stringify({passed:true,cachedSwitchPaintMs:painted.milliseconds,paintedBeforeServer:true,draftsPreserved:true,failedSelectionRestored:true,rapidSelectionPreserved:true,selectionIndependentOfShellRefresh:true}));
}finally{release?.();releaseShell?.();await browser?.close();fixture.kill()}
