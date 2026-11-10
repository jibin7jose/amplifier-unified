import nodeTest from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {TaskContinuity}=await vite.ssrLoadModule('/src/task-continuity.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const test=(name,fn)=>nodeTest(name,{timeout:5000},fn);
nodeTest.after(()=>vite.close());
const content=node=>typeof node==='string'?node:(node?.children||[]).map(content).join('');
const text=root=>content(root.toJSON());
const button=(root,action)=>root.root.findByProps({'data-action':action});
const accepted=result=>({accepted:true,result});
const session={id:'owner',task:null};
// Exact bounded task.get producer shape from b38189e task_continuity._project.
const projection=(revision=3,extra={})=>({status:'available',task:{
 id:'task-one',revision,appliedRevision:revision-1,status:'paused',continuationEpoch:2,
 createdAt:1,updatedAt:2,completedAt:null,maxTurns:5,correctionCount:1,
 originalObjectiveAvailable:true,originalObjectiveRef:{action:'task.detail',args:{sessionId:'owner',taskId:'task-one',expectedRevision:revision,field:'originalObjective'}},
 historyRef:{action:'task.history',args:{sessionId:'owner',taskId:'task-one'}},
 objective:'Verify the retained report',objectiveCount:26,constraints:['Keep original files'],constraintsCount:1,
 corrections:[{revision:2,at:2,origin:'user',text:'Use the exact candidate'}],correctionsCount:1,
 questionIds:['question-one'],questionIdsCount:1,operationIds:['operation-one'],operationIdsCount:1,
 artifactRefs:['artifact-one'],artifactRefsCount:1,completionEvidence:[],completionEvidenceCount:0,blockedReason:null,
 ...extra},historyCount:0,historyRef:{action:'task.history',args:{sessionId:'owner'}},goal:null,
 detailRequired:false,detailRefs:[],continuity:{supported:false,status:'not_observed_cold'},continuationSupported:null});
const missingProjection=(revision,values)=>{
 const result=projection(revision);
 result.detailRequired=true;
 result.detailRefs=Object.entries(values).map(([field,value])=>{
  result.task[field]=null;result.task[field+'Count']=Array.from(value).length;
  return {action:'task.detail',args:{sessionId:'owner',taskId:'task-one',expectedRevision:revision,field}};
 });
 return result;
};
// task.detail offsets and totals are Unicode code points; lists travel as JSON.
const detailPage=(args,value)=>{
 const valueText=typeof value==='string'?value:JSON.stringify(value),characters=Array.from(valueText);
 const end=Math.min(args.offset+args.limit,characters.length);
 return {taskId:args.taskId,revision:args.expectedRevision,field:args.field,historyRevision:null,
  offset:args.offset,total:characters.length,sha256:createHash('sha256').update(valueText).digest('hex'),
  text:characters.slice(args.offset,end).join(''),truncated:end<characters.length,nextOffset:end<characters.length?end:null};
};
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};
async function flush(){await act(async()=>{await Promise.resolve()})}
async function click(root,action){await act(async()=>{await button(root,action).props.onClick()})}
const render=(state,owner,dispatch)=>React.createElement(TaskContinuity,{state,session:owner,act:dispatch});
// Node executes .mjs without JSX; use the same element factory throughout.
async function open(dispatch,state={},owner=session){let root;await act(async()=>{root=create(render(state,owner,dispatch))});return root}
async function close(root){await act(async()=>root?.unmount())}

test('Load saved task consumes the cold result locally without ordinary-state publication or warming',async()=>{
 const state=Object.freeze({runtimeControl:Object.freeze({})}),owner=Object.freeze({...session}),calls=[];
 const payload=projection();let root;
 try{
  root=await open(async(...args)=>{calls.push(args);return accepted(payload)},state,owner);
  assert.deepEqual(calls,[],'Mounting is passive');
  await click(root,'task.get');
  assert.match(text(root),/Verify the retained report/);assert.match(text(root),/Task paused · revision 3/);
  assert.match(text(root),/applies at the next request/);assert.match(text(root),/not observed.*cold/i);
  assert.deepEqual(calls,[['task.get',{sessionId:'owner'}]]);
  assert.deepEqual(state,{runtimeControl:{}});assert.equal(owner.task,null);
 }finally{await close(root)}
});

