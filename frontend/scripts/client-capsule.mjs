// Disposable current-client export, not another maintained source owner.
// The existing build-input inventory owns which canonical inputs travel.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFileSync,mkdirSync,readFileSync,readdirSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildIdentity,buildInputs} from './build-identity.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
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

function exportSource(destination,serverRevision){
 const revision=git('rev-parse','HEAD'),files=[...new Set([...buildInputs(root),...extras])].sort();
 // Export one committed input set; local configuration and generated output
 // never enter it. Other unrelated working changes do not block an export.
 git('diff','--exit-code','HEAD','--',...files);
 const tracked=new Set(git('ls-files','-z','--',...files).split('\0'));
 if(files.some(name=>!tracked.has(name)))throw Error('Commit the selected source inputs before exporting');
 const paired=git('rev-parse','--verify',(serverRevision||revision)+'^{commit}');
 mkdirSync(dirname(destination),{recursive:true});mkdirSync(destination);
 for(const name of files){const path=join(destination,name);mkdirSync(dirname(path),{recursive:true});copyFileSync(join(root,name),path)}
 const identity=buildIdentity(destination);
 write(join(destination,'component.json'),{
  schema:1,kind:'unified-web-source-capsule',
  source:{repository:'https://github.com/microsoft/amplifier-unified',revision},
  pairedServer:{repository:'https://github.com/microsoft/amplifier-unified',revision:paired,transport:'current-http-sse',qualification:'unqualified'},
  build:identity,inputs:inventory(destination),
 });
 console.log(JSON.stringify({directory:destination,source:revision,pairedServer:paired,build:identity,files:files.length}));
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
 if(!output||!['export','package'].includes(command))throw Error('Usage: node frontend/scripts/client-capsule.mjs export NEW_DIRECTORY [SERVER_REVISION] | package NEW_DIRECTORY');
 if(command==='export')exportSource(resolve(output),serverRevision);else packageStatic(resolve(output));
}
