import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {MaintenanceSettings}=await server.ssrLoadModule('/src/maintenance.jsx');
const {SettingsPageContext}=await server.ssrLoadModule('/src/settings-ui.jsx');
test.after(()=>server.close());
globalThis.IS_REACT_ACT_ENVIRONMENT=true;

test('Advanced recovery explains shared keep-one and retries cleanup without another reset',async()=>{
 const calls=[],state={sessions:[],view:{},maintenance:{resetOperationId:'fixture',
  retained:'/fixture/backups/reset-fixture',detail:'Reset succeeded; older-backup cleanup incomplete.',
  resetRetention:{cleanupComplete:false,errors:['Fixture access denied']}}};
 let renderer;
 await act(()=>{renderer=create(React.createElement(SettingsPageContext.Provider,{value:'reset'},
  React.createElement(MaintenanceSettings,{state,act:async(...args)=>calls.push(args)})))});
 try{
  const text=JSON.stringify(renderer.toJSON());
  assert.match(text,/Each successful reset replaces the previous reset recovery point/);
  assert.match(text,/shared with command-line resets/);
  assert.match(text,/Reset succeeded; older-backup cleanup incomplete/);
  assert.match(text,/Fixture access denied/);
  const retry=renderer.root.findByProps({'data-action':'maintenance.resetCleanup'});
  await act(()=>retry.props.onClick());
  assert.deepEqual(calls,[['maintenance.resetCleanup']]);
  state.actionStatus={'maintenance.resetCleanup':{phase:'working'}};
  await act(()=>renderer.update(React.createElement(SettingsPageContext.Provider,{value:'reset'},
   React.createElement(MaintenanceSettings,{state,act:async(...args)=>calls.push(args)}))));
  assert.equal(renderer.root.findByProps({'data-action':'maintenance.resetCleanup'}).props.disabled,true);
 }finally{await act(()=>renderer.unmount())}
});