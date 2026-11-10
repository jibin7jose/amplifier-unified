import test from 'node:test';
import assert from 'node:assert/strict';
import {chatPage,visibleWorkspaces,workspaceLabel,CHAT_PAGE_SIZE,recentLimit,recentPageMatches} from '../src/chat-navigation.js';
const fixture=()=>({view:{},selectedWorkspaceId:'project',selectedSessionId:'chat-0',workspaces:[{id:'project',path:'/fixture',available:true},{id:'other',path:'/other',available:true}],sessions:Array.from({length:5000},(_,i)=>({id:'chat-'+i,title:'Saved chat '+i,workspaceId:'project',workspace:'/fixture'}))});

test('pinning retains Recent membership and normal sort with stable ties',()=>{
 for(const navSort of ['activity','created','name']){
  const state=fixture();state.view.navSort=navSort;
  state.sessions=[{id:'new',title:'New',recentActivityAt:20,createdAt:20,workspaceId:'project'},
   {id:'old-a',title:'Same',recentActivityAt:10,createdAt:10,workspaceId:'project'},
   {id:'old-b',title:'Same',recentActivityAt:10,createdAt:10,workspaceId:'project'}];
  const rows=section=>chatPage(state,null,{section}).items.map(row=>row.id);
  const expected=rows('recent');
  for(const pins of [['old-b','old-a'],['old-a','old-b'],['old-a'],[]]){
   state.pinnedSessionIds=pins;
   assert.deepEqual(rows('recent'),expected);assert.deepEqual(rows('pinned'),pins);
  }
 }
});

test('Recent accepts safe cumulative steps, keeps requested 120 at actual 101 exhaustion and fences every scope',()=>{
 for(const limit of [20,100,120,5000])assert.equal(recentLimit(limit),limit);
 for(const limit of [0,21,Infinity,Number.MAX_SAFE_INTEGER+1,true,'120'])assert.equal(recentLimit(limit),20);
 const scope={section:'shortcuts',mode:'all',workspaceId:null,filter:'',age:'all',limit:120,showAgentCreated:false,
  selectedSessionId:'selected',clientId:'client',instanceId:'chats',generation:2};
 const page={items:Array.from({length:101},(_,id)=>({id:String(id)})),total:101,end:101,remaining:0,scope};
 assert.equal(recentPageMatches(page,scope),true);
 for(const [key,value] of Object.entries({limit:101,showAgentCreated:true,selectedSessionId:'other',mode:'workspace',
  workspaceId:'other',clientId:'other',instanceId:'other',generation:3,filter:'search',age:'24h'}))
  assert.equal(recentPageMatches(page,{...scope,[key]:value}),false,key);
});

test('Recent canonical null workspace scope does not equal an unavailable selected ID',()=>{
 const scope={section:'shortcuts',mode:'workspace',workspaceId:null,filter:'',limit:40,
  showAgentCreated:false,selectedSessionId:'selected',clientId:'client',instanceId:'chats',generation:2};
 const page={items:[],total:0,end:0,remaining:0,scope};
 assert.equal(recentPageMatches(page,scope),true);
 assert.equal(recentPageMatches(page,{...scope,workspaceId:'missing-registration'}),false);
 assert.equal(recentPageMatches({...page,scope:{...scope,workspaceId:'restored'}},scope),false);
});

test('shared CLI IDs are searchable without replacing internal keys',()=>{
 const state=fixture();
 state.sessions=[{id:'internal-import',runtimeSessionId:'cli-root-123',workspaceId:'project'},
  {id:'other-import',nativeIdentity:'cli-root-123',workspaceId:'other'},
  {id:'unified-root-456',workspaceId:'project'}];
 state.view={navChatScope:'all',navFilter:'cli-root'};state.pinnedSessionIds=['internal-import'];
 const before=structuredClone(state);
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['internal-import','other-import']);
 assert.deepEqual(state,before);
 state.view.navChatScope='workspace';
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['internal-import']);
 state.view.navFilter='unified-root';
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['unified-root-456']);
});

