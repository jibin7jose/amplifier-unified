import {fixturePython} from './fixture-python.mjs';
// Real settings/actions with synthetic keys; no provider network calls.
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/settings_collections_server.py',import.meta.url))]);
let browser,page,stderr='';fixture.stderr.on('data',data=>stderr+=data);
try{
 const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(stderr||'Fixture timeout')),30000);fixture.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0])}});fixture.on('exit',code=>reject(Error('Fixture exited '+code+stderr)))});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});page=await browser.newPage({viewport:{width:1280,height:920},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.waitForSelector('#amp-one');await page.waitForFunction(()=>window.amplifier?.getState());await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:['ai-connections']}}));await openSettingsPage(page,'ai-connections');
 await page.getByRole('button',{name:/OpenAI API.*one/}).click();
 await page.getByText('Account and connection checks',{exact:true}).click();
 const visible=()=>page.locator('.a-settings-page-content:not([hidden])');
 await expect(visible().getByText('fixtur…-key',{exact:true})).toBeVisible();
 await expect(visible().getByText('FIXTURE_KEY',{exact:true})).toBeVisible();
 await expect(visible().getByText(/Existing chats may still use an earlier key/)).toBeVisible();
 assert.ok(!(await page.content()).includes('fixture-private-key'));
 await visible().getByRole('button',{name:'Refresh credentials',exact:true}).click();
 await expect(visible().getByRole('button',{name:'Refresh credentials',exact:true})).toBeEnabled();
 // Agent and UI receive the same masked identity, including after a save.
 const replacement='rotate-private-middle-value-9876';
 await page.evaluate(async apiKey=>{const state=window.amplifier.getState();await window.amplifier.dispatch('providers.save',{sessionId:state.activeSessionId,id:'one',module:'provider-openai',apiKey,config:{default_model:'fixture-model'}})},replacement);
 await expect(visible().getByText('rotate…9876',{exact:true})).toBeVisible();
 await expect(visible().getByText(/Private keys file/)).toBeVisible();
 const state=await page.evaluate(()=>window.amplifier.getState());
 assert.equal(state.setup.providers.find(p=>p.id==='one').credential.preview.masked,'rotate…9876');
 assert.ok(!JSON.stringify(state).includes(replacement));
 assert.ok(!(await page.content()).includes(replacement));
 await openSettingsPage(page,'providers');
 await visible().locator('[data-collection-id=one] button').click();
 await expect(visible().getByText('rotate…9876',{exact:true})).toBeVisible();
 await visible().getByRole('button',{name:'Refresh credentials',exact:true}).click();
 await expect(visible().getByRole('button',{name:'Refresh credentials',exact:true})).toBeEnabled();
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 await page.screenshot({path:'/tmp/provider-key-preview-mobile.png'});
 await page.setViewportSize({width:1280,height:920});
 await page.screenshot({path:'/tmp/provider-key-preview-desktop.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({maskedPreview:true,source:true,rotation:true,sharedActions:true,advancedAndEveryday:true,mobile:true,fullSecretsAbsent:true,browserErrors:0}));
}catch(error){if(page){await page.screenshot({path:'/tmp/provider-key-preview-failure.png'});console.log('Saved synthetic fixture screenshot to /tmp/provider-key-preview-failure.png')}throw error}finally{await browser?.close();fixture.kill('SIGTERM')}
