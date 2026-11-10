import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ComposerOwnership}=await server.ssrLoadModule('/src/composer-ownership.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());

test('an empty host keeps its first-message composer available before a session exists',async()=>{
 let root;
 for(const session of [undefined,null]){
  await act(async()=>{root=create(React.createElement(ComposerOwnership,{session,dispatch:()=>assert.fail('No implicit takeover')},React.createElement('textarea',{'aria-label':'Message Amplifier'})))});
  assert.equal(root.root.findByType('fieldset').props.disabled,false);
  assert.equal(root.root.findByType('fieldset').props.inert,false);
  assert.equal(root.root.findAllByProps({'aria-label':'Conversation access'}).length,0);
  await act(async()=>root.unmount());
 }
});