test('workspace navigation includes only verified folders without discarding registrations',()=>{
 const workspaces=[
  {id:'a',name:'project',path:'/Users/me/work/project',available:true},
  {id:'b',name:'project',path:'/Users/me/personal/project',available:true},
  {id:'missing',path:'/old/project',available:false},
  {id:'unknown',path:null,available:false},
  {id:'unchecked',path:'/unchecked/project'},
 ];
 assert.deepEqual(visibleWorkspaces({workspaces}).map(w=>w.id),['a','b']);
 assert.equal(workspaces.length,5);
 workspaces[2].available=true;
 assert.deepEqual(visibleWorkspaces({workspaces}).map(w=>w.id),['a','b','missing']);
});

test('large navigation lists use bounded pages while keeping all chats searchable',()=>{
 const state=fixture(),workspace=state.workspaces[0],first=chatPage(state,workspace);
 assert.equal(first.items.length,CHAT_PAGE_SIZE);assert.equal(first.total,5000);assert.equal(first.pages,50);
 state.view.navChatPage={...first.scope,index:1};
 assert.equal(chatPage(state,workspace).items[0].id,'chat-100');
 state.view.navFilter='Saved chat 4999';
 const filtered=chatPage(state,workspace);assert.equal(filtered.items.length,1);assert.equal(filtered.items[0].id,'chat-4999');assert.equal(filtered.index,0);
 assert.equal(state.sessions.length,5000);
});

test('selection moves to its page while page controls can browse elsewhere',()=>{
 const state=fixture(),workspace=state.workspaces[0];state.selectedSessionId='chat-4901';
 const selected=chatPage(state,workspace);assert.equal(selected.index,49);assert.ok(selected.items.some(chat=>chat.id==='chat-4901'));
 state.view.navChatPage={...selected.scope,index:0};assert.equal(chatPage(state,workspace).index,0);
 state.selectedSessionId='chat-2400';assert.equal(chatPage(state,workspace).index,24);
 state.view.navChatPage={...chatPage(state,workspace).scope,index:900000};assert.equal(chatPage(state,workspace).index,49);
});

test('primary chat lists exclude workers, preserve independent forks, and count only roots',()=>{
 const state=fixture();state.sessions=[
  {id:'root',workspaceId:'project',sessionKind:'root'},
  {id:'worker',title:'Worker-only title',workspaceId:'project',sessionKind:'worker',parentId:'root'},
  {id:'internal',title:'Internal-only title',workspaceId:'project',sessionKind:'internal'},
  {id:'legacy-worker',workspaceId:'project',nativeParentId:'root'},
  {id:'web-fork',workspaceId:'project',parentId:'root',forkTranscript:[]},
  {id:'cli-fork',workspaceId:'project',sessionKind:'root',nativeParentId:'root'},
  {id:'legacy-root',workspaceId:'project',nativeParentId:null},
 ];state.selectedSessionId='worker';
 const expected=['root','web-fork','cli-fork','legacy-root'];
 assert.equal(chatPage(state,state.workspaces[0]).total,4);
 state.view.navFilter='Worker-only';assert.equal(chatPage(state,state.workspaces[0]).total,0);
 state.view.navFilter='Internal-only';assert.equal(chatPage(state,state.workspaces[0]).total,0);
 state.selectedSessionId='internal';state.view.navFilter='';assert.deepEqual(chatPage(state,state.workspaces[0]).items.map(row=>row.id),expected);
 assert.equal(state.sessions.length,7,'worker and internal history stays available in app state');
});

test('legacy forks and edits with native lineage retain independent chat navigation',async()=>{
 const {isTopLevelChat}=await import('../src/chat-navigation.js');
 assert.equal(isTopLevelChat({nativeParentId:'parent',forkTranscript:[{role:'user',content:'Plan'}]}),true);
 assert.equal(isTopLevelChat({nativeParentId:'parent',editOrigin:{turn:1}}),true);
 assert.equal(isTopLevelChat({nativeParentId:'parent',forkTranscript:[]}),false);
 assert.equal(isTopLevelChat({sessionKind:'worker',nativeParentId:'parent',forkTranscript:[{}],editOrigin:{turn:1}}),false,'explicit worker classification wins');
});

test('workspace labels lead with full paths and retain custom names',()=>{
 assert.equal(workspaceLabel({path:'/Users/me/work/project',name:'project'}),'/Users/me/work/project');
 assert.equal(workspaceLabel({path:'/Users/me/work/project',name:'Client work'}),'/Users/me/work/project · Client work');
});

