import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync,spawnSync} from 'node:child_process';
import {copyFileSync,existsSync,mkdirSync,mkdtempSync,readFileSync,rmSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const shared=[
 'LICENSE',
 'amplifier_web/ai_services.json','amplifier_web/provider_sources.json','amplifier_web/voice_options.json',
 'assets/UPSTREAM-LICENSE','assets/branding/favicons/apple-touch-icon.png',
 'assets/branding/favicons/favicon-32.png','assets/branding/favicons/favicon.ico',
 'assets/branding/icons/amplifier-icon-128.png','assets/branding/pwa/pwa-192.png','assets/branding/pwa/pwa-512.png',
 'packages/shell-sdk/LICENSE','packages/shell-sdk/index.js','packages/shell-sdk/package.json',
].sort();
const helper='scripts/web-client-inputs.mjs';
const git=(directory,...args)=>execFileSync('git',args,{cwd:directory,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
function fixture(t){
 const temporary=mkdtempSync(join(tmpdir(),'web-inputs-')),source=join(temporary,'source');
 t.after(()=>rmSync(temporary,{recursive:true,force:true}));
 for(const name of [...shared,helper]){
  const target=join(source,name);mkdirSync(dirname(target),{recursive:true});
  copyFileSync(join(root,name),target);
 }
 git(source,'init','--quiet');git(source,'add','.');
 git(source,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Canonical fixture inputs');
 const revision=git(source,'rev-parse','HEAD');
 const run=(...args)=>spawnSync(process.execPath,[join(source,helper),...args],{cwd:temporary,encoding:'utf8'});
 const output=join(temporary,'inputs');
 const exported=run('inputs',output);assert.equal(exported.status,0,exported.stderr);
 return {temporary,source,revision,output,run};
}

test('finite shared inputs preserve canonical owner bytes without exporting client/backend source',t=>{
 const f=fixture(t),lock=JSON.parse(readFileSync(join(f.output,'build-inputs.lock.json')));
 assert.deepEqual(Object.keys(lock.inputs).sort(),shared);
 assert.equal(lock.source.revision,f.revision);
 assert.equal(lock.pairedServer,undefined,'Input-owner revision is not compatibility evidence');
 for(const name of shared)assert.deepEqual(readFileSync(join(f.output,name)),readFileSync(join(f.source,name)));
 assert.equal(existsSync(join(f.output,'components')),false);
 assert.equal(existsSync(join(f.output,'.git')),false);
 assert.equal(existsSync(join(f.output,'pyproject.toml')),false,'Server release metadata is not a client build input');
 for(const command of ['export','package']){
  const output=join(f.temporary,command);
  assert.notEqual(f.run(command,output,f.revision).status,0,'Retired pilot commands cannot create another source/artifact owner');
  assert.equal(existsSync(output),false);
 }
});

test('verification is offline and rejects wrong selection, missing, changed and extra inputs',t=>{
 const f=fixture(t);
 rmSync(join(f.source,'.git'),{recursive:true});
 assert.equal(f.run('verify-inputs',f.output,f.revision).status,0);
 assert.notEqual(f.run('verify-inputs',f.output,'0'.repeat(40)).status,0);
 const file=join(f.output,'amplifier_web/voice_options.json'),saved=readFileSync(file);
 writeFileSync(file,'changed');assert.notEqual(f.run('verify-inputs',f.output,f.revision).status,0);
 rmSync(file);assert.notEqual(f.run('verify-inputs',f.output,f.revision).status,0);
 writeFileSync(file,saved);
 writeFileSync(join(f.output,'unexpected.txt'),'not an input');assert.notEqual(f.run('verify-inputs',f.output,f.revision).status,0);
});

test('unrelated working edits do not block export but uncommitted selected inputs do',t=>{
 const f=fixture(t);
 writeFileSync(join(f.source,'unrelated-client.html'),'unrelated generated client edit');
 writeFileSync(join(f.source,'pyproject.toml'),'[project]\nversion = "99.0.0"\n');
 assert.equal(f.run('inputs',join(f.temporary,'unrelated')).status,0);
 writeFileSync(join(f.source,'amplifier_web/ai_services.json'),'uncommitted shared edit');
 const output=join(f.temporary,'refused');
 assert.notEqual(f.run('inputs',output).status,0);
 assert.equal(existsSync(output),false);
});

test('source and delivered input symlinks are refused instead of following unowned bytes',t=>{
 const f=fixture(t),name='packages/shell-sdk/index.js';
 const target=join(f.temporary,'external.js');writeFileSync(target,'external');
 rmSync(join(f.source,name));symlinkSync(target,join(f.source,name));
 git(f.source,'add',name);git(f.source,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Linked source fixture');
 const output=join(f.temporary,'refused');
 assert.match(f.run('inputs',output).stderr,/Expected a regular source input/);
 assert.equal(existsSync(output),false);
 rmSync(join(f.output,name));symlinkSync(target,join(f.output,name));
 assert.notEqual(f.run('verify-inputs',f.output,f.revision).status,0);
});

test('malformed or unsupported locks do not pass an empty verification',t=>{
 const f=fixture(t),path=join(f.output,'build-inputs.lock.json'),lock=JSON.parse(readFileSync(path));
 for(const change of [{schema:2},{kind:'another-format'},{inputs:{}},{inputs:[]},{source:{...lock.source,repository:'https://example.invalid'}}]){
  writeFileSync(path,JSON.stringify({...lock,...change}));
  assert.notEqual(f.run('verify-inputs',f.output,f.revision).status,0);
 }
});

function rejectsNonregularLock(f){
 const result=spawnSync(process.execPath,[join(f.source,helper),'verify-inputs',f.output,f.revision],
  {cwd:f.temporary,encoding:'utf8',timeout:2000,killSignal:'SIGKILL'});
 assert.equal(result.error,undefined,'Lock verification must exit without blocking on a read');
 assert.equal(result.status,1,result.stderr);
 assert.match(result.stderr,/Expected a regular build input lock/);
}

for(const linked of [false,true])test(`${linked?'Linked FIFO':'FIFO'} lock is rejected before a read can wait for a writer`,{skip:process.platform==='win32'},t=>{
 const f=fixture(t),path=join(f.output,'build-inputs.lock.json'),fifo=linked?join(f.temporary,'fifo'):path;
 rmSync(path);execFileSync('mkfifo',[fifo]);
 if(linked)symlinkSync(fifo,path);
 rejectsNonregularLock(f);
});

test('directory lock is rejected before reading its contents',t=>{
 const f=fixture(t),path=join(f.output,'build-inputs.lock.json');
 rmSync(path);mkdirSync(path);
 rejectsNonregularLock(f);
});
