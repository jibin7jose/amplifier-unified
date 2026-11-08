import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AssignmentTaskList}=await server.ssrLoadModule('/src/assignment-task-list.jsx');
const {AssignmentControls}=await server.ssrLoadModule('/src/assignment-controls.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('all SSR setup finishes before test and teardown registration',()=>{
 const source=readFileSync(new URL(import.meta.url),'utf8');
 const loads=[...source.matchAll(/await server\.ssrLoadModule\(/g)].map(match=>match.index);
 assert.equal(loads.length,2);
 assert.ok(loads.every(index=>index<source.indexOf('test.after(')));
});
const board=()=>({assignmentId:'assignment',assignmentRevision:3,assignmentStatus:'active',ownerSessionId:'owner',canManage:true,version:'v1',members:[{sessionId:'owner',label:'Coordinator'},{sessionId:'peer',label:'Draft helper'}],tasks:[{id:'first',title:'Inspect source',description:'Check input',blockedReason:'',revision:1,status:'open',dependsOn:[],blockedBy:[],readiness:'ready',claim:null},{id:'second',title:'Review report',description:'',blockedReason:'',revision:1,status:'open',dependsOn:['first'],blockedBy:['first'],readiness:'blocked',claim:null}]});
const text=root=>JSON.stringify(root.toJSON());
const button=(root,label)=>root.root.findAllByType('button').find(row=>row.children.join('')===label);
const expand=async root=>{const details=root.root.findAllByType('details')[0],element={open:true};await act(async()=>details.props.onToggle({target:element,currentTarget:element}))};
function storage(){const map=new Map();return {getItem:key=>map.get(key),setItem:(key,value)=>map.set(key,value),map}}
function summaryBoard(value,args={}){
 const kind=args.view||'active',offset=args.after?Number(args.after.split(':')[1]):0,limit=args.limit||8;
 const all=value.tasks.filter(task=>['completed','cancelled'].includes(task.status)===(kind==='history'));
 return {...value,view:kind,nextAfter:offset+limit<all.length?kind+':'+(offset+limit):null,coverage:{kind:'current-page',changed:false},tasks:all.slice(offset,offset+limit).map(({id,title,revision,status,readiness,claim,worker,blockedReason='',dependsOn=[],blockedBy=[]})=>({id,title,revision,status,readiness,claim,worker,blockedReason,blockedReasonTruncated:false,blockedCount:blockedBy.length,dependencyCount:dependsOn.length,detailRef:{action:'assignment.tasks.read',args:{sessionId:'owner',taskId:id}}}))};
}
function fixture(value=board(),mutation=()=>({accepted:true,result:{task:board().tasks[0]}})){
 const calls=[];let resolveWait,waitArgs,current=value;
 const dispatch=(action,args,meta={})=>{
  calls.push({action,args,meta});
  if(action.endsWith('.list'))return Promise.resolve({result:summaryBoard(current,args)});
  if(action.endsWith('.read')){
   const task=current.tasks.find(row=>row.id===args.taskId);assert.ok(task,'Exact read must name an existing fixture task');
   const {tasks,...envelope}=current;
   return Promise.resolve({result:{...envelope,task:structuredClone(task),dependencies:(task.dependsOn||[]).map(id=>{const row=tasks.find(task=>task.id===id);return {id,available:!!row,...(row?{title:row.title,revision:row.revision,status:row.status}:{})}})}});
  }
  if(action.endsWith('.wait'))return new Promise((resolve,reject)=>{resolveWait=resolve;waitArgs=args;meta.signal?.addEventListener('abort',()=>reject(Error('aborted')),{once:true})});
  return Promise.resolve().then(()=>mutation(action,args,meta));
 };
 return {dispatch,calls,wake:next=>{current=next;resolveWait({result:summaryBoard(next,waitArgs),changed:true})}};
}

