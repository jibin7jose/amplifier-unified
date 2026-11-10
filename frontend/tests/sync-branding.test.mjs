import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFileSync,mkdtempSync,mkdirSync,readFileSync,readdirSync,rmSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';

// Normal test preparation supplies the exact selected client and its locked inputs.
const client=new URL('../.client/',import.meta.url);
const shared=JSON.parse(readFileSync(new URL('build-inputs.lock.json',client),'utf8'));
const inputs=[
 'scripts/sync-branding.mjs','scripts/build-identity.mjs','scripts/prepare-inputs.mjs',
 'src/service-worker.js','build-inputs.lock.json','.build-inputs/build-inputs.lock.json',
 ...Object.keys(shared.inputs).map(name=>'.build-inputs/'+name),
 ...['offline.html','app-pages.css','pwa.js','manifest.webmanifest','login.html','appearance-bootstrap.js'].map(name=>'public/'+name),
];
function write(root,name,bytes){mkdirSync(dirname(join(root,name)),{recursive:true});writeFileSync(join(root,name),bytes)}
function fixture(t){
 const root=mkdtempSync(join(tmpdir(),'branding-output-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const name of inputs){mkdirSync(dirname(join(root,name)),{recursive:true});copyFileSync(new URL(name,client),join(root,name))}
 write(root,'public/custom/note.txt','Authored public content');
 return root;
}
function snapshot(root){
 return Object.fromEntries(readdirSync(root,{recursive:true,withFileTypes:true})
  .filter(entry=>entry.isFile()).map(entry=>{
   const path=join(entry.parentPath,entry.name);return [path.slice(root.length+1),createHash('sha256').update(readFileSync(path)).digest('hex')];
  }).sort(([a],[b])=>a.localeCompare(b)));
}
function run(root){execFileSync(process.execPath,[join(root,'scripts/sync-branding.mjs')])}

test('reused branding staging matches a fresh build and preserves authored inputs',t=>{
 const fresh=fixture(t),reused=fixture(t);
 const originals=inputs.map(name=>readFileSync(join(reused,name)));
 run(fresh);run(reused);
 write(reused,'public/branding/retired/old-icon.png','obsolete');
 write(reused,'public/licenses/retired.txt','obsolete');
 write(reused,'public/branding/favicons/favicon.ico','stale current copy');
 run(reused);
 const expected=snapshot(join(fresh,'public'));
 assert.deepEqual(snapshot(join(reused,'public')),expected);
 run(reused);assert.deepEqual(snapshot(join(reused,'public')),expected);
 assert.deepEqual(inputs.map(name=>readFileSync(join(reused,name))),originals);
 assert.equal(readFileSync(join(reused,'public/custom/note.txt'),'utf8'),'Authored public content');
});

test('replacing generated directory links never changes their targets',t=>{
 const fresh=fixture(t),reused=fixture(t),outside=mkdtempSync(join(tmpdir(),'branding-link-target-'));
 t.after(()=>rmSync(outside,{recursive:true,force:true}));
 for(const name of ['branding','licenses']){
  write(outside,name+'/keep.txt','Separate owner');
  symlinkSync(join(outside,name),join(reused,'public',name),'junction');
 }
 const before=snapshot(outside);run(fresh);run(reused);
 assert.deepEqual(snapshot(outside),before);
 assert.deepEqual(snapshot(join(reused,'public')),snapshot(join(fresh,'public')));
});
