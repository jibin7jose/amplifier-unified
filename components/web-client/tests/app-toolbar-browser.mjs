import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
// Real packaged UI and actions, isolated storage, no model or external feedback.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,touchBrowser,page,primaryError;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-toolbar';
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Toolbar fixture startup timed out')),15000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Toolbar fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(5000);
 const errors=[],calls=[];page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const more=page.getByRole('button',{name:'App options',exact:true}),menu=page.getByRole('group',{name:'App options',exact:true});
 const footer=async(target=page)=>{if(!await target.getByRole('button',{name:'App options',exact:true}).isVisible())await target.getByRole('button',{name:'Open navigation',exact:true}).click();await expect(target.getByRole('button',{name:'App options',exact:true})).toBeVisible()};
 const closeNavigation=async()=>{const close=page.getByRole('button',{name:'Close navigation',exact:true});if(await close.isVisible()){await close.click();await expect(close).toBeHidden()}};
 const toggle=page.locator('.a-work-header .a-canvas-toggle'),canvas=page.getByRole('complementary',{name:'Canvas',exact:true});
 await expect(page.getByRole('button',{name:'Open workspace canvas'})).toHaveCount(0);
 // The current WorkHeader omits chat-only controls before creation; the retired
 // app toolbar kept a disabled Canvas button. Both prevent an ownerless Canvas.
 await expect(toggle).toHaveCount(0);await action('session.create');
 await expect(toggle).toHaveAccessibleName('Open canvas');await expect(toggle).toHaveText('');await expect(toggle).toHaveAttribute('aria-pressed','false');
 const initialBox=await toggle.boundingBox();await toggle.click();await expect(canvas).toBeVisible();
 // Empty Canvas now owns the selected chat overview, not the retired launcher.
 await expect(canvas).toHaveAttribute('data-mode','overview');await expect(canvas.getByRole('tab',{name:'Chat overview',exact:true})).toBeVisible();await expect(canvas.getByRole('searchbox')).toHaveCount(0);await expect(canvas.getByRole('button',{name:/Saved artifacts/})).toHaveCount(0);await page.screenshot({path:out+'/canvas-empty.png'});
 await expect(toggle).toHaveAccessibleName('Hide canvas');await expect(toggle).toHaveAttribute('aria-pressed','true');assert.deepEqual(await toggle.boundingBox(),initialBox);
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();await expect(page.locator('#workspace-canvas')).toBeHidden();await expect(toggle).toHaveAttribute('aria-pressed','false');assert.deepEqual(await toggle.boundingBox(),initialBox);
 await action('session.create');const session=(await state()).selectedSessionId;
 await action('view.update',{patch:{draft:'Keep this unsent message',navPinned:true}});
 await action('canvas.show',{kind:'markdown',title:'Workspace notes',content:'# Workspace notes\n\nA saved artifact beside the conversation.'});
 const artifact=(await state()).canvas.id;
 await expect(toggle).toHaveAttribute('aria-pressed','true');await toggle.click();await expect(page.locator('#workspace-canvas')).toBeHidden();await toggle.click();await expect(canvas).toBeVisible();
 assert.equal((await state()).canvas.id,artifact);assert.equal((await state()).selectedSessionId,session);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Keep this unsent message');
 await action('canvas.close');await expect(toggle).toHaveAttribute('aria-pressed','false');await action('canvas.reopen');await expect(toggle).toHaveAttribute('aria-pressed','true');
 await page.screenshot({path:out+'/canvas-open.png'});
 // App-level destinations live in the navigation footer; Canvas remains in
 // WorkHeader. Test the mounted owners rather than legacy toolbarMenuOpen state.
 await footer();await more.focus();await page.keyboard.press('Enter');await expect(menu).toBeVisible();await expect(menu.getByRole('button',{name:'Send feedback'})).toBeFocused();
 await page.keyboard.press('Tab');await expect(menu.getByRole('button',{name:'What the agent sees'})).toBeFocused();await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(more).toBeFocused();
 await more.click();await page.getByRole('textbox',{name:'Message Amplifier'}).click();await expect(menu).toHaveCount(0);await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeFocused();
 for(const [label,panel] of [['Send feedback','feedback'],['What the agent sees','agent']]){
  await more.click();await menu.getByRole('button',{name:label,exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await expect(menu).toHaveCount(0);
  await expect.poll(async()=>(await state()).view.panel).toBe(panel);await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(more).toBeFocused();
 }
 await openSettingsPage(page,'appearance');await expect(page.locator('.a-settings-experience[data-settings-page="appearance"]')).toBeVisible();
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/})).toBeFocused();
 await more.click();await expect(menu.getByRole('button',{name:/Activity|Customize appearance/})).toHaveCount(0);await page.keyboard.press('Escape');
 await action('view.update',{patch:{layout:'work'}});await expect(toggle.locator('.lucide-panel-left')).toHaveCount(1);await action('view.update',{patch:{layout:'balanced'}});await expect(toggle.locator('.lucide-panel-right')).toHaveCount(1);
 for(const scheme of ['light','dark']){
  await action('view.update',{patch:{scheme}});
  for(const width of [1280,800,600,390,320]){
   await page.setViewportSize({width,height:900});await closeNavigation();await expect(toggle).toBeVisible();
   const before=await toggle.boundingBox();const headerActions=await page.locator('.a-work-header-actions > button,.a-work-header-actions > .a-work-menu > button').evaluateAll(nodes=>nodes.filter(node=>node.getClientRects().length).map(node=>({label:node.getAttribute('aria-label'),right:node.getBoundingClientRect().right})));
   assert.equal(headerActions.at(-1).label,'Hide canvas');assert.ok(headerActions.every(row=>row.right<=before.x+before.width+1),'Canvas remains the rightmost WorkHeader action');assert.ok(before.x>=0&&before.x+before.width<=width);
   await footer();await more.click();await expect(menu).toBeVisible();const rect=await menu.boundingBox();assert.ok(rect.x>=0&&rect.x+rect.width<=width&&rect.y>=0&&rect.y+rect.height<=900,'Footer menu fits viewport '+JSON.stringify({scheme,width,rect}));
   await page.screenshot({path:out+`/toolbar-${scheme}-${width}.png`});await page.keyboard.press('Escape');await closeNavigation();
   await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','false');assert.deepEqual(await toggle.boundingBox(),before);
   await toggle.click();await expect(toggle).toHaveAttribute('aria-pressed','true');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
 }
 touchBrowser=await chromium.launch({headless:true});
 const touch=await touchBrowser.newPage({viewport:{width:320,height:844},hasTouch:true,isMobile:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});await touch.goto(url);await showFixtureConversation(touch);await touch.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await touch.evaluate(()=>window.amplifier.dispatch('session.create'));await touch.locator('.a-work-header .a-canvas-toggle').waitFor();
 const touchTargets=await touch.locator('.a-work-header-actions > button,.a-work-header-actions > .a-work-menu > button').evaluateAll(buttons=>buttons.filter(button=>button.getClientRects().length).map(button=>{const r=button.getBoundingClientRect();return {width:r.width,height:r.height}}));
 assert.ok(touchTargets.length>0);assert.ok(touchTargets.every(r=>r.width>=44&&r.height>=44));assert.ok(await touch.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await footer(touch);const touchMore=touch.getByRole('button',{name:'App options',exact:true});const touchBox=await touchMore.boundingBox();assert.ok(touchBox.width>=44&&touchBox.height>=44);await touchMore.tap();await expect(touch.getByRole('group',{name:'App options',exact:true})).toBeVisible();
 assert.equal(calls.some(call=>['conversation.send','runtime.start','feedback.submit'].includes(call.action)),false);const inspected=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(inspected.sent,[]);assert.deepEqual(inspected.started,[]);console.log('Fixture cache observation',JSON.stringify({foundationCachePresent:inspected.foundationCachePresent}));assert.deepEqual(errors,[]);
 console.log('Toolbar passed: stable toggle geometry, saved artifact/draft preservation, current header/footer actions and destinations, keyboard/focus/outside dismissal, both canvas sides, light/dark 320–1280px, touch targets; no model or feedback submissions.');
}catch(error){primaryError=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}finally{
 const failures=[];for(const owned of [touchBrowser,browser])try{await owned?.close()}catch(error){failures.push(error)}
 try{await new Promise((resolve,reject)=>{if(fixture.exitCode!==null||fixture.signalCode!==null)return fixture.exitCode===0?resolve():reject(Error('App controls fixture exited unsuccessfully'));const timer=setTimeout(()=>{fixture.kill('SIGKILL');reject(Error('App controls fixture failed graceful cleanup'))},10000);fixture.once('exit',(code,signal)=>{clearTimeout(timer);if(code===0||(process.platform==='win32'&&signal==='SIGTERM'))resolve();else reject(Error('App controls fixture cleanup failed'))});fixture.kill('SIGTERM')})}catch(error){failures.push(error)}
 if(failures.length){if(primaryError)console.error('Additional cleanup failure',failures);else throw failures[0]}
}
