import {finishFixture} from './fixture-lifecycle.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {startSettingsFixture} from './settings-fixture.mjs';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const {base,stop}=await startSettingsFixture();
let browser,page,failure;
const errors=[];
try{
 browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}:{}),...(process.env.PLAYWRIGHT_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE_PATH}:{})});
 page=await browser.newPage({viewport:{width:1100,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/');await page.waitForSelector('#amp-one');
 await openSettingsPage(page,'testing');
 const section=page.getByRole('region',{name:'Testing and resources'});
 await expect(section.getByRole('heading',{name:'Test runner not ready'})).toBeVisible();
 await section.getByText('Advanced limits',{exact:true}).click();
 await section.getByLabel('Running test environments',{exact:true}).fill('7');
 await section.getByLabel('Minimum free disk space (GiB)',{exact:true}).fill('0');
 await expect(page.getByText('You have unsaved settings changes. They are kept while Settings stays open.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('status').first()).toContainText('Testing limits saved');
 let state=await page.evaluate(()=>window.amplifier.dispatch('testing.capacity.read',{}));
 assert.equal(state.result.defaults.maxRunning,7);
 assert.equal(state.result.defaults.minFreeBytes,0);
 assert.equal(state.result.runnerId,null);

 // The server saves successfully but its acknowledgement is lost. The UI
 // recognizes the exact saved values with one read, without another mutation.
 let saves=0;
 const lostReply=async route=>{
  if(route.request().postDataJSON()?.action!=='testing.capacity.save'){await route.continue();return}
  saves++;await route.fetch();
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Save acknowledgement unavailable'})});
 };
 await page.route('**/api/actions',lostReply);
 await section.getByLabel('Running test environments',{exact:true}).fill('6');
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('status').first()).toContainText('Current testing settings match your changes');
 assert.equal(saves,1);
 await page.unroute('**/api/actions',lostReply);

 // Another settings writer wins; stale edits must not overwrite it.
 await section.getByLabel('Running test environments',{exact:true}).fill('8');
 await page.evaluate(async()=>{const current=(await window.amplifier.dispatch('testing.capacity.read',{})).result;await window.amplifier.dispatch('testing.capacity.save',{expectedRevision:current.revision,defaults:{maxRunning:9}})});
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('alert')).toContainText('changed');
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('8');
 await section.getByRole('button',{name:'Discard changes and reload'}).click();
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('9');

 // Shared physical-machine budgets use their own revision and lost-ack readback.
 const machine=page.getByRole('region',{name:'Shared machine resources'});
 await machine.getByText('Advanced machine budgets',{exact:true}).click();
 await machine.getByLabel('Reserved CPU cores',{exact:false}).fill('24');
 await machine.getByLabel('Keep memory available (GiB)',{exact:false}).fill('0');
 let machineSaves=0;
 const lostMachine=async route=>{
  if(route.request().postDataJSON()?.action!=='testing.machine.save'){await route.continue();return}
  machineSaves++;await route.fetch();await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Lost machine save acknowledgement'})});
 };
 await page.route('**/api/actions',lostMachine);
 await machine.getByRole('button',{name:'Save machine budgets'}).click();
 await expect(machine.getByRole('status')).toContainText('Current machine limits match');
 assert.equal(machineSaves,1);await page.unroute('**/api/actions',lostMachine);
 await machine.getByLabel('Reserved CPU cores',{exact:false}).fill('20');
 await page.evaluate(async()=>{const current=(await window.amplifier.dispatch('testing.machine.read',{})).result;await window.amplifier.dispatch('testing.machine.save',{expectedRevision:current.revision,limits:{maxReservedCpu:22}})});
 await machine.getByRole('button',{name:'Save machine budgets'}).click();
 await expect(machine.getByRole('alert')).toContainText('another installation');
 await expect(machine.getByLabel('Reserved CPU cores',{exact:false})).toHaveValue('20');
 await machine.getByRole('button',{name:'Discard machine edits and refresh'}).click();
 await expect(machine.getByLabel('Reserved CPU cores',{exact:false})).toHaveValue('22');

 // Execution policy is independent, global, and recovers a lost save receipt.
 const execution=page.getByRole('region',{name:'Test execution limits',exact:true});
 await expect(execution).toContainText('No count limit on pending checks');
 await execution.getByText('Advanced execution limits',{exact:true}).click();
 await execution.getByLabel('Outstanding task chats per manager',{exact:true}).fill('12');
 await execution.getByLabel('Owned test environments per chat',{exact:true}).fill('3');
 await execution.getByLabel('Owned resources across this installation',{exact:true}).fill('700');
 await execution.getByLabel('Owned resources per chat',{exact:true}).fill('40');
 await execution.getByLabel('Cooperative reservations across this installation',{exact:true}).fill('1500');
 await execution.getByLabel('Cooperative reservations per chat',{exact:true}).fill('80');
 for(const [label,value] of [['Pending messages and reserved replies per chat','70'],['Pending messages from one sender per chat','40'],['Pending messages from one assignment per chat','60'],['Messages per minute between two chats','45'],['Pending status watches per chat','140']])await execution.getByLabel(label,{exact:true}).fill(value);
 await execution.getByLabel('Maximum pending checks',{exact:true}).fill('128');
 await execution.getByLabel('Maximum pending checks per chat',{exact:true}).fill('3');
 await execution.getByLabel('Default queue wait (seconds)',{exact:true}).fill('0');
 await execution.getByLabel('Small Python check: CPU cores',{exact:true}).fill('3');
 await execution.getByLabel('Small Python check: Maximum check time (seconds)',{exact:true}).fill('7200');
 await execution.getByLabel('Small Python check: Default check time (seconds)',{exact:true}).fill('3600');
 let executionSaves=0;
 const lostExecution=async route=>{
  if(route.request().postDataJSON()?.action!=='testing.execution.save'){await route.continue();return}
  executionSaves++;await route.fetch();await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Lost execution save acknowledgement'})});
 };
 await page.route('**/api/actions',lostExecution);
 await execution.getByRole('button',{name:'Save execution limits'}).click();
 await expect(execution.getByRole('status')).toContainText('Current execution limits match');
 assert.equal(executionSaves,1);
 const concurrency=(await page.evaluate(()=>window.amplifier.dispatch('testing.execution.read',{}))).result.effectiveConcurrency;
 assert.equal(concurrency.maxTaskChatsPerSource,12);assert.equal(concurrency.maxEnvironmentsPerChat,3);assert.equal(concurrency.maxOwnedResources,700);assert.equal(concurrency.maxOwnedResourcesPerChat,40);assert.equal(concurrency.maxResourceReservations,1500);assert.equal(concurrency.maxResourceReservationsPerChat,80);assert.equal(concurrency.maxPendingPeerInputs,70);assert.equal(concurrency.maxPendingPeerInputsPerSender,40);assert.equal(concurrency.maxPendingPeerInputsPerAssignment,60);
 assert.equal((await page.evaluate(()=>window.amplifier.dispatch('testing.execution.read',{}))).result.effectiveQueue.maxPendingPerChat,3);
 await page.unroute('**/api/actions',lostExecution);
 await expect(execution.getByText('Waiting checks stay queued until stopped or cancelled.',{exact:false})).toBeVisible();
 await execution.getByLabel('Maximum pending checks',{exact:true}).fill('200');
 await execution.getByLabel('Maximum pending checks per chat',{exact:true}).fill('4');
 await page.evaluate(async()=>{const current=(await window.amplifier.dispatch('testing.execution.read',{})).result;await window.amplifier.dispatch('testing.execution.save',{expectedRevision:current.revision,queue:{maxPending:150,maxPendingPerChat:5,defaultWaitSeconds:0},profiles:current.profiles})});
 await execution.getByRole('button',{name:'Save execution limits'}).click();
 await expect(execution.getByRole('alert')).toContainText('changed');
 await expect(execution.getByLabel('Maximum pending checks',{exact:true})).toHaveValue('200');
 await expect(execution.getByLabel('Maximum pending checks per chat',{exact:true})).toHaveValue('4');
 await execution.getByRole('button',{name:'Discard execution edits and refresh'}).click();
 await expect(execution.getByLabel('Maximum pending checks',{exact:true})).toHaveValue('150');
 await expect(execution.getByLabel('Maximum pending checks per chat',{exact:true})).toHaveValue('5');
 await execution.getByLabel('Maximum pending checks per chat',{exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:`/tmp/per-chat-queue-settings-${page.viewportSize().width}.png`,fullPage:true});

 // Saved global settings remain reachable on narrow layouts and cold reopen.
 await page.setViewportSize({width:390,height:844});
 await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-compact','true');
 await section.getByLabel('Running test environments',{exact:true}).fill('10');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('10');
 await page.getByRole('button',{name:'Save testing limits'}).click();
 await expect(section.getByRole('status').first()).toContainText('Testing limits saved');
 await machine.getByLabel('Concurrent dependency builds',{exact:false}).fill('3');
 await machine.getByRole('button',{name:'Save machine budgets'}).click();
 await expect(machine.getByRole('status')).toContainText('Machine limits saved');
 await page.reload();await page.waitForSelector('#amp-one');
 await openSettingsPage(page,'testing');
 await section.getByText('Advanced limits',{exact:true}).click();
 await expect(section.getByLabel('Running test environments',{exact:true})).toHaveValue('10');
 await execution.getByText('Advanced execution limits',{exact:true}).click();
 await expect(execution.getByLabel('Maximum pending checks',{exact:true})).toHaveValue('150');
 await expect(execution.getByLabel('Maximum pending checks per chat',{exact:true})).toHaveValue('5');
 await execution.getByLabel('Maximum pending checks per chat',{exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:`/tmp/per-chat-queue-settings-${page.viewportSize().width}.png`,fullPage:true});
 for(const [label,value] of [['Outstanding task chats per manager','12'],['Owned test environments per chat','3'],['Owned resources across this installation','700'],['Owned resources per chat','40'],['Cooperative reservations across this installation','1500'],['Cooperative reservations per chat','80'],['Pending messages and reserved replies per chat','70'],['Pending messages from one sender per chat','40'],['Pending messages from one assignment per chat','60'],['Messages per minute between two chats','45'],['Pending status watches per chat','140']])await expect(execution.getByLabel(label,{exact:true})).toHaveValue(value);
 await expect(execution.getByLabel('Small Python check: CPU cores',{exact:true})).toHaveValue('3');
 await execution.getByLabel('Maximum pending checks',{exact:true}).fill('160');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toContainText('Test execution limits');
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(execution.getByLabel('Maximum pending checks',{exact:true})).toHaveValue('160');
 await execution.getByRole('button',{name:'Save execution limits'}).click();
 await expect(execution.getByRole('status')).toContainText('Execution limits saved');
 await execution.scrollIntoViewIfNeeded();
 await page.screenshot({path:process.env.EXECUTION_SETTINGS_SCREENSHOT||'/tmp/execution-settings-browser.png',fullPage:true});
 await execution.getByLabel('Small Python check: Memory (MiB)',{exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/execution-profile-browser.png',fullPage:true});
 // Every work count can use zero; save and cold reopen retain it.
 const countLabels=['Outstanding task chats per manager','Owned test environments per chat','Owned resources across this installation','Owned resources per chat','Cooperative reservations across this installation','Cooperative reservations per chat','Maximum pending checks','Maximum pending checks per chat','Pending messages and reserved replies per chat','Pending messages from one sender per chat','Pending messages from one assignment per chat','Messages per minute between two chats','Pending status watches per chat'];
 for(const label of countLabels)await execution.getByLabel(label,{exact:true}).fill('0');
 await execution.getByRole('button',{name:'Save execution limits'}).click();
 await expect(execution.getByRole('status')).toContainText('Execution limits saved');
 await expect(execution).toContainText('with no per-chat count limit');
 await expect(execution).toContainText('with no per-chat environment count limit');
 const uncapped=(await page.evaluate(()=>window.amplifier.dispatch('testing.execution.read',{}))).result;
 assert.equal(uncapped.concurrency.maxTaskChatsPerSource,0);assert.equal(uncapped.concurrency.maxEnvironmentsPerChat,0);assert.equal(uncapped.queue.maxPendingPerChat,0);
 assert.ok(Object.values(uncapped.effectiveConcurrency).every(value=>value===0));assert.equal(uncapped.queue.maxPending,0);assert.equal(uncapped.queue.defaultWaitSeconds,0);
 await expect(execution).toContainText('No count limit on pending checks');
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'testing');
 await execution.getByText('Advanced execution limits',{exact:true}).click();
 for(const label of countLabels)await expect(execution.getByLabel(label,{exact:true})).toHaveValue('0');
 await expect(execution.getByText('Effective: No count limit',{exact:true})).toHaveCount(Object.values(uncapped.effectiveConcurrency).filter(value=>value===0).length);
 await execution.getByLabel('Pending status watches per chat',{exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:process.env.PEER_INBOX_SETTINGS_SCREENSHOT||'/tmp/peer-inbox-settings-browser.png',fullPage:true});
 // Mounted remote-target setup is a bounded protocol fixture, not a host install.
 let targets={revision:'r1',runners:[],enrollments:[],scope:'Approved machines, shared per account.'},targetWrites=0,refreshes=0,policyWrites=0,losePolicyReply=false;
 let candidate;
 const backendDefaults={maxRunning:0,maxInstances:0,minFreeBytes:10*1024**3};
 let targetPolicy={revision:'p1',limits:{},defaults:backendDefaults,effective:backendDefaults,scope:'This target account and Incus project.',behavior:'Lowering limits keeps active checks running.'};
 await page.route('**/api/actions',async route=>{
  const request=route.request().postDataJSON(),action=request?.action;
  if(!action?.startsWith('testing.runners.')){await route.continue();return}
  const args=request.args||request.arguments||{};
  let result;
  if(action==='testing.runners.read') result=targets;
  else if(action==='testing.runners.register'){
   targetWrites++;candidate={id:'target',machineId:'authority',name:args.name,connection:args.connection,configPath:args.configPath||'',enabled:true,profiles:['linux-python']};
   targets={...targets,revision:'r2',enrollments:[{id:'check-one',requestId:args.requestId,name:args.name,connection:args.connection,status:'capacity',canCancel:true,detail:'Waiting for room on this test machine. Existing work can continue.'}]};
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Acknowledgement lost'})});return;
  }else if(action==='testing.runners.refresh'){refreshes++;targets={...targets,enrollments:[{id:'check-two',requestId:args.requestId,name:candidate.name,connection:candidate.connection,status:'unknown',canCancel:true,canReconcile:true,detail:'The last operation is not yet confirmed. Its original owner is retained; the check is not repeated.'}]};result=targets}
  else if(action==='testing.runners.enrollment.reconcile'){assert.equal(args.id,'check-two');result=targets}
  else if(action==='testing.runners.enrollment.cancel'){assert.equal(args.id,'check-two');targets={...targets,enrollments:targets.enrollments.map(item=>({...item,status:'cancelled',canCancel:false,canReconcile:false,detail:'Connection check cancelled. This request did not enable the machine.'}))};result=targets}
  else if(action==='testing.runners.enable'){targets={...targets,revision:'r3',runners:targets.runners.map(item=>({...item,enabled:args.enabled}))};result=targets}
  else if(action==='testing.runners.machine.read')result={supported:false,reason:'Machine observation fixture'};
  else if(action==='testing.runners.capacity.read')result=targetPolicy;
  else if(action==='testing.runners.capacity.save'){
   policyWrites++;
   if(args.expectedRevision!==targetPolicy.revision){await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Test environment limits changed. Refresh before saving.'})});return}
   targetPolicy={...targetPolicy,revision:'p'+(Number(targetPolicy.revision.slice(1))+1),limits:args.limits,effective:{...backendDefaults,...args.limits}};
   if(losePolicyReply){losePolicyReply=false;await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Save acknowledgement unavailable'})});return}
   result=targetPolicy;
  }
  else throw Error('Unexpected target action '+action);
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({accepted:true,result})});
 });
 const targetsRegion=page.getByRole('region',{name:'Connected test machines',exact:true});
 await targetsRegion.getByRole('button',{name:'Refresh test machines',exact:true}).click();
 await targetsRegion.getByText('Connect a test machine',{exact:true}).click();
 await targetsRegion.getByLabel('Name to show',{exact:true}).fill('Office testing machine');
 await targetsRegion.getByLabel('Saved SSH connection',{exact:true}).fill('office-tests');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toContainText('Connected test machines');
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await targetsRegion.getByRole('button',{name:'Connect and check',exact:true}).click();
 await expect(targetsRegion.getByRole('status').first()).toContainText('saved connection check was found');
 assert.equal(targetWrites,1);
 await expect(targetsRegion.getByText('Waiting for room on this test machine. Existing work can continue.',{exact:true})).toBeVisible();
 assert.equal(targets.runners.length,0);
 await targetsRegion.getByRole('heading',{name:'Connected test machines',exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/connection-check-narrow.png'});
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'testing');
 await expect(targetsRegion.getByText('Waiting for room on this test machine. Existing work can continue.',{exact:true})).toBeVisible();
 assert.equal(targetWrites,1);
 targets={...targets,runners:[{...candidate,lastCheck:{status:'passed',checkedAt:Date.now()/1000}}],enrollments:targets.enrollments.map(item=>({...item,status:'connected',canCancel:false,detail:'Connection checked. Its result was retained and its test environment was removed.'}))};
 await targetsRegion.getByRole('button',{name:'Refresh test machines',exact:true}).click();
 await targetsRegion.getByText('Office testing machine · Allowed for new checks',{exact:true}).click();
 await expect(targetsRegion.getByText(/Last connection check passed/)).toBeVisible();
 const targetLimits=targetsRegion.getByRole('region',{name:'Office testing machine environment limits'});
 await targetLimits.getByText('Advanced environment limits',{exact:true}).click();
 await targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true}).fill('13');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toContainText('Office testing machine environment limits');
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true})).toHaveValue('13');
 losePolicyReply=true;
 await targetLimits.getByRole('button',{name:'Save environment limits',exact:true}).click();
 await expect(targetLimits.getByRole('status')).toContainText('save was not repeated');
 assert.equal(policyWrites,1);assert.equal(targetPolicy.limits.maxRunning,13);
 await targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true}).fill('17');
 targetPolicy={...targetPolicy,revision:'p3',limits:{maxRunning:15},effective:{...backendDefaults,maxRunning:15}};
 await targetLimits.getByRole('button',{name:'Save environment limits',exact:true}).click();
 await expect(targetLimits.getByRole('alert')).toContainText('Your edits are kept');
 await expect(targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true})).toHaveValue('17');
 assert.equal(targetPolicy.limits.maxRunning,15);
 await targetLimits.getByRole('button',{name:'Discard environment edits and refresh',exact:true}).click();
 await expect(targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true})).toHaveValue('15');
 await targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true}).fill('0');
 await targetLimits.getByRole('button',{name:'Save environment limits',exact:true}).click();
 await expect(targetLimits.getByRole('status')).toContainText('Environment limits saved');
 assert.equal(targetPolicy.effective.maxRunning,0);
 await targetsRegion.getByRole('button',{name:'Pause new checks',exact:true}).click();
 await expect(targetsRegion.getByText('Office testing machine · Paused for new checks',{exact:true})).toBeVisible();
 await targetsRegion.getByRole('button',{name:'Check connection and controller',exact:true}).click();
 await expect(targetsRegion.getByRole('status').first()).toContainText('Connection check saved');assert.equal(refreshes,1);
 await expect(targetsRegion.getByText(/The last operation is not yet confirmed/)).toBeVisible();
 await targetsRegion.getByRole('button',{name:'Check saved result',exact:true}).click();
 assert.equal(refreshes,1);
 await targetsRegion.getByRole('button',{name:'Cancel connection check',exact:true}).click();
 await expect(targetsRegion.getByText('Connection check cancelled. This request did not enable the machine.',{exact:true})).toBeVisible();
 assert.equal(targets.runners[0].enabled,false);
 await page.reload();await page.waitForSelector('#amp-one');await openSettingsPage(page,'testing');
 await expect(targetsRegion.getByText('Office testing machine · Paused for new checks',{exact:true})).toBeVisible();
 await targetsRegion.getByText('Office testing machine · Paused for new checks',{exact:true}).click();
 await targetLimits.getByText('Advanced environment limits',{exact:true}).click();
 await expect(targetLimits.getByLabel('Office testing machine: Maximum active test environments',{exact:true})).toHaveValue('0');
 await targetsRegion.scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/registered-test-machines-browser.png',fullPage:true});
 // Capture the optional desktop evidence after the cold-reopen assertions.
 // Resizing Settings changes browser history asynchronously; do not combine
 // that separate transition with the reload under test.
 await page.setViewportSize({width:1100,height:900});
 await expect(targetsRegion.getByText('Office testing machine · Paused for new checks',{exact:true})).toBeVisible();
 await targetsRegion.getByRole('heading',{name:'Connected test machines',exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:'/tmp/connection-check-desktop.png'});

 assert.deepEqual(errors,[]);
 console.log('PASS: global settings without a runner, zero override, lost-acknowledgement readback without replay, stale writer protection, preserved draft, narrow layout and cold reopen');
}catch(error){
 failure=error;
 await page?.screenshot({path:'/tmp/testing-settings-failure.png',fullPage:true}).catch(()=>{});
 const state=await page?.evaluate(()=>({url:location.href,panel:window.amplifier?.getState()?.view?.panel,
  route:document.querySelector('.a-settings-experience')?.dataset.settingsRoute,
  compact:document.querySelector('.a-settings-experience')?.dataset.compact,
  buttons:[...document.querySelectorAll('.a-settings-mobile-head button')].map(item=>item.getAttribute('aria-label'))})).catch(()=>null);
 console.error(JSON.stringify({pageErrors:errors,settingsState:state}));
 throw error;
}
finally{await finishFixture({label:'Testing settings fixture',close:[['Testing settings browser',()=>browser?.close()]],stop,originalError:failure})}
