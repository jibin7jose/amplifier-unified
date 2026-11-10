import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';

const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {SettingsExperience}=await server.ssrLoadModule('/src/settings-experience.jsx');
const {settingsPatch}=await server.ssrLoadModule('/src/settings-navigation.js');
test.after(()=>server.close());
const unrelated='ValueError: Unsupported runtime control: goal.get';
function fixture(page='updates'){
 return {view:settingsPatch(page),setup:{providers:[]},settings:{updates:{autoCheck:true,autoInstall:false,intervalHours:8}},
  management:{phase:'error',operation:'runtime.control',error:unrelated},
  updates:{phase:'app-staged',application:{current:'1.0.0',latest:'v1.0.1',status:'update'},pendingApp:{latest:'v1.0.1'},
   blockers:['one','two','three'].map(sessionId=>({kind:'conversation',sessionId,label:'Conversation work is running or finishing'}))}};
}
function render(state){
 const before=structuredClone(state),calls=[];
 const html=renderToStaticMarkup(React.createElement(SettingsExperience,{state,act:(...args)=>calls.push(args)}));
 assert.deepEqual(state,before);assert.deepEqual(calls,[]);
 return html;
}

test('Updates keeps its queued work and preferences without unrelated runtime errors',()=>{
 const html=render(fixture());
 assert.doesNotMatch(html,/Unsupported runtime control/);
 assert.match(html,/Waiting for 3 chats to finish/);
 assert.match(html,/View active work \(3\)/);
 assert.equal((html.match(/Conversation work is running or finishing/g)||[]).length,3);
 assert.match(html,/<option value="8" selected="">Every 8 hours<\/option>/);
 assert.match(html,/type="checkbox" data-action="settings.update" checked=""/);
});
test('Updates still shows its own failure while an unrelated management failure is retained',()=>{
 const state=fixture();state.updates.phase='error';state.updates.error='The managed restart request was rejected.';
 const html=render(state);
 assert.match(html,/Updates need attention/);
 assert.match(html,/role="alert"[\s\S]*The managed restart request was rejected\./);
 assert.doesNotMatch(html,/Unsupported runtime control/);
 assert.equal(state.management.error,unrelated);
});
test('the Updates exclusion does not remove existing feedback from other Settings pages',()=>{
 assert.match(render(fixture('ready-conversations')),/role="alert">ValueError: Unsupported runtime control: goal.get/);
});
