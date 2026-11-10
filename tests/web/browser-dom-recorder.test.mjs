import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../../scripts/browser_dom_recorder.js',import.meta.url),'utf8');
function recorder({groups=0,singles=0,embedded=0,unmountNavigation=false,paneTarget='target',sameMessages=false,startWorkspace=false,projection=null,host='host'}={}){
 const window={},timers=[];let clicked=false,now=0,currentProjection=projection,currentHost=host;
 const group={closest:()=>null},groupRows=Array.from({length:groups},()=>({parentElement:{closest:()=>null}}));
 const singleRows=Array.from({length:singles},()=>({closest:()=>null}));
 const embeddedRows=Array.from({length:embedded},()=>({closest:()=>group}));
 const message={getAttribute:()=>clicked&&!sameMessages?'new-message':'old-message'};
 const pane={closest:()=>null,getClientRects:()=>[{}],getAttribute:key=>key==='data-session-id'?(clicked?paneTarget:'old'):key==='data-host-instance-id'?currentHost:key==='data-work-projection'?currentProjection:'false',querySelectorAll(selector){
  if(selector==='article.a-message[data-message-id]')return [message];
  if(selector==='[data-part="execution"]')return [...singleRows,...embeddedRows];
  if(selector==='[data-part="execution-group"],.a-work-group')return groupRows;
  throw Error('Unexpected selector '+selector);
 }};
 const button={disabled:false,getClientRects:()=>[{}],closest(selector){return selector==='[data-session-id]'?{getAttribute:()=> 'target'}:null},
  getAttribute(key){return key==='aria-label'?'Target':key==='aria-current'&&clicked?'page':null},click(){clicked=true}};
 const app={getAttribute:()=>startWorkspace&&!clicked?'workspace':'chat',querySelector(selector){
  if(selector.includes('messages'))return pane;
  if(selector==='.a-work-heading strong')return {getAttribute:()=> 'Target'};
  if(selector.includes('Message Amplifier'))return {value:''};
  return null;
 },querySelectorAll:()=>[]};
 const document={title:'Synthetic',hidden:false,readyState:'complete',getElementById:()=>app,querySelectorAll:()=>clicked&&unmountNavigation?[]:[button]};
 const context={window,document,performance:{now:()=>now,timeOrigin:0},getComputedStyle:()=>({visibility:'visible'}),
  setTimeout:callback=>{timers.push(callback);return timers.length},clearTimeout:()=>{}};
 vm.runInNewContext(source,context);
 return {api:window.__unifiedDomTiming,project(value){currentProjection=value},host(value){currentHost=value},tick(){now+=100;timers.shift()?.()}};
}

test('single-turn Work headers count alongside groups, without embedded double counting',()=>{
 const r=recorder({groups:2,singles:3,embedded:7}),state=r.api.report().current;
 assert.equal(state.workGroups,2);assert.equal(state.workSingleTurns,3);assert.equal(state.workHeaders,5);
 assert.equal(state.work,undefined,'The old groups-only field is not relabeled as completeness');
 r.api.dispose();
});

test('opening a group does not manufacture new top-level Work headers',()=>{
 const closed=recorder({groups:1,singles:1}),open=recorder({groups:1,singles:1,embedded:10});
 assert.equal(closed.api.report().current.workHeaders,open.api.report().current.workHeaders);
 closed.api.dispose();open.api.dispose();
});

test('minimum-header milestone includes single turns and explicitly disclaims completeness',()=>{
 const r=recorder({singles:2});assert.equal(r.api.start({id:'target',minWorkHeaders:2}).started,true);
 r.tick();const result=r.api.report();
 assert.equal(result.schemaVersion,6);assert.equal(result.marks.firstWorkHeaderMs,100);
 assert.equal(result.marks.minimumWorkHeadersMs,100);assert.equal(result.marks.expectedWorkCountMs,undefined);
 assert.ok(result.limitations.some(x=>x.includes('not canonical turns, events or complete history')));
 r.api.dispose();
});

test('no Work headers cannot satisfy a nonzero observation minimum',()=>{
 const r=recorder();r.api.start({id:'target',minWorkHeaders:1});r.tick();
 assert.equal(r.api.report().marks.minimumWorkHeadersMs,undefined);
 assert.equal(r.api.report().marks.readyCandidateMs,undefined);r.api.dispose();
});

test('an old groups-only minimum is rejected rather than silently lowering the gate',()=>{
 const r=recorder({singles:1});assert.match(r.api.start({id:'target',minWork:23}).error,/minWorkHeaders/);
 assert.equal(r.api.report().running,false);r.api.dispose();
});


