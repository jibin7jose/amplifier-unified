import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DeviceSignIn}=await server.ssrLoadModule('/src/device-sign-in.jsx');
const {ChatGPTSignInChoice,ChatGPTAccount}=await server.ssrLoadModule('/src/chatgpt-sign-in.jsx');
const {safeLoginUrl}=await server.ssrLoadModule('/src/setup-data.js');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
const text=root=>JSON.stringify(root.toJSON());

test('Plan disables the capped message test while Codex keeps it available',async()=>{
 const {ProviderMessageTest}=await server.ssrLoadModule('/src/provider-message-test.jsx');
 let root;const calls=[];
 const props={id:'account',act:async(...args)=>{calls.push(args);return {accepted:true,operationId:'test'}}};
 const state=mode=>({setup:{providers:[{id:'account',module:'provider-openai-chatgpt',config:{auth_mode:mode},account:{authMode:mode}}]}});
 await act(async()=>{root=create(React.createElement(ProviderMessageTest,{...props,state:state('chatgpt_plan')}))});
 assert.equal(root.root.findByType('button').props.disabled,true);
 assert.ok(text(root).includes('model catalog'));
 await act(async()=>root.root.findByType('button').props.onClick());
 assert.equal(calls.length,0);
 for(const mode of ['chatgpt_codex','legacy_codex']){
  await act(async()=>root.update(React.createElement(ProviderMessageTest,{...props,state:state(mode)})));
  assert.equal(root.root.findByType('button').props.disabled,false);
 }
 await act(async()=>root.unmount());
});

test('plan browser sign-in is usable without a device code and does not expose token hints',async()=>{
 let root,cancelled=false;
 await act(async()=>{root=create(React.createElement(DeviceSignIn,{login:{authMode:'chatgpt_plan',status:'waiting',url:'https://auth.openai.com/api/accounts/authorize?state=s&code_challenge=c'},onCancel:()=>{cancelled=true}}))});
 assert.equal(root.root.findByType('a').props.href,'https://auth.openai.com/api/accounts/authorize?state=s&code_challenge=c');
 assert.ok(text(root).includes('Continue with ChatGPT'));
 assert.ok(!text(root).includes('Getting your sign-in code'));
 await act(async()=>root.root.findByType('button').props.onClick());assert.ok(cancelled);
 await act(async()=>root.update(React.createElement(DeviceSignIn,{login:{authMode:'chatgpt_plan',status:'waiting',url:'https://auth.openai.com/?id_token_hint=private'}})));
 assert.equal(root.root.findAllByType('a').length,0);assert.ok(!text(root).includes('private'));
 await act(async()=>root.unmount());
});

test('identity-only sign-in asks for plan permission and never claims plan access',async()=>{
 let root,enabled=false;
 await act(async()=>{root=create(React.createElement(DeviceSignIn,{login:{authMode:'chatgpt_plan',status:'completed',account:{authMode:'chatgpt_plan',connected:true,planEnabled:false}},onEnablePlan:()=>{enabled=true}}))});
 assert.ok(text(root).includes('Plan access needs permission'));
 assert.ok(!text(root).includes('You’re using your ChatGPT plan'));
 await act(async()=>root.root.findByType('button').props.onClick());assert.ok(enabled);
 await act(async()=>root.unmount());
});

test('first plan welcome is dismissible and carries a real usage link',async()=>{
 let root,dismissed=false;
 const props={login:{authMode:'chatgpt_plan',status:'completed',showPlanWelcome:true,account:{authMode:'chatgpt_plan',connected:true,planEnabled:true}},onWelcomeDismiss:()=>{dismissed=true}};
 await act(async()=>{root=create(React.createElement(DeviceSignIn,props))});
 assert.ok(text(root).includes('You’re using your ChatGPT plan'));
 assert.equal(root.root.findByType('a').props.href,'https://chatgpt.com/settings/usage');
 await act(async()=>root.root.findByType('button').props.onClick());assert.ok(dismissed);
 await act(async()=>root.update(React.createElement(DeviceSignIn,{...props,welcomeDismissed:true})));
 assert.ok(!text(root).includes('You’re using your ChatGPT plan'));
 await act(async()=>root.unmount());
});

