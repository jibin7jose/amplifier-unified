import test from 'node:test';
import assert from 'node:assert/strict';
import {executionData,messageTurnId,treeForTurn,usageLabel,combinedWork,workLabel} from '../src/timeline-data.js';

test('stopped work remains terminal while independent running work remains visible',()=>{
 const stopped={phase:'stopped',startedAt:1,endedAt:10};
 assert.equal(combinedWork([stopped],20).phase,'stopped');
 const summary=combinedWork([stopped,{phase:'running',startedAt:11}],20);
 assert.equal(summary.running,true);assert.equal(summary.phase,'stopped');
 assert.equal(workLabel(summary.phase,summary.elapsed,summary.running),'Working · 18s');
 assert.equal(workLabel('completed','9s'),'Worked for 9s');
});

test('a focused empty modern window does not synthesize a partial legacy timeline',()=>{
 const session={id:'modern',workerSummary:{bounded:true,total:900},
  execution:{nodes:[],turns:[],detailsDeferred:true},workers:[{id:'active',status:'working'}]};
 assert.deepEqual(executionData(session).nodes,[]);
 assert.deepEqual(executionData(session).turns,[]);
 delete session.workerSummary;
 assert.equal(executionData(session).nodes.length,1,'older servers retain legacy behavior');
});

for(const [status,expected] of [['working','running'],['starting','running'],['idle','completed'],['error','error'],['failed','failed'],['stopped','stopped']])test(`legacy tool errors respect the host ${status} lifecycle`,()=>{
 const session={status,runtimeEvents:[{type:'runtime.tool',phase:'error',callId:'failed-fetch',tool:'web_fetch',at:100,error:'HTTP 404: Not Found'}]};
 const before=JSON.stringify(session),data=executionData(session);
 assert.equal(data.turns[0].status,expected);assert.equal(data.nodes[0].status,'error');assert.equal(JSON.stringify(session),before);
});
test('turns attach to their user input and preserve tool-worker-subtool hierarchy',()=>{
 const data={turns:[{id:'input-1'}],nodes:[{id:'tool-a',turnId:'input-1',kind:'tool'},{id:'worker-a',parentId:'tool-a',turnId:'input-1',kind:'worker'},{id:'tool-b',parentId:'worker-a',turnId:'input-1',kind:'tool'},{id:'llm-b',parentId:'tool-b',turnId:'input-1',kind:'llm'}]};
 assert.equal(messageTurnId({role:'user',inputId:'input-1'},data.turns),'input-1');
 assert.equal(messageTurnId({role:'assistant',inputId:'input-1'},data.turns),null);
 const tree=treeForTurn(data,'input-1');assert.deepEqual(tree.roots.map(n=>n.id),['tool-a']);assert.equal(tree.children.get('worker-a')[0].id,'tool-b');
});
test('cycles and orphan actions remain inspectable without recursive traversal failure',()=>{
 const data={nodes:[{id:'a',parentId:'b',turnId:'x'},{id:'b',parentId:'a',turnId:'x'},{id:'orphan',parentId:'missing',turnId:'x'}]};
 assert.equal(treeForTurn(data,'x').roots.length,2);
});
test('usage distinguishes actual, estimated, partial, and unavailable cost',()=>{
 assert.match(usageLabel({totalTokens:1500,costUsd:.012,costType:'reported'}).text,/1.5k tokens · \$0.012/);
 assert.match(usageLabel({totalTokens:1500,costUsd:.012,costType:'estimated'}).text,/≈\$/);
 const partial=usageLabel({totalTokens:1500,costUsd:.012,costType:'partial',calls:2,pricedCalls:1});assert.equal(partial.text,'1.5k tokens · $0.012');assert.doesNotMatch(partial.title,/partial|pending|awaiting|not reported|of 2/i);
 const unknown=usageLabel({calls:1,totalTokens:100,costUsd:0,costType:'unavailable'});assert.equal(unknown.text,'100 tokens');assert.ok(!unknown.text.includes('$0'));
 assert.equal(usageLabel({calls:0,totalTokens:0,costUsd:0,costType:'unavailable'}),null);
});
test('legacy worker and tool observations form one nested group without duplicating job/session records',()=>{
 const data=executionData({runtimeEvents:[{type:'runtime.tool',phase:'pre',callId:'call-a',tool:'delegate'}],workers:[{id:'job-a',callId:'call-a',kind:'job',status:'running'},{id:'session-a',sessionId:'session-a',callId:'call-a',kind:'session',status:'running'}]});
 assert.equal(data.nodes.filter(n=>n.kind==='worker').length,1);
 const worker=data.nodes.find(n=>n.kind==='worker');assert.equal(worker.parentId,'tool:call-a');assert.equal(worker.workerId,'session-a');assert.equal(data.turns[0].id,'observed-activity');
});

