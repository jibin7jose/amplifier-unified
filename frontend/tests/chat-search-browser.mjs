import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/chat_library_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/\{"port":\s*(\d+)\}/);if(match){clearTimeout(timer);resolve(Number(match[1]))}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(`http://127.0.0.1:${port}`);await showFixtureConversation(page,{navPinned:true});
 const draft=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await draft.fill('Keep this unfinished draft.');
 const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await action('session.pin',{id:selected,pinned:true});
 await page.getByRole('button',{name:'Search chats',exact:true}).click();
 const search=page.getByRole('searchbox',{name:'Search chats',exact:true}),area=page.getByRole('region',{name:'Chat search',exact:true});
 const results=area.locator('.a-nav-chat-select');
 await expect(search).toBeFocused();await expect(area.getByRole('status')).toHaveText('206 chats');
 await expect(page.getByRole('button',{name:/^Back(?: to chat)?$/})).toHaveCount(0);
 assert.equal(await page.locator('.a-browser-pins').count(),0);
 assert.ok((await search.boundingBox()).y<300,'search appears above results without scrolling');
 await expect(area.locator('details')).not.toHaveAttribute('open','');
 // Organization controls are outside Filters. The entire fixture is old, so
 // finite ranges keep only the selected pin, independent of current page.
 const grouping=area.getByRole('combobox',{name:'Group conversations'}),age=page.locator('#workspace-navigation').getByRole('slider',{name:'Filter conversations by age'});
 await expect(page.getByRole('slider',{name:'Filter conversations by age'})).toHaveCount(1);
 await expect(area.getByRole('slider')).toHaveCount(0);
 await expect(grouping).toBeVisible();await expect(age).toBeVisible();await expect(age).toHaveValue('5');
 const sliderBox=await age.boundingBox();
 await page.mouse.click(sliderBox.x+8,sliderBox.y+sliderBox.height/2);
 await expect(age).toHaveValue('0');await expect(results).toHaveCount(1);
 await age.focus();await age.press('End');await expect(area.getByRole('status')).toHaveText('206 chats');
 await area.getByRole('button',{name:'Show more conversations'}).click();
 await expect(area.locator('.a-nav-pagination')).toContainText('41–80');
 await grouping.selectOption('workspace');await expect(area.locator('.a-nav-pagination')).toContainText('1–40');
 await age.focus();await age.press('Home');await expect(age).toHaveValue('0');await expect(age).toHaveAttribute('aria-valuetext','Within the last 24 hours');
 await expect(area.getByRole('status')).toHaveText('1 chat');await expect(results).toHaveCount(1);await expect(area.getByRole('heading',{name:'Pinned',exact:true})).toBeVisible();
 // The sidebar choice governs arrival cards, Status, shortcuts and workspace
 // aggregates, not just a filter on this previously loaded second page.
 await action('view.update',{patch:{workSurface:'home',workHomeView:'brief',workHomePages:{idle:9}}});
 await expect(page.locator('.a-home-recent .a-work-card')).toHaveCount(1);
 await expect(page.locator('[data-sidebar-section=recent] .a-nav-chat')).toHaveCount(1);
 await page.getByRole('button',{name:'Status',exact:true}).click();
 await expect(page.locator('[data-status-group=idle] .a-nav-chat')).toHaveCount(1);
 await expect(age).toHaveValue('0');
 await page.getByRole('button',{name:'Browse',exact:true}).click();
 await page.screenshot({path:'/tmp/unified-age-home.png'});
 await action('session.pin',{id:selected,pinned:false});
 await expect(page.locator('.a-home-recent .a-work-card')).toHaveCount(0);
 await expect(page.locator('[data-sidebar-section=recent] .a-nav-chat')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep this unfinished draft.');
 await action('session.pin',{id:selected,pinned:true});
 await action('view.update',{patch:{workSurface:'chats'}});
 await page.reload();await expect(age).toHaveValue('0');await expect(grouping).toHaveValue('workspace');await expect(results).toHaveCount(1);
 const other=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 await other.goto(`http://127.0.0.1:${port}`);await showFixtureConversation(other,{navPinned:true});
 await other.getByRole('button',{name:'Search chats',exact:true}).click();
 await expect(other.getByRole('slider',{name:'Filter conversations by age'})).toHaveValue('5');
 await expect(other.getByRole('combobox',{name:'Group conversations'})).toHaveValue('recent');await other.close();
 await grouping.selectOption('status');await age.focus();await age.press('End');await expect(age).toHaveValue('5');
 await age.press('ArrowLeft');await expect(age).toHaveValue('4');await expect(age).toHaveAttribute('aria-valuetext','Within the last year');
 await age.press('ArrowRight');await expect(age).toHaveValue('5');
 await expect(area.getByRole('status')).toHaveText('206 chats');await expect(area.getByRole('heading',{name:'Pick up again',exact:true})).toBeVisible();
 await expect(page.locator('[data-sidebar-section=recent] .a-nav-chat')).toHaveCount(20);
 await expect(page.locator('[data-sidebar-section=recent] .a-recent-shortcuts')).toHaveAttribute('aria-busy','false');
 await grouping.selectOption('recent');
 await search.fill('Image');
 assert.equal(await search.evaluate(el=>getComputedStyle(el).outlineStyle),'none');
 assert.equal(await search.evaluate(el=>getComputedStyle(el.parentElement).outlineStyle),'solid');
 await expect(area.getByRole('button',{name:'Clear search',exact:true})).toHaveCount(1);
 await area.getByRole('button',{name:'Clear search',exact:true}).click();await expect(search).toHaveValue('');await expect(search).toBeFocused();
 await search.fill('Alpha 201');await expect(results).toHaveCount(1);await expect(results).toHaveText(/Alpha 201/);
 await search.press('ArrowDown');await expect(results.first()).toBeFocused();await results.first().press('ArrowUp');await expect(search).toBeFocused();
 await search.fill('Beta 00[12]');await expect(results).toHaveCount(2);
 await search.fill('does not exist');await expect(area.getByRole('heading',{name:'No chats found'})).toBeVisible();
 await area.getByRole('button',{name:'Clear search and filters'}).click();await expect(area.getByRole('status')).toHaveText('206 chats');
 await area.getByRole('button',{name:'Show more conversations'}).click();await expect(area.locator('.a-nav-pagination')).toContainText('41–80');
 await search.fill('Beta');await expect(results).toHaveCount(3);await expect(area.locator('.a-nav-pagination')).toHaveCount(0);
 await area.locator('summary').click();await area.getByLabel('Active or archived conversations').selectOption('archived');await expect(results).toHaveCount(0);
 await area.getByRole('button',{name:'Clear filters',exact:true}).click();await expect(results).toHaveCount(3);
 await area.getByLabel('Sort conversations').selectOption('name');await expect(results.first()).toHaveText(/Beta 000/);
 await area.locator('summary').click();await page.screenshot({path:'/tmp/unified-search-desktop.png'});
 await page.emulateMedia({colorScheme:'dark'});await page.screenshot({path:'/tmp/unified-search-dark.png'});
 await page.setViewportSize({width:390,height:844});await expect(search).toBeVisible();
 await expect(grouping).toBeVisible();
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();await expect(age).toBeVisible();
 await expect(page.getByRole('button',{name:/^Back(?: to chat)?$/})).toHaveCount(0);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // Wait for the responsive navigation state to follow the resized viewport.
 await expect.poll(()=>area.evaluate(el=>el.scrollWidth>el.clientWidth+1)).toBe(false);
 await page.emulateMedia({forcedColors:'active'});await expect(grouping).toBeVisible();await expect(age).toBeVisible();
 await age.focus();await age.press('Home');await expect(age).toHaveValue('0');await expect(results).toHaveCount(0);await expect(area.getByRole('heading',{name:'No chats found'})).toBeVisible();
 await page.screenshot({path:'/tmp/unified-age-mobile-forced-colors.png'});
 await age.press('End');await expect(age).toHaveValue('5');await expect(area.getByRole('status')).toHaveText('3 chats found');
 await page.emulateMedia({forcedColors:'none'});
 await page.getByRole('button',{name:'Close navigation',exact:true}).click();
 await area.locator('summary').click();await page.screenshot({path:'/tmp/unified-search-mobile.png'});
 await page.setViewportSize({width:1280,height:900});
 if(!await age.isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 // Workspace Chats uses Home's cards, not the All Chats catalog controls.
 // Its query remains independent, but the sidebar age is shared.
 await grouping.selectOption('status');await age.focus();await age.press('Home');
 await expect(results).toHaveCount(0);
 const workspaceId=await page.evaluate(()=>window.amplifier.getState().selectedWorkspaceId);
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspaceId,workWorkspaceTab:'chats'}});
 const workspaceArea=page.locator('[data-landing="workspace"]'),workspaceCards=workspaceArea.locator('.a-work-card[data-session-id]');
 await expect(workspaceArea.getByRole('searchbox',{name:'Search your work',exact:true})).toHaveValue('');
 await expect.poll(()=>page.evaluate(id=>{const snapshot=window.amplifier?.getShellState?.()?.snapshots?.chats;
  return snapshot?.navigationResources?.includes('workHome')===true&&snapshot.workHome?.scope===id&&snapshot.workHome?.query===''&&!snapshot.navigationResourcePending?.workHome},workspaceId),{timeout:15000}).toBe(true);
 await expect(workspaceCards).toHaveCount(1);
 await expect(age).toHaveValue('0');
 await expect(workspaceArea.getByRole('slider',{name:'Filter conversations by age'})).toHaveCount(0);
 await expect(workspaceArea.getByRole('combobox',{name:'Group conversations'})).toHaveCount(0);
 assert.ok((await workspaceCards.evaluateAll(cards=>cards.map(card=>card.dataset.sessionId))).every(id=>!id.startsWith('beta-')));
 await age.focus();await age.press('End');
 await expect.poll(()=>workspaceCards.count()).toBeGreaterThan(1);
 await age.press('Home');await expect(workspaceCards).toHaveCount(1);
 assert.equal(await workspaceArea.locator('.a-home-toolbar').count(),1);
 await action('view.update',{patch:{workSurface:'chats'}});
 await expect(grouping).toHaveValue('status');await expect(age).toHaveValue('0');await expect(search).toHaveValue('Beta');await expect(results).toHaveCount(0);
 await age.focus();await age.press('End');await grouping.selectOption('recent');
 await expect(area.getByRole('status')).toHaveText('3 chats found');
 await search.focus();await search.press('Escape');await expect(search).toHaveValue('');
 await search.press('Escape');await expect(search).toBeFocused();await expect(area).toBeVisible();await expect(draft).toBeHidden();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 // A newer browse intent must survive an older, still pending same-chat
 // selection. Optimistic composer visibility is not its server receipt.
 let releaseSelection,selectionHeld=false;
 const selectionGate=new Promise(resolve=>releaseSelection=resolve);
 const holdSelection=async route=>{
  const body=route.request().postDataJSON(),command=body.action==='shell.command'?body.args:body;
  if(!selectionHeld&&command.action==='session.select'&&command.args?.id===selected){selectionHeld=true;await selectionGate}
  await route.continue();
 };
 await page.route('**/api/actions',holdSelection);
 try{
  await page.locator('[data-sidebar-section=pinned] [data-session-id="'+selected+'"] .a-nav-chat-select').click();
  await expect.poll(()=>selectionHeld).toBe(true);
  await expect(draft).toHaveDraft('Keep this unfinished draft.');await expect(draft).toBeVisible();
  await page.getByRole('button',{name:'Search chats',exact:true}).click();await expect(area).toBeVisible();await search.fill('Beta 002');
  // This unrelated filter receipt drains the command queue without releasing
  // selection. Older code accepted browse on that queue before this barrier.
  await action('view.update',{patch:{navFilter:'Beta 002'}});
  releaseSelection();
  await expect.poll(()=>page.evaluate(()=>Boolean(window.amplifier.getState().navigationPending))).toBe(false);
  await expect(results).toHaveCount(1);
 }finally{releaseSelection();await page.unroute('**/api/actions',holdSelection)}
 await search.press('ArrowDown');await results.first().press('Enter');await expect(area).toHaveCount(0);
 assert.notEqual(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 const info=await (await page.request.get(`http://127.0.0.1:${port}/api/fixture/info`)).json();assert.deepEqual(info.runtimeSends,[]);
 assert.deepEqual(errors,[]);
 console.log('Search passed: focused top field, pinned matches, wildcard search, paging reset, archive and sort, no implicit Back/Escape return, delayed same-chat selection followed by newer search, retained draft, compact light/dark and narrow layouts; no model sends.');
}catch(error){console.error('Overflow diagnostics',await page?.evaluate(()=>[...document.querySelectorAll('.a-chat-search-page *')].filter(el=>el.scrollWidth>el.clientWidth+1).map(el=>({tag:el.tagName,className:el.className,width:el.clientWidth,scroll:el.scrollWidth,box:el.getBoundingClientRect().toJSON(),css:{minWidth:getComputedStyle(el).minWidth,whiteSpace:getComputedStyle(el).whiteSpace}}))).catch(()=>[]));await page?.screenshot({path:'/tmp/unified-search-failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}}
