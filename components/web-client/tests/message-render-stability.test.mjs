import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

// Count actual remark processor visits, without a production instrumentation API.
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',
 optimizeDeps:{noDiscovery:true,include:[]},plugins:[{name:'count-message-parses',enforce:'pre',transform(code,id){
  if(!id.endsWith('/src/markdown.js'))return;
  const needle='remarkPlugins:[remarkGfm,';
  assert.equal(code.split(needle).length,2);
  return code.replace(needle,'remarkPlugins:[()=>()=>{globalThis.messageParseVisits.push(original)},remarkGfm,');
 }}]});
const {MessageEntry}=await server.ssrLoadModule('/src/message-actions.jsx');
const {InlineMedia}=await server.ssrLoadModule('/src/inline-media.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const observer=globalThis.IntersectionObserver;
globalThis.IntersectionObserver=class{observe(){}disconnect(){}};
test.after(async()=>{globalThis.IntersectionObserver=observer;delete globalThis.messageParseVisits;await server.close()});
const initial=()=>({message:{id:'answer',role:'assistant',text:'Stable **answer**'},session:{id:'chat',workspace:'/workspace'},state:{view:{}},act:()=>{},working:false});
const render=props=>React.createElement(MessageEntry,props);

test('unrelated parent updates do not reparse unchanged assistant text',async()=>{
 let root,props=initial();globalThis.messageParseVisits=[];
 try{
  await act(async()=>{root=create(render(props))});
  assert.equal(globalThis.messageParseVisits.length,1);
  for(let i=0;i<5;i++){
   props={...props,message:{...props.message},session:{...props.session},state:{view:{draft:'Draft '+i}}};
   await act(async()=>root.update(render(props)));
  }
  console.log(JSON.stringify({initialParses:1,unchangedUpdateParses:globalThis.messageParseVisits.length-1,updates:5}));
  assert.equal(globalThis.messageParseVisits.length,1);
  props={...props,state:{view:{messageCopy:{sessionId:'chat',messageId:'answer',status:'ready'}}}};
  await act(async()=>root.update(render(props)));
  assert.ok(root.root.findAllByProps({role:'status'}).some(row=>row.children.includes('Copied Markdown')));
  assert.equal(globalThis.messageParseVisits.length,1,'live copy acknowledgement does not invalidate text');
 }finally{await act(async()=>root?.unmount())}
});

test('media keeps current dispatch and revision outside the memoized text body',async()=>{
 let root,props=initial();const calls=[];
 const first=(...args)=>calls.push(['first',...args]),next=(...args)=>calls.push(['next',...args]);
 props={...props,dispatch:first};globalThis.messageParseVisits=[];
 try{
  await act(async()=>{root=create(render(props))});
  const media=()=>root.root.findByType(InlineMedia);
  assert.equal(media().props.dispatch,first);
  props={...props,dispatch:next,session:{...props.session,mediaRevision:2,linkedMediaAvailable:true}};
  await act(async()=>root.update(render(props)));
  assert.equal(media().props.dispatch,next);
  assert.equal(media().props.mediaRevision,2);
  assert.equal(media().props.linkedMediaAvailable,true);
  assert.equal(media().props.sessionId,'chat');assert.equal(media().props.messageId,'answer');
  media().props.dispatch('canvas.openGif',{id:'synthetic-media'});
  assert.deepEqual(calls,[['next','canvas.openGif',{id:'synthetic-media'}]]);
  assert.equal(globalThis.messageParseVisits.length,1,'media updates must not reparse unchanged text');
  props={...props,state:{view:{messageEdit:{sessionId:'chat',messageId:'answer',text:props.message.text}}}};
  await act(async()=>root.update(render(props)));
  assert.equal(root.root.findAllByType(InlineMedia).length,0,'editing still hides inline media');
 }finally{await act(async()=>root?.unmount())}
});

test('text, detail, role, workspace, identity and action changes remain live',async()=>{
 let root,props=initial();globalThis.messageParseVisits=[];
 try{
  await act(async()=>{root=create(render(props))});
  for(const update of [
   p=>({...p,message:{...p.message,text:'Edited **answer**'}}),
   p=>({...p,message:{...p.message,role:'user'}}),
   p=>({...p,message:{...p.message,role:'assistant',id:'another'}}),
   p=>({...p,session:{...p.session,workspace:'/other'}}),
   p=>({...p,session:{...p.session,id:'other-chat'}}),
   p=>({...p,act:()=>{}}),
  ]){
   const before=globalThis.messageParseVisits.length;props=update(props);
   await act(async()=>root.update(render(props)));
   assert.ok(globalThis.messageParseVisits.length>before,'changed body input must invalidate');
  }
  const reference={sessionId:props.session.id,part:'messages',id:props.message.id,field:'text',digest:'changed',length:100};
  props={...props,message:{...props.message,textDetail:reference}};
  await act(async()=>root.update(render(props)));
  assert.equal(root.root.find(row=>row.type?.name==='DetailText').props.reference,reference);
  assert.ok(JSON.stringify(root.toJSON()).includes('Edited'));
 }finally{await act(async()=>root?.unmount())}
});

test('edit permission and empty working placeholder update independently of memoized text',async()=>{
 let root,props=initial();props.message={...props.message,role:'user',text:''};globalThis.messageParseVisits=[];
 try{
  await act(async()=>{root=create(render(props))});
  const editButton=()=>root.root.findByProps({'aria-label':'Edit message'});
  assert.equal(editButton().props.disabled,false);
  props={...props,working:true};await act(async()=>root.update(render(props)));
  assert.equal(editButton().props.disabled,true);assert.ok(JSON.stringify(root.toJSON()).includes('…'));
  props={...props,working:false,session:{...props.session,configurationBusy:true}};
  await act(async()=>root.update(render(props)));
  assert.equal(editButton().props.disabled,true);assert.ok(!JSON.stringify(root.toJSON()).includes('…'));
 }finally{await act(async()=>root?.unmount())}
});

test('loaded detail survives unrelated updates and late replaced detail cannot win',async()=>{
 let root,props=initial(),pending=[];const fetch=globalThis.fetch;
 const response=value=>new Response(JSON.stringify({value,nextOffset:null}));
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 const ref=digest=>({sessionId:'chat',part:'messages',id:'answer',field:'text',digest,length:100});
 props.message={...props.message,role:'user',textDetail:ref('first')};globalThis.messageParseVisits=[];
 const button=()=>root.root.findAllByType('button').find(row=>row.children.includes('Show full text'));
 const contains=text=>JSON.stringify(root.toJSON()).includes(text);
 try{
  await act(async()=>{root=create(render(props))});
  let load;await act(async()=>{load=button().props.onClick()});
  await act(async()=>{pending.shift()(response('Complete first body'));await load});
  assert.ok(contains('Complete first body'));
  const count=globalThis.messageParseVisits.length;
  props={...props,state:{view:{draft:'Unrelated'}}};await act(async()=>root.update(render(props)));
  assert.ok(contains('Complete first body'));assert.equal(globalThis.messageParseVisits.length,count);
  props={...props,message:{...props.message,textDetail:ref('second')}};await act(async()=>root.update(render(props)));
  await act(async()=>{load=button().props.onClick()});
  props={...props,message:{...props.message,textDetail:ref('third')}};await act(async()=>root.update(render(props)));
  await act(async()=>{pending.shift()(response('Stale second body'));await load});
  assert.ok(!contains('Stale second body'));
  await act(async()=>{load=button().props.onClick()});
  await act(async()=>{pending.shift()(response('Complete third body'));await load});
  assert.ok(contains('Complete third body'));
 }finally{await act(async()=>root?.unmount());globalThis.fetch=fetch}
});