for(const independent of [false,true])test(`passive disclosure and readable ${independent?'independent':'assignment'} scope`,async()=>{
 globalThis.sessionStorage=storage();const value=independent?{assignmentId:null,tasks:[],members:[],canManage:false,version:'unassigned'}:board();const f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});assert.equal(f.calls.length,0);await expand(root);assert.equal(f.calls[0].action,'assignment.tasks.list');assert.equal(f.calls[1].action,'assignment.tasks.wait');assert.equal(f.calls[1].meta.presentation,true);
 if(independent){assert.match(text(root),/independent chat has no shared task list/);assert.equal(button(root,'Add task'),undefined)}else{assert.ok(root.root.findAllByType('p').some(row=>row.children.join('').includes('Waiting for 1 prerequisite')));assert.match(text(root),/Inspect source/);assert.equal(root.root.findAllByType('button').filter(row=>row.children.join('')==='Claim for this chat').length,1)}
 }finally{await act(async()=>root.unmount())}
});

test('peer changes arrive passively and completion stays a report',async()=>{
 globalThis.sessionStorage=storage();const f=fixture();let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);const next=board();next.version='v2';next.tasks[0]={...next.tasks[0],status:'completed',completion:{report:'Checked source',reportedBy:'peer',verification:'participant_report'}};await act(async()=>f.wake(next));assert.doesNotMatch(text(root),/Reported complete/);await act(async()=>button(root,'Task history').props.onClick());assert.match(text(root),/Reported complete/);await act(async()=>button(root,'View task details').props.onClick());assert.match(text(root),/Draft helper/);assert.match(text(root),/Artifact verification and integration remain separate/);assert.ok(f.calls.every(row=>['assignment.tasks.list','assignment.tasks.wait','assignment.tasks.read'].includes(row.action)));
 }finally{await act(async()=>root.unmount())}
});

test('uncertain claim uses one saved request identity on retry and reload',async()=>{
 globalThis.sessionStorage=storage();let writes=0;const f=fixture(board(),()=>{if(++writes===1)throw Error('Connection lost');return {accepted:true,result:{task:board().tasks[0]}}});let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Claim for this chat').props.onClick());assert.match(text(root),/no confirmed response/);const first=f.calls.find(row=>row.action==='assignment.tasks.claim');await act(async()=>root.unmount());await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Check the same task change').props.onClick());const claims=f.calls.filter(row=>row.action==='assignment.tasks.claim');assert.equal(claims.length,2);assert.equal(claims[1].meta.id,first.meta.id);assert.deepEqual(claims[1].args,first.args);assert.doesNotMatch(text(root),/no confirmed response/);
 }finally{await act(async()=>root.unmount())}
});

test('session switch ignores delayed previous board and keeps drafts private',async()=>{
 globalThis.sessionStorage=storage();let release;const calls=[];const dispatch=(action,args,meta)=>{calls.push({action,args,meta});if(args.sessionId==='first'&&action.endsWith('.list'))return new Promise(resolve=>{release=()=>resolve({result:{...board(),tasks:[{...board().tasks[0],title:'OLD PRIVATE TASK'}]}})});if(action.endsWith('.list'))return Promise.resolve({result:{...board(),tasks:[]}});return new Promise((resolve,reject)=>meta.signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}))};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch,sessionId:'first'}))});await expand(root);await act(async()=>root.update(React.createElement(AssignmentTaskList,{dispatch,sessionId:'second'})));await act(async()=>release());assert.doesNotMatch(text(root),/OLD PRIVATE TASK/);await expand(root);assert.equal(calls.filter(row=>row.args.sessionId==='second'&&row.action.endsWith('.list')).length,1);
 }finally{await act(async()=>root.unmount())}
});

test('stale responsibility cannot complete and reassignment explains running effects',async()=>{
 globalThis.sessionStorage=storage();const value=board();value.tasks[0]={...value.tasks[0],status:'claimed',claim:{id:'claim',sessionId:'owner',status:'stale',detail:'The holder was stopped.'}};const f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);assert.match(text(root),/Claim needs review/);assert.match(text(root),/Prior file or tool work may continue/);assert.equal(button(root,'Mark reported complete'),undefined);await act(async()=>button(root,'Release claim').props.onClick());const release=f.calls.find(row=>row.action==='assignment.tasks.release');assert.equal(release.args.claimId,'claim');assert.equal(release.args.expectedRevision,1);
 }finally{await act(async()=>root.unmount())}
});


