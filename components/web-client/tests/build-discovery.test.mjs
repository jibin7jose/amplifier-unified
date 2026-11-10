import test from 'node:test';
import assert from 'node:assert/strict';
import {createBuildDiscovery} from '../src/app-reload.js';

const loaded={id:'aaaaaaaaaaaaaaaa',version:'0.20.146'},updated={id:'bbbbbbbbbbbbbbbb',version:'0.20.147'};
const client=(frontend=loaded,host='host-1')=>({id:'document',kind:'web',hostInstanceId:host,build:{serverVersion:frontend.version,frontend}});
function fixture({read}={}){
 let time=0,sequence=0,visible=true;const timers=new Map(),requests=[],notices=[];
 const setTimer=(callback,delay)=>{const id=++sequence;timers.set(id,{callback,at:time+delay});return id};
 const clearTimer=id=>timers.delete(id);
 const owner=createBuildDiscovery({loaded,onInstalled:value=>notices.push(value),now:()=>time,setTimer,clearTimer,visible:()=>visible,
  read:read||((signal)=>new Promise((resolve,reject)=>{requests.push({signal,resolve,reject});signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})}))});
 const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
 const advance=async ms=>{const end=time+ms;while(true){const next=[...timers].filter(([,row])=>row.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;time=next[1].at;timers.delete(next[0]);next[1].callback();await flush()}time=end;await flush()};
 return {owner,requests,notices,advance,flush,visible(value){visible=value;owner.wake()},finish:()=>owner.dispose()};
}

test('healthy initial and restarted baselines require zero periodic/focus build reads',async()=>{
 const f=fixture();try{
  f.owner.observe(client());await f.advance(600000);
  for(let i=0;i<10;i++){f.owner.wake();f.visible(false);f.visible(true)}await f.advance(600000);
  assert.equal(f.requests.length,0);assert.deepEqual(f.notices,[]);
  f.owner.observe(null);await f.advance(5000);f.owner.observe(client(loaded,'host-2'));await f.advance(600000);
  assert.equal(f.requests.length,0,'same-build restart does not poll');
  f.owner.observe(client(updated,'host-3'));assert.deepEqual(f.notices,[updated]);assert.equal(f.requests.length,0);
 }finally{f.finish()}
});

test('unvalidated startup uses one bounded failure check; focus cannot accelerate backoff',async()=>{
 const f=fixture();try{
  f.owner.observe({kind:'web',hostInstanceId:'socket-open'});
  await f.advance(9999);assert.equal(f.requests.length,0);await f.advance(1);assert.equal(f.requests.length,1);
  for(let i=0;i<8;i++)f.owner.wake();assert.equal(f.requests.length,1);
  await f.advance(5000);assert.equal(f.requests[0].signal.aborted,true);
  await f.advance(54999);assert.equal(f.requests.length,1);await f.advance(1);assert.equal(f.requests.length,2);
  f.requests[1].resolve({id:'invalid',version:'fake'});await f.flush();assert.deepEqual(f.notices,[]);
  await f.advance(120000);assert.equal(f.requests.length,3);
  f.requests[2].resolve(updated);await f.flush();assert.deepEqual(f.notices,[updated]);
  await f.advance(1000000);assert.equal(f.requests.length,3,'known update does not keep polling');
 }finally{f.finish()}
});

test('connection failure gets a grace period and hidden documents release reads',async()=>{
 const f=fixture();try{
  f.owner.observe(client());f.owner.observe(null);await f.advance(59999);assert.equal(f.requests.length,0);
  f.visible(false);await f.advance(600000);assert.equal(f.requests.length,0);
  f.visible(true);await f.advance(0);assert.equal(f.requests.length,1);
  f.visible(false);assert.equal(f.requests[0].signal.aborted,true);await f.flush();
  f.visible(true);await f.advance(59999);assert.equal(f.requests.length,1);await f.advance(1);assert.equal(f.requests.length,2);
 }finally{f.finish()}
});

test('new host baseline fences a late fallback response, including readers that ignore abort',async()=>{
 let finish;const f=fixture({read:()=>new Promise(resolve=>finish=resolve)});try{
  await f.advance(10000);f.owner.observe(client(updated,'host-2'));finish(loaded);await f.flush();
  assert.deepEqual(f.notices,[updated]);await f.advance(1000000);assert.deepEqual(f.notices,[updated]);
  f.owner.observe(client(loaded,'host-3'));assert.deepEqual(f.notices,[updated,null]);
 }finally{f.finish()}
});

test('dispose releases timers and requests without late notifications',async()=>{
 const f=fixture();await f.advance(10000);f.finish();assert.equal(f.requests[0].signal.aborted,true);
 f.requests[0].resolve(updated);await f.advance(1000000);assert.deepEqual(f.notices,[]);assert.equal(f.requests.length,1);
});

test('fallback rejects an oversized body before publishing any build identity',async()=>{
 const prior=globalThis.fetch;let reads=0;globalThis.fetch=async()=>{reads++;return new Response(JSON.stringify({...updated,extra:'x'.repeat(2000)}))};
 let now=0,callback;const notices=[];
 const owner=createBuildDiscovery({loaded,onInstalled:value=>notices.push(value),now:()=>now,
  setTimer:(fn,delay)=>{if(delay===10000)callback=fn;return delay},clearTimer:()=>{},visible:()=>true});
 try{now=10000;await callback();assert.equal(reads,1);assert.deepEqual(notices,[])}
 finally{owner.dispose();globalThis.fetch=prior}
});