test('remote-host limitations and explicit Codex choice stay visible',async()=>{
 let root,mode='';
 await act(async()=>{root=create(React.createElement(ChatGPTSignInChoice,{mode:'chatgpt_plan',onChange:value=>{mode=value}}))});
 assert.ok(text(root).includes('transfer the credentials over SSH'));
 assert.equal(root.root.findAllByType('option').length,2);
 await act(async()=>root.root.findByType('select').props.onChange({target:{value:'chatgpt_codex'}}));assert.equal(mode,'chatgpt_codex');
 await act(async()=>root.update(React.createElement(ChatGPTAccount,{account:{authMode:'legacy_codex',connected:true,planEnabled:false}})));
 assert.ok(text(root).includes('ChatGPT Codex with device sign-in'));
 assert.equal(root.root.findAllByType('a').length,0);
 await act(async()=>root.unmount());
});

test('login link filter rejects credentials, fragments, and token-bearing navigation',()=>{
 for(const url of ['https://user:password@auth.openai.com/','https://auth.openai.com/#secret','https://auth.openai.com/?ID_TOKEN_HINT=secret','https://auth.openai.com/?refresh_token=secret','javascript:alert(1)'])assert.equal(safeLoginUrl(url),null);
 assert.equal(safeLoginUrl('https://auth.openai.com/codex/device'),'https://auth.openai.com/codex/device');
});

for(const target of [{kind:'global'},{kind:'workspace',workspaceId:'other'}])test(`everyday reconnect uses its ${target.kind} owner without saving the draft`,async()=>{
 const {AIConnections}=await server.ssrLoadModule('/src/ai-connections.jsx');
 const {ConfigurationBoundary}=await server.ssrLoadModule('/src/configuration-scope.jsx');
 let root;const calls=[];
 const editor={step:'connect',id:'account',module:'provider-openai-chatgpt',authMode:'chatgpt_plan',scope:'local',saved:true};
 const state={view:{aiConnectionEditor:editor,configurationDrafts:{'workspace:other':{aiConnectionEditor:editor}}},setup:{configurationTarget:target,providers:[{id:'account',module:'provider-openai-chatgpt',config:{token_file_path:'/private/kept.json'}}]}};
 const dispatch=async(name,args)=>{calls.push({name,args});return {accepted:true,operationId:'request'}};
 await act(async()=>{root=create(React.createElement(ConfigurationBoundary,{state,target,act:dispatch},props=>React.createElement(AIConnections,{...props,navigate:()=>{}})))});
 await act(async()=>root.root.findAllByType('button').find(button=>button.props['data-action']==='providers.login').props.onClick());
 assert.deepEqual(calls.find(call=>call.name==='providers.login').args,{id:'account',authMode:'chatgpt_plan',scope:target.kind==='global'?'global':'local',configurationTarget:target});
 assert.ok(!calls.some(call=>call.name==='providers.save'));
 await act(async()=>root.unmount());
});

test('identity-only everyday login does not fetch models or advance beyond consent',async()=>{
 const {AIConnections}=await server.ssrLoadModule('/src/ai-connections.jsx');
 let root;const calls=[];
 const state={view:{aiConnectionEditor:{step:'connect',id:'account',module:'provider-openai-chatgpt',authMode:'chatgpt_plan',saved:true}},setup:{providers:[{id:'account',module:'provider-openai-chatgpt',config:{auth_mode:'chatgpt_plan'}}],login:{providerId:'account',loginId:'fresh',authMode:'chatgpt_plan',status:'completed',account:{authMode:'chatgpt_plan',connected:true,planEnabled:false}}}};
 await act(async()=>{root=create(React.createElement(AIConnections,{state,session:{id:'chat',workspace:'/work'},navigate:()=>{},act:async(name,args)=>{calls.push({name,args});return {accepted:true,operationId:'request'}}}))});
 assert.ok(text(root).includes('Enable ChatGPT plan access'));
 assert.ok(!calls.some(call=>call.name==='providers.models'));
 await act(async()=>root.unmount());
});

