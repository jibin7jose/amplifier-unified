// Build the real example from an installed tarball outside the source checkout.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {copyFile,mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root=fileURLToPath(new URL('../',import.meta.url));
const output=resolve(process.argv[2]||join(root,'output/shell-sdk-proof'));
if(process.platform==='win32')throw new Error('This qualification helper runs on Linux/macOS. Use npm run build in the standalone example on Windows.');
const scratch=await mkdtemp(join(tmpdir(),'amplifier-shell-sdk-'));
const run=(args,cwd)=>execFileSync('npm',args,{cwd,encoding:'utf8',stdio:['ignore','pipe','inherit']});
try{
 const [packed]=JSON.parse(run(['pack','--json','--pack-destination',scratch],join(root,'packages/shell-sdk')));
 assert.deepEqual(packed.files.map(row=>row.path).sort(),['LICENSE','README.md','index.d.ts','index.js','package.json']);
 const archive=join(scratch,packed.filename),consumer=join(scratch,'consumer');
 await mkdir(consumer);
 for(const name of ['index.js','manifest.json','package.json','build.mjs','README.md'])
  await copyFile(join(root,'examples/shell-controls',name),join(consumer,name));
 // Explicit artifact installation also replaces the not-yet-published registry reference.
 run(['install',archive,'--save-exact','--no-audit','--no-fund'],consumer);
 run(['run','build'],consumer);
 const meta=JSON.parse(await readFile(join(consumer,'dist/build-inputs.json'),'utf8'));
 assert.deepEqual(Object.keys(meta.inputs).sort(),['index.js','node_modules/@amplifier/shell-sdk/index.js']);
 assert.ok(Object.values(meta.outputs).every(item=>item.imports.length===0),'The artifact must be self-contained, including its SDK');
 const manifest=JSON.parse(await readFile(join(consumer,'dist/manifest.json'),'utf8'));
 assert.equal(manifest.apiVersion,'1.0');
 const hash=async path=>createHash('sha256').update(await readFile(path)).digest('hex');
 const receipt={package:packed.name,version:packed.version,files:packed.files.map(({path,size})=>({path,size})),
  tarball:packed.filename,tarballSha256:await hash(archive),moduleSha256:await hash(join(consumer,'dist/module.mjs')),
  node:process.version,npm:run(['--version'],consumer).trim(),inputs:Object.keys(meta.inputs),
  apiVersion:manifest.apiVersion,standaloneBuild:true,hostAcceptance:'Run shell-components-browser with AMPLIFIER_SHELL_COMPONENT_PACKAGE pointing here'};
 await mkdir(output,{recursive:true});
 for(const name of ['module.mjs','manifest.json','build-inputs.json'])await copyFile(join(consumer,'dist',name),join(output,name));
 await copyFile(archive,join(output,packed.filename));
 await writeFile(join(output,'qualification.json'),JSON.stringify(receipt,null,2)+'\n');
 console.log(JSON.stringify(receipt,null,2));
}finally{
 await rm(scratch,{recursive:true,force:true});
}
