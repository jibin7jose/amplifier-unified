import {shellFor} from './shell-host.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act as renderAct} from 'react';
import {create} from 'react-test-renderer';
import {createServer} from 'vite';
const server=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {ChatDetails,WorkspaceManager}=await server.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceRail:Rail,ChatRename,AgentCanvas,CanvasToggle,A2UISurface,reopenCanvas,SessionHistoryControls}=await server.ssrLoadModule('/src/shell-panels.jsx');
const {WorkHeader}=await server.ssrLoadModule('/src/work-shell.jsx');
const {CanvasDocumentRefresh}=await server.ssrLoadModule('/src/canvas-controls.jsx');
const {WorkNavigationContext}=await server.ssrLoadModule('/src/work-navigation.js');
function WorkspaceRail(props){return React.createElement(Rail,{...props,shell:shellFor(props.state,props.act)})}
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test('document Refresh reads saved state only and ignores an obsolete response',async()=>{
 let root,resolve,reject,completion,calls=[];
 let view={viewId:'primary',resourceId:'one',resourceRevision:'body-one',selectedVersion:1,latestVersion:2};
 const dispatch=(name,args)=>{calls.push([name,args]);return new Promise((yes,no)=>{resolve=yes;reject=no})};
 const render=()=>React.createElement(CanvasDocumentRefresh,{view,dispatch});
 await renderAct(async()=>{root=create(render())});
 const button=()=>root.root.findByProps({'aria-label':'Refresh document'});
 const text=()=>JSON.stringify(root.toJSON());
 assert.match(text(),/Historical · Version 2 available/);
 await renderAct(async()=>{completion=button().props.onClick()});
 assert.equal(button().props['aria-disabled'],true);assert.match(text(),/Checking saved content/);
 await renderAct(async()=>{await button().props.onClick()});
 assert.deepEqual(calls,[['canvas.views.inspect',{}]],'No duplicate, reload, selection or source-file action');
 view={...view,resourceId:'two',selectedVersion:null,latestVersion:1};await renderAct(async()=>root.update(render()));
 await renderAct(async()=>{resolve({result:{views:[{...view,resourceId:'one',selectedVersion:1}]}});await completion});
 assert.doesNotMatch(text(),/Checked|Could not refresh/);
 await renderAct(async()=>{completion=button().props.onClick()});
 await renderAct(async()=>{reject(Error('Offline'));await completion});
 assert.match(text(),/Could not refresh: Offline/);assert.equal(button().props['aria-disabled'],false);
 await renderAct(async()=>{completion=button().props.onClick()});
 await renderAct(async()=>{resolve({result:{views:[view]}});await completion});
 assert.match(text(),/Checked · Latest · Version 1 · Current saved content/);
 await renderAct(async()=>{completion=button().props.onClick()});
 view={...view,resourceRevision:'body-two',latestVersion:2};await renderAct(async()=>root.update(render()));
 assert.equal(button().props['aria-disabled'],true);assert.match(text(),/Checking saved content/);
 await renderAct(async()=>{reject(Error('Disconnected after update'));await completion});
 assert.match(text(),/Disconnected after update/);assert.equal(button().props['aria-disabled'],false);
 await renderAct(async()=>{completion=button().props.onClick()});
 await renderAct(async()=>{resolve({result:{views:[view]}});await completion});
 assert.match(text(),/Checked · Latest · Version 2/);
 await renderAct(async()=>root.unmount());
});
test.after(()=>server.close());
test('retained executable preview labels running version and loads only the clicked exact revision',async()=>{
 let root,resolve,completion;
 const calls=[],view={viewId:'primary',resourceId:'html-one',resourceRevision:'v1-body',generation:2,selectedVersion:null,runningVersion:1,latestVersion:2,previewRetained:true};
 const dispatch=(name,args)=>{calls.push({name,args});return new Promise(done=>{resolve=done})};
 const render=()=>React.createElement(CanvasDocumentRefresh,{view,dispatch});
 await renderAct(async()=>{root=create(render())});
 assert.match(JSON.stringify(root.toJSON()),/Viewing v1 · v2 available · preview retained/);
 const button=()=>root.root.findByProps({className:'a-soft a-canvas-load-version'});
 await renderAct(async()=>{completion=button().props.onClick()});
 view.latestVersion=3;await renderAct(async()=>root.update(render()));
 await renderAct(async()=>button().props.onClick());
 assert.deepEqual(calls,[{name:'canvas.views.loadVersion',args:{viewId:'primary',resourceId:'html-one',resourceRevision:'v1-body',generation:2,version:2}}]);
 await renderAct(async()=>{resolve({accepted:true});await completion});
 await renderAct(async()=>root.unmount());
});
for(const outcome of ['accepted-read-failed','unknown','refused'])test('Canvas Load feedback preserves '+outcome+' separately from readback',async()=>{
 let root,loads=0,reads=0,failRead=true;
 const view={viewId:'primary',resourceId:'feedback-html',resourceRevision:'v1',generation:4,selectedVersion:null,runningVersion:1,latestVersion:2,previewRetained:true};
 const accepted={accepted:true,result:{...view,runningVersion:2,resourceRevision:'v2',generation:5},stateConfirmation:{status:'unconfirmed',message:'read unavailable'}};
 const dispatch=async name=>{
  if(name==='canvas.views.inspect'){reads++;if(failRead)throw Error('read unavailable');return {accepted:true,result:{views:[view]}}}
  loads++;
  if(outcome==='accepted-read-failed')return accepted;
  throw Object.assign(Error(outcome==='refused'?'unfinished edits':'connection lost'),{unconfirmed:outcome==='unknown'});
 };
 await renderAct(async()=>{root=create(React.createElement(CanvasDocumentRefresh,{view,dispatch}))});
 const load=()=>root.root.findByProps({className:'a-soft a-canvas-load-version'}),refresh=()=>root.root.findByProps({'aria-label':'Refresh document'});
 const text=()=>JSON.stringify(root.toJSON());
 await renderAct(async()=>load().props.onClick());
 if(outcome==='refused'){
  assert.match(text(),/unfinished edits.*current preview is retained/);assert.equal(load().props['aria-disabled'],false);
 }else{
  assert.match(text(),outcome==='unknown'?/completion is unconfirmed/:/Load v2 was accepted/);
  assert.doesNotMatch(text(),/preview retained|preview is retained/);
  assert.equal(load().props['aria-disabled'],true);
  await renderAct(async()=>load().props.onClick());assert.equal(loads,1);
  await renderAct(async()=>refresh().props.onClick());
  assert.doesNotMatch(text(),/open document is retained/);assert.equal(load().props['aria-disabled'],true);
  failRead=false;await renderAct(async()=>refresh().props.onClick());
  assert.equal(loads,1);assert.equal(reads,2);assert.equal(load().props['aria-disabled'],false);
 }
 await renderAct(async()=>root.unmount());
});
const initial=()=>({view:{navExpanded:true,navSimple:false},workspaceExplorer:{path:'/',parentPath:null,breadcrumbs:[{name:'/',path:'/'}],filter:'',page:1,pages:1,totalWorkspaces:2,rows:[{path:'/one',name:'one',workspaceId:'one',chatCount:2,canBrowse:false,unread:0},{path:'/two',name:'two',workspaceId:'two',chatCount:1,canBrowse:false,unread:0}]},workspaces:[{id:'one',name:'One',path:'/one',available:true},{id:'two',name:'Two',path:'/two',available:true}],selectedWorkspaceId:'one',sessions:[{id:'a',title:'First plan',workspace:'/one'},{id:'b',title:'Another plan',workspace:'/one'},{id:'c',title:'Other workspace',workspace:'/two'}]});

