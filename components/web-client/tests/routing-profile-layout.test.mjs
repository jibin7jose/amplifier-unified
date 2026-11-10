import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({cacheDir:'.vite-controls-proof',configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {RoutingSettings}=await server.ssrLoadModule('/src/routing-settings.jsx');
const {SettingsLayoutContext}=await server.ssrLoadModule('/src/settings-layout.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('desktop profile fields remain reachable while compact disclosure and unsaved edits survive resizing',async()=>{
 const state={view:{routingEditor:{name:'unsaved profile',profileKey:'fixture',scope:'global',matrix:{name:'fixture',roles:{general:{candidates:[]}}}}},setup:{providers:[],matrices:[]}};
 const calls=[];
 const dispatch=(name,args)=>{calls.push([name,args]);return Promise.resolve({accepted:true})};
 const render=compact=>React.createElement(SettingsLayoutContext.Provider,{value:{compact,active:true,footer:null}},React.createElement(RoutingSettings,{state,act:dispatch}));
 let root;
 try{
  await act(async()=>{root=create(render(false))});
  const details=()=>root.root.findByProps({className:'a-routing-profile-options'});
  const input=()=>root.root.findByProps({id:'routing-name'});
  assert.equal(details().props.open,true,'Desktop hides the summary, so its fields must be open');
  await act(async()=>input().props.onChange({target:{value:'private unsaved name'}}));
  await act(async()=>root.update(render(true)));
  assert.equal(details().props.open,false,'Compact mode keeps the initial disclosure closed');
  assert.equal(input().props.value,'private unsaved name');
  await act(async()=>details().findByType('summary').props.onClick({preventDefault(){}}));
  assert.equal(details().props.open,true);
  await act(async()=>details().findByType('summary').props.onClick({preventDefault(){}}));
  assert.equal(details().props.open,false);
  await act(async()=>root.update(render(false)));
  assert.equal(details().props.open,true,'Returning to desktop must expose fields after compact close');
  assert.equal(input().props.value,'private unsaved name');
  assert.equal(calls.some(([name])=>name==='routing.save'),false,'Resizing and disclosure never save the draft');
 }finally{if(root)await act(async()=>root.unmount())}
});
