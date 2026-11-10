import test from 'node:test';
import assert from 'node:assert/strict';
import {chatDeepLink,createChatLinkReceiver} from '../src/chat-deep-link.js';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(url='https://host/?keep=yes#canvas'){
 const location=new URL(url),selected=[],states=[];let launch;
 const receiver=createChatLinkReceiver({location,
  history:{state:{private:'kept'},replaceState(state,_title,url){assert.deepEqual(state,{private:'kept'});location.href=url.href}},
  launchQueue:{setConsumer(fn){launch=fn}},onChange:state=>states.push(state)});
 return {location,selected,states,receiver,launch:targetURL=>launch({targetURL})};
}
test('only a bounded explicit chat target is accepted',()=>{
 assert.equal(chatDeepLink('?chat=session-123'),'session-123');
 assert.equal(chatDeepLink('?chat=%2Fodd%20id'),'/odd id');
 for(const query of ['?action=conversation.send&text=hello','?chat=','?chat=%00','?chat='+'a'.repeat(201)])assert.equal(chatDeepLink(query),null);
});
test('latest pre-boot launch wins; cold duplicates and foreign targets are ignored',async()=>{
 const h=harness('https://host/?chat=first&keep=yes#canvas');
 h.launch(h.location.href);h.launch('https://host/?chat=second');
 assert.deepEqual(h.selected,[]);
 const unbind=h.receiver.bind(async id=>{h.selected.push(id);return {accepted:true}});await tick();
 assert.deepEqual(h.selected,['second']);assert.equal(h.location.href,'https://host/?keep=yes#canvas');
 h.launch('https://evil/?chat=foreign');h.launch('https://host/other?chat=wrong-path');
 h.launch('https://host/?chat=third');await tick();assert.deepEqual(h.selected,['second','third']);
 unbind();h.launch('https://host/?chat=fourth');assert.equal(h.selected.length,2);
 h.receiver.bind(async id=>{h.selected.push(id);return {accepted:true}});await tick();assert.equal(h.selected.at(-1),'fourth');
});
test('failed warm target survives in URL, does not retry on rebind, and explicit retry recovers',async()=>{
 const h=harness();let fail=true;
 const select=async id=>{h.selected.push(id);if(fail)throw Error('Offline');return {accepted:true}};
 const unbind=h.receiver.bind(select);h.launch('https://host/?chat=exact');await tick();
 assert.equal(h.location.search,'?keep=yes&chat=exact');assert.equal(h.states.at(-1).status,'failed');
 unbind();h.receiver.bind(select);await tick();assert.deepEqual(h.selected,['exact']);
 fail=false;h.receiver.retry();await tick();assert.deepEqual(h.selected,['exact','exact']);
 assert.equal(h.location.href,'https://host/?keep=yes#canvas');assert.equal(h.states.at(-1),null);
});
test('missing and forbidden targets remain retryable without looping; dismissal clears intent',async()=>{
 for(const status of [403,404]){
  const h=harness();h.receiver.bind(async id=>{h.selected.push(id);throw Object.assign(Error('Unavailable'),{status})});
  h.launch('https://host/?chat=missing');await tick();await tick();
  assert.equal(h.selected.length,1);assert.equal(h.location.searchParams.get('chat'),'missing');
  h.receiver.dismiss();h.receiver.retry();await tick();assert.equal(h.selected.length,1);assert.equal(h.location.search,'?keep=yes');
 }
});
test('rejected selection keeps the cold target and its reason',async()=>{
 const h=harness('https://host/?chat=missing');
 h.receiver.bind(async()=>({accepted:false,error:'Settings changes were kept.'}));await tick();
 assert.equal(h.location.search,'?chat=missing');assert.equal(h.states.at(-1).error.message,'Settings changes were kept.');
});
test('new user navigation supersedes an in-flight failure without reporting or replaying it',async()=>{
 const h=harness();let reject,isCurrent;
 h.receiver.bind((id,current)=>{isCurrent=current;h.selected.push(id);return new Promise((_resolve,no)=>{reject=no})});
 h.launch('https://host/?chat=old');assert.equal(isCurrent(),true);h.receiver.dismiss();assert.equal(isCurrent(),false);reject(Error('Late failure'));await tick();
 h.receiver.retry();await tick();assert.deepEqual(h.selected,['old']);assert.equal(h.states.at(-1),null);assert.equal(h.location.search,'?keep=yes');
});
test('a newer launch survives an older success and is selected next',async()=>{
 const h=harness();let complete;
 h.receiver.bind(id=>{h.selected.push(id);return id==='old'?new Promise(resolve=>{complete=resolve}):Promise.resolve({accepted:true})});
 h.launch('https://host/?chat=old');h.launch('https://host/?chat=new');
 assert.equal(h.location.searchParams.get('chat'),'new');complete({accepted:true});await tick();
 assert.deepEqual(h.selected,['old','new']);assert.equal(h.location.search,'?keep=yes');
});
