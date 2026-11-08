import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ModelControl,readAttachment,useNextMessageConfiguration}=await server.ssrLoadModule('/src/chat-controls.jsx');
const {ComposerOptions}=await server.ssrLoadModule('/src/composer-options.jsx');
const record=calls=>async(name,args)=>{calls.push({name,args});return {accepted:true,...(name==='conversation.configure'?{result:{revision:args.expectedRevision+1}}:{})}};
// Match the actual form control, not ModelSelect's component props. A user
// change must exercise both onChange and onCommit on the rendered element.
const field=(root,id)=>root.root.find(node=>typeof node.type==='string'&&node.props.id===id);
const optionValues=(root,id)=>field(root,id).findAllByType('option').map(node=>node.props.value);
const renderedText=node=>typeof node==='string'?node:Array.isArray(node)?node.map(renderedText).join(''):renderedText(node?.children||[]);
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>server.close());
test('one compact model panel embeds bundle choices under Advanced and shares its revision chain',async()=>{
 const calls=[],session={id:'chat',status:'idle',bundle:'work',nextMessageRevision:4};
 const selection={instance:'one',model:'model',effort:'high'};
 const state={view:{composerModel:{open:true,sessionId:'chat',...selection}},
  modelCatalogs:{'account-one':{phase:'ready',models:['model','other']}},
  registeredBundles:[{value:'work',label:'Work'},{value:'research',label:'Research'}],
  runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,nextMessageRevision:4,
   providers:[{id:'one',displayName:'Personal',sharedCatalogKey:'account-one',info:{defaults:{model:'model'},config_fields:[{id:'reasoning_effort',choices:['low','high']}]}}],
   effective:selection}}}};
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ComposerOptions,{state,session,act:record(calls)})));
  const dialog=root.root.findByProps({role:'dialog','aria-label':'Model for this chat'});
  assert.equal(root.root.findAllByProps({role:'dialog'}).length,1);
  for(const name of ['model','provider','effort'])assert.notEqual(dialog.findByProps({'data-composer-page':name}).props.hidden,true);
  assert.equal(dialog.findAllByProps({'data-composer-page':'overview'}).length,0);
  assert.equal(dialog.findAllByProps({'aria-label':'Back to chat options'}).length,0);
  const advanced=dialog.findByType('details');
  assert.deepEqual(advanced.findByType('summary').children,['Advanced']);
  assert.notEqual(advanced.props.open,true);
  assert.equal(advanced.findByProps({id:'composer-options-bundle'}).props.value,'work');
  assert.equal(root.root.findAllByProps({'aria-label':'Conversation bundle'}).length,0,'Bundle has no second trigger/dialog');
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false,'Opening the panel does not save choices');
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'other'}}));
  await renderAct(async()=>field(root,'composer-options-bundle').props.onChange({target:{value:'research'}}));
  assert.equal(root.root.findByProps({role:'dialog','aria-label':'Model for this chat'}),dialog);
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args.expectedRevision),[4,5]);
  assert.equal(calls.some(c=>['conversation.send','session.create','bundle.switch'].includes(c.name)),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
for(const commit of ['onPointerUp','onKeyUp','onBlur'])test('reasoning effort survives a background view refresh until '+commit,async()=>{
 const calls=[],session={id:'chat',status:'idle'},selection={instance:'one',model:'model',effort:'high'};
 let state={view:{composerModel:{open:true,sessionId:'chat',...selection}},modelCatalogs:{'account-one':{phase:'ready',models:['model'],metadata:{configSchema:{fields:[{id:'reasoning_effort',choices:['low','medium','high']}]}}}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[{id:'one',sharedCatalogKey:'account-one',info:{defaults:{model:'model'}}}],effective:selection}}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act:record(calls),working:false});
 try{
  await renderAct(async()=>root=create(render()));
  const slider=()=>root.root.findByProps({id:'chat-effort'});
  await renderAct(async()=>slider().props.onChange({currentTarget:{value:'1'}}));
  // Streaming/view polling can replace an equal server object before release.
  state={...state,view:{...state.view,composerModel:{...state.view.composerModel}}};
  await renderAct(async()=>root.update(render()));
  assert.equal(slider().props['aria-valuetext'],'medium');
  await renderAct(async()=>slider().props[commit]({currentTarget:{value:String(slider().props.value)}}));
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args),[{sessionId:'chat',expectedRevision:0,selection:{...selection,effort:'medium'}}]);
  await renderAct(async()=>slider().props.onBlur({currentTarget:{value:'1'}}));
  assert.equal(calls.filter(c=>c.name==='conversation.configure').length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('attachment intake accepts 32 MiB and rejects oversized files before reading',async()=>{
 const previous=globalThis.FileReader;let reads=0;
 globalThis.FileReader=class{readAsDataURL(){reads++;this.result='data:image/png;base64,fixture';this.onload()}};
 try{
  for(const size of [8*1024*1024+1,22*1024*1024,32*1024*1024])assert.equal(await readAttachment({size,name:'camera.png'}),'fixture');
  for(const size of [0,32*1024*1024+1])await assert.rejects(readAttachment({size,name:'bad.png'}),/32 MB/);
  assert.equal(reads,3);
 }finally{globalThis.FileReader=previous}
});
test('provider configuration changes refresh draft choices without retaining stale defaults',async()=>{
 const calls=[];let state={configurationRevision:0,view:{newSessionDraft:{workspace:'/trial',selection:{}}},setup:{providersRequestedWorkspace:'/trial'},draftDefaults:{'["/trial",""]':{phase:'ready',effective:{instance:'old',model:'old-model'},providers:[{id:'old',info:{defaults:{model:'old-model'}}}]}}},root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const render=()=>React.createElement(ModelControl,{state,act,working:false});
 await renderAct(async()=>{root=create(render());});
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/old-model/);
 state={...state,configurationRevision:1,draftDefaults:{},setup:{}};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/Loading model/);
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.equal(calls.filter(row=>row.name==='configuration.defaults').length,1);
 assert.equal(calls.filter(row=>row.name==='providers.list').length,1);
 // An explicit draft choice survives discovery and a preference change.
 state={...state,view:{newSessionDraft:{workspace:'/trial',selection:{instance:'chosen',model:'chosen-model'}}}};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/chosen-model/);
 await renderAct(async()=>root.unmount());
});
test('a new workspace stays idle until its model controls are explicitly opened',async()=>{
 const calls=[],session={id:'new-workspace-chat',status:'idle',deferRuntimeUntilInteraction:true};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}},state={view:{}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false}))});
 assert.ok(!calls.some(call=>call.name==='runtime.control'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='runtime.control'&&call.args.operation==='configuration.catalog'));
 await renderAct(async()=>root.unmount());
});
test('late catalogs preserve selector choices and shared provider actions',async()=>{
 const calls=[],act=record(calls),session={id:'chat',status:'idle'};
 let state={view:{composerModel:{open:true,sessionId:'chat',instance:'openai',model:'first'}},runtimeControl:{chat:{}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act,ensureSession:async()=>session,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({id:'chat-model'}).filter(n=>typeof n.type==='string').length,1);
 state={...state,modelCatalogs:{'account-openai':{phase:'ready',models:[{id:'first'},{id:'second'}]}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[{id:'openai',sharedCatalogKey:'account-openai',info:{defaults:{model:'first'}}}],effective:{instance:'openai',model:'first'}}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'second'}}));
 assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args),[{sessionId:'chat',expectedRevision:0,selection:{instance:'openai',model:'second'}}]);
 state={...state,modelCatalogs:{'account-openai':{phase:'ready',models:['first']}}};
 await renderAct(async()=>root.update(render()));
 assert.equal(picker().type,'select');
 assert.equal(picker().props.value,'');
 assert.deepEqual(optionValues(root,'chat-model'),['','first']);
 assert.match(renderedText(root.toJSON()),/Saved selection: .*second.*not in the discovered list/);
 state={...state,modelCatalogs:{'account-openai':{phase:'ready',models:[]}}};
 await renderAct(async()=>root.update(render()));
 assert.equal(picker().type,'input');
 assert.equal(picker().props.value,'second','An empty catalog does not overwrite the saved choice');
 // An agent uses this same action; its returned catalog must update the trigger.
 state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,'configuration.catalog':{...state.runtimeControl.chat['configuration.catalog'],pinned:true,effective:{instance:'openai',model:'agent-choice'}}}}};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/openai · agent-choice/);
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('openai · agent-choice')));
 await renderAct(async()=>root.unmount());
});

