import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyStateDelta} from '../src/state-transport.js';

test('patches preserve untouched React inputs and reject a missing baseline',()=>{
 const previous={revision:5,sessions:[{id:'one',status:'idle'},{id:'two',status:'idle'}],theme:{css:'huge'},view:{remove:true}};
 const next=applyStateDelta(previous,{baseRevision:5,revision:6,changes:[{path:['revision'],value:6},{path:['sessions',1,'status'],value:'working'},{path:['view','remove'],remove:true}]});
 assert.equal(next.sessions[1].status,'working');assert.equal(next.sessions[0],previous.sessions[0]);assert.equal(next.theme,previous.theme);
 assert.equal(previous.sessions[1].status,'idle');assert.deepEqual(next.view,{});
 assert.throws(()=>applyStateDelta(next,{baseRevision:5,revision:6,changes:[]}));
 const safe=applyStateDelta(previous,{baseRevision:5,revision:5,changes:[{path:['__proto__','bad'],value:true}]});
 assert.equal(Object.getPrototypeOf(safe),Object.prototype);assert.equal({}.bad,undefined);assert.equal(safe.__proto__.bad,true);
});
