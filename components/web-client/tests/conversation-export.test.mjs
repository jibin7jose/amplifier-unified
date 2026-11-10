import test from 'node:test';
import assert from 'node:assert/strict';
import {deliverConversationExport} from '../src/conversation-export.js';

for(const destination of ['clipboard','download'])test(`conversation export delivers exact snapshot via ${destination}`,async()=>{
 const snapshot={content:'# Chat\n\n```python\nprint("α")  \n```\n',filename:'chat.md',mimeType:'text/markdown'};
 const receipts=[],delivered=[];
 await deliverConversationExport({url:'/frozen/snapshot',destination,requestId:'one'},{
  request:async(url,options)=>url==='/frozen/snapshot'?snapshot:receipts.push(options.body),
  clipboard:{writeText:async text=>delivered.push(text)},download:(name,text,type)=>{assert.equal(name,'chat.md');assert.equal(type,'text/markdown');delivered.push(text)}
 });
 assert.deepEqual(delivered,[snapshot.content]);assert.equal(receipts[0].action,'session.exportResult');assert.equal(receipts[0].args.status,'ready');
});
test('clipboard failure reports failure without claiming success or a fallback download',async()=>{
 const receipts=[];
 await deliverConversationExport({url:'/frozen',destination:'clipboard',requestId:'two'},{request:async(url,options)=>url==='/frozen'?{content:'text'}:receipts.push(options.body),clipboard:{writeText:async()=>{throw Error('Permission denied')}},download:()=>assert.fail('Unexpected download')});
 assert.equal(receipts[0].args.status,'error');assert.match(receipts[0].args.message,/Permission denied/);
});
