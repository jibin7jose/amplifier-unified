import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {TaskEnvironments}=await server.ssrLoadModule('/src/workspace-test-environments.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalStorage=globalThis.sessionStorage,storage=new Map();
globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
test.beforeEach(()=>storage.clear());
test.after(()=>{globalThis.sessionStorage=originalStorage;return server.close()});
const key='amplifier-test-environment:w:a';
const accepted=result=>({accepted:true,result});
const page={items:[],capability:{status:'ready',profiles:[{id:'linux-python',label:'Python'}]}};
const text=node=>typeof node==='string'?node:(node?.children||[]).map(text).join('');
const button=(root,label)=>root.root.findAllByType('button').find(node=>text(node)===label);
const read={action:'workspace.environments.read',args:{workspaceId:'w',sessionId:'a',offset:0}};

for(const savedAction of ['workspace.environments.start','workspace.tests.submit'])for(const lost of [false,true])test(`cancel targets the queued check with a pending ${savedAction} and ${lost?'a lost response':'an acknowledgment'}`,async()=>{
 let root,settle;const calls=[];
 const pending={action:savedAction,args:{workspaceId:'w',sessionId:'a',profileId:'linux-python',command:'python -m unittest',requestId:'older-request'},id:'older-command'};
 const saved=JSON.stringify({profileId:'linux-python',command:'python -m unittest',pending});storage.set(key,saved);
 let check={id:'queued-check',status:'queued'};
 const cancel={action:'workspace.tests.cancel',args:{workspaceId:'w',sessionId:'a',requestId:check.id}};
 const dispatch=(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action===read.action)return Promise.resolve(accepted({...page,checks:[check]}));
  if(action===cancel.action)return new Promise((resolve,reject)=>{settle=()=>{check={...check,status:'cancelled',cancelRequested:true};lost?reject(Error('Lost cancellation response')):resolve(accepted(check))}});
  return Promise.resolve(accepted({request:{state:'completed'}}));
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  const cancelButton=button(root,'Cancel check');assert.equal(cancelButton.props.disabled,false);
  await act(async()=>{cancelButton.props.onClick();cancelButton.props.onClick()});
  assert.deepEqual(calls,[read,cancel],'Cancel uses its target once and does not replay the saved request');
  assert.equal(storage.get(key),saved,'Cancel does not overwrite the recovery receipt');
  assert.equal(button(root,'Check the same environment request').props.disabled,true);
  await act(async()=>settle());
  assert.deepEqual(calls,[read,cancel,read],'even a lost acknowledgment refreshes canonical check state');
  assert.equal(storage.get(key),saved,'settling cancellation preserves the unresolved request');
  assert.match(text(root.toJSON()),/Check: cancelled/);assert.equal(button(root,'Cancel check'),undefined);
  assert.equal(Boolean(button(root,'Check the same environment request').props.disabled),false);
  assert.equal(button(root,'Start test environment').props.disabled,true);
  if(lost)assert.match(text(root.toJSON()),/Lost cancellation response/);
  await act(async()=>button(root,'Check the same environment request').props.onClick());
  assert.deepEqual(calls[3],{action:pending.action,args:pending.args,meta:{id:pending.id}},'only explicit recovery sends the original request identity');
  assert.deepEqual(calls[4],read);assert.equal(JSON.parse(storage.get(key)).pending,null);
 }finally{await act(async()=>root?.unmount())}
});

test('cancellation refresh reports an admitted check still stopping without declaring it finished',async()=>{
 let root;const calls=[];let check={id:'running-check',status:'checking'};
 const dispatch=async(action,args)=>{calls.push({action,args});if(action==='workspace.tests.cancel')check={...check,cancelRequested:true};return accepted(action===read.action?{...page,checks:[check]}:check)};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>button(root,'Cancel check').props.onClick());
  assert.deepEqual(calls,[read,{action:'workspace.tests.cancel',args:{workspaceId:'w',sessionId:'a',requestId:'running-check'}},read]);
  assert.match(text(root.toJSON()),/Check: checking/);assert.match(text(root.toJSON()),/Cancellation requested/);
  assert.equal(button(root,'Cancel check'),undefined);assert.equal(storage.has(key),false,'cancellation needs no new browser recovery receipt');
 }finally{await act(async()=>root?.unmount())}
});

for(const lost of [false,true])test(`a late cancellation ${lost?'failure':'acknowledgment'} after unmount leaves a newer recovery receipt and view untouched`,async()=>{
 let root,settle;const calls=[];
 const saved=JSON.stringify({profileId:'linux-python',command:'',pending:{action:'workspace.environments.start',args:{workspaceId:'w',sessionId:'a',requestId:'old-request'},id:'old-command'}});storage.set(key,saved);
 const check={id:'queued-check',status:'queued'};
 const dispatch=(action,args)=>{calls.push({action,args});return action===read.action?Promise.resolve(accepted({...page,checks:[check]})):new Promise((resolve,reject)=>{settle=()=>lost?reject(Error('Late cancellation response')):resolve(accepted({...check,status:'cancelled',cancelRequested:true}))})};
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>{button(root,'Cancel check').props.onClick()});
  assert.deepEqual(calls[1],{action:'workspace.tests.cancel',args:{workspaceId:'w',sessionId:'a',requestId:check.id}});
  await act(async()=>root.unmount());
  const newer=JSON.stringify({profileId:'linux-python',command:'',pending:{action:'workspace.tests.submit',args:{workspaceId:'w',sessionId:'a',requestId:'new-request'},id:'new-command'}});storage.set(key,newer);
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>settle());
  assert.deepEqual(calls,[read,{action:'workspace.tests.cancel',args:{workspaceId:'w',sessionId:'a',requestId:check.id}},read],'the old mount neither refreshes nor replays after settling');
  assert.equal(storage.get(key),newer);assert.match(text(root.toJSON()),/Check: queued/);assert.doesNotMatch(text(root.toJSON()),/Late cancellation response/);
  assert.equal(Boolean(button(root,'Check the same environment request').props.disabled),false);
 }finally{await act(async()=>root?.unmount())}
});

