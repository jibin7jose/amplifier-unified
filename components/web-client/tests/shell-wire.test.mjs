import test from 'node:test';
import assert from 'node:assert/strict';
import {retainEqual} from '../src/shell/wire.js';
import {activityFor,attentionTone} from '../src/navigation-presentation.js';

test('equal refreshes retain identity and a changed field replaces only its branch',()=>{
 const old={items:[{id:1},{id:2}],view:{scope:'a'}};
 assert.equal(retainEqual(old,structuredClone(old)),old);
 const next=retainEqual(old,{...structuredClone(old),view:{scope:'b'}});
 assert.notEqual(next,old);assert.equal(next.items,old.items);assert.equal(next.view.scope,'b');
 assert.deepEqual(retainEqual({a:1},{b:undefined}),{b:undefined});
});
test('task blockers, decisions and real failures have distinct presentation',()=>{
 assert.equal(attentionTone(activityFor({id:'a',status:'idle',task:{status:'blocked'}},{})),'blocked');
 assert.equal(attentionTone(activityFor({approvals:[{status:'pending'}]},{})),'decision');
 assert.equal(attentionTone(activityFor({status:'error'},{})),'error');
 assert.equal(attentionTone(activityFor({status:'working',task:{status:'blocked'}},{})),null);
});
