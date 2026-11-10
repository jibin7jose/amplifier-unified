import {test} from 'node:test';
import assert from 'node:assert/strict';
import {selectedMetrics,metricsVisible,metricExport,metricState} from '../src/execution-observability.js';
import {usageMetrics} from '../src/usage-details.js';

test('explicit scope choices stay independent of detail level and one another',()=>{
 const p={interfaceDetail:'minimal',executionVisibility:{step:true,turn:false,conversation:true},executionMetrics:['costUsd']};
 assert.equal(metricsVisible(p,'step'),true);assert.equal(metricsVisible(p,'turn'),false);assert.equal(metricsVisible(p,'conversation'),true);
 assert.equal(metricsVisible({...p,interfaceDetail:'detailed'},'turn'),false);
 assert.deepEqual(selectedMetrics({...p,executionMetrics:[]}),[]);
 assert.deepEqual(selectedMetrics({executionMetrics:['costUsd','secret']}),['costUsd']);
});
test('selected export is a receipt projection with attribution, never payload or another sum',()=>{
 const row={id:'child-call',sessionId:'child',turnId:'parent-turn',provider:'fixture',model:'finite',phase:'cancelled',startedAt:5,endedAt:7,calls:1,request:{key:'secret'},reasoning:'private',aggregateUsage:{totalTokens:999}};
 const metrics=usageMetrics({inputTokens:100,outputTokens:20,cacheReadTokens:50,cacheWriteTokens:10,reasoningTokens:8,costUsd:.2,costType:'estimated'});
 const out=metricExport({scope:'step',record:row,metrics,presentation:{executionMetrics:['grossTotalTokens','costUsd','elapsedSeconds']},observedAt:9});
 assert.deepEqual(Object.keys(out.metrics),['grossTotalTokens','costUsd','elapsedSeconds']);
 assert.equal(out.metrics.grossTotalTokens.value,130);assert.equal(out.metrics.elapsedSeconds.value,2);assert.equal(out.identity.sessionId,'child');assert.equal(out.identity.phase,'cancelled');
 assert.equal(out.metrics.costUsd.estimatedCalls,1);assert.ok(!JSON.stringify(out).includes('secret'));assert.ok(!JSON.stringify(out).includes('private'));
 assert.deepEqual(metricExport({scope:'step',record:row,metrics,presentation:{executionMetrics:['grossTotalTokens','costUsd','elapsedSeconds']},observedAt:9}),out,'rendering again cannot increment usage');
});
test('missing, reported zero, partial and live duration are distinct',()=>{
 const out=metricExport({scope:'turn',metrics:{costUsd:{value:0,status:'partial',knownCalls:1,unknownCalls:2}},presentation:{executionMetrics:['costUsd','reasoningTokens','elapsedSeconds']},record:{phase:'running',startedAt:5},observedAt:9});
 assert.equal(out.metrics.costUsd.value,0);assert.equal(metricState(out.metrics.costUsd),'Partial');assert.equal(metricState(out.metrics.reasoningTokens),'Unavailable');
 assert.deepEqual(out.metrics.elapsedSeconds,{value:4,status:'pending',unit:'seconds'});assert.equal(metricState(out.metrics.elapsedSeconds),'Live');
 assert.equal(metricExport({record:{phase:'cancelled',startedAt:5},observedAt:9,presentation:{executionMetrics:['elapsedSeconds']}}).metrics.elapsedSeconds.value,null);
});

test('selected voice exports preserve the last duration and incomplete state without inventing cost',()=>{
 const metrics=usageMetrics({voiceSeconds:37.5,usageFinalized:false,usageStatus:'incomplete'});
 const out=metricExport({scope:'step',metrics,presentation:{executionMetrics:['voiceSeconds','costUsd']}});
 assert.deepEqual(out.metrics.voiceSeconds,{value:37.5,unit:'seconds',knownCalls:1,pendingCalls:0,incompleteCalls:1,status:'partial'});
 assert.equal(metricState(out.metrics.voiceSeconds),'Incomplete');assert.equal(out.metrics.costUsd.value,null);
 assert.equal(metricState(usageMetrics({voiceSeconds:38,usageFinalized:false,usageStatus:'running'}).voiceSeconds),'Live');
 assert.ok(selectedMetrics({}).includes('voiceSeconds'));
});
