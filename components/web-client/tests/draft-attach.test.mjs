import test from 'node:test';
import assert from 'node:assert/strict';
import {createDraftJournal} from '../src/draft-journal.js';
async function fixture(run){
 const oldFetch=globalThis.fetch,oldStorage=Object.getOwnPropertyDescriptor(globalThis,'sessionStorage'),values=new Map();
 const storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,value:storage});storage.setItem('amplifier.clientId','previous');
 const journal=createDraftJournal('previous',()=>storage);journal.stage('chat','unsent text');journal.stage(null,'');
 try{await run({storage,api:await import('../src/api.js?test='+crypto.randomUUID())})}finally{globalThis.fetch=oldFetch;if(oldStorage)Object.defineProperty(globalThis,'sessionStorage',oldStorage);else delete globalThis.sessionStorage}
}
test('attach restores unsaved drafts only into its new client before resolving',()=>fixture(async({storage,api})=>{
 const calls=[];globalThis.fetch=async(path,options)=>{calls.push({path,headers:options.headers,body:JSON.parse(options.body)});return new Response(JSON.stringify({accepted:true}))};
 await api.attachClient();await api.attachClient();assert.equal(calls.length,3);assert.equal(calls[0].body.resumeClientId,'previous');assert.equal(calls[0].body.clientId,api.clientId);assert.notEqual(api.clientId,'previous');
 assert.deepEqual(calls.slice(1).map(call=>call.body),[{action:'view.update',args:{sessionId:'chat',patch:{draft:'unsent text',draftDocument:null}}},{action:'view.update',args:{sessionId:null,patch:{draft:'',draftDocument:null}}}]);
 for(const call of calls)assert.equal(call.headers['X-Amplifier-Client'],api.clientId);
 assert.equal(storage.getItem('amplifier.pendingDrafts.v1'),undefined);assert.equal(storage.getItem('amplifier.clientId'),api.clientId);
}));
test('failed restoration retains text for a connection retry without resubmitting work',()=>fixture(async({storage,api})=>{
 let failed=false;const actions=[];globalThis.fetch=async(path,options)=>{const body=JSON.parse(options.body);if(path==='/api/actions'){actions.push(body.action);if(!failed){failed=true;throw Error('offline')}}return new Response(path==='/api/actions'?'{"accepted":true}':'{}')};
 await assert.rejects(api.attachClient(),/interrupted/);assert.equal(JSON.parse(storage.getItem('amplifier.pendingDrafts.v1')).rows[0].text,'unsent text');
 await api.attachClient();assert.deepEqual(actions,['view.update','view.update','view.update']);assert.equal(storage.getItem('amplifier.pendingDrafts.v1'),undefined);
}));
test('a missing conversation keeps its recovery copy without blocking other drafts',()=>fixture(async({storage,api})=>{
 globalThis.fetch=async(path,options)=>{const body=JSON.parse(options.body);return path==='/api/actions'&&body.args.sessionId==='chat'?new Response(JSON.stringify({error:'Select or create a conversation first.'}),{status:404}):new Response(path==='/api/actions'?'{"accepted":true}':'{}')};
 await api.attachClient();assert.deepEqual(JSON.parse(storage.getItem('amplifier.pendingDrafts.v1')).rows.map(({sessionId,text})=>[sessionId,text]),[['chat','unsent text']]);
}));
