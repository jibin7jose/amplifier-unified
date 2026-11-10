import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {create} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}});
const {WorkNavigationContext,browsePatch}=await vite.ssrLoadModule('/src/work-navigation.js');
const {WorkSurface,LiveChatActivity,WorkspaceFiles}=await vite.ssrLoadModule('/src/work-shell.jsx');
const {ConversationList,PinnedItems,useRecentShortcuts}=await vite.ssrLoadModule('/src/shell/navigation-components.jsx');
const {WorkspaceExplorer}=await vite.ssrLoadModule('/src/workspace-explorer.jsx');
const {AgentCanvas}=await vite.ssrLoadModule('/src/shell-panels.jsx');
const {newChatSetup}=await vite.ssrLoadModule('/src/new-chat.jsx');
const {fitPanels}=await vite.ssrLoadModule('/src/panel-layout.jsx');
const {WorkspacePicker}=await vite.ssrLoadModule('/src/workspace-setup.jsx');
const {WorkHeader}=await vite.ssrLoadModule('/src/work-shell.jsx');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
test.after(()=>vite.close());
const workspace={id:'b',path:'/research',name:'Research',available:true};
const row={workspaceId:'b',path:'/research',name:'Research',chatCount:1};
const scope={mode:'all',workspaceId:null,filter:'',selectedSessionId:'a',section:'recent',showAgentCreated:false};
const page={items:[{id:'b-chat',title:'Research ideas',workspace:'/research',workspaceId:'b'}],scope,total:1,pages:1,index:0,start:0,end:1};
const snapshot={navigationResources:['pinnedNavigation','recentNavigation','workspaceShortcuts','workspaceExplorer','sidebarSearch','sidebarRecent','sidebarPinned'],view:{},selectedSessionId:'a',selectedWorkspaceId:'b',workspaces:[workspace],library:{bounded:true,workspaceCount:1},workspaceShortcuts:[row],recentShortcuts:page.items,recentNavigation:{...page,remaining:0,limit:20,scope:{...scope,section:'shortcuts',showAgentCreated:false,limit:20,viewRevision:0}},workspaceExplorer:{rows:[row],mode:'recent'},sidebarNavigation:{pinned:{items:[],total:0,pages:1},recent:page,search:{...page,scope:{...scope,section:'search'}},recentView:{}},sharedHistory:{}};
const host={instanceId:'chats',getSnapshot:()=>snapshot,subscribe:()=>()=>{},dispatch:async()=>({accepted:true})};
const navigation={browse(){},create(){},newChat(){},sidebarView:{},updateSidebar(){}};
const render=child=>React.createElement(WorkNavigationContext.Provider,{value:navigation},child);
for(const surface of ['chat','chats'])test(`managed delete obtains one confirmation token on the ${surface} surface`,async()=>{
 const calls=[],current={...snapshot,view:{workSurface:surface,workspaceDraft:{mode:'chat-delete',id:'managed',name:'Reviewed chat'}}};
 const scopedHost={...host,getSnapshot:()=>current,dispatch:async(action,args)=>{
  calls.push([action,args]);return {accepted:true,result:{confirmationToken:'only-preview',title:'Reviewed chat'}};
 }};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>scopedHost};
 let root;
 try{
  await act(async()=>{root=create(render(React.createElement(React.Fragment,null,
   React.createElement(ConversationList,{host:scopedHost}),
   React.createElement(WorkSurface,{shell,state:{view:{workSurface:surface}},act:scopedHost.dispatch}))));});
  assert.deepEqual(calls.filter(([name])=>name==='session.deletePreview'),[['session.deletePreview',{id:'managed'}]]);
  const remove=root.root.findAll(node=>node.type==='button'&&node.props['data-action']==='session.delete');
  assert.equal(remove.length,1);
  await act(async()=>remove[0].props.onClick());
  assert.deepEqual(calls.filter(([name])=>name==='session.delete'),[['session.delete',{id:'managed',confirmationToken:'only-preview'}]]);
 }finally{if(root)await act(async()=>root.unmount())}
});
test('compact sidebar has shortcuts; filtering lives in the main surface',()=>{
 const html=renderToStaticMarkup(render(React.createElement(ConversationList,{host})));
 assert.match(html,/All workspaces/);
 assert.doesNotMatch(html,/All chats|Review chats|View all chats|of 1 recent chats|a-recent-controls/);
 assert.doesNotMatch(html,/Filter conversations|Filter workspaces|Browse folders/);
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>host};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'chats'}},act:host.dispatch})));
 assert.match(main,/Research ideas/);assert.match(main,/aria-label="Search chats"/);assert.doesNotMatch(main,/a-browser-pins/);
});
test('empty Pinned disappears; workspace-only and mixed pins share one section',async()=>{
 const calls=[];
 const folder={...row,pinned:true};
 const chat={...page.items[0],pinned:true};
 const workspacePin={id:'workspace:b',kind:'workspace',row:folder},chatPin={id:'session:b-chat',kind:'session',row:chat};
 let current={...snapshot,view:{navSectionsCollapsed:['pinned']},pinnedItemIds:[],pinnedNavigation:{items:[],total:0,pages:1,index:0}};
 const pinHost={...host,getSnapshot:()=>current,dispatch:async(...args)=>{calls.push(args);return {accepted:true}}};
 let root;const element=()=>render(React.createElement(ConversationList,{host:pinHost}));
 await act(async()=>{root=create(element())});
 assert.equal(root.root.findAllByProps({'data-sidebar-section':'pinned'}).length,0);
 for(const items of [[workspacePin],[chatPin],[chatPin,workspacePin]]){
  current={...current,pinnedItemIds:items.map(item=>item.id),pinnedNavigation:{items,total:items.length,pages:1,index:0}};
  await act(async()=>root.update(element()));
  const section=root.root.findByProps({'data-sidebar-section':'pinned'});
  assert.equal(section.props['aria-label'],'Pinned items');
  assert.equal(section.findAllByType('button')[0].props['aria-expanded'],false);
  assert.equal(section.findAllByProps({className:'a-reorder-list a-pinned-chats'}).length,1);
 }
 current={...current,pinnedNavigation:{items:[],total:0,pages:1,index:0}};
 await act(async()=>root.update(element()));
 assert.equal(root.root.findAllByProps({'data-sidebar-section':'pinned'}).length,0);
 assert.deepEqual(current.view.navSectionsCollapsed,['pinned']);assert.deepEqual(calls,[]);
 await act(async()=>root.unmount());
});
test('mixed keyboard ordering preserves hidden pins and typed IDs, and workspace opens the browser',async()=>{
 const calls=[],selects=[];
 const items=[{id:'session:b',kind:'session',row:{id:'b',title:'Pinned chat',pinned:true}},
              {id:'workspace:b',kind:'workspace',row:{...row,pinned:true}}];
 const current={...snapshot,pinnedItemIds:['session:b','workspace:hidden','workspace:b','session:off-page'],
                pinnedNavigation:{items,total:2,pages:1,index:0}};
 const pinHost={...host,getSnapshot:()=>current,dispatch:async(...args)=>{calls.push(args);return {accepted:true}}};
 const nav={...navigation,browse:(...args)=>selects.push(args)};
 let root;await act(async()=>{root=create(React.createElement(WorkNavigationContext.Provider,{value:nav},React.createElement(ConversationList,{host:pinHost})))});
 const section=root.root.findByProps({'data-sidebar-section':'pinned'});
 assert.equal(section.findAll(node=>node.type==='div'&&node.props['data-order-id']).length,2);
 await act(async()=>section.findByProps({'aria-label':'Reorder Research'}).props.onKeyDown({key:'ArrowUp',preventDefault(){}}));
 assert.deepEqual(calls.at(-1),['navigation.pinOrder',{ids:['workspace:b','session:b','workspace:hidden','session:off-page']}]);
 await act(async()=>section.findByProps({'aria-label':'Open chats in /research'}).props.onClick());
 assert.deepEqual(selects,[['workspace','b']]);
 await act(async()=>section.findByProps({'aria-label':'New chat in /research'}).props.onClick({stopPropagation(){}}));
 assert.deepEqual(calls.at(-1),['session.draft',{workspace:'/research',workspaceId:'b',location:{kind:'workspace'}}]);
 const ordinary=root.root.findByProps({'data-sidebar-section':'workspaces'});
 await act(async()=>ordinary.findByProps({'aria-label':'Open chats in /research'}).props.onClick());
 assert.deepEqual(selects,[['workspace','b'],['workspace','b']]);
 await act(async()=>ordinary.findByProps({'aria-label':'New chat in /research'}).props.onClick({stopPropagation(){}}));
 assert.deepEqual(calls.at(-1),['session.draft',{workspace:'/research',workspaceId:'b',location:{kind:'workspace'}}]);
 await act(async()=>root.unmount());
});
test('pending mixed reorder handles concurrent unpin without rendering an identity-less row',async()=>{
 let settle,root;const calls=[];
 const pins=[{id:'session:a',kind:'session',row:{id:'a',title:'One',pinned:true}},
             {id:'workspace:b',kind:'workspace',row:{...row,pinned:true}}];
 let pinPage={items:pins,total:2,index:0,pages:1};
 const current={...snapshot,pinnedItemIds:pins.map(item=>item.id),pinnedNavigation:pinPage};
 const model={state:current,host:{...host,getSnapshot:()=>current},
  act:(...args)=>{calls.push(args);return new Promise(resolve=>{settle=resolve})},view:{},setDraft(){},choose(){}};
 const element=()=>React.createElement(PinnedItems,{page:pinPage,model,now:0});
 await act(async()=>{root=create(element())});
 await act(async()=>root.root.findByProps({'aria-label':'Reorder Research'}).props.onKeyDown({key:'ArrowUp',preventDefault(){}}));
 assert.deepEqual(calls,[['navigation.pinOrder',{ids:['workspace:b','session:a']}]]);
 assert.equal(typeof settle,'function','the reorder must be admitted and unresolved before unpin');
 current.pinnedItemIds=['session:a'];pinPage={...pinPage,items:[pins[0]],total:1};
 current.pinnedNavigation=pinPage;
 await act(async()=>root.update(element()));
 assert.equal(root.root.findAllByProps({'aria-label':'Reorder Research'}).length,0);
 assert.equal(root.root.findAllByProps({className:'a-nav-chat-select'}).length,1);
 assert.equal(root.root.findByProps({className:'a-nav-chat-select'}).props['aria-label'],'One');
 await act(async()=>settle({accepted:false,error:'Pins changed'}));
 assert.equal(calls.length,1,'rejection must not replay the reorder');
 await act(async()=>root.unmount());
});
test('standalone chat sidebar exposes a workspace edit without replacing an active chat edit',async()=>{
 const calls=[];let current={...snapshot,pinnedItemIds:['workspace:b'],
  pinnedNavigation:{items:[{id:'workspace:b',kind:'workspace',row:{...row,pinned:true}}],total:1,index:0,pages:1},
  view:{workspaceDraft:{mode:'rename',id:'b',name:'Retained workspace edit'}}};
 const pinHost={...host,getSnapshot:()=>current,dispatch:async(...args)=>{calls.push(args);return {accepted:true}}};
 let root;const element=()=>render(React.createElement(ConversationList,{host:pinHost}));
 await act(async()=>{root=create(element())});
 assert.equal(root.root.findAll(node=>node.type==='input'&&node.props.value==='Retained workspace edit').length,1);
 current={...current,view:{workspaceDraft:{mode:'chat-rename',id:'a',name:'Retained chat edit'}}};
 await act(async()=>root.update(element()));
 const workspace=root.root.findAll(node=>node.props.row?.workspaceId==='b'&&node.props.compact===true&&node.props.handle);
 assert.equal(workspace.length,1);assert.equal(workspace[0].props.onEdit,undefined);
 assert.deepEqual(calls,[]);
 await act(async()=>root.unmount());
});
test('mismatched bounded search scope stays pending instead of using a local fallback',()=>{
 const mismatched={...snapshot,sidebarNavigation:{...snapshot.sidebarNavigation,
  search:{...page,scope:{...scope,section:'search',showAgentCreated:true}}}};
 const mismatchedHost={...host,getSnapshot:()=>mismatched};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>mismatchedHost};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'chats'}},act:host.dispatch})));
 assert.match(main,/Searching…/);assert.doesNotMatch(main,/Research ideas/);
});
test('hidden main browser leaves controllers and saved edits intact without reading chat pages',()=>{
 const current={...snapshot,view:{workspaceDraft:{mode:'rename',id:'b',name:'Keep this edit'}}};
 Object.defineProperty(current,'sidebarNavigation',{enumerable:true,get(){throw Error('Hidden chat page read');}});
 const scopedHost={...host,getSnapshot:()=>current};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>scopedHost};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'chat'}},act:host.dispatch})));
 assert.match(main,/hidden=""/);assert.match(main,/Keep this edit/);
 assert.doesNotMatch(main,/a-work-browser-heading|aria-label="Search chats"|workspace-resources/);
 assert.equal(current.view.workspaceDraft.name,'Keep this edit');
});
test('workspace details do not compute the hidden chat page',()=>{
 const current={...snapshot};
 Object.defineProperty(current,'sidebarNavigation',{enumerable:true,get(){throw Error('Hidden workspace chat page read');}});
 const scopedHost={...host,getSnapshot:()=>current};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>scopedHost};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'details'},workspaces:[workspace]},act:host.dispatch})));
 assert.match(main,/Rename workspace/);assert.doesNotMatch(main,/Research ideas/);
});
test('Shared Work tab reads only through the rejecting presentation dispatcher and aborts when hidden',async()=>{
 const current={...snapshot},calls=[],acts=[],pending=[];
 Object.defineProperty(current,'sidebarNavigation',{enumerable:true,get(){throw Error('Work must not demand a chat page')}});
 const scopedHost={...host,getSnapshot:()=>current};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>scopedHost};
 let state={view:{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'work'},workspaces:[workspace]},root;
 const dispatch=async(action,args,meta)=>{
  calls.push([action,args,meta]);
  if(action==='workspace.work.list')return {accepted:true,result:{workspaceId:'b',version:1,observationVersion:'a'.repeat(64),items:[],total:0,truncated:false}};
  assert.equal(action,'workspace.work.wait');
  return new Promise(resolve=>{pending.push(resolve)});
 };
 const element=()=>render(React.createElement(WorkSurface,{shell,state,dispatch,act:async(...args)=>acts.push(args)}));
 try{
  await act(async()=>{root=create(element())});
  assert.equal(root.root.findAllByProps({'aria-label':'Shared work'}).length,1);
  const tabs=root.root.findByProps({'aria-label':'Workspace view'});
  assert.deepEqual(tabs.findAllByType('button').map(node=>node.children.join('')),['Chats','Work','Files','Details']);
  assert.deepEqual(calls.map(([name])=>name),['workspace.work.list','workspace.work.wait']);
  assert.ok(calls.every(([,args,meta])=>args.workspaceId==='b'&&meta.presentation===true&&meta.feedback===false&&meta.signal));
  assert.deepEqual(acts,[]);
  state={...state,view:{...state.view,workSurface:'chat'}};
  await act(async()=>root.update(element()));
  assert.equal(root.root.findAllByProps({'aria-label':'Shared work'}).length,0);
  assert.ok(calls.every(([, ,meta])=>meta.signal.aborted),'Hidden Work releases every reader');
 }finally{await act(async()=>{root?.unmount();for(const resolve of pending)resolve({accepted:false,error:'aborted'})})}
});
test('Shared Work explicit Open chat uses the existing dispatcher, not presentation writes or chat selection on inspection',async()=>{
 const current={...snapshot},calls=[];
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>({...host,getSnapshot:()=>current})};
 const item={id:'work:wire',workspaceId:'b',revision:1,promiseRevision:1,title:'Wiring promise',ownerSessionId:'owner',originSessionId:'owner',commitment:'accepted',disposition:'open'};
 const state={view:{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'work'},workspaces:[workspace]};
 let root,release;const dispatch=async(action,args,meta)=>{
  calls.push([action,args,meta]);
  if(action==='workspace.work.list')return {accepted:true,result:{workspaceId:'b',version:1,observationVersion:'a'.repeat(64),items:[item],total:1,truncated:false}};
  if(action==='workspace.work.wait')return new Promise(resolve=>{release=resolve});
  if(action==='workspace.work.read')return {accepted:true,result:{item:{...item,resources:[],links:[],criteria:[],constraints:[]}}};
  assert.equal(action,'session.select');return {accepted:false,error:'Dirty settings retained.'};
 };
 try{
  await act(async()=>{root=create(render(React.createElement(WorkSurface,{shell,state,dispatch,act:()=>{throw Error('Wrong dispatcher')}})))});
  await act(async()=>root.root.findByProps({'data-work-id':'work:wire'}).props.onClick());
  assert.ok(calls.every(([name])=>name.startsWith('workspace.work.')));
  await act(async()=>root.root.findByProps({'aria-label':'Open chat: owner'}).props.onClick());
  assert.deepEqual(calls.at(-1),['session.select',{id:'owner'},undefined]);
  assert.ok(root.root.findAllByProps({role:'alert'}).some(node=>node.children.join('').includes('Dirty settings retained')));
 }finally{await act(async()=>{root?.unmount();release?.({accepted:false,error:'aborted'})})}
});
test('omitted scoped workspace page displays loading instead of a false empty result',()=>{
 const current={...snapshot,navigationResources:['pinnedNavigation','recentNavigation','workspaceShortcuts']};
 delete current.workspaceExplorer;
 const scopedHost={...host,getSnapshot:()=>current};
 const shell={composition:{instances:[{id:'chats',package:'builtin.chats'}]},hostFor:()=>scopedHost};
 const main=renderToStaticMarkup(render(React.createElement(WorkSurface,{shell,state:{view:{workSurface:'workspaces'}},act:host.dispatch})));
 assert.match(main,/Loading workspaces…/);assert.doesNotMatch(main,/Create a workspace to start a chat/);
});
test('workspace shortcut browses without changing execution scope',async()=>{
 const calls=[],selects=[];let root;
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state:snapshot,compact:true,act:async(...args)=>calls.push(args),onSelect:row=>selects.push(row.workspaceId)}))});
 await act(async()=>root.root.findByProps({'aria-label':'Open chats in /research'}).props.onClick());
 assert.deepEqual(selects,['b']);assert.deepEqual(calls,[]);
 assert.deepEqual(browsePatch('workspace','b'),{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'chats'});
 await act(async()=>root.unmount());
});
test('row New chat is an adjacent standalone action bound to the row, not the global folder',async()=>{
 const calls=[],selects=[];let root;
 const state={...snapshot,settings:{workspace:'/wrong'},view:{workWorkspaceId:'elsewhere'}};
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state,compact:true,act:async(...args)=>{calls.push(args);return {accepted:true}},onSelect:row=>selects.push(row.workspaceId)}))});
 const button=root.root.findByProps({'aria-label':'New chat in /research'});
 assert.equal(button.type,'button');assert.equal(button.props.disabled,false);
 assert.notEqual(button.parent.type,'button');
 assert.equal(button.parent.findByProps({'aria-label':'Details and actions for Research'}).type,'button');
 let stopped=false;
 await act(async()=>button.props.onClick({stopPropagation(){stopped=true}}));
 assert.equal(stopped,true);assert.deepEqual(selects,[]);
 assert.deepEqual(calls,[['session.draft',{workspace:'/research',workspaceId:'b',location:{kind:'workspace'}}]]);
 await act(async()=>root.update(React.createElement(WorkspaceExplorer,{state:{...state,workspaceShortcuts:[{...row,available:false}]},compact:true,act:host.dispatch})));
 assert.equal(root.root.findByProps({'aria-label':'New chat in /research'}).props.disabled,true);
 await act(async()=>root.update(React.createElement(WorkspaceExplorer,{state:{...state,workspaceShortcuts:[{...row,path:'../invalid'}]},compact:true,act:host.dispatch})));
 assert.equal(root.root.findByProps({'aria-label':'New chat in ../invalid'}).props.disabled,true);
 await act(async()=>root.unmount());
});
test('workspace flyout pins and reorders the complete workspace vector, separately from chats',async()=>{
 const calls=[];let root,details;
 const state={...snapshot,pinnedWorkspaceIds:['unavailable','b','off-page'],pinnedSessionIds:['chat-pin'],workspaceShortcuts:[{...row,pinned:true}]};
 await act(async()=>{root=create(React.createElement(WorkspaceExplorer,{state,compact:true,act:async(...args)=>calls.push(args)}))});
 const navigationRow=root.root.find(node=>typeof node.props.details==='function'&&node.props.label==='Research');
 await act(async()=>{details=create(navigationRow.props.details({close(){}}))});
 await act(async()=>details.root.findByProps({'aria-label':'Move workspace up /research'}).props.onClick());
 assert.deepEqual(calls[0],['workspace.pinOrder',{ids:['b','unavailable','off-page']}]);
 await act(async()=>details.root.findByProps({'aria-label':'Move workspace down /research'}).props.onClick());
 assert.deepEqual(calls[1],['workspace.pinOrder',{ids:['unavailable','off-page','b']}]);
 await act(async()=>details.root.findByProps({'aria-label':'Unpin workspace /research'}).props.onClick());
 assert.deepEqual(calls[2],['workspace.pin',{id:'b',pinned:false}]);
 assert.deepEqual(state.pinnedWorkspaceIds,['unavailable','b','off-page']);
 assert.deepEqual(state.pinnedSessionIds,['chat-pin']);
 await act(async()=>details.unmount());await act(async()=>root.unmount());
});
test('browsing suppresses the retained canvas host',()=>{
 const state={view:{},selectedSessionId:'a',canvas:{open:true},sessions:[{id:'a',messages:[]}]};
 const html=renderToStaticMarkup(React.createElement(AgentCanvas,{state,act:host.dispatch,suppressed:true}));
 assert.match(html,/id="workspace-canvas"[^>]*hidden=""/);assert.match(html,/Chat overview/);assert.doesNotMatch(html,/No attachments in the loaded messages/);
});
test('workspace choices retain draft model and bundle',()=>{
 const setup={workspace:'/research',location:{kind:'workspace'},bundle:'custom',selection:{instance:'provider',model:'actual',effort:'high'}};
 assert.deepEqual(newChatSetup({view:{newSessionDraft:setup}}),{title:'',...setup});
});
test('new chat has no hidden global-folder fallback and respects visible workspace context',()=>{
 const state={settings:{workspace:'/hidden'},workspaces:[workspace],selectedWorkspaceId:'b',view:{}};
 assert.equal(newChatSetup(state).workspace,'/research');
 assert.equal(newChatSetup({...state,selectedWorkspaceId:null}).location.kind,'managed');
 assert.equal(newChatSetup({...state,view:{workSurface:'chats'}}).workspace,'');
 assert.equal(newChatSetup({...state,selectedWorkspaceId:null,view:{workSurface:'workspace',workWorkspaceId:'b'}}).workspace,'/research');
 assert.equal(newChatSetup({...state,view:{newSessionDraft:{workspace:'/explicit'}}}).location.kind,'workspace');
});
test('collapsed navigation gives all space back, including between chat and canvas',()=>{
 const collapsed=fitPanels({available:1000,canvasOpen:true,canvasWidth:900});
 assert.equal(collapsed.nav,0);assert.equal(collapsed.canvas,628);
 assert.equal(collapsed.nav+collapsed.canvas+360+12,1000);
 assert.equal(fitPanels({available:675,canvasOpen:true}).overlay,false);
 assert.equal(fitPanels({available:671,canvasOpen:true}).overlay,true);
 const expanded=fitPanels({available:1000,navPinned:true,canvasOpen:true});
 assert.ok(expanded.nav+expanded.canvas+360+24<=1000);
});
test('collapsed sidebar toggle lives in the header without duplicate chat controls',()=>{
 const html=renderToStaticMarkup(render(React.createElement(WorkHeader,{state:{view:{}},session:{id:'a',title:'Chat'},presentation:{},act:host.dispatch})));
 assert.match(html,/Open navigation/);assert.doesNotMatch(html,/Chat controls/);
 assert.match(html,/aria-label="Chat"[^>]*><svg[^>]*lucide-message-circle/);
 assert.doesNotMatch(html,/<span>Overview<\/span>|lucide-ellipsis/);
 const expanded=renderToStaticMarkup(render(React.createElement(WorkHeader,{state:{view:{navPinned:true}},presentation:{},act:host.dispatch})));
 assert.doesNotMatch(expanded,/Open navigation/);
 const draft=renderToStaticMarkup(render(React.createElement(WorkHeader,{state:{workspaces:[workspace],selectedWorkspaceId:'b',view:{newSessionDraft:{workspace:'',location:{kind:'managed'}}}},presentation:{},act:host.dispatch})));
 assert.doesNotMatch(draft,/Research/);
});
test('browsing headers have no return control with or without a retained chat',()=>{
 for(const surface of ['home','workspace','workspaces','chats'])for(const narrow of [false,true])for(const session of [undefined,{id:'a',title:'Retained chat',status:'working'}]){
  const state={view:{workSurface:surface,workWorkspaceId:'b'},workspaces:[workspace]};
  const html=renderToStaticMarkup(render(React.createElement(WorkHeader,{state,session,narrow,presentation:{},act:host.dispatch})));
  assert.doesNotMatch(html,/Back to chat|>Back<|Retained chat|Resume|Return to draft|Go to Home/);
  assert.match(html,/Open navigation/);
 }
});

