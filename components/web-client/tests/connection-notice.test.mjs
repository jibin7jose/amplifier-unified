import test from 'node:test';
import assert from 'node:assert/strict';
import {transportFailure,retainUnconfirmed,afterReconnect,connectionNotice,RECONNECT_GRACE_MS} from '../src/connection-notice.js';
test('only known activation is described as an update restart',()=>{
 assert.match(connectionNotice({connected:false,updates:{pendingRestart:{}}}).title,/Updating/);
 assert.match(connectionNotice({connected:false,updates:{phase:'activating',pendingReplacement:{}}}).title,/Updating/);
 for(const updates of [{},{phase:'checking'},{pendingApp:{}},{pendingRestart:{},error:'Restart failed'}])assert.equal(connectionNotice({connected:false,updates}).title,'Reconnecting to Amplifier…');
 assert.equal(connectionNotice({connected:true}),null);
});
test('prolonged disconnection offers recovery instead of indefinite update reassurance',()=>{
 const notice=connectionNotice({connected:false,updates:{pendingRestart:{}},delayed:true});
 assert.equal(notice.retry,true);assert.match(notice.detail,/Check the server status/);assert.equal(notice.error,true);assert.equal(RECONNECT_GRACE_MS,60000);
});
test('reconnection clears transient reads but preserves unconfirmed mutations',()=>{
 const read={code:'transport_unavailable',unconfirmed:false},write={...read,unconfirmed:true};
 assert.ok(transportFailure(read));assert.equal(transportFailure(new TypeError('Failed to fetch')),false);
 assert.equal(afterReconnect(read),null);assert.equal(afterReconnect(write),write);
 assert.equal(retainUnconfirmed(write,read),write);
 assert.equal(connectionNotice({connected:true,failure:write}).dismiss,true);
 assert.match(connectionNotice({connected:true,failure:write}).detail,/nothing was automatically repeated/);
});
