import test from 'node:test';
import assert from 'node:assert/strict';
import {createViewReporter} from '../src/view-reporter.js';

test('one in-flight observation retains only the newest pending screen',async()=>{
 const sent=[],resolves=[];
 const report=createViewReporter(value=>{sent.push(value);return new Promise(resolve=>resolves.push(resolve))});
 const settled=report({screen:'first'});
 for(let index=0;index<100;index++)report({screen:`pending-${index}`});
 assert.deepEqual(sent,[{screen:'first'}]);
 resolves.shift()();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(sent,[{screen:'first'},{screen:'pending-99'}]);
 resolves.shift()();await settled;
 await report({screen:'pending-99'});
 assert.equal(sent.length,2);
});

test('duplicate in-flight reports deduplicate, failures can retry, and payloads are captured',async()=>{
 let finish;const sent=[],errors=[];
 const report=createViewReporter(value=>{sent.push(value);return new Promise((resolve,reject)=>finish={resolve,reject})},error=>errors.push(error));
 const payload={controls:[{label:'Current'}]},first=report(payload);
 payload.controls[0].label='Changed';
 report({controls:[{label:'Current'}]});
 finish.resolve();await first;
 assert.deepEqual(sent,[{controls:[{label:'Current'}]}]);
 const failed=report({controls:[{label:'Next'}]});
 finish.reject(new Error('offline'));await failed;
 assert.equal(errors.length,1);
 const retried=report({controls:[{label:'Next'}]});
 finish.resolve();await retried;
 assert.equal(sent.length,3);
});

test('reconnect resends unchanged observations and rejects old acknowledgments',async()=>{
 let finish;const sent=[];
 const report=createViewReporter(value=>{sent.push(value);return new Promise(resolve=>finish=resolve)});
 const payload={controls:[{label:'Send'}]},first=report(payload);
 report.invalidate();report(payload);finish();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(sent.length,2);finish();await first;
 await report(payload);assert.equal(sent.length,2);
 report.invalidate();const next=report(payload);assert.equal(sent.length,3);finish();await next;
});