const filesState=()=>({selectedSessionId:'original',selectedWorkspaceId:'b',sessions:[{id:'original',title:'Research notes',workspace:'/research'}],
 view:{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'files'},
 locationListing:{controlId:'workspace-files-b',path:'/research',entries:[{name:'plan.md',path:'/research/plan.md',directory:false}]}});
async function filesFixture(result){
 const calls=[];let state=filesState(),finish,root;
 const dispatch=async(name,args)=>{
  calls.push([name,args]);
  if(name==='canvas.openFile')return result?result:new Promise(resolve=>{finish=resolve});
  return {accepted:true};
 };
 const element=()=>React.createElement(WorkspaceFiles,{workspace,state,act:dispatch});
 await act(async()=>{root=create(element())});
 return {calls,root,button:()=>root.root.findByProps({'data-action':'canvas.openFile'}),
  change:async patch=>{state={...state,...patch};await act(async()=>root.update(element()))},
  settle:async value=>{await act(async()=>finish(value))}};
}
test('file preview names its captured destination in one host command with no follow-up navigation',async()=>{
 const fixture=await filesFixture();
 assert.ok(fixture.button().findByType('small').children.includes('Open in “Research notes”'));
 await act(async()=>{fixture.button().props.onClick();fixture.button().props.onClick()});
 assert.equal(fixture.button().props.disabled,true);
 assert.equal(fixture.calls.filter(([name])=>name==='canvas.openFile').length,1);
 await fixture.settle({accepted:true,result:{status:'opened',id:'artifact'}});
 assert.deepEqual(fixture.calls.slice(1),[
  ['canvas.openFile',{sessionId:'original',workspace:'/research',path:'/research/plan.md',showConversation:true}],
 ]);
 await act(async()=>fixture.root.unmount());
});
for(const result of [
 {accepted:true,result:{status:'unavailable',message:'File unavailable'}},
 {accepted:false,error:'Dirty editor'},
 {accepted:true},
])test('file preview does not navigate without an opened receipt: '+JSON.stringify(result),async()=>{
 const fixture=await filesFixture(result);
 await act(async()=>fixture.button().props.onClick());
 assert.equal(fixture.calls.filter(([name])=>name==='session.select'||name==='view.update').length,0);
 assert.ok(fixture.root.root.findByProps({role:'alert'}));
 assert.equal(!!fixture.button().props.disabled,false);
 await act(async()=>fixture.root.unmount());
});
for(const [name,patch] of [
 ['selected chat',{selectedSessionId:'other'}],
 ['selected workspace',{selectedWorkspaceId:'elsewhere'}],
 ['pending same-chat selection',{navigationPending:true}],
 ['browsed workspace',{view:{workSurface:'workspace',workWorkspaceId:'other',workWorkspaceTab:'files'}}],
 ['surface',{view:{workSurface:'chats'}}],
 ['tab',{view:{workSurface:'workspace',workWorkspaceId:'b',workWorkspaceTab:'details'}}],
])test('late file preview never navigates after changing '+name+' and returning',async()=>{
 const fixture=await filesFixture();
 await act(async()=>{fixture.button().props.onClick()});
 await fixture.change(patch);
 await fixture.change({...filesState(),navigationPending:false});
 await fixture.settle({accepted:true,result:{status:'opened',id:'artifact'}});
 assert.equal(fixture.calls.filter(([name])=>name==='session.select'||name==='view.update').length,0);
 await act(async()=>fixture.root.unmount());
});
test('unmounted file browser drops late navigation; duplicate titles expose the exact destination',async()=>{
 const fixture=await filesFixture();
 const original=filesState();
 await fixture.change({sessions:[...original.sessions,{...original.sessions[0],id:'duplicate'}]});
 assert.ok(fixture.button().findByType('small').children.includes('Open in “Research notes” · original'));
 await act(async()=>{fixture.button().props.onClick()});
 await act(async()=>fixture.root.unmount());
 await fixture.settle({accepted:true,result:{status:'opened'}});
 assert.equal(fixture.calls.filter(([name])=>name==='session.select').length,0);
});
test('an opened receipt never selects or restores an artifact that has since been replaced',async()=>{
 const fixture=await filesFixture();
 await act(async()=>{fixture.button().props.onClick()});
 await fixture.change({canvas:{id:'newer-artifact',open:true}});
 await fixture.settle({accepted:true,result:{status:'opened',id:'original-artifact'}});
 assert.deepEqual(fixture.calls.map(([name])=>name),['locations.list','canvas.openFile']);
 await act(async()=>fixture.root.unmount());
});
test('folder navigation cannot displace a pending named file preview',async()=>{
 const fixture=await filesFixture(),base=filesState().locationListing;
 await fixture.change({locationListing:{...base,entries:[...base.entries,{name:'docs',path:'/research/docs',directory:true}]}});
 const folder=()=>fixture.root.root.findAllByType('button').find(button=>button.findAllByType('span').some(span=>span.children.includes('docs')));
 await act(async()=>folder().props.onClick());
 await fixture.change({locationListing:{...base,path:'/research/docs',parent:'/research',entries:[base.entries[0],{name:'docs',path:'/research/docs/nested',directory:true}]}});
 const parent=()=>fixture.root.root.findByProps({'aria-label':'Parent folder'});
 assert.equal(parent().props.disabled,false);
 await act(async()=>{fixture.button().props.onClick();folder().props.onClick();parent().props.onClick()});
 assert.equal(folder().props.disabled,true);assert.equal(parent().props.disabled,true);
 assert.equal(fixture.root.root.findByType('code').children.join(''),'/research/docs');
 await fixture.settle({accepted:true,result:{status:'unavailable'}});
 assert.equal(parent().props.disabled,false);
 await act(async()=>fixture.root.unmount());
});
test('workspace picker searches the host catalog, pages results, and keeps actions separate',async()=>{
 let root;const calls=[],choices=[];
 const dispatch=async(name,args)=>{calls.push({name,args});return {accepted:true,result:{items:[{id:'w'+args.offset,name:'Reports',path:'/deep/team/'+(args.query||'q1')+'/reports'}],nextOffset:args.offset===0?40:null,total:41}}};
 await act(async()=>{root=create(React.createElement(WorkspacePicker,{state:{},setup:{workspace:'',location:{kind:'managed'}},act:dispatch,onChange:value=>choices.push(value)}))});
 await act(async()=>await new Promise(resolve=>setTimeout(resolve,20)));
 assert.equal(root.root.findAllByType('select').length,0);
 assert.deepEqual(calls[0],{name:'workspace.list',args:{query:'',offset:0,limit:40}});
 const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
 await act(async()=>button('Next').props.onClick());
 await act(async()=>await new Promise(resolve=>setTimeout(resolve,20)));
 assert.equal(calls.at(-1).args.offset,40);
 await act(async()=>root.root.findByProps({'aria-label':'Find a workspace'}).props.onChange({target:{value:'q4'}}));
 await act(async()=>await new Promise(resolve=>setTimeout(resolve,180)));
 assert.deepEqual(calls.at(-1).args,{query:'q4',offset:0,limit:40});
 await act(async()=>root.root.findByProps({'aria-label':'Reports · /deep/team/q4/reports'}).props.onClick());
 assert.deepEqual(choices,[{workspace:'/deep/team/q4/reports',location:{kind:'workspace'}}]);
 assert.ok(button('New workspace'));assert.ok(button('Use existing folder'));
 await act(async()=>root.unmount());
});

