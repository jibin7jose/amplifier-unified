import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
import {settingsSections} from '../src/settings-navigation.js';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {AIConnections}=await server.ssrLoadModule('/src/ai-connections.jsx');
const {AdvancedSettings}=await server.ssrLoadModule('/src/settings-everyday.jsx');
const {SettingsDraftProvider}=await server.ssrLoadModule('/src/settings-drafts.jsx');
const {ConfigurationBoundary}=await server.ssrLoadModule('/src/configuration-scope.jsx');
const {ConfigurationDiagnostics}=await server.ssrLoadModule('/src/registry.jsx');
const {NotificationSettings}=await server.ssrLoadModule('/src/notification-settings.jsx');
test.after(()=>server.close());globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test('AI setup welcome is only shown after an authoritative empty provider read',()=>{
 const render=setup=>renderToStaticMarkup(React.createElement(AIConnections,{state:{setup},act:()=>Promise.resolve({accepted:true}),navigate:()=>{}}));
 assert.doesNotMatch(render({}),/Start with one connection/);
 assert.match(render({}),/Loading AI connections/);
 assert.match(render({providers:[],providersLoadedAt:1}),/Start with one connection/);
 assert.doesNotMatch(render({operations:{'providers.list:':{phase:'error',error:'Offline'}}}),/Start with one connection/);
 assert.match(render({operations:{'providers.list:':{phase:'error',error:'Offline'}}}),/Retry loading connections/);
 assert.doesNotMatch(render({providers:[{id:'one',module:'provider-openai',enabled:true,config:{model:'test'}}],providersLoadedAt:1}),/Use for images/);
});

test('connection details and Advanced retain ordinary controls, not image-only settings',()=>{
 const provider={id:'one',module:'provider-openai',credentialsConfigured:true,config:{default_model:'chat-fixture',image_generation:{enabled:true,model:'legacy-image-pin'}}};
 const state={view:{aiConnectionEditor:{step:'detail',id:'one',module:provider.module}},setup:{providers:[provider],providersLoadedAt:1,metadata:{'provider-openai':{imageGeneration:{schemaVersion:1}}}}};
 const detail=renderToStaticMarkup(React.createElement(AIConnections,{state,act:()=>{},navigate:()=>{}}));
 for(const control of ['Choose model','Change credentials','Check connection','Refresh credentials','Send test message','Remove connection','Full connection settings'])assert.ok(detail.includes(control),control);
 assert.match(detail,/chat-fixture/);
 const advanced=renderToStaticMarkup(React.createElement(AdvancedSettings,{state,sections:settingsSections,navigate:()=>{}}));
 assert.match(advanced,/data-settings-destination="routing"/);
 assert.match(advanced,/data-settings-destination="providers"/);
 for(const html of [detail,advanced])assert.doesNotMatch(html,/Manage image generation|Image preferences|Image generation settings|Use for images|Image model|image-generation|providers\.(?:configureImages|imageModels)/);
});

test('a legacy image draft neither requests image discovery nor registers an unsaved image editor',async()=>{
 const calls=[],entries=new Map(),registry={register:(id,entry)=>entries.set(id,entry),remove:id=>entries.delete(id),hasUnsaved:()=>[...entries.values()].some(entry=>entry.dirty),confirmClose:async()=>assert.fail('No draft navigation decision expected')};
 const state={view:{aiConnectionEditor:{step:'images',imageEdited:true,imageModel:'unsaved-image'},imageSettingsEditor:{imageEdited:true}},
  setup:{providers:[{id:'one',module:'provider-openai',config:{default_model:'chat-fixture'}}],providersLoadedAt:1}};
 let renderer;
 await act(()=>{renderer=create(React.createElement(SettingsDraftProvider,{value:registry},React.createElement(AIConnections,{state,act:async(action,args)=>{calls.push([action,args]);return {accepted:true}},navigate:()=>{}})))});
 try{
  assert.deepEqual(calls.map(([action])=>action).sort(),['modules.diagnostics','providers.decisionModels','providers.list','routing.list']);
  assert.deepEqual([...entries.keys()],['ai-connections']);
  assert.equal(entries.get('ai-connections').dirty,false);
  assert.ok(renderer.root.findAllByType('button').some(button=>button.children.includes('Connect another service')));
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()),/Manage image generation|Image preferences|Use for images|Image model/);
 }finally{await act(()=>renderer.unmount())}
 assert.equal(entries.size,0);
});