test('all chats include only roots in existing folders and group pins before activity order',()=>{
 const state={view:{navChatScope:'all'},selectedWorkspaceId:'missing',pinnedSessionIds:['pin-old','pin-new','worker','missing'],
  workspaces:[{id:'a',name:'Client work',path:'/work/project',available:true},{id:'b',name:'Personal',path:'/personal/project',available:true},{id:'missing',path:'/gone/project',available:false}],
  sessions:[
   {id:'new',workspaceId:'b',recentActivityAt:40},
   {id:'pin-old',workspaceId:'a',recentActivityAt:1},
   {id:'equal-one',workspaceId:'a',recentActivityAt:30},
   {id:'pin-new',workspaceId:'b',recentActivityAt:2},
   {id:'equal-two',workspace:'/work/project',recentActivityAt:30},
   {id:'worker',workspaceId:'a',sessionKind:'worker',recentActivityAt:100},
   {id:'missing',workspaceId:'missing',recentActivityAt:100},
   {id:'unregistered',workspace:'/elsewhere',recentActivityAt:100},
  ]};
 const before=structuredClone(state),page=chatPage(state);
 assert.deepEqual(page.items.map(row=>row.id),['pin-old','pin-new','new','equal-one','equal-two']);
 assert.deepEqual(page.items.slice(0,2).map(row=>row.pinned),[true,true]);
 assert.equal(page.items[0].workspace,'/work/project');
 assert.equal(page.scope.mode,'all');assert.equal(page.scope.workspaceId,null);
 assert.deepEqual(state,before,'navigation must not mutate recency or session records');
});

test('recent order accepts finite nonnegative activity and ignores metadata-only updates',()=>{
 const state={view:{},selectedWorkspaceId:'w',workspaces:[{id:'w',path:'/work',available:true}],sessions:[
  {id:'zero',workspaceId:'w',recentActivityAt:0,createdAt:100},
  {id:'new',workspaceId:'w',recentActivityAt:9,createdAt:1},
  {id:'invalid',workspaceId:'w',recentActivityAt:Infinity,createdAt:7},
  {id:'negative',workspaceId:'w',recentActivityAt:-1,createdAt:6},
  {id:'old-renamed',workspaceId:'w',createdAt:5,updatedAt:9000},
  {id:'string',workspaceId:'w',recentActivityAt:'10000',createdAt:4},
 ]};
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['new','invalid','negative','old-renamed','string','zero']);
 state.pinnedSessionIds=['old-renamed'];
 assert.equal(chatPage(state).items[0].id,'old-renamed');
 state.pinnedSessionIds=[];
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['new','invalid','negative','old-renamed','string','zero']);
});

test('all-chat search matches full workspace paths and aliases as well as chat metadata',()=>{
 const state={view:{navChatScope:'all'},workspaces:[{id:'w',path:'/Users/me/work/project',name:'Client website',available:true}],sessions:[{id:'id-unique',workspaceId:'w',title:'Release plan',description:'Ship Tuesday'}]};
 for(const filter of ['*work/project','client website','release','Tuesday','id-unique']){
  state.view.navFilter=filter;assert.equal(chatPage(state).total,1,filter);
 }
 state.view.navFilter='*personal*';assert.equal(chatPage(state).total,0);
});

test('all chats open newest page despite an old selection and use scoped bounded paging',()=>{
 const state=fixture();state.selectedSessionId='chat-4901';
 state.view.navChatPage={...chatPage(state,state.workspaces[0]).scope,index:49};
 state.view.navChatScope='all';
 const first=chatPage(state);
 assert.equal(first.index,0);assert.equal(first.items.length,100);assert.equal(first.total,5000);
 state.view.navChatPage={...first.scope,index:2};assert.equal(chatPage(state).items[0].id,'chat-200');
 state.pinnedSessionIds=['chat-4901'];state.view.navChatPage=null;
 assert.equal(chatPage(state).items[0].id,'chat-4901');
});