test('browsing has no remembered-chat strip but retains call controls and screen sharing',async()=>{
 const calls=[];let root;
 const sharing=React.createElement('div',{'aria-label':'Screen sharing active'},'Stop sharing');
 // Legacy props deliberately exercise the removed branch as a regression.
 const retained={browsing:true,working:true,session:{id:'running',title:'Report'},onReturn:()=>calls.push(['return'])};
 await act(async()=>{root=create(React.createElement(LiveChatActivity,{...retained,act:async(...args)=>calls.push(args)},sharing))});
 assert.equal(root.root.findAllByType('button').length,0);
 assert.ok(root.root.findByProps({'aria-label':'Screen sharing active'}));
 await act(async()=>root.update(React.createElement(LiveChatActivity,{...retained,callActive:true,voice:{status:'connected',muted:false},act:async(...args)=>calls.push(args)},sharing)));
 assert.equal(root.root.findAllByProps({'data-action':'conversation.stop'}).length,0);
 await act(async()=>root.root.findByProps({'data-action':'call.end'}).props.onClick());
 await act(async()=>root.root.findByProps({'data-action':'call.mute'}).props.onClick());
 assert.deepEqual(calls,[['call.end'],['call.mute',{muted:true}]]);
 assert.ok(root.root.findByProps({'aria-label':'Screen sharing active'}));
 assert.ok(root.root.findAllByType('button').every(button=>!button.children.includes('Back to chat')));
 await act(async()=>root.unmount());
});

