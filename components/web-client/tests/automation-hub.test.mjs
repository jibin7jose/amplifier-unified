import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,cacheDir:'/tmp/unified-automation-hub-vite',server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AutomationHub}=await server.ssrLoadModule('/src/automation-hub.jsx');
const {ScheduleControls}=await server.ssrLoadModule('/src/schedules.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let root;test.afterEach(async()=>{await act(async()=>root?.unmount());root=null});test.after(()=>server.close());
const row={id:'schedule',sessionId:'owner',revision:2,status:'needs_review',displayState:'needs_review',prompt:'Read the report',destination:'new_task',spec:{kind:'interval',intervalSeconds:60,timezone:'UTC'},nextDue:1800000060,owner:{id:'owner',title:'Stopped owner',status:'stopped'}};
const page={items:[row],total:1,offset:0,limit:30,hasMore:false,readAt:1800000000};
const review={binding:{prompt:row.prompt,spec:row.spec,newTaskConfiguration:{workspace:'/review',bundle:'work',selection:{model:'model'}},newTaskTitle:'Report',newTaskMaxTurns:1},occurrences:[{utc:'2027-01-15T08:01:00Z',dueAt:1800000060}],previewHash:'reviewed-hash',scheduleRevision:2,requiresStopAcknowledgement:true,ownerStatus:'stopped'};
const view=(dispatch,isCurrent=()=>true)=>React.createElement(AutomationHub,{state:{selectedSessionId:'current'},dispatch,isCurrent});
const button=name=>root.root.findAllByType('button').find(row=>row.children.join('')===name);
const text=()=>JSON.stringify(root.toJSON());
test('overview reads one bounded page on demand and ordinary rerenders do not poll',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:page}};
 await act(async()=>{root=create(view(dispatch))});assert.deepEqual(calls,[{action:'schedule.overview',args:{offset:0,limit:30,status:'current'}}]);assert.match(text(),/Schedule snapshot/);
 await act(async()=>root.update(view(dispatch)));assert.equal(calls.length,1);
 await act(async()=>button('Refresh automations').props.onClick());assert.equal(calls.length,2);
});
test('human review requires Stop acknowledgement and lost resume response cannot be blindly repeated',async()=>{
 const calls=[];let finish;
 const dispatch=async(action,args)=>{calls.push({action,args});if(action==='schedule.repairResume')return new Promise(resolve=>finish=resolve);return {accepted:true,result:action==='schedule.repairPreview'?review:page}};
 await act(async()=>{root=create(view(dispatch))});await act(async()=>button('Review future runs').props.onClick());
 assert.ok(button('Resume future runs').props.disabled);await act(async()=>root.root.findByType('input').props.onChange({target:{checked:true}}));assert.equal(button('Resume future runs').props.disabled,false);
 await act(async()=>{button('Resume future runs').props.onClick();button('Resume future runs')?.props.onClick()});
 assert.equal(calls.filter(row=>row.action==='schedule.repairResume').length,1);assert.equal(button('Resume future runs'),undefined);
 await act(async()=>finish({accepted:false,error:'Connection lost. Result unconfirmed.'}));assert.match(text(),/Result unconfirmed/);assert.equal(button('Resume future runs'),undefined);
 assert.deepEqual(calls.at(-1).args,{sessionId:'owner',id:'schedule',expectedRevision:2,previewHash:'reviewed-hash',acknowledgeStop:true});
});
test('cancelled review and browsing never navigate; opening owner uses existing selected-chat controls',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:action==='schedule.repairPreview'?review:page}};
 await act(async()=>{root=create(view(dispatch))});await act(async()=>button('Review future runs').props.onClick());await act(async()=>button('Not now').props.onClick());assert.ok(calls.every(row=>row.action.startsWith('schedule.')));
 await act(async()=>button('Open owning chat').props.onClick());assert.deepEqual(calls.slice(-2),[{action:'session.select',args:{id:'owner'}},{action:'view.update',args:{patch:{panel:'chat-work',runtimeDraft:{section:'schedules'}}}}]);
});
test('unmounted pending overview cannot publish or perform additional actions',async()=>{
 let finish;const calls=[];const dispatch=(action,args)=>{calls.push({action,args});return new Promise(resolve=>finish=resolve)};
 await act(async()=>{root=create(view(dispatch))});await act(async()=>root.unmount());root=null;
 await act(async()=>finish({accepted:true,result:page}));assert.equal(calls.length,1);
});