test('workspace drill-in filters independently of Recent',async()=>{
 const state=initial();state.view.navWorkspaceList=false;state.view.navFilter='First*';let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 const workspace=root.root.findByProps({'aria-label':'Chats in One'});
 assert.deepEqual(workspace.findAll(node=>node.props.className==='a-nav-chat-select').map(n=>n.props['aria-label']),['First plan']);
 assert.equal(root.root.findByProps({'data-sidebar-section':'recent'}).findAllByProps({className:'a-nav-chat-select'}).length,3);
 await renderAct(async()=>root.unmount());
});

test('three sections collapse independently without changing drafts, selection or folder browsing',async()=>{
 const state=initial(),calls=[];state.view.navWorkspacePath='/saved/folder';state.view.draft='Unsent message';
 state.pinnedSessionIds=['a'];
 const act=async(name,args)=>{calls.push({name,args});if(name==='view.update')state.view={...state.view,...args.patch};return {accepted:true}};
 let root;const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({'aria-label':'Chat view'}).length,0);
 const section=id=>root.root.findByProps({'data-sidebar-section':id});
 for(const id of ['pinned','workspaces','recent']){
  await renderAct(async()=>section(id).findAllByType('button')[0].props.onClick());
  await renderAct(async()=>root.update(render()));
  assert.equal(section(id).findAllByType('button')[0].props['aria-expanded'],false);
 }
 assert.deepEqual(state.view.navSectionsCollapsed,['pinned','workspaces','recent']);
 await renderAct(async()=>section('workspaces').findAllByType('button')[0].props.onClick());
 await renderAct(async()=>root.update(render()));
 assert.equal(section('workspaces').findAllByType('button')[0].props['aria-expanded'],true);
 assert.equal(section('recent').findAllByType('button')[0].props['aria-expanded'],false);
 assert.equal(state.view.navWorkspacePath,'/saved/folder');assert.equal(state.view.draft,'Unsent message');
 assert.ok(calls.every(call=>call.name==='view.update'));
 await renderAct(async()=>root.root.findByProps({'aria-label':'New chat'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='session.draft'));
 await renderAct(async()=>root.unmount());
});

test('pin and unpin use shared actions without selecting chats or rewriting activity',async()=>{
 const state=initial(),calls=[];state.view.navChatScope='all';state.pinnedSessionIds=['b'];
 state.sessions[0].recentActivityAt=30;state.sessions[1].recentActivityAt=1;state.sessions[2].recentActivityAt=20;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 let root;const render=()=>React.createElement(WorkspaceRail,{state,act,session:state.sessions[0]});
 await renderAct(async()=>{root=create(render())});
 const rows=()=>root.root.findAll(node=>node.type==='div'&&node.props['data-session-id']).map(node=>node.props['data-session-id']);
 assert.deepEqual(rows(),['b','a','c','b']);
 assert.equal(root.root.findByProps({'aria-label':'Pinned items'}).findAll(node=>node.type==='div'&&node.props['data-session-id']).length,1);
 let detail;const renderDetail=chat=>React.createElement(ChatDetails,{chat,model:{state,act,draft:{},choose:()=>{},setDraft:()=>{}},now:100,close:()=>{}});
 await renderAct(async()=>{detail=create(renderDetail({...state.sessions[1],pinned:true}))});
 const unpin=detail.root.findByProps({'aria-label':'Unpin Another plan'});
 assert.equal(unpin.props['aria-pressed'],true);
 await renderAct(async()=>unpin.props.onClick());
 assert.deepEqual(calls,[{name:'session.pin',args:{id:'b',pinned:false}}]);
 state.pinnedSessionIds=[];
 await renderAct(async()=>root.update(render()));
 assert.deepEqual(rows(),['a','c','b']);
 await renderAct(async()=>detail.update(renderDetail(state.sessions[2])));
 await renderAct(async()=>detail.root.findByProps({'aria-label':'Pin Other workspace'}).props.onClick());
 await renderAct(async()=>detail.unmount());
 assert.deepEqual(calls.at(-1),{name:'session.pin',args:{id:'c',pinned:true}});
 assert.deepEqual(state.sessions.map(row=>row.recentActivityAt),[30,1,20]);
 assert.equal(calls.some(call=>call.name==='session.select'),false);
 await renderAct(async()=>root.unmount());
});

