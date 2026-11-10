import test from 'node:test';
import assert from 'node:assert/strict';
import {deferPeerFocus,peerFocusScope,peerReturnTarget} from '../src/peer-preview-focus.js';

function fixture(){
 const listeners=new Map(),frames=new Map();let id=0;
 const doc={hidden:false,body:{isConnected:true},documentElement:{isConnected:true},
  addEventListener:(event,fn)=>listeners.set(event,fn),removeEventListener:(event,fn)=>{if(listeners.get(event)===fn)listeners.delete(event)}};
 doc.activeElement=doc.body;
 doc.defaultView={requestAnimationFrame:fn=>{frames.set(++id,fn);return id},cancelAnimationFrame:key=>frames.delete(key),getComputedStyle:n=>({visibility:n.visibility||'visible'})};
 function node(){
  const attrs=new Map();return {isConnected:true,disabled:false,inert:false,ownerDocument:doc,dataset:{},
   getAttribute:k=>attrs.get(k)??null,hasAttribute:k=>attrs.has(k),setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),
   getClientRects:()=>[{}],closest(){return this.inert?{}:null},addEventListener(event,fn){this[event]=fn},
   focus(options){this.options=options;doc.activeElement=this}};
 }
 const thread=node(),trigger=node(),disclosure=node(),message=node();
 thread.dataset.sessionId='parent';message.dataset.messageId='original';
 thread.contains=n=>[thread,trigger,disclosure,message].includes(n);
 thread.querySelectorAll=()=>[message];message.querySelector=()=>disclosure;
 trigger.closest=selector=>selector==='[data-part="messages"][data-session-id]'?thread:selector==='[data-message-id]'?message:trigger.inert?{}:null;
 const origin=peerReturnTarget(trigger,'parent','original');
 const flush=()=>{const work=[...frames.values()];frames.clear();work.forEach(fn=>fn())};
 return {doc,thread,trigger,disclosure,message,origin,listeners,flush};
}
test('valid invoker is preferred with preventScroll, after the frame boundary',()=>{
 const f=fixture();deferPeerFocus(f.origin,()=>true);assert.equal(f.doc.activeElement,f.doc.body);f.flush();
 assert.equal(f.doc.activeElement,f.trigger);assert.deepEqual(f.trigger.options,{preventScroll:true});assert.equal(f.listeners.size,0);
});
test('removed invoker returns to exact originating disclosure without input or scroll',()=>{
 const f=fixture();f.trigger.isConnected=false;deferPeerFocus(f.origin,()=>true);f.flush();
 assert.equal(f.doc.activeElement,f.disclosure);assert.deepEqual(f.disclosure.options,{preventScroll:true});
});
test('missing message returns only to the verified parent thread with temporary tab stop',()=>{
 const f=fixture();f.trigger.isConnected=false;f.thread.querySelectorAll=()=>[];
 deferPeerFocus(f.origin,()=>true);f.flush();assert.equal(f.doc.activeElement,f.thread);
 assert.equal(f.thread.getAttribute('tabindex'),'-1');f.thread.blur();assert.equal(f.thread.hasAttribute('tabindex'),false);
});
test('a replaced thread identity or inert parent refuses restoration',()=>{
 for(const change of [f=>f.thread.dataset.sessionId='other',f=>f.thread.isConnected=false,f=>{f.trigger.inert=f.disclosure.inert=f.thread.inert=true}]){
  const f=fixture();deferPeerFocus(f.origin,()=>true);change(f);f.flush();assert.equal(f.doc.activeElement,f.doc.body);
 }
});
test('new preview/navigation intent, focus, input or cancellation prevents a late return',()=>{
 for(const event of ['pointerdown','keydown','beforeinput','input','focusin']){
  const f=fixture();deferPeerFocus(f.origin,()=>true);f.listeners.get(event)();f.flush();assert.equal(f.doc.activeElement,f.doc.body);
 }
 const stale=fixture();deferPeerFocus(stale.origin,()=>false);stale.flush();assert.equal(stale.doc.activeElement,stale.doc.body);
 const chosen=fixture();deferPeerFocus(chosen.origin,()=>true);chosen.doc.activeElement={isConnected:true};const focus=chosen.doc.activeElement;chosen.flush();assert.equal(chosen.doc.activeElement,focus);
 const closed=fixture();const cancel=deferPeerFocus(closed.origin,()=>true);cancel();closed.flush();assert.equal(closed.doc.activeElement,closed.doc.body);assert.equal(closed.listeners.size,0);
});
test('origin binding rejects other messages/chats; scope changes reject navigation and draft changes',()=>{
 const f=fixture();assert.equal(peerReturnTarget(f.trigger,'other','original'),null);assert.equal(peerReturnTarget(f.trigger,'parent','other'),null);
 const state={selectedSessionId:'parent',selectedWorkspaceId:'workspace',view:{draft:'unsent',workSurface:'chat'},canvas:{id:'artifact',selectedVersion:1}};
 for(const changed of [{...state,selectedSessionId:'other'},{...state,navigationPending:true},{...state,view:{...state.view,draft:'new input'}},{...state,view:{...state.view,panel:'settings'}},{...state,canvas:{...state.canvas,selectedVersion:2}}])assert.notEqual(peerFocusScope(changed),peerFocusScope(state));
});