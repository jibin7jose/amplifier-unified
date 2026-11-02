import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UIUseSetup}=await server.ssrLoadModule('/src/ui-use-setup.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalFetch=globalThis.fetch,originalStorage=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage');
const storage=new Map(),roots=new Set();
Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}});
test.beforeEach(()=>{storage.clear()});
test.afterEach(async()=>{for(const root of roots)await renderAct(async()=>root.unmount());roots.clear();globalThis.fetch=originalFetch});
test.after(async()=>{await server.close();if(originalStorage)Object.defineProperty(globalThis,'sessionStorage',originalStorage);else delete globalThis.sessionStorage});
const support={hostInstanceId:'host-one',featureSetup:{supported:true,pending:false,features:[{id:'managed-browser',label:'Amplifier browser',installed:false},{id:'decisions-openai',label:'OpenAI decisions',installed:false},{id:'decisions-jev',label:'Jev decisions',installed:false}]}};
const button=(root,text)=>root.root.findAllByType('button').find(node=>node.children.join('')===text);
// The application's public action boundary handles errors unless its caller
// explicitly owns recovery. Exercise that contract rather than injecting a
// dispatch function that always throws through to the component.
function actionBoundary(dispatch){return async(name,args,meta={})=>{try{return await dispatch(name,args,meta)}catch(error){if(meta.propagateError)throw error;return undefined}}}
async function render(props){let root;await renderAct(async()=>{root=create(React.createElement(UIUseSetup,{kind:'browser',state:{updates:{}},report:support,...props,act:actionBoundary(props.act)}))});roots.add(root);return root}
async function click(root,text){const target=button(root,text);assert.ok(target,text);assert.equal(Boolean(target.props.disabled),false,text+' should be usable');await renderAct(async()=>target.props.onClick())}
const stored=kind=>JSON.parse(storage.get('amplifier.uiUseSetup.request.'+kind)||'null');

test('lost admission survives fresh status, remount and exact-ID reconciliation',async()=>{
 const calls=[];let lose=true,reads=0;
 const dispatch=async(action,args,meta)=>{calls.push({action,args:{...args},meta:{...meta}});if(lose)throw Object.assign(Error('Connection interrupted'),{unconfirmed:true,requestPath:'/api/actions'});return {accepted:true,requestId:meta.id}};
 let root=await render({act:dispatch});
 await click(root,'Install Amplifier browser');
 assert.equal(calls.length,1);assert.equal(calls[0].meta.propagateError,true);const original=stored('browser');assert.ok(original?.id);
 assert.equal(button(root,'Install Amplifier browser').props.disabled,true);
 const fresh={...support,hostInstanceId:'host-two'};
 globalThis.fetch=async path=>{reads++;assert.equal(path,'/api/ui-use/v1/state');return new Response(JSON.stringify(fresh))};
 await click(root,'Check support again');
 assert.equal(reads,1);assert.equal(calls.length,1);assert.deepEqual(stored('browser'),original);
 assert.equal(button(root,'Install Amplifier browser').props.disabled,true);
 await renderAct(async()=>root.unmount());roots.delete(root);
 root=await render({act:dispatch,report:fresh});
 lose=false;await click(root,'Check installation request');
 assert.equal(calls.length,2);assert.deepEqual(calls[1],calls[0]);
 assert.equal(calls[1].args.hostInstanceId,'host-one');
 const state={updates:{featureResults:{[original.id]:{requestId:original.id,feature:'managed-browser',phase:'installed',updatedAt:1,detail:'Installed'}}}};
 await renderAct(async()=>root.update(React.createElement(UIUseSetup,{kind:'browser',state,report:fresh,act:dispatch})));
 assert.equal(stored('browser'),null);
 await click(root,'Install Amplifier browser');
 assert.notEqual(calls[2].meta.id,original.id);assert.equal(calls[2].args.hostInstanceId,'host-two');
});

test('confirmed action refusal permits a fresh explicit attempt after a real setup refresh',async()=>{
 const calls=[];let reject=true,reads=0;
 const dispatch=async(action,args,meta)=>{calls.push({args:{...args},id:meta.id});if(reject)throw Object.assign(Error('Host changed'),{status:409,requestPath:'/api/actions'});return {accepted:true,requestId:meta.id}};
 const root=await render({act:dispatch});await click(root,'Install Amplifier browser');
 assert.equal(stored('browser'),null);assert.equal(button(root,'Install Amplifier browser').props.disabled,false);
 globalThis.fetch=async()=>{reads++;return new Response(JSON.stringify({...support,hostInstanceId:'host-two'}))};
 await click(root,'Check support again');assert.equal(reads,1);
 reject=false;await click(root,'Install Amplifier browser');
 assert.notEqual(calls[0].id,calls[1].id);assert.equal(calls[1].args.hostInstanceId,'host-two');
});

test('post-acceptance state authorization failure keeps the original installation identity',async()=>{
 const calls=[];let first=true;
 const dispatch=async(action,args,meta)=>{calls.push(meta.id);if(first){first=false;throw Object.assign(Error('State refresh denied'),{status:403,requestPath:'/api/state'})}return {accepted:true,requestId:meta.id}};
 const root=await render({act:dispatch});await click(root,'Install Amplifier browser');
 assert.equal(stored('browser').id,calls[0]);assert.equal(button(root,'Install Amplifier browser').props.disabled,true);
 await click(root,'Check installation request');assert.deepEqual(calls,[calls[0],calls[0]]);
});

test('failed initial readiness read is recoverable without any installation request',async()=>{
 let reads=0,calls=0;
 globalThis.fetch=async()=>{reads++;if(reads===1)throw Error('Network unavailable');return new Response(JSON.stringify(support))};
 const root=await render({report:undefined,act:async()=>{calls++}});
 assert.equal(reads,1);await click(root,'Check support again');assert.equal(reads,2);assert.equal(calls,0);
 assert.equal(button(root,'Install Amplifier browser').props.disabled,false);
});

test('an unresolved provider request locks selection and duplicate clicks cannot create a second request',async()=>{
 let resolve;const calls=[];
 const dispatch=(action,args,meta)=>{calls.push({args,meta});return new Promise(done=>resolve=done)};
 const root=await render({kind:'decisions',act:dispatch});
 const install=button(root,'Install provider support');let first;
 await renderAct(async()=>{first=install.props.onClick();install.props.onClick()});
 assert.equal(calls.length,1);assert.equal(root.root.findByType('select').props.disabled,true);
 await renderAct(async()=>{resolve({accepted:true,requestId:calls[0].meta.id});await first});
 assert.equal(root.root.findByType('select').props.disabled,true);
 const id=calls[0].meta.id,state={updates:{featureResults:{[id]:{feature:'decisions-openai',phase:'error',detail:'Rejected during qualification',updatedAt:1}}}};
 await renderAct(async()=>root.update(React.createElement(UIUseSetup,{kind:'decisions',state,report:support,act:dispatch})));
 assert.equal(stored('decisions'),null);assert.equal(root.root.findByType('select').props.disabled,false);
});
