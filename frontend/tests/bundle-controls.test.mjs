import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {BundleControl,BundleDefaults}=await server.ssrLoadModule('/src/bundle-controls.jsx');
const {ModuleSettings}=await server.ssrLoadModule('/src/bundles.jsx');
const {isConversationChoiceRead,createSettingsActionQueue}=await server.ssrLoadModule('/src/settings-action-queue.js');
const record=calls=>async(name,args)=>{calls.push({name,args});return {accepted:true,...(name==='conversation.configure'?{result:{revision:args.expectedRevision+1}}:{})}};
globalThis.IS_REACT_ACT_ENVIRONMENT=true;test.after(()=>server.close());
test('late catalog preserves selection and shared actions target the explicit conversation',async()=>{
 const calls=[],act=record(calls);
 let session={id:'chat',bundle:'anchors',status:'idle'},state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}}},root;
 const render=()=>React.createElement(BundleControl,{state,session,act,working:false});
 await renderAct(async()=>{root=create(render())});state={...state,registeredBundles:[{name:'work',label:'Work',value:'work'}]};
 await renderAct(async()=>root.update(render()));assert.equal(root.root.findAllByProps({id:'conversation-bundle'}).find(n=>n.type==='select').props.value,'work');
 await renderAct(async()=>root.root.findByProps({'data-action':'bundle.preview'}).props.onClick());assert.deepEqual(calls.find(row=>row.name==='bundle.preview').args,{sessionId:'chat',bundle:'work'});
 session={...session,bundlePreview:{bundle:'work',previewId:'token',modelCompatible:true,changes:{}},bundleChange:{phase:'ready',action:'bundle.preview'}};
 await renderAct(async()=>root.update(render()));await renderAct(async()=>root.root.findByProps({'data-action':'conversation.configure'}).props.onClick());assert.deepEqual(calls.find(row=>row.name==='conversation.configure').args,{sessionId:'chat',expectedRevision:0,bundle:'work',resetModel:false});
 assert.equal(calls.some(row=>row.name==='bundle.switch'),false);
 session={...session,nextMessageError:'Failed mount'};await renderAct(async()=>root.update(render()));assert.equal(root.root.findAllByProps({id:'conversation-bundle'}).find(n=>n.type==='select').props.value,'work');assert.equal(root.root.findByProps({role:'alert'}).findByType('strong').children.join(''),'Failed mount');await renderAct(async()=>root.unmount());
});
test('registered default selection saves immediately with an explicit scope',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}},state={view:{},bundleDefaults:{app:null,shared:'anchors',effective:'anchors',source:'shared',workspacePath:'/workspace'},registeredBundles:[{name:'work',value:'work'}]};
 let root;await renderAct(async()=>{root=create(React.createElement(BundleDefaults,{state,act}))});await renderAct(async()=>root.root.findAllByProps({id:'default-bundle'}).find(n=>n.type==='select').props.onChange({target:{value:'work'}}));assert.equal(calls.filter(row=>row.name==='bundle.default').length,1);
 assert.deepEqual(calls.find(row=>row.name==='bundle.default').args,{scope:'app',bundle:'work'});await renderAct(async()=>root.unmount());
});
test('global Settings refuses a retained workspace scope draft',async()=>{
 const calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}},state={view:{bundleDefaultsDraft:{scope:'workspace',bundle:'private'}},bundleDefaults:{app:'anchors',workspace:'private',workspacePath:'/private'},registeredBundles:[{value:'anchors'}]};
 let root;await renderAct(async()=>{root=create(React.createElement(BundleDefaults,{state,act}))});
 assert.equal(root.root.findByProps({id:'bundle-default-scope'}).props.value,'app');
 assert.equal(root.root.findAllByType('option').some(row=>row.props.value==='workspace'),false);
 await renderAct(async()=>root.root.findAllByProps({id:'default-bundle'}).find(n=>n.type==='select').props.onChange({target:{value:'anchors'}}));
 assert.deepEqual(calls.find(row=>row.name==='bundle.default').args,{scope:'app',bundle:'anchors'});
 await renderAct(async()=>root.unmount());
});
test('Next message save and explicit fork need no preview and show immediate pending feedback',async()=>{
 let release;const calls=[],act=(name,args)=>{if(name==='bundles.list')return Promise.resolve({accepted:true});calls.push({name,args});return new Promise(resolve=>{release=resolve})};
 const session={id:'chat',bundle:'anchors'},state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'work',label:'Work'}]};
 let root,operation;await renderAct(async()=>{root=create(React.createElement(BundleControl,{state,session,act,working:false}))});
 const click=()=>root.root.findByProps({'data-action':'conversation.configure'}).props.onClick();
 await renderAct(async()=>{operation=click()});
 assert.deepEqual(calls,[{name:'conversation.configure',args:{sessionId:'chat',expectedRevision:0,bundle:'work',resetModel:false}}]);
 assert.equal(root.root.findByProps({'data-action':'bundle.preview'}).props.disabled,true);
 assert.equal(root.root.findByProps({'data-action':'bundle.fork'}).props.disabled,true);
 assert.ok(root.root.findAll(n=>n.type==='strong'&&n.children.join('')==='Saving Next message settings…').length);
 await renderAct(async()=>click());assert.equal(calls.length,1);
 await renderAct(async()=>{release({accepted:true,result:{revision:1}});await operation});
 await renderAct(async()=>{operation=root.root.findByProps({'data-action':'bundle.fork'}).props.onClick()});
 assert.deepEqual(calls[1],{name:'bundle.fork',args:{sessionId:'chat',bundle:'work',resetModel:false}});
 await renderAct(async()=>{release();await operation;root.unmount()});
});
for(const first of ['save','preview'])test(`a slow preview permits a new bundle save with independent pending guards (${first} settles first)`,async()=>{
 const calls=[],releases={};let root,previewOperation,saveOperation;
 const act=(name,args)=>{calls.push({name,args});return ['bundle.preview','conversation.configure'].includes(name)&&!releases[name]?new Promise(resolve=>{releases[name]=resolve}):Promise.resolve({accepted:true,result:{revision:5}})};
 const state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'anchors'},{value:'work'},{value:'other'}]};
 let session={id:'chat',bundle:'anchors',nextMessageRevision:4};
 const render=()=>React.createElement(BundleControl,{state,session,act,working:false});
 const button=action=>root.root.findByProps({'data-action':action});
 const picker=()=>root.root.findAllByProps({id:'conversation-bundle'}).find(n=>n.type==='select');
 const settle=async kind=>{
  if(kind==='save'){
   releases['conversation.configure']({accepted:true,result:{revision:5}});await saveOperation;
   session={...session,nextMessageRevision:5,pendingBundleSelection:{bundle:'other',resetModel:false}};
  }else{releases['bundle.preview']({accepted:true});await previewOperation}
  session={...session,bundleChange:{phase:'ready',action:'bundle.preview'}};
  root.update(render());
 };
 try{
  await renderAct(async()=>{root=create(render())});
  await renderAct(async()=>{previewOperation=button('bundle.preview').props.onClick();await button('bundle.preview').props.onClick();await button('bundle.fork').props.onClick()});
  assert.equal(calls.filter(c=>c.name==='bundle.preview').length,1);assert.equal(calls.some(c=>c.name==='bundle.fork'),false);
  assert.equal(picker().props.disabled,false);assert.equal(button('conversation.configure').props.disabled,false);
  assert.equal(button('bundle.preview').props.disabled,true);assert.equal(button('bundle.fork').props.disabled,true);
  assert.match(JSON.stringify(root.toJSON()),/Previewing bundle changes/);
  session={...session,bundleChange:{phase:'working',action:'bundle.preview'}};
  await renderAct(async()=>root.update(render()));
  assert.equal(picker().props.disabled,false);assert.equal(button('conversation.configure').props.disabled,false);
  await renderAct(async()=>picker().props.onChange({target:{value:'other'}}));
  await renderAct(async()=>{saveOperation=button('conversation.configure').props.onClick();await button('conversation.configure').props.onClick()});
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure'),[{name:'conversation.configure',args:{sessionId:'chat',expectedRevision:4,bundle:'other',resetModel:false}}]);
  assert.equal(picker().props.disabled,true);assert.equal(button('conversation.configure').props.disabled,true);
  assert.equal(button('bundle.preview').props.disabled,true);assert.equal(button('bundle.fork').props.disabled,true);
  assert.match(JSON.stringify(root.toJSON()),/Saving Next message settings/);
  await renderAct(async()=>settle(first));
  assert.equal(picker().props.disabled,first==='preview','Only the unfinished save locks the picker');
  assert.equal(button('conversation.configure').props.disabled,true,'An unfinished save or unchanged saved choice cannot be saved again');
  assert.equal(button('bundle.preview').props.disabled,true);assert.equal(button('bundle.fork').props.disabled,true);
  await renderAct(async()=>settle(first==='save'?'preview':'save'));
  assert.equal(picker().props.disabled,false);assert.equal(picker().props.value,'other');
  assert.equal(button('conversation.configure').props.disabled,true);
  assert.equal(button('bundle.preview').props.disabled,false);assert.equal(button('bundle.fork').props.disabled,false);
  assert.equal(calls.filter(c=>c.name==='bundle.preview').length,1);
  assert.equal(calls.some(c=>c.name==='bundle.switch'||c.name==='conversation.send'||c.name==='bundle.fork'),false);
 }finally{
  await renderAct(async()=>{releases['bundle.preview']?.({accepted:true});releases['conversation.configure']?.({accepted:true,result:{revision:5}});await Promise.all([previewOperation,saveOperation]);root?.unmount()});
 }
});
test('a host-reported preview allows editing but keeps configuration and unsaved-choice guards',async()=>{
 const calls=[],act=record(calls),base={id:'chat',bundle:'anchors',bundleChange:{phase:'working',action:'bundle.preview'}};
 let session=base,state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'anchors'},{value:'work'}]},root;
 const render=()=>React.createElement(BundleControl,{state,session,act,working:false});
 const button=action=>root.root.findByProps({'data-action':action});
 const picker=()=>root.root.findAllByProps({id:'conversation-bundle'}).find(n=>n.type==='select');
 try{
  await renderAct(async()=>{root=create(render())});
  assert.equal(picker().props.disabled,false);assert.equal(button('conversation.configure').props.disabled,false);
  assert.equal(button('bundle.preview').props.disabled,true);assert.equal(button('bundle.fork').props.disabled,true);
  await renderAct(async()=>{await button('bundle.preview').props.onClick();await button('bundle.fork').props.onClick()});
  assert.equal(calls.some(c=>c.name==='bundle.preview'||c.name==='bundle.fork'),false);
  for(const guard of [{configurationBusy:true},{pendingConfiguration:{phase:'queued'}},{ownership:{status:'yielded'}}]){
   session={...base,...guard};await renderAct(async()=>root.update(render()));
   assert.equal(picker().props.disabled,true);assert.equal(button('conversation.configure').props.disabled,true);
   await renderAct(async()=>button('conversation.configure').props.onClick());
  }
  session=base;
  for(const bundle of ['anchors',' ']){
   state={...state,view:{composerBundle:{open:true,sessionId:'chat',bundle}}};await renderAct(async()=>root.update(render()));
   assert.equal(button('conversation.configure').props.disabled,true);
  }
  state={...state,view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}}};
  session={...base,bundlePreview:{bundle:'work',previewId:'prior-preview',modelCompatible:false}};
  await renderAct(async()=>root.update(render()));
  assert.equal(button('conversation.configure').props.disabled,true);
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('an incompatible preview requires an explicit model choice before saving',async()=>{
 const calls=[],act=record(calls);
 const state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}}},session={id:'chat',bundle:'anchors',bundlePreview:{bundle:'work',previewId:'choice',modelCompatible:false}};
 let root;await renderAct(async()=>{root=create(React.createElement(BundleControl,{state,session,act,working:false}))});
 assert.equal(root.root.findByProps({'data-action':'conversation.configure'}).props.disabled,true);
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{checked:true}}));
 assert.equal(root.root.findByProps({'data-action':'conversation.configure'}).props.disabled,false);
 await renderAct(async()=>root.root.findByProps({'data-action':'conversation.configure'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'conversation.configure',args:{sessionId:'chat',expectedRevision:0,bundle:'work',resetModel:true}});
 await renderAct(async()=>root.unmount());
});