test('ordinary credential drafts retain private values and workspace scope after rejected saves',async()=>{
 const target={kind:'workspace',workspaceId:'workspace-one'},calls=[],entries=new Map(),navigation=[];
 const provider={id:'one',module:'provider-openai',config:{default_model:'chat-fixture',image_generation:{enabled:false}}};
 let state={setup:{configurationTarget:target,providers:[provider],providersLoadedAt:1},view:{
  aiConnectionEditor:{step:'model',id:'global-other',model:'global-draft'},
  configurationDrafts:{'workspace:workspace-one':{aiConnectionEditor:{step:'connect',id:'one',module:provider.module,scope:'project',credentialMode:'private'}},
   'workspace:workspace-two':{providerEditor:{id:'other',model:'other-draft'}}}}};
 const originalGlobal=structuredClone(state.view.aiConnectionEditor),originalOther=structuredClone(state.view.configurationDrafts['workspace:workspace-two']);
 const registry={register:(id,entry)=>entries.set(id,entry),remove:id=>entries.delete(id),hasUnsaved:()=>[...entries.values()].some(entry=>entry.dirty),confirmClose:async()=>assert.fail('No draft navigation decision expected')};
 const send=async(action,args)=>{
  calls.push([action,args]);
  if(action==='view.update')state={...state,view:{...state.view,...args.patch}};
  return action==='providers.save'?{accepted:false,error:'Fixture connection save rejected'}:{accepted:true};
 };
 const render=()=>React.createElement(SettingsDraftProvider,{value:registry},
  React.createElement(ConfigurationBoundary,{state,act:send,target},config=>React.createElement(AIConnections,{...config,navigate:page=>navigation.push(page)})));
 let renderer;await act(()=>{renderer=create(render())});
 try{
  const key=()=>renderer.root.findByProps({id:'workspace-workspace-one-ai-private-key'});
  await act(()=>key().props.onChange({target:{value:'private-fixture-key'}}));
  assert.equal(entries.get('ai-connections').dirty,true);
  assert.ok(!JSON.stringify(state).includes('private-fixture-key'));
  await act(()=>renderer.root.findAllByType('button').find(button=>button.props['data-action']==='providers.save').props.onClick());
  assert.deepEqual(calls.find(([action])=>action==='providers.save'),['providers.save',{id:'one',module:provider.module,config:provider.config,apiKey:'private-fixture-key',scope:'project',configurationTarget:target}]);
  assert.equal(key().props.value,'private-fixture-key');
  assert.equal(entries.get('ai-connections').dirty,true);
  assert.match(JSON.stringify(renderer.toJSON()),/Fixture connection save rejected/);
  await act(()=>entries.get('ai-connections').review());
  await act(()=>renderer.update(render()));
  assert.deepEqual(navigation,['ai-connections']);
  assert.equal(key().props.value,'private-fixture-key');
  assert.equal(state.view.configurationDrafts['workspace:workspace-one'].aiConnectionEditor.scope,'project');
  assert.deepEqual(state.view.aiConnectionEditor,originalGlobal);
  assert.deepEqual(state.view.configurationDrafts['workspace:workspace-two'],originalOther);
  assert.ok(!calls.some(([action])=>['providers.configureImages','providers.imageModels'].includes(action)));
  await act(()=>entries.get('ai-connections').discard());
  await act(()=>renderer.update(render()));
  assert.equal(entries.get('ai-connections').dirty,false);
  assert.equal(state.view.configurationDrafts['workspace:workspace-one'].aiConnectionEditor.scope,'project');
  assert.deepEqual(state.view.aiConnectionEditor,originalGlobal);
  assert.deepEqual(state.view.configurationDrafts['workspace:workspace-two'],originalOther);
 }finally{await act(()=>renderer.unmount())}
});

test('capability diagnostics distinguish unconfirmed scope, pending read, failure and confirmed empty',()=>{
 const render=(diagnostic,readStatus)=>renderToStaticMarkup(React.createElement(ConfigurationDiagnostics,{
  scope:'local',state:{registry:{diagnostics:diagnostic},actionStatus:{'modules.diagnostics':readStatus}},act:()=>{}
 }));
 for(const [diagnostic,status] of [
  [undefined,undefined],
  [{scope:'global',items:[]},undefined],
  [{scope:'local'},undefined],
  [{scope:'local',items:[]},{phase:'queued'}],
  [{scope:'local',items:[]},{phase:'working'}],
 ]){
  const html=render(diagnostic,status);
  assert.match(html,/Loading configuration diagnostics for local/);
  assert.doesNotMatch(html,/No retained preferences|Use composed policy/);
 }
 for(const diagnostic of [{scope:'global',items:[]},{scope:'local',items:[]}]){
  const html=render(diagnostic,{phase:'error',error:'Fixture local read failed'});
  assert.match(html,/role="alert".*Fixture local read failed/);
  assert.match(html,/Refresh capability diagnostics/);
  assert.doesNotMatch(html,/No retained preferences|Use composed policy/);
 }
 assert.match(render({scope:'local',items:[]},{phase:'ready'}),/No retained preferences need reconciliation/);
 assert.match(render({scope:'local',items:[]},{phase:'error'}),/Configuration diagnostics are unavailable/);
});

