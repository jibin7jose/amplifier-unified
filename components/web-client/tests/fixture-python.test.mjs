import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync,readdirSync} from 'node:fs';
import test from 'node:test';
import {fixturePython} from './fixture-python.mjs';

function environment(t,value) {
 const previous=process.env.AMPLIFIER_TEST_PYTHON;
 t.after(()=>{
  if(previous===undefined)delete process.env.AMPLIFIER_TEST_PYTHON;
  else process.env.AMPLIFIER_TEST_PYTHON=previous;
 });
 if(value===undefined)delete process.env.AMPLIFIER_TEST_PYTHON;
 else process.env.AMPLIFIER_TEST_PYTHON=value;
}

test('prepared interpreter wins over checkout and legacy fallbacks without rewriting paths',t=>{
 environment(t,'/prepared environment/Pythón/bin/python');
 assert.equal(fixturePython('/checkout/.venv/bin/python'),process.env.AMPLIFIER_TEST_PYTHON);
 assert.equal(fixturePython('/legacy/bin/python'),process.env.AMPLIFIER_TEST_PYTHON);
 process.env.AMPLIFIER_TEST_PYTHON=String.raw`C:\Prepared environment\Scripts\python.exe`;
 assert.equal(fixturePython('/checkout/.venv/bin/python'),process.env.AMPLIFIER_TEST_PYTHON);
});

test('unset or empty override retains the existing local or legacy fallback at launch time',t=>{
 environment(t,undefined);
 for(const fallback of ['/checkout/.venv/bin/python','/legacy override/python',String.raw`C:\Work\Scripts\python.exe`]){
  assert.equal(fixturePython(fallback),fallback);
 }
 process.env.AMPLIFIER_TEST_PYTHON='';
 assert.equal(fixturePython('/legacy/python'),'/legacy/python');
 process.env.AMPLIFIER_TEST_PYTHON='/next/prepared/python';
 assert.equal(fixturePython('/legacy/python'),'/next/prepared/python');
});

test('selected executable launches a real child with separate unchanged arguments',t=>{
 environment(t,process.execPath);
 const argument='value with spaces, ü, $ and quotes "';
 const child=spawnSync(fixturePython('/nonexistent-checkout/.venv/bin/python'),
  ['-e','process.stdout.write(JSON.stringify(process.argv.slice(1)))',argument],
  {encoding:'utf8',timeout:10000});
 assert.equal(child.error,undefined);
 assert.equal(child.status,0,child.stderr);
 assert.deepEqual(JSON.parse(child.stdout),[argument]);
});

test('an invalid explicit interpreter fails instead of silently using a working fallback',t=>{
 environment(t,'/nonexistent-prepared-environment/python');
 const child=spawnSync(fixturePython(process.execPath),['-e','process.stdout.write("wrong fallback")'],
  {encoding:'utf8',timeout:10000});
 assert.equal(child.error?.code,'ENOENT');
 assert.equal(child.status,null);
 assert.ok(!child.stdout);
});

test('checkout interpreter fallbacks in frontend launchers honor the prepared environment',()=>{
 const directory=new URL('./',import.meta.url),missing=[];
 for(const name of readdirSync(directory).filter(name=>name.endsWith('.mjs')&&!name.endsWith('.test.mjs'))){
  const lines=readFileSync(new URL(name,directory),'utf8').split('\n');
  lines.forEach((line,index)=>{
   if(line.includes('.venv/bin/python')&&!line.includes('AMPLIFIER_TEST_PYTHON')&&!line.includes('fixturePython(')){
    missing.push(`${name}:${index+1}`);
   }
  });
 }
 assert.deepEqual(missing,[],'Python fixture launchers must retain AMPLIFIER_TEST_PYTHON support');
});
