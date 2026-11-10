// A copied source record preserves a trusted selection, not independent authorship authentication.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {lstatSync,mkdirSync,readFileSync,realpathSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildInputs,generatedPublic} from './build-identity.mjs';

const repository='https://github.com/microsoft/amplifier-client-web';
const git=(root,...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8'}).trim();
function regular(path){
 if(!lstatSync(path).isFile())throw Error('Expected a regular client source file: '+path);
 return readFileSync(path);
}
export function selectedInputs(root){
 return [...new Set([...buildInputs(root).filter(name=>!name.startsWith('.build-inputs/')),
  'LICENSE','build-inputs.lock.json','scripts/client-source.mjs','scripts/prepare-inputs.mjs','scripts/package-client.mjs'])].sort();
}
function inputsSha256(root){
 const hash=createHash('sha256');
 for(const name of selectedInputs(root)){
  const bytes=regular(join(root,name));hash.update(name+'\0'+bytes.length+'\0').update(bytes);
 }
 return hash.digest('hex');
}
export function createSourceRecord(root){
 if(realpathSync(git(root,'rev-parse','--show-toplevel'))!==realpathSync(root))
  throw Error('Client source is nested in another Git repository; supply its trusted --source-record');
 const files=selectedInputs(root);
 // Include deleted authored files, which no longer appear in the current inventory.
 git(root,'diff','--exit-code','HEAD','--',...files,'src','public',
  ...[...generatedPublic].map(name=>':(exclude)public/'+name));
 const tracked=new Set(git(root,'ls-files','-z','--',...files).split('\0'));
 if(files.some(name=>!tracked.has(name)))throw Error('Commit selected client inputs before recording their source');
 return {schema:1,kind:'amplifier-web-client-source',source:{repository,revision:git(root,'rev-parse','HEAD')},inputsSha256:inputsSha256(root)};
}
export function verifySource(root,path){
 const record=JSON.parse(regular(path));
 if(record.schema!==1||record.kind!=='amplifier-web-client-source'||record.source?.repository!==repository
   ||!/^[a-f0-9]{40}$/.test(record.source?.revision||'')||!/^[a-f0-9]{64}$/.test(record.inputsSha256||''))
  throw Error('Unsupported client source record');
 if(inputsSha256(root)!==record.inputsSha256)throw Error('Client source differs from its trusted record; select the changed canonical source before packaging');
 return {repository,revision:record.source.revision};
}
export const clientSource=(root,record)=>record?verifySource(root,record):createSourceRecord(root).source;

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [command,path]=process.argv.slice(2),root=fileURLToPath(new URL('../',import.meta.url));
 if(!path||!['record','verify'].includes(command))throw Error('Usage: node scripts/client-source.mjs record NEW_RECORD | verify RECORD');
 if(command==='verify')console.log(JSON.stringify(verifySource(root,resolve(path))));
 else{
  const record=createSourceRecord(root);mkdirSync(dirname(resolve(path)),{recursive:true});
  writeFileSync(resolve(path),JSON.stringify(record,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(record));
 }
}
