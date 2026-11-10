import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ManagedResources}=await server.ssrLoadModule('/src/workspace-managed-resources.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const workspace={id:'w',path:'/work'},state={sessions:[{id:'a',title:'Owner A',workspace:'/work'}],selectedSessionId:'a'};
const originalFetch=globalThis.fetch;
test.beforeEach(()=>{globalThis.fetch=async url=>{
 const query=new URL(url,'http://fixture').searchParams;
 assert.equal(query.get('workspace'),'/work');assert.equal(query.get('kind'),'root');assert.equal(query.get('limit'),'50');
 return new Response(JSON.stringify({items:[{id:'a',title:'Owner A'},{id:'b',title:'Owner B'}],nextOffset:null}));
}});
test.afterEach(()=>{globalThis.fetch=originalFetch});
const result=sid=>({accepted:true,result:{sessionId:sid,publishing:{items:[{id:sid,kind:'preview',status:'running',port:12345,url:'http://127.0.0.1:12345/',releaseId:'release',releaseDirectory:'/retained/release/files'}]},operations:{items:[{id:'p-'+sid,kind:'process',state:'outcome_unknown'}]}}});
for(const transition of ['selected task','inactive workspace','closed panel'])for(const fail of [false,true]){
 test(`ignores late ${fail?'error':'page'} after ${transition}`,async()=>{
  let root,release;
  const calls=[];
  const dispatch=(action,args)=>{
   calls.push({action,args});
   if(action==='workspace.environments.read')return Promise.resolve({accepted:true,result:{items:[],capability:{status:'unavailable'}}});
   assert.equal(action,'workspace.resources.managed','inspection only reads resource owners');
   if(calls.length===1)return new Promise((resolve,reject)=>{release=()=>fail?reject(Error('STALE ERROR')):resolve(result('STALE'))});
   return Promise.resolve(result(args.sessionId));
  };
  const props={workspace,state,act:dispatch,active:true};
  try{
   await act(async()=>{root=create(React.createElement(ManagedResources,props))});
   assert.equal(calls.length,0,'collapsed view is passive');
   await act(async()=>root.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
   if(transition==='selected task')await act(async()=>root.root.findByType('select').props.onChange({target:{value:'b'}}));
   else if(transition==='inactive workspace')await act(async()=>root.update(React.createElement(ManagedResources,{...props,active:false})));
   else await act(async()=>root.root.findByType('details').props.onToggle({currentTarget:{open:false}}));
   await act(async()=>release());
   const visible=JSON.stringify(root.toJSON());
   assert.doesNotMatch(visible,/STALE/);
   if(transition==='selected task'){
    assert.match(visible,/Owner B/);assert.match(visible,/outcome unknown/);assert.match(visible,/Task storage/);
   }
   assert.deepEqual(calls,[
    {action:'workspace.resources.managed',args:{workspaceId:'w',sessionId:'a',limit:25}},
    ...(transition==='selected task'?[
     {action:'workspace.resources.managed',args:{workspaceId:'w',sessionId:'b',limit:25}},
     {action:'workspace.environments.read',args:{workspaceId:'w',sessionId:'b',offset:0}},
    ]:[]),
   ],'only the current page mounts its environment reader; no mutations or stale-owner reads');
  }finally{await act(async()=>root.unmount())}
 });
}

test('owner page controls preserve other cursor and reset explicitly',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{calls.push(args);const value=result(args.sessionId);value.result.publishing.nextCursor=['preview','last'];value.result.operations.nextCursor='last-operation';return value};
 try{
  await act(async()=>{root=create(React.createElement(ManagedResources,{workspace,state,act:dispatch,active:true}))});
  await act(async()=>root.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
  const click=label=>root.root.findAllByType('button').find(row=>row.children.includes(label)).props.onClick();
  await act(async()=>click('More publishing resources'));
  await act(async()=>click('More recorded operations'));
  assert.deepEqual(calls.at(-1),{workspaceId:'w',sessionId:'a',limit:25,publishingBefore:['preview','last'],operationsBefore:'last-operation'});
  await act(async()=>click('Refresh managed resources'));
  assert.deepEqual(calls.at(-1),{workspaceId:'w',sessionId:'a',limit:25});
 }finally{await act(async()=>root.unmount())}
});
