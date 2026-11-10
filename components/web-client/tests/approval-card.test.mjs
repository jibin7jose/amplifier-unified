import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ApprovalCard,ApprovalPanel,PermissionHistory,approvalGroups,isCapabilityCardAction,createCapabilityStopFence,stopFailureOutcome}=await server.ssrLoadModule('/src/approval-card.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('only exact bundled capability-card operations leave document ordering',()=>{
 const args={sessionId:'rendered-chat',id:'capability:request'},meta={capabilityCard:true};
 for(const action of ['capability.read','approval.respond'])assert.equal(isCapabilityCardAction(action,args,meta),true);
 assert.equal(isCapabilityCardAction('capability.revoke',{...args,dismissRequest:true},meta),true);
 for(const [action,input,hint] of [
  ['capability.read',args,{}],['capability.read',{id:args.id},meta],
  ['capability.read',{sessionId:args.sessionId},meta],['approval.respond',{...args,id:'native'},meta],
  ['capability.revoke',args,meta],['capability.request',args,meta],['canvas.select',args,meta]
 ])assert.equal(!!isCapabilityCardAction(action,input,hint),false);
});
const reviewed=(fence,sessionId='chat-a',id='capability:pending',hash='hash')=>
 fence.finishRead(fence.beginRead({sessionId,id}),hash);
