import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {EventEmitter,once} from 'node:events';
import {PassThrough} from 'node:stream';
import test from 'node:test';
import {startFixture,observeFixtureShutdown} from './fixture-readiness.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';

const posix={skip:process.platform==='win32'?'Graceful SIGTERM is a POSIX fixture contract':false};
const receipt="{fixture:'sample-ui',pid:process.pid,base:'http://127.0.0.1:12345'}";

// Synthetic children exercise the actual pipe, identity and shutdown boundary.
async function child(t,body,options={}) {
 const directory=await mkdtemp(join(tmpdir(),'fixture-readiness-'));
 let running;
 t.after(async()=>{
  try{await running?.stop()}
  finally{await rm(directory,{recursive:true,force:true})}
 });
 const stopped=join(directory,'stopped');
 const script=`
  import {writeFileSync} from 'node:fs';
  process.on('SIGTERM',()=>{writeFileSync(${JSON.stringify(stopped)},'stopped');process.exit(0)});
  setInterval(()=>{},1000);
  ${body}
 `;
 return {
  started:startFixture({command:process.execPath,args:['--input-type=module','-e',script],
   env:process.env,tag:'sample-ui',name:'Sample',...options}).then(result=>{running=result;return result}),
  stopped:async()=>assert.equal(await readFile(stopped,'utf8'),'stopped'),
 };
}

test('only the owned receipt read across chunks completes readiness',posix,async t=>{
 const {started,stopped}=await child(t,`
  console.log('ordinary startup log');
  console.log('null');console.log('[]');console.log('"not a receipt"');
  console.log(JSON.stringify({...${receipt},fixture:'other-ui',pid:0}));
  const ready=JSON.stringify({...${receipt},base:process.env.FIXTURE_BASE});
  process.stdout.write(ready.slice(0,20));
  setTimeout(()=>process.stdout.write(ready.slice(20)+'\\n'),20);
 `,{env:{...process.env,FIXTURE_BASE:'http://127.0.0.1:23456'}});
 const {fixture,base,stop}=await started;
 try {
  assert.equal(base,'http://127.0.0.1:23456');
  assert.equal(fixture.stdout.listenerCount('data'),0);
  assert.equal(fixture.listenerCount('exit'),0);
  // ownFixture's error observer remains after readiness removes its own.
  assert.equal(fixture.listenerCount('error'),1);
 } finally {
  const first=stop();assert.equal(stop(),first);
  assert.deepEqual(await first,{pid:fixture.pid,code:0,signal:null});
 }
 await stopped();
});

test('a matching tag still requires the exact child PID and loopback address',posix,async t=>{
 for(const [name,change] of [
  ['different PID','pid:process.pid+1'],
  ['string PID','pid:String(process.pid)'],
  ['other host',"base:'http://localhost:12345'"],
  ['remote host',"base:'http://192.0.2.1:12345'"],
  ['wrong scheme',"base:'https://127.0.0.1:12345'"],
  ['zero port',"base:'http://127.0.0.1:0'"],
  ['URL path',"base:'http://127.0.0.1:12345/unrelated'"],
 ]) await t.test(name,async t=>{
  const {started,stopped}=await child(t,`console.log(JSON.stringify({...${receipt},${change}}));`);
  await assert.rejects(started,/^Error: Invalid sample fixture readiness receipt$/);
  await stopped();
 });
});

test('an unrelated receipt does not satisfy readiness and timeout stops the child',posix,async t=>{
 const {started,stopped}=await child(t,`
  process.stderr.write('waiting for owned receipt');
  console.log(JSON.stringify({...${receipt},fixture:'other-ui'}));
 `,{timeoutMs:500});
 await assert.rejects(started,error=>{
  assert.equal(error.message,'Sample fixture did not become ready');
  assert.equal(error.fixtureDiagnostics.stderr.bytes,Buffer.byteLength('waiting for owned receipt'));
  assert.equal(error.fixtureDiagnostics.closed,true);
  assert.equal(error.fixtureDiagnostics.exitCode,0);
  return true;
 });
 await stopped();
});

test('the incomplete-line bound rejects excess output and stops the child',posix,async t=>{
 const {started,stopped}=await child(t,"process.stdout.write('x'.repeat(8001));");
 await assert.rejects(started,/Sample fixture readiness output exceeded its bound/);
 await stopped();
});

test('an incomplete line at the bound can finish before an owned receipt',posix,async t=>{
 const {started,stopped}=await child(t,`
  process.stdout.write('x'.repeat(8000));
  setTimeout(()=>console.log('\\n'+JSON.stringify(${receipt})),20);
 `);
 const {base,stop}=await started;
 try{assert.equal(base,'http://127.0.0.1:12345')}
 finally{await stop()}
 await stopped();
});