for(const isDraft of [false,true])test('authoritative empty refresh replaces cached options, not the saved selection: '+(isDraft?'draft':'chat'),async()=>{
 const calls=[],providers=[{id:'one',sharedCatalogKey:'account-one',info:{defaults:{model:'default-only'}}}];
 let entry={phase:'ready',models:['saved','retired']},root;
 const render=()=>{
  const state={configurationRevision:1,view:{composerModel:{open:true,sessionId:isDraft?null:'chat',instance:'one',model:'saved'},newSessionDraft:{workspace:'/trial',selection:{instance:'one',model:'saved'}}},
   modelCatalogs:{'account-one':entry},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:1,providers,effective:{instance:'one',model:'saved'}}}},
   setup:{providersRequestedWorkspace:'/trial',providers,providerCatalogs:{one:{sharedCatalogKey:'account-one'}}},
   draftDefaults:{'["/trial",""]':{phase:'ready',providers,effective:{instance:'one',model:'saved'}}}};
  return React.createElement(ModelControl,{state,session:isDraft?null:{id:'chat'},act:async(name,args)=>calls.push({name,args})});
 };
 const values=()=>root.root.findByProps({'data-activity-region':'models'}).findAllByType('option').map(n=>n.props.value).filter(Boolean);
 try{
  await renderAct(async()=>root=create(render()));assert.deepEqual(values(),['saved','retired']);
  for(const transient of [{phase:'working'},{phase:'error'},{phase:'ready',modelsDeferred:true}]){
   // Retention belongs to the host's shared entry, never a client-side union.
   entry={...transient,models:['saved','retired']};await renderAct(async()=>root.update(render()));
   assert.deepEqual(values(),['saved','retired']);
  }
  const refresh=root.root.findAllByType('button').find(n=>n.children.includes('Refresh this connection'));
  await renderAct(async()=>refresh.props.onClick());
  assert.deepEqual(calls.at(-1),isDraft
   ?{name:'providers.models',args:{id:'one',workspace:'/trial',refresh:true}}
   :{name:'runtime.control',args:{sessionId:'chat',operation:'configuration.catalog',args:{refresh:true,instance:'one'}}});
  entry={phase:'ready',models:[]};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),[]);assert.equal(field(root,'chat-model').type,'input');assert.equal(field(root,'chat-model').props.value,'saved');
  assert.match(renderedText(root.toJSON()),/Saved selection: .*saved.*not in the discovered list/);
  entry={phase:'ready',modelsDeferred:true};await renderAct(async()=>root.update(render()));assert.deepEqual(values(),[]);
  entry={phase:'ready',models:['fresh']};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),['fresh']);assert.equal(field(root,'chat-model').props.value,'');
  assert.equal(values().includes('default-only'),false,'Defaults are not discovery');
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
  assert.equal(calls.some(c=>c.name==='view.update'&&c.args?.patch?.newSessionDraft),false);
 }finally{await renderAct(async()=>root?.unmount())}
});


