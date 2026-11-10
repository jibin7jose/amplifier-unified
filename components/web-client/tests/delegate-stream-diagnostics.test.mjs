// Source-bound fault probes: no browser, server, provider, or product imports.
import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {setImmediate} from 'node:timers/promises';
import {delegateStreamDiagnostics} from './delegate-stream-diagnostics.mjs';

test('listener and early read failures cannot escape before final collection',async()=>{
  const diagnostics=delegateStreamDiagnostics(),emitter=new EventEmitter();
  const primary=new assert.AssertionError({message:'original browser assertion'});
  const originalStack=primary.stack,calls=[];
  let report;
  emitter.on('write',diagnostics.listen('lookup listener',()=>{throw Error('lookup disk full')}));
  emitter.on('response',diagnostics.listen('response listener',()=>{
    diagnostics.track('response body',async()=>{throw Error('body read rejected')});
    diagnostics.track('response evidence',async()=>{throw Error('post-read write failed')});
  }));
  assert.doesNotThrow(()=>{emitter.emit('write');emitter.emit('response')});
  // A full turn before collection exposes an unhandled-rejection regression to node:test.
  await setImmediate();
  await assert.rejects(diagnostics.finish({error:primary},{
    cleanup:[['browser close',()=>calls.push('close')],['fixture stop',()=>calls.push('stop')]],
    writes:[['lookup report',()=>calls.push('lookup')],['API report',()=>calls.push('api')]],
    report:value=>{report=value}
  }),error=>error===primary);
  assert.equal(primary.stack,originalStack);
  assert.deepEqual(calls,['close','stop','lookup','api']);
  assert.deepEqual(report.errors.map(row=>row.stage),['lookup listener','response body','response evidence']);
  assert.deepEqual(primary.diagnosticFailures,report);
});

test('independent writes and cleanup preserve the primary even if the final report fails',async()=>{
  const diagnostics=delegateStreamDiagnostics(),primary=new Error('original'),calls=[];
  await assert.rejects(diagnostics.finish({error:primary},{
    cleanup:[['browser close',()=>{calls.push('close');throw Error('close failed')}],
      ['fixture stop',()=>calls.push('stop')]],
    writes:[['lookup report',()=>{calls.push('lookup');throw Error('lookup failed')}],
      ['API report',()=>{calls.push('api');throw Error('api failed')}]],
    report:()=>{calls.push('report');throw Error('report destination failed')}
  }),error=>error===primary);
  assert.deepEqual(calls,['close','stop','lookup','api','report']);
  assert.deepEqual(primary.diagnosticFailures.errors.map(row=>row.stage),
    ['browser close','lookup report','API report','diagnostic error report']);
});

for(const stage of ['listener','read','write','cleanup','report']) {
  test(`otherwise successful run fails explicitly on required ${stage} failure`,async()=>{
    const diagnostics=delegateStreamDiagnostics(),fault=()=>{throw Error(stage+' failed')};
    if(stage==='listener')diagnostics.listen('listener',fault)();
    if(stage==='read')diagnostics.track('read',async()=>fault());
    await setImmediate();
    await assert.rejects(diagnostics.finish(undefined,{
      cleanup:stage==='cleanup'?[['cleanup',fault]]:[],
      writes:stage==='write'?[['write',fault]]:[],
      report:stage==='report'?fault:()=>{}
    }),error=>error instanceof AggregateError&&error.errors.some(item=>item.message.includes(stage+' failed')));
  });
}

test('secondary records are bounded and a frozen primary still survives',async()=>{
  const diagnostics=delegateStreamDiagnostics(),primary=Object.freeze(new Error('frozen primary'));
  let report;
  for(let i=0;i<25;i++)diagnostics.listen('listener',()=>{throw Error('x'.repeat(4000))})();
  await assert.rejects(diagnostics.finish({error:primary},{report:value=>{report=value}}),error=>error===primary);
  assert.equal(report.errors.length,20);
  assert.equal(report.omitted,5);
  assert.ok(report.errors.every(row=>row.message.length<=2000));
});

test('closing the browser settles a pending read before final collection',{timeout:1000},async()=>{
  const diagnostics=delegateStreamDiagnostics(),primary=new Error('original');
  let rejectRead,stopped=false;
  diagnostics.track('closing response',()=>new Promise((_,reject)=>{rejectRead=reject}));
  await assert.rejects(diagnostics.finish({error:primary},{
    cleanup:[['browser close',()=>rejectRead(Error('response closed'))],
      ['fixture stop',()=>{stopped=true}]]
  }),error=>error===primary);
  assert.equal(stopped,true);
  assert.equal(primary.diagnosticFailures.errors[0].stage,'closing response');
});

test('successful reads, writes and cleanup do not manufacture a failure',async()=>{
  const diagnostics=delegateStreamDiagnostics(),calls=[];
  diagnostics.track('read',async()=>calls.push('read'));
  await diagnostics.finish(undefined,{
    cleanup:[['stop',()=>calls.push('stop')]],writes:[['write',()=>calls.push('write')]],
    report:value=>assert.deepEqual(value,{errors:[],omitted:0})
  });
  assert.deepEqual(calls,['read','stop','write']);
});