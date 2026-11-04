// Exercise the real editor component with only its visual child controls stubbed.
// Shared theme props change just as main.jsx changes them after a peer's Apply.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {build} from 'esbuild';

globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const require=createRequire(new URL('../package.json',import.meta.url));
const stubs={
 './settings-layout':`import React from 'react';export const SettingsLayoutContext=React.createContext({active:true});`,
 './settings-ui':`export const FileDrop=()=>null;`,
 './working-label':`export const WorkingLabel=({children})=>children;`,
 './theme-presentation':`export const ThemeDecorationControl=()=>null;export const useThemeScheme=scheme=>scheme==='dark'?'dark':'light';`,
 './appearance-thumbnail':`export const AppearanceThumbnail=()=>null;`,
 './execution-observability.jsx':`export const ObservabilityPreferences=()=>null;`,
 'lucide-react':`export const Check=()=>null,Download=Check,Upload=Check,Palette=Check,Sun=Check,Moon=Check,Monitor=Check,FolderOpen=Check,RefreshCw=Check,Copy=Check,LoaderCircle=Check,AlertCircle=Check;`,
};
const compiled=await build({
 stdin:{contents:`export {AppearanceSettings} from './appearance-settings.jsx';export {useAppearanceEditor,appearanceEditorKey} from './appearance-editor.jsx';export {SettingsLayoutContext} from './settings-layout';export {clearAppearanceCatalogs} from './appearance-catalog';`,resolveDir:fileURLToPath(new URL('../src/',import.meta.url))},
 bundle:true,write:false,platform:'node',format:'cjs',external:['react'],loader:{'.css':'empty'},
 plugins:[{name:'visual-children',setup(bundler){
  bundler.onResolve({filter:/.*/},args=>Object.hasOwn(stubs,args.path)?{path:args.path,namespace:'visual-child'}:undefined);
  bundler.onLoad({filter:/.*/,namespace:'visual-child'},args=>({contents:stubs[args.path],loader:'js'}));
 }}],
});
const module={exports:{}};
new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
const {AppearanceSettings,useAppearanceEditor,appearanceEditorKey,SettingsLayoutContext,clearAppearanceCatalogs}=module.exports;
test.beforeEach(()=>clearAppearanceCatalogs());
function memoryStorage(){const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value)}}
function EditorHost({storage,open=true,active=true,...props}){
 const editor=useAppearanceEditor(props.state.theme,props.defaultSkin,{storage});
 return open?React.createElement(SettingsLayoutContext.Provider,{value:{active}},React.createElement(AppearanceSettings,{...props,editor})):null;
}
const button=(root,label)=>root.findAllByType('button').find(node=>node.props['aria-label']===label||node.children.filter(child=>typeof child==='string').join('')===label);
const input=(root,id)=>root.findByProps({id});

test('saved target and dirty payload survive cross-client committed-theme prop replacement',async()=>{
 const original={id:'saved:ocean.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',description:'Original',css:'#amp-one{color:red}'};
 const calls=[];
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='theme.list')return {accepted:true,result:{items:[original],errors:[]}};
  if(action==='theme.read')return {accepted:true,result:original};
  if(action==='theme.update')return {accepted:true,result:{...original,...args,fingerprint:'c'.repeat(64)}};
  return {accepted:true};
 };
 const saved=memoryStorage(),storage=()=>saved;
 const props={state:{theme:{name:'Current',css:'#amp-one{color:black}'}},shell:{composition:{presentation:{}}},act:dispatch,storage,preview:false,setPreview:()=>{},defaultSkin:'#amp-one{}',advancedOnly:true};
 let renderer;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
  await act(async()=>{await button(renderer.root,'Preview Ocean').props.onClick()});
  await act(async()=>{await input(renderer.root,'theme-name').props.onChange({target:{value:'My edited Ocean'}})});
  await act(async()=>{await input(renderer.root,'theme-css').props.onChange({target:{value:'#amp-one{color:purple}'}})});
  await act(async()=>{await input(renderer.root,'theme-description').props.onChange({target:{value:'My local description'}})});
  const peerTheme={name:'Peer appearance',css:'#amp-one{color:blue}'};
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-name').props.value,'My edited Ocean');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'My local description');
  assert.deepEqual(calls.filter(row=>row.action==='view.update').at(-1).args.patch,{themeDraft:'#amp-one{color:purple}',themeDraftName:'My edited Ocean',themePreview:false});
  // Close/reopen unmounts Settings, but the main-held draft remains atomic.
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme},open:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  // A full tab reload restores the exact target and payload from sessionStorage.
  await act(async()=>{renderer.unmount()});
  const stored=JSON.parse(saved.getItem(appearanceEditorKey));
  assert.equal(stored.entry.id,original.id);
  assert.equal(stored.entry.fingerprint,original.fingerprint);
  assert.equal(stored.description,'My local description');
  await act(async()=>{renderer=create(React.createElement(EditorHost,{...props,state:{theme:peerTheme}}))});
  assert.equal(input(renderer.root,'theme-name').props.value,'My edited Ocean');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'My local description');
  const preview=renderer.root.findAllByType('button').find(node=>node.props['data-action']==='theme.preview'&&!node.props['aria-label']);
  await act(async()=>{await preview.props.onClick()});
  assert.deepEqual(calls.filter(row=>row.action==='theme.preview').at(-1).args,{name:'My edited Ocean',css:'#amp-one{color:purple}'});
  const update=renderer.root.findAllByType('button').find(node=>node.props['data-action']==='theme.update');
  await act(async()=>{await update.props.onClick()});
  assert.deepEqual(calls.find(row=>row.action==='theme.update').args,{
   id:original.id,expectedFingerprint:original.fingerprint,name:'My edited Ocean',
   css:'#amp-one{color:purple}',description:'My local description',
  });
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