test('chat details review only the displayed failure through the shared action',async()=>{
 const state=initial(),chat={...state.sessions[0],status:'error',error:'Saved failure'},calls=[];
 const item={id:'session:a',sessionId:'a',fingerprint:'failure-one',read:false};
 state.attention={items:[item,{id:'approval:permission',sessionId:'a',fingerprint:'permission-one',read:false}]};
 const act=async(name,args)=>{calls.push({name,args});item.read=true;return {accepted:true}};
 let root;const render=()=>React.createElement(ChatDetails,{chat,model:{state,act,draft:{},choose:()=>{},setDraft:()=>{}},now:100,close:()=>{}});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'data-action':'attention.read'}).props.onClick());
 assert.deepEqual(calls,[{name:'attention.read',args:{ids:['session:a'],fingerprints:{'session:a':'failure-one'}}}]);
 assert.equal(chat.error,'Saved failure');assert.equal(chat.status,'error');
 assert.equal(state.attention.items[1].read,false);
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAllByProps({'data-action':'attention.read'}).length,0);
 item.read=false;item.fingerprint='failure-two';
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findAllByProps({'data-action':'attention.read'}).length,1);
 await renderAct(async()=>root.unmount());
});

test('header sidebar toggle, workspace drill-in and chat selection use shared actions',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};let root;
 await renderAct(async()=>{root=create(React.createElement(React.Fragment,null,
  React.createElement(WorkNavigationContext.Provider,{value:{history:null,visitHistory(){}}},
   React.createElement(WorkHeader,{state,act,presentation:{}})),
  React.createElement(WorkspaceRail,{state,act})))});
 assert.equal(root.root.findByType(WorkspaceRail).findAllByProps({'aria-label':'Close navigation'}).length,0);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Close navigation'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{navPinned:false,navExpanded:false,toolbarMenuOpen:false}}});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Open chats in /two'}).props.onClick());
 assert.ok(calls.some(call=>call.name==='workspace.select'&&call.args.id==='two'));
 assert.equal(calls.at(-1).args.patch.navWorkspaceList,false);
 await renderAct(async()=>root.root.findAllByProps({className:'a-nav-chat-select'})[1].props.onClick());
 assert.ok(calls.some(call=>call.name==='session.select'&&call.args.id==='b'));
 await renderAct(async()=>root.unmount());
});

test('sidebar New chat and Search stay outside the scrollable modules on Home in both modes',async()=>{
 const state=initial(),calls=[];
 state.view.workSurface='home';
 const act=async(name,args)=>{calls.push([name,args]);return {accepted:true}};
 const navigation={sidebarView:state.view,updateSidebar:patch=>act('view.update',{patch}),
  browse:mode=>calls.push(['browse',mode]),create(){},newChat(){}};
 let root;
 try{
  for(const navMode of ['browse','status']){
   state.view.navMode=navMode;
   const element=React.createElement(WorkNavigationContext.Provider,{value:navigation},React.createElement(WorkspaceRail,{state,act}));
   await renderAct(async()=>{if(root)root.update(element);else root=create(element)});
   const scroll=root.root.findByProps({className:'a-nav-content a-nav-reveal'});
   assert.equal(scroll.findAllByProps({'aria-label':'New chat'}).length,0);
   assert.equal(scroll.findAllByProps({className:'a-nav-search-launch'}).length,0);
   await renderAct(async()=>root.root.findByProps({className:'a-nav-search-launch'}).props.onClick());
   assert.deepEqual(calls.at(-1),['browse','chats']);
   await renderAct(async()=>root.root.findByProps({'aria-label':'New chat'}).props.onClick());
   assert.ok(calls.some(([name])=>name==='session.draft'));
  }
 }finally{if(root)await renderAct(async()=>root.unmount())}
});

test('chat rename opens the flyout editor and submits its new title',async()=>{
 const state=initial(),chat=state.sessions[1],calls=[];
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const setDraft=value=>{state.view.workspaceDraft=value;calls.push({name:'view.update',args:{patch:{workspaceDraft:value}}})};
 let root;const render=()=>React.createElement(ChatDetails,{chat,model:{state,act,draft:state.view.workspaceDraft||{},setDraft,prefix:'nav-chats'},now:100,close:()=>{}});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByProps({'aria-label':'Rename Another plan'}).props.onClick());
 await renderAct(async()=>root.update(render()));
 const input=root.root.findByProps({id:'nav-chats-name'});assert.equal(input.props.value,'Another plan');
 await renderAct(async()=>input.props.onChange({target:{value:'Renamed plan'}}));
 await renderAct(async()=>root.root.findByType(ChatRename).findByType('form').props.onSubmit({preventDefault(){}}));
 assert.ok(calls.some(call=>call.name==='session.rename'&&call.args.id==='b'&&call.args.title==='Renamed plan'));
 assert.equal(calls.filter(call=>call.name==='view.update').length,2);
 await renderAct(async()=>root.unmount());
});

