import test from 'node:test';
import assert from 'node:assert/strict';
import {createDestinationHistory,destination,destinationCommand,destinationKey} from '../src/destination-history.js';

const STORAGE='amplifier.destinationHistory';
function memory(seed){
 const values=new Map(seed),writes=[];
 return {values,writes,getItem:key=>values.get(key)||null,setItem:(key,value)=>{writes.push(value);values.set(key,value)},removeItem:key=>values.delete(key)};
}
let harnessSequence=0;
function harness(store=memory()){
 const instance=++harnessSequence;let sequence=0;const notifications=[];
 const history=createDestinationHistory({storage:()=>store,key:()=>`visit-${instance}-${++sequence}`,changed:value=>notifications.push(value)});
 return {history,store,notifications};
}
function frame(name,revision=1,{scope='scope-a',surface='chat',tab='chats',host='host-a',title=name}={}){
 return {revision,client:{id:'browser-a',kind:'web',historyScope:scope,hostInstanceId:host},selectedSessionId:name,selectedWorkspaceId:'project',
  sessions:name?[{id:name,title,workspaceId:'project',messages:[{text:'PRIVATE BODY'}]}]:[],workspaces:[{id:'project',name:'Project'}],
  view:{workSurface:surface,workWorkspaceId:'project',workWorkspaceTab:tab,draft:'PRIVATE DRAFT'},canvas:{content:'PRIVATE CANVAS'}};
}
const receipt=revision=>({accepted:true,stateRevision:revision,hostInstanceId:'host-a'});
function trail(){const h=harness();h.history.observe(frame('a'));h.history.observe(frame('b',2));h.history.observe(frame('c',3));return h}
function back(history,revision=4){const key=history.snapshot().backKey;const target=history.begin(key);history.observe(frame(target.id,revision));assert.equal(history.finish(receipt(revision)),true);return target}

test('only typed, bounded destinations are persisted; same identity is not another visit',()=>{
 const {history,store}=harness();history.observe(frame('a'));history.observe(frame('a',2));
 assert.equal(history.snapshot().entries.length,1);
 for(let i=0;i<40;i++)history.observe(frame('chat-'+i,i+3,{title:'x'.repeat(500)}));
 const value=JSON.parse(store.getItem(STORAGE));assert.equal(value.entries.length,30);assert.equal(value.cursor,29);
 assert.equal(value.entries[0].id,'chat-10');assert.ok(value.entries.every(row=>row.label.length<=160));
 assert.doesNotMatch(store.getItem(STORAGE),/PRIVATE|messages|canvas|draft|content/);
});

test('browse destination differs from retained selected chat; tabs replace the current workspace visit',()=>{
 const {history}=harness();history.observe(frame('a'));
 history.observe(frame('a',2,{surface:'workspace'}));history.observe(frame('a',3,{surface:'workspace',tab:'files'}));
 assert.equal(history.snapshot().entries.length,2);assert.equal(history.snapshot().entries[1].tab,'files');
 history.observe(frame('a',4));assert.equal(history.snapshot().entries.length,3);
 assert.equal(history.snapshot().entries[2].kind,'chat');
 assert.deepEqual(destinationCommand(history.snapshot().entries[1]),['view.update',{patch:{workSurface:'workspace',workWorkspaceId:'project',workWorkspaceTab:'files',navExpanded:false}}]);
});

