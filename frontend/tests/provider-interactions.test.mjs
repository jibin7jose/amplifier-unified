import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ProviderSettings}=await server.ssrLoadModule('/src/setup.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const renderedText=node=>typeof node==='string'?node:Array.isArray(node)?node.map(renderedText).join(''):renderedText(node?.children||[]);
test.after(()=>server.close());

for(const source of ['configured','static','simulated','unsupported']){
 test(`Settings keeps ${source} choices but disables API connection checks`,async()=>{
  const entry={phase:'ready',provenance:source,models:[{id:'Exact/Alias'}],
   metadata:{modelListInfo:{version:1,source},supports:{test:false},configSchema:{fields:[]}}};
  const state={view:{providerEditor:{id:'one',module:'provider-fixture',detailOpen:true}},setup:{
   providers:[{id:'one',module:'provider-fixture',config:{}}],providerCatalogs:{one:entry}}};
  let root;const calls=[];
  try{
   await renderAct(async()=>{root=create(React.createElement(ProviderSettings,{state,act:async(...args)=>calls.push(args)}))});
   assert.equal(root.root.findByProps({'data-action':'providers.test'}).props.disabled,true);
   assert.match(renderedText(root.toJSON()),/do not verify connectivity/);
   assert.equal(root.root.findByProps({'data-action':'providers.models'}).props.disabled,false);
   assert.equal(calls.some(([name])=>name==='providers.test'),false);
  }finally{await renderAct(async()=>root?.unmount())}
 });
}

test('opening settings loads saved configuration and keeps subsequent edits during refresh',async()=>{
 const calls=[],dispatch=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let state={view:{providerEditor:{id:'openai',module:'provider-openai'}},setup:{}},root;
 const render=()=>React.createElement(ProviderSettings,{state,session:{id:'s',workspace:'/work',status:'stopped'},act:dispatch});
 await renderAct(async()=>{root=create(render())});
 assert.equal(calls.filter(c=>c.name==='providers.list').length,1);
 assert.equal(calls.find(c=>c.name==='providers.list').args.sessionId,'s');
 state={...state,setup:{providersLoadedAt:Date.now()/1000+1,providersWorkspace:'/work',providers:[{id:'openai',module:'provider-openai',config:{default_model:'saved-model',reasoning_effort:'high'},credential:{envVar:'CUSTOM_KEY',available:true}}]}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAll(node=>['input','select'].includes(node.type)&&node.props.id==='provider-model')[0].props.value,'saved-model');
 assert.equal(root.root.findByProps({id:'provider-key-env'}).props.value,'CUSTOM_KEY');
 await renderAct(async()=>root.root.findAll(node=>['input','select'].includes(node.type)&&node.props.id==='provider-model')[0].props.onChange({target:{value:'my-edited-model'}}));
 state={...state,setup:{...state.setup,providersLoadedAt:Date.now()/1000+2}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAll(node=>['input','select'].includes(node.type)&&node.props.id==='provider-model')[0].props.value,'my-edited-model');
 await renderAct(async()=>root.unmount());
});

test('first provider hydration preserves private-only unfinished JSON without publishing it',async()=>{
 const calls=[],dispatch=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const unfinished='{"synthetic-private-edit":';
 let state={view:{providerEditor:{id:'',module:'',config:'{}',advanced:true,detailOpen:true,dirty:false}},setup:{}},root;
 const render=()=>React.createElement(ProviderSettings,{state,act:dispatch});
 await renderAct(async()=>{root=create(render())});
 try{
  const callsBefore=calls.length;
  await renderAct(async()=>root.root.findByProps({id:'provider-config'}).props.onChange({target:{value:unfinished}}));
  state={...state,setup:{providersLoadedAt:Date.now()/1000+1,providers:[{id:'one',module:'provider-test',config:{model:'first'}}]}};
  await renderAct(async()=>root.update(render()));
  assert.equal(root.root.findByProps({id:'provider-config'}).props.value,unfinished,'private-only JSON survives delayed initial hydration');
  assert.equal(root.root.findByProps({id:'provider-id'}).props.value,'','Hydration cannot select a saved provider over an unfinished new one');
  assert.equal(calls.slice(callsBefore).filter(call=>call.name==='view.update').length,0);
  assert.equal(JSON.stringify(calls).includes('synthetic-private-edit'),false);
 }finally{await renderAct(async()=>root.unmount())}
});

test('native choice edits persist in the save action; model checks have immediate progress',async()=>{
 const calls=[];let resolveModels;
 const dispatch=(name,args)=>{calls.push({name,args});return name==='providers.models'?new Promise(resolve=>{resolveModels=resolve}):Promise.resolve({accepted:true})};
 const state={view:{providerEditor:{id:'openai',module:'provider-openai',model:'model',envVar:'OPENAI_API_KEY',credentialMode:'environment',config:'{}'}},setup:{providers:[{id:'openai',module:'provider-openai'}],metadata:{'provider-openai':{info:{config_fields:[{id:'reasoning_effort',choices:['low','high'],field_type:'choice'}]}}}}};
 let root;
 await renderAct(async()=>{root=create(React.createElement(ProviderSettings,{state,act:dispatch}))});
 await renderAct(async()=>root.root.findByProps({id:'provider-option-reasoning_effort'}).props.onChange({target:{value:'high'}}));
 const button=action=>root.root.findAll(node=>node.type==='button'&&node.props['data-action']===action)[0];
 await renderAct(async()=>button('providers.save').props.onClick());
 assert.equal(calls.find(c=>c.name==='providers.save').args.config.reasoning_effort,'high');
 await renderAct(async()=>{button('providers.models').props.onClick()});
 assert.equal(button('providers.models').props.disabled,true);
 assert.match(JSON.stringify(root.toJSON()),/Discovering models/);
 await renderAct(async()=>resolveModels({accepted:true}));
 await renderAct(async()=>root.unmount());
});

test('provider test results remain clear when another action overwrites global management status',async()=>{
 const {ResultNotice}=await server.ssrLoadModule('/src/settings-ui.jsx');
 let root;
 await renderAct(async()=>{root=create(React.createElement(ResultNotice,{phase:'ready',message:'Provider check passed',detail:'32 models returned'}))});
 assert.equal(root.root.findByProps({role:'status'}).props.className,'a-check-result success');
 await renderAct(async()=>root.update(React.createElement(ResultNotice,{phase:'error',message:'Provider authentication failed'})));
 assert.equal(root.root.findByProps({role:'alert'}).props.className,'a-check-result error');
 await renderAct(async()=>root.unmount());
});

test('model selector searches only discovered models and displays an unlisted saved ID separately',async()=>{
 const {ModelSelect}=await server.ssrLoadModule('/src/model-select.jsx');
 const calls=[],changed=[],committed=[],entry={phase:'ready',models:[{id:'catalog-one',displayName:'First model'},{id:'catalog-two',displayName:'Second model'}]};
 let root,value='saved-model';
 const render=()=>React.createElement(ModelSelect,{id:'choice',label:'Model',value,entry,state:{view:{}},act:async(name,args)=>calls.push({name,args}),onChange:value=>changed.push(value),onCommit:value=>committed.push(value)});
 const values=()=>root.root.findAllByType('option').map(row=>row.props.value);
 try{
  await renderAct(async()=>root=create(render()));
  assert.equal(root.root.findAllByType('select').length,1);
  assert.equal(root.root.findByType('input').props.type,'search','A real list has search, not manual ID entry');
  assert.equal(root.root.findByType('select').props.value,'');
  assert.deepEqual(values(),['','catalog-one','catalog-two']);
  assert.match(renderedText(root.toJSON()),/Saved selection: .*saved-model.*not in the discovered list/);
  await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'Second'}}));
  assert.deepEqual(values(),['','catalog-two'],'Search matches display names as well as IDs');
  assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{settingsFilters:{'model-picker-choice':'Second'}}}});
  assert.deepEqual(changed,[]);assert.deepEqual(committed,[],'Filtering is not a selection');
  await renderAct(async()=>root.root.findByProps({'aria-label':'Clear search model'}).props.onClick());
  await renderAct(async()=>root.root.findByType('select').props.onChange({target:{value:'catalog-one'}}));
  assert.deepEqual(changed,['catalog-one']);assert.deepEqual(committed,['catalog-one']);
  value='catalog-one';await renderAct(async()=>root.update(render()));
  assert.equal(root.root.findByType('select').props.value,'catalog-one');
  await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'no match'}}));
  assert.deepEqual(values(),['']);
  assert.equal(root.root.findByType('select').props.value,'','Filtering out the selected row must not invent an option');
  assert.match(JSON.stringify(root.toJSON()),/No matches/);
  await renderAct(async()=>root.root.findByType('select').props.onChange({target:{value:''}}));
  assert.deepEqual(changed,['catalog-one']);assert.deepEqual(committed,['catalog-one']);
  await renderAct(async()=>root.root.findAllByType('button').find(node=>node.props.children==='Enter an exact model ID').props.onClick());
  const exact=root.root.find(node=>node.type==='input'&&node.props.id==='choice');
  await renderAct(async()=>exact.props.onChange({target:{value:'hidden-exact-id'}}));
  await renderAct(async()=>exact.props.onKeyDown({key:'Enter',currentTarget:{value:'hidden-exact-id'},preventDefault:()=>{}}));
  assert.deepEqual(committed,['catalog-one','hidden-exact-id']);
  assert.deepEqual(values(),[''],'Manual choice never becomes a discovered option');
  assert.match(JSON.stringify(root.toJSON()),/not verified by discovery/);
 }finally{await renderAct(async()=>root?.unmount())}
});

