import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({cacheDir:'.vite-controls-proof',configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {CapacityControls}=await server.ssrLoadModule('/src/capacity.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('compact model evidence refreshes usage without resetting edits or reading deferred nodes',async()=>{
 let calls=0,root;
 const result=()=>({budget:{revision:1,enabled:true,maxTotalTokens:100,warningFraction:.8,unknownPolicy:'pause'},admission:{state:calls>1?'paused':'active',reasons:[],warnings:[]},usage:{calls:calls>1?1:0,metrics:{},providers:[],receipts:[],receiptCount:0,offset:0},observedAt:1});
 const dispatch=async name=>{assert.equal(name,'capacity.read');calls++;return {result:result()}};
 const frame=(revision,total=0)=>{const execution={detailsDeferred:true,segments:[{id:'group',detailRevision:revision}],aggregateUsage:{calls:total,totalTokens:total*10}};Object.defineProperty(execution,'nodes',{get(){throw Error('Compact consumer must not read deferred nodes')}});return {id:'selected',execution}};
 const render=session=>React.createElement(CapacityControls,{session,act:dispatch});
 try{
  await act(async()=>{root=create(render(frame('a')))});assert.equal(calls,0,'No read before the user inspects capacity');
  await act(async()=>root.root.findAllByType('button').find(row=>row.props.children==='Inspect task usage').props.onClick());
  assert.equal(calls,1);
  await act(async()=>root.root.findByProps({id:'capacity-tokens'}).props.onChange({target:{value:'42'}}));
  await act(async()=>root.update(render(frame('b',1))));
  assert.equal(calls,2);assert.match(JSON.stringify(root.toJSON()),/paused/);
  assert.equal(root.root.findByProps({id:'capacity-tokens'}).props.value,'42');
  await act(async()=>root.update(render(frame('b',1))));assert.equal(calls,2,'Equivalent reconstructed frames do not re-read capacity');
  await act(async()=>root.update(render(frame('b',2))));assert.equal(calls,3,'Off-page usage changes refresh even when visible group receipts are unchanged');
  assert.equal(root.root.findByProps({id:'capacity-tokens'}).props.value,'42');
 }finally{if(root)await act(async()=>root.unmount())}
});
