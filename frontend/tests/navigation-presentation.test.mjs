import test from 'node:test';
import assert from 'node:assert/strict';
import {activityFor,relativeActivity,compactParent,parentPath,pinnedWorkspaceSubtitle} from '../src/navigation-presentation.js';

test('navigation distinguishes unresolved input, active work, errors, and new replies',()=>{
 assert.equal(activityFor({status:'working',approvals:[{status:'pending'}]}).kind,'attention');
 assert.equal(activityFor({status:'idle',approvals:[{status:'approved'}]}).kind,'idle');
 assert.equal(activityFor({status:'working',error:'previous'}).kind,'working');
 assert.equal(activityFor({status:'error'}).kind,'attention');
 assert.equal(activityFor({id:'new'}, {attention:{sessions:{new:1}}}).kind,'unread');
 assert.equal(activityFor({activity:{kind:'attention',label:'Approval requested'}}).label,'Approval requested');
});
test('waiting is host evidence, pause and unknown interruption are different',()=>{
 const waiting={kind:'waiting',label:'Waiting for another chat'};
 assert.equal(activityFor({id:'owner',task:{status:'blocked'}},{navigationActivityById:{owner:waiting}}),waiting);
 assert.equal(activityFor({id:'owner',task:{status:'blocked'},workers:[{status:'working'}]}).label,'Work blocked');
 assert.equal(activityFor({status:'stopped'}).label,'Stopped');
 assert.equal(activityFor({task:{status:'paused'}}).kind,'paused');
 assert.equal(activityFor({status:'interrupted'}).label,'Outcome unknown');
 assert.equal(activityFor({status:'working',task:{status:'paused'}}).kind,'working');
});
test('reviewing an exact failure clears its marker without hiding current obligations',()=>{
 const chat={id:'failed',status:'error',error:'Saved failure'};
 const item={id:'session:failed',sessionId:'failed',read:true,fingerprint:'current'};
 const state={attention:{items:[item]}};
 assert.deepEqual(activityFor(chat,state),{kind:'idle',label:'Idle'});
 assert.equal(activityFor({...chat,status:'working'},state).kind,'working');
 assert.equal(activityFor({...chat,approvals:[{status:'pending'}]},state).label,'Approval requested');
 assert.equal(activityFor({...chat,questions:[{status:'pending',required:true}]},state).label,'Answer requested');
 assert.equal(activityFor({...chat,questions:[{status:'answered',delivery:{status:'rejected'}}]},state).label,'Check answer delivery');
 assert.equal(activityFor(chat,{...state,runtimeControl:{failed:{'task.get':{task:{status:'blocked'}}}}}).label,'Work blocked');
 assert.equal(activityFor(chat,{attention:{...state.attention,sessions:{failed:1}}}).kind,'unread');
 item.read=false;assert.equal(activityFor(chat,state).kind,'attention');
 item.id='completion:failed';item.read=true;assert.equal(activityFor(chat,state).kind,'attention');
});
test('recency handles unknown and future timestamps without inventing activity',()=>{
 for(const value of [null,undefined,NaN,Infinity,'1',0,-1])assert.equal(relativeActivity(value,3600).short,'—');
 assert.equal(relativeActivity(4000,3600).short,'now');
 assert.equal(relativeActivity(3540,3600).long,'1 minute ago');
 assert.equal(relativeActivity(1,7201).short,'2h');
 assert.equal(relativeActivity(1,172801).long,'2 days ago');
});
test('workspace parent labels preserve tails on POSIX and Windows paths',()=>{
 assert.equal(parentPath('/project'),'/');
 assert.equal(parentPath('/deep/parent/project/'),'/deep/parent');
 assert.equal(compactParent('/home/person/dev/experiments/september/project'),'…/experiments/september');
 assert.equal(parentPath('C:\\work\\project'),'C:\\work');
});

test('workspace rows retain distinguishing ancestors beyond a common deep parent',async()=>{
 const {workspaceContext}=await import('../src/navigation-presentation.js');
 assert.equal(workspaceContext({name:'app',path:'/one/common/deep/app',pathLabel:'one/common/deep/app'}),'one/common/deep');
 assert.equal(workspaceContext({name:'app',path:'/two/common/deep/app',pathLabel:'two/common/deep/app'}),'two/common/deep');
});

test('pinned workspaces identify their type, qualifying only collisions or explicit full paths',()=>{
 const row={name:'app',path:'/one/common/deep/app',pathLabel:'one/common/deep/app'};
 assert.equal(pinnedWorkspaceSubtitle(row),'Workspace','leaf collisions alone do not imply duplicate aliases');
 assert.equal(pinnedWorkspaceSubtitle({...row,nameQualifier:'one/common/deep'}),'Workspace · one/common/deep');
 assert.equal(pinnedWorkspaceSubtitle({...row,customName:'Shared',nameQualifier:'one/common/deep/app'}),'Workspace · one/common/deep/app');
 assert.equal(pinnedWorkspaceSubtitle(row,true),'Workspace · /one/common/deep/app');
 assert.equal(pinnedWorkspaceSubtitle({...row,path:'C:\\work\\app'},true),'Workspace · C:\\work\\app');
 assert.equal(pinnedWorkspaceSubtitle({...row,path:'/'},true),'Workspace · /');
});

test('active execution supersedes saved blocked task, while questions still require attention',()=>{
 const state={runtimeControl:{chat:{'task.get':{task:{status:'blocked'}}}}};
 assert.equal(activityFor({id:'chat',status:'working'},state).kind,'working');
 assert.equal(activityFor({id:'chat',status:'idle'},state).label,'Work blocked');
 assert.equal(activityFor({id:'chat',status:'working',questions:[{status:'pending',required:true}]},state).label,'Answer requested');
});

test('current admitted activity updates retained rows independently of page membership',()=>{
 const chats=Array.from({length:12},(_,index)=>({id:'chat-'+index,activity:{kind:'idle',label:'Idle'}}));
 const before=JSON.stringify(chats);
 const state={navigationResourcePending:{pinnedNavigation:{status:'pending'}},
  navigationActivityById:Object.fromEntries(chats.map(chat=>[chat.id,{kind:'working',label:'Working'}]))};
 for(const chat of chats)assert.deepEqual(activityFor(chat,state),{kind:'working',label:'Working'});
 state.navigationActivityById['chat-3']={kind:'attention',label:'Approval requested'};
 state.navigationActivityById['chat-7']={kind:'unread',label:'New response'};
 state.navigationActivityById['chat-9']={kind:'idle',label:'Idle'};
 assert.equal(activityFor(chats[3],state).label,'Approval requested');
 assert.equal(activityFor(chats[7],state).kind,'unread');
 assert.equal(activityFor(chats[9],state).kind,'idle');
 assert.equal(activityFor({id:'unobserved',activity:{kind:'attention',label:'Needs attention'}},state).kind,'attention');
 assert.equal(activityFor(chats[0],{navigationActivityById:{}}).kind,'idle','a replaced context cannot inherit another owner’s activity');
 assert.equal(activityFor({id:'constructor',activity:{kind:'idle',label:'Idle'}},state).kind,'idle');
 assert.equal(JSON.stringify(chats),before,'current activity never mutates cached page rows');
});
