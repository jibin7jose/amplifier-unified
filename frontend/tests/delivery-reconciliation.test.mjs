// Passive recovery only. Existing message-delivery tests own explicit resend.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MessageDelivery}=await server.ssrLoadModule('/src/message-delivery.jsx');
const {readOutbox,outboxMessages,useMessageOutbox}=await server.ssrLoadModule('/src/message-outbox.jsx');
const {deliveryScope}=await server.ssrLoadModule('/src/pending-delivery-checks.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

const roots=new Set(),deferredReads=new Set();
let originalGlobals,online,scopeSequence=0;
test.beforeEach(()=>{
 originalGlobals=new Map(['window','navigator','sessionStorage'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 online=true;
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};
 const navigator={get onLine(){return online}},window=new EventTarget();
 Object.assign(window,{navigator,sessionStorage:storage});
 for(const [key,value] of Object.entries({window,navigator,sessionStorage:storage}))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
});
test.afterEach(async()=>{
 await act(async()=>{
  for(const read of deferredReads)read.resolve(receipt('unknown'));
  deferredReads.clear();
  for(const root of roots)root.unmount();
  roots.clear();
 });
 for(const [key,descriptor] of originalGlobals){
  if(descriptor)Object.defineProperty(globalThis,key,descriptor);
  else delete globalThis[key];
 }
});

const receipt=(delivery='accepted',extra={})=>({accepted:true,result:{sessionId:'chat',inputId:'original',delivery,message:delivery==='accepted'?'Received. Nothing was resent.':'Delivery remains uncertain. Nothing was resent.',...extra}});
const serialized=root=>JSON.stringify(root.toJSON());
const checkButton=root=>root.root.findAllByType('button').find(row=>row.props['data-action']==='conversation.delivery');
const reconnect=()=>act(async()=>{window.dispatchEvent(new Event('amplifier-reconnected'))});
function deferred(){
 let resolve,reject;
 const promise=new Promise((yes,no)=>{resolve=yes;reject=no});
 const read={promise,resolve,reject};deferredReads.add(read);return read;
}
async function mount(overrides={},read=()=>receipt()){
 const calls=[],retries=[],discards=[];
 const message={id:'saved',inputId:'original',role:'user',text:'Keep the original request.',attachments:[{id:'attachment',name:'reference.txt'}]};
 let props={
  message,session:{id:'chat',status:'idle',messages:[message]},delivery:{status:'unknown',error:'The original acknowledgement was lost.'},
  localDelivery:{commandId:'original',sessionId:'chat',text:message.text,attachmentIds:['attachment'],status:'unknown'},
  recovery:{scope:`fixture-host/account-${++scopeSequence}`,connection:'instance-1'},
  dispatch:async(action,args,options)=>{calls.push({action,args,options});return read(action,args)},
  retry:async value=>{retries.push(value)},discard:value=>{discards.push(value)},...overrides,
 };
 let root;
 const render=async()=>act(async()=>{root=create(React.createElement(MessageDelivery,props));roots.add(root)});
 await render();
 return {
  get root(){return root},get props(){return props},calls,retries,discards,
  async update(patch={}){props={...props,...patch};await act(async()=>root.update(React.createElement(MessageDelivery,props)))},
  async remount(){await act(async()=>root.unmount());roots.delete(root);await render()},
  passiveOnly(){
   assert.ok(calls.every(call=>call.action==='conversation.delivery'),'No automatic send, retry, edit, or discard action');
   for(const call of calls){
    assert.deepEqual(Object.keys(call.args).sort(),['inputId','sessionId'],'Recovery scope is not sent as authority or payload');
    assert.equal(call.options?.feedback,false);
    // Internal source-fencing metadata may accompany dispatch; it must not
    // become an action argument or otherwise grant authority.
   }
   assert.deepEqual(retries,[]);assert.deepEqual(discards,[]);
  },
 };
}

test('lost ACK: mount checks the exact identity without a click and hides only a positive warning',async()=>{
 const f=await mount();
 assert.deepEqual(f.calls.map(({action,args})=>({action,args})),[{action:'conversation.delivery',args:{sessionId:'chat',inputId:'original'}}]);
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed|Not sent|Check delivery|Send again/);
 assert.equal(f.props.message.text,'Keep the original request.');
 assert.equal(f.props.message.attachments[0].name,'reference.txt');
 f.passiveOnly();
});

test('saved exact input reconciles in another browser without a local payload or outbox',async()=>{
 const message={id:'saved',role:'user',inputId:'original',text:'Saved request',attachments:[{id:'a',name:'reference.txt'}]};
 const f=await mount({message,session:{id:'chat',status:'idle',messages:[message]},localDelivery:null});
 assert.equal(f.calls.length,1);
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed|Send again|Retry/);
 assert.deepEqual(f.props.session.messages,[message]);
 f.passiveOnly();
});

