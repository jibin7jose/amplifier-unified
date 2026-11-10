import test from 'node:test';
import assert from 'node:assert/strict';
import {splitWork,turnPlacements} from '../src/timeline-data.js';

const messages=[{id:'user',role:'user',createdAt:1},{id:'interim',role:'assistant',createdAt:10},{id:'answer',role:'assistant',createdAt:20}];
const data={turns:[{id:'turn',anchorMessageId:'user',startedAt:2,endedAt:19,phase:'completed'}],nodes:[
 {id:'first-model',turnId:'turn',kind:'llm',startedAt:2,endedAt:4,phase:'completed',usage:{inputTokens:10,outputTokens:2,totalTokens:12,costUsd:.01}},
 {id:'first-tool',turnId:'turn',kind:'tool',startedAt:4,endedAt:5,phase:'completed',input:'one',output:'ok'},
 {id:'second-tool',turnId:'turn',kind:'tool',startedAt:11,endedAt:12,phase:'completed',input:'two',output:'ok'},
 {id:'second-model',turnId:'turn',kind:'llm',startedAt:13,endedAt:19,phase:'completed',usage:{inputTokens:20,outputTokens:3,totalTokens:23,costUsd:.02}},
]};
test('work splits around interim replies with disjoint timings usage and call counts',()=>{
 const grouped=splitWork(messages,data),placement=turnPlacements(messages,grouped);
 assert.equal(grouped.turns.length,2);assert.deepEqual(placement.after.get('user'),['turn@user']);assert.deepEqual(placement.after.get('interim'),['turn@interim']);assert.equal(placement.after.has('answer'),false);
 assert.deepEqual(grouped.turns.map(t=>[t.startedAt,t.endedAt]),[[2,5],[11,19]]);
 assert.deepEqual(grouped.turns.map(t=>t.aggregateUsage.totalTokens),[12,23]);
 assert.deepEqual(grouped.turns.map(t=>t.nodeCounts),[{tools:1,models:1},{tools:1,models:1}]);
 assert.equal(grouped.turns.reduce((n,t)=>n+t.aggregateUsage.costUsd,0),.03);
});
test('streaming new work does not rename earlier segments or shift them past an answer',()=>{
 const first=splitWork(messages.slice(0,2),{...data,nodes:data.nodes.slice(0,2)});
 const next=splitWork(messages,{...data,nodes:[...data.nodes,{id:'later',kind:'tool',turnId:'turn',phase:'running',startedAt:21}]});
 assert.equal(first.turns[0].id,next.turns[0].id);
 assert.equal(next.turns.at(-1).anchorMessageId,'answer');assert.equal(next.turns.at(-1).phase,'running');assert.equal(next.turns[0].phase,'completed');
});
test('canonical segment totals include earlier action pages without moving anchors',()=>{
 const grouped=splitWork(messages,{...data,nodes:[data.nodes[0]],segments:[{id:'turn@user',anchorMessageId:'user',startedAt:2,endedAt:9,phase:'completed',nodeCounts:{tools:90,models:100},aggregateUsage:{totalTokens:5000,costUsd:1}}]});
 assert.equal(grouped.turns[0].nodeCounts.models,100);assert.equal(grouped.turns[0].aggregateUsage.totalTokens,5000);assert.equal(grouped.turns[0].endedAt,9);
});
test('undated native transcript anchors take precedence over fallback timestamps',()=>{
 const grouped=splitWork(messages.map(m=>({...m,createdAt:0,timestampKnown:false})),{...data,nodes:[{...data.nodes[0],anchorMessageId:'interim'}]});
 assert.equal(grouped.turns[0].anchorMessageId,'interim');
});
for(const phase of ['error','failed','cancelled','interrupted','stopped'])test(`manager ${phase} survives a successful final model call`,()=>{
 const source={...data,turns:[{...data.turns[0],phase,endedAt:22}],nodes:[...data.nodes].reverse()};
 const before=JSON.stringify(source),grouped=splitWork(messages,source);
 const byAnchor=new Map(grouped.turns.map(turn=>[turn.anchorMessageId,turn]));
 assert.equal(byAnchor.get('user').phase,'completed');assert.equal(byAnchor.get('user').endedAt,5);
 assert.equal(byAnchor.get('interim').phase,phase);assert.equal(byAnchor.get('interim').endedAt,22);
 assert.equal(byAnchor.get('interim').aggregateUsage.totalTokens,23);
 assert.ok(grouped.nodes.every(node=>node.phase==='completed'));
 assert.equal(JSON.stringify(source),before);
});
test('earlier paged work uses canonical success even when the manager later failed',()=>{
 const grouped=splitWork(messages,{...data,turns:[{...data.turns[0],phase:'error'}],nodes:[data.nodes[0]],
  segments:[{id:'turn@user',phase:'completed',endedAt:5}]});
 assert.equal(grouped.turns[0].phase,'completed');assert.equal(grouped.turns[0].endedAt,5);
});
test('failed manager summary leaves independently running children intact',()=>{
 const grouped=splitWork(messages,{turns:[{id:'turn',phase:'error',endedAt:22}],
  nodes:[{id:'worker',turnId:'turn',kind:'worker',startedAt:21,phase:'running'}]});
 assert.equal(grouped.turns[0].phase,'error');assert.equal(grouped.nodes[0].phase,'running');
});
test('summary tokens include cache writes once while preserving raw counters',async()=>{
 const {usageLabel}=await import('../src/timeline-data.js');
 const usage={inputTokens:4,outputTokens:2,totalTokens:6,cacheReadTokens:3,cacheWriteTokens:100,costUsd:.001,costType:'reported'};
 assert.match(usageLabel(usage).text,/106 tokens/);assert.equal(usage.totalTokens,6);
 assert.match(usageLabel({...usage,grossTotalTokens:106}).text,/106 tokens/);
});

