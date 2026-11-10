import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';

test('closing a development server leaves packaged pages and build identity unchanged',async()=>{
 const paths=['login.html','build.json','unified.amplifier.css'].map(name=>fileURLToPath(new URL('../../amplifier_web/static/'+name,import.meta.url)));
 const snapshot=async()=>Promise.all(paths.map(async path=>({bytes:await readFile(path),mtime:(await stat(path,{bigint:true})).mtimeNs})));
 const before=await snapshot();
 const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
 await server.close();
 assert.deepEqual(await snapshot(),before,'Development server shutdown must not write production assets');
});