test('exact receipt still clears warning after input leaves the bounded message window',async()=>{
 const f=await mount({session:{id:'chat',status:'idle',messages:[]},localDelivery:null});
 assert.equal(f.calls.length,1);
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed|Not sent|Retry|Send again/);
 f.passiveOnly();
});

const uncertainResults=[
 ['explicit unknown',receipt('unknown')],
 ['no saved record',receipt('not_saved',{message:'No saved copy was found. Nothing was resent.'})],
 ['missing response',undefined],
 ['null response',null],
 ['generic acceptance only',{accepted:true}],
 ['missing result identity',{accepted:true,result:{delivery:'accepted'}}],
 ['wrong chat',receipt('accepted',{sessionId:'other-chat'})],
 ['wrong input',receipt('accepted',{inputId:'other-input'})],
 ['contradictory outer rejection',{...receipt(),accepted:false}],
 ['contradictory result rejection',receipt('accepted',{accepted:false})],
 ...[0,1,'true','false',null].map(accepted=>[
  `nonboolean result accepted=${JSON.stringify(accepted)}`,receipt('accepted',{accepted}),
 ]),
 ['contradictory result status',receipt('accepted',{status:'failed'})],
 ['contradictory result error',receipt('accepted',{error:'The receipt could not be read.'})],
 ['unrecognized delivery',receipt('complete')],
 ['malformed result',{accepted:true,result:'accepted'}],
];
for(const [label,value] of uncertainResults){
 test(`${label}: retain uncertainty, never infer success or resend`,async()=>{
  const f=await mount({},()=>value);
  assert.equal(f.calls.length,1);
  assert.match(serialized(f.root),/Delivery not confirmed/);
  for(let revision=1;revision<=5;revision++)await f.update({
   session:{...f.props.session,revision},
   recovery:{...f.props.recovery},
   dispatch:async(action,args,options)=>{f.calls.push({action,args,options});return value},
  });
  assert.equal(f.calls.length,1,'New render objects and callbacks do not poll');
  assert.match(serialized(f.root),/Delivery not confirmed/);
  f.passiveOnly();
 });
}

test('a failed passive read leaves the warning honest and does not spin or send',async()=>{
 const f=await mount({},async()=>{throw Error('Synthetic receipt read unavailable')});
 assert.equal(f.calls.length,1);
 assert.match(serialized(f.root),/Delivery not confirmed/);
 assert.match(serialized(f.root),/Synthetic receipt read unavailable/);
 await f.update({delivery:{status:'unknown'}});
 assert.equal(f.calls.length,1);
 f.passiveOnly();
});

test('offline mount and offline reconnect do not read; restored connection checks exactly once',async()=>{
 online=false;
 const pending=deferred(),f=await mount({},()=>pending.promise);
 assert.equal(f.calls.length,0);
 await reconnect();assert.equal(f.calls.length,0);
 online=true;
 await reconnect();
 assert.equal(f.calls.length,1);
 assert.equal(checkButton(f.root).props.disabled,true);
 await f.update({session:{...f.props.session,revision:2}});
 await reconnect(); // Another notification cannot fork an in-flight read.
 assert.equal(f.calls.length,1);
 await act(async()=>pending.resolve(receipt()));
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed/);
 f.passiveOnly();
});

