import {fixturePython} from './fixture-python.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdirSync,readFileSync} from 'node:fs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const built=JSON.parse(readFileSync(root+'/amplifier_web/static/build.json'));
const fixture=spawn(fixturePython(root+'/.venv/bin/python'),[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error('startup')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timeout);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:940},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 let buildReads=0;await page.route('**/build.json',route=>{buildReads++;return route.abort('failed')});
 // Synthetic installed identity on real host state/delta transport. The backend
 // suite separately verifies capture immutability and a real host restart.
 await page.addInitScript(({built})=>{
  window.fixtureInstalled=built;
  const Native=window.WebSocket;
  window.WebSocket=class extends Native{
   set onmessage(handler){super.onmessage=event=>{
    const frame=JSON.parse(event.data),build={serverVersion:window.fixtureInstalled.version,frontend:window.fixtureInstalled};
    if(frame.event==='state')frame.data.client.build=build;
    if(frame.event==='state-delta')frame.data.changes.push({path:['client','build'],value:build});
    handler(new MessageEvent('message',{data:JSON.stringify(frame)}));
   }}
  };
 },{built});
 const installed=async value=>{
  await page.evaluate(value=>{window.fixtureInstalled=value},value);
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{navExpanded:!window.amplifier.getState().view.navExpanded}}));
 };
 // Keep the composer debounce pending until the reload action explicitly flushes it.
 await page.addInitScript(()=>{const native=window.setTimeout.bind(window);window.setTimeout=(fn,delay,...args)=>native(fn,delay===220?60000:delay,...args)});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await page.evaluate(()=>window.amplifier.dispatch('session.create'));
 await page.evaluate(()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await expect(page.locator('[data-part=app-reload]')).toHaveCount(0);
 await installed({...built,id:'bbbbbbbbbbbbbbbb'});
 await expect(page.locator('[data-part=app-reload]')).toBeVisible();
 mkdirSync('/tmp/settings-refinements',{recursive:true});await page.screenshot({path:'/tmp/settings-refinements/reload-desktop.png'});
 await openSettingsPage(page,'updates');await expect(page.locator('.a-updates [data-part=app-reload]')).toBeVisible();
 await page.screenshot({path:'/tmp/settings-refinements/reload-updates.png'});
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null}}));
 await composer.fill('Keep this unsent draft across the update.');
 let rejectSave=true;await page.route('**/api/actions',async route=>{const payload=route.request().postDataJSON();if(rejectSave&&payload?.action==='view.update'&&Object.hasOwn(payload.args.patch||{},'draft'))return route.fulfill({status:503,json:{error:'Draft save is temporarily unavailable.'}});return route.continue()});
 await page.getByRole('button',{name:'Reload now',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Draft save is temporarily unavailable.');await expect(composer).toHaveDraft('Keep this unsent draft across the update.');
 rejectSave=false;
 await Promise.all([page.waitForEvent('load'),page.getByRole('button',{name:'Reload now',exact:true}).click()]);
 await expect(composer).toHaveDraft('Keep this unsent draft across the update.');await expect(page.locator('[data-part=app-reload]')).toHaveCount(0);
 await installed({...built,id:'cccccccccccccccc'});await page.setViewportSize({width:390,height:844});await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await expect(page.locator('[data-part=app-reload]')).toBeVisible();assert.ok(await page.locator('#amp-one').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:'/tmp/settings-refinements/reload-mobile.png'});
 assert.equal(buildReads,0,'healthy updates are discovered through the existing stream, never build polling');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({buildReads,streamedIdentity:true,sameBuildQuiet:true,changedBuildNotice:true,updatesPageNotice:true,failedSavePreventsReload:true,unsentDraftSurvives:true,narrowLayout:true,browserErrors:errors}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
