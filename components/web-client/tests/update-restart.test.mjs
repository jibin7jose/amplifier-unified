import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateRestart}=await server.ssrLoadModule('/src/update-restart.jsx');
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const target={hostInstanceId:'host-one',revision:'a'.repeat(40),generation:'b'.repeat(32),attemptId:'attempt-one'};
const updates={phase:'app-staged',forceRestartTarget:target,pendingApp:{...target,latest:'v99.0.0'},blockers:[{kind:'conversation',label:'Running chat',sessionId:'one'}]};
const button=(root,text)=>root.root.findAllByType('button').find(row=>row.children.join('')===text);

test('force entry is inside expanded active-work disclosure and absent without a staged target',()=>{
 const html=renderToStaticMarkup(React.createElement(UpdateSettings,{state:{updates,settings:{updates:{}},view:{}},act:()=>{}}));
 assert.match(html,/<details[^>]*data-part="update-blockers"[^>]*>[\s\S]*Force restart…[\s\S]*<\/details>/);
 assert.doesNotMatch(html,/role="alertdialog"/);
 assert.equal(renderToStaticMarkup(React.createElement(UpdateRestart,{updates:{},act:()=>{}})),'');
});

test('confirmation cancels without effects and submits exactly the displayed target once',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{calls.push({action,args});return{accepted:true,requestId:'request-one'}};
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 assert.equal(calls.length,0);
 const text=JSON.stringify(root.toJSON());
 assert.match(text,/will be interrupted/);assert.match(text,/will not be replayed automatically/);assert.match(text,/Saved conversation history and drafts/);assert.match(text,/checkout handoffs/);
 await act(async()=>button(root,'Cancel').props.onClick());
 assert.equal(calls.length,0);
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.deepEqual(calls,[{action:'updates.forceRestart',args:{...target,confirm:true}}]);
 assert.equal(button(root,'Force restart…').props.disabled,true);
 await act(async()=>root.unmount());
});

for(const field of ['hostInstanceId','revision','generation','attemptId'])test(`changed ${field} cannot consume old confirmation`,async()=>{
 let root;const calls=[];
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates,act:(...args)=>calls.push(args)}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...updates,forceRestartTarget:{...target,[field]:'new'}},act:(...args)=>calls.push(args)})));
 assert.equal(button(root,'Interrupt work and restart').props.disabled,true);
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.equal(calls.length,0);
 await act(async()=>root.unmount());
});

test('only matching asynchronous rejection unlocks a fresh explicit confirmation',async()=>{
 let root;const calls=[];
 const dispatch=async(...args)=>{calls.push(args);return{accepted:true,requestId:'current'}};
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...updates,forceRestartResult:{requestId:'older',status:'rejected',detail:'Old refusal'}},act:dispatch})));
 assert.equal(button(root,'Force restart…').props.disabled,true);
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...updates,forceRestartResult:{requestId:'current',status:'rejected',detail:'Review and confirm again'}},act:dispatch})));
 assert.equal(button(root,'Force restart…').props.disabled,false);
 assert.equal(calls.length,1);
 await act(async()=>button(root,'Force restart…').props.onClick());
 assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});

test('newer availability does not relabel the exact prepared restart confirmation',async()=>{
 let root;const calls=[];const dispatch=async(...args)=>{calls.push(args);return{accepted:true,requestId:'reviewed'}};
 const newer={...updates,application:{latest:'v99.0.1'}};
 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates:newer,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 assert.match(JSON.stringify(root.toJSON()),/prepared Amplifier 99\.0\.0/);
 assert.match(JSON.stringify(root.toJSON()),/99\.0\.1/);
 assert.match(JSON.stringify(root.toJSON()),/Cancel and let the newer release finish preparing/);
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.deepEqual(calls,[['updates.forceRestart',{...target,confirm:true}]]);
 await act(async()=>root.unmount());

 await act(async()=>{root=create(React.createElement(UpdateRestart,{updates:newer,act:dispatch}))});
 await act(async()=>button(root,'Force restart…').props.onClick());
 await act(async()=>root.update(React.createElement(UpdateRestart,{updates:{...newer,pendingApp:{...newer.pendingApp,latest:'v99.0.1'},forceRestartTarget:{...target,generation:'c'.repeat(32)}},act:dispatch})));
 assert.match(JSON.stringify(root.toJSON()),/prepared Amplifier 99\.0\.0/);
 assert.equal(button(root,'Interrupt work and restart').props.disabled,true);
 await act(async()=>button(root,'Interrupt work and restart').props.onClick());
 assert.equal(calls.length,1);
 await act(async()=>root.unmount());
});