test('draft model controls discover without creating a session and save choices through shared view actions',async()=>{
 const calls=[];let state={settings:{workspace:'/future'},view:{newSessionDraft:{workspace:'/future',bundle:'work',selection:{}}}},root;
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}};return {accepted:true}};
 const render=()=>React.createElement(ModelControl,{state,session:null,act,ensureSession:async()=>{throw Error('Must not create a chat')},working:false});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.deepEqual(calls.find(c=>c.name==='providers.list').args,{workspace:'/future'});
 state={...state,draftDefaults:{'["/future","work"]':{phase:'ready',effective:{instance:'one',model:'first'}}},modelCatalogs:{'account-one':{phase:'ready',models:['first','chosen']}},setup:{providersRequestedWorkspace:'/future',providers:[{id:'one',module:'provider-test',displayName:'Test connection',sharedCatalogKey:'account-one',config:{model:'first'}}],providerCatalogs:{one:{sharedCatalogKey:'account-one'}}}};
 await renderAct(async()=>root.update(render()));
 const picker=()=>root.root.findAllByProps({id:'chat-model'}).find(n=>typeof n.type==='string');
 await renderAct(async()=>picker().props.onChange({target:{value:'chosen'}}));
 assert.deepEqual(state.view.newSessionDraft.selection,{instance:'one',model:'chosen'});
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findAllByType('span').some(n=>n.children.includes('Test connection · chosen')));
 assert.equal(root.root.findByProps({id:'chat-provider'}).findAllByType('option').some(n=>n.children.includes('Use bundle default')),false);
 assert.equal(calls.some(c=>['session.create','runtime.control'].includes(c.name)),false);
 assert.equal(state.view.newSessionDraft.bundle,'work');await renderAct(async()=>root.unmount());
});

test('inherited choices are displayed without pinning and the popup opens before requests finish',async()=>{
 const calls=[];let state={settings:{workspace:'/new'},workspaces:[{id:'new',path:'/new'}],selectedWorkspaceId:'new',view:{},setup:{providersRequestedWorkspace:'/new'},draftDefaults:{'["/new",""]':{phase:'ready',bundle:'work',effective:{instance:'one',model:'actual-model',effort:'high'},providers:[{id:'one',info:{defaults:{model:'actual-model'}}}]}}};
 const act=(name,args)=>{calls.push({name,args});return new Promise(()=>{})};let root;
 await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,act,working:false}))});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('one · actual-model (high)'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.equal(root.root.findAllByType('section').length,1);
 assert.equal(calls.some(c=>c.name==='session.create'||c.args?.patch?.newSessionDraft),false);
 assert.equal(field(root,'chat-model').type,'input');
 assert.equal(field(root,'chat-model').props.value,'actual-model');
 assert.equal(field(root,'chat-model').props.disabled,true,'Defaults do not invent a configured connection');
 assert.deepEqual(optionValues(root,'chat-provider'),['']);
 state={...state,view:{newSessionDraft:{workspace:'/new',bundle:'another',selection:{}}}};
 await renderAct(async()=>root.update(React.createElement(ModelControl,{state,act,working:false})));
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('Loading model…'));
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.equal(calls.some(c=>c.name==='providers.list'),false);
 await renderAct(async()=>root.unmount());
});

test('same-type connections stay separate and changing models never changes the connection',async()=>{
 const calls=[],providers=[{id:'opus',module:'provider-anthropic',displayName:'Anthropic',sharedCatalogKey:'account-opus',info:{defaults:{model:'opus-model'}}},{id:'fable',module:'provider-anthropic',displayName:'Anthropic',sharedCatalogKey:'account-fable',info:{defaults:{model:'fable-model'}}},{id:'openai',module:'provider-openai',displayName:'OpenAI',sharedCatalogKey:'account-openai',info:{defaults:{model:'openai-model'}}}];
 const state={view:{composerModel:{open:true,sessionId:'chat',instance:'fable',model:'fable-model'}},modelCatalogs:{'account-fable':{phase:'ready',models:['fable-model','opus-model']},'account-opus':{phase:'ready',models:['opus-only']},'account-openai':{phase:'ready',models:[]}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers,effective:{instance:'fable',model:'fable-model'}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:record(calls),working:false}))});
 const choices=root.root.findByProps({id:'chat-provider'}).findAllByType('option');
 assert.deepEqual(choices.map(n=>[n.props.value,n.children.join('')]),[['','Choose a connection'],['opus','Anthropic (opus)'],['fable','Anthropic (fable)'],['openai','OpenAI']]);
 assert.equal(root.root.findByProps({id:'chat-provider'}).props.value,'fable');
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/Anthropic \(fable\)/);
 assert.deepEqual(optionValues(root,'chat-model'),['','fable-model','opus-model']);
 await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'opus-model'}}));
 assert.deepEqual(calls.find(c=>c.name==='conversation.configure').args,{sessionId:'chat',expectedRevision:0,selection:{instance:'fable',model:'opus-model'}});
 await renderAct(async()=>root.root.findByProps({id:'chat-provider'}).props.onChange({target:{value:'opus'}}));
 assert.equal(calls.filter(c=>c.name==='conversation.configure').length,1,'Changing a connection does not copy the old model or pin its default');
 assert.equal(field(root,'chat-model').props.value,'');
 assert.deepEqual(optionValues(root,'chat-model'),['','opus-only']);
 await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'opus-only'}}));
 assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').at(-1).args,{sessionId:'chat',expectedRevision:1,selection:{instance:'opus',model:'opus-only'}});
 await renderAct(async()=>root.root.findAllByType('button').find(n=>n.children.includes('Refresh this connection')).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'runtime.control',args:{sessionId:'chat',operation:'configuration.catalog',args:{refresh:true,instance:'opus'}}});
 await renderAct(async()=>root.unmount());
});

