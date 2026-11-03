import assert from 'node:assert/strict';
import test from 'node:test';
import {createHook} from 'node:async_hooks';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {performance} from 'node:perf_hooks';
import {createSelectionEvidence,observeOptional,OPTIONAL_OBSERVATION_MS} from './selection-failure-evidence.mjs';

const pending=()=>{
 let resolve,reject;
 const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
 return {promise,resolve,reject};
};
const bind=(evidence,page,stage='source-revision-before-admission')=>
 evidence.binding(page,'selectContents',stage,'p:nth-of-type(2)');
const timeoutError=()=>Object.freeze(Object.assign(Error('Original quote action timeout'),{name:'TimeoutError'}));

test('never-settling evaluate expires, rethrows the same error and reaches finally', {timeout:2000},async()=>{
 const evidence=createSelectionEvidence(),error=timeoutError();
 let calls=0,cleanup=false;
 const page={evaluate(){calls++;return new Promise(()=>{})}};
 const binding=bind(evidence,page),started=performance.now();
 await assert.rejects(async()=>{
  try{
   try{throw error}
   catch(original){await evidence.observeFailure(page,original,'late-click');throw original}
  }finally{cleanup=true}
 },caught=>caught===error);
 const elapsed=performance.now()-started;
 assert.ok(elapsed>=OPTIONAL_OBSERVATION_MS-25&&elapsed<1000,`normal-event-loop elapsed ${elapsed}ms`);
 assert.equal(calls,1);assert.equal(cleanup,true);
 assert.deepEqual(evidence.observations,[{phase:'selection-timeout',...binding,
  available:false,reason:'observation_deadline',budgetMs:OPTIONAL_OBSERVATION_MS,failureStage:'late-click'}]);
 await evidence.observeFailure(page,error);
 assert.equal(calls,1,'Outer rethrow must not repeat the timed-out observation');
 assert.equal(evidence.observations.length,1);
});

for(const settlement of ['resolve','reject']){
 test(`late ${settlement} cannot rewrite unavailable facts or become unhandled`,{timeout:2000},async t=>{
  const evidence=createSelectionEvidence(),operation=pending(),unhandled=[];
  const onUnhandled=error=>unhandled.push(error);
  process.on('unhandledRejection',onUnhandled);t.after(()=>process.off('unhandledRejection',onUnhandled));
  const page={evaluate:()=>operation.promise},binding=bind(evidence,page);
  await evidence.observeFailure(page,timeoutError(),'original-stage');
  const saved=JSON.stringify(evidence.observations);
  if(settlement==='resolve')operation.resolve({...binding,available:true,exactTextMatch:true});
  else operation.reject(Error('Private late diagnostic error'));
  await nextTurn();await nextTurn();
  assert.equal(JSON.stringify(evidence.observations),saved);
  assert.deepEqual(unhandled,[]);
 });
}

test('deferred comparison timeout retains exact binding after main work and cap overflow',{timeout:2000},async()=>{
 const evidence=createSelectionEvidence(),operation=pending(),original=timeoutError();
 let comparisonReads=0,mainReads=0;
 const comparison={evaluate(){comparisonReads++;return operation.promise}};
 const main={evaluate(){mainReads++;throw Error('Do not substitute main-page facts')}};
 const expected=bind(evidence,comparison,'comparison-capture');
 const observation=evidence.observeFailure(comparison,original,'capture 390');
 // A later attempt cannot change the captured pre-await binding.
 bind(evidence,comparison,'newer-comparison-attempt');
 await observation;
 const first={...evidence.observations[0]};
 bind(evidence,main,'main-continued');
 for(let i=0;i<20;i++)evidence.retain('range-installed',{attempt:100+i});
 assert.equal(evidence.observations.length,16);assert.equal(evidence.omitted,5);
 await evidence.observeFailure(main,original);
 assert.deepEqual(evidence.observations.at(-1),first);
 assert.equal(evidence.observations.at(-1).attempt,expected.attempt);
 assert.equal(evidence.observations.at(-1).pageId,expected.pageId);
 assert.equal(evidence.observations.at(-1).failureStage,'capture 390');
 assert.equal(evidence.omitted,6);assert.equal(evidence.observations.length,16);
 operation.resolve({...expected,available:true});await nextTurn();
 assert.deepEqual(evidence.observations.at(-1),first);
 assert.equal(comparisonReads,1);assert.equal(mainReads,0);
});