test('call work stays stoppable in chat and targets the call even after navigating elsewhere',async()=>{
 const calls=[];let root;
 const callSession={id:'voice-chat',title:'Voice task',status:'working'};
 const render=props=>React.createElement(LiveChatActivity,{callActive:true,voice:{status:'connected',muted:true},callSession,act:async(...args)=>calls.push(args),...props});
 await act(async()=>{root=create(render())});
 const stop=()=>root.root.findByProps({'data-action':'conversation.stop'});
 assert.equal(stop().props.disabled,false);
 await act(async()=>stop().props.onClick());
 assert.deepEqual(calls,[['conversation.stop',{sessionId:'voice-chat'}]]);
 assert.match(root.root.findByProps({'data-action':'call.mute'}).props.title,/Unmute/);
 assert.match(root.root.findByProps({'data-action':'call.end'}).props.title,/work continues/);
 assert.ok(root.root.findAllByType('small').some(node=>node.children.includes('Microphone muted. Replies and work continue.')));
 await act(async()=>root.update(render({callSession:{...callSession,status:'stopping'}})));
 assert.equal(stop().props.disabled,true);
 assert.ok(stop().children.includes('Stopping work…'));
 // An idle foreground turn can still have active delegated work.
 await act(async()=>root.update(render({callSession:{...callSession,status:'idle',workers:[{status:'running'}]}})));
 assert.equal(stop().props.disabled,false);
 await act(async()=>root.update(render({callSession:{...callSession,historyReadOnlyReason:'Moved elsewhere'}})));
 assert.equal(stop().props.disabled,true);
 await act(async()=>root.update(render({callSession:{...callSession,status:'idle'}})));
 assert.equal(root.root.findAllByProps({'data-action':'conversation.stop'}).length,0);
 await act(async()=>root.update(render({callSession:undefined})));
 assert.equal(root.root.findAllByProps({'data-action':'conversation.stop'}).length,0);
 await act(async()=>root.unmount());
});
test('quiet navigation retains rows without refresh text and keeps failures and refresh access',()=>{
 const state={...snapshot,sharedHistory:{loading:true,issueCount:2,error:'History unavailable'}};
 const html=renderToStaticMarkup(render(React.createElement(ConversationList,{host:{...host,getSnapshot:()=>state}})));
 assert.doesNotMatch(html,/Finding existing chats|Some saved folders or chats need attention/);
 assert.match(html,/Research ideas/);
 assert.match(html,/History unavailable/);assert.match(html,/Refresh workspaces and chats/);
});