test('registration removal requires explicit confirm and preserves unrelated chat actions',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>{calls.push({name,args});return {accepted:true}};state.view.workspaceDraft={mode:'remove',id:'two',name:'Two'};let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act}))});
 assert.match(JSON.stringify(root.toJSON()),/files and chats will stay/);
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(calls[0],{name:'workspace.remove',args:{id:'two'}});
 assert.equal(calls.some(call=>call.name==='session.delete'),false);
 await renderAct(async()=>root.unmount());
});

test('bounded workspace registrations use the full catalog count for removal availability',async()=>{
 const state=initial();state.workspaces=state.workspaces.slice(0,1);state.library={bounded:true,workspaceCount:3000};let root;
 const render=()=>React.createElement(WorkspaceManager,{host:shellFor(state,async()=>({accepted:true})).hostFor({id:'workspaces'})});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findByProps({'aria-label':'Remove workspace registration'}).props.disabled,false);
 state.library.workspaceCount=1;await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByProps({'aria-label':'Remove workspace registration'}).props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('A2UI button records exactly the declared action while text stays inert',async()=>{
 const calls=[],surface={surfaceId:'plan',root:'root',components:[{id:'root',component:{Column:{children:{explicitList:['intro','button']}}}},{id:'intro',component:{Text:{text:{literalString:'<script>window.evil=true</script>'}}}},{id:'button',component:{Button:{child:'label',action:{name:'review'}}}},{id:'label',component:{Text:{text:{literalString:'Review plan'}}}}]};let root;
 await renderAct(async()=>{root=create(React.createElement(A2UISurface,{surface,act:async(name,args)=>calls.push({name,args})}))});
 assert.equal(root.root.findAllByType('script').length,0);
 await renderAct(async()=>root.root.findByType('button').props.onClick());
 assert.deepEqual(calls,[{name:'canvas.event',args:{surfaceId:'plan',componentId:'button',name:'review'}}]);
 await renderAct(async()=>root.unmount());
});

test('canvas keyboard resize persists bounded width and header toggle hides through public action',async()=>{
 const previousDocument=globalThis.document;
 globalThis.document={addEventListener(){},removeEventListener(){}};
 const calls=[],state={selectedSessionId:'a',view:{canvasWidth:440},canvas:{open:true,kind:'text',content:'Document',title:'Preview'}},act=async(name,args)=>calls.push({name,args});let root;
 try{
 await renderAct(async()=>{root=create(React.createElement(React.Fragment,null,React.createElement(CanvasToggle,{state,act}),React.createElement(AgentCanvas,{state,act})))});
 await renderAct(async()=>root.root.findByProps({role:'separator'}).props.onKeyDown({key:'ArrowLeft',preventDefault(){}}));
 assert.deepEqual(calls.at(-1),{name:'view.update',args:{patch:{canvasWidth:460}}});
 await renderAct(async()=>root.root.findByProps({role:'separator'}).props.onKeyDown({key:'End',preventDefault(){}}));
 assert.equal(calls.at(-1).args.patch.canvasWidth,root.root.findByProps({role:'separator'}).props['aria-valuemax']);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Close canvas panel'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'canvas.visibility',args:{open:false}});
 const toggle=root.root.findByProps({'aria-label':'Hide canvas'});
 assert.equal(toggle.props['aria-expanded'],true);
 await renderAct(async()=>toggle.props.onClick());
 assert.deepEqual(calls.at(-1),{name:'canvas.visibility',args:{open:false}});
 }finally{
  if(root)await renderAct(async()=>root.unmount());
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
 }
});

test('reopening the canvas keeps its saved snapshot without creating another artifact',async()=>{
 let called;
 await reopenCanvas({canvas:{kind:'markdown',path:'/one/plan.md',title:'Plan',content:'Old content',events:[{}]}},async(name,args)=>{called={name,args}});
 assert.deepEqual(called,{name:'canvas.visibility',args:{open:true}});
});

test('Canvas Details and reader are stable siblings below the one navigation bar',async()=>{
 const previousDocument=globalThis.document;
 globalThis.document={addEventListener(){},removeEventListener(){}};
 let root,state={selectedSessionId:'a',view:{settingsCanvas:true},canvas:{open:true,kind:'settings',title:'Settings'}};
 const render=()=>React.createElement(AgentCanvas,{state,act:async()=>({accepted:true}),settingsSurface:React.createElement('input',{'aria-label':'Retained field',defaultValue:'Unsent'})});
 try{
  await renderAct(async()=>{root=create(render())});
  const inspector=root.root.findByProps({id:'canvas-options'}),host=root.root.findByProps({'data-part':'canvas-view-controls'});
  const reader=root.root.findByProps({className:'a-canvas-reader'}),field=root.root.findByProps({'aria-label':'Retained field'});
  assert.equal(inspector.parent,reader.parent);
  assert.equal(reader.parent.props.className,'a-canvas-content');
  assert.equal(root.root.findByProps({className:'a-canvas-chrome'}).findAllByProps({id:'canvas-options'}).length,0);
  assert.equal(reader.findAllByProps({className:'a-canvas-artifact-toolbar'}).length,1);
  assert.equal(inspector.props.role,'region');assert.equal(inspector.props['aria-labelledby'],'canvas-details-title');
  for(const [open,visible] of [[true,true],[true,false],[true,true],[false,true]]){
   state={...state,view:{...state.view,canvasControlsExpanded:open},canvas:{...state.canvas,open:visible}};
   await renderAct(async()=>root.update(render()));
   assert.equal(root.root.findByProps({id:'canvas-options'}),inspector);
   assert.equal(root.root.findByProps({'data-part':'canvas-view-controls'}),host,'Portal target must not be replaced by visibility/layout changes');
   assert.equal(root.root.findByProps({'aria-label':'Retained field'}),field);
   assert.equal(inspector.props.hidden,!open);
   assert.equal(root.root.findAllByProps({'aria-label':'Back to chat'}).length,0);
  }
 }finally{if(root)await renderAct(async()=>root.unmount());if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument}
});

test('Canvas Details resize, Escape priority and focus return follow the current interaction',async()=>{
 const previousDocument=globalThis.document,previousObserver=globalThis.ResizeObserver,listeners=new Map();
 let root,width=800,measure,state={selectedSessionId:'a',view:{canvasControlsExpanded:false},canvas:{open:true,kind:'text',title:'Document'}};
 const calls=[],doc={body:{},activeElement:null,addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn)},removeEventListener(name,fn){listeners.get(name)?.delete(fn)},querySelector(){return invoker}};
 const node=area=>({area,isConnected:true,getClientRects:()=>[{}],closest:()=>null,focus(){doc.activeElement=this}});
 const invoker=node('reader'),readerField=node('reader'),close=node('inspector'),version=node('inspector'),newIntent=node('header');
 const panelNode={getBoundingClientRect:()=>({width}),contains:n=>['reader','inspector'].includes(n?.area),querySelector:()=>invoker};
 const readerNode={contains:n=>n?.area==='reader'};
 const inspectorNode={contains:n=>n?.area==='inspector',querySelector:selector=>selector.includes('Artifact version')?version:close};
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const render=()=>React.createElement(AgentCanvas,{state,act});
 const update=async patch=>{state={...state,view:{...state.view,...patch}};await renderAct(async()=>root.update(render()))};
 const open=async()=>{doc.activeElement=invoker;await renderAct(async()=>root.root.findByProps({'aria-label':'Canvas details'}).props.onClick());await update({canvasControlsExpanded:true})};
 globalThis.document=doc;
 globalThis.ResizeObserver=class {constructor(callback){measure=callback}observe(){}disconnect(){}};
 try{
  await renderAct(async()=>{root=create(render(),{createNodeMock:({props})=>props.id==='workspace-canvas'?panelNode:props.id==='canvas-options'?inspectorNode:props.className==='a-canvas-content'?{getBoundingClientRect:()=>({width})}:props.className==='a-canvas-reader'?readerNode:null})});
  await open();assert.equal(doc.activeElement,close);assert.equal(root.root.findByProps({className:'a-canvas-reader'}).props.inert,false);
  doc.activeElement=readerField;width=390;await renderAct(async()=>measure());
  assert.equal(doc.activeElement,close,'A responsive overlay must not strand focus in its covered reader');
  assert.equal(root.root.findByProps({className:'a-canvas-reader'}).props.inert,true);
  const panel=()=>root.root.findByProps({id:'workspace-canvas'});
  let prevented=false;
  await renderAct(async()=>panel().props.onKeyDown({key:'Escape',defaultPrevented:false,target:{closest:()=>({})},preventDefault(){prevented=true}}));
  assert.equal(prevented,false,'Native dialog owns Escape before Details');
  calls.length=0;
  await renderAct(async()=>panel().props.onKeyDown({key:'Escape',defaultPrevented:false,target:{closest:()=>null},preventDefault(){prevented=true}}));
  assert.equal(prevented,true);assert.deepEqual(calls,[{name:'view.update',args:{patch:{canvasControlsExpanded:false}}}]);
  await update({canvasControlsExpanded:false});assert.equal(doc.activeElement,readerField);
  assert.equal(root.root.findByProps({className:'a-canvas-reader'}).props.inert,false);
  await open();
  await renderAct(async()=>root.root.findByProps({'aria-label':'Close canvas details'}).props.onClick());
  doc.activeElement=newIntent;for(const fn of listeners.get('pointerdown')||[])fn();
  await update({canvasControlsExpanded:false});assert.equal(doc.activeElement,newIntent,'Late close settlement cannot steal newer header intent');
 }finally{
  if(root)await renderAct(async()=>root.unmount());
  if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument;
  if(previousObserver===undefined)delete globalThis.ResizeObserver;else globalThis.ResizeObserver=previousObserver;
 }
});


