// Production discovery/actions/persistence in a disposable service; runtime is synthetic.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const legacyTouchOnly=process.argv.includes('--legacy-touch-only');
const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/chat_library_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';
fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const ready=new Promise((resolve,reject)=>{
 let output='';
 fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});
 fixture.once('error',reject);
 fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${fixtureLog}`)));
});
let vite,browser,page;
try{
 const port=await ready,target=`http://127.0.0.1:${port}`;
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
 await vite.listen();
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const api=(path,body)=>page.evaluate(async([path,body])=>{
  const headers={'X-Amplifier-Client':window.amplifier.getState().client.id};
  const response=await fetch(path,body===undefined?{headers}:{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(await response.text());return response.json();
 },[path,body]);
 const info=()=>api('/api/fixture/info');
 const agent=async(action,args)=>api('/api/fixture/agent',{args:action==='view.update'?{action:'shell.view.update',args:{...args,clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats',patch:{navRecentView:args.patch}}}:{action,args}});
 const state=()=>page.evaluate(()=>({...window.amplifier.getState(),...window.amplifier.getShellState().snapshots.chats,chatNavigation:window.amplifier.getShellState().snapshots.chats.sidebarNavigation.recent,view:{...window.amplifier.getState().view,...window.amplifier.getShellState().snapshots.chats.view}}));
 const rows=()=>page.locator('[data-sidebar-section=pinned] .a-nav-chat[data-session-id], [data-sidebar-section=recent] .a-nav-chat[data-session-id]');
 const row=id=>page.locator(`[data-sidebar-section=pinned] .a-nav-chat[data-session-id="${id}"], [data-sidebar-section=recent] .a-nav-chat[data-session-id="${id}"]`);
 const ids=()=>rows().evaluateAll(elements=>elements.map(element=>element.dataset.sessionId));
 const waitScope=scope=>page.waitForFunction(scope=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent?.scope?.mode===scope,scope);
 const waitPage=index=>page.waitForFunction(index=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent?.index===index,index);
 const waitFilter=value=>page.waitForFunction(value=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent?.scope?.filter===value,value);

 await page.goto(vite.resolvedUrls.local[0]);
 await page.locator('[data-sidebar-section=recent] .a-nav-chat').first().waitFor();
 const first=await info(),paths=first.paths,initial=first.initialSession,quiet=first.quietSession;
 let beta;
 if(!legacyTouchOnly){
 await waitScope('all');
 assert.equal(await page.locator('[data-sidebar-section]').count(),3);
 let current=await state();
 assert.equal(current.selectedSessionId,initial);
 assert.equal(current.chatNavigation.total,206);
 assert.equal(current.chatNavigation.index,0);
 assert.equal(await rows().count(),40);
 assert.equal(current.chatNavigation.items[0].title,'Beta 002');
 assert.equal(await page.getByRole('group',{name:'Chat view'}).count(),0);
 assert.equal(await page.locator('.a-workspace-explorer:visible').count(),1);
 for(const path of [paths.one,paths.two])assert.ok((await page.locator('.a-nav-chat-workspace').evaluateAll(rows=>rows.map(row=>row.title))).includes(path),'full workspace paths remain available with compact distinguishing labels');
 assert.equal(current.chatNavigation.items.some(chat=>chat.title==='Hidden worker'||chat.title==='Deleted workspace chat'),false);
 assert.deepEqual((await info()).runtimeStarts,[]);
 assert.deepEqual((await info()).runtimeSends,[]);

 assert.equal(await page.getByRole('button',{name:'New workspace',exact:true}).count(),1);

 // Bounded paging does not change selection. Pinning reveals the global pin while Recent keeps its page.
 await page.getByRole('button',{name:'Show more conversations'}).click();await waitPage(1);assert.equal(await rows().count(),40);
 for(let index=2;index<=5;index++){await page.getByRole('button',{name:'Show more conversations'}).click();await waitPage(index)}assert.equal(await rows().count(),6);
 assert.equal((await state()).selectedSessionId,initial);
 await row(quiet).getByRole('button',{name:/Details and actions/}).click();
 await page.locator('.a-navigation-flyout').getByRole('button',{name:'Pin Quiet older chat',exact:true}).click();
 await page.waitForFunction(id=>window.amplifier.getState().pinnedSessionIds.includes(id),quiet);await waitPage(5);
 assert.equal((await ids())[0],quiet);
 await row(quiet).getByRole('button',{name:/Details and actions/}).click();
 assert.equal(await page.locator('.a-navigation-flyout').getByRole('button',{name:'Unpin Quiet older chat',exact:true}).getAttribute('aria-pressed'),'true');
 await page.keyboard.press('Escape');
 assert.equal((await state()).selectedSessionId,initial);

 // Search includes full paths and fnmatch wildcards, irrespective of folder browsing.
 const search=page.getByRole('searchbox',{name:'Filter conversations',exact:true});
 await search.fill('*/research/*');await waitFilter('*/research/*');
 await page.waitForFunction(()=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent.total===3);
 assert.equal(await page.locator('[data-sidebar-section=recent] .a-nav-chat').count(),3);
 assert.equal(await page.getByRole('button',{name:'Show more conversations'}).count(),0);
 await search.fill('Beta 00[12]');await waitFilter('Beta 00[12]');
 await page.waitForFunction(()=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent.total===2);
 await search.fill('Hidden worker');await waitFilter('Hidden worker');assert.equal((await state()).chatNavigation.total,0);
 await search.fill('Deleted workspace chat');await waitFilter('Deleted workspace chat');assert.equal((await state()).chatNavigation.total,0);
 await search.fill('');await waitFilter('');

 // Agent pin/unpin uses exactly the user action and resets to normal recent order.
 await agent('session.pin',{id:quiet,pinned:false});
 await page.waitForFunction(id=>!window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent.items.find(row=>row.id===id)?.pinned&&window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent.items[0]?.title==='Beta 002',quiet);
 assert.equal((await state()).chatNavigation.items[0].title,'Beta 002');
 beta=(await state()).chatNavigation.items.find(chat=>chat.title==='Beta 002');
 const before=await ids();
 await agent('session.rename',{id:beta.id,title:'Beta latest renamed'});
 await page.waitForFunction(id=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent.items.find(chat=>chat.id===id)?.title==='Beta latest renamed',beta.id);
 assert.deepEqual(await ids(),before,'renaming is not conversation activity');
 await agent('view.update',{patch:{navSort:'name'}});
 await agent('view.update',{patch:{navSort:'activity'}});await waitScope('all');
 assert.deepEqual(await ids(),before,'view changes do not alter activity order');
 assert.equal((await state()).selectedSessionId,initial);
 assert.deepEqual((await info()).runtimeStarts,[]);

 // Select an old quiet chat, then submit a real turn to the synthetic runtime.
 await agent('session.select',{id:quiet});
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,quiet);
 assert.deepEqual(await ids(),before,'viewing an older chat does not promote it');
 assert.deepEqual((await info()).runtimeStarts,[]);
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Make this chat active now.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.getByText('Fixture reply to a real submitted turn.',{exact:true}).waitFor();
 await page.waitForFunction(id=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.recent.items[0]?.id===id,quiet);
 assert.deepEqual((await info()).runtimeSends,[quiet]);
 assert.equal((await state()).chatNavigation.items[0].pinned,false,'actual activity promotes the unpinned chat');

 // Pins, scope, and shared agent visibility survive destruction/recreation of the service.
 await agent('session.pin',{id:beta.id,pinned:true});
 await page.waitForFunction(id=>window.amplifier.getShellState()?.snapshots?.chats?.sidebarNavigation?.pinned.items[0]?.id===id,beta.id);
 const agentPage=await agent('shell.query',{clientId:await page.evaluate(()=>window.amplifier.shellClientId),instanceId:'chats'});
 assert.equal(agentPage.result.sidebarNavigation.recent.scope.mode,'all');assert.equal(agentPage.result.sidebarNavigation.pinned.items[0].id,beta.id);
 const restartService=async()=>{
  const generation=(await info()).generation;
  await api('/api/fixture/restart',{});
  for(let attempt=0;attempt<100;attempt++){
   try{
    const response=await page.request.get(target+'/api/fixture/info');
    if(response.ok()&&(await response.json()).generation>generation)break;
   }catch{}
   if(attempt===99)throw Error('Fixture service did not restart');
   await new Promise(resolve=>setTimeout(resolve,100));
  }
  await page.reload();await page.locator('[data-sidebar-section=recent] .a-nav-chat').first().waitFor();await waitScope('all');
 };
 await restartService();
 await page.waitForFunction(id=>window.amplifier.getState().pinnedSessionIds.includes(id),beta.id);
 assert.equal((await state()).sidebarNavigation.pinned.items[0].id,beta.id);
 assert.equal((await state()).selectedSessionId,quiet);
 await page.screenshot({path:'/tmp/chat-library-views-desktop.png'});

 // Long paths and icon controls remain accessible inside a narrow navigation panel.
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight),'document stays inside narrow viewport');
 const overflow=await rows().evaluateAll(elements=>elements.filter(element=>element.scrollWidth>element.clientWidth+1).length);
 assert.equal(overflow,0,'chat labels truncate without horizontal row overflow');
 await row(beta.id).getByRole('button',{name:/Details and actions/}).click();
 assert.ok(await page.locator('.a-navigation-flyout').getByRole('button',{name:'Unpin Beta latest renamed',exact:true}).isVisible());
 await page.keyboard.press('Escape');
 await page.screenshot({path:'/tmp/chat-library-views-narrow.png'});
 await page.setViewportSize({width:1280,height:900});

 // Unpinning is durable too; reopening cannot resurrect a removed favorite.
 await agent('session.pin',{id:beta.id,pinned:false});
 await page.waitForFunction(id=>!window.amplifier.getState().pinnedSessionIds.includes(id),beta.id);
 await restartService();
 assert.equal((await state()).pinnedSessionIds.includes(beta.id),false);
 assert.equal((await state()).chatNavigation.items[0].id,quiet);

 // The All view retains an explicit workspace target for new chats.
 await row(beta.id).locator('.a-nav-chat-select').click();
 await page.waitForFunction(id=>window.amplifier.getState().selectedSessionId===id,beta.id);
 const newChat=page.getByRole('button',{name:'New chat',exact:true});
 assert.equal(await newChat.getAttribute('title'),'New chat');
 await newChat.click();
 await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId===null&&window.amplifier.getShellState()?.snapshots?.chats?.selectedSessionId===null);
 current=await state();
 assert.equal(current.selectedSessionId,null);
 assert.equal(current.view.newSessionDraft.workspace,paths.two);
 assert.equal(current.chatNavigation.scope.mode,'all');
 assert.deepEqual((await info()).runtimeSends,[quiet]);

 }else{
  await agent('view.update',{patch:{navFilter:''}});await waitScope('all');
  beta=(await state()).chatNavigation.items.find(chat=>chat.title==='Beta 002');
 }
 const touchTitle=(await state()).sessions.find(chat=>chat.id===beta.id).title;
 // A saved custom skin is injected after new base CSS. Its hidden
 // actions/nowrap rules must not make the new controls inaccessible on touch.
 const legacyCss=await readFile(new URL('../../tests/fixtures/legacy_theme.css',import.meta.url),'utf8');
 assert.ok(legacyCss.includes('#amp-one .a-nav-chat .a-nav-chat-edit{width:22px;height:30px;display:none;flex-shrink:0}'));
 await agent('theme.apply',{name:'Saved custom skin',css:legacyCss});
 const touchContext=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true,deviceScaleFactor:2,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const touchPage=await touchContext.newPage();touchPage.on('pageerror',error=>errors.push(error.message));
 try{
  await touchPage.goto(vite.resolvedUrls.local[0]);
  await touchPage.getByRole('button',{name:'Open navigation',exact:true}).tap();
  await touchPage.locator('[data-sidebar-section=recent] .a-nav-chat').first().waitFor();
  await touchPage.waitForFunction(()=>window.amplifier.getState().theme.name==='Saved custom skin');
  assert.equal(await touchPage.evaluate(()=>matchMedia('(hover: none)').matches&&matchMedia('(pointer: coarse)').matches),true);
  const selectedBefore=await touchPage.evaluate(()=>window.amplifier.getState().selectedSessionId);
  assert.notEqual(selectedBefore,beta.id);
  assert.equal(await touchPage.evaluate(()=>window.amplifier.getState().theme.css),legacyCss,'saved theme is retained unchanged');
  const touchRow=touchPage.locator(`.a-nav-chat[data-session-id="${beta.id}"]`);
  const more=touchRow.getByRole('button',{name:/Details and actions/});
  assert.equal(await more.isVisible(),true);await more.tap();
  const pin=touchPage.locator('.a-navigation-flyout').getByRole('button',{name:'Pin '+touchTitle,exact:true});
  await pin.waitFor();
  assert.equal(await pin.isVisible(),true,'an unselected/unpinned row exposes Pin on touch, despite the saved old skin');
  assert.ok(['flex','inline-flex'].includes(await pin.evaluate(element=>getComputedStyle(element).display)));
  const path=touchRow.locator('.a-nav-chat-workspace');
  assert.equal(await path.getAttribute('title'),paths.two);
  assert.ok((await touchPage.locator('.a-navigation-flyout').innerText()).includes(paths.two));
  assert.equal(await path.evaluate(element=>getComputedStyle(element).whiteSpace),'nowrap');
  assert.equal(await touchRow.evaluate(element=>element.scrollWidth>element.clientWidth+1),false);
  await pin.tap();
  await touchPage.waitForFunction(id=>window.amplifier.getState().pinnedSessionIds.includes(id),beta.id);
  assert.equal(await touchPage.evaluate(()=>window.amplifier.getState().selectedSessionId),selectedBefore,'pinning an unselected chat does not select it');
  await touchRow.getByRole('button',{name:/Details and actions/}).tap();
  await touchPage.locator('.a-navigation-flyout').getByRole('button',{name:'Unpin '+touchTitle,exact:true}).waitFor();
  assert.ok(await touchPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
  await touchPage.screenshot({path:'/tmp/chat-library-views-legacy-touch.png'});
 }catch(error){
  await touchPage.screenshot({path:'/tmp/chat-library-views-legacy-touch-failure.png'}).catch(()=>{});
  throw error;
 }finally{await touchContext.close()}
 assert.deepEqual(errors,[]);
 console.log(legacyTouchOnly?'Saved custom skin real-touch checks passed: visible unselected-row details and Pin, fixed rows and full-path flyouts, tap without selecting, saved skin retained.':'Chat library browser checks passed: 206 roots, bounded Recent pages, independent sidebar sections, full paths, fnmatch search, pins first, true activity recency, agent parity, real service restart pin/unpin persistence, narrow layout, correct new-chat workspace, real touch with saved custom skin, no provider calls.');
}catch(error){
 await page?.screenshot({path:'/tmp/chat-library-views-failure.png'}).catch(()=>{});
 if(fixtureLog)console.error(fixtureLog);
 throw error;
}finally{
 await browser?.close();await vite?.close();
 if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}
}
