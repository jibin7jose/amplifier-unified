import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatDelete}=await server.ssrLoadModule('/src/chat-delete.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const preview=id=>({accepted:true,result:{id,title:'Managed notes',confirmationToken:'review-'+id,summary:{conversationCount:1,fileCount:2,attachmentCount:1,artifactCount:0,shareCount:1},description:'Saved history and files will be permanently deleted.',preserved:['Independent copies']}});
test('delete requires reviewed scope, cancel never deletes, and duplicate clicks submit once',async()=>{
 const calls=[];let root,finish,cancelled=0;
 const act=async(name,args)=>{calls.push({name,args});return name==='session.deletePreview'?preview(args.id):new Promise(resolve=>finish=resolve)};
 const props={id:'one',act,cancel:()=>cancelled++};
 await renderAct(async()=>{root=create(React.createElement(ChatDelete,props))});
 assert.deepEqual(calls,[{name:'session.deletePreview',args:{id:'one'}}]);
 assert.match(JSON.stringify(root.toJSON()),/cannot be undone/);
 await renderAct(async()=>root.root.findByProps({'data-action':'view.update'}).props.onClick());
 assert.equal(cancelled,1);assert.equal(calls.length,1);
 const button=root.root.findByProps({'data-action':'session.delete'});
 await renderAct(async()=>{button.props.onClick();button.props.onClick()});
 assert.equal(calls.length,2);assert.deepEqual(calls[1],{name:'session.delete',args:{id:'one',confirmationToken:'review-one'}});
 assert.equal(root.root.findByProps({'data-action':'session.delete'}).props.disabled,true);
 await renderAct(async()=>finish({accepted:true}));assert.equal(cancelled,2);
 await renderAct(async()=>root.unmount());
});
test('failed deletion keeps the panel open and requires a fresh review',async()=>{
 let closed=false,root;const calls=[];
 const act=async(name,args)=>{calls.push(name);if(name==='session.deletePreview')return preview(args.id);throw Error('Chat is now working. Stop it first.')};
 await renderAct(async()=>{root=create(React.createElement(ChatDelete,{id:'one',act,cancel:()=>closed=true}))});
 await renderAct(async()=>root.root.findByProps({'data-action':'session.delete'}).props.onClick());
 assert.equal(closed,false);assert.match(JSON.stringify(root.toJSON()),/Chat is now working/);
 assert.equal(root.root.findAllByProps({'data-action':'session.delete'}).length,0);
 await renderAct(async()=>root.root.findByProps({'data-action':'session.deletePreview'}).props.onClick());
 assert.deepEqual(calls,['session.deletePreview','session.delete','session.deletePreview']);
 await renderAct(async()=>root.unmount());
});
test('late review for another chat cannot authorize the current chat',async()=>{
 let root,complete;const calls=[];
 const act=async(name,args)=>{calls.push({name,args});return args.id==='one'?new Promise(resolve=>complete=resolve):preview(args.id)};
 const render=id=>React.createElement(ChatDelete,{id,act,cancel:()=>{}});
 await renderAct(async()=>{root=create(render('one'))});
 await renderAct(async()=>root.update(render('two')));
 await renderAct(async()=>complete(preview('one')));
 await renderAct(async()=>root.root.findByProps({'data-action':'session.delete'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'session.delete',args:{id:'two',confirmationToken:'review-two'}});
 await renderAct(async()=>root.unmount());
});
test('workspace or active-work refusal never offers a delete button',async()=>{
 let root;await renderAct(async()=>{root=create(React.createElement(ChatDelete,{id:'workspace',act:async()=>{throw Error('Archive this workspace chat instead.')},cancel:()=>{}}))});
 assert.match(JSON.stringify(root.toJSON()),/Archive this workspace chat instead/);
 assert.equal(root.root.findAllByProps({'data-action':'session.delete'}).length,0);
 await renderAct(async()=>root.unmount());
});
test('review distinguishes same titles by authoritative workspace, activity and scoped identity',async()=>{
 let root;const calls=[];
 const act=async(name,args)=>{calls.push({name,args});return {...preview(args.nativeId),result:{...preview(args.nativeId).result,reviewIdentity:{scope:'session',workspace:'/projects/one',sessionId:args.nativeId,lastActivityAt:1791979200}}}};
 try{
  await renderAct(async()=>{root=create(React.createElement(ChatDelete,{id:'history-one',historySelection:{project:'projects-one',nativeId:'one'},act,cancel:()=>{}}))});
  const rendered=JSON.stringify(root.toJSON());assert.match(rendered,/\/projects\/one/);assert.match(rendered,/Session ID/);
  assert.equal(root.root.findByType('code').children.join(''),'one');assert.equal(root.root.findByType('time').props.dateTime,'2026-10-14T12:00:00.000Z');
  assert.deepEqual(calls,[{name:'storage.historyDeletePreview',args:{project:'projects-one',nativeId:'one'}}]);
 }finally{if(root)await renderAct(async()=>root.unmount())}
});
