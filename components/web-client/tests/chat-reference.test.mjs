import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

// Exercise the actual production validator without importing React/DOM. The
// rendered component and click dispatcher are exercised by the browser journey.
const source=readFileSync(new URL('../src/chat-reference.jsx',import.meta.url),'utf8');
const validator=source.slice(source.indexOf('const referenceFields='),source.indexOf('function ChatReferenceCard('))
 .replace('export function validateChatReferenceMetadata','function validateChatReferenceMetadata');
const validate=runInNewContext(`${validator}\nvalidateChatReferenceMetadata`);
const item=()=>({id:'saved-output',sessionId:'source',messageId:'reply',version:1,revision:1,
 targetSessionId:'destination',title:'Investigation',originalTitle:'Investigation',workspace:'Example',archived:false,available:true});

test('chat reference metadata is a fixed bounded data shape',()=>{
 const input={items:[item()],overflow:false};
 assert.equal(validate(input,'source','reply'),input);
 assert.equal(validate({items:[],overflow:true},'source','reply').overflow,true);
 const untrusted={...item(),title:'<img src=x onerror=alert(1)>'};
 assert.equal(validate({items:[untrusted],overflow:false},'source','reply').items[0].title,untrusted.title);
 const renamed={...item(),title:'Current title',originalTitle:'<b>Original title</b>'};
 assert.equal(validate({items:[renamed],overflow:false},'source','reply').items[0].originalTitle,renamed.originalTitle);
 for(const originalTitle of [undefined,'','x'.repeat(201),'original\nname']){
  assert.throws(()=>validate({items:[{...item(),originalTitle}],overflow:false},'source','reply'),/Malformed chat reference metadata/);
 }
});

for(const [name,change] of [
 ['action',row=>{row.action='approval.respond'}],
 ['URL',row=>{row.url='javascript:alert(1)'}],
 ['foreign source',row=>{row.sessionId='foreign'}],
 ['foreign message',row=>{row.messageId='other'}],
 ['missing target',row=>{delete row.targetSessionId}],
 ['wrong version',row=>{row.version='1'}],
 ['invalid revision',row=>{row.revision=0}],
 ['unbounded label',row=>{row.title='x'.repeat(201)}],
 ['control character',row=>{row.workspace='one\ntwo'}],
 ['missing availability',row=>{delete row.available}],
])test(`chat reference rejects ${name}`,()=>{
 const row=item();change(row);
 assert.throws(()=>validate({items:[row],overflow:false},'source','reply'),/Malformed chat reference metadata/);
});

test('chat reference rejects duplicate, oversized and expanded envelopes',()=>{
 assert.throws(()=>validate({items:[item(),item()],overflow:false},'source','reply'),/duplicate/);
 assert.throws(()=>validate({items:Array.from({length:21},(_,n)=>({...item(),id:String(n)})),overflow:false},'source','reply'),/bounded/);
 assert.throws(()=>validate({items:[],overflow:false,action:'session.create'},'source','reply'),/bounded/);
});