for(const outcome of ['passed','failed','unknown','cancelled'])test(`later cleanup preserves the ${outcome} check outcome`,async()=>{
 let root;const calls=[];
 const check={id:'q',status:'unknown',detail:'Old cleanup was not confirmed.',
  recoveredOutcome:{outcome,cleanup:{environmentState:'removed'}},check:{state:outcome==='passed'?'completed':'outcome_unknown'}};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,
   act:async(action,args)=>{calls.push(action);return accepted({...page,checks:[check]})}}))});
  const statuses=root.root.findAllByProps({role:'status'}).map(text);
  assert.ok(statuses.includes('Check: '+outcome));
  assert.match(text(root.toJSON()),/Cleanup recovered\. The environment was removed; the check was not repeated\./);
  assert.equal(calls.length,1);assert.equal(calls[0],'workspace.environments.read');
  assert.ok(button(root,'Read check result'),'original result remains inspectable');
  assert.equal(button(root,'Cancel check'),undefined);
 }finally{await act(async()=>root?.unmount())}
});

for(const status of ['stopped','running'])test(`artifact evidence export is ${status==='stopped'?'available':'blocked'} for ${status} work`,async()=>{
 let root;const calls=[],resource={id:'env',revision:8,status,recovery:{available:true}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[resource],capability:{status:'unavailable'}}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  const recover=button(root,'Save artifact evidence');
  assert.equal(recover.props.disabled,status!=='stopped');
  if(status==='stopped'){
   await act(async()=>recover.props.onClick());
   const mutations=calls.filter(row=>row.action!==read.action);
   assert.equal(mutations.length,1);
   assert.equal(mutations[0].action,'workspace.environments.exportArtifacts');
   assert.equal(mutations[0].args.id,'env');assert.equal(mutations[0].args.expectedRevision,8);
  }
 }finally{await act(async()=>root?.unmount())}
});

test('artifacts-only recovery retains the old unknown removal and does not offer replay',async()=>{
 let root;
 const resource={id:'env',revision:9,status:'stopped',recovery:{available:true},
  lastOperation:{state:'outcome_unknown'},lastArtifactExport:{state:'outcome_unknown'},
  artifactExport:{verified:true,archiveBytes:2048}};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:async()=>accepted({items:[resource]})}))});
  assert.equal(button(root,'Save artifact evidence').props.disabled,true);
  assert.match(text(root.toJSON()),/does not include the working folder or authorize removal/);
  assert.match(text(root.toJSON()),/Last request: outcome unknown/);
 }finally{await act(async()=>root?.unmount())}
});

for(const available of [true,false])test(`original environment cleanup remains ${available?'available':'blocked'} when new execution is unavailable`,async()=>{
 let root;const calls=[],resource={id:'env',revision:8,status:'stopped',recovery:{available,reason:'Original connection unavailable'}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[resource],capability:{status:'unavailable',reason:'New backend is unavailable'}}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  assert.equal(button(root,'Run check').props.disabled,true);
  assert.equal(button(root,'Remove environment…').props.disabled,!available);
  assert.equal(button(root,'Check and recover').props.disabled,!available);
  if(available){
   await act(async()=>button(root,'Check and recover').props.onClick());
   const mutation=calls.find(row=>row.action!=='workspace.environments.read');
   assert.equal(mutation.action,'workspace.environments.reconcile');assert.equal(mutation.args.id,'env');assert.equal(mutation.args.expectedRevision,8);
   assert.equal(calls.filter(row=>row.action!=='workspace.environments.read').length,1,'recovery must not run, stop or remove an environment');
  }else assert.match(text(root.toJSON()),/Original connection unavailable/);
 }finally{await act(async()=>root?.unmount())}
});

test('retained size is shown only from verified accounting and an uncertain operation stays visible',async()=>{
 let root;const props={workspaceId:'w',sessionId:'a',active:true};
 const resource={id:'env',revision:9,status:'removed',lastOperation:{state:'outcome_unknown'},lastRecovery:{state:'completed'},retention:{accounting:{verified:true,totalBytes:2048,format:'tar',policy:'files'}}};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{...props,act:async()=>accepted({items:[resource]})}))});
  assert.match(text(root.toJSON()),/Latest saved copy: 2 KiB/);assert.match(text(root.toJSON()),/Last request: outcome unknown/);assert.match(text(root.toJSON()),/Last recovery check: completed/);
  await act(async()=>root.unmount());resource.retention.accounting.verified=false;
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{...props,act:async()=>accepted({items:[resource]})}))});
  assert.match(text(root.toJSON()),/storage size has not been verified/);assert.doesNotMatch(text(root.toJSON()),/Latest saved copy: 0/);
 }finally{await act(async()=>root?.unmount())}
});

for(const full of [false,true])test(`removal keeps ${full?'an explicitly selected complete copy':'the smaller export by default'}`,async()=>{
 let root;const calls=[],resource={id:'env',revision:8,status:'stopped',recovery:{available:true},retention:{mode:'files',fullAvailable:true}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[resource],capability:{status:'unavailable'}}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>button(root,'Remove environment…').props.onClick());
  assert.equal(calls.length,1,'opening the choice has no backend effect');
  const checkbox=root.root.findByProps({type:'checkbox'});assert.equal(checkbox.props.checked,false);
  if(full)await act(async()=>checkbox.props.onChange({target:{checked:true}}));
  await act(async()=>button(root,'Save files and remove environment').props.onClick());
  const mutations=calls.filter(row=>row.action!=='workspace.environments.read');assert.equal(mutations.length,1);
  assert.equal(mutations[0].action,'workspace.environments.remove');assert.equal(mutations[0].args.retentionMode,full?'full':'files');
 }finally{await act(async()=>root?.unmount())}
});

