import {test} from 'node:test';
import assert from 'node:assert/strict';
import {usageMetric} from '../src/conversation-usage.js';
test('missing, partial, pending and estimated usage remain distinct from zero',()=>{
 assert.equal(usageMetric({value:null,status:'empty'}),'');
 assert.equal(usageMetric({value:null,status:'pending'}),'');
 assert.equal(usageMetric({value:null,status:'unknown'}),'');
 assert.equal(usageMetric({value:0,status:'known'},true),'$0');
 assert.match(usageMetric({value:1.25,estimatedCalls:1,pendingCalls:2,unknownCalls:3},true),/^\$1.25 · includes estimates$/);
});
test('available sums have no coverage label while additional calls resolve',()=>{
 for(const metric of [{value:120,status:'partial'},{value:120,pendingCalls:2,unknownCalls:3}])assert.equal(usageMetric(metric),'120');
});