test('early exit retains bounded diagnostics and reports cleanup without replacing the error',async t=>{
 const reports=[];t.mock.method(console,'error',(...args)=>reports.push(args));
 const {started}=await child(t,`
  process.stderr.write('discarded-prefix'+'x'.repeat(8000),()=>process.exit(7));
 `);
 await assert.rejects(started,error=>{
  assert.equal(error.message,'Sample fixture exited before readiness (7)');
  assert.equal(error.fixtureDiagnostics.stderr.bytes,8016);
  assert.equal(error.fixtureDiagnostics.stderr.retainedBytes,8000);
  assert.equal(error.fixtureDiagnostics.stderr.truncated,true);
  assert.equal(error.fixtureDiagnostics.closed,true);
  assert.equal(error.fixtureDiagnostics.exitCode,7);
  return true;
 });
 const cleanup=reports.filter(row=>row[0]==='Sample startup cleanup failed:');
 assert.equal(cleanup.length,1);
 assert.match(cleanup[0][1].message,/Sample fixture shutdown failed \(7\)/);
 assert.ok(!JSON.stringify(reports).includes('discarded-prefix'));
});

test('signal cancellation before readiness remains a failure',posix,async t=>{
 const reports=[];t.mock.method(console,'error',(...args)=>reports.push(args));
 const {started}=await child(t,"process.removeAllListeners('SIGTERM');process.kill(process.pid,'SIGTERM');");
 await assert.rejects(started,/Sample fixture exited before readiness \(SIGTERM\)/);
 const cleanup=reports.filter(row=>row[0]==='Sample startup cleanup failed:');
 assert.equal(cleanup.length,1);
 assert.match(cleanup[0][1].message,/Sample fixture shutdown failed \(SIGTERM\)/);
});

