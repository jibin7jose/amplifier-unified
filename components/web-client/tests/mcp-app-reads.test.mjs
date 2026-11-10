import test from 'node:test';
import assert from 'node:assert/strict';
import {createMcpReadGate} from '../src/mcp-app-reads.js';
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('duplicate slow reads share one in-flight result; later reads get fresh results',async()=>{
 const gate=createMcpReadGate(),slow=deferred();let calls=0;
 const run=()=>{calls++;return slow.promise};
 const a=gate.run({name:'read',arguments:{b:2,a:1}},run),b=gate.run({name:'read',arguments:{a:1,b:2}},run);
 assert.equal(a,b);await tick();assert.equal(calls,1);
 slow.resolve('first');assert.deepEqual(await Promise.all([a,b]),['first','first']);
 assert.equal(await gate.run({name:'read',arguments:{a:1,b:2}},()=>++calls),2);
});
test('background reads have bounded concurrency and queue without replaying rejected work',async()=>{
 const gate=createMcpReadGate({limit:2,maxQueued:1}),slow=deferred();const started=[];
 const run=i=>gate.run({name:'read',arguments:{i}},async()=>{started.push(i);await slow.promise;return i});
 const first=run(1),second=run(2),queued=run(3);await tick();assert.deepEqual(started,[1,2]);
 await assert.rejects(run(4),/Too many/);slow.resolve();
 assert.deepEqual(await Promise.all([first,second,queued]),[1,2,3]);assert.deepEqual(started,[1,2,3]);
});
test('hiding or closing a view rejects queued reads without cancelling accepted work',async()=>{
 let visible=true;const gate=createMcpReadGate({limit:1,isVisible:()=>visible}),slow=deferred();let calls=0;
 const first=gate.run({name:'read'},()=>slow.promise);
 const queued=gate.run({name:'read',arguments:{next:true}},()=>calls++);
 const rejected=assert.rejects(queued,/paused/);visible=false;slow.resolve('accepted');
 assert.equal(await first,'accepted');await rejected;assert.equal(calls,0);
 await assert.rejects(gate.run({name:'read'},()=>calls++),/paused/);
 visible=true;assert.equal(await gate.run({name:'read'},()=>++calls),1);
 gate.close();await assert.rejects(gate.run({name:'read'},()=>calls++),/closed/);
});
