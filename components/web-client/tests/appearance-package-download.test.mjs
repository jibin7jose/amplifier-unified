import test from 'node:test';
import assert from 'node:assert/strict';
import {download} from '../src/api.js';
import {downloadBlobContent} from '../src/appearance-library.js';

function replace(target,key,value){
 const descriptor=Object.getOwnPropertyDescriptor(target,key);
 Object.defineProperty(target,key,{configurable:true,writable:true,value});
 return ()=>descriptor?Object.defineProperty(target,key,descriptor):delete target[key];
}

function captureDownload(filename,content,type){
 const anchor={href:'',download:'',clicked:false,click(){this.clicked=true}};
 let blob;
 const restoreDocument=replace(globalThis,'document',{createElement(name){assert.equal(name,'a');return anchor}});
 const restoreCreate=replace(URL,'createObjectURL',value=>{blob=value;return 'blob:appearance-package-test'});
 const restoreRevoke=replace(URL,'revokeObjectURL',()=>{});
 const restoreTimer=replace(globalThis,'setTimeout',()=>0);
 try{download(filename,content,type)}finally{restoreTimer();restoreRevoke();restoreCreate();restoreDocument()}
 return {anchor,blob};
}

test('ZIP download preserves exact bytes, MIME type, and server filename',async()=>{
 const bytes=Uint8Array.of(0x50,0x4b,0x03,0x04,0x00,0xff,0x41);
 const base64=btoa(String.fromCharCode(...bytes));
 const {anchor,blob}=captureDownload('lightline.zip',{base64},'application/zip');
 assert.equal(anchor.download,'lightline.zip');
 assert.equal(anchor.clicked,true);
 assert.equal(blob.type,'application/zip');
 assert.deepEqual(new Uint8Array(await blob.arrayBuffer()),bytes);
 assert.notEqual(await blob.text(),JSON.stringify({base64},null,2));
});

test('malformed binary payloads fail instead of downloading JSON',()=>{
 assert.throws(()=>download('broken.zip',{base64:'not base64'},'application/zip'),/binary download payload is invalid/);
 assert.throws(()=>download('broken.zip',{base64:42},'application/zip'),/binary download payload is invalid/);
 assert.throws(()=>download('broken.zip','plain text','application/zip'),/binary download payload is invalid/);
});

test('binary decoder preserves full-size and near-boundary ZIP payloads',()=>{
 for(const length of [3_999_998,3_999_999,4_000_000]){
  const bytes=Buffer.alloc(length);
  for(let index=0;index<length;index++)bytes[index]=index%251;
  const decoded=downloadBlobContent({base64:bytes.toString('base64')},'application/zip');
  assert.ok(decoded instanceof Uint8Array);
  assert.equal(decoded.byteLength,length);
  assert.deepEqual(Buffer.from(decoded),bytes);
 }
});

test('binary decoder rejects over-limit bytes and malformed full-size encodings',()=>{
 const valid=Buffer.alloc(4_000_000,0xa5).toString('base64');
 const extra=Buffer.alloc(4_000_001,0xa5).toString('base64');
 assert.equal(extra.length,valid.length,'Decoded-size fencing must be independent of encoded length.');
 for(const base64 of [
  extra,
  Buffer.alloc(4_000_003,0xa5).toString('base64'),
  valid.slice(0,-4)+'!!!!',
  valid.slice(0,-4)+' A==',
  valid.slice(0,4)+'='+valid.slice(5),
  valid.slice(0,-4)+'pR==', // Same byte as pQ==, but noncanonical padding bits.
  valid.slice(0,-1),
 ]){
  assert.throws(()=>downloadBlobContent({base64},'application/zip'),/binary download payload is invalid/);
 }
});

test('text and ordinary JSON downloads retain their original bytes',async()=>{
 const text='Résumé\r\n';
 const textDownload=captureDownload('notes.txt',text,'text/plain');
 assert.equal(await textDownload.blob.text(),text);
 const record={message:'café',count:2};
 const jsonDownload=captureDownload('report.json',record,'application/json');
 assert.equal(await jsonDownload.blob.text(),JSON.stringify(record,null,2));
 const base64Record={base64:'not a binary protocol at this MIME type'};
 const ordinaryDownload=captureDownload('record.json',base64Record,'application/json');
 assert.equal(await ordinaryDownload.blob.text(),JSON.stringify(base64Record,null,2));
});