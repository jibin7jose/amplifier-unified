import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DesktopReadiness}=await server.ssrLoadModule('/src/desktop-readiness.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
const session={id:'synthetic-chat'};
const report={sessionId:session.id,observedAt:1,host:{host:{label:'Synthetic host'},platform:'fixture',python:{version:'fixture',path:'/fixture/python'},computerUsePackage:{status:'missing'}},nativeObservation:{available:false,status:'unsupported'},worker:{status:'unavailable',reason:'No worker started'},voiceObservation:{source:{kind:'window',label:'Earlier selected source'},expiresAt:100},nextActions:[]};
const state=()=>({computerVisual:{id:'grant-one',sessionId:session.id,available:true}});

for(const change of [{id:'replacement',sessionId:session.id,available:true},{available:false}]){
 test(`conversation screen ${change.available?'replacement':'revoke'} clears an old setup report`,async()=>{
  let current=state(),calls=0,root;
  const act=async()=>{calls++;return {result:report}};
  await renderAct(async()=>{root=create(React.createElement(DesktopReadiness,{state:current,session,act}))});
  await renderAct(async()=>root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick());
  assert.match(JSON.stringify(root.toJSON()),/Earlier selected source/);
  current={...current,computerVisual:change};
  await renderAct(async()=>root.update(React.createElement(DesktopReadiness,{state:current,session,act})));
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Earlier selected source/);
  assert.equal(calls,1,'permission changes must not automatically recheck or capture');
  await renderAct(async()=>root.unmount());
 });
}

test('a late setup result cannot restore a revoked conversation grant',async()=>{
 let release,root,current=state();const pending=new Promise(resolve=>release=resolve),act=()=>pending;
 await renderAct(async()=>{root=create(React.createElement(DesktopReadiness,{state:current,session,act}))});
 let request;
 await renderAct(async()=>{request=root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick()});
 current={...current,computerVisual:{available:false}};
 await renderAct(async()=>root.update(React.createElement(DesktopReadiness,{state:current,session,act})));
 await renderAct(async()=>{release({result:report});await request});
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/Earlier selected source/);
 await renderAct(async()=>root.unmount());
});


test('Chat Computer access mounts explicit scoped readiness beside capture controls',async()=>{
 const {ChatPages}=await server.ssrLoadModule('/src/chat-pages.jsx');
 const calls=[];let root;
 const act=async(action,args)=>{calls.push({action,args});return {result:report}};
 await renderAct(async()=>{root=create(React.createElement(ChatPages,{page:'chat-computer',state:{...state(),view:{}},session,act,computerControls:React.createElement('span',{'data-capture-controls':true},'Capture controls')}))});
 assert.equal(root.root.findAllByProps({'data-capture-controls':true}).length,1);
 assert.equal(calls.length,0,'opening chat controls does not inspect or start a worker');
 await renderAct(async()=>root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick());
 assert.deepEqual(calls,[{action:'desktop.readiness',args:{sessionId:session.id}}]);
 assert.match(JSON.stringify(root.toJSON()),/Earlier selected source/);
 await renderAct(async()=>root.unmount());
});

test('global Settings readiness never inspects the selected conversation',async()=>{
 const calls=[];let root;
 const act=async(action,args)=>{calls.push({action,args});return {result:{...report,sessionId:null}}};
 await renderAct(async()=>{root=create(React.createElement(DesktopReadiness,{state:state(),session,act,globalOnly:true}))});
 await renderAct(async()=>root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick());
 assert.deepEqual(calls,[{action:'desktop.readiness',args:{}}]);
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/Earlier selected source|Conversation computer tools/);
 await renderAct(async()=>root.unmount());
});

for(const [label,tool,toolArgs] of [['Open computer target setup','computer_use_unavailable','{"action":"discover"}'],['Open desktop doctor','desktop','{"action":"doctor"}']]){
 test(`${label} opens the tool controls with an explicit prefill`,async()=>{
  const calls=[];let root;
  const ready={...report,worker:{...report.host,status:'available',mountedTools:[],modules:[],browserContext:{reason:'Synthetic worker'},computerControl:{status:'unavailable',doctorSupported:true}}};
  const act=async(action,args)=>{calls.push({action,args});return {result:ready}};
  await renderAct(async()=>{root=create(React.createElement(DesktopReadiness,{state:state(),session,act}))});
  await renderAct(async()=>root.root.findByProps({'data-action':'desktop.readiness'}).props.onClick());
  const button=root.root.findAllByType('button').find(row=>row.props.children===label);
  await renderAct(async()=>button.props.onClick());
  const patch=calls.at(-1).args.patch;
  assert.equal(patch.panel,'chat-models');
  assert.deepEqual(patch.runtimeDraft,{tab:'tools',tool,toolArgs});
  await renderAct(async()=>root.unmount());
  const {ChatPages}=await server.ssrLoadModule('/src/chat-pages.jsx');
  await renderAct(async()=>{root=create(React.createElement(ChatPages,{page:patch.panel,state:{view:patch},session,act}))});
  assert.equal(root.root.findAllByProps({'aria-label':'Refresh tool catalog'}).length,1);
  assert.equal(calls.filter(row=>row.action==='runtime.control'&&row.args.operation==='tool.invoke').length,0,'opening the tool form must not invoke a tool');
  await renderAct(async()=>root.unmount());
 });
}
