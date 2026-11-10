import test from 'node:test';
import assert from 'node:assert/strict';
import {liveActivity,visibleWorkers} from '../src/activity.js';

test('current runtime event history exposes five parallel delegates without private content',()=>{
 const events=Array.from({length:5},(_,i)=>({type:'tool.pre',call_id:`call-${i}`,tool:'delegate',time:100+i,arguments:'private input'}));
 const activity=liveActivity({status:'working',messages:[{role:'user',createdAt:100}],runtimeEvents:events},167000);
 assert.equal(activity.label,'5 tool calls pending');assert.deepEqual(activity.toolLabels,['delegate × 5']);assert.equal(activity.elapsed,'1m 07s');assert.ok(!JSON.stringify(activity).includes('private input'));
});
test('finished calls stop appearing active while queued delegate jobs continue',()=>{
 const session={status:'working',runtimeEvents:[{type:'tool.pre',call_id:'a',tool:'delegate'},{type:'job.queued',call_id:'a',tool:'delegate'},{type:'tool.post',call_id:'a',tool:'delegate'}]};
 assert.equal(liveActivity(session).label,'1 tool call pending');
 session.runtimeEvents.push({type:'job.returned',call_id:'a'});
 assert.equal(liveActivity(session).phase,'composing');
});
test('server activity takes precedence and active workers remain visible while main session idles',()=>{
 const active=liveActivity({status:'working',activity:{activeTools:[{callId:'b',tool:'read_file'}],startedAt:200},runtimeEvents:[{type:'tool.pre',call_id:'old',tool:'old'}]},205000);
 assert.deepEqual(active.toolLabels,['read_file']);assert.equal(active.elapsed,'5s');
 assert.equal(liveActivity({status:'idle',workers:[{status:'running'}]}).label,'1 worker lane is active');
 assert.equal(liveActivity({status:'idle',workers:[]}),null);
});

test('job and child session rows with the same call identify one worker and the session stop target',()=>{
 const rows=[{id:'job-a',callId:'a',kind:'job',status:'running'},{id:'session-a',callId:'a',kind:'session',status:'running'},{id:'job-b',callId:'b',kind:'job',status:'running'}];
 assert.deepEqual(visibleWorkers(rows).map(w=>w.id),['session-a','job-b']);
 assert.equal(liveActivity({status:'working',workers:rows}).workerCount,2);
});
test('installed backend runtime.tool events work without requiring a backend restart',()=>{
 const session={status:'working',runtimeEvents:[{type:'runtime.tool',phase:'pre',callId:'x',tool:'delegate'}],messages:[{role:'user',createdAt:100}]};
 assert.equal(liveActivity(session,110000).label,'1 tool call pending');assert.equal(liveActivity(session,110000).elapsed,'10s');
 session.runtimeEvents.push({type:'runtime.tool',phase:'post',callId:'x',tool:'delegate'});
 assert.equal(liveActivity(session,110000).phase,'composing');
});

test('provider retry status takes precedence over pending tools and counts preparing workers',()=>{
 const result=liveActivity({status:'working',activity:{phase:'retrying',label:'Provider retry 4 of 5',activeTools:[{callId:'a',tool:'delegate'}]},workers:[{id:'b',status:'preparing'},{id:'c',status:'retrying'}]});
 assert.equal(result.phase,'retrying');assert.equal(result.label,'Provider retry 4 of 5');assert.equal(result.workerCount,2);
});

test('terminal job outcomes override stale session progress for the same call',()=>{
 for(const [event,status] of [['job.returned','completed'],['job.cancelled','cancelled'],['job.failed','error']]){
  const workers=[{id:'job',kind:'job',callId:'call',event,status},{id:'child',kind:'session',callId:'call',status:'running',phase:'retrying'}];
  assert.equal(visibleWorkers(workers)[0].id,'child');assert.equal(visibleWorkers(workers)[0].status,status);
  assert.equal(liveActivity({status:'idle',workers}),null);
  assert.equal(liveActivity({status:'idle',workers:[...workers,{id:'other',kind:'session',callId:'new-call',status:'running'}]}).workerCount,1);
 }
});