for(const outcome of ['success','transport error','confirmed rejection'])test(`a mutation settled with ${outcome} while hidden releases controls on reopen without replay`,async()=>{
 let settle,root;const calls=[];
 const dispatch=(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action==='workspace.environments.read')return Promise.resolve(accepted(page));
  if(calls.filter(row=>row.action!=='workspace.environments.read').length===1)return new Promise((resolve,reject)=>{settle=()=>outcome==='transport error'?reject(Error('Lost response')):resolve(outcome==='confirmed rejection'?{accepted:false,error:'Rejected'}:accepted({request:{state:'complete'}}))});
  return Promise.resolve(accepted({request:{state:'complete'}}));
 };
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>root.root.findByProps({'aria-label':'Start test environment'}).props.onSubmit({preventDefault(){}}));
  const saved=JSON.parse(storage.get(key)).pending,original=calls[1];
  assert.equal(saved.action,'workspace.environments.start');assert.equal(saved.args.requestId,original.args.requestId);assert.equal(saved.id,original.meta.id);
  assert.equal(button(root,'Refresh test environments').props.disabled,true);
  await act(async()=>root.update(React.createElement(TaskEnvironments,{...props,active:false})));
  await act(async()=>settle());
  assert.doesNotMatch(text(root.toJSON()),/Waiting for the environment request/);
  assert.equal(calls.length,2,'hidden completion does not refresh or replay');
  await act(async()=>root.update(React.createElement(TaskEnvironments,props)));
  assert.deepEqual(calls,[read,original,read],'reopen only refreshes the scoped observation');
  assert.equal(button(root,'Refresh test environments').props.disabled,false);
  if(outcome==='transport error'){
   assert.deepEqual(JSON.parse(storage.get(key)).pending,saved);
   assert.equal(button(root,'Start test environment').props.disabled,true);
   const retry=button(root,'Check the same environment request');assert.equal(Boolean(retry.props.disabled),false);
   await act(async()=>retry.props.onClick());
   assert.deepEqual(calls[3],original,'explicit recovery uses the same request identity and arguments');assert.deepEqual(calls[4],read);
  }else assert.equal(button(root,'Start test environment').props.disabled,false);
  assert.equal(JSON.parse(storage.get(key)).pending,null);
 }finally{await act(async()=>root?.unmount())}
});

for(const failed of [false,true])test(`a late environment ${failed?'error':'page'} cannot replace the reopened view`,async()=>{
 let settle,root;const calls=[];
 const dispatch=(action,args)=>{calls.push({action,args});if(calls.length===1)return new Promise((resolve,reject)=>{settle=()=>failed?reject(Error('STALE ERROR')):resolve(accepted({...page,capability:{status:'unavailable',reason:'STALE PAGE'}}))});return Promise.resolve(accepted(page))};
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>root.update(React.createElement(TaskEnvironments,{...props,active:false})));
  await act(async()=>root.update(React.createElement(TaskEnvironments,props)));
  await act(async()=>settle());
  assert.doesNotMatch(text(root.toJSON()),/STALE/);assert.equal(button(root,'Start test environment').props.disabled,false);assert.deepEqual(calls,[read,read]);
 }finally{await act(async()=>root?.unmount())}
});

for(const failed of [false,true])test(`a settled ${failed?'transport error':'success'} after unmount preserves recovery without replay on remount`,async()=>{
 let settle,root;const calls=[];
 const dispatch=(action,args,meta)=>{calls.push({action,args,...(meta?{meta}:{})});return action==='workspace.environments.read'?Promise.resolve(accepted(page)):new Promise((resolve,reject)=>{settle=()=>failed?reject(Error('Lost response')):resolve(accepted({request:{state:'complete'}}))})};
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>root.root.findByProps({'aria-label':'Start test environment'}).props.onSubmit({preventDefault(){}}));
  assert.ok(JSON.parse(storage.get(key)).pending);
  await act(async()=>root.unmount());await act(async()=>settle());
  assert.equal(Boolean(JSON.parse(storage.get(key)).pending),failed);assert.equal(calls.length,2);
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  assert.deepEqual(calls[2],read);assert.equal(calls.length,3);assert.equal(Boolean(button(root,'Start test environment').props.disabled),failed);
  if(failed){assert.equal(JSON.parse(storage.get(key)).pending.args.requestId,calls[1].args.requestId);assert.equal(Boolean(button(root,'Check the same environment request').props.disabled),false)}
 }finally{await act(async()=>root?.unmount())}
});

for(const rejected of [false,true])test(`an old mount's late ${rejected?'rejection':'success'} cannot clear a newer durable request`,async()=>{
 let root,settleOld,settleNew;const calls=[];
 const dispatch=(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action==='workspace.environments.read')return Promise.resolve(accepted(page));
  const mutations=calls.filter(row=>row.action!=='workspace.environments.read');
  if(mutations.length===1)return new Promise(resolve=>{settleOld=()=>resolve(rejected?{accepted:false,error:'Original rejected'}:accepted({request:{state:'complete'}}))});
  if(mutations.length===2)return Promise.resolve(accepted({request:{state:'complete'}}));
  return new Promise(resolve=>{settleNew=()=>resolve(accepted({request:{state:'complete'}}))});
 };
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 const start=()=>root.root.findByProps({'aria-label':'Start test environment'}).props.onSubmit({preventDefault(){}});
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});await act(async()=>start());
  const original=JSON.parse(storage.get(key)).pending;await act(async()=>root.unmount());
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  assert.equal(calls.filter(row=>row.action!=='workspace.environments.read').length,1,'remount does not replay');
  await act(async()=>button(root,'Check the same environment request').props.onClick());
  assert.equal(calls.filter(row=>row.action!=='workspace.environments.read')[1].args.requestId,original.args.requestId);
  await act(async()=>start());const newer=JSON.parse(storage.get(key)).pending;assert.notEqual(newer.id,original.id);assert.notEqual(newer.args.requestId,original.args.requestId);
  await act(async()=>settleOld());assert.deepEqual(JSON.parse(storage.get(key)).pending,newer,'old completion preserves the newer recovery receipt');
  assert.equal(button(root,'Refresh test environments').props.disabled,true,'the new request still owns its pending controls');
  await act(async()=>settleNew());assert.equal(JSON.parse(storage.get(key)).pending,null);assert.equal(button(root,'Start test environment').props.disabled,false);
 }finally{await act(async()=>root?.unmount())}
});

