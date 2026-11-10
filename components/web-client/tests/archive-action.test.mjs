import test from 'node:test';
import assert from 'node:assert/strict';
import {archiveDestination,createArchiveAction} from '../src/archive-action.js';

const chat={id:'a',title:'Example',workspace:'/example',workspaceId:'w'};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
function fixture(dispatch,navigate){
 let state={selectedSessionId:'a',selectedWorkspaceId:'w',workspaces:[{id:'w',path:'/example'}],view:{workSurface:'chat',draft:'kept'}},revision=0,feedback;
 const calls=[],navigations=[];
 const action=createArchiveAction({getState:()=>state,getRevision:()=>revision,onChange:value=>{feedback=value},
  dispatch:async(name,args)=>{calls.push([name,args]);return dispatch?dispatch(name,args):{accepted:true,result:{id:args.id,archived:name==='session.archive'}}},
  navigate:async(patch,isCurrent,isActive)=>{assert.ok(isCurrent());navigations.push(patch);return navigate?navigate(patch,isCurrent,isActive):{accepted:true}}});
 return {action,calls,navigations,get feedback(){return feedback},get state(){return state},set state(value){state=value},move:()=>revision++};
}
test('pending is immediate, guards repeats across archive/restore, then navigates after exact receipt',async()=>{
 const wait=deferred(),f=fixture(()=>wait.promise),run=f.action.run(chat);
 assert.equal(f.feedback.notice.text,'Archiving…');assert.deepEqual(f.feedback.pending,['a']);
 await f.action.run(chat);await f.action.run(chat,false);assert.equal(f.calls.length,1);assert.equal(f.navigations.length,0);
 wait.resolve({accepted:true,result:{id:'a',archived:true}});await run;
 assert.equal(f.feedback.notice.text,'Chat archived');assert.equal(f.feedback.notice.archive,true);
 assert.equal(f.navigations[0].workSurface,'workspace');assert.equal(f.state.view.draft,'kept');
 await f.action.run(chat,false);assert.equal(f.feedback.notice.phase,'unknown');assert.equal(f.navigations.length,1);
});
test('Undo restores only its id without navigating or selecting',async()=>{
 const f=fixture();await f.action.run(chat);await f.action.run(chat,false);
 assert.deepEqual(f.calls,[['session.archive',{id:'a'}],['session.restore',{id:'a'}]]);
 assert.equal(f.feedback.notice.text,'Chat restored');assert.equal(f.navigations.length,1);
});
for(const [name,response] of [
 ['rejection',{accepted:false,error:'Not allowed'}],['missing result',{accepted:true}],
 ['wrong id',{accepted:true,result:{id:'b',archived:true}}],['wrong archive state',{accepted:true,result:{id:'a',archived:false}}],
]){
 test(name+' stays put without replay',async()=>{
  const f=fixture(()=>response);await f.action.run(chat);
  assert.equal(f.navigations.length,0);assert.equal(f.calls.length,1);
  assert.equal(f.feedback.notice.phase,name==='rejection'?'error':'unknown');
 });
}
for(const status of [400,403,408,500,undefined]){
 test('throw '+status+' distinguishes rejection from unknown',async()=>{
  const f=fixture(()=>{throw Object.assign(Error('Rejected'),{status})});await f.action.run(chat);
  assert.equal(f.feedback.notice.phase,[400,403].includes(status)?'error':'unknown');
  assert.equal(f.navigations.length,0);assert.equal(f.calls.length,1);
 });
}
test('lost reply after archive commit is unknown and never replays',async()=>{
 let committed=false;const f=fixture(()=>{committed=true;throw Error('Lost response')});await f.action.run(chat);
 assert.ok(committed);assert.equal(f.feedback.notice.phase,'unknown');assert.equal(f.navigations.length,0);assert.equal(f.calls.length,1);
});
for(const mode of ['away','roundtrip','workspace','panel']){
 test('late archive cannot hijack '+mode,async()=>{
  const wait=deferred(),f=fixture(()=>wait.promise),run=f.action.run(chat);
  if(mode==='away')f.state={...f.state,selectedSessionId:'b'};
  if(mode==='roundtrip'){f.move();f.move()}
  if(mode==='workspace')f.state={...f.state,selectedWorkspaceId:'other'};
  if(mode==='panel')f.state={...f.state,view:{...f.state.view,panel:'settings'}};
  wait.resolve({accepted:true,result:{id:'a',archived:true}});await run;
  assert.equal(f.navigations.length,0);assert.equal(f.feedback.notice.text,'Chat archived');
 });
}
test('archiving another chat, or from a list, never navigates',async()=>{
 const f=fixture();await f.action.run({...chat,id:'b'});f.state={...f.state,view:{workSurface:'workspace'}};
 await f.action.run(chat);assert.equal(f.navigations.length,0);
});
test('managed or missing workspace goes to existing all-chats surface',()=>{
 const state={workspaces:[{id:'w',path:'/example'}]};
 assert.equal(archiveDestination(state,{...chat,location:{kind:'managed'}}).workSurface,'chats');
 assert.equal(archiveDestination({workspaces:[]},chat).workSurface,'chats');
});
test('another target cannot hide an in-flight archive outcome',async()=>{
 const a=deferred(),f=fixture(()=>a.promise);
 const first=f.action.run(chat);await f.action.run({...chat,id:'b',title:'Other'});
 assert.equal(f.calls.length,1);assert.equal(f.feedback.notice.id,'a');
 a.resolve({accepted:true,result:{id:'a',archived:true}});await first;
 assert.equal(f.feedback.notice.id,'a');assert.equal(f.navigations.length,1);
});
test('dismiss does not resurrect a pending result or redirect',async()=>{
 const wait=deferred(),f=fixture(()=>wait.promise),run=f.action.run(chat);f.action.dismiss();
 wait.resolve({accepted:true,result:{id:'a',archived:true}});await run;
 assert.equal(f.feedback.notice,null);assert.equal(f.navigations.length,0);
});
for(const next of ['undo','other','dismiss'])for(const outcome of ['rejected','thrown']){
 test(`held navigation releases mutation lock; ${next} supersedes late ${outcome} browse`,async()=>{
  const held=deferred(),entered=deferred();let ownsNavigation;
  const f=fixture(undefined,(patch,isCurrent,isActive)=>{ownsNavigation=isActive;entered.resolve();return held.promise});
  const first=f.action.run(chat);await entered.promise;
  assert.equal(f.feedback.notice.text,'Chat archived');assert.deepEqual(f.feedback.pending,[]);
  assert.equal(ownsNavigation(),true);
  if(next==='undo')await f.action.run(chat,false);
  if(next==='other')await f.action.run({...chat,id:'b'});
  if(next==='dismiss')f.action.dismiss();
  assert.equal(ownsNavigation(),false);
  const latest=f.feedback.notice;
  if(next==='undo')assert.deepEqual(f.calls.at(-1),['session.restore',{id:'a'}]);
  if(next==='other')assert.deepEqual(f.calls.at(-1),['session.archive',{id:'b'}]);
  if(next==='dismiss')assert.equal(latest,null);
  assert.deepEqual(f.feedback.pending,[]);
  f.move(); // A newer view cannot be overwritten by the old navigation.
  if(outcome==='thrown')held.reject(Error('Late navigation failure'));else held.resolve({accepted:false});
  await first;
  assert.deepEqual(f.feedback.notice,latest);assert.deepEqual(f.feedback.pending,[]);
  assert.equal(f.calls.length,next==='dismiss'?1:2); // No mutation replay.
 });
}