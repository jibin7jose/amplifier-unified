import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {useWorkspaceChats,WorkspaceChatChoices}=await server.ssrLoadModule('/src/workspace-chat-choices.jsx');
const {WorkspaceResources}=await server.ssrLoadModule('/src/workspace-resource-inventory.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const originalFetch=globalThis.fetch,workspace={id:'w',path:'/exact/workspace'};
let choices;
function Picker(props){choices=useWorkspaceChats(props.workspace,props.active,props.preferredId);return React.createElement(WorkspaceChatChoices,{choices,id:'owner',label:'Owner'})}
const response=(items,nextOffset=null)=>new Response(JSON.stringify({items,nextOffset}));
test.after(()=>server.close());test.afterEach(()=>{globalThis.fetch=originalFetch});

test('collapsed picker is passive and late aborted data never fills a closed or different workspace',async()=>{
 let root;const requests=[];
 globalThis.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
 try{
  await act(async()=>{root=create(React.createElement(Picker,{workspace,active:false}))});assert.equal(requests.length,0);
  await act(async()=>root.update(React.createElement(Picker,{workspace,active:true})));
  const query=new URL(requests[0].url,'http://fixture').searchParams;
  assert.deepEqual(Object.fromEntries(query),{workspace:'/exact/workspace',kind:'root',offset:'0',limit:'50'});
  assert.match(JSON.stringify(root.toJSON()),/Loading workspace chats/);assert.doesNotMatch(JSON.stringify(root.toJSON()),/No chats in/);
  await act(async()=>root.update(React.createElement(Picker,{workspace,active:false})));
  assert.equal(requests[0].options.signal.aborted,true);
  await act(async()=>requests[0].resolve(response([{id:'old',title:'STALE'}])));
  assert.deepEqual(choices.options,[]);
  const next={id:'new',path:'/different'};
  await act(async()=>root.update(React.createElement(Picker,{workspace:next,active:true})));
  await act(async()=>requests[1].resolve(response([{id:'new-owner',title:'Current'}])));
  assert.equal(choices.selectedId,'new-owner');assert.doesNotMatch(JSON.stringify(root.toJSON()),/STALE/);
 }finally{await act(async()=>root.unmount())}
});

test('paging keeps one explicit owner without silently selecting a new page or refresh result',async()=>{
 let root;const requests=[];
 globalThis.fetch=async url=>{requests.push(new URL(url,'http://fixture').searchParams);return requests.length===1?response([{id:'a',title:'A'},{id:'b',title:'B'}],50):response(Array.from({length:50},(_,i)=>({id:'c'+i,title:'Page '+i})),100)};
 try{
  await act(async()=>{root=create(React.createElement(Picker,{workspace,active:true,preferredId:'a'}))});
  await act(async()=>choices.select('b'));assert.equal(choices.selectedId,'b');
  await act(async()=>choices.more());assert.equal(requests[1].get('offset'),'50');
  assert.equal(choices.selectedId,'b');assert.equal(choices.options.length,51);assert.equal(choices.options[0].title,'B');
  await act(async()=>choices.select('c12'));
  await act(async()=>choices.first());assert.equal(requests[2].get('offset'),'0');assert.equal(choices.selectedId,'c12');
  globalThis.fetch=async()=>new Response(JSON.stringify({error:'Catalog unavailable'}),{status:503});
  await act(async()=>choices.first());assert.equal(choices.selectedId,'c12');assert.match(JSON.stringify(root.toJSON()),/Catalog unavailable/);assert.doesNotMatch(JSON.stringify(root.toJSON()),/No chats in/);
 }finally{await act(async()=>root.unmount())}
});

test('empty is shown only after an authoritative successful page, and a new workspace clears the old owner',async()=>{
 let root;globalThis.fetch=async()=>response([{id:'owner',title:'Old owner'}]);
 try{
  await act(async()=>{root=create(React.createElement(Picker,{workspace,active:true}))});assert.equal(choices.selectedId,'owner');
  globalThis.fetch=async()=>response([]);
  await act(async()=>root.update(React.createElement(Picker,{workspace:{id:'other',path:'/other'},active:true})));
  assert.equal(choices.selectedId,undefined);assert.deepEqual(choices.options,[]);assert.match(JSON.stringify(root.toJSON()),/No chats in this workspace/);
 }finally{await act(async()=>root.unmount())}
});


test('visited workspace resource forms keep private edits while hidden and stop picker reads',async()=>{
 let root;const requests=[],calls=[],priorWindow=globalThis.window;
 globalThis.window={addEventListener(){},removeEventListener(){}};
 globalThis.fetch=async url=>{requests.push(url);return response([{id:'owner',title:'Owner'}])};
 const state={workspaces:[workspace],selectedSessionId:'owner'},dispatch=async(name,args)=>{
  calls.push([name,args]);return {accepted:true,result:name==='workspace.resources.list'?{resources:[]}:{items:[],nextOffset:null}};
 };
 const element=id=>React.createElement(WorkspaceResources,{state,act:dispatch,workspaceId:id});
 try{
  await act(async()=>{root=create(element(undefined))});assert.equal(calls.length,0);assert.equal(requests.length,0);
  await act(async()=>root.update(element(workspace.id)));
  const panel=root.root.findByProps({'aria-label':'Resource reservations'}),details=panel.parent;
  await act(async()=>details.props.onToggle({currentTarget:{open:true}}));
  const input=panel.findByProps({placeholder:'preview:my-project'});
  await act(async()=>input.props.onChange({target:{value:'Private reservation draft'}}));
  const queries=requests.length,reads=calls.length;
  await act(async()=>root.update(element(undefined)));
  assert.equal(root.root.findByProps({'aria-label':'Resource reservations'}).findByProps({placeholder:'preview:my-project'}).props.value,'Private reservation draft');
  assert.equal(requests.length,queries);assert.equal(calls.length,reads);
  assert.equal(root.root.findAll(node=>node.type==='div'&&node.props.hidden===true).length,1);
  await act(async()=>root.update(element(workspace.id)));
  assert.equal(root.root.findByProps({'aria-label':'Resource reservations'}).findByProps({placeholder:'preview:my-project'}).props.value,'Private reservation draft');
  assert.equal(requests.length,queries+1);
 }finally{await act(async()=>root?.unmount());globalThis.window=priorWindow}
});

test('resource drafts and late save receipts stay with their registered workspace',async()=>{
 let root,finishSave;const calls=[],priorWindow=globalThis.window,other={id:'other',name:'Other',path:'/other'};
 globalThis.window={addEventListener(){},removeEventListener(){}};
 const dispatch=async(name,args)=>{
  calls.push([name,args]);
  if(name==='workspace.resources.add')await new Promise(resolve=>{finishSave=resolve});
  return {accepted:true,result:name==='workspace.resources.list'?{resources:[]}:{items:[],nextOffset:null}};
 };
 const element=(row,unavailableId)=>React.createElement(WorkspaceResources,{state:{workspaces:row?[row]:[]},act:dispatch,workspaceId:row?.id,unavailableId});
 const editors=()=>root.root.findAllByProps({'aria-label':'Workspace resources'});
 const editor=id=>editors().find(node=>node.findAllByType('h3')[0].children.join('').includes(id));
 const click=async(node,label)=>act(async()=>node.findAllByType('button').find(button=>button.children.join('')===label).props.onClick());
 const fill=async(node,value)=>act(async()=>node.findAllByType('input')[1].props.onChange({target:{value}}));
 const value=node=>node.findAllByType('input')[1]?.props.value;
 try{
  await act(async()=>{root=create(element({...workspace,name:'First'}))});
  await click(editor('First'),'Record resource');await fill(editor('First'),'first draft');
  await act(async()=>editor('First').findByType('form').props.onSubmit({preventDefault(){}}));
  assert.ok(finishSave);assert.equal(calls.filter(([name])=>name==='workspace.resources.add').length,1);
  await act(async()=>root.update(element(other)));
  await click(editor('Other'),'Record resource');await fill(editor('Other'),'other draft');
  await act(async()=>finishSave());
  assert.equal(value(editor('Other')),'other draft');
  assert.equal(editor('First').findAllByType('form').length,0,'only the initiating form clears after its receipt');
  assert.equal(calls.find(([name])=>name==='workspace.resources.add')[1].workspaceId,workspace.id);
  await act(async()=>root.update(element({...workspace,name:'First'})));
  assert.equal(value(editor('Other')),'other draft','bounded registration absence does not discard another workspace');
  await act(async()=>root.update(element(other)));
  await click(editor('Other'),'Cancel resource');await click(editor('Other'),'Record resource');assert.equal(value(editor('Other')),'');
  await fill(editor('Other'),'must not follow replacement');
  await act(async()=>root.update(element({...other,path:'/replacement'})));
  assert.equal(editor('Other').findAllByType('form').length,0,'replacement path cannot inherit the former editor');
  await click(editor('Other'),'Record resource');await fill(editor('Other'),'removed registration draft');
  await act(async()=>root.update(element(null,other.id)));
  assert.equal(editors().some(node=>node.findAllByType('h3')[0].children.join('').includes('Other')),false);
  await act(async()=>root.update(element({...other,path:'/replacement'})));
  assert.equal(editor('Other').findAllByType('form').length,0,'explicit unavailability discards the retired registration');
  assert.equal(calls.filter(([name])=>name==='workspace.resources.add').length,1,'navigation never replays saves');
 }finally{await act(async()=>root?.unmount());globalThis.window=priorWindow}
});