test('rename preserves failed text and newer drafts across external renames',async()=>{
 let finish;const calls=[];let cancelled=0;
 const act=async(name,args)=>{calls.push({name,args});return await new Promise(resolve=>finish=resolve)};
 let chat={id:'a',title:'Original'},root;
 const render=()=>React.createElement(ChatRename,{chat,act,cancel:()=>cancelled++});
 await renderAct(async()=>{root=create(render())});
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'My new name'}}));
 let pending;
 await renderAct(async()=>{pending=root.root.findByType('form').props.onSubmit({preventDefault(){}})});
 await renderAct(async()=>root.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.equal(calls.length,1);
 await renderAct(async()=>{finish(undefined);await pending});
 assert.equal(cancelled,0);assert.equal(root.root.findByType('input').props.value,'My new name');
 assert.equal(root.root.findByProps({role:'alert'}).children.length,1);
 chat={...chat,title:'Agent supplied title'};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('input').props.value,'My new name');
 await renderAct(async()=>root.unmount());
});


test('background discovery keeps existing chats quiet, reports errors and uses the shared refresh action',async()=>{
 const state=initial(),calls=[],act=async(name,args)=>calls.push({name,args});state.sharedHistory={loading:true};let root;
 const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.doesNotMatch(JSON.stringify(root.toJSON()),/Finding existing chats/);
 assert.match(JSON.stringify(root.toJSON()),/First plan/);
 assert.equal(root.root.findByProps({'aria-label':'Refresh workspaces and chats'}).props.disabled,true);
 state.sharedHistory={error:'One project could not be read.'};
 await renderAct(async()=>root.update(render()));
 assert.match(root.root.findByProps({role:'alert'}).children.join(''),/could not be read/);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Refresh workspaces and chats'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'history.refresh',args:{}});
 await renderAct(async()=>root.unmount());
});

