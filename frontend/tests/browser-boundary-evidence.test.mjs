import assert from 'node:assert/strict';
import test from 'node:test';
import {performance} from 'node:perf_hooks';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {createHook} from 'node:async_hooks';
import {bounded} from './chat-reference-harness.mjs';
import {boundaryRead,openBoundaryHandle,closeBoundaryHandle,boundaryIdentity,boundaryRequests,boundaryLease,
 BOUNDARY_OBSERVATION_MS} from './browser-boundary-evidence.mjs';

const never=()=>new Promise(()=>{});
const deferred=()=>{
 let resolve,reject;
 const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
 return {promise,resolve,reject};
};

test('never-settling optional setup and snapshots return a deadline, not cancellation',{timeout:2500},async()=>{
 for(const read of [()=>openBoundaryHandle(never),()=>boundaryRead(never)]){
  const start=performance.now(),result=await read(),elapsed=performance.now()-start;
  assert.deepEqual(result,{status:'timed-out'});
  assert.ok(elapsed>=BOUNDARY_OBSERVATION_MS-25&&elapsed<1000,`responsive event loop: ${elapsed}ms`);
 }
});

for(const hung of ['evaluate','dispose','both']){
 test(`finalization with hung ${hung} preserves the original failure and reaches outer cleanup`,{timeout:2500},async()=>{
  const calls=[],original=Object.freeze(Error('original UI assertion'));
  const handle={
   evaluate(fn){calls.push('evaluate');return hung==='evaluate'||hung==='both'?never():Promise.resolve(fn({finish:()=>({records:[],omitted:0})}))},
   dispose(){calls.push('dispose');return hung==='dispose'||hung==='both'?never():Promise.resolve()},
  };
  let result,cleanup=false;const start=performance.now();
  await assert.rejects(async()=>{
   try{try{throw original}finally{result=await closeBoundaryHandle(handle)}}finally{cleanup=true}
  },error=>error===original);
  assert.deepEqual(calls,['evaluate','dispose']);assert.equal(cleanup,true);
  assert.equal(result.events.status,hung==='dispose'?'observed':'timed-out');
  assert.equal(result.disposal.status,hung==='evaluate'?'observed':'timed-out');
  assert.ok(performance.now()-start<1500,'Two bounded operations, never join a hung evaluation');
 });
}

for(const outcome of ['resolve','reject']){
 test(`late snapshot ${outcome} cannot rewrite timed-out facts or reject unhandled`,{timeout:1500},async t=>{
  const operation=deferred(),unhandled=[];
  const listen=error=>unhandled.push(error);
  process.on('unhandledRejection',listen);t.after(()=>process.off('unhandledRejection',listen));
  const result=await boundaryRead(()=>operation.promise),saved=JSON.stringify(result);
  if(outcome==='resolve')operation.resolve({private:'not copied after timeout'});
  else operation.reject(Error('private late rejection'));
  await nextTurn();await nextTurn();
  assert.equal(JSON.stringify(result),saved);assert.deepEqual(result,{status:'timed-out'});
  assert.deepEqual(unhandled,[]);
 });
}

test('late finalization and disposal rejection cannot rewrite the receipt or become unhandled',{timeout:2000},async t=>{
 const events=deferred(),disposal=deferred(),unhandled=[],listen=error=>unhandled.push(error);
 process.on('unhandledRejection',listen);t.after(()=>process.off('unhandledRejection',listen));
 const result=await closeBoundaryHandle({evaluate:()=>events.promise,dispose:()=>disposal.promise});
 const saved=JSON.stringify(result);
 assert.equal(result.events.status,'timed-out');assert.equal(result.disposal.status,'timed-out');
 events.reject(Error('private late event cleanup'));disposal.reject(Error('private late disposal'));
 await nextTurn();await nextTurn();
 assert.equal(JSON.stringify(result),saved);assert.deepEqual(unhandled,[]);
});

test('late setup handle gets one bounded cleanup without changing its unavailable receipt',{timeout:2000},async()=>{
 const operation=deferred(),disposed=deferred(),calls=[];
 const result=await openBoundaryHandle(()=>operation.promise);
 assert.deepEqual(result,{status:'timed-out'});
 const saved=JSON.stringify(result);
 operation.resolve({evaluate(){calls.push('evaluate');return never()},dispose(){calls.push('dispose');disposed.resolve()}});
 await bounded(disposed.promise,1000,'Late handle cleanup was not attempted');
 assert.deepEqual(calls,['evaluate','dispose']);
 assert.equal(JSON.stringify(result),saved);
});