test('model selector retains only host-supplied refresh data and never unions earlier connection lists',async()=>{
 const {ModelSelect}=await server.ssrLoadModule('/src/model-select.jsx');
 const changed=[],committed=[],props={id:'choice',label:'Model',value:'saved-model',state:{view:{}},act:async()=>{},onChange:value=>changed.push(value),onCommit:value=>committed.push(value)};
 let root,entry={phase:'ready',models:['catalog-model'],loadedAt:1700000000};
 const render=()=>React.createElement(ModelSelect,{...props,entry});
 const values=()=>root.root.findAllByType('option').map(row=>row.props.value);
 try{
  await renderAct(async()=>root=create(render()));
  entry={...entry,phase:'working'};await renderAct(async()=>root.update(render()));
  assert.equal(root.root.findByType('select').props.disabled,false);
  assert.deepEqual(values(),['','catalog-model'],'Last successful rows are retained in the shared host entry');
  entry={...entry,phase:'error',error:'Discovery failed',stale:true};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),['','catalog-model']);
  assert.equal(root.root.findByType('select').props.id,'choice-catalog');
  assert.equal(root.root.findByType('select').props.value,'');
  assert.match(JSON.stringify(root.toJSON()),/Discovery failed/);
  assert.match(JSON.stringify(root.toJSON()),/Last successful refresh/);
  assert.match(JSON.stringify(root.toJSON()),/Model list is stale/);
  const manual=root.root.find(node=>node.type==='input'&&node.props.id==='choice');
  await renderAct(async()=>manual.props.onChange({target:{value:'new-model-id'}}));
  assert.deepEqual(committed,[],'Typing alone does not commit a manual ID');
  await renderAct(async()=>manual.props.onBlur({target:{value:' new-model-id '}}));
  assert.deepEqual(changed,['new-model-id']);assert.deepEqual(committed,['new-model-id']);
  entry={phase:'working',models:[]};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),[],'Missing rows in a different entry do not reuse the prior list');
  assert.equal(root.root.findAllByType('input').length,0);
  assert.match(JSON.stringify(root.toJSON()),/Loading provider models/);
  entry={phase:'error',models:[]};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),[]);
  assert.equal(root.root.findByType('input').props.value,'saved-model');
  entry={phase:'ready',models:['other-connection-model']};await renderAct(async()=>root.update(render()));
  assert.deepEqual(values(),['','other-connection-model']);
  assert.equal(root.root.findByType('select').props.value,'');
 }finally{await renderAct(async()=>root?.unmount())}
});

