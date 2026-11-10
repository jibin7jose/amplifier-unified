import {showFixtureConversation} from './browser-chat-pages.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect as playwrightExpect} from '@playwright/test';
const expect=playwrightExpect.configure({timeout:15000});
// Publishing is a chat-owned Canvas destination, not a global Settings page.
async function openPublishing(page){
 await page.waitForFunction(()=>window.amplifier?.getState()?.selectedSessionId);
 const canvas=page.locator('#workspace-canvas');
 if(!await canvas.isVisible())await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 const overview=canvas.getByRole('tab',{name:'Chat overview',exact:true});
 if(await overview.getAttribute('aria-selected')!=='true')await overview.click();
 const delivery=canvas.locator('[data-overview-section="publishing"]');
 if(!await delivery.evaluate(el=>el.open))await delivery.locator(':scope > summary').click();
 await expect(canvas.getByRole('region',{name:'Managed publishing'})).toBeVisible();
}

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/publishing_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Publishing fixture'});
let browser,failure,cleanup,result;
try{
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.publishingFixtureCleanup)cleanup=row.publishingFixtureCleanup;if(row.url){clearTimeout(timer);resolve(row)}}catch{}});fixture.once('error',error=>{clearTimeout(timer);reject(error)});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(ready.url);await showFixtureConversation(page);await openPublishing(page);
 const panel=page.getByRole('region',{name:'Managed publishing'}),release=panel.getByLabel('Selected release',{exact:true}),status=panel.getByLabel('Site status',{exact:true});
 const capture=panel.getByRole('button',{name:'Capture release',exact:true});
 const list=(target)=>page.request.get(ready.url+'/fixture'+(target?'?target='+encodeURIComponent(target):'')).then(response=>response.json());
 const content=async url=>{const response=await page.request.get(url);assert.equal(response.status(),200);return response.text()};
 const reviewed=async note=>{await panel.getByLabel('Review note for this exact release').fill(note);await panel.getByRole('button',{name:'Record review',exact:true}).click();await expect(release).toContainText('Reviewed: '+note)};
 await panel.getByLabel('Site ID',{exact:true}).fill('fixture-site');await panel.getByLabel('Built output folder').fill('dist');
 await capture.evaluate(button=>{button.click();button.click()});await expect(release).toBeVisible();
 let snapshot=await list();assert.equal(snapshot.publishing.releases.length,1);const first=snapshot.publishing.releases[0];assert.match(first.id,/^[a-f0-9]{64}$/);
 await expect(release).toContainText(first.manifestDigest);await expect(panel.getByRole('button',{name:'Deploy reviewed release'})).toBeDisabled();
 await panel.getByRole('button',{name:'Start preview',exact:true}).click();const preview=panel.getByRole('link',{name:'Open exact release preview'});await expect(preview).toBeVisible();assert.match(await content(await preview.getAttribute('href')),/First release/);
 await reviewed('Verified the first release preview.');
 // Drop a real deployment response after the server commits. Retry must retain
 // the exact request and resolve its receipt, without another revision change.
 let dropped=false;
 await page.route('**/api/actions*',async route=>{const body=route.request().postDataJSON();if(body?.action==='publishing.deploy'&&!dropped){dropped=true;await route.fetch();await route.abort('failed')}else await route.continue()});
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();const pending=panel.getByLabel('Unconfirmed publishing request');await expect(pending).toContainText('Request outcome unconfirmed');await expect(capture).toBeDisabled();
 const afterLost=await list(),pendingRequest=afterLost.calls.filter(row=>row.action==='publishing.deploy').at(-1).args;
 assert.equal(afterLost.publishing.sites[0].status,'running');
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();await openPublishing(page);
 // On reopen only a read runs. An explicit retry keeps the submitted identity.
 await expect(pending).toBeVisible();await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(pending).toHaveCount(0);await expect(status).toContainText('running');
 snapshot=await list();const deploys=snapshot.calls.filter(row=>row.action==='publishing.deploy');assert.equal(deploys.length,2);assert.deepEqual(deploys[1].args,pendingRequest);assert.equal(snapshot.publishing.sites[0].revision,afterLost.publishing.sites[0].revision);
 await page.unroute('**/api/actions*');
 let siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/First release/);
 await page.request.post(ready.url+'/fixture/change');await panel.getByLabel('Site ID',{exact:true}).fill('fixture-site');await panel.getByLabel('Built output folder').fill('dist');await capture.click();await expect(release).not.toContainText(first.id);await reviewed('Verified the second release files.');
 snapshot=await list();const second=snapshot.publishing.releases.find(row=>row.id!==first.id),beforeStale=snapshot.publishing.sites[0];
 await page.request.post(ready.url+'/api/actions',{data:{action:'publishing.stop',args:{sessionId:ready.sessionId,targetId:'loopback',siteId:'fixture-site',expectedRevision:beforeStale.revision,requestId:crypto.randomUUID()}}});
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(panel.getByRole('alert')).toContainText(/revision|stale|changed/i);await expect(status).toContainText('stopped');await expect(pending).toHaveCount(0);
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(status).toContainText(second.id);siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/Second release/);
 await panel.getByRole('button',{name:'Read site logs'}).click();await expect(panel.getByLabel('Publishing inspection')).toBeVisible();
 await panel.getByRole('button',{name:'Roll back to previous release'}).click();await expect(status).toContainText('Deployed release'+first.id);siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/First release/);
 await panel.getByRole('button',{name:'Check site status'}).click();await expect(panel.getByLabel('Publishing inspection')).toContainText('running');
 // A durable unknown result is different from a missing response: acknowledge
 // it without replay, then explicitly stop the fenced site before resuming.
 await page.request.post(ready.url+'/fixture/fail-deploy');
 // HTTP 409 cannot override a durable uncertain disposition. Exercise both
 // code-only errors and retained running/unknown receipt metadata through the
 // real request helper, while the underlying failed write stays unknown.
 let uncertainErrors=0;
 await page.route('**/api/actions*',async route=>{const body=route.request().postDataJSON();if(body?.action==='publishing.deploy'&&uncertainErrors<4){const response=await route.fetch(),payload=await response.json(),receipt=payload.receipt||payload.result;assert.equal(receipt.state,'unknown');const attempt=uncertainErrors++;await route.fulfill({status:409,json:{accepted:false,code:attempt===0?'unknown_outcome':'invalid_response',error:'The retained publishing outcome is uncertain.',...(attempt>=2?{receipt:{...receipt,state:attempt===2?'running':'unknown'}}:{})}})}else await route.continue()});
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(pending).toContainText('Request outcome unconfirmed');await expect(capture).toBeDisabled();
 const uncertainBefore=await list(),uncertainArgs=uncertainBefore.calls.filter(row=>row.action==='publishing.deploy').at(-1).args;
 await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(panel.getByRole('alert')).toContainText('Keep this request unchanged');await expect(pending).toContainText(uncertainArgs.requestId);await expect(capture).toBeDisabled();
 await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(panel.getByRole('alert')).toContainText('Keep this request unchanged');await expect(pending).toBeVisible();await expect(pending.getByRole('button',{name:'Acknowledge unknown outcome'})).toHaveCount(0);
 await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(pending).toContainText('Recorded unknown outcome');await expect(capture).toBeDisabled();
 snapshot=await list();assert.equal(uncertainErrors,4);assert.ok(snapshot.calls.filter(row=>row.action==='publishing.deploy').slice(-4).every(row=>JSON.stringify(row.args)===JSON.stringify(uncertainArgs)));assert.equal(snapshot.publishing.sites[0].revision,uncertainBefore.publishing.sites[0].revision);
 await page.unroute('**/api/actions*');
 await panel.getByRole('button',{name:'Inspect publishing',exact:true}).click();await expect(pending).toContainText('Recorded unknown outcome');
 const effectCalls=rows=>rows.filter(row=>!['publishing.target.list','publishing.list','publishing.status','publishing.logs'].includes(row.action));
 snapshot=await list();const unknownCount=snapshot.calls.filter(row=>row.action==='publishing.deploy').length,unknownCalls=structuredClone(effectCalls(snapshot.calls)),unknownSite=structuredClone(snapshot.publishing.sites[0]);assert.equal(unknownSite.status,'unknown');
 await pending.getByRole('button',{name:'Acknowledge unknown outcome'}).click();await expect(pending).toHaveCount(0);await expect(panel.getByRole('button',{name:'Deploy reviewed release'})).toBeDisabled();
 snapshot=await list();assert.equal(snapshot.calls.filter(row=>row.action==='publishing.deploy').length,unknownCount);assert.deepEqual(effectCalls(snapshot.calls),unknownCalls);assert.deepEqual(snapshot.publishing.sites[0],unknownSite);
 await panel.getByRole('button',{name:'Stop site',exact:true}).click();await expect(status).toContainText('stopped');
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(status).toContainText('running');
 if(process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS)await page.screenshot({path:process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS+'-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});await status.scrollIntoViewIfNeeded();assert.ok(await page.locator('#workspace-canvas').evaluate(el=>el.scrollWidth<=el.clientWidth));
 if(process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS)await page.screenshot({path:process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS+'-mobile.png',animations:'disabled'});
 await panel.getByRole('button',{name:'Stop site',exact:true}).click();await expect(status).toContainText('stopped');await expect(panel.getByRole('link',{name:'Open deployed site'})).toHaveCount(0);
 await panel.getByRole('button',{name:'Remove site',exact:true}).click();await expect(status).toContainText('removed');
 await page.reload();await page.getByRole('region',{name:'Managed publishing'}).waitFor();await expect(panel.getByRole('button',{name:/fixture-site.*removed/})).toBeVisible();
 snapshot=await list();assert.equal(snapshot.publishing.releases.length,2);assert.equal(snapshot.publishing.sites[0].status,'removed');assert.ok(snapshot.publishing.receipts.length>=10);assert.equal(snapshot.selected,ready.sessionId);assert.equal(snapshot.draft,'Keep publishing draft');assert.deepEqual(snapshot.sent,[]);assert.deepEqual(errors,[]);
 // Configure an existing service through shared app controls. The fixture's
 // PATH-scoped fake SSH executes the real socket request CLI locally; it does
 // not establish a production SSH connection or provision a host.
 await page.setViewportSize({width:1280,height:950});
 if(!await panel.locator('.a-publishing-targets').evaluate(el=>el.open))await panel.getByText('Publishing targets for this task',{exact:true}).click();
 const targetFields={targetId:'Target ID',label:'Target label',hostname:'SSH hostname',python:'Remote Python path',socketPath:'Remote service socket',expectedBind:'Expected bind address'};
 for(const [key,label] of Object.entries(targetFields))await panel.getByLabel(label,{exact:true}).fill(ready.remoteConfig[key]);
 const beforeSave=await list('loopback');let targetSaveDropped=false;
 await page.route('**/api/actions*',async route=>{const body=route.request().postDataJSON();if(body?.action==='publishing.target.save'&&!targetSaveDropped){targetSaveDropped=true;await route.fetch();await route.abort('failed')}else await route.continue()});
 await panel.getByRole('button',{name:'Save target configuration',exact:true}).click();await expect(pending).toContainText('publishing.target.save');await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(pending).toHaveCount(0);await expect(panel.getByRole('alert')).toContainText(/revision|changed/i);await page.unroute('**/api/actions*');
 const remoteTarget=panel.getByLabel('Target private-fixture',{exact:true}),localTarget=panel.getByLabel('Target loopback',{exact:true});
 await expect(remoteTarget).toContainText('Not inspected.');await expect(remoteTarget.getByRole('button',{name:'Select target',exact:true})).toBeDisabled();
 assert.equal((await list('loopback')).sshCalls.length,beforeSave.sshCalls.length,'Saving target configuration must not connect');
 await remoteTarget.getByRole('button',{name:'Inspect target',exact:true}).click();await expect(remoteTarget).toContainText(ready.remoteServiceId);await expect(remoteTarget).toContainText('loopback-only');
 await remoteTarget.getByRole('button',{name:'Edit target',exact:true}).click();snapshot=await list('loopback');const editingRevision=snapshot.targets.targets.find(row=>row.id==='private-fixture').revision;const concurrentSave=await page.request.post(ready.url+'/api/actions',{data:{action:'publishing.target.save',args:{sessionId:ready.sessionId,...ready.remoteConfig,label:'Changed by another client',expectedRevision:editingRevision}}});assert.equal(concurrentSave.status(),200);await panel.getByRole('button',{name:'Inspect publishing',exact:true}).click();await panel.getByRole('button',{name:'Save target configuration',exact:true}).click();await expect(panel.getByRole('alert')).toContainText(/changed|revision/i);await expect(remoteTarget).toContainText('Changed by another client');
 // A changed configuration invalidates inspected identity. A configured bind
 // never becomes an access-policy claim until the actual service agrees.
 await remoteTarget.getByRole('button',{name:'Edit target',exact:true}).click();await panel.getByLabel('Expected bind address',{exact:true}).fill('10.0.0.1');await panel.getByRole('button',{name:'Save target configuration',exact:true}).click();await expect(remoteTarget).toContainText('Not inspected.');
 await remoteTarget.getByRole('button',{name:'Inspect target',exact:true}).click();await expect(panel.getByRole('alert')).toContainText(/differs|mismatch/i);await expect(remoteTarget.getByRole('button',{name:'Select target',exact:true})).toBeDisabled();
 await panel.getByLabel('Expected bind address',{exact:true}).fill('127.0.0.1');await panel.getByRole('button',{name:'Save target configuration',exact:true}).click();await remoteTarget.getByRole('button',{name:'Inspect target',exact:true}).click();await expect(remoteTarget).toContainText(ready.remoteServiceId);
 await remoteTarget.getByRole('button',{name:'Select target',exact:true}).click();await expect(panel.getByLabel('Publishing target',{exact:true})).toContainText('private-fixture');await expect(panel.getByLabel('Publishing target',{exact:true})).toContainText('A loopback URL opens only on the service host.');
 // Another client can replace an unused target under the same ID. The stale
 // panel must submit its reviewed service A/revision, never import into B.
 await expect(capture).toBeDisabled();
 const observedBefore=await list('private-fixture'),observedRevision=observedBefore.targets.selectedTargetRevision;
 const replaceTarget=async config=>{const state=await list('loopback'),revision=state.targets.targets.find(row=>row.id==='private-fixture').revision;for(const [action,args] of [['publishing.target.save',{...config,expectedRevision:revision}],['publishing.target.inspect',{targetId:'private-fixture',expectedRevision:revision+1}],['publishing.target.select',{targetId:'private-fixture',expectedRevision:revision+1,serviceId:config.socketPath===ready.alternateSocketPath?ready.alternateServiceId:ready.remoteServiceId}]]){const response=await page.request.post(ready.url+'/api/actions',{data:{action,args:{sessionId:ready.sessionId,...args}}});assert.equal(response.status(),200,await response.text())}};
 await replaceTarget({...ready.remoteConfig,socketPath:ready.alternateSocketPath});
 await panel.getByLabel('Site ID',{exact:true}).fill('remote-fixture');await panel.getByLabel('Built output folder').fill('dist');await capture.click();await expect(panel.getByRole('alert')).toContainText(/target|changed|revision/i);await expect(pending).toHaveCount(0);await expect(remoteTarget).toContainText(ready.alternateServiceId);
 const rejectedCapture=await list('private-fixture'),staleCapture=rejectedCapture.calls.filter(row=>row.action==='publishing.build'&&row.args.targetId==='private-fixture').at(-1);assert.equal(staleCapture.args.targetRevision,observedRevision);assert.equal(staleCapture.args.serviceId,ready.remoteServiceId);assert.equal(rejectedCapture.publishing.releases.length,0);assert.equal(rejectedCapture.sshCalls.filter(row=>row.method==='import').length,observedBefore.sshCalls.filter(row=>row.method==='import').length);
 await replaceTarget(ready.remoteConfig);await panel.getByRole('button',{name:'Inspect publishing',exact:true}).click();await expect(remoteTarget).toContainText(ready.remoteServiceId);await expect(panel.getByLabel('Site ID',{exact:true})).toBeEnabled();
 await panel.getByLabel('Site ID',{exact:true}).fill('remote-fixture');await panel.getByLabel('Built output folder').fill('dist');await capture.click();await expect(release).toBeVisible();
 snapshot=await list('private-fixture');const remoteFirst=snapshot.publishing.releases[0];assert.ok(remoteFirst);await reviewed('Reviewed imported exact bytes on the isolated service.');
 await panel.getByRole('button',{name:'Start preview',exact:true}).click();await expect(preview).toBeVisible();assert.match(await content(await preview.getAttribute('href')),/Second release/);
 let remoteDropped=false;
 await page.route('**/api/actions*',async route=>{const body=route.request().postDataJSON();if(body?.action==='publishing.deploy'&&body.args.targetId==='private-fixture'&&!remoteDropped){remoteDropped=true;await route.fetch();await route.abort('failed')}else await route.continue()});
 await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(pending).toContainText('Request outcome unconfirmed');await expect(localTarget.getByRole('button',{name:'Select target',exact:true})).toBeDisabled();
 const remoteLost=await list('private-fixture'),remoteRequest=remoteLost.calls.filter(row=>row.action==='publishing.deploy'&&row.args.targetId==='private-fixture').at(-1).args;
 // Another client may change the task selection. This pending retry remains
 // bound to the originally submitted target and exact request identity.
 await page.request.post(ready.url+'/api/actions',{data:{action:'publishing.target.select',args:{sessionId:ready.sessionId,targetId:'loopback',expectedRevision:0}}});
 await page.getByRole('button',{name:'Hide canvas',exact:true}).click();await openPublishing(page);await expect(pending).toContainText('private-fixture');
 await pending.getByRole('button',{name:'Retry exact request'}).click();await expect(pending).toHaveCount(0);await expect(status).toContainText('running');
 snapshot=await list('private-fixture');const remoteDeploys=snapshot.calls.filter(row=>row.action==='publishing.deploy'&&row.args.targetId==='private-fixture');assert.equal(remoteDeploys.length,2);assert.deepEqual(remoteDeploys[1].args,remoteRequest);assert.equal(snapshot.publishing.sites[0].revision,remoteLost.publishing.sites[0].revision);
 await page.unroute('**/api/actions*');siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/Second release/);
 if(!await panel.locator('.a-publishing-targets').evaluate(el=>el.open))await panel.getByText('Publishing targets for this task',{exact:true}).click();await remoteTarget.getByRole('button',{name:'Select target',exact:true}).click();
 await page.request.post(ready.url+'/fixture/remote-change');await panel.getByLabel('Site ID',{exact:true}).fill('remote-fixture');await panel.getByLabel('Built output folder').fill('dist');await capture.click();await expect(release).not.toContainText(remoteFirst.id);await reviewed('Reviewed the imported update.');const remoteSecond=(await list('private-fixture')).publishing.releases.find(row=>row.id!==remoteFirst.id);await panel.getByRole('button',{name:'Deploy reviewed release'}).click();await expect(status).toContainText('Deployed release'+remoteSecond.id);siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/Third release/);
 await panel.getByRole('button',{name:'Roll back to previous release'}).click();await expect(status).toContainText('Deployed release'+remoteFirst.id);siteUrl=await panel.getByRole('link',{name:'Open deployed site'}).getAttribute('href');assert.match(await content(siteUrl),/Second release/);
 await panel.getByRole('button',{name:'Read site logs'}).click();await expect(panel.getByLabel('Publishing inspection')).toBeVisible();
 if(process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS){await remoteTarget.scrollIntoViewIfNeeded();await page.screenshot({path:process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS+'-remote-target-desktop.png',animations:'disabled'});await page.setViewportSize({width:390,height:844});await remoteTarget.scrollIntoViewIfNeeded();assert.ok(await page.locator('#workspace-canvas').evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:process.env.AMPLIFIER_PUBLISHING_SCREENSHOTS+'-remote-target-mobile.png',animations:'disabled'})}
 await panel.getByRole('button',{name:'Stop site',exact:true}).click();await expect(status).toContainText('stopped');await panel.getByRole('button',{name:'Remove site',exact:true}).click();await expect(status).toContainText('removed');
 snapshot=await list('private-fixture');assert.equal(snapshot.publishing.releases.length,2);assert.equal(snapshot.publishing.sites[0].status,'removed');assert.ok(snapshot.sshCalls.some(row=>row.method==='import'));assert.ok(snapshot.sshCalls.every(row=>row.argv.includes('BatchMode=yes')&&row.argv.includes('StrictHostKeyChecking=yes')));
 await localTarget.getByRole('button',{name:'Select target',exact:true}).click();await expect(panel.getByLabel('Publishing target',{exact:true})).toContainText('loopback');await remoteTarget.getByRole('button',{name:'Remove target configuration',exact:true}).click();await expect(panel.getByRole('alert')).toContainText(/retained|audit|in use/i);await expect(remoteTarget).toBeVisible();
 // Used targets stay registered for lifecycle controls and audit. An unused,
 // unselected configuration can be removed without contacting the service.
 await panel.getByRole('button',{name:'Clear target form',exact:true}).click();for(const [key,label] of Object.entries(targetFields))await panel.getByLabel(label,{exact:true}).fill(key==='targetId'?'unused-fixture':ready.remoteConfig[key]);const beforeUnused=await list('loopback');await panel.getByRole('button',{name:'Save target configuration',exact:true}).click();const unusedTarget=panel.getByLabel('Target unused-fixture',{exact:true});await expect(unusedTarget).toBeVisible();await unusedTarget.getByRole('button',{name:'Remove target configuration',exact:true}).click();await expect(unusedTarget).toHaveCount(0);assert.equal((await list('loopback')).sshCalls.length,beforeUnused.sshCalls.length);
 snapshot=await list('loopback');assert.equal(snapshot.targets.selectedTargetId,'loopback');assert.equal(snapshot.publishing.sites.find(row=>row.id==='fixture-site').status,'removed');assert.equal(snapshot.selected,ready.sessionId);assert.equal(snapshot.draft,'Keep publishing draft');assert.deepEqual(snapshot.sent,[]);assert.deepEqual(errors,[]);
 const mutations=snapshot.calls.filter(row=>['publishing.build','publishing.preview','publishing.review','publishing.deploy','publishing.rollback','publishing.stop','publishing.remove'].includes(row.action));assert.ok(mutations.every(row=>['loopback','private-fixture'].includes(row.args.targetId)));
 assert.ok(mutations.filter(row=>row.args.targetId==='private-fixture').every(row=>Number.isInteger(row.args.targetRevision)&&row.args.serviceId===ready.remoteServiceId));
 result={passed:true,immutableReleaseAndPreview:true,explicitReviewedDeployment:true,lostResponseExactRetry:true,confirmedUnknownAcknowledgedWithoutReplay:true,uncertain409RetainsPending:true,generic409RetryRetainsPending:true,duplicateSubmissionSuppressed:true,staleRevisionRefresh:true,actualUpdateAndRollbackBytes:true,statusAndLogs:true,stopRemoveRetainHistory:true,reload:true,mobileBounds:true,draftAndSelectionPreserved:true,noModelInvocations:true,targetSaveDoesNotConnect:true,lostTargetSaveResolvesRevisionRejection:true,actualServiceIdentityInspection:true,targetConfigurationInvalidatesInspection:true,staleTargetFormCannotOverwrite:true,staleTargetIdentityCannotImport:true,actualUnixServiceViaFakeSSH:true,immutableRetryTargetDespiteSelectionChange:true,remoteMutationsCarryReviewedIdentity:true,remoteImportUpdateRollbackAndCleanup:true,noProductionSSHClaim:true};
}catch(error){failure=error;throw error}
finally{
 await finishFixture({label:'Publishing fixture',originalError:failure,
  close:[['browser',()=>browser?.close()]],stop:async()=>{
   await stopFixture();
   assert.equal(cleanup?.pid,fixture.pid,'Expected cleanup receipt from the owned fixture');
   assert.equal(cleanup.signal,'SIGTERM');
   assert.equal(cleanup.runnerCleanup,true);assert.equal(cleanup.remoteCleanup,true);
   assert.equal(cleanup.homeRemoved,true);assert.equal(cleanup.remoteHomeRemoved,true);
   assert.equal(existsSync(cleanup.home),false);assert.equal(existsSync(cleanup.remoteHome),false);
   console.log(JSON.stringify({publishingFixtureCleanup:cleanup}));
  }});
}
console.log(JSON.stringify({...result,fixtureCleanupVerified:true}));
