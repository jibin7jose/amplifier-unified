import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {McpContractReview}=await server.ssrLoadModule('/src/mcp-contract-review.jsx');
test.after(()=>server.close());
const changes={previousDefinitionsAvailable:true,added:[],removed:[],removedCatalog:[],changed:[],launcherChanged:[],tools:[],requestedCsp:{},requestedPermissions:{}};

for(const outcome of ['resolved','rejected'])test(`late ${outcome} contract read cannot replace a newer view's review`,async()=>{
 let finish,fail,component;
 const held=new Promise((resolve,reject)=>{finish=resolve;fail=reject});
 const render=revision=>React.createElement(McpContractReview,{canvas:{id:'view',revision},recovery:{status:'contract_changed',bindingRevision:`binding-${revision}`},command:()=>held,onApplied:async()=>{}});
 await act(async()=>{component=create(render(1))});
 const button=label=>component.root.findAllByType('button').find(node=>node.children.join('')===label);
 try{
  let pending;await act(async()=>{pending=button('Review tool changes').props.onClick()});
  await act(async()=>component.update(render(2)));
  await act(async()=>{outcome==='resolved'?finish({bindingRevision:'binding-1',fingerprint:'obsolete',changes}):fail(new Error('Old view no longer available'));await pending});
  assert.ok(button('Review tool changes'),'The current view must offer its own review');
  assert.equal(button('Approve existing actions'),undefined,'Obsolete definitions must not become the current review');
  assert.equal(component.root.findAllByProps({role:'alert'}).length,0,'An old request must not report an error on the new view');
  assert.equal(button('Review tool changes').props.disabled,false);
 }finally{await act(async()=>component.unmount())}
});

for(const destination of ['new-revision','closed'])test(`late approval receipt does not refresh a ${destination} view`,async()=>{
 let finish,component,refreshed=0;
 const held=new Promise(resolve=>{finish=resolve});
 const command=action=>action==='smartTools.reviewView'?Promise.resolve({bindingRevision:'binding-1',fingerprint:'reviewed',changes}):held;
 const render=revision=>React.createElement(McpContractReview,{canvas:{id:'view',revision},recovery:{status:'contract_changed',bindingRevision:`binding-${revision}`},command,onApplied:async()=>{refreshed++}});
 const button=label=>component.root.findAllByType('button').find(node=>node.children.join('')===label);
 await act(async()=>{component=create(render(1))});
 try{
  await act(async()=>button('Review tool changes').props.onClick());
  let pending;await act(async()=>{pending=button('Approve existing actions').props.onClick()});
  await act(async()=>destination==='closed'?component.unmount():component.update(render(2)));
  await act(async()=>{finish({status:'ready'});await pending});
  assert.equal(refreshed,0);
  if(destination!=='closed')assert.ok(button('Review tool changes'));
 }finally{if(destination!=='closed')await act(async()=>component.unmount())}
});
