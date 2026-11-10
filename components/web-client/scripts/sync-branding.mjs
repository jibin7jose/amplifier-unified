// Copy the official assets verbatim; generate only the public offline cache version.
import {copyFileSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';
import {createHash} from 'node:crypto';
import {brandingFiles as files} from './build-identity.mjs';
import {verifyInputs} from './prepare-inputs.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),publicDir=join(root,'public');
verifyInputs(root,join(root,'.build-inputs'));
// These roots are wholly generated. Remove old files and links, never their targets.
for(const name of ['branding','licenses'])rmSync(join(publicDir,name),{recursive:true,force:true});
for(const name of files){const dest=join(publicDir,'branding',name);mkdirSync(dirname(dest),{recursive:true});copyFileSync(join(root,'.build-inputs/assets/branding',name),dest)}
copyFileSync(join(root,'.build-inputs/assets/branding/favicons/favicon.ico'),join(publicDir,'favicon.ico'));
mkdirSync(join(publicDir,'licenses'),{recursive:true});copyFileSync(join(root,'.build-inputs/assets/UPSTREAM-LICENSE'),join(publicDir,'licenses/amplifier-assets.txt'));
const source=readFileSync(join(root,'src/service-worker.js'),'utf8');
const digest=createHash('sha256').update(source);
for(const name of ['offline.html','app-pages.css','pwa.js','manifest.webmanifest','favicon.ico',...files.map(name=>'branding/'+name)])digest.update(readFileSync(join(publicDir,name)));
writeFileSync(join(publicDir,'sw.js'),source.replace('__PUBLIC_ASSET_VERSION__',digest.digest('hex').slice(0,16)));