for(const newer of ['other-chat','closed-panel'])test(`delayed owner selection cannot open controls after ${newer}`,async()=>{
 const calls=[];let finish;let current=true;
 const dispatch=async(action,args)=>{calls.push({action,args});if(action==='session.select')return new Promise(resolve=>finish=resolve);return {accepted:true,result:page}};
 await act(async()=>{root=create(view(dispatch,()=>current))});
 await act(async()=>{button('Open owning chat').props.onClick()});current=false;
 await act(async()=>finish({accepted:true}));assert.deepEqual(calls.map(row=>row.action),['schedule.overview','session.select']);
});

test('Option B empty state leads to a focused owner-bound editor without pagination',async()=>{
 const calls=[];const dispatch=async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...page,items:[],total:0}}};
 await act(async()=>{root=create(view(dispatch))});
 assert.match(text(),/Give a routine a schedule/);
 assert.equal(button('Previous schedules'),undefined);assert.equal(button('More schedules'),undefined);
 await act(async()=>button('Create schedule').props.onClick());
 assert.deepEqual(calls.at(-1),{action:'view.update',args:{patch:{panel:'chat-work',runtimeDraft:{section:'schedules'}}}});
});

test('Option B next occurrence and attention are page-local and separate from last-run state',async()=>{
 const items=[
  {...row,id:'paused',status:'paused',displayState:'paused',prompt:'Paused task',nextDue:1},
  {...row,id:'later',status:'active',displayState:'active',prompt:'Later task',nextDue:200},
  {...row,id:'first',status:'active',displayState:'active',prompt:'First task',nextDue:100,lastRun:{phase:'failed',dueAt:90,detail:'Provider failed'}},
  {...row,id:'uncertain',status:'needs_review',displayState:'needs_review',prompt:'Unknown task',lastRun:{phase:'unknown',dueAt:80}}
 ];
 await act(async()=>{root=create(view(async()=>({accepted:true,result:{...page,items,total:40,hasMore:true}})))});
 const next=root.root.findByProps({'data-part':'automation-next'});assert.equal(next.findByType('h3').children.join(''),'First task');
 assert.match(text(),/On this page/);assert.match(text(),/Needs attention/);assert.match(text(),/Future runs paused/);
 const unknown=root.root.findByProps({'data-automation-id':'uncertain'});
 assert.equal(unknown.findAllByType('button').some(b=>b.children.join('')==='Review future runs'),false);
 assert.equal(root.root.findAll(n=>n.props['data-automation-id']).length,4);
});

test('changing the filter clears the previous page before the new response',async()=>{
 let finish;let reads=0;
 const dispatch=async()=>{if(++reads===1)return {accepted:true,result:page};return new Promise(resolve=>finish=resolve)};
 await act(async()=>{root=create(view(dispatch))});
 await act(async()=>root.root.findByType('select').props.onChange({target:{value:'all'}}));
 assert.equal(root.root.findAll(n=>n.props['data-automation-id']).length,0);
 await act(async()=>finish({accepted:true,result:{...page,items:[],total:0}}));
});

test('an admitted one-shot without another occurrence does not invent a review blocker',async()=>{
 const oneShot={...row,status:'active',displayState:'active',nextDue:null,lastRun:{phase:'accepted',dueAt:1800000000},spec:{kind:'once',startAt:'2027-01-15T08:00:00Z',timezone:'UTC'}};
 await act(async()=>{root=create(view(async()=>({accepted:true,result:{...page,items:[oneShot]}})))});
 assert.match(text(),/No further occurrence/);assert.match(text(),/Input accepted/);
 assert.doesNotMatch(text(),/Review required before future runs/);
});

