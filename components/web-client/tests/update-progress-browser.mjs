import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const python=fixturePython(process.env.UNIFIED_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)));
const output=process.env.UNIFIED_BROWSER_EVIDENCE||(process.env.AMPLIFIER_TEST_ARTIFACTS+'/update-progress');if(!process.env.UNIFIED_BROWSER_EVIDENCE&&!process.env.AMPLIFIER_TEST_ARTIFACTS)throw Error('Owned artifact directory required');
const fixture=spawn(python,['-u',fileURLToPath(new URL('../../tests/fixtures/update_progress_server.py',import.meta.url))]);
let browser,page,log='';const result={status:'pending',syntheticTransitions:true,realHTTPAndSSE:true,paidCalls:0};
fixture.stderr.on('data',data=>log+=data);
try{
 const headers={Authorization:'Bearer fixture-browser-control-token'};
 const boot=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error(log||'Fixture timeout')),60000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+log))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 assert.equal(boot.package,process.env.AMPLIFIER_TEST_PACKAGE_ROOT?resolve(process.env.AMPLIFIER_TEST_PACKAGE_ROOT,'__init__.py'):fileURLToPath(new URL('../../amplifier_web/__init__.py',import.meta.url)));
 const base=boot.url;
 browser=await chromium.launch({channel:'chromium',headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:headers});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const overview=page.locator('[data-part="application-update"]').first(),updates=page.locator('[data-part="updates"]'),advanced=page.locator('[data-part="ecosystem-update-details"]');
 await expect(overview).toContainText('Finishing your update');
 await expect(updates.locator('[data-part="update-batch"]')).not.toBeVisible();
 await expect(updates.locator('[data-part="update-support"]')).not.toBeVisible();
 await advanced.locator('summary').first().click();
 await updates.locator('[data-part="update-completed-batches"]>summary').click();
 await expect(updates).toContainText('24 of 37 completed');
 await expect(updates).toContainText('Current batch: amplifier-module-hooks-approval');
 await expect(updates).toContainText('Included components installed at');
 await expect(updates).not.toContainText('Other components installed at');
 await page.screenshot({path:output+'-desktop.png'});
 await page.setViewportSize({width:320,height:844});
 for(const button of await updates.locator('[data-part="update-support"] button').all()){
  // Resize and font loading can reflow between separate boundingBox calls.
  // Read both rectangles in one layout frame and allow that reflow to settle.
  await expect.poll(()=>button.evaluate(el=>{
   const svg=el.querySelector('svg').getBoundingClientRect(),label=el.querySelector('span').getBoundingClientRect();
   return Math.abs(svg.y+svg.height/2-label.y-label.height/2);
  }),{message:'diagnostic icon and label must stay inline'}).toBeLessThan(2);
 }

 await expect.poll(()=>page.locator('.a-dialog').evaluate(el=>el.scrollWidth-el.clientWidth),
  {message:'mobile dialog must fit after responsive reflow'}).toBeLessThanOrEqual(1);
 await page.screenshot({path:output+'-mobile.png'});
 const stage=async value=>{const response=await page.request.post(base+'/fixture/update-progress',{data:{stage:value}});assert.ok(response.ok(),await response.text())};
 // A failed chat control belongs to that command, not the host updater. Keep
 // genuine update failures, blockers and saved preferences independently visible.
 const readObservations=async()=>{const response=await page.request.get(base+'/fixture/update-observations');assert.ok(response.ok());return response.json()};
 const beforeControlError=await readObservations();
 await stage('unrelated-control');
 await expect(overview).toContainText('Waiting for 3 chats to finish');
 await expect(page.locator('[data-part="settings-experience"]')).not.toContainText('Unsupported runtime control');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().management?.error)).toBe('ValueError: Unsupported runtime control: goal.get');
 await updates.locator('[data-part="update-blockers"]>summary').click();
 await expect(updates.locator('[data-part="update-blockers"] li')).toHaveCount(3);
 for(let index=1;index<=3;index++)await expect(updates.locator('[data-part="update-blockers"]')).toContainText(`Running fixture chat ${index}`);
 assert.deepEqual(await readObservations(),beforeControlError);
 await stage('unrelated-control-update-failed');
 await expect(overview).toContainText('Updates need attention');
 await expect(updates.getByRole('alert').filter({hasText:'Synthetic restart failed; the update remains fenced.'}).first()).toBeVisible();
 await expect(page.locator('[data-part="settings-experience"]')).not.toContainText('Unsupported runtime control');
 assert.deepEqual(await readObservations(),beforeControlError);
 await stage('unrelated-control-reset');
 result.errorOwnership={unrelatedControlRetained:true,unrelatedAlertExcluded:true,threeChatBlockers:true,updateFailureVisible:true,preferencesUnchanged:true,noUpdateEffects:true};
 await stage('initial');await expect(overview).toContainText('Getting Amplifier ready');await expect(overview).not.toContainText('Finishing your update');
 await stage('regular');await expect(overview).toContainText('Finishing your update');
 await stage('compatibility');await expect(updates).toContainText('12 of 37 completed');
 await stage('waiting');await expect(overview).toContainText('Waiting for your work to finish');
 await expect(updates).not.toContainText('12 of 37 completed');
 await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
 await updates.locator('[data-part="update-blockers"]>summary').click();
 await expect(updates.locator('[data-part="update-blockers"]')).toContainText('A voice call is active');
 await stage('activating');
 const check=page.locator('[data-action="updates.check"]'),install=page.locator('[data-action="updates.install"]');
 await expect(install).toHaveCount(0);
 await expect(check).toBeDisabled();await expect(check).toContainText('Finishing update');
 await expect(overview).toContainText('Finishing your update');
 await expect(overview).toContainText('continues automatically');
 const spinner=overview.locator('.a-progress-spinner');await expect(spinner).toBeVisible();
 await expect.poll(()=>spinner.evaluate(el=>getComputedStyle(el).animationName)).toBe('ampSpin');
 await page.emulateMedia({reducedMotion:'reduce'});
 await expect.poll(()=>spinner.evaluate(el=>getComputedStyle(el).animationName)).toBe('none');
 await expect(overview).toContainText('Finishing your update');
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.screenshot({path:output+'-activation-mobile.png'});
 await page.setViewportSize({width:1280,height:950});
 await page.screenshot({path:output+'-activation-desktop.png'});
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await overview.isVisible())await openSettingsPage(page,'updates');
 await expect(install).toHaveCount(0);await expect(overview).toContainText('Finishing your update');
 await stage('failed');await expect(overview).toContainText('Updates need attention');
 await expect(updates).not.toContainText('Other components installed at');
 await expect(install).toBeEnabled();
 await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
 await stage('source-mismatch');
 await expect(overview).toContainText('Updates need attention');
 await expect(updates).toContainText('A component changed to a different source during update preparation.');
 await expect(updates).toContainText('Amplifier kept the current components active.');
 await expect(updates).toContainText('amplifier-foundation');
 await page.screenshot({path:output+'-source-mismatch.png'});
 // A later component failure cannot erase a verified app result. Reload keeps
 // both outcomes; a stale install identity or unconfirmed restart cannot claim it.
 await stage('application-confirmed');
 await expect(overview).toContainText('Application updated successfully');
 const confirmedAppResult=await page.evaluate(()=>window.amplifier.getState().updates.applicationResult);
 const confirmedAppText=await overview.locator('[data-part="confirmed-application-result"]').innerText();
 assert.equal(confirmedAppResult.phase,'restart-ack');
 await stage('component-source-changed');
 // The exact current batch owns the heading; older unresolved episodes stay visible.
 await expect(overview.getByRole('heading',{level:4})).toHaveText('Some components were not updated');
 await expect(overview.locator('#update-action-status')).toContainText('Check for updates again before retrying');
 await expect(overview.locator('[data-part="confirmed-application-result"]')).toHaveText(confirmedAppText);
 const changedFailure=await page.evaluate(()=>{
  const {updates,attention}=window.amplifier.getState();
  return {lastFailure:updates.diagnostics.lastFailure,batch:updates.diagnostics.batch,
   applicationResult:updates.applicationResult,actionRequired:attention.items.some(item=>item.actionRequired&&item.firstFailure?.id===updates.diagnostics.lastFailure.id)};
 });
 assert.equal(changedFailure.lastFailure.reason,'runtime-source-changed');
 assert.equal(changedFailure.lastFailure.status,'failed');
 assert.equal(changedFailure.lastFailure.phase,'ecosystem-validation');
 assert.equal(changedFailure.lastFailure.attemptId,changedFailure.batch.attemptId);
 assert.deepEqual(changedFailure.batch.components,['amplifier-foundation']);
 assert.equal(changedFailure.actionRequired,true);
 assert.deepEqual(changedFailure.applicationResult,confirmedAppResult);
 await expect(advanced.locator('.a-update-failure[role="alert"]')).toContainText('Worker sources changed while checking. Let source changes finish, then check for updates again.');
 await expect(check).toBeEnabled();
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await overview.isVisible())await openSettingsPage(page,'updates');
 await expect(overview.locator('[data-part="confirmed-application-result"]')).toHaveText(confirmedAppText);
 await expect(overview.getByRole('heading',{level:4})).toHaveText('Some components were not updated');
 await expect(overview.locator('#update-action-status')).toContainText('Affected batch: amplifier-foundation');
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().updates.applicationResult),confirmedAppResult);
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().updates.diagnostics.lastFailure),changedFailure.lastFailure);
 await page.setViewportSize({width:320,height:844});
 await expect.poll(()=>page.locator('.a-dialog').evaluate(el=>el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(1);
 await page.screenshot({path:output+'-partial-success-mobile.png'});
 // A declaration constraint is not evidence of local edits or an override.
 // Publish it over the same HTTP/SSE path; retain the exact confirmed app receipt.
 const beforeConstraint=await readObservations();
 const constraintGuidance='A package source declaration could not be reconciled with the selected component. The current components remain active. This does not establish local edits. Share the update diagnostics for review; do not remove pins or caches to bypass this check.';
 const terminalDetail='Component update preparation failed. Current components remain active.';
 const appOnlyDetail='The latest published application release is installed.';
 const constraintState=()=>page.evaluate(()=>{
  const updates=window.amplifier.getState().updates;
  return {phase:updates.phase,detail:updates.detail,error:updates.error,
   applicationDetail:updates.application.detail,applicationResult:updates.applicationResult,
   attemptId:updates.diagnostics.attemptId,lastFailure:updates.diagnostics.lastFailure};
 });
 const assertConstraint=async()=>{
  await expect.poll(async()=>(await constraintState()).lastFailure?.reason).toBe('runtime-source-constraint');
  await expect(overview.getByRole('heading',{level:4})).toHaveText('Some components were not updated');
  await expect(overview.locator('[data-part="confirmed-application-result"]')).toHaveText(confirmedAppText);
  await expect(overview.locator('#update-action-status')).toContainText('Review the component issue below before trying again');
  await expect(overview).not.toContainText('You’re up to date');
  await expect(check).toBeEnabled();
  await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
  const issue=advanced.locator('.a-update-failure[role="alert"]');
  await expect(issue).toBeVisible();
  await expect(issue).toContainText('amplifier-foundation');
  await expect(issue).toContainText('ProtectedRuntimeSource');
  await expect(issue).toContainText(constraintGuidance);
  await expect(updates.getByRole('alert').filter({hasText:constraintGuidance}).first()).toBeVisible();
  await expect(advanced.getByText(appOnlyDetail,{exact:true})).toBeVisible();
  await expect(updates).not.toContainText('Local source changes or an explicit dependency override were preserved.');
  await expect(updates).not.toContainText('The latest published application release and checked components are installed.');
  await expect(updates).not.toContainText('Preparing dependencies in a separate runtime');
  const state=await constraintState();
  assert.equal(state.phase,'error');
  assert.equal(state.detail,terminalDetail);
  assert.equal(state.error,constraintGuidance);
  assert.equal(state.applicationDetail,appOnlyDetail);
  assert.deepEqual(state.applicationResult,confirmedAppResult);
  assert.equal(state.lastFailure.attemptId,state.attemptId);
  assert.equal(state.lastFailure.phase,'ecosystem-validation');
  assert.equal(state.lastFailure.status,'failed');
  assert.equal(state.lastFailure.errorType,'ProtectedRuntimeSource');
  assert.equal(state.lastFailure.package,'amplifier-foundation');
  return state;
 };
 await stage('component-source-constraint');
 const constraintBeforeReload=await assertConstraint();
 await page.setViewportSize({width:1280,height:950});
 await overview.scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'-source-constraint-desktop.png'});
 const diagnosticPanel=updates.locator('[data-part="update-diagnostics"]');
 await diagnosticPanel.getByRole('button',{name:'View update details',exact:true}).click();
 const receipt=JSON.parse(await updates.locator('#update-diagnostic-receipt').inputValue());
 assert.equal(receipt.attemptId,constraintBeforeReload.attemptId);
 assert.deepEqual(receipt.lastFailure,constraintBeforeReload.lastFailure);
 assert.ok(receipt.events.length<=50);
 const constraintEvents=receipt.events.filter(event=>event.attemptId===receipt.attemptId);
 assert.deepEqual(constraintEvents.map(event=>[event.phase,event.status]),
  [['ecosystem-validation','failed'],['ecosystem-stage','failed']]);
 assert.deepEqual(constraintEvents[0],receipt.lastFailure);
 // Keep only this attempt, not unrelated historical diagnostics, in the evidence.
 result.constraintFailure={beforeReload:constraintBeforeReload,
  diagnosticReceipt:{attemptId:receipt.attemptId,lastFailure:receipt.lastFailure,events:constraintEvents}};
 await page.reload();await page.waitForSelector('#amp-one');
 if(!await overview.isVisible())await openSettingsPage(page,'updates');
 // Reload restores Settings, not the local Advanced details disclosure.
 if(!await advanced.evaluate(element=>element.open))await advanced.locator('summary').first().click();
 const constraintAfterReload=await assertConstraint();
 assert.deepEqual(constraintAfterReload,constraintBeforeReload);
 await page.setViewportSize({width:320,height:844});
 await expect.poll(()=>page.locator('.a-dialog').evaluate(el=>el.scrollWidth-el.clientWidth),
  {message:'constraint guidance must fit the narrow dialog'}).toBeLessThanOrEqual(1);
 await advanced.locator('.a-update-failure[role="alert"]').scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'-source-constraint-mobile.png'});
 assert.deepEqual(await readObservations(),beforeConstraint);
 result.constraintFailure.afterReload=constraintAfterReload;
 result.constraintFailure.noUpdateEffects=true;
 await stage('app-result-stale');
 await expect(overview).not.toContainText('Application updated successfully');
 await expect(overview.getByRole('heading',{level:4})).toHaveText('Updates need attention');
 await stage('application-confirmed');await stage('unconfirmed-restart');
 await expect(overview).toContainText('Waiting for restart confirmation');
 await expect(overview).not.toContainText('Application updated successfully');
 await stage('clear-app-result');await page.setViewportSize({width:1280,height:950});
 // These synthetic completion/check stages do not establish matching recovery
 // of the failure recorded after this batch began. Do not infer it from phase.
 await stage('adopting');await expect(overview.getByRole('heading',{level:4})).toHaveText('Component update needs attention');await expect(updates).toContainText('2 conversation workers are finishing on earlier components.');await expect(check).toBeEnabled();
 const adoption=await page.evaluate(()=>{const u=window.amplifier.getState().updates;return {workers:u.adoption,phase:u.phase,stage:u.sequence.stage,restarting:!!u.pendingRestart}});
 assert.deepEqual(adoption,{workers:{pendingWorkers:2,activeWorkers:2},phase:'installed',stage:'complete',restarting:false});
 await stage('installed');await expect(updates).toContainText('Other components installed at');
 await expect(updates).toContainText('Included components installed at');
 await expect(overview.getByRole('heading',{level:4})).toHaveText('Component update needs attention');
 await expect(overview.locator('#update-action-status')).toContainText('runtime-source-constraint');
 await expect(overview).not.toContainText('You’re up to date');
 await expect(check).toBeEnabled();await expect(install).toHaveCount(0);
 await expect(overview.locator('.a-progress-spinner')).toHaveCount(0);
 await page.reload();await page.waitForSelector('#amp-one');
 // Settings is a persisted client view. Do not click the covered navigation
 // toggle when reload has already restored the open Updates dialog.
 if(!await overview.isVisible())await openSettingsPage(page,'updates');
 await expect(updates).toContainText('Other components installed at');
 await page.setViewportSize({width:1280,height:950});
 await openSettingsPage(page,'appearance');
 await page.getByLabel('Color mode',{exact:true}).selectOption('dark');
 await openSettingsPage(page,'updates');
 await stage('waiting');
 await expect(overview).toContainText('Waiting for your work to finish');
 await expect(updates.locator('[data-part="update-support"]')).not.toBeVisible();
 await page.screenshot({path:output+'-waiting-dark.png'});
 await updates.locator('[data-part="update-blockers"]>summary').click();
 await expect(updates.locator('[data-part="update-blockers"]')).toContainText('A voice call is active');
 await advanced.locator('summary').first().click();
 await updates.locator('[data-part="update-support"]').scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'-advanced-dark.png'});
 await stage('activating');
 await updates.getByRole('button',{name:'Return to chat',exact:true}).click();
 await expect(page.locator('.a-settings-experience')).not.toBeVisible();
 await openSettingsPage(page,'updates');await expect(overview).toContainText('Finishing your update');
 // The release card follows the target through discovery, restart and component
 // completion; reading its ordinary changes does not acknowledge guidance.
 await stage('summary-before');
 const summary=updates.locator('[data-part="update-release-summary"]');
 await expect(summary).toBeVisible();await expect(summary).toContainText('What’s new in 0.20.206');
 await expect(summary).toContainText('See what an update includes before installing.');
 assert.equal(await summary.evaluate(el=>!!el.closest('details')),false);
 assert.equal(await summary.evaluate(el=>!!(el.compareDocumentPosition(document.querySelector('[data-part="update-controls"]'))&Node.DOCUMENT_POSITION_FOLLOWING)),true);
 const noticeRead=()=>page.evaluate(()=>window.amplifier.getState().attention?.items?.find(item=>item.id==='release-notice:0.20.206:summary-guidance')?.read);
 await expect.poll(noticeRead).toBe(false);
 await page.screenshot({path:output+'-release-before.png'});
 await stage('summary-queued');await expect(summary).toContainText('Included in this update · 0.20.206');
 await expect(summary).not.toContainText('Later release');await expect(overview).toContainText('Latest v0.20.207');
 await page.reload();await openSettingsPage(page,'updates');await expect(summary).toContainText('Included in this update · 0.20.206');
 await stage('summary-restarting');await expect(summary).toContainText('Included in this update · 0.20.206');
 await expect(summary).not.toContainText('Now installed');
 await page.screenshot({path:output+'-release-during.png'});
 await stage('summary-after');await expect(summary).toContainText('Now installed · 0.20.206');
 await expect(overview).toContainText('Finishing test runner update');await expect(overview).toContainText('Application updated successfully');
 await page.reload();await openSettingsPage(page,'updates');await expect(summary).toContainText('Now installed · 0.20.206');
 await expect.poll(noticeRead).toBe(false);
 await page.screenshot({path:output+'-release-after.png'});
 await page.setViewportSize({width:320,height:844});
 await expect.poll(()=>page.locator('.a-dialog').evaluate(el=>el.scrollWidth-el.clientWidth)).toBeLessThanOrEqual(1);
 await summary.scrollIntoViewIfNeeded();await page.screenshot({path:output+'-release-phone.png'});
 // High-impact acknowledgment still requires viewing that separate content.
 await updates.locator('[data-part="important-release-changes"]>summary').click();
 await updates.getByText('Example important guidance',{exact:true}).first().scrollIntoViewIfNeeded();
 await expect.poll(noticeRead).toBe(true);
 await stage('summary-missing');await expect(summary).toContainText('Included in this update · 0.20.204');
 await expect(summary).toContainText('Release notes for this version are unavailable');
 await expect(summary.getByRole('link')).toHaveAttribute('href','https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.204');
 await expect(summary).not.toContainText('Clearer update summaries');
 await stage('summary-reset');await page.setViewportSize({width:1280,height:950});
 result.releaseSummary={before:true,exactQueuedTarget:true,newerDiscovery:true,uncertainRestart:true,confirmedWithRunnerWait:true,reload:true,missingNotes:true,phone:true,noticeReadSeparate:true};
 // Ordinary preferences retain an unfamiliar saved interval until explicitly changed.
 await stage('installed');
 const observations=async()=>{const response=await page.request.get(base+'/fixture/update-observations');assert.ok(response.ok());return response.json()};
 assert.deepEqual((await observations()).calls,{check:0,install:0,rollback:0});
 if(!await advanced.getByText('Update preferences',{exact:true}).isVisible())await advanced.locator('summary').first().click();
 await updates.getByText('Update preferences',{exact:true}).click();
 const frequency=updates.getByLabel('Check for updates',{exact:true}),automatic=updates.getByRole('checkbox',{name:'Automatically check for updates',exact:true});
 await expect(frequency).toHaveValue('');await expect(updates.getByText('Your saved schedule checks every 7 hours. It stays in effect until you choose a new interval.',{exact:true})).toBeVisible();
 await automatic.click();await expect.poll(async()=>(await observations()).preferences.autoCheck).toBe(false);await expect(automatic).not.toBeChecked();assert.equal((await observations()).preferences.intervalHours,7);
 await page.reload();await openSettingsPage(page,'updates');if(!await updates.getByText('Update preferences',{exact:true}).isVisible())await advanced.locator('summary').first().click();if(!await frequency.isVisible())await updates.getByText('Update preferences',{exact:true}).click();
 await expect(automatic).not.toBeChecked();await expect(frequency).toHaveValue('');assert.equal((await observations()).preferences.intervalHours,7);
 await frequency.selectOption('24');await expect.poll(async()=>(await observations()).preferences.intervalHours).toBe(24);await automatic.click();
 await expect.poll(async()=>(await observations()).preferences.autoCheck).toBe(true);await expect(automatic).toBeChecked();assert.deepEqual((await observations()).calls,{check:0,install:0,rollback:0});
 await page.reload();await openSettingsPage(page,'updates');if(!await updates.getByText('Update preferences',{exact:true}).isVisible())await advanced.locator('summary').first().click();if(!await frequency.isVisible())await updates.getByText('Update preferences',{exact:true}).click();
 await expect(automatic).toBeChecked();await expect(frequency).toHaveValue('24');
 // Explicit checks carry their own outcome; a successful lookup cannot clear
 // a retained component failure without matching recovery.
 await check.click();await expect(overview.getByRole('heading',{level:4})).toHaveText('Updates need attention');await expect(updates).toContainText('Synthetic update lookup failed. Check again.');assert.equal((await observations()).calls.check,1);
 await check.click();await expect(overview.getByRole('heading',{level:4})).toHaveText('Component update needs attention');await expect(overview.locator('#update-action-status')).toContainText('runtime-source-constraint');await expect(overview).not.toContainText('You’re up to date');await expect(updates.getByText('Synthetic update lookup failed. Check again.',{exact:true})).toHaveCount(0);assert.deepEqual((await observations()).calls,{check:2,install:0,rollback:0});
 // One status projection governs headline, details and retry state, including
 // reload. A check alone left attention above; only the captured covered batch
 // is settled by this fixture's synthetic activation receipt.
 const priorIssues=await page.evaluate(()=>window.amplifier.getState().attention.items.filter(item=>item.page==='updates'&&item.actionRequired).map(item=>item.id));
 assert.ok(priorIssues.length);
 await expect(advanced).toHaveAttribute('open','');
 await stage('covered-retry');
 await expect(overview).toContainText('Updating other components');
 await expect(check).toBeDisabled();
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().attention.items.filter(item=>item.page==='updates'&&item.actionRequired).map(item=>item.id)),priorIssues);
 await page.reload();await openSettingsPage(page,'updates');
 await expect(overview).toContainText('Updating other components');
 await expect(check).toBeDisabled();
 if(!await advanced.evaluate(element=>element.open))await advanced.locator('summary').first().click();
 await expect(updates.locator('[data-part="update-batch"]')).toContainText('and 3 more');
 await expect(updates.locator('[data-part="update-batch"]')).not.toContainText('fixture-component-5');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().updates.diagnostics.batch.components.length),8);
 await stage('covered-activation');
 await expect(overview.getByRole('heading',{level:4})).toHaveText('You’re up to date');
 await expect(check).toBeEnabled();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.items.filter(item=>item.page==='updates'&&item.actionRequired).length)).toBe(0);
 await page.reload();await openSettingsPage(page,'updates');
 await expect(overview.getByRole('heading',{level:4})).toHaveText('You’re up to date');
 await expect(advanced.locator('summary').first()).toHaveText('Advanced details');
 assert.ok(await page.evaluate(()=>window.amplifier.getState().updates.diagnostics.events.some(event=>event.status==='failed')));
 assert.deepEqual((await observations()).calls,{check:2,install:0,rollback:0});
 result.statusProjection={retryKeepsAttention:true,currentOperationOwnsHeading:true,fullScopeRetained:true,boundedBatchPreview:true,coveredActivationSettles:true,reloadConsistent:true,failedHistoryPreserved:true};
 assert.deepEqual(errors,[]);result.status='passed';result.checks={progress:true,componentIdentity:true,completedMilestones:true,partialSuccess:true,partialSuccessReload:true,staleAppResultExcluded:true,unconfirmedRestartExcluded:true,savedPreferences:true,unknownIntervalPreserved:true,explicitCheckFailureRetry:true,noInstallEffects:true,noPrematureActivation:true,waitingDistinct:true,activationBusy:true,duplicateInstallDisabled:true,reducedMotion:true,retryAfterFailure:true,completionRestoresControls:true,failureDistinct:true,reload:true,mobileOverflow:false};result.browserErrors=errors;result.browserVersion=browser.version();
}catch(error){result.status='failed';result.error=String(error);if(page)await page.screenshot({path:output+'-failed.png'});throw error}
finally{try{await browser?.close()}finally{fixture.kill('SIGTERM');await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},10000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});result.fixtureLog=log;await writeFile(output+'.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));}}
