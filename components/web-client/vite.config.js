import {defineConfig} from 'vite';
import {copyFileSync,cpSync,existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {buildIdentity as identifyBuild} from './scripts/build-identity.mjs';
const require=createRequire(import.meta.url);
function packageFile(name,file){
 const directory=require.resolve.paths(name).find(path=>existsSync(join(path,name,'package.json')));
 if(!directory)throw Error('Missing build dependency: '+name);
 return join(directory,name,file);
}
const source=fileURLToPath(new URL('./src/unified.css',import.meta.url));
const destination=fileURLToPath(new URL('./dist/unified.amplifier.css',import.meta.url));
const buildIdentity=identifyBuild(fileURLToPath(new URL('./',import.meta.url)));
export default defineConfig({
 define:{__AMPLIFIER_BUILD__:JSON.stringify(buildIdentity)},

 base:'/',
 plugins:[{name:'bundle-shareable-skin',apply:'build',closeBundle(){
  writeFileSync(fileURLToPath(new URL('./dist/build.json',import.meta.url)),JSON.stringify(buildIdentity)+'\n');
  copyFileSync(source,destination);
  const vendor=fileURLToPath(new URL('./dist/vendor',import.meta.url));mkdirSync(vendor,{recursive:true});
  copyFileSync(packageFile('babylonjs','babylon.js'),`${vendor}/babylon.js`);
  for(const name of ['cmaps','standard_fonts','wasm'])cpSync(packageFile('pdfjs-dist',name),`${vendor}/pdfjs/${name}`,{recursive:true});
  const licenses=fileURLToPath(new URL('./dist/licenses',import.meta.url));mkdirSync(licenses,{recursive:true});
  copyFileSync(packageFile('babylonjs','license.md'),`${licenses}/babylonjs.txt`);
  copyFileSync(packageFile('babylonjs','NOTICE.md'),`${licenses}/babylonjs-notice.txt`);
  for(const [name,file] of [['@modelcontextprotocol/ext-apps','mcp-apps'],['@modelcontextprotocol/client','mcp-client'],['@modelcontextprotocol/core','mcp-core'],['zod','zod']])copyFileSync(packageFile(name,'LICENSE'),`${licenses}/${file}.txt`);
  for(const name of ['prosemirror-model','prosemirror-state','prosemirror-view','prosemirror-markdown','prosemirror-inputrules','prosemirror-keymap','prosemirror-commands','prosemirror-history','prosemirror-schema-list','prosemirror-transform','orderedmap','rope-sequence','w3c-keyname'])copyFileSync(packageFile(name,'LICENSE'),`${licenses}/${name}.txt`);
  copyFileSync(packageFile('pdfjs-dist','LICENSE'),`${licenses}/pdfjs.txt`);
  for(const name of ['mermaid','dompurify','highlight.js','katex'])copyFileSync(packageFile(name,'LICENSE'),`${licenses}/${name}.txt`);
 }}],
 build:{outDir:'./dist',emptyOutDir:true,rollupOptions:{input:{app:fileURLToPath(new URL('./index.html',import.meta.url)),shellValidation:fileURLToPath(new URL('./shell-validation.html',import.meta.url))}}},
 server:{proxy:{'/api':{target:'http://127.0.0.1:8765',ws:true}}},
});
