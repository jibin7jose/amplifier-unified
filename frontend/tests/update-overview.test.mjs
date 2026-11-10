import test from 'node:test';
import assert from 'node:assert/strict';
import {updateOverview} from '../src/update-overview.js';
import {matchingSetupOperation} from '../src/ai-connections-data.js';

test('only a completed whole sequence is described as up to date',()=>{
 assert.notEqual(updateOverview({application:{status:'current'},lastCheck:1}).tone,'ready');
 assert.equal(updateOverview({application:{status:'current'},sequence:{stage:'complete'}}).tone,'ready');
});

const confirmedApp={attemptId:'a'.repeat(32),phase:'restart-ack',version:'0.20.199',revision:'b'.repeat(40),verifiedAt:10};
const componentFailure={application:{status:'current',current:confirmedApp.version,runningRevision:confirmedApp.revision},
 applicationResult:confirmedApp,phase:'error',error:'Component update failed',sequence:{stage:'other'},
 diagnostics:{attemptId:'component',batch:{attemptId:'component',kind:'ecosystem',tier:'other',components:['foundation'],startedAt:11},
 lastFailure:{attemptId:'component',kind:'ecosystem',phase:'ecosystem-validation',status:'failed',reason:'runtime-source-changed',at:12}}};
test('confirmed application success is separate from a later component failure',()=>{
 const result=updateOverview(componentFailure);
 assert.equal(result.applicationResult?.version,confirmedApp.version);
 assert.equal(result.title,'Some components were not updated');
 assert.equal(result.tone,'error');
 assert.match(result.detail,/foundation/);
 assert.match(result.detail,/Check for updates again/);
 assert.equal(result.busy,false);
});
test('stale or unverified app results never relabel a failure as partial success',()=>{
 for(const patch of [
  {applicationResult:null},
  {applicationResult:{...confirmedApp,revision:'c'.repeat(40)}},
  {applicationResult:{...confirmedApp,version:'0.20.198'}},
  {applicationResult:{...confirmedApp,verifiedAt:0}},
  {applicationResult:{...confirmedApp,phase:'replacement-probe'}},
  {application:{...componentFailure.application,runningRevision:null}},
  {pendingApp:{}},{pendingRestart:{requestStatus:'uncertain'}},{pendingReplacement:{}},
 ]){
  const result=updateOverview({...componentFailure,...patch});
  assert.equal(result.applicationResult,null,JSON.stringify(patch));
  assert.notEqual(result.title,'Some components were not updated');
 }
 for(const diagnostics of [
  {...componentFailure.diagnostics,lastFailure:{...componentFailure.diagnostics.lastFailure,attemptId:'older'}},
  {...componentFailure.diagnostics,lastFailure:{...componentFailure.diagnostics.lastFailure,at:5}},
  {...componentFailure.diagnostics,batch:{...componentFailure.diagnostics.batch,kind:'application'}},
 ])assert.notEqual(updateOverview({...componentFailure,diagnostics}).title,'Some components were not updated');
 assert.notEqual(updateOverview({...componentFailure,lastAttempt:13}).title,'Some components were not updated');
 assert.notEqual(updateOverview({...componentFailure,sequence:{stage:'application'}}).title,'Some components were not updated');
 assert.notEqual(updateOverview({...componentFailure,application:{...componentFailure.application,status:'check_failed'}}).title,'Some components were not updated');
 const reconciled=updateOverview({...componentFailure,applicationResult:{...confirmedApp,phase:'restart-reconcile'}});
 assert.match(reconciled.detail,/current application is verified/);
 assert.doesNotMatch(reconciled.detail,/application update is confirmed/);
});
test('install intent survives restart and idle wait without asking again',()=>{
 for(const pending of [{pendingApp:{}},{pendingRelease:{}},{pendingRestart:{requestStatus:'uncertain'}}]){
  const result=updateOverview({...pending,sequence:{stage:'application',install:true,nextStage:'included'}});
  assert.equal(result.continuing,true);assert.equal(result.tone,'working');assert.notEqual(result.title,'You’re up to date');
 }
});
test('protected edits and failed required components never claim completion',()=>{
 const result=updateOverview({sequence:{stage:'included',install:true,included:{issues:1,protected:1}}});
 assert.equal(result.blocked,true);assert.equal(result.tone,'error');
 assert.equal(updateOverview({pendingReplacement:{},sequence:{stage:'complete'}}).tone,'error');
 assert.equal(updateOverview({application:{status:'check_failed'},sequence:{stage:'complete'}}).tone,'error');
});