test('background naming never creates working rows, failures or costs in chat work',()=>{
 for(const phase of ['running','error','completed']){
  const naming={id:'naming',kind:'llm',turnId:'turn',startedAt:25,phase,lifecycle:'background',label:'Session naming',usage:{totalTokens:5,costUsd:.001}};
  const source={...data,nodes:[...data.nodes,naming]};
  const grouped=splitWork(messages,source);
  assert.deepEqual(grouped,splitWork(messages,data));
  assert.equal(source.nodes.at(-1),naming);
 }
});

for(const [kind,phase] of [['tool','error'],['worker','failed'],['llm','error'],['llm','cancelled']])test(`${kind} ${phase} stays on the action without failing a completed turn`,()=>{
 const source={...data,nodes:[{...data.nodes[0],kind,phase},...data.nodes.slice(1)]};
 const before=JSON.stringify(source),grouped=splitWork(messages,source);
 assert.ok(grouped.turns.every(turn=>turn.phase==='completed'));
 assert.equal(grouped.nodes[0].phase,phase);assert.equal(JSON.stringify(source),before);
});
test('latest segment stays active between calls, including after a tool error',()=>{
 const source={...data,turns:[{...data.turns[0],phase:'running',endedAt:undefined}],
  nodes:[{...data.nodes[0],kind:'tool',phase:'error'},...data.nodes.slice(1)]};
 const grouped=splitWork(messages,source);
 assert.equal(grouped.turns[0].phase,'completed');assert.equal(grouped.turns[1].phase,'running');
 assert.equal(grouped.turns[1].endedAt,undefined);assert.equal(grouped.nodes[0].phase,'error');
 source.turns[0].phase='completed';source.turns[0].endedAt=22;
 assert.ok(splitWork(messages,source).turns.every(turn=>turn.phase==='completed'));
});
test('an unknown turn outcome stays neutral after a failed tool',()=>{
 const grouped=splitWork(messages,{turns:[{id:'unknown'}],nodes:[{id:'tool',turnId:'unknown',kind:'tool',phase:'error',startedAt:2,endedAt:3}]});
 assert.equal(grouped.turns[0].phase,'recorded');assert.equal(grouped.nodes[0].phase,'error');
});
test('admitted work is active before its first action',()=>{
 const grouped=splitWork(messages,{turns:[{id:'new',anchorMessageId:'user',phase:'running',startedAt:2}],nodes:[]});
 assert.equal(grouped.turns[0].phase,'running');assert.equal(grouped.turns[0].endedAt,undefined);
});

test('deferred summaries keep completed work placement without loading any nodes',()=>{
 const grouped=splitWork(messages,{sessionId:'chat',detailsDeferred:true,nodes:[],turns:[{id:'t',phase:'completed'}],segments:[{id:'t@user',turnId:'t',anchorMessageId:'user',phase:'completed',startedAt:2,endedAt:3,detailRevision:'v1',nodeCounts:{tools:25,models:2}}]});
 assert.equal(grouped.turns.length,1);assert.equal(grouped.turns[0].detailRevision,'v1');assert.equal(grouped.turns[0].nodeCounts.tools,25);
 assert.deepEqual(grouped.nodes,[]);assert.equal(grouped.detailsDeferred,true);assert.equal(grouped.sessionId,'chat');
});