test('mutation uses the observed cold revision and consumes its exact successful projection',async()=>{
 const calls=[];let root;
 try{
  root=await open(async(action,args)=>{calls.push([action,args]);return accepted(action==='task.get'?projection(7):{...projection(8,{status:'active',appliedRevision:8}),receipt:{taskId:'task-one',revision:8,commandId:'host-bound'},currentRevision:8,snapshotIsCurrent:true})});
  await click(root,'task.get');await click(root,'task.resume');
  assert.deepEqual(calls[1],['task.resume',{sessionId:'owner',expectedRevision:7}]);
  assert.match(text(root),/Task active · revision 8/);
  await click(root,'task.pause');assert.equal(calls[2][1].expectedRevision,8);
 }finally{await close(root)}
});

test('bounded null fields never fall back to stale full task details or authorize completion',async()=>{
 const stale=projection(2).task;stale.objective='PRIVATE OLD OBJECTIVE';stale.questionIds=['PRIVATE OLD QUESTION'];
 const state={runtimeControl:{owner:{'task.get':{task:stale}}}},before=structuredClone(state);
 const value=projection(3,{objective:null,objectiveCount:6000,questionIds:null,questionIdsCount:2,corrections:null,correctionsCount:9});
 value.detailRequired=true;value.detailRefs=['objective','questionIds','corrections'].map(field=>({action:'task.detail',args:{sessionId:'owner',taskId:'task-one',expectedRevision:3,field}}));
 value.goal={task_id:'task-one',task_revision:3,cap:5,turns_used:2,continuations:1};
 const calls=[];let root;
 try{
  root=await open(async(...args)=>{calls.push(args);return accepted(value)},state,{...session,task:stale});
  await click(root,'task.get');
  assert.match(text(root),/not loaded/i);assert.doesNotMatch(text(root),/PRIVATE OLD/);
  assert.doesNotMatch(text(root),/Questions: None/);assert.match(text(root),/Questions: Not loaded \(2\)/);
  await act(async()=>root.root.findByProps({id:'task-evidence'}).props.onChange({target:{value:'A report exists'}}));
  assert.equal(button(root,'task.complete').props.disabled,true);
  assert.deepEqual(calls.map(x=>x[0]),['task.get'],'No implicit task.detail, runtime.control or warm reads');
  assert.deepEqual(state,before,'Bounded display does not replace canonical state');
 }finally{await close(root)}
});

test('explicit paged details display exact current fields and allow completion without changing the cold projection',async()=>{
 const values={objective:'Review 🧭 the retained report. '.repeat(180),questionIds:['question-one','question-two'],
  corrections:[{revision:2,at:2,origin:'user',text:'Keep the exact candidate. '.repeat(110)}]};
 const value=missingProjection(3,values),before=structuredClone(value),calls=[];let root;
 const dispatch=async(action,args)=>{
  calls.push([action,args]);
  return accepted(action==='task.detail'?detailPage(args,values[args.field]):action==='task.complete'?projection(4,{status:'completed',objective:values.objective}):value);
 };
 try{
  root=await open(dispatch);await click(root,'task.get');
  await act(async()=>root.root.findByProps({id:'task-evidence'}).props.onChange({target:{value:'The retained report was verified'}}));
  assert.equal(button(root,'task.complete').props.disabled,true);
  assert.deepEqual(calls.map(([action])=>action),['task.get']);
  await click(root,'task.detail');
  assert.equal(root.root.findByType('strong').children.join(''),values.objective);
  assert.ok(text(root).includes(values.corrections[0].text));assert.match(text(root),/Questions: question-one, question-two/);
  assert.equal(button(root,'task.complete').props.disabled,false);
  assert.equal(root.root.findAllByProps({'data-action':'task.detail'}).length,0);
  const pages=calls.filter(([action])=>action==='task.detail');
  assert.deepEqual(pages.filter(([,args])=>args.field==='objective').map(([,args])=>args.offset),[0,2000,4000]);
  assert.ok(pages.every(([,args])=>args.taskId==='task-one'&&args.expectedRevision===3&&args.limit===2000));
  assert.deepEqual(value,before,'Exact details remain private to the reader');
  const applied=structuredClone(value);applied.task.appliedRevision=3;
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':applied}}},session,dispatch)));
  assert.equal(root.root.findByType('strong').children.join(''),values.objective,'An applied boundary does not change the task revision');
  assert.doesNotMatch(text(root),/applies at the next request/);
  await click(root,'task.complete');
  assert.deepEqual(calls.at(-1),['task.complete',{sessionId:'owner',expectedRevision:3,condition:values.objective,evidence:['The retained report was verified']}]);
  assert.match(text(root),/Task completed · revision 4/);
  assert.ok(calls.every(([action])=>['task.get','task.detail','task.complete'].includes(action)),'No history or runtime hydration');
 }finally{await close(root)}
});

