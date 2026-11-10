import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatShortcutScope}=await server.ssrLoadModule('/src/chat-shortcuts.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

for(const missing of ['window','document'])test(`shortcut scope stays passive without ${missing}`,async()=>{
 const saved={window:globalThis.window,document:globalThis.document},calls=[];
 const target={addEventListener:()=>calls.push('add'),removeEventListener:()=>calls.push('remove')};
 globalThis.window=target;globalThis.document=target;delete globalThis[missing];
 let root;
 try{
  await act(async()=>{root=create(React.createElement(ChatShortcutScope,{enabled:true,navigation:{current:null},scope:'chat-a'},React.createElement('button',null,'Existing chat')))});
  assert.equal(root.root.findByType('button').children[0],'Existing chat');
  assert.deepEqual(calls,[]);
  await act(async()=>root.unmount());root=null;
 }finally{
  if(root)await act(async()=>root.unmount());
  for(const name of ['window','document'])if(saved[name]===undefined)delete globalThis[name];else globalThis[name]=saved[name];
 }
});
