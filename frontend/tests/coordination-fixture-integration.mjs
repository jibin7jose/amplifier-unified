// Focused integration: requires the prepared Python app environment, unlike
// the frontend-only *.test.mjs suite. Run explicitly inside the owned test lane:
// AMPLIFIER_TEST_PYTHON=/path/to/python node --test frontend/tests/coordination-fixture-integration.mjs
// Real POSIX fixture processes; no provider calls. Tests consume their own
// deliberately retained failure state only after asserting that it survived.
import assert from 'node:assert/strict';
import {mkdtemp,readFile,readdir,rm,stat,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {startCoordinationFixture} from './coordination-fixture.mjs';

const posix={skip:process.platform==='win32'?'Graceful SIGTERM is a POSIX fixture contract':false};
async function setup(t) {
 const output=await mkdtemp(join(tmpdir(),'coordination-lifecycle-test-'));
 const children=[];
 t.after(async()=>{
  for(const child of children)try{await child.stop()}catch{}
  await rm(output,{recursive:true,force:true});
 });
 return {output,start:async options=>{
  const child=await startCoordinationFixture({output,...options});children.push(child);return child;
 }};
}

test('successful journey removes only its home after browser and application cleanup',posix,async t=>{
 const {output,start}=await setup(t),child=await start();
 await writeFile(join(output,'report.json'),'retained evidence');
 await writeFile(join(child.home,'owned-state'),'temporary');
 const reply=await fetch(child.base+'/fixture');assert.equal(reply.status,200);
 await child.finish({close:[['Browser',async()=>{
  assert.equal(await readFile(join(child.home,'owned-state'),'utf8'),'temporary');
  assert.equal(child.fixture.exitCode,null);
 }]]});
 assert.equal(child.fixture.exitCode,0);assert.equal(child.fixture.signalCode,null);
 await assert.rejects(stat(child.home),{code:'ENOENT'});
 assert.equal(await readFile(join(output,'report.json'),'utf8'),'retained evidence');
 await assert.rejects(fetch(child.base+'/fixture'));
});

test('assertion failure keeps exact state and error after graceful shutdown',posix,async t=>{
 const {start}=await setup(t),child=await start(),failure=new Error('original assertion');
 await writeFile(join(child.home,'candidate.txt'),'retained candidate');
 await assert.rejects(async()=>{
  try{throw failure}finally{await child.finish({originalError:failure})}
 },error=>error===failure);
 assert.equal(child.fixture.exitCode,0);
 assert.equal(await readFile(join(child.home,'candidate.txt'),'utf8'),'retained candidate');
});

test('browser cleanup failure stops the application and retains its state',posix,async t=>{
 const {start}=await setup(t),child=await start(),failure=new Error('browser close failed');
 await assert.rejects(child.finish({close:[['Browser',async()=>{throw failure}]]}),
  error=>error instanceof AggregateError&&error.errors[0]===failure);
 assert.equal(child.fixture.exitCode,0);assert.ok((await stat(child.home)).isDirectory());
});

test('cleanup failure cannot replace the original assertion or remove its evidence',posix,async t=>{
 const {start}=await setup(t),child=await start(),failure=new Error('original assertion');
 const reports=[];t.mock.method(console,'error',value=>reports.push(value));
 await assert.rejects(async()=>{
  try{throw failure}finally{await child.finish({originalError:failure,
   close:[['Browser',async()=>{throw new Error('close failed')}]]})}
 },error=>error===failure);
 assert.equal(child.fixture.exitCode,0);assert.ok((await stat(child.home)).isDirectory());
 assert.ok(reports.some(value=>value instanceof AggregateError));
});

test('abnormal child shutdown remains failure and keeps state',posix,async t=>{
 const {start}=await setup(t),child=await start();
 child.fixture.kill('SIGKILL');
 await assert.rejects(child.finish(),error=>error instanceof AggregateError&&
  error.errors.some(cause=>/shutdown failed \(SIGKILL\)/.test(cause.message)));
 assert.equal(child.fixture.signalCode,'SIGKILL');assert.ok((await stat(child.home)).isDirectory());
});

test('startup failure retains and identifies its allocated home',async t=>{
 const {output,start}=await setup(t),reports=[];
 t.mock.method(console,'error',(...values)=>reports.push(values));
 await assert.rejects(start({python:join(output,'missing-python')}),{code:'ENOENT'});
 const homes=(await readdir(output)).filter(name=>name.startsWith('coordination-fixture-'));
 assert.equal(homes.length,1);
 const retained=reports.flat().filter(value=>typeof value==='string'&&value.startsWith('{'))
  .map(value=>JSON.parse(value)).find(value=>value.coordinationFixture)?.coordinationFixture;
 assert.deepEqual(retained,{home:join(output,homes[0]),status:'retained',reason:'startup failed'});
});