test('late setup rejection is handled and never manufactures a handle',{timeout:1500},async t=>{
 const operation=deferred(),unhandled=[],listen=error=>unhandled.push(error);
 process.on('unhandledRejection',listen);t.after(()=>process.off('unhandledRejection',listen));
 const result=await openBoundaryHandle(()=>operation.promise);
 operation.reject(Error('late private setup rejection'));await nextTurn();await nextTurn();
 assert.deepEqual(result,{status:'timed-out'});assert.deepEqual(unhandled,[]);
});

test('settled success and failure clear all observation timers',async()=>{
 const timers=new Set(),destroyed=new Set();
 const hook=createHook({init(id,type){if(type==='Timeout')timers.add(id)},destroy(id){destroyed.add(id)}});
 hook.enable();
 try{
  assert.deepEqual(await boundaryRead(()=>42),{status:'observed',value:42});
  assert.deepEqual(await boundaryRead(()=>{throw Error('private')}),{status:'unavailable'});
  assert.deepEqual(await boundaryRead(()=>Promise.reject(Error('private'))),{status:'unavailable'});
  await nextTurn();await nextTurn();
  assert.equal(timers.size,3);
  assert.ok([...timers].every(id=>destroyed.has(id)));
 }finally{hook.disable()}
});

const expected={resourceId:'artifact',version:1,questionId:'question',questionContentRevision:2};
for(const key of Object.keys(expected)){
 test(`wrong and missing fade ${key} remain candidates, never false absence`,()=>{
  const wrong={...expected,[key]:typeof expected[key]==='number'?999:'other'};
  const missing={...expected};delete missing[key];
  const rows=[{action:'canvas.select',identityMatch:boundaryIdentity(wrong,expected)}];
  assert.equal(rows[0].identityMatch[key],false);
  assert.deepEqual(boundaryRequests(rows,'canvas.select',false),
   {status:'mismatched-observed',candidates:1,matching:0,incomplete:0,complete:true});
  rows.push({action:'canvas.select',identityMatch:boundaryIdentity(missing,expected)});
  assert.equal(rows[1].identityMatch[key],null);
  assert.equal(boundaryRequests(rows,'canvas.select',false).status,'incomplete-unknown');
 });
}

test('wrong or missing send owner differs from no request and capped observation',()=>{
 const row=value=>({action:'conversation.send',identityMatch:boundaryIdentity({sessionId:value},{sessionId:'owner'})});
 assert.equal(boundaryRequests([row('other')],'conversation.send',false).status,'mismatched-observed');
 assert.equal(boundaryRequests([row(null)],'conversation.send',false).status,'incomplete-unknown');
 assert.equal(boundaryRequests([],'conversation.send',false).status,'none-observed');
 assert.equal(boundaryRequests([],'conversation.send',true).status,'unknown');
 assert.equal(boundaryRequests([row('other')],'conversation.send',true).status,'unknown');
 assert.deepEqual(boundaryRequests([row('owner'),row('other')],'conversation.send',true),
  {status:'matching-observed',candidates:2,matching:1,incomplete:0,complete:false});
});

const lease=()=>({action:'canvas.views.inline',identityMatch:boundaryIdentity(expected,expected),responseState:'observed',
 response:{accepted:true,result:{viewId:'inline-1',resourceId:'artifact',resourceRevision:'revision',generation:0}}});
const resource={args:{viewId:'inline-1',resourceId:'artifact',resourceRevision:'revision',generation:0}};
for(const state of ['not-observed','body-pending','body-unreadable','body-size-unavailable-or-over-limit','request-failed']){
 test(`lease association is unknown for ${state}`,()=>{
  const row=lease();row.responseState=state;delete row.response;
  assert.equal(boundaryLease(resource,[row],false),null);
 });
}
test('lease association requires an exact accepted identity; complete mismatch is separate from unknown',()=>{
 const row=lease();
 assert.equal(boundaryLease(resource,[row],false),true);
 assert.equal(boundaryLease(resource,[],false),null);
 row.identityMatch.questionId=false;assert.equal(boundaryLease(resource,[row],false),null);
 row.identityMatch.questionId=true;row.response.result.viewId='other';
 assert.equal(boundaryLease(resource,[row],false),false);
 assert.equal(boundaryLease(resource,[row],true),null);
 delete row.response.result.resourceId;assert.equal(boundaryLease(resource,[row],false),null);
});