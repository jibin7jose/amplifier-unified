import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir,copyFile} from 'node:fs/promises';

const root=fileURLToPath(new URL('../../',import.meta.url));
const examples=new Set(['shell-reader','shell-controls','shell-navigator']);

export async function buildShellExample(name){
 if(!examples.has(name))throw new Error('Unknown shell example');
 const source=root+'examples/'+name;
 await mkdir(source+'/dist',{recursive:true});
 await build({entryPoints:[source+'/index.js'],outfile:source+'/dist/module.mjs',bundle:true,format:'esm',platform:'browser',target:'es2022',
  alias:{'@amplifier/shell-sdk':root+'packages/shell-sdk'}});
 await copyFile(source+'/manifest.json',source+'/dist/manifest.json');
}
