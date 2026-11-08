import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFileSync,mkdtempSync,mkdirSync,readFileSync,readdirSync,rmSync,symlinkSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import {brandingFiles} from '../scripts/build-identity.mjs';

const inputs=[
 'frontend/scripts/sync-branding.mjs','frontend/scripts/build-identity.mjs',
 'frontend/src/service-worker.js','assets/UPSTREAM-LICENSE',
 ...brandingFiles.map(name=>'assets/branding/'+name),
 ...['offline.html','app-pages.css','pwa.js','manifest.webmanifest','login.html','appearance-bootstrap.js'].map(name=>'frontend/public/'+name),
];
function write(root,name,bytes){mkdirSync(dirname(join(root,name)),{recursive:true});writeFileSync(join(root,name),bytes)}
function fixture(t){
 const root=mkdtempSync(join(tmpdir(),'branding-output-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const name of inputs){mkdirSync(dirname(join(root,name)),{recursive:true});copyFileSync(new URL('../../'+name,import.meta.url),join(root,name))}
 write(root,'frontend/public/custom/note.txt','Authored public content');
 return root;
}
function snapshot(root){
 return Object.fromEntries(readdirSync(root,{recursive:true,withFileTypes:true})
  .filter(entry=>entry.isFile()).map(entry=>{
   const path=join(entry.parentPath,entry.name);return [path.slice(root.length+1),createHash('sha256').update(readFileSync(path)).digest('hex')];
  }).sort(([a],[b])=>a.localeCompare(b)));
}
function run(root){execFileSync(process.execPath,[join(root,'frontend/scripts/sync-branding.mjs')])}

test('reused branding staging matches a fresh build and preserves authored inputs',t=>{
 const fresh=fixture(t),reused=fixture(t);
 const originals=inputs.map(name=>readFileSync(join(reused,name)));
 run(fresh);run(reused);
 write(reused,'frontend/public/branding/retired/old-icon.png','obsolete');
 write(reused,'frontend/public/licenses/retired.txt','obsolete');
 write(reused,'frontend/public/branding/favicons/favicon.ico','stale current copy');
 run(reused);
 const expected=snapshot(join(fresh,'frontend/public'));
 assert.deepEqual(snapshot(join(reused,'frontend/public')),expected);
 run(reused);assert.deepEqual(snapshot(join(reused,'frontend/public')),expected);
 assert.deepEqual(inputs.map(name=>readFileSync(join(reused,name))),originals);
 assert.equal(readFileSync(join(reused,'frontend/public/custom/note.txt'),'utf8'),'Authored public content');
});

test('replacing generated directory links never changes their targets',t=>{
 const fresh=fixture(t),reused=fixture(t),outside=mkdtempSync(join(tmpdir(),'branding-link-target-'));
 t.after(()=>rmSync(outside,{recursive:true,force:true}));
 for(const name of ['branding','licenses']){
  write(outside,name+'/keep.txt','Separate owner');
  symlinkSync(join(outside,name),join(reused,'frontend/public',name),'junction');
 }
 const before=snapshot(outside);run(fresh);run(reused);
 assert.deepEqual(snapshot(outside),before);
 assert.deepEqual(snapshot(join(reused,'frontend/public')),snapshot(join(fresh,'frontend/public')));
});