test('draft bundle trigger reflects saved choice and can restore workspace inheritance without a session',async()=>{
 let state={view:{newSessionDraft:{workspace:'/future',bundle:'anchors',selection:{instance:'one',model:'chosen'}},composerBundle:{open:true,sessionId:null,bundle:'work'}}},root;
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}}};
 const render=()=>React.createElement(BundleControl,{state,session:null,act,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.ok(root.root.findByProps({'aria-label':'Conversation bundle'}).findByType('span').children.includes('anchors'));
 await renderAct(async()=>root.root.findAllByType('button').find(n=>n.children.includes('Use bundle')).props.onClick());
 assert.equal(state.view.newSessionDraft.bundle,'work');assert.equal(state.view.newSessionDraft.selection.model,'chosen');
 await renderAct(async()=>root.update(render()));await renderAct(async()=>root.root.findByProps({'aria-label':'Conversation bundle'}).props.onClick());
 await renderAct(async()=>root.root.findAllByType('button').find(n=>n.children.includes('Use workspace default')).props.onClick());
 assert.equal(state.view.newSessionDraft.bundle,'');assert.equal(calls.some(c=>c.name.startsWith('bundle.')||c.name==='session.create'),false);
 await renderAct(async()=>root.unmount());
});

test('compact draft selector shows resolved default and applies a choice without a session',async()=>{
 let state={settings:{workspace:'/new'},workspaces:[{id:'new',path:'/new'}],selectedWorkspaceId:'new',view:{},registeredBundles:[{value:'work',label:'Work'},{value:'anchors',label:'Anchors'}],draftDefaults:{'["/new",""]':{bundle:'work'}}},root;
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state={...state,view:{...state.view,...args.patch}}};
 const render=()=>React.createElement(BundleControl,{compact:true,state,act,working:false});
 await renderAct(async()=>{root=create(render())});
 assert.ok(root.root.findByProps({'aria-label':'Conversation bundle'}).findByType('span').children.includes('Work'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Conversation bundle'}).props.onClick());
 await renderAct(async()=>root.root.findByType('select').props.onChange({target:{value:'anchors'}}));
 assert.equal(state.view.newSessionDraft.bundle,'anchors');assert.equal(calls.some(c=>c.name==='session.create'),false);
 assert.equal(root.root.findAllByType('input').length,0);await renderAct(async()=>root.unmount());
});

test('loaded bundle choices are usable while the inherited default is unresolved',async()=>{
 const calls=[],state={view:{composerBundle:{open:true,sessionId:null}},registeredBundles:[{value:'work',label:'Work'}]};let root;
 try{
  await renderAct(async()=>root=create(React.createElement(BundleControl,{state,compact:true,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}})));
  const picker=root.root.findByType('select');
  assert.equal(picker.findAllByType('option')[0].children.join(''),'Choose a bundle');
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Loading bundle/);
  await renderAct(async()=>picker.props.onChange({target:{value:'work'}}));
  assert.equal(calls.find(c=>c.args?.patch?.newSessionDraft)?.args.patch.newSessionDraft.bundle,'work');
 }finally{await renderAct(async()=>root?.unmount())}
});