test('hub Create selects a new draft, while manage and Review retain existing edits without retaining authority',async()=>{
 const id='cross-surface-owner',calls=[];
 const saved={...row,sessionId:id,status:'active',spec:{kind:'daily',timezone:'UTC',startDate:'2027-01-18',localTime:'08:30'},runs:[],kind:'task',missedRunPolicy:'skip',notificationPolicy:'changes'};
 const session={id,title:'Draft owner',schedules:[saved]},original=structuredClone(saved);
 let panel='editor';
 const render=()=>panel==='automation'?React.createElement(AutomationHub,{state:{selectedSessionId:id,sessions:[session]},dispatch,isCurrent:owner=>owner===id}):React.createElement(ScheduleControls,{session,act:dispatch,focused:true});
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='view.update'){panel='editor';root.update(render())}
  return {accepted:true,result:action==='schedule.overview'?{...page,items:[{...saved,owner:{id,title:session.title}}]}:action==='schedule.preview'?{binding:{...args,...(args.destination==='new_task'?{newTaskConfiguration:{workspace:'/example',bundle:'work',selection:{model:'test-model'}}}:{})},previewHash:'fresh-review',occurrences:[{utc:'2027-01-18T08:30:00Z',dueAt:1800261000}]}:{}};
 };
 const type=async value=>act(async()=>root.root.findByProps({id:'schedule-prompt'}).props.onChange({target:{value}}));
 const navigate=async()=>{panel='automation';await act(async()=>root.update(render()))};
 await act(async()=>{root=create(render())});await type('New draft already in this tab');
 await act(async()=>button('Review schedule').props.onClick());await type('Unsaved edits to the existing schedule');
 await act(async()=>button('Preview schedule').props.onClick());assert.ok(button('Save and activate schedule'));
 await navigate();await act(async()=>button('Open owning chat').props.onClick());
 assert.equal(root.root.findByProps({id:'schedule-prompt'}).props.value,'Unsaved edits to the existing schedule');
 assert.equal(button('Save and activate schedule'),undefined,'manage cannot retain activation authority');
 await navigate();await act(async()=>button('Create schedule').props.onClick());
 assert.match(text(),/New schedule/);assert.doesNotMatch(text(),/Editing saved revision/);
 assert.equal(root.root.findByProps({id:'schedule-prompt'}).props.value,'New draft already in this tab');
 await act(async()=>button('Preview schedule').props.onClick());await act(async()=>button('Save and activate schedule').props.onClick());
 const mutations=calls.filter(call=>['schedule.create','schedule.update'].includes(call.action));
 assert.equal(mutations.length,1);assert.equal(mutations[0].action,'schedule.create');assert.equal(mutations[0].args.id,undefined);assert.equal(mutations[0].args.expectedRevision,0);
 assert.deepEqual(saved,original);
 await act(async()=>button('Review schedule').props.onClick());
 assert.equal(root.root.findByProps({id:'schedule-prompt'}).props.value,'Unsaved edits to the existing schedule','creating does not discard the existing edit draft');
 assert.equal(button('Save and activate schedule'),undefined);
});

test('approved fidelity separates schedule/run state and disambiguates workspace in compact row groups',async()=>{
 const items=[
  {...row,id:'failed',status:'active',displayState:'active',owner:{id:'a',title:'Planning',workspace:'/teams/alpha'},lastRun:{phase:'failed',dueAt:1799999900}},
  {...row,id:'unknown',owner:{id:'b',title:'Planning',workspace:'/teams/beta'},lastRun:{phase:'unknown',dueAt:1799999800}}
 ];
 const render=()=>React.createElement(AutomationHub,{state:{selectedSessionId:'a',sessions:[items[0].owner]},isCurrent:()=>true,dispatch:async()=>({accepted:true,result:{...page,items}})});
 await act(async()=>{root=create(render())});
 for(const item of items){
  const card=root.root.findByProps({'data-automation-id':item.id});
  assert.equal(card.props['data-run-state'],item.lastRun.phase);
  assert.equal(card.findByProps({className:'a-automation-identity'}).findByType('h3').children.join(''),item.prompt);
  assert.equal(card.findByProps({'data-part':'automation-workspace'}).children.join(''),item.owner.workspace);
  assert.equal(card.findByProps({'data-part':'automation-run-state'}).children.join(''),item.lastRun.phase==='failed'?'Failed':'Unknown outcome');
 }
 assert.match(text(),/\/teams\/alpha/);assert.match(text(),/\/teams\/beta/);
 assert.equal(root.root.findByProps({'data-part':'automation-next'}).findByProps({'data-part':'automation-workspace'}).children.join(''),'/teams/alpha');
 const attention=root.root.findByProps({'aria-label':'Needs attention'});
 const disclosure=attention.findByProps({className:'a-automation-exceptions'});
 assert.equal(disclosure.props.open,true);
 assert.match(disclosure.findAllByType('summary')[0].children.join(''),/on this page/);
 await act(async()=>disclosure.props.onToggle({currentTarget:{open:false}}));
 await act(async()=>button('Refresh automations').props.onClick());
 assert.equal(root.root.findByProps({className:'a-automation-exceptions'}).props.open,false,'refresh preserves disclosure choice');
});

test('approved fidelity states no attention only for a loaded populated page, never global health',async()=>{
 let result={...page,items:[{...row,status:'paused',displayState:'paused'}],total:40,hasMore:true};
 await act(async()=>{root=create(view(async()=>({accepted:true,result})))});
 assert.match(text(),/No schedules need attention on this page/);
 assert.doesNotMatch(text(),/All schedules are healthy/);
 result={...page,items:[],total:0};await act(async()=>button('Refresh automations').props.onClick());
 assert.doesNotMatch(text(),/No schedules need attention on this page/);
 assert.equal(root.root.findAllByProps({'aria-label':'Needs attention'}).length,0);
});
