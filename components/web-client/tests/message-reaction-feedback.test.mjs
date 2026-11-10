import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MessageInteractions}=await vite.ssrLoadModule('/src/message-interactions.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>vite.close());
const pointer={currentTarget:{closest:()=>null,getBoundingClientRect:()=>({top:200})}};
test('exact accepted reaction overlays detached row; a newer canonical removal wins',async()=>{
 let root,release,request,sessionId='one',message={id:'old',text:'Canonical unchanged'};
 const render=()=>React.createElement(MessageInteractions,{message,sessionId,dispatch:()=>new Promise(resolve=>release=resolve)});
 try{
  await act(async()=>root=create(render()));await act(async()=>root.root.findByProps({'aria-label':'React to message'}).props.onClick(pointer));
  await act(async()=>{request=root.root.findByProps({'aria-label':'Heart'}).props.onClick(pointer)});
  assert.equal(root.root.findAllByProps({'aria-label':'Remove Heart reaction'}).length,1,'Local feedback must render while the server response is deliberately held');
  assert.notEqual(root.root.findByProps({'aria-label':'Remove Heart reaction'}).props.disabled,true,'Pending feedback must remain reversible');
  await act(async()=>{release({accepted:true,result:{sessionId:'one',messageId:'old',reactionActor:'user',reactions:['❤️'],annotationRevision:2}});await request});
  assert.equal(root.root.findByProps({'aria-label':'Remove Heart reaction'}).props['aria-pressed'],'true');
  assert.deepEqual(message,{id:'old',text:'Canonical unchanged'});
  message={...message,reactions:[],annotationRevision:1};await act(async()=>root.update(render()));assert.equal(root.root.findAllByProps({'aria-label':'Remove Heart reaction'}).length,1,'Stale annotation read cannot undo newer accepted receipt');
  message={...message,reactions:[],annotationRevision:3};await act(async()=>root.update(render()));assert.equal(root.root.findAllByProps({'aria-label':'Remove Heart reaction'}).length,0,'Later authoritative removal wins');
 }finally{await act(async()=>root?.unmount())}
});
test('a held reaction cannot decorate another chat reusing the same message ID',async()=>{
 let root,release,request,sessionId='one';const message={id:'same',text:'Original'};
 const render=()=>React.createElement(MessageInteractions,{message,sessionId,dispatch:()=>new Promise(resolve=>release=resolve)});
 try{
  await act(async()=>root=create(render()));await act(async()=>root.root.findByProps({'aria-label':'React to message'}).props.onClick(pointer));await act(async()=>{request=root.root.findByProps({'aria-label':'Heart'}).props.onClick(pointer)});
  sessionId='two';await act(async()=>root.update(render()));await act(async()=>{release({accepted:true,result:{sessionId:'one',messageId:'same',reactionActor:'user',reactions:['❤️'],annotationRevision:2}});await request});
  assert.equal(root.root.findAllByProps({'aria-label':'Remove Heart reaction'}).length,0);assert.deepEqual(message,{id:'same',text:'Original'});
 }finally{await act(async()=>root?.unmount())}
});
