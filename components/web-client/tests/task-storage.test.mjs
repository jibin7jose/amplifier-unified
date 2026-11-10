import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {TaskStorage}=await server.ssrLoadModule('/src/workspace-task-storage.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const store=new Map();globalThis.sessionStorage={getItem:key=>store.get(key),setItem:(key,value)=>store.set(key,value)};
test.after(()=>server.close());test.beforeEach(()=>store.clear());
const row={id:'resource',kind:'scratch-directory',label:'Owned',status:'ready',revision:3,path:'/private/task/resource',access:{status:'denied'},observation:'present'};
const page={items:[row],nextOffset:null};
const ok=value=>({accepted:true,result:value});
const props={workspaceId:'workspace',sessionId:'owner',active:true,initial:page};
const click=(root,label)=>root.root.findAllByType('button').find(button=>button.children.includes(label)).props.onClick();
const submit=root=>root.root.findByType('form').props.onSubmit({preventDefault(){}});

test('draft and unconfirmed command survive remount; retry uses identical id and arguments',async()=>{
 let root;const calls=[];let lost=true;
 const dispatch=async(action,args,options)=>{calls.push({action,args,options});if(lost){lost=false;throw Error('Lost response')}return ok(page)};
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:dispatch}))});
  await act(async()=>root.root.findByType('input').props.onChange({target:{value:'Keep my label'}}));
  await act(async()=>submit(root));
  assert.match(JSON.stringify(root.toJSON()),/Lost response/);
  assert.equal(calls.length,1);
  await act(async()=>root.unmount());
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:dispatch}))});
  assert.equal(root.root.findByType('input').props.value,'Keep my label');
  await act(async()=>click(root,'Check the same storage change'));
  assert.deepEqual(calls[1],calls[0]);assert.equal(calls[2].action,'workspace.resources.read');
  assert.equal(root.root.findByType('input').props.value,'');
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/no confirmed response/);
 }finally{await act(async()=>root.unmount())}
});

test('retirement uses exact displayed revision; error retains files and request',async()=>{
 let root;const calls=[];
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:async(...args)=>{calls.push(args);throw Error('The allocation changed')}}))});
  await act(async()=>click(root,'Retire and retain files'));
  assert.deepEqual(calls[0][1],{workspaceId:'workspace',sessionId:'owner',id:'resource',expectedRevision:3});
  assert.match(JSON.stringify(root.toJSON()),/allocation changed/);
  await act(async()=>click(root,'Dismiss retry'));
  assert.equal(calls.length,1);assert.match(JSON.stringify(root.toJSON()),/Owned/);
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/no confirmed response/);
 }finally{await act(async()=>root.unmount())}
});

for(const mode of ['readOnly','inactive'])test(`${mode} cannot start or retry storage changes`,async()=>{
 let root;const calls=[];
 store.set('amplifier-task-storage:workspace:owner',JSON.stringify({kind:'scratch-directory',label:'Pending',pending:{action:'workspace.resources.provision',args:{workspaceId:'workspace',sessionId:'owner',kind:'scratch-directory'},id:'existing'}}));
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,readOnly:mode==='readOnly',active:mode!=='inactive',act:async(...args)=>{calls.push(args);return ok(page)}}))});
  if(mode==='readOnly'){
   assert.equal(root.root.findAllByType('form').length,0);
   assert.ok(!root.root.findAllByType('button').some(button=>button.children.includes('Check the same storage change')));
  }else assert.ok(root.root.findAllByType('button').filter(button=>!button.children.includes('Dismiss retry')).every(button=>button.props.disabled));
  assert.equal(calls.length,0);
 }finally{await act(async()=>root.unmount())}
});

test('late read after navigation cannot replace current page or expose old error',async()=>{
 let root,release;
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:()=>new Promise(resolve=>release=resolve)}))});
  await act(async()=>{click(root,'Refresh task storage')});
  await act(async()=>root.update(React.createElement(TaskStorage,{...props,active:false,initial:{items:[],nextOffset:null},act:async()=>ok(page)})));
  await act(async()=>release(ok({items:[{...row,label:'STALE PAGE'}],nextOffset:null})));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/STALE PAGE/);
  await act(async()=>root.update(React.createElement(TaskStorage,{...props,active:true,act:async()=>ok(page)})));
  assert.equal(root.root.findAllByType('button').find(button=>button.children.includes('Refresh task storage')).props.disabled,false);
 }finally{await act(async()=>root.unmount())}
});

test('resource ownership and access limitations remain visible without mutation',async()=>{
 let root;const calls=[];
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:async(...args)=>{calls.push(args);return ok(page)}}))});
  const rendered=JSON.stringify(root.toJSON());
  for(const text of ['denied','file-access settings','does not launch','retains all contents'])assert.ok(rendered.includes(text));
  assert.equal(calls.length,0);
 }finally{await act(async()=>root.unmount())}
});

test('blocked browser storage prevents allocation without a recoverable identity',async()=>{
 let root;const calls=[],save=sessionStorage.setItem;
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:async(...args)=>{calls.push(args);return ok(page)}}))});
  sessionStorage.setItem=()=>{throw Error('Storage blocked')};
  await act(async()=>submit(root));
  assert.equal(calls.length,0,'No allocation may be sent without its saved recovery request');
  assert.match(JSON.stringify(root.toJSON()),/Nothing was sent/);
 }finally{sessionStorage.setItem=save;await act(async()=>root.unmount())}
});

test('a late completed mount cannot erase a newer saved allocation request',async()=>{
 let root,release;const calls=[];
 const dispatch=async(...args)=>{calls.push(args);return new Promise(resolve=>release=resolve)};
 const key='amplifier-task-storage:workspace:owner';
 try{
  await act(async()=>{root=create(React.createElement(TaskStorage,{...props,act:dispatch}))});
  await act(async()=>{submit(root)});
  const first=JSON.parse(store.get(key));
  await act(async()=>root.unmount());
  const newer={...first,label:'Newer allocation',pending:{...first.pending,id:'newer-request'}};
  store.set(key,JSON.stringify(newer));
  await act(async()=>release(ok(page)));
  assert.deepEqual(JSON.parse(store.get(key)),newer);
  assert.equal(calls.length,1,'An old unmounted completion cannot refresh its old owner');
 }finally{await act(async()=>root.unmount())}
});