test('setup recommends managed Docker, retains explicit Incus and shows durable progress',async()=>{
 let root;const calls=[],setup={status:'disabled',reason:'Not set up',backends:[{id:'docker',label:'Docker',available:true,reason:'Available'},{id:'incus',label:'Incus',available:true,reason:'Available'}],operation:null};
 const dispatch=async(action,args)=>{calls.push({action,args});if(action!=='workspace.environments.read')setup.operation={id:'setup',state:'running',detail:'Downloading the approved base image.'};return accepted(action==='workspace.environments.read'?{items:[],capability:{status:'disabled'},setup}:{operation:setup.operation})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  assert.match(text(root.toJSON()),/already installed local test service/);
  assert.match(text(root.toJSON()),/Use Docker for new ordinary checks with the managed Smart Tool runner/);
  assert.match(text(root.toJSON()),/Existing environments keep their original backend/);
  assert.ok(button(root,'Set up with Incus'));
  await act(async()=>button(root,'Set up with Docker').props.onClick());
  const mutation=calls.find(row=>row.action!=='workspace.environments.read');
  assert.equal(mutation.action,'workspace.environments.setup');assert.equal(mutation.args.backend,'docker');assert.ok(mutation.args.requestId);
  assert.deepEqual(Object.keys(mutation.args).sort(),['backend','requestId','sessionId','workspaceId']);
  assert.match(text(root.toJSON()),/Downloading the approved base image/);assert.equal(button(root,'Set up with Docker').props.disabled,true);
 }finally{await act(async()=>root?.unmount())}
});

test('browser check profile uses its server budget and automatic preparation',async()=>{
 let root;const calls=[],view={items:[],capability:{status:'ready',profiles:[{id:'linux-python',label:'Python',defaultTimeoutSeconds:600},{id:'linux-browser',label:'Browser',defaultTimeoutSeconds:1200,preparationRequired:true}]}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?view:{request:{state:'queued'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  const form=root.root.findByProps({'aria-label':'Queue isolated check'});
  await act(async()=>form.findByType('select').props.onChange({target:{value:'linux-browser'}}));
  await act(async()=>form.findByType('textarea').props.onChange({target:{value:'npm test'}}));
  assert.match(text(root.toJSON()),/20-minute limit/);assert.match(text(root.toJSON()),/prepares this project’s locked dependencies/);
  await act(async()=>form.props.onSubmit({preventDefault(){}}));
  const mutation=calls.find(row=>row.action!=='workspace.environments.read');assert.equal(mutation.action,'workspace.tests.submit');
  assert.equal(mutation.args.profileId,'linux-browser');assert.equal(mutation.args.timeoutSeconds,1200);
 }finally{await act(async()=>root?.unmount())}
});

test('unsupported platform explains needs without a misleading setup button',async()=>{
 let root;const setup={status:'unavailable',reason:'Local setup is qualified on Linux.',backends:[{id:'docker',label:'Docker',available:false,reason:'Use a supported Linux host.'}]};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:async()=>accepted({items:[],setup,capability:{status:'unavailable'}}),active:true}))});
  assert.match(text(root.toJSON()),/Use a supported Linux host/);assert.equal(button(root,'Set up with Docker'),undefined);
 }finally{await act(async()=>root?.unmount())}
});

test('verified setup completion reuses original request and remains owner scoped',async()=>{
 let root;const calls=[],setup={status:'unavailable',requestId:'original',operation:{state:'outcome_unknown',canComplete:true,backend:'incus',sessionId:'a',workspaceId:'w'}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action==='workspace.environments.read'?{items:[],setup,capability:{status:'unavailable'}}:{operation:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>button(root,'Finish verified setup').props.onClick());
  const mutation=calls.find(row=>row.action==='workspace.environments.setup');
  assert.deepEqual(mutation.args,{workspaceId:'w',sessionId:'a',requestId:'original',backend:'incus'});
  await act(async()=>{root.update(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'other',act:dispatch,active:true}))});
  assert.equal(button(root,'Finish verified setup').props.disabled,true);
  assert.equal(button(root,'Check setup result').props.disabled,true);
  assert.equal(button(root,'Clean up interrupted setup').props.disabled,true);
 }finally{await act(async()=>root?.unmount())}
});

// Test-first panel contract, now carried on the integrated implementation base.
// Ordinary panel activation still has its existing read. The inspection journey
// must add ONLY passive list/read calls, never mutate() or a hidden refresh.
const removalList='workspace.environments.removal.list',removalRead='workspace.environments.removal.read';
const removalResource={id:'env',sessionId:'a',workspaceId:'w',revision:8,status:'stopped',
 recovery:{available:true},lastOperation:{requestId:'later-stop',action:'workspace.environments.stop',state:'completed'}};
const removalDiscovery=(overrides={})=>({version:1,sessionId:'a',id:'env',revision:8,
 items:[{originalRequestId:'original-remove',state:'outcome_unknown',binding:'resource'}],
 nextOffset:null,...overrides});
const removalHeld=(overrides={})=>({version:1,sessionId:'a',id:'env',revision:8,
 original:{requestId:'original-remove',state:'outcome_unknown'},status:'held',
 code:'original_binding_unverified',executor:'unobserved',physical:'not_observed',
 continuationAvailable:false,riskDecisionAvailable:false,verification:{status:'not_retained'},...overrides});
const inspectArgs={workspaceId:'w',sessionId:'a',id:'env',expectedRevision:8};
const originalArgs={...inspectArgs,originalRequestId:'original-remove'};
const requireButton=(root,label)=>{const found=button(root,label);assert.ok(found,`Missing actual component control: ${label}`);return found};

