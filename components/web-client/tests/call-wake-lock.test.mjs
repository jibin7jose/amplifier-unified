import test from 'node:test';
import assert from 'node:assert/strict';
import {CallWakeLock} from '../src/call-wake-lock.js';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Sentinel extends EventTarget {
 released=false;
 async release(){this.released=true;this.dispatchEvent(new Event('release'));}
}
function fixture(request){
 const document=new EventTarget();document.visibilityState='visible';
 const requests=[],states=[];
 const lock=new CallWakeLock({document,navigator:{wakeLock:{request:type=>{requests.push(type);return request?request():Promise.resolve(new Sentinel())}}},onState:value=>states.push(value)});
 return {document,lock,requests,states};
}
test('wake lock follows call, visibility and user preference without repeated requests',async()=>{
 const f=fixture();f.lock.setActive(true);f.lock.setActive(true);await flush();
 assert.deepEqual(f.requests,['screen']);assert.equal(f.states.at(-1),'active');
 f.document.visibilityState='hidden';await f.lock.sentinel.release();
 f.document.dispatchEvent(new Event('visibilitychange'));await flush();assert.equal(f.requests.length,1);
 f.document.visibilityState='visible';f.document.dispatchEvent(new Event('visibilitychange'));await flush();
 assert.equal(f.requests.length,2);f.lock.setEnabled(false);await flush();assert.equal(f.states.at(-1),'off');
 f.document.dispatchEvent(new Event('visibilitychange'));await flush();assert.equal(f.requests.length,2);
 f.lock.setEnabled(true);await flush();assert.equal(f.requests.length,3);
 const held=f.lock.sentinel;f.lock.setActive(false);await flush();assert.ok(held.released);
 f.lock.dispose();f.document.dispatchEvent(new Event('visibilitychange'));await flush();assert.equal(f.requests.length,3);
});
test('late acquisition after hangup is released and cannot revive the call',async()=>{
 let resolve;const held=new Sentinel(),f=fixture(()=>new Promise(r=>resolve=r));
 f.lock.setActive(true);await flush();f.lock.setActive(false);resolve(held);await flush();
 assert.ok(held.released);assert.equal(f.states.at(-1),'off');assert.equal(f.lock.sentinel,null);f.lock.dispose();
});
test('a new call can acquire after an old request completes',async()=>{
 let resolve;const first=new Sentinel(),f=fixture(()=>f.requests.length===1?new Promise(r=>resolve=r):Promise.resolve(new Sentinel()));
 f.lock.setActive(true);await flush();f.lock.setActive(false);f.lock.setActive(true);resolve(first);await flush();await flush();
 assert.ok(first.released);assert.equal(f.requests.length,2);assert.equal(f.states.at(-1),'active');f.lock.dispose();
});
test('battery policy rejection is visible and does not spin',async()=>{
 const f=fixture(()=>Promise.reject(new Error('NotAllowedError')));f.lock.setActive(true);await flush();await flush();
 assert.equal(f.states.at(-1),'unavailable');assert.equal(f.requests.length,1);f.lock.dispose();
});
test('unsupported browsers still allow the call lifecycle',()=>{
 const document=new EventTarget();document.visibilityState='visible';const states=[];
 const lock=new CallWakeLock({document,navigator:{},onState:value=>states.push(value)});
 lock.setActive(true);assert.equal(states.at(-1),'unsupported');lock.setActive(false);assert.equal(states.at(-1),'off');lock.dispose();
});
