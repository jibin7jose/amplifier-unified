import test from 'node:test';
import assert from 'node:assert/strict';
import {bounded,deferredRead,observeCompletion,requireCompletion,unknownCleanupValue} from './shared-work-async.mjs';

test('Cleanup value accepts explicit uncertainty but rejects None or leakage from another facet',()=>{
 for(const value of ['Unknown','Not recorded','Unknown / not recorded'])assert.match(value,unknownCleanupValue);
 for(const value of ['None','No resources','Cleanup complete','Human decision Not recorded Cleanup None','Not recorded elsewhere; Cleanup None'])assert.doesNotMatch(value,unknownCleanupValue);
});

test('handler observation supports both synchronous returns and held asynchronous completion',async()=>{
 const held=deferredRead();let secondSelected=false;
 const completion=observeCompletion((async()=>{await held.promise;assert.ok(secondSelected);return 'old result'})());
 try {
  // Collecting completion must not require resolving it before a second action.
  secondSelected=true;
  assert.equal(await requireCompletion(observeCompletion(undefined)),undefined);
  held.resolve();assert.equal(await bounded(requireCompletion(completion),1000,'Async handler control'),'old result');
 } finally {
  held.resolve();await bounded(requireCompletion(completion),1000,'Control settlement');
 }
});

test('handler rejection is observed immediately and remains a failure when joined',async()=>{
 const failure=Error('Handler control failed');
 const completion=observeCompletion(Promise.reject(failure));
 await assert.rejects(requireCompletion(completion),error=>error===failure);
});

test('finite barrier rejects a missing completion and owned deferred work can still settle',async()=>{
 const held=deferredRead();const completion=observeCompletion(held.promise);
 try {await assert.rejects(bounded(requireCompletion(completion),10,'Held control'),/Held control timed out/)}
 finally {held.resolve('released');assert.equal(await bounded(requireCompletion(completion),1000,'Cleanup control'),'released')}
});