test('an unconfirmed response keeps the exact pending request',async()=>{
 globalThis.sessionStorage=storage();const f=fixture(board(),()=>({accepted:false}));let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Claim for this chat').props.onClick());assert.match(text(root),/no confirmed receipt/);assert.ok(button(root,'Check the same task change'));const saved=JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner'));assert.equal(saved.pending.id,f.calls.find(row=>row.action==='assignment.tasks.claim').meta.id);
 }finally{await act(async()=>root.unmount())}
});

test('task revision updates refresh assignment controls without usage reads',async()=>{
 globalThis.sessionStorage=storage();const value=board();value.assignmentControl={id:'assignment',revision:3,status:'active',ownerSessionId:'owner',limits:{maxOpenTasks:8},members:{owner:{status:'bound'}}};const f=fixture(value),observed=[];let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner',onAssignmentChange:row=>observed.push(row)}))});await expand(root);const next=structuredClone(value);next.version='v2';next.assignmentControl.revision=4;await act(async()=>f.wake(next));assert.deepEqual(observed.map(row=>row.revision),[3,4]);assert.ok(f.calls.every(row=>row.action.startsWith('assignment.tasks.')));
 }finally{await act(async()=>root.unmount())}
});

const control=revision=>({id:'assignment',revision,status:'active',ownerSessionId:'owner',limits:{maxOpenTasks:8},members:{owner:{status:'bound'}}});
const scopedBoard=(revision,status='active')=>summaryBoard({...board(),assignmentRevision:revision,assignmentStatus:status,version:'v'+revision,assignmentControl:{...control(revision),status}});
const defer=()=>{let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve}};
const disclosure=(root,label)=>root.root.findAllByType('details').find(row=>row.findAllByType('summary')[0]?.children.join('')===label);
const toggle=async(root,label,open)=>{const element={open};await act(async()=>disclosure(root,label).props.onToggle({target:element,currentTarget:element}))};

test('late task wait cannot undo a newer confirmed parent Stop',async()=>{
 globalThis.sessionStorage=storage();let revision=3,reads=0;const waits=[];
 const dispatch=action=>{if(action==='assignment.read'){reads++;return Promise.resolve({result:{assignment:{...control(revision),status:revision===5?'stopped':'active'}}})}if(action==='assignment.stop'){revision=5;return Promise.resolve({accepted:true})}if(action==='assignment.tasks.list')return Promise.resolve({result:scopedBoard(revision,revision===5?'stopped':'active')});if(action==='assignment.tasks.wait'){const wait=defer();waits.push(wait);return wait.promise}throw Error(action)};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentControls,{dispatch,sessionId:'owner'}))});await toggle(root,'Shared tasks and assignment',true);await toggle(root,'Shared task list',true);const old=waits[0];await act(async()=>button(root,'Stop assignment').props.onClick());assert.equal(reads,2);assert.equal(button(root,'Stop assignment'),undefined);await act(async()=>old.resolve({result:scopedBoard(4)}));assert.match(text(root),/Assignment stopped/);assert.equal(button(root,'Stop assignment'),undefined);assert.equal(button(root,'Claim for this chat'),undefined)}finally{await act(async()=>root.unmount())}
});

test('late parent read cannot replace newer task-projected assignment revision',async()=>{
 globalThis.sessionStorage=storage();const old=defer(),waits=[];let reads=0;
 const dispatch=action=>{if(action==='assignment.read')return ++reads===1?Promise.resolve({result:{assignment:control(3)}}):old.promise;if(action==='assignment.tasks.list')return Promise.resolve({result:scopedBoard(3)});if(action==='assignment.tasks.wait'){const wait=defer();waits.push(wait);return wait.promise}throw Error(action)};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentControls,{dispatch,sessionId:'owner'}))});await toggle(root,'Shared tasks and assignment',true);await toggle(root,'Shared task list',true);act(()=>{button(root,'Refresh assignment').props.onClick()});await act(async()=>waits[0].resolve({result:scopedBoard(5,'stopped')}));await act(async()=>old.resolve({result:{assignment:control(4)}}));assert.match(text(root),/Assignment stopped/);assert.equal(button(root,'Stop assignment'),undefined)}finally{await act(async()=>root.unmount())}
});