test('compact catalog loading and retry preserve the explicit unlisted bundle',async()=>{
 const calls=[];let root,state={view:{newSessionDraft:{workspace:'/trial',bundle:'custom:saved'},composerBundle:{open:true,sessionId:null}},registeredBundles:[]};
 const render=()=>React.createElement(BundleControl,{state,compact:true,act:async(name,args)=>{calls.push({name,args});return {accepted:false,error:'Catalog unavailable'}}});
 try{
  await renderAct(async()=>root=create(render()));
  assert.equal(root.root.findByType('select').props.value,'custom:saved');
  assert.equal(root.root.findByType('select').findByType('option').children.join(''),'custom:saved');
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/No bundles|Loading bundles/);
  state={...state,registeredBundles:[{value:'work',label:'Work'}]};await renderAct(async()=>root.update(render()));
  assert.equal(root.root.findByType('select').props.value,'custom:saved');
  assert.equal(calls.some(row=>row.name==='view.update'||row.name==='bundle.switch'||row.name==='conversation.send'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('compact Next message save locks pending choice and failed completion does not resubmit',async()=>{
 const calls=[];let root,release,operation;
 const state={view:{composerBundle:{open:true,sessionId:'chat'}},registeredBundles:[{value:'work'},{value:'anchors'}]},session={id:'chat',bundle:'work'};
 const act=(name,args)=>{calls.push({name,args});return name==='conversation.configure'?new Promise((resolve,reject)=>{release=()=>reject(Error('Save unavailable'))}):Promise.resolve({accepted:true})};
 try{
  await renderAct(async()=>root=create(React.createElement(BundleControl,{state,session,compact:true,act,working:false})));
  const picker=()=>root.root.findByType('select');
  await renderAct(async()=>{operation=picker().props.onChange({target:{value:'anchors'}})});
  assert.equal(picker().props.disabled,true);assert.equal(picker().props.value,'work');
  await renderAct(async()=>picker().props.onChange({target:{value:'anchors'}}));
  assert.equal(calls.filter(row=>row.name==='conversation.configure').length,1);
  await renderAct(async()=>{release();await operation});
  assert.equal(picker().props.disabled,false);assert.equal(picker().props.value,'work');
  assert.match(JSON.stringify(root.toJSON()),/Save unavailable/);
  assert.equal(calls.filter(row=>row.name==='conversation.configure').length,1);
  assert.equal(calls.some(row=>row.name==='conversation.send'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('compact model-reset recovery uses the failed pending bundle without a preview or the stale draft',async()=>{
 const calls=[],act=record(calls),state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'stale-draft'}},registeredBundles:[{value:'anchors'},{value:'work'},{value:'stale-draft'}]};
 let session={id:'chat',bundle:'anchors',nextMessageRevision:6,pendingBundleSelection:{bundle:'work',resetModel:false},nextMessageFailure:{code:'incompatible-model',bundle:'work'},nextMessageError:'Pinned model unavailable',bundleChange:{phase:'ready',action:'bundle.preview'}},root;
 const render=()=>React.createElement(BundleControl,{compact:true,state,session,act,working:false});
 const recovery=()=>root.root.findAllByType('button').filter(n=>n.children.includes('Use the new bundle’s model'));
 try{
  await renderAct(async()=>{root=create(render())});
  assert.equal(session.bundlePreview,undefined);assert.equal(root.root.findByType('select').props.value,'work');
  assert.equal(recovery().length,1);assert.equal(recovery()[0].props.disabled,false);
  await renderAct(async()=>recovery()[0].props.onClick());
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure'),[{name:'conversation.configure',args:{sessionId:'chat',expectedRevision:6,bundle:'work',resetModel:true}}]);
  assert.equal(calls.find(c=>c.name==='view.update').args.patch.composerBundle.bundle,'work');
  assert.equal(calls.some(c=>c.name==='bundle.preview'||c.name==='bundle.switch'||c.name==='conversation.send'),false);
  session={...session,nextMessageRevision:7,pendingBundleSelection:{bundle:'work',resetModel:true},nextMessageFailure:null,nextMessageError:null};
  await renderAct(async()=>root.update(render()));assert.equal(recovery().length,0);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('compact model-reset recovery ignores failures not tied to the current pending bundle',async()=>{
 const calls=[],state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'work'}]};
 const base={id:'chat',bundle:'work',pendingBundleSelection:{bundle:'work',resetModel:false},nextMessageError:'Pinned model unavailable'};
 let session=base,root;
 const render=()=>React.createElement(BundleControl,{compact:true,state,session,act:record(calls),working:false});
 try{
  await renderAct(async()=>{root=create(render())});
  for(const change of [{},{nextMessageFailure:{code:'incompatible-model',bundle:'old-root'}},{nextMessageFailure:{code:'other-failure',bundle:'work'}},{pendingBundleSelection:null,nextMessageFailure:{code:'incompatible-model',bundle:'work'}}]){
   session={...base,...change};await renderAct(async()=>root.update(render()));
   assert.equal(root.root.findAllByType('button').some(n=>n.children.includes('Use the new bundle’s model')),false);
  }
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('compact preview-based model-reset recovery remains explicit',async()=>{
 const calls=[],state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'anchors'},{value:'work'}]};
 const session={id:'chat',bundle:'anchors',nextMessageRevision:3,bundlePreview:{bundle:'work',previewId:'choice',modelCompatible:false},bundleChange:{phase:'error',action:'bundle.switch',error:'Pinned model unavailable'}};
 let root;
 try{
  await renderAct(async()=>{root=create(React.createElement(BundleControl,{compact:true,state,session,act:record(calls),working:false}))});
  assert.equal(calls.some(c=>c.name==='conversation.configure'),false);
  await renderAct(async()=>root.root.findAllByType('button').find(n=>n.children.includes('Use the new bundle’s model')).props.onClick());
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure'),[{name:'conversation.configure',args:{sessionId:'chat',expectedRevision:3,bundle:'work',resetModel:true}}]);
 }finally{await renderAct(async()=>root?.unmount())}
});

for(const compact of [false,true])test(`catalog failure preserves saved choice and retries only explicitly (${compact?'compact':'detailed'})`,async()=>{
 const calls=[],session={id:'chat',bundle:'saved-unlisted'},act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 let state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'saved-unlisted'}},registeredBundles:[],actionStatus:{'bundles.list':{phase:'working'}}};
 const render=()=>React.createElement(BundleControl,{compact,state,session,act});
 try{
  await renderAct(async()=>root=create(render()));assert.match(JSON.stringify(root.toJSON()),/Loading bundle choices/);
  const original=calls.length;state={...state,actionStatus:{'bundles.list':{phase:'error',error:'Catalog unavailable'}}};await renderAct(async()=>root.update(render()));
  assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Catalog unavailable');assert.equal(calls.length,original);
  await renderAct(async()=>root.root.findByProps({'data-action':'bundles.list'}).props.onClick());assert.deepEqual(calls.at(-1),{name:'bundles.list',args:{}});
  state={...state,actionStatus:{'bundles.list':{phase:'working'}}};await renderAct(async()=>root.update(render()));assert.equal(root.root.findAllByProps({'data-action':'bundles.list'}).length,0);
  state={...state,actionStatus:{'bundles.list':{phase:'ready'}},registeredBundles:[{value:'work',label:'Work'}]};await renderAct(async()=>root.update(render()));
  assert.match(JSON.stringify(root.toJSON()),/saved-unlisted.*not in the current catalog/);assert.equal(session.bundle,'saved-unlisted');assert.equal(state.view.composerBundle.bundle,'saved-unlisted');assert.equal(calls.some(row=>row.name==='bundle.switch'),false);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('successful empty bundle catalog is distinct from loading or failure',async()=>{
 const state={view:{composerBundle:{open:true,sessionId:null}},registeredBundles:[],actionStatus:{'bundles.list':{phase:'ready'}}};let root;
 try{
  await renderAct(async()=>root=create(React.createElement(BundleControl,{compact:true,state,act:async()=>({accepted:true})})));
  const text=JSON.stringify(root.toJSON());assert.match(text,/No bundle choices/);assert.match(text,/No registered bundle choices are available/);assert.doesNotMatch(text,/Loading bundle/);assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('catalog retry transport rejection stays actionable without changing a choice',async()=>{
 const {BundleCatalogStatus}=await server.ssrLoadModule('/src/settings-ui.jsx');let root,calls=0;
 const state={registeredBundles:[],actionStatus:{'bundles.list':{phase:'error',error:'Initial catalog error'}}};
 try{
  await renderAct(async()=>root=create(React.createElement(BundleCatalogStatus,{state,value:'saved-choice',act:async()=>{calls++;if(calls===1)throw Error('Retry transport unavailable');return undefined}})));
  await renderAct(async()=>root.root.findByType('button').props.onClick());
  assert.equal(calls,1);assert.equal(root.root.findByType('button').props.disabled,false);assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Retry transport unavailable');
  await renderAct(async()=>root.root.findByType('button').props.onClick());assert.equal(root.root.findByProps({role:'alert'}).children.join(''),'Bundle choices could not be loaded. Try again.');assert.equal(calls,2);
  assert.deepEqual(state.registeredBundles,[]);
 }finally{await renderAct(async()=>root?.unmount())}
});

for(const compact of [true,false])for(const activity of [
 {status:'starting'},
 {status:'working',collaborationGeneration:{id:'live',terminal:false}},
 {status:'starting',messages:[{delivery:{status:'sending'}}]},
 {status:'working',workers:[{status:'starting'}]},
])test('active work permits Next message bundle save but not eager switch: '+compact+JSON.stringify(activity),async()=>{
 const calls=[],state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'work'},{value:'anchors'}]};
 let session={id:'chat',bundle:'anchors',nextMessageRevision:7,...activity},root;
 const render=()=>React.createElement(BundleControl,{compact,state,session,working:true,act:record(calls)});
 try{
  await renderAct(async()=>root=create(render()));
  const control=()=>root.root.findByProps({'data-action':'conversation.configure'});
  assert.equal(control().props.disabled,false);
  await renderAct(async()=>compact?control().props.onChange({target:{value:'work'}}):control().props.onClick());
  assert.deepEqual(calls.find(c=>c.name==='conversation.configure').args,{sessionId:'chat',expectedRevision:7,bundle:'work',resetModel:false});
  assert.equal(session.bundle,'anchors');
  assert.ok(!calls.some(c=>c.name==='bundle.switch'||c.name==='conversation.send'));
  session={...session,nextMessageRevision:8,pendingBundleSelection:{bundle:'work',resetModel:false}};
  await renderAct(async()=>root.update(render()));
  assert.match(JSON.stringify(root.toJSON()),/not applied yet/);
  assert.deepEqual(root.root.findByProps({'aria-label':'Conversation bundle'}).findByType('span').children,['work']);
  session={...session,nextMessageError:'The selected bundle could not be prepared.'};
  await renderAct(async()=>root.update(render()));
  assert.match(JSON.stringify(root.toJSON()),/could not be prepared/);
 }finally{await renderAct(async()=>root?.unmount())}
});
for(const guard of [
 {pendingConfiguration:{phase:'queued'}},
 {pendingConfiguration:{phase:'error'}},
 {ownership:{status:'yielded'}},
 {configurationBusy:true}
])test('bundle save retains guards while starting '+JSON.stringify(guard),async()=>{
 const calls=[],session={id:'chat',bundle:'anchors',status:'starting',...guard},state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'work'}]};
 for(const compact of [true,false]){
  let root;
  try{
   await renderAct(async()=>root=create(React.createElement(BundleControl,{compact,state,session,working:true,act:record(calls)})));
   const control=root.root.findByProps({'data-action':'conversation.configure'});
   assert.equal(control.props.disabled,true);
   await renderAct(async()=>compact?control.props.onChange({target:{value:'work'}}):control.props.onClick());
   assert.ok(!calls.some(c=>c.name==='conversation.configure'));
  }finally{await renderAct(async()=>root?.unmount())}
 }
});
test('compact saves reuse only acknowledged own revisions before a state refresh',async()=>{
 const calls=[],state={view:{composerBundle:{open:true,sessionId:'chat'}},registeredBundles:[{value:'work'},{value:'anchors'}]},session={id:'chat',bundle:'anchors',nextMessageRevision:2};
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement(BundleControl,{compact:true,state,session,act:record(calls)})));
  await renderAct(async()=>root.root.findByType('select').props.onChange({target:{value:'work'}}));
  await renderAct(async()=>root.root.findByType('select').props.onChange({target:{value:'anchors'}}));
  assert.deepEqual(calls.filter(c=>c.name==='conversation.configure').map(c=>c.args.expectedRevision),[2,3]);
  assert.equal(session.bundle,'anchors');
 }finally{await renderAct(async()=>root?.unmount())}
});
test('mounting module details never inspects a runtime; Load configuration is explicit',async()=>{
 const calls=[],session={id:'chat',title:'Dormant',bundle:'anchors',status:'idle'};
 let root;
 try{
  await renderAct(async()=>root=create(React.createElement('details',{},React.createElement('summary',{},'Conversation modules'),React.createElement(ModuleSettings,{state:{view:{}},session,act:record(calls)}))));
  assert.ok(!calls.some(c=>c.name==='configuration.inspect'||c.name==='runtime.inspect'||c.name==='runtime.control'));
  await renderAct(async()=>root.root.findByProps({'data-action':'configuration.inspect'}).props.onClick());
  assert.deepEqual(calls.filter(c=>c.name==='configuration.inspect'),[{name:'configuration.inspect',args:{id:'chat'}}]);
 }finally{await renderAct(async()=>root?.unmount())}
});
test('only named passive choice requests can leave the command queue',async()=>{
 for(const action of ['bundles.list','bundle.preview'])assert.equal(isConversationChoiceRead(action),true);
 assert.equal(isConversationChoiceRead('runtime.control',{operation:'configuration.catalog'}),true);
 for(const operation of ['configuration.providers','configuration.providerModels','provider.queueSelection','configuration.apply','native.compact','native.status'])
  assert.equal(isConversationChoiceRead('runtime.control',{operation}),false);
 for(const action of ['configuration.inspect','bundle.switch','bundle.fork','conversation.configure','view.update'])
  assert.equal(isConversationChoiceRead(action),false);
 assert.equal(isConversationChoiceRead('runtime.control',{operation:'configuration.catalog'},{expectedRevision:0}),false);
 const queue=createSettingsActionQueue(),commands={current:Promise.resolve()},calls=[];
 let release;const stalled=new Promise(resolve=>{release=resolve});
 const read=queue({current:Promise.resolve()},async()=>{calls.push('catalog');await stalled},null,commands.current);
 await queue(commands,async()=>calls.push('save'));
 assert.ok(calls.includes('save'),'A held catalog may not serialize a later settings save');
 release();await read;
});
test('a detailed bundle draft retains its revision when another client changes settings',async()=>{
 const calls=[],state={view:{composerBundle:{open:true,sessionId:'chat',bundle:'work'}},registeredBundles:[{value:'work'},{value:'anchors'}]};
 let session={id:'chat',bundle:'anchors',nextMessageRevision:4},root;
 const act=async(name,args)=>{calls.push({name,args});return name==='conversation.configure'?{accepted:false,error:'Next message settings changed. Review the current choices before saving again.'}:{accepted:true}};
 const render=()=>React.createElement(BundleControl,{state,session,act});
 try{
  await renderAct(async()=>root=create(render()));
  session={...session,nextMessageRevision:5,pendingModelSelection:{instance:'peer',model:'peer-model'}};
  await renderAct(async()=>root.update(render()));
  await renderAct(async()=>root.root.findByProps({'data-action':'conversation.configure'}).props.onClick());
  assert.equal(calls.find(c=>c.name==='conversation.configure').args.expectedRevision,4);
  assert.match(JSON.stringify(root.toJSON()),/Review the current choices/);
  assert.equal(calls.filter(c=>c.name==='conversation.configure').length,1);
 }finally{await renderAct(async()=>root?.unmount())}
});