test('new everyday ChatGPT setup defaults to Codex and retains the explicit plan choice',async()=>{
 const {AIConnections}=await server.ssrLoadModule('/src/ai-connections.jsx');
 let root;const calls=[];
 const state={view:{aiConnectionEditor:{step:'services'}},setup:{providers:[]}};
 const draft={step:'connect',id:'chatgpt-new',module:'provider-openai-chatgpt',authMode:'chatgpt_codex',scope:'global'};
 const dispatch=async(name,args)=>{
  calls.push({name,args});
  return {accepted:true,operationId:'request',result:{editor:{id:draft.id,module:draft.module}}};
 };
 await act(async()=>{root=create(React.createElement(AIConnections,{state,navigate:()=>{},act:dispatch}))});
 const button=root.root.findAllByType('button').find(button=>button.findAllByType('strong').some(label=>label.children.join('').includes('ChatGPT')));
 await act(async()=>button.props.onClick());
 assert.deepEqual(calls.find(call=>call.name==='settings.connection.select').args,{scope:'global',module:'provider-openai-chatgpt'});
 assert.equal(draft.authMode,'chatgpt_codex');
 await act(async()=>root.update(React.createElement(AIConnections,{state:{...state,view:{aiConnectionEditor:draft}},navigate:()=>{},act:async()=>({accepted:true,operationId:'request'})})));
 const select=root.root.findAllByType('select').find(select=>select.props.value==='chatgpt_codex');
 assert.ok(select);
 assert.deepEqual(select.findAllByType('option').map(option=>option.props.value),['chatgpt_codex','chatgpt_plan']);
 await act(async()=>root.unmount());
});

for(const oldMode of [undefined,'legacy_codex','chatgpt_codex'])test(`Codex reconnect keeps the account and normalizes ${oldMode}`,async()=>{
 const {AIConnections}=await server.ssrLoadModule('/src/ai-connections.jsx');
 let root;const calls=[];
 const state={view:{aiConnectionEditor:{step:'connect',id:'account',module:'provider-openai-chatgpt',authMode:oldMode,saved:true}},setup:{providers:[{id:'account',module:'provider-openai-chatgpt',config:{auth_mode:oldMode,token_file_path:'/private/kept.json'}}]}};
 await act(async()=>{root=create(React.createElement(AIConnections,{state,navigate:()=>{},act:async(name,args)=>{calls.push({name,args});return {accepted:true,operationId:'request'}}}))});
 await act(async()=>root.root.findAllByType('button').find(button=>button.props['data-action']==='providers.login').props.onClick());
 assert.equal(calls.find(call=>call.name==='providers.login').args.authMode,'chatgpt_codex');
 assert.ok(!calls.some(call=>call.name==='providers.save'));
 await act(async()=>root.unmount());
});

test('advanced provider metadata cannot create a second conflicting ChatGPT mode control',async()=>{
 const {ProviderSettings}=await server.ssrLoadModule('/src/setup.jsx');
 let root;const calls=[];
 const provider={id:'account',module:'provider-openai-chatgpt',config:{auth_mode:'chatgpt_codex',token_file_path:'/private/kept.json'}};
 const state={view:{providerEditor:{id:'account',selectionKey:'account',module:provider.module,config:JSON.stringify(provider.config),detailOpen:true}},setup:{providers:[provider],metadata:{[provider.module]:{info:{config_fields:[{id:'auth_mode',display_name:'ChatGPT connection',field_type:'choice',choices:['chatgpt_codex','chatgpt_plan'],default:'chatgpt_codex'},{id:'app_name',display_name:'Application name',field_type:'text'}]}}}}};
 await act(async()=>{root=create(React.createElement(ProviderSettings,{state,navigate:()=>{},act:async(name,args)=>{calls.push({name,args});return {accepted:true,operationId:'request'}}}))});
 const modeControls=()=>root.root.findAllByType('select').filter(select=>select.findAllByType('option').some(option=>option.props.value==='chatgpt_plan'));
 assert.equal(modeControls().length,1);
 await act(async()=>modeControls()[0].props.onChange({target:{value:'chatgpt_plan'}}));
 const loginButton=root.root.findAllByType('button').find(button=>button.props['data-action']==='providers.login');
 await act(async()=>loginButton.props.onClick());
 assert.equal(calls.find(call=>call.name==='providers.login').args.authMode,'chatgpt_plan');
 assert.ok(!calls.some(call=>call.name==='providers.save'));
 assert.equal(state.setup.providers[0].config.auth_mode,'chatgpt_codex');
 await act(async()=>root.unmount());
});
