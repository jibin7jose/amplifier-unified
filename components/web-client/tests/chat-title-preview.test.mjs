import test from 'node:test';
import assert from 'node:assert/strict';
import {createTitlePreviewCache,needsTitlePreview,titlePreviewKey,untitledLabel} from '../src/chat-title-preview.js';
const chat={id:'a',title:'Conversation deadbeef',workspace:'/one'};
test('only machine fallback titles get previews; explicit choices win',()=>{
 assert.equal(needsTitlePreview(chat),true);
 for(const patch of [{title:'My chat'},{titleSource:'manual'},{nativeNameSource:'generated'},{titleSource:'generated'}])assert.equal(needsTitlePreview({...chat,...patch}),false);
 assert.equal(needsTitlePreview(null),false);
 assert.equal(untitledLabel(chat),'Untitled chat');
 assert.match(untitledLabel({...chat,createdAt:1791399156}),/^Untitled chat · /);
 assert.notEqual(titlePreviewKey(chat),titlePreviewKey({...chat,workspace:'/two'}));
});
test('duplicate visible rows share requests; slow previews are concurrency bounded',async()=>{
 let calls=0,active=0,max=0;const releases=[];
 const preview=createTitlePreviewCache(async chat=>{calls++;active++;max=Math.max(max,active);await new Promise(resolve=>releases.push(resolve));active--;return chat.id},{concurrency:2});
 const first=preview(chat);assert.equal(preview(chat),first);
 const pending=[first,...[1,2,3,4].map(i=>preview({...chat,id:String(i)}))];
 await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,2);
 while(releases.length){releases.splice(0).forEach(resolve=>resolve());await new Promise(resolve=>setImmediate(resolve))}
 assert.deepEqual(await Promise.all(pending),['a','1','2','3','4']);assert.equal(max,2);assert.equal(calls,5);
 assert.equal(await preview(chat),'a');assert.equal(calls,5);
});
test('changed titles invalidate previews; failed reads leave a quiet fallback',async()=>{
 let calls=0;const preview=createTitlePreviewCache(async()=>{calls++;throw Error('unavailable')});
 assert.equal(await preview(chat),null);assert.equal(await preview(chat),null);assert.equal(calls,1);
 await preview({...chat,title:'Conversation abcdef12'});assert.equal(calls,2);
});