test('deleted connection remains unavailable until an explicit replacement is selected',async()=>{
 const calls=[],wanted={instance:'removed',model:'saved-model',effort:'high'},providers=[{id:'remaining',module:'provider-anthropic',displayName:'Anthropic',sharedCatalogKey:'remaining-account',info:{defaults:{model:'different-model'}}}];
 const state={view:{composerModel:{open:true,sessionId:'chat'}},modelCatalogs:{'remaining-account':{phase:'ready',models:['replacement-model']}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers,effective:wanted,selection:wanted,selectionIssue:'Choose a replacement.'}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:record(calls)}))});
 assert.equal(field(root,'chat-provider').props.value,'');
 assert.equal(field(root,'chat-model').props.value,'saved-model');
 assert.equal(field(root,'chat-model').props.disabled,true);
 assert.deepEqual(optionValues(root,'chat-provider'),['','remaining']);
 assert.match(JSON.stringify(root.toJSON()),/Saved connection .*removed.*not available/);
 assert.match(JSON.stringify(root.toJSON()),/Choose a replacement/);
 assert.equal(root.root.findAllByType('button').find(n=>n.children.includes('Refresh this connection')).props.disabled,true);
 assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 await renderAct(async()=>root.root.findByProps({id:'chat-provider'}).props.onChange({target:{value:'remaining'}}));
 assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 assert.equal(field(root,'chat-model').props.value,'');
 assert.deepEqual(optionValues(root,'chat-model'),['','replacement-model']);
 await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'replacement-model'}}));
 assert.deepEqual(calls.find(c=>c.name==='conversation.configure').args,{sessionId:'chat',expectedRevision:0,selection:{instance:'remaining',model:'replacement-model'}});
 assert.equal(calls.some(c=>c.name==='conversation.send'),false);
 await renderAct(async()=>root.unmount());
});

test('catalog order does not select a connection when no effective connection is known',async()=>{
 const calls=[],state={view:{composerModel:{open:true,sessionId:'chat'}},modelCatalogs:{'account-first':{phase:'ready',models:['first-model']}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[{id:'first',sharedCatalogKey:'account-first',info:{defaults:{model:'first-model'}}}],effective:{}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async(name,args)=>calls.push({name,args})}))});
 assert.equal(root.root.findByProps({id:'chat-provider'}).props.value,'');
 assert.deepEqual(optionValues(root,'chat-provider'),['','first']);
 assert.equal(field(root,'chat-model').props.disabled,true);
 assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 await renderAct(async()=>root.unmount());
});

test('only the current configured roster and exact shared catalog key supply choices',async()=>{
 const calls=[],selection={instance:'configured',model:'saved-only'},session={id:'chat',selection,runtimeReport:{provider_choices:[{id:'mounted',display_name:'Mounted only',model:'mounted-model'}],effective_selection:{instance:'mounted',model:'mounted-model'}}};
 const providers=[{id:'configured',displayName:'Configured connection',module:'provider-test',sharedCatalogKey:'account-v1',info:{defaults:{model:'default-only'}}}];
 let state={configurationRevision:2,view:{composerModel:{open:true,sessionId:'chat',...selection}},
  setup:{providers:[{id:'setup-only'}]},modelCatalogs:{'account-v1':{phase:'ready',models:['real-model']},configured:{phase:'ready',models:['wrong-key-model']}},
  runtimeControl:{chat:{'configuration.catalog':{configurationRevision:2,providers,effective:selection},'configuration.providers':{providers:[{id:'legacy-only'}]},modelCatalogs:{configured:{phase:'ready',models:['runtime-only']}}}}},root;
 const render=()=>React.createElement(ModelControl,{state,session,act:record(calls)});
 try{
  await renderAct(async()=>root=create(render()));
  assert.deepEqual(optionValues(root,'chat-provider'),['','configured']);
  assert.deepEqual(optionValues(root,'chat-model'),['','real-model']);
  assert.equal(field(root,'chat-model').props.value,'');
  assert.match(renderedText(root.toJSON()),/Saved selection: .*saved-only.*not in the discovered list/);
  state={...state,configurationRevision:3};
  await renderAct(async()=>root.update(render()));
  assert.deepEqual(optionValues(root,'chat-provider'),[''],'A previous configuration revision is not a roster fallback');
  assert.deepEqual(field(root,'chat-provider').findByType('option').children,['Loading connections…'],'A stale roster is unresolved, not an authoritative empty configuration');
  assert.equal(field(root,'chat-provider').props.value,'');
  assert.equal(field(root,'chat-model').props.disabled,true);
  assert.equal(field(root,'chat-model').props.value,'saved-only');
  assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,1);
  state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,'configuration.catalog':{configurationRevision:3,providers:[{...providers[0],sharedCatalogKey:'account-v2'}],effective:selection}}}};
  await renderAct(async()=>root.update(render()));
  assert.equal(field(root,'chat-model').type,'input');
  assert.equal(root.root.findAllByType('option').some(n=>['real-model','wrong-key-model','runtime-only'].includes(n.props.value)),false,'A missing exact key cannot borrow another account or mounted catalog');
  const reads=calls.filter(c=>c.args?.operation==='configuration.catalog').length;
  await renderAct(async()=>root.update(render()));
  assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,reads,'Unchanged missing entry does not re-request on every render');
  state={...state,modelCatalogs:{...state.modelCatalogs,'account-v2':{phase:'ready',models:['new-account-model']}}};
  await renderAct(async()=>root.update(render()));
  assert.deepEqual(optionValues(root,'chat-model'),['','new-account-model']);
  assert.equal(field(root,'chat-model').props.value,'');
  assert.equal(calls.some(c=>['conversation.configure','conversation.send','session.create'].includes(c.name)),false);
 }finally{await renderAct(async()=>root?.unmount())}
});