test('unresolved folders are hidden but the launcher lets users choose a valid workspace',async()=>{
 const state=initial();state.workspaces=[{id:'native-one',name:'One',path:null,available:false},{id:'native-two',name:'Two',path:null,available:false}];state.selectedWorkspaceId='native-two';state.workspaceExplorer={rows:[],totalWorkspaces:0};
 state.sessions=[{id:'a',title:'Project one chat',workspaceId:'native-one',workspace:null},{id:'b',title:'Project two chat',workspaceId:'native-two',workspace:null}];let root;
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 const rows=root.root.findAll(node=>node.props.className==='a-nav-chat-select');
 assert.equal(rows.length,0);
 assert.equal(root.root.findAllByProps({'data-action':'workspace.select'}).length,0);
 assert.match(JSON.stringify(root.toJSON()),/Create a workspace to start a chat/);
 assert.ok(!root.root.findByProps({'aria-label':'New chat'}).props.disabled);
 await renderAct(async()=>root.unmount());
});

test('native history paging, failures and unavailable workspaces have visible shared controls',async()=>{
 const calls=[],act=async(name,args)=>calls.push({name,args});let loadingOlder=0;
 let session={id:'native-chat',historyLoaded:false,historyLoading:true,sharedHistoryOffset:80};let root;
 const render=()=>React.createElement(SessionHistoryControls,{session,act,onLoadEarlier:()=>loadingOlder++});
 await renderAct(async()=>{root=create(render())});
 assert.match(JSON.stringify(root.toJSON()),/Loading conversation/);
 assert.equal(root.root.findByProps({'data-action':'session.history'}).props.disabled,true);
 session={...session,historyLoaded:true,historyLoading:false};
 await renderAct(async()=>root.update(render()));
 await renderAct(async()=>root.root.findByProps({'data-action':'session.history'}).props.onClick());
 assert.equal(loadingOlder,1);assert.deepEqual(calls.at(-1),{name:'session.history',args:{id:'native-chat',before:80,limit:100}});
 session={...session,historyError:'The transcript could not be read.',workspaceAvailable:false};
 await renderAct(async()=>root.update(render()));
 assert.match(JSON.stringify(root.toJSON()),/read its saved chats/);
 assert.equal(root.root.findAllByProps({role:'alert'}).length,1);
 await renderAct(async()=>root.root.findByProps({'data-action':'session.select'}).props.onClick());
 assert.deepEqual(calls.at(-1),{name:'session.select',args:{id:'native-chat'}});
 await renderAct(async()=>root.unmount());
});

test('background history refresh preserves the loaded conversation layout without an empty notice',async()=>{
 let root;
 const session={id:'native-chat',historyLoaded:true,sharedHistoryOffset:80,messages:[{id:'saved',text:'Saved reply'}]};
 const render=patch=>React.createElement(SessionHistoryControls,{session:{...session,...patch},hideEarlier:true,act:()=>{}});
 await renderAct(async()=>{root=create(render({historyLoading:false}))});
 assert.equal(root.toJSON(),null);
 await renderAct(async()=>root.update(render({historyLoading:true})));
 assert.equal(root.toJSON(),null,'A hidden background refresh must not insert a flex child and move the reader');
 await renderAct(async()=>root.update(render({historyLoading:true,messages:[]})));
 assert.match(JSON.stringify(root.toJSON()),/Loading conversation/);
 await renderAct(async()=>root.update(render({historyLoading:true,historyError:'Read failed'})));
 assert.match(JSON.stringify(root.toJSON()),/Read failed/);
 await renderAct(async()=>root.unmount());
});

test('local outbox and streaming text suppress only the initial history notice',async()=>{
 let root;
 const session={id:'live-chat',historyLoaded:false,historyLoading:true,messages:[]};
 const render=(patch={},messages)=>React.createElement(SessionHistoryControls,{session:{...session,...patch},messages,hideEarlier:true,act:()=>{}});
 await renderAct(async()=>{root=create(render({},[{id:'outbox'}]))});
 assert.equal(root.toJSON(),null);
 await renderAct(async()=>root.update(render({streaming:'Live response'})));
 assert.equal(root.toJSON(),null);
 await renderAct(async()=>root.update(render({historyError:'Read failed'},[{id:'outbox'}])));
 assert.match(JSON.stringify(root.toJSON()),/Read failed/);
 await renderAct(async()=>root.update(render()));
 assert.match(JSON.stringify(root.toJSON()),/Loading conversation/);
 await renderAct(async()=>root.unmount());
});

