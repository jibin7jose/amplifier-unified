import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AssignmentAdoption}=await server.ssrLoadModule('/src/assignment-adoption.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const assignment={id:'assignment',revision:3,members:{owner:{status:'bound'}}};
const button=(root,label)=>root.root.findAllByType('button').find(row=>row.children.join('')===label);
const toggle=async(root,open)=>{const node={open};await act(async()=>root.root.findByType('details').props.onToggle({target:node,currentTarget:node}))};
const observation={sessionId:'peer',title:'Existing chat',eligible:true,revision:'preview',assignmentRevision:3,effect:'Does not restart work.'};
function fixture(read=()=>Promise.resolve({result:observation})){
 const calls=[],writes=[];
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='coordination.context')return {result:{workspace:'/workspace'}};
  if(action==='coordination.list')return {result:{items:[{title:'Existing chat',target:{sessionId:'peer'}}],nextOffset:args.offset===0?20:null}};
  if(action==='assignment.adoption')return read();
  throw Error('Unexpected mutation');
 };
 return {calls,writes,props:{dispatch,sessionId:'owner',assignment,busy:false,onAdopt:args=>writes.push(args)}};
}
test('inspection is passive and add uses only the reviewed target',async()=>{
 const f=fixture();let root;
 try{
  await act(async()=>{root=create(React.createElement(AssignmentAdoption,f.props))});
  assert.equal(f.calls.length,0);
  await toggle(root,true);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:'peer'}}));
  assert.equal(f.writes.length,0);
  await act(async()=>button(root,'Add chat').props.onClick());
  assert.deepEqual(f.writes,[{sessionId:'owner',targetSessionId:'peer',expectedRevision:3,expectedTargetRevision:'preview'}]);
  assert.equal(f.calls.filter(row=>row.action==='assignment.adoption').length,1);
 }finally{await act(async()=>root.unmount())}
});
test('closing or changing assignment invalidates a late review',async()=>{
 for(const close of [false,true]){
  let done;const f=fixture(()=>new Promise(resolve=>{done=resolve}));let root;
  try{
   await act(async()=>{root=create(React.createElement(AssignmentAdoption,f.props))});
   await toggle(root,true);
   await act(()=>{void root.root.findByType('select').props.onChange({target:{value:'peer'}})});
   if(close)await toggle(root,false);
   else await act(async()=>root.update(React.createElement(AssignmentAdoption,{...f.props,assignment:{...assignment,revision:4}})));
   await act(async()=>done({result:observation}));
   assert.equal(button(root,'Add chat'),undefined);
   assert.equal(f.writes.length,0);
  }finally{await act(async()=>root.unmount())}
 }
});
test('unavailable target cannot be added; paging remains bounded',async()=>{
 const f=fixture(()=>Promise.resolve({result:{...observation,eligible:false,reason:'Current work has not settled.'}}));let root;
 try{
  await act(async()=>{root=create(React.createElement(AssignmentAdoption,f.props))});
  await toggle(root,true);
  await act(async()=>button(root,'Next chats').props.onClick());
  assert.deepEqual(f.calls.filter(row=>row.action==='coordination.list').map(row=>[row.args.offset,row.args.limit]),[[0,20],[20,20]]);
  await act(async()=>root.root.findByType('select').props.onChange({target:{value:'peer'}}));
  assert.equal(button(root,'Add chat').props.disabled,true);
  assert.match(JSON.stringify(root.toJSON()),/Current work has not settled/);
 }finally{await act(async()=>root.unmount())}
});