test('settled unknown is checked once on reconnect, not on unrelated state renders',async()=>{
 const f=await mount({},()=>receipt('unknown'));
 assert.equal(f.calls.length,1);
 await f.update({session:{...f.props.session,title:'Unrelated title update'}});
 assert.equal(f.calls.length,1);
 await reconnect();assert.equal(f.calls.length,2);
 await f.update({recovery:{...f.props.recovery}});
 assert.equal(f.calls.length,2);
 await f.update({recovery:{...f.props.recovery,connection:'instance-2'}});
 assert.equal(f.calls.length,3,'A changed host instance is a new connection epoch');
 await f.update({session:{...f.props.session,revision:3}});
 assert.equal(f.calls.length,3);
 f.passiveOnly();
});

test('a previous sending report cannot suppress reconnect after authoritative idle/unknown state',async()=>{
 let reads=0;
 const f=await mount({},()=>++reads===1?receipt('sending',{message:'Previously still sending.'}):receipt());
 assert.equal(f.calls.length,1);
 assert.match(serialized(f.root),/Previously still sending/);
 await f.update({
  session:{...f.props.session,status:'idle',revision:2,execution:{currentTurnId:null}},
  delivery:{status:'unknown'},localDelivery:null,
 });
 assert.equal(f.calls.length,1,'An authoritative frame is not a polling trigger');
 await reconnect();
 assert.equal(f.calls.length,2,'Old report activity is not authoritative current activity');
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed|Previously still sending/);
 f.passiveOnly();
});

test('optional report accepted=true is valid only with the bound positive identity',async()=>{
 const f=await mount({},()=>receipt('accepted',{accepted:true}));
 assert.equal(f.calls.length,1);
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed/);
 f.passiveOnly();
});

test('automatic pending read survives remount and manual Check shares its owner',async()=>{
 const pending=deferred(),f=await mount({},()=>pending.promise);
 assert.equal(f.calls.length,1);
 assert.equal(checkButton(f.root).props.disabled,true);
 await act(async()=>{checkButton(f.root).props.onClick()});
 await f.remount();
 assert.equal(checkButton(f.root).props.disabled,true);
 assert.equal(f.root.root.findByType('details').props.open,true);
 await act(async()=>{checkButton(f.root).props.onClick()});
 assert.equal(f.calls.length,1,'Remount and manual Check must join the original operation');
 await act(async()=>pending.resolve(receipt('unknown')));
 assert.match(serialized(f.root),/Delivery remains uncertain/);
 assert.equal(checkButton(f.root).props.disabled,false);
 assert.equal(f.calls.length,1,'Settling the pending owner does not start another automatic read');
 f.passiveOnly();
});

test('manual Check already in flight is joined by reconnect recovery',async()=>{
 online=false;
 const pending=deferred(),f=await mount({},()=>pending.promise);
 assert.equal(f.calls.length,0);
 let manual;
 await act(async()=>{manual=checkButton(f.root).props.onClick()});
 assert.equal(f.calls.length,1);
 online=true;await reconnect();
 assert.equal(f.calls.length,1);
 await act(async()=>{pending.resolve(receipt());await manual});
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed/);
 f.passiveOnly();
});

for(const target of ['chat','input','source']){
 test(`late positive result cannot clear a different ${target}'s warning`,async()=>{
  const first=deferred(),second=deferred();
  let readNumber=0;
  const f=await mount({},()=>++readNumber===1?first.promise:second.promise);
  const oldProps=f.props;
  const patch=target==='chat'?{session:{...oldProps.session,id:'other-chat'}}:
   target==='input'?{message:{...oldProps.message,inputId:'other-input'},localDelivery:null}:
   {recovery:{...oldProps.recovery,scope:oldProps.recovery.scope+'/different-account'}};
  await f.update(patch);
  assert.equal(f.calls.length,2,'Different exact target has a separate pending owner');
  await act(async()=>first.resolve(receipt()));
  assert.match(serialized(f.root),/Delivery not confirmed/);
  assert.equal(checkButton(f.root).props.disabled,true,'Old completion cannot settle the new owner');
  await act(async()=>second.resolve(receipt('unknown',{
   sessionId:f.props.session.id,inputId:f.props.message.inputId,message:'New target is still uncertain.',
  })));
  assert.match(serialized(f.root),/New target is still uncertain/);
  assert.match(serialized(f.root),/Delivery not confirmed/);
  assert.equal(checkButton(f.root).props.disabled,false);
  f.passiveOnly();
 });
}