function recentFixture(limit=20,showAgentCreated=false,viewRevision=0,total=125){
 const items=Array.from({length:Math.min(limit,total)},(_,i)=>({id:'recent-'+i,title:'Recent '+i,workspace:'/research',workspaceId:'b'}));
 return {items,total,remaining:total-items.length,end:items.length,limit,dataRevision:viewRevision,
  scope:{mode:'all',workspaceId:null,filter:'',selectedSessionId:'a',section:'shortcuts',showAgentCreated,limit,viewRevision}};
}
test('quiet Recent grows past 100 to natural exhaustion with only an explicit keyboard fallback',async()=>{
 let state={...snapshot,recentNavigation:recentFixture(20,false,0,101),view:{navRecentView:{navFilter:'retained'}}},root;
 const calls=[],listeners=new Set(),browsed=[];
 const dynamic={...host,getSnapshot:()=>state,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)},dispatch:async(name,args)=>{
  calls.push([name,args]);const {navRecentLimit,navShowAgentCreated}=args.patch;
  state={...state,view:{...state.view,...args.patch},recentNavigation:recentFixture(navRecentLimit,navShowAgentCreated,calls.length,101)};
  listeners.forEach(fn=>fn());return {accepted:true};
 }};
 await act(async()=>{root=create(React.createElement(WorkNavigationContext.Provider,{value:{...navigation,browse:kind=>browsed.push(kind)}},React.createElement(ConversationList,{host:dynamic})))});
 const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
 const rows=()=>root.root.findAll(node=>node.props['data-session-id']?.startsWith('recent-')&&node.type==='div');
 assert.equal(calls.length,0,'mount never fills the viewport');
 for(const limit of [20,40,60,80,100,120]){
  assert.equal(rows().length,Math.min(limit,101));
  assert.equal(new Set(rows().map(row=>row.props['data-session-id'])).size,rows().length);
  if(limit<120){
   assert.equal(button('Load older chats').type,'button');
   assert.ok(button('Load older chats').props.style.minHeight>=44);
   await act(async()=>button('Load older chats').props.onClick());
  }
 }
 assert.equal(button('Load older chats'),undefined);
 assert.equal(button('View all chats'),undefined);assert.equal(button('All chats'),undefined);
 assert.equal(button('Show agent-created'),undefined);
 assert.equal(rows().length,101);assert.equal(state.view.navRecentLimit,120);
 assert.deepEqual(state.view.navRecentView,{navFilter:'retained'});
 assert.deepEqual(browsed,[]);
 state={...state,recentNavigation:recentFixture(120,false,7,7)};
 await act(async()=>listeners.forEach(fn=>fn()));
 assert.equal(rows().length,7);assert.equal(button('Load older chats'),undefined);assert.equal(button('View all chats'),undefined);
 assert.equal(button('All chats'),undefined);
 assert.ok(calls.every(([name,args])=>name==='view.update'&&Object.keys(args.patch).sort().join(',')==='navRecentLimit,navShowAgentCreated'));
 await act(async()=>root.unmount());
});


