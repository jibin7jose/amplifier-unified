import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ScheduleControls}=await server.ssrLoadModule('/src/schedules.jsx');
const {NotificationSettings}=await server.ssrLoadModule('/src/notification-settings.jsx');
test.after(()=>server.close());
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};

test('schedule completion preserves newer drafts and stale previews never become approval',async()=>{
 let renderer,request,holdPreview=false,previewSequence=0;
 const calls=[];
 const previewReceipt=(args,previewHash)=>({accepted:true,result:{previewHash,binding:args,occurrences:[{utc:'one',dueAt:1}]}});
 const send=(action,args)=>{
  calls.push({action,args});
  if(action==='schedule.preview'&&!holdPreview)return Promise.resolve(previewReceipt(args,`fixture-${++previewSequence}`));
  request={...deferred(),action,args};return request.promise;
 };
 await act(async()=>{renderer=create(React.createElement(ScheduleControls,{session:{id:'chat'},act:send}))});
 const input=()=>renderer.root.findByProps({id:'schedule-prompt'});
 const fill=value=>act(()=>input().props.onChange({target:{value}}));
 const click=action=>renderer.root.findByProps({'data-action':action}).props.onClick();
 const openCreate=()=>renderer.root.findAllByType('button').find(row=>row.children.includes('Create schedule')).props.onClick();
 const previews=()=>renderer.root.findAllByProps({'data-part':'schedule-preview'});
 const saves=()=>renderer.root.findAllByProps({'data-action':'schedule.create'});
 try{
  assert.equal(renderer.root.findAllByProps({id:'schedule-prompt'}).length,0);
  await act(()=>{openCreate()});
  await fill('First schedule');await act(async()=>{await click('schedule.preview')});
  assert.equal(previews().length,1);let submitted;
  await act(()=>{submitted=click('schedule.create')});await fill('A different schedule');
  await act(async()=>{request.resolve({accepted:true});await submitted});
  assert.equal(input().props.value,'A different schedule');
  assert.equal(previews().length,0);
  assert.equal(saves().length,0,'Completing the older save must not approve the newer draft');
  await act(async()=>{await click('schedule.preview')});
  assert.equal(previews().length,1);
  const unconfirmedSave=saves()[0].props.onClick;
  await act(()=>{submitted=unconfirmedSave()});
  const unconfirmedHash=request.args.previewHash;
  await act(async()=>{request.resolve(undefined);await submitted});assert.equal(input().props.value,'A different schedule');
  assert.equal(previews().length,0,'An unconfirmed save consumes its preview');
  assert.equal(saves().length,0,'A fresh preview is required before another save');
  assert.match(renderer.root.findByProps({role:'alert'}).children.join(''),/could not be confirmed/);
  const callsBeforeRetry=calls.length;
  await act(()=>{submitted=unconfirmedSave()});
  assert.equal(calls.length,callsBeforeRetry,'A retained save callback must not replay the consumed preview');
  await act(async()=>{await submitted});
  await act(async()=>{await click('schedule.preview')});
  assert.equal(previews().length,1);
  await act(()=>{submitted=click('schedule.create')});
  assert.notEqual(request.args.previewHash,unconfirmedHash,'The next save must use the fresh preview');
  assert.equal(request.args.prompt,'A different schedule');
  assert.equal(request.args.sessionId,'chat');
  await act(async()=>{request.resolve({accepted:true});await submitted});
  assert.equal(renderer.root.findAllByProps({id:'schedule-prompt'}).length,0,'An unchanged successful save closes the editor');
  await act(()=>{openCreate()});assert.equal(input().props.value,'');
  holdPreview=true;await fill('Preview this');await act(()=>{submitted=click('schedule.preview')});await fill('Changed before preview');
  assert.equal(request.action,'schedule.preview');
  assert.equal(request.args.prompt,'Preview this');
  assert.equal(request.args.sessionId,'chat');
  await act(async()=>{request.resolve(previewReceipt(request.args,'old'));await submitted});
  assert.equal(input().props.value,'Changed before preview');
  assert.equal(previews().length,0,'A valid owner-bound receipt for an older draft must not become approval');
  assert.equal(saves().length,0);
  assert.equal(renderer.root.findAllByProps({role:'alert'}).length,0,'The late receipt must be discarded, not rejected as malformed');
  await act(()=>{submitted=click('schedule.preview')});
  assert.equal(request.args.prompt,'Changed before preview');
  await act(async()=>{request.resolve(previewReceipt(request.args,'current'));await submitted});
  assert.equal(previews().length,1,'The same receipt shape must be accepted for the current draft');
  assert.equal(saves().length,1);
  assert.equal(input().props.value,'Changed before preview');
 }finally{await act(()=>renderer.unmount())}
});

test('notification secrets wait for confirmed completion and preserve edits per field',async t=>{
 // The permission hook observes browser events; provide that environment while
 // keeping these tests focused on saved preferences and private draft receipts.
 const prior={window:globalThis.window,document:globalThis.document};
 globalThis.window=new EventTarget();globalThis.document=new EventTarget();
 t.after(()=>{for(const [key,value] of Object.entries(prior)){if(value===undefined)delete globalThis[key];else globalThis[key]=value}});
 let renderer,request,state={view:{}},sequence=0;
 const send=()=>{request=deferred();return request.promise};
 const render=()=>React.createElement(NotificationSettings,{state,act:send});
 await act(async()=>{renderer=create(render())});
 const inputs=()=>renderer.root.findAllByType('input').filter(row=>row.props.type==='password');
 const fill=(index,value)=>act(()=>inputs()[index].props.onChange({target:{value}}));
 const save=()=>renderer.root.findAllByType('button').find(row=>row.children.includes('Save phone connection')).props.onClick();
 const complete=async(id,phase)=>{state={...state,actionStatus:{'notifications.save':{commandId:id,phase,error:phase==='error'?'Try again':undefined}}};await act(()=>renderer.update(render()))};
 try{
  await fill(0,'fixture-topic');await fill(1,'fixture-token');let submitted;
  for(const result of [undefined,{accepted:false}]){
   await act(()=>{submitted=save()});await act(async()=>{request.resolve(result);await submitted});
   assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic','fixture-token']);
  }
  await act(()=>{submitted=save()});await act(async()=>{request.resolve({accepted:true,operationId:'failed'});await submitted});
  await complete('failed','error');assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic','fixture-token']);
  await act(()=>{submitted=save()});await fill(0,'changed');await fill(0,'fixture-topic');
  await act(async()=>{request.resolve({accepted:true,operationId:'saved'});await submitted});
  assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic','fixture-token'],'Acknowledgment is not completion');
  await complete('saved','ready');
  assert.deepEqual(inputs().map(row=>row.props.value),['fixture-topic',''],'Only the unchanged field clears');
  await act(()=>{submitted=save()});await act(async()=>{request.resolve({accepted:true,operationId:'again'});await submitted});await complete('again','ready');
  assert.deepEqual(inputs().map(row=>row.props.value),['','']);
 }finally{await act(()=>renderer.unmount())}
});