for(const [name,entry] of [
 ['unavailable',undefined],
 ['unsupported',{phase:'ready',supported:false,models:[]}],
 ['empty',{phase:'ready',models:[]}],
 ['failed',{phase:'error',models:[],error:'Discovery failed'}]
])test('a '+name+' catalog accepts an explicit manual ID without sending or inventing choices',async()=>{
 const calls=[],selection={instance:'one',model:'saved-only'},session={id:'chat',nextMessageRevision:7,selection};
 const state={view:{composerModel:{open:true,sessionId:'chat',...selection}},modelCatalogs:entry?{'account-one':entry}:{},
  runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,nextMessageRevision:7,providers:[{id:'one',sharedCatalogKey:'account-one',info:{defaults:{model:'default-only'}}}],effective:selection}}}};
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,act:record(calls)})));
  assert.equal(field(root,'chat-model').type,'input');
  assert.equal(field(root,'chat-model').props.disabled,false);
  assert.equal(field(root,'chat-model').props.value,'saved-only');
  assert.deepEqual(optionValues(root,'chat-provider'),['','one']);
  assert.equal(root.root.findByProps({'data-activity-region':'models'}).findAllByType('option').length,0);
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:' deployment-id '}}));
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
  await renderAct(async()=>field(root,'chat-model').props.onBlur({target:{value:' deployment-id '}}));
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args),[{sessionId:'chat',expectedRevision:7,selection:{instance:'one',model:'deployment-id'}}]);
  assert.equal(calls.some(c=>['conversation.send','session.create','bundle.switch'].includes(c.name)),false);
 }finally{await renderAct(async()=>root?.unmount())}
});


test('managed drafts do not retain a prior location default while discovery loads',async()=>{
 const calls=[];let state={settings:{workspace:'/prior'},view:{newSessionDraft:{workspace:'/prior',bundle:'',selection:{}}},draftDefaults:{'["/prior",""]':{phase:'ready',bundle:'work',effective:{instance:'private',model:'workspace-model'},providers:[{id:'private',info:{defaults:{model:'workspace-model'}}}]}}};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 const render=()=>React.createElement(ModelControl,{state,act,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('private · workspace-model'));
 state={...state,view:{newSessionDraft:{workspace:'',location:{kind:'managed'},bundle:'',selection:{}}}};
 await renderAct(async()=>root.update(render()));
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('Loading model…'));
 await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
 assert.deepEqual(calls.find(row=>row.name==='configuration.defaults').args,{workspace:'',bundle:'',location:{kind:'managed'}});
 assert.deepEqual(calls.find(row=>row.name==='providers.list').args,{workspace:'',location:{kind:'managed'}});
 assert.equal(calls.some(row=>row.name==='session.create'),false);
 await renderAct(async()=>root.unmount());
});

for(const [providerId,label] of [['openai','OpenAI'],['copilot-sdk','GitHub Copilot SDK'],['openai-chatgpt','OpenAI ChatGPT']]){
 test('closed selector identifies '+label+' even for an identical model',async()=>{
  const state={view:{},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[{id:'alias',module:providerId,displayName:label,info:{display_name:'Not the configured label'}}],effective:{instance:'alias',model:'same-model',effort:'high'}}}}};
  let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session:{id:'chat'},act:async()=>({accepted:true}),working:false}))});
  assert.deepEqual(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children,[label+' · same-model (high)']);
  await renderAct(async()=>root.unmount());
 });
}
test('runtime report displays the saved selection but never supplies the configured roster',async()=>{
 const session={id:'chat',runtimeReport:{provider_choices:[{id:'routing-alias',provider:'copilot-sdk',display_name:'GitHub Copilot SDK',model:'same-model',effort:'high'}],effective_selection:{instance:'routing-alias',model:'same-model',effort:'high'}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state:{view:{}},session,act:async()=>({accepted:true}),working:false}))});
 assert.deepEqual(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children,['routing-alias · same-model (high)']);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.deepEqual(optionValues(root,'chat-provider'),['']);
 assert.equal(field(root,'chat-provider').props.value,'');
 assert.equal(field(root,'chat-model').props.value,'same-model');
 assert.equal(field(root,'chat-model').props.disabled,true);
 await renderAct(async()=>root.unmount());
});
test('closed conversation and optimistic navigation never browse catalogs',async()=>{
 const calls=[],session={id:'chat',selection:{instance:'private',model:'saved-model'}},act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let root,state={view:{},navigationPending:true};
 await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,act})));
 assert.equal(calls.length,0);
 assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.join(''),/saved-model/);
 state={view:{},configurationRevision:2};await renderAct(async()=>root.update(React.createElement(ModelControl,{state,session,act})));
 assert.equal(calls.length,0);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.onClick());
 assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,1);
 await renderAct(async()=>root.unmount());
});
for(const shared of [{},{open:false}])test('embedded picker retains server demand after initialization: '+JSON.stringify(shared),async()=>{
 const calls=[],state={view:{composerModel:shared},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[],effective:{}}}}};
 let root;
 await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session:{id:'chat'},embedded:true,act:async(name,args)=>calls.push({name,args})})));
 const updates=calls.filter(c=>c.name==='view.update');
 assert.ok(updates.length);assert.ok(updates.every(c=>c.args.patch.composerModel.open&&c.args.patch.composerModel.sessionId==='chat'));
 await renderAct(async()=>root.unmount());
 assert.equal(calls.at(-1).args.patch.composerModel.open,false);
});
test('an empty resolved provider catalog offers setup instead of loading forever',async()=>{
 const calls=[],state={view:{newSessionDraft:{workspace:'/trial'},composerModel:{open:true,sessionId:null}},draftDefaults:{'["/trial",""]':{phase:'ready',providers:[],effective:{}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,act:async(...args)=>calls.push(args)}))});
 assert.ok(root.root.findByProps({'aria-label':'Model and reasoning settings'}).findByType('span').children.includes('Set up a model'));
 const setup=root.root.findAllByType('button').find(row=>row.children.includes('Manage connections'));
 await renderAct(async()=>setup.props.onClick());
 assert.equal(calls.at(-1)[1].patch.panel,'settings');
 assert.deepEqual(calls.at(-1)[1].patch.settingsExpanded,['ai-connections']);
 assert.equal(calls.at(-1)[1].patch.aiConnectionEditor.step,'list');
 await renderAct(async()=>root.unmount());
});