let recentRead=async()=>{throw Error('Unexpected resource read')};
const recentHost=options=>({subscribe:()=>()=>{},refreshResources:()=>recentRead(),...options});
async function timedOutChange(t,api,value){
 t.mock.timers.enable({apis:['setTimeout']});
 try{let pending;await act(async()=>{pending=api.change(value)});await act(async()=>{t.mock.timers.tick(10000);await pending})}
 finally{t.mock.timers.reset()}
}

test('failed Recent read retains rows; explicit Retry reads only current resources, never a write replay',async t=>{
 let current={...snapshot,recentNavigation:recentFixture()},api,root;
 const calls=[];
 function Probe(){api=useRecentShortcuts(recentHost({clientId:'test-client',instanceId:'chats',getSnapshot:()=>current}),current,async(...args)=>{calls.push(args);return {accepted:true}});return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await timedOutChange(t,api,{limit:40});
 assert.equal(api.page.items.length,20);assert.match(api.error,/previous chats are kept/);
 const previousRead=recentRead,reads=[];
 recentRead=async()=>{reads.push('resources');return {recentNavigation:recentFixture(40,false,1)}};
 try{
  await act(async()=>api.retry());
  assert.equal(api.page.items.length,40);assert.equal(api.error,'');
  assert.equal(calls.length,1);assert.equal(reads.length,1);assert.equal(reads[0],'resources');
 }finally{recentRead=previousRead;await act(async()=>root.unmount())}
});

test('change and retry share synchronous singleflight; a late response cannot replace a changed scope',async()=>{
 let current={...snapshot,recentNavigation:recentFixture()},api,root;
 const held=[];
 function Probe(){api=useRecentShortcuts(recentHost({getSnapshot:()=>current}),current,()=>new Promise(resolve=>held.push(resolve)));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 let first;
 await act(async()=>{
  first=api.change({showAgentCreated:true});
  api.change({showAgentCreated:false});api.retry();
 });
 assert.equal(held.length,1);
 current={...current,selectedSessionId:'other',recentScope:{mode:'workspace',workspaceId:'other',filter:''},recentNavigation:{...recentFixture(20,true,1),
  scope:{...recentFixture(20,true,1).scope,selectedSessionId:'a'}}};
 await act(async()=>{held[0]({accepted:true});await first;root.update(React.createElement(Probe))});
 assert.deepEqual(api.page.items,[],'old-scope rows cannot appear under the new selection');
 assert.match(api.error,/previous chats are kept/);
 current={...current,selectedSessionId:'a',recentScope:undefined,recentNavigation:undefined};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.equal(api.page.scope.viewRevision,0);assert.equal(api.page.scope.showAgentCreated,false);
 assert.equal(api.page.items.length,20,'accepted prior scope is retained without accepting the late response');
 await act(async()=>root.unmount());
});

test('Retry prefers a newer host snapshot received while its read was delayed',async t=>{
 let current={...snapshot,recentNavigation:recentFixture(40,false,1)},api,root;
 const writes=[];
 function Probe(){api=useRecentShortcuts(recentHost({clientId:'retry-client',instanceId:'chats',getSnapshot:()=>current}),current,async(...args)=>{writes.push(args);return {accepted:true}});return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await timedOutChange(t,api,{limit:60});
 assert.equal(api.page.limit,40);assert.equal(api.blocked,true);
 const previousRead=recentRead;
 let resolveRead;
 recentRead=()=>new Promise(resolve=>{resolveRead=resolve});
 try{
  let retry;
  await act(async()=>{retry=api.retry()});
  current={...current,view:{navRecentLimit:80,navShowAgentCreated:true},recentNavigation:recentFixture(80,true,3)};
  await act(async()=>root.update(React.createElement(Probe)));
  await act(async()=>{
   resolveRead({recentNavigation:recentFixture(60,false,2)});
   await retry;
  });
  assert.equal(api.page.limit,80);assert.equal(api.settings.showAgentCreated,true);
  assert.equal(api.page.scope.viewRevision,3);assert.equal(api.blocked,false);
  assert.equal(writes.length,1);
 }finally{recentRead=previousRead;await act(async()=>root.unmount())}
});

test('failed Load older requires reconciliation and never skips to a larger limit',async()=>{
 let root;const writes=[];
 const state={...snapshot,recentNavigation:recentFixture()};
 await act(async()=>{root=create(render(React.createElement(ConversationList,{host:{...host,refreshResources:()=>recentRead(),getSnapshot:()=>state,dispatch:async(...args)=>{writes.push(args);throw Error('Unconfirmed Recent write')}}})))});
 const button=text=>root.root.findAllByType('button').find(node=>node.children.includes(text));
 await act(async()=>button('Load older chats').props.onClick());
 assert.equal(button('Load older chats').props['aria-disabled'],true);
 await act(async()=>button('Load older chats').props.onClick());
 assert.equal(button('Show agent-created'),undefined);
 assert.equal(writes.length,1);assert.equal(writes[0][1].patch.navRecentLimit,40);
 assert.ok(button('Retry'));
 const previousRead=recentRead;
 let resolveRead;
 recentRead=()=>new Promise(resolve=>{resolveRead=resolve});
 try{
  let retry;
  await act(async()=>{retry=button('Retry').props.onClick();button('Retry').props.onClick()});
  assert.equal(button('Load older chats').props['aria-disabled'],true);
  assert.equal(button('Show agent-created'),undefined);
  assert.equal(writes.length,1);
  await act(async()=>{
   resolveRead({recentNavigation:recentFixture()});
   await retry;
  });
  assert.equal(button('Load older chats').props['aria-disabled'],false);
 }finally{recentRead=previousRead;await act(async()=>root.unmount())}
});

test('old matching limit with no new revision is not a successful write acknowledgement',async t=>{
 let api,root;const current={...snapshot,recentNavigation:recentFixture()};
 function Probe(){api=useRecentShortcuts(recentHost({getSnapshot:()=>current}),current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await timedOutChange(t,api,{showAgentCreated:false});
 assert.equal(api.blocked,true);assert.equal(api.page.scope.viewRevision,0);
 assert.match(api.error,/previous chats are kept/);
 await act(async()=>root.unmount());
});

test('expanded Recent scope uses independent host scope, not a late response self-matching its identity',async()=>{
 const expected={mode:'workspace',workspaceId:'b',filter:'',clientId:'scope-client',instanceId:'chats',generation:2};
 let current={...snapshot,generation:2,recentScope:expected,recentNavigation:{...recentFixture(),
  scope:{...recentFixture().scope,...expected}}},api,root;
 const scopedHost=recentHost({clientId:'scope-client',instanceId:'chats',getSnapshot:()=>current});
 function Probe(){api=useRecentShortcuts(scopedHost,current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 for(const [key,value] of Object.entries({mode:'all',workspaceId:'elsewhere',clientId:'other',instanceId:'other',generation:3})){
  current={...current,recentNavigation:{...recentFixture(40,false,10),
   scope:{...recentFixture(40,false,10).scope,...expected,[key]:value}}};
  await act(async()=>root.update(React.createElement(Probe)));
  assert.equal(api.page.limit,20,key);
 }
 await act(async()=>root.unmount());
});

test('a stale origin scope cannot self-match after the saved origin setting changes',async()=>{
 let current={...snapshot,recentNavigation:recentFixture(),view:{navShowAgentCreated:false}},api,root;
 function Probe(){api=useRecentShortcuts(recentHost({getSnapshot:()=>current}),current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 current={...current,view:{navShowAgentCreated:true},recentNavigation:recentFixture(40,false,2)};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.equal(api.page.items.length,0,'changed origin filter cannot show the old filter result');
 current={...current,recentNavigation:recentFixture(40,true,2)};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.equal(api.page.limit,40);assert.equal(api.page.scope.showAgentCreated,true);
 await act(async()=>root.unmount());
});

for(const registration of ['missing','unavailable'])test(`Recent retains canonical null scope for a ${registration} pinned workspace`,async()=>{
 const effective={mode:'workspace',workspaceId:null,filter:'',clientId:'pinned-client',instanceId:'chats',generation:2};
 const empty=(limit,revision)=>({...recentFixture(limit,false,revision,0),scope:{...recentFixture(limit,false,revision,0).scope,...effective}});
 let current={...snapshot,selectedWorkspaceId:'pinned-missing',generation:2,recentScope:effective,
  workspaces:registration==='missing'?[]:[{id:'pinned-missing',path:'/gone',available:false}],
  recentNavigation:empty(20,0)},api,root;
 const scopedHost=recentHost({clientId:'pinned-client',instanceId:'chats',getSnapshot:()=>current});
 function Probe(){api=useRecentShortcuts(scopedHost,current,async(_name,{patch})=>{
  current={...current,view:{...current.view,...patch},recentNavigation:empty(patch.navRecentLimit,1)};
  return {accepted:true};
 });return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await act(async()=>api.change({limit:40}));
 assert.equal(api.error,'');assert.equal(api.blocked,false);
 assert.equal(api.page.limit,40);assert.equal(api.page.scope.workspaceId,null);assert.deepEqual(api.page.items,[]);
 // The registered restoration changes effective scope, not the selected ID.
 const restoredScope={...effective,workspaceId:'pinned-missing'};
 const restored={...recentFixture(40,false,2,1),items:[{id:'restored',workspaceId:'pinned-missing'}],
  scope:{...recentFixture(40,false,2,1).scope,...restoredScope}};
 current={...current,recentScope:restoredScope,workspaces:[{id:'pinned-missing',path:'/restored',available:true}],recentNavigation:restored};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.deepEqual(api.page.items,restored.items);assert.equal(api.page.scope.workspaceId,'pinned-missing');
 const previousRead=recentRead;let release;
 recentRead=()=>new Promise(resolve=>{release=resolve});
 try{
  let pending;await act(async()=>{pending=api.retry()});
  // Delayed old response must not self-match even with a larger revision.
  current={...current,recentNavigation:empty(40,99)};
  await act(async()=>root.update(React.createElement(Probe)));
  await act(async()=>{
   release({recentNavigation:empty(40,99)});
   await pending;
  });
  assert.deepEqual(api.page.items,restored.items);assert.equal(api.page.scope.workspaceId,'pinned-missing');
  assert.match(api.error,/changed during the read/);
 }finally{recentRead=previousRead;await act(async()=>root.unmount())}
});


test('Recent receipt stays pending until its exact scoped page arrives through the stream',async()=>{
 let current={...snapshot,recentNavigation:recentFixture()},api,root;const listeners=new Set(),writes=[];
 const scopedHost=recentHost({getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}});
 function Probe(){api=useRecentShortcuts(scopedHost,current,async(...args)=>{writes.push(args);return {accepted:true}});return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 let pending;await act(async()=>{pending=api.change({limit:40})});
 assert.equal(api.busy,true);assert.equal(api.error,'');assert.equal(api.page.limit,20);assert.equal(listeners.size,1);
 current={...current,view:{navRecentLimit:40},recentNavigation:recentFixture(40,false,1)};
 await act(async()=>{listeners.forEach(fn=>fn());await pending;root.update(React.createElement(Probe))});
 assert.equal(api.busy,false);assert.equal(api.error,'');assert.equal(api.page.limit,40);assert.equal(listeners.size,0);assert.equal(writes.length,1);
 await act(async()=>root.unmount());
});

test('Recent unmount cancels its projection wait and releases its listener without retrying a write',async()=>{
 let api,root;const current={...snapshot,recentNavigation:recentFixture()},listeners=new Set(),writes=[];
 const scopedHost=recentHost({getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}});
 function Probe(){api=useRecentShortcuts(scopedHost,current,async(...args)=>{writes.push(args);return {accepted:true}});return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 let pending;await act(async()=>{pending=api.change({limit:40})});assert.equal(listeners.size,1);
 await act(async()=>root.unmount());await pending;
 assert.equal(listeners.size,0);assert.equal(writes.length,1);
});

test('cold resource placeholders never claim empty catalogs and retained sidebar updates stay quiet',()=>{
 const pending={...snapshot,navigationResources:[],navigationResourcePending:{
  pinnedNavigation:{status:'pending'},recentNavigation:{status:'pending'},workspaceShortcuts:{status:'pending'}},
  recentNavigation:undefined,recentShortcuts:undefined,workspaceShortcuts:undefined};
 const coldHost={...host,getSnapshot:()=>pending,refreshResources:async()=>{throw Error('No automatic read')}};
 const cold=renderToStaticMarkup(render(React.createElement(ConversationList,{host:coldHost})));
 assert.match(cold,/Loading recent chats/);assert.match(cold,/Loading workspaces/);
 assert.doesNotMatch(cold,/Your recent chats appear here|Create a workspace to start/);
 const updating={...snapshot,navigationResourcePending:{recentNavigation:{status:'pending'}}};
 const warm=renderToStaticMarkup(render(React.createElement(ConversationList,{host:{...coldHost,getSnapshot:()=>updating}})));
 assert.doesNotMatch(warm,/Updating recent chats|Loading recent chats/);
 assert.match(warm,/Research ideas/);assert.match(warm,/aria-busy="true"/);
 const retrying={...updating,navigationResourcePending:{recentNavigation:{status:'retry'}}};
 const failed=renderToStaticMarkup(render(React.createElement(ConversationList,{host:{...coldHost,getSnapshot:()=>retrying}})));
 assert.match(failed,/Retry recent chats/);assert.match(failed,/Research ideas/);
});


test('selected chat changes retain loaded Recent rows while only a current-selection page can settle expansion',async()=>{
 let current={...snapshot,recentNavigation:recentFixture(60,false,3),view:{navRecentLimit:60}},api,root;
 const listeners=new Set();const readHost=recentHost({getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}});
 function Probe(){api=useRecentShortcuts(readHost,current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 let pending;await act(async()=>{pending=api.change({limit:80})});
 current={...current,selectedSessionId:'other',recentNavigation:undefined,navigationResourcePending:{recentNavigation:{status:'pending'}}};
 await act(async()=>{root.update(React.createElement(Probe));listeners.forEach(fn=>fn())});
 assert.equal(api.page.items.length,60);assert.equal(api.error,'');assert.equal(api.busy,true);
 current={...current,view:{navRecentLimit:80},recentNavigation:recentFixture(80,false,4),navigationResourcePending:{}};
 await act(async()=>{root.update(React.createElement(Probe));listeners.forEach(fn=>fn())});
 assert.equal(api.page.items.length,60,'old-selection completion cannot be accepted');
 current={...current,recentNavigation:{...current.recentNavigation,scope:{...current.recentNavigation.scope,selectedSessionId:'other'}}};
 await act(async()=>{root.update(React.createElement(Probe));listeners.forEach(fn=>fn());await pending});
 assert.equal(api.page.items.length,80);assert.equal(api.error,'');assert.equal(api.busy,false);
 await act(async()=>root.unmount());
});

test('Recent retry waits for a pending resource baseline and a failure never also reports loading',async t=>{
 let current={...snapshot,recentNavigation:recentFixture()},api,root;const listeners=new Set();
 const readHost=recentHost({getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}});
 function Probe(){api=useRecentShortcuts(readHost,current,async()=>({accepted:true}));return null}
 await act(async()=>{root=create(React.createElement(Probe))});
 await timedOutChange(t,api,{limit:40});
 current={...current,recentNavigation:undefined,navigationResourcePending:{recentNavigation:{status:'pending'}}};
 await act(async()=>root.update(React.createElement(Probe)));
 assert.equal(api.busy,false);assert.match(api.error,/previous chats/);assert.equal(api.page.items.length,20);
 const old=recentRead;recentRead=async()=>current;
 try{
  let retry;await act(async()=>{retry=api.retry()});assert.equal(api.busy,true);
  current={...current,view:{navRecentLimit:40},recentNavigation:recentFixture(40,false,1),navigationResourcePending:{}};
  await act(async()=>{root.update(React.createElement(Probe));listeners.forEach(fn=>fn());await retry});
  assert.equal(api.page.items.length,40);assert.equal(api.error,'');assert.equal(api.busy,false);
 }finally{recentRead=old;await act(async()=>root.unmount())}
});


test('Recent subscriptions reject a new host before React commits the owner change',async()=>{
 for(const operation of ['change','retry']){
  let current={...snapshot,generation:2,navigationResourceScope:['old-host','bootstrap',1,2],recentNavigation:recentFixture()},api,root;
  let rendered=current;const listeners=new Set(),old=recentRead;
  const readHost=recentHost({getSnapshot:()=>current,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}});
  function Probe(){api=useRecentShortcuts(readHost,rendered,async()=>({accepted:true}));return null}
  await act(async()=>{root=create(React.createElement(Probe))});
  recentRead=async()=>({...current,recentNavigation:undefined,navigationResourcePending:{recentNavigation:{status:'pending'}}});
  try{
   if(operation==='retry')current={...current,recentNavigation:undefined,navigationResourcePending:{recentNavigation:{status:'pending'}}};
   let pending;await act(async()=>{pending=operation==='change'?api.change({limit:40}):api.retry()});
   current={...current,navigationResourceScope:['new-host','bootstrap',1,2],view:{navRecentLimit:40},recentNavigation:recentFixture(40,false,99),navigationResourcePending:{}};
   await act(async()=>{listeners.forEach(fn=>fn());await pending});
   assert.equal(api.page.items.length,20,operation+' cannot accept another host through subscription');
   assert.match(api.error,/Recent changed/);
   rendered=current;await act(async()=>root.update(React.createElement(Probe)));
   assert.equal(api.page.items.length,40,'new owner may independently accept its current page');
  }finally{recentRead=old;await act(async()=>root.unmount())}
 }
});