test('voice work stays at its saved position across later messages and completion',async()=>{
 const {turnPlacements}=await import('../src/timeline-data.js');
 const messages=[{id:'u',role:'user',createdAt:10},{id:'ack',role:'assistant',createdAt:11},{id:'response',role:'assistant',createdAt:20}];
 const data={turns:[{id:'voice:one',anchorMessageId:'u',startedAt:12},{id:'voice:two',anchorMessageId:'ack',startedAt:13},{id:'voice:three',anchorMessageId:'ack',startedAt:14}],nodes:[]};
 const original=turnPlacements(messages,data);
 assert.deepEqual(original.before,[]);assert.deepEqual(original.after.get('ack'),['voice:two','voice:three']);
 messages.push({id:'next',role:'user',createdAt:30});data.turns.forEach(t=>{t.phase='completed';t.endedAt=35});
 assert.deepEqual(turnPlacements(messages,data),original);
});

test('older voice records use start time, and unknown records never collect at the bottom',async()=>{
 const {turnPlacements}=await import('../src/timeline-data.js');
 const messages=[{id:'u',role:'user',createdAt:10},{id:'ack',role:'assistant',createdAt:11},{id:'response',role:'assistant',createdAt:20}];
 const data={turns:[{id:'voice:old',startedAt:12},{id:'node-only'},{id:'unknown'}],nodes:[{id:'tool',turnId:'node-only',startedAt:15}]};
 const result=turnPlacements(messages,data);
 assert.deepEqual(result.after.get('ack'),['voice:old','node-only']);assert.deepEqual(result.before,['unknown']);assert.equal(result.after.has('response'),false);
});

test('usage shows only known amounts, with no placeholders for arriving metrics',()=>{
 const missing={calls:1,totalTokens:0,costUsd:0,costType:'unavailable',pricedCalls:0,unknownCalls:1,tokenUnknownCalls:1};
 assert.equal(usageLabel({...missing,tokenPendingCalls:1,costPendingCalls:1}),null);
 assert.equal(usageLabel({...missing,tokenPendingCalls:0,costPendingCalls:0},{pending:true}),null);
 assert.equal(usageLabel({calls:0,totalTokens:0,costUsd:0},{pending:true}),null);
 const partial=usageLabel({calls:2,totalTokens:120,costUsd:.01,costType:'partial',pricedCalls:1,estimatedCalls:1,unknownCalls:1,tokenUnknownCalls:1,tokenPendingCalls:1,costPendingCalls:1});
 assert.equal(partial.text,'120 tokens · ≈$0.010');assert.match(partial.title,/Includes estimated cost/);
 assert.equal(usageLabel({calls:1,tokenUnknownCalls:1,costUsd:.01,costType:'reported'}).text,'$0.010');
});

test('elapsed time uses host timestamps and stops on durable terminal events',async()=>{
 const {elapsedLabel,isRunning}=await import('../src/timeline-data.js');
 assert.equal(elapsedLabel({phase:'running',startedAt:100},112.8),'12s');
 assert.equal(elapsedLabel({phase:'queued',startedAt:100},131),'31s');
 assert.equal(elapsedLabel({phase:'completed',startedAt:100,endedAt:101.4},900),'1.4s');
 assert.equal(elapsedLabel({phase:'error',startedAt:100,endedAt:162},1000),'1m 2s');
 assert.equal(elapsedLabel({phase:'interrupted',startedAt:100},1000),null);
 assert.equal(elapsedLabel({phase:'running',startedAt:200},100),'0s');
 assert.equal(isRunning({phase:'running',endedAt:120}),false);
 const data=executionData({execution:{nodes:[],turns:[{id:'new',startedAt:100,phase:'running'}]}});assert.equal(data.turns[0].id,'new');
});

test('public result links accept only explicit web URLs without credentials',async()=>{
 const {detailLinks}=await import('../src/timeline-data.js');
 assert.deepEqual(detailLinks(JSON.stringify({url:'https://example.com/report',nested:{uri:'file:///private/key',artifact_url:'javascript:alert(1)'},items:[{html_url:'https://user:pass@example.com'},{web_url:'https://example.com/report'}]})),['https://example.com/report']);
 assert.deepEqual(detailLinks('unstructured result'),[]);
});

