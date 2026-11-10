import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
// Real host/actions and production assets; temporary files, no provider calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-switching';
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{ }),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[],chats=[];
 for(const name of ['Project A','Project B']){
  const folder=path.join(home,name);await mkdir(folder);await writeFile(path.join(folder,name+'.txt'),name+' contents');
  await action('workspace.add',{path:folder,name});
  const row=(await action('workspace.list',{query:name})).result.items.find(row=>row.path===folder);assert.ok(row);workspaces.push(row);
  const created=await action('session.create',{workspace:folder,title:name+' chat'});assert.equal(created.accepted,true);chats.push(created.result.sessionId);
 }
 const [a,b]=workspaces;
 const [aChat,bChat]=chats;
 // Keep the destination chat outside the bounded Recent shortcut page.
 for(let index=0;index<25;index++)assert.equal((await action('session.create',{workspace:a.path,title:'Recent filler '+index})).accepted,true);
 await action('session.select',{id:aChat});
 await action('session.pin',{id:aChat,pinned:true});
 await expect(page.locator('[data-sidebar-section=recent] [data-session-id="'+bChat+'"]')).toHaveCount(0);
 await expect(page.locator('[data-sidebar-section=pinned] [data-session-id="'+bChat+'"]')).toHaveCount(0);
 await action('view.update',{patch:{navPinned:true}});
 await composer.fill('Unsent draft belongs to Project A');
 await action('attachment.add',{sessionId:aChat,name:'draft.txt',base64:'cHJlc2VydmU='});
 await page.request.post(url+'/fixture/activity',{data:{sessionId:aChat,status:'working'}});
 const body=page.locator('.a-work-browser'),header=page.locator('.a-work-heading');
 const shortcut=workspace=>page.getByRole('button',{name:'Open chats in '+workspace.path,exact:true});
 async function expectWorkspace(workspace,other){
  await expect(header.locator('strong')).toHaveText(workspace.name);
  await expect(page.getByRole('navigation',{name:'Site navigation',exact:true}).getByRole('button',{name:'Home',exact:true})).toBeVisible();
  await expect(body.getByRole('heading',{level:1,name:workspace.name,exact:true})).toBeVisible();
  await expect(shortcut(workspace)).toHaveAttribute('aria-pressed','true');
  await expect(shortcut(other)).toHaveAttribute('aria-pressed','false');
  await expect(body.locator('[data-session-id]').getByRole('button',{name:workspace.name+' chat',exact:true})).toBeVisible();
  await expect(body.locator('[data-session-id]').getByRole('button',{name:other.name+' chat',exact:true})).toHaveCount(0);
  const current=await state();assert.equal(current.selectedSessionId,aChat);assert.equal(current.selectedWorkspaceId,a.id);
  await expect(page.getByRole('status').filter({hasText:'Project A chat · Working…'})).toHaveCount(0);
  await expect(page.getByRole('button',{name:/^Back(?: to chat)?$/})).toHaveCount(0);
 }
 const pinStatus=page.locator('[data-sidebar-section=pinned] [data-session-id="'+aChat+'"] .a-navigation-status');
 async function expectColor(locator,token){
  await expect.poll(()=>locator.evaluate((node,token)=>{const probe=document.createElement('span');probe.style.color='var('+token+')';node.append(probe);const expected=getComputedStyle(probe).color;probe.remove();return getComputedStyle(node).color===expected},token)).toBe(true);
 }
 await expect(pinStatus).toHaveAttribute('data-kind','working');await expectColor(pinStatus,'--a-accent');
 await expectColor(shortcut(b).locator(':scope > svg'),'--a-muted');
 await shortcut(a).click();await expectWorkspace(a,b);
 await expectColor(body.locator('.a-navigation-status[data-kind="working"]').first(),'--a-accent');
 // Force optimistic UI to lead the persisted action, as on a busy host.
 await page.route('**/api/actions',async route=>{const request=route.request().postDataJSON();if(request?.action==='view.update'&&request.args?.patch?.workSurface==='workspace')await new Promise(resolve=>setTimeout(resolve,250));await route.continue()});
 await shortcut(b).click();await expectWorkspace(b,a);
 await shortcut(a).click();await expectWorkspace(a,b);
 // Agent-visible shared action uses the same navigation state as sidebar clicks.
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:b.id,workWorkspaceTab:'chats'}});await expectWorkspace(b,a);
 await body.getByRole('button',{name:'Details',exact:true}).click();await expect(body.getByText(b.path,{exact:true})).toBeVisible();
 await body.getByRole('button',{name:'Files',exact:true}).click();await expect(body.getByRole('button',{name:'Project B.txt',exact:true})).toBeVisible();await expect(body.getByRole('button',{name:'Project A.txt',exact:true})).toHaveCount(0);
 await shortcut(a).click();await expectWorkspace(a,b);
 await shortcut(b).click();await expectWorkspace(b,a);
 await page.screenshot({path:out+'/project-b-while-a-working.png'});
 await page.locator('[data-sidebar-section=pinned] [data-session-id="'+aChat+'"] .a-nav-chat-select').click();
 await expect(composer).toHaveDraft('Unsent draft belongs to Project A');
 await expect(page.getByRole('button',{name:'Remove draft.txt',exact:true})).toBeVisible();
 await shortcut(b).click();await expectWorkspace(b,a);
 await body.getByRole('button',{name:'New chat',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBeNull();
 const draft=await state();assert.equal(draft.selectedSessionId,null);assert.equal(draft.view.newSessionDraft.workspace,b.path);
 await action('session.select',{id:aChat});await expect(composer).toHaveDraft('Unsent draft belongs to Project A');await expect(page.getByRole('button',{name:'Remove draft.txt',exact:true})).toBeVisible();
 // File previews explicitly name their chat, and a late preview response never
 // follows the user into a newer visit (including leaving and returning to A).
 const browseFiles=()=>action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:a.id,workWorkspaceTab:'files'}});
 for(const width of [1440,390]){
  await page.setViewportSize({width,height:1000});await browseFiles();
  await expect(page.getByRole('button',{name:/^Back(?: to chat)?$/})).toHaveCount(0);
  const file=body.getByRole('button',{name:'Project A.txt Open in “Project A chat”',exact:true});
  await expect(file).toBeVisible();
  await page.screenshot({path:out+'/named-preview-'+width+'.png'});
  await file.click();await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
  await expect.poll(async()=>(await state()).canvas.title).toBe('Project A.txt');
  assert.equal((await state()).selectedSessionId,aChat);
  await action('canvas.visibility',{sessionId:aChat,canvasId:(await state()).canvas.id,open:false});
  await expect(composer).toBeVisible();await expect(composer).toHaveDraft('Unsent draft belongs to Project A');
 }
 await page.setViewportSize({width:1440,height:1000});
 await mkdir(path.join(a.path,'docs'));
 await writeFile(path.join(a.path,'docs','notes.txt'),'Nested preview');
 await mkdir(path.join(a.path,'docs','nested'));
 await browseFiles();
 // Refresh the Files mount after adding a synthetic directory.
 await body.getByRole('button',{name:'Details',exact:true}).click();
 await body.getByRole('button',{name:'Files',exact:true}).click();
 await body.getByRole('button',{name:'docs',exact:true}).click();
 // Hold before host execution to prove folder/parent controls cannot advance
 // independently of the one pending preview intent.
 let releaseRequest,requestHeld=false;
 const requestGate=new Promise(resolve=>{releaseRequest=resolve});
 const holdRequest=async route=>{
  if(route.request().postDataJSON()?.action==='canvas.openFile'){requestHeld=true;await requestGate}
  await route.continue();
 };
 await page.route('**/api/actions',holdRequest);
 await body.getByRole('button',{name:'notes.txt Open in “Project A chat”',exact:true}).click();
 await expect.poll(()=>requestHeld).toBe(true);
 await expect(body.getByRole('button',{name:'Parent folder',exact:true})).toBeDisabled();
 await expect(body.getByRole('button',{name:'nested',exact:true})).toBeDisabled();
 releaseRequest();
 await expect.poll(async()=>(await state()).view.workSurface).toBe('chat');
 await page.unroute('**/api/actions',holdRequest);
 await action('canvas.visibility',{sessionId:aChat,canvasId:(await state()).canvas.id,open:false});
 const fileActions=[];
 page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/api/actions'))fileActions.push(request.postDataJSON())});
 for(const leaveAndReturn of [false,true]){
  await browseFiles();
  let release,responseReady=false;
  const gate=new Promise(resolve=>{release=resolve});
  const hold=async route=>{
   const request=route.request().postDataJSON();
   if(request?.action!=='canvas.openFile'){await route.continue();return}
   const response=await route.fetch();responseReady=true;await gate;await route.fulfill({response});
  };
  await page.route('**/api/actions',hold);
  const file=body.getByRole('button',{name:'Project A.txt Open in “Project A chat”',exact:true});
  await file.click();await expect.poll(()=>responseReady).toBe(true);
  const visits=[];
  if(leaveAndReturn){
   // Presentation paints optimistically while its writes wait behind the
   // held file command. Do not await those receipts before releasing it.
   visits.push(action('view.update',{patch:{workSurface:'chats'}}));
   await expect.poll(async()=>(await state()).view.workSurface).toBe('chats');
   visits.push(browseFiles());
   await expect.poll(async()=>(await state()).view.workSurface).toBe('workspace');
  }else await action('session.select',{id:bChat});
  const before=fileActions.length;
  release();await Promise.all(visits);
  // This harmless queued action acknowledges after the held command settles.
  await action('view.update',{patch:{notice:null}});
  await page.unroute('**/api/actions',hold);
  assert.ok(!fileActions.slice(before).some(request=>request.action==='session.select'),'Late file response must not select a chat');
  assert.equal((await state()).selectedSessionId,leaveAndReturn?aChat:bChat);
  assert.equal((await state()).view.workSurface,leaveAndReturn?'workspace':'chat');
  await action('session.select',{id:aChat});
 }
 // The normal Stop remains available after explicitly opening a running chat.
 await composer.fill('');await page.getByRole('button',{name:'Remove draft.txt',exact:true}).click();
 await action('canvas.visibility',{sessionId:aChat,canvasId:(await state()).canvas.id,open:false});
 await page.getByRole('button',{name:'Stop response',exact:true}).click();
 await expect.poll(async()=>(await (await page.request.get(url+'/fixture')).json()).stopped).toEqual([aChat]);
 await page.getByRole('button',{name:'Search chats',exact:true}).click();await expect(body.locator('[data-session-id]').getByRole('button',{name:'Project A chat',exact:true})).toBeVisible();await expect(body.locator('[data-session-id]').getByRole('button',{name:'Project B chat',exact:true})).toBeVisible();
 await page.request.post(url+'/fixture/activity',{data:{sessionId:aChat,status:'error'}});
 await expect(pinStatus).toHaveAttribute('data-kind','attention');await expectColor(pinStatus,'--a-danger');
 const recentId=await page.locator('[data-sidebar-section=recent] [data-session-id]').first().getAttribute('data-session-id');
 await page.request.post(url+'/fixture/activity',{data:{sessionId:recentId,status:'error'}});
 const recentStatus=page.locator('[data-sidebar-section=recent] [data-session-id="'+recentId+'"] .a-navigation-status');
 await expect(recentStatus).toHaveAttribute('data-kind','attention');await expectColor(recentStatus,'--a-danger');
 await page.request.post(url+'/fixture/activity',{data:{sessionId:recentId,status:'idle'}});
 await expect(recentStatus).toHaveAttribute('data-kind','idle');await expectColor(recentStatus,'--a-muted');
 await action('view.update',{patch:{draft:'Unsent draft belongs to Project A'}});
 await action('attachment.add',{sessionId:aChat,name:'draft.txt',base64:'cHJlc2VydmU='});
  // The same unread marker must paint outside the sidebar, not merely exist in
  // the DOM. Browsing and peeking must leave the active chat and unread state alone.
  assert.equal((await action('session.markUnread',{id:recentId})).accepted,true);
  await shortcut(a).click();
  const workspaceRow=body.locator('[data-session-id="'+recentId+'"]');
  const workspaceStatus=workspaceRow.locator('.a-navigation-status');
  async function expectUnread(locator){
   await expect(locator).toHaveAttribute('data-kind','unread');
   await expect(locator).toHaveAttribute('aria-label','New response');
   const dot=locator.locator('.a-navigation-unread');
   await expect(dot).toBeVisible();
   await expect(dot).toHaveCSS('width','6px');
   await expect(dot).toHaveCSS('height','6px');
   await expect(dot).toHaveCSS('border-radius','50%');
   await expect.poll(()=>dot.evaluate(node=>getComputedStyle(node).backgroundColor===getComputedStyle(node.parentElement).color)).toBe(true);
   await expectColor(locator,'--a-accent');
  }
  for(const scheme of ['light','dark']){
   await action('view.update',{patch:{scheme}});
   for(const width of [1440,390]){
    await page.setViewportSize({width,height:1000});
    await expectUnread(workspaceStatus);
    if(width===1440)await expectUnread(recentStatus);
    await page.screenshot({path:out+'/unread-'+scheme+'-'+width+'.png'});
   }
  }
  await page.setViewportSize({width:1440,height:1000});
  await action('view.update',{patch:{scheme:'light',navPinned:true,navExpanded:true}});
  await workspaceRow.hover();
  const details=page.getByRole('dialog',{name:/^Details for Recent filler/});
  await expect(details).toBeVisible();
  await expectUnread(details.locator('.a-navigation-status'));
  await page.keyboard.press('Escape');
  assert.equal((await state()).selectedSessionId,aChat);
  assert.ok((await state()).attention.sessions[recentId]);
  // Work and errors retain precedence over unread in both views.
  for(const [status,kind,token] of [['working','working','--a-accent'],['error','attention','--a-danger']]){
   await page.request.post(url+'/fixture/activity',{data:{sessionId:recentId,status}});
   for(const locator of [recentStatus,workspaceStatus]){
    await expect(locator).toHaveAttribute('data-kind',kind);
    await expect(locator.locator('svg')).toBeVisible();
    await expect(locator.locator('.a-navigation-unread')).toHaveCount(0);
    await expectColor(locator,token);
   }
  }
  await page.request.post(url+'/fixture/activity',{data:{sessionId:recentId,status:'idle'}});
  await expectUnread(workspaceStatus);await expectUnread(recentStatus);
  await action('view.update',{patch:{workSurface:'chats'}});
  await expectUnread(body.locator('[data-session-id="'+recentId+'"] .a-navigation-status'));
  await page.reload();
  await expectUnread(body.locator('[data-session-id="'+recentId+'"] .a-navigation-status'));
  assert.equal((await state()).selectedSessionId,aChat);
  await action('view.update',{patch:{workSurface:'chat'}});
  await expect(composer).toHaveDraft('Unsent draft belongs to Project A');
  await expect(page.getByRole('button',{name:'Remove draft.txt',exact:true})).toBeVisible();
 const fixtureState=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(fixtureState.sent,[]);assert.deepEqual(fixtureState.stopped,[aChat]);
 assert.equal(fixtureState.registeredWorkspaces.length,3);assert.notEqual(aChat,bChat);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',scenarios:['destination absent from pinned/recent','gray idle, accent working, red attention in pinned/recent/browser','repeated workspace clicks','shared navigation action','header/body/sidebar/list coherence','details/files scope','active chat preserved','draft and attachment preserved','new chat destination','no return controls or working strip','named file preview on desktop/mobile','late preview cannot navigate','explicit same-chat reopen and stop','all chats scope','no sends'],screenshots:out}));
}catch(error){if(page)await page.screenshot({path:out+'/failure.png'});throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
