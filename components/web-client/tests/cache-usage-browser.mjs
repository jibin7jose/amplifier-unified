import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/cache_usage_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser, failure;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Cache usage fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Cache usage fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);
 await page.locator('#amp-one').waitFor();
 // The empty fixture opens connection setup once discovery finishes. Wait for
 // that real transition so it cannot redirect Settings after Updates opens.
 await page.waitForFunction(()=>window.amplifier.getState().view?.setupJourney==='connect');
 await openSettingsPage(page,'updates');
 const details=page.locator('[data-part=ecosystem-update-details]');
 if(!await details.evaluate(el=>el.open))await details.locator(':scope > summary').click();
 await expect(page.getByRole('heading',{name:'Other cached sources',exact:true})).toBeVisible();
 await expect(page.locator('#update-issues-list li')).toHaveCount(1);
 await expect(page.locator('#update-issues-list')).toContainText('Configured bundle');
 await expect(page.locator('#unknown-source-issues-list li')).toHaveCount(2);
 await expect(page.locator('#unknown-source-issues-list')).toContainText('Historical branch');
 await expect(page.locator('#unknown-source-issues-list')).toContainText('Tracked source changes are preserved.');
 await expect(page.locator('#available-updates-list')).toContainText('Indirect dependency');
 await expect(page.getByRole('button',{name:'Update Amplifier',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Show all 5 sources',exact:true})).toHaveAttribute('aria-expanded','false');
 await page.getByRole('heading',{name:'Other cached sources',exact:true}).locator('..').scrollIntoViewIfNeeded();
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-cache-usage-desktop.png'});
 await page.getByRole('button',{name:'Check for updates',exact:true}).click();
 await expect.poll(async()=>await (await page.request.get(url+'/fixture')).json()).toEqual(['check']);
 await page.getByRole('button',{name:'Update Amplifier',exact:true}).click();
 await expect.poll(async()=>await (await page.request.get(url+'/fixture')).json()).toEqual(['check','install']);
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('heading',{name:'Other cached sources',exact:true}).locator('..').scrollIntoViewIfNeeded();
 assert.ok(await page.locator('.a-dialog').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.screenshot({animations:'disabled',path:'/tmp/amplifier-cache-usage-mobile.png'});
 await page.getByRole('button',{name:'Show all 5 sources',exact:true}).click();
 await expect(page.locator('#update-sources-list li')).toHaveCount(5);
 await expect(page.locator('#update-sources-list')).toContainText('Pinned dependency');
 assert.deepEqual(errors,[]);
 console.log('Cache usage UI passed: configured/unknown grouping, visible failures with inventory collapsed, indirect updates installable, synthetic check/install controls, preserved pins, desktop and mobile layout.');
}catch(error){failure=error;throw error}
finally{
 const cleanupErrors=[];
 try{await browser?.close()}catch(error){cleanupErrors.push(error)}
 try{
  if(fixture.exitCode===null&&fixture.signalCode===null){
   const exited=once(fixture,'exit');
   let timer;
   fixture.kill('SIGTERM');
   try{
    await Promise.race([exited,new Promise((_,reject)=>{
     timer=setTimeout(()=>{
      fixture.kill('SIGKILL');
      reject(Error('Cache usage fixture did not finish graceful shutdown within 5 seconds'));
     },5000);
    })]);
   }finally{clearTimeout(timer)}
  }
  assert.equal(fixture.exitCode,0,'Cache usage fixture must finish its cleanup');
  assert.equal(fixture.signalCode,null,'Cache usage fixture must exit normally');
 }catch(error){cleanupErrors.push(error)}
 if(cleanupErrors.length)throw new AggregateError([...(failure?[failure]:[]),...cleanupErrors],'Cache usage cleanup failed');
}
