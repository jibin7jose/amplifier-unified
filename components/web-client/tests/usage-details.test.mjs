import {test} from 'node:test';
import assert from 'node:assert/strict';
import {usageMetrics,cachedPercent,compactBreakdown,usageRows} from '../src/usage-details.js';
import {segmentUsage,combinedWork} from '../src/timeline-data.js';

test('CLI-compatible totals include writes once and never re-add cache reads or reasoning',()=>{
 const usage={inputTokens:95000,cacheReadTokens:80000,cacheWriteTokens:5000,outputTokens:1000,reasoningTokens:400,costUsd:1.62,costType:'reported'};
 const metrics=usageMetrics(usage);
 assert.equal(metrics.grossInputTokens.value,100000);assert.equal(metrics.grossTotalTokens.value,101000);
 assert.equal(cachedPercent(metrics),'80%');assert.equal(metrics.reasoningTokens.value,400);
 assert.equal(compactBreakdown(usage),'In 100k (80% cached) · Out 1k');
});
test('missing usage is inspectable',()=>{assert.equal(usageMetrics(null).grossTotalTokens.value,null)});

test('zero, unavailable, pending and mismatched coverage do not invent cache percentages',()=>{
 assert.equal(cachedPercent(usageMetrics({inputTokens:10,cacheReadTokens:0})),'0%');
 assert.equal(cachedPercent(usageMetrics({inputTokens:0,cacheReadTokens:0})),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:10})),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:10,cacheReadTokens:11})),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:1000,cacheReadTokens:1})),'<1%');
 const partial={inputTokens:10,cacheReadTokens:2,calls:2,metricKnownCalls:{inputTokens:2,cacheReadTokens:1}};
 assert.equal(cachedPercent(usageMetrics(partial)),null);
 assert.equal(usageMetrics({costUsd:0,costType:'unavailable'}).costUsd.value,null);
 assert.equal(usageMetrics({costUsd:0,costType:'reported'}).costUsd.value,0);
});
test('segmented and combined work carry metric coverage and reasoning without inventing zero',()=>{
 const one=segmentUsage([{kind:'llm',usage:{inputTokens:10,outputTokens:2,cacheReadTokens:5,reasoningTokens:1}}]);
 const two=segmentUsage([{kind:'llm',usage:{inputTokens:30,outputTokens:3}}]);
 const merged=combinedWork([{aggregateUsage:one},{aggregateUsage:two}],100).usage,metrics=usageMetrics(merged);
 assert.equal(metrics.grossTotalTokens.value,45);assert.equal(metrics.reasoningTokens.value,1);
 assert.equal(metrics.reasoningTokens.status,'partial');assert.equal(cachedPercent(metrics),null);
 assert.equal(usageMetrics(two).cacheReadTokens.value,null);
});

test('cache writes use the same input denominator and suppress incomplete percentages',()=>{
 assert.equal(cachedPercent(usageMetrics({inputTokens:95,cacheWriteTokens:5}),'cacheWriteTokens'),'5%');
 assert.equal(cachedPercent(usageMetrics({inputTokens:95,cacheWriteTokens:0}),'cacheWriteTokens'),'0%');
 assert.equal(cachedPercent(usageMetrics({inputTokens:95}),'cacheWriteTokens'),null);
 assert.equal(cachedPercent(usageMetrics({inputTokens:95,cacheWriteTokens:5,calls:2,metricKnownCalls:{inputTokens:2,cacheWriteTokens:1}}),'cacheWriteTokens'),null);
});

test('available metrics render without placeholders and new results appear as calls resolve',()=>{
 const pending={calls:1,tokenUnknownCalls:1,tokenPendingCalls:1,costPendingCalls:1,costType:'unavailable',costUsd:0,totalTokens:0};
 assert.deepEqual(usageRows(usageMetrics(pending)),[]);
 const available=segmentUsage([{kind:'llm',usage:{inputTokens:10,outputTokens:0,costUsd:0,costType:'reported'}},{kind:'llm',phase:'running'}]);
 assert.equal(compactBreakdown(available),'In 10 · Out 0');
 assert.deepEqual(usageRows(usageMetrics(available)).map(row=>row.label),['Input tokens','Output tokens','Total tokens','Cost (USD)']);
 const resolved=segmentUsage([{kind:'llm',usage:{inputTokens:10,outputTokens:0,costUsd:0,costType:'reported'}},{kind:'llm',usage:{inputTokens:20,outputTokens:5,reasoningTokens:2,costUsd:1,costType:'reported'}}]);
 assert.equal(compactBreakdown(resolved),'In 30 · Out 5');
 assert.equal(usageRows(usageMetrics(resolved)).find(row=>row.key==='reasoningTokens').metric.value,2);
});

test('voice duration stays separate from backend tokens through segment and combined summaries',()=>{
 const voice=segmentUsage([{kind:'llm',phase:'incomplete',usage:{voiceSeconds:12.5,usageFinalized:false,usageStatus:'incomplete'}}]);
 const backend=segmentUsage([{kind:'llm',phase:'completed',usage:{inputTokens:10,outputTokens:2}}]);
 const metrics=usageMetrics(combinedWork([{aggregateUsage:voice},{aggregateUsage:backend}],100).usage);
 assert.deepEqual(metrics.voiceSeconds,{value:12.5,unit:'seconds',knownCalls:1,pendingCalls:0,incompleteCalls:1,status:'partial'});
 assert.equal(metrics.grossTotalTokens.value,12);assert.equal(metrics.costUsd.value,null);
 assert.equal(usageMetrics(voice).grossTotalTokens.value,null);
 assert.equal(usageRows(metrics).find(row=>row.key==='voiceSeconds').label,'Voice seconds');
 const missing=segmentUsage([{kind:'llm',phase:'incomplete',usage:{usageFinalized:false,usageStatus:'incomplete'}}]);
 const mixed=usageMetrics(combinedWork([{aggregateUsage:voice},{aggregateUsage:missing}],100).usage).voiceSeconds;
 assert.equal(mixed.value,12.5);assert.equal(mixed.incompleteCalls,2);
});

test('reported zero, live duration and incomplete finalization remain distinct',()=>{
 const live=usageMetrics({voiceSeconds:12.5,usageFinalized:false,usageStatus:'running'}).voiceSeconds;
 assert.equal(live.status,'pending');assert.equal(live.incompleteCalls,0);
 const final=usageMetrics({voiceSeconds:0,usageFinalized:true,usageStatus:'final'}).voiceSeconds;
 assert.equal(final.value,0);assert.equal(final.status,'known');
 const missing=usageRows(usageMetrics({usageFinalized:false,usageStatus:'incomplete'}));
 assert.equal(missing.length,1);assert.equal(missing[0].metric.value,null);assert.equal(missing[0].metric.incompleteCalls,1);
 assert.equal(usageMetrics({totalTokens:12}).voiceSeconds,undefined);
 assert.equal(usageRows(usageMetrics({totalTokens:12}),undefined,{includeUnavailable:true}).some(row=>row.key==='voiceSeconds'),false);
});
