import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

// Exercise the maintained browser script's actual finalization statements,
// without importing it (which would launch its host/browser fixture).
const source=readFileSync(new URL('./attachment-experience-browser.mjs',import.meta.url),'utf8');
const start=' report.complete=true;',end="\n}\nif(!report.passed)throw primary||Error('Attachment experience checks incomplete');";
assert.equal(source.split(start).length,2,'Final report boundary must be unique');
assert.equal(source.split(end).length,2,'Final verdict must be unique');
const from=source.indexOf(start),to=source.indexOf(end,from);
assert.ok(to>from,'Final report precedes its verdict');
const body=source.slice(from,to)+end.slice(2);
const AsyncFunction=Object.getPrototypeOf(async()=>{}).constructor;
const compile=text=>new AsyncFunction('report','primary','stderr','save',text+'\nreturn report;');
const finalize=compile(body);
const reportFor=()=>({cases:[{status:'passed'}],fixtureExit:{code:0}});

async function assertPrimaryPreserved(run){
 const primary=new Error('Original attachment assertion'),writeError=new Error('Final evidence write failed');
 const report={...reportFor(),error:primary.stack};let writes=0;
 await assert.rejects(()=>run(report,primary,'fixture stderr',async()=>{writes++;throw writeError}),error=>{
  assert.ok(error instanceof AggregateError,'Primary failure must survive the final report write failure');
  assert.equal(error.cause,primary);assert.deepEqual(error.errors,[primary,writeError]);return true;
 });
 assert.equal(writes,1,'Do not replay a failed report write');
 assert.equal(report.complete,true);assert.equal(report.passed,false);
 assert.equal(report.error,primary.stack);assert.equal(report.stderr,'fixture stderr');
}

test('final report write failure retains the primary failure and its cause',async()=>{
 await assertPrimaryPreserved(finalize);
});

test('report write failure alone fails and cannot leave a passing verdict',async()=>{
 const report=reportFor(),writeError=new Error('Evidence unavailable');let writes=0;
 await assert.rejects(()=>finalize(report,undefined,'',async()=>{writes++;throw writeError}),error=>error===writeError);
 assert.equal(writes,1);assert.equal(report.complete,true);assert.equal(report.passed,false);
});

test('successful reporting retains the original failure or successful verdict',async()=>{
 const primary=new Error('Original failed case'),failed=reportFor();let writes=0;
 await assert.rejects(()=>finalize(failed,primary,'',async()=>{writes++}),error=>error===primary);
 assert.equal(failed.passed,false);assert.equal(writes,1);
 const passed=reportFor();
 assert.equal(await finalize(passed,undefined,'',async()=>{writes++}),passed);
 assert.equal(passed.complete,true);assert.equal(passed.passed,true);assert.equal(writes,2);
 const incomplete={cases:[{status:'unrun'}],fixtureExit:{code:0}};
 await assert.rejects(()=>finalize(incomplete,undefined,'',async()=>{}),/Attachment experience checks incomplete/);
 assert.equal(incomplete.passed,false);
});

test('the same primary-preservation oracle rejects the original unguarded write',async()=>{
 const guard=body.match(/ try\{await save\(\)\}catch\(error\)\{[\s\S]*?\n \}/g);
 assert.equal(guard?.length,1,'Negative control must replace the actual guarded write');
 const original=compile(body.replace(guard[0],' await save();'));
 await assert.rejects(()=>assertPrimaryPreserved(original),error=>{
  assert.equal(error.code,'ERR_ASSERTION');
  assert.match(error.message,/Primary failure must survive/);return true;
 });
});