test('stale update rejection retains all local draft fields and the original read fingerprint',async()=>{
 const original={id:'saved:ocean.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',description:'Original',css:'#amp-one{color:red}'};
 const updates=[];
 const dispatch=async(action,args)=>{
  if(action==='theme.list')return {accepted:true,result:{items:[{...original,fingerprint:'b'.repeat(64)}],errors:[]}};
  if(action==='theme.read')return {accepted:true,result:original};
  if(action==='theme.update'){updates.push(args);throw Error('This appearance changed. Read it again.')}
  return {accepted:true};
 };
 let renderer;
 const saved=memoryStorage(),storage=()=>saved;
 try{
  await act(async()=>{renderer=create(React.createElement(EditorHost,{state:{theme:{}},shell:{composition:{presentation:{}}},act:dispatch,storage,preview:false,setPreview:()=>{},defaultSkin:'#amp-one{}'}))});
  await act(async()=>{await button(renderer.root,'Preview Ocean').props.onClick()});
  for(const [id,value] of [['theme-name','Draft title'],['theme-css','#amp-one{color:purple}'],['theme-description','Draft description']]){
   await act(async()=>{await input(renderer.root,id).props.onChange({target:{value}})});
  }
  const update=()=>renderer.root.findAllByType('button').find(node=>node.props['data-action']==='theme.update');
  await act(async()=>{await update().props.onClick()});
  assert.equal(input(renderer.root,'theme-name').props.value,'Draft title');
  assert.equal(input(renderer.root,'theme-css').props.value,'#amp-one{color:purple}');
  assert.equal(input(renderer.root,'theme-description').props.value,'Draft description');
  assert.match(renderer.root.findByProps({role:'alert'}).props.children,/appearance changed/);
  await act(async()=>{await update().props.onClick()});
  assert.equal(updates.length,2);
  assert.ok(updates.every(args=>args.expectedFingerprint===original.fingerprint));
 }finally{if(renderer)await act(async()=>renderer.unmount())}
});

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const actionButton=(root,action)=>root.findAllByType('button').find(node=>node.props['data-action']===action&&!node.props['aria-label']);
const entryA={id:'saved:ocean.css',source:'saved',fingerprint:'a'.repeat(64),name:'Ocean',description:'Original',css:'#amp-one{color:red}'};
const entryB={id:'saved:forest.css',source:'saved',fingerprint:'b'.repeat(64),name:'Forest',description:'Second',css:'#amp-one{color:green}'};
async function heldEditor(operation){
 const saved=memoryStorage(),storage=()=>saved,held=deferred(),calls=[];
 const items=new Map([entryA,entryB].map(entry=>[entry.id,entry]));
 const result=operation==='save'?{...entryA,id:'saved:copy.css',fingerprint:'c'.repeat(64)}:{...entryA,fingerprint:'c'.repeat(64)};
 let shouldHold=true,nextHold;
 const dispatch=async(action,args)=>{
  calls.push({action,args});
  if(action==='theme.list')return {accepted:true,result:{items:[...items.values()],errors:[]}};
  if(action==='theme.read')return {accepted:true,result:items.get(args.id)};
  if(action==='theme.'+operation){
   const pending=shouldHold?held:nextHold;shouldHold=false;nextHold=undefined;
   const receipt=pending?await pending.promise:{accepted:true,result};
   if(receipt?.accepted){if(operation==='delete')items.delete(args.id);else items.set(receipt.result.id,receipt.result)}
   return receipt;
  }
  return {accepted:true};
 };
 const props={state:{theme:{name:'Current',css:'#amp-one{color:black}'}},shell:{composition:{presentation:{}}},act:dispatch,storage,preview:false,setPreview:()=>{},defaultSkin:'#amp-one{}'};
 let renderer;
 await act(async()=>{renderer=create(React.createElement(EditorHost,props))});
 await act(async()=>{await button(renderer.root,'Preview Ocean').props.onClick()});
 const start=async()=>{
  if(operation==='delete')await act(async()=>{button(renderer.root,'Delete Ocean').props.onClick()});
  let completion;
  await act(async()=>{completion=actionButton(renderer.root,'theme.'+operation).props.onClick()});
  assert.equal(input(renderer.root,'theme-name').props.disabled,true,'The active mount locks during its own mutation');
  return {completion};
 };
 const remount=async()=>{
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,open:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,props))});
  assert.equal(input(renderer.root,'theme-name').props.disabled,false,'A reopened editor is usable without replaying a pending mutation');
 };
 const reactivate=async()=>{
  await act(async()=>{renderer.update(React.createElement(EditorHost,{...props,active:false}))});
  await act(async()=>{renderer.update(React.createElement(EditorHost,props))});
  assert.equal(input(renderer.root,'theme-name').props.disabled,false,'Returning to an inactive Settings section releases only its abandoned local lock');
 };
 return {saved,held,calls,items,result,start,remount,reactivate,
  setActive:active=>act(async()=>renderer.update(React.createElement(EditorHost,{...props,active}))),
  holdNext(){return nextHold=deferred()},get root(){return renderer.root},close:()=>act(async()=>renderer.unmount())};
}
async function setFields(root,values){
 for(const [id,value] of Object.entries(values))await act(async()=>{await input(root,id).props.onChange({target:{value}})});
}
const newerFields={'theme-name':'Successor draft','theme-description':'Successor description','theme-css':'#amp-one{color:purple}'};

