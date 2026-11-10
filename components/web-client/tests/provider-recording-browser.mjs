import {openSettingsPage} from './browser-settings.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 ['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],
 {stdio:['ignore','pipe','pipe']});
let browser,log='',fixtureCookie;fixture.stderr.on('data',chunk=>log+=chunk);
try{
 const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);const result=JSON.parse(line);fixtureCookie=result.cookie;resolve(result.port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-detail-token'}}),errors=[];
 page.on('pageerror',error=>{errors.push(error.message);console.error('Page error:',error.message)});
 await page.context().addCookies([{name:'amplifier_unified_session',value:fixtureCookie,url:`http://127.0.0.1:${port}`,httpOnly:true,sameSite:'Strict'}]);
 await page.goto(`http://127.0.0.1:${port}`);
 await page.waitForSelector('#amp-one');
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsExpanded:['diagnostics']}}));
 await openSettingsPage(page,'diagnostics');
 const recording=page.getByLabel('Record provider requests and responses for troubleshooting',{exact:true});
 await expect(recording).not.toBeChecked();
 await expect(page.getByText(/Earlier calls cannot be recovered/)).toBeVisible();
 await recording.check();
 await page.waitForFunction(()=>window.amplifier.getState().diagnostics.config.providerRequests===true);
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations),[]);
 await page.reload();
 // The saved Settings panel can restore after the initial shell state.
 // Wait for that restored overlay before trying to navigate through it.
 await page.locator('.a-settings-experience').waitFor({state:'visible'});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).toBeChecked();
 await page.screenshot({path:'/tmp/unified-provider-recording-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).toBeChecked();
 await recording.uncheck();
 await page.waitForFunction(()=>window.amplifier.getState().diagnostics.config.providerRequests===false);
 await page.reload();
 await page.locator('.a-settings-experience').waitFor({state:'visible'});
 await openSettingsPage(page,'diagnostics');
 await expect(recording).not.toBeChecked();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await recording.scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/unified-provider-recording-mobile.png'});
 // The real paged browser flow must work without the complete event-tree
 // projection that older canonical fixtures called before opening the UI.
 await page.setViewportSize({width:1280,height:1000});
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null,executionExpanded:[]}}));
 const seeded=await page.request.post(`http://127.0.0.1:${port}/api/fixture/control`,{data:{op:'bounded-provider-records',delayCapture:true}});
 assert.equal(seeded.ok(),true,await seeded.text());
 await page.reload();
 await showFixtureConversation(page);
 const live=page.locator('[data-group-id$="@recording-question"]');
 await live.locator('button.a-execution-turn-line').click();
 await expect(live.getByRole('button',{name:'Load earlier steps',exact:true})).toBeVisible();
 await live.getByRole('button',{name:'Load earlier steps',exact:true}).click();
 await expect(live.locator('[data-kind="tool"]')).toHaveCount(105);
 const reads=[];
 page.on('request',r=>{if(r.url().includes('/api/conversation/detail?')&&new URL(r.url()).searchParams.has('field'))reads.push(r.url())});
 const model=live.locator('[data-kind="llm"]').filter({hasText:'bounded-recording'});
 await model.locator('button.a-execution-action-line').click();
 assert.equal(reads.length,0,'Opening model facts must not disclose raw payloads');
 await expect(model).toContainText('This call’s request recording isn’t available here.');
 await expect(model.getByRole('button',{name:/^View request/})).toHaveCount(0);
 const flushed=await page.request.post(`http://127.0.0.1:${port}/api/fixture/control`,{data:{op:'flush-provider-records'}});
 assert.equal(flushed.ok(),true,await flushed.text());
 // No reload, toggle, or new execution event: the open completed model must
 // subscribe again when its accepted canonical recording arrives.
 await expect(model.getByRole('button',{name:/^View request/})).toBeVisible();
 await model.getByRole('button',{name:/^View request/}).click();
 await model.getByRole('button',{name:/^Request Object/}).click();
 await expect(model).toContainText('RECORDED-REQUEST');
 await model.getByRole('button',{name:/^View response/}).click();
 await model.getByRole('button',{name:/^Response Object/}).click();
 await expect(model).toContainText('RECORDED-RESPONSE');
 const fullDownload=async container=>{
  const pending=page.waitForEvent('download');
  await container.getByRole('link',{name:'Download full recorded request (JSON)',exact:true}).click();
  const download=await pending;
  const body=JSON.parse(await readFile(await download.path(),'utf8'));
  assert.equal(body.payload.end,'FULL-RECORDING-END');
  assert.equal(body.capture.truncated,false);
 };
 await fullDownload(model);
 const legacy=live.locator('[data-node-id="llm:legacy"]');
 await legacy.locator('button.a-execution-action-line').click();
 await expect(legacy).toContainText('This call’s request recording isn’t available here.');
 await expect(legacy).toContainText('Recording settings save automatically');
 assert.ok(!(await legacy.innerText()).includes('Save capture settings'));
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 const savedParent=page.locator('.a-work-group').filter({has:page.locator(':scope > button', {hasText:'Saved work pages'})});
 const savedToggle=savedParent.locator(':scope > button');
 await expect(savedToggle).toBeVisible();
 if(await savedToggle.getAttribute('aria-expanded')==='true')await savedToggle.click();
 await expect(page.locator('[data-group-id^="event-window:"]')).toHaveCount(0);
 await legacy.getByRole('button',{name:'Show saved recordings',exact:true}).click();
 await expect(savedToggle).toHaveAttribute('aria-expanded','true');
 await expect(savedParent.locator('[data-group-id^="event-window:"]')).toHaveCount(2);
 const saved=savedParent.locator('[data-group-id^="event-window:"]').filter({hasText:'legacy-recording'});
 await expect(saved).toBeInViewport();
 const request=saved.locator('[data-kind="llm"]').filter({hasText:'Provider request · test · legacy-recording'});
 await request.locator('button.a-execution-action-line').click();
 await expect(request).toContainText('Recorded');
 await expect(request).toContainText(new Date().getFullYear().toString());
 await request.getByRole('button',{name:/^View request/}).click();
 await request.getByRole('button',{name:/^Request Object/}).click();
 await expect(request).toContainText('LEGACY-REQUEST');
 // The same full download must work from an independent saved observation.
 const canonical=saved.locator('[data-kind="llm"]').filter({hasText:'Provider request · test · bounded-recording'});
 await canonical.locator('button.a-execution-action-line').click();
 await fullDownload(canonical);
 await page.setViewportSize({width:390,height:844});
 await savedToggle.click();
 await expect(page.locator('[data-group-id^="event-window:"]')).toHaveCount(0);
 await legacy.getByRole('button',{name:'Show saved recordings',exact:true}).click();
 await expect(savedToggle).toHaveAttribute('aria-expanded','true');
 await expect(saved).toBeInViewport();
 // Runtime diagnostics separately retain a summary-only completion event.
 // Open both valid observations; only the provider attempt has raw bytes.
 const responses=saved.locator('[data-kind="llm"]').filter({hasText:'Provider response · test · legacy-recording'});
 await expect(responses).toHaveCount(2);
 for(const row of await responses.all())await row.locator('button.a-execution-action-line').click();
 await expect(responses.filter({hasText:'This event has no response payload.'})).toHaveCount(1);
 const recordedResponse=responses.filter({has:page.getByRole('button',{name:/^View response/})});
 await expect(recordedResponse).toHaveCount(1);
 const response=saved.locator(`[data-node-id="${await recordedResponse.getAttribute('data-node-id')}"]`);
 await response.getByRole('button',{name:/^View response/}).click();
 await response.getByRole('button',{name:/^Response Object/}).click();
 await expect(response).toContainText('LEGACY-RESPONSE');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/unified-provider-recording-window-mobile.png'});
 assert.deepEqual(errors,[]);
 console.log('Provider recording: autosave, 107 paged live steps, delayed exact capture without execution events, explicit disclosure, full downloads from linked and independent windows, legacy request/response navigation through two collapsed saved pages with timestamps, and narrow viewport passed.');
}finally{
 await browser?.close();fixture.kill();
}
