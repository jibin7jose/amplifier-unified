import {showFixtureConversation} from './browser-chat-pages.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/bundle_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<300;i++){try{if((await fetch('http://127.0.0.1:8963/api/health')).ok)break}catch{}await new Promise(resolve=>setTimeout(resolve,100))}
const browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]}),page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer bundle-fixture-control'}}),errors=[];
page.on('pageerror',error=>errors.push(error.message));
const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const picker=()=>page.getByLabel('Registered root bundles',{exact:true});
const runtimeCalls=()=>page.request.get('http://127.0.0.1:8963/fixture/runtime-calls').then(r=>r.json());
const options=page.getByRole('button',{name:'Model and reasoning settings',exact:true});
const openOptions=async()=>{if(!await page.getByRole('dialog',{name:'Chat options',exact:true}).isVisible())await options.click();await showComposerOption(page,'bundle');await expect(picker()).toBeVisible()};
try{
 await page.goto('http://127.0.0.1:8963');await showFixtureConversation(page);await options.waitFor();const source=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await openOptions();
 await expect.poll(()=>picker().locator('option').evaluateAll(rows=>rows.map(row=>row.value))).toEqual(['anchors','anchors-amp-dev','work','work-amp-dev','anchors-work','broken']);
 assert.equal(await picker().locator('option[value="work-amp-dev"]').innerText(),'Work · Amplifier development');
 // The bundled picker saves the next-message choice without applying it.
 assert.deepEqual(await runtimeCalls(),[]);
 await picker().selectOption('work-amp-dev');
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.pendingBundleSelection?.bundle==='work-amp-dev');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).bundle),'anchors');
 await page.reload();await options.waitFor();
 await openOptions();await expect(picker().locator('option:checked')).toHaveText('Work · Amplifier development');
 assert.ok(await page.getByText('Keep my original history.',{exact:true}).isVisible());
 assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).bundle),'anchors');
 assert.deepEqual(await runtimeCalls(),[],'Saving and reloading a choice must not prepare a runtime');
 // Cancel the saved choice through the same UI before exercising the explicit
 // immediate public action. Genuine next-input application has its own native probe.
 await picker().selectOption('anchors');
 await page.waitForFunction(()=>!window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.pendingBundleSelection);
 assert.deepEqual(await runtimeCalls(),[]);
 await action('bundle.switch',{sessionId:source,bundle:'work-amp-dev'});
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.bundle==='work-amp-dev');
 await page.reload();await options.waitFor();await openOptions();
 await expect(picker().locator('option:checked')).toHaveText('Work · Amplifier development');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).bundle),'work-amp-dev');
 if(await picker().isVisible())await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await action('canvas.show',{kind:'markdown',title:'Preserved artifact',content:'# Keep this artifact'});await action('canvas.close',{});await openOptions();await action('bundle.switch',{sessionId:source,bundle:'work'});
 await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.bundle==='work');assert.ok(await page.getByText('Keep my original history.',{exact:true}).isVisible());
 await action('bundle.switch',{sessionId:source,bundle:'anchors'});await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.bundle==='anchors');
 await action('bundle.switch',{sessionId:source,bundle:'broken'});await page.getByText('candidate failed',{exact:false}).first().waitFor();assert.equal(await page.evaluate(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId).bundle),'anchors');
 // Preview/fork remain shared actions and advanced settings, outside the compact picker.
 await action('bundle.preview',{sessionId:source,bundle:'anchors-work'});await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.bundlePreview?.bundle==='anchors-work');
 await action('bundle.fork',{sessionId:source,bundle:'anchors-work'});await page.waitForFunction(source=>window.amplifier.getState().selectedSessionId!==source,source);assert.equal(await page.evaluate(source=>window.amplifier.getState().sessions.find(s=>s.id===source).bundle,source),'anchors');assert.ok(await page.getByText('Keep my original history.',{exact:true}).isVisible());assert.ok(await page.evaluate(()=>window.amplifier.getState().canvasArtifacts.some(a=>a.sessionId===window.amplifier.getState().selectedSessionId&&a.title==='Preserved artifact')));
 await action('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['defaults']}});await page.locator('#default-bundle option[value=work]').waitFor({state:'attached'});await page.locator('#default-bundle').selectOption('work');await page.waitForFunction(()=>window.amplifier.getState().bundleDefaults.app==='work');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const workspaceId=await page.evaluate(()=>window.amplifier.getState().selectedWorkspaceId);
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspaceId,workWorkspaceTab:'details'}});
 const workspaceDefault=page.getByRole('region',{name:'Workspace bundle default',exact:true});
 await workspaceDefault.getByText(/^New chats use /).waitFor();
 await workspaceDefault.locator('select').selectOption('work-amp-dev');await page.waitForFunction(()=>window.amplifier.getState().bundleDefaults.effective==='work-amp-dev');
 await workspaceDefault.getByRole('button',{name:'Use inherited workspace default',exact:true}).click();await page.waitForFunction(()=>window.amplifier.getState().bundleDefaults.effective==='work');
 await action('view.update',{patch:{workSurface:'chat'}});await action('session.draft',{});assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),null);await action('session.create',{});await page.waitForFunction(()=>window.amplifier.getState().sessions.find(s=>s.id===window.amplifier.getState().selectedSessionId)?.bundle==='work');await page.reload();await options.waitFor();await openOptions();await expect(picker().locator('option:checked')).toHaveText('Work');await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await openOptions();await page.getByRole('region',{name:'Choose conversation bundle'}).waitFor();assert.ok(await page.evaluate(()=>{const menu=document.querySelector('.a-compact-popover').getBoundingClientRect(),chat=document.querySelector('.a-conversation').getBoundingClientRect();return menu.left>=0&&menu.right<=innerWidth&&menu.top>=0&&menu.bottom<=innerHeight}));await page.screenshot({path:process.env.AMPLIFIER_BUNDLE_SCREENSHOT||'/tmp/amplifier-bundle-mobile.png'});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);console.log('Bundle browser passed: passive next-message choice/reload/cancellation, optional preview, explicit immediate switch, rollback, fork/history/artifact preservation, scoped defaults, reload and narrow layout.');
}finally{await browser.close();fixture.kill()}
