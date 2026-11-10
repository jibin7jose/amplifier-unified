import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.location={origin:'http://fixture.invalid'};
globalThis.document={head:{},documentElement:{},getElementById:()=>null};
globalThis.getComputedStyle=()=>({getPropertyValue:()=>'',colorScheme:'light'});
globalThis.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.MutationObserver=class {observe(){} disconnect(){}};
globalThis.requestAnimationFrame=()=>1;globalThis.cancelAnimationFrame=()=>{};
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {CanvasAppViewer}=await server.ssrLoadModule('/src/canvas-app-viewer.jsx');
const {hasLocalCanvasEdits}=await server.ssrLoadModule('/src/canvas-reload-guard.js');
test.after(()=>server.close());
async function fixture(dispatcher){
 const callbacks=new Map(),out=[],calls=[],contentWindow={postMessage:data=>out.push(data)};
 globalThis.window={addEventListener:(name,fn)=>callbacks.set(name,fn),removeEventListener:name=>callbacks.delete(name)};
 let canvas={id:'fixture',sessionId:'session',title:'Test',viewId:'primary',resourceId:'fixture',resourceRevision:'r1',generation:0,app:{revision:1,stateRevision:0,state:{tab:'draw'},manifest:{},versions:[],requests:[]}},component;
 const dispatch=async(name,args)=>{calls.push({name,args});if(dispatcher)await dispatcher(name,args);return {result:{app:{...canvas.app,stateRevision:canvas.app.stateRevision+1}}}};
 await act(async()=>{component=create(React.createElement(CanvasAppViewer,{canvas,dispatch}),{createNodeMock:node=>node.type==='iframe'?{contentWindow}:null})});
 const channel=out.at(-1).channel;
 const send=async data=>act(async()=>callbacks.get('message')({source:contentWindow,data:{type:'canvas-app',id:'fixture',channel,revision:1,stateRevision:0,editVersion:1,...data}}));
 return {component,calls,out,send,dirty:()=>JSON.stringify(component.toJSON()).includes('Unsaved input'),
  update:async patch=>act(async()=>{canvas={...canvas,...patch};component.update(React.createElement(CanvasAppViewer,{canvas,dispatch}))}),
  close:()=>act(async()=>component.unmount())};
}
test('unrelated events never clear custom edits; only the acknowledged edit is committed',async()=>{
 const f=await fixture();try{
  await f.send({op:'editing',editVersion:1});assert.ok(f.dirty());
  await f.send({op:'event',requestId:'tab',args:{name:'setTab',payload:{tab:'clock'}}});assert.ok(f.dirty());
  await f.send({op:'editing',editVersion:2});
  await f.send({op:'state',requestId:'old',args:{patch:{strokes:[]}},commit:1});assert.ok(f.dirty());
  await f.send({op:'state',requestId:'save',editVersion:2,args:{patch:{strokes:[]}},commit:2});assert.equal(f.dirty(),false);
 }finally{await f.close()}
});
test('failed save keeps dirty input and rendering errors survive successful unrelated operations',async()=>{
 const f=await fixture(async name=>{if(name==='canvas.apps.state')throw Error('Conflict')});try{
  await f.send({op:'editing'});await f.send({op:'state',requestId:'save',commit:1,args:{patch:{}}});assert.ok(f.dirty());
  await f.send({op:'status',status:'error',message:'Negative radius'});
  await f.send({op:'dirty',requestId:'dirty',args:{dirty:true}});
  assert.ok(JSON.stringify(f.component.toJSON()).includes('Negative radius'));
  await f.send({op:'status',status:'ready',message:'Repaired'});assert.equal(f.component.root.findAllByProps({role:'alert'}).length,0);
 }finally{await f.close()}
});
test('duplicate snapshots and render statuses are not republished',async()=>{
 const f=await fixture();try{
  const before=f.out.length;await f.update({});assert.equal(f.out.length,before);
  await f.send({op:'status',status:'error',message:'Broken'});await f.send({op:'status',status:'error',message:'Broken'});
  assert.equal(f.calls.filter(c=>c.name==='canvas.views.status').length,1);
 }finally{await f.close()}
});

test('interactive local edits block reload until the exact commit acknowledgement and retire on unmount',async()=>{
 const f=await fixture();try{
  assert.equal(hasLocalCanvasEdits(),false);
  await f.send({op:'editing',editVersion:1});assert.equal(hasLocalCanvasEdits(),true);
  await f.send({op:'event',requestId:'unrelated',args:{name:'setTab',payload:{tab:'clock'}}});assert.equal(hasLocalCanvasEdits(),true);
  await f.send({op:'state',requestId:'commit',args:{patch:{value:'saved'}},commit:1});assert.equal(hasLocalCanvasEdits(),false);
  await f.send({op:'editing',editVersion:2});assert.equal(hasLocalCanvasEdits(),true);
 }finally{await f.close()}
 assert.equal(hasLocalCanvasEdits(),false);
});
test('rejected interactive dirty declaration stays locally protected',async()=>{
 const f=await fixture(async name=>{if(name==='canvas.views.dirty')throw Error('offline')});try{
  await f.send({op:'editing'});assert.equal(hasLocalCanvasEdits(),true);
  await f.send({op:'dirty',requestId:'clear',args:{dirty:false}});assert.equal(hasLocalCanvasEdits(),true);
 }finally{await f.close()}
 assert.equal(hasLocalCanvasEdits(),false);
});

test('a delayed discard acknowledgement cannot replace a newer interactive edit',async()=>{
 let release;const held=new Promise(resolve=>release=resolve);
 const f=await fixture(async(name,args)=>{if(name==='canvas.views.dirty'&&!args.dirty)await held});
 try{
  await f.send({op:'editing',editVersion:1});
  await f.update({app:{revision:2,stateRevision:2,state:{},manifest:{},versions:[],requests:[]}});
  const discard=()=>f.component.root.findAllByType('button').find(button=>String(button.props.children).includes('Discard unfinished input'));
  let pending;await act(async()=>{pending=discard().props.onClick();await Promise.resolve()});
  await f.send({op:'editing',editVersion:2});
  await act(async()=>{release();await pending});
  assert.ok(discard(),'new design must not replace the newer local edit');
  assert.equal(f.dirty(),true);assert.equal(hasLocalCanvasEdits(),true);
 }finally{release();await f.close()}
 assert.equal(hasLocalCanvasEdits(),false);
});
