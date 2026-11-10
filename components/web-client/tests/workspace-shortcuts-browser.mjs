import {showFixtureConversation} from './browser-chat-pages.mjs';
// Manager-run DTU fixture: production assets/actions, synthetic runtime, no model calls.
// Run from frontend after the manager builds this candidate: node tests/workspace-shortcuts-browser.mjs
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-shortcuts';
let browser,context,page,calls=[];
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{ }),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const headers={Authorization:'Bearer fixture-browser-control-token'},errors=[];
 context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:headers});
 page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const saved=async target=>{
  const clientId=await target.evaluate(()=>window.amplifier.getState().client.id);
  const response=await target.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':clientId}});
  assert.ok(response.ok());return response.json();
 };
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 const expectComposer=async text=>{await expect(composer).toHaveDraft(text);await expect.poll(()=>composer.textContent()).toBe(text)};
 const home=path.dirname((await state()).workspaceDefaults.root),workspaces=[];
 for(const team of ['team-one','team-two']){
  const folder=path.join(home,team,'same-long-workspace-name-that-must-truncate-without-moving-actions');
  await mkdir(folder,{recursive:true});assert.equal((await action('workspace.add',{path:folder})).accepted,true);
  const rows=(await action('workspace.list',{query:folder})).result.items;
  workspaces.push(rows.find(row=>row.path===folder));assert.ok(workspaces.at(-1));
 }
 const [a,b]=workspaces,created=await action('session.create',{workspace:a.path,title:'Original chat'}),sid=created.result.sessionId;
 await action('session.draft',{workspace:a.path});
 const setup={workspace:a.path,location:{kind:'workspace'},bundle:'work',selection:{instance:'test-provider',model:'chosen-model',effort:'high'}};
 await action('view.update',{patch:{newSessionDraft:setup,draft:'Retained unsent chat'}});
 await action('attachment.add',{name:'unsent.txt',base64:'a2VlcA=='});
 await action('session.select',{id:sid});
 await action('view.update',{patch:{draft:'Original chat text',navPinned:true}});
 await action('attachment.add',{sessionId:sid,name:'original.txt',base64:'a2VlcA=='});
 await action('canvas.show',{kind:'text',title:'Original Canvas',content:'Retained original Canvas'});
 const canvasId=(await saved(page)).canvas.id;
 const other=await context.newPage();await other.setViewportSize({width:1280,height:900});await other.goto(url);await showFixtureConversation(other);await other.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const otherBefore=await saved(other),baseline=(await saved(page)).library.sessionCount;
 // A different workspace is visible in the main browser, and the active chat is in A.
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:a.id}});
 const section=page.locator('[data-sidebar-section=workspaces]'),pinned=page.locator('[data-sidebar-section=pinned]');
 await expect(pinned).toHaveCount(0);
 let workspaceRegion=section;
 const row=workspace=>workspaceRegion.locator('.a-workspace-row').filter({has:page.getByRole('button',{name:'New chat in '+workspace.path,exact:true})});
 const newChat=workspace=>row(workspace).getByRole('button',{name:'New chat in '+workspace.path,exact:true});
 const geometry=async workspace=>row(workspace).evaluate(node=>{
  const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
  const select=node.querySelector('.a-workspace-select'),draft=node.querySelector('.a-workspace-new-chat'),more=node.querySelector('.a-navigation-more'),label=node.querySelector('.a-workspace-label');
  if(node.querySelector('button button'))throw Error('Nested buttons');
  return {row:rect(node),select:rect(select),draft:rect(draft),more:rect(more),label:rect(label),overflow:node.scrollWidth>node.clientWidth+1};
 });
 for(const width of [1440,1024]){
  await page.setViewportSize({width,height:1000});await section.locator('.a-sidebar-section-heading>button').first().focus();await page.mouse.move(width-10,900);
  await expect(page.locator('.a-navigation-flyout')).toHaveCount(0);
  const before=await geometry(b);await row(b).hover();await expect(newChat(b)).toHaveCSS('opacity','1');
  const select=row(b).locator('.a-workspace-select'),title=select.locator('[data-scroll-title]').first();
  await expect(select).toHaveAttribute('data-tooltip','off');
  assert.equal(await select.locator('[title]').count(),0);
  await expect(title).toHaveAttribute('data-overflow','true');
  const motion=await title.evaluate(element=>{
   const text=element.firstElementChild,animations=text.getAnimations();
   animations.forEach(animation=>animation.finish());
   return {animations:animations.length,transform:getComputedStyle(text).transform};
  });
  assert.equal(motion.animations,1);assert.notEqual(motion.transform,'none');
  await expect(page.getByRole('dialog',{name:'Details for '+b.name,exact:true})).toBeVisible();
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  const hovered=await geometry(b);
  assert.deepEqual(hovered.row,before.row);assert.equal(before.overflow,false);assert.equal(hovered.overflow,false);
  assert.equal(before.draft.width,0);assert.equal(before.more.width,0);
  assert.ok(before.label.width>=hovered.label.width+55,'Resting workspace title reclaims both actions');
  assert.ok(hovered.label.x+hovered.label.width<=hovered.draft.x+1);
  assert.ok(hovered.draft.x+hovered.draft.width<=hovered.more.x+1);assert.ok(hovered.more.x+hovered.more.width<=hovered.row.x+hovered.row.width+1);
  await row(b).getByRole('button',{name:'Open chats in '+b.path,exact:true}).focus();await page.keyboard.press('Tab');
  await expect(newChat(b)).toBeFocused();await expect(newChat(b)).toHaveCSS('opacity','1');
  assert.deepEqual(await geometry(b),hovered);
  await page.keyboard.press('Escape');await section.locator('.a-sidebar-section-heading>button').first().focus();await page.mouse.move(width-10,900);
  await expect(page.locator('.a-navigation-flyout')).toHaveCount(0);
 }
 await row(b).locator('.a-workspace-select').focus();await page.keyboard.press('Tab');await expect(newChat(b)).toBeFocused();
 const callStart=calls.length;
 await page.keyboard.press('Enter');await expect.poll(async()=>(await saved(page)).view.newSessionDraft.workspace).toBe(b.path);
 let current=await saved(page);assert.equal(current.selectedSessionId,null);assert.equal(current.library.sessionCount,baseline);
 assert.equal(current.view.draft,'Retained unsent chat');assert.deepEqual(current.view.newSessionDraft.selection,setup.selection);assert.equal(current.view.newSessionDraft.bundle,'work');
 await expectComposer('Retained unsent chat');
 await expect(page.getByRole('button',{name:'Remove unsent.txt',exact:true})).toBeVisible();
 const invoked=calls.slice(callStart).flatMap(call=>call.action==='shell.command'?[{action:call.args.action,args:call.args.args}]:[call]);
 assert.ok(invoked.some(call=>call.action==='session.draft'&&call.args.workspace===b.path&&call.args.workspaceId===b.id));
 assert.equal(invoked.some(call=>['session.create','conversation.send','workspace.select'].includes(call.action)),false);
 await action('session.select',{id:sid});await expectComposer('Original chat text');await expect(page.getByRole('button',{name:'Remove original.txt',exact:true})).toBeVisible();
 current=await saved(page);assert.equal(current.canvas.id,canvasId);assert.equal(current.canvas.content,'Retained original Canvas');
 const otherAfter=await saved(other);assert.equal(otherAfter.selectedSessionId,otherBefore.selectedSessionId);assert.equal(otherAfter.selectedWorkspaceId,otherBefore.selectedWorkspaceId);assert.deepEqual(otherAfter.view,otherBefore.view);assert.deepEqual(otherAfter.canvas,otherBefore.canvas);
 await page.screenshot({path:out+'/desktop.png'});
 // Pin B before its first chat: both copies remain usable.
 const scopeBefore=await saved(page);
 await row(b).locator('.a-workspace-select').focus();
 await page.keyboard.press('Tab');await expect(newChat(b)).toBeFocused();
 await page.keyboard.press('Tab');
 await expect(row(b).getByRole('button',{name:'Details and actions for '+b.name,exact:true})).toBeFocused();
 await page.keyboard.press('Enter');
 const keyboardPin=page.getByRole('button',{name:'Pin workspace '+b.path,exact:true});
 await keyboardPin.focus();await page.keyboard.press('Enter');
 await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([b.id]);
 const scopeAfter=await saved(page);
 assert.equal(scopeAfter.selectedSessionId,scopeBefore.selectedSessionId);assert.equal(scopeAfter.selectedWorkspaceId,scopeBefore.selectedWorkspaceId);assert.deepEqual(scopeAfter.view,scopeBefore.view);assert.deepEqual(scopeAfter.canvas,scopeBefore.canvas);
 await expect(pinned.getByRole('button',{name:'New chat in '+b.path,exact:true})).toHaveCount(1);
 await expect(section.getByRole('button',{name:'New chat in '+b.path,exact:true})).toHaveCount(1);
 await action('session.pin',{id:sid,pinned:true});
 await expect(page.locator(`[data-sidebar-section=recent] [data-session-id="${sid}"]`)).toHaveCount(1);
 workspaceRegion=pinned;
 const order=()=>pinned.locator('[data-order-id]').evaluateAll(rows=>rows.map(row=>row.dataset.orderId));
 await expect.poll(order).toEqual(['workspace:'+b.id,'session:'+sid]);
 await expect(section.getByRole('button',{name:'New chat in '+b.path,exact:true})).toHaveCount(1);
 await pinned.getByRole('button',{name:'Reorder Original chat',exact:true}).focus();
 await page.keyboard.press('ArrowUp');
 await expect.poll(order).toEqual(['session:'+sid,'workspace:'+b.id]);
 await expect.poll(()=>pinned.evaluate(node=>node.getAnimations({subtree:true}).filter(animation=>animation.playState==='running').length)).toBe(0);
 // The same row-sized pointer control moves across types, not just among workspaces.
 const grip=pinned.getByRole('button',{name:'Reorder Original chat',exact:true}),from=await grip.boundingBox(),to=await row(b).boundingBox();
 await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();
 await page.mouse.move(from.x+from.width/2,to.y+to.height/2,{steps:8});await page.mouse.up();
 await expect.poll(order).toEqual(['workspace:'+b.id,'session:'+sid]);
 await expect.poll(()=>pinned.evaluate(node=>node.getAnimations({subtree:true}).filter(animation=>animation.playState==='running').length)).toBe(0);
 // Resting titles reclaim space in every shortcut, not just unpinned workspaces.
 const actionSpace=[];
 const checkSpace=async(target,labelSelector,selectSelector,actions)=>{
  assert.equal(await page.evaluate(()=>matchMedia('(hover:hover) and (pointer:fine)').matches),true,'Desktop checks require a real fine-hover context');
  await composer.focus();await page.mouse.move(1000,900);await page.keyboard.press('Escape');
  await expect(page.locator('.a-navigation-flyout')).toHaveCount(0);
  const measure=()=>target.evaluate((node,labelSelector)=>{
   const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
   return {row:rect(node),rail:rect(node.closest('.a-nav-rail')),label:rect(node.querySelector(labelSelector)),actions:[...node.querySelectorAll(':scope>.a-navigation-more,:scope>.a-workspace-new-chat')].map(rect)};
  },labelSelector);
  const rest=await measure();assert.ok(rest.actions.every(rect=>rect.width===0),JSON.stringify({rest,context:await target.evaluate(node=>({classes:node.className,hover:node.matches(':hover'),focus:node.matches(':focus-within'),fine:matchMedia('(hover:hover) and (pointer:fine)').matches,quiet:!!node.closest('.a-quiet-sidebar'),styles:[...node.querySelectorAll(':scope>.a-navigation-more,:scope>.a-workspace-new-chat')].map(el=>({width:getComputedStyle(el).width,min:getComputedStyle(el).minWidth,padding:getComputedStyle(el).padding,basis:getComputedStyle(el).flexBasis}))}))}));
  await page.screenshot({path:out+`/action-space-${actionSpace.length}-rest.png`});
  await target.hover();const hover=await measure();
  await page.screenshot({path:out+`/action-space-${actionSpace.length}-hover.png`});
  assert.deepEqual(hover.row,rest.row);assert.deepEqual(hover.rail,rest.rail);
  assert.ok(rest.label.width>=hover.label.width+actions*28-1);
  assert.ok(hover.label.x+hover.label.width<=hover.actions[0].x+1);
  assert.ok(hover.actions.every(rect=>rect.width>=26));
  await page.keyboard.press('Escape');await composer.focus();await page.mouse.move(1000,900);
  await expect(page.locator('.a-navigation-flyout')).toHaveCount(0);
  const select=target.locator(selectSelector),more=target.locator(':scope>.a-navigation-more');
  await select.focus();assert.deepEqual(await measure(),hover);
  for(let index=0;index<actions;index++)await page.keyboard.press('Tab');
  await expect(more).toBeFocused();await page.keyboard.press('Enter');
  await expect(page.locator('.a-navigation-flyout')).toBeVisible();
  assert.deepEqual(await measure(),hover,'Portaled details keep their trigger space');
  await page.keyboard.press('Escape');await expect(more).toBeFocused();
  // A collapsed trigger stays focusable; reverse Tab returns to its sibling.
  await composer.focus();await more.focus();assert.deepEqual(await measure(),hover);
  await page.keyboard.press('Shift+Tab');
  await expect(target.locator(actions===2?'.a-workspace-new-chat':selectSelector)).toBeFocused();
  await composer.focus();assert.deepEqual(await measure(),rest);
  actionSpace.push({kind:labelSelector,rest,hover});
 };
 const shortcutRows=[row(b),section.locator('.a-workspace-row').filter({has:page.getByRole('button',{name:'New chat in '+a.path,exact:true})}),pinned.locator(`[data-session-id="${sid}"]`),page.locator(`[data-sidebar-section=recent] [data-session-id="${sid}"]`)];
 await checkSpace(shortcutRows[0],'.a-workspace-label','.a-workspace-select',2);
 await checkSpace(shortcutRows[1],'.a-workspace-label','.a-workspace-select',2);
 await checkSpace(shortcutRows[2],'.a-nav-chat-label','.a-nav-chat-select',1);
 await checkSpace(shortcutRows[3],'.a-nav-chat-label','.a-nav-chat-select',1);
 // Everything mode promises visible list actions, including at rest.
 const presentation=detail=>page.evaluate(async detail=>{
  const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,interfaceDetail:detail}}},{presentation:true});
  await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id},{presentation:true});
 },detail);
 await presentation('detailed');await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail','detailed');
 await composer.focus();await page.mouse.move(1000,900);
 for(const target of shortcutRows){
  assert.equal(await target.evaluate(node=>node.matches(':hover,:focus-within,.is-peeking')),false);
  const more=target.locator(':scope>.a-navigation-more');
  await expect(more).toHaveCSS('opacity','1');
  assert.ok((await more.boundingBox()).width>=26,'Everything mode keeps resting list actions visible');
 }
 await page.screenshot({path:out+'/action-space-detailed.png'});
 await presentation('standard');await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail','standard');
 for(const target of shortcutRows)assert.equal((await target.locator(':scope>.a-navigation-more').boundingBox()).width,0);
 await writeFile(out+'/action-space.json',JSON.stringify(actionSpace,null,2)+'\n');
 await row(b).hover();
 const pinnedGeometry=await geometry(b);
 assert.ok(pinnedGeometry.label.width>=50,'Pinned workspace name retains readable width: '+JSON.stringify(pinnedGeometry));
 assert.equal(pinnedGeometry.overflow,false);
 assert.ok(pinnedGeometry.draft.x+pinnedGeometry.draft.width<=pinnedGeometry.more.x+1);
 const labelGeometry=await row(b).evaluate(node=>{
  const rect=el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}};
  const label=node.querySelector('.a-workspace-label'),icon=node.querySelector('.a-workspace-select>.a-navigation-status');
  return {row:rect(node),label:rect(label),icon:rect(icon),lines:[...label.children].map(line=>({rect:rect(line),overflow:getComputedStyle(line).overflowX})),overflow:getComputedStyle(label).overflowX};
 });
 assert.ok(labelGeometry.icon.right<=labelGeometry.label.left);
 assert.ok(labelGeometry.label.right<=pinnedGeometry.draft.x);
 assert.ok(labelGeometry.row.top<=labelGeometry.label.top&&labelGeometry.label.bottom<=labelGeometry.row.bottom);
 assert.equal(labelGeometry.overflow,'hidden');
 assert.ok(labelGeometry.lines.every(line=>line.rect.left>=labelGeometry.label.left&&line.rect.right<=labelGeometry.label.right&&line.overflow==='hidden'));
 await composer.focus();await page.mouse.move(1000,900);await expect(page.getByRole('tooltip')).toHaveCount(0);
 await page.screenshot({path:out+'/mixed-pins.png'});
 // Run fine-hover checks before touch emulation, which changes Chromium's
 // pointer media for the rest of this context even after disabling touch.
 // Touch uses always-visible sibling targets and closes the modal on New chat.
 const touch=page,desktopViewport=page.viewportSize(),cdp=await context.newCDPSession(page);
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await cdp.send('Emulation.setEmitTouchEventsForMouse',{enabled:true,configuration:'mobile'});
 await touch.setViewportSize({width:390,height:844});
 assert.equal(await touch.evaluate(()=>matchMedia('(pointer:coarse)').matches&&matchMedia('(hover:none)').matches),true);
 await expect(touch.locator('#workspace-navigation')).toHaveAttribute('role','dialog');
 await expect.poll(async()=>(await saved(touch)).view.navExpanded).toBe(false);
 const openNavigation=touch.getByRole('button',{name:'Open navigation',exact:true});
 await expect(openNavigation).toBeVisible();
 const openPoint=await openNavigation.boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:openPoint.x+openPoint.width/2,y:openPoint.y+openPoint.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 const touchScopeBefore=await saved(touch),touchCallStart=calls.length;
 const tap=async locator=>{await expect(locator).toBeVisible();await locator.click({trial:true});const box=await locator.boundingBox();await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+box.width/2,y:box.y+box.height/2}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]})};
 await tap(row(b).getByRole('button',{name:'Details and actions for '+b.name,exact:true}));
 await tap(touch.getByRole('button',{name:'Unpin workspace '+b.path,exact:true}));
 await expect.poll(async()=>(await saved(touch)).pinnedWorkspaceIds).toEqual([]);
 workspaceRegion=section;
 await tap(row(b).getByRole('button',{name:'Details and actions for '+b.name,exact:true}));
 await tap(touch.getByRole('button',{name:'Pin workspace '+b.path,exact:true}));
 await expect.poll(async()=>(await saved(touch)).pinnedWorkspaceIds).toEqual([b.id]);
 workspaceRegion=pinned;
 const touchScopeAfter=await saved(touch);
 assert.equal(touchScopeAfter.selectedSessionId,touchScopeBefore.selectedSessionId);
 assert.equal(touchScopeAfter.selectedWorkspaceId,touchScopeBefore.selectedWorkspaceId);
 assert.equal(touchScopeAfter.view.draft,touchScopeBefore.view.draft);
 assert.equal(touchScopeAfter.canvas.id,touchScopeBefore.canvas.id);
 assert.equal(touchScopeAfter.library.sessionCount,baseline);
 const touchPinCalls=calls.slice(touchCallStart).flatMap(call=>call.action==='shell.command'?[{action:call.args.action,args:call.args.args}]:[call]);
 assert.equal(touchPinCalls.some(call=>['session.select','session.create','conversation.send','workspace.select'].includes(call.action)),false,'Touch workspace pin actions must not select or execute a conversation');
 const touchNew=touch.locator('[data-sidebar-section=workspaces]').getByRole('button',{name:'New chat in '+b.path,exact:true});
 await expect(touchNew).toBeVisible();await expect(touchNew).toHaveCSS('opacity','1');
 await touchNew.click({trial:true});
 const box=await touchNew.boundingBox();assert.ok(box.width>=44&&box.height>=44);
 await tap(touchNew);
 await expect.poll(async()=>(await saved(touch)).view.newSessionDraft.workspace).toBe(b.path);
 assert.equal((await saved(touch)).library.sessionCount,baseline);await expect.poll(async()=>(await saved(touch)).view.navExpanded).toBe(false);
 await touch.screenshot({path:out+'/touch.png'});
 await cdp.send('Emulation.setEmitTouchEventsForMouse',{enabled:false});
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:false});
 await page.setViewportSize(desktopViewport);
 // Re-pinning appends the workspace; restore the prior fixture order through
 // its public keyboard control before the independent reload/order assertions.
 await expect.poll(order).toEqual(['session:'+sid,'workspace:'+b.id]);
 await pinned.getByRole('button',{name:'Reorder '+b.name,exact:true}).focus();
 await page.keyboard.press('ArrowUp');
 await expect.poll(order).toEqual(['workspace:'+b.id,'session:'+sid]);
 await action('session.select',{id:sid});await expectComposer('Original chat text');
 assert.equal((await saved(page)).canvas.id,canvasId);
 await expect.poll(async()=>(await saved(page)).canvas.renderReports?.preview?.status).toBe('ready');
 await page.reload();await composer.waitFor();await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([b.id]);
 await expect.poll(order).toEqual(['workspace:'+b.id,'session:'+sid]);
 for(let index=0;index<8;index++){
  const folder=path.join(home,'active-'+index);await mkdir(folder,{recursive:true});await action('workspace.add',{path:folder});
  await action('session.create',{workspace:folder,title:'Activity pressure '+index});
 }
 await expect(pinned.getByRole('button',{name:'New chat in '+b.path,exact:true})).toHaveCount(1);
 await expect(section.getByRole('button',{name:'New chat in '+b.path,exact:true})).toHaveCount(0);
 await row(b).hover();
 await row(b).getByRole('button',{name:'Details and actions for '+b.name,exact:true}).click();
 const details=page.getByRole('dialog',{name:'Details for '+b.name,exact:true});
 await expect(details.getByText('0',{exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await action('workspace.rename',{id:b.id,name:'Pinned empty workspace'});
 await expect(row(b).getByRole('button',{name:'Details and actions for Pinned empty workspace',exact:true})).toBeVisible();
 await action('workspace.pin',{id:a.id,pinned:true});
 await row(b).hover();
 await row(b).getByRole('button',{name:'Details and actions for Pinned empty workspace',exact:true}).click();
 await page.getByRole('button',{name:'Move workspace down '+b.path,exact:true}).click();
 await expect.poll(order).toEqual(['session:'+sid,'workspace:'+b.id,'workspace:'+a.id]);
 await page.getByRole('button',{name:'Move workspace down '+b.path,exact:true}).click();
 await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([a.id,b.id]);
 await page.reload();await composer.waitFor();
 await expect.poll(async()=>(await saved(page)).pinnedWorkspaceIds).toEqual([a.id,b.id]);
 assert.equal(await pinned.getByRole('button',{name:'New chat in '+b.path,exact:true}).count(),1);
 // Pinned workspace keeps the same usable sibling actions on coarse-touch phones.
 await action('session.select',{id:sid});await expectComposer('Original chat text');
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await cdp.send('Emulation.setEmitTouchEventsForMouse',{enabled:true,configuration:'mobile'});
 await page.setViewportSize({width:390,height:844});
 await expect.poll(async()=>(await saved(page)).view.navExpanded).toBe(false);
 const pinOpen=page.getByRole('button',{name:'Open navigation',exact:true});await expect(pinOpen).toBeVisible();
 const pinOpenPoint=await pinOpen.boundingBox();
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:pinOpenPoint.x+pinOpenPoint.width/2,y:pinOpenPoint.y+pinOpenPoint.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 const pinnedNew=pinned.getByRole('button',{name:'New chat in '+b.path,exact:true});
 await expect(pinnedNew).toBeVisible();await expect(pinnedNew).toHaveCSS('opacity','1');
 const pinnedTouchBox=await pinnedNew.boundingBox();assert.ok(pinnedTouchBox.width>=44&&pinnedTouchBox.height>=44);
 const pinMobile=await geometry(b);assert.ok(pinMobile.label.width>=50);assert.equal(pinMobile.overflow,false);
 await page.screenshot({path:out+'/mixed-pins-touch.png'});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:pinnedTouchBox.x+pinnedTouchBox.width/2,y:pinnedTouchBox.y+pinnedTouchBox.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await expect.poll(async()=>(await saved(page)).selectedSessionId).toBe(null);
 await expect.poll(async()=>(await saved(page)).view.newSessionDraft.workspace).toBe(b.path);
 await expect.poll(async()=>(await saved(page)).view.navExpanded).toBe(false);
 await cdp.send('Emulation.setEmitTouchEventsForMouse',{enabled:false});
 await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:false});await page.setViewportSize(desktopViewport);
 await action('session.select',{id:sid});await expectComposer('Original chat text');
 // Last eligible unpin removes the whole section, with no empty instructional row.
 await action('workspace.pin',{id:a.id,pinned:false});await action('workspace.pin',{id:b.id,pinned:false});await action('session.pin',{id:sid,pinned:false});
 await expect(pinned).toHaveCount(0);
 const afterPins=await saved(other);assert.equal(afterPins.selectedSessionId,otherBefore.selectedSessionId);assert.equal(afterPins.selectedWorkspaceId,otherBefore.selectedWorkspaceId);assert.deepEqual(afterPins.view,otherBefore.view);
 const runtime=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(runtime.sent,[]);assert.deepEqual(runtime.started,[]);assert.deepEqual(runtime.stopped,[]);assert.deepEqual(errors,[]);
 console.log(JSON.stringify({status:'passed',scenarios:['empty Pinned hidden','row path not active folder','no creation or model turn','retained draft text attachments model bundle','chat Canvas roundtrip','other client preserved','long equal names stable hover and focus geometry','keyboard workspace Details and pin activation','actual touch workspace Details and unpin/re-pin preserve selection and draft','touch target','empty workspace pin survives reload and activity pressure','rename keeps pin identity','mixed keyboard and drag order survives reload without duplicates','last unpin hides section'],screenshots:out}));
}catch(error){if(page){await page.screenshot({path:out+'/failure.png'});await writeFile(out+'/failure-evidence.json',JSON.stringify({error:error.message,calls,state:await page.evaluate(()=>window.amplifier.getState()),pinned:await page.locator('[data-sidebar-section=pinned]').evaluateAll(nodes=>nodes.map(node=>node.outerHTML))},null,2))}throw error}
finally{await context?.close();await browser?.close();fixture.kill('SIGTERM')}
