import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,renameSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildIdentity,buildInputs} from '../scripts/build-identity.mjs';

// Independent fixture inputs include every current producer/import boundary.
const inputs=[
 'src/main.js','src/nested/view.js',
 'public/login.html','public/nested/style.css',
 'index.html','shell-validation.html',
 'package.json','package-lock.json','vite.config.js',
 'scripts/build-identity.mjs','scripts/sync-branding.mjs',
 '.build-inputs/amplifier_web/provider_sources.json','.build-inputs/amplifier_web/voice_options.json','.build-inputs/amplifier_web/ai_services.json',
 '.build-inputs/packages/shell-sdk/index.js','.build-inputs/packages/shell-sdk/package.json','.build-inputs/assets/UPSTREAM-LICENSE',
 '.build-inputs/assets/branding/favicons/favicon.ico','.build-inputs/assets/branding/favicons/favicon-32.png',
 '.build-inputs/assets/branding/favicons/apple-touch-icon.png','.build-inputs/assets/branding/pwa/pwa-192.png',
 '.build-inputs/assets/branding/pwa/pwa-512.png','.build-inputs/assets/branding/icons/amplifier-icon-128.png',
];
function write(root,name,bytes){mkdirSync(dirname(join(root,name)),{recursive:true});writeFileSync(join(root,name),bytes)}
function fixture(t){
 const root=mkdtempSync(join(tmpdir(),'frontend-identity-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const name of inputs)write(root,name,name);
 write(root,'package.json',JSON.stringify({version:'1.2.3'}));
 return root;
}

test('each authored input affects same-version identity, including sibling imports, public and branding',t=>{
 const root=fixture(t),before=buildIdentity(root);
 for(const name of inputs){
  const original=readFileSync(join(root,name));write(root,name,name==='package.json'?JSON.stringify({...JSON.parse(original),description:'changed'}):Buffer.concat([original,Buffer.from(' changed')]));
  const after=buildIdentity(root);assert.notEqual(after.id,before.id,name);assert.equal(after.version,before.version);
  write(root,name,original);assert.deepEqual(buildIdentity(root),before);
 }
});

test('identity ignores unrelated files and generated copies; client package owns version',t=>{
 const root=fixture(t),before=buildIdentity(root);
 for(const name of ['.build-inputs/amplifier_web/backend.py','docs/guide.md','tests/example.test.mjs',
  '.build-inputs/amplifier_web/static/build.json','.build-inputs/amplifier_web/static/assets/app.js',
  'public/sw.js','public/favicon.ico','public/branding/favicons/favicon.ico',
  'public/licenses/amplifier-assets.txt','node_modules/library/index.js']){
  write(root,name,'unrelated or generated');assert.deepEqual(buildIdentity(root),before,name);
 }
 write(root,'.build-inputs/pyproject.toml','[project]\nversion = "99.0.0"\n');
 assert.deepEqual(buildIdentity(root),before);
 write(root,'package.json',JSON.stringify({version:'1.2.4'}));
 assert.equal(buildIdentity(root).version,'1.2.4');assert.notEqual(buildIdentity(root).id,before.id);
});

test('relative names and explicit boundaries distinguish renames and content repartition',t=>{
 const root=fixture(t),before=buildIdentity(root);
 renameSync(join(root,'src/main.js'),join(root,'src/renamed.js'));
 assert.notEqual(buildIdentity(root).id,before.id);
 renameSync(join(root,'src/renamed.js'),join(root,'src/main.js'));
 write(root,'public/added.html','new page');assert.notEqual(buildIdentity(root).id,before.id);
 rmSync(join(root,'public/added.html'));assert.deepEqual(buildIdentity(root),before);
 write(root,'src/a.js','ab');write(root,'src/b.js','c');const first=buildIdentity(root);
 write(root,'src/a.js','a');write(root,'src/b.js','bc');assert.notEqual(buildIdentity(root).id,first.id);
});

test('same authored source is stable across separate checkout paths without Git metadata',t=>{
 assert.deepEqual(buildIdentity(fixture(t)),buildIdentity(fixture(t)));
});

// Check the committed selection against the real build boundary, not a mock lock.
test('shared lock supplies the actual client inputs without server release metadata',()=>{
 const root=fileURLToPath(new URL('../',import.meta.url));
 const lock=JSON.parse(readFileSync(join(root,'build-inputs.lock.json')));
 const consumed=buildInputs(root).filter(name=>name.startsWith('.build-inputs/')).map(name=>name.slice('.build-inputs/'.length));
 assert.deepEqual(Object.keys(lock.inputs).sort(),[...consumed,'LICENSE','packages/shell-sdk/LICENSE'].sort());
 assert.equal(Object.hasOwn(lock.inputs,'pyproject.toml'),false);
});
