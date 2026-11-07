import test from 'node:test';
import assert from 'node:assert/strict';
import {updateOverview} from '../src/update-overview.js';
const base={phase:'checked',sequence:{stage:'complete'},application:{status:'current',current:'0.60.0'},available:0,diagnostics:{activeFailures:{old:{kind:'ecosystem'}}}};
test('host current health wins over historical badge debt',()=>{
 const result=updateOverview({...base,currentStatus:{status:'current',title:'You’re up to date',detail:'Verified current graph',actionRequired:false}}, {}, [{id:'updates:failure:old',page:'updates',actionRequired:true,title:'Old failure',detail:'old'}]);
 assert.equal(result.tone,'ready');assert.equal(result.title,'You’re up to date');assert.deepEqual(result.attention,[]);
});
test('current protected source has a supported action independent of read state',()=>{
 const status={status:'protected',title:'Component source needs review',detail:'Current installation kept',actionRequired:true,action:'sources'};
 for(const read of [true,false]){
  const result=updateOverview({...base,currentStatus:status},{},[{id:'updates:current',page:'updates',actionRequired:true,read}]);
  assert.equal(result.tone,'error');assert.equal(result.current.action,'sources');assert.equal(result.attention.length,1);
 }
});
test('transient backoff is visible without a red action chore',()=>{
 const result=updateOverview({...base,currentStatus:{status:'retrying',title:'Update will retry later',detail:'Bounded retry',actionRequired:false}});
 assert.equal(result.tone,'working');assert.equal(result.failed,false);
});