for(const boundary of ['mutation','close','visibility'])test(`task observations invalidated immediately at ${boundary}`,async()=>{
 globalThis.sessionStorage=storage();const waits=[],write=defer();let visibility;
 if(boundary==='visibility')globalThis.document={hidden:false,addEventListener:(_,fn)=>{visibility=fn},removeEventListener:()=>{}};
 const dispatch=action=>{if(action.endsWith('.list'))return Promise.resolve({result:summaryBoard(board())});if(action.endsWith('.wait')){const wait=defer();waits.push(wait);return wait.promise}return write.promise};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch,sessionId:'owner'}))});await expand(root);const late={...board(),version:'old-late',tasks:[{...board().tasks[0],title:'STALE OBSERVATION'}]};await act(async()=>{if(boundary==='mutation')button(root,'Claim for this chat').props.onClick();else if(boundary==='close'){const el={open:false};disclosure(root,'Shared task list').props.onToggle({target:el,currentTarget:el})}else{document.hidden=true;visibility()}waits[0].resolve({result:late})});assert.doesNotMatch(text(root),/STALE OBSERVATION/);if(boundary==='mutation')await act(async()=>write.resolve({accepted:true,result:{task:board().tasks[0]}}))}finally{await act(async()=>root.unmount());if(boundary==='visibility')delete globalThis.document}
});

test('replacement claim preserves private report and requires deliberate rebinding',async()=>{
 globalThis.sessionStorage=storage();const claimed=(id,revision)=>{const value=board();value.assignmentRevision=revision;value.version='v'+revision;value.tasks[0]={...value.tasks[0],revision,status:'claimed',claim:{id,sessionId:'owner',status:'current'}};return value};const f=fixture(claimed('original',3));let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);const report=()=>root.root.findAllByType('textarea').find(row=>row.props.maxLength===4000&&row.props.required);await act(async()=>report().props.onChange({target:{value:'PRIVATE COMPLETION DRAFT'}}));assert.equal(report().props['data-private'],'true');const released=board();released.assignmentRevision=4;released.version='v4';await act(async()=>f.wake(released));await act(async()=>f.wake(claimed('replacement',5)));assert.equal(report().props.value,'PRIVATE COMPLETION DRAFT');assert.match(text(root),/Your report is retained for the earlier claim/);assert.equal(button(root,'Mark reported complete').props.disabled,true);await act(async()=>button(root,'Use reviewed report for current claim').props.onClick());await act(async()=>button(root,'Mark reported complete').parent.props.onSubmit({preventDefault(){}}));const receipt=f.calls.find(row=>row.action==='assignment.tasks.complete');assert.equal(receipt.args.claimId,'replacement');assert.equal(receipt.args.expectedRevision,5);assert.equal(receipt.args.report,'PRIVATE COMPLETION DRAFT')}finally{await act(async()=>root.unmount())}
});

test('confirmed claim conflict clears only request, preserves private task draft and refreshes',async()=>{
 globalThis.sessionStorage=storage();const f=fixture(board(),()=>{throw Object.assign(Error('Another member claimed this task. Read current tasks.'),{status:409})});let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);const title=root.root.findAllByType('input').find(row=>row.props.maxLength===200);await act(async()=>title.props.onChange({target:{value:'PRIVATE TASK DRAFT'}}));for(const field of root.root.findAll(row=>['input','textarea'].includes(row.type)&&row.props.type!=='checkbox'))assert.equal(field.props['data-private'],'true');await act(async()=>button(root,'Claim for this chat').props.onClick());assert.match(text(root),/Another member claimed this task/);assert.equal(button(root,'Check the same task change'),undefined);assert.equal(JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner')).draft.title,'PRIVATE TASK DRAFT');assert.equal(f.calls.filter(row=>row.action==='assignment.tasks.list').length,2)}finally{await act(async()=>root.unmount())}
});