const unavailableApp={id:'application',kind:'app',label:'Amplifier Unified',current:'0.60.1',status:'check_failed',
 checkStage:'release_metadata',checkReason:'unavailable',detail:'Could not read published-release metadata. No update was installed by this check.'};
const independentComponents={phase:'available',application:unavailableApp,
 items:[unavailableApp,{id:'foundation',kind:'bundle / module',status:'update',updateTier:'included'}],
 sequence:{stage:'included',included:{available:1,issues:0},other:{status:'waiting'}}};
test('application availability attention does not stop available component controls',()=>{
 for(const status of ['check_failed','release_channel_needed']){
  const application={...unavailableApp,status};
  for(const currentStatus of [undefined,{status:'available',title:'Updates available',
   detail:'Eligible component updates can proceed independently.',actionRequired:false}]){
   const updates={...independentComponents,application,items:[application,independentComponents.items[1]],currentStatus};
   const before=JSON.stringify(updates),result=updateOverview(updates,{autoInstall:false});
   assert.equal(result.installable,true);assert.equal(result.blocked,false);
   assert.equal(result.busy,false);assert.equal(result.failed,false);assert.equal(result.tone,'available');
   assert.match(result.detail,/independently/);
   assert.equal(result.application.state,status==='check_failed'?'failed':status);
   assert.equal(result.applicationResult,null);
   assert.equal(JSON.stringify(updates),before);
  }
 }
});
test('failed application check leaves component validation progress active and separate',()=>{
 const updates={...independentComponents,phase:'validating',
  diagnostics:{attemptId:'components',batch:{attemptId:'components',kind:'ecosystem',tier:'included',components:['foundation']}},
  probeProgress:{attemptId:'components',phase:'prepare',completed:2,total:3},
  currentStatus:{status:'working',title:'Updating Amplifier',detail:'Preparing components.',actionRequired:false}};
 const result=updateOverview(updates);
 assert.equal(result.title,'Finishing your update');assert.equal(result.tone,'working');
 assert.equal(result.active,true);assert.equal(result.processing,true);assert.equal(result.busy,true);
 assert.equal(result.failed,false);assert.equal(result.currentProgress.completed,2);
 assert.deepEqual(result.currentBatch.components,['foundation']);
 assert.equal(result.application.state,'failed');assert.equal(result.application.detail,unavailableApp.detail);
 assert.equal(result.applicationResult,null);
});
test('failed application check keeps waiting and per-worker adoption busy',()=>{
 for(const activation of ['per-worker',undefined]){
  const result=updateOverview({...independentComponents,phase:'staged',pendingRelease:'candidate',
   plan:{activation},blockers:[{kind:'conversation',sessionId:'one'}],
   currentStatus:{status:'waiting',title:'Update ready; waiting for work',detail:'Waiting for activation.',actionRequired:false}});
  assert.equal(result.pending,true);assert.equal(result.busy,true);assert.equal(result.failed,false);
  assert.equal(result.waiting,activation!=='per-worker');
  assert.equal(result.processing,activation==='per-worker');
  assert.equal(result.title,activation==='per-worker'?'Finishing your update':'Waiting for 1 chat to finish');
  assert.equal(result.application.state,'failed');assert.equal(result.application.detail,unavailableApp.detail);
 }
});
test('component completion never turns a failed or unconfigured app check into current',()=>{
 const complete={phase:'installed',sequence:{stage:'complete'},componentResults:[{id:'foundation',status:'updated'}]};
 for(const pendingWorkers of [0,2]){
  for(const currentStatus of [undefined,{status:'action-required',title:'Application check needs attention',
   detail:unavailableApp.detail,actionRequired:true,action:'check',component:unavailableApp.label}]){
   const result=updateOverview({...complete,application:unavailableApp,items:[unavailableApp],adoption:{pendingWorkers},currentStatus});
   assert.equal(result.title,'Application check needs attention');assert.equal(result.tone,'error');
   assert.equal(result.failed,true);assert.equal(result.busy,false);assert.equal(result.processing,false);
   assert.equal(result.application.state,'failed');assert.equal(result.applicationResult,null);
  }
  const unconfigured=updateOverview({...complete,application:{...unavailableApp,status:'release_channel_needed'},adoption:{pendingWorkers}});
  assert.equal(unconfigured.title,'Automatic app updates aren’t configured');assert.notEqual(unconfigured.tone,'ready');
  assert.equal(unconfigured.application.state,'release_channel_needed');
 }
});
test('real component operation failure wins over application availability attention without changing receipts',()=>{
 const updates={...independentComponents,phase:'error',error:'Component qualification failed',
  diagnostics:componentFailure.diagnostics};
 const before=JSON.stringify(updates),fallback=updateOverview(updates);
 assert.equal(fallback.title,'Updates need attention');assert.equal(fallback.failed,true);
 assert.equal(fallback.application.state,'failed');assert.equal(fallback.application.detail,unavailableApp.detail);
 const currentStatus={status:'protected',title:'Component source needs review',detail:'The checked component source changed.',
  actionRequired:true,action:'sources',component:'foundation'};
 const canonical=updateOverview({...updates,currentStatus});
 assert.equal(canonical.title,currentStatus.title);assert.equal(canonical.detail,currentStatus.detail);
 assert.equal(canonical.current.action,'sources');assert.equal(canonical.application.state,'failed');
 assert.equal(JSON.stringify(updates),before);
});
test('application check independence does not unlock replacement or restart boundaries',()=>{
 for(const marker of [{pendingReplacement:{}},{pendingRestart:{requestStatus:'uncertain'}},
  {pendingRestart:{requestStatus:'rejected'},phase:'error',error:'Restart failed'}]){
  const result=updateOverview({...independentComponents,...marker});
  assert.equal(result.blocked,true);assert.notEqual(result.tone,'available');assert.notEqual(result.tone,'ready');
  assert.equal(result.applicationResult,null);
  if(!marker.error)assert.equal(result.busy,true);
 }
 const interrupted=updateOverview({...independentComponents,phase:'interrupted',error:'Component operation outcome is unknown'});
 assert.equal(interrupted.failed,true);assert.equal(interrupted.processing,false);
 assert.notEqual(interrupted.title,'Application check needs attention');
});

