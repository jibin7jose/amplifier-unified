import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({cacheDir:'.vite-reorder-focus-tests',server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ReorderList}=await vite.ssrLoadModule('/src/reorder-list.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());

async function fixture(t){
 const frames=[],composer={},grip={focus(){document.activeElement=grip;grip.calls++},calls:0};
 const globals={document:{activeElement:grip,addEventListener(){},removeEventListener(){}},window:{matchMedia:()=>({matches:true})},CSS:{escape:value=>value},requestAnimationFrame:callback=>frames.push(callback)};
 for(const [name,value]of Object.entries(globals)){const previous=Object.getOwnPropertyDescriptor(globalThis,name);Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});t.after(()=>previous?Object.defineProperty(globalThis,name,previous):delete globalThis[name])}
 let resolve,reject,root;
 const receipt=new Promise((yes,no)=>{resolve=yes;reject=no});
 const element=React.createElement(ReorderList,{items:[{id:'a',label:'A'},{id:'b',label:'B'}],ids:['a','b'],onChange:()=>receipt,renderItem:(item,{handle})=>handle});
 await act(async()=>{root=create(element,{createNodeMock:node=>node.type==='div'&&node.props.className?.startsWith('a-reorder-list')?{querySelector:()=>grip,querySelectorAll:()=>[],closest:()=>null}:null})});
 const move=async()=>act(async()=>root.root.findByProps({'aria-label':'Reorder B'}).props.onKeyDown({key:'ArrowUp',preventDefault(){}}));
 const settle=async failed=>act(async()=>failed?reject(Error('Refused reorder')):resolve({accepted:true}));
 return {composer,grip,move,settle,flush(){while(frames.length)frames.shift()()},close:async()=>act(async()=>root.unmount())};
}

for(const failed of [false,true])test(`${failed?'Rejected':'Accepted'} keyboard reorder retains the same focused handle`,async t=>{
 const f=await fixture(t);try{await f.move();await f.settle(failed);f.flush();assert.equal(document.activeElement,f.grip);assert.equal(f.grip.calls,1)}finally{await f.close()}
});
for(const failed of [false,true])test(`${failed?'Rejected':'Accepted'} delayed reorder does not steal newer composer focus`,async t=>{
 const f=await fixture(t);try{await f.move();document.activeElement=f.composer;await f.settle(failed);f.flush();assert.equal(document.activeElement,f.composer);assert.equal(f.grip.calls,0)}finally{await f.close()}
});
test('focus moved after receipt but before animation frame is preserved',async t=>{
 const f=await fixture(t);try{await f.move();await f.settle();document.activeElement=f.composer;f.flush();assert.equal(document.activeElement,f.composer);assert.equal(f.grip.calls,0)}finally{await f.close()}
});
test('unmounted reorder cannot focus a late receipt',async t=>{
 const f=await fixture(t);await f.move();await f.close();document.activeElement=f.composer;await f.settle();f.flush();assert.equal(document.activeElement,f.composer);assert.equal(f.grip.calls,0);
});