const recordedVerification={status:'recorded',version:1,reasonCode:'unsupported_type',memberType:'fifo',relativePath:'artifacts/fixture-pipe'};
for(const [label,verification,valid] of [
 ['not retained',{status:'not_retained'},true],
 ['unverified',{status:'unverified'},true],
 ['recorded minimal',{status:'recorded',version:1,reasonCode:'archive_format'},true],
 ['recorded full',recordedVerification,true],
 ['recorded type only',{status:'recorded',version:1,reasonCode:'symbolic_cycle',memberType:'symbolic'},true],
 ['recorded path only',{status:'recorded',version:1,reasonCode:'missing_root',relativePath:'workspace'},true],
 ['missing',undefined,false],['null',null,false],['array',[],false],
 ['unknown status',{status:'success'},false],
 ['extra absent detail',{status:'not_retained',reasonCode:'unsupported_type'},false],
 ['unknown reason',{...recordedVerification,reasonCode:'SYNTHETIC_PRIVATE_VALUE'},false],
 ['wrong version',{...recordedVerification,version:'1'},false],
 ['missing reason',{status:'recorded',version:1},false],
 ['unknown type',{...recordedVerification,memberType:'socket'},false],
 ['null type',{...recordedVerification,memberType:null},false],
 ['extra content',{...recordedVerification,content:'SYNTHETIC_PRIVATE_VALUE'},false],
 ['link target',{...recordedVerification,linkTarget:'/SYNTHETIC_PRIVATE_VALUE'},false],
 ['absolute path',{...recordedVerification,relativePath:'/SYNTHETIC_PRIVATE_VALUE'},false],
 ['parent path',{...recordedVerification,relativePath:'workspace/../SYNTHETIC_PRIVATE_VALUE'},false],
 ['dot path',{...recordedVerification,relativePath:'workspace/.'},false],
 ['newline path',{...recordedVerification,relativePath:'workspace/file\n'},false],
 ['other root',{...recordedVerification,relativePath:'other/file'},false],
 ['long path',{...recordedVerification,relativePath:'workspace/'+'x'.repeat(247)},false],
 ['empty path',{...recordedVerification,relativePath:''},false],
 ['null path',{...recordedVerification,relativePath:null},false],
]){
 test(`passive verification ${label} is ${valid?'accepted without effects':'rejected without rendering details'}`,async()=>{
  let root;const calls=[];
  storage.set(key,JSON.stringify({command:'retained draft',pending:null}));const saved=storage.get(key);
  const dispatch=async(action,args)=>{
   calls.push({action,args});
   if(action==='workspace.environments.read')return accepted({items:[removalResource]});
   if(action===removalList)return accepted(removalDiscovery());
   if(action===removalRead){const value=removalHeld({verification});if(label==='missing')delete value.verification;return accepted(value)}
   throw Error('Unexpected effect: '+action);
  };
  try{
   await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true,readOnly:true}))});
   await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
   await act(async()=>requireButton(root,'Read original removal original-remove').props.onClick());
   if(valid){assert.match(text(root.toJSON()),/Removal remains held/);assert.doesNotMatch(text(root.toJSON()),/does not match/)}
   else{assert.match(text(root.toJSON()),/does not match/);assert.doesNotMatch(text(root.toJSON()),/Removal remains held/)}
   assert.doesNotMatch(text(root.toJSON()),/SYNTHETIC_PRIVATE_VALUE/);
   assert.equal(button(root,'Continue removal'),undefined);assert.equal(button(root,'Replace removal'),undefined);
   assert.equal(storage.get(key),saved);
   assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs},{action:removalRead,args:originalArgs}]);
  }finally{await act(async()=>root?.unmount())}
 });
}

for(const readOnly of [false,true])test(`original removal inspection is passive with readOnly=${readOnly} and a later stop`,async()=>{
 let root;const calls=[],pending={action:'workspace.environments.remove',args:{requestId:'browser-pending-not-original'},id:'saved-command'};
 storage.set(key,JSON.stringify({profileId:'linux-python',command:'keep draft',pending}));const saved=storage.get(key);
 const dispatch=async(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action==='workspace.environments.read'){assert.equal(calls.length,1,'inspection must not refresh');return accepted({items:[removalResource],capability:{status:'unavailable'}})}
  if(action===removalList)return accepted(removalDiscovery());
  if(action===removalRead)return accepted(removalHeld());
  throw Error('Unexpected effect during passive inspection: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true,readOnly}))});
  const inspect=requireButton(root,'Inspect original removal');assert.notEqual(inspect.props.disabled,true);
  await act(async()=>inspect.props.onClick());
  await act(async()=>requireButton(root,'Read original removal original-remove').props.onClick());
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs},{action:removalRead,args:originalArgs}]);
  assert.match(text(root.toJSON()),/Removal remains held/);assert.match(text(root.toJSON()),/Original request: original-remove/);
  assert.match(text(root.toJSON()),/outcome unknown/);assert.match(text(root.toJSON()),/not observed/i);
  assert.equal(button(root,'Continue removal'),undefined);assert.equal(button(root,'Replace removal'),undefined);
  assert.equal(storage.get(key),saved,'passive read must not overwrite/replay a pending effect or draft');
 }finally{await act(async()=>root?.unmount())}
});

for(const problem of ['unbound','foreign-root','wrong-resource','stale-revision'])test(`discovery ${problem} cannot supply a guessed original`,async()=>{
 let root;const calls=[];let value=removalDiscovery();
 if(problem==='unbound')value=removalDiscovery({items:[{originalRequestId:'unbound-original',state:'outcome_unknown',binding:'unbound'}]});
 if(problem==='foreign-root')value.sessionId='other-root';
 if(problem==='wrong-resource')value.id='other-env';
 if(problem==='stale-revision')value.revision=7;
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read'){assert.equal(calls.length,1);return accepted({items:[removalResource]})}
  if(action===removalList)return accepted(value);
  throw Error('No original read or effect may follow invalid/unbound discovery: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  assert.equal(root.root.findAllByType('button').filter(n=>text(n).startsWith('Read original removal')).length,0);
  assert.match(text(root.toJSON()),problem==='unbound'?/not associated with this environment/i:/changed|another|does not match/i);
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs}]);
  assert.equal(button(root,'Continue removal'),undefined);
 }finally{await act(async()=>root?.unmount())}
});

