import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {LiveActivity}=await server.ssrLoadModule('/src/live-activity.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('elapsed ticks render only the activity leaf and stop while hidden or idle',async()=>{
 const original={document:globalThis.document,now:Date.now,interval:globalThis.setInterval,clear:globalThis.clearInterval};
 let now=100000,tick,root,parentRenders=0;const listeners=new Set();
 globalThis.document={hidden:false,addEventListener:(_name,fn)=>listeners.add(fn),removeEventListener:(_name,fn)=>listeners.delete(fn)};
 Date.now=()=>now;globalThis.setInterval=fn=>{tick=fn;return 1};globalThis.clearInterval=()=>{tick=null};
 let session={id:'one',status:'working',messages:[{role:'user',createdAt:100}]};
 function Parent(){parentRenders++;return React.createElement(LiveActivity,{session})}
 try{
  await act(async()=>{root=create(React.createElement(Parent))});assert.equal(parentRenders,1);
  now+=1000;await act(async()=>tick());
  assert.equal(root.root.findByProps({role:'timer'}).children[0],'1s');assert.equal(parentRenders,1);
  document.hidden=true;now+=1000;await act(async()=>tick());
  assert.equal(root.root.findByProps({role:'timer'}).children[0],'1s');
  document.hidden=false;await act(async()=>listeners.forEach(fn=>fn()));
  assert.equal(root.root.findByProps({role:'timer'}).children[0],'2s');
  session={...session,status:'idle'};await act(async()=>root.update(React.createElement(Parent)));
  assert.equal(root.toJSON(),null);assert.equal(tick,null);assert.equal(listeners.size,0);
 }finally{
  await act(async()=>root?.unmount());globalThis.document=original.document;Date.now=original.now;
  globalThis.setInterval=original.interval;globalThis.clearInterval=original.clear;
 }
});