for(const operation of ['save','update','delete']){
 for(const successor of ['edit','target']){
  test(`held ${operation} completion after remount preserves successor ${successor}, fields and fingerprint`,async()=>{
   const host=await heldEditor(operation);
   try{
    const {completion}=await host.start();
    await host.remount();
    if(successor==='target')await act(async()=>{await button(host.root,'Preview Forest').props.onClick()});
    await setFields(host.root,newerFields);
    const expected=JSON.parse(host.saved.getItem(appearanceEditorKey));
    await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
    for(const [id,value] of Object.entries(newerFields))assert.equal(input(host.root,id).props.value,value);
    assert.deepEqual(JSON.parse(host.saved.getItem(appearanceEditorKey)),expected,'No late result can mark newer intent clean or change its target');
    assert.equal(input(host.root,'theme-name').props.disabled,false);
    assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,1,'Remount/completion cannot replay a mutation');
    if(operation==='delete')assert.equal(host.items.has(entryA.id),false,'The confirmed deletion still happened');
    else assert.deepEqual(host.items.get(host.result.id),host.result,'The confirmed saved result remains in the library');
   }finally{await host.close()}
  });
 }
 test(`held ${operation} can settle the unchanged persistent draft after remount`,async()=>{
  const host=await heldEditor(operation);
  try{
   const {completion}=await host.start();
   await host.remount();
   await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
   const stored=JSON.parse(host.saved.getItem(appearanceEditorKey));
   assert.equal(stored.entry?.id||null,operation==='delete'?null:host.result.id);
   assert.equal(stored.entry?.fingerprint||null,operation==='delete'?null:host.result.fingerprint);
   assert.equal(stored.dirty,operation==='delete');
   assert.equal(input(host.root,'theme-name').props.disabled,false);
  }finally{await host.close()}
 });
 for(const outcome of ['rejected','missing','thrown']){
  test(`${operation} ${outcome} completion preserves draft and releases its lock without retry`,async()=>{
   const host=await heldEditor(operation);
   try{
    const expected=host.saved.getItem(appearanceEditorKey),{completion}=await host.start();
    await act(async()=>{
     if(outcome==='thrown')host.held.reject(Error('Mutation unavailable'));
     else host.held.resolve(outcome==='missing'?undefined:{accepted:false,error:'Mutation rejected'});
     await completion;
    });
    assert.equal(host.saved.getItem(appearanceEditorKey),expected,'Failure or unknown completion is not a confirmed save/delete');
    assert.equal(input(host.root,'theme-name').props.disabled,false);
    assert.equal(host.root.findAllByProps({role:'alert'}).length,1,'Failure has visible feedback');
    assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,1);
    await setFields(host.root,newerFields);
    // A deliberate retry is possible after settlement, but never automatic.
    if(operation==='delete')await act(async()=>{button(host.root,'Delete Ocean').props.onClick()});
    await act(async()=>{await actionButton(host.root,'theme.'+operation).props.onClick()});
    assert.equal(host.calls.filter(call=>call.action==='theme.'+operation).length,2);
    assert.equal(input(host.root,'theme-name').props.disabled,false);
   }finally{await host.close()}
  });
 }
}

