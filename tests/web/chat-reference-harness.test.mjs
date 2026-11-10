import test from 'node:test';
import assert from 'node:assert/strict';
import {combinedFailure,observed,resultOf,withActionRoute} from './chat-reference-harness.mjs';

function deferred(){let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve}}
function fakePage(){
 const page={handler:null,pending:new Set(),events:[],lastArrival:null,
  async route(pattern,handler){assert.equal(pattern,'**/api/actions');this.handler=handler;this.events.push('registered')},
  invoke(route){
   assert.ok(this.handler,'route remains owned');
   const promise=this.handler(route);this.pending.add(promise);
   void promise.then(()=>this.pending.delete(promise));return promise;
  },
  async unrouteAll(options){
   assert.deepEqual(options,{behavior:'wait'});
   this.events.push('unregistering');
   if(this.lastArrival)this.invoke(this.lastArrival);
   await Promise.all([...this.pending]);
   this.handler=null;this.events.push('unregistered');
  },
 };
 return page;
}

test('held route stays blocked through exercise and drains once before unregistration',async()=>{
 const page=fakePage(),gate=deferred(),started=deferred(),trace=[];let continued=0;
 await withActionRoute(page,{label:'draft',trace,release:gate.resolve,
  handle:async route=>{started.resolve();await gate.promise;await route.continue()}},async()=>{
  page.invoke({continue:async()=>{continued++;assert.ok(page.handler)}});
  await started.promise;
  assert.equal(continued,0,'Draft is still held at the simulated click');
  assert.deepEqual(page.events,['registered']);
 });
 assert.equal(continued,1);assert.equal(page.pending.size,0);assert.equal(page.handler,null);
 assert.deepEqual(trace.map(row=>row.phase),['registered','started','gate-released','completed','drained','unregistered']);
});

test('drain includes concurrent callbacks and waiting unroute joins a final protocol arrival',async()=>{
 const page=fakePage(),gate=deferred(),started=deferred();let held=0,continued=0;
 const route=()=>({continue:async()=>{continued++}});
 page.lastArrival=route();
 await withActionRoute(page,{label:'several',release:gate.resolve,
  handle:async value=>{if(++held===2)started.resolve();await gate.promise;await value.continue()}},async()=>{
  page.invoke(route());page.invoke(route());await started.promise;assert.equal(continued,0);
 });
 assert.equal(continued,3);assert.equal(page.pending.size,0);
});

test('callback errors become an awaited failure, not ignored or retried',async()=>{
 const page=fakePage(),failure=Error('Route is already handled!');let attempts=0;
 await assert.rejects(withActionRoute(page,{label:'failed-continue',handle:route=>route.continue()},async()=>{
  await page.invoke({continue:async()=>{attempts++;throw failure}});
 }),error=>error===failure);
 assert.equal(attempts,1);assert.equal(page.handler,null);
});

test('primary assertion survives successful release and cleanup',async()=>{
 const page=fakePage(),gate=deferred(),started=deferred(),primary=Error('original draft assertion');let completed=false;
 await assert.rejects(withActionRoute(page,{label:'primary',release:gate.resolve,
  handle:async()=>{started.resolve();await gate.promise;completed=true}},async()=>{
  page.invoke({});await started.promise;throw primary;
 }),error=>error===primary);
 assert.ok(completed);assert.equal(page.handler,null);
});

test('primary error is retained with callback and teardown errors as secondary',async()=>{
 const page=fakePage(),primary=Error('original assertion'),callback=Error('continue rejected'),cleanup=Error('unroute rejected');
 const unroute=page.unrouteAll.bind(page);
 page.unrouteAll=async options=>{await unroute(options);throw cleanup};
 await assert.rejects(withActionRoute(page,{label:'multiple',handle:async()=>{throw callback}},async()=>{
  await page.invoke({});throw primary;
 }),error=>error instanceof AggregateError&&error.cause===primary&&
  error.errors[0]===primary&&error.errors[1]===callback&&error.errors[2]===cleanup);
});

test('early-started action rejection stays observable until it is awaited',async()=>{
 const failure=Error('delayed action failed');
 const outcome=observed(Promise.reject(failure));
 await Promise.resolve();
 await assert.rejects(resultOf(outcome),error=>error===failure);
 assert.equal(await resultOf(observed(Promise.resolve('accepted'))),'accepted');
});

test('cleanup failure alone fails and cannot replace a prior primary failure',()=>{
 const primary=Error('journey failed'),cleanup=Error('fixture exit failed');
 assert.equal(combinedFailure(null,[cleanup]),cleanup);
 const combined=combinedFailure(primary,[cleanup,cleanup]);
 assert.equal(combined.cause,primary);assert.deepEqual(combined.errors,[primary,cleanup]);
 assert.equal(combinedFailure(null,[]),null);
});