test('Shared Work workspace visit survives traversal and reload without private state',()=>{
 const {history,store}=harness();
 history.observe(frame('a'));
 history.observe(frame('a',2,{surface:'workspace',tab:'work'}));
 history.observe(frame('b',3));
 const entry=history.begin(history.snapshot().backKey);
 assert.equal(entry.tab,'work');
 assert.deepEqual(destinationCommand(entry),['view.update',{patch:{workSurface:'workspace',workWorkspaceId:'project',workWorkspaceTab:'work',navExpanded:false}}]);
 history.observe(frame('b',4,{surface:'workspace',tab:'work'}));
 assert.equal(history.finish(receipt(4)),true);
 const restored=harness(store).history;
 restored.observe(frame('b',5,{surface:'workspace',tab:'work'}),{baseline:true});
 assert.deepEqual(restored.snapshot().entries,history.snapshot().entries);
 assert.equal(restored.snapshot().entries[restored.snapshot().cursor].tab,'work');
 const next=restored.begin(restored.snapshot().forwardKey);
 assert.equal(next.kind,'chat');assert.equal(next.id,'b');
 restored.observe(frame('b',6));assert.equal(restored.finish(receipt(6)),true);
 assert.doesNotMatch(store.getItem(STORAGE),/PRIVATE|messages|canvas|draft|content/);
});

test('Home is one reloadable destination; filters and reading state do not become visits',()=>{
 const {history,store}=harness();history.observe(frame('a'));
 const home=frame('a',2,{surface:'home'});history.observe(home);
 const first=history.snapshot();
 for(let revision=3;revision<=5;revision++)history.observe({...home,revision,
  view:{...home.view,workHomeView:'focus',workHomeFilter:'working',workHomeQuery:'PRIVATE QUERY',workHomePages:{working:revision}}});
 assert.deepEqual(history.snapshot(),first);
 history.observe(frame('b',6));const attempt=history.begin(history.snapshot().backKey);
 assert.equal(attempt.kind,'home');
 assert.deepEqual(destinationCommand(attempt),['view.update',{patch:{workSurface:'home',navExpanded:false}}]);
 history.observe({...home,revision:7});assert.equal(history.finish(receipt(7)),true);
 assert.equal(history.snapshot().cursor,1);
 const reloaded=harness(store).history;reloaded.observe({...home,revision:8},{baseline:true});
 assert.deepEqual(reloaded.snapshot().entries,history.snapshot().entries);
 assert.equal(reloaded.snapshot().cursor,1);assert.equal(reloaded.snapshot().detached,false);
 assert.ok(reloaded.snapshot().forwardKey);assert.doesNotMatch(store.getItem(STORAGE),/PRIVATE QUERY|workHome/);
});

test('workspace browser destinations retain the canonical All workspaces label',()=>{
 const state=frame('a',2,{surface:'workspaces'}),{history}=harness();
 assert.deepEqual(destination(state),{kind:'workspaces',label:'All workspaces'});
 history.observe(state);
 assert.equal(history.snapshot().entries[0].label,'All workspaces');
 assert.deepEqual(destinationCommand(history.snapshot().entries[0]),['view.update',{patch:{workSurface:'workspaces',navExpanded:false}}]);
});

test('cursor commits only for accepted receipt plus matching host, revision and destination',()=>{
 for(const failure of ['rejected','old-revision','wrong-host','wrong-route','unknown']){
  const {history}=trail();const target=history.begin(history.snapshot().backKey);
  history.observe(frame(failure==='wrong-route'?'a':target.id,4));
  assert.equal(history.snapshot().cursor,2,'observed state alone must not move cursor');
  const result=failure==='unknown'?null:failure==='rejected'?{...receipt(4),accepted:false}:failure==='old-revision'?receipt(5):failure==='wrong-host'?{...receipt(4),hostInstanceId:'other'}:receipt(4);
  const outcome=history.finish(result);
  assert.notEqual(outcome,true,failure);assert.equal(history.snapshot().cursor,2);assert.equal(history.snapshot().entries.length,3);
  if(outcome===null)assert.equal(history.finish(null),false,'timeout explicitly ends an unconfirmed traversal');
 }
 const {history}=trail();back(history);assert.equal(history.snapshot().cursor,1);assert.equal(history.snapshot().entries.length,3);
 assert.equal(history.finish(receipt(4)),false,'duplicate receipt does not move again');
});

