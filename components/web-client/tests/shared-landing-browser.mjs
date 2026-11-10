// Production client/catalog, isolated synthetic fixture. No live history/providers.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks/shared-landing');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(root,'tests/fixtures/chat_library_server.py')],
 {env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',browser,isolatedBrowser,page,failure;
const launchOptions={headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{})};
fixture.stderr.on('data',chunk=>{logs=(logs+chunk).slice(-12000)});
const report={checks:[],errors:[],liveProvider:false};
try{
 const port=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+logs)),30000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.port){clearTimeout(timer);resolve(data.port)}}catch{}});
 });
 browser=await chromium.launch(launchOptions);
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(15000);page.on('pageerror',error=>report.errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const info=async()=>(await page.request.get(`http://127.0.0.1:${port}/api/fixture/info`)).json();
 const landing=page.locator('.a-adaptive-home:visible'),sidebar=page.locator('#workspace-navigation');
 const choose=mode=>landing.getByRole('group',{name:'Work view',exact:true}).getByRole('button',{name:mode,exact:true}).click();
 const settled=()=>expect.poll(async()=>Object.keys((await shell()).navigationResourcePending||{}).length).toBe(0);
 const openSidebar=async()=>{if(!await sidebar.isVisible())await page.locator('#navigation-toggle').click()};
 const destinationCommands=new Set();
 const acceptedDestination=async(surface,click)=>{
  // Each helper waits for its click before another destination helper begins.
  // Visible rows can be optimistic or retained while this command is queued.
  const requested=page.waitForRequest(request=>{
   if(request.method()!=='POST'||new URL(request.url()).pathname!=='/api/actions')return false;
   const command=request.postDataJSON();return command?.action==='view.update'&&command.args?.patch?.workSurface===surface;
  });
  await click();const request=await requested,command=request.postDataJSON();
  assert.equal(typeof command.id,'string');assert.ok(command.id);assert.ok(!destinationCommands.has(command.id));destinationCommands.add(command.id);
  const response=await request.response();assert.ok(response);assert.equal(response.request(),request);
  const receipt=await response.json();assert.equal(receipt.accepted,true);
  await expect.poll(async()=>{const current=await state();return current.client.hostInstanceId===receipt.hostInstanceId&&current.revision>=receipt.stateRevision}).toBe(true);
 };
 const home=async()=>{await openSidebar();await acceptedDestination('home',()=>sidebar.getByRole('button',{name:'Home',exact:true}).click());await expect(landing).toHaveAttribute('data-landing','home');await settled()};
 const allChats=async()=>{await openSidebar();await acceptedDestination('chats',()=>sidebar.getByRole('button',{name:'Search chats',exact:true}).click());await expect(landing.getByRole('heading',{level:1})).toHaveText('All chats');await settled()};
 const allWorkspaces=async()=>{await openSidebar();await sidebar.getByRole('button',{name:'Browse',exact:true}).click();await acceptedDestination('workspaces',()=>sidebar.getByRole('button',{name:'All workspaces',exact:true}).click());await expect(landing).toHaveAttribute('data-landing','workspaces');await settled()};
 const focusRow=()=>landing.locator('.a-landing-chat[data-session-id]');
 const pickUpAgain=()=>landing.getByRole('group',{name:'Filter work'}).getByRole('button',{name:/^Pick up again/});
 const checkIdle=async(section,filter='')=>{
  await expect(pickUpAgain()).toHaveAttribute('aria-pressed','true');await settled();
  await expect(async()=>{
   // Capture coverage and rendered rows together; a new search can omit pending results.
   const observed=await page.evaluate(section=>{
    const snapshot=window.amplifier.getShellState().snapshots.chats;
    return {result:snapshot.sidebarNavigation[section],
     pending:Object.keys(snapshot.navigationResourcePending||{}),
     ids:[...document.querySelectorAll('.a-adaptive-home .a-landing-chat')].map(row=>row.dataset.sessionId)};
   },section);
   const result=observed.result;
   assert.equal(observed.pending.length,0);assert.ok(result);
   assert.equal(result.scope.statusFilter,'idle');assert.equal(result.scope.filter,filter);
   assert.ok(result.total>0);assert.equal(result.total,result.activityCounts.idle);
   assert.ok(result.items.every(row=>row.activity.kind==='idle'));
   assert.ok(result.items.every(row=>!['alpha-200','alpha-199','alpha-198','alpha-197'].includes(row.id)));
   assert.deepEqual(observed.ids,result.items.map(row=>row.id));
  }).toPass({timeout:15000});
 };
 await page.goto(`http://127.0.0.1:${port}`);await expect(landing).toHaveAttribute('data-landing','home');await settled();
 const initial=await info();
 await openSidebar();
 await expect(sidebar.getByRole('button',{name:'Home',exact:true})).toHaveAttribute('aria-current','page');
 await expect(sidebar.getByRole('button',{name:'Search chats',exact:true})).toBeVisible();
 await expect(sidebar.getByRole('button',{name:'New chat',exact:true})).toBeVisible();
 assert.equal(await page.locator('header #navigation-toggle').count(),1);
 await expect(page.locator('header')).not.toContainText('Home');
 await landing.getByRole('button',{name:'Home appearance',exact:true}).click();
 for(const detail of ['Focused','Everything','Balanced']){
  await landing.getByRole('group',{name:'Appearance detail'}).getByRole('button',{name:detail,exact:true}).click();
  await expect(landing.getByRole('group',{name:'Work view',exact:true}).getByRole('button')).toHaveCount(3);
 }
 await landing.getByRole('button',{name:'Close home appearance',exact:true}).click();
 await landing.getByRole('group',{name:'Filter work'}).getByRole('button',{name:/^Working/}).click();
 await expect(landing.getByRole('button',{name:'Clear status filter'})).toBeVisible();
 await choose('Brief');await settled();await expect(landing.getByRole('button',{name:'Clear status filter'})).toBeVisible();
 await choose('Studio');await settled();await expect(landing.getByRole('button',{name:'Clear status filter'})).toBeVisible();
 await landing.getByRole('button',{name:'Clear status filter'}).click();
 await page.screenshot({path:join(output,'home-studio.png')});
 report.checks.push('Home icon shares the first sidebar line with Browse/Status; header toggle remains available; Layout choices stay stable across display detail; active status remains visible in Brief and Studio');

 await allChats();await expect(page).toHaveTitle('All chats - Amplifier');
 await choose('Focus');await expect(focusRow()).toHaveCount(1);
 const first=await focusRow().getAttribute('data-session-id');
 await landing.getByRole('button',{name:'Next Focus item',exact:true}).click();await expect(focusRow()).not.toHaveAttribute('data-session-id',first);
 const second=await focusRow().getAttribute('data-session-id');
 await home();await allChats();await expect(landing).toHaveAttribute('data-view','focus');await expect(focusRow()).toHaveAttribute('data-session-id',second);
 await landing.getByRole('searchbox',{name:'Search chats',exact:true}).fill('Alpha');await settled();
 await landing.getByRole('group',{name:'Filter work'}).getByRole('button',{name:/^Working/}).click();await settled();
 await expect(landing.getByRole('button',{name:'Clear status filter'})).toBeVisible();
 await landing.getByRole('button',{name:'Clear status filter'}).click();await settled();
 await expect(landing.getByRole('searchbox',{name:'Search chats',exact:true})).toHaveValue('Alpha');
 await landing.getByRole('button',{name:'Clear search',exact:true}).click();await settled();
 await choose('Brief');await expect.poll(()=>landing.locator('.a-landing-chat').count()).toBeGreaterThan(1);
 await landing.getByLabel('Sort conversations',{exact:true}).selectOption('name');await settled();
 await landing.locator('.a-search-filters>summary').click();
 await expect(landing.getByLabel('Active or archived conversations',{exact:true})).toBeVisible();
 await expect(landing.getByLabel('Filter chats by location',{exact:true})).toBeVisible();
 await landing.locator('.a-search-filters>summary').click();
 await landing.locator('.a-navigation-more').first().click();
 await expect(page.getByRole('dialog')).toContainText('Chat details');
 await expect(page.getByRole('dialog').getByRole('button',{name:'Open chat',exact:true})).toBeVisible();
 await expect(page.getByRole('dialog').getByRole('button',{name:/^Rename /})).toBeVisible();
 await expect(page.getByRole('dialog').getByRole('button',{name:/^Archive /})).toBeVisible();
 await page.keyboard.press('Escape');
 await page.screenshot({path:join(output,'all-chats-brief.png')});
 await landing.getByRole('button',{name:'Show more conversations',exact:true}).click();await settled();
 await expect.poll(async()=>(await shell()).sidebarNavigation.search?.index).toBe(1);
 await choose('Focus');await expect(focusRow()).toHaveCount(1);
 await landing.getByRole('button',{name:'Previous Focus item',exact:true}).click();await settled();
 await expect.poll(async()=>(await shell()).sidebarNavigation.search?.index).toBe(0);
 await landing.getByRole('button',{name:'Next Focus item',exact:true}).click();await settled();
 await expect.poll(async()=>(await shell()).sidebarNavigation.search?.index).toBe(1);
 const later=await focusRow().getAttribute('data-session-id');
 await focusRow().locator('.a-nav-chat-select').click();await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await allChats();await expect(focusRow()).toHaveAttribute('data-session-id',later);
 await choose('Brief');await landing.getByRole('button',{name:'Show previous conversations',exact:true}).click();await settled();
 await choose('Studio');await page.screenshot({path:join(output,'all-chats-studio.png')});
 report.checks.push('All chats supports the shared layouts, saved Focus position, search-preserving status reset, existing sort/archive/location filters and shared chat action flyout');

 await pickUpAgain().click();await checkIdle('search');
 await home();await allChats();await checkIdle('search');
 await page.reload();await expect(landing).toHaveAttribute('data-landing','chats');await checkIdle('search');
 await landing.getByRole('searchbox',{name:'Search chats',exact:true}).fill('Beta');await checkIdle('search','Beta');
 await expect(landing.locator('.a-landing-chat')).toHaveCount(3);
 await landing.getByRole('button',{name:'Clear status filter'}).click();await settled();
 await expect(landing.getByRole('searchbox',{name:'Search chats',exact:true})).toHaveValue('Beta');
 await landing.getByRole('button',{name:'Clear search',exact:true}).click();await settled();

 await allWorkspaces();await expect(page).toHaveTitle('All workspaces - Amplifier');
 await landing.getByRole('button',{name:'Recent',exact:true}).click();await settled();
 await choose('Brief');await expect.poll(()=>landing.locator('.a-landing-workspace').count()).toBeGreaterThan(1);
 await expect(landing.getByLabel('Workspace archive filter',{exact:true})).toBeVisible();
 await landing.getByRole('searchbox',{name:'Filter workspaces',exact:true}).fill('playground');await settled();
 await expect(landing.locator('.a-landing-workspace')).toHaveCount(2);
 await landing.getByRole('searchbox',{name:'Filter workspaces',exact:true}).fill('');await settled();
 await landing.locator('.a-navigation-more').first().click();await expect(page.getByRole('dialog')).toContainText('Workspace details');
 await expect(page.getByRole('dialog').getByRole('button',{name:'Rename',exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await choose('Focus');await expect(landing.locator('.a-landing-workspace')).toHaveCount(1);
 await landing.getByRole('button',{name:'Next Focus item',exact:true}).click();
 const workspaceCursor=await landing.locator('.a-landing-workspace').getAttribute('data-workspace-path');
 await home();await allWorkspaces();await expect(landing.locator('.a-landing-workspace')).toHaveAttribute('data-workspace-path',workspaceCursor);
 await choose('Studio');await page.screenshot({path:join(output,'all-workspaces-studio.png')});
 await landing.getByRole('button',{name:'Browse folders',exact:true}).click();await settled();
 await expect(landing.getByRole('navigation',{name:'Workspace folder path'})).toBeVisible();
 await landing.getByRole('button',{name:'Recent',exact:true}).click();await settled();
 await landing.getByRole('button',{name:'Open chats in '+initial.paths.one,exact:true}).click();await settled();
 await expect(landing).toHaveAttribute('data-landing','workspace');
 await expect(landing.getByRole('group',{name:'Workspace view'})).toBeVisible();
 await choose('Brief');
 await expect(landing.getByRole('searchbox',{name:'Filter conversations',exact:true})).toBeVisible();
 await landing.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Files',exact:true}).click();
 await expect(landing.getByRole('region',{name:'Workspace files'})).toBeVisible();
 await landing.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Details',exact:true}).click();
 await expect(landing.getByRole('button',{name:'Rename workspace',exact:true})).toBeVisible();
 await expect(landing.locator('.a-workspace-work-entry')).toBeVisible();
 await expect(landing.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Work',exact:true})).toBeVisible();
 await landing.getByRole('group',{name:'Workspace view'}).getByRole('button',{name:'Chats',exact:true}).click();await settled();
 await expect(landing.locator('.a-workspace-work-entry')).toHaveCount(0);
 await choose('Focus');await expect(focusRow()).toHaveCount(1);
 await landing.getByRole('button',{name:'Next Focus item',exact:true}).click();
 const workspaceChat=await focusRow().getAttribute('data-session-id');
 await focusRow().locator('.a-nav-chat-select').click();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await page.getByRole('navigation',{name:'Breadcrumb'}).getByRole('button').last().click();await settled();
 await expect(landing).toHaveAttribute('data-view','focus');await expect(focusRow()).toHaveAttribute('data-session-id',workspaceChat);
 await page.screenshot({path:join(output,'workspace-focus.png')});
 report.checks.push('All workspaces keeps search, archive, folder browsing and workspace action menus; workspace landing keeps Chats/Work/Files/Details with Workspace work inspection in Details; Focus cursors and layouts survive leaving and returning');

 await choose('Brief');await pickUpAgain().click();await checkIdle('workspace');
 await landing.locator('.a-landing-chat .a-nav-chat-select').first().click();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 await page.getByRole('navigation',{name:'Breadcrumb'}).getByRole('button').last().click();await checkIdle('workspace');
 await page.reload();await expect(landing).toHaveAttribute('data-landing','workspace');await checkIdle('workspace');
 await landing.getByRole('button',{name:'Clear status filter'}).click();await settled();
 report.checks.push('Pick up again is accepted through All chats and workspace controls, returns only quiet read chats, survives navigation/reload, and clears without losing search');

 // A user can choose a destination from the optimistic drawer before its open
 // request arrives. That older request must not reopen the drawer afterward.
 let delayedDrawerOpen=false;
 const delayDrawerOpen=async route=>{
  const command=route.request().postDataJSON(),patch=command?.args?.patch;
  if(!delayedDrawerOpen&&page.viewportSize().width<=900&&command?.action==='view.update'&&patch?.navExpanded===true&&!patch.workSurface){
   delayedDrawerOpen=true;await new Promise(resolve=>setTimeout(resolve,800));
  }
  await route.continue();
 };
 await page.route('**/api/actions',delayDrawerOpen);
 for(const destination of [home,allChats,allWorkspaces]){
  await destination();await choose('Brief');
  if(await sidebar.isVisible())await page.locator('#navigation-toggle').click();
  for(const width of [320,820]){
   await page.setViewportSize({width,height:900});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`No overflow at ${width}`);
   assert.ok(await landing.evaluate(node=>node.scrollWidth<=node.clientWidth+1),`Landing fits at ${width}`);
   await expect(landing.getByRole('group',{name:'Work view',exact:true})).toBeVisible();
  }
 }
 assert.equal(delayedDrawerOpen,true);await expect(sidebar).toBeHidden();
 await page.setViewportSize({width:320,height:900});
 await page.locator('#amp-one').evaluate(node=>{node.style.setProperty('--a-text-base','20px');node.style.setProperty('--a-text-sm','18px');node.style.setProperty('--a-text-xs','16px')});
 await choose('Focus');assert.ok(await landing.evaluate(node=>node.scrollWidth<=node.clientWidth+1),'Enlarged text fits');
 await expect(landing.getByRole('button',{name:'New workspace',exact:true})).toBeVisible();
 await page.locator('#amp-one').evaluate(node=>{for(const name of ['--a-text-base','--a-text-sm','--a-text-xs'])node.style.removeProperty(name)});
 await choose('Brief');await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(output,'phone-workspaces.png')});
 await allChats();if(await sidebar.isVisible())await page.locator('#navigation-toggle').click();await page.screenshot({path:join(output,'phone-chats.png')});
 await page.emulateMedia({forcedColors:'active'});await openSidebar();
 await sidebar.getByRole('button',{name:'Home',exact:true}).focus();await page.keyboard.press('Enter');
 await expect(landing).toHaveAttribute('data-landing','home');await expect(sidebar).toBeHidden();
 await page.screenshot({path:join(output,'phone-home-forced-colors.png')});
 await page.emulateMedia({forcedColors:'none'});await page.setViewportSize({width:1440,height:1000});
 await allChats();await choose('Studio');await page.reload();await expect(landing).toHaveAttribute('data-landing','chats');await expect(landing).toHaveAttribute('data-view','studio');
 // Single-process Chromium needs another process for another isolated context.
 // Ordinary runs retain the normal separate-context behavior.
 isolatedBrowser=process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?await chromium.launch(launchOptions):undefined;
 const fresh=await (isolatedBrowser||browser).newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await fresh.goto(`http://127.0.0.1:${port}`);await expect(fresh.locator('.a-adaptive-home:visible')).toHaveAttribute('data-landing','home');await fresh.close();
 await isolatedBrowser?.close();isolatedBrowser=undefined;
 isolatedBrowser=process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?await chromium.launch(launchOptions):undefined;
 const linked=await (isolatedBrowser||browser).newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await linked.goto(`http://127.0.0.1:${port}/?chat=${encodeURIComponent(initial.initialSession)}`);await expect(linked.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();assert.equal(await linked.evaluate(()=>window.amplifier.getState().selectedSessionId),initial.initialSession);await linked.close();
 const after=await info();assert.deepEqual(after.runtimeStarts,initial.runtimeStarts);assert.deepEqual(after.runtimeSends,initial.runtimeSends);
 report.checks.push('320px, tablet, phone and enlarged text fit; a delayed mobile drawer open cannot overtake destination selection; keyboard Home and forced colors work; fresh windows start Home, refresh restores the destination and direct chat links open the chat; browsing starts no runtimes or model calls');
 assert.deepEqual(report.errors,[]);
 report.build=JSON.parse(await readFile(join(root,'amplifier_web/static/build.json'),'utf8'));report.passed=true;
}catch(error){failure=error;report.passed=false;report.failure=error.stack;report.fixtureLog=logs;report.diagnostics=await page?.evaluate(async()=>(await fetch('/api/diagnostics/performance')).json()).catch(()=>null);await page?.screenshot({path:join(output,'failure.png')}).catch(()=>{});report.text=await page?.locator('body').innerText().catch(()=>null)}
finally{
 try{await isolatedBrowser?.close()}finally{await browser?.close()}
 if(fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit');fixture.kill('SIGTERM');let timer;
  try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture cleanup timeout')),10000)})]);report.fixtureClosed=true}
  catch(error){fixture.kill('SIGKILL');await exited;failure ||= error;report.cleanupError=error.message;report.passed=false}
  finally{clearTimeout(timer)}
 }
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
if(failure)throw failure;
