import test from 'node:test';
import assert from 'node:assert/strict';
import {workStream} from '../src/work-stream.js';

function scene(){
 const worker={id:'w',kind:'worker',turnId:'t',sessionId:'child',parentId:'d',phase:'completed',label:'Review',
  exchange:{id:'d',startedAt:1,endedAt:30,delegateRequest:'request',delivery:'separate'}};
 const child=(id,at)=>({id,kind:'tool',label:'read_file',turnId:'t',sessionId:'child',parentId:'w',startedAt:at,endedAt:at+1});
 return {turns:[{id:'t'}],nodes:[worker,child('read',4),
  {id:'follow',kind:'tool',label:'live_worker',turnId:'t',sessionId:'main',rootSessionId:'main',exchangeOwnerId:'w',delegate:worker,startedAt:22,
   exchange:{id:'follow',delegateRequest:'again',phase:'completed',startedAt:22}},
  {id:'reply2',kind:'exchange',turnId:'t',delegate:worker,startedAt:30,exchange:{id:'reply2',delegateResponse:'two',report:true}},
  {id:'main-model',kind:'llm',turnId:'t',sessionId:'main',rootSessionId:'main',startedAt:20,endedAt:21},
  child('search',5),
  {id:'reply1',kind:'exchange',turnId:'t',delegate:worker,startedAt:18,exchange:{id:'reply1',delegateResponse:'one',report:true}}]};
}
test('requests, replies and both owners operations interleave without mutating accounting',()=>{
 const data=scene(),before=JSON.stringify(data);
 const entries=workStream(data,'t').entries.filter(entry=>entry.kind!=='worker-status');
 assert.deepEqual(entries.map(entry=>entry.id),['d:delegateRequest','read','search','reply1:delegateResponse','main-model','follow:delegateRequest','reply2:delegateResponse']);
 assert.match(entries.find(entry=>entry.id==='read').actor,/^Delegate/);
 assert.equal(entries.find(entry=>entry.id==='main-model').actor,'Main chat');
 assert.equal(JSON.stringify(data),before);
});
test('inline return uses tool end, not the request start or first completed child',()=>{
 const data=scene();data.nodes=data.nodes.filter(node=>node.kind!=='exchange'&&node.id!=='follow');
 data.nodes[0].exchange={id:'d',startedAt:1,endedAt:30,delegateRequest:'ask',delegateResponse:'answer'};
 const entries=workStream(data,'t').entries.filter(entry=>entry.kind!=='worker-status');
 assert.equal(entries.at(-1).id,'d:delegateResponse');
 assert.equal(entries.at(-1).at,30);
});
test('record-local saved response keeps unknown association and no fabricated request',()=>{
 const node={id:'saved',kind:'tool',turnId:'t',sessionId:'child',association:'unverified',delegateResponse:'recorded'};
 const entries=workStream({nodes:[node]},'t').entries;
 assert.deepEqual(entries.map(entry=>entry.id),['saved:delegateResponse']);
 assert.match(entries[0].direction,/association unverified/);
 assert.equal(entries[0].at,undefined);
});
test('a terminal follow-up receipt is not a reply',()=>{
 const entries=workStream(scene(),'t').entries;
 assert.equal(entries.filter(entry=>entry.id==='follow:delegateResponse').length,0);
});
test('unknown timestamps retain their source boundary rather than sorting as zero',()=>{
 const nodes=[{id:'a',kind:'tool',turnId:'t',startedAt:3},{id:'unknown',kind:'tool',turnId:'t'},
  {id:'b',kind:'tool',turnId:'t',startedAt:2}];
 assert.deepEqual(workStream({nodes},'t').entries.map(entry=>entry.id),['a','unknown','b']);
});
test('running reply placeholders cannot reorder child operations after later main work',()=>{
 const data=scene();
 data.nodes=data.nodes.filter(node=>!['reply1','reply2','follow'].includes(node.id));
 data.nodes[0].phase='running';data.nodes[0].exchange={id:'d',startedAt:1,phase:'running',delegateRequest:'ask'};
 data.nodes.find(node=>node.id==='read').phase='running';
 delete data.nodes.find(node=>node.id==='read').endedAt;
 assert.deepEqual(workStream(data,'t').entries.filter(entry=>entry.kind!=='worker-status').map(entry=>entry.id),
  ['d:delegateRequest','read','search','main-model']);
});
test('failed request without a spawned worker retains explicit error receipt access',()=>{
 const node={id:'failed',kind:'tool',label:'delegate',turnId:'t',phase:'error',
  exchange:{id:'failed',phase:'error',startedAt:1,endedAt:2,delegateRequest:'ask',errorDetail:{id:'failed',field:'error'}}};
 const entry=workStream({nodes:[node]},'t').entries[0];
 assert.equal(entry.receipt,true);assert.equal(entry.followup,false);
 assert.deepEqual(entry.node.exchange.errorDetail,{id:'failed',field:'error'});
});
test('legacy worker without an exchange gets status only, not invented parent messages',()=>{
 const node={id:'legacy',kind:'worker',turnId:'t',summary:'retained',label:'Worker'};
 assert.deepEqual(workStream({nodes:[node]},'t').entries.map(entry=>entry.kind),['worker-status']);
});
test('repeated owner context does not replay messages and remains inspectable',()=>{
 const exchange={id:'d',startedAt:1,endedAt:30,delegateRequest:'ask',delegateResponse:'answer'};
 const owner={id:'w',sessionId:'child',label:'Review',phase:'completed',exchange};
 const first={...owner,kind:'worker',turnId:'t1'};
 const later={id:'later',kind:'tool',label:'read_file',turnId:'t2',sessionId:'child',startedAt:40,endedAt:41,delegate:owner};
 const ids=(nodes,turn)=>workStream({nodes},turn).entries.filter(entry=>entry.kind!=='worker-status').map(entry=>entry.id);
 assert.deepEqual(ids([first,later],'t1'),['d:delegateRequest','d:delegateResponse']);
 assert.deepEqual(ids([first,later],'t2'),['later']);
 assert.deepEqual(ids([later],'t2'),['later']);
 const context=workStream({nodes:[later]},'t2').entries.find(entry=>entry.kind==='worker-status');
 assert.equal(context.context,true);assert.equal(context.node.exchange.delegateRequest,'ask');
});
test('follow-up requires recipient proof and never labels its caller as the target',()=>{
 const ancestor={id:'outer',kind:'worker',sessionId:'outer-session',turnId:'t',label:'Outer'};
 const sender={id:'inner',kind:'worker',sessionId:'sender',parentWorkerId:'outer',ancestors:[ancestor],turnId:'t',label:'Sender'};
 const follow={id:'follow',kind:'tool',tool:'live_worker',sessionId:'sender',turnId:'t',parentId:'inner',
  delegate:sender,inputSummary:{action:'message'},startedAt:10};
 const entry=workStream({nodes:[ancestor,sender,follow]},'t').entries.find(entry=>entry.id==='follow:delegateRequest');
 assert.match(entry.direction,/Delegate · Sender → Delegate \(recipient unavailable\)/);
});
test('ambiguous worker runs and missing root evidence never claim Main chat',()=>{
 const one={id:'one',kind:'worker',sessionId:'same',turnId:'prior',label:'One'};
 const two={...one,id:'two',label:'Two'};
 const follow={id:'follow',kind:'tool',tool:'live_worker',sessionId:'same',turnId:'t',inputSummary:{action:'steer'},startedAt:1};
 const entry=workStream({nodes:[one,two,follow]},'t').entries.find(entry=>entry.kind==='exchange');
 assert.match(entry.direction,/Worker \(owner unavailable\)/);assert.doesNotMatch(entry.direction,/Main chat/);
});
for(const action of ['message','steer'])test(`${action} hint supports request lookup but stray response is never an answer`,()=>{
 const node={id:'follow',kind:'tool',tool:'live_worker',turnId:'t',sessionId:'main',rootSessionId:'main',
  inputSummary:{action},delegateResponse:'not a child report'};
 const entries=workStream({nodes:[node]},'t').entries;
 assert.deepEqual(entries.map(entry=>entry.field),['delegateRequest']);
 assert.match(entries[0].direction,/Main chat → Delegate \(recipient unavailable\)/);
});
for(const action of ['cancel','finish','list'])test(`${action} hint is an ordinary operation`,()=>{
 const node={id:'control',kind:'tool',tool:'live_worker',turnId:'t',inputSummary:{action}};
 assert.deepEqual(workStream({nodes:[node]},'t').entries.map(entry=>entry.kind),['operation']);
});

