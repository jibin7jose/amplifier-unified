import test from 'node:test';
import assert from 'node:assert/strict';
import {readDetail} from '../src/detail-read.js';

test('saved field paging preserves text and Unicode offsets supplied by the host',async()=>{
 const offsets=[];
 const value=await readDetail({part:'field'},async url=>{
  const offset=Number(new URL(url,'http://fixture').searchParams.get('offset'));offsets.push(offset);
  return offset?{value:'done',nextOffset:null,total:5}:{value:'🚀',nextOffset:1,total:5};
 });
 assert.equal(value,'🚀done');assert.deepEqual(offsets,[0,1]);
});
test('non-progressing or malformed pages fail instead of leaving an infinite loader',async()=>{
 for(const page of [{value:'x',nextOffset:0},{value:'x',nextOffset:-1},{value:'x'},{nextOffset:null},{value:'',nextOffset:1}]){
  let calls=0;await assert.rejects(readDetail({},async()=>{calls++;return page}),/could not finish/);assert.equal(calls,1);
 }
 let calls=0;
 await assert.rejects(readDetail({},async()=>++calls===1?{value:'a',nextOffset:1,total:3}:{value:'bc',nextOffset:null,total:4}),/changed while loading/);
});
test('timeout and cancellation release the reader and abort pending work',async()=>{
 let seen;
 await assert.rejects(readDetail({},(_,options)=>{seen=options.signal;return new Promise(()=>{})},undefined,{timeoutMs:10}),/took too long/);
 assert.equal(seen.aborted,true);
 const controller=new AbortController();
 const pending=readDetail({},(_,options)=>{seen=options.signal;return new Promise(()=>{})},controller.signal);
 controller.abort();await assert.rejects(pending,{name:'AbortError'});assert.equal(seen.aborted,true);
 let calls=0;await assert.rejects(readDetail({},()=>{calls++},controller.signal),{name:'AbortError'});assert.equal(calls,0);
});

test('declared partial terminal response never becomes a completed copy source',async()=>{
 await assert.rejects(readDetail({},async()=>({value:'short',nextOffset:null,total:1000})),/incomplete/);
 await assert.rejects(readDetail({},async()=>({value:'a',nextOffset:2,total:4})),/incomplete/);
});
test('abort-aware transports still report timeout as timeout',async()=>{
 await assert.rejects(readDetail({},(_,options)=>new Promise((resolve,reject)=>{
  options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted request','AbortError')));
 }),undefined,{timeoutMs:10}),/took too long/);
});
