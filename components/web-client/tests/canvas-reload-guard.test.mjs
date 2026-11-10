import test from 'node:test';
import assert from 'node:assert/strict';
import {createCanvasReloadGuard,hasLocalCanvasEdits,canvasReloadBlocked} from '../src/canvas-reload-guard.js';
import {reloadWhenSaved} from '../src/app-reload.js';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
test('local intent blocks before dispatch and remains after rejected dirty declaration',async()=>{
 const guard=createCanvasReloadGuard(),retire=guard.mount();try{
  const wait=deferred(),write=guard.declare(true,()=>{assert.equal(hasLocalCanvasEdits(),true);return wait.promise});
  wait.reject(Error('offline'));await assert.rejects(write,/offline/);assert.equal(hasLocalCanvasEdits(),true);
  await assert.rejects(guard.declare(false,()=>{throw Error('still offline')}),/still offline/);assert.equal(hasLocalCanvasEdits(),true);
  const clear=deferred(),clearing=guard.declare(false,()=>clear.promise);assert.equal(hasLocalCanvasEdits(),true);
  clear.resolve();await clearing;assert.equal(hasLocalCanvasEdits(),false);
 }finally{retire()}
});
test('a newer edit survives an earlier clean acknowledgement, other mounted owners remain protected',async()=>{
 const a=createCanvasReloadGuard(),b=createCanvasReloadGuard(),ra=a.mount(),rb=b.mount();try{
  await a.declare(true,async()=>{});const clear=deferred(),clearing=a.declare(false,()=>clear.promise);
  await a.declare(true,async()=>{});clear.resolve();await clearing;assert.equal(hasLocalCanvasEdits(),true);
  await b.declare(true,async()=>{});await a.declare(false,async()=>{});assert.equal(hasLocalCanvasEdits(),true);
  rb();assert.equal(hasLocalCanvasEdits(),false);
 }finally{ra();rb()}
});
test('unmount retires pending owners and late acknowledgements cannot clear a remounted edit',async()=>{
 const guard=createCanvasReloadGuard(),retire=guard.mount(),old=deferred(),write=guard.declare(false,()=>old.promise);
 retire();assert.equal(hasLocalCanvasEdits(),false);await assert.rejects(guard.declare(true,async()=>{}),/no longer mounted/);
 const again=guard.mount();try{await guard.declare(true,async()=>{});old.resolve();await write;assert.equal(hasLocalCanvasEdits(),true)}finally{again()}
 assert.equal(hasLocalCanvasEdits(),false);
});
test('canonical dirty views block even without local owners',()=>{
 assert.equal(canvasReloadBlocked({views:[{dirty:true}]}),true);assert.equal(canvasReloadBlocked({views:[{dirty:false}]}),false);assert.equal(canvasReloadBlocked(null),false);
});
test('reload rechecks after asynchronous saves so a new local edit cannot be lost',async()=>{
 const guard=createCanvasReloadGuard(),retire=guard.mount(),save=deferred();let navigated=false;
 try{const reloading=reloadWhenSaved({prepare:()=>save.promise,assertReady:()=>{if(canvasReloadBlocked())throw Error('unfinished Canvas edit')},reload:()=>{navigated=true}});
  await guard.declare(true,async()=>{});save.resolve();await assert.rejects(reloading,/unfinished Canvas edit/);assert.equal(navigated,false);
 }finally{retire()}
});
