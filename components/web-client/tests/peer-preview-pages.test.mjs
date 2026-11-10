import test from 'node:test';
import assert from 'node:assert/strict';
import {prependPeerPage,refreshPeerPages} from '../src/peer-preview-pages.js';
const ids=s=>s.messages.map(row=>row.id);
function fixture(count=80,revision='v1'){
 const calls=[],rows=Array.from({length:count},(_,i)=>({id:String(i+1),text:`row ${i+1}`}));
 return {calls,rows,read:async before=>{
  calls.push(before??null);const end=before===undefined?count:Number(before)-1,start=Math.max(0,end-20);
  return {source:{available:true},capturedAt:count,page:{revision,items:rows.slice(start,end),before:start?String(start+1):null,hasEarlier:start>0}};
 }};
}
test('ordinary paging and Refresh then earlier retain exact full order and oldest boundary',async()=>{
 const f=fixture();let state=await refreshPeerPages(null,f.read);
 for(let i=0;i<2;i++)state=prependPeerPage(state,await f.read(state.page.before));
 const retained=state.messages;
 state=await refreshPeerPages(state,f.read);
 assert.equal(state.messages,retained);assert.equal(state.page.before,'21');
 state=prependPeerPage(state,await f.read(state.page.before));
 assert.deepEqual(ids(state),f.rows.map(row=>row.id));
 assert.deepEqual(f.calls,[null,'61','41',null,'21']);
});
test('arrival refresh reanchors from new revision, preserves IDs and can page afterward',async()=>{
 const f=fixture();let state=await refreshPeerPages(null,f.read);
 state=prependPeerPage(state,await f.read(state.page.before));
 const next=fixture(81,'v2');state=await refreshPeerPages(state,next.read);
 assert.deepEqual(ids(state),next.rows.slice(21).map(row=>row.id));
 assert.deepEqual(next.calls,[null,'62','42']);
 state=prependPeerPage(state,await next.read(state.page.before));
 assert.deepEqual(ids(state),next.rows.slice(1).map(row=>row.id));
 assert.equal(state.page.revision,'v2');
});
test('changed cursor, overlapping pages and bounded reconnect failure never rewrite retained state',async()=>{
 const f=fixture();const state=await refreshPeerPages(null,f.read),before=JSON.stringify(state);
 assert.throws(()=>prependPeerPage(state,{...state,page:{...state.page,revision:'v2'}}),/Source changed/);
 assert.throws(()=>prependPeerPage(state,state),/overlaps/);
 const distant=fixture(200,'v2');await assert.rejects(refreshPeerPages(state,distant.read),/five bounded pages/);
 assert.equal(distant.calls.length,5);assert.equal(JSON.stringify(state),before);
});