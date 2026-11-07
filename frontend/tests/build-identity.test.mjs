import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,renameSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import {buildIdentity} from '../scripts/build-identity.mjs';

// Independent fixture inputs include every current producer/import boundary.
const inputs=[
 'frontend/src/main.js','frontend/src/nested/view.js',
 'frontend/public/login.html','frontend/public/nested/style.css',
 'frontend/index.html','frontend/shell-validation.html',
 'frontend/package.json','frontend/package-lock.json','frontend/vite.config.js',
 'frontend/scripts/build-identity.mjs','frontend/scripts/sync-branding.mjs',
 'amplifier_web/provider_sources.json','amplifier_web/voice_options.json','amplifier_web/ai_services.json',
 'packages/shell-sdk/index.js','packages/shell-sdk/package.json','assets/UPSTREAM-LICENSE',
 'assets/branding/favicons/favicon.ico','assets/branding/favicons/favicon-32.png',
 'assets/branding/favicons/apple-touch-icon.png','assets/branding/pwa/pwa-192.png',
 'assets/branding/pwa/pwa-512.png','assets/branding/icons/amplifier-icon-128.png',
];
function write(root,name,bytes){mkdirSync(dirname(join(root,name)),{recursive:true});writeFileSync(join(root,name),bytes)}
function fixture(t){
 const root=mkdtempSync(join(tmpdir(),'frontend-identity-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const name of inputs)write(root,name,name);
 write(root,'pyproject.toml','[project]\nversion = "1.2.3"\ndescription = "fixture"\n');
 return root;
}

test('each authored input affects same-version identity, including sibling imports, public and branding',t=>{
 const root=fixture(t),before=buildIdentity(root);
 for(const name of inputs){
  const original=readFileSync(join(root,name));write(root,name,Buffer.concat([original,Buffer.from(' changed')]));
  const after=buildIdentity(root);assert.notEqual(after.id,before.id,name);assert.equal(after.version,before.version);
  write(root,name,original);assert.deepEqual(buildIdentity(root),before);
 }
});

test('identity ignores backend, docs, tests and generated copies; version remains separate',t=>{
 const root=fixture(t),before=buildIdentity(root);
 for(const name of ['amplifier_web/backend.py','docs/guide.md','frontend/tests/example.test.mjs',
  'amplifier_web/static/build.json','amplifier_web/static/assets/app.js',
  'frontend/public/sw.js','frontend/public/favicon.ico','frontend/public/branding/favicons/favicon.ico',
  'frontend/public/licenses/amplifier-assets.txt','frontend/node_modules/library/index.js']){
  write(root,name,'unrelated or generated');assert.deepEqual(buildIdentity(root),before,name);
 }
 write(root,'pyproject.toml','[project]\nversion = "1.2.3"\ndescription = "backend-only edit"\n');
 assert.deepEqual(buildIdentity(root),before);
 write(root,'pyproject.toml','[project]\nversion = "1.2.4"\n');
 assert.deepEqual(buildIdentity(root),{...before,version:'1.2.4'});
});

test('relative names and explicit boundaries distinguish renames and content repartition',t=>{
 const root=fixture(t),before=buildIdentity(root);
 renameSync(join(root,'frontend/src/main.js'),join(root,'frontend/src/renamed.js'));
 assert.notEqual(buildIdentity(root).id,before.id);
 renameSync(join(root,'frontend/src/renamed.js'),join(root,'frontend/src/main.js'));
 write(root,'frontend/public/added.html','new page');assert.notEqual(buildIdentity(root).id,before.id);
 rmSync(join(root,'frontend/public/added.html'));assert.deepEqual(buildIdentity(root),before);
 write(root,'frontend/src/a.js','ab');write(root,'frontend/src/b.js','c');const first=buildIdentity(root);
 write(root,'frontend/src/a.js','a');write(root,'frontend/src/b.js','bc');assert.notEqual(buildIdentity(root).id,first.id);
});

test('same authored source is stable across separate checkout paths without Git metadata',t=>{
 assert.deepEqual(buildIdentity(fixture(t)),buildIdentity(fixture(t)));
});
