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
  assert.equal(issue.tone,'error');
  assert.equal(issue.title,unresolvedComponent.title);
  assert.equal(issue.application.state,'current');
  assert.equal(issue.applicationResult.version,confirmedApp.version);
  assert.equal(issue.busy,false);
 }
 const retry=updateOverview({...healthy,phase:'validating',sequence:{stage:'other',install:true}}, {}, [unresolvedComponent]);
 assert.equal(retry.tone,'working');
 assert.equal(retry.attention.length,1); // Preparing a retry does not resolve its old issue.
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
 assert.equal(result.attention.length,1);
});
