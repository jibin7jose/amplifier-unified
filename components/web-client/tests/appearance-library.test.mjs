import test from 'node:test';
import assert from 'node:assert/strict';
import {appearanceImportArgs,appearanceImportFileArgs,appearanceMutationTarget} from '../src/appearance-library.js';

function uploadFile(name,content){
 const bytes=content instanceof Uint8Array?content:new TextEncoder().encode(content);
 return {name,size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};
}

test('mutation targets retain the exact read fingerprint and exclude CSS drafts',()=>{
 const read={id:'saved:ocean.amplifier.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',css:'original'};
 assert.deepEqual(appearanceMutationTarget(read),{id:read.id,expectedFingerprint:read.fingerprint});
 const refreshed={...read,fingerprint:'b'.repeat(64)};
 assert.notEqual(appearanceMutationTarget(read).expectedFingerprint,appearanceMutationTarget(refreshed).expectedFingerprint);
});

test('included entries and incomplete reads cannot become mutation targets',()=>{
 for(const entry of [null,{id:'builtin:aurora',source:'built-in',fingerprint:'a'.repeat(64)},{id:'saved:ocean.css',source:'saved'},{id:'saved:ocean.css',source:'saved',fingerprint:'invalid'}]){
  assert.throws(()=>appearanceMutationTarget(entry),/Read a saved appearance/);
 }
});

test('upload filename is only a fallback, never an override for embedded metadata',()=>{
 const css='/* amplifier-appearance: {"name":"Portable title"} */\n#amp-one{color:red}';
 assert.deepEqual(appearanceImportArgs('downloaded.amplifier.css',css),{css,fallbackName:'downloaded'});
 assert.deepEqual(appearanceImportArgs('.css',css),{css,fallbackName:'Imported appearance'});
 assert.deepEqual(appearanceImportArgs('lightline.zip',css),{css,fallbackName:'lightline'});
 assert.equal(appearanceImportArgs('x'.repeat(200)+'.CSS',css).fallbackName.length,100);
});

test('file import decodes UTF-8 CSS and chooses ZIP from content magic, not its filename',async()=>{
 const css='/* océan */\n#amp-one{color:red}';
 assert.deepEqual(await appearanceImportFileArgs(uploadFile('ocean.amplifier.css',css)),{css,fallbackName:'ocean'});
 const archive=Uint8Array.of(0x50,0x4b,0x03,0x04,0x00,0xff);
 const expectedBase64=btoa(String.fromCharCode(...archive));
 assert.deepEqual(await appearanceImportFileArgs(uploadFile('mystery.bundle',archive)),{packageBase64:expectedBase64,fallbackName:'mystery.bundle'});
 assert.deepEqual(await appearanceImportFileArgs(uploadFile('not-a-zip.zip',css)),{css,fallbackName:'not-a-zip'});
});

test('invalid UTF-8 CSS is rejected rather than silently replacing source bytes',async()=>{
 await assert.rejects(appearanceImportFileArgs(uploadFile('broken.css',Uint8Array.of(0x23,0xff))),/valid UTF-8/);
});

test('file import enforces exact CSS and ZIP byte limits without reading oversized packages',async()=>{
 const css=await appearanceImportFileArgs(uploadFile('at-limit.css',new Uint8Array(1_000_000).fill(0x20)));
 assert.equal(css.css.length,1_000_000);
 await assert.rejects(appearanceImportFileArgs(uploadFile('over-limit.css',new Uint8Array(1_000_001).fill(0x20))),/CSS appearances must be 1 MB or smaller/);
 const archive=new Uint8Array(4_000_000);archive.set([0x50,0x4b,0x03,0x04]);
 const packaged=await appearanceImportFileArgs(uploadFile('at-limit.zip',archive));
 assert.equal(packaged.packageBase64.length,5_333_336);
 const oversized={name:'over-limit.zip',size:4_000_001,arrayBuffer(){throw Error('Oversized input must be rejected before reading.')}};
 await assert.rejects(appearanceImportFileArgs(oversized),/up to 1 MB or a ZIP package up to 4 MB/);
});