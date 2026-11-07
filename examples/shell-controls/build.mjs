import {build} from 'esbuild';
import {mkdir,copyFile,writeFile} from 'node:fs/promises';

await mkdir('dist',{recursive:true});
const result=await build({entryPoints:['index.js'],outfile:'dist/module.mjs',bundle:true,
 format:'esm',platform:'browser',target:'es2022',metafile:true});
await copyFile('manifest.json','dist/manifest.json');
await writeFile('dist/build-inputs.json',JSON.stringify(result.metafile,null,2)+'\n');
