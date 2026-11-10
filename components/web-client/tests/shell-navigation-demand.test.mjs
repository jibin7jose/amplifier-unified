import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {navigationResources} from '../src/shell/navigation-demand.js';
import {shellScope} from '../src/shell/resources.js';

const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useShell}=await vite.ssrLoadModule('/src/shell/runtime.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const instance={id:'chats',package:'builtin.chats',slot:'navigation'};
const state=(view={workSurface:'chat'},extra={})=>({revision:1,shellDataKey:'ignored',shellBootstrapKey:'bootstrap-1',selectedSessionId:'chat-a',selectedWorkspaceId:'workspace-a',sessions:[],view:{navPinned:true,...view},...extra});
const descriptors=(key='bootstrap-1')=>({clientId:'test-client',hostInstanceId:'host-1',shellBootstrapKey:key,revision:1,resolvedInstances:[instance],effectiveComposition:{instances:[instance]},instanceGenerations:{chats:1}});
function demandFor(call){
 if(call.options.method==='POST')return JSON.parse(call.options.body);
 const params=new URL(call.path,'http://fixture').searchParams;
 return {clientId:params.get('clientId'),demandId:Number(params.get('demandId')),scope:JSON.parse(params.get('scope')),resources:JSON.parse(params.get('navigationResources'))};
}
function envelope(demand,input,changes={}){
 const resources={context:{revision:1,value:{view:input.view,viewRevision:1,selectedSessionId:input.selectedSessionId,selectedWorkspaceId:input.selectedWorkspaceId,library:{bounded:true},sidebarNavigation:{recentView:{}}}}};
 for(const name of demand.resources){
  const page={items:[{id:'first',title:'First'}],scope:{workspaceId:input.selectedWorkspaceId},total:1};
  resources[name]={revision:1,value:name.startsWith('sidebar')?{sidebarNavigation:{[name.slice(7).toLowerCase()]:page}}:{[name]:page}};
 }
 return {hostInstanceId:'host-1',clientId:'test-client',bootstrapKey:input.shellBootstrapKey,demandId:demand.demandId,scope:demand.scope,instances:{chats:{generation:1,stateRevision:1,pending:{},resources}},...changes};
}
async function fixture(run,{dispatch=async()=>({accepted:true})}={}){
 const prior={fetch:globalThis.fetch,document:globalThis.document,window:globalThis.window};
 const attributes={};globalThis.document=Object.assign(new EventTarget(),{hidden:false,documentElement:{setAttribute:(key,value)=>attributes[key]=value}});globalThis.window=new EventTarget();
 const calls=[];let root,current,input;
 globalThis.fetch=(path,options={})=>{
  if(path.startsWith('/api/actions'))return Promise.resolve(new Response('{}'));
  return new Promise((resolve,reject)=>{
   const call={path,options,resolve,reject};calls.push(call);
   options.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
  });
 };
 function Probe({input}){current=useShell(input,dispatch,'test-client',input);return null}
 const render=async next=>{input=next;await act(async()=>{if(root)root.update(React.createElement(Probe,{input}));else root=create(React.createElement(Probe,{input}))})};
 const reply=async(index,value,status=200)=>act(async()=>calls[index].resolve(new Response(JSON.stringify(value),{status})));
 const accept=async(index,next=input)=>{
  const call=calls[index],resources=envelope(demandFor(call),next);
  await reply(index,call.options.method==='POST'?resources:{...descriptors(next.shellBootstrapKey),shellResources:resources});
  return resources;
 };
 try{await run({calls,render,reply,accept,current:()=>current,input:()=>input,attributes})}
 finally{if(root)await act(async()=>root.unmount());Object.assign(globalThis,prior)}
}

for(const outcome of ['success','error'])test(`late bootstrap ${outcome} cannot settle a replaced navigation demand`,()=>fixture(async f=>{
 const chat=state(),search=state({workSurface:'chats'});
 await f.render(chat);assert.equal(f.calls.length,1);
 assert.deepEqual(demandFor(f.calls[0]).resources,navigationResources(chat));
 await f.render(search);
 await act(async()=>{void f.current().refresh();void f.current().refresh()});
 assert.equal(f.calls.length,1,'one held read and one coalesced replacement');
 if(outcome==='success')await f.accept(0,chat);else await f.reply(0,{error:'Old surface failed'},500);
 assert.equal(f.calls.length,2);assert.equal(f.current().data,null);assert.equal(f.current().error,'');
 assert.deepEqual(demandFor(f.calls[1]).resources,navigationResources(search));
 assert.deepEqual(demandFor(f.calls[1]).scope,shellScope(search));
 await f.accept(1,search);
 assert.equal(f.current().ready,true);assert.equal(f.current().data.snapshots.chats.view.workSurface,'chats');
}));

test('unchanged scope ignores app/catalog/Canvas/runtime churn and shell notifications',()=>fixture(async f=>{
 await f.render(state());const resources=await f.accept(0);
 for(let revision=2;revision<12;revision++)await f.render(state(undefined,{revision,shellDataKey:'new-'+revision,shellChangeToken:'token-'+revision,canvas:{id:'different',open:true},runtime:{available:false},sessions:[{id:'chat-a',streaming:'progress'}],shellResources:resources}));
 await act(async()=>{const event=new Event('amplifier-shell');event.detail={shellClientId:'test-client'};window.dispatchEvent(event)});
 assert.equal(f.calls.length,1,'no whole-shell invalidation from unrelated publication');
 const next=structuredClone(resources);next.instances.chats.stateRevision++;next.instances.chats.resources.recentNavigation={revision:2,value:{recentNavigation:{items:[{id:'changed'}],total:1}}};
 await f.render({...f.input(),shellResources:next});
 assert.equal(f.calls.length,1);assert.equal(f.current().data.snapshots.chats.recentNavigation.items[0].id,'changed');
}));

test('same resource names in different workspaces are fenced by scope and demand identity',()=>fixture(async f=>{
 const first=state({workSurface:'workspace',workWorkspaceId:'one',workWorkspaceTab:'chats'});
 await f.render(first);await f.accept(0);
 const second=state({...first.view,workWorkspaceId:'two'}),third=state({...first.view,workWorkspaceId:'three'});
 await f.render(second);assert.equal(f.calls.length,2);
 assert.deepEqual(f.current().data.snapshots.chats.navigationResources,[],'previous-scope pages are pending immediately');
 assert.equal(f.current().data.snapshots.chats.sidebarNavigation?.workspace,undefined);
 await f.render(third);await f.accept(1,second);
 assert.equal(f.calls.length,3);assert.notEqual(demandFor(f.calls[1]).demandId,demandFor(f.calls[2]).demandId);
 assert.notEqual(f.current().data.snapshots.chats.view.workWorkspaceId,'two');
 await f.accept(2,third);assert.equal(f.current().data.snapshots.chats.view.workWorkspaceId,'three');
}));

test('older HTTP baseline cannot overwrite a newer same-demand stream resource',()=>fixture(async f=>{
 await f.render(state());await f.accept(0);
 await act(async()=>{void f.current().refresh()});
 const newer=envelope(demandFor(f.calls[1]),f.input());
 newer.instances.chats.stateRevision++;newer.instances.chats.resources.recentNavigation={revision:5,value:{recentNavigation:{items:[{id:'new'}]}}};
 await f.render({...f.input(),shellResources:newer});await f.accept(1);
 assert.equal(f.current().data.snapshots.chats.recentNavigation.items[0].id,'new');assert.equal(f.calls.length,2);
 const diagnostics=JSON.parse(f.attributes['data-amplifier-shell-diagnostics']);
 const resourceEvent=diagnostics.events.filter(event=>event.reason==='resources').at(-1);
 assert.equal(resourceEvent.changedRecords,0,'older HTTP record retained by the receiver is not a new revision');
 assert.equal(diagnostics.resourceRevisions.changedByName.recentNavigation,2,'bootstrap plus the newer streamed page');
}));

test('runtime diagnostics distinguish repeated baselines and context-only updates without new reads',()=>fixture(async f=>{
 await f.render(state());const baseline=await f.accept(0);
 const read=()=>JSON.parse(f.attributes['data-amplifier-shell-diagnostics']);
 const initial=read().resourceRevisions.changedRecords;
 for(let index=0;index<3;index++)await f.render({...f.input(),revision:index+2,shellResources:structuredClone(baseline)});
 assert.equal(read().resourceRevisions.acceptedEnvelopes,4);assert.equal(read().resourceRevisions.unchangedEnvelopes,3);
 assert.equal(read().resourceRevisions.changedRecords,initial);
 const context=structuredClone(baseline);context.instances.chats.stateRevision++;context.instances.chats.resources.context.revision++;
 await f.render({...f.input(),shellResources:context});
 assert.equal(read().events.at(-1).contextRecords,1);assert.equal(read().events.at(-1).pageRecords,0);
 const stale={...context,demandId:999};await f.render({...f.input(),shellResources:stale});
 assert.equal(read().resourceRevisions.acceptedEnvelopes,5,'rejected envelopes are not accepted revision tuples');
 assert.equal(read().counts.stale,1);assert.equal(f.calls.length,1,'diagnostics add no reads');
}));

test('reconnect consumes its state baseline; a retired registration uses one idempotent POST',()=>fixture(async f=>{
 await f.render(state());const baseline=await f.accept(0);
 await act(async()=>window.dispatchEvent(new Event('amplifier-reconnected')));assert.equal(f.calls.length,1);
 await f.render({...f.input(),revision:2,shellResources:baseline});assert.equal(f.calls.length,1);
 await act(async()=>window.dispatchEvent(new Event('amplifier-reconnected')));assert.equal(f.calls.length,1);
 await f.render({...f.input(),revision:3,shellResources:undefined});assert.equal(f.calls.length,2);
 assert.equal(f.calls[1].options.method,'POST');assert.equal(demandFor(f.calls[1]).demandId,demandFor(f.calls[0]).demandId);
 await f.accept(1);await f.render({...f.input(),revision:4});assert.equal(f.calls.length,2);
}));

test('hidden page releases resource interest then restores it without another bootstrap',()=>fixture(async f=>{
 await f.render(state());await f.accept(0);
 await act(async()=>{document.hidden=true;document.dispatchEvent(new Event('visibilitychange'))});
 assert.deepEqual(demandFor(f.calls[1]).resources,[]);await f.accept(1);
 assert.deepEqual(f.current().data.snapshots.chats.navigationResources,[]);
 assert.equal(f.current().data.snapshots.chats.recentNavigation,undefined);
 await act(async()=>{document.hidden=false;document.dispatchEvent(new Event('visibilitychange'))});
 assert.deepEqual(demandFor(f.calls[2]).resources,navigationResources(f.input()));await f.accept(2);
 assert.equal(f.calls.filter(call=>call.options.method!=='POST').length,1);
}));

test('a bootstrap key change fetches descriptors once even if its preceding state is still visible',()=>fixture(async f=>{
 await f.render(state());await f.accept(0);
 await f.render(state(undefined,{shellBootstrapKey:'bootstrap-2'}));assert.equal(f.calls.length,2);
 await f.accept(1);await f.render({...f.input(),revision:2});assert.equal(f.calls.length,2);
 // An explicit composition command may receive its newer descriptors before state.
 await act(async()=>{void f.current().refresh('composition')});
 const future=state(undefined,{shellBootstrapKey:'bootstrap-3'});await f.accept(2,future);
 await f.render({...f.input(),revision:3});assert.equal(f.calls.length,3);
 await f.render(future);assert.equal(f.calls.length,3);
}));

test('scope rejection is observable and does not retry until the authoritative scope changes',()=>fixture(async f=>{
 await f.render(state());await f.reply(0,{error:'Scope changed'},409);
 assert.match(f.current().error,/Scope changed/);
 for(let revision=2;revision<6;revision++)await f.render(state(undefined,{revision}));
 assert.equal(f.calls.length,1);
 await f.render(state({workSurface:'chats'}));assert.equal(f.calls.length,2);await f.accept(1);
}));

test('resource budget rejection is explicit rather than a permanent loading placeholder',()=>fixture(async f=>{
 await f.render(state());const demand=demandFor(f.calls[0]);
 await f.reply(0,{...descriptors(),shellResources:envelope(demand,f.input(),{instances:{},error:{code:'shell_resource_limit',message:'Narrow the requested pages.'}})});
 assert.equal(f.current().ready,true);assert.match(f.current().error,/Narrow/);
 assert.deepEqual(f.current().data.snapshots.chats.navigationResources,[]);assert.equal(f.calls.length,1);
}));

test('view receipts preserve committed edits until the resource stream catches up, without GETs',async()=>{
 let resolve;
 await fixture(async f=>{
  await f.render(state());const baseline=await f.accept(0),host=f.current().hostFor(instance);
  let operation;await act(async()=>{operation=host.dispatch('view.update',{patch:{navFilter:'new'}})});
  assert.equal(host.getSnapshot().view.navFilter,'new');
  await act(async()=>{resolve({accepted:true,result:{shellView:{instanceId:'chats',generation:1,viewRevision:2,view:{workSurface:'chat',navFilter:'new'}}}});await operation});
  await f.render({...f.input(),shellResources:baseline});assert.equal(host.getSnapshot().view.navFilter,'new');
  const caught=structuredClone(baseline);caught.instances.chats.stateRevision++;caught.instances.chats.resources.context={revision:3,value:{...baseline.instances.chats.resources.context.value,viewRevision:2,view:{workSurface:'chat',navFilter:'new'}}};
  await f.render({...f.input(),shellResources:caught});
  const changed=structuredClone(caught);changed.instances.chats.stateRevision++;changed.instances.chats.resources.context={revision:4,value:{...caught.instances.chats.resources.context.value,viewRevision:3,view:{workSurface:'chat',navFilter:'later'}}};
  await f.render({...f.input(),shellResources:changed});assert.equal(host.getSnapshot().view.navFilter,'later');assert.equal(f.calls.length,1);
 },{dispatch:()=>new Promise(done=>resolve=done)});
});


test('a descriptor change during a resource request cannot be lost behind its accepted demand',()=>fixture(async f=>{
 await f.render(state());await f.accept(0);
 await act(async()=>{void f.current().refresh()});
 await f.render(state(undefined,{shellBootstrapKey:'bootstrap-2'}));
 await f.accept(1,state());assert.equal(f.calls.length,3);
 assert.equal(f.calls[2].options.method,undefined);await f.accept(2);
 assert.equal(f.current().data.shellBootstrapKey,'bootstrap-2');
}));

test('failed view command rolls back only its private pending patch without a refresh',async()=>{
 let reject;
 await fixture(async f=>{
  await f.render(state());await f.accept(0);const host=f.current().hostFor(instance);
  let operation;await act(async()=>{operation=host.dispatch('view.update',{patch:{navFilter:'unsaved'}});operation.catch(()=>{})});
  assert.equal(host.getSnapshot().view.navFilter,'unsaved');
  await act(async()=>{reject(Error('Rejected'));await assert.rejects(operation,/Rejected/)});
  assert.equal(host.getSnapshot().view.navFilter,undefined);assert.equal(f.calls.length,1);
 },{dispatch:()=>new Promise((_,fail)=>reject=fail)});
});


test('bootstrap completion cannot install a generation already superseded by authoritative state',()=>fixture(async f=>{
 const older=state();await f.render(older);
 const newer=state(undefined,{shellBootstrapKey:'bootstrap-2'});await f.render(newer);
 await f.accept(0,older);
 assert.equal(f.current().data,null,'obsolete descriptors never mount');assert.equal(f.calls.length,2);
 await f.accept(1,newer);assert.equal(f.current().data.shellBootstrapKey,'bootstrap-2');
}));


test('a replaced component cannot apply its delayed view receipt to the new generation',async()=>{
 let resolve;
 await fixture(async f=>{
  await f.render(state());await f.accept(0);const old=f.current().hostFor(instance);
  let operation;await act(async()=>{operation=old.dispatch('view.update',{patch:{navFilter:'old'}})});
  const newer=state(undefined,{shellBootstrapKey:'bootstrap-2'});await f.render(newer);
  const resources=envelope(demandFor(f.calls[1]),newer);resources.instances.chats.generation=2;
  await f.reply(1,{...descriptors('bootstrap-2'),instanceGenerations:{chats:2},shellResources:resources});
  await act(async()=>{resolve({accepted:true,result:{shellView:{instanceId:'chats',generation:1,viewRevision:99,view:{navFilter:'obsolete'}}}});await operation});
  assert.equal(f.current().data.snapshots.chats.generation,2);assert.equal(f.current().hostFor(instance).getSnapshot().view.navFilter,undefined);
 },{dispatch:()=>new Promise(done=>resolve=done)});
});


test('a newer streamed view cannot be rewound by an older command receipt',async()=>{
 let resolve;
 await fixture(async f=>{
  await f.render(state());const baseline=await f.accept(0),host=f.current().hostFor(instance);
  let operation;await act(async()=>{operation=host.dispatch('view.update',{patch:{navFilter:'requested'}})});
  const newer=structuredClone(baseline);newer.instances.chats.stateRevision++;newer.instances.chats.resources.context={revision:3,value:{...baseline.instances.chats.resources.context.value,viewRevision:3,view:{workSurface:'chat',navFilter:'newer'}}};
  await f.render({...f.input(),shellResources:newer});
  await act(async()=>{resolve({accepted:true,result:{shellView:{instanceId:'chats',generation:1,viewRevision:2,view:{navFilter:'requested'}}}});await operation});
  assert.equal(host.getSnapshot().view.navFilter,'newer');assert.equal(f.current().data.snapshots.chats.viewRevision,3);assert.equal(f.calls.length,1);
 },{dispatch:()=>new Promise(done=>resolve=done)});
});


test('explicit builtin resource retry uses one POST and cannot turn a failed read into stale success',()=>fixture(async f=>{
 await f.render(state());await f.accept(0);const host=f.current().hostFor(instance);
 let retry;await act(async()=>{retry=host.refreshResources();retry.catch(()=>{})});
 assert.equal(f.calls[1].path,'/api/shell/resources');assert.equal(demandFor(f.calls[1]).reason,'manual');
 await f.reply(1,{error:'Temporarily unavailable'},503);await assert.rejects(retry,/could not be refreshed/);
 assert.equal(f.calls.length,2);
}));

for(const held of [false,true])test(`host restart replaces descriptors and rejects old-host resources${held?' while a read is pending':''}`,()=>fixture(async f=>{
 const original=state(undefined,{client:{hostInstanceId:'host-1'},shellBootstrapKey:undefined});
 await f.render(original);
 const initial=envelope(demandFor(f.calls[0]),state());
 await f.reply(0,{...descriptors(),shellResources:initial});
 if(held)await act(async()=>{void f.current().refresh()});
 const restarted=state(undefined,{client:{hostInstanceId:'host-2'},shellBootstrapKey:undefined});
 await act(async()=>window.dispatchEvent(new Event('amplifier-reconnected')));
 await f.render(restarted);
 assert.deepEqual(f.current().data.snapshots.chats.navigationResources,[],'old host pages immediately become pending');
 if(held)await f.reply(1,initial);
 const last=f.calls.length-1;
 assert.equal(f.calls[last].options.method,undefined,'new host requires descriptors, not a resource-only POST');
 const resources=envelope(demandFor(f.calls[last]),state(undefined,{shellBootstrapKey:'bootstrap-2'}),{hostInstanceId:'host-2'});
 await f.reply(last,{...descriptors('bootstrap-2'),hostInstanceId:'host-2',shellResources:resources});
 assert.equal(f.current().data.hostInstanceId,'host-2');assert.ok(f.current().data.snapshots.chats.navigationResources.length);
 const count=f.calls.length;
 await f.render({...restarted,revision:2,shellBootstrapKey:'bootstrap-2',shellResources:resources});
 assert.equal(f.calls.length,count,'the following authoritative baseline does not request descriptors again');
}));

test('a previous host cannot restore its acknowledged view after restart',async()=>{
 let resolve;
 await fixture(async f=>{
  await f.render(state(undefined,{client:{hostInstanceId:'host-1'}}));await f.accept(0);
  const host=f.current().hostFor(instance);let operation;
  await act(async()=>{operation=host.dispatch('view.update',{patch:{navFilter:'old intent'}})});
  const restarted=state(undefined,{client:{hostInstanceId:'host-2'},shellBootstrapKey:'bootstrap-2'});await f.render(restarted);
  const resources=envelope(demandFor(f.calls[1]),restarted,{hostInstanceId:'host-2'});
  await f.reply(1,{...descriptors('bootstrap-2'),hostInstanceId:'host-2',shellResources:resources});
  await act(async()=>{resolve({accepted:true,result:{shellView:{instanceId:'chats',generation:1,viewRevision:99,view:{navFilter:'obsolete'}}}});await operation});
  assert.equal(host.getSnapshot().view.navFilter,undefined);
  assert.equal(f.current().data.snapshots.chats.viewRevision,1);
  assert.equal(f.calls.length,2,'restart does not replay the old command');
 },{dispatch:()=>new Promise(done=>resolve=done)});
});

test('late pending HTTP cannot resurrect retry after an equal-value ready stream entry',()=>fixture(async f=>{
 await f.render(state());const baseline=await f.accept(0);
 await act(async()=>{void f.current().refresh()});
 const pending=structuredClone(baseline);pending.instances.chats.stateRevision=2;
 pending.instances.chats.pending={recentNavigation:{status:'retry',retryAfterMs:1000}};
 const ready=structuredClone(baseline);ready.instances.chats.stateRevision=3;
 await f.render({...f.input(),shellResources:ready});
 await f.reply(1,pending);
 assert.deepEqual(f.current().data.snapshots.chats.navigationResourcePending,{});
 assert.equal(f.current().data.snapshots.chats.recentNavigation.items[0].id,'first');
 assert.equal(f.calls.length,2,'pending state adds no polling or replay');
}));