test('new successful destinations truncate Forward, revisits stay distinct',()=>{
 const {history}=trail();back(history);history.observe(frame('d',5));
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','d']);assert.equal(history.snapshot().forwardKey,null);
 history.observe(frame('a',6));assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','d','a']);
});

test('New chat is detached: Back returns to the current visit, never replays creating a draft',()=>{
 const {history}=trail();back(history);const before=history.snapshot();history.observe(frame(null,5));
 assert.equal(destination(frame(null)),null);assert.equal(history.snapshot().entries.length,3);assert.equal(history.snapshot().detached,true);
 assert.equal(history.snapshot().backKey,before.entries[1].key);assert.equal(history.snapshot().forwardKey,before.entries[2].key);
 const target=back(history,6);assert.equal(target.id,'b');assert.deepEqual(destinationCommand(target),['session.select',{id:'b'}]);
 assert.equal(history.snapshot().cursor,1);assert.equal(history.snapshot().entries.length,3);
 history.observe(frame(null,7));history.observe(frame('submitted-new-chat',8));
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','submitted-new-chat']);
});

test('reload restores only this scope, duplicated tabs evolve independent copies, reconnect does not push',()=>{
 const {history,store}=trail();back(history);
 const copy=memory(store.values),reloaded=harness(store).history,duplicated=harness(copy).history;
 reloaded.observe(frame('b',20),{baseline:true});duplicated.observe(frame('b',20),{baseline:true});
 assert.equal(reloaded.snapshot().cursor,1);assert.equal(reloaded.snapshot().entries.length,3);
 reloaded.observe(frame('b',1,{host:'restarted'}),{baseline:true});assert.equal(reloaded.snapshot().entries.length,3);
 duplicated.observe(frame('d',21));assert.deepEqual(reloaded.snapshot().entries.map(row=>row.id),['a','b','c']);
 assert.equal(new Set(duplicated.snapshot().entries.map(row=>row.key)).size,duplicated.snapshot().entries.length);
 assert.equal(back(duplicated,22).id,'b');
 const forward=duplicated.begin(duplicated.snapshot().forwardKey);
 assert.equal(forward.id,'d');duplicated.observe(frame('d',23));assert.equal(duplicated.finish(receipt(23)),true);
 const reloadedCopy=harness(copy).history;reloadedCopy.observe(frame('d',24),{baseline:true});
 assert.deepEqual(reloadedCopy.snapshot().entries,duplicated.snapshot().entries);
 assert.equal(reloadedCopy.snapshot().cursor,duplicated.snapshot().cursor);
 const other=harness(store).history;other.observe(frame('private-other',1,{scope:'scope-b'}));
 assert.deepEqual(other.snapshot().entries.map(row=>row.id),['private-other']);
});

test('authentication invalidation cannot be undone by old stream frames or repeated 401',()=>{
 const {history,store}=trail();history.invalidate();history.invalidate();history.observe(frame('c',50));
 assert.equal(history.snapshot().ready,false);assert.equal(store.getItem(STORAGE),null);
 history.observe(frame('new-account',1,{scope:'scope-b'}));assert.equal(history.snapshot().entries.length,1);
 history.observe(frame('c',51));assert.deepEqual(history.snapshot().entries.map(row=>row.id),['new-account']);
});

test('malformed or oversized stored history is discarded without executing stored content',()=>{
 for(const raw of ['not json','x'.repeat(65537),JSON.stringify({version:1,scope:'scope-a',cursor:0,entries:[{kind:'chat',id:'a',key:'same'},{kind:'chat',id:'b',key:'same'}]})]){
  const store=memory([[STORAGE,raw]]),{history}=harness(store);history.observe(frame('safe'));
  assert.deepEqual(history.snapshot().entries.map(row=>row.id),['safe']);
 }
});

