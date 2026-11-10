import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkspaceReadiness}=await server.ssrLoadModule('/src/workspace-starters.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const setup={id:'setup-a',revision:2,status:'partial',starter:{name:'Test starter'},files:[],repositories:[{directory:'repo',status:'failed'}]};
const state=(id='a',value=setup)=>({selectedWorkspaceId:id,workspaces:[{id,setup:value}]});
const button=(root,label)=>root.root.findAllByType('button').find(row=>row.children.includes(label));
const ok=result=>({accepted:true,result});

test('inspect waits for completion and retry uses its receipt before delayed projection arrives',async()=>{
 let root,release;const calls=[];
 const dispatch=(action,args)=>{calls.push({action,args});return action==='workspace.setup.reconcile'?new Promise(resolve=>release=resolve):Promise.resolve(ok({...setup,revision:4,status:'pending'}))};
 try{
  await act(async()=>{root=create(React.createElement(WorkspaceReadiness,{state:state(),workspaceId:'a',act:dispatch}))});
  await act(async()=>{button(root,'Inspect retained imports').props.onClick()});
  assert.equal(button(root,'Retry unfinished setup').props.disabled,true);
  await act(async()=>{button(root,'Inspect retained imports').props.onClick()});
  assert.equal(calls.length,1,'Same-render/repeated click must not inspect twice');
  await act(async()=>release(ok({...setup,revision:3,inspectionStatus:'complete'})));
  assert.equal(calls.length,1,'Inspect must not provision automatically');
  await act(async()=>button(root,'Retry unfinished setup').props.onClick());
  assert.deepEqual(calls[1],{action:'workspace.setup.retry',args:{workspaceId:'a',expectedRevision:3}});
  assert.match(JSON.stringify(root.toJSON()),/Preparing workspace/);
 }finally{await act(async()=>root?.unmount())}
});

test('late inspection cannot change another workspace or retain its busy state',async()=>{
 let root,release;const calls=[];
 const dispatch=(action,args)=>{calls.push({action,args});return new Promise(resolve=>release=resolve)};
 try{
  await act(async()=>{root=create(React.createElement(WorkspaceReadiness,{state:state(),workspaceId:'a',act:dispatch}))});
  await act(async()=>{button(root,'Inspect retained imports').props.onClick()});
  await act(async()=>root.update(React.createElement(WorkspaceReadiness,{state:state('b',{...setup,id:'setup-b',revision:8}),workspaceId:'b',act:dispatch})));
  assert.equal(button(root,'Retry unfinished setup').props.disabled,false);
  await act(async()=>release(ok({...setup,revision:99,status:'ready',inspectionStatus:'complete'})));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Ready to work/);
  assert.equal(calls.length,1);
 }finally{await act(async()=>root?.unmount())}
});

test('retained unconfirmed inspection is explicit and never automatically retried',async()=>{
 let root;const calls=[];
 try{
  await act(async()=>{root=create(React.createElement(WorkspaceReadiness,{state:state(),workspaceId:'a',act:async(action,args)=>{calls.push({action,args});return ok({...setup,inspectionStatus:'pending'})}}))});
  await act(async()=>button(root,'Inspect retained imports').props.onClick());
  assert.match(JSON.stringify(root.toJSON()),/Inspection is not confirmed/);
  assert.equal(calls.length,1);
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Ready to work/);
 }finally{await act(async()=>root?.unmount())}
});

test('removed workspace renders no stale setup controls',async()=>{
 let root;
 try{
  await act(async()=>{root=create(React.createElement(WorkspaceReadiness,{state:{workspaces:[]},workspaceId:'removed',act:()=>assert.fail('No command expected')}))});
  assert.equal(root.toJSON(),null);
 }finally{await act(async()=>root?.unmount())}
});
