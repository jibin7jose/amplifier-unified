// Production client/catalog, isolated synthetic fixture. No live history/providers.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||join(root,'output/browser-checks/home-sidebar');
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||join(root,'.venv/bin/python'),
 ['-u',join(root,'tests/fixtures/chat_library_server.py')],
 {env:{...process.env,AMPLIFIER_NAVIGATION_PROOF:'1'},stdio:['ignore','pipe','pipe']});
let logs='',browser,page,failure;
fixture.stderr.on('data',chunk=>{logs=(logs+chunk).slice(-12000)});
const report={checks:[],errors:[],liveProvider:false};
try{
 const port=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+logs)),30000);
  fixture.once('error',error=>{clearTimeout(timer);reject(error)});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.port){clearTimeout(timer);resolve(data.port)}}catch{}});
 });
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(15000);page.on('pageerror',error=>report.errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const shell=()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats);
 const act=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const info=async()=>(await page.request.get(`http://127.0.0.1:${port}/api/fixture/info`)).json();
 const home=page.locator('.a-adaptive-home'),sidebar=page.locator('#workspace-navigation');
 const siteNavigation=page.getByRole('navigation',{name:'Site navigation',exact:true});
 const homeButton=siteNavigation.getByRole('button',{name:'Home',exact:true});
 const brand=page.getByRole('button',{name:'Amplifier Home',exact:true});
 const sidebarView=sidebar.getByRole('group',{name:'Sidebar view',exact:true});
 const newChat=sidebar.getByRole('button',{name:'New chat',exact:true});
 const newChatStyle=()=>newChat.evaluate(node=>{
  const box=node.getBoundingClientRect(),style=getComputedStyle(node);
  return {x:box.x,y:box.y,width:box.width,height:box.height,padding:style.padding,background:style.backgroundColor,color:style.color,font:style.font,borderRadius:style.borderRadius};
 });
 const choose=mode=>home.getByRole('group',{name:'Work view'}).getByRole('button',{name:mode,exact:true}).click();
 const switchTo=mode=>sidebarView.getByRole('button',{name:mode,exact:true}).click();
 const ready=()=>expect(home.locator('.a-home-filters')).toBeVisible();
 const goHome=async()=>{await homeButton.click();await ready()};
 const settled=()=>expect.poll(async()=>Object.keys((await shell()).navigationResourcePending||{}).length).toBe(0);
 await page.goto(`http://127.0.0.1:${port}`);await ready();await choose('Brief');
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 // Compact sidebar qualification uses the same production fixture and actions.
 const baseline=await info(),originalPins=[...baseline.state.pinnedSessionIds];
 const slider=sidebar.getByRole('slider',{name:'Filter conversations by age',exact:true});
 const ageValue=()=>sidebar.locator('.a-chat-age-filter strong');
 const scroller=sidebar.locator('.a-nav-content');
 const options=sidebar.getByRole('button',{name:'App options',exact:true});
 const menu=sidebar.getByRole('group',{name:'App options',exact:true});
 const resetScroll=()=>scroller.evaluate(node=>{node.scrollTop=0});
 const oneRow=async()=>{
  await expect.poll(()=>sidebar.evaluate(node=>{const rail=node.getBoundingClientRect();return [...node.querySelectorAll('.a-sidebar-modes button')].every(button=>{const box=button.getBoundingClientRect();return box.left>=rail.left&&box.right<=rail.right})})).toBe(true);
  const boxes=await sidebar.locator('.a-sidebar-modes button').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right}}));
  assert.equal(boxes.length,3);assert.ok(boxes.every(box=>Math.abs(box.top-boxes[0].top)<2));
  assert.ok(boxes.every((box,i)=>!i||box.left>=boxes[i-1].right-1));
  const rail=await sidebar.boundingBox();assert.ok(boxes.every(box=>box.left>=rail.x&&box.right<=rail.x+rail.width));
  assert.ok(await newChat.evaluate(node=>{const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))}));
  await expect(newChat).toHaveText('');await expect(newChat).toHaveAttribute('title','New chat');
  await expect(sidebar.getByRole('button',{name:'Home',exact:true})).toHaveCount(0);
  await expect(homeButton).toHaveCount(1);
  await expect(newChat.locator('svg.lucide-message-square-plus')).toHaveCount(1);
 };
 await oneRow();
 assert.equal(await sidebar.locator('.a-chat-age-stops>span').count(),6);
 assert.equal(await sidebar.locator('.a-chat-age-stops').textContent(),'');
 assert.equal(await sidebar.locator('.a-chat-age-filter small').count(),0);
 assert.ok(await slider.evaluate(node=>getComputedStyle(node).padding==='0px'&&getComputedStyle(node).borderWidth==='0px'));
 const ages=['24h','7d','14d','30d','90d','all'];
 for(let index=0;index<ages.length;index++){
  await slider.focus();await page.keyboard.press('Home');
  for(let key=0;key<index;key++)await page.keyboard.press('ArrowRight');
  await expect(ageValue()).toHaveText(ages[index]==='all'?'All':ages[index]);
  await expect.poll(async()=>(await state()).view.navAge).toBe(ages[index]);
  await settled();
 }
 for(let index=0;index<ages.length;index++){
  const box=await slider.boundingBox();
  await page.mouse.click(box.x+8+(box.width-16)*index/5,box.y+box.height/2);
  await expect.poll(async()=>(await state()).view.navAge).toBe(ages[index]);
 }
 await act('view.update',{patch:{navAge:'1y'}});await expect(ageValue()).toHaveText('1y');
 await page.reload();await ready();await expect(ageValue()).toHaveText('1y');
 await slider.focus();await page.keyboard.press('ArrowLeft');await expect(ageValue()).toHaveText('90d');
 await act('view.update',{patch:{navAge:'1y'}});await slider.focus();await page.keyboard.press('ArrowRight');await expect(ageValue()).toHaveText('All');
 report.checks.push('Six unlabeled native tick stops work with pointer and keyboard, 14d is canonical; saved 1y remains truthful through reload and arrows choose 90d/All');
 const inspectFooter=async(name)=>{
  await options.click();await expect(menu).toBeVisible();
  for(const label of ['Automation','Activity','Settings','Send feedback','What the agent sees'])await expect(menu.getByRole('button',{name:new RegExp('^'+label)})).toBeVisible();
  const box=await menu.boundingBox(),footerBox=await sidebar.locator('.a-work-footer').boundingBox();
  assert.ok(box.y>=0&&box.y+box.height<=footerBox.y+footerBox.height&&box.x>=0);
  assert.ok(await menu.evaluate(node=>{const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.left+r.width/2,r.top+10))}));
  assert.ok(await menu.getByRole('button',{name:'What the agent sees',exact:true}).evaluate(node=>{const r=node.getBoundingClientRect();return node.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))}));
  await page.screenshot({path:join(output,name+'.png')});
  await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();
 };
 for(const width of [320,216]){
  await act('view.update',{patch:{navWidth:width,navPinned:true,navExpanded:true}});
  await oneRow();await resetScroll();
  const positions=()=>sidebar.evaluate(node=>Object.fromEntries(['.a-sidebar-modes','.a-nav-search-launch','.a-chat-age-filter','.a-work-footer'].map(selector=>[selector,node.querySelector(selector).getBoundingClientRect().top])));
  const top=await positions();await scroller.evaluate(node=>{node.scrollTop=node.scrollHeight});
  await expect.poll(()=>scroller.evaluate(node=>node.scrollTop)).toBeGreaterThan(0);
  const bottom=await positions();
  assert.equal(bottom['.a-sidebar-modes'],top['.a-sidebar-modes']);assert.equal(bottom['.a-work-footer'],top['.a-work-footer']);
  assert.ok(bottom['.a-nav-search-launch']<top['.a-nav-search-launch']);assert.ok(bottom['.a-chat-age-filter']<top['.a-chat-age-filter']);
  await inspectFooter('compact-footer-'+width);await resetScroll();
  await page.screenshot({path:join(output,'compact-desktop-'+width+'.png')});
 }
 const details=page.locator('.a-navigation-flyout');
 const pinTarget=baseline.state.sessions.find(row=>row.nativeIdentity==='alpha-198');
 await act('session.pin',{id:pinTarget.id,pinned:true});await settled();
 const pinnedRow=sidebar.locator(`[data-sidebar-section=pinned] [data-session-id="${pinTarget.id}"]`);
 await pinnedRow.hover();
 await pinnedRow.getByRole('button',{name:/Details and actions/}).click();await expect(details).toBeVisible();
 assert.deepEqual(await details.locator('code').allTextContents(),['alpha-198',pinTarget.workspace]);
 assert.ok(await details.evaluate(node=>[...node.querySelectorAll('.a-navigation-copy,dl')].findIndex(n=>n.textContent.includes('Session ID'))<[...node.querySelectorAll('.a-navigation-copy,dl')].findIndex(n=>n.textContent.startsWith('Workspace'))));
 await expect(details).not.toContainText('Discovered in');await expect(details.getByRole('button',{name:/^Move .* (up|down)$/})).toHaveCount(0);
 await page.context().grantPermissions(['clipboard-read','clipboard-write']);
 await details.getByRole('button',{name:'Copy session id',exact:true}).click();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'alpha-198');
 await page.screenshot({path:join(output,'compact-pinned-flyout.png')});await page.keyboard.press('Escape');
 await expect(pinnedRow.getByRole('button',{name:/Details and actions/})).toBeFocused();
 if(!originalPins.includes(pinTarget.id))await act('session.pin',{id:pinTarget.id,pinned:false});
 await settled();await resetScroll();
 const unpinnedRow=sidebar.locator(`[data-session-id="${pinTarget.id}"]`).first();await unpinnedRow.hover();
 await unpinnedRow.getByRole('button',{name:/Details and actions/}).click();
 await expect(details.getByRole('button',{name:'Pin '+pinTarget.title,exact:true})).toBeVisible();
 await page.screenshot({path:join(output,'compact-unpinned-flyout.png')});await page.keyboard.press('Escape');
 await act('view.update',{patch:{navAge:'24h'}});await settled();await resetScroll();
 await page.screenshot({path:join(output,'compact-short-list.png')});
 await act('view.update',{patch:{navAge:'all',navWidth:320}});
 await page.locator('#amp-one').evaluate(node=>{node.style.fontSize='20px';node.style.setProperty('--a-text-sm','16px')});
 await oneRow();await page.screenshot({path:join(output,'compact-enlarged-text.png')});
 await page.locator('#amp-one').evaluate(node=>{node.style.removeProperty('font-size');node.style.removeProperty('--a-text-sm')});
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.a-layout')).toHaveAttribute('data-narrow','true');
 await act('view.update',{patch:{navExpanded:false}});
 await expect(sidebar).toBeHidden();await page.locator('#navigation-toggle').click();await expect(sidebar).toBeVisible();
 await oneRow();await resetScroll();await page.screenshot({path:join(output,'compact-mobile.png')});
 await scroller.evaluate(node=>{node.scrollTop=node.scrollHeight});await inspectFooter('compact-mobile-footer');
 await page.keyboard.press('Escape');await expect(sidebar).toBeHidden();
 await expect(page.locator('#navigation-toggle')).toBeFocused();
 await page.setViewportSize({width:1440,height:1000});
 await act('view.update',{patch:{navPinned:true,navExpanded:true}});await resetScroll();
 assert.deepEqual((await info()).state.pinnedSessionIds,originalPins);
 assert.deepEqual((await info()).runtimeSends,baseline.runtimeSends);assert.deepEqual((await info()).runtimeStarts,baseline.runtimeStarts);
 report.checks.push('216px/320px one-row controls; search+age actually scroll while modes/footer stay fixed; upward footer entries and focus/Escape; mobile drawer; pinned/unpinned flyout native ID copy and order; pins/runtime preserved');
 await switchTo('Status');await settled();
 const before=await info(),selected=(await state()).selectedSessionId;
 await expect(brand).toBeVisible();await expect(homeButton).toHaveAttribute('aria-current','page');
 assert.equal(await brand.evaluate(node=>node.tagName),'BUTTON');
 assert.equal(await homeButton.evaluate(node=>node.tagName),'BUTTON');
 await expect(homeButton).toHaveText('');
 const homeStyle=await homeButton.evaluate(node=>{const style=getComputedStyle(node);return {background:style.backgroundColor,border:style.borderWidth}});
 assert.deepEqual(homeStyle,{background:'rgba(0, 0, 0, 0)',border:'0px'},'Home uses the existing unboxed icon treatment');
 const initialNewChatStyle=await newChatStyle();
 const track=await sidebarView.evaluate(node=>{const style=getComputedStyle(node),box=node.getBoundingClientRect();return {background:style.backgroundColor,outline:style.boxShadow,radius:parseFloat(style.borderRadius),bottom:box.bottom}});
 assert.notEqual(track.background,'rgba(0, 0, 0, 0)');assert.notEqual(track.outline,'none');assert.ok(track.radius>0);
 assert.ok(initialNewChatStyle.y<track.bottom&&initialNewChatStyle.y+initialNewChatStyle.height>track.bottom-40,'New chat shares the sidebar switch row');
 await expect(newChat).toHaveText('');
 await expect(sidebarView.getByRole('button')).toHaveCount(2);
 for(const label of ['New chat','Search chats'])await expect(sidebar.getByRole('button',{name:label,exact:true})).toBeVisible();
 assert.equal(await page.locator('header #navigation-toggle').count(),1);
 assert.equal(await sidebar.getByRole('button',{name:'Close navigation',exact:true}).count(),0);
 const headings=home.locator('.a-home-brief>.a-work-group h2');
 await expect(headings).toHaveText(['Needs you','Ready for you']);
 const boxes=await headings.evaluateAll(nodes=>nodes.map(node=>({x:node.getBoundingClientRect().x,y:node.getBoundingClientRect().y})));
 assert.ok(boxes[1].x>boxes[0].x&&Math.abs(boxes[1].y-boxes[0].y)<2);
 await page.screenshot({path:join(output,'desktop-status-brief.png')});
 await choose('Studio');
 await expect(home.locator('.a-home-studio>.a-work-group h2')).toHaveText(['Needs you','Ready for you','Working']);
 await page.screenshot({path:join(output,'desktop-status-studio.png')});
 report.checks.push('Brief pairs waiting/ready work; Studio separates three status columns; both sidebar actions remain accessible on Home');

 // Both views use the same chat row and respect the existing path preference.
 const completed=before.state.sessions.find(row=>row.nativeIdentity==='alpha-198');
 const chatRow=()=>sidebar.locator(`.a-nav-chat[data-session-id="${completed.id}"]`).first();
 const rowStyle=()=>chatRow().evaluate(node=>({
  height:node.getBoundingClientRect().height,
  location:getComputedStyle(node.querySelector('.a-nav-chat-workspace')).display,
  text:node.querySelector('.a-nav-chat-workspace').textContent,
  status:node.querySelector('.a-navigation-status').dataset.kind
 }));
 const workspaceName=before.state.workspaces.find(row=>row.id===completed.workspaceId).name;
 const statusStyle=await rowStyle();assert.equal(statusStyle.location,'block');
 assert.equal(statusStyle.text,workspaceName);assert.equal(statusStyle.height,54);
 await switchTo('Browse');await settled();
 await expect(sidebarView.getByRole('button',{name:'Browse',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(sidebarView.getByRole('button',{name:'Status',exact:true})).toHaveAttribute('aria-pressed','false');
 assert.deepEqual(await newChatStyle(),initialNewChatStyle);
 await sidebarView.getByRole('button',{name:'Status',exact:true}).focus();await page.keyboard.press('Space');await settled();
 await expect(sidebarView.getByRole('button',{name:'Status',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(sidebarView.getByRole('button',{name:'Browse',exact:true})).toHaveAttribute('aria-pressed','false');
 assert.deepEqual(await newChatStyle(),initialNewChatStyle);
 await switchTo('Browse');await settled();
 assert.deepEqual(await rowStyle(),statusStyle);
 report.checks.push('Browse/Status share one visual track and keep native pressed-button keyboard behavior; New chat geometry and appearance remain stable between modes');
 for(const mode of ['Browse','Status']){
  await switchTo(mode);await settled();
  await act('settings.update',{patch:{workspaces:{showPaths:true}}});await settled();
  await expect(chatRow().locator('.a-nav-chat-workspace')).toBeVisible();
  assert.equal((await rowStyle()).text,completed.workspace);
  assert.equal((await rowStyle()).height,54);
  await act('settings.update',{patch:{workspaces:{showPaths:false}}});await settled();
  await expect(chatRow().locator('.a-nav-chat-workspace')).toBeVisible();
  assert.equal((await rowStyle()).text,workspaceName);
 }
 for(const level of ['detailed','minimal','standard']){
  const current=await page.evaluate(()=>window.amplifier.getShellState());
  const composition=structuredClone(current.composition);
  composition.presentation={...composition.presentation,interfaceDetail:level,executionDetail:level};
  const clientId=(await state()).client.id;
  const prepared=await act('shell.changes.prepare',{clientId,expectedRevision:current.revision,composition});
  await act('shell.changes.apply',{clientId,expectedRevision:current.revision,changeId:prepared.result.id});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail',level);
  const styles=[];
  for(const mode of ['Browse','Status']){
   await switchTo(mode);await settled();styles.push(await rowStyle());
  }
  assert.deepEqual(styles[0],styles[1],level+' keeps the two sidebar presentations aligned');
  assert.equal(styles[0].location,'block');
  assert.equal(styles[0].text,workspaceName);assert.equal(styles[0].height,54);
 }
 await act('history.refresh',{});await settled();
 await expect(home).not.toContainText('Updating your work');
 await expect(sidebar).not.toContainText(/Updating|Finding existing chats/);
 assert.deepEqual(await rowStyle(),statusStyle);
 report.checks.push('Browse and Status share row height/status/location; folder-path preference is honored in both; background refresh leaves no updating text');
 const longName='Customer work — a deliberately long workspace name that must stay on one line in the sidebar';
 await act('workspace.rename',{id:completed.workspaceId,name:longName});await settled();
 for(const mode of ['Browse','Status']){
  await switchTo(mode);await settled();
  const subtitle=chatRow().locator('.a-nav-chat-workspace');
  await expect(subtitle).toBeVisible();await expect(subtitle).toHaveText(longName);
  const style=await subtitle.evaluate(node=>{const css=getComputedStyle(node),title=node.previousElementSibling;return {whiteSpace:css.whiteSpace,overflow:css.overflow,textOverflow:css.textOverflow,smaller:parseFloat(css.fontSize)<parseFloat(getComputedStyle(title).fontSize),below:node.getBoundingClientRect().top>=title.getBoundingClientRect().bottom}});
  assert.deepEqual(style,{whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',smaller:true,below:true});
  assert.equal((await rowStyle()).height,54);
 }
 await page.screenshot({path:join(output,'workspace-subtitle.png')});
 await act('workspace.rename',{id:completed.workspaceId,name:workspaceName});await settled();
 report.checks.push('Workspace names stay visible below chat titles in all detail levels; renamed/long names update in Browse and Status with small single-line ellipsis styling');

 const idle=sidebar.locator('[data-status-group=idle]');
 await idle.getByRole('button',{name:'Show more pick up again chats',exact:true}).click();
 const first=(await shell()).workStatusIdle;
 assert.ok(first.pages>1&&first.items.length<=50);
 await idle.getByRole('button',{name:'Next pick up again page',exact:true}).focus();
 await page.keyboard.press('Enter');await settled();
 await expect.poll(async()=>(await shell()).workStatusIdle?.index).toBe(1);
 await expect(idle.getByRole('button',{name:'Next pick up again page',exact:true})).toBeFocused();
 const saved=(await shell()).workStatusIdle.items.map(row=>row.id);
 assert.equal(saved.some(id=>first.items.some(row=>row.id===id)),false);
 await home.getByLabel('Search your work',{exact:true}).fill('no matching home query');await home.getByLabel('Search your work',{exact:true}).press('Tab');
 await expect(home).toContainText('No matching work.');
 assert.deepEqual((await shell()).workStatusIdle.items.map(row=>row.id),saved);
 const working=sidebar.locator('[data-status-group=working]');
 await working.getByRole('button',{name:/^Working/}).click();await settled();
 await expect.poll(async()=>(await shell()).navigationResources.includes('workStatusWorking')).toBe(false);
 await switchTo('Browse');await settled();await expect(sidebar.locator('[data-sidebar-section=workspaces]')).toBeVisible();
 await expect.poll(async()=>(await shell()).navigationResources.some(name=>name.startsWith('workStatus'))).toBe(false);
 await switchTo('Status');await settled();
 await expect(working.getByRole('button',{name:/^Working/})).toHaveAttribute('aria-expanded','false');
 await expect.poll(async()=>(await shell()).workStatusIdle?.index).toBe(1);
 await page.reload();await ready();await settled();
 await expect(sidebar.getByRole('button',{name:'Status',exact:true})).toHaveAttribute('aria-pressed','true');
 await expect(home.getByLabel('Search your work',{exact:true})).toHaveValue('no matching home query');
 assert.deepEqual((await shell()).workStatusIdle.items.map(row=>row.id),saved);
 assert.equal((await state()).selectedSessionId,selected);
 const after=await info();assert.deepEqual(after.runtimeStarts,before.runtimeStarts);assert.deepEqual(after.runtimeSends,before.runtimeSends);
 assert.deepEqual(after.state.attention,before.state.attention);
 report.checks.push('Status paging/expansion and Home search remain independent across Browse/Status and reload; navigation starts no workers and changes no attention');

 await idle.locator('.a-nav-chat-select').first().click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});await expect(composer).toBeVisible();
 await composer.fill('Keep my draft through both sidebar views');
 const chosen=(await state()).selectedSessionId;
 const breadcrumb=page.getByRole('navigation',{name:'Breadcrumb',exact:true});
 await expect(breadcrumb.getByRole('button',{name:'Home',exact:true})).toHaveCount(0);
 const current=await state(),workspace=current.workspaces.find(row=>row.id===current.selectedWorkspaceId);
 assert.ok(workspace,'The selected fixture chat has a workspace breadcrumb');
 await expect(breadcrumb.getByRole('button',{name:workspace.name,exact:true})).toBeVisible();
 await expect(breadcrumb.locator('[aria-current=page]')).toHaveText(current.sessions.find(row=>row.id===chosen).title);
 for(const [control,key] of [[brand,'Enter'],[brand,'Space'],[homeButton,'Enter']]){
  await control.focus();await page.keyboard.press(key);await ready();
  await expect(homeButton).toHaveAttribute('aria-current','page');
  assert.equal((await state()).selectedSessionId,chosen);
  assert.equal((await state()).view.navExpanded,false);
  await sidebar.locator(`[data-session-id="${chosen}"] .a-nav-chat-select`).click();
  await expect(composer).toContainText('Keep my draft through both sidebar views');
 }
 report.checks.push('Brand is a native Amplifier Home button; Enter/Space and site Home preserve the selected chat/draft; Breadcrumb keeps workspace and title, not Home');
 await switchTo('Browse');await switchTo('Status');await settled();await goHome();
 await expect(sidebar.locator('.a-nav-chat-select[aria-current=page]')).toHaveCount(0);
 await expect(page.locator('header')).not.toContainText('Home');
 await expect(home.getByLabel('Search your work',{exact:true})).toHaveValue('no matching home query');
 await sidebar.locator(`[data-session-id="${chosen}"] .a-nav-chat-select`).click();await expect(composer).toContainText('Keep my draft through both sidebar views');
 await goHome();await home.getByLabel('Search your work',{exact:true}).fill('');await home.getByLabel('Search your work',{exact:true}).press('Tab');await settled();
 await page.getByRole('button',{name:'Close navigation',exact:true}).click();await settled();
 await expect.poll(async()=>(await shell()).navigationResources.some(name=>name.startsWith('workStatus'))).toBe(false);
 await choose('Focus');await expect(sidebar).toBeHidden();
 report.checks.push('Chat selection/draft survives mode changes; Home has no stale highlight; hidden Status releases its page demand');

 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();await settled();
 await expect(sidebar).toBeVisible();await expect(page.locator('#navigation-toggle')).toBeEnabled();
 const rect=await sidebar.boundingBox(),header=await page.locator('header').boundingBox();
 assert.ok(rect.y>=header.y+header.height-1&&rect.width>=388);
 await page.screenshot({path:join(output,'phone-status.png')});
 await switchTo('Browse');await settled();await page.screenshot({path:join(output,'phone-browse.png')});
 await page.keyboard.press('Escape');await expect(sidebar).toBeHidden();await expect(page.locator('#navigation-toggle')).toBeFocused();
 await page.keyboard.press('Enter');await expect(sidebar).toBeVisible();
 await sidebar.getByRole('button',{name:'Search chats',exact:true}).click();await expect(sidebar).toBeHidden();
 await expect(page.getByRole('searchbox',{name:'Search chats',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await switchTo('Status');await settled();await sidebar.locator('.a-nav-chat-select').first().click();await expect(sidebar).toBeHidden();await expect(composer).toBeVisible();
 for(const width of [320,390,820]){
  await page.setViewportSize({width,height:900});await expect(homeButton).toBeVisible();
  const toggleBox=await page.locator('#navigation-toggle').boundingBox(),homeBox=await homeButton.boundingBox(),titleBox=await breadcrumb.locator('[aria-current=page]').boundingBox();
  assert.ok(Math.abs(homeBox.y+homeBox.height/2-toggleBox.y-toggleBox.height/2)<1,'Home stays on the site-navigation row');
  assert.ok(titleBox.width>0,'The destination title retains readable space');
  if(width<=760)assert.ok(titleBox.y>=homeBox.y+homeBox.height,'The title stays below site navigation on phones');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(width===320)await page.screenshot({path:join(output,'phone-320-chat-header.png')});
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await sidebar.getByRole('button',{name:'New chat',exact:true}).click();await expect(sidebar).toBeHidden();await expect(composer).toBeVisible();
 await goHome();await choose('Brief');
 for(const width of [320,820]){
  await page.setViewportSize({width,height:900});await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#navigation-toggle').click();await expect(sidebar).toBeHidden();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 }
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:join(output,'phone-brief.png')});
 await page.emulateMedia({forcedColors:'active'});await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await expect(sidebar.getByRole('button',{name:'Status',exact:true})).toBeVisible();await page.screenshot({path:join(output,'phone-forced-colors.png')});
 report.checks.push('Phone/tablet sidebar stays below the header; Escape and header control work; chat/search/new-chat close it; 320px and forced colors remain usable');
 assert.deepEqual(report.errors,[]);
 report.build=JSON.parse(await readFile(join(root,'amplifier_web/static/build.json'),'utf8'));report.passed=true;
}catch(error){failure=error;report.passed=false;report.failure=error.stack;report.fixtureLog=logs;report.diagnostics=await page?.evaluate(async()=>(await fetch('/api/diagnostics/performance')).json()).catch(()=>null);await page?.screenshot({path:join(output,'failure.png')}).catch(()=>{});report.text=await page?.locator('body').innerText().catch(()=>null)}
finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){
  const exited=once(fixture,'exit');fixture.kill('SIGTERM');let timer;
  try{await Promise.race([exited,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Fixture cleanup timeout')),10000)})]);report.fixtureClosed=true}
  catch(error){fixture.kill('SIGKILL');await exited;failure ||= error;report.cleanupError=error.message;report.passed=false}
  finally{clearTimeout(timer)}
 }
 await writeFile(join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}
if(failure)throw failure;
