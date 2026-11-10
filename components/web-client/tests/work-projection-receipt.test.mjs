import test from 'node:test';
import assert from 'node:assert/strict';
import {workProjectionReceipt} from '../src/work-projection-receipt.js';
const receipt={hostInstanceId:'host',sessionId:'chat',generation:3,sourceCount:4,nodeCount:18000,turnCount:43};
const session=patch=>({id:'chat',workProjection:{status:'ready',current:true,receipt,...patch}});

test('exports only bounded owner and accepted generation facts without traversing Work',()=>{
 const row=session();Object.defineProperty(row,'execution',{get(){throw Error('must not traverse Work')}});
 row.workProjection.receipt={...receipt,paths:['private'],sourceText:'private'};
 const value=workProjectionReceipt(row,'host'),parsed=JSON.parse(value);
 assert.equal(parsed.current,true);assert.deepEqual(parsed.receipt,{generation:3,sourceCount:4,nodeCount:18000,turnCount:43});
 assert.equal(value.includes('private'),false);assert.ok(new TextEncoder().encode(value).length<=1024);
});
test('retained receipt is noncurrent while pending, retrying or unavailable',()=>{
 for(const status of ['loading','retrying','unavailable']){
  const parsed=JSON.parse(workProjectionReceipt(session({status}),'host'));
  assert.equal(parsed.current,false);assert.equal(parsed.receipt.generation,3);
 }
 assert.equal(JSON.parse(workProjectionReceipt(session({current:false}),'host')).current,false);
});
test('missing or malformed and mismatched receipts cannot qualify current Work',()=>{
 for(const bad of [undefined,null,{...receipt,hostInstanceId:'old'},{...receipt,sessionId:'other'},
  {...receipt,generation:0},{...receipt,nodeCount:-1},{...receipt,turnCount:1.5},{...receipt,sourceCount:NaN}]){
  const parsed=JSON.parse(workProjectionReceipt(session({receipt:bad}),'host'));
  assert.equal(parsed.current,false);assert.equal(parsed.receipt,null);
 }
 assert.equal(workProjectionReceipt({id:'chat'},'host'),undefined);
 assert.equal(workProjectionReceipt(session(),null),undefined);
 assert.equal(workProjectionReceipt(session({status:'invented'}),'host'),undefined);
});
test('bound includes UTF-8 size and rejects excessive identity instead of truncating authority',()=>{
 const long='界'.repeat(200),row={id:long,workProjection:{status:'ready',current:true,receipt:{...receipt,hostInstanceId:long,sessionId:long}}};
 assert.equal(workProjectionReceipt(row,long),undefined);
 assert.equal(workProjectionReceipt({...session(),id:'x'.repeat(201)},'host'),undefined);
});


test('bounded Work receipts qualify only the selected window, never complete history',()=>{
 const supplied={hostInstanceId:'host',sessionId:'chat',generation:5,scope:'recent-events',windowKey:'owned',historyComplete:false,sourceCount:1,itemCount:92,scannedBytes:4194304,scannedEvents:123};
 const value=JSON.parse(workProjectionReceipt(session({receipt:supplied}),'host'));
 assert.equal(value.version,2);assert.equal(value.current,true);
 assert.equal(value.receipt.historyComplete,false);assert.equal(value.receipt.itemCount,92);
 assert.equal('nodeCount' in value.receipt,false);assert.equal('turnCount' in value.receipt,false);
 for(const patch of [{historyComplete:true},{windowKey:''},{scannedBytes:-1},{scannedEvents:undefined}]){
  const invalid=JSON.parse(workProjectionReceipt(session({receipt:{...supplied,...patch}}),'host'));
  assert.equal(invalid.current,false);assert.equal(invalid.receipt,null);
 }
});
