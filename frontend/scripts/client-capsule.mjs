// Disposable current-client export, not another maintained source owner.
// The existing build-input inventory owns which canonical inputs travel.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFileSync,lstatSync,mkdirSync,readFileSync,readdirSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildIdentity,buildInputs} from './build-identity.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const repository='https://github.com/microsoft/amplifier-unified';
const inputLock='build-inputs.lock.json';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const read=path=>JSON.parse(readFileSync(path,'utf8'));
const write=(path,value)=>writeFileSync(path,JSON.stringify(value,null,2)+'\n');
const git=(...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8'}).trim();
const extras=['LICENSE','pyproject.toml','packages/shell-sdk/LICENSE',
 'frontend/scripts/client-capsule.mjs','frontend/tests/client-artifact-browser.mjs',
 'frontend/tests/shell-http-fixture.mjs','frontend/tests/shell-host.mjs',
 'frontend/tests/build-identity.test.mjs'];

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

function exportSource(destination,serverRevision){
 const files=[...new Set([...buildInputs(root),...extras])].sort(),revision=committedInputs(files);
 const paired=git('rev-parse','--verify',(serverRevision||revision)+'^{commit}');
 copyInputs(destination,files);
 const identity=buildIdentity(destination);
 write(join(destination,'component.json'),{
  schema:1,kind:'unified-web-source-capsule',
  source:{repository,revision},
  pairedServer:{repository,revision:paired,transport:'current-http-sse',qualification:'unqualified'},
  build:identity,inputs:inventory(destination),
 });
 console.log(JSON.stringify({directory:destination,source:revision,pairedServer:paired,build:identity,files:files.length}));
}

function exportBuildInputs(destination){
 // Current build metadata and licenses already travel with the full capsule.
 // Keep the same owner paths; these copies are generated inputs, never sources.
 const files=[...new Set([...buildInputs(root),...extras].filter(name=>!name.startsWith('frontend/')))].sort();
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

function packageStatic(destination){
 const capsule=read(join(root,'component.json'));
 if(capsule.kind!=='unified-web-source-capsule')throw Error('Package from an exported source capsule');
 for(const [name,expected] of Object.entries(capsule.inputs)){
  if(hash(readFileSync(join(root,name)))!==expected.sha256)throw Error('Capsule source changed; export the new committed inputs: '+name);
 }
 const assets=join(root,'amplifier_web/static'),identity=read(join(assets,'build.json'));
 if(JSON.stringify(identity)!==JSON.stringify(capsule.build))throw Error('Build identity differs from the exported inputs');
 mkdirSync(dirname(destination),{recursive:true});mkdirSync(destination);
 for(const name of Object.keys(inventory(assets))){
  const path=join(destination,'static',name);mkdirSync(dirname(path),{recursive:true});copyFileSync(join(assets,name),path);
 }
 copyFileSync(join(root,'LICENSE'),join(destination,'LICENSE'));
 // The direct SDK import retains its license beside bundled third-party notices.
 copyFileSync(join(root,'packages/shell-sdk/LICENSE'),join(destination,'static/licenses/shell-sdk.txt'));
 writeFileSync(join(destination,'index.mjs'),"import {fileURLToPath} from 'node:url';\nexport const webDirectory=fileURLToPath(new URL('./static/',import.meta.url));\n");
 write(join(destination,'package.json'),{name:'@amplifier/unified-client-web',version:'0.0.0-pilot.'+capsule.source.revision.slice(0,12),
  private:true,type:'module',license:'MIT',exports:{'.':'./index.mjs','./manifest':'./component.json'},
  files:['static','index.mjs','component.json','LICENSE']});
 write(join(destination,'component.json'),{...capsule,kind:'unified-web-static-artifact',assets:inventory(join(destination,'static'))});
 console.log(JSON.stringify({directory:destination,build:identity,pairedServer:capsule.pairedServer,assets:Object.keys(inventory(join(destination,'static'))).length}));
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [command,output,serverRevision]=process.argv.slice(2);
 if(!output||!['export','package','inputs','verify-inputs'].includes(command))throw Error('Usage: node frontend/scripts/client-capsule.mjs export NEW_DIRECTORY [SERVER_REVISION] | package NEW_DIRECTORY | inputs NEW_DIRECTORY | verify-inputs DIRECTORY SOURCE_COMMIT');
 if(command==='export')exportSource(resolve(output),serverRevision);
 else if(command==='inputs')exportBuildInputs(resolve(output));
 else if(command==='verify-inputs')verifyBuildInputs(resolve(output),serverRevision);
 else packageStatic(resolve(output));
}