for(const status of [408,503])test(`uncertain ${status} keeps exact claim retry`,async()=>{
 globalThis.sessionStorage=storage();const f=fixture(board(),()=>{throw Object.assign(Error('No confirmed result'),{status})});let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Claim for this chat').props.onClick());assert.ok(button(root,'Check the same task change'));assert.equal(JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner')).pending.id,f.calls.find(row=>row.action==='assignment.tasks.claim').meta.id)}finally{await act(async()=>root.unmount())}
});

test('integration member fallback stays distinct without loading more histories',async()=>{
 globalThis.sessionStorage=storage();const calls=[];const assignment={...control(3),members:{owner:{status:'bound'},'unlisted-a':{status:'bound'},'unlisted-b':{status:'bound'}}};const dispatch=async action=>{calls.push(action);return {result:{assignment}}};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentControls,{dispatch,sessionId:'owner'}))});await toggle(root,'Shared tasks and assignment',true);const names=root.root.findAllByType('option').map(row=>row.children.join(''));assert.ok(names.includes('Member chat · unlisted-a'));assert.ok(names.includes('Member chat · unlisted-b'));assert.deepEqual(calls,['assignment.read'])}finally{await act(async()=>root.unmount())}
});

test('observed worker blockage updates without changing responsibility or readiness',async()=>{
 globalThis.sessionStorage=storage();const value=board();value.tasks[0]={...value.tasks[0],status:'claimed',claim:{id:'claim',sessionId:'peer',status:'current'},worker:{label:'Working',blockedReason:''}};const f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);assert.match(text(root),/Working/);const next=structuredClone(value);next.version='worker-blocked';next.tasks[0].worker={label:'Work blocked',blockedReason:'Waiting for a decision'};await act(async()=>f.wake(next));assert.match(text(root),/Work blocked/);assert.match(text(root),/Waiting for a decision/);assert.match(text(root),/Claimed/);assert.match(text(root),/Draft helper/);assert.equal(button(root,'Claim for this chat'),undefined);assert.ok(f.calls.every(row=>['assignment.tasks.list','assignment.tasks.wait'].includes(row.action)))}finally{await act(async()=>root.unmount())}
});

test('explicit assignment resume preserves revision and uncertain command identity without sending',async()=>{
 globalThis.sessionStorage=storage();const calls=[];let status='stopped',revision=9,first=true;
 const dispatch=async(action,args,meta)=>{calls.push({action,args,meta});if(action==='assignment.read')return {result:{assignment:{...control(revision),status}}};if(action==='assignment.resume'){if(first){first=false;throw Error('Response lost')}status='active';revision=10;return {accepted:true}}throw Error(action)};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentControls,{dispatch,sessionId:'owner'}))});await toggle(root,'Shared tasks and assignment',true);assert.match(text(root),/nothing is sent automatically/);await act(async()=>button(root,'Resume assignment').props.onClick());assert.ok(button(root,'Check the same change'));await act(async()=>button(root,'Check the same change').props.onClick());const resumes=calls.filter(row=>row.action==='assignment.resume');assert.equal(resumes.length,2);assert.deepEqual(resumes[0],resumes[1]);assert.deepEqual(resumes[0].args,{sessionId:'owner',expectedRevision:9});assert.ok(button(root,'Stop assignment'));assert.equal(button(root,'Resume assignment'),undefined);assert.ok(calls.every(row=>['assignment.read','assignment.resume'].includes(row.action)))}finally{await act(async()=>root.unmount())}
});

test('commissioned member cannot resume its assignment from its own controls',async()=>{
 globalThis.sessionStorage=storage();const dispatch=async()=>({result:{assignment:{...control(9),status:'stopped'}}});let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentControls,{dispatch,sessionId:'helper'}))});await toggle(root,'Shared tasks and assignment',true);assert.equal(button(root,'Resume assignment'),undefined);assert.match(text(root),/commissioning chat/)}finally{await act(async()=>root.unmount())}
});


