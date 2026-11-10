import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useConversationDetail}=await server.ssrLoadModule('/src/conversation-detail.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
const source=id=>({id,messages:[{id:id+'-last',text:'Current'}],messageWindow:{offset:1,before:id+'-last'},execution:{nodes:[],turns:[],segments:[]},executionWindow:{offset:1,before:'g-last',part:'groups'}});
const response=value=>new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
test('unchanged message branches survive parent renders and unrelated session updates',async()=>{
 let detail,root,value=source('one');
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  const original=detail.session,rows=original.messages;
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session,original);
  value={...value,status:'working'};await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.messages,rows);assert.equal(detail.session.status,'working');
  value={...value,messageAnnotations:{'one-last':{reactions:['like']}}};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.deepEqual(detail.session.messages[0].reactions,['like']);
  value={...value,messages:[{...value.messages[0],text:'Edited!'}]};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(detail.session.messages[0].text,'Edited!');
  assert.equal(rows[0].text,'Current');
  value={...value,messageAnnotations:{}};await act(async()=>root.update(React.createElement(Probe)));
  assert.deepEqual(detail.session.messages[0].reactions,[]);
  assert.equal(detail.session.messages[0].annotationRevision,0);
 }finally{await act(async()=>root?.unmount())}
});
test('paired history failure preserves both windows and a retry commits them together',async()=>{
 let detail,root,pending=[],value=source('one');const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  assert.equal(pending.length,2);
  await act(async()=>pending[1](response({items:[{id:'g-first'}],turns:[],offset:0,before:null})));
  assert.equal(detail.session.execution.segments.length,0);
  await act(async()=>{pending[0](new Response(JSON.stringify({error:'Unavailable'}),{status:503}));await loading});
  assert.equal(detail.session.messages.length,1);assert.equal(detail.session.execution.segments.length,0);
  assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
  pending=[];await act(async()=>{loading=detail.earlier()});
  await act(async()=>{pending[0](response({items:[{id:'one-first'}],offset:0}));pending[1](response({items:[{id:'g-first'}],turns:[],offset:0,before:null}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-first','one-last']);
  assert.equal(detail.session.execution.segments.length,1);assert.equal(detail.session.executionWindow.offset,0);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});
test('late history cannot attach to a chat after leaving and returning',async()=>{
 let detail,root,pending=[],value=source('one');const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  value=source('two');await act(async()=>root.update(React.createElement(Probe)));
  value=source('one');await act(async()=>root.update(React.createElement(Probe)));
  await act(async()=>{pending[0](response({items:[{id:'stale'}],offset:0}));pending[1](response({items:[],turns:[],offset:0}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-last']);assert.equal(detail.busy,'');
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('reading-window restoration does not issue a jump or override newer navigation intent',async()=>{
 let detail,root,pending=[],value=source('one'),current=true;const original=globalThis.fetch;
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.around('older',{jump:false,isCurrent:()=>current})});
  await act(async()=>{pending.shift()(response({messages:[{id:'older'}],execution:{nodes:[],turns:[]}}));await loading});
  assert.equal(detail.focused,true);assert.equal(detail.jumpRequest,null);
  await act(async()=>detail.latest(false));
  await act(async()=>{loading=detail.around('stale',{jump:false,isCurrent:()=>current})});
  current=false;
  await act(async()=>{pending.shift()(response({messages:[{id:'stale'}],execution:{nodes:[],turns:[]}}));await loading});
  assert.equal(detail.focused,false);assert.equal(detail.busy,'');assert.equal(detail.jumpRequest,null);
  assert.deepEqual(detail.session.messages.map(row=>row.id),['one-last']);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('unknown history totals page only on demand and consume opaque cursors without canonical expansion',async()=>{
 let detail,root,pending=[],calls=[],commands=[];
 const original=globalThis.fetch;
 const value={...source('window'),sharedHistoryOffset:999999,
  messageWindow:{mode:'cursor',hasEarlier:true,before:'opaque-first',offset:null,total:null},
  executionWindow:{mode:'cursor',hasEarlier:false,before:null,part:'groups'}};
 globalThis.fetch=url=>{calls.push(new URL(url,'http://test'));return new Promise(resolve=>pending.push(resolve))};
 function Probe(){detail=useConversationDetail(value,null,(...args)=>commands.push(args));return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  assert.equal(calls.length,0,'mounting must not request old pages');
  let loading;await act(async()=>{loading=detail.earlier()});
  assert.equal(calls.length,1);assert.equal(calls[0].searchParams.get('before'),'opaque-first');
  await act(async()=>{pending.shift()(response({mode:'cursor',items:[{id:'prior'}],hasEarlier:true,before:'opaque-second',offset:null,total:null}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['prior','window-last']);
  assert.equal(detail.session.messageWindow.total,null);
  assert.equal(calls.length,1,'settling a page must not fetch another one');
  await act(async()=>{loading=detail.earlier()});
  assert.equal(calls[1].searchParams.get('before'),'opaque-second');
  // A bounded range can contain no visible messages; its continuation still advances.
  await act(async()=>{pending.shift()(response({mode:'cursor',items:[],hasEarlier:false,before:null,offset:null,total:null}));await loading});
  assert.equal(detail.session.messageWindow.hasEarlier,false);
  assert.equal(root.root.findAllByType('button').filter(row=>row.children.join('').includes('Load earlier')).length,0);
  assert.deepEqual(commands,[],'cursor reads must not expand the authoritative session history');
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('stale cursor failure preserves readable messages and the last accepted cursor',async()=>{
 let detail,root,pending=[];const original=globalThis.fetch;
 const value={...source('window'),messageWindow:{mode:'cursor',hasEarlier:true,before:'first',offset:null,total:null},executionWindow:null};
 globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});
  let loading;await act(async()=>{loading=detail.earlier()});
  await act(async()=>{pending.shift()(new Response(JSON.stringify({error:'History changed. Return to latest and try again.'}),{status:409}));await loading});
  assert.deepEqual(detail.session.messages.map(row=>row.id),['window-last']);
  assert.equal(detail.session.messageWindow.before,'first');
  assert.match(JSON.stringify(root.toJSON()),/History changed/);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});


test('earlier pages remain a snapshot while new publications and live status continue',async()=>{
 let detail,root,value={...source('window'),status:'idle',messageWindow:{mode:'cursor',hasEarlier:true,before:'first',revision:'old'},executionWindow:null};
 const original=globalThis.fetch;globalThis.fetch=async()=>response({mode:'cursor',items:[{id:'prior'}],hasEarlier:false,before:null,revision:'old'});
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});await act(async()=>detail.earlier());
  value={...value,status:'working',messages:[{id:'changed',text:'New epoch'}],messageWindow:{mode:'cursor',hasEarlier:false,before:null,revision:'new'}};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.deepEqual(detail.session.messages.map(row=>row.id),['prior','window-last']);
  assert.equal(detail.session.status,'working');
  await act(async()=>detail.latest());assert.deepEqual(detail.session.messages.map(row=>row.id),['changed']);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('runtime history is explicit, separately paged, and never merged with saved web messages',async()=>{
 let detail,root,calls=[];const original=globalThis.fetch;
 const value={...source('window'),nativeContinuation:{status:'available'},executionWindow:null};
 globalThis.fetch=async url=>{calls.push(new URL(url,'http://test'));return response({mode:'cursor',items:[{id:calls.length===1?'native-latest':'native-prior',displayOnly:true}],hasEarlier:calls.length===1,before:calls.length===1?'native-cursor':null})};
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});assert.equal(calls.length,0);
  await act(async()=>detail.openNativeHistory());assert.deepEqual(detail.session.messages.map(row=>row.id),['native-latest']);
  await act(async()=>detail.earlier());assert.deepEqual(detail.session.messages.map(row=>row.id),['native-prior','native-latest']);
  assert.ok(calls.every(url=>url.searchParams.get('native')==='true'));
  assert.equal(calls[1].searchParams.get('before'),'native-cursor');
  await act(async()=>detail.latest());assert.deepEqual(detail.session.messages.map(row=>row.id),['window-last']);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});

test('malformed or nonadvancing pages cannot replace a readable window',async()=>{
 let detail,root;const original=globalThis.fetch;
 const value={...source('window'),messageWindow:{mode:'cursor',hasEarlier:true,before:'same'},executionWindow:null};
 globalThis.fetch=async()=>response({mode:'cursor',items:[{id:'should-not-appear'}],hasEarlier:true,before:'same'});
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});await act(async()=>detail.earlier());
  assert.deepEqual(detail.session.messages.map(row=>row.id),['window-last']);assert.match(JSON.stringify(root.toJSON()),/did not advance/);
 }finally{await act(async()=>root?.unmount());globalThis.fetch=original}
});


test('booting without a selected session does not dereference an absent history snapshot',async()=>{
 let detail,root,value;
 function Probe(){detail=useConversationDetail(value);return detail.controls}
 try{
  await act(async()=>{root=create(React.createElement(Probe))});assert.equal(detail.session,undefined);
  value=null;await act(async()=>root.update(React.createElement(Probe)));assert.equal(detail.session,null);
  value=source('loaded');await act(async()=>root.update(React.createElement(Probe)));assert.equal(detail.session.id,'loaded');
 }finally{await act(async()=>root?.unmount())}
});
