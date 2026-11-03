import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {readComposerDraft} from './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
let base=process.env.AMPLIFIER_TEST_URL,fixture;
if(!base){
 fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/mobile_navigation_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
 base=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>{fixture.kill();reject(Error('Fixture startup timed out'))},15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;const url=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];if(url){clearTimeout(timer);resolve(url)}})});
}
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-mobile-navigation';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
const patch=patch=>action('view.update',{patch});
const results=[];
const readingPosition=()=>page.locator('.a-messages').evaluate(pane=>{
 const rect=pane.getBoundingClientRect(),message=[...pane.querySelectorAll('[data-message-id]')].find(row=>row.getBoundingClientRect().bottom>rect.top);
 if(!message)throw Error('No visible conversation message');
 const offset=message.getBoundingClientRect().top-rect.top;
 return {id:message.dataset.messageId,offset,scrollTop:pane.scrollTop,contentTop:offset+pane.scrollTop,width:rect.width,height:rect.height};
});
const workReady=()=>page.waitForFunction(()=>{
 const state=window.amplifier.getState(),session=state.sessions.find(row=>row.id===state.selectedSessionId);
 return session?.workProjection?.status==='ready'&&!document.querySelector('[data-part="work-projection-status"]');
});
try{
 await page.goto(base);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await action('theme.reset');
 await action('session.create',{title:'Improve mobile navigation and Canvas'});
 await action('conversation.send',{text:'How can we make navigation work better on a phone?'});
 await patch({navPinned:true,navExpanded:false});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Keep this unsent draft');
 const drawer=page.getByRole('dialog',{name:'Workspaces and chats',exact:true});
 for(const width of [320,360,390,412,600,760,1024,1440]){
  await page.setViewportSize({width,height:844});await page.waitForTimeout(150);
  const metrics=await page.evaluate(()=>{
   const box=s=>{const el=document.querySelector(s),b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,display:getComputedStyle(el).display}};
   const title=document.querySelector('.a-work-heading strong'),style=getComputedStyle(title),measure=document.createElement('canvas').getContext('2d');measure.font=style.font;
   return {viewport:innerWidth,body:document.documentElement.scrollWidth,header:box('.a-work-header'),home:box('.a-work-home-nav'),title:{...box('.a-work-heading strong'),text:title.textContent,full:title.title,readableWidth:measure.measureText(title.textContent.split(/\s+/)[0]+'…').width},nav:box('.a-nav-slot'),chat:box('.a-conversation'),font:getComputedStyle(document.querySelector('#amp-one')).fontSize,actions:[...document.querySelectorAll('.a-work-header button[aria-label]')].filter(b=>b.getClientRects().length).map(b=>({label:b.getAttribute('aria-label'),x:b.getBoundingClientRect().x,y:b.getBoundingClientRect().y,width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height}))};
  });
  results.push(metrics);assert.equal(metrics.body,width);assert.equal(metrics.font,'14px');
  if(width<=760){
   assert.equal(metrics.chat.width,width);assert.equal(metrics.nav.display,'none');
   assert.equal(metrics.title.text,'Improve mobile navigation and Canvas');assert.equal(metrics.title.full,metrics.title.text);
   // Phone headers wrap. Keep the title readable and every control contained
   // without assuming the title and actions occupy the same row.
   assert.ok(metrics.title.width>=metrics.title.readableWidth);assert.ok(metrics.title.height>0);
   assert.ok(metrics.title.y>=metrics.home.y+metrics.home.height,'Site Home remains above the breadcrumb title');
   const separate=(a,b)=>a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y;
   const contained=row=>row.x>=metrics.header.x&&row.y>=metrics.header.y&&row.x+row.width<=metrics.header.x+metrics.header.width&&row.y+row.height<=metrics.header.y+metrics.header.height;
   assert.ok(contained(metrics.title)&&contained(metrics.home));
   assert.ok(metrics.actions.every(row=>separate(metrics.title,row)&&(row.label==='Home'||separate(metrics.home,row))));
   assert.ok(metrics.actions.every((row,i)=>metrics.actions.slice(i+1).every(other=>separate(row,other))));
   assert.notEqual(metrics.home.display,'none');assert.ok(metrics.home.width>=44&&metrics.home.height>=44);
   assert.deepEqual(metrics.actions.map(row=>row.label),['Open navigation','Home','Back in navigation history','Forward in navigation history','Recent places','Browser & computer','Chat','Open canvas']);
   assert.ok(metrics.actions.every(row=>contained(row)&&row.width>=44&&row.height>=44&&row.x>=0&&row.x+row.width<=width));
  }
  await page.screenshot({path:`${out}/chat-${width}.png`});
  if(width<=760){
   await page.getByRole('button',{name:'Open navigation',exact:true}).tap();await drawer.waitFor();
   const navigation=await drawer.evaluate(el=>{
    const rect=el.getBoundingClientRect(),rail=el.querySelector('.a-nav-rail').getBoundingClientRect();
    return {x:rect.x,y:rect.y,width:rect.width,height:rect.height,railWidth:rail.width,headerBottom:document.querySelector('.a-work-header').getBoundingClientRect().bottom,viewportWidth:innerWidth,viewportHeight:innerHeight,bodyWidth:document.documentElement.scrollWidth};
   });
   results.push({navigation});
   // The persistent header owns the toggle; the drawer fills the space below it.
   assert.ok(navigation.headerBottom>0&&navigation.headerBottom<navigation.viewportHeight);
   assert.equal(navigation.x,0);assert.equal(navigation.y,navigation.headerBottom);
   assert.equal(navigation.width,navigation.viewportWidth,'Phone navigation must fill the viewport width');
   assert.equal(navigation.height,navigation.viewportHeight-navigation.headerBottom);
   assert.equal(navigation.railWidth,navigation.viewportWidth);assert.equal(navigation.bodyWidth,width);
   await page.screenshot({path:`${out}/drawer-${width}.png`});
   await page.getByRole('button',{name:'Close navigation',exact:true}).tap();
   assert.equal(await drawer.isVisible(),false);
   assert.equal(await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'})),'Keep this unsent draft');
  }
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await drawer.waitFor();assert.equal((await drawer.boundingBox()).width,390);
 assert.ok(await page.locator('.a-conversation').evaluate(el=>el.inert));
 await page.screenshot({path:`${out}/drawer.png`});assert.ok(await drawer.locator('input').evaluateAll(nodes=>nodes.filter(node=>node.getClientRects().length).every(node=>parseFloat(getComputedStyle(node).fontSize)>=16)));
 const toggle=page.locator('#navigation-toggle');
 await expect(drawer.getByRole('button',{name:'Home',exact:true})).toBeFocused();
 await page.keyboard.press('Shift+Tab');await expect(toggle).toBeFocused();
 await page.keyboard.press('Shift+Tab');assert.ok(await drawer.evaluate(el=>el.contains(document.activeElement)));
 await page.keyboard.press('Tab');await expect(toggle).toBeFocused();
 await page.keyboard.press('Tab');await expect(drawer.getByRole('button',{name:'Home',exact:true})).toBeFocused();
 const details=drawer.locator('.a-navigation-more').first();await details.focus();await details.click();await page.locator('.a-navigation-flyout').waitFor();
 await page.screenshot({path:`${out}/details.png`});
 await page.keyboard.press('Escape');assert.equal(await drawer.isVisible(),true);
 await page.keyboard.press('Escape');await page.waitForFunction(()=>document.querySelector('.a-nav-slot').hidden);
 assert.equal(await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'})),'Keep this unsent draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.navPinned),true);
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.getByRole('button',{name:'Close navigation',exact:true}).tap();
 assert.equal(await drawer.isVisible(),false);
 await page.getByRole('button',{name:'Chat',exact:true}).click();
 await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Chat info',exact:true}).waitFor();
 await page.screenshot({path:`${out}/more.png`});
 assert.ok(await page.getByRole('group',{name:'Chat',exact:true}).evaluate(el=>el.contains(document.activeElement)));
 await page.keyboard.press('Escape');
 for(const scheme of ['dark','light']){
  await patch({scheme});await page.setViewportSize({width:320,height:740});
  await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  assert.equal(Math.round((await drawer.boundingBox()).width),320);assert.equal(Math.round((await drawer.locator('.a-nav-rail').boundingBox()).width),320);
  await page.screenshot({path:`${out}/drawer-${scheme}-320.png`});
  await page.keyboard.press('Escape');
 }
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
 // A loading/retry notice above the messages legitimately changes scrollTop
 // when it settles. This first case deliberately fixes that geometry.
 await workReady();
 await page.locator('.a-messages').evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-500}));el.scrollTop=150});
 await page.waitForTimeout(100);
 const before=await readingPosition(),scrollBefore=before.scrollTop;assert.equal(scrollBefore,150);
 await action('canvas.show',{kind:'html',title:'Persistent viewer',content:'<!doctype html><html><body><button onclick="this.textContent=String(Number(this.textContent)+1)">0</button><input aria-label="Retained canvas input"></body></html>'});
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('button',{name:'0',exact:true}).click();await frame.getByRole('textbox').fill('Keep my viewer state');
 const iframe=await page.locator('.a-canvas-html').elementHandle();
 await page.route('**/api/actions',async route=>{if(route.request().postDataJSON()?.action==='canvas.visibility')await new Promise(r=>setTimeout(r,1200));await route.continue()});
 for(const name of ['Hide canvas','Open canvas','Hide canvas','Open canvas']){
  await page.getByRole('button',{name,exact:true}).click();
  const expected=name==='Open canvas';
  assert.equal(await page.locator('#workspace-canvas').isVisible(),expected);
  assert.equal(await page.locator('.a-canvas-toggle').getAttribute('aria-busy'),null);
  assert.equal(await page.locator('.a-canvas-toggle').getAttribute('data-action-pending'),null);
 }
 await page.waitForTimeout(5500);
 assert.equal(await page.locator('#workspace-canvas').isVisible(),true);
 assert.equal(await iframe.evaluate(el=>el===document.querySelector('.a-canvas-html')),true);
 assert.equal(await frame.getByRole('button',{name:'1',exact:true}).count(),1);
 assert.equal(await frame.getByRole('textbox').inputValue(),'Keep my viewer state');
 await page.screenshot({path:`${out}/canvas.png`});
 assert.equal(await page.evaluate(()=>window.amplifier.getState().renderedView.panes.conversation.interactive),false);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().renderedView.controls.some(control=>control.label==='Message Amplifier')),false);
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
 assert.equal(await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'})),'Keep this unsent draft');
 const after=await readingPosition();
 assert.deepEqual(after,before,'Stable geometry retains both raw scrollTop and the visible message anchor');
 results.push({reading:'stable',before,after});
 await page.waitForTimeout(1500);
 await page.reload();await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();assert.equal(await page.locator('#workspace-canvas').isVisible(),false);
 await page.unroute('**/api/actions');
 let releaseCommand,held=false;
 const gate=new Promise(resolve=>{releaseCommand=resolve});
 const hold=async route=>{const body=route.request().postDataJSON();if(body?.action==='view.update'&&body.args?.patch?.workerDraft==='Held fixture preference'){held=true;await gate}await route.continue()};
 await page.route('**/api/actions',hold);
 await page.evaluate(()=>{window.heldPreference=window.amplifier.dispatch('view.update',{patch:{workerDraft:'Held fixture preference'}})});
 for(let i=0;i<30&&!held;i++)await page.waitForTimeout(20);assert.equal(held,true);
 const acknowledged=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='canvas.visibility',{timeout:1500});
 await page.getByRole('button',{name:'Open canvas',exact:true}).click();await acknowledged;
 assert.equal(await page.locator('#workspace-canvas').isVisible(),true);
 releaseCommand();await page.evaluate(()=>window.heldPreference);await page.unroute('**/api/actions',hold);
 let rejected=false;
 const reject=async route=>{if(!rejected&&route.request().postDataJSON()?.action==='canvas.visibility'){rejected=true;await new Promise(resolve=>setTimeout(resolve,300));await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture visibility save failed'})})}else await route.continue()};
 await page.route('**/api/actions',reject);
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();assert.equal(await page.locator('#workspace-canvas').isVisible(),false);
 await page.getByText('Fixture visibility save failed',{exact:true}).waitFor();assert.equal(await page.locator('#workspace-canvas').isVisible(),true);
 await page.getByRole('button',{name:'Dismiss error',exact:true}).click();await page.unroute('**/api/actions',reject);
 if(fixture){
  // Reproduce a delayed Work retry finishing while Canvas covers the chat.
  // The same message must stay at the same position even though the notice
  // disappears and raw scrollTop must decrease by its height plus the gap.
  await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
  await workReady();
  const sessionId=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
  const work=async operation=>{const response=await page.request.post(base+'/api/fixture/work-reader',{data:{operation,sessionId}});assert.equal(response.ok(),true)};
  await work('retry');
  const notice=page.locator('[data-part="work-projection-status"]');
  await expect(notice).toHaveText('Work details could not finish loading. Retrying shortly. You can keep reading this chat.');
  const removedHeight=await notice.evaluate(node=>node.getBoundingClientRect().height+parseFloat(getComputedStyle(node.parentElement).rowGap));
  await page.locator('.a-messages').evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-500}));el.scrollTop=150});
  await expect.poll(async()=>(await readingPosition()).scrollTop).toBe(150);
  const retryBefore=await readingPosition();
  await page.getByRole('button',{name:'Open canvas',exact:true}).click();
  await work('release');await workReady();
  await page.getByRole('button',{name:'Hide canvas',exact:true}).click();
  await expect.poll(async()=>Math.abs((await readingPosition()).offset-retryBefore.offset)).toBeLessThan(1);
  const retryAfter=await readingPosition();
  assert.equal(retryAfter.id,retryBefore.id);
  assert.equal(retryAfter.width,retryBefore.width);assert.equal(retryAfter.height,retryBefore.height);
  assert.ok(Math.abs(retryAfter.contentTop-retryBefore.contentTop+removedHeight)<1);
  assert.ok(Math.abs(retryAfter.scrollTop-retryBefore.scrollTop+removedHeight)<1);
  assert.equal(await readComposerDraft(page.getByRole('textbox',{name:'Message Amplifier'})),'Keep this unsent draft');
  results.push({reading:'retry-to-ready',removedHeight,before:retryBefore,after:retryAfter});
 }
 assert.deepEqual(errors,[]);await writeFile(`${out}/results.json`,JSON.stringify({results,errors},null,2));console.log('Responsive layouts, drawer focus, sheets, typography, delayed rapid toggles, retained viewer, draft and reload passed');
}finally{await writeFile(`${out}/results-partial.json`,JSON.stringify({results,errors},null,2));await browser.close();fixture?.kill()}
