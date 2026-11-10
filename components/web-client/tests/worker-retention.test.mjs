import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkerRetentionSettings}=await server.ssrLoadModule('/src/worker-retention.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

test('readiness saves valid count and hours without a selection-start policy',async()=>{
 const calls=[],state={runtime:{retention:{max_warm_workers:32,idle_timeout_hours:12}}};let root;
 const send=async(action,args)=>{calls.push({action,args});return {accepted:true}};
 await renderAct(()=>{root=create(React.createElement(WorkerRetentionSettings,{state,act:send}))});
 try{
  const field=id=>root.root.findByProps({id});
  const blur=(id,value,valid)=>renderAct(()=>field(id).props.onBlur({target:{value,checkValidity:()=>valid}}));
  assert.equal(field('warm-worker-count').props.value,'32');
  await blur('warm-worker-count','1.5',false);await blur('warm-worker-count','',true);
  assert.deepEqual(calls,[]);
  await blur('warm-worker-count','100',true);
  await blur('warm-worker-hours','336',true);
  assert.equal(root.root.findAllByProps({type:'checkbox'}).length,0);
  assert.deepEqual(calls.map(c=>c.args.patch),[{max_warm_workers:100},{idle_timeout_hours:336}]);
  await blur('warm-worker-count','0',true);
  assert.deepEqual(calls.at(-1),{action:'runtime.retention.update',args:{patch:{max_warm_workers:0}}});
 }finally{await renderAct(()=>root.unmount())}
});