for(const mismatch of ['revision','sha256'])test('a stale detail page '+mismatch+' cannot combine with current content, and an explicit retry recovers',async()=>{
 const values={objective:'Exact current objective. '.repeat(200)},calls=[];let root,fail=true;
 const dispatch=async(action,args)=>{
  calls.push([action,args]);
  const page=detailPage(args,values.objective);
  if(fail&&args.offset){fail=false;page[mismatch]=mismatch==='revision'?2:'stale-content-hash'}
  return accepted(page);
 };
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':missingProjection(3,values)}}});
  await act(async()=>root.root.findByProps({id:'task-evidence'}).props.onChange({target:{value:'Verified'}}));
  await click(root,'task.detail');
  assert.match(text(root),/detail page changed/);assert.match(text(root),/Task objective not loaded/);
  assert.equal(button(root,'task.complete').props.disabled,true);assert.equal(calls.length,2,'Stop before fetching another stale page');
  await click(root,'task.detail');
  assert.equal(root.root.findByType('strong').children.join(''),values.objective);
  assert.equal(button(root,'task.complete').props.disabled,false);
  assert.ok(calls.every(([action])=>action==='task.detail'),'Retry needs no read acknowledgment or unrelated request');
 }finally{await close(root)}
});

test('a new task revision invalidates loaded fields and stops an in-flight old page',async()=>{
 const values={3:{objective:'Previous private objective. '.repeat(200)},4:{objective:'New objective. '.repeat(180)},5:{objective:'Latest objective. '.repeat(180)}};
 const old=deferred(),started=deferred(),calls=[];let root,request;
 const dispatch=async(action,args)=>{
  calls.push([action,args]);
  if(args.expectedRevision===3&&args.offset){started.resolve();return old.promise}
  return accepted(detailPage(args,values[args.expectedRevision].objective));
 };
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':missingProjection(3,values[3])}}});
  await act(async()=>{request=button(root,'task.detail').props.onClick();await started.promise});
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':missingProjection(4,values[4])}}},session,dispatch)));
  old.resolve(accepted(detailPage(calls.at(-1)[1],values[3].objective)));await request;await flush();
  assert.match(text(root),/revision 4/);assert.doesNotMatch(text(root),/Previous private objective/);
  assert.match(text(root),/changed while loading details/);assert.equal(calls.length,2);
  await click(root,'task.detail');
  assert.equal(root.root.findByType('strong').children.join(''),values[4].objective);
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':missingProjection(5,values[5])}}},session,dispatch)));
  assert.match(text(root),/Task objective not loaded/);assert.doesNotMatch(text(root),/New objective/);
  assert.equal(button(root,'task.complete').props.disabled,true);
  await click(root,'task.detail');
  assert.equal(root.root.findByType('strong').children.join(''),values[5].objective);
 }finally{old.resolve({accepted:false});await request;await close(root)}
});

