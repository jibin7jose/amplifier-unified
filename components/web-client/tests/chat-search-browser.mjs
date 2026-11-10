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
 await expect(page.getByRole('button',{name:/^Back(?: to chat)?$/})).toHaveCount(0);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // Wait for the responsive navigation state to follow the resized viewport.
 await expect.poll(()=>area.evaluate(el=>el.scrollWidth>el.clientWidth+1)).toBe(false);
 await area.locator('summary').click();await page.screenshot({path:'/tmp/unified-search-mobile.png'});
 await page.setViewportSize({width:1280,height:900});
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
}catch(error){await page?.screenshot({path:'/tmp/unified-search-failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}}