test('lost passive reply requires an explicit re-read, preserving the original effect receipt',async()=>{
 let root;const calls=[];let reads=0;
 storage.set(key,JSON.stringify({pending:{id:'effect-command',action:'workspace.environments.remove',args:{requestId:'old-effect'}},command:'saved draft'}));
 const saved=storage.get(key);
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read'){assert.equal(calls.length,1);return accepted({items:[removalResource]})}
  if(action===removalList)return accepted(removalDiscovery());
  if(action===removalRead){if(++reads===1)throw Error('Lost passive response');return accepted(removalHeld())}
  throw Error('Unexpected effect: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  await act(async()=>requireButton(root,'Read original removal original-remove').props.onClick());
  assert.match(text(root.toJSON()),/Lost passive response/);assert.equal(reads,1);assert.equal(storage.get(key),saved);
  await act(async()=>requireButton(root,'Read original removal original-remove').props.onClick());
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs},
   {action:removalRead,args:originalArgs},{action:removalRead,args:originalArgs}]);
  assert.match(text(root.toJSON()),/Removal remains held/);assert.equal(storage.get(key),saved);
 }finally{await act(async()=>root?.unmount())}
});

for(const transition of ['hidden','new-owner','unmounted'])for(const failed of [false,true])test(`late passive ${failed?'error':'result'} after ${transition} is not applied or retried`,async()=>{
 let root,settle;const calls=[];
 const dispatch=(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read')return Promise.resolve(accepted({items:args.sessionId==='a'?[removalResource]:[]}));
  if(action===removalList)return Promise.resolve(accepted(removalDiscovery()));
  if(action===removalRead)return new Promise((resolve,reject)=>{settle=()=>failed?reject(Error('STALE PASSIVE ERROR')):resolve(accepted(removalHeld({code:'STALE_PASSIVE_RESULT'})))});
  throw Error('Unexpected effect: '+action);
 };
 const props={workspaceId:'w',sessionId:'a',act:dispatch,active:true};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,props))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  await act(async()=>{requireButton(root,'Read original removal original-remove').props.onClick()});
  assert.equal(typeof settle,'function','actual read must be in flight');
  if(transition==='unmounted')await act(async()=>root.unmount());
  else await act(async()=>root.update(React.createElement(TaskEnvironments,{...props,
   ...(transition==='hidden'?{active:false}:{sessionId:'other'})})));
  const before=calls.length;
  await act(async()=>settle());
  assert.equal(calls.length,before,'late reply must not refresh, retry or mutate');
  if(transition!=='unmounted')assert.doesNotMatch(text(root.toJSON()),/STALE|Removal remains held/);
  assert.equal(calls.filter(c=>c.action===removalRead).length,1);
  assert.ok(calls.every(c=>['workspace.environments.read',removalList,removalRead].includes(c.action)));
 }finally{await act(async()=>root?.unmount())}
});

test('inspection coexists with the integrated artifact-export and ordinary removal controls',async()=>{
 // PR831 supplies the existing export control. Keep its effect independent of
 // passive inspection; require the real component rather than a fake control.
 let root;const calls=[];
 const dispatch=async(action,args,meta)=>{
  calls.push({action,args,...(meta?{meta}:{})});
  if(action==='workspace.environments.read')return accepted({items:[removalResource],capability:{status:'unavailable'}});
  if(action===removalList)return accepted(removalDiscovery());
  if(action===removalRead)return accepted(removalHeld());
  if(action==='workspace.environments.exportArtifacts')return accepted({request:{state:'completed'}});
  throw Error('Unexpected destructive action: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  const exportControl=requireButton(root,'Save artifact evidence');
  assert.equal(exportControl.props.disabled,false);
  await act(async()=>requireButton(root,'Remove environment…').props.onClick());
  await act(async()=>requireButton(root,'Keep environment').props.onClick());
  assert.deepEqual(calls,[read],'opening/cancelling removal is not inspection or removal');
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  await act(async()=>requireButton(root,'Read original removal original-remove').props.onClick());
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs},{action:removalRead,args:originalArgs}]);
  await act(async()=>requireButton(root,'Save artifact evidence').props.onClick());
  const effects=calls.filter(c=>!['workspace.environments.read',removalList,removalRead].includes(c.action));
  assert.equal(effects.length,1);assert.equal(effects[0].action,'workspace.environments.exportArtifacts');
  assert.equal(effects[0].args.id,'env');assert.equal(effects[0].args.expectedRevision,8);
  assert.equal(effects[0].args.sessionId,'a');assert.equal(effects[0].args.workspaceId,'w');
  assert.ok(effects[0].args.requestId);assert.ok(effects[0].meta.id);
 }finally{await act(async()=>root?.unmount())}
});

test('removal discovery paging does not choose an original or refresh the environment',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read'){assert.equal(calls.length,1);return accepted({items:[removalResource]})}
  if(action===removalList)return accepted(args.offset===20
   ?removalDiscovery({items:[{originalRequestId:'later-original',state:'outcome_unknown',binding:'resource'}]})
   :removalDiscovery({items:Array.from({length:20},(_,i)=>({originalRequestId:'original-'+i,state:'outcome_unknown',binding:'resource'})),nextOffset:20}));
  throw Error('Paging is not permission to read or repeat an operation: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  await act(async()=>requireButton(root,'More removal requests').props.onClick());
  assert.ok(requireButton(root,'Read original removal later-original'));
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs},
   {action:removalList,args:{...inspectArgs,offset:20,limit:20}}]);
  assert.equal(button(root,'More removal requests'),undefined);
 }finally{await act(async()=>root?.unmount())}
});