for(const status of ['failed','sending','queued','queued_for_update','held','accepted']){
 test(`${status} is not an automatic uncertainty-recovery candidate`,async()=>{
  const f=await mount({delivery:{status,reason:'Retained after Stop.'},localDelivery:null});
  await reconnect();
  await f.update({session:{...f.props.session,revision:2}});
  assert.equal(f.calls.length,0);
  if(status==='held')assert.match(serialized(f.root),/Retained after Stop/);
  if(status==='failed')assert.match(serialized(f.root),/Not sent/);
  f.passiveOnly();
 });
}

for(const status of ['starting','working','running','stopping']){
 test(`exact active input during ${status} is not checked or resent automatically`,async()=>{
  const f=await mount({session:{id:'chat',status,messages:[],execution:{currentTurnId:'original'}}});
  await reconnect();
  assert.equal(f.calls.length,0);
  f.passiveOnly();
 });
}

test('another active input does not block passive recovery of this older unknown input',async()=>{
 const f=await mount({session:{id:'chat',status:'working',messages:[],execution:{currentTurnId:'newer-input'}}});
 assert.equal(f.calls.length,1);
 assert.doesNotMatch(serialized(f.root),/Delivery not confirmed/);
 f.passiveOnly();
});

for(const disposition of ['sending','queued','unknown','held','applied','next_turn']){
 test(`steering ${disposition} stays with its own delivery semantics`,async()=>{
  const message={id:'steering',inputId:'original',role:'user',text:'Direction',steering:{generationId:'run',disposition}};
  const f=await mount({message,session:{id:'chat',status:'idle',messages:[message]},localDelivery:null});
  await reconnect();
  assert.equal(f.calls.length,0);
  assert.doesNotMatch(serialized(f.root),/Retry original submission|Send again|Send this message again/);
  f.passiveOnly();
 });
}

test('no saved message or retained local payload means no actionable retry advice',async()=>{
 const f=await mount({
  message:{id:'notice',inputId:'original',role:'user'},
  session:{id:'chat',status:'idle',messages:[]},localDelivery:null,
 },()=>receipt('not_saved',{message:'No saved copy was found. Nothing was resent.'}));
 assert.equal(f.calls.length,1);
 assert.match(serialized(f.root),/Delivery not confirmed/);
 assert.match(serialized(f.root),/No saved copy was found/);
 assert.doesNotMatch(serialized(f.root),/Retry original submission|Retry the original submission|Send again|Send this message again/);
 f.passiveOnly();
});

test('a message with no conversation and no local copy cannot recommend an unavailable original retry',async()=>{
 const f=await mount({message:{inputId:'original'},session:{id:null,status:'idle',messages:[]},localDelivery:null});
 assert.equal(f.calls.length,0);
 await act(async()=>checkButton(f.root).props.onClick());
 assert.equal(f.calls.length,0);
 assert.doesNotMatch(serialized(f.root),/Retry original submission|Retry the original submission|Send again|Send this message again/);
 f.passiveOnly();
});

for(const recovery of [undefined,{scope:'',connection:'instance-1'}]){
 test(`recovery ${recovery?'without scope':'not supplied'} preserves the existing manual-only contract`,async()=>{
  const f=await mount({recovery},()=>receipt());
  assert.equal(f.calls.length,0);
  await reconnect();assert.equal(f.calls.length,0);
  await act(async()=>checkButton(f.root).props.onClick());
  assert.equal(f.calls.length,1);
  assert.doesNotMatch(serialized(f.root),/Delivery not confirmed/);
  f.passiveOnly();
 });
}

const origin=(historyScope='account-one',identity='native-chat',workspace='/fixture')=>JSON.stringify([historyScope,workspace,identity]);
const retainedEntry=(id,scope)=>({
 id,commandId:'original',sessionId:'chat',text:`Retained payload ${id}`,attachmentIds:['attachment'],
 attachments:[{id:'attachment',name:'reference.txt'}],status:'unknown',
 ...(scope===undefined?{}:{scope}),
});

