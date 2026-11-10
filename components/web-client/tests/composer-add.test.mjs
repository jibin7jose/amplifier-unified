import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ComposerAdd}=await server.ssrLoadModule('/src/composer-add.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('Add only opens a menu; file and image choices reuse the caller and access never creates a chat',async()=>{
 const files=[],access=[];let root;
 const render=(sessionId=null)=>React.createElement(ComposerAdd,{sessionId,onFiles:kind=>files.push(kind),onAccess:()=>access.push(true)});
 const button=name=>root.root.findByProps({'aria-label':name});
 try{
  await act(async()=>root=create(render()));
  await act(async()=>button('Add attachments').props.onClick());
  assert.deepEqual(files,[]);assert.deepEqual(access,[]);
  assert.equal(button('Chat access').props.disabled,true);
  await act(async()=>button('Add an image').props.onClick());
  assert.deepEqual(files,['image']);
  await act(async()=>button('Add attachments').props.onClick());
  await act(async()=>button('Attach files').props.onClick());
  assert.deepEqual(files,['image','files']);
  await act(async()=>root.update(render('chat')));
  await act(async()=>button('Add attachments').props.onClick());
  assert.equal(button('Chat access').props.disabled,false);
  await act(async()=>button('Chat access').props.onClick());
  assert.deepEqual(access,[true]);
 }finally{await act(async()=>root?.unmount())}
});
test('another interaction surface dismisses Add and returning does not reopen it',async()=>{
 let root;
 const render=panel=>React.createElement(ComposerAdd,{sessionId:'chat',panel,onFiles:()=>{throw Error('No chooser during navigation')},onAccess:()=>{throw Error('No access change during navigation')}});
 const trigger=()=>root.root.findByProps({'aria-label':'Add attachments'});
 try{
  await act(async()=>root=create(render(null)));
  await act(async()=>trigger().props.onClick());
  assert.equal(trigger().props['aria-expanded'],true);
  await act(async()=>root.update(render('settings')));
  assert.equal(trigger().props['aria-expanded'],false);
  assert.equal(root.root.findAllByProps({role:'dialog'}).length,0);
  assert.equal(trigger().props.disabled,true);
  await act(async()=>trigger().props.onClick());
  assert.equal(trigger().props['aria-expanded'],false);
  await act(async()=>root.update(render(null)));
  assert.equal(trigger().props['aria-expanded'],false);
  await act(async()=>trigger().props.onClick());
  await act(async()=>root.update(render('settings-canvas')));
  assert.equal(trigger().props['aria-expanded'],false);
  // Non-modal Settings does not permanently disable the adjacent composer.
  assert.equal(trigger().props.disabled,false);
  await act(async()=>trigger().props.onClick());
  assert.equal(trigger().props['aria-expanded'],true);
  await act(async()=>root.update(render(null)));
  assert.equal(trigger().props['aria-expanded'],false);
 }finally{await act(async()=>root?.unmount())}
});