import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const output=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!output)throw Error('Owned evidence folder required');
const child=spawn(fixturePython(),['-u',fileURLToPath(new URL('../../tests/fixtures/update_current_health_server.py',import.meta.url))]);
let log='',browser;child.stderr.on('data',x=>log+=x);
const result={paidCalls:0,liveInstallCalls:0,syntheticRecordedGraph:true,capturedLegacyAttempts:true};
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error(log||'boot timeout')),60000);child.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code+log))});child.stdout.on('data',x=>{text+=x;for(const line of text.split('\n'))try{const v=JSON.parse(line);if(v.url){clearTimeout(timer);resolve(v)}}catch{}})});
 browser=await chromium.launch({headless:true,channel:'chromium',args:['--disable-gpu','--no-zygote']});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const panel=page.locator('[data-part="updates"]'),status=page.locator('[data-part="application-update"]').first();
 await expect(status).toContainText('You’re up to date');await expect(panel.locator('.a-update-current-action')).toHaveCount(0);
 assert.equal((await (await page.request.get(boot.url+'/fixture/current-update')).json()).originalHistoryUnchanged,true);
 await page.screenshot({path:output+'/current-desktop.png'});
 await page.setViewportSize({width:390,height:844});await expect(status).toContainText('You’re up to date');await page.screenshot({path:output+'/current-mobile.png'});
 await panel.locator('[data-part="ecosystem-update-details"]>summary').click();await panel.locator('[data-part="update-attempt-history"]>summary').click();await expect(panel).toContainText('No earlier attempt is rewritten as successful.');await expect(panel).toContainText('amplifier-foundation');
 await page.reload();await page.waitForSelector('#amp-one');if(!await status.isVisible())await openSettingsPage(page,'updates');await expect(status).toContainText('You’re up to date');
 const transition=async mode=>{const response=await page.request.post(boot.url+'/fixture/current-update',{data:{mode}});assert.ok(response.ok());return response.json()};
 const failed=await transition('failure');assert.equal(failed.historyPreserved,true);await expect(status).toContainText('Component source needs review');await expect(panel.getByRole('button',{name:'Review source details'})).toBeVisible();await expect(panel).not.toContainText('Open Updates to review');
 await page.screenshot({path:output+'/blocked-mobile.png'});await page.setViewportSize({width:1280,height:950});await page.screenshot({path:output+'/blocked-desktop.png'});
 await transition('uncertain');await expect(status).toContainText('Installation needs verification');
 await transition('current');await expect(status).toContainText('You’re up to date');
 assert.deepEqual(errors,[]);Object.assign(result,{status:'passed',package:boot.package,currentDesktop:true,currentMobile:true,historyRetained:true,reload:true,currentFailureHasAction:true,uncertainPreserved:true});
} catch(error){Object.assign(result,{status:'failed',error:String(error),fixtureLog:log});throw error}
finally{await writeFile(output+'/browser-result.json',JSON.stringify(result,null,2));await browser?.close();child.kill('SIGTERM');await new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);setTimeout(resolve,5000)})}