for(const malformed of ['too-many','duplicate','oversized','next-offset','binding','extra-field'])test(`malformed discovery ${malformed} never renders untrusted request choices`,async()=>{
 let root;const calls=[],row={originalRequestId:'original-remove',state:'outcome_unknown',binding:'resource'};
 let value=removalDiscovery();
 if(malformed==='too-many')value.items=Array.from({length:21},(_,i)=>({...row,originalRequestId:'id-'+i}));
 if(malformed==='duplicate')value.items=[row,row];
 if(malformed==='oversized'){
  value.items=Array.from({length:20},(_,i)=>({...row,originalRequestId:'\u0000'.repeat(190)+i}));
  assert.equal(value.items.length,20);assert.equal(new Set(value.items.map(x=>x.originalRequestId)).size,20);
  assert.ok(value.items.every(x=>x.originalRequestId.length<=200));
  assert.ok(new TextEncoder().encode(JSON.stringify(value)).length>16384,'Only the encoded page bound is exceeded');
 }
 if(malformed==='next-offset')value.nextOffset=0;
 if(malformed==='binding')value.items=[{...row,binding:'trusted-by-caller'}];
 if(malformed==='extra-field')value.items=[{...row,privateConfig:'SYNTHETIC_PRIVATE_VALUE'}];
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read'){assert.equal(calls.length,1);return accepted({items:[removalResource]})}
  if(action===removalList)return accepted(value);
  throw Error('Malformed data cannot trigger a read or effect: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  assert.match(text(root.toJSON()),/does not match/);assert.doesNotMatch(text(root.toJSON()),/SYNTHETIC_PRIVATE_VALUE/);
  assert.equal(root.root.findAllByType('button').filter(n=>text(n).startsWith('Read original removal')).length,0);
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs}]);
 }finally{await act(async()=>root?.unmount())}
});

for(const mismatch of ['original-id','revision','continuation','private-field'])test(`mismatched passive result ${mismatch} is not presented as held success`,async()=>{
 let root;const calls=[];let value=removalHeld();
 if(mismatch==='original-id')value.original={requestId:'other-original',state:'outcome_unknown'};
 if(mismatch==='revision')value.revision=9;
 if(mismatch==='continuation')value.continuationAvailable=true;
 if(mismatch==='private-field')value.privateConfig='SYNTHETIC_PRIVATE_VALUE';
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read'){assert.equal(calls.length,1);return accepted({items:[removalResource]})}
  if(action===removalList)return accepted(removalDiscovery());
  if(action===removalRead)return accepted(value);
  throw Error('Unexpected effect: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  await act(async()=>requireButton(root,'Read original removal original-remove').props.onClick());
  assert.match(text(root.toJSON()),/does not match/);assert.doesNotMatch(text(root.toJSON()),/Removal remains held|SYNTHETIC_PRIVATE_VALUE/);
  assert.equal(button(root,'Continue removal'),undefined);
  assert.deepEqual(calls,[read,{action:removalList,args:inspectArgs},{action:removalRead,args:originalArgs}]);
 }finally{await act(async()=>root?.unmount())}
});

test('explicit environment refresh invalidates an in-flight inspection without wedging its controls',async()=>{
 let root,settle;const calls=[];let revision=8;
 const dispatch=(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read')return Promise.resolve(accepted({items:[{...removalResource,revision}]}));
  if(action===removalList)return Promise.resolve(accepted(removalDiscovery({revision})));
  if(action===removalRead)return new Promise(resolve=>{settle=()=>resolve(accepted(removalHeld()))});
  throw Error('Unexpected action: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  await act(async()=>{requireButton(root,'Read original removal original-remove').props.onClick()});
  assert.equal(typeof settle,'function');revision=9;
  await act(async()=>requireButton(root,'Refresh test environments').props.onClick());
  await act(async()=>settle());
  assert.doesNotMatch(text(root.toJSON()),/Removal remains held/);
  assert.notEqual(requireButton(root,'Inspect original removal').props.disabled,true);
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  assert.deepEqual(calls.at(-1),{action:removalList,args:{...inspectArgs,expectedRevision:9}});
  assert.equal(calls.filter(x=>x.action===removalRead).length,1);
 }finally{await act(async()=>root?.unmount())}
});

test('inspection started while refresh is pending cannot wedge the refreshed row',async()=>{
 let root,settleRefresh,settleInspection;const calls=[];let refreshes=0;
 const dispatch=(action,args)=>{
  calls.push({action,args});
  if(action==='workspace.environments.read'){
   if(++refreshes===1)return Promise.resolve(accepted({items:[removalResource]}));
   return new Promise(resolve=>{settleRefresh=()=>resolve(accepted({items:[{...removalResource,revision:9}]}))});
  }
  if(action===removalList){
   if(args.expectedRevision===9)return Promise.resolve(accepted(removalDiscovery({revision:9})));
   return new Promise(resolve=>{settleInspection=()=>resolve(accepted(removalDiscovery()))});
  }
  throw Error('No implicit detail read or effect: '+action);
 };
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',act:dispatch,active:true}))});
  await act(async()=>{requireButton(root,'Refresh test environments').props.onClick()});
  assert.equal(typeof settleRefresh,'function');
  await act(async()=>{requireButton(root,'Inspect original removal').props.onClick()});
  assert.equal(typeof settleInspection,'function');
  await act(async()=>settleRefresh());
  await act(async()=>settleInspection());
  assert.notEqual(requireButton(root,'Inspect original removal').props.disabled,true);
  assert.equal(button(root,'Read original removal original-remove'),undefined,'old revision result is discarded');
  await act(async()=>requireButton(root,'Inspect original removal').props.onClick());
  assert.deepEqual(calls.at(-1),{action:removalList,args:{...inspectArgs,expectedRevision:9}});
  assert.ok(requireButton(root,'Read original removal original-remove'));
  assert.equal(calls.filter(x=>x.action===removalRead).length,0);
 }finally{await act(async()=>root?.unmount())}
});

