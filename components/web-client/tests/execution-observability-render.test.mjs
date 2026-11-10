import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ShellContext}=await server.ssrLoadModule('/src/shell/runtime.jsx');
const {UsageDetails}=await server.ssrLoadModule('/src/usage-details.jsx');
const {MetricSummary}=await server.ssrLoadModule('/src/execution-observability.jsx');
const {ConversationUsage}=await server.ssrLoadModule('/src/conversation-usage.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let root;
test.afterEach(async()=>{await act(async()=>root?.unmount());root=null});test.after(()=>server.close());
const wrap=(presentation,child)=>React.createElement(ShellContext.Provider,{value:{composition:{presentation}}},child);
const text=()=>JSON.stringify(root.toJSON());
const details=(scope,presentation)=>wrap(presentation,React.createElement(UsageDetails,{scope,record:{id:'step',phase:'cancelled',startedAt:1,endedAt:3,calls:1},usage:{inputTokens:10,outputTokens:2,costUsd:.5,costType:'estimated'},label:'Selected metrics'}));
test('scope switches remove metrics but do not disclose excluded fields',async()=>{
 const presentation={executionVisibility:{step:false,turn:true},executionMetrics:['outputTokens','costUsd','elapsedSeconds']};
 await act(async()=>{root=create(details('step',presentation))});assert.equal(root.toJSON(),null);
 await act(async()=>root.update(details('turn',presentation)));assert.match(text(),/Output tokens/);assert.match(text(),/Elapsed seconds/);assert.ok(!text().includes('Input tokens'));assert.ok(!text().includes('Reasoning tokens'));
 await act(async()=>root.update(details('turn',{...presentation,executionMetrics:['reasoningTokens']})));assert.match(text(),/Unavailable/);assert.ok(!text().includes('$0.5'));
});
test('Focused retains explicitly opened accounting unless its scope was hidden',async()=>{
 await act(async()=>{root=create(details('step',{interfaceDetail:'minimal',executionMetrics:['outputTokens']}))});assert.match(text(),/Output tokens/);
});
test('compact conversation is demand-only and fences late results from previous chat',async()=>{
 const pending=[],dispatch=(name,args)=>new Promise(resolve=>pending.push({name,args,resolve}));
 const view=(id,status='working')=>wrap({},React.createElement(ConversationUsage,{sessionId:id,act:dispatch,compact:true,status}));
 await act(async()=>{root=create(view('one'))});assert.equal(pending.length,0);
 const button=()=>root.root.findAllByType('button').find(row=>row.props['data-action']==='capacity.read');
 await act(async()=>{button().props.onClick()});assert.equal(pending.length,1);
 await act(async()=>root.update(view('two')));assert.equal(pending.length,1);
 await act(async()=>pending[0].resolve({result:{observedAt:1,usage:{sessionId:'one',metrics:{},providers:[],calls:9}}}));assert.ok(!text().includes('9 recorded'));
 await act(async()=>{button().props.onClick()});assert.equal(pending[1].args.sessionId,'two');
 await act(async()=>pending[1].resolve({result:{observedAt:2,usage:{sessionId:'two',metrics:{},providers:[],calls:3}}}));assert.match(text(),/3/);
 await act(async()=>root.update(view('two','idle')));assert.equal(pending.length,2,'render updates do not poll');assert.match(text(),/idle/);assert.match(text(),/Usage snapshot/);assert.ok(text().includes(new Date(2000).toLocaleTimeString()),'retained usage stays explicitly dated after current status changes');
});
test('hidden conversation totals never request usage',async()=>{
 let count=0;await act(async()=>{root=create(wrap({executionVisibility:{conversation:false}},React.createElement(ConversationUsage,{sessionId:'one',act:()=>{count++}})))});assert.equal(count,0);assert.equal(root.toJSON(),null);
});

test('selected-only compact input/output keep partial and live state',async()=>{
 const view=(usage,pending=false)=>wrap({interfaceDetail:'detailed',executionMetrics:['grossInputTokens','outputTokens']},React.createElement(MetricSummary,{usage,pending}));
 await act(async()=>{root=create(view({calls:2,inputTokens:10,metricKnownCalls:{inputTokens:1}}))});assert.match(text(),/In 10 · Partial/);
 await act(async()=>root.update(view({outputTokens:3},true)));assert.match(text(),/Out 3 · Live/);
});

test('voice usage remains visible and incomplete after close without a zero token or price claim',async()=>{
 const usage={voiceSeconds:12.5,usageFinalized:false,usageStatus:'incomplete'};
 await act(async()=>{root=create(wrap({},React.createElement(UsageDetails,{usage})))});
 assert.match(text(),/Voice seconds/);assert.match(text(),/12.5/);assert.match(text(),/Incomplete/);
 assert.ok(!text().includes('Total tokens'));assert.ok(!text().includes('$'));
 await act(async()=>root.update(wrap({executionMetrics:['voiceSeconds']},React.createElement(MetricSummary,{usage}))));
 assert.match(text(),/voice seconds/);assert.match(text(),/Incomplete/);assert.ok(!text().includes('tokens'));
 await act(async()=>root.update(wrap({executionMetrics:['costUsd']},React.createElement(MetricSummary,{usage}))));
 assert.equal(root.toJSON(),null);
 await act(async()=>root.update(wrap({executionMetrics:['voiceSeconds']},React.createElement(MetricSummary,{usage:{voiceSeconds:12.5,usageFinalized:true,usageStatus:'final'},pending:true}))));
 assert.match(text(),/voice seconds/);assert.ok(!text().includes('Live'),'ongoing backend work cannot make a finalized voice duration live');
});
