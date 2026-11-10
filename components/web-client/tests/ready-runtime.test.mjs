import test from 'node:test';
import assert from 'node:assert/strict';
import {readyExecution} from './ready-runtime.mjs';

const base={AMPLIFIER_TEST_PYTHON:'/fixture/.venv/bin/python',AMPLIFIER_TEST_ARTIFACTS:'/fixture/evidence'};
const owner='0123456789abcdef0123456789abcdef';
const contract={version:1,profileId:'browser-test',python:base.AMPLIFIER_TEST_PYTHON,source:{revision:'exact-admitted-source'}};
const missing=async()=>{throw Object.assign(new Error('missing contract'),{code:'ENOENT'})};
const load=(env,read)=>readyExecution({env:{...base,...env},read});

test('background runner may omit only its absent default contract without fabricating admission',async()=>{
 let requested;
 const result=await load({AMPLIFIER_BROWSER_CHECK_OWNER:owner},async(path,encoding)=>{
  requested={path,encoding};return missing();
 });
 assert.deepEqual(requested,{path:'/opt/unified-prepared/execution-contract.json',encoding:'utf8'});
 assert.deepEqual(result,{executionMode:'background',runnerOwnerTag:owner});
 assert.equal(Object.hasOwn(result,'executionContract'),false);
});
for(const tag of [undefined,'','unrecognized-owner','0123456789abcdef0123456789abcdeF'])
 test('missing contract rejects invalid or absent cleanup owner '+String(tag),async()=>{
  await assert.rejects(load({AMPLIFIER_BROWSER_CHECK_OWNER:tag},missing),/missing contract/);
 });
for(const key of ['AMPLIFIER_TEST_PYTHON','AMPLIFIER_TEST_ARTIFACTS'])
 test('explicit '+key+' remains mandatory in background execution',async()=>{
  await assert.rejects(load({[key]:'',AMPLIFIER_BROWSER_CHECK_OWNER:owner},missing),new RegExp(key));
 });
for(const explicit of [false,true]){
 const env={AMPLIFIER_BROWSER_CHECK_OWNER:owner,...(explicit?{AMPLIFIER_TEST_CONTRACT:'/fixture/contract.json'}:{})};
 test((explicit?'explicit':'default')+' managed contract keeps exact admitted identity',async()=>{
  let requested;
  const result=await load(env,async path=>{requested=path;return JSON.stringify(contract)});
  assert.equal(requested,explicit?'/fixture/contract.json':'/opt/unified-prepared/execution-contract.json');
  assert.deepEqual(result,{executionMode:'managed',executionContract:{version:1,profileId:'browser-test',source:contract.source}});
  assert.equal(Object.hasOwn(result,'runnerOwnerTag'),false);
 });
 for(const [name,value] of [['wrong profile',{...contract,profileId:'linux-python'}],
  ['wrong Python',{...contract,python:'/different/python'}],['missing source',{...contract,source:null}]])
  test((explicit?'explicit':'default')+' invalid '+name+' never falls back despite owner tag',async()=>{
   await assert.rejects(load(env,async()=>JSON.stringify(value)));
  });
 test((explicit?'explicit':'default')+' malformed JSON never falls back',async()=>{
  await assert.rejects(load(env,async()=>'{'),SyntaxError);
 });
 test((explicit?'explicit':'default')+' permission error never falls back',async()=>{
  const denied=Object.assign(new Error('unreadable contract'),{code:'EACCES'});
  await assert.rejects(load(env,async()=>{throw denied}),error=>error===denied);
 });
}
test('explicit missing contract cannot use background fallback',async()=>{
 await assert.rejects(load({AMPLIFIER_TEST_CONTRACT:'/fixture/missing.json',AMPLIFIER_BROWSER_CHECK_OWNER:owner},missing),/missing contract/);
});
test('explicit empty contract cannot use default or background fallback',async()=>{
 await assert.rejects(load({AMPLIFIER_TEST_CONTRACT:'',AMPLIFIER_BROWSER_CHECK_OWNER:owner},missing),/must not be empty/);
});
