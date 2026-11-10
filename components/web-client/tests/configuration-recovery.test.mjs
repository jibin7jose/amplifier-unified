import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {recoveryRequestObserver} from './configuration-recovery-observer.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ConfigurationRecovery,recoveryMeaning}=await server.ssrLoadModule('/src/configuration-recovery.jsx');
const {ModuleSettings}=await server.ssrLoadModule('/src/bundles.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const session=(id='owner')=>({id,title:id,pendingConfiguration:{operationId:'op-'+id,revision:3,phase:'error',effect:'unknown'},configurationBusy:true});
const report=(s,extra={})=>({sessionId:s.id,operationId:s.pendingConfiguration.operationId,revision:3,attempts:1,state:{phase:'error',effect:'unknown'},history:[],recovery:{canReconcile:true,canResume:false,reason:'Check the saved outcome.'},...extra});
const button=(root,verb)=>root.root.findAllByType('button').find(n=>n.props['data-action']==='configuration.operation.'+verb);
const text=root=>JSON.stringify(root.toJSON());

test('held view is visible without a plan and never inspects or edits its saved draft',async()=>{
 const s=session(),calls=[],state={view:{moduleEditor:{sessionId:s.id,text:'uncommitted-draft'}}};let root;
 const act=async(action,args)=>{calls.push({action,args});return {accepted:true,result:report(s)}};
 await renderAct(async()=>{root=create(React.createElement(ModuleSettings,{state,session:s,act}))});
 assert.match(text(root),/Original configuration change/);
 assert.deepEqual(calls.map(c=>c.action),['configuration.operation.read']);
 assert.equal(state.view.moduleEditor.text,'uncommitted-draft');
 assert.equal(button(root,'resume').props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('healthy opening waits for explicit inspection and retains ordinary apply refresh',async()=>{
 let root,s={id:'healthy',title:'Healthy',configuration:{plan:{tools:[]}}};
 const state={view:{moduleEditor:{sessionId:s.id,text:'{"tools":[]}',appliedAt:1}}},calls=[];
 const act=async(action,args)=>{calls.push({action,args});return {accepted:true,result:{...report(session()),sessionId:s.id}}};
 const render=()=>React.createElement(ModuleSettings,{state,session:s,act});
 await renderAct(async()=>{root=create(render())});
 assert.ok(!calls.some(c=>c.action==='configuration.inspect'));
 await renderAct(async()=>root.root.findAllByType('button')
  .find(n=>n.props['data-action']==='configuration.inspect').props.onClick());
 assert.equal(calls.filter(c=>c.action==='configuration.inspect'&&c.args.id===s.id).length,1);
 await renderAct(async()=>{
  s={...s,pendingConfiguration:{phase:'ready',effect:'mounted',appliedAt:2},configuration:{plan:{tools:[],hooks:[]}}};
  root.update(render());
 });
 assert.ok(calls.some(c=>c.action==='view.update'&&c.args.patch.moduleEditor.appliedAt===2));
 await renderAct(async()=>root.unmount());
});

test('real buttons use original operation CAS, acceptance stays queued, and clicks do not double-submit',async()=>{
 let s=session(),view=report(s),root;const calls=[];
 const act=async(action,args,meta)=>{
  calls.push({action,args,meta});
  if(action.endsWith('.reconcile')){view=report(s,{revision:4,state:{phase:'error',effect:'none'},recovery:{canResume:true,canReconcile:true}});s={...s,pendingConfiguration:{...s.pendingConfiguration,revision:4}}}
  if(action.endsWith('.resume'))view=report(s,{revision:5,state:{phase:'queued',effect:'none'},recovery:{canResume:false,canReconcile:false}});
  return {accepted:true,result:view};
 };
 const render=()=>React.createElement(ConfigurationRecovery,{session:s,act});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>{await button(root,'reconcile').props.onClick();root.update(render())});
 assert.equal(button(root,'resume').props.disabled,false);
 await renderAct(async()=>{const click=button(root,'resume').props.onClick;await Promise.all([click(),click()])});
 const resume=calls.filter(c=>c.action.endsWith('.resume'));
 assert.equal(resume.length,1);
 assert.deepEqual(resume[0].args,{sessionId:'owner',operationId:'op-owner',expectedRevision:4});
 assert.equal(resume[0].meta.id,'configuration-recovery-op-owner-4-resume');
 assert.match(text(root),/not mounted yet/);assert.doesNotMatch(text(root),/Changes are applied to the loaded session/);
 await renderAct(async()=>root.unmount());
});

test('lost acknowledgement preserves a read-only recovery route and never resends the same revision',async()=>{
 const s=session();let root;const calls=[];
 const act=async(action)=>{calls.push(action);if(action.endsWith('.resume'))throw Error('private failure');return {accepted:true,result:report(s,{state:{phase:'error',effect:'none'},recovery:{canResume:true,canReconcile:true}})}};
 await renderAct(async()=>{root=create(React.createElement(ConfigurationRecovery,{session:s,act}))});
 await renderAct(async()=>button(root,'resume').props.onClick());
 assert.match(text(root),/request result is not confirmed/);assert.doesNotMatch(text(root),/private failure/);
 await renderAct(async()=>button(root,'read').props.onClick());
 assert.equal(button(root,'resume').props.disabled,true);
 assert.equal(calls.filter(c=>c.endsWith('.resume')).length,1);
 await renderAct(async()=>root.unmount());
});

test('queued own-task settings explain automatic continuation without an extra command',async()=>{
 const s=session();let root;const calls=[];
 const view=report(s,{state:{phase:'queued',effect:'none'},recovery:{canResume:false,canReconcile:false,
  reason:'Waiting for the settings change; this task will continue automatically after verification.'}});
 const act=async(action)=>{calls.push(action);return {accepted:true,result:view}};
 await renderAct(async()=>{root=create(React.createElement(ConfigurationRecovery,{session:s,act}))});
 assert.match(text(root),/continue automatically after verification/);
 assert.match(text(root),/settings change is queued and is not mounted yet/);
 assert.equal(button(root,'resume').props.disabled,true);
 assert.equal(button(root,'reconcile').props.disabled,true);
 assert.deepEqual(calls,['configuration.operation.read']);
 await renderAct(async()=>root.unmount());
});

test('proven saved continuation uses the original resume and remains unconfirmed during readback',async()=>{
 const s=session();let root;const calls=[];
 const view=report(s,{state:{phase:'error',effect:'saved_not_mounted'},recovery:{canResume:true,canReconcile:true,
  reason:'Settings were saved. Resume checks the loaded session; it never repeats the settings write.'}});
 const act=async(action,args)=>{calls.push({action,args});return {accepted:true,result:view}};
 await renderAct(async()=>{root=create(React.createElement(ConfigurationRecovery,{session:s,act}))});
 assert.equal(button(root,'resume').props.disabled,false);
 assert.match(text(root),/never repeats the settings write/);
 await renderAct(async()=>button(root,'resume').props.onClick());
 assert.deepEqual(calls.find(c=>c.action.endsWith('.resume')).args,
  {sessionId:'owner',operationId:'op-owner',expectedRevision:3});
 assert.equal(calls.filter(c=>c.action.endsWith('.resume')).length,1);
 const meaning=recoveryMeaning({state:{phase:'applying',effect:'saved_not_mounted'}});
 assert.match(meaning,/Confirming the saved change/);assert.match(meaning,/No settings write is repeated/);
 assert.doesNotMatch(meaning,/Changes are applied/);
 await renderAct(async()=>root.unmount());
});

test('foreign responses cannot enable a control and a stale owner completion cannot replace a new owner',async()=>{
 let root,resolve;const owner=session(),peer=session('peer');
 const act=async()=>new Promise(done=>{resolve=done});
 await renderAct(async()=>{root=create(React.createElement(ConfigurationRecovery,{key:owner.id,session:owner,act}))});
 const oldResolve=resolve;
 await renderAct(async()=>root.update(React.createElement(ConfigurationRecovery,{key:peer.id,session:peer,act})));
 await renderAct(async()=>oldResolve({accepted:true,result:report(owner,{recovery:{canResume:true}})}));
 assert.doesNotMatch(text(root),/op-owner/);assert.equal(button(root,'resume').props.disabled,true);
 await renderAct(async()=>resolve({accepted:true,result:report(owner,{recovery:{canResume:true}})}));
 assert.match(text(root),/Status could not be confirmed/);assert.equal(button(root,'resume').props.disabled,true);
 await renderAct(async()=>root.unmount());
});

for(const effect of ['unknown','saved_not_mounted'])test(`${effect} stays held regardless of unrelated global action success`,async()=>{
 const s=session();let root;
 const act=async()=>({accepted:true,result:report(s,{state:{phase:'error',effect},recovery:{canResume:false,canReconcile:false,reason:'Held.'}})});
 await renderAct(async()=>{root=create(React.createElement(ModuleSettings,{session:s,state:{view:{},actionStatus:{'configuration.apply':{phase:'ready',target:{id:'peer'}}}},act}))});
 assert.equal(button(root,'resume').props.disabled,true);assert.doesNotMatch(text(root),/Changes are applied to the loaded session/);
 await renderAct(async()=>root.unmount());
});

const observedRequest=(method='POST',body={action:'configuration.operation.read',args:{sessionId:'owner'},id:'read-1'},url='http://127.0.0.1:9000/api/actions')=>({
 method:()=>method,url:()=>url,postDataJSON:()=>body,
});

test('recovery observer separates bodyless GET catalog reads from validated action POSTs',()=>{
 const actions=[],observer=recoveryRequestObserver('http://127.0.0.1:9000',actions);
 const get=observedRequest('GET',null);
 get.postDataJSON=()=>{throw Error('A catalog read must not parse any action body.')};
 assert.doesNotThrow(()=>observer.record(get,0));
 observer.record(observedRequest(),1);
 observer.assertHealthy();
 assert.deepEqual(actions,[{browser:1,action:'configuration.operation.read',args:{sessionId:'owner'},id:'read-1'}]);
 assert.deepEqual(observer.snapshot(),{catalogReads:1,postRequests:1,recordedActions:1,failureCount:0,failures:[],omittedFailures:0});
});

test('recovery observer requires the exact fixture origin and action endpoint',()=>{
 const actions=[],observer=recoveryRequestObserver('http://127.0.0.1:9000',actions);
 for(const url of ['http://127.0.0.1:9001/api/actions','http://127.0.0.1:9000/else/api/actions','http://127.0.0.1:9000/api/actions/']){
  const request=observedRequest('POST',null,url);
  request.postDataJSON=()=>{throw Error('Unrelated requests must not be parsed.')};
  observer.record(request,0);
 }
 observer.assertHealthy();assert.deepEqual(actions,[]);
 assert.equal(observer.snapshot().postRequests,0);
});

test('null or malformed POST bodies stay explicit failures rather than recorded actions',()=>{
 for(const body of [null,[],{},'text',{action:7},{action:''},{action:'configuration.operation.resume',args:null},{action:'configuration.operation.resume',id:[]} ]){
  const actions=[],observer=recoveryRequestObserver('http://127.0.0.1:9000',actions);
  assert.doesNotThrow(()=>observer.record(observedRequest('POST',body),0));
  assert.deepEqual(actions,[]);
  assert.equal(observer.snapshot().failureCount,1);
  assert.equal(observer.snapshot().failures[0].code,'invalid_action_post');
  assert.throws(()=>observer.assertHealthy(),/Recovery request observer failed/);
 }
});

test('POST JSON decoding failure is bounded and never leaks raw error or request contents',()=>{
 const observer=recoveryRequestObserver('http://127.0.0.1:9000',[]),request=observedRequest();
 request.postDataJSON=()=>{throw Error('SYNTHETIC_PRIVATE_BODY')};
 for(let i=0;i<20;i++)assert.doesNotThrow(()=>observer.record(request,0));
 const snapshot=observer.snapshot();
 assert.equal(snapshot.failureCount,20);assert.equal(snapshot.failures.length,8);assert.equal(snapshot.omittedFailures,12);
 assert.equal(snapshot.failures[0].code,'action_post_decode_failed');
 assert.doesNotMatch(JSON.stringify(snapshot),/SYNTHETIC_PRIVATE_BODY/);
 assert.throws(()=>observer.assertHealthy(),/Recovery request observer failed/);
});

test('malformed observer evidence fails the awaited body while browser and fixture cleanup still run',async()=>{
 const observer=recoveryRequestObserver('http://127.0.0.1:9000',[]),closed=[];
 let originalError,retained;
 await assert.rejects(async()=>{
  try{
   observer.record(observedRequest('POST',null),0);
   observer.assertHealthy();
  }catch(error){originalError=error;throw error}
  finally{
   try{await finishFixture({close:[['Owned browser',async()=>closed.push('browser')]],stop:async()=>closed.push('fixture'),originalError})}
   finally{retained=observer.snapshot()}
  }
 },error=>error===originalError&&/Recovery request observer failed/.test(error.message));
 assert.deepEqual(closed,['browser','fixture']);
 assert.equal(retained.failureCount,1);assert.equal(retained.failures[0].code,'invalid_action_post');
});

test('late observer failure during browser close is retained and still fails after fixture shutdown',async()=>{
 const observer=recoveryRequestObserver('http://127.0.0.1:9000',[]),closed=[];
 let retained;
 await assert.rejects(async()=>{
  try{
   await finishFixture({
    close:[['Owned browser',async()=>{observer.record(observedRequest('POST',null),0);closed.push('browser')}]],
    stop:async()=>closed.push('fixture'),
   });
  }finally{retained=observer.snapshot();observer.assertHealthy()}
 },/Recovery request observer failed/);
 assert.deepEqual(closed,['browser','fixture']);assert.equal(retained.failureCount,1);
});