test('outbox origin identifies history source, workspace and canonical native chat, not a restart epoch',()=>{
 const state={client:{historyScope:'account-one',hostInstanceId:'instance-1'}};
 const session={id:'chat',workspace:'/fixture',runtimeSessionId:'native-chat',nativeIdentity:'unused-fallback'};
 assert.equal(deliveryScope(state,session),origin());
 assert.equal(deliveryScope({...state,client:{...state.client,hostInstanceId:'instance-2'}},session),origin());
 assert.equal(deliveryScope({client:{hostInstanceId:'instance-1'}},session),origin('instance-1'));
 assert.equal(deliveryScope(state,{id:'chat',workspace:'/fixture',nativeIdentity:'native-chat'}),origin());
 assert.equal(deliveryScope(state,{id:'chat'}),origin('account-one','chat',null));
 assert.equal(deliveryScope(state,null),origin('account-one',null,null));
 assert.equal(deliveryScope({},session),null);
});

test('persisted outbox projects only exact source/workspace/native identity; legacy payload stays unbound',()=>{
 const scope=origin(),otherScope=origin('account-two');
 const rows=[
  retainedEntry('matching',scope),retainedEntry('other-source',otherScope),
  retainedEntry('other-workspace',origin('account-one','native-chat','/elsewhere')),
  retainedEntry('other-native-chat',origin('account-one','different-native-chat')),
  retainedEntry('legacy-unbound'),
 ];
 sessionStorage.setItem('amplifier.messageOutbox.v1',JSON.stringify(rows));
 const loaded=readOutbox(sessionStorage);
 assert.deepEqual(loaded,rows,'Reading must not adopt, erase or rewrite an unbound entry');
 assert.deepEqual(outboxMessages([],loaded,'chat',scope).map(row=>row.id),['matching']);
 assert.deepEqual(outboxMessages([],loaded,'chat',otherScope).map(row=>row.id),['other-source']);
 assert.deepEqual(outboxMessages([],loaded,'chat',origin('new-source')),[]);
 assert.deepEqual(outboxMessages([],loaded,'chat',undefined),[]);
 const saved={id:'server-message',inputId:'original',role:'user',text:'Server text',delivery:{status:'unknown'}};
 const projected=outboxMessages([saved],loaded,'chat',otherScope);
 assert.equal(projected.length,1);
 assert.equal(projected[0].localDelivery.id,'other-source');
 assert.equal(projected[0].text,'Server text');
 assert.deepEqual(JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')),rows);
});

test('exact scoped acknowledgement survives remount without adopting or clearing same-ID foreign and legacy copies',async()=>{
 const scope=origin(),otherScope=origin('account-two');
 const rows=[retainedEntry('old-source',scope),retainedEntry('new-source',otherScope),retainedEntry('legacy-unbound')];
 sessionStorage.setItem('amplifier.messageOutbox.v1',JSON.stringify(rows));
 let api,root;
 function Harness(){api=useMessageOutbox();return null}
 const render=()=>act(async()=>{root=create(React.createElement(Harness));roots.add(root)});
 await render();
 await act(async()=>api.acknowledge('chat','original',receipt().result,undefined));
 assert.deepEqual(api.entries,rows,'An unbound acknowledgement has no origin authority');
 await act(async()=>api.acknowledge('chat','original',receipt('unknown').result,otherScope));
 assert.deepEqual(api.entries,rows,'Generic acceptance and unknown delivery cannot retire payload');
 await act(async()=>api.acknowledge('chat','original',receipt().result,otherScope));
 const kept=[rows[0],rows[2]];
 assert.deepEqual(api.entries,kept,'Only matching scope/chat/input was acknowledged');
 assert.deepEqual(JSON.parse(sessionStorage.getItem('amplifier.messageOutbox.v1')),kept);
 await act(async()=>root.unmount());roots.delete(root);
 await render();
 assert.deepEqual(api.entries,kept,'Reloading the persisted outbox cannot recreate the acknowledged copy');
 assert.deepEqual(outboxMessages([],api.entries,'chat',otherScope),[],'Old and unbound copies never project into the new source');
 await act(async()=>api.update('new-source',{status:'unknown',error:'Late transport error'}));
 assert.deepEqual(api.entries,kept,'Late errors cannot recreate an acknowledged payload');
 assert.deepEqual(outboxMessages([],api.entries,'chat',scope).map(row=>row.id),['old-source']);
});