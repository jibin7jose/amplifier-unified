import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createAppearanceRequestObserver} from './appearance-request-observer.mjs';

function request({method='POST',url='http://fixture.invalid/api/actions',body={action:'theme.import',args:{}},parse}={}){
 return {method:()=>method,url:()=>url,postDataJSON:parse||(()=>body)};
}

for(const method of ['GET','HEAD','OPTIONS'])test('bodyless '+method+' catalog request is not an action dispatch',()=>{
 const observer=createAppearanceRequestObserver();
 const catalog=request({method,parse(){throw Error('A non-POST body must not be parsed.')}});
 assert.doesNotThrow(()=>observer.observe(catalog));
 assert.equal(observer.matches(catalog,'theme.import'),false);
 assert.deepEqual(observer.actions,[]);
 assert.deepEqual(observer.evidence(),{errors:[],errorCount:0,catalogRequests:[{method,endpoint:'/api/actions'}],catalogCount:1});
 assert.doesNotThrow(()=>observer.assertHealthy());
});

test('endpoint is checked before parsing POST JSON',()=>{
 const observer=createAppearanceRequestObserver();
 observer.observe(request({url:'http://fixture.invalid/other/api/actions-extra',parse(){throw Error('Not an action endpoint.')}}));
 assert.deepEqual(observer.actions,[]);assert.doesNotThrow(()=>observer.assertHealthy());
});

test('valid POST actions count once despite response/route classification',()=>{
 const observer=createAppearanceRequestObserver();
 const first=request({body:{action:'theme.import',args:{packageBase64:'synthetic',fallbackName:'file'}}});
 assert.equal(observer.matches(first,'theme.import'),true);
 observer.observe(first);observer.observe(first);
 assert.equal(observer.inspect(first).action,'theme.import');
 observer.observe(request({url:'http://fixture.invalid/api/actions?client=synthetic',body:{action:'theme.list'}}));
 assert.deepEqual(observer.actions,[{action:'theme.import',argsKeys:['packageBase64','fallbackName']},{action:'theme.list',argsKeys:[]}]);
 assert.doesNotThrow(()=>observer.assertHealthy());
});

for(const [label,body] of [
 ['null',null],['array',[]],['string','theme.import'],['missing action',{}],
 ['empty action',{action:''}],['nonstring action',{action:42}],
 ['null args',{action:'theme.import',args:null}],['array args',{action:'theme.import',args:[]}],
])test('malformed '+label+' action POST is retained as failure, never credited',()=>{
 const observer=createAppearanceRequestObserver(),bad=request({body});
 assert.doesNotThrow(()=>observer.observe(bad));
 assert.equal(observer.matches(bad,'theme.import'),false);
 assert.deepEqual(observer.actions,[]);
 assert.equal(observer.evidence().errorCount,1,'Classification is cached, not silently retried.');
 assert.deepEqual(observer.evidence().errors[0].context,{stage:'request',method:'POST',endpoint:'/api/actions'});
 assert.throws(()=>observer.assertHealthy(),/Appearance request observer failed/);
});

test('parser exception stays inside callback and fails at awaited boundary with cleanup reachable',async()=>{
 const observer=createAppearanceRequestObserver(),page=new EventEmitter(),cleanup=[];
 page.on('request',observer.observe);
 const bad=request({parse(){throw Error('Synthetic JSON parse failure.')}});
 await assert.rejects((async()=>{
  try{
   assert.doesNotThrow(()=>page.emit('request',bad));
   await Promise.resolve(); // The journey controls when the retained error is raised.
   observer.assertHealthy();
  }finally{
   cleanup.push('browser-close','fixture-stop','report-write');
  }
 })(),/Synthetic JSON parse failure/);
 assert.deepEqual(cleanup,['browser-close','fixture-stop','report-write']);
 assert.equal(observer.evidence().errorCount,1);
 assert.equal(observer.evidence().errors[0].context.method,'POST');
});

test('projection exceptions are retained without escaping an event callback',()=>{
 const observer=createAppearanceRequestObserver();
 let reads=0;
 const body={get action(){if(++reads>2)throw Error('Synthetic projection failure.');return 'theme.import'},args:{}};
 assert.doesNotThrow(()=>observer.observe(request({body})));
 assert.deepEqual(observer.actions,[]);
 assert.equal(observer.evidence().errors[0].context.stage,'projection');
 assert.throws(()=>observer.assertHealthy(),/Synthetic projection failure/);
});

test('observer failure evidence is bounded but excess errors cannot become a pass',()=>{
 const observer=createAppearanceRequestObserver();
 for(let index=0;index<20;index++)observer.observe(request({parse(){throw Error('x'.repeat(2000))}}));
 const evidence=observer.evidence();
 assert.equal(evidence.errorCount,20);assert.equal(evidence.errors.length,16);
 assert.ok(evidence.errors.every(error=>error.message.length<=500));
 assert.throws(()=>observer.assertHealthy(),/Appearance request observer failed/);
});