test('matching server projection wins over partial local summaries and stale scopes fall back',()=>{
 const state=fixture();state.view.navChatScope='all';
 const scope=chatPage(state).scope;
 const projection={scope,items:[{id:'server-only',title:'Authoritative',workspace:'/fixture',pinned:true}],total:5200,index:0,pages:52,start:0,end:100};
 state.chatNavigation=projection;
 assert.equal(chatPage(state),projection);
 state.view.navFilter='Saved chat 4999';
 assert.equal(chatPage(state).items[0].id,'chat-4999');
 state.view.navFilter='';state.view.navChatScope='workspace';
 assert.equal(chatPage(state).items[0].id,'chat-0');
});

test('bounded navigation waits for the real page after optimistic search, scope, or page changes',()=>{
 const state=fixture(),first=chatPage(state);state.chatNavigation=first;state.library={bounded:true};state.sessions=state.sessions.slice(0,100);
 assert.equal(chatPage(state),first);
 state.view.navChatPage={...first.scope,index:1};
 assert.equal(chatPage(state).pending,true);assert.deepEqual(chatPage(state).items,[]);
 state.chatNavigation={...first,index:1,start:100,end:200,items:[{id:'from-next-page'}]};
 assert.equal(chatPage(state),state.chatNavigation);
 state.view.navFilter='outside the published page';assert.equal(chatPage(state).pending,true);
 state.view.navFilter='';state.view.navChatScope='all';assert.equal(chatPage(state).pending,true);
});

test('subagent projection keeps complete counts and waits for changed searches and pages',async()=>{
 const {subagentPage,subagentCount}=await import('../src/chat-navigation.js');
 const parent={id:'root',subagentCount:123},projection={items:[{id:'first'}],total:123,unfilteredTotal:123,index:0,pages:3,start:0,end:50,scope:{sessionId:'root',filter:'',index:0}};
 const state={browserResources:['selectedContext','subagentNavigation'],library:{bounded:true},sessions:[parent],view:{},subagentNavigation:projection};
 assert.equal(subagentCount(state,parent),123);assert.equal(subagentPage(state,parent),projection);
 state.view.subagentHistory={sessionId:'root',filter:'research',index:0};assert.equal(subagentPage(state,parent).pending,true);
 state.subagentNavigation={...projection,scope:{sessionId:'root',filter:'research',index:0},total:60,unfilteredTotal:123,pages:2};
 assert.equal(subagentPage(state,parent),state.subagentNavigation);assert.equal(subagentCount(state,parent),123);
 state.view.subagentHistory.index=1;assert.equal(subagentPage(state,parent).pending,true);
 const other={id:'different-root',subagentCount:5};assert.equal(subagentCount(state,other),5,'a previously browsed parent cannot hide the current parent’s workers');
});


test('managed chats are reachable without workspace rows and location filters cannot reuse a stale page',()=>{
 const state={sessions:[{id:'managed',title:'My chat',workspace:'/app/chats/id/files',location:{kind:'managed'}},{id:'project',workspace:'/project',workspaceId:'project'}],workspaces:[{id:'project',path:'/project',available:true}],view:{navChatScope:'all'},selectedWorkspaceId:'project'};
 assert.deepEqual(new Set(chatPage(state).items.map(row=>row.id)),new Set(['managed','project']));
 assert.equal(chatPage(state).items.find(row=>row.id==='managed').workspaceLabel,'No workspace');
 state.view.navLocationFilter='managed';const managed=chatPage(state);assert.deepEqual(managed.items.map(row=>row.id),['managed']);
 state.chatNavigation=managed;state.library={bounded:true};state.view.navLocationFilter='all';
 assert.equal(chatPage(state).pending,true);
 state.library={};delete state.chatNavigation;state.view.navChatScope='workspace';
 assert.deepEqual(chatPage(state).items.map(row=>row.id),['project']);
});

test('sort choices use ready time and never reorder pins by activity',()=>{
 const state={view:{navChatScope:'all'},workspaces:[{id:'w',path:'/w',available:true}],pinnedSessionIds:['p1','p2'],sessions:[
  {id:'p1',workspaceId:'w',recentActivityAt:1},{id:'p2',workspaceId:'w',recentActivityAt:999},
  {id:'a',workspaceId:'w',title:'Alpha',navigationActivityAt:10,recentActivityAt:999,createdAt:20},
  {id:'b',workspaceId:'w',title:'beta',recentActivityAt:30,createdAt:10},
  {id:'c',workspaceId:'w',title:'Charlie',recentActivityAt:20,createdAt:30}]};
 for(const [sort,expected] of [['activity',['b','c','a']],['created',['c','a','b']],['name',['a','b','c']]]){
  state.view.navSort=sort;
  assert.deepEqual(chatPage(state).items.map(row=>row.id),['p1','p2',...expected]);
 }
});