test('retiring a finished helper uses the current revision and retains history after retry',async()=>{
 globalThis.sessionStorage=storage();const calls=[];let assignment={...control(4),members:{owner:{status:'bound'},peer:{status:'bound'},past:{status:'retired'}}},first=true;
 const dispatch=async(action,args,meta)=>{calls.push({action,args,meta});if(action==='assignment.read')return {result:{assignment}};if(action==='assignment.retire'){if(first){first=false;throw Error('Response lost')}assignment={...assignment,revision:5,members:{...assignment.members,peer:{status:'retired'}}};return {accepted:true}}throw Error(action)};let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentControls,{dispatch,sessionId:'owner'}))});await toggle(root,'Shared tasks and assignment',true);assert.ok(root.root.findAllByProps({role:'status'}).some(row=>row.children.join('').includes('2 current members · 3 retained members')));const select=root.root.findByProps({id:'retire-member-owner'});assert.deepEqual(select.findAllByType('option').map(row=>row.props.value),['','peer']);await act(async()=>select.props.onChange({target:{value:'peer'}}));await act(async()=>button(root,'Retire finished member').props.onClick());assert.ok(button(root,'Check the same change'));await act(async()=>button(root,'Check the same change').props.onClick());const writes=calls.filter(row=>row.action==='assignment.retire');assert.equal(writes.length,2);assert.deepEqual(writes[0],writes[1]);assert.deepEqual(writes[0].args,{sessionId:'owner',targetSessionId:'peer',expectedRevision:4});assert.equal(root.root.findByProps({id:'retire-member-owner'}).findAllByType('option').length,1);assert.ok(calls.every(row=>['assignment.read','assignment.retire'].includes(row.action)));assert.ok(root.root.findAllByProps({role:'status'}).some(row=>row.children.join('').includes('1 current members · 3 retained members')))}finally{await act(async()=>root.unmount())}
});