const mutation=(sessionId='chat-a',id='capability:pending',hash='hash')=>({sessionId,id,expectedRequestHash:hash});
test('Stop fences later Allow immediately while history/Stop have not reached HTTP',()=>{
 const fence=createCapabilityStopFence(),before=reviewed(fence),other=reviewed(fence,'chat-b');
 fence.beginStop('chat-a','stop-queued-behind-history');
 assert.throws(()=>fence.assertMutation(mutation(),before),/Stop was requested/);
 assert.throws(()=>fence.assertMutation(mutation(),reviewed(fence)),/Stop was requested/);
 assert.doesNotThrow(()=>fence.assertMutation(mutation('chat-b'),other),'Unrelated chat stays actionable');
});
for(const outcome of ['accepted','not-applied'])test(`Stop during preflight invalidates it after ${outcome}; no automatic replay`,()=>{
 const fence=createCapabilityStopFence(),pendingRead=fence.beginRead({sessionId:'chat-a',id:'capability:pending'});
 const stop=fence.beginStop('chat-a','stop-during-preflight'),during=reviewed(fence);
 fence.settleStop(stop,outcome);
 assert.throws(()=>fence.assertMutation(mutation(),fence.finishRead(pendingRead,'hash')),/Stop was requested/);
 assert.throws(()=>fence.assertMutation(mutation(),during),/Stop was requested/);
 assert.doesNotThrow(()=>fence.assertMutation(mutation(),reviewed(fence)),'Only a fresh guarded user attempt is eligible');
 fence.settleStop(stop,'unknown');
 assert.doesNotThrow(()=>fence.assertMutation(mutation(),reviewed(fence)),'Later state recovery failure cannot undo exact acceptance/refusal');
});
test('queued mutation rechecks Stop at execution; history without Stop remains independent',async()=>{
 const fence=createCapabilityStopFence(),read=reviewed(fence);let release;
 const pending=new Promise(resolve=>{release=resolve}),posts=[];
 fence.assertMutation(mutation(),read); // Admission before Stop.
 const queued=pending.then(()=>{fence.assertMutation(mutation(),read);posts.push('Allow')});
 const rejected=assert.rejects(queued,/Stop was requested/);
 fence.beginStop('chat-a','stop-before-mutation-post');release();await rejected;
 assert.deepEqual(posts,[]);
 const independent=createCapabilityStopFence();
 for(const decision of ['Allow','Deny']){
  independent.assertMutation(mutation(),reviewed(independent));posts.push(decision);
 }
 assert.deepEqual(posts,['Allow','Deny']);
});
test('exact passive reconciliation clears only its unknown Stop, never old read tokens',async()=>{
 const calls=[];
 const fence=createCapabilityStopFence({clientId:'client-a',request:async(path,options)=>{
  calls.push({path,body:options.body});
  return {...options.body,status:'accepted'};
 }}),before=reviewed(fence),old=fence.beginStop('chat-a','unknown');
 fence.settleStop(old,'unknown');
 const newer=fence.beginStop('chat-a','refused');
 fence.settleStop(newer,'not-applied');
 assert.throws(()=>fence.assertMutation(mutation(),reviewed(fence)),/Stop was requested/);
 const during=fence.beginRead({sessionId:'chat-a',id:'capability:pending'});
 await fence.reconcile('chat-a');
 assert.deepEqual(calls,[{path:'/api/conversation/stop-status',body:{sessionId:'chat-a',commandId:'unknown',clientId:'client-a'}}]);
 for(const token of [before,fence.finishRead(during,'hash')])
  assert.throws(()=>fence.assertMutation(mutation(),token),/Stop was requested/);
 assert.doesNotThrow(()=>fence.assertMutation(mutation(),reviewed(fence)));
 assert.throws(()=>fence.assertMutation(mutation('chat-b'),reviewed(fence)),/Stop was requested/);
 assert.throws(()=>fence.assertMutation(mutation('chat-a','capability:other'),reviewed(fence)),/Stop was requested/);
 assert.throws(()=>fence.assertMutation(mutation('chat-a','capability:pending','other-hash'),reviewed(fence)),/Stop was requested/);
});
for(const [name,reply] of [
 ['missing',()=>null],['empty',()=>({})],['unknown',body=>({...body,status:'unknown'})],
 ['wrong status',body=>({...body,status:'completed'})],['boolean acceptance',body=>({...body,accepted:true})],
 ...['sessionId','commandId','clientId'].map(key=>['wrong '+key,body=>({...body,status:'accepted',[key]:'other'})]),
 ...['sessionId','commandId','clientId'].map(key=>['missing '+key,body=>{
  const receipt={...body,status:'accepted'};delete receipt[key];return receipt;
 }]),
 ['non-object',()=>('accepted')],
 ['transport failure',()=>{throw Error('Disconnected')}],
 ['malformed JSON',()=>{throw SyntaxError('Malformed JSON')}],
])test(`passive Stop lookup stays fenced on ${name}`,async()=>{
 let lookups=0;
 const fence=createCapabilityStopFence({clientId:'client-a',request:async(path,{body})=>{lookups++;return reply(body)}});
 fence.settleStop(fence.beginStop('chat-a','unknown'),'unknown');
 await fence.reconcile('chat-a');
 assert.equal(lookups,1,'No retry or polling');
 assert.throws(()=>fence.assertMutation(mutation(),reviewed(fence)),/Stop was requested/);
});
test('concurrent recovery coalesces exact commands, retains another unknown and ignores pending Stops',async()=>{
 const calls=[],releases=new Map();
 const fence=createCapabilityStopFence({clientId:'client-a',request:(path,{body,method,signal})=>{
  assert.equal(path,'/api/conversation/stop-status');assert.equal(method,'POST');assert.ok(signal);
  calls.push(body);
  return new Promise(resolve=>releases.set(body.commandId,status=>resolve({...body,status})));
 }});
 for(const commandId of ['old','other'])fence.settleStop(fence.beginStop('chat-a',commandId),'unknown');
 const pending=fence.beginStop('chat-b','pending');
 const first=fence.reconcile('chat-a'),second=fence.reconcile();
 await Promise.resolve();
 assert.deepEqual(calls.map(row=>row.commandId),['old','other']);
 releases.get('old')('accepted');releases.get('other')('unknown');
 await Promise.all([first,second]);
 assert.throws(()=>fence.assertMutation(mutation(),reviewed(fence)),/Stop was requested/);
 const third=fence.reconcile('chat-a');await Promise.resolve();
 assert.deepEqual(calls.map(row=>row.commandId),['old','other','other'],'Only the unresolved command is read again');
 releases.get('other')('accepted');await third;
 assert.doesNotThrow(()=>fence.assertMutation(mutation(),reviewed(fence)));
 assert.throws(()=>fence.assertMutation(mutation('chat-b'),reviewed(fence,'chat-b')),/Stop was requested/);
 fence.settleStop(pending,'not-applied');
});
test('bounded lookup aborts, ignores a late accepted reply, and permits a later explicit read',async()=>{
 let resolveLate,signal,calls=0;
 const fence=createCapabilityStopFence({clientId:'client-a',timeoutMs:10,request:(path,options)=>{
  calls++;signal=options.signal;
  if(calls===1)return new Promise(resolve=>{resolveLate=()=>resolve({...options.body,status:'accepted'})});
  return Promise.resolve({...options.body,status:'accepted'});
 }});
 fence.settleStop(fence.beginStop('chat-a','unknown'),'unknown');
 await fence.reconcile('chat-a');assert.equal(signal.aborted,true);assert.equal(calls,1);
 resolveLate();await Promise.resolve();
 assert.throws(()=>fence.assertMutation(mutation(),reviewed(fence)),/Stop was requested/);
 await fence.reconcile('chat-a');
 assert.equal(calls,2);
 assert.doesNotThrow(()=>fence.assertMutation(mutation(),reviewed(fence)));
});
test('only known pre-POST failure or action ownership refusal is definitely unapplied',()=>{
 assert.equal(stopFailureOutcome(Error('local before-hook rejected'),false),'not-applied');
 assert.equal(stopFailureOutcome({code:'session_busy',status:409},true),'not-applied');
 for(const error of [{status:400},{status:409},{status:500},{unconfirmed:true},{name:'AbortError'},Error('Malformed response')])
  assert.equal(stopFailureOutcome(error,true),'unknown');
});
for(const decision of ['approve','deny'])test(`note accompanies ${decision}, targets the rendered chat and survives failure`,async()=>{
 let root;const calls=[];
 await act(async()=>{root=create(React.createElement(ApprovalCard,{approval:{id:'approval'},sessionId:'original-chat',act:async(...args)=>{calls.push(args);throw Error('Not confirmed')}}))});
 await act(async()=>root.root.findByType('textarea').props.onChange({target:{value:'Use the workspace copy.'}}));
 await act(async()=>root.root.findAllByType('button')[decision==='approve'?0:1].props.onClick());
 assert.deepEqual(calls,[['approval.respond',{sessionId:'original-chat',id:'approval',decision,comment:'Use the workspace copy.'}]]);
 assert.equal(root.root.findByType('textarea').props.value,'Use the workspace copy.');
 assert.match(JSON.stringify(root.toJSON()),/Not confirmed/);
 await act(async()=>root.unmount());
});

