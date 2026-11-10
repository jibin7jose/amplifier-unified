import test from 'node:test';
import assert from 'node:assert/strict';
import {ownershipState} from '../src/ownership.js';
import {sessionStatus} from '../src/session-status.js';

test('yielded views stay read-only and need deliberate resumption',()=>{
  const session={status:'idle',ownership:{status:'yielded',source:'Amplifier CLI'}};
  assert.equal(ownershipState(session).blocked,true);
  assert.equal(ownershipState(session).canTakeover,true);
  assert.match(sessionStatus(session).label,/Amplifier CLI/);
  assert.equal(sessionStatus(session).busy,false);
});
test('pending or failed shutdown never offers an overlapping takeover',()=>{
  for(const status of ['yielding','yield-failed','taking-over']){
    assert.equal(ownershipState({ownership:{status}}).blocked,true);
    assert.equal(ownershipState({ownership:{status}}).canTakeover,false);
  }
});
test('ordinary idle parking stays available',()=>{
  assert.equal(ownershipState({status:'idle'}).blocked,false);
});

test('a failed takeover remains blocked and offers an explicit retry',()=>{
  const state=ownershipState({ownership:{status:'blocked',reason:'takeover-failed',detail:'Owner unavailable'}});
  assert.equal(state.blocked,true);
  assert.equal(state.canTakeover,true);
  assert.equal(state.retry,true);
  assert.equal(state.label,'Could not continue here');
  assert.equal(state.detail,'Owner unavailable');
});

test('ownership rejections do not also create a global error banner',async()=>{
  const {actionErrorMessage}=await import('../src/ownership.js');
  const {request}=await import('../src/api.js');
  const previous=globalThis.fetch;
  const state={revision:12,sessions:[]};
  globalThis.fetch=async()=>({ok:false,status:409,text:async()=>JSON.stringify({accepted:false,error:'In use',code:'session_busy',state})});
  try{
    await assert.rejects(()=>request('/api/actions'),error=>{
      assert.equal(actionErrorMessage(error),'');
      assert.deepEqual(error.state,state);
      return true;
    });
    assert.equal(actionErrorMessage(new Error('Provider failed')),'Provider failed');
  }finally{globalThis.fetch=previous}
});
test('older owners give useful guidance without pretending they can yield',()=>{
  const state=ownershipState({ownership:{status:'blocked',source:'Amplifier CLI',supportsTakeover:false}});
  assert.equal(state.canTakeover,true);
  assert.match(state.detail,/does not support takeover/);
});

test('saved worker history explains its read-only status without a stale owner or takeover',()=>{
 const reason='Worker sessions are read-only; continuing them as root chats would lose their worker configuration.';
 for(const ownership of [{status:'blocked',source:'Amplifier CLI'},{status:'yielded',source:'Amplifier CLI'}]){
  const session={sessionKind:'worker',historyReadOnlyReason:reason,ownership};
  const state=ownershipState(session);
  assert.equal(state.blocked,true);
  assert.equal(state.canTakeover,false);
  assert.equal(state.label,'Worker history');
  assert.equal(state.detail,reason);
  assert.equal(sessionStatus(session).label,'Worker history');
 }
});