test('included source issues allow independent updates only with the host proceed decision',()=>{
 const updates={phase:'available',sequence:{stage:'included',included:{issues:1,available:1}},
  items:[{id:'a',status:'check_failed'},{id:'b',status:'update'}]};
 for(const includedCanProceed of [undefined,false]){
  const result=updateOverview({...updates,includedCanProceed},{autoInstall:false});
  assert.equal(result.blocked,true);assert.equal(result.tone,'error');
 }
 const result=updateOverview({...updates,includedCanProceed:true},{autoInstall:false});
 assert.equal(result.blocked,false);assert.equal(result.busy,false);
 assert.equal(result.installable,true);assert.equal(result.title,'Updates available');
 const preparing=updateOverview({...updates,includedCanProceed:true,phase:'validating'});
 assert.equal(preparing.active,true);assert.equal(preparing.tone,'working');
 assert.equal(updateOverview({...updates,includedCanProceed:true,error:'Qualification failed'}).tone,'error');
});
test('partial source results never turn a retained or awaiting component into all-current',()=>{
 const base={phase:'installed',sequence:{stage:'complete'},application:{status:'current'}};
 for(const status of ['failed','dependency-held']){
  const result=updateOverview({...base,componentResults:[{id:'a',status,revision:'a'.repeat(40)},{id:'b',status:'updated',revision:'b'.repeat(40)}]});
  assert.equal(result.title,'Some components kept their working versions');
  assert.equal(result.tone,'error');assert.equal(result.busy,false);
  assert.equal(result.application.state,'current');
 }
 const awaiting=updateOverview({...base,componentResults:[{id:'b',status:'awaiting-adoption'}]});
 assert.equal(awaiting.title,'Components awaiting adoption');assert.equal(awaiting.tone,'working');
 assert.equal(updateOverview({...base,componentResults:[{id:'b',status:'updated'}]}).title,'You’re up to date');
});
test('partial source permission preserves pending adoption and unknown app boundaries',()=>{
 const updates={includedCanProceed:true,sequence:{stage:'included',included:{issues:1,available:1}},
  componentResults:[{id:'a',status:'failed'},{id:'b',status:'awaiting-adoption'}]};
 const staged=updateOverview({...updates,phase:'staged',pendingRelease:'candidate',plan:{activation:'per-worker'}});
 assert.equal(staged.title,'Finishing your update');assert.equal(staged.busy,true);assert.equal(staged.pending,true);
 for(const pending of [{pendingReplacement:{}},{pendingRestart:{requestStatus:'uncertain'}}]){
  const result=updateOverview({...updates,...pending});
  assert.equal(result.blocked,true);assert.equal(result.busy,true);
  assert.notEqual(result.title,'You’re up to date');
  assert.notEqual(result.title,'Some components kept their working versions');
 }
 const interrupted=updateOverview({...updates,phase:'interrupted',error:'Source operation outcome is unknown'});
 assert.equal(interrupted.tone,'error');assert.notEqual(interrupted.title,'Updates available');
});
test('an install failure with continuation intent remains visibly actionable',()=>{
 const result=updateOverview({pendingApp:{},phase:'error',error:'Restart failed',sequence:{stage:'application',install:true}});
 assert.equal(result.tone,'error');assert.equal(result.installable,true);assert.equal(result.blocked,false);
});
test('provider setup ignores stale operation acknowledgments',()=>{
 const pending={action:'providers.save',id:'one',commandId:'new'};
 assert.equal(matchingSetupOperation({setup:{operations:{'providers.save:one':{commandId:'old',phase:'ready'}}}},pending),null);
 assert.equal(matchingSetupOperation({setup:{operations:{'providers.save:one':{commandId:'new',phase:'error'}}}},pending).phase,'error');
});