test('a delayed detail page cannot follow the reader into another chat',async()=>{
 const values={objective:'Old private objective. '.repeat(200)},old=deferred();let root,request;
 const dispatch=()=>old.promise;
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':missingProjection(3,values)}}});
  await act(async()=>{request=button(root,'task.detail').props.onClick()});
  await act(async()=>root.update(render({}, {id:'other'},dispatch)));
  old.resolve(accepted(detailPage({taskId:'task-one',expectedRevision:3,field:'objective',offset:0,limit:2000},values.objective)));await request;await flush();
  assert.doesNotMatch(text(root),/Old private objective|revision 3/);
  assert.equal(button(root,'task.get').props.disabled,false);
 }finally{old.resolve({accepted:false});await request;await close(root)}
});

test('available task:null clears a stale cached task; unavailable does not masquerade as empty',async()=>{
 let response=accepted({...projection(),task:null});const stale=projection(2).task;let root;
 try{
  root=await open(async()=>response,{runtimeControl:{owner:{'task.get':{task:stale}}}},{...session,task:stale});
  await click(root,'task.get');assert.doesNotMatch(text(root),/Task paused/);assert.match(text(root),/No saved task/);
  assert.equal(button(root,'task.create').props.disabled,true,'No objective supplied');
  response=accepted({status:'unavailable',task:null,coverage:'unknown',detailRequired:true,reason:'PRIVATE /control-state.json',authority:'Missing saved state is not empty.'});
  await click(root,'task.get');assert.match(text(root),/unavailable/i);assert.doesNotMatch(text(root),/No saved task|PRIVATE|control-state/);
  assert.equal(root.root.findAllByProps({'data-action':'task.create'}).length,0);
 }finally{await close(root)}
});

for(const failure of ['refusal','throw','missing'])test('cold read '+failure+' is an explicit error, not an accepted task',async()=>{
 let root;
 try{
  root=await open(async()=>{if(failure==='throw')throw Error('Read disconnected');if(failure==='refusal')return {accepted:false,error:'Read refused'};return undefined});
  await click(root,'task.get');assert.ok(root.root.findAllByProps({role:'alert'}).length);
  assert.doesNotMatch(text(root),/No saved task/);
  assert.equal(root.root.findAllByProps({'data-action':'task.create'}).length,0);
 }finally{await close(root)}
});

for(const resultKind of ['success','refusal','throw'])test('delayed '+resultKind+' from a previous chat cannot replace current data or busy state',async()=>{
 const old=deferred(),next=deferred();let root,first,second;
 const dispatch=async(_action,args)=>{if(args.sessionId==='owner'){await old.promise;if(resultKind==='throw')throw Error('Old failure');return resultKind==='refusal'?{accepted:false,error:'Old refusal'}:accepted(projection(50,{objective:'OLD PRIVATE TASK'}))}return next.promise};
 try{
  root=await open(dispatch);await act(async()=>{first=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({}, {id:'other'},dispatch)));
  await act(async()=>{second=button(root,'task.get').props.onClick()});
  old.resolve();await first;await flush();
  assert.doesNotMatch(text(root),/OLD PRIVATE TASK|Old failure|Old refusal/);
  assert.equal(button(root,'task.get').props.disabled,true,'Old finally must not release the new owner request');
  const value=projection(1,{id:'other-task',objective:'Current owner'});value.historyRef.args.sessionId='other';
  next.resolve(accepted(value));await second;await flush();assert.match(text(root),/Current owner/);
 }finally{old.resolve();next.resolve({accepted:false});await Promise.allSettled([first,second]);await close(root)}
});

