import {openSettingsPage,settingsToolObservations} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/settings_collections_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture timed out: '+stderr)),60000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});fixture.on('exit',code=>reject(new Error('Fixture exited '+code+': '+stderr)));});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(base);await page.waitForSelector('#amp-one');
 await openSettingsPage(page,'routing');await page.locator('#routing-name').waitFor();
 const routing=page.locator('[data-part=routing-settings]');
 assert.equal(await routing.locator('.a-collection-list>.a-collection-row').count(),13);
 assert.equal(await routing.locator('.a-routing-selected').count(),1);
 await routing.locator('[data-collection-id=general]').first().getByRole('button').click();
 await page.getByLabel('general model 1',{exact:true}).fill('custom-pattern-*');
 await page.getByRole('button',{name:'Save active profile',exact:true}).click();
 await expect(page.getByText('Routing profile saved and selected.',{exact:true})).toBeVisible();
 let saved=(await state()).setup.matrix;assert.equal(saved.roles.general.candidates[0].model,'custom-pattern-*');assert.equal(saved.roles.general.candidates[0].config.custom,'keep');assert.equal(saved.roles.general.candidates[0].unknownCandidate,0);assert.equal(saved.unknownProfile.keep,true);
 // Another management operation must not lock routing edits.
 await page.evaluate(()=>window.amplifier.dispatch('providers.models',{id:'one',refresh:true}));
 await expect(page.getByRole('button',{name:'Save active profile',exact:true})).toBeEnabled();
 await routing.locator('[data-collection-id=fast]').first().getByRole('button').click();
 await expect(page.getByLabel('fast model 1',{exact:true})).toBeEditable();
 await page.screenshot({path:'/tmp/settings-collections-routing.png'});
 // Exact insertion feedback, draft-only drop, keyboard and explicit position.
 await openSettingsPage(page,'providers');
 const provider=page.locator('[data-part=provider-settings]');
 await provider.getByRole('button',{name:'Preference order',exact:true}).click();
 const order=provider.locator('.a-order-editor'),ids=()=>order.locator('[data-order-id]').evaluateAll(rows=>rows.map(row=>row.dataset.orderId));
 const original=await ids();assert.deepEqual(original,['one','two','three']);
 const drag=async()=>{const handle=order.getByRole('button',{name:'Reorder one',exact:true});await handle.hover();const from=await handle.boundingBox(),to=await order.locator('[data-order-id=three]').boundingBox();await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();await page.mouse.move(from.x+from.width/2,to.y+to.height/2,{steps:8});};
 await drag();await expect(page.locator('.a-order-floating')).toBeVisible();await expect(order.getByRole('button',{name:'Save order',exact:true})).toBeDisabled();assert.deepEqual(await ids(),['two','three','one']);
 const geometry=await order.locator('.a-order-insertion').evaluate(line=>{const a=line.getBoundingClientRect(),b=line.parentElement.getBoundingClientRect();return {line:a.top,row:b.top,height:a.height};});assert.ok(geometry.line<geometry.row&&geometry.height===3);
 await page.screenshot({path:'/tmp/settings-collections-drag.png'});
 await page.keyboard.press('Escape');await page.mouse.up();assert.deepEqual(await ids(),original);
 await drag();await expect(page.locator('.a-order-floating')).toBeVisible();await expect(order.getByRole('button',{name:'Save order',exact:true})).toBeDisabled();assert.deepEqual(await ids(),['two','three','one']);await page.mouse.up();await expect.poll(ids).toEqual(['two','three','one']);assert.deepEqual((await state()).setup.providers.map(p=>p.id),original);
 await order.getByRole('button',{name:'Cancel',exact:true}).click();await provider.getByRole('button',{name:'Preference order',exact:true}).click();assert.deepEqual(await ids(),original);
 await order.getByLabel('Position of one',{exact:true}).selectOption({value:'2'});await order.getByRole('button',{name:'Save order',exact:true}).click();await expect(order).toHaveCount(0);assert.deepEqual((await state()).setup.providers.map(p=>p.id),['two','three','one']);
 await provider.getByRole('button',{name:'Preference order',exact:true}).click();const committed=await ids();
 const handle=order.getByRole('button',{name:'Reorder two',exact:true}),box=await handle.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(15,15,{steps:8});await page.mouse.up();assert.deepEqual(await ids(),committed);
 await handle.focus();await page.keyboard.press('ArrowDown');await expect.poll(ids).toEqual(['three','two','one']);await order.getByRole('button',{name:'Move two up',exact:true}).click();await expect.poll(ids).toEqual(committed);await order.getByRole('button',{name:'Cancel',exact:true}).click();
 // No catalog rediscovery is needed to reorder, and credentials stay in component memory.
 await provider.locator('[data-collection-id=two]>button').click();await provider.locator('summary').filter({hasText:'Provider & access'}).click();await page.locator('#provider-key-source').selectOption('private');await page.locator('#provider-key').fill('private-draft-never-shared');await page.locator('#provider-model').fill('unsaved-custom-model');
 await provider.locator('[data-collection-id=one]>button').click();await provider.locator('[data-collection-id=two]>button').click();assert.equal(await page.locator('#provider-model').inputValue(),'unsaved-custom-model');assert.equal(await page.locator('#provider-key').inputValue(),'private-draft-never-shared');assert.ok(!JSON.stringify(await state()).includes('private-draft-never-shared'));
 const row=provider.locator('[data-collection-id=one]');await row.hover();assert.equal(await row.evaluate(el=>getComputedStyle(el).backgroundColor),await provider.locator('.a-collection-row.selected').evaluate(el=>getComputedStyle(el).backgroundColor));
 await row.locator('svg').click();assert.equal(await page.locator('#provider-id').inputValue(),'one');
 await page.screenshot({path:'/tmp/settings-collections-providers.png'});
 // Composition excludes disabled entries and standalone registrations.
 await openSettingsPage(page,'app-bundles');await page.getByRole('button',{name:'Composition order',exact:true}).click();const bundleOrder=page.locator('.a-order-editor:visible');const entries=(await state()).bundles;const enabled=(Array.isArray(entries)?entries:entries.entries).filter(row=>row.role!=='standalone'&&row.enabled!==false);assert.equal(await bundleOrder.locator('[data-order-id]').count(),enabled.length);assert.ok(!(await bundleOrder.innerText()).includes('Capability-4'));assert.ok(!(await bundleOrder.innerText()).includes('Capability-5'));await bundleOrder.getByRole('button',{name:'Cancel',exact:true}).click();
 const selectedBundle=await page.locator('.a-collection-row.selected:visible').getAttribute('data-collection-id');await page.getByRole('checkbox',{name:'Enable Capability-1',exact:true}).uncheck();assert.equal(await page.locator('.a-collection-row.selected:visible').getAttribute('data-collection-id'),selectedBundle);
 // Catalog selection is global to the collection, with the requested 50/40 threshold.
 await openSettingsPage(page,'tool-connections');await page.getByRole('button',{name:'Tool catalog',exact:true}).click();
 const tools=page.locator('[data-part=smart-tools-settings]');await expect(tools.locator('.a-collection-row')).toHaveCount(40);
 await page.getByRole('checkbox',{name:'Select Tool 00',exact:true}).check();await page.getByRole('checkbox',{name:'Select Tool 02',exact:true}).check();await page.getByRole('button',{name:'Next',exact:true}).click();await expect(tools.locator('.a-collection-row')).toHaveCount(11);await page.getByRole('checkbox',{name:'Select Tool 50',exact:true}).check();
 await page.locator('#filter-smart-tool-catalog').fill('Research');await expect(tools.locator('.a-collection-row')).toHaveCount(32);await expect(page.getByRole('button',{name:'Review & install',exact:true})).toBeEnabled();
 await page.getByRole('button',{name:'Review & install',exact:true}).click();await expect(page.getByRole('heading',{name:'Install 3 tools?',exact:true})).toBeVisible();await page.getByRole('button',{name:'Install selected (3)',exact:true}).click();await expect(page.getByRole('button',{name:'Retry unfinished installations',exact:true})).toBeVisible();let batch=(await settingsToolObservations(page)).operations.filter(op=>op.action==='smartTools.installBatch').at(-1);assert.deepEqual(batch.items.map(item=>item.status),['completed','failed','completed']);
 await page.getByRole('button',{name:'Retry unfinished installations',exact:true}).click();await expect.poll(async()=>{const rows=(await settingsToolObservations(page)).operations.filter(op=>op.action==='smartTools.installBatch');return rows.length===2&&rows[1].status==='completed';}).toBe(true);batch=(await settingsToolObservations(page)).operations.filter(op=>op.action==='smartTools.installBatch').at(-1);assert.deepEqual(batch.items.map(item=>item.id),['tool-2']);
 await page.screenshot({path:'/tmp/settings-collections-catalog.png'});await page.getByRole('button',{name:'Back to catalog',exact:true}).click();
 // Browser back from details and full-row hits remain usable on narrow screens.
 for(const width of [320,390,736,1024]){await page.setViewportSize({width,height:900});await page.locator('#filter-smart-tool-catalog').fill('Research');if(width<960){await tools.locator('[data-collection-id=tool-1]>button').click();await expect(page.getByRole('button',{name:'Back to Catalog',exact:true})).toBeVisible();await page.getByRole('button',{name:'Back to Catalog',exact:true}).click();}assert.equal(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth),true,`overflow at ${width}`);}
 assert.deepEqual(errors,[]);console.log('Settings collections: routing, reorder preview/cancel/save, private drafts, composition, catalog pagination, batch retry, mobile checks passed.');
}catch(error){if(page)await page.screenshot({path:'/tmp/settings-collections-failure.png'});throw error;}
finally{await browser?.close();fixture.kill('SIGTERM');}
