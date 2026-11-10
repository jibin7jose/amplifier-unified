// Materialize one trusted canonical export; these files are never authored here.
import {createHash} from 'node:crypto';
import {lstatSync,mkdirSync,mkdtempSync,readFileSync,readdirSync,renameSync,rmSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const lockName='build-inputs.lock.json';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
function regular(path){
 if(!lstatSync(path).isFile())throw Error('Expected a regular build input: '+path);
 return readFileSync(path);
}
export function inventory(directory,prefix=''){
 return Object.fromEntries(readdirSync(join(directory,prefix),{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(entry=>{
  const name=prefix+entry.name;
  if(entry.isDirectory())return Object.entries(inventory(directory,name+'/'));
  const bytes=regular(join(directory,name));return [[name,{sha256:hash(bytes),bytes:bytes.length}]];
 }));
}
function selectedLock(root){
 const bytes=regular(join(root,lockName)),lock=JSON.parse(bytes);
 if(lock.schema!==1||lock.kind!=='unified-web-build-inputs'||lock.source?.repository!=='https://github.com/microsoft/amplifier-unified'
   ||!/^[a-f0-9]{40}$/.test(lock.source?.revision||'')||!lock.inputs||Array.isArray(lock.inputs)||typeof lock.inputs!=='object'||!Object.keys(lock.inputs).length)
  throw Error('Unsupported canonical build input lock');
 for(const [name,value] of Object.entries(lock.inputs)){
  if(!/^[A-Za-z0-9._/-]+$/.test(name)||name.split('/').some(part=>!part||part==='.'||part==='..')||name===lockName
    ||!/^[a-f0-9]{64}$/.test(value?.sha256||'')||!Number.isSafeInteger(value?.bytes)||value.bytes<0)
   throw Error('Unsupported build input path or digest: '+name);
 }
 return {bytes,lock};
}
export function verifyInputs(root,directory){
 const {bytes,lock}=selectedLock(root),selected=regular(join(directory,lockName));
 if(!bytes.equals(selected))throw Error('Build input lock differs from the selected canonical export');
 const observed=inventory(directory);delete observed[lockName];
 if(JSON.stringify(Object.keys(observed).sort())!==JSON.stringify(Object.keys(lock.inputs).sort())
   ||Object.entries(observed).some(([name,value])=>value.sha256!==lock.inputs[name]?.sha256||value.bytes!==lock.inputs[name]?.bytes))
  throw Error('Build input files differ from the selected lock');
 return lock;
}
export async function fetchInputs(root,{fetch=globalThis.fetch,timeoutMs=15000}={}){
 const {bytes,lock}=selectedLock(root),staging=mkdtempSync(join(root,'.build-inputs-stage-'));
 try{
  writeFileSync(join(staging,lockName),bytes);
  for(const [name,expected] of Object.entries(lock.inputs)){
   const url='https://raw.githubusercontent.com/microsoft/amplifier-unified/'+lock.source.revision+'/'+name;
   const response=await fetch(url,{signal:AbortSignal.timeout(timeoutMs),redirect:'error'});
   if(!response.ok){await response.body?.cancel();throw Error('Canonical input request failed ('+response.status+'): '+name)}
   const chunks=[];let size=0;
   for await(const chunk of response.body){
    size+=chunk.length;if(size>expected.bytes)throw Error('Canonical input exceeds its locked size: '+name);
    chunks.push(chunk);
   }
   const content=Buffer.concat(chunks);
   if(size!==expected.bytes||hash(content)!==expected.sha256)throw Error('Canonical input differs from its locked bytes: '+name);
   const path=join(staging,name);mkdirSync(dirname(path),{recursive:true});writeFileSync(path,content);
  }
  return prepareInputs(root,staging);
 }finally{rmSync(staging,{recursive:true,force:true})}
}
export function prepareInputs(root,source){
 const lock=verifyInputs(root,source),destination=join(root,'.build-inputs');
 const staging=mkdtempSync(join(root,'.build-inputs-stage-')),previous=join(staging,'previous');
 const ready=join(staging,'ready');mkdirSync(ready);
 let moved=false,retainRecovery=false;
 try{
  for(const name of [...Object.keys(lock.inputs),lockName]){
   const path=join(ready,name);mkdirSync(dirname(path),{recursive:true});writeFileSync(path,regular(join(source,name)));
  }
  verifyInputs(root,ready);
  // Rename only this generated root. A linked old root is moved, never followed.
  let current;try{current=lstatSync(destination)}catch(error){if(error.code!=='ENOENT')throw error}
  if(current){
   renameSync(destination,previous);moved=true;
  }
  try{renameSync(ready,destination)}catch(error){
   if(moved)try{renameSync(previous,destination)}catch(rollback){
    retainRecovery=true;throw new AggregateError([error,rollback],'Restore prior build inputs from '+previous);
   }
   throw error;
  }
  return {directory:destination,source:lock.source,files:Object.keys(lock.inputs).length};
 }finally{if(!retainRecovery)rmSync(staging,{recursive:true,force:true})}
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const source=process.argv[2],root=fileURLToPath(new URL('../',import.meta.url));
 console.log(JSON.stringify(source?prepareInputs(root,resolve(source)):await fetchInputs(root)));
}