test('same-session newer published revision wins over an awaited older cold result',async()=>{
 const reply=deferred();let root,request;const dispatch=()=>reply.promise;
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':projection(2)}}});
  await act(async()=>{request=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(5,{objective:'Newer revision'})}}},session,dispatch)));
  reply.resolve(accepted(projection(3,{objective:'Late older revision'})));await request;await flush();
  assert.match(text(root),/Newer revision/);assert.doesNotMatch(text(root),/Late older revision/);
  assert.match(text(root),/newer|changed/i);
 }finally{reply.resolve({accepted:false});await request;await close(root)}
});

test('same-revision applied boundary cannot regress during an awaited read',async()=>{
 const reply=deferred();let root,request;const dispatch=()=>reply.promise;
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':projection(3)}}});
  await act(async()=>{request=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(3,{appliedRevision:3})}}},session,dispatch)));
  reply.resolve(accepted(projection(3)));await request;await flush();
  assert.doesNotMatch(text(root),/applies at the next request/);
 }finally{reply.resolve({accepted:false});await request;await close(root)}
});

test('a different current task and a response naming another owner are refused',async()=>{
 const reply=deferred();let root,request;const dispatch=()=>reply.promise;
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':projection(8)}}});
  await act(async()=>{request=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(1,{id:'replacement-task',objective:'Replacement'})}}},session,dispatch)));
  reply.resolve(accepted(projection(9,{objective:'Obsolete task'})));await request;await flush();
  assert.match(text(root),/Replacement/);assert.doesNotMatch(text(root),/Obsolete task/);
  await close(root);root=null;
  const wrong=projection();wrong.historyRef.args.sessionId='wrong-owner';
  root=await open(async()=>accepted(wrong));await click(root,'task.get');
  assert.match(text(root),/another chat|owner/i);assert.doesNotMatch(text(root),/Verify the retained report/);
 }finally{reply.resolve({accepted:false});await request;await close(root)}
});

test('returning to a chat does not revive its previous visit or copy its draft',async()=>{
 const old=deferred();let root,request;const dispatch=()=>old.promise;
 try{
  root=await open(dispatch);
  await act(async()=>root.root.findByProps({id:'task-objective'}).props.onChange({target:{value:'PRIVATE draft'}}));
  await act(async()=>{request=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({}, {id:'other'},dispatch)));
  await act(async()=>root.update(render({}, session,dispatch)));
  old.resolve(accepted(projection(20)));await request;await flush();
  assert.doesNotMatch(text(root),/Verify the retained report/);
  assert.equal(root.root.findByProps({id:'task-objective'}).props.value,'');
 }finally{old.resolve({accepted:false});await request;await close(root)}
});

test('superseded mutation result cannot replace a later published revision',async()=>{
 const reply=deferred();let root,request;
 const dispatch=async action=>action==='task.get'?accepted(projection(3)):reply.promise;
 try{
  root=await open(dispatch);await click(root,'task.get');
  await act(async()=>{request=button(root,'task.resume').props.onClick()});
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(6,{status:'blocked',blockedReason:'Current blocker'})}}},session,dispatch)));
  reply.resolve(accepted({...projection(4,{status:'active'}),currentRevision:6,snapshotIsCurrent:false}));await request;await flush();
  assert.match(text(root),/revision 6/);assert.match(text(root),/Current blocker/);assert.doesNotMatch(text(root),/Task active/);
 }finally{reply.resolve({accepted:false});await request;await close(root)}
});

test('an unavailable mutation projection preserves the committed distinction and never retries',async()=>{
 const calls=[];let root;
 try{
  root=await open(async(action,args)=>{calls.push([action,args]);return accepted(action==='task.get'?projection(3):{
   status:'unavailable',task:null,coverage:'unknown',detailRequired:true,reason:'Private transport detail',
   receipt:{taskId:'task-one',revision:4,commandId:'original'},publication:{status:'unavailable'},currentRevision:null,snapshotIsCurrent:null,
  })});
  await click(root,'task.get');await click(root,'task.resume');
  assert.match(text(root),/change was recorded.*current state is unavailable/);
  assert.doesNotMatch(text(root),/No saved task|Private transport detail/);
  assert.deepEqual(calls.map(([name])=>name),['task.get','task.resume']);
  assert.equal(root.root.findAllByProps({'data-action':'task.create'}).length,0);
 }finally{await close(root)}
});

