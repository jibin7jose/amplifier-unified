import test from 'node:test';
import assert from 'node:assert/strict';
import {createDraftJournal} from '../src/draft-journal.js';
const storage=()=>{const data=new Map();return {getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key),copy(){const target=storage();for(const [key,value] of data)target.setItem(key,value);return target}}};
test('reload keeps unacknowledged text and empty edits for each chat',()=>{
 const saved=storage(),old=createDraftJournal('old',()=>saved);old.stage('chat-a','draft A');old.stage('chat-b','');old.stage(null,'new chat');
 const next=createDraftJournal('new',()=>saved);next.resume('old');assert.deepEqual(next.entries().map(({sessionId,text})=>[sessionId,text]),[['chat-a','draft A'],['chat-b',''],[null,'new chat']]);
 const again=createDraftJournal('again',()=>saved);again.resume('new');assert.equal(again.entries().length,3);
});
test('late acknowledgements cannot erase newer text or a send clear',()=>{
 const saved=storage(),drafts=createDraftJournal('client',()=>saved),old=drafts.stage('a','old'),newer=drafts.stage('a','new');drafts.acknowledge(old);assert.equal(drafts.entries()[0].text,'new');
 const cleared=drafts.stage('a','');drafts.acknowledge(newer);assert.equal(drafts.entries()[0].text,'');drafts.acknowledge(cleared);assert.deepEqual(drafts.entries(),[]);
 const next=createDraftJournal('next',()=>saved);next.resume('client');assert.deepEqual(next.entries(),[]);
});
test('creating a conversation moves only the matching pre-chat edit',()=>{
 const saved=storage(),drafts=createDraftJournal('client',()=>saved),old=drafts.stage(null,'first'),newer=drafts.stage(null,'second');drafts.bind(old,'a');assert.equal(drafts.entries()[0].sessionId,null);drafts.bind(newer,'a');assert.equal(drafts.entries()[0].sessionId,'a');drafts.acknowledge(newer);assert.deepEqual(drafts.entries(),[]);
});
test('duplicated tabs recover independent copies and unrelated identities cannot recover drafts',()=>{
 const saved=storage(),first=createDraftJournal('first',()=>saved);first.stage('a','original');const duplicate= saved.copy(),second=createDraftJournal('second',()=>duplicate);second.resume('first');second.stage('a','second edit');assert.equal(first.entries()[0].text,'original');
 const unrelated=createDraftJournal('unrelated',()=>saved);unrelated.resume('wrong');assert.deepEqual(unrelated.entries(),[]);
});
test('unavailable storage does not prevent typing or keeping the in-memory draft',()=>{
 const drafts=createDraftJournal('client',()=>{throw Error('Storage disabled')});drafts.resume('old');assert.equal(drafts.stage('a','retain').persisted,false);assert.equal(drafts.entries()[0].text,'retain');
});
