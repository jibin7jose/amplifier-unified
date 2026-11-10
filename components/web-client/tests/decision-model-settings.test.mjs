import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {DecisionModelSettings}=await vite.ssrLoadModule('/src/decision-model-settings.jsx');
const {AIConnections}=await vite.ssrLoadModule('/src/ai-connections.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalFetch=globalThis.fetch;
const support={hostInstanceId:'host',featureSetup:{supported:true,features:[{id:'decisions-jev',label:'Jev decisions',installed:true,providerModule:'provider-jev'}]}};
test.beforeEach(()=>{globalThis.fetch=async()=>new Response(JSON.stringify(support))});
test.after(()=>{globalThis.fetch=originalFetch;return vite.close()});
const empty={catalog:[],services:[{module:'provider-jev',name:'Jev',decisionOnly:true,auth:'key'}],selection:{},backends:{}};
const available={...empty,catalog:[{id:'my-jev',module:'provider-jev',label:'Jev · my-jev',default_model:'jev-test'}]};
const button=(root,text)=>root.root.findAllByType('button').find(node=>node.children.join('')===text);
async function click(root,text){const node=button(root,text);assert.ok(node,text);assert.ok(!node.props.disabled,text);await act(async()=>node.props.onClick())}

test('fresh settings expose a canonical connection action without installing, saving, or probing a model',async()=>{
 let root,calls=[];
 const state={view:{},setup:{providers:[],providersLoadedAt:1,decisionModels:empty}};
 try{
  await act(async()=>{root=create(React.createElement(AIConnections,{state,navigate:()=>{},act:async(...args)=>{calls.push(args);return {accepted:true,result:{editor:{id:'new-jev'},credentialCheck:{action:'providers.credentials',operationId:'check'}}}}}))});
  const section=root.root.findAllByType('details').find(node=>node.findAllByType('summary').some(summary=>summary.children.join('')==='Decision models (optional)'));
  await act(async()=>section.props.onToggle({currentTarget:{open:true}}));
  assert.equal(root.root.findByProps({id:'decision-purpose'}).props.value,'both');
  assert.equal(root.root.findByProps({value:'decision'}).props.disabled,true);
  assert.match(root.root.findByProps({value:'decision'}).children.join(''),/connect a provider first/);
  await click(root,'Connect Jev');
  assert.deepEqual(calls.filter(([action])=>action.startsWith('settings.connection.')),[['settings.connection.select',{scope:'global',module:'provider-jev'}]]);
  assert.ok(calls.every(([action])=>!['providers.save','providers.models','providers.login','updates.featureInstall'].includes(action)));
 }finally{await act(async()=>root?.unmount())}
});

test('pending and failed reads explain unavailable choices and support retry without reconnecting',async()=>{
 let root,calls=[],reject=false;
 let state={setup:{decisionModels:empty,operations:{'providers.decisionModels:':{phase:'working',commandId:'initial'}}}};
 const render=()=>React.createElement(DecisionModelSettings,{state,navigate:()=>{},onConnect:()=>{throw Error('must not reconnect')},act:async(...args)=>{calls.push(args);if(reject)throw Error('Connection interrupted');return {accepted:true,operationId:'retry'}}});
 try{
  await act(async()=>{root=create(render())});
  assert.match(root.root.findByProps({value:'decision'}).children.join(''),/loading choices/);
  assert.equal(root.root.findAllByProps({'data-part':'decision-connect'}).length,0);
  assert.equal(button(root,'Refresh choices').props.disabled,true);
  state={setup:{...state.setup,operations:{'providers.decisionModels:':{phase:'error',commandId:'initial',error:'Read failed'}}}};
  await act(async()=>root.update(render()));
  assert.ok(JSON.stringify(root.toJSON()).includes('Read failed'));
  reject=true;
  await click(root,'Refresh choices');
  assert.ok(JSON.stringify(root.toJSON()).includes('Connection interrupted'));
  assert.equal(button(root,'Refresh choices').props.disabled,false);
  reject=false;await click(root,'Refresh choices');
  state={setup:{decisionModels:available,operations:{'providers.decisionModels:':{phase:'ready',commandId:'retry'}}}};
  await act(async()=>root.update(render()));
  assert.equal(root.root.findByProps({value:'decision'}).props.disabled,false);
  assert.equal(root.root.findAllByProps({role:'alert'}).length,0);
  assert.deepEqual(calls,[['providers.decisionModels',{}],['providers.decisionModels',{}],['providers.decisionModels',{}]]);
 }finally{await act(async()=>root?.unmount())}
});

test('saved choices remain usable during refresh and optional metadata errors remain visible',async()=>{
 let root;
 let state={setup:{decisionModels:available,operations:{'providers.decisionModels:':{phase:'working'}}}};
 const render=()=>React.createElement(DecisionModelSettings,{state,navigate:()=>{},act:async()=>({accepted:true})});
 try{
  await act(async()=>{root=create(render())});
  assert.equal(root.root.findByProps({value:'decision'}).props.disabled,false);
  state={setup:{decisionModels:available,decisionModelsError:'Refresh choices to try again.'}};
  await act(async()=>root.update(render()));
  assert.ok(JSON.stringify(root.toJSON()).includes('Could not load decision model choices.'));
  assert.equal(button(root,'Refresh choices').props.disabled,false);
  assert.equal(root.root.findByProps({value:'decision'}).props.disabled,false);
 }finally{await act(async()=>root?.unmount())}
});

test('both-purpose save is one explicit request with consent and the existing account',async()=>{
 let root,calls=[];
 try{
  await act(async()=>{root=create(React.createElement(DecisionModelSettings,{state:{setup:{decisionModels:available}},navigate:()=>{},act:async(...args)=>{calls.push(args);return {accepted:true,operationId:'save'}}}))});
  await act(async()=>root.root.findByProps({id:'decision-mode'}).props.onChange({target:{value:'decision'}}));
  assert.equal(root.root.findByProps({id:'decision-service'}).props.value,'my-jev');
  assert.equal(root.root.findByProps({id:'decision-model'}).props.value,'jev-test');
  assert.equal(button(root,'Save selection').props.disabled,true);
  assert.equal(root.root.findByProps({id:'decision-purpose'}).props.disabled,true,'a dirty selection cannot silently move to another purpose');
  await act(async()=>root.root.findByProps({type:'checkbox'}).props.onChange({target:{checked:true}}));
  await click(root,'Save selection');
  assert.deepEqual(calls.filter(([action])=>action==='providers.configureDecisions'),[['providers.configureDecisions',{purpose:'both',mode:'decision',scope:'global',backend:{provider:'my-jev',model:'jev-test'},allowExternalState:true}]]);
 }finally{await act(async()=>root?.unmount())}
});

test('different saved choices remain visible and applying one to both requires renewed consent',async()=>{
 let root,calls=[];
 const data={...available,selection:{browser:{mode:'decision',backend:'browser',allow_external_state:true},computer:{mode:'standard'}},backends:{browser:{provider:'my-jev',model:'jev-test'}}};
 try{
  await act(async()=>{root=create(React.createElement(DecisionModelSettings,{state:{setup:{decisionModels:data}},navigate:()=>{},act:async(...args)=>{calls.push(args);return {accepted:true,operationId:'save'}}}))});
  assert.equal(root.root.findByProps({id:'decision-purpose'}).props.value,'browser');
  assert.deepEqual(root.root.findByProps({'data-part':'decision-current'}).findAllByType('dd').map(node=>node.children.join('')),['Jev · my-jev · jev-test','Ordinary model']);
  await act(async()=>root.root.findByProps({id:'decision-purpose'}).props.onChange({target:{value:'both'}}));
  assert.equal(root.root.findByProps({type:'checkbox'}).props.checked,false);
  assert.equal(button(root,'Save selection').props.disabled,true);
  assert.ok(JSON.stringify(root.toJSON()).includes('Saving will replace both existing selections.'));
  await click(root,'Discard changes');
  await act(async()=>root.root.findByProps({id:'decision-model'}).props.onChange({target:{value:'another-model'}}));
  assert.equal(root.root.findByProps({type:'checkbox'}).props.checked,false,'discarding a combined edit cannot copy single-purpose consent to both');
  assert.equal(button(root,'Save selection').props.disabled,true);
  assert.ok(calls.every(([action])=>action!=='providers.configureDecisions'));
 }finally{await act(async()=>root?.unmount())}
});
