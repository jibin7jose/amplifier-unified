import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

// No listener or generated assets: exercise the actual panel with deferred I/O.
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {OperationsPanel}=await server.ssrLoadModule('/src/operations.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const response=(result,status=200)=>new Response(JSON.stringify(status===200?{accepted:true,result}:{error:result}),{status});

for(const action of ['operations.list','operations.request'])for(const transition of ['conversation','reopen'])for(const outcome of ['success','error']){
 test(`${action} ignores late ${outcome} after ${transition}`,async()=>{
  const original=globalThis.fetch,calls=[];
  let root,defer=false,release,pending;
  globalThis.fetch=async(path,options)=>{
   const call=JSON.parse(options.body);calls.push(call);
   if(defer&&call.action===action){defer=false;return new Promise(resolve=>{release=()=>resolve(outcome==='error'?response('STALE ERROR',409):response(action==='operations.list'?{operations:[{id:'stale',kind:'process',state:'failed'}],requests:[]}:{requestId:'a-request',state:'accepted',message:'STALE RECEIPT'}))})}
   if(call.action==='operations.list')return response({operations:[{id:call.args.sessionId,kind:'process',state:'completed'}],requests:[{requestId:'a-request',state:'outcome_unknown'}]});
   if(call.action==='question.list')return response({items:[]});
   throw new Error('Unexpected fixture action: '+call.action);
  };
  const toggle=()=>root.root.findAllByType('button')[0].props.onClick();
  try{
   await act(async()=>{root=create(React.createElement(OperationsPanel,{sessionId:'A'}))});
   await act(async()=>toggle());
   defer=true;
   await act(async()=>{pending=root.root.findByProps({'data-action':action}).props.onClick()});
   assert.equal(typeof release,'function');
   if(transition==='conversation')await act(async()=>root.update(React.createElement(OperationsPanel,{sessionId:'B'})));
   else await act(async()=>toggle());
   await act(async()=>toggle());
   assert.match(JSON.stringify(root.toJSON()),/Completed/);
   await act(async()=>{release();await pending});
   const rendered=JSON.stringify(root.toJSON());
   assert.doesNotMatch(rendered,/STALE|Failed/);
   assert.match(rendered,/Completed/);
   assert.ok(calls.every(call=>['operations.list','operations.request','question.list'].includes(call.action)),'read completion must never resubmit work');
  }finally{if(root)await act(async()=>root.unmount());globalThis.fetch=original}
 });
}

test('current-view refresh and request checks still update observed evidence',async()=>{
 const original=globalThis.fetch;
 let root,completed=false;
 globalThis.fetch=async(path,options)=>{
  const call=JSON.parse(options.body);
  assert.equal(call.args.sessionId,'A');
  if(call.action==='operations.list')return response({operations:[{id:'owned',kind:'process',state:completed?'completed':'running'}],requests:[{requestId:'request',state:'outcome_unknown'}]});
  if(call.action==='operations.request')return response({requestId:'request',state:'accepted',message:'ADMISSION CONFIRMED'});
  return response({items:[]});
 };
 try{
  await act(async()=>{root=create(React.createElement(OperationsPanel,{sessionId:'A'}))});
  await act(async()=>root.root.findAllByType('button')[0].props.onClick());
  assert.match(JSON.stringify(root.toJSON()),/Running/);
  completed=true;
  await act(async()=>root.root.findByProps({'data-action':'operations.list'}).props.onClick());
  assert.match(JSON.stringify(root.toJSON()),/Completed/);
  await act(async()=>root.root.findByProps({'data-action':'operations.request'}).props.onClick());
  assert.match(JSON.stringify(root.toJSON()),/ADMISSION CONFIRMED/);
 }finally{if(root)await act(async()=>root.unmount());globalThis.fetch=original}
});

for(const action of ['operations.list','question.list']){
 test(`aborted initial ${action} cannot replace a new conversation`,async()=>{
  const original=globalThis.fetch;
  let root,release;
  globalThis.fetch=async(path,options)=>{
   const call=JSON.parse(options.body);
   if(call.args.sessionId==='A'&&call.action===action)return new Promise(resolve=>{release=()=>resolve(response(action==='operations.list'?{operations:[{id:'old',kind:'process',state:'failed'}]}:{items:[{id:'old',prompt:'STALE QUESTION',status:'answered'}]}))});
   return response(call.action==='operations.list'?{operations:[],requests:[]}:{items:[]});
  };
  const toggle=()=>root.root.findAllByType('button')[0].props.onClick();
  try{
   await act(async()=>{root=create(React.createElement(OperationsPanel,{sessionId:'A'}))});
   await act(async()=>toggle());
   await act(async()=>root.update(React.createElement(OperationsPanel,{sessionId:'B'})));
   await act(async()=>toggle());
   await act(async()=>release());
   assert.doesNotMatch(JSON.stringify(root.toJSON()),/STALE|Failed/);
  }finally{if(root)await act(async()=>root.unmount());globalThis.fetch=original}
 });
}
