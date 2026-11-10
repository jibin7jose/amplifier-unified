import {fixturePython} from './fixture-python.mjs';
// Shared UI/agent test-message action with synthetic success/failure; no provider network calls.
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const out=process.env.AMPLIFIER_TEST_ARTIFACTS;if(!out)throw Error('Owned artifact directory required');
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
 const before=await page.evaluate(()=>window.amplifier.getState().sessions.length);
 await visible().getByRole('button',{name:'Send test message',exact:true}).click();
 await expect(visible().getByText('Test message succeeded',{exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.length),before);
 // The public global Settings surface and agent request must name the same configuration owner.
 const receipt=await page.evaluate(()=>window.amplifier.dispatch('providers.testMessage',{id:'two',configurationTarget:{kind:'global'}}));
 assert.equal(receipt.accepted,true);
 await page.waitForFunction(()=>window.amplifier.getState().setup.messageTests?.two?.error==='insufficient_quota');
 await openSettingsPage(page,'providers');
 await visible().locator('[data-collection-id=two] button').click();
 await expect(visible().getByText('Test message failed',{exact:true})).toBeVisible();
 await expect(visible().getByText('insufficient_quota',{exact:true})).toBeVisible();
 await visible().getByRole('button',{name:'Send test message',exact:true}).click();
 await expect(visible().getByRole('button',{name:'Send test message',exact:true})).toBeEnabled();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.length),before);
 assert.ok(!(await page.content()).includes('fixture-private-key'));
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('.a-settings-content').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 await page.screenshot({path:out+'/mobile.png'});
 await page.setViewportSize({width:1280,height:920});
 await page.screenshot({path:out+'/desktop.png'});
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({actualTestAction:true,successAndError:true,noChatCreated:true,sharedActions:true,advancedAndEveryday:true,mobile:true,fullSecretsAbsent:true,browserErrors:0}));
}catch(error){if(page){await page.screenshot({path:out+'/failure.png'});console.log('Saved synthetic fixture failure screenshot')}throw error}finally{try{await browser?.close()}finally{fixture.kill('SIGTERM')}}
