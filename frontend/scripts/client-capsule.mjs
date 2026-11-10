// Export the finite Unified-owned inputs consumed by the independent web client.
// Client source, version and artifact packaging belong to its own repository.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFileSync,lstatSync,mkdirSync,readFileSync,readdirSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const repository='https://github.com/microsoft/amplifier-unified';
const inputLock='build-inputs.lock.json';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const read=path=>JSON.parse(readFileSync(path,'utf8'));
const write=(path,value)=>writeFileSync(path,JSON.stringify(value,null,2)+'\n');
const git=(...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8'}).trim();
const sharedInputs=[
 'LICENSE',
 'amplifier_web/ai_services.json','amplifier_web/provider_sources.json','amplifier_web/voice_options.json',
 'assets/UPSTREAM-LICENSE','assets/branding/favicons/apple-touch-icon.png',
 'assets/branding/favicons/favicon-32.png','assets/branding/favicons/favicon.ico',
 'assets/branding/icons/amplifier-icon-128.png','assets/branding/pwa/pwa-192.png','assets/branding/pwa/pwa-512.png',
 'packages/shell-sdk/LICENSE','packages/shell-sdk/index.js','packages/shell-sdk/package.json',
].sort();

export function inventory(directory,prefix=''){
 return Object.fromEntries(readdirSync(join(directory,prefix),{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).flatMap(entry=>{
  const name=prefix+entry.name;
  if(entry.isDirectory())return Object.entries(inventory(directory,name+'/'));
  if(!entry.isFile())throw Error('Expected a regular artifact file: '+name);
  const bytes=readFileSync(join(directory,name));return [[name,{sha256:hash(bytes),bytes:bytes.length}]];
 }));
}

function committedInputs(files){
 const revision=git('rev-parse','HEAD');
 // Export one committed input set; local configuration and generated output
 // never enter it. Other unrelated working changes do not block an export.
 git('diff','--exit-code','HEAD','--',...files);
 const tracked=new Set(git('ls-files','-z','--',...files).split('\0'));
 if(files.some(name=>!tracked.has(name)))throw Error('Commit the selected source inputs before exporting');
 return revision;
}

function copyInputs(destination,files){
 for(const name of files)if(!lstatSync(join(root,name)).isFile())throw Error('Expected a regular source input: '+name);
 mkdirSync(dirname(destination),{recursive:true});mkdirSync(destination);
 for(const name of files){const path=join(destination,name);mkdirSync(dirname(path),{recursive:true});copyFileSync(join(root,name),path)}
}

function exportBuildInputs(destination){
 // Owner paths stay unchanged; exported copies are generated build inputs.
 // The independently versioned client does not consume Unified's pyproject.
 const files=sharedInputs;
 const revision=committedInputs(files);
 copyInputs(destination,files);
 const inputs=inventory(destination);
 write(join(destination,inputLock),{schema:1,kind:'unified-web-build-inputs',source:{repository,revision},inputs});
 console.log(JSON.stringify({directory:destination,source:revision,files:files.length}));
}

function verifyBuildInputs(directory,revision){
 // Verification is offline and independent of this checkout's Git state.
 // The consumer supplies the owner revision from its trusted input selection.
 const lockPath=join(directory,inputLock);
 if(!lstatSync(lockPath).isFile())throw Error('Expected a regular build input lock: '+lockPath);
 const lock=read(lockPath);
 if(!/^[a-f0-9]{40}$/.test(revision||'')||lock.schema!==1||lock.kind!=='unified-web-build-inputs'
   ||lock.source?.repository!==repository||lock.source?.revision!==revision||!lock.inputs||typeof lock.inputs!=='object'
   ||Array.isArray(lock.inputs)||!Object.keys(lock.inputs).length){
  throw Error('Build input lock does not match the selected source revision');
 }
 const observed=inventory(directory);delete observed[inputLock];
 if(JSON.stringify(Object.keys(observed).sort())!==JSON.stringify(Object.keys(lock.inputs).sort())
   ||Object.entries(observed).some(([name,value])=>value.sha256!==lock.inputs[name]?.sha256||value.bytes!==lock.inputs[name]?.bytes)){
  throw Error('Build input files differ from the lock; export the selected committed inputs again');
 }
 console.log(JSON.stringify({directory,source:revision,files:Object.keys(observed).length,verified:true}));
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [command,output,revision,...extra]=process.argv.slice(2);
 if(!output||extra.length||!['inputs','verify-inputs'].includes(command)
   ||(command==='inputs'&&revision)||(command==='verify-inputs'&&!revision))
  throw Error('Usage: node frontend/scripts/client-capsule.mjs inputs NEW_DIRECTORY | verify-inputs DIRECTORY SOURCE_COMMIT');
 if(command==='inputs')exportBuildInputs(resolve(output));
 else verifyBuildInputs(resolve(output),revision);
}
