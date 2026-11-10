// Breadbin journey launch classification, not source or permission attestation.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

export async function readyExecution({env=process.env,read=readFile}={}){
 const python=env.AMPLIFIER_TEST_PYTHON;
 assert.ok(python,'AMPLIFIER_TEST_PYTHON must name the prepared interpreter; no .venv fallback');
 assert.ok(env.AMPLIFIER_TEST_ARTIFACTS,'AMPLIFIER_TEST_ARTIFACTS must name the owned evidence directory');
 const explicit=Object.hasOwn(env,'AMPLIFIER_TEST_CONTRACT');
 if(explicit)assert.ok(env.AMPLIFIER_TEST_CONTRACT,'An explicit execution contract path must not be empty');
 const path=explicit?env.AMPLIFIER_TEST_CONTRACT:'/opt/unified-prepared/execution-contract.json';
 let text;
 try{text=await read(path,'utf8')}
 catch(error){
  // Only the ordinary runner's missing default is supported without a contract.
  // Its UUID tag identifies cleanup ownership; it is NOT managed admission proof.
  if(explicit||error.code!=='ENOENT'||
   !/^[a-f0-9]{32}$/.test(env.AMPLIFIER_BROWSER_CHECK_OWNER||''))throw error;
  return {executionMode:'background',runnerOwnerTag:env.AMPLIFIER_BROWSER_CHECK_OWNER};
 }
 const contract=JSON.parse(text);
 assert.equal(contract.profileId,'browser-test');assert.equal(contract.python,python);
 assert.ok(contract.source&&typeof contract.source==='object','Prepared executionContract must retain the admitted source identity');
 return {executionMode:'managed',executionContract:{version:contract.version,profileId:contract.profileId,source:contract.source}};
}
