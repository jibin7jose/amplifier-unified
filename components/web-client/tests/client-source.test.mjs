import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync,spawnSync} from 'node:child_process';
import {cpSync,mkdirSync,mkdtempSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {basename,dirname,join} from 'node:path';
import {createSourceRecord,selectedInputs,verifySource} from '../scripts/client-source.mjs';

const git=(root,...args)=>execFileSync('git',args,{cwd:root,encoding:'utf8',stdio:'pipe'}).trim();
function repository(root){
 mkdirSync(root,{recursive:true});git(root,'init','-q');git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid');
}
function commit(root){git(root,'add','.');git(root,'-c','commit.gpgsign=false','commit','-qm','fixture')}
function fixture(t){
 const home=mkdtempSync(join(tmpdir(),'client-source-'));t.after(()=>rmSync(home,{recursive:true,force:true}));
 const root=join(home,'client');repository(root);mkdirSync(join(root,'src'));mkdirSync(join(root,'public'));
 writeFileSync(join(root,'README.md'),'Unrelated documentation');
 writeFileSync(join(root,'src/main.js'),'client');writeFileSync(join(root,'public/page.html'),'page');
 for(const name of selectedInputs(root)){mkdirSync(dirname(join(root,name)),{recursive:true});writeFileSync(join(root,name),name)}
 commit(root);const record=createSourceRecord(root),path=join(home,'source.json');writeFileSync(path,JSON.stringify(record));
 return {home,root,record,path};
}
function copy(root,destination){cpSync(root,destination,{recursive:true,filter:path=>basename(path)!=='.git'})}
test('Git-free and nested copies retain the canonical client identity, including distribution-owned fields',t=>{
 const {home,root,record,path}=fixture(t),plain=join(home,'plain'),host=join(home,'host');copy(root,plain);
 assert.deepEqual(verifySource(plain,path),record.source);
 repository(host);writeFileSync(join(host,'host.txt'),'Host');commit(host);
 const nested=join(host,'components/web-client');copy(root,nested);
 writeFileSync(path,JSON.stringify({...record,delivery:{type:'directory',path:'components/web-client'}}));
 assert.deepEqual(verifySource(nested,path),record.source);
 assert.throws(()=>createSourceRecord(nested),/nested in another Git repository/);
 assert.equal(createSourceRecord(root).source.revision,git(root,'rev-parse','HEAD'));
});
test('autocrlf checkouts preserve canonical text, binary bytes and copied-source identity',t=>{
 const {home,root,path}=fixture(t),text=Buffer.from('first line\nsecond line\n'),binary=Buffer.from([0,13,10,255,10]);
 writeFileSync(join(root,'.gitattributes'),readFileSync(new URL('../.gitattributes',import.meta.url)));
 writeFileSync(join(root,'src/main.js'),text);writeFileSync(join(root,'src/binary.dat'),binary);commit(root);
 const record=createSourceRecord(root);writeFileSync(path,JSON.stringify(record));
 function checkout(source,destination){
  git(home,'-c','core.autocrlf=true','clone','-q','--no-local',source,destination);
  git(destination,'config','core.autocrlf','true');
  assert.equal(git(destination,'status','--porcelain'),'');
 }
 function verify(directory){
  assert.deepEqual(verifySource(directory,path),record.source);
  assert.deepEqual(readFileSync(join(directory,'src/main.js')),text);
  assert.deepEqual(readFileSync(join(directory,'src/binary.dat')),binary);
 }
 const standalone=join(home,'standalone');checkout(root,standalone);verify(standalone);
 const distribution=join(home,'distribution');repository(distribution);
 writeFileSync(join(distribution,'.gitattributes'),'* text=auto eol=crlf\n');
 copy(root,join(distribution,'components/web-client'));commit(distribution);
 const nested=join(home,'nested');checkout(distribution,nested);verify(join(nested,'components/web-client'));
});
test('changed selected source, tools, license, lock and added browser inputs cannot claim the old source',t=>{
 const {root,path}=fixture(t);
 for(const name of ['src/main.js','scripts/client-source.mjs','scripts/package-client.mjs','scripts/prepare-inputs.mjs','LICENSE','build-inputs.lock.json']){
  const original=readFileSync(join(root,name));writeFileSync(join(root,name),'changed');
  assert.throws(()=>verifySource(root,path),/differs from its trusted record/);writeFileSync(join(root,name),original);
 }
 writeFileSync(join(root,'src/new.js'),'unrecorded');assert.throws(()=>verifySource(root,path),/differs from its trusted record/);
 assert.throws(()=>createSourceRecord(root),/Commit selected client inputs/);
});
test('selected dirty Git inputs and unsupported records fail explicitly',t=>{
 const {root,path,record}=fixture(t);writeFileSync(join(root,'src/main.js'),'dirty');assert.throws(()=>createSourceRecord(root));
 for(const changed of [{...record,schema:2},{...record,source:{...record.source,repository:'https://github.com/microsoft/amplifier-unified'}},{...record,source:{...record.source,revision:'latest'}}]){
  writeFileSync(path,JSON.stringify(changed));assert.throws(()=>verifySource(root,path),/Unsupported client source record/);
 }
});
test('deleted browser inputs cannot claim HEAD, while unrelated tracked docs do not block packaging',t=>{
 const {root,record}=fixture(t);
 for(const name of ['src/main.js','public/page.html']){
  const original=readFileSync(join(root,name));rmSync(join(root,name));
  assert.throws(()=>createSourceRecord(root),'Tracked deletion must not disappear from the selected Git check');
  writeFileSync(join(root,name),original);
 }
 writeFileSync(join(root,'README.md'),'Edited unrelated documentation');assert.deepEqual(createSourceRecord(root),record);
});
test('a FIFO source record is rejected before reading',{skip:process.platform==='win32'},t=>{
 const {root,path}=fixture(t);rmSync(path);execFileSync('mkfifo',[path]);
 const module=new URL('../scripts/client-source.mjs',import.meta.url).href;
 const result=spawnSync(process.execPath,['--input-type=module','-e',`import {verifySource} from ${JSON.stringify(module)};verifySource(process.argv[1],process.argv[2]);`,root,path],{encoding:'utf8',timeout:2000,killSignal:'SIGKILL'});
 assert.equal(result.error,undefined);assert.equal(result.status,1);assert.match(result.stderr,/regular client source file/);
});
