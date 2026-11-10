import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DiagnosticsSettings}=await server.ssrLoadModule('/src/diagnostics.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('provider recording defaults off, saves an explicit choice, and can be turned off independently of forwarding',async()=>{
 const calls=[];
 let state={view:{settingsSection:'maintenance',settingsExpanded:['diagnostics']},diagnostics:{config:{enabled:false,streams:[],destinations:[],retentionDays:30,maxRecords:20000}}},root;
 const dispatch=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}};return {accepted:true}};
 const render=()=>React.createElement(DiagnosticsSettings,{state,act:dispatch});
 await renderAct(async()=>{root=create(render())});
 const recording=()=>root.root.findAllByType('label').find(node=>node.children.includes('Record provider requests and responses for troubleshooting')).findByType('input');
 assert.equal(recording().props.checked,false);
 assert.match(JSON.stringify(root.toJSON()),/Earlier calls cannot be recovered/);
 await renderAct(async()=>{recording().props.onChange({target:{checked:true}});root.update(render())});
 const first=calls.find(call=>call.name==='diagnostics.configure').args.patch;
 assert.equal(first.providerRequests,true);
 assert.deepEqual(first,{providerRequests:true});
 state={...state,diagnostics:{...state.diagnostics,config:{...state.diagnostics.config,...first}}};
 await renderAct(async()=>root.update(render()));
 assert.equal(recording().props.checked,true);
 await renderAct(async()=>{recording().props.onChange({target:{checked:false}});root.update(render())});
 assert.equal(calls.filter(call=>call.name==='diagnostics.configure').at(-1).args.patch.providerRequests,false);
 await renderAct(async()=>root.unmount());
});
