import test from 'node:test';
import assert from 'node:assert/strict';
import {recoveryRequestObserver} from './configuration-recovery-observer.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';

const observedRequest=(method='POST',body={action:'configuration.operation.read',args:{sessionId:'owner'},id:'read-1'},url='http://127.0.0.1:9000/api/actions')=>({
 method:()=>method,url:()=>url,postDataJSON:()=>body,
});

test('recovery observer separates bodyless GET catalog reads from validated action POSTs',()=>{
 const actions=[],observer=recoveryRequestObserver('http://127.0.0.1:9000',actions);
 const get=observedRequest('GET',null);
 get.postDataJSON=()=>{throw Error('A catalog read must not parse any action body.')};
 assert.doesNotThrow(()=>observer.record(get,0));
 observer.record(observedRequest(),1);
 observer.assertHealthy();
 assert.deepEqual(actions,[{browser:1,action:'configuration.operation.read',args:{sessionId:'owner'},id:'read-1'}]);
 assert.deepEqual(observer.snapshot(),{catalogReads:1,postRequests:1,recordedActions:1,failureCount:0,failures:[],omittedFailures:0});
});

test('recovery observer requires the exact fixture origin and action endpoint',()=>{
 const actions=[],observer=recoveryRequestObserver('http://127.0.0.1:9000',actions);
 for(const url of ['http://127.0.0.1:9001/api/actions','http://127.0.0.1:9000/else/api/actions','http://127.0.0.1:9000/api/actions/']){
  const request=observedRequest('POST',null,url);
  request.postDataJSON=()=>{throw Error('Unrelated requests must not be parsed.')};
  observer.record(request,0);
 }
 observer.assertHealthy();assert.deepEqual(actions,[]);
 assert.equal(observer.snapshot().postRequests,0);
});

test('null or malformed POST bodies stay explicit failures rather than recorded actions',()=>{
 for(const body of [null,[],{},'text',{action:7},{action:''},{action:'configuration.operation.resume',args:null},{action:'configuration.operation.resume',id:[]} ]){
  const actions=[],observer=recoveryRequestObserver('http://127.0.0.1:9000',actions);
  assert.doesNotThrow(()=>observer.record(observedRequest('POST',body),0));
  assert.deepEqual(actions,[]);
  assert.equal(observer.snapshot().failureCount,1);
  assert.equal(observer.snapshot().failures[0].code,'invalid_action_post');
  assert.throws(()=>observer.assertHealthy(),/Recovery request observer failed/);
 }
});

test('POST JSON decoding failure is bounded and never leaks raw error or request contents',()=>{
 const observer=recoveryRequestObserver('http://127.0.0.1:9000',[]),request=observedRequest();
 request.postDataJSON=()=>{throw Error('SYNTHETIC_PRIVATE_BODY')};
 for(let i=0;i<20;i++)assert.doesNotThrow(()=>observer.record(request,0));
 const snapshot=observer.snapshot();
 assert.equal(snapshot.failureCount,20);assert.equal(snapshot.failures.length,8);assert.equal(snapshot.omittedFailures,12);
 assert.equal(snapshot.failures[0].code,'action_post_decode_failed');
 assert.doesNotMatch(JSON.stringify(snapshot),/SYNTHETIC_PRIVATE_BODY/);
 assert.throws(()=>observer.assertHealthy(),/Recovery request observer failed/);
});

test('malformed observer evidence fails the awaited body while browser and fixture cleanup still run',async()=>{
 const observer=recoveryRequestObserver('http://127.0.0.1:9000',[]),closed=[];
 let originalError,retained;
 await assert.rejects(async()=>{
  try{
   observer.record(observedRequest('POST',null),0);
   observer.assertHealthy();
  }catch(error){originalError=error;throw error}
  finally{
   try{await finishFixture({close:[['Owned browser',async()=>closed.push('browser')]],stop:async()=>closed.push('fixture'),originalError})}
   finally{retained=observer.snapshot()}
  }
 },error=>error===originalError&&/Recovery request observer failed/.test(error.message));
 assert.deepEqual(closed,['browser','fixture']);
 assert.equal(retained.failureCount,1);assert.equal(retained.failures[0].code,'invalid_action_post');
});

test('late observer failure during browser close is retained and still fails after fixture shutdown',async()=>{
 const observer=recoveryRequestObserver('http://127.0.0.1:9000',[]),closed=[];
 let retained;
 await assert.rejects(async()=>{
  try{
   await finishFixture({
    close:[['Owned browser',async()=>{observer.record(observedRequest('POST',null),0);closed.push('browser')}]],
    stop:async()=>closed.push('fixture'),
   });
  }finally{retained=observer.snapshot();observer.assertHealthy()}
 },/Recovery request observer failed/);
 assert.deepEqual(closed,['browser','fixture']);assert.equal(retained.failureCount,1);
});