for(const [provenance,label] of [
 ['configured','Configured models — not checked against the service.'],
 ['static','Static recommendations — not a live inventory.'],
 ['simulated','Simulated models — test responses only.']
])test(provenance+' catalog stays selectable with an honest source in every refresh state',async()=>{
 const {ModelSelect}=await server.ssrLoadModule('/src/model-select.jsx');
 const committed=[];let root,entry={provenance,phase:'ready',supported:true,models:[{id:'Exact/Alias'}],loadedAt:1700000000};
 const render=()=>React.createElement(ModelSelect,{id:'origin',value:'saved-hidden',entry,state:{view:{}},act:async()=>{},onChange:()=>{},onCommit:value=>committed.push(value)});
 try{
  await renderAct(async()=>root=create(render()));
  assert.equal(root.root.findByProps({'data-model-origin':provenance}).props.children,label);
  assert.match(JSON.stringify(root.toJSON()),/not in the .*catalog/);
  assert.doesNotMatch(JSON.stringify(root.toJSON()),/Choose a discovered model/);
  await renderAct(async()=>root.root.findByType('select').props.onChange({target:{value:'Exact/Alias'}}));
  assert.deepEqual(committed,['Exact/Alias']);
  for(const patch of [{phase:'working'},{phase:'error',error:'Refresh failed',stale:true},{phase:'ready',error:null,stale:false,models:[]}]){
   entry={...entry,...patch};await renderAct(async()=>root.update(render()));
   assert.equal(root.root.findByProps({'data-model-origin':provenance}).props.children,label);
  }
  assert.equal(root.root.findAllByType('option').length,0);
  assert.equal(root.root.findByType('input').props.value,'saved-hidden');
  assert.deepEqual(committed,['Exact/Alias'],'Refresh never rewrites the saved choice');
 }finally{await renderAct(async()=>root?.unmount())}
});

