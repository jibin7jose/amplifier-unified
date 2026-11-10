import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;if(!out)throw Error('Owned artifacts required');
await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON,['-u',root+'tests/fixtures/navigation_waiting_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,vite,logs='',failure;const observations=[],errors=[];
fixture.stderr.on('data',data=>logs+=data);
try{
 const ready=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),25000);
  fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+logs))});
  fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}});
 });
 if(process.env.AMPLIFIER_TEST_DEV){
  const target=ready.url;vite=await createServer({configFile:false,root:root+'frontend',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,r=>r.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});
  await vite.listen();
 }
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite?.resolvedUrls.local[0]||ready.url);await showFixtureConversation(page,{navPinned:true});
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await expect(composer).toBeEditable();await composer.fill('Keep this draft while inspecting waits');
 const waiting=page.getByRole('region',{name:'Waiting for other chats'});
 const mode=async value=>{const response=await page.request.post(ready.url+'/fixture/wait',{data:{mode:value}});assert.equal(response.status(),200)};
 const select=id=>page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),id);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await expect(waiting).toContainText('Waiting for Unified: Readiness');
 await expect(waiting.getByRole('button',{name:'Unified: Readiness',exact:true})).toBeVisible();
 const nav=page.locator('[data-sidebar-section=pinned]');
 await expect(nav.locator('.a-nav-chat[data-session-id="'+ready.ids[0]+'"] [data-kind=waiting]')).toHaveCount(1);
 assert.equal(await waiting.locator('.a-progress-spinner').count(),0);
 await page.screenshot({path:out+'/one-desktop.png'});
 const columns=await nav.evaluate(el=>{
  const measure=selector=>{const node=el.querySelector(selector),r=node.getBoundingClientRect();return {x:r.x,width:r.width,height:r.height}};
  return {chat:measure('.a-nav-chat .a-nav-chat-label'),workspace:measure('.a-workspace-label'),
          chatIcon:measure('.a-nav-chat .a-navigation-status'),workspaceIcon:measure('.a-workspace-row .a-navigation-status')};
 });
 observations.push({id:'shared-columns',columns});
 assert.ok(Math.abs(columns.chat.x-columns.workspace.x)<1,JSON.stringify(columns));
 assert.ok(Math.abs(columns.chatIcon.x-columns.workspaceIcon.x)<1,JSON.stringify(columns));
 assert.equal(await page.locator('.a-workspace-pin-mark').count(),0);
 const workspace=nav.locator('.a-workspace-row');
 await workspace.hover();
 await workspace.getByRole('button',{name:'Details and actions for Unified: Launch Readiness',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Details for Unified: Launch Readiness',exact:true}).getByRole('button',{name:'Unpin workspace '+ready.path,exact:true})).toBeVisible();
 await page.keyboard.press('Escape');
 await mode('many');await expect(waiting.locator('summary')).toContainText('Waiting for 2 chats');
 await waiting.locator('summary').focus();await page.keyboard.press('Enter');
 await expect(waiting.getByRole('button',{name:'Second worker with a long descriptive title',exact:true})).toBeVisible();
 assert.equal((await state()).selectedSessionId,ready.ids[0]);await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
 await page.screenshot({path:out+'/many-desktop.png'});
 await waiting.getByRole('button',{name:'Unified: Readiness',exact:true}).click();
 await expect.poll(async()=>(await state()).selectedSessionId).toBe(ready.ids[1]);
 await expect(waiting).toHaveCount(0);await select(ready.ids[0]);await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
 for(const value of ['paused','stopped','ready','question','new-input']){
  await mode(value);await expect(waiting).toHaveCount(0);
  observations.push({id:value,selectedActivity:(await state()).selectedActivity});
 }
 await mode('recipient-error');await expect(waiting).toContainText('error');
 await mode('many');await expect(waiting.locator('summary')).toBeVisible();
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navPinned:false}}));
  if(!await waiting.getByRole('button',{name:'Unified: Readiness',exact:true}).isVisible())await waiting.locator('summary').click();
  await expect(waiting.getByRole('button',{name:'Unified: Readiness',exact:true})).toBeVisible();
  const geometry=await waiting.evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,height:el.getBoundingClientRect().height}));
  assert.ok(geometry.scroll<=geometry.width+1,JSON.stringify(geometry));
  await expect(composer).toHaveDraft('Keep this draft while inspecting waits');
  observations.push({id:'mobile-'+width,geometry});
  await page.screenshot({path:out+'/many-mobile-'+width+'.png'});
 }
 assert.deepEqual(errors,[]);observations.push({id:'selection-draft-keyboard-and-status',passed:true});
}catch(error){failure=error;await page?.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{
 await browser?.close();await vite?.close();
 if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}finally{clearTimeout(timer)}}
 await writeFile(out+'/fixture.log',logs);
 await writeFile(out+'/observations.json',JSON.stringify({observations,errors,failure:failure?.message,mode:process.env.AMPLIFIER_TEST_DEV?'dev':'built'},null,2)+'\n');
}