test('storage failure is memory-only and visible; state-only progress neither writes nor notifies',()=>{
 const broken={getItem(){throw Error('blocked')},setItem(){throw Error('blocked')},removeItem(){throw Error('blocked')}};
 const volatile=harness(broken).history;volatile.observe(frame('a'));assert.equal(volatile.snapshot().memoryOnly,true);
 volatile.observe(frame('b',2));assert.equal(volatile.snapshot().entries.length,2);
 const {history,store,notifications}=trail();const writes=store.writes.length,changes=notifications.length;
 for(let i=0;i<50;i++)history.observe(frame('c',i+4));
 assert.equal(store.writes.length,writes);assert.equal(notifications.length,changes);
});

test('renames refresh known metadata only; absence from a bounded projection does not delete entries',()=>{
 const {history}=trail();history.observe(frame('c',4,{title:'Renamed'}));
 assert.equal(history.snapshot().entries.length,3);assert.equal(history.snapshot().entries[2].label,'Renamed');
 history.observe({...frame('c',5),sessions:[]});
 assert.equal(history.snapshot().entries[2].label,'Renamed');assert.equal(history.snapshot().entries[0].id,'a');
});

test('progress cannot evict traversal confirmation, and later observed navigation remains later',()=>{
 const {history}=trail();history.begin(history.snapshot().backKey);history.observe(frame('b',4));
 for(let i=0;i<40;i++)history.observe(frame('b',i+5));
 history.observe(frame('d',45));assert.equal(history.finish(receipt(4)),true);
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','d']);assert.equal(history.snapshot().cursor,2);
});

test('receipt and authoritative observation join in either order without appending a visit',async()=>{
 for(const receiptFirst of [true,false]){
  const {history}=trail(),attempt=history.begin(history.snapshot().backKey);
  if(receiptFirst)assert.equal(history.finish(receipt(4)),null);
  history.observe(frame('b',4));
  if(!receiptFirst)assert.equal(history.finish(receipt(4)),true);
  assert.equal(await attempt.confirmed,true);assert.equal(history.snapshot().pending,false);
  assert.equal(history.snapshot().cursor,1);assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','c']);
 }
});

test('suffix frames from an old host or lower revision cannot replace Forward',()=>{
 const {history}=trail();history.begin(history.snapshot().backKey);history.observe(frame('b',10));
 history.observe(frame('d',9));history.observe(frame('d',11,{host:'other'}));
 assert.equal(history.finish(receipt(10)),true);
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','c']);assert.equal(history.snapshot().cursor,1);
});

test('singleton routes strip arbitrary stored IDs and extra fields before persistence',()=>{
 const value={version:1,scope:'scope-a',cursor:0,entries:[{key:'old',kind:'chats',label:'Search chats',id:{message:'PRIVATE'},draft:'PRIVATE'}]};
 const store=memory([[STORAGE,JSON.stringify(value)]]),{history}=harness(store);
 history.observe(frame('hidden',1,{surface:'chats'}));
 assert.deepEqual(history.snapshot().entries,[{key:'old',kind:'chats',label:'Search chats'}]);
 assert.doesNotMatch(store.getItem(STORAGE),/PRIVATE|draft|message/);
});

test('a reconnect baseline can confirm traversal, but never append its own visit',async()=>{
 for(const baselineFirst of [true,false]){
  const {history}=trail(),attempt=history.begin(history.snapshot().backKey);
  if(baselineFirst)history.observe(frame('c',3),{baseline:true});
  else history.finish(receipt(4));
  history.observe(frame('b',4),{baseline:true});
  if(baselineFirst)assert.equal(history.finish(receipt(4)),true);
  assert.equal(await attempt.confirmed,true);
  assert.equal(history.snapshot().cursor,1);assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','c']);
 }
 const {history}=trail();history.begin(history.snapshot().backKey);
 history.observe(frame('b',4));history.observe(frame('d',5),{baseline:true});
 assert.equal(history.finish(receipt(4)),true);
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','c']);assert.equal(history.snapshot().detached,true);
});

