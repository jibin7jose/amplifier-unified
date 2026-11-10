import './composer-test-helpers.mjs';
import {openSettingsPage,openSettingsDialog} from '../../.build/web-client/tests/browser-settings.mjs';
import {settingsSections} from '../../.build/web-client/src/settings-navigation.js';
import {spawn} from 'node:child_process';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const evidence=process.env.AMPLIFIER_TEST_OUTPUT||join(tmpdir(),'amplifier-settings-everyday');await mkdir(evidence,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/settings_collections_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),60000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});fixture.on('exit',code=>reject(Error('Fixture exited '+code+stderr)))});
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{})});page=await browser.newPage({viewport:{width:1280,height:920},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const preferenceLayouts=[];
 async function checkPreferenceLayout(surface){
  const control=page.locator('.a-settings-page-content:not([hidden])').getByLabel('Preferred chat connection',{exact:true});
  await expect(control).toBeVisible();
  const geometry=await control.evaluate(select=>{
   const row=select.closest('.a-connection-capability'),label=row.querySelector('label'),copy=label.parentElement;
   const box=element=>{const {x,y,width,height}=element.getBoundingClientRect();return {x,y,width,height}};
   return {row:box(row),copy:box(copy),label:box(label),select:box(select),lineHeight:parseFloat(getComputedStyle(label).lineHeight)};
  });
  preferenceLayouts.push({surface,width:page.viewportSize().width,...geometry});
  await writeFile(join(evidence,'preferred-connection-layouts.json'),JSON.stringify(preferenceLayouts,null,2));
  assert.ok(geometry.copy.width>=geometry.row.width*.6,`${surface}: preference explanation collapsed: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.label.height<=geometry.lineHeight*3,`${surface}: preference label wraps excessively`);
  assert.ok(geometry.select.width>=Math.min(180,geometry.row.width-2),`${surface}: picker is too narrow`);
  assert.ok(geometry.select.x>=geometry.row.x-1&&geometry.select.x+geometry.select.width<=geometry.row.x+geometry.row.width+1,`${surface}: picker overflows its row`);
 }
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(base);await page.waitForSelector('#amp-one');
 // A fresh browser starts on Home. This journey checks an existing chat's
 // saved draft, so explicitly open that chat before testing its settings.
 assert.equal((await state()).view.workSurface,'home');
 await page.evaluate(()=>{const chat=window.amplifier.getState().sessions.find(row=>row.title==='Settings test');return window.amplifier.dispatch('session.select',{id:chat.id})});
 await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='chat');
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 const savedDraft=page.waitForResponse(response=>{if(response.request().method()!=='POST'||!response.url().includes('/api/actions'))return false;const body=response.request().postDataJSON();return body.action==='view.update'&&body.args?.patch?.draft==='Keep this while I connect AI'&&response.ok()});
 await composer.fill('Keep this while I connect AI');await savedDraft;
 await openSettingsPage(page,'ai-connections');
 assert.equal(await page.locator('.a-settings-sidebar>button').count(),settingsSections.length);
 await expect(page.locator('.a-settings-sidebar').getByRole('button',{name:'Workspaces',exact:true})).toBeVisible();
 assert.equal(await page.locator('.a-settings-sidebar').getByText('Bundles & modules',{exact:true}).count(),0);
 const preferred=page.locator('.a-settings-page-content:not([hidden])').getByLabel('Preferred chat connection',{exact:true});
 await expect(preferred).toBeVisible();
 const originalPreference=await preferred.inputValue();
 const alternatePreference=await preferred.locator('option').evaluateAll((rows,current)=>rows.find(row=>row.value!==current).value,originalPreference);
 await preferred.selectOption(alternatePreference);
 await expect.poll(async()=>(await state()).setup.providers[0].id).toBe(alternatePreference);
 await expect(page.getByText('Preferred chat connection saved. Existing chats and model rules are unchanged.',{exact:true})).toBeVisible();
 await preferred.selectOption(originalPreference);
 await expect.poll(async()=>(await state()).setup.providers[0].id).toBe(originalPreference);
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();
 await page.getByRole('button',{name:/Anthropic API Connect with/}).click();
 await page.getByLabel('API key',{exact:true}).fill('everyday-private-fixture-key');
 await openSettingsPage(page,'appearance');await openSettingsPage(page,'ai-connections');
 await expect(page.getByRole('button',{name:'Connect another service',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Connect another service',exact:true}).click();await page.getByRole('button',{name:/Anthropic API Connect with/}).click();await page.getByLabel('API key',{exact:true}).fill('everyday-private-fixture-key');
 assert.ok(!JSON.stringify(await state()).includes('everyday-private-fixture-key'));
 await page.getByRole('button',{name:'Save and check connection',exact:true}).click();
 await page.getByRole('button',{name:'Finish setup',exact:true}).waitFor();
 const modelPicker=page.locator('select#ai-model');
 await expect(modelPicker.locator('option[value="fixture-alternative"]')).toHaveCount(1);
 await modelPicker.selectOption('fixture-alternative');
 const oldMatrix=(await state()).setup.active;
 await page.getByRole('button',{name:'Finish setup',exact:true}).click();
 await expect(page.getByText('Model saved. Existing model rules are unchanged.',{exact:true})).toBeVisible();
 assert.equal((await state()).setup.active,oldMatrix);
 assert.ok((await state()).setup.providers.some(p=>p.module==='provider-anthropic'&&p.config.default_model==='fixture-alternative'));
 assert.ok(!JSON.stringify(await state()).includes('everyday-private-fixture-key'));
 await page.getByRole('button',{name:'Return to chat',exact:true}).click();
 await expect(page.getByRole('region',{name:'Settings content',exact:true})).toHaveCount(0);
 await expect(composer).toHaveDraft('Keep this while I connect AI');
 await openSettingsPage(page,'ai-connections');
 // Existing model selection saves on change without a second completion click.
 await page.getByRole('button',{name:/Anthropic API.*fixture-alternative/}).click();
 await page.getByRole('button',{name:'Choose model',exact:true}).click();
 const defaultEffort=page.getByLabel('Default reasoning effort',{exact:true});
 await expect(defaultEffort).toBeVisible();
 const chosenProvider=(await state()).view.aiConnectionEditor.id;
 await defaultEffort.selectOption('high');
 await expect.poll(async()=>(await state()).setup.providers.find(p=>p.id===chosenProvider).config.reasoning_effort).toBe('high');
 await expect(page.getByText('Default reasoning effort saved for new conversations. Existing chats keep their choices.',{exact:true})).toBeVisible();
 // Provider-default reset is a distinct saved null, not the literal 'none'.
 await defaultEffort.selectOption('');
 await expect.poll(async()=>(await state()).setup.providers.find(p=>p.id===chosenProvider).config.reasoning_effort).toBe(null);
 await page.reload();await expect(defaultEffort).toHaveValue('');
 await expect(modelPicker.locator('option[value="fixture-model"]')).toHaveCount(1);
 await modelPicker.selectOption('fixture-model');
 await expect(page.getByText('Model saved. Existing model rules are unchanged.',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Finish setup',exact:true})).toHaveCount(0);
 await page.screenshot({path:join(evidence,'settings-everyday-ai-desktop.png')});
 await openSettingsPage(page,'smart-tools');await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
 await page.getByRole('checkbox',{name:'Select Tool 00',exact:true}).check();await page.getByRole('checkbox',{name:'Select Tool 02',exact:true}).check();
 await page.getByRole('button',{name:'Review & install',exact:true}).click();
 await expect(page.getByRole('region',{name:'Review installation',exact:true})).toBeVisible();
 assert.equal((await state()).smartTools.operations.filter(op=>op.action==='smartTools.installBatch').length,0);
 await page.getByRole('button',{name:'Install selected (2)',exact:true}).click();
 await page.getByRole('button',{name:'Retry unfinished installations',exact:true}).click();
 await page.waitForFunction(()=>window.amplifier.getState().smartTools.operations.filter(op=>op.action==='smartTools.installBatch').at(-1)?.status==='completed');
 await page.getByRole('button',{name:'Installed',exact:true}).click();
 await expect(page.getByText('Installed · finish setup',{exact:true}).first()).toBeVisible();
 await page.getByRole('button',{name:/tool-0 Installed · finish setup/}).click();
 await expect(page.getByText('Advanced setup required',{exact:true})).toBeVisible();
 await openSettingsPage(page,'notifications');
 await page.getByRole('checkbox',{name:/On this device/}).uncheck();
 await page.waitForFunction(()=>window.amplifier.getState().notificationSettings.desktop===false);
 const notification=page.getByText('Set up phone notifications',{exact:true});
 assert.equal(await notification.locator('..').getAttribute('open'),null);await notification.click();
 await page.getByRole('checkbox',{name:'Include a response preview'}).check();await page.waitForFunction(()=>window.amplifier.getState().notificationSettings.preview===true);
 await page.getByText('Server & account',{exact:true}).click();await page.getByLabel(/^Access token/).fill('notification-private-fixture');await openSettingsPage(page,'privacy');await openSettingsPage(page,'notifications');
 await page.getByText('Set up phone notifications',{exact:true}).click();await page.getByText('Server & account',{exact:true}).click();assert.equal(await page.getByLabel(/^Access token/).inputValue(),'notification-private-fixture');assert.ok(!JSON.stringify(await state()).includes('notification-private-fixture'));
 let layouts=0;
 for(const width of [320,390,736,1280]){
  await page.setViewportSize({width,height:920});
  for(const section of settingsSections)for(const [id] of section.pages){
   await openSettingsPage(page,id);
   await expect(page.locator('.a-settings-page-content:not([hidden])')).toBeVisible();
   assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} overflow at ${width}`);
   assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),`${id} dialog overflow at ${width}`);layouts++;
  }
 }
 await openSettingsPage(page,'updates');await page.screenshot({path:join(evidence,'settings-everyday-updates-desktop.png')});
 await page.setViewportSize({width:390,height:844});await openSettingsPage(page,'ai-connections');
 await page.locator('.a-settings-mobile-head button').first().click();await page.screenshot({path:join(evidence,'settings-everyday-mobile-index.png')});
 await page.locator('[data-settings-section=privacy]').click();await page.screenshot({path:join(evidence,'settings-everyday-mobile-privacy.png')});
 // Exercise both entry points after the existing behavior journey so mounting
 // the retained advanced editor cannot add hidden duplicates to its locators.
 for(const destination of ['ai-connections','providers']){
  for(const width of [320,390,600,601,736,960,1280,1920]){
   await page.setViewportSize({width,height:920});
   await openSettingsPage(page,destination);
   if(destination==='providers'&&!await page.locator('#advanced-preferred-chat-connection').isVisible()){
    await page.getByRole('button',{name:'one fixture-model · credentials ready',exact:true}).click();
   }
   await checkPreferenceLayout(destination);
  }
 }
 await page.emulateMedia({forcedColors:'active'});
 await checkPreferenceLayout('providers-forced-colors');
 await page.emulateMedia({forcedColors:'none'});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({guidedProvider:true,privateDrafts:true,routingPreserved:true,catalogReviewAndRetry:true,installedVsReady:true,layouts,preferenceLayouts:preferenceLayouts.length,browserErrors:0}));
}catch(error){if(page)await page.screenshot({path:join(evidence,'settings-everyday-failure.png')});throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
