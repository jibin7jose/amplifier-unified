// Static installation artifact: no server, updater, dependency resolver or runtime.
import {createHash} from 'node:crypto';
import {copyFileSync,mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildIdentity} from './build-identity.mjs';
import {inventory,verifyInputs} from './prepare-inputs.mjs';
import {clientSource} from './client-source.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
const read=path=>JSON.parse(readFileSync(path,'utf8'));
const write=(path,value)=>writeFileSync(path,JSON.stringify(value,null,2)+'\n');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

export function packageClient(destination,pairedRevision,sourceRecord){
 if(!/^[a-f0-9]{40}$/.test(pairedRevision||''))throw Error('Declare one exact paired Unified server commit');
 const shared=verifyInputs(root,join(root,'.build-inputs'));
 const source=clientSource(root,sourceRecord);
 const assets=join(root,'dist'),build=read(join(assets,'build.json'));
 if(JSON.stringify(build)!==JSON.stringify(buildIdentity(root)))throw Error('Built assets do not match current source identity; rebuild');
 const pkg=read(join(root,'package.json'));
 mkdirSync(dirname(destination),{recursive:true});mkdirSync(destination);
 for(const name of Object.keys(inventory(assets))){
  const path=join(destination,'static',name);mkdirSync(dirname(path),{recursive:true});copyFileSync(join(assets,name),path);
 }
 copyFileSync(join(root,'LICENSE'),join(destination,'LICENSE'));
 copyFileSync(join(root,'.build-inputs/packages/shell-sdk/LICENSE'),join(destination,'static/licenses/shell-sdk.txt'));
 writeFileSync(join(destination,'index.mjs'),"import {fileURLToPath} from 'node:url';\nexport const webDirectory=fileURLToPath(new URL('./static/',import.meta.url));\n");
 write(join(destination,'package.json'),{name:pkg.name,version:pkg.version,private:true,type:'module',license:'MIT',
  exports:{'.':'./index.mjs','./manifest':'./component.json'},files:['static','index.mjs','component.json','LICENSE']});
 const component={schema:1,kind:'unified-web-static-artifact',source,
  sharedInputs:{source:shared.source,lockSha256:hash(readFileSync(join(root,'build-inputs.lock.json')))},
  pairedServer:{repository:'https://github.com/microsoft/amplifier-unified',revision:pairedRevision,transport:'current-http-sse',qualification:'unqualified'},
  build,assets:inventory(join(destination,'static'))};
 write(join(destination,'component.json'),component);
 return {directory:destination,source,build,pairedServer:component.pairedServer,assets:Object.keys(component.assets).length};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const [output,pairedRevision,option,record,...extra]=process.argv.slice(2);
 if(!output||(option&&(option!=='--source-record'||!record))||extra.length)
  throw Error('Usage: npm run package:artifact -- NEW_DIRECTORY PAIRED_SERVER_COMMIT [--source-record RECORD]');
 console.log(JSON.stringify(packageClient(resolve(output),pairedRevision,record&&resolve(record))));
}
