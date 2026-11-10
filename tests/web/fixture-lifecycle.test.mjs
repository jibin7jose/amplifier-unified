import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import test from 'node:test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

async function child(script,options) {
 const fixture=spawn(process.execPath,['--input-type=module','-e',script],{stdio:['ignore','pipe','pipe']});
 const stop=ownFixture(fixture,options);
 const controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(),5000);
 try{await once(fixture.stdout,'data',{signal:controller.signal})}
 catch(error){fixture.kill('SIGKILL');try{await stop()}catch{}throw error}
 finally{clearTimeout(timer)}
 return {fixture,stop};
}
const posix={skip:process.platform==='win32'?'Graceful SIGTERM is a POSIX fixture contract':false};

test('fixture stop waits for graceful completion and is idempotent',posix,async()=>{
 const {fixture,stop}=await child("process.on('SIGTERM',()=>setTimeout(()=>{console.log('cleanup completed');process.exit(0)},30));console.log('ready');setInterval(()=>{},1000)");
 const output=[];fixture.stdout.on('data',chunk=>output.push(String(chunk)));
 const first=stop();assert.equal(stop(),first);
 assert.deepEqual(await first,{pid:fixture.pid,code:0,signal:null});
 assert.match(output.join(''),/cleanup completed/);
 assert.equal(fixture.exitCode,0);
});

test('fixture escalation reaps the child but fails qualification',posix,async()=>{
 const {fixture,stop}=await child("process.on('SIGTERM',()=>{});console.log('ready');setInterval(()=>{},1000)",{timeoutMs:50});
 await assert.rejects(stop(),/did not finish graceful shutdown/);
 assert.equal(fixture.signalCode,'SIGKILL');
});

test('unexpected fixture exit is never reported as successful cleanup',async()=>{
 const {fixture,stop}=await child("console.log('ready');setTimeout(()=>process.exit(7),10)");
 if(fixture.exitCode===null)await once(fixture,'close');
 await assert.rejects(stop(),/shutdown failed \(7\)/);
});

test('browser cleanup failure still closes Vite and fixture and fails a passing journey',async()=>{
 const failure=new Error('browser close failed');const closed=[];
 await assert.rejects(finishFixture({close:[
  ["browser",async()=>{closed.push('browser');throw failure}],
  ["Vite",async()=>{closed.push('Vite')}],
 ],stop:async()=>{closed.push('fixture')}}),error=>error instanceof AggregateError&&error.errors[0]===failure);
 assert.deepEqual(closed,['browser','Vite','fixture']);
});

test('cleanup failure is reported without replacing an existing assertion error',async t=>{
 const originalError=new Error('original assertion');const cleanupError=new Error('server close failed');
 const reports=[];t.mock.method(console,'error',error=>reports.push(error));let stopped=0;
 const journey=async()=>{try{throw originalError}finally{await finishFixture({stop:async()=>{stopped++;throw cleanupError},originalError})}};
 await assert.rejects(journey(),error=>error===originalError);
 assert.equal(stopped,1);assert.equal(reports.length,1);
 assert.equal(reports[0].errors[0],cleanupError);
});
