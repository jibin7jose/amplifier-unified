import test from 'node:test';
import assert from 'node:assert/strict';
import {shellScope,resourceSnapshots,retainResourceVersions,pendingSnapshots,sidebarReadingSnapshot} from '../src/shell/resources.js';
import {createShellDiagnostics} from '../src/shell/diagnostics.js';
const bootstrap={clientId:'client',hostInstanceId:'host',shellBootstrapKey:'key',resolvedInstances:[{id:'chat',package:'builtin.chats'},{id:'button',package:'builtin.actions'},{id:'custom',package:'custom.sidebar'}],instanceGenerations:{chat:1,button:2,custom:3}};
const demand={id:1,scope:['chat','workspace','chat',null,null],resources:['sidebarSearch']};
const envelope=()=>({
 clientId:'client',hostInstanceId:'host',bootstrapKey:'key',demandId:1,scope:demand.scope,
 instances:{
  chat:{generation:1,stateRevision:1,pending:{},resources:{
   context:{revision:1,value:{view:{draft:'private'},sidebarNavigation:{recentView:{query:'private'}}}},
   sidebarSearch:{revision:1,value:{sidebarNavigation:{search:{items:[{id:'one'}]}}}},
  }},
  custom:{generation:3,stateRevision:1,pending:{},resources:{customSnapshot:{revision:1,value:{ownField:42}}}},
 },
});

test('resource assembly preserves context, exact coverage and independent custom snapshots',()=>{
 const result=resourceSnapshots(bootstrap,envelope(),demand);
 assert.deepEqual(result.chat.navigationResources,['sidebarSearch']);assert.equal(result.chat.sidebarNavigation.search.items[0].id,'one');
 assert.equal(result.chat.sidebarNavigation.recentView.query,'private');assert.equal(result.button,undefined);assert.deepEqual(result.custom,{ownField:42,generation:3});
});
test('missing resources stay uncovered and stale identities or scopes cannot become a baseline',()=>{
 for(const patch of [{clientId:'other'},{hostInstanceId:'other'},{bootstrapKey:'other'},{demandId:2},{scope:['chat','other','chat',null,null]}])assert.equal(resourceSnapshots(bootstrap,{...envelope(),...patch},demand),null);
 const missing=envelope();delete missing.instances.chat.resources.sidebarSearch;
 assert.deepEqual(resourceSnapshots(bootstrap,missing,demand).chat.navigationResources,[]);
 missing.instances.chat.generation=9;assert.equal(resourceSnapshots(bootstrap,missing,demand).chat,undefined);
});
test('per-resource ordering never resurrects removed coverage or crosses demand identities',()=>{
 const prior=envelope();prior.instances.chat.resources.sidebarSearch.revision=5;
 const next=envelope();next.instances.chat.stateRevision=2;assert.equal(retainResourceVersions(prior,next).instances.chat.resources.sidebarSearch.revision,5);
 delete next.instances.chat.resources.sidebarSearch;assert.equal(retainResourceVersions(prior,next).instances.chat.resources.sidebarSearch,undefined);
 assert.equal(retainResourceVersions(prior,{...envelope(),demandId:2}).instances.chat.resources.sidebarSearch.revision,1);
});
test('scope excludes irrelevant inactive workspace state',()=>{
 assert.deepEqual(shellScope({selectedSessionId:'chat',selectedWorkspaceId:'workspace',view:{workSurface:'chat',workWorkspaceId:'old',workWorkspaceTab:'files'}}),demand.scope);
 assert.deepEqual(shellScope({view:{workSurface:'workspace',workWorkspaceId:'one'}}),[null,null,'workspace','one','chats']);
});
test('DOM diagnostics are bounded and exclude resource values, paths, filters and drafts',()=>{
 let text;const log=createShellDiagnostics({setAttribute:(name,value)=>{assert.equal(name,'data-amplifier-shell-diagnostics');text=value}});
 for(let i=0;i<200;i++)log.record('demand',{demandId:i,resources:['sidebarSearch','private query'],requestId:'a'.repeat(32),elapsedMs:10,status:200,value:'secret',path:'/private/home',draft:'secret',query:'secret'});
 log.record('secret reason',{draft:'secret'});log.record('error',{requestId:'bad private value'});
 assert.ok(text.length<=8192);const result=JSON.parse(text);assert.ok(result.events.length<=24);assert.equal(result.counts.demand,200);
 assert.doesNotMatch(text,/secret|private|\/home/);assert.deepEqual(result.events[0].resources,['sidebarSearch']);
});