test('historical decisions leave chat; pending and unresolved requests stay actionable',()=>{
 const approvals=[
  {id:'capability:allowed',status:'allow',prompt:'Approved scope',comment:'Decision note',actor:'human',source:'fixture'},
  {id:'native-pending',status:'pending',prompt:'Current native request'},
  {id:'capability:expired',status:'expired',prompt:'Unresolved old request'},
  {id:'capability:denied',status:'deny',prompt:'Denied scope'},
  {id:'capability:dismissed',status:'expired',dismissedAt:42,prompt:'Dismissed scope',dismissalComment:'History note'},
  {id:'legacy-pending',prompt:'Legacy current request'}
 ];
 const session={id:'original-chat',approvals},before=JSON.stringify(session),groups=approvalGroups(session);
 assert.deepEqual(groups.pending.map(row=>row.id),['native-pending','legacy-pending']);
 assert.deepEqual(groups.retired.map(row=>row.id),['capability:expired']);
 assert.deepEqual(groups.history.map(row=>row.id),['capability:allowed','capability:denied','capability:dismissed']);
 const chat=renderToStaticMarkup(React.createElement(ApprovalPanel,{session}));
 assert.match(chat,/Permissions history \(3\)/);
 assert.match(chat,/Current native request/);
 assert.match(chat,/Unresolved old request/);
 assert.doesNotMatch(chat,/data-permission-history-id|Approved scope|Decision note|Denied scope|Dismissed scope/);
 const history=renderToStaticMarkup(React.createElement(PermissionHistory,{session}));
 assert.match(history,/Approved scope.*Decision note.*Denied scope.*History note/);
 assert.doesNotMatch(history,/data-approval-id|approval.respond|<button|Unresolved old request|Current native request/);
 assert.equal(JSON.stringify(session),before,'Presentation preserves exact records and their order');
 const historicalOnly=renderToStaticMarkup(React.createElement(ApprovalPanel,{session:{id:session.id,approvals:groups.history}}));
 assert.doesNotMatch(historicalOnly,/data-part="approvals"|<details/);
});

test('history entry uses the rendered chat and retains an unconfirmed opening error',async()=>{
 const calls=[];let root;
 await act(async()=>{root=create(React.createElement(ApprovalPanel,{session:{id:'rendered-chat',approvals:[{id:'capability:done',status:'deny'}]},canvasDraft:{path:'keep-draft.md'},dispatch:async(...args)=>{calls.push(args);return {accepted:false,error:'View changed'}}}))});
 await act(async()=>root.root.findByType('button').props.onClick());
 assert.deepEqual(calls,[['view.update',{sessionId:'rendered-chat',patch:{settingsCanvas:false,canvasControlsExpanded:false,canvasDraft:{path:'keep-draft.md',library:true,open:false,browser:false,delivery:'permissions'}}}]]);
 assert.match(JSON.stringify(root.toJSON()),/View changed/);
 assert.equal(root.root.findByType('button').props.disabled,false);
 await act(async()=>root.unmount());
});