test('known pre-removal failure offers exact-owner recovery with original request identity',async()=>{
 let root;const calls=[],resource={id:'env',sessionId:'a',workspaceId:'w',revision:12,status:'stopped',recovery:{available:true},lastOperation:{action:'workspace.environments.remove',requestId:'original-remove',state:'failed',result:{effectAdmitted:false,phase:'verifying',code:'archive_verification_failed'}}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action===read.action?{items:[resource]}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:dispatch}))});
  await act(async()=>button(root,'Preserve files and finish cleanup').props.onClick());
  const [mutation]=calls.filter(row=>row.action!==read.action);
  assert.equal(mutation.action,'workspace.environments.finishCleanup');
  assert.equal(mutation.args.previousRequestId,'original-remove');
  assert.equal(mutation.args.id,'env');assert.equal(mutation.args.expectedRevision,12);
 }finally{await act(async()=>root?.unmount())}
});

for(const failure of [
 {state:'outcome_unknown',result:{effectAdmitted:false,phase:'verifying',code:'archive_verification_failed'}},
 {state:'failed',result:{phase:'verifying',code:'archive_verification_failed'}},
 {state:'failed',result:{effectAdmitted:false,phase:'verifying',code:'owner_changed'}},
 {state:'failed',result:{effectAdmitted:true,phase:'removing',code:'preservation_failed'}},
]){
 test(`cleanup remains held for ${failure.state}/${failure.result.code}/${failure.result.effectAdmitted}`,async()=>{
  let root;const calls=[],resource={id:'env',sessionId:'a',workspaceId:'w',revision:12,status:'stopped',recovery:{available:true},lastOperation:{action:'workspace.environments.remove',requestId:'original-remove',...failure}};
  const dispatch=async(action,args)=>{calls.push({action,args});return accepted({items:[resource]})};
  try{
   await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:dispatch}))});
   assert.equal(button(root,'Preserve files and finish cleanup'),undefined);
   assert.ok(button(root,'Inspect original removal'));
   assert.deepEqual(calls.map(row=>row.action),[read.action]);
  }finally{await act(async()=>root?.unmount())}
 });
}


test('independent queued check remains available while another check runs',async()=>{
 let root;const calls=[];
 const view={items:[],checks:[{id:'running',status:'checking'}],capability:{status:'ready',ownedEnvironments:1,maxEnvironmentsPerChat:2,profiles:[{id:'linux-python',label:'Python',defaultTimeoutSeconds:300}]}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action===read.action?view:{status:'queued'})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:dispatch}))});
  const form=root.root.findByProps({'aria-label':'Queue isolated check'});
  await act(async()=>form.findByType('textarea').props.onChange({target:{value:'python second.py'}}));
  await act(async()=>form.findByType('input').props.onChange({target:{checked:true}}));
  await act(async()=>form.props.onSubmit({preventDefault(){}}));
  const mutation=calls.find(row=>row.action==='workspace.tests.submit');
  assert.equal(mutation.args.independent,true);assert.equal(mutation.args.command,'python second.py');
 }finally{await act(async()=>root?.unmount())}
});

test('environment paging keeps exact owner when stopping a later page',async()=>{
 let root;const calls=[];
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action===read.action?{
  items:[{id:args.offset?'older-owner':'newer-owner',revision:7,status:'running',recovery:{available:true}}],
  offset:args.offset||0,nextOffset:args.offset?null:10,capability:{status:'ready',profiles:[]}}:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:dispatch}))});
  await act(async()=>button(root,'Older environments').props.onClick());
  assert.match(text(root.toJSON()),/older-owner/);assert.doesNotMatch(text(root.toJSON()),/newer-owner/);
  await act(async()=>button(root,'Stop and keep data').props.onClick());
  const mutation=calls.find(row=>row.action==='workspace.environments.stop');
  assert.equal(mutation.args.id,'older-owner');assert.equal(mutation.args.expectedRevision,7);
  assert.equal(calls.filter(row=>row.action===read.action).at(-1).args.offset,10);
 }finally{await act(async()=>root?.unmount())}
});


test('automatic observation keeps the selected environment page while checks run',async()=>{
 let root;const calls=[],timers=new Map();let counter=0;
 const originalInterval=globalThis.setInterval,originalClear=globalThis.clearInterval;
 globalThis.setInterval=callback=>{const id=++counter;timers.set(id,callback);return id};
 globalThis.clearInterval=id=>timers.delete(id);
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted({items:[],offset:args.offset,nextOffset:args.offset===0?10:null,checks:[{id:'running',status:'checking'}]})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:dispatch}))});
  await act(async()=>button(root,'Older environments').props.onClick());
  await act(async()=>{for(const callback of timers.values())callback()});
  assert.equal(calls.at(-1).args.offset,10);
 }finally{await act(async()=>root?.unmount());globalThis.setInterval=originalInterval;globalThis.clearInterval=originalClear}
});


for(const limit of [0,1,2])test(`manual Start follows the effective environment count limit ${limit}`,async()=>{
 let root;const calls=[];
 const view={items:[{id:'retained',revision:2,status:'stopped'}],capability:{status:'ready',ownedEnvironments:1,maxEnvironmentsPerChat:limit,profiles:[{id:'linux-python',label:'Python'}]}};
 const dispatch=async(action,args)=>{calls.push({action,args});return accepted(action===read.action?view:{request:{state:'completed'}})};
 try{
  await act(async()=>{root=create(React.createElement(TaskEnvironments,{workspaceId:'w',sessionId:'a',active:true,act:dispatch}))});
  const forms=root.root.findAllByProps({'aria-label':'Start test environment'});
  assert.equal(forms.length,limit===1?0:1);
  if(limit===0){
   await act(async()=>forms[0].props.onSubmit({preventDefault(){}}));
   const start=calls.find(row=>row.action==='workspace.environments.start');
   assert.equal(start.args.workspaceId,'w');assert.equal(start.args.sessionId,'a');assert.equal(start.args.profileId,'linux-python');
   assert.equal(calls.filter(row=>row.action==='workspace.environments.remove').length,0);
  }
 }finally{await act(async()=>root?.unmount())}
});