test('catalog grouping and age use ready activity before paging, retain pins and fence changed preferences',()=>{
 const now=Math.floor(Date.now()/60000)*60;
 const state={view:{navChatScope:'all',navAge:'24h',navGroup:'status'},workspaces:[{id:'w',path:'/w',name:'Workspace',available:true}],pinnedSessionIds:['old-pin'],sessions:[
  {id:'old-pin',workspaceId:'w',recentActivityAt:1},
  {id:'boundary',workspaceId:'w',navigationActivityAt:now-86400,recentActivityAt:now},
  {id:'outside',workspaceId:'w',navigationActivityAt:now-86401,recentActivityAt:now},
  {id:'unknown',workspaceId:'w',recentActivityAt:0},
  ...Array.from({length:120},(_,i)=>({id:'work-'+i,workspaceId:'w',status:'working',recentActivityAt:now-i}))
 ]};
 const first=chatPage(state,null,{section:'search'});
 assert.equal(first.total,122);assert.equal(first.items.length,40);
 assert.equal(first.items[0].id,'old-pin');assert.equal(first.items[0].group.title,'Pinned');
 assert.ok(first.items.slice(1).every(row=>row.group.title==='Working'));
 state.view.navChatPage={...first.scope,index:3};
 const last=chatPage(state,null,{section:'search'});assert.ok(last.items.some(row=>row.id==='boundary'));
 state.view.navAge='all';
 assert.equal(chatPage(state,null,{section:'search'}).index,0);
 const all=chatPage(state,null,{section:'search'});assert.equal(all.total,124);
 state.sidebarNavigation={search:all};state.library={bounded:true};
 state.view.navGroup='workspace';
 assert.equal(chatPage(state,null,{section:'search'}).pending,true);
});
test('moving pins retains every filtered or off-page pin',async()=>{
 const {movePin}=await import('../src/chat-navigation.js');
 const ids=['first','hidden','second','off-page'];
 assert.deepEqual(movePin(ids,'second','first'),['second','first','hidden','off-page']);
 assert.deepEqual(movePin(ids,'first','second'),['hidden','second','first','off-page']);
 assert.equal(movePin(ids,'absent','first'),ids);
 assert.equal(movePin(ids,'first','first'),ids);
});


test('subagent pages require exact coverage and requested scope without scanning resident chats',async()=>{
 const {subagentPage,subagentCount}=await import('../src/chat-navigation.js');
 const parent={id:'root',subagentCount:123},page={items:[],total:0,unfilteredTotal:123,index:0,pages:1,start:0,end:0,scope:{sessionId:'root',filter:'absent',index:9}};
 const state={view:{subagentHistory:{sessionId:'root',filter:'absent',index:9}},subagentNavigation:page};
 Object.defineProperty(state,'sessions',{get(){throw Error('Selected context is not a resident child catalog')}});
 assert.equal(subagentCount(state,parent),123);
 assert.equal(subagentCount(state,{id:'uncovered'}),0);
 for(const coverage of [undefined,null,{},'subagentNavigation',[],['selectedContext'],['subagentNavigation']]){
  state.browserResources=coverage;
  const pending=subagentPage(state,parent);assert.equal(pending.pending,true);assert.deepEqual(pending.items,[]);
 }
 state.browserResources=['selectedContext','subagentNavigation'];
 assert.equal(subagentPage(state,parent),page,'an explicitly covered empty page is authoritative, including clamped index');
 state.subagentNavigation={...page,scope:{...page.scope,index:8}};
 assert.equal(subagentPage(state,parent).pending,true,'same clamped page does not cover another requested index');
 state.subagentNavigation={...page,scope:{...page.scope,sessionId:'other'}};
 assert.equal(subagentPage(state,parent).pending,true);
});
