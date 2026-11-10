import {finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage,openPhoneNotificationSettings,settingsToolObservations} from './browser-settings.mjs';
import {settingsSections} from '../src/settings-navigation.js';
import {startSettingsFixture} from './settings-fixture.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const {base,stop}=await startSettingsFixture();
let browser,page,failure;const errors=[];
try{
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/');await page.waitForSelector('#amp-one');
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const presentation=async(id,value)=>{
  if(id!=='scheme'&&!await page.locator('#'+id).isVisible())await page.getByText('Layout & background',{exact:true}).click();
  const control=id==='scheme'?page.getByRole('combobox',{name:'Color mode',exact:true}):page.locator('#'+id);
  // A prior edit may already be painted but still awaiting persistence. Do not
  // accidentally accept its response as the acknowledgment for this edit.
  await expect(control).toBeEnabled();
  const selected=await control.inputValue()===value;
  if(!selected){
   const prepared=page.waitForResponse(response=>{
    const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;
    return response.url().endsWith('/api/actions')&&body?.action==='shell.changes.prepare'&&body.args.composition.presentation[id]===value;
   });
   const applied=page.waitForResponse(async response=>{
    const request=response.request(),body=request.method()==='POST'?request.postDataJSON():null;
    if(!response.url().endsWith('/api/actions')||body?.action!=='shell.changes.apply')return false;
    const preparation=await prepared;
    return body.args.changeId===(await preparation.json()).result.id;
   });
   await control.selectOption(value);
   const preparation=await prepared;assert.ok(preparation.ok());
   const application=await applied;assert.ok(application.ok());
   assert.equal((await application.json()).accepted,true);
  }
  await expect(control).toBeEnabled();
  // The actual shell snapshot is separate from the optimistic controls.
  await expect.poll(()=>page.evaluate(key=>window.amplifier.getShellState()?.effectiveComposition.presentation[key],id)).toBe(value);
  await expect(control).toHaveValue(value);
 };
 await openSettingsPage(page,'notifications');
 assert.equal((await page.locator('.a-dialog').boundingBox()).width,1120);
 assert.equal(await page.locator('.a-dialog').evaluate(el=>getComputedStyle(el).padding),'0px');
 assert.equal(await page.locator('.a-dialog-head').evaluate(el=>getComputedStyle(el).paddingLeft),'24px');
 await openPhoneNotificationSettings(page);
 await page.getByLabel('Notification server',{exact:true}).fill('https://notify.example');
 await page.getByLabel(/^Topic/).fill('fixture-private-topic');
 await page.getByLabel(/^Access token/).fill('fixture-private-token');
 await page.getByRole('checkbox',{name:/^On another device/}).check();
 await page.getByLabel('Include a response preview',{exact:true}).check();
 await openSettingsPage(page,'appearance');await openSettingsPage(page,'notifications');
 assert.equal(await page.getByLabel(/^Topic/).inputValue(),'fixture-private-topic');
 assert.equal(await page.getByLabel(/^Access token/).inputValue(),'fixture-private-token');
 assert.ok(!JSON.stringify(await state()).includes('fixture-private-token'));
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.tokenConfigured);
 await page.waitForFunction(()=>[...document.querySelectorAll('input[type=password]')].every(input=>input.value===''));
 assert.equal(await page.getByLabel(/^Access token/).inputValue(),'');
 await openPhoneNotificationSettings(page);
 await page.getByLabel('Include a response preview',{exact:true}).uncheck();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.preview===false);
 await page.reload();await openPhoneNotificationSettings(page);
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.server==='https://notify.example');
 let notify=(await state()).notificationSettings;assert.ok(notify.tokenConfigured&&notify.topicConfigured&&notify.enabled&&!notify.preview);assert.equal(notify.topic,undefined);assert.equal(notify.token,undefined);
 // Server-side rejection is visible and does not replace the last good settings.
 await page.getByLabel('Notification server',{exact:true}).fill('http://invalid.example');
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();
 await page.getByText('Enter an HTTPS notification server URL',{exact:true}).first().waitFor();
 assert.equal((await state()).notificationSettings.server,'https://notify.example');
 await page.getByLabel('Notification server',{exact:true}).fill('https://notify.example');
 await expect(page.getByRole('button',{name:'Save phone connection',exact:true})).toHaveCount(0);
 // Returning to the saved value needs no write; a new valid edit exercises
 // recovery from the rejected URL through the actual save operation.
 await page.getByLabel('Notification server',{exact:true}).fill('https://notify-recovered.example');
 await page.getByRole('button',{name:'Save phone connection',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings?.server==='https://notify-recovered.example');
 await expect(page.getByText('Enter an HTTPS notification server URL',{exact:true})).toHaveCount(0);
 await openSettingsPage(page,'voice');
 await page.locator('#preferred-voice').selectOption('gpt-realtime-2.1');
 await page.waitForFunction(()=>window.amplifier.getState().settings.preferredVoice==='gpt-realtime-2.1');
 await page.getByText('If the preferred model is unavailable',{exact:true}).click();
 await page.getByRole('combobox',{name:'Fallback model',exact:true}).selectOption('gpt-live-1');
 await page.waitForFunction(()=>window.amplifier.getState().settings.fallbackVoice==='gpt-live-1');
 await page.reload();await page.locator('#preferred-voice').waitFor();await page.getByText('If the preferred model is unavailable',{exact:true}).click();
 assert.equal(await page.locator('#preferred-voice').inputValue(),'gpt-realtime-2.1');assert.equal(await page.getByRole('combobox',{name:'Fallback model',exact:true}).inputValue(),'gpt-live-1');
 await openSettingsPage(page,'providers');
 // Missing credentials open this disclosure initially; only expand a closed one.
 if(!await page.locator('.a-provider-access').evaluate(el=>el.open))await page.locator('.a-provider-access summary').click();
 await page.locator('#provider-key-source').selectOption('private');
 await page.locator('#provider-key').fill('fixture-unsaved-key');
 await openSettingsPage(page,'routing');await openSettingsPage(page,'providers');
 assert.equal(await page.locator('#provider-key').inputValue(),'fixture-unsaved-key');
 assert.ok(!JSON.stringify(await state()).includes('fixture-unsaved-key'));
 await openSettingsPage(page,'custom-appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 await page.locator('#theme-name').fill('Acceptance skin');await page.locator('#theme-css').fill('#amp-one { --a-accent: #6b4dcc; }');
 await page.getByRole('button',{name:'Preview',exact:true}).click();
 assert.notEqual((await state()).theme?.name,'Acceptance skin');
 await openSettingsPage(page,'updates');await openSettingsPage(page,'custom-appearance');
 await expect.poll(async()=>(await state()).view.themePreview).toBe(false);
 await page.getByRole('button',{name:'Apply appearance',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().theme.name==='Acceptance skin');
 // Persistence must survive a reload even when appearance paints before the
 // server accepts it. Keep these real writes in flight long enough to expose
 // a helper that mistakes optimistic controls for saved settings.
 await page.route('**/api/actions',async route=>{
  if(['shell.changes.prepare','shell.changes.apply'].includes(route.request().postDataJSON()?.action))await new Promise(resolve=>setTimeout(resolve,500));
  await route.continue();
 });
 await openSettingsPage(page,'appearance');await presentation('scheme','dark');await presentation('layout','work');
 await page.reload();await page.locator('.a-settings-experience').waitFor();await openSettingsPage(page,'custom-appearance');await page.getByText('Advanced customization',{exact:true}).click();await page.locator('#theme-name').waitFor();
 assert.equal(await page.locator('#theme-name').inputValue(),'Acceptance skin');
 // Shell settings hydrate separately from the host's skin controls after reload.
 await openSettingsPage(page,'appearance');await page.getByText('Layout & background',{exact:true}).click();await expect(page.locator('#layout')).toHaveValue('work');
 await page.unrouteAll({behavior:'wait'});
 await openSettingsPage(page,'custom-appearance');await page.getByRole('button',{name:'Restore original appearance',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().theme.name!=='Acceptance skin');
 await openSettingsPage(page,'tool-connections');
 await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
 await page.getByRole('button',{name:/Fixture catalog tool/}).waitFor();
 await page.locator('#filter-smart-tool-catalog').fill('fixture*');
 await page.getByRole('button',{name:/Fixture catalog tool/}).click();await page.getByRole('button',{name:'Inspect source and setup',exact:true}).click();
 await page.getByText('Python 3.13',{exact:true}).waitFor();
 await page.locator('#smart-tool-extras').fill('mcp');
 await page.getByRole('button',{name:'Install package',exact:true}).click();
 await page.getByRole('button',{name:'Configure MCP connection',exact:true}).click();
 await page.locator('#smart-tool-command').fill('/fixture/bin/mcp');
 await page.locator('#smart-tool-arguments').fill('--read-only\n--verbose');
 await page.locator('#smart-tool-environment').fill('TOOL_KEY=FIXTURE_KEY');
 await page.getByRole('button',{name:'Save connection',exact:true}).click();
 await expect.poll(async()=>(await settingsToolObservations(page)).servers.length).toBe(1);
 await page.getByRole('button',{name:'View connections',exact:true}).click();
 await page.getByRole('button',{name:/Fixture catalog tool.*disconnected/i}).waitFor();
 const server=(await settingsToolObservations(page)).servers[0];assert.deepEqual(server.args,['--read-only','--verbose']);assert.deepEqual(server.env,{TOOL_KEY:'FIXTURE_KEY'});
 await page.reload();await page.getByRole('button',{name:/Fixture catalog tool.*disconnected/i}).waitFor();
 // Every declared destination is reachable by pointer and remains inside the
 // dialog on small phones, tablets, desktop, and both appearance schemes.
 let layouts=0;
 for(const scheme of ['light','dark']){
  await openSettingsPage(page,'appearance');await presentation('scheme',scheme);
  for(const width of [320,390,736,1280]){
   await page.setViewportSize({width,height:900});
   for(const section of settingsSections)for(const [id] of section.pages){
    await openSettingsPage(page,id);
    assert.ok(await page.locator('.a-settings-page-content:not([hidden])').isVisible(),id);
    assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} overflow at ${width} ${scheme}`);
    assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} dialog overflow at ${width}`);
    layouts++;
   }
  }
 }
 await openSettingsPage(page,'notifications');await page.screenshot({animations:'disabled',path:'/tmp/settings-notifications-final.png'});
 await openSettingsPage(page,'providers');await page.screenshot({animations:'disabled',path:'/tmp/settings-providers-final.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({destinations:settingsSections.reduce((count,section)=>count+section.pages.length,0),layouts,notificationPersistence:true,privateDrafts:true,voicePersistence:true,skinPersistence:true,catalogToConnection:true,browserErrors:0}));
}catch(error){
 failure=error;
 console.error(error);
 try{if(page)await page.screenshot({animations:'disabled',path:'/tmp/settings-completeness-failure.png'})}catch(captureError){console.error('Failure screenshot unavailable:',captureError.message)}
 throw error;
}
finally{await finishFixture({label:"Settings fixture",close:[["Settings browser",()=>browser?.close()]],stop,originalError:failure})}