test('model picker discloses inherited routing from the shared runtime catalog',async()=>{
 const session={id:'routing-chat',status:'idle'},state={view:{composerModel:{open:true,sessionId:'routing-chat'}},runtimeControl:{'routing-chat':{'configuration.catalog':{configurationRevision:0,providers:[],delegationRouting:{modelInheritance:'bundle',resolverActive:true,resolverName:'personal',matrixSource:'user',crossProviderRestriction:'not_enforced'}}}}};
 let root;await renderAct(async()=>{root=create(React.createElement(ModelControl,{state,session,act:async()=>{},working:false}))});
 const text=root.root.findByType('details').findByProps({'data-part':'delegation-routing'}).children.join('');
 assert.match(text,/bundle and agent model settings/);assert.match(text,/personal/);assert.match(text,/user settings/);assert.match(text,/Other connected providers may be used/);
 await renderAct(async()=>root.unmount());
});

test('a configured model picker manages connections without changing this conversation',async()=>{
 const calls=[],session={id:'chat',status:'idle'},state={view:{composerModel:{open:true,sessionId:'chat'}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers:[{id:'one',info:{defaults:{model:'first'}}}],effective:{instance:'one',model:'first'}}}}};
 let root;await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,act:async(...args)=>calls.push(args)})));
 const button=root.root.findAllByType('button').find(row=>row.children.includes('Manage connections'));assert.ok(button);
 calls.length=0;await renderAct(async()=>button.props.onClick());
 assert.equal(calls.length,1);assert.equal(calls[0][0],'view.update');
 assert.equal(calls[0][1].patch.panel,'settings');assert.equal(calls[0][1].patch.composerModel.open,false);
 assert.equal(calls[0][1].patch.aiConnectionEditor.step,'list');
 assert.deepEqual(calls[0][1].patch.settingsExpanded,['ai-connections']);
 await renderAct(async()=>root.unmount());
});

for(const lost of ['evicted','restarted'])test('draft discovery recovers '+lost+' results without remount or a message send',async()=>{
 const calls=[];let root,state={hostInstanceId:'first',view:{newSessionDraft:{workspace:'/trial'}},setup:{providersRequestedWorkspace:'/trial'},draftDefaults:{}};
 const render=()=>React.createElement(ModelControl,{state,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.equal(calls.filter(c=>c.name==='configuration.defaults').length,1);
  if(lost==='evicted'){
   state={...state,draftDefaults:{'["/trial",""]':{phase:'ready',effective:{model:'chosen'},providers:[]}}};
   await renderAct(async()=>root.update(render()));
   state={...state,draftDefaults:{}};
  }else state={...state,hostInstanceId:'restarted'};
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.equal(calls.filter(c=>c.name==='configuration.defaults').length,2);
  assert.ok(calls.every(c=>!['session.create','conversation.send'].includes(c.name)));
 }finally{await renderAct(async()=>root?.unmount())}
});
test('a rejected draft read can be retried in place without replacing explicit choices',async()=>{
 const calls=[];let root,state={view:{newSessionDraft:{workspace:'/trial',selection:{instance:'picked',model:'explicit'}},composerModel:{open:true,sessionId:null}},draftDefaults:{}};
 const render=()=>React.createElement(ModelControl,{state,act:async(name,args)=>{calls.push({name,args});return name==='configuration.defaults'?undefined:{accepted:true}}});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.match(JSON.stringify(root.toJSON()),/Could not load model settings/);
  await renderAct(async()=>root.root.findAllByType('button').find(n=>n.children.includes('Retry model settings')).props.onClick());
  await renderAct(async()=>{await new Promise(r=>setTimeout(r,300))});
  assert.equal(calls.filter(c=>c.name==='configuration.defaults').length,2);
  assert.ok(calls.every(c=>!c.args?.patch?.newSessionDraft));
  assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/explicit/);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('authoritative unsupported reasoning shows no invented slider or selection',async()=>{
 const calls=[],session={id:'chat'},providers=[{id:'one',sharedCatalogKey:'account-one',info:{defaults:{model:'plain'}},configSchema:{fields:[]}}];
 const state={view:{composerModel:{open:true,sessionId:'chat',instance:'one',model:'plain'}},modelCatalogs:{'account-one':{phase:'ready',models:['plain'],metadata:{configSchema:{fields:[]}}}},runtimeControl:{chat:{'configuration.catalog':{configurationRevision:0,providers,effective:{instance:'one',model:'plain'}}}}};let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}})));
  assert.equal(root.root.findAllByProps({id:'chat-effort'}).length,0);
  assert.match(JSON.stringify(root.toJSON()),/Reasoning effort is not configurable for this model/);
  assert.equal(calls.some(row=>row.name==='conversation.configure'||row.name==='conversation.send'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});

