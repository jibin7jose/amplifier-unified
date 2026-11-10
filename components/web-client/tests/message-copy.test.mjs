import test from 'node:test';
import assert from 'node:assert/strict';
import {messageTextForCopy} from '../src/message-copy.js';

test('copy uses an available message without another request',async()=>{
 const state={sessions:[{id:'chat',messages:[{id:'answer',text:'**Markdown**'}]}]};
 assert.equal(await messageTextForCopy(state,{sessionId:'chat',messageId:'answer'},()=>assert.fail('unexpected read')),'**Markdown**');
});

test('copy reads an explicitly addressed off-page chat without changing selection',async()=>{
 const state={selectedSessionId:'visible',sessions:[{id:'visible',messages:[]},{id:'off/page',messages:[]}]};
 let url;
 const text=await messageTextForCopy(state,{sessionId:'off/page',messageId:'answer'},async value=>{
  url=value;return {selectedSessionId:'visible',sessions:[{id:'off/page',messages:[{id:'answer',text:'# Saved answer'}]}]};
 });
 assert.equal(url,'/api/state?sessionId=off%2Fpage');assert.equal(text,'# Saved answer');
 assert.equal(state.selectedSessionId,'visible');assert.deepEqual(state.sessions[1].messages,[]);
});

test('missing scoped message reports failure instead of copying unrelated content',async()=>{
 await assert.rejects(messageTextForCopy({sessions:[]},{sessionId:'chat',messageId:'missing'},async()=>({sessions:[{id:'chat',messages:[{id:'other',text:'Not the requested entry'}]}]})),/Message no longer available/);
});

test('copy retains prose and appends only the host-supplied inert reference fallback',async()=>{
 const state={sessions:[{id:'chat',messages:[{id:'answer',text:'Original **prose**'}]}]};
 const reference='Chat reference: Saved destination — Workspace (host-local reference; open in Unified)';
 assert.equal(await messageTextForCopy(state,{sessionId:'chat',messageId:'answer',chatReferenceText:reference},
  ()=>assert.fail('unexpected read')),`Original **prose**\n\n${reference}`);
 assert.equal(state.sessions[0].messages[0].text,'Original **prose**');
});