test('scope-less errors and old-host receipts cannot add or confirm destinations',()=>{
 const {history}=trail(),unscoped=frame('d',4);delete unscoped.client.historyScope;
 history.observe(unscoped);assert.equal(history.snapshot().entries.length,3);
 history.begin(history.snapshot().backKey);history.observe(frame('b',4));
 history.observe(frame('b',1,{host:'restarted'}),{baseline:true});
 assert.equal(history.finish(receipt(4)),false);assert.equal(history.snapshot().cursor,2);
});

test('stream-first sign-in change is not invalidated again by its later HTTP response',()=>{
 const {history}=trail();
 history.observe(frame('new-account',1,{scope:'scope-b'}),{baseline:true});
 history.invalidate('scope-b');
 assert.equal(history.snapshot().ready,true);
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['new-account']);
 history.observe(frame('late-old-account',50));
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['new-account']);
 history.invalidate();history.observe(frame('new-account',2,{scope:'scope-b'}),{baseline:true});
 assert.equal(history.snapshot().ready,false);
});

test('Recent places deduplicates identities and follows confirmed traversal without changing steps',()=>{
 const {history}=trail();
 history.observe(frame('a',4));
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','c','a']);
 assert.deepEqual(history.snapshot().recent.map(row=>row.id),['a','c','b']);
 const before=history.snapshot().entries;
 back(history,5);
 assert.deepEqual(history.snapshot().recent.map(row=>row.id),['c','a','b']);
 assert.deepEqual(history.snapshot().entries,before);
 const forward=history.begin(history.snapshot().forwardKey);
 history.observe(frame(forward.id,6));
 assert.deepEqual(history.snapshot().recent.map(row=>row.id),['c','a','b'],'unconfirmed frames cannot reorder MRU');
 assert.equal(history.finish(receipt(6)),true);
 assert.deepEqual(history.snapshot().recent.map(row=>row.id),['a','c','b']);
 assert.deepEqual(history.snapshot().entries,before);
});

test('choosing a recent place is a new guarded visit, not a cursor jump',async()=>{
 const {history}=trail();back(history);
 const target=history.snapshot().recent.find(row=>row.id==='a');
 const attempt=history.beginRecent(destinationKey(target));
 history.observe(frame('a',5));
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','c']);
 assert.equal(history.finish(receipt(5)),true);assert.equal(await attempt.confirmed,true);
 assert.deepEqual(history.snapshot().entries.map(row=>row.id),['a','b','a']);
 assert.equal(history.snapshot().forwardKey,null);
 assert.deepEqual(history.snapshot().recent.map(row=>row.id),['a','b','c'],'places survive Forward branching');
});

test('recent-place failure, wrong host/revision/route or missing receipt keeps both projections',()=>{
 for(const failure of ['rejected','old-revision','wrong-host','wrong-route','unknown']){
  const {history}=trail(),before=history.snapshot();
  history.beginRecent(destinationKey(before.recent.find(row=>row.id==='a')));
  history.observe(frame(failure==='wrong-route'?'b':'a',4));
  const result=failure==='unknown'?null:failure==='rejected'?{...receipt(4),accepted:false}:failure==='old-revision'?receipt(5):failure==='wrong-host'?{...receipt(4),hostInstanceId:'other'}:receipt(4);
  const outcome=history.finish(result);
  if(outcome===null)history.finish(null);
  assert.notEqual(outcome,true,failure);
  assert.deepEqual(history.snapshot().entries,before.entries);
  assert.deepEqual(history.snapshot().recent,before.recent);
  assert.equal(history.snapshot().cursor,before.cursor);
 }
});