const savedModelState=()=>({
 view:{composerModel:{open:true,sessionId:'chat',instance:'one',model:'first'}},
 modelCatalogs:{'account-one':{phase:'ready',models:['first','second'],metadata:{configSchema:{fields:[{id:'reasoning_effort',choices:['low','high']}]}}}},
 runtimeControl:{chat:{
  'configuration.catalog':{configurationRevision:0,providers:[{id:'one',sharedCatalogKey:'account-one',info:{defaults:{model:'first'}}}],effective:{instance:'one',model:'first'}}
 }}
});
test('a connection conflict explains withheld choices instead of claiming the model is undiscovered',async()=>{
 const calls=[],state=savedModelState(),session={id:'chat',status:'working'};
 state.runtimeControl.chat['configuration.catalog'].connectionBindings={
  one:{eligible:false,reason:'This conversation has different saved connection settings.'}
 };
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,working:true,act:record(calls)})));
  assert.equal(field(root,'chat-provider').props.disabled,false,'Another connection remains selectable');
  assert.equal(field(root,'chat-model').props.disabled,true);
  assert.equal(field(root,'chat-model').props.value,'first');
  const text=renderedText(root.toJSON());
  assert.match(text,/does not mean the model is unavailable/);
  assert.doesNotMatch(text,/not in the discovered list|Manual ID/);
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('one compact dialog bridges model, bundle and effort saves but not another client revision',async()=>{
 const calls=[],state={...savedModelState(),registeredBundles:[{value:'anchors'},{value:'work'}]};
 let session={id:'chat',bundle:'anchors',nextMessageRevision:0},actualRevision=0,root;
 const act=async(name,args)=>{
  calls.push({name,args});
  if(name!=='conversation.configure')return {accepted:true};
  if(args.expectedRevision!==actualRevision)return {accepted:false,error:'Next message settings changed. Review the current choices.'};
  session={...session,nextMessageRevision:++actualRevision,...(args.selection?{pendingModelSelection:args.selection}:{}),...(args.bundle?{pendingBundleSelection:{bundle:args.bundle}}:{})};
  return {accepted:true,result:{revision:actualRevision}};
 };
 const render=()=>React.createElement(ComposerOptions,{state,session,act});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'second'}}));
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>root.root.findByProps({id:'composer-options-bundle'}).props.onChange({target:{value:'work'}}));
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onChange({currentTarget:{value:'1'}}));
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onKeyUp());
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args.expectedRevision),[0,1,2]);
  assert.equal(actualRevision,3);
  actualRevision=4;session={...session,nextMessageRevision:4,pendingModelSelection:{instance:'one',model:'first',effort:'low'}};
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onChange({currentTarget:{value:'0'}}));
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onKeyUp());
  assert.equal(calls.at(-1).args.expectedRevision,3);
  assert.equal(actualRevision,4);
  assert.match(JSON.stringify(root.toJSON()),/Review the current choices/);
 }finally{await renderAct(async()=>root?.unmount())}
});
for(const saving of ['model','bundle'])test('compact menu shares the pending guard during '+saving+' save',async()=>{
 const state={...savedModelState(),registeredBundles:[{value:'anchors'},{value:'work'}]},session={id:'chat',bundle:'anchors'};
 let root,release;
 const act=async(name,args)=>name==='conversation.configure'?new Promise(resolve=>{release=()=>resolve({accepted:true,result:{revision:args.expectedRevision+1}})}):{accepted:true};
 try{
  await renderAct(async()=>root=create(React.createElement(ComposerOptions,{state,session,act})));
  await renderAct(async()=>{field(root,saving==='model'?'chat-model':'composer-options-bundle').props.onChange({target:{value:saving==='model'?'second':'work'}})});
  for(const id of ['chat-provider','chat-model','chat-effort','composer-options-bundle'])assert.equal(field(root,id).props.disabled,true,id);
  await renderAct(async()=>release());
  for(const id of ['chat-provider','chat-model','chat-effort','composer-options-bundle'])assert.equal(field(root,id).props.disabled,false,id);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('starting without accepted input keeps saved choices usable without runtime mutation',async()=>{
 const calls=[],state=savedModelState(),session={id:'chat',status:'starting',nextMessageRevision:3};
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelControl,{state,session,working:true,act:record(calls)})));
  assert.equal(field(root,'chat-model').props.disabled,false);
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'second'}}));
  await renderAct(async()=>{
   root.root.findByProps({id:'chat-effort'}).props.onChange({currentTarget:{value:'1'}});
  });
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onKeyUp());
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args),[
   {sessionId:'chat',expectedRevision:3,selection:{instance:'one',model:'second'}},
   {sessionId:'chat',expectedRevision:4,selection:{instance:'one',model:'second',effort:'high'}}
  ]);
  assert.match(JSON.stringify(root.toJSON()),/Next message/);
  assert.ok(!calls.some(c=>c.name==='conversation.send'||c.args?.operation==='provider.queueSelection'||c.name==='configuration.inspect'));
 }finally{await renderAct(async()=>root?.unmount())}
});
for(const guard of [
 {configurationBusy:true},
 {pendingConfiguration:{phase:'error'}},
 {ownership:{status:'blocked'}},
])test('Next message choices retain configuration/ownership guard '+JSON.stringify(guard),async()=>{
 const calls=[],session={id:'chat',status:'starting',...guard};let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelControl,{state:savedModelState(),session,working:true,act:record(calls)})));
  for(const id of ['chat-provider','chat-model','chat-effort'])assert.equal(field(root,id).props.disabled,true);
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'second'}}));
  assert.ok(!calls.some(c=>c.name==='conversation.configure'));
 }finally{await renderAct(async()=>root?.unmount())}
});
for(const active of [
 {status:'working',collaborationGeneration:{id:'live',terminal:false}},
 {status:'starting',messages:[{role:'user',delivery:{status:'sending'}}]},
 {status:'running',workers:[{status:'running'}]},
 {status:'stopping',workers:[{status:'stopping'}]}
])test('active chat can save model effort and bundle for its next message '+JSON.stringify(active),async()=>{
 const calls=[],selection={instance:'one',model:'first',effort:'low'};
 const session={id:'chat',bundle:'anchors',nextMessageRevision:3,
  runtimeReport:{effective_selection:selection},...active};
 const before=structuredClone(session);
 const state={...savedModelState(),registeredBundles:[{value:'anchors'},{value:'work'}]};
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ComposerOptions,{state,session,working:true,act:record(calls)})));
  for(const id of ['chat-provider','chat-model','chat-effort','composer-options-bundle'])assert.equal(field(root,id).props.disabled,false,id);
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'second'}}));
  await renderAct(async()=>field(root,'chat-effort').props.onChange({currentTarget:{value:'1'}}));
  await renderAct(async()=>field(root,'chat-effort').props.onKeyUp());
  await renderAct(async()=>field(root,'composer-options-bundle').props.onChange({target:{value:'work'}}));
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args.expectedRevision),[3,4,5]);
  assert.deepEqual(session,before,'Saving must not change running session props');
  assert.ok(!calls.some(c=>['conversation.send','bundle.switch','configuration.apply'].includes(c.name)||['provider.select','provider.queueSelection'].includes(c.args?.operation)));
  assert.match(renderedText(root.toJSON()),/current turn.*unchanged/i);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('saving locks only the choice controls and rejected receipts remain visible',async()=>{
 const calls=[];let root,release,session={id:'chat',nextMessageRevision:1};
 const act=async(name,args)=>{calls.push({name,args});return name==='conversation.configure'?new Promise(resolve=>{release=resolve}):{accepted:true}};
 const render=()=>React.createElement(ModelControl,{state:savedModelState(),session,act});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>{field(root,'chat-model').props.onChange({target:{value:'second'}})});
  assert.equal(field(root,'chat-model').props.disabled,true);
  assert.equal(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.disabled,false);
  assert.match(JSON.stringify(root.toJSON()),/Saving Next message/);
  await renderAct(async()=>release({accepted:false,error:'Next message settings changed.'}));
  assert.equal(field(root,'chat-model').props.disabled,false);
  assert.match(JSON.stringify(root.toJSON()),/Next message settings changed/);
  assert.equal(calls.filter(c=>c.name==='conversation.configure').length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('pending model choice and failed application are not reported as mounted behavior',async()=>{
 const session={id:'chat',pendingModelSelection:{instance:'one',model:'second'},nextMessageError:'Could not apply the saved model.'};let root;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelControl,{state:savedModelState(),session,act:record([])})));
  assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/second/);
  assert.match(JSON.stringify(root.toJSON()),/Saved; not applied yet/);
  assert.match(JSON.stringify(root.toJSON()),/Could not apply the saved model/);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('receipt revisions bridge only own saves, never an unrelated later client update',async()=>{
 const calls=[];let control,root,session={id:'chat',nextMessageRevision:4},reject=false;
 function Harness(){control=useNextMessageConfiguration(session,async(name,args)=>{
  calls.push({name,args});return reject?{accepted:false,error:'Settings changed'}:{accepted:true,result:{revision:args.expectedRevision+1}};
 });return null}
 try{
  await renderAct(async()=>root=create(React.createElement(Harness)));
  const original=control.configure;
  await renderAct(async()=>original({selection:{instance:'one',model:'first'}}));
  await renderAct(async()=>original({selection:{instance:'one',model:'second'}}));
  assert.deepEqual(calls.map(c=>c.args.expectedRevision),[4,5]);
  session={...session,nextMessageRevision:9};
  await renderAct(async()=>root.update(React.createElement(Harness)));
  reject=true;
  await renderAct(async()=>assert.rejects(original({bundle:'work'}),/Settings changed/));
  assert.equal(calls.at(-1).args.expectedRevision,6,'A stale action must not adopt the other client’s revision 9');
  assert.equal(calls.length,3,'A conflict must not retry');
  reject=false;
  await renderAct(async()=>control.configure({bundle:'work'}));
  assert.equal(calls.at(-1).args.expectedRevision,9,'A fresh user action captures the currently rendered revision');
  session={id:'another',nextMessageRevision:0};
  await renderAct(async()=>root.update(React.createElement(Harness)));
  await renderAct(async()=>original({bundle:'anchors'}));
  assert.equal(calls.length,4,'A stale action must not cross a conversation boundary');
 }finally{await renderAct(async()=>root?.unmount())}
});
test('a saved bundle default refreshes choices without presenting the old mounted model as next',async()=>{
 const calls=[];let state=savedModelState(),session={id:'chat',bundle:'anchors',nextMessageRevision:0},root;
 const render=()=>React.createElement(ModelControl,{state,session,act:record(calls)});
 try{
  await renderAct(async()=>root=create(render()));
  assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,0);
  session={...session,nextMessageRevision:1,pendingBundleSelection:{bundle:'work',resetModel:true}};
  await renderAct(async()=>root.update(render()));
  assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,1);
  assert.equal(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,'Bundle default');
  state={...state,runtimeControl:{chat:{...state.runtimeControl.chat,'configuration.catalog':{...state.runtimeControl.chat['configuration.catalog'],nextMessageRevision:1,effective:{instance:'one',model:'second'}}}}};
  await renderAct(async()=>root.update(render()));
  assert.match(root.root.findByProps({'aria-label':'Model and reasoning settings'}).props.title,/second/);
  assert.equal(calls.filter(c=>c.args?.operation==='configuration.catalog').length,1);
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('an edited model form does not attach a peer revision to its stale model during an effort change',async()=>{
 const calls=[],state=savedModelState();let session={id:'chat',nextMessageRevision:0},actualRevision=0,root;
 const act=async(name,args)=>{
  calls.push({name,args});
  if(name!=='conversation.configure')return {accepted:true};
  if(args.expectedRevision!==actualRevision)return {accepted:false,error:'Next message settings changed. Review the current choices before saving again.'};
  return {accepted:true,result:{revision:++actualRevision}};
 };
 const render=()=>React.createElement(ModelControl,{state,session,act});
 try{
  await renderAct(async()=>root=create(render()));
  await renderAct(async()=>field(root,'chat-model').props.onChange({target:{value:'second'}}));
  actualRevision=2;
  session={...session,nextMessageRevision:2,pendingModelSelection:{instance:'one',model:'first',effort:'low'}};
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onChange({currentTarget:{value:'1'}}));
  await renderAct(async()=>root.root.findByProps({id:'chat-effort'}).props.onKeyUp());
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args.expectedRevision),[0,1]);
  assert.equal(actualRevision,2);
  assert.match(JSON.stringify(root.toJSON()),/Review the current choices/);
 }finally{await renderAct(async()=>root?.unmount())}
});