test('worker routing distinguishes selected inheritance from deliberate overrides',async()=>{
 const {delegationRoutingLabel}=await import('../src/delegation-routing.js');
 assert.match(delegationRoutingLabel({selectionSource:'inherited_conversation'}),/inherited the conversation selection/);
 assert.match(delegationRoutingLabel({selectionSource:'delegation_preferences'}),/delegation provider preferences/);
 assert.match(delegationRoutingLabel({resolverActive:true}),/Routing is active/);
 assert.equal(delegationRoutingLabel(null),'Worker routing has not been reported.');
});


test('neighboring execution turns and delegated observations share a message gap',async()=>{
 const {conversationWorkRows,turnPlacements}=await import('../src/timeline-data.js');
 const messages=[{id:'u',role:'user'},{id:'note',role:'user',observation:{source:'amplifier-delegate'}},{id:'a',role:'assistant'},{id:'b',role:'assistant'}];
 const data={nodes:[],turns:[{id:'t1',anchorMessageId:'u'},{id:'t2',anchorMessageId:'note'},{id:'t3',anchorMessageId:'note'},{id:'t4',anchorMessageId:'a'}]};
 const rows=conversationWorkRows(messages,turnPlacements(messages,data));
 assert.deepEqual(rows.map(row=>row.kind),['message','work','message','work','message']);
 assert.deepEqual(rows[1].items.map(item=>item.turnId||item.message.id),['t1','t2','t3']);
 assert.deepEqual(rows[3].items,[{turnId:'t4'}]);
});

test('work summary unions overlapping intervals and preserves unknown usage and failures',async()=>{
 const {combinedWork}=await import('../src/timeline-data.js');
 const turns=[{id:'a',startedAt:10,endedAt:20,phase:'completed',aggregateUsage:{calls:1,totalTokens:100,costUsd:.01,costType:'reported'}},
 {id:'b',startedAt:15,endedAt:25,phase:'failed',nodeCounts:{models:1}},
 {id:'c',startedAt:40,phase:'running',nodeCounts:{models:1}}];
 const summary=combinedWork(turns,45);
 assert.equal(summary.elapsed,'20s');assert.equal(summary.running,true);assert.equal(summary.phase,'failed');
 assert.equal(summary.usage.calls,3);assert.equal(summary.usage.pricedCalls,1);assert.equal(summary.usage.unknownCalls,2);
 assert.equal(summary.usage.costPendingCalls,1);assert.equal(summary.usage.totalTokens,100);assert.equal(summary.usage.costType,'partial');
 assert.equal(usageLabel(summary.usage).text,'100 tokens · $0.010');
 assert.equal(combinedWork([{phase:'completed'}],100).elapsed,null);
});


test('recovery observations stay in saved data without rendering chat notices',async()=>{
 const {conversationWorkRows}=await import('../src/timeline-data.js');
 const notice=id=>({id,observation:{source:'local-job-recovery'}});
 const messages=[{id:'user'},notice('a'),notice('b'),{id:'assistant'},notice('attached'),notice('last')],before=JSON.stringify(messages);
 const rows=conversationWorkRows(messages,{before:[],after:new Map([['attached',['turn']]])});
 assert.deepEqual(rows.map(r=>[r.kind,r.id]),[['message','user'],['message','assistant'],['work','work:turn']]);
 assert.equal(JSON.stringify(messages),before,'hiding notices must preserve canonical observations');
});


test('paged calls after a pause stay under individual delegate headers',()=>{
 const owner={id:'worker:review',sessionId:'review',label:'Review'};
 const nodes=[{id:'main',kind:'llm',sessionId:'root',turnId:'gap',model:'primary'},
  {id:'call',kind:'llm',sessionId:'review',turnId:'gap',parentId:owner.id,delegate:owner,provider:'fixture',model:'reviewer',startedAt:11,endedAt:12,phase:'completed',usage:{totalTokens:50}},
  {id:'tool',kind:'tool',sessionId:'review',turnId:'gap',parentId:owner.id,delegate:owner,startedAt:13,phase:'running'},
  {id:'other',kind:'llm',sessionId:'other',turnId:'gap',delegate:{id:'worker:other',sessionId:'other',label:'Second review'},startedAt:12,endedAt:13,phase:'completed'}];
 const tree=treeForTurn({nodes},'gap');
 assert.deepEqual(tree.roots.map(n=>n.kind),['llm','worker','worker']);
 const group=tree.roots[1];assert.equal(group.label,'Delegate · Review');assert.equal(group.model,'fixture · reviewer');
 assert.equal(group.phase,'running');assert.equal(group.aggregateUsage.totalTokens,50);
 assert.deepEqual(tree.children.get(group.id).map(n=>n.id),['call','tool']);
 assert.equal(nodes[1].parentId,owner.id);
});