for(const [name,entry,notice] of [
 ['unavailable',undefined,/Manual ID/],
 ['failed',{phase:'error',models:[]},/Model list unavailable/],
 ['unsupported',{phase:'ready',supported:false,models:[]},/does not supply verified model discovery/],
 ['empty',{phase:'ready',models:[]},/returned no models/]
])test('manual model ID remains explicit for '+name+' discovery',async()=>{
 const {ModelSelect}=await server.ssrLoadModule('/src/model-select.jsx');
 const changed=[],committed=[];let root,prevented=0;
 try{
  await renderAct(async()=>root=create(React.createElement(ModelSelect,{id:'manual-choice',label:'Model',value:'saved-model',entry,...(entry?{models:['fallback-only']}:{}),state:{view:{}},act:async()=>{},onChange:value=>changed.push(value),onCommit:value=>committed.push(value)})));
  assert.equal(root.root.findAllByType('select').length,0);
  assert.equal(root.root.findAllByType('option').length,0,'Neither saved IDs nor caller fallback rows replace an authoritative empty catalog');
  const input=root.root.findByType('input');
  assert.equal(input.props.id,'manual-choice');
  assert.equal(input.props.value,'saved-model');
  await renderAct(async()=>input.props.onBlur({target:{value:'saved-model'}}));
  assert.deepEqual(committed,[],'Focusing and leaving an inherited manual value must not create a pin');
  assert.match(JSON.stringify(root.toJSON()),notice);
  assert.match(JSON.stringify(root.toJSON()),/not verified by discovery/);
  await renderAct(async()=>input.props.onChange({target:{value:' deployment-id '}}));
  assert.deepEqual(changed,[' deployment-id ']);assert.deepEqual(committed,[]);
  await renderAct(async()=>input.props.onBlur({target:{value:'   '}}));
  await renderAct(async()=>input.props.onKeyDown({key:'Enter',currentTarget:{value:'   '},preventDefault:()=>prevented++}));
  assert.deepEqual(committed,[]);assert.equal(prevented,0);
  await renderAct(async()=>input.props.onKeyDown({key:'Enter',currentTarget:{value:' deployment-id '},preventDefault:()=>prevented++}));
  assert.deepEqual(committed,['deployment-id']);assert.equal(prevented,1);
 }finally{await renderAct(async()=>root?.unmount())}
});