test('diagnostic scope change and refresh recover from a failed read without exposing stale reconciliation',async()=>{
 const calls=[],row={id:'legacy-image-off',module:'tool-image',sourceScope:'local',sourceKey:'image_generation.mode',revision:'a'.repeat(64),state:'disabled',canReconcile:true,message:'Retained Off'};
 let scope='global',state={registry:{diagnostics:{scope:'global',items:[]}}},renderer;
 const send=async(action,args)=>{calls.push([action,args]);return {accepted:true}};
 const render=()=>React.createElement(ConfigurationDiagnostics,{scope,state,act:send});
 await act(()=>{renderer=create(render())});
 try{
  assert.match(JSON.stringify(renderer.toJSON()),/No retained preferences/);
  scope='local';await act(()=>renderer.update(render()));
  assert.match(JSON.stringify(renderer.toJSON()),/Loading configuration diagnostics for /);
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()),/No retained preferences|Use composed policy/);
  state={...state,actionStatus:{'modules.diagnostics':{phase:'error',error:'Fixture local read failed'}}};
  await act(()=>renderer.update(render()));
  assert.equal(renderer.root.findByProps({role:'alert'}).children.join(''),'Fixture local read failed');
  const refresh=renderer.root.findAllByType('button').find(b=>b.children.includes('Refresh capability diagnostics'));
  assert.equal(refresh.props.disabled,false);
  await act(()=>refresh.props.onClick());
  assert.deepEqual(calls,[['modules.diagnostics',{scope:'global'}],['modules.diagnostics',{scope:'local'}],['modules.diagnostics',{scope:'local'}]]);
  state={registry:{diagnostics:{scope:'local',items:[row]}},actionStatus:{'modules.diagnostics':{phase:'ready'}}};
  await act(()=>renderer.update(render()));
  assert.equal(renderer.root.findAllByProps({role:'alert'}).length,0);
  assert.doesNotMatch(JSON.stringify(renderer.toJSON()),/No retained preferences/);
  const reconcile=()=>renderer.root.findAllByType('button').find(b=>b.children.includes('Use composed policy for new chats'));
  assert.equal(reconcile().props.disabled,true);
  await act(()=>renderer.root.findByType('input').props.onChange({target:{checked:true}}));
  assert.equal(reconcile().props.disabled,false);
  state={...state,actionStatus:{'modules.diagnostics':{phase:'error',error:'Refresh failed'}}};
  await act(()=>renderer.update(render()));
  assert.equal(reconcile(),undefined);
  assert.ok(calls.every(([action])=>action==='modules.diagnostics'));
 }finally{await act(()=>renderer.unmount())}
});

test('notification toggle writes one preference without committing or clearing private connection drafts',async t=>{
 // The permission hook observes browser events; provide that environment while
 // keeping these tests focused on saved preferences and private draft receipts.
 const prior={window:globalThis.window,document:globalThis.document};
 globalThis.window=new EventTarget();globalThis.document=new EventTarget();
 t.after(()=>{for(const [key,value] of Object.entries(prior)){if(value===undefined)delete globalThis[key];else globalThis[key]=value}});
 const calls=[];let renderer;
 let state={view:{maintenanceDraft:{notifyServer:'https://unfinished.example'}},notificationSettings:{desktop:true,preview:false,enabled:false,server:'https://ntfy.sh'}};
 const send=async(action,args)=>{calls.push([action,args]);return {accepted:true,operationId:'toggle'}};
 const render=()=>React.createElement(NotificationSettings,{state,act:send});
 await act(()=>{renderer=create(render())});
 try{
  const inputs=renderer.root.findAllByType('input');
  await act(()=>inputs.find(n=>n.props.id==='notify-token').props.onChange({target:{value:'unsaved-secret'}}));
  await act(()=>inputs.find(n=>n.props.type==='checkbox'&&n.props.checked===true).props.onChange({target:{checked:false}}));
  assert.deepEqual(calls,[['notifications.save',{patch:{desktop:false}}]]);
  assert.equal(renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox')[1].props.checked,false);
  state={...state,actionStatus:{'notifications.save':{commandId:'toggle',phase:'ready'}}};await act(()=>renderer.update(render()));
  assert.equal(renderer.root.findByProps({id:'notify-token'}).props.value,'unsaved-secret');
  await act(()=>renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox')[3].props.onChange({target:{checked:true}}));
  assert.deepEqual(calls.at(-1),['notifications.save',{patch:{preview:true}}]);
  state={...state,actionStatus:{'notifications.save':{commandId:'toggle',phase:'error',error:'Offline'}}};await act(()=>renderer.update(render()));
  assert.equal(renderer.root.findAllByType('input').filter(n=>n.props.type==='checkbox')[3].props.checked,false);
  assert.equal(renderer.root.findByProps({id:'notify-token'}).props.value,'unsaved-secret');
 }finally{await act(()=>renderer.unmount())}
});