test('new batch shows real successful configuration count and retains installed milestones',()=>{
 const diagnostics={attemptId:'new',batch:{attemptId:'new',kind:'ecosystem',tier:'other',components:['approval'],startedAt:1},
  completedBatches:[{attemptId:'old',tier:'other',completedAt:2}]};
 const result=updateOverview({phase:'validating',sequence:{stage:'other',install:true},diagnostics,
  probeProgress:{attemptId:'new',phase:'prepare',completed:24,total:37}});
 assert.equal(result.title,'Finishing your update');
 assert.equal(result.currentProgress.completed,24);assert.equal(result.currentProgress.total,37);
 assert.equal(result.completedBatches[0].attemptId,'old');
 assert.equal(result.currentBatch.components[0],'approval');
});
test('stale, invalid and terminal progress never masquerades as current preparation',()=>{
 for(const probeProgress of [{attemptId:'old',completed:37,total:37},{attemptId:'new',completed:38,total:37},{attemptId:'new',completed:-1,total:37}]){
  assert.equal(updateOverview({phase:'validating',diagnostics:{attemptId:'new',batch:{attemptId:'new'}},probeProgress}).currentProgress,null);
 }
 const result=updateOverview({phase:'staged',pendingRelease:'candidate',diagnostics:{attemptId:'new',batch:{attemptId:'new'}},probeProgress:{attemptId:'new',phase:'prepare',completed:37,total:37}});
 assert.equal(result.currentProgress,null);assert.equal(result.title,'Waiting for your work to finish');
});
test('app preparation is never mislabeled as other components',()=>{
 assert.equal(updateOverview({phase:'staging',sequence:{stage:'application'}}).title,'Updating Amplifier');
});

