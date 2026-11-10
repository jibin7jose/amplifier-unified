import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {UpdateSettings}=await server.ssrLoadModule('/src/updates.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
test('old conversation settings are informational and link through shared selection',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const state={view:{},settings:{},updates:{lastCheck:100,items:[{id:'history',label:'Older conversation settings',kind:'history',status:'historical',detail:'History is kept.',sourceIssues:[{reference:'converge-w4',workspace:'/tmp',sessionId:'native',appSessionId:'app',reason:'Choose an available bundle.'}]}]}};
 let root;await renderAct(async()=>{root=create(React.createElement(UpdateSettings,{state,act}))});
 const text=JSON.stringify(root.toJSON());assert.match(text,/converge-w4/);assert.match(text,/Older conversation settings/);assert.doesNotMatch(text,/Needs attention/);
 await renderAct(async()=>root.root.findByProps({'data-action':'session.select'}).props.onClick());
 assert.deepEqual(calls,[{name:'session.select',args:{id:'app'}},{name:'view.update',args:{patch:{panel:null}}}]);
 await renderAct(async()=>root.unmount());
});
