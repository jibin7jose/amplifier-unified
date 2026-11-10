import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {cursorStore,portableClientTiming,recapCommitTiming} from '../src/coordination-observations.js';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {CoordinationObservations}=await server.ssrLoadModule('/src/coordination-observations.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const storage=()=>{const rows=new Map();return {getItem:key=>rows.get(key),setItem:(key,value)=>rows.set(key,value),rows}};

test('comparison cursors survive local reload and stay independent by tab and chat',()=>{
 const first=storage(),second=storage(),a=cursorStore(first),b=cursorStore(second);
 assert.equal(a.mark('one',undefined,'first'),true);
 assert.equal(cursorStore(first).read('one'),'first');
 assert.equal(b.read('one'),undefined);assert.equal(a.read('two'),undefined);
 assert.equal(a.mark('one',undefined,'late'),false);assert.equal(a.read('one'),'first');
 assert.equal(a.mark('one','first','next'),true);
 for(let i=0;i<40;i++)a.mark('chat-'+i,undefined,'cursor-'+i);
 assert.equal(Object.keys(JSON.parse([...first.rows.values()][0])).length,32);
});

test('portable client timing drops routes, bodies, IDs and invalid numbers',()=>{
 const result=portableClientTiming([{at:2,queueMs:20,runMs:NaN,action:'secret',draft:'PRIVATE'}],
  [{at:3,elapsedMs:100,serverMs:Infinity,status:200,route:'/private',requestId:'SECRET',body:'PRIVATE'}]);
 assert.doesNotMatch(JSON.stringify(result),/SECRET|PRIVATE|\/private/);
 assert.deepEqual(result.actionQueue,[{at:2,queueMs:20}]);
 assert.deepEqual(result.request,[{at:3,elapsedMs:100,status:200}]);
 assert.equal(result.rendering.status,'unavailable');assert.deepEqual(result.rendering.samples,[]);
});

test('recap commit timing measures one exact monotonic interval and retains a bounded detached ring',()=>{
 let now=10;const timing=recapCommitTiming(()=>now);
 assert.equal(timing.snapshot().status,'unavailable');
 const pending=timing.received();now=12.5;
 assert.equal(timing.committed(pending),true);assert.equal(timing.committed(pending),false);
 assert.deepEqual(timing.snapshot().samples,[{receivedMs:10,committedMs:12.5,elapsedMs:2.5}]);
 assert.equal(recapCommitTiming(()=>now).committed(pending),false);
 const copy=timing.snapshot();copy.samples[0].elapsedMs=999;assert.equal(timing.snapshot().samples[0].elapsedMs,2.5);
 for(let i=0;i<35;i++){now=20+i;const token=timing.received();now+=.5;timing.committed(token)}
 const snapshot=timing.snapshot();assert.equal(snapshot.samples.length,30);assert.equal(snapshot.samples[0].receivedMs,25);
 assert.equal(snapshot.boundary,'recap dispatch response delivered to component → React layout effect after DOM mutations');
 assert.equal(snapshot.scope,'current recap component mount');assert.equal(snapshot.clock,'performance.now');
 assert.deepEqual(portableClientTiming([],[],snapshot).rendering,snapshot);
});

test('invalid or reversed recap clocks remain unavailable rather than becoming zero observations',()=>{
 for(const [start,end] of [[NaN,10],[10,Infinity],[10,9],[-1,0]]){
  let now=start;const timing=recapCommitTiming(()=>now),token=timing.received();now=end;
  assert.equal(timing.committed(token),false);assert.equal(timing.snapshot().status,'unavailable');
 }
});

for(const cancelled of ['hidden','session-change'])for(const fails of [false,true]){
 test(`unseen ${cancelled} ${fails?'failure':'response'} never advances a cursor`,async()=>{
  const saved=storage();globalThis.sessionStorage=saved;
  const doc=new EventTarget();doc.visibilityState='visible';globalThis.document=doc;
  const observers=[];globalThis.IntersectionObserver=class {constructor(fn){this.fn=fn;observers.push(this)}observe(){}disconnect(){}};
  let root,release;const calls=[];
  const result=cursor=>({accepted:true,result:{changes:[{id:'row',label:'Related task',status:'Waiting for reply'}],nextCursor:cursor,cursorStatus:'initial'}});
  const dispatch=(action,args)=>{calls.push({action,args});if(calls.length===1)return new Promise((resolve,reject)=>{release=()=>fails?reject(Error('STALE ERROR')):resolve(result('STALE'))});return Promise.resolve(result('CURRENT'))};
  let actualVisibility=true;
  try{
   await act(async()=>{root=create(React.createElement(CoordinationObservations,{dispatch,sessionId:'a'}),{createNodeMock:()=>({getClientRects:()=>actualVisibility?[{}]:[]})})});
   assert.equal(calls.length,0);
   await act(async()=>observers.at(-1).fn([{isIntersecting:true}]));
   if(cancelled==='hidden')await act(async()=>{actualVisibility=false;observers.at(-1).fn([{isIntersecting:false}])});
   else await act(async()=>root.update(React.createElement(CoordinationObservations,{dispatch,sessionId:'b'})));
   await act(async()=>release());
   assert.equal(cursorStore(saved).read('a'),undefined);
   assert.doesNotMatch(JSON.stringify(root.toJSON()),/STALE/);
   await act(async()=>{actualVisibility=true;observers.at(-1).fn([{isIntersecting:true}])});
   assert.equal(cursorStore(saved).read(cancelled==='hidden'?'a':'b'),'CURRENT');
   assert.ok(calls.every(row=>row.action==='coordination.recap'));
  }finally{await act(async()=>root.unmount())}
 });
}

test('related-work metadata rerenders do not replace a visible recap with an empty second read',async()=>{
 const saved=storage();globalThis.sessionStorage=saved;
 const doc=new EventTarget();doc.visibilityState='visible';globalThis.document=doc;
 let observe;globalThis.IntersectionObserver=class {constructor(fn){observe=fn}observe(){}disconnect(){}};
 let root,calls=0;
 const dispatch=async()=>{calls++;return {result:{changes:[{id:'one',label:'Shown task',status:'Message delivered'}],nextCursor:'seen',cursorStatus:'initial'}}};
 try{
  await act(async()=>{root=create(React.createElement(CoordinationObservations,{dispatch,sessionId:'a',refreshToken:0}),{createNodeMock:()=>({getClientRects:()=>[{}]})})});
  await act(async()=>observe([{isIntersecting:true}]));
  await act(async()=>root.update(React.createElement(CoordinationObservations,{dispatch,sessionId:'a',refreshToken:1})));
  assert.equal(calls,1);assert.match(JSON.stringify(root.toJSON()),/Shown task/);
 }finally{await act(async()=>root.unmount())}
});
