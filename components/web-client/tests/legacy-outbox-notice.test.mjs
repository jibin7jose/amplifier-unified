// Presentation-only unit checks. Actual host delivery and browser form events
// remain with their maintained integration/browser owners.
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {LegacyOutboxNotice}=await server.ssrLoadModule('/src/legacy-outbox-notice.jsx');
const originalActEnvironment=Object.getOwnPropertyDescriptor(globalThis,'IS_REACT_ACT_ENVIRONMENT');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(async()=>{
 await server.close();
 if(originalActEnvironment)Object.defineProperty(globalThis,'IS_REACT_ACT_ENVIRONMENT',originalActEnvironment);
 else delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

const noticeKey='amplifier.legacyOutboxNotice.v1';
const outboxKey='amplifier.messageOutbox.v1',draftKey='amplifier.pendingDrafts.v1';
const payloads=['PAYLOAD-message-body','PAYLOAD-attachment-name','PAYLOAD-attachment-id',
 'PAYLOAD-attachment-path','PAYLOAD-reply-text','PAYLOAD-error','PAYLOAD-draft-body'];
const protectedValues=new Map([
 [outboxKey,JSON.stringify({text:payloads[0],attachments:[payloads[1]]})],
 [draftKey,JSON.stringify({rows:[{sessionId:'private-draft-chat',text:payloads[6]}]})],
]);
const roots=new Set(),inputs=[];
let originalGlobals,values,reads,writes,removals,localAccess,failRead,failWrite;
test.beforeEach(()=>{
 originalGlobals=new Map(['window','sessionStorage','localStorage'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 values=new Map(protectedValues);reads=[];writes=[];removals=[];localAccess=[];failRead=false;failWrite=false;
 const storage={
  getItem(key){reads.push(key);if(failRead)throw Error('PRIVATE storage read exception');return values.get(key)??null},
  setItem(key,value){writes.push([key,String(value)]);if(failWrite)throw Error('PRIVATE storage write exception');values.set(key,String(value))},
  removeItem(key){removals.push(key);values.delete(key)},
 };
 const localStorage={
  getItem(key){localAccess.push(['getItem',key]);assert.fail('Notice preferences must remain in this tab')},
  setItem(key){localAccess.push(['setItem',key]);assert.fail('Do not store tab preferences in localStorage')},
  removeItem(key){localAccess.push(['removeItem',key]);assert.fail('Do not remove localStorage values')},
 };
 for(const [key,value] of Object.entries({sessionStorage:storage,localStorage,window:{sessionStorage:storage,localStorage}}))
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
});
test.afterEach(async()=>{
 try{
  await act(async()=>{for(const root of roots)root.unmount();roots.clear()});
  for(const [rows,before] of inputs)assert.equal(JSON.stringify(rows),before,'Notice cannot mutate its frozen outbox input');
  for(const [key,value] of protectedValues)assert.equal(values.get(key),value,'Outbox and draft bytes must be unchanged');
  assert.ok(reads.every(key=>key===noticeKey),'Do not read message/draft journals for presentation preferences');
  assert.ok(writes.every(([key])=>key===noticeKey),'Only the notice preference key may be written');
  assert.deepEqual(removals,[],'Hiding a notice is not deletion of a retained copy or draft');
  assert.deepEqual(localAccess,[],'Even caught localStorage access violates the tab-only preference boundary');
  for(const [,value] of writes)for(const payload of payloads)assert.ok(!value.includes(payload),'Never copy payload into preference metadata');
 }finally{
  inputs.length=0;
  for(const [key,descriptor] of originalGlobals){
   if(descriptor)Object.defineProperty(globalThis,key,descriptor);
   else delete globalThis[key];
  }
 }
});

function deepFreeze(value){
 if(value&&typeof value==='object'&&!Object.isFrozen(value)){
  for(const child of Object.values(value))deepFreeze(child);
  Object.freeze(value);
 }
 return value;
}
function frozen(rows){
 deepFreeze(rows);inputs.push([rows,JSON.stringify(rows)]);return rows;
}
function entry(id,extra={}){
 return {id,commandId:`command-${id}`,sessionId:`chat-${id}`,createdAt:'2026-11-12T10:00:00Z',
  status:'unknown',text:payloads[0],attachmentIds:[payloads[2]],
  attachments:[{id:payloads[2],name:payloads[1],path:payloads[3]}],
  replyTo:{text:payloads[4]},error:payloads[5],...extra};
}
const text=value=>typeof value==='string'?value:Array.isArray(value)?value.map(text).join(''):text(value?.children||[]);
const serialized=root=>JSON.stringify(root.toJSON());
const button=root=>root.root.findByType('button');
function assertExpanded(root){
 const control=button(root),description=root.root.findAllByType('p').find(row=>text(row.children).includes('delivery is unknown'));
 assert.equal(text(control.children),'Hide notice');
 assert.equal(control.props.type,'button','Notice controls cannot submit the enclosing composer');
 assert.equal(control.props['aria-expanded'],true);
 assert.ok(description,'Expanded notice must describe uncertainty');
 assert.equal(description.props.role,'status');
 assert.ok(description.props.id);
 assert.equal(control.props['aria-controls'],description.props.id);
}
function assertCollapsed(root,count){
 const control=button(root);
 assert.equal(text(control.children),`Retained copies (${count})`);
 assert.equal(control.props.type,'button');
 assert.equal(control.props['aria-expanded'],false);
 assert.equal(control.props['aria-controls'],undefined,'No dangling description reference');
 assert.doesNotMatch(text(root.toJSON()),/Their delivery is unknown/);
}
async function mount(entries){
 let root,props={entries,selectedChat:'selected-chat-a'},submits=0;
 function Composer({entries}){
  // A different selected chat rerenders the same tab-owned notice with the full
  // outbox. The component has no chat selector or send callback of its own.
  return React.createElement('form',{onSubmit:()=>{submits++}},
   React.createElement(LegacyOutboxNotice,{entries}));
 }
 const render=()=>act(async()=>{root=create(React.createElement(Composer,props));roots.add(root)});
 await render();
 return {
  get root(){return root},get submits(){return submits},
  async toggle(){await act(async()=>button(root).props.onClick())},
  async update(patch={}){props={...props,...patch};await act(async()=>root.update(React.createElement(Composer,props)))},
  async remount(){await act(async()=>root.unmount());roots.delete(root);await render()},
 };
}

test('unbound tab copies are honestly unknown, with no payload or entry IDs rendered',async()=>{
 const rows=frozen([entry('UNBOUND-one'),entry('UNBOUND-two',{sessionId:null}),
  entry('SCOPED-only',{scope:'verified-host/chat'})]);
 const f=await mount(rows);
 assertExpanded(f.root);
 assert.match(text(f.root.toJSON()),/This tab retains earlier message copies with no verified host identity/);
 assert.match(text(f.root.toJSON()),/Their delivery is unknown\. Nothing was sent again\./);
 assert.doesNotMatch(text(f.root.toJSON()),/this (?:chat|conversation)|not sent|delivered successfully|send again|discard|delete/i);
 assert.deepEqual(writes,[],'Mount is not a preference change or an outbox migration');
 for(const value of [...payloads,...rows.flatMap(row=>[row.id,row.commandId,row.sessionId]).filter(Boolean)])
  assert.ok(!serialized(f.root).includes(value),'Entry identities and private payloads must not be rendered');
 await f.toggle();assertCollapsed(f.root,2);
 for(const value of [...payloads,...rows.flatMap(row=>[row.id,row.commandId,row.sessionId]).filter(Boolean)])
  assert.ok(!serialized(f.root).includes(value));
 assert.equal(f.submits,0);
});

test('Hide is reversible, uses button semantics, and saves only payload-free set metadata',async()=>{
 const f=await mount(frozen([entry('one'),entry('two',{scope:null})]));
 assertExpanded(f.root);
 await f.toggle();assertCollapsed(f.root,2);
 assert.equal(writes.length,1);
 const hidden=values.get(noticeKey);
 assert.equal(typeof hidden,'string');assert.ok(hidden.length>0);
 assert.doesNotMatch(hidden,/attachmentIds|attachments|replyTo|PAYLOAD|status|error/);
 await f.toggle();assertExpanded(f.root);
 assert.equal(values.get(noticeKey),'');
 await f.remount();assertExpanded(f.root);
 await f.toggle();assertCollapsed(f.root,2);
 assert.equal(values.get(noticeKey),hidden,'Same retained set produces the same preference');
 assert.equal(f.submits,0);
});

test('hidden preference survives rerenders, chat switches, and a new component mount',async()=>{
 const rows=frozen([entry('one'),entry('two')]);
 const f=await mount(rows);
 await f.toggle();
 const saved=values.get(noticeKey);
 for(const selectedChat of ['selected-chat-b','selected-chat-a',null]){
  await f.update({selectedChat,entries:frozen(rows.map(row=>({...row})))});
  assertCollapsed(f.root,2);
 }
 assert.equal(writes.length,1,'Renders/navigation must not rewrite a journal or preference');
 await f.remount();assertCollapsed(f.root,2);
 const other=await mount(rows);
 assertCollapsed(other.root,2);
 assert.equal(values.get(noticeKey),saved);
 assert.equal(writes.length,1);
});

test('reordering the same frozen set and changing scoped entries do not resurface the notice',async()=>{
 const first=entry('one'),second=entry('two');
 const f=await mount(frozen([first,second,entry('scoped-a',{scope:'host-a'})]));
 await f.toggle();
 const saved=values.get(noticeKey);
 await f.update({entries:frozen([entry('scoped-b',{scope:'host-b'}),second,first])});
 assertCollapsed(f.root,2);
 await f.remount();assertCollapsed(f.root,2);
 assert.equal(values.get(noticeKey),saved);
 assert.equal(writes.length,1);
});

test('message content and delivery status are neither preference identity nor rendered disclosure',async()=>{
 const original=entry('one');
 const f=await mount(frozen([original]));
 await f.toggle();assertCollapsed(f.root,1);
 const saved=values.get(noticeKey);
 await f.update({entries:frozen([{...original,text:'PRIVATE changed body',status:'failed',
  attachments:[{id:'PRIVATE-new-attachment',name:'PRIVATE-name'}]}])});
 assertCollapsed(f.root,1);
 await f.remount();assertCollapsed(f.root,1);
 assert.equal(values.get(noticeKey),saved);
 assert.equal(writes.length,1);
 await f.toggle();assertExpanded(f.root);
 assert.doesNotMatch(serialized(f.root),/PRIVATE|Not sent|Send again/);
});

for(const field of ['id','commandId']){
 test(`same count with a new ${field} resurfaces on the same mount and after remount`,async()=>{
  const first=entry('one'),second=entry('two');
  const f=await mount(frozen([first,second]));
  await f.toggle();assertCollapsed(f.root,2);
  const oldPreference=values.get(noticeKey);
  await f.update({entries:frozen([first,{...second,[field]:`NEW-${field}`}])});
  assertExpanded(f.root);
  assert.equal(values.get(noticeKey),oldPreference,'A new identity must not be silently acknowledged');
  await f.remount();assertExpanded(f.root);
  await f.toggle();assertCollapsed(f.root,2);
  assert.notEqual(values.get(noticeKey),oldPreference);
  await f.remount();assertCollapsed(f.root,2);
 });
}

test('adding or replacing a retained copy remains visible until explicitly hidden',async()=>{
 const first=entry('one');
 const f=await mount(frozen([first]));
 await f.toggle();assertCollapsed(f.root,1);
 await f.update({entries:frozen([first,entry('two')])});assertExpanded(f.root);
 await f.toggle();assertCollapsed(f.root,2);
 await f.update({entries:frozen([entry('replacement')])});assertExpanded(f.root);
});

test('empty or scoped-only entries render nothing and never count scoped payloads',async()=>{
 let root;
 await act(async()=>{root=create(React.createElement(LegacyOutboxNotice,{entries:frozen([])}));roots.add(root)});
 assert.equal(root.toJSON(),null);
 const scoped=frozen([entry('a',{scope:'host-a'}),entry('b',{scope:'host-b'})]);
 await act(async()=>root.update(React.createElement(LegacyOutboxNotice,{entries:scoped})));
 assert.equal(root.toJSON(),null);
 assert.deepEqual(writes,[]);
 const mixed=frozen([...scoped,entry('legacy',{scope:''})]);
 await act(async()=>root.update(React.createElement(LegacyOutboxNotice,{entries:mixed})));
 assertExpanded(root);
 await act(async()=>button(root).props.onClick());assertCollapsed(root,1);
 const saved=values.get(noticeKey);
 await act(async()=>root.update(React.createElement(LegacyOutboxNotice,{entries:frozen([])})));
 assert.equal(root.toJSON(),null);
 assert.equal(values.get(noticeKey),saved,'No entries does not delete retained-set metadata');
 await act(async()=>root.update(React.createElement(LegacyOutboxNotice,{entries:mixed})));
 assertCollapsed(root,1);
});

for(const corrupt of ['{broken','null','true','1','[]','{}','["PRIVATE corrupt metadata"]']){
 test(`corrupt or unrelated preference ${corrupt} fails visible`,async()=>{
  values.set(noticeKey,corrupt);
  const f=await mount(frozen([entry('one')]));
  assertExpanded(f.root);
  assert.doesNotMatch(serialized(f.root),/PRIVATE corrupt metadata/);
  assert.equal(values.get(noticeKey),corrupt);
  assert.deepEqual(writes,[],'Malformed metadata must not trigger journal repair');
  await f.toggle();assertCollapsed(f.root,1);
  await f.remount();assertCollapsed(f.root,1);
 });
}

test('storage reads throwing fail visible but a later successful save can persist Hide',async()=>{
 failRead=true;
 const f=await mount(frozen([entry('one')]));
 assertExpanded(f.root);
 assert.doesNotMatch(serialized(f.root),/PRIVATE storage/);
 await f.toggle();assertCollapsed(f.root,1);
 failRead=false;
 await f.remount();assertCollapsed(f.root,1);
 assert.doesNotMatch(text(f.root.toJSON()),/preference could not be saved/);
});

for(const readAlsoFails of [false,true]){
 test(`storage writes throwing retain in-memory Hide and explicitly report preference failure (read failure=${readAlsoFails})`,async()=>{
  failRead=readAlsoFails;failWrite=true;
  const f=await mount(frozen([entry('one')]));
  assertExpanded(f.root);
  await f.toggle();assertCollapsed(f.root,1);
  assert.match(text(f.root.toJSON()),/The notice preference could not be saved\. It may reappear after reload\. Message copies are unchanged\./);
  assert.doesNotMatch(serialized(f.root),/PRIVATE storage/);
  await f.update({selectedChat:'selected-chat-b'});assertCollapsed(f.root,1);
  await f.toggle();assertExpanded(f.root);
  await f.toggle();assertCollapsed(f.root,1);
  assert.equal(values.has(noticeKey),false,'Failed writes cannot be claimed as persisted');
  await f.remount();assertExpanded(f.root);
  failRead=false;failWrite=false;
  await f.toggle();assertCollapsed(f.root,1);
  assert.doesNotMatch(text(f.root.toJSON()),/preference could not be saved/);
  await f.remount();assertCollapsed(f.root,1);
  assert.equal(f.submits,0);
 });
}

test('sessionStorage access itself may throw; notice remains usable without exposing exception prose',async()=>{
 const getter=()=>{throw Error('PRIVATE blocked storage getter')};
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,get:getter});
 Object.defineProperty(window,'sessionStorage',{configurable:true,get:getter});
 const f=await mount(frozen([entry('one')]));
 assertExpanded(f.root);
 await f.toggle();assertCollapsed(f.root,1);
 assert.match(text(f.root.toJSON()),/notice preference could not be saved/);
 assert.doesNotMatch(serialized(f.root),/PRIVATE blocked/);
 await f.update();assertCollapsed(f.root,1);
 await f.toggle();assertExpanded(f.root);
 assert.deepEqual(writes,[]);
});