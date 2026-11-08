import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MessageDelivery}=await server.ssrLoadModule('/src/message-delivery.jsx');
const {useMessageOutbox}=await server.ssrLoadModule('/src/message-outbox.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const text=node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button=(root,label)=>root.root.findAllByType('button').find(row=>text(row)===label);
async function fixture(saved=true){
 const calls=[],retryCalls=[],message={id:'m',inputId:'original',role:'user',text:'Request'},session={id:'chat',status:'stopped',messages:saved?[message]:[]};
 const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:action==='conversation.delivery'?{delivery:'unknown',message:'Checking does not resend it.'}:{delivery:'accepted',resent:true}}};
 const retry=async m=>retryCalls.push(m);let root;
 await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status:'unknown'},localDelivery:saved?null:{commandId:'original'},dispatch,retry}))});
 return {root,calls,retryCalls,session};
}
for(const [disposition,label] of Object.entries({queued:'Received by the active run; waiting for its next step',applied:'Added to the active run’s context',held:'Message not delivered',next_turn:'Received for the next turn',unknown:'Steering delivery could not be confirmed'})){
 test(`steering ${disposition} stays distinct from ordinary delivery and never offers resend`,async()=>{
  const message={id:'m',inputId:'correction',role:'user',text:'Pause',steering:{generationId:'run',disposition}};
  let root;
  await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session:{id:'chat',status:'idle',messages:[message]},delivery:disposition==='applied'?null:{status:'unknown'},dispatch:async()=>({result:{steering:message.steering}})}))});
  assert.ok(JSON.stringify(root.toJSON()).includes(label));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Send again|Retry|Send this message again/);
  await act(async()=>root.unmount());
 });
}
test('another browser can check saved delivery and confirm one explicit resend',async()=>{
 const {root,calls,retryCalls}=await fixture();
 await act(async()=>button(root,'Check delivery').props.onClick());
 assert.deepEqual(calls,[{action:'conversation.delivery',args:{sessionId:'chat',inputId:'original'}}]);
 assert.match(JSON.stringify(root.toJSON()),/Checking does not resend it/);
 await act(async()=>button(root,'Send again').props.onClick());
 assert.equal(calls.length,1);assert.match(JSON.stringify(root.toJSON()),/could repeat/);
 await act(async()=>button(root,'Cancel').props.onClick());assert.equal(calls.length,1);
 await act(async()=>button(root,'Send again').props.onClick());
 await act(async()=>button(root,'Send this message again').props.onClick());
 assert.deepEqual(calls[1],{action:'conversation.retry',args:{sessionId:'chat',inputId:'original',confirmUncertain:true}});
    assert.doesNotMatch(JSON.stringify(root.toJSON()),/Delivery not confirmed|Not sent/);
 assert.equal(retryCalls.length,0);await act(async()=>root.unmount());
});
for(const [phase,label] of Object.entries({sent:'Sending live direction',accepted:'Accepted; applying live direction',pending:'Accepted; queued at the provider',applied:'Live direction applied to the successor response'})){
 test(`native ${phase} is not presented as app-held boundary input`,async()=>{
  const message={id:'m',inputId:'direction',steering:{generationId:'run',mode:'native',phase,disposition:phase==='applied'?'applied':'queued'}};
  let root;await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session:{id:'chat',status:'working',messages:[message]}}))});
  assert.match(JSON.stringify(root.toJSON()),new RegExp(label));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/waiting for its next step|Added to the active run|Send again|Retry/);
  await act(async()=>root.unmount());
 });
}
test('a local-only message explicitly retries its original identity without claiming to repeat work',async()=>{
 const {root,calls,retryCalls}=await fixture(false);
 await act(async()=>button(root,'Check delivery').props.onClick());
 assert.equal(retryCalls.length,0);
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/could repeat that work/);
 await act(async()=>button(root,'Retry original submission').props.onClick());
 assert.equal(retryCalls[0].inputId,'original');assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});