for(const outcome of ['success','failure']){
 test(`late ${outcome} from an old mount cannot settle a successor operation's lock or draft`,async()=>{
  const host=await heldEditor('save');
  try{
   const {completion}=await host.start();
   await host.remount();
   await setFields(host.root,newerFields);
   const expected=host.saved.getItem(appearanceEditorKey),newSave=host.holdNext();
   let newCompletion;
   await act(async()=>{newCompletion=actionButton(host.root,'theme.save').props.onClick()});
   assert.equal(input(host.root,'theme-name').props.disabled,true);
   await act(async()=>{
    if(outcome==='failure')host.held.reject(Error('Old mutation unavailable'));
    else host.held.resolve({accepted:true,result:host.result});
    await completion;
   });
   assert.equal(host.saved.getItem(appearanceEditorKey),expected);
   assert.equal(host.root.findAllByProps({role:'alert'}).length,0);
   assert.equal(input(host.root,'theme-name').props.disabled,true,'Only the successor receipt releases its submission lock');
   const result={...entryB,name:newerFields['theme-name'],css:newerFields['theme-css'],description:newerFields['theme-description']};
   await act(async()=>{newSave.resolve({accepted:true,result});await newCompletion});
   assert.equal(input(host.root,'theme-name').props.disabled,false);
   assert.equal(JSON.parse(host.saved.getItem(appearanceEditorKey)).entry.id,result.id);
   assert.equal(host.calls.filter(call=>call.action==='theme.save').length,2);
  }finally{await host.close()}
 });
}

test('changing a draft away and back still supersedes the earlier mutation revision',async()=>{
 const host=await heldEditor('save');
 try{
  await setFields(host.root,{'theme-description':'Already dirty before submission'});
  const submitted=host.saved.getItem(appearanceEditorKey);
  const {completion}=await host.start();
  await host.remount();
  await setFields(host.root,{'theme-name':'Temporary title'});
  await setFields(host.root,{'theme-name':entryA.name});
  const expected=host.saved.getItem(appearanceEditorKey);
  assert.equal(expected,submitted,'Every field, target, fingerprint and dirty flag is equal again; only the revision changed');
  await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
  assert.equal(host.saved.getItem(appearanceEditorKey),expected,'Compare the draft revision, not merely equal field values');
 }finally{await host.close()}
});

test('a new read fingerprint on the same target supersedes the held update result',async()=>{
 const host=await heldEditor('update');
 try{
  const {completion}=await host.start();
  await host.remount();
  host.items.set(entryA.id,{...entryA,fingerprint:'d'.repeat(64)});
  await act(async()=>{await button(host.root,'Preview Ocean').props.onClick()});
  const expected=host.saved.getItem(appearanceEditorKey);
  await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
  assert.equal(host.saved.getItem(appearanceEditorKey),expected);
  assert.equal(JSON.parse(expected).entry.fingerprint,'d'.repeat(64));
 }finally{await host.close()}
});