const manyTasks=()=>{
 const value=board();value.tasks=Array.from({length:18},(_,index)=>({...board().tasks[0],id:'task-'+index,title:'Task '+index,description:'Exact details '+index,dependsOn:[],blockedBy:[],status:index>=16?'completed':'open',completion:index>=16?{report:'Historical report '+index,reportedBy:'peer',references:['ref-'+index]}:undefined}));return value;
};
test('active and history navigation replaces pages and scopes each wait',async()=>{
 globalThis.sessionStorage=storage();const f=fixture(manyTasks());let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);
 assert.equal(root.root.findAll(row=>row.props['data-task-id']).length,8);assert.doesNotMatch(text(root),/Exact details/);
 await act(async()=>button(root,'Next tasks').props.onClick());assert.doesNotMatch(text(root),/"Task 0"/);assert.match(text(root),/"Task 8"/);assert.equal(root.root.findAll(row=>row.props['data-task-id']).length,8);
 const wait=f.calls.filter(row=>row.action.endsWith('.wait')).at(-1);assert.equal(wait.args.after,'active:8');assert.equal(wait.args.view,'active');assert.equal(wait.args.limit,8);
 await act(async()=>button(root,'Task history').props.onClick());assert.match(text(root),/Reported complete/);assert.doesNotMatch(text(root),/"Task 8"/);assert.equal(button(root,'Previous tasks').props.disabled,true);
 await act(async()=>button(root,'View task details').props.onClick());assert.match(text(root),/Historical report 16/);assert.equal(f.calls.filter(row=>row.action.endsWith('.read')).length,1);
 }finally{await act(async()=>root.unmount())}
});
test('exact edit preserves off-page prerequisites and private content through paging',async()=>{
 globalThis.sessionStorage=storage();const value=manyTasks();value.tasks[0].dependsOn=['task-17'];const f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Edit task').props.onClick());
 assert.equal(f.calls.find(row=>row.action.endsWith('.read')).args.taskId,'task-0');assert.match(text(root),/Exact details 0/);assert.ok(button(root,'Remove prerequisite Task 17'));
 const input=()=>root.root.findAllByType('input').find(row=>row.props.maxLength===200);await act(async()=>input().props.onChange({target:{value:'PRIVATE PAGED EDIT'}}));
 await act(async()=>button(root,'Next tasks').props.onClick());assert.equal(input().props.value,'PRIVATE PAGED EDIT');assert.equal(button(root,'Save task').props.disabled,false);
 const saved=JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner'));assert.deepEqual(saved.draft.dependsOn,['task-17']);assert.equal(saved.draft.expectedRevision,1);
 await act(async()=>button(root,'Prerequisite history').props.onClick());const selected=root.root.findAllByType('input').find(row=>row.props.type==='checkbox'&&row.props.checked);assert.ok(selected);
 await act(async()=>button(root,'Active prerequisites').props.onClick());assert.ok(button(root,'Remove prerequisite Task 17'));await act(async()=>button(root,'Remove prerequisite Task 17').props.onClick());assert.deepEqual(JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner')).draft.dependsOn,[]);
 }finally{await act(async()=>root.unmount())}
});
test('off-page edit observes its exact revision without rebinding a saved draft',async()=>{
 globalThis.sessionStorage=storage();const value=manyTasks(),f=fixture(value);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch:f.dispatch,sessionId:'owner'}))});await expand(root);await act(async()=>button(root,'Edit task').props.onClick());await act(async()=>button(root,'Next tasks').props.onClick());
 const next=structuredClone(value);next.assignmentRevision=4;next.version='v4';next.tasks[0].revision=2;await act(async()=>f.wake(next));assert.match(text(root),/Your edits are retained; inspect it again/);assert.equal(button(root,'Save task').props.disabled,true);assert.equal(JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner')).draft.expectedRevision,1);
 }finally{await act(async()=>root.unmount())}
});
test('a delayed exact detail cannot replace a later selection',async()=>{
 globalThis.sessionStorage=storage();const f=fixture(),late=defer();const dispatch=(action,args,meta)=>action==='assignment.tasks.read'&&args.taskId==='first'?late.promise:f.dispatch(action,args,meta);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch,sessionId:'owner'}))});await expand(root);const details=()=>root.root.findAllByType('button').filter(row=>row.children.join('')==='View task details');await act(async()=>details()[0].props.onClick());await act(async()=>details()[1].props.onClick());await act(async()=>late.resolve({result:{...board(),task:{...board().tasks[0],description:'STALE PRIVATE DETAIL'},dependencies:[]}}));assert.doesNotMatch(text(root),/STALE PRIVATE DETAIL/);
 }finally{await act(async()=>root.unmount())}
});

for(const failure of [false,true])test(`explicit edit loading protects the private draft; ${failure?'failed read retains it':'success binds exact detail'}`,async()=>{
 globalThis.sessionStorage=storage();const f=fixture(),pending=defer();const dispatch=(action,args,meta)=>action==='assignment.tasks.read'?pending.promise:f.dispatch(action,args,meta);let root;
 try{await act(async()=>{root=create(React.createElement(AssignmentTaskList,{dispatch,sessionId:'owner'}))});await expand(root);
 const title=()=>root.root.findAllByType('input').find(row=>row.props.maxLength===200);await act(async()=>title().props.onChange({target:{value:'PRIVATE UNSAVED DRAFT'}}));
 await act(async()=>button(root,'Edit task').props.onClick());assert.equal(title().props.disabled,true);assert.equal(title().props.value,'PRIVATE UNSAVED DRAFT');
 await act(async()=>pending.resolve(failure?Promise.reject(Error('Detail unavailable')):{result:{...board(),task:board().tasks[0],dependencies:[]}}));
 assert.equal(title().props.disabled,false);assert.equal(title().props.value,failure?'PRIVATE UNSAVED DRAFT':'Inspect source');
 if(failure)assert.match(text(root),/Detail unavailable/);else assert.equal(JSON.parse(sessionStorage.getItem('amplifier-assignment-tasks:owner')).draft.expectedRevision,1);
 }finally{await act(async()=>root.unmount())}
});
