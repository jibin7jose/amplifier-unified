import {test} from 'node:test';
import assert from 'node:assert/strict';
import {imageLayout,imageAttachment} from '../src/attachment-view.js';
import {isFileDrag,dropFiles} from '../src/chat-file-drop.js';

test('fit contains every shape and never upscales natural CSS pixels',()=>{
 for(const image of [[2400,1500],[700,2400],[2800,600],[28,16]])for(const viewport of [[1200,700],[358,600],[288,300]]){
  const g=imageLayout(image,viewport);assert.ok(g.scale<=1);assert.ok(g.width<=viewport[0]-32+.01);assert.ok(g.height<=viewport[1]-32+.01);
  assert.ok(Math.abs(g.width/g.height-image[0]/image[1])<1e-12,'Aspect ratio is preserved within floating-point arithmetic');
 }
 assert.equal(imageLayout([28,16],[358,600]).scale,1);
});
test('100% is natural size; manual zoom clamps and invalid geometry stays unavailable',()=>{
 assert.equal(imageLayout([2400,1500],[358,600],1).width,2400);
 assert.equal(imageLayout([2400,1500],[358,600],100).scale,8);
 assert.equal(imageLayout([2400,1500],[358,600],0).scale,imageLayout([2400,1500],[358,600]).fit);
 assert.equal(imageLayout([0,10],[300,300]),null);
 assert.equal(imageLayout([10,10],[0,300]),null);
});
test('only existing raster originals enter viewer; SVG and remote URLs do not',()=>{
 const url='/api/attachments/'+'a'.repeat(32);
 for(const mime of ['image/png','image/jpeg','image/gif','image/webp'])assert.equal(imageAttachment({mime,url}),true);
 for(const file of [{mime:'image/svg+xml',url},{mime:'image/png',url:'https://example.invalid/image.png'},{mime:'image/png',url:url+'?x'}])assert.equal(imageAttachment(file),false);
});
test('file drop preserves order and separate same-name items but ignores text and links',()=>{
 assert.equal(isFileDrag({types:['text/plain','text/uri-list']}),false);
 const files=[{name:'same.png'},{name:'same.png'}];
 assert.deepEqual(dropFiles({types:['Files'],items:files.map(file=>({kind:'file',getAsFile:()=>file}))}),files);
 assert.deepEqual(dropFiles({types:['text/plain'],files:[]}),[]);
});
test('directories and unreadable entries reject explicitly, not recursively or silently',()=>{
 assert.throws(()=>dropFiles({types:['Files'],items:[{kind:'file',webkitGetAsEntry:()=>({isDirectory:true})}]}),/folders/);
 assert.throws(()=>dropFiles({types:['Files'],items:[{kind:'file',getAsFile:()=>null}]}),/read/);
});