test('workspace chat actions offer Archive but neither Remove nor Delete',async()=>{
 const state=initial(),calls=[];let root;
 const act=async(name,args)=>{calls.push({name,args});return {accepted:true}};
 const model={state,act,draft:{},setDraft:value=>calls.push(value),choose:()=>{}};
 await renderAct(async()=>{root=create(React.createElement(ChatDetails,{chat:state.sessions[0],model,now:100,close:()=>{}}))});
 const labels=root.root.findAllByType('button').map(node=>node.props['aria-label']||node.children.join(''));
 assert.ok(labels.includes('Archive First plan'));assert.ok(!labels.some(label=>/Remove|Delete/.test(label)));
 await renderAct(async()=>root.update(React.createElement(ChatDetails,{chat:{...state.sessions[0],location:{kind:'managed'}},model,now:100,close:()=>{}})));
 await renderAct(async()=>root.root.findByProps({'aria-label':'Delete First plan'}).props.onClick());
 assert.deepEqual(calls,[{mode:'chat-delete',id:'a',name:'First plan'}]);
 assert.match(JSON.stringify(root.toJSON()),/No workspace/);
 await renderAct(async()=>root.unmount());
});

test('Recent pagination stays bounded and is shared with agents',async()=>{
 const state=initial();state.selectedSessionId='chat-0';state.sessions=Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved '+i,workspace:'/one'}));
 const calls=[],act=async(name,args)=>{calls.push({name,args});if(name==='view.update')Object.assign(state.view,args.patch);return {accepted:true}};let root;
 const render=()=>React.createElement(WorkspaceRail,{state,act});
 await renderAct(async()=>{root=create(render())});
 assert.equal(root.root.findAllByProps({className:'a-nav-chat-select'}).length,40);
 await renderAct(async()=>root.root.findByProps({'aria-label':'Show more conversations'}).props.onClick());
 assert.deepEqual(calls.at(-1).args.patch.navRecentView.navChatPage,{mode:'all',workspaceId:null,filter:'',selectedSessionId:'chat-0',section:'recent',showAgentCreated:false,index:1});
 await renderAct(async()=>root.update(render()));
 const rows=root.root.findAllByProps({className:'a-nav-chat-select'});
 assert.equal(rows[0].props['aria-label'],'Saved 40');assert.equal(rows.length,40);
 await renderAct(async()=>root.unmount());
});


test('one sidebar keeps a pinned chat accessible once in Pinned and once in Recent',async()=>{
 const state=initial();state.pinnedSessionIds=['a'];state.view.navWorkspaceList=true;
 let root;await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 assert.deepEqual(root.root.findAll(node=>typeof node.type==='string'&&node.props['data-sidebar-section']).map(node=>node.props['data-sidebar-section']),['pinned','workspaces','recent']);
 for(const id of ['pinned','recent'])assert.equal(root.root.findByProps({'data-sidebar-section':id}).findAll(node=>typeof node.type==='string'&&node.props['data-session-id']==='a').length,1);
 assert.equal(root.root.findAllByProps({'data-session-id':'b'}).filter(node=>typeof node.type==='string').length,1);
 assert.equal(root.root.findAllByProps({'data-session-id':'c'}).filter(node=>typeof node.type==='string').length,1);
 assert.equal(root.root.findAllByProps({'data-part':'workspace-explorer'}).length,1);
 assert.equal(root.root.findAllByProps({'aria-label':'New workspace'}).length,1);
 await renderAct(async()=>root.unmount());
});


test('single workspace explorer expands its preview including empty registered folders',async()=>{
 const state=initial();state.workspaceExplorer.mode='recent';
 state.workspaceExplorer.rows=Array.from({length:12},(_,i)=>({workspaceId:'w'+i,path:'/w'+i,name:'Folder '+i,chatCount:0}));
 let root;await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='workspace.select').length,8);
 const more=root.root.findAllByType('button').find(button=>button.children[0]==='More workspaces');
 await renderAct(async()=>more.props.onClick());
 assert.equal(root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='workspace.select').length,12);
 assert.equal(root.root.findAllByProps({'data-part':'workspace-explorer'}).length,1);
 await renderAct(async()=>root.unmount());
});