test('recent identity ignores names, refreshes renamed metadata without reordering, and remembers workspace tab',()=>{
 const {history}=harness();history.observe(frame('a',1,{title:'Same title'}));
 history.observe(frame('b',2,{title:'Same title'}));
 history.observe(frame('b',3,{surface:'workspace',tab:'files'}));
 history.observe(frame('b',4,{surface:'workspace',tab:'work'}));
 history.observe(frame('a',5,{title:'Same title'}));
 const before=history.snapshot().recent.map(destinationKey);
 history.observe({...frame('a',6,{title:'Renamed'}),sessions:[{id:'a',title:'Renamed',workspaceId:'project'},{id:'b',title:'Same title',workspaceId:'other'}],workspaces:[{id:'project',name:'Renamed project'},{id:'other',name:'Other'}]});
 assert.deepEqual(history.snapshot().recent.map(destinationKey),before);
 const recent=history.snapshot().recent;
 assert.equal(recent.length,3);assert.equal(recent[0].label,'Renamed');
 assert.equal(recent[1].tab,'work');assert.equal(recent[1].label,'Renamed project');
 assert.equal(recent[2].id,'b');assert.equal(recent[2].workspaceLabel,'Other');
 history.observe({...frame('a',7),sessions:[],workspaces:[]});
 assert.deepEqual(history.snapshot().recent,recent,'absence from bounded catalogs is not deletion');
});

test('MRU reload, cap and sign-in scope are independent of the visit cursor',()=>{
 const {history,store}=trail();back(history);
 const before=history.snapshot();
 const restored=harness(store).history;restored.observe(frame('b',5),{baseline:true});
 assert.deepEqual(restored.snapshot().recent,before.recent);
 assert.equal(restored.snapshot().cursor,1);
 for(let i=0;i<40;i++)restored.observe(frame('new-'+i,i+6));
 assert.equal(restored.snapshot().recent.length,30);
 assert.equal(restored.snapshot().recent[29].id,'new-10');
 restored.invalidate();assert.equal(store.getItem(STORAGE),null);assert.deepEqual(restored.snapshot().recent,[]);
 restored.observe(frame('new-account',1,{scope:'scope-b'}));
 assert.deepEqual(restored.snapshot().recent.map(row=>row.id),['new-account']);
});

test('legacy trail is retained and seeds bounded recent places; malformed optional recent data cannot erase it',()=>{
 for(const recent of [undefined,'invalid',[{kind:'chat',id:'unsafe',key:null}]]){
  const value={version:1,scope:'scope-a',cursor:1,entries:[
   {key:'a1',kind:'chat',id:'a',label:'Alpha'},
   {key:'b1',kind:'chat',id:'b',label:'Beta'},
   {key:'a2',kind:'chat',id:'a',label:'Alpha'}],...(recent===undefined?{}:{recent})};
  const {history}=harness(memory([[STORAGE,JSON.stringify(value)]]));
  history.observe({...frame('b'),sessions:[],workspaces:[]},{baseline:true});
  assert.deepEqual(history.snapshot().entries.map(row=>[row.key,row.id]),value.entries.map(row=>[row.key,row.id]));
  assert.equal(history.snapshot().cursor,1);
  assert.deepEqual(history.snapshot().recent.map(row=>row.id),['a','b']);
 }
});

test('queued recent selection retains the selected tab while an earlier browse updates the same place',async()=>{
 const {history}=harness();
 history.observe(frame('a',1,{surface:'workspace',tab:'files'}));history.observe(frame('a',2));
 const chosen=history.snapshot().recent.find(row=>row.kind==='workspace');
 history.observe(frame('a',3,{surface:'workspace',tab:'chats'}));
 assert.equal(history.snapshot().recent[0].tab,'chats');
 const attempt=history.beginRecent(destinationKey(chosen),chosen,'scope-a');
 assert.equal(attempt.tab,'files');
 history.observe(frame('a',4,{surface:'workspace',tab:'files'}));history.finish(receipt(4));
 assert.equal(await attempt.confirmed,true);
 assert.equal(history.snapshot().recent[0].tab,'files');
 assert.equal(history.beginRecent(destinationKey(chosen),chosen,'old-scope'),null);
});