test('a successful exact probe is retained without changing the error',async()=>{
 const evidence=createSelectionEvidence(),error=timeoutError();
 let expected;
 const page={evaluate:async()=>({...expected,available:true,exactTextMatch:false})};
 expected=bind(evidence,page);
 await evidence.observeFailure(page,error);
 assert.deepEqual(evidence.observations[0],{phase:'selection-timeout',...expected,
  available:true,exactTextMatch:false,failureStage:null});
 assert.deepEqual(Object.keys(error),['name']);
});

for(const field of ['pageId','attempt','helper','stage','selector']){
 test(`a mismatched ${field} cannot supply failure facts`,async()=>{
  const evidence=createSelectionEvidence();let expected;
  const page={evaluate:async()=>({...expected,[field]:'wrong',available:true})};
  expected=bind(evidence,page);
  await evidence.observeFailure(page,Error('Original'));
  assert.deepEqual(evidence.observations[0],{phase:'selection-failed',...expected,
   available:false,reason:'probe_not_bound',failureStage:null});
 });
}

test('unbound, missing, throwing and rejected probes retain only unavailable facts',async()=>{
 const evidence=createSelectionEvidence(),unbound={evaluate(){assert.fail('Unbound must not read')}};
 await evidence.observeFailure(unbound,Error('Original'),'before-page-ready');
 assert.equal(evidence.observations[0].reason,'attempt_unbound');
 assert.equal(evidence.observations[0].failureStage,'before-page-ready');
 for(const [reason,evaluate] of [
  ['probe_unavailable',async()=>null],
  ['observation_unavailable',()=>{throw Error('Private sync error')}],
  ['observation_unavailable',async()=>{throw Error('Private rejected error')}],
  ['observation_unavailable',async()=>({get pageId(){throw Error('Private invalid observation')}})],
 ]){
  const page={evaluate},expected=bind(evidence,page);
  await evidence.observeFailure(page,Error('Original'));
  assert.deepEqual(evidence.observations.at(-1),{phase:'selection-failed',...expected,
   available:false,reason,failureStage:null});
 }
 assert.ok(!JSON.stringify(evidence.observations).includes('Private'));
});

test('settled optional reads clear their deadline timers',async()=>{
 const created=new Set(),destroyed=new Set();
 const hook=createHook({
  init(identity,type){if(type==='Timeout')created.add(identity)},
  destroy(identity){destroyed.add(identity)},
 });
 hook.enable();
 try{
  assert.deepEqual(await observeOptional(()=>42),{status:'observed',value:42});
  assert.deepEqual(await observeOptional(()=>{throw Error('private')}),{status:'unavailable'});
  assert.deepEqual(await observeOptional(()=>Promise.reject(Error('private'))),{status:'unavailable'});
  await nextTurn();await nextTurn();
  assert.equal(created.size,3);
  for(const identity of created)assert.ok(destroyed.has(identity),'Deadline timer must be cleared');
 }finally{hook.disable()}
});

test('optional failure screenshot also expires without blocking original rethrow',{timeout:2000},async()=>{
 const original=timeoutError();let cleanup=false,receipt;
 await assert.rejects(async()=>{
  try{
   try{throw original}
   catch(error){
    const screenshot=await observeOptional(()=>new Promise(()=>{}));
    receipt={status:screenshot.status,budgetMs:OPTIONAL_OBSERVATION_MS};
    throw error;
   }
  }finally{cleanup=true}
 },error=>error===original);
 assert.equal(cleanup,true);
 assert.deepEqual(receipt,{status:'timed-out',budgetMs:OPTIONAL_OBSERVATION_MS});
});