test('unpinned workspace selection qualifies exact pane content after navigation unmounts',()=>{
 const r=recorder({singles:2,unmountNavigation:true,startWorkspace:true,sameMessages:true});
 assert.equal(r.api.start({id:'target',minWorkHeaders:2,settleMs:300}).started,true);
 for(let i=0;i<5;i++)r.tick();
 const result=r.api.report();
 assert.equal(result.current.selected.length,0);
 assert.equal(result.marks.selectionMs,undefined);
 assert.equal(result.current.paneSessionId,'target');
 assert.equal(result.marks.paneIdentityMs,100);
 assert.equal(result.marks.messagesMs,100);
 assert.equal(result.marks.minimumWorkHeadersMs,100);
 assert.equal(result.reason,'ready-observed');r.api.dispose();
});

for(const paneTarget of ['old',null])test(`optimistic navigation cannot qualify ${paneTarget===null?'unidentified':'old'} pane content`,()=>{
 const r=recorder({singles:2,paneTarget});
 r.api.start({id:'target',minWorkHeaders:2});r.tick();
 const result=r.api.report();
 assert.equal(result.marks.selectionMs,100);
 assert.equal(result.marks.headerMs,100);
 assert.equal(result.marks.paneIdentityMs,undefined);
 assert.equal(result.marks.messagesMs,undefined);
 assert.equal(result.marks.firstWorkHeaderMs,undefined);
 assert.equal(result.marks.readyCandidateMs,undefined);r.api.dispose();
});

const readyReceipt=(patch={})=>JSON.stringify({version:1,hostInstanceId:'host',sessionId:'target',status:'ready',current:true,
 receipt:{generation:7,sourceCount:3,nodeCount:240,turnCount:12},...patch});

test('first headers remain distinct from explicit current projection readiness',()=>{
 const r=recorder({singles:2,projection:readyReceipt({status:'loading',current:false})});
 r.api.start({id:'target',expectWork:true,settleMs:300});r.tick();
 assert.equal(r.api.report().marks.firstWorkHeaderMs,100);
 assert.equal(r.api.report().marks.projectionReadyMs,undefined);
 assert.equal(r.api.report().marks.readyCandidateMs,undefined);
 r.project(readyReceipt());r.tick();
 assert.equal(r.api.report().marks.projectionReadyMs,200);
 assert.equal(r.api.report().marks.readyCandidateMs,200);
 assert.equal(r.api.report().current.workProjection.receipt.turnCount,12);
 r.api.dispose();
});

for(const projection of [null,'{',readyReceipt({receipt:null}),readyReceipt({current:false}),readyReceipt({hostInstanceId:'old'}),readyReceipt({sessionId:'other'})])
 test('missing, stale or malformed projection receipt cannot qualify Work: '+String(projection),()=>{
  const r=recorder({singles:50,projection});r.api.start({id:'target',expectWork:true});r.tick();
  assert.equal(r.api.report().marks.firstWorkHeaderMs,100);
  assert.equal(r.api.report().marks.projectionReadyMs,undefined);
  assert.equal(r.api.report().marks.readyCandidateMs,undefined);r.api.dispose();
 });

test('exact unpinned pane receipt qualifies without a sidebar row and does not claim header total',()=>{
 const r=recorder({projection:readyReceipt(),unmountNavigation:true,startWorkspace:true});
 r.api.start({id:'target',expectWork:true,minWorkHeaders:0});r.tick();
 const report=r.api.report();assert.equal(report.current.selected.length,0);
 assert.equal(report.marks.projectionReadyMs,100);assert.equal(report.marks.firstWorkHeaderMs,undefined);
 assert.equal(report.current.workHeaders,0);assert.equal(report.current.workProjection.receipt.turnCount,12);r.api.dispose();
});

test('host restart and old content pane cannot qualify a pending measurement',()=>{
 const r=recorder({singles:1,projection:readyReceipt()});r.api.start({id:'target',expectWork:true});
 r.host('new');r.project(readyReceipt({hostInstanceId:'new'}));r.tick();
 assert.equal(r.api.report().marks.projectionReadyMs,undefined);r.api.dispose();
 const old=recorder({singles:1,paneTarget:'old',projection:readyReceipt()});old.api.start({id:'target',expectWork:true});old.tick();
 assert.equal(old.api.report().marks.projectionReadyMs,undefined);old.api.dispose();
});