function diagnosticFixture(){
 let text;const log=createShellDiagnostics({setAttribute:(_name,value)=>text=value});
 return {log,read:()=>JSON.parse(text),text:()=>text};
}
test('accepted unchanged revision tuples do not count as changed resource records',()=>{
 const f=diagnosticFixture(),baseline=envelope();f.log.acceptResources(null,baseline);
 const first=f.read();assert.equal(first.resourceRevisions.acceptedEnvelopes,1);
 assert.equal(first.resourceRevisions.changedRecords,3);assert.equal(first.resourceRevisions.unchangedEnvelopes,0);
 f.log.acceptResources(baseline,structuredClone(baseline));
 const next=f.read();assert.equal(next.resourceRevisions.acceptedEnvelopes,2);assert.equal(next.resourceRevisions.unchangedEnvelopes,1);
 assert.equal(next.resourceRevisions.changedRecords,3);assert.equal(next.resourceRevisions.unchangedRecords,3);
 assert.equal(next.events.at(-1).changedRecords,0);assert.deepEqual(next.events.at(-1).changedByName,{});
});
test('context, named pages and custom revisions have separate numeric change counts',()=>{
 const f=diagnosticFixture();let prior=envelope();f.log.acceptResources(null,prior);
 for(const [instance,name,category] of [['chat','context','contextRecords'],['chat','sidebarSearch','pageRecords'],['custom','customSnapshot','customRecords']]){
  const next=structuredClone(prior);next.instances[instance].resources[name].revision++;
  f.log.acceptResources(prior,next);const event=f.read().events.at(-1);
  assert.equal(event.changedRecords,1);assert.equal(event.unchangedRecords,2);assert.equal(event[category],1);
  for(const other of ['contextRecords','pageRecords','customRecords'])if(other!==category)assert.equal(event[other],0);
  assert.deepEqual(event.changedByName,{[name]:1});prior=next;
 }
 assert.deepEqual(f.read().resourceRevisions.changedByName,{context:2,sidebarSearch:2,customSnapshot:2});
});
test('coverage removal is separate from changed values and replacement identities are not unchanged',()=>{
 const f=diagnosticFixture(),prior=envelope(),removed=envelope();delete removed.instances.chat.resources.sidebarSearch;
 f.log.acceptResources(prior,removed);
 assert.equal(f.read().events[0].removedRecords,1);assert.equal(f.read().events[0].changedRecords,0);
 assert.equal(f.read().resourceRevisions.unchangedEnvelopes,0);
 for(const patch of [{hostInstanceId:'new-host'},{clientId:'new-client'},{bootstrapKey:'new-key'},{demandId:2},{scope:['other','workspace','chat',null,null]}]){
  f.log.acceptResources(prior,{...envelope(),...patch});const event=f.read().events.at(-1);
  assert.equal(event.changedRecords,3);assert.equal(event.removedRecords,3);assert.equal(event.unchangedRecords,0);
 }
 const generation=envelope();generation.instances.chat.generation++;
 f.log.acceptResources(prior,generation);assert.equal(f.read().events.at(-1).changedRecords,2);
 assert.equal(f.read().events.at(-1).unchangedRecords,1);assert.equal(f.read().events.at(-1).removedRecords,2);
});
test('revision diagnostics never read payloads and remain bounded across many instances and updates',()=>{
 const f=diagnosticFixture(),baseline=envelope(),names=['context','customSnapshot','chatNavigation','homeNavigation','pinnedNavigation','recentNavigation','workspaceShortcuts','workspaceExplorer','sidebarSearch','sidebarWorkspace','sidebarRecent','sidebarPinned','workspaceOverview'];
 baseline.instances={};
 for(let index=0;index<64;index++){
  const records={};for(const name of names)records[name]={revision:1,get value(){throw Error('Resource value must not be inspected')}};
  Object.defineProperty(records,'private-resource-name',{get(){throw Error('Unknown resource must not be inspected')}});
  baseline.instances['private-instance-title-'+index]={generation:1,resources:records};
 }
 for(let index=0;index<200;index++)f.log.acceptResources(null,baseline);
 const result=f.read();assert.ok(f.text().length<=8192);assert.ok(result.events.length<=24);
 assert.equal(result.resourceRevisions.changedRecords,200*64*13);assert.equal(result.resourceRevisions.changedByName.recentNavigation,200*64);
 assert.equal(result.resourceRevisions.pageRecords,200*64*11);
 assert.doesNotMatch(f.text(),/private|draft|client|host/);
});