test('section deactivation releases abandoned UI ownership without letting its late receipt unlock a new operation',async()=>{
 const host=await heldEditor('save');
 try{
  const {completion}=await host.start();
  await host.reactivate();
  await setFields(host.root,newerFields);
  const expected=host.saved.getItem(appearanceEditorKey),next=host.holdNext();
  let nextCompletion;
  await act(async()=>{nextCompletion=actionButton(host.root,'theme.save').props.onClick()});
  await act(async()=>{host.held.resolve({accepted:true,result:host.result});await completion});
  assert.equal(host.saved.getItem(appearanceEditorKey),expected);
  assert.equal(input(host.root,'theme-name').props.disabled,true);
  await act(async()=>{next.reject(Error('Successor save unavailable'));await nextCompletion});
  assert.equal(input(host.root,'theme-name').props.disabled,false);
  assert.match(host.root.findByProps({role:'alert'}).props.children,/Successor save unavailable/);
 }finally{await host.close()}
});

test('a failure arriving on an inactive section remains visible for the unchanged draft on return',async()=>{
 const host=await heldEditor('save');
 try{
  const expected=host.saved.getItem(appearanceEditorKey),{completion}=await host.start();
  await host.setActive(false);
  await act(async()=>{host.held.reject(Error('Inactive save failed'));await completion});
  await host.setActive(true);
  assert.equal(host.saved.getItem(appearanceEditorKey),expected);
  assert.equal(input(host.root,'theme-name').props.disabled,false);
  assert.match(host.root.findByProps({role:'alert'}).props.children,/Inactive save failed/);
 }finally{await host.close()}
});

for(const operation of ['save','update','delete']){
 for(const successor of ['unchanged','edit','operation']){
  test(`${operation} failure after section return respects ${successor} feedback ownership`,async()=>{
   const host=await heldEditor(operation);
   try{
    const {completion}=await host.start();
    await host.reactivate();
    if(successor==='edit')await setFields(host.root,newerFields);
    const expected=host.saved.getItem(appearanceEditorKey);
    let next,nextCompletion;
    if(successor==='operation'){
     next=host.holdNext();
     await act(async()=>{
      if(operation==='delete')button(host.root,'Delete Ocean').props.onClick();
     });
     await act(async()=>{nextCompletion=actionButton(host.root,'theme.'+operation).props.onClick()});
    }
    await act(async()=>{host.held.reject(Error('Returned section mutation failed'));await completion});
    assert.equal(host.saved.getItem(appearanceEditorKey),expected);
    const alerts=host.root.findAllByProps({role:'alert'});
    if(successor==='unchanged')assert.match(alerts[0]?.props.children||'',/Returned section mutation failed/);
    else assert.equal(alerts.length,0,'Superseded intent owns its own failure feedback');
    assert.equal(input(host.root,'theme-name').props.disabled,successor==='operation');
    if(next){
     await act(async()=>{next.reject(Error('Successor mutation failed'));await nextCompletion});
     assert.match(host.root.findByProps({role:'alert'}).props.children,/Successor mutation failed/);
     assert.equal(input(host.root,'theme-name').props.disabled,false);
    }
    assert.equal(host.calls.filter(row=>row.action==='theme.'+operation).length,successor==='operation'?2:1);
   }finally{await host.close()}
  });
 }
}

for(const simple of [false,true]){
 test(`selection reports ${simple?'Apply':'preview'} failure after its own draft replacement`,async()=>{
  const saved=memoryStorage(),storage=()=>saved,calls=[];
  const dispatch=async(action,args)=>{
   calls.push({action,args});
   if(action==='theme.list')return {accepted:true,result:{items:[entryA],errors:[]}};
   if(action==='theme.read')return {accepted:true,result:entryA};
   if(action===(simple?'theme.apply':'theme.preview'))throw Error('Selected appearance failed');
   return {accepted:true};
  };
  let renderer;
  try{
   await act(async()=>{renderer=create(React.createElement(EditorHost,{
    state:{theme:{name:'Current',css:'#amp-one{color:black}'}},
    shell:{composition:{presentation:{}}},act:dispatch,storage,simple,
    preview:false,setPreview:()=>{},defaultSkin:'#amp-one{}',
   }))});
   await act(async()=>{await button(renderer.root,(simple?'Use ':'Preview ')+entryA.name).props.onClick()});
   assert.match(renderer.root.findByProps({role:'alert'}).props.children,/Selected appearance failed/);
   assert.equal(JSON.parse(saved.getItem(appearanceEditorKey)).entry.id,entryA.id);
   assert.equal(button(renderer.root,(simple?'Use ':'Preview ')+entryA.name).props.disabled,false);
   assert.equal(calls.filter(row=>row.action===(simple?'theme.apply':'theme.preview')).length,1);
  }finally{if(renderer)await act(async()=>renderer.unmount())}
 });
}