test('late unavailable read keeps a newer task projection instead of erasing it',async()=>{
 const reply=deferred();let root,request;const dispatch=()=>reply.promise;
 try{
  root=await open(dispatch,{runtimeControl:{owner:{'task.get':projection(2)}}});
  await act(async()=>{request=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(5,{objective:'Newly published task'})}}},session,dispatch)));
  reply.resolve(accepted({status:'unavailable',task:null,coverage:'unknown'}));await request;await flush();
  assert.match(text(root),/Newly published task/);assert.match(text(root),/changed while loading/);
 }finally{reply.resolve({accepted:false});await request;await close(root)}
});

test('committed unavailable state survives an older publication until its receipt revision is observed',async()=>{
 const calls=[];let root;
 const dispatch=async(action,args)=>{
  calls.push([action,args]);
  return accepted(action==='task.get'?projection(3):{
   status:'unavailable',task:null,coverage:'unknown',detailRequired:true,
   receipt:{taskId:'task-one',revision:4,commandId:'original'},currentRevision:null,snapshotIsCurrent:null,
  });
 };
 try{
  root=await open(dispatch);
  await click(root,'task.get');await click(root,'task.resume');
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(3,{appliedRevision:3})}}},session,dispatch)));
  assert.match(text(root),/change was recorded.*current state is unavailable/);
  assert.equal(root.root.findAllByProps({'data-action':'task.resume'}).length,0);
  assert.equal(root.root.findAllByProps({'data-action':'task.create'}).length,0);
  assert.deepEqual(calls.map(([name])=>name),['task.get','task.resume']);
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(undefined,{revision:undefined})}}},session,dispatch)));
  assert.match(text(root),/change was recorded.*current state is unavailable/);
  assert.equal(root.root.findAllByProps({'data-action':'task.resume'}).length,0);
  await act(async()=>root.update(render({runtimeControl:{owner:{'task.get':projection(4,{status:'active',appliedRevision:4})}}},session,dispatch)));
  assert.doesNotMatch(text(root),/change was recorded/);
  assert.match(text(root),/Task active · revision 4/);
  assert.equal(button(root,'task.pause').props.disabled,false);
 }finally{await close(root)}
});

test('host restart invalidates an in-flight observation even when the chat id is unchanged',async()=>{
 const reply=deferred();let root,request;const dispatch=()=>reply.promise;
 try{
  root=await open(dispatch,{client:{hostInstanceId:'old-host'}});
  await act(async()=>{request=button(root,'task.get').props.onClick()});
  await act(async()=>root.update(render({client:{hostInstanceId:'new-host'}},session,dispatch)));
  reply.resolve(accepted(projection(9)));await request;await flush();
  assert.doesNotMatch(text(root),/Verify the retained report/);
  assert.equal(button(root,'task.get').props.disabled,false);
 }finally{reply.resolve({accepted:false});await request;await close(root)}
});

test('a current owner read binds canonical alias provenance without a warm action',async()=>{
 const calls=[],objective='Exact canonical owner objective. '.repeat(80);let root;
 const value=missingProjection(5,{objective});value.historyRef.args.sessionId='native-owner';
 value.detailRefs[0].args.sessionId='native-owner';
 try{
  root=await open(async(action,args)=>{calls.push([action,args]);return accepted(action==='task.detail'?detailPage(args,objective):value)}, {},{id:'display-owner',runtimeSessionId:'native-owner'});
  await click(root,'task.get');assert.match(text(root),/revision 5/);
  assert.deepEqual(calls,[['task.get',{sessionId:'display-owner'}]]);
  await click(root,'task.detail');
  assert.equal(root.root.findByType('strong').children.join(''),objective);
  assert.ok(calls.slice(1).every(([action,args])=>action==='task.detail'&&args.sessionId==='native-owner'));
 }finally{await close(root)}
});