test('spawn errors are propagated even when cleanup reports the same failure',async t=>{
 const reports=[];t.mock.method(console,'error',(...args)=>reports.push(args));
 const directory=await mkdtemp(join(tmpdir(),'fixture-readiness-missing-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 let original;
 await assert.rejects(startFixture({command:join(directory,'missing'),args:[],env:process.env,
  tag:'sample-ui',name:'Sample'}),error=>{original=error;return error.code==='ENOENT'});
 const cleanup=reports.filter(row=>row[0]==='Sample startup cleanup failed:');
 assert.equal(cleanup.length,1);
 assert.equal(cleanup[0][1],original);
});

const traceback='Traceback (most recent call last):\n  File "/private/SECRET/fixture.py", line 27, in close\n    token = "SECRET source"\nRuntimeError: SECRET credential \u001b[31m\n';
const structural={frames:[{file:'fixture.py',line:27,function:'close'}],omittedFrames:0,exceptionType:'RuntimeError'};

for(const when of ['after readiness','during shutdown'])test(`nonzero fixture exit ${when} retains final stderr structure`,posix,async t=>{
 t.mock.method(console,'error',()=>{});
 const script=`
  let timer=setInterval(()=>{},1000);
  const fail=()=>{clearInterval(timer);process.exitCode=1;process.stderr.end(${JSON.stringify(traceback)})};
  process.on('SIGTERM',${when==='during shutdown'?'fail':'()=>{}'});
  console.log(JSON.stringify(${receipt}));
  ${when==='after readiness'?'setImmediate(fail)':''}
 `;
 const {fixture,stop}=await startFixture({command:process.execPath,args:['--input-type=module','-e',script],
  env:process.env,tag:'sample-ui',name:'Sample'});
 let failure;
 t.after(async()=>assert.rejects(stop(),error=>error===failure));
 if(when==='after readiness'&&fixture.exitCode===null)await once(fixture,'close');
 const first=stop();assert.equal(stop(),first);
 await assert.rejects(first,error=>{
  failure=error;
  assert.match(error.message,/Sample fixture shutdown failed \(1\)/);
  assert.equal(error.fixtureDiagnostics.closed,true);
  assert.equal(error.fixtureDiagnostics.exitCode,1);
  assert.equal(error.fixtureDiagnostics.signal,null);
  assert.equal(error.fixtureDiagnostics.stderr.ended,true);
  assert.equal(error.fixtureDiagnostics.stderr.bytes,Buffer.byteLength(traceback));
  assert.deepEqual(error.fixtureDiagnostics.stderr.traceback,structural);
  assert.ok(!JSON.stringify(error.fixtureDiagnostics).includes('SECRET'));
  return true;
 });
});

test('stop diagnostics include the final pipe chunk, bound bytes and preserve rejection identity',async t=>{
 const reports=[];t.mock.method(console,'error',line=>reports.push(line));
 const fixture=new EventEmitter();fixture.stderr=new PassThrough();
 let rejectStop,calls=0;
 const original=new Error('original lifecycle refusal');
 const {stop}=observeFixtureShutdown(fixture,()=>{calls++;return new Promise((_,reject)=>{rejectStop=reject})});
 fixture.stderr.write(Buffer.alloc(20000,255));
 fixture.stderr.write('\nSECRET arbitrary payload\u0000\u001b\r\n');
 const promise=stop();assert.equal(stop(),promise);assert.equal(calls,1);
 const ended=once(fixture.stderr,'end');
 fixture.stderr.end(traceback);await ended;
 // Child close follows pipe drain; the original stop settles only afterwards.
 fixture.emit('close',1,null);rejectStop(original);
 await assert.rejects(promise,error=>error===original);
 assert.equal(stop(),promise);
 const facts=original.fixtureDiagnostics;
 assert.equal(facts.stderr.bytes,20000+Buffer.byteLength('\nSECRET arbitrary payload\u0000\u001b\r\n'+traceback));
 assert.equal(facts.stderr.retainedBytes,8000);assert.equal(facts.stderr.truncated,true);
 assert.deepEqual(facts.stderr.traceback,structural);
 assert.equal(reports.length,1);
 const encoded=reports[0].slice('FIXTURE_DIAGNOSTICS '.length);
 assert.deepEqual(JSON.parse(encoded),facts);
 assert.ok(encoded.length<2000&&!/SECRET|private|\\u001b|\\u0000|\u001b|\u0000/.test(encoded));
});

test('traceback fields are bounded and invalid structural/control lines are not published',async t=>{
 t.mock.method(console,'error',()=>{});
 const fixture=new EventEmitter();fixture.stderr=new PassThrough();
 const original=new Error('failed'),{stop}=observeFixtureShutdown(fixture,()=>Promise.reject(original));
 fixture.stderr.write('Traceback (most recent call last):\n');
 for(let i=1;i<=12;i++)fixture.stderr.write(`  File "/secret/fixture.py", line ${i}, in close\n`);
 fixture.stderr.write('  File "/SECRET/\u001b[31m.py", line 1, in close\n');
 fixture.stderr.write('  File "/SECRET/fixture.py", line 1, in bad\u0000function\n');
 const ended=once(fixture.stderr,'end');
 fixture.stderr.end('ValueError: PRIVATE arbitrary payload\n');await ended;
 fixture.emit('close',1,null);
 await assert.rejects(stop(),error=>error===original);
 const trace=original.fixtureDiagnostics.stderr.traceback;
 assert.equal(trace.frames.length,8);assert.equal(trace.omittedFrames,4);
 assert.deepEqual(trace.frames.map(row=>row.line),[5,6,7,8,9,10,11,12]);
 assert.equal(trace.exceptionType,'ValueError');
 assert.ok(!/SECRET|PRIVATE|secret|bad/.test(JSON.stringify(original.fixtureDiagnostics)));
});

test('unclosed/frozen rejection stays unknown and cannot replace the journey error',async t=>{
 const reports=[];t.mock.method(console,'error',value=>reports.push(value));
 const fixture=new EventEmitter();fixture.stderr=new PassThrough();
 const refusal=Object.freeze(new Error('forced cleanup remained uncertain')),journey=new Error('original assertion');
 let calls=0;
 const {stop}=observeFixtureShutdown(fixture,()=>{calls++;return Promise.reject(refusal)});
 const run=async()=>{try{throw journey}finally{await finishFixture({stop,originalError:journey})}};
 await assert.rejects(run(),error=>error===journey);
 await assert.rejects(stop(),error=>error===refusal);
 assert.equal(calls,1);
 const detail=JSON.parse(reports.find(value=>typeof value==='string').slice('FIXTURE_DIAGNOSTICS '.length));
 assert.equal(detail.closed,false);assert.equal(detail.exitCode,null);assert.equal(detail.signal,null);
 assert.equal(detail.stderr.ended,false);assert.equal(detail.stderr.traceback.exceptionType,null);
 assert.equal(reports.find(value=>value instanceof AggregateError).errors[0],refusal);
 fixture.stderr.end();fixture.emit('close',null,'SIGKILL');
 assert.equal(detail.closed,false); // Late information does not rewrite the saved observation.
});