test('pending activation locks duplicate updates, but a failed candidate can be retried',()=>{
 for(const pending of [{pendingApp:{}},{pendingRelease:'candidate',plan:{activation:'per-worker'}},{pendingSmartTools:[{id:'tool'}]}]){
  const running=updateOverview({...pending,phase:'staged'});
  assert.equal(running.busy,true);
  assert.equal(updateOverview({...pending,phase:'error',error:'Failed'}).busy,false);
 }
});
test('restart waiting wins over a previous component plan and counts distinct chats only',()=>{
 const result=updateOverview({phase:'staged',pendingApp:{},pendingRelease:'candidate',plan:{activation:'per-worker'},blockers:[{kind:'conversation',sessionId:'one'},{kind:'conversation',sessionId:'one'}]});
 assert.equal(result.waiting,true);assert.equal(result.processing,false);assert.match(result.title,/1 chat to finish/);assert.match(result.detail,/restart automatically/);
 const mixed=updateOverview({pendingApp:{},blockers:[{kind:'voice',sessionId:'one'},{kind:'conversation',sessionId:'one'}]});
 assert.equal(mixed.title,'Waiting for your work to finish');
});
test('component adoption is ready only after activation and all update stages finish',()=>{
 const state={adoption:{pendingWorkers:2},sequence:{stage:'complete'}};
 assert.equal(updateOverview(state).title,'Ready for new work');
 assert.equal(updateOverview({...state,pendingRelease:'candidate',plan:{activation:'per-worker'}}).tone,'working');
 assert.equal(updateOverview({...state,error:'Failed'}).tone,'error');
 assert.equal(updateOverview({...state,adoption:{pendingWorkers:0}}).title,'You’re up to date');
});

test('runner-only qualification is independent of chat idleness and keeps unknown results explicit',()=>{
 const runner={runnerSource:{runnerKind:'managed-dtu-core'},runnerUpdate:'check'};
 const base={phase:'staged',pendingSmartTools:[runner],blockers:[{kind:'conversation',sessionId:'one'}]};
 const result=updateOverview(base);
 assert.equal(result.title,'Finishing test runner update');
 assert.match(result.detail,/machine capacity/);assert.match(result.detail,/Existing chats and checks can continue/);
 assert.equal(result.runnerOnlyPending,true);assert.equal(result.busy,true);
 assert.match(updateOverview({...base,pendingSmartTools:[{...runner,runnerWaiting:'unknown'}]}).detail,/confirming the isolated check/);
 assert.match(updateOverview({...base,pendingApp:{}}).title,/chat to finish/);
 assert.match(updateOverview({...base,pendingSmartTools:[runner,{previous:'ordinary-tool'}]}).title,/chat to finish/);
 assert.equal(updateOverview({...base,phase:'error',error:'Qualification failed'}).tone,'error');
});

test('a waiting runner does not conceal an installable app or component update',()=>{
 const pendingSmartTools=[{runnerSource:{runnerKind:'managed-dtu-core'},runnerWaiting:'unknown'}];
 for(const available of [{application:{status:'update'}},{items:[{kind:'bundle / module',status:'update'}]}]){
  const result=updateOverview({phase:'available',pendingSmartTools,...available},{autoInstall:false});
  assert.equal(result.title,'Updates available');
  assert.equal(result.busy,false);assert.equal(result.waiting,false);
  assert.equal(result.installable,true);assert.equal(result.blocked,false);
 }
 const hot=updateOverview({phase:'staged',pendingSmartTools,pendingRelease:'candidate',plan:{activation:'per-worker'}});
 assert.equal(hot.title,'Finishing your update');assert.equal(hot.waiting,false);
 assert.equal(updateOverview({phase:'app-staged',pendingSmartTools,pendingApp:{},sequence:{nextStage:'included'}}).waiting,true);
});