function pinnedNavigation(simple){
 const state=initial();state.view.navSimple=simple;state.view.navWorkspaceList=false;
 state.pinnedSessionIds=['a','hidden','b','off-page'];
 state.homeNavigation={items:state.sessions.slice(0,2).map(chat=>({...chat,pinned:true}))};
 state.workspaceOverview={items:state.workspaces};return state;
}
for(const simple of [true,false]){
 const label=simple?'default sidebar':'All chats sidebar';
 test(`${label} restores keyboard pin ordering without dropping hidden pins`,async()=>{
  const state=pinnedNavigation(simple),calls=[];let root;
  await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async(name,args)=>{calls.push({name,args});return {accepted:true}}}))});
  const handle=title=>root.root.findByProps({'aria-label':'Reorder '+title});
  const key=(key,altKey=true)=>({key,altKey,preventDefault(){}});
  await renderAct(async()=>{handle('First plan').props.onKeyDown(key('ArrowUp'));handle('Another plan').props.onKeyDown(key('ArrowDown'))});
  assert.equal(calls.length,0);
  await renderAct(async()=>handle('Another plan').props.onKeyDown(key('ArrowUp')));
  assert.deepEqual(calls.at(-1),{name:'session.pinOrder',args:{ids:['b','a','hidden','off-page']}});
  await renderAct(async()=>handle('First plan').props.onKeyDown(key('ArrowDown')));
  assert.deepEqual(calls.at(-1),{name:'session.pinOrder',args:{ids:['hidden','b','a','off-page']}});
  assert.ok(calls.every(call=>call.name==='session.pinOrder'));
  await renderAct(async()=>root.unmount());
 });
 test(label+' uses the shared Settings reorder control and full chat contents',async()=>{
  const state=pinnedNavigation(simple);let root;
  await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act:async()=>({accepted:true})}))});
  const list=root.root.findByProps({className:'a-reorder-list a-pinned-chats'});
  assert.equal(typeof list.props.onPointerMove,'function');
  const handle=root.root.findByProps({'aria-label':'Reorder Another plan'});
  assert.equal(handle.props.draggable,false);assert.equal(typeof handle.props.onPointerDown,'function');
  const pinned=root.root.findByProps({'data-sidebar-section':'pinned'});
  assert.equal(pinned.findAllByProps({className:'a-nav-chat-workspace'}).length,2);
  assert.equal(pinned.findAllByProps({'data-view-source':'activity-time'}).length,2);
  await renderAct(async()=>root.unmount());
 });
}
test('default sidebar prevents duplicate pending reorders and allows retry after a rejected receipt',async()=>{
 const state=pinnedNavigation(true),calls=[];let root,settle;
 const act=async(name,args)=>{calls.push({name,args});return calls.length===1?new Promise(resolve=>{settle=resolve}):{accepted:true}};
 await renderAct(async()=>{root=create(React.createElement(WorkspaceRail,{state,act}))});
 const handle=()=>root.root.findByProps({'aria-label':'Reorder Another plan'});
 const event={altKey:true,key:'ArrowUp',preventDefault(){}};
 await renderAct(async()=>{handle().props.onKeyDown(event);handle().props.onKeyDown(event)});
 assert.equal(calls.length,1);assert.equal(handle().props['aria-disabled'],true);assert.equal(handle().props.draggable,false);
 await renderAct(async()=>settle({accepted:true,result:{accepted:false,error:'Pin order changed. Please retry.'}}));
 assert.equal(!!handle().props['aria-disabled'],false);
 assert.ok(root.root.findAllByProps({role:'alert'}).some(node=>node.children.includes('Pin order changed. Please retry.')));
 assert.deepEqual(state.pinnedSessionIds,['a','hidden','b','off-page']);
 await renderAct(async()=>handle().props.onKeyDown(event));assert.equal(calls.length,2);
 assert.equal(root.root.findAllByProps({role:'alert'}).some(node=>node.children.includes('Pin order changed. Please retry.')),false);
 await renderAct(async()=>root.unmount());
});


test('inline Auto name now shares the naming action and preserves policy and newer drafts',async()=>{
 let finish;const calls=[];let cancelled=0;
 const act=async(name,args)=>{calls.push({name,args});return new Promise(resolve=>finish=resolve)};
 let chat={id:'native-a',title:'Original',autoName:false,status:'idle'},root;
 const render=()=>React.createElement(ChatRename,{chat,act,cancel:()=>cancelled++});
 await renderAct(async()=>{root=create(render())});
 const generate=()=>root.root.findByProps({'data-action':'session.naming'});
 assert.match(root.root.findByProps({className:'a-nav-name-help'}).children.join(''),/Future automatic naming stays off/);
 let pending;
 await renderAct(async()=>{pending=generate().props.onClick()});
 await renderAct(async()=>generate().props.onClick());
 assert.deepEqual(calls,[{name:'session.naming',args:{id:'native-a',regenerate:true}}]);
 await renderAct(async()=>{finish({accepted:true});await pending});
 assert.equal(cancelled,0);
 chat={...chat,naming:{status:'working'}};
 await renderAct(async()=>root.update(render()));
 assert.equal(generate().props.disabled,true);
 assert.match(root.root.findByProps({role:'status'}).children.join(''),/Generating/);
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'My newer draft'}}));
 chat={...chat,title:'Generated title',naming:{status:'ready'}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('input').props.value,'My newer draft');
 assert.equal(generate().props.disabled,true);
 await renderAct(async()=>root.unmount());
});

test('inline automatic name reflects completed results and recoverable errors',async()=>{
 let chat={id:'a',title:'Original',status:'working'},root;
 const render=()=>React.createElement(ChatRename,{chat,act:async()=>({accepted:false,error:'Retry later'}),cancel:()=>{}});
 await renderAct(async()=>{root=create(render())});
 const generate=()=>root.root.findByProps({'data-action':'session.naming'});
 assert.equal(!!generate().props.disabled,false);
 chat={...chat,status:'idle'};
 await renderAct(async()=>root.update(render()));
 await renderAct(async()=>generate().props.onClick());
 assert.match(root.root.findByProps({role:'alert'}).children.join(''),/Retry later/);
 assert.equal(root.root.findByType('input').props.value,'Original');
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'Temporary edit'}}));
 assert.equal(generate().props.disabled,true);
 await renderAct(async()=>root.root.findByType('input').props.onChange({target:{value:'Original'}}));
 assert.equal(generate().props.disabled,false);
 chat={...chat,title:'New automatic name',naming:{status:'ready'}};
 await renderAct(async()=>root.update(render()));
 assert.equal(root.root.findByType('input').props.value,'New automatic name');
 await renderAct(async()=>root.unmount());
});