test('pending query assembly keeps matching rows and leaves initial coverage absent',()=>{
 const value=envelope();value.instances.chat.stateRevision=2;
 value.instances.chat.pending={sidebarSearch:{status:'pending',retryAfterMs:0}};
 const updating=resourceSnapshots(bootstrap,value,demand).chat;
 assert.equal(updating.sidebarNavigation.search.items[0].id,'one');
 assert.equal(updating.navigationResourcePending.sidebarSearch.status,'pending');
 delete value.instances.chat.resources.sidebarSearch;
 const initial=resourceSnapshots(bootstrap,value,demand).chat;
 assert.deepEqual(initial.navigationResources,[]);assert.equal(initial.sidebarNavigation.search,undefined);
 assert.equal(initial.navigationResourcePending.sidebarSearch.status,'pending');
 for(const entry of [{stateRevision:-1},{stateRevision:undefined},{pending:{sidebarSearch:{status:'complete'}}}])
  assert.equal(resourceSnapshots(bootstrap,{...value,instances:{...value.instances,chat:{...value.instances.chat,...entry}}},demand),null);
});
test('late pending and retry entries cannot rewind equal-value completion',()=>{
 const ready=envelope();ready.instances.chat.stateRevision=4;
 for(const status of ['pending','retry']){
  const old=envelope();old.instances.chat.stateRevision=3;old.instances.chat.pending={sidebarSearch:{status,retryAfterMs:1000}};
  const retained=retainResourceVersions(ready,old);
  assert.equal(retained.instances.chat,ready.instances.chat);
  assert.deepEqual(resourceSnapshots(bootstrap,retained,demand).chat.navigationResourcePending,{});
 }
 const next=envelope();next.instances.chat.stateRevision=5;next.instances.chat.pending={sidebarSearch:{status:'pending'}};
 delete next.instances.chat.resources.sidebarSearch;
 assert.equal(retainResourceVersions(ready,next).instances.chat.resources.sidebarSearch,undefined);
 const switched={...next,demandId:2};assert.equal(retainResourceVersions(ready,switched),switched);
});

function readingFixture(){
 const wanted={...demand,resources:['pinnedNavigation','recentNavigation','workspaceShortcuts','sidebarSearch']};
 const value=envelope(),entry=value.instances.chat;
 entry.resources.context.value.recentScope={mode:'all',workspaceId:null,filter:'',clientId:'client',instanceId:'chat',generation:1};
 for(const name of wanted.resources.filter(name=>name!=='sidebarSearch'))entry.resources[name]={revision:1,value:{[name]:name==='workspaceShortcuts'?[{workspaceId:'one'}]:{items:[{id:name}],total:1}}};
 const snapshots=resourceSnapshots(bootstrap,value,wanted);
 return {wanted,value,previous:{shellResources:value,snapshots}};
}
test('selection pending retains sidebar reading geometry without granting coverage or stale search',()=>{
 const {wanted,previous}=readingFixture();
 const changed={...wanted,id:2,scope:['next-chat','next-workspace','workspace','next-workspace','chats']};
 const pending=pendingSnapshots(bootstrap,previous,changed).chat;
 assert.deepEqual(pending.navigationResources,[]);
 assert.equal(pending.pinnedNavigation,undefined);
 assert.deepEqual(Object.keys(pending.navigationResourceRetained),['pinnedNavigation','recentNavigation','workspaceShortcuts']);
 const reading=sidebarReadingSnapshot(pending);
 assert.equal(reading.recentNavigation.items.length,1);assert.equal(reading.workspaceShortcuts.length,1);
 assert.equal(reading.sidebarNavigation.search,undefined);
 assert.equal(reading.navigationResourcePending.recentNavigation.status,'pending');
});
test('retained sidebar survives pending baseline but clears for new host, module, query or released interest',()=>{
 const {wanted,previous,value}=readingFixture(),changed={...wanted,id:2};
 const waiting=pendingSnapshots(bootstrap,previous,changed);
 const next=structuredClone(value);next.demandId=2;next.instances.chat.resources={context:next.instances.chat.resources.context};
 next.instances.chat.pending=Object.fromEntries(wanted.resources.map(name=>[name,{status:'pending'}]));
 const assembled=resourceSnapshots(bootstrap,next,changed,waiting).chat;
 assert.equal(sidebarReadingSnapshot(assembled).recentNavigation.items.length,1);
 assert.deepEqual(assembled.navigationResources,[]);
 const moved=structuredClone(next);moved.instances.chat.resources.context.value.recentScope.workspaceId='other';
 assert.deepEqual(resourceSnapshots(bootstrap,moved,changed,waiting).chat.navigationResourceRetained,{});
 const filtered=structuredClone(next);filtered.instances.chat.resources.context.value.view.navShowAgentCreated=true;
 assert.equal(resourceSnapshots(bootstrap,filtered,changed,waiting).chat.navigationResourceRetained.recentNavigation,undefined);
 assert.deepEqual(pendingSnapshots(bootstrap,previous,changed,'new-host').chat.navigationResourceRetained,{});
 assert.equal(pendingSnapshots({...bootstrap,instanceGenerations:{...bootstrap.instanceGenerations,chat:2}},previous,changed).chat,undefined);
 assert.deepEqual(pendingSnapshots(bootstrap,previous,{...changed,resources:[]}).chat.navigationResourceRetained,{});
});
