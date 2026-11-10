import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({configFile:false,cacheDir:'node_modules/.vite-schedule-editor-tests',server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ScheduleControls,ScheduleVisibilityContext}=await server.ssrLoadModule('/src/schedules.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let root;
test.afterEach(async()=>{await act(async()=>root?.unmount());root=null});
test.after(()=>server.close());
const owner=(id,extra={})=>({id,title:'Report owner',workspace:'/example',bundle:'work',selection:{model:'example-model',effort:'high'},executionRevision:1,interruptionRevision:0,schedules:[],...extra});
const record=(sessionId,extra={})=>({id:'saved-schedule',sessionId,revision:9,status:'active',prompt:'Read the report',destination:'new_task',newTaskTitle:'Report',newTaskMaxTurns:3,kind:'task',spec:{kind:'weekly',timezone:'UTC',startDate:'2027-01-15',localTime:'09:30',weekdays:[1,4]},missedRunPolicy:'skip',notificationPolicy:'failures_only',runs:[],...extra});
const preview=args=>({binding:{...args,...(args.destination==='new_task'?{newTaskConfiguration:{workspace:'/example',bundle:'work',selection:{model:'example-model',effort:'high'}}}:{})},occurrences:[{utc:'2027-01-15T09:30:00Z',dueAt:1800000000}],previewHash:'exact-review',daylightSaving:'Repeated local times run once.'});
const view=(session,dispatch,focused=true)=>React.createElement(ScheduleControls,{session,act:dispatch,focused});
const button=name=>root.root.findAllByType('button').find(node=>node.children.join('')===name);
const field=id=>root.root.findByProps({id});
const text=()=>JSON.stringify(root.toJSON());
const content=node=>node.children.map(child=>typeof child==='string'?child:content(child)).join('');
const change=async(id,value)=>act(async()=>field(id).props.onChange({target:{value}}));
const click=async name=>act(async()=>button(name).props.onClick());
const passive=(calls)=>(action,args)=>{calls.push({action,args});return Promise.resolve({accepted:true,result:action==='schedule.preview'?preview(args):{items:[]}})};

test('hidden owner controls retain details but discard activation preview on return',async()=>{
 const calls=[],session=owner('hidden-owner'),dispatch=passive(calls);
 const render=visible=>React.createElement(ScheduleVisibilityContext.Provider,{value:visible},view(session,dispatch));
 await act(async()=>{root=create(render(true))});await change('schedule-prompt','Keep the draft, not the authority');await click('Preview schedule');
 assert.ok(button('Save and activate schedule'));
 await act(async()=>root.update(render(false)));assert.equal(root.toJSON(),null);
 await act(async()=>root.update(render(true)));assert.equal(field('schedule-prompt').props.value,'Keep the draft, not the authority');
 assert.equal(button('Save and activate schedule'),undefined);
});

test('focused entry opens and focuses the owner editor, reads schedules once, and never starts runtime',async()=>{
 const calls=[],focused=[];const session=owner('focused-owner'),dispatch=passive(calls);
 await act(async()=>{root=create(view(session,dispatch),{createNodeMock:node=>({focus:()=>focused.push(node.props.id||node.type)})})});
 assert.deepEqual(calls,[{action:'schedule.list',args:{sessionId:session.id}}]);
 assert.match(text(),/Report owner/);assert.ok(field('schedule-prompt'));assert.ok(focused.includes('schedule-prompt'));
 await act(async()=>root.update(view({...session},dispatch)));assert.equal(calls.length,1);
 await change('schedule-prompt','Focus review after preview');await click('Preview schedule');assert.equal(focused.at(-1),'h4');
 await click('Cancel');assert.equal(focused.at(-1),'button');
});

test('editor and same-task confirmation show the owning workspace, not the execution directory',async()=>{
 const calls=[],session=owner('workspace-owner',{workspace:'/projects/reports',workingDirectory:'/checkouts/report-run'}),dispatch=passive(calls);
 await act(async()=>{root=create(view(session,dispatch))});
 assert.ok(root.root.findAllByType('p').map(content).includes('Workspace: /projects/reports'));
 assert.doesNotMatch(text(),/\/checkouts\/report-run/);
 await change('schedule-prompt','Review in the owning workspace');await click('Preview schedule');
 const review=root.root.findByProps({'data-part':'schedule-preview'});
 assert.ok(review.findAllByType('p').map(content).includes('Workspace: /projects/reports'));
 assert.ok(review.findAllByType('p').map(content).includes('Continue this saved task.'));
 assert.doesNotMatch(content(review),/\/checkouts\/report-run/);
 assert.deepEqual(calls.map(row=>row.action),['schedule.list','schedule.preview']);
 await act(async()=>root.update(view({...session,workspace:'/projects/updated-reports'},dispatch)));
 assert.equal(button('Save and activate schedule'),undefined);
 assert.ok(root.root.findAllByType('p').map(content).includes('Workspace: /projects/updated-reports'));
});

test('new-task confirmation keeps its bound execution workspace beside the owning workspace context',async()=>{
 const calls=[],session=owner('new-task-workspace-owner',{workspace:'/projects/reports',workingDirectory:'/checkouts/current'});
 const dispatch=(action,args)=>{calls.push({action,args});const result=action==='schedule.preview'?preview(args):{items:[]};if(action==='schedule.preview')result.binding.newTaskConfiguration.workspace='/checkouts/new-task';return Promise.resolve({accepted:true,result})};
 await act(async()=>{root=create(view(session,dispatch))});
 await change('schedule-prompt','Run a fresh report');await change('schedule-destination','new_task');await click('Preview schedule');
 const review=root.root.findByProps({'data-part':'schedule-preview'}),destination=review.findByProps({'data-part':'schedule-destination-preview'});
 assert.ok(review.findAllByType('p').map(content).includes('Workspace: /projects/reports'));
 assert.ok(destination.findAllByType('p').map(content).includes('Workspace: /checkouts/new-task'));
 assert.doesNotMatch(content(review),/\/checkouts\/current/);
 assert.deepEqual(calls.map(row=>row.action),['schedule.list','schedule.preview']);
});

for(const [spec,expected] of [
 [{kind:'once',timezone:'America/Los_Angeles',startAt:'2027-01-16T01:30:00+00:00'},'2027-01-15'],
 [{kind:'interval',timezone:'Asia/Tokyo',startAt:'2027-01-15T18:30:00+00:00',intervalSeconds:3600},'2027-01-16'],
 [{kind:'daily',timezone:'America/Los_Angeles',startDate:'2027-01-16',localTime:'09:30'},'2027-01-16'],
 [{kind:'weekly',timezone:'America/Los_Angeles',startDate:'2027-01-18',localTime:'09:30',weekdays:[0]},'2027-01-18'],
])test(`${spec.kind} confirmation shows Start date from the normalized binding in its timezone`,async()=>{
 const calls=[],session=owner(`start-date-${spec.kind}`);
 const dispatch=(action,args)=>{calls.push({action,args});return Promise.resolve({accepted:true,result:action==='schedule.preview'?preview({...args,spec}):{items:[]}})};
 await act(async()=>{root=create(view(session,dispatch))});
 await change('schedule-prompt','Show the reviewed start date');await change('schedule-recurrence',spec.kind);
 await change('schedule-date','2027-02-01');await change('schedule-zone','UTC');await click('Preview schedule');
 const review=root.root.findByProps({'data-part':'schedule-preview'}),details=review.findByType('dl').children;
 const label=details.findIndex(node=>node.type==='dt'&&content(node)==='Start date');
 assert.notEqual(label,-1);assert.equal(details[label+1].type,'dd');
 assert.equal(content(details[label+1]),`${expected} (${spec.timezone})`);
 assert.equal(field('schedule-date').props.value,'2027-02-01');assert.equal(field('schedule-zone').props.value,'UTC');
 assert.deepEqual(calls.map(row=>row.action),['schedule.list','schedule.preview']);
});

test('normal entry waits for Create; every field choice survives Cancel, reopen, and panel unmount without retaining a preview',async()=>{
 const calls=[],session=owner('retained-owner'),dispatch=passive(calls);
 await act(async()=>{root=create(view(session,dispatch,false))});assert.equal(calls.length,0);assert.equal(button('Preview schedule'),undefined);
 await click('Create schedule');
 assert.deepEqual(field('schedule-recurrence').findAllByType('option').map(node=>node.props.value),['once','daily','weekly','interval']);
 assert.deepEqual(field('schedule-destination').findAllByType('option').map(node=>node.props.value),['same_task','new_task']);
 assert.deepEqual(field('schedule-missed').findAllByType('option').map(node=>node.props.value),['latest','skip']);
 assert.deepEqual(field('schedule-notify').findAllByType('option').map(node=>node.props.value),['changes','always','failures_only']);
 await change('schedule-recurrence','interval');await change('schedule-interval','17');
 const choices={'schedule-prompt':'Compare the weekly report','schedule-destination':'new_task','schedule-title':'Fresh report','schedule-turns':'7','schedule-kind':'task','schedule-recurrence':'weekly','schedule-date':'2027-01-15','schedule-time':'16:45','schedule-zone':'America/Los_Angeles','schedule-missed':'skip','schedule-notify':'failures_only'};
 for(const [id,value] of Object.entries(choices))await change(id,value);
 const weekdays=()=>root.root.findAllByType('input').filter(node=>node.props.type==='checkbox');
 await act(async()=>weekdays()[4].props.onChange({target:{checked:true}}));
 await click('Preview schedule');assert.ok(button('Save and activate schedule'));assert.match(text(),/example-model/);assert.match(text(),/Maximum turns/);
 await click('Cancel');assert.match(text(),/Unsaved details kept in this tab/);assert.equal(button('Preview schedule'),undefined);
 await click('Create schedule');
 for(const [id,value] of Object.entries(choices))assert.equal(field(id).props.value,value);
 assert.equal(weekdays()[4].props.checked,true);assert.equal(button('Save and activate schedule'),undefined);
 await act(async()=>root.unmount());root=null;
 await act(async()=>{root=create(view(session,dispatch))});
 for(const [id,value] of Object.entries(choices))assert.equal(field(id).props.value,value);
 assert.equal(weekdays()[4].props.checked,true);assert.equal(button('Save and activate schedule'),undefined);
 assert.ok(calls.every(row=>['schedule.list','schedule.preview'].includes(row.action)));
 await change('schedule-recurrence','interval');assert.equal(field('schedule-interval').props.value,'17');
});

test('cancelled edits reopen the same selected revision, while Create deliberately starts a new schedule',async()=>{
 const calls=[],session=owner('retained-existing-owner');session.schedules=[record(session.id)];const dispatch=passive(calls);
 await act(async()=>{root=create(view(session,dispatch))});await click('Review schedule');await change('schedule-prompt','Keep my edited instructions');
 await click('Cancel');await click('Review schedule');assert.equal(field('schedule-prompt').props.value,'Keep my edited instructions');
 await click('Cancel');await click('Continue editing schedule');assert.equal(field('schedule-prompt').props.value,'Keep my edited instructions');
 await click('Create schedule');assert.equal(field('schedule-prompt').props.value,'');
 await change('schedule-prompt','A distinct new schedule');await click('Preview schedule');await click('Save and activate schedule');
 assert.equal(calls.at(-1).action,'schedule.create');assert.equal(calls.at(-1).args.expectedRevision,0);
});

test('Review loads the exact saved record and revision; a changed revision refuses activation',async()=>{
 const calls=[];const session=owner('existing-owner');session.schedules=[record(session.id)];const dispatch=passive(calls);
 await act(async()=>{root=create(view(session,dispatch))});await click('Review schedule');
 assert.equal(field('schedule-prompt').props.value,session.schedules[0].prompt);assert.equal(field('schedule-turns').props.value,3);
 await click('Preview schedule');
 await act(async()=>root.update(view({...session,schedules:[{...session.schedules[0],revision:10}]},dispatch)));
 assert.ok(!button('Save and activate schedule')||button('Save and activate schedule').props.disabled);
 assert.equal(calls.filter(row=>row.action==='schedule.update').length,0);
 await click('Review schedule');await click('Preview schedule');await click('Save and activate schedule');
 const saved=calls.find(row=>row.action==='schedule.update');assert.equal(saved.args.expectedRevision,10);assert.equal(saved.args.id,'saved-schedule');assert.equal(saved.args.sessionId,session.id);
});

for(const interruption of ['edit','cancel','owner','configuration','unmount'])test(`late preview cannot survive ${interruption}`,async()=>{
 const calls=[];let finish;const session=owner(`preview-${interruption}`);
 const dispatch=(action,args)=>{calls.push({action,args});return action==='schedule.preview'?new Promise(resolve=>finish=()=>resolve({accepted:true,result:preview(args)})):Promise.resolve({accepted:true,result:{items:[]}})};
 await act(async()=>{root=create(view(session,dispatch))});await change('schedule-prompt','Original instructions');
 await act(async()=>{button('Preview schedule').props.onClick()});
 if(interruption==='edit')await change('schedule-prompt','Newer instructions');
 if(interruption==='cancel')await click('Cancel');
 if(interruption==='owner')await act(async()=>root.update(view(owner('different-owner'),dispatch)));
 if(interruption==='configuration')await act(async()=>root.update(view({...session,executionRevision:2},dispatch)));
 if(interruption==='unmount'){await act(async()=>root.unmount());root=null}
 await act(async()=>finish());
 if(interruption==='unmount')await act(async()=>{root=create(view(session,dispatch))});
 assert.equal(button('Save and activate schedule'),undefined);
 if(interruption==='owner')assert.equal(field('schedule-prompt').props.value,'');
 if(interruption==='edit')assert.equal(field('schedule-prompt').props.value,'Newer instructions');
 assert.ok(calls.every(row=>['schedule.preview','schedule.list'].includes(row.action)));
});

test('every draft edit clears review, and same-frame duplicate or lost saves cannot reuse its one-use confirmation',async()=>{
 const calls=[];let finish;const session=owner('lost-save-owner');
 const dispatch=(action,args)=>{calls.push({action,args});return action==='schedule.create'?new Promise(resolve=>finish=resolve):Promise.resolve({accepted:true,result:action==='schedule.preview'?preview(args):{items:[]}})};
 await act(async()=>{root=create(view(session,dispatch))});await change('schedule-prompt','Original');await click('Preview schedule');
 await change('schedule-notify','always');assert.equal(button('Save and activate schedule'),undefined);await click('Preview schedule');
 const save=button('Save and activate schedule').props.onClick;
 await act(async()=>{save();save()});assert.equal(calls.filter(row=>row.action==='schedule.create').length,1);assert.equal(button('Save and activate schedule'),undefined);
 await act(async()=>finish({accepted:false,error:'Connection lost. Result unconfirmed.'}));
 assert.match(text(),/Result unconfirmed/);await act(async()=>save());assert.equal(calls.filter(row=>row.action==='schedule.create').length,1);
 const submitted=calls.find(row=>row.action==='schedule.create').args;assert.equal(submitted.expectedRevision,0);assert.equal(submitted.previewHash,'exact-review');assert.equal(submitted.notificationPolicy,'always');
});

test('late confirmed save releases the gate without erasing newer draft edits',async()=>{
 const calls=[];let finish;const session=owner('newer-edit-owner');
 const dispatch=(action,args)=>{calls.push({action,args});return action==='schedule.create'?new Promise(resolve=>finish=resolve):Promise.resolve({accepted:true,result:action==='schedule.preview'?preview(args):{items:[]}})};
 await act(async()=>{root=create(view(session,dispatch))});await change('schedule-prompt','Reviewed instructions');await click('Preview schedule');
 await act(async()=>{button('Save and activate schedule').props.onClick()});await change('schedule-prompt','Keep this newer draft');
 await act(async()=>finish({accepted:true,result:{schedule:record(session.id)}}));
 assert.equal(field('schedule-prompt').props.value,'Keep this newer draft');assert.equal(button('Preview schedule').props.disabled,false);assert.equal(button('Save and activate schedule'),undefined);
 await click('Cancel');await click('Create schedule');assert.equal(field('schedule-prompt').props.value,'Keep this newer draft');
});

test('save result after leaving an owner cannot clear its retained draft or target another owner',async()=>{
 const calls=[];let finish;const session=owner('departed-owner');
 const dispatch=(action,args)=>{calls.push({action,args});return action==='schedule.create'?new Promise(resolve=>finish=resolve):Promise.resolve({accepted:true,result:action==='schedule.preview'?preview(args):{items:[]}})};
 await act(async()=>{root=create(view(session,dispatch))});await change('schedule-prompt','Keep after leaving');await click('Preview schedule');
 const save=button('Save and activate schedule').props.onClick;await act(async()=>{save()});
 await act(async()=>root.update(view(owner('new-owner-after-save'),dispatch)));await act(async()=>finish({accepted:true,result:{schedule:record(session.id)}}));await act(async()=>save());
 assert.equal(field('schedule-prompt').props.value,'');assert.equal(calls.filter(row=>row.action==='schedule.create').length,1);
 await act(async()=>root.update(view(session,dispatch)));assert.equal(field('schedule-prompt').props.value,'Keep after leaving');assert.equal(button('Save and activate schedule'),undefined);
});

test('unknown run disables review and activation but keeps exact-revision evidence reconciliation and confirmed destination links',async()=>{
 const calls=[],session=owner('unknown-owner');session.schedules=[record(session.id,{status:'needs_review',runs:[{id:'uncertain-run',revision:4,phase:'unknown',dueAt:1800000000,detail:'Outcome uncertain',destinationSessionId:'reserved-only',creationConfirmed:false},{id:'known-run',revision:2,phase:'completed',dueAt:1799900000,destinationSessionId:'confirmed-target',creationConfirmed:true}]})];
 await act(async()=>{root=create(view(session,passive(calls)))});
 assert.equal(button('Review schedule').props.disabled,true);await act(async()=>button('Review schedule').props.onClick());assert.equal(field('schedule-prompt').props.value,'');
 const links=root.root.findAllByType('button').filter(node=>node.children.join('')==='Open new task');assert.equal(links.length,1);
 await act(async()=>links[0].props.onClick());assert.deepEqual(calls.at(-1),{action:'session.select',args:{id:'confirmed-target'}});
 const evidence=root.root.findAllByType('textarea').find(node=>node.props['aria-label']==='Reconciliation evidence');
 await act(async()=>evidence.props.onChange({target:{value:'Checked the saved result; do not replay.'}}));await click('Mark run abandoned');
 assert.deepEqual(calls.at(-1),{action:'schedule.reconcile',args:{sessionId:session.id,runId:'uncertain-run',expectedRevision:4,resolution:'abandoned',evidence:'Checked the saved result; do not replay.'}});
 assert.equal(calls.filter(row=>['schedule.create','schedule.update','schedule.preview'].includes(row.action)).length,0);
});

test('a new unknown run invalidates an already open existing-schedule review',async()=>{
 const calls=[],session=owner('later-unknown-owner');session.schedules=[record(session.id)];const dispatch=passive(calls);
 await act(async()=>{root=create(view(session,dispatch))});await click('Review schedule');await click('Preview schedule');
 const save=button('Save and activate schedule').props.onClick;
 await act(async()=>root.update(view({...session,schedules:[{...session.schedules[0],runs:[{id:'new-unknown',revision:1,phase:'unknown',dueAt:1800000000}]}]},dispatch)));
 assert.ok(button('Preview schedule').props.disabled);assert.ok(!button('Save and activate schedule')||button('Save and activate schedule').props.disabled);
 await change('schedule-prompt','Do not apply edits to an unknown run');
 assert.equal(field('schedule-prompt').props.value,session.schedules[0].prompt);
 await act(async()=>save());assert.equal(calls.filter(row=>row.action==='schedule.update').length,0);
});

test('a preview for a different owner is rejected, not retargeted',async()=>{
 const calls=[],session=owner('wrong-binding-owner');
 const dispatch=(action,args)=>{calls.push({action,args});return Promise.resolve({accepted:true,result:action==='schedule.preview'?preview({...args,sessionId:'another-owner'}):{items:[]}})};
 await act(async()=>{root=create(view(session,dispatch))});await change('schedule-prompt','Bound to this owner');await click('Preview schedule');
 assert.match(text(),/did not identify this owning chat/);assert.equal(button('Save and activate schedule'),undefined);
 assert.equal(calls.at(-1).args.sessionId,session.id);
});

for(const name of ['Pause schedule','Cancel schedule'])test(`${name} consumes the exact revision once and shows a rejected receipt inline`,async()=>{
 const calls=[];let finish;const session=owner(`mutation-${name}`);session.schedules=[record(session.id)];
 const dispatch=(action,args)=>{calls.push({action,args});return action==='schedule.list'?Promise.resolve({accepted:true,result:{items:[]}}):new Promise(resolve=>finish=resolve)};
 await act(async()=>{root=create(view(session,dispatch))});const mutate=button(name).props.onClick;
 await act(async()=>{mutate();mutate()});assert.equal(calls.length,2);assert.equal(calls[1].args.expectedRevision,9);
 await act(async()=>finish({accepted:false,error:'Schedule changed. Refresh before reviewing.'}));assert.match(text(),/Schedule changed/);
 await act(async()=>mutate());assert.equal(calls.length,2);assert.equal(button(name).props.disabled,true);assert.match(text(),/future runs only/i);
});