test('known startup failure retries the saved identity without an uncertainty confirmation',async()=>{
 const calls=[],message={id:'m',inputId:'original',role:'user',text:'Request'},session={id:'chat',status:'error',messages:[message]};let root;
 await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status:'failed'},localDelivery:{commandId:'original',status:'failed'},dispatch:async(action,args)=>{calls.push({action,args});return {result:{delivery:'accepted'}}},retry:()=>{throw Error('Must reuse saved input')}}))});
 await act(async()=>button(root,'Retry').props.onClick());
 assert.deepEqual(calls,[{action:'conversation.retry',args:{sessionId:'chat',inputId:'original',confirmUncertain:true}}]);
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/could repeat that work/);
 await act(async()=>root.unmount());
});
test('a pending input shows preparation rather than sending while the chat warms',async()=>{
 const message={id:'m',inputId:'original',role:'user',text:'Request'};let root;
 for(const session of [{status:'starting'},{status:'working',activity:{phase:'runtime-setup'}}]){
  await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status:'sending'}}))});
  assert.match(JSON.stringify(root.toJSON()),/Preparing chat/);
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Sending|Send again|Retry/);
  await act(async()=>root.unmount());
 }
});

function storage(){
 const values=new Map();
 return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values};
}
test('dismissal preserves uncertainty, survives remount, restores and stays scoped to chat/input',async()=>{
 globalThis.sessionStorage=storage();
 const {root,calls,retryCalls,session}=await fixture();
 await act(async()=>button(root,'Dismiss warning').props.onClick());
 assert.equal(root.root.findByType('details').props.open,false);
 assert.equal(root.root.findByType('summary').children.join(''),'Delivery details');
 assert.equal(calls.length,0);assert.equal(retryCalls.length,0);
 assert.match(JSON.stringify(root.toJSON()),/Delivery not confirmed/);
 assert.equal(session.messages[0].inputId,'original');
 await act(async()=>root.unmount());
 const again=await fixture();
 assert.equal(again.root.root.findByType('details').props.open,false);
 await act(async()=>button(again.root,'Show warning').props.onClick());
 assert.ok(button(again.root,'Dismiss warning'));assert.equal(sessionStorage.values.size,0);
 await act(async()=>button(again.root,'Dismiss warning').props.onClick());
 const props={message:{id:'m',inputId:'original'},session:{id:'other-chat',messages:[]},delivery:{status:'unknown'}};
 await act(async()=>again.root.update(React.createElement(MessageDelivery,props)));
 assert.ok(button(again.root,'Dismiss warning'),'same input in another chat is not dismissed');
 await act(async()=>again.root.update(React.createElement(MessageDelivery,{...props,session:{id:'chat'},message:{inputId:'other-input'}})));
 assert.ok(button(again.root,'Dismiss warning'),'another input is not dismissed');
 await act(async()=>again.root.unmount());delete globalThis.sessionStorage;
});
test('exact accepted check removes stale warning without a message in the visible window',async()=>{
 const calls=[],message={id:'m',inputId:'original'},session={id:'chat',messages:[],status:'idle'};let root;
 await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status:'unknown'},dispatch:async(action,args)=>{calls.push({action,args});return {accepted:true,result:{delivery:'accepted',message:'Received, not resent.'}}}}))});
 await act(async()=>button(root,'Check delivery').props.onClick());
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/Delivery not confirmed|Not sent|Send again/);
 assert.deepEqual(calls,[{action:'conversation.delivery',args:{sessionId:'chat',inputId:'original'}}]);
 await act(async()=>root.unmount());
});
for(const status of ['sending','queued','queued_for_update','accepted']){
 test(`cannot dismiss ${status}`,async()=>{
  const message={inputId:'same'},session={id:'chat',status:'idle'};let root;
  await act(async()=>{root=create(React.createElement(MessageDelivery,{message,session,delivery:{status}}))});
  assert.equal(button(root,'Dismiss warning'),undefined);
  await act(async()=>root.unmount());
 });
}
test('unknown warning cannot hide the exact active input or a pending check; old warnings remain dismissible during unrelated work',async()=>{
 let finish,root;
 const props={message:{inputId:'original'},session:{id:'chat',status:'working',execution:{currentTurnId:'original'}},delivery:{status:'unknown'},dispatch:()=>new Promise(r=>{finish=r})};
 await act(async()=>{root=create(React.createElement(MessageDelivery,props))});
 assert.equal(button(root,'Dismiss warning'),undefined);
 await act(async()=>root.update(React.createElement(MessageDelivery,{...props,session:{...props.session,execution:{currentTurnId:'new'}}})));
 assert.ok(button(root,'Dismiss warning'));
 let request;await act(async()=>{request=button(root,'Check delivery').props.onClick()});
 assert.equal(button(root,'Dismiss warning').props.disabled,true);
 await act(async()=>{finish({result:{delivery:'unknown'}});await request});
 assert.equal(button(root,'Dismiss warning').props.disabled,false);
 await act(async()=>root.unmount());
});
test('exact positive check retires only matching outbox and a late error cannot recreate it',async()=>{
 globalThis.sessionStorage=storage();let api,root;
 function Harness(){api=useMessageOutbox();return null}
 await act(async()=>{root=create(React.createElement(Harness))});
 await act(async()=>{
  api.update('one',{sessionId:'chat',commandId:'input',text:'Keep',attachmentIds:['a'],status:'unknown'});
  api.update('two',{sessionId:'other',commandId:'input',text:'Other',attachmentIds:[],status:'unknown'});
 });
 await act(async()=>api.acknowledge('chat','input',{accepted:true,delivery:'unknown'}));
 assert.equal(api.entries.length,2);
 await act(async()=>api.acknowledge('chat','input',{delivery:'accepted'}));
 assert.deepEqual(api.entries.map(row=>row.id),['two']);
 await act(async()=>api.update('one',{status:'unknown',error:'Late timeout'}));
 assert.deepEqual(api.entries.map(row=>row.id),['two']);
 await act(async()=>root.unmount());delete globalThis.sessionStorage;
});
test('a check reporting active sending cannot be dismissed while the original frame is uncertain',async()=>{
 let root;
 await act(async()=>{root=create(React.createElement(MessageDelivery,{message:{inputId:'input'},session:{id:'chat'},delivery:{status:'unknown'},dispatch:async()=>({result:{delivery:'sending',message:'Still in progress'}})}))});
 await act(async()=>button(root,'Check delivery').props.onClick());
 assert.equal(button(root,'Dismiss warning'),undefined);
 await act(async()=>root.unmount());
});
test('pending delivery check survives remount without collapse, duplicate request or cross-chat borrowing',async()=>{
 globalThis.sessionStorage=storage();let finish,root,calls=0;
 const props={message:{inputId:'pending'},session:{id:'chat'},delivery:{status:'unknown'},
  dispatch:()=>{calls++;return new Promise(resolve=>{finish=resolve})}};
 await act(async()=>{root=create(React.createElement(MessageDelivery,props))});
 await act(async()=>button(root,'Dismiss warning').props.onClick());
 let running;await act(async()=>{running=button(root,'Check delivery').props.onClick()});
 await act(async()=>root.unmount());
 await act(async()=>{root=create(React.createElement(MessageDelivery,{...props,session:{id:'other'}}))});
 assert.ok(button(root,'Check delivery'));assert.ok(button(root,'Dismiss warning'));
 await act(async()=>root.unmount());
 await act(async()=>{root=create(React.createElement(MessageDelivery,props))});
 assert.equal(root.root.findByType('details').props.open,true);
 assert.equal(button(root,'Checking…').props.disabled,true);
 assert.equal(button(root,'Show warning').props.disabled,true);
 await act(async()=>button(root,'Checking…').props.onClick());
 assert.equal(calls,1);
 await act(async()=>{finish({result:{delivery:'unknown',message:'Settled uncertain'}});await running});
 assert.match(JSON.stringify(root.toJSON()),/Settled uncertain/);
 await act(async()=>root.unmount());delete globalThis.sessionStorage;
});
