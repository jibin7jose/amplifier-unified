import test from 'node:test';
import assert from 'node:assert/strict';
import {clipboardPaste,PASTED_TEXT_THRESHOLD} from '../src/clipboard-paste.js';
const clipboard=(text,files=[])=>({getData:()=>text,items:files.map(file=>({kind:'file',getAsFile:()=>file}))});
test('ordinary pastes remain native, including exactly the threshold',()=>{
 for(const text of ['Hi','x'.repeat(PASTED_TEXT_THRESHOLD)])assert.equal(clipboardPaste(clipboard(text)).handled,false);
});
test('large Unicode pastes become exact text files and coexist with images',async()=>{
 const text='A report 📋\r\n'.repeat(1000),image=new File(['image'],'image.png',{type:'image/png'});
 const result=clipboardPaste(clipboard(text,[image]));
 assert.equal(result.handled,true);assert.equal(result.text,'');assert.equal(result.files[0],image);
 assert.equal(result.files[1].name,'Pasted text.txt');assert.equal(result.files[1].attachmentSource,'clipboard-text');
 assert.equal(await result.files[1].text(),text);
 const mixed=clipboardPaste(clipboard('Image caption',[image]));assert.equal(mixed.text,'Image caption');assert.equal(mixed.files.length,1);
});
