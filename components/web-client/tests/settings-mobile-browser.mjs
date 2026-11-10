import {openChatAdvancedPage} from './browser-chat-pages.mjs';
import {openSettingsDialog,openSettingsPage,openPhoneNotificationSettings,settingsToolObservations} from './browser-settings.mjs';
import {settingsSections} from '../src/settings-navigation.js';
import {createSettingsReloadObservation} from './settings-reload-observation.mjs';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const evidence=process.env.AMPLIFIER_TEST_OUTPUT||join(tmpdir(),'amplifier-settings-mobile');await mkdir(evidence,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/settings_collections_server.py',import.meta.url))]);
let browser,page,reloadObservation,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture timed out: '+stderr)),60000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});fixture.on('exit',code=>reject(new Error('Fixture exited '+code+': '+stderr)));});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const root=page.locator('.a-settings-experience'),footer=page.locator('.a-settings-mobile-actions'),state=()=>page.evaluate(()=>window.amplifier.getState());
 const route=key=>expect(root).toHaveAttribute('data-settings-route',key);
 const back=async()=>{const before=await root.getAttribute('data-settings-route');await page.locator('.a-settings-mobile-head button').first().click();await page.waitForFunction(previous=>document.querySelector('.a-settings-experience')?.dataset.settingsRoute!==previous,before);};
 await page.goto(base);await page.waitForSelector('#amp-one');await openSettingsDialog(page);await route('index');
 assert.equal(await page.locator('.a-dialog-head').isVisible(),false);assert.equal(await root.locator('.a-settings-sidebar>button').count(),settingsSections.length);
 await expect(page.getByRole('button',{name:'Close settings',exact:true})).toBeVisible();await page.screenshot({animations:'disabled',path:join(evidence,'settings-mobile-index.png')});
 await openSettingsPage(page,'providers');await route('providers');
 const providers=page.locator('[data-part=provider-settings]');await providers.locator('[data-collection-id=two]>button').click();await route('providers/detail');
 const access=providers.locator('.a-provider-access'),accessSummary=access.locator('summary');
 await accessSummary.focus();await page.keyboard.press('Enter');await expect(access).toHaveAttribute('open','');await page.keyboard.press('Space');await expect(access).not.toHaveAttribute('open','');
 await accessSummary.click();await page.locator('#provider-key-source').selectOption('private');await page.locator('#provider-key').fill('mobile-private-never-share');await page.locator('#provider-model').fill('mobile-unsaved-model');
 await expect(footer.getByRole('button',{name:/Save/})).toBeVisible();
 await page.evaluate(()=>history.back());await route('providers');await page.evaluate(()=>history.forward());await route('providers/detail');
 for(let i=0;i<8;i++){await page.locator('#provider-model').fill('mobile-unsaved-model');await page.evaluate(()=>history.back());await route('providers');await page.evaluate(()=>history.forward());await route('providers/detail');}
 assert.equal(await page.locator('#provider-key').inputValue(),'mobile-private-never-share');assert.equal(await page.locator('#provider-model').inputValue(),'mobile-unsaved-model');
 assert.ok(!JSON.stringify(await state()).includes('mobile-private-never-share'));assert.ok(!JSON.stringify(await page.evaluate(()=>history.state)).includes('mobile-private-never-share'));
 await back();await providers.getByRole('button',{name:'Preference order',exact:true}).click();await route('providers/order');
 await page.getByLabel('Position of one',{exact:true}).selectOption({value:'2'});await footer.getByRole('button',{name:'Cancel',exact:true}).click();await route('providers');assert.deepEqual((await state()).setup.providers.map(p=>p.id),['one','two','three']);
 await providers.getByRole('button',{name:'Preference order',exact:true}).click();await page.getByLabel('Position of one',{exact:true}).selectOption({value:'2'});await footer.getByRole('button',{name:'Save order',exact:true}).click();await route('providers');assert.deepEqual((await state()).setup.providers.map(p=>p.id),['two','three','one']);

 // Touch drag has a lifted preview and insertion line; Back retains the unfinished order.
 await providers.getByRole('button',{name:'Preference order',exact:true}).click();
 const order=providers.locator('.a-order-editor'),ids=()=>order.locator('[data-order-id]').evaluateAll(rows=>rows.map(row=>row.dataset.orderId));
 const cdp=await page.context().newCDPSession(page),from=await order.getByRole('button',{name:'Reorder two',exact:true}).boundingBox(),to=await order.locator('[data-order-id=one]').boundingBox();
 const touch=async(type,x,y)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'?[]:[{x,y,id:1}]});
 await touch('touchStart',from.x+from.width/2,from.y+from.height/2);
 await touch('touchMove',from.x+from.width/2,to.y+to.height/2);
 await expect(page.locator('.a-order-floating')).toBeVisible();await expect(order.locator('.a-order-insertion')).toBeVisible();
 const preview=await ids();assert.deepEqual(preview,['three','one','two']);await touch('touchEnd');await expect.poll(ids).toEqual(preview);
 await back();await providers.getByRole('button',{name:'Preference order',exact:true}).click();assert.deepEqual(await ids(),preview);
 await footer.getByRole('button',{name:'Cancel',exact:true}).click();assert.deepEqual((await state()).setup.providers.map(p=>p.id),['two','three','one']);
 await openSettingsPage(page,'routing');await route('routing');await page.locator('[data-collection-id=general]>button').click();await route('routing/role/general');await page.locator('.a-routing-candidates [data-collection-id="0"]>button').click();await route('routing/choice/general');
 await page.getByLabel('general model 1',{exact:true}).fill('mobile-routing-*');await page.screenshot({path:join(evidence,'settings-mobile-choice.png')});await back();await route('routing/role/general');await back();await route('routing');
 await footer.getByRole('button',{name:'Save active profile',exact:true}).click();await expect(page.getByText('Routing profile saved and selected.',{exact:true})).toBeVisible();assert.equal((await state()).setup.matrix.roles.general.candidates[0].model,'mobile-routing-*');

 // A changed candidate set cannot reuse positional IDs from an unfinished order.
 await page.locator('[data-collection-id=general]>button').click();await page.getByRole('button',{name:'Preference order',exact:true}).click();
 await page.getByLabel('Position of mobile-routing-*',{exact:true}).selectOption({value:'5'});await back();
 await page.locator('.a-routing-candidates [data-collection-id="5"]>button').click();await page.getByRole('button',{name:'Remove general candidate 6',exact:true}).click();await back();
 await page.getByRole('button',{name:'Preference order',exact:true}).click();await expect(page.locator('[data-order-id]')).toHaveCount(5);await expect(page.getByText('The choices changed. Review the current order before saving.',{exact:true})).toBeVisible();
 await footer.getByRole('button',{name:'Save order',exact:true}).click();await route('routing/role/general');assert.equal((await state()).view.routingEditor.matrix.roles.general.candidates.length,5);assert.equal((await state()).setup.matrix.roles.general.candidates.length,6);
 await openSettingsPage(page,'notifications');await openPhoneNotificationSettings(page);await page.getByLabel(/^Topic/).fill('mobile-private-topic');await page.getByLabel(/^Access token/).fill('mobile-private-token');await back();await root.locator('[data-settings-section=appearance]').click();await back();await root.locator('[data-settings-section=notifications]').click();assert.equal(await page.getByLabel(/^Topic/).inputValue(),'mobile-private-topic');assert.equal(await page.getByLabel(/^Access token/).inputValue(),'mobile-private-token');
 await openSettingsPage(page,'tool-connections');await page.getByRole('button',{name:'Tool catalog',exact:true}).click();await route('tools/catalog');
 await page.getByRole('checkbox',{name:'Select Tool 00',exact:true}).check();await page.getByRole('checkbox',{name:'Select Tool 02',exact:true}).check();await page.getByRole('button',{name:'Next',exact:true}).click();await page.getByRole('checkbox',{name:'Select Tool 50',exact:true}).check();await page.locator('#filter-smart-tool-catalog').fill('Research');
 await expect(footer.getByRole('button',{name:'Review & install',exact:true})).toBeVisible();await expect(footer.getByText('1 outside this view',{exact:true})).toBeVisible();
 await page.locator('[data-collection-id=tool-1]>button').click();await route('tools/catalog/detail');await expect(page.locator('#filter-smart-tool-catalog')).toBeHidden();await expect(footer.getByRole('button',{name:'Review & install',exact:true})).toBeVisible();await back();await route('tools/catalog');assert.equal(await page.locator('#filter-smart-tool-catalog').inputValue(),'Research');

 // Back must restore against the list, even when rendering spans browser frames.
 // A passive editor sync briefly left detail-sized content under the list route
 // and clamped the saved scroll position before the list appeared.
 const body=page.locator('.a-settings-content'),row=page.locator('[data-collection-id=tool-8]>button');
 await cdp.send('Emulation.setCPUThrottlingRate',{rate:12});
 for(let attempt=0;attempt<3;attempt++){
  await row.scrollIntoViewIfNeeded();
  const scrollBefore=await body.evaluate(el=>el.scrollTop);await row.click();await route('tools/catalog/detail');await back();
  await expect.poll(()=>body.evaluate(el=>el.scrollTop)).toBeCloseTo(scrollBefore,0);
 }
 await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});
 // Native touch on a row scrolls normally rather than starting a reorder.
 const box=await row.boundingBox();await touch('touchStart',box.x+80,Math.min(box.y+20,650));await touch('touchMove',box.x+80,250);await touch('touchEnd');
 await expect(page.locator('.a-order-floating')).toHaveCount(0);
 await page.screenshot({path:join(evidence,'settings-mobile-catalog.png')});

 // A portaled submit button still belongs to its real form.
 await openSettingsPage(page,'tool-connections');await page.getByRole('button',{name:'Add MCP connection',exact:true}).click();await route('tools/connection');
 await page.getByLabel('Display name',{exact:true}).fill('Mobile fixture connection');await page.getByLabel('Executable',{exact:true}).fill('/fixture/bin/mcp');
 assert.equal(await footer.getByRole('button',{name:'Save connection',exact:true}).evaluate(el=>el.form?.id),'smart-connection-form');
 await footer.getByRole('button',{name:'Save connection',exact:true}).click();await expect.poll(async()=>(await settingsToolObservations(page)).servers.some(row=>row.name==='Mobile fixture connection')).toBe(true);
 // Exercise the visual viewport resize path used by soft keyboards.
 await page.getByLabel('Environment variable references (optional)',{exact:true}).focus();
 await page.evaluate(()=>{Object.defineProperty(visualViewport,'height',{configurable:true,value:480});visualViewport.dispatchEvent(new Event('resize'));});
 await expect.poll(()=>footer.evaluate(el=>Math.round(el.getBoundingClientRect().bottom))).toBe(480);
 await expect.poll(()=>page.getByLabel('Environment variable references (optional)',{exact:true}).evaluate(el=>el.getBoundingClientRect().bottom<=el.closest('.a-settings-content').getBoundingClientRect().bottom)).toBe(true);
 await page.evaluate(()=>{delete visualViewport.height;visualViewport.dispatchEvent(new Event('resize'));});
 await page.setViewportSize({width:736,height:390});await expect.poll(()=>footer.evaluate(el=>Math.round(el.getBoundingClientRect().bottom))).toBe(390);
 assert.ok(await body.evaluate(el=>el.clientHeight>80));await page.setViewportSize({width:390,height:844});

 // App-owned diagnostics use the same nested header and preserve destination drafts.
 await openSettingsPage(page,'diagnostics');await page.getByRole('button',{name:'Add server',exact:true}).click();await route('diagnostics/destination');
 await page.getByLabel('Name',{exact:true}).fill('Mobile diagnostics draft');await page.getByLabel('Server URL',{exact:true}).fill('https://fixture.invalid');
 await expect(footer.getByRole('button',{name:'Save destination',exact:true})).toBeVisible();await back();await route('diagnostics');
 await page.locator('.a-diagnostic-destinations button').filter({hasText:'Mobile diagnostics draft'}).click();await route('diagnostics/destination');assert.equal(await page.getByLabel('Server URL',{exact:true}).inputValue(),'https://fixture.invalid');
 // Every existing destination remains reachable through narrow navigation.
 for(const width of [320,390,736,960,1280]){
  await page.setViewportSize({width,height:844});await expect(root).toHaveAttribute('data-compact',String(width<960));
  for(const section of settingsSections)for(const [destination]of section.pages){
   await openSettingsPage(page,destination);
   assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Horizontal overflow: '+width+' '+destination);
  }
 }
 reloadObservation=await createSettingsReloadObservation(page,evidence);
 await reloadObservation.confirmed({
  open:async()=>{await page.setViewportSize({width:390,height:844});await openSettingsPage(page,'providers')},
  select:async()=>{await providers.locator('[data-collection-id=two]>button').click();assert.equal(await page.locator('#provider-key').inputValue(),'mobile-private-never-share')},
 });
 await route('providers/detail');
 // An optimistic detail is not a saved route. Keep immediate-reload coverage
 // with a real request held before HTTP forwarding, then retire it by abort only.
 await reloadObservation.pending({
  back:async()=>{await back();await route('providers')},
  select:()=>providers.locator('[data-collection-id=two]>button').click(),
 });
 await reloadObservation.save();reloadObservation.dispose();
 await route('providers');await back();await route('advanced');await back();await route('index');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();await page.getByRole('button',{name:'Discard changes',exact:true}).click();await expect(root).toHaveCount(0);assert.equal(await page.evaluate(()=>history.state?.amplifierSettings),undefined);await openSettingsDialog(page);await route('index');await page.evaluate(()=>history.back());await expect(root).toHaveCount(0);
 // Conversation modules now belong to the selected chat's public Model & tools page.
 const closeNavigation=page.getByRole('button',{name:'Close navigation',exact:true});
 if(await closeNavigation.isVisible())await closeNavigation.click();
 await openChatAdvancedPage(page,'Model & tools');await expect(page.getByRole('button',{name:'Back to Advanced',exact:true})).toBeVisible();await page.getByText('Conversation modules',{exact:true}).click();
 // Details are passive; explicitly inspect the conversation before editing loaded modules.
 await page.getByRole('dialog',{name:'Model & tools',exact:true}).getByRole('button',{name:'Load configuration',exact:true}).click();
 await expect(page.getByRole('searchbox',{name:'Filter loaded modules',exact:true})).toBeVisible();await expect(page.getByRole('checkbox',{name:'Enable context-simple',exact:true})).toBeChecked();await expect(page.getByRole('checkbox',{name:'Enable context-simple',exact:true})).toBeDisabled();await page.screenshot({path:join(evidence,'settings-mobile-modules.png')});
 assert.deepEqual(errors,[]);console.log('Mobile Settings: index, browser Back/Forward, private drafts, nested routing, footer actions, ordering, catalog selection and all destinations at 5 widths passed.');
}catch(error){if(reloadObservation)try{await reloadObservation.save(error)}catch{console.error('[settings-reload:evidence-write-failed]')}if(page)await page.screenshot({path:join(evidence,'settings-mobile-failure.png')});throw error;}
finally{reloadObservation?.dispose();await browser?.close();fixture.kill('SIGTERM');}