test('a nested request with unloaded ancestry does not impersonate Main chat',()=>{
 const request={id:'nested-request',kind:'tool',label:'delegate',turnId:'t',sessionId:'unloaded-worker',rootSessionId:'main',
  startedAt:1,endedAt:3,exchange:{id:'nested-request',sessionId:'unloaded-worker',startedAt:1,endedAt:3,delegateRequest:'inspect',delegateResponse:'checked'}};
 const worker={id:'inner',kind:'worker',turnId:'t',sessionId:'child',parentId:request.id,label:'Inner',exchange:request.exchange};
 const data={sessionId:'main',nodes:[request,worker]};
 const before=JSON.stringify(data),entries=workStream(data,'t').entries.filter(row=>row.kind==='exchange');
 assert.deepEqual(entries.map(row=>row.direction),['Worker (owner unavailable) → Delegate','Delegate → Worker (owner unavailable)']);
 assert.equal(JSON.stringify(data),before);
 // A paged worker header still carries its request's source session, not a
 // fabricated root sender inferred from the absent parent row.
 assert.deepEqual(workStream({sessionId:'main',nodes:[worker]},'t').entries.filter(row=>row.kind==='exchange').map(row=>row.direction),
  entries.map(row=>row.direction));
});

test('exact root request and exact nested caller keep their own directions',()=>{
 const root={id:'root-request',kind:'tool',label:'delegate',turnId:'t',sessionId:'main',rootSessionId:'main',
  exchange:{id:'root-request',sessionId:'main',delegateRequest:'root ask',delegateResponse:'root reply'}};
 assert.deepEqual(workStream({sessionId:'main',nodes:[root]},'t').entries.map(row=>row.direction),
  ['Main chat → Delegate','Delegate → Main chat']);
 const ancestor={id:'ancestor',kind:'worker',sessionId:'outer',label:'Outer',turnId:'t'};
 const caller={id:'caller',kind:'worker',sessionId:'parent',label:'Reviewer',turnId:'t',parentWorkerId:'ancestor',ancestors:[ancestor]};
 const failed={...root,id:'failed',sessionId:'parent',parentId:'caller',delegate:caller,phase:'error',
  exchange:{id:'failed',sessionId:'parent',phase:'error',delegateRequest:'nested ask'}};
 const entries=workStream({sessionId:'main',nodes:[ancestor,caller,failed]},'t').entries.filter(row=>row.kind==='exchange');
 assert.deepEqual(entries.map(row=>row.direction),['Delegate · Reviewer → Delegate','Delegate → Delegate · Reviewer']);
});

test('missing sender evidence stays unavailable and never changes message contents',()=>{
 const exchange={id:'no-source',delegateRequest:'exact request',delegateResponse:'exact reply'};
 const node={id:'worker',kind:'worker',turnId:'t',exchange};
 const entries=workStream({nodes:[node]},'t').entries.filter(row=>row.kind==='exchange');
 assert.deepEqual(entries.map(row=>row.direction),['Sender unavailable → Delegate','Delegate → Sender unavailable']);
 assert.equal(entries[0].node.exchange.delegateRequest,exchange.delegateRequest);
 assert.equal(entries[1].node.exchange.delegateResponse,exchange.delegateResponse);
});
