import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {NewChatHandoffs}=await server.ssrLoadModule('/src/new-chat-handoffs.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let root;
test.afterEach(async()=>{await act(async()=>root?.unmount());root=null});test.after(()=>server.close());
const row={requestId:'request',sourceSessionId:'source',workspace:'/project/B',title:'Read the findings',text:'Read FINDINGS.md, then wait. <script>untrusted</script>',bundle:'work',selection:{instance:'provider',model:'model'},reviewHash:'exact-review',status:'pending',revision:1,message:'Review before creating the chat.'};
const state=(entry=row,selectedSessionId='source')=>({selectedSessionId,handoffs:[entry]});
const view=(current,dispatch)=>React.createElement(NewChatHandoffs,{state:current,dispatch});
const button=action=>root.root.findAllByType('button').find(b=>b.props['data-action']===action);
const text=()=>JSON.stringify(root.toJSON());

test('review is demand-only, shows exact plain brief and hides unrelated source requests',async()=>{
 let calls=0;await act(async()=>{root=create(view(state(),()=>{calls++}))});
 assert.equal(calls,0);assert.match(text(),/FINDINGS.md/);assert.match(text(),/\/project\/B/);
 assert.equal(root.root.findAllByType('script').length,0);
 assert.equal(root.root.findByType('pre').children.join(''),row.text);
 assert.equal(button('session.select'),undefined);
 await act(async()=>root.update(view(state(row,'other'),()=>{calls++})));
 assert.equal(root.toJSON(),null);assert.equal(calls,0);
});
test('double click creates once and acceptance never navigates or sends from the browser',async()=>{
 const calls=[];let finish;
 const dispatch=(action,args)=>{calls.push({action,args});return new Promise(resolve=>{finish=resolve})};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>{button('session.handoff.confirm').props.onClick();button('session.handoff.confirm').props.onClick()});
 assert.deepEqual(calls,[{action:'session.handoff.confirm',args:{requestId:'request',reviewHash:'exact-review'}}]);
 await act(async()=>finish({accepted:true,result:{...row,revision:3,status:'accepted',creation:'created',targetSessionId:'target',message:'Opening message accepted.'}}));
 assert.equal(button('session.handoff.confirm'),undefined);assert.equal(calls.length,1);
 await act(async()=>{button('session.select').props.onClick()});
 assert.deepEqual(calls[1],{action:'session.select',args:{id:'target'}});
 await act(async()=>finish({accepted:true}));
});
test('lost acknowledgement offers a read, not another create, and newer server receipt wins',async()=>{
 const calls=[];
 const accepted={...row,revision:3,status:'accepted',creation:'created',targetSessionId:'target',message:'Opening message accepted.'};
 const dispatch=async(action,args)=>{calls.push({action,args});if(action.endsWith('.confirm'))throw Error('Connection lost');return {accepted:true,result:accepted}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());
 assert.equal(button('session.handoff.confirm'),undefined);assert.ok(button('session.handoff.read'));
 await act(async()=>button('session.handoff.read').props.onClick());
 assert.equal(calls.length,2);assert.equal(calls[1].action,'session.handoff.read');assert.ok(button('session.select'));
 await act(async()=>root.update(view(state({...accepted,revision:4,status:'unknown',message:'Delivery remains unconfirmed.'}),dispatch)));
 assert.match(text(),/Delivery remains unconfirmed/);assert.equal(calls.length,2);
});
test('only a definitive pending read re-enables confirmation after lost response',async()=>{
 const calls=[];const dispatch=async(action)=>{calls.push(action);if(action.endsWith('.confirm'))throw Error('Connection lost');return {accepted:true,result:row}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.confirm').props.onClick());assert.equal(button('session.handoff.confirm'),undefined);
 await act(async()=>button('session.handoff.read').props.onClick());assert.ok(button('session.handoff.confirm'));assert.equal(calls.length,2);
});
test('cancellation sends no creation or navigation and terminal rows remain inspectable',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...row,revision:2,status:'cancelled',message:'Nothing was created.'}}};
 await act(async()=>{root=create(view(state(),dispatch))});
 await act(async()=>button('session.handoff.cancel').props.onClick());
 assert.deepEqual(calls,[{action:'session.handoff.cancel',args:{requestId:'request'}}]);assert.match(text(),/Nothing was created/);assert.equal(button('session.handoff.confirm'),undefined);assert.equal(button('session.select'),undefined);
});
