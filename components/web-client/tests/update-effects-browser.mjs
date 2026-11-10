// Real public intent and retention; installer/rollback effects are explicitly synthetic.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/update_effects_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),actions=[],errors=[];
 const servedAssets={},assetReads=[];
 page.on('response',response=>{const url=new URL(response.url());if(url.origin===boot.url&&url.pathname.startsWith('/assets/')&&response.ok())assetReads.push(response.body().then(body=>{servedAssets[url.pathname.slice(1)]=createHash('sha256').update(body).digest('hex')}));});
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST')actions.push(request.postDataJSON())});
 const receipt=async mode=>{const response=mode?await page.request.post(boot.url+'/fixture/update-effects',{data:{mode}}):await page.request.get(boot.url+'/fixture/update-effects');assert.ok(response.ok(),`Fixture ${mode||'read'} returned ${response.status()}: ${await response.text()}`);return response.json()};
 await page.goto(boot.url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep my unsent update draft');const before=await receipt();
 await openSettingsPage(page,'updates');const updates=page.locator('[data-part="updates"]'),overview=page.locator('[data-part="application-update"]').first();
 const identity=overview.locator('.a-update-version-line');
 await expect(identity).toHaveText('Amplifier 0.20.165 · Latest 99.0.0');
 await updates.getByRole('button',{name:'Update Amplifier',exact:true}).click();
 await expect(overview).toContainText('Waiting for 1 chat to finish');const staged=await receipt();assert.equal(staged.calls.length,1);assert.equal(staged.calls[0].application.latest,'99.0.0');assert.equal(staged.generation.active,'original-generation');assert.deepEqual(staged.updates.pendingApp,staged.target);
 await updates.locator('[data-part="update-blockers"]>summary').click();await expect(updates.locator('[data-part="update-blockers"]')).toContainText('Settings test');
 const blockers=updates.locator('[data-part="update-blockers"]');
 for(const [mode,label,code,linked] of [
  ['management-hold','Update waits for global configuration management to settle','management-lock-held',false],
  ['unobservable-hold','Update cannot verify global runtime activity','runtime-observer-unavailable',false],
  ['operation-hold','Conversation configuration operation remains held for recovery','operation-bound-recovery',true]]){
  await receipt(mode);await expect(blockers).toContainText(label);
  await expect(blockers).not.toContainText('Conversation settings are being applied');
  await expect(blockers.getByRole('button',{name:'Settings test',exact:true})).toHaveCount(linked?1:0);
  const response=await page.request.post(boot.url+'/api/actions',{data:{action:'updates.diagnostics',args:{}}});
  assert.ok(response.ok());const report=(await response.json()).result;
  assert.equal(report.activationGate.blocked,true);
  assert.equal(report.activationGate.configurationHolds[0].reasonCode,code);
  assert.ok(!JSON.stringify(report).includes('PRIVATE_'));
  assert.equal((await receipt()).calls.length,1);
  if(process.env.AMPLIFIER_TEST_ARTIFACTS){await mkdir(process.env.AMPLIFIER_TEST_ARTIFACTS,{recursive:true});await page.screenshot({path:process.env.AMPLIFIER_TEST_ARTIFACTS+'/'+mode+'.png'});await writeFile(process.env.AMPLIFIER_TEST_ARTIFACTS+'/'+mode+'.json',JSON.stringify(report,null,2));}
 }
 await receipt('hold-clear');await expect(overview).toContainText('Waiting for 1 chat to finish');
 assert.equal(actions.filter(row=>row.action==='updates.forceRestart').length,0);assert.equal(staged.updates.application.current,'0.20.165');
 await expect(identity).toHaveText('Amplifier 0.20.165 · Latest 99.0.0');await expect(overview).not.toContainText('Latest release installed');
 // A public check refreshes the channel while work is running, without turning
 // that read into an install or losing the exact qualified pending receipt.
 await receipt('newer-release');
 await updates.getByRole('button',{name:'Check for newer release',exact:true}).click();
 await expect(identity).toContainText('Amplifier 0.20.165 · Latest 99.0.1');
 await expect(updates.getByRole('button',{name:'Check for newer release',exact:true})).toBeEnabled();
 const refreshed=await receipt();assert.equal(refreshed.calls.length,1);assert.equal(refreshed.checks.length,1);assert.deepEqual(refreshed.updates.pendingApp,staged.updates.pendingApp);assert.equal(refreshed.generation.active,'original-generation');
 await updates.getByRole('button',{name:'Force restart…',exact:true}).click();
 const confirmation=updates.getByRole('alertdialog',{name:'Confirm update restart'});
 await expect(confirmation).toContainText('prepared Amplifier 99.0.0');
 await expect(confirmation).toContainText('Amplifier 99.0.1 is available');
 await confirmation.getByRole('button',{name:'Cancel',exact:true}).click();
 await expect(confirmation).toHaveCount(0);
 await receipt('offline');await updates.getByRole('button',{name:'Check for newer release',exact:true}).click();
 await expect(updates).toContainText('The prepared update is still available');
 await expect(overview).toContainText('Waiting for 1 chat to finish');
 assert.deepEqual((await receipt()).updates.pendingApp,staged.updates.pendingApp);
 if(process.env.AMPLIFIER_TEST_ARTIFACTS){await mkdir(process.env.AMPLIFIER_TEST_ARTIFACTS,{recursive:true});await page.screenshot({path:process.env.AMPLIFIER_TEST_ARTIFACTS+'/queued-update-offline.png'});}
 await receipt('newer-release');await updates.getByRole('button',{name:'Check for newer release',exact:true}).click();
 await expect(updates).not.toContainText('The prepared update is still available');
 await expect(updates.getByRole('button',{name:'Check for newer release',exact:true})).toBeEnabled();
 await receipt('unknown');await expect(overview).toContainText('Updates need attention');await expect(updates).toContainText('installation needs verification');await expect(updates.locator('[data-action="updates.check"]')).toBeDisabled();await expect(updates.locator('[data-action="updates.install"]')).toHaveCount(0);assert.equal((await receipt()).calls.length,1);
 await expect(identity).toContainText('Amplifier 0.20.165 · Latest 99.0.1');assert.equal((await receipt()).generation.active,'original-generation');
 await receipt('known-failure');await expect(updates.getByRole('button',{name:'Try update again',exact:true})).toBeEnabled();assert.equal((await receipt()).generation.active,'original-generation');
 await expect(identity).toContainText('Amplifier 0.20.165 · Latest 99.0.1');await expect(updates).toContainText('Synthetic staging failed; original generation retained.');
 await updates.getByRole('button',{name:'Try update again',exact:true}).click();await expect(overview).toContainText('Waiting for 1 chat to finish');assert.equal((await receipt()).calls.length,2);
 await receipt('rollback-ready');
 // The only rollback control is within Advanced details; this is supplemental
 // coverage of a public entry, not ordinary-path credit for hidden internals.
 const restore=updates.getByRole('button',{name:'Restore previous components',exact:true});
 if(!await restore.isVisible())await updates.locator('[data-part="ecosystem-update-details"]>summary').click();
 await restore.click();await expect(restore).toHaveCount(0);const after=await receipt();assert.equal(after.generation.active,'previous-generation');assert.deepEqual(after.calls.at(-1),{operation:'rollback',target:'previous-generation'});assert.deepEqual(after.messages,before.originalMessages);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await expect(composer).toHaveDraft('Keep my unsent update draft');assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),before.sessionId);assert.deepEqual(errors,[]);
 assert.equal(actions.filter(row=>['conversation.send','session.warm','updates.forceRestart'].includes(row.action)).length,0);
 await Promise.all(assetReads);assert.ok(Object.keys(servedAssets).length>0);
 if(process.env.AMPLIFIER_TEST_ARTIFACTS)await writeFile(process.env.AMPLIFIER_TEST_ARTIFACTS+'/served-assets.json',JSON.stringify(servedAssets,null,2));
 console.log(JSON.stringify({passed:true,syntheticEffects:true,exactStage:true,unknownPreventsRetry:true,explicitKnownRetry:true,rollbackIntent:true,historyAndDraftRetained:true,hostRestarts:0,limits:['Real installer and generation activation are not exercised.','Rollback target is verified at effect boundary; UI target presentation is not asserted.']}));
}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch{}finally{clearTimeout(timer)}}}}