const unresolvedComponent={id:'updates:failure:component',page:'updates',actionRequired:true,
 title:'Component update needs attention',detail:'Saved component issue remains unresolved.'};
test('one current status owns attention, app result and retry boundaries after reload',()=>{
 const healthy={phase:'checked',application:componentFailure.application,applicationResult:confirmedApp,sequence:{stage:'complete'}};
 const restore=value=>JSON.parse(JSON.stringify(value));
 for(const saved of [healthy,restore(healthy)]){
  const issue=updateOverview(saved,{},[unresolvedComponent]);
  assert.equal(issue.tone,'ready');
  assert.notEqual(issue.title,unresolvedComponent.title);
  assert.equal(issue.application.state,'current');
  assert.equal(issue.applicationResult.version,confirmedApp.version);
  assert.equal(issue.busy,false);
 }
 const retry=updateOverview({...healthy,phase:'validating',sequence:{stage:'other',install:true}}, {}, [unresolvedComponent]);
 assert.equal(retry.tone,'working');
 assert.equal(retry.attention.length,0); // History is not another current-health registry.
 assert.notEqual(retry.title,unresolvedComponent.title);
 const continuation=updateOverview({...healthy,sequence:{stage:'application',nextStage:'included',install:true}}, {}, [unresolvedComponent]);
 assert.equal(continuation.tone,'working');assert.equal(continuation.busy,true);
 assert.equal(updateOverview(restore(healthy),{},[]).tone,'ready');
 for(const pending of [{pendingRestart:{requestStatus:'uncertain'}},{pendingReplacement:{}}]){
  const result=updateOverview(restore({...healthy,...pending}),{},[unresolvedComponent]);
  assert.equal(result.applicationResult,null);
  assert.notEqual(result.application.state,'current');
  assert.notEqual(result.title,unresolvedComponent.title);
  assert.equal(result.busy,true);
 }
});
test('the exact current component failure wins over an older actionable episode',()=>{
 const result=updateOverview(componentFailure,{},[unresolvedComponent]);
 assert.equal(result.title,'Some components were not updated');
 assert.match(result.detail,/foundation/);
 assert.match(result.detail,/Check for updates again/);
 assert.equal(result.application.state,'current');
 assert.equal(result.attention.length,0);
});

test('failed optional tool stays actionable without holding independent app or component work',()=>{
 const pendingSmartTools=[{previous:'stories-old',failure:{reason:'smart-tool-update-failed'}}];
 const result=updateOverview({phase:'checked',sequence:{stage:'complete'},pendingSmartTools});
 assert.equal(result.title,'Some tools were not updated');
 assert.equal(result.tone,'error');assert.equal(result.pending,false);assert.equal(result.busy,false);assert.equal(result.waiting,false);
 for(const available of [{application:{status:'update'}},{items:[{kind:'smart-tool',status:'update'}]}]){
  const next=updateOverview({phase:'available',pendingSmartTools,...available});
  assert.equal(next.title,'Updates available');assert.equal(next.busy,false);assert.equal(next.blocked,false);
 }
 const hot=updateOverview({phase:'staged',pendingSmartTools,pendingRelease:'candidate',plan:{activation:'per-worker'}});
 assert.equal(hot.title,'Finishing your update');assert.equal(hot.waiting,false);
 const mixed=updateOverview({phase:'staged',pendingSmartTools:[...pendingSmartTools,{previous:'healthy'}]});
 assert.equal(mixed.toolResultsIndependent,false);assert.equal(mixed.waiting,true);
});