test('delegate grouping keeps an available report and its detail reference',()=>{
 const worker={id:'worker:one',sessionId:'one',kind:'worker',turnId:'turn',label:'Review',summary:'Review result',summaryDetail:{id:'worker:one',field:'summary'}};
 const call={id:'call',sessionId:'one',kind:'llm',turnId:'turn',delegate:{id:worker.id,sessionId:'one',label:'Review'}};
 const group=treeForTurn({nodes:[worker,call]},'turn').roots[0];
 assert.equal(group.summary,'Review result');assert.deepEqual(group.summaryDetail,worker.summaryDetail);
});

test('delegate exchange suppresses its duplicate source tool without changing accounting',()=>{
 const exchange={id:'tool:root:d',delegateRequest:'Exact request',delegateResponse:'Exact response'};
 const worker={id:'worker:child:d',sessionId:'child',kind:'worker',turnId:'t',parentId:exchange.id,phase:'idle',exchange};
 const tool={id:exchange.id,sessionId:'root',kind:'tool',turnId:'t',exchange};
 const call={id:'call',sessionId:'child',kind:'llm',turnId:'t',parentId:worker.id,phase:'completed',usage:{totalTokens:7}};
 const tree=treeForTurn({nodes:[tool,worker,call]},'t');
 assert.equal(tree.roots.length,1);assert.equal(tree.roots[0].exchange,exchange);
 assert.equal(tree.roots[0].aggregateUsage.totalTokens,7);
 assert.equal(tree.roots[0].workerPhase,'idle');
 assert.deepEqual(tree.children.get(tree.roots[0].id).map(n=>n.id),['call']);
});

test('resumed sessions retain exact run owners instead of latest-session ownership',()=>{
 const first={id:'worker:c:first',sessionId:'c',kind:'worker',turnId:'t',exchange:{id:'d1',delegateRequest:'First'}};
 const second={...first,id:'worker:c:second',exchange:{id:'d2',delegateRequest:'Second'}};
 const call=(id,parent)=>({id,sessionId:'c',kind:'llm',turnId:'t',parentId:parent});
 const tree=treeForTurn({nodes:[first,second,call('a',first.id),call('b',second.id),call('ambiguous',null)]},'t');
 assert.equal(tree.roots.find(n=>n.ownerId===first.id).exchange.delegateRequest,'First');
 assert.equal(tree.roots.find(n=>n.ownerId===second.id).exchange.delegateRequest,'Second');
 assert.ok(tree.roots.some(n=>n.id==='ambiguous'));
});

test('nested delegate stays under its parent when ancestor and spawning tools are paged out',()=>{
 const outer={id:'w1',sessionId:'one',label:'Outer',phase:'running',exchange:{id:'d1',delegateRequest:'Outer request'}};
 const inner={id:'w2',sessionId:'two',label:'Inner',parentWorkerId:'w1',ancestors:[outer],phase:'running',exchange:{id:'d2',delegateRequest:'Inner request'}};
 const tree=treeForTurn({nodes:[{id:'call',kind:'llm',turnId:'t',sessionId:'two',delegate:inner,phase:'completed'}]},'t');
 assert.equal(tree.roots.length,1);assert.equal(tree.roots[0].ownerId,'w1');
 const nested=tree.children.get(tree.roots[0].id)[0];
 assert.equal(nested.ownerId,'w2');assert.equal(nested.workerPhase,'running');
 assert.deepEqual(tree.children.get(nested.id).map(n=>n.id),['call']);
});

test('new nested worker-only page still reconstructs its ancestor',()=>{
 const outer={id:'w1',sessionId:'one',label:'Outer',phase:'running',exchange:{id:'d1'}};
 const inner={id:'w2',kind:'worker',turnId:'t',sessionId:'two',label:'Inner',parentWorkerId:'w1',ancestors:[outer],phase:'starting',exchange:{id:'d2'}};
 const tree=treeForTurn({nodes:[inner]},'t');
 assert.equal(tree.roots.length,1);assert.equal(tree.roots[0].ownerId,'w1');
 assert.equal(tree.children.get(tree.roots[0].id)[0].ownerId,'w2');
});
