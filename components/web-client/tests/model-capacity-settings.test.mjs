import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ModelCapacitySettings}=await server.ssrLoadModule('/src/model-capacity-settings.jsx');
const {liveActivity}=await server.ssrLoadModule('/src/activity.js');
const {durationHint}=await server.ssrLoadModule('/src/duration-hint.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.crypto??=webcrypto;
const storage=new Map();globalThis.sessionStorage={getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)};
test.after(()=>server.close());test.beforeEach(()=>storage.clear());
const button=(root,label)=>root.root.findAllByType('button').find(node=>node.children.includes(label));
const edit=(root,id,value)=>renderAct(async()=>root.root.findByProps({id}).props.onChange({target:{value}}));
const initial={revision:0,maxWaitingCalls:256,waitTimeoutSeconds:0};

test('global settings need no chat and save an explicit no-deadline preference',async()=>{
 let root;const calls=[];const act=async(action,args,options)=>{calls.push({action,args,options});return {accepted:true,result:action.endsWith('read')?initial:{revision:1,maxWaitingCalls:500,waitTimeoutSeconds:0}}};
 await renderAct(async()=>{root=create(React.createElement(ModelCapacitySettings,{act}))});
 assert.deepEqual(calls[0].args,{});assert.match(JSON.stringify(root.toJSON()),/all current and new workspaces/);
 await edit(root,'model-capacity-max-waiting','500');
 await edit(root,'model-capacity-timeout','');assert.equal(button(root,'Save model-capacity settings').props.disabled,true);
 await edit(root,'model-capacity-timeout','0');
 await renderAct(async()=>button(root,'Save model-capacity settings').props.onClick());
 assert.deepEqual(calls[1].args,{expectedRevision:0,maxWaitingCalls:500,waitTimeoutSeconds:0});
 assert.match(JSON.stringify(root.toJSON()),/Saved for all chats/);
 await renderAct(async()=>root.unmount());
});

test('lost acknowledgement survives reopen and retries the identical save',async()=>{
 let root,attempt=0;const saves=[];
 const act=async(action,args,options)=>{if(action.endsWith('read'))return {accepted:true,result:initial};saves.push({args,options});if(++attempt===1)throw Error('Response lost');return {accepted:true,result:{revision:1,maxWaitingCalls:300,waitTimeoutSeconds:0}}};
 await renderAct(async()=>{root=create(React.createElement(ModelCapacitySettings,{act}))});
 await edit(root,'model-capacity-max-waiting','300');await renderAct(async()=>button(root,'Save model-capacity settings').props.onClick());
 assert.match(JSON.stringify(root.toJSON()),/Response lost/);await renderAct(async()=>root.unmount());
 await renderAct(async()=>{root=create(React.createElement(ModelCapacitySettings,{act}))});
 assert.equal(root.root.findByType('fieldset').props.disabled,true);
 await renderAct(async()=>button(root,'Check the same save').props.onClick());
 assert.deepEqual(saves[0],saves[1]);assert.equal(storage.size,0);await renderAct(async()=>root.unmount());
});

test('stale edits are retained and cannot overwrite a newer observed revision',async()=>{
 let root,read=0;
 const act=async(action)=>{if(action.endsWith('read'))return {accepted:true,result:read++?{...initial,revision:1,maxWaitingCalls:400}:initial};const error=Error('Settings changed');error.status=409;throw error};
 await renderAct(async()=>{root=create(React.createElement(ModelCapacitySettings,{act}))});
 await edit(root,'model-capacity-max-waiting','300');await renderAct(async()=>button(root,'Save model-capacity settings').props.onClick());
 assert.equal(storage.size,0);await renderAct(async()=>button(root,'Reload saved settings').props.onClick());
 assert.equal(root.root.findByProps({id:'model-capacity-max-waiting'}).props.value,300);
 assert.equal(button(root,'Save model-capacity settings').props.disabled,true);
 await renderAct(async()=>button(root,'Discard edits and use saved settings').props.onClick());
 assert.equal(root.root.findByProps({id:'model-capacity-max-waiting'}).props.value,400);await renderAct(async()=>root.unmount());
});

test('capacity waiting is visible without a misleading turn-duration estimate',()=>{
 const session={status:'working',activity:{phase:'capacity',label:'Waiting for model capacity. Work will continue automatically.'},
  turnDurationHint:{version:1,basis:'recent_similar_turns',scope:'turn',sampleCount:5,startedAt:1,lowerSeconds:2,upperSeconds:8}};
 assert.match(liveActivity(session).label,/continue automatically/);assert.equal(durationHint(session),null);
});

test('unlimited waiting clears both optional limits only after saving',async()=>{
 let root;const writes=[];
 const act=async(action,args)=>{if(action.endsWith('read'))return {accepted:true,result:{...initial,waitTimeoutSeconds:60}};writes.push(args);return {accepted:true,result:{...args,revision:1}}};
 await renderAct(async()=>{root=create(React.createElement(ModelCapacitySettings,{act}))});
 await renderAct(async()=>button(root,'Use unlimited waiting').props.onClick());
 assert.equal(writes.length,0);
 assert.equal(root.root.findByProps({id:'model-capacity-max-waiting'}).props.value,0);
 assert.equal(root.root.findByProps({id:'model-capacity-timeout'}).props.value,0);
 assert.equal(button(root,'Save model-capacity settings').props.disabled,false);
 await renderAct(async()=>button(root,'Save model-capacity settings').props.onClick());
 assert.deepEqual(writes,[{expectedRevision:0,maxWaitingCalls:0,waitTimeoutSeconds:0}]);
 assert.match(JSON.stringify(root.toJSON()),/no waiting-call count limit/);
 await renderAct(async()=>root.unmount());
});
