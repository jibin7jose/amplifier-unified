import {filterList} from './list-filter.js';
import {activityFor,sessionIdentity} from './navigation-presentation.js';

export const CHAT_PAGE_SIZE=100;
export const CHAT_AGES=['24h','7d','14d','30d','90d','all'];
const ageLimits={'24h':86400,'7d':7*86400,'14d':14*86400,'30d':30*86400,'90d':90*86400,'1y':365*86400};
// Date groups retain their established boundaries independently of slider stops.
const dateSeconds=[86400,7*86400,30*86400,90*86400,365*86400];
const dateGroups=['Last 24 hours','1–7 days ago','7–30 days ago','30–90 days ago','90 days–1 year ago','Over 1 year ago','Activity time unavailable'];
const statusGroups={attention:'Needs you',unread:'Ready for you',working:'Working',waiting:'Waiting',paused:'Paused',idle:'Pick up again'};
function listGroup(chat,group,now){
 if(chat.pinned)return {id:'pin',title:'Pinned',rank:-1};
 if(group==='workspace')return {id:'workspace:'+(chat.workspaceId||''),title:chat.workspaceName||'No workspace',rank:0};
 if(group==='status'){const kind=Object.hasOwn(statusGroups,chat.activity.kind)?chat.activity.kind:'idle';return {id:'status:'+kind,title:statusGroups[kind],rank:Object.keys(statusGroups).indexOf(kind)}}
 const index=chat.recentActivityAt>0?dateSeconds.findIndex(seconds=>chat.recentActivityAt>=now-seconds):-1;
 const rank=chat.recentActivityAt>0?(index<0?5:index):6;
 return {id:'date:'+rank,title:dateGroups[rank],rank};
}
export function recentLimit(value){
 return Number.isSafeInteger(value)&&value>=20&&value%20===0?value:20;
}
export function recentPageMatches(page,{limit,showAgentCreated,selectedSessionId,...scope}){
 return Array.isArray(page?.items)&&page.scope?.section==='shortcuts'
  &&recentLimit(page.scope.limit)===page.scope.limit&&typeof page.scope.showAgentCreated==='boolean'
  &&page.scope.limit===limit&&page.scope.showAgentCreated===showAgentCreated
  &&page.scope.selectedSessionId===(selectedSessionId??null)
  &&Object.entries(scope).every(([key,value])=>page.scope[key]===value);
}
export function visibleWorkspaces(state){
 return (state.workspaces||[]).filter(workspace=>workspace.available===true&&!!workspace.path);
}
export function workspaceLabel(workspace){
 const path=workspace.path||'',leaf=path.split(/[\\/]/).filter(Boolean).at(-1);
 return path+(workspace.name&&workspace.name!==leaf?` · ${workspace.name}`:'');
}
export function isTopLevelChat(chat){
 if(chat?.sessionKind==='root')return true;
 if(chat?.sessionKind==='worker'||chat?.sessionKind==='internal')return false;
 if(chat?.forkTranscript?.length||chat?.editOrigin)return true;
 return !chat?.nativeParentId;
}
function recentActivity(chat){
 return [chat.navigationActivityAt,chat.recentActivityAt,chat.createdAt].find(value=>typeof value==='number'&&Number.isFinite(value)&&value>=0)??0;
}
function orderedChats(state,workspace,mode='workspace',pinFirst=true){
 const workspaces=visibleWorkspaces(state),byId=new Map(workspaces.map(row=>[row.id,row])),byPath=new Map(workspaces.map(row=>[row.path,row]));
 const pinned=new Set(state.pinnedSessionIds||[]);
 const organization=state.conversationOrganization||{},archived=organization.archived||{},view=state.view||{},archive=view.navArchive||'active';
 const sort=view.navSort||'activity';
 const pinOrder=new Map((state.pinnedSessionIds||[]).map((id,index)=>[id,index]));
 return (state.sessions||[]).map((chat,position)=>({chat,position,workspace:byId.get(chat.workspaceId)||byPath.get(chat.workspace)}))
  .filter(row=>isTopLevelChat(row.chat)&&(row.workspace||row.chat.location?.kind==='managed')&&(mode==='all'||row.chat.location?.kind!=='managed'&&row.workspace?.id===workspace?.id))
  .filter(({chat})=>mode!=='all'||view.navLocationFilter!=='managed'||chat.location?.kind==='managed')
  .filter(({chat})=>(archive==='all'||(archive==='archived')===Object.hasOwn(archived,chat.id)))
  .map(row=>({...row.chat,workspace:row.workspace?.path||row.chat.workspace,workspaceId:row.chat.location?.kind==='managed'?null:row.workspace.id,workspaceName:row.chat.location?.kind==='managed'?'No workspace':row.workspace.name||'',workspaceLabel:row.chat.location?.kind==='managed'?'No workspace':row.workspace?.label,pinned:pinned.has(row.chat.id),recentActivityAt:recentActivity(row.chat),position:row.position}))
  .sort((a,b)=>(pinFirst?Number(b.pinned)-Number(a.pinned):0)||(pinFirst&&a.pinned?pinOrder.get(a.id)-pinOrder.get(b.id):sort==='name'?((a.title||'Untitled conversation').toLowerCase()<(b.title||'Untitled conversation').toLowerCase()?-1:(a.title||'Untitled conversation').toLowerCase()>(b.title||'Untitled conversation').toLowerCase()?1:0):sort==='created'?(b.createdAt||0)-(a.createdAt||0):b.recentActivityAt-a.recentActivityAt)||a.position-b.position);
}
export function chatPage(state,workspace,{section=null}={}){
 const view=state.view||{},mode=view.navChatScope==='all'?'all':'workspace',filter=view.navFilter||'',selectedSessionId=state.selectedSessionId??null;
 workspace=visibleWorkspaces(state).find(row=>row.id===(workspace?.id??state.selectedWorkspaceId));
 const scope={mode,workspaceId:mode==='all'?null:workspace?.id??null,filter,selectedSessionId};
 if(section)scope.section=section;
 const grouped=['search','workspace'].includes(section),group=view.navGroup||'recent',age=view.navAge||'all',now=Math.floor(Date.now()/60000)*60;
 scope.age=age;
 if(grouped)scope.group=group;
 if(section==='recent'||section==='search')scope.showAgentCreated=view.navShowAgentCreated===true;
 if(view.navSort&&view.navSort!=='activity')scope.sort=view.navSort;
 if(mode==='all'&&view.navLocationFilter==='managed')scope.locationFilter='managed';
 const statusFilter=view.navStatusFilter||'all';
 if(statusFilter!=='all')scope.statusFilter=statusFilter;
 if(view.navArchive&&view.navArchive!=='active')scope.archive=view.navArchive;
 const projection=section?state.sidebarNavigation?.[section]:state.chatNavigation;
 const sameSort=value=>(value?.sort||'activity')===(scope.sort||'activity');
 const sameLocation=value=>(value?.locationFilter||'all')===(scope.locationFilter||'all');
 const saved=view.navChatPage,matches=saved&&sameLocation(saved)&&sameSort(saved)&&Object.entries(scope).every(([key,value])=>saved[key]===value);
 const requestedIndex=section==='pinned'?Math.max(0,Math.min((projection?.pages||1)-1,view.navPinnedPage||0)):matches&&Number.isSafeInteger(saved.index)?Math.max(0,Math.min((projection?.pages||1)-1,saved.index)):null;
 if(projection?.scope&&sameLocation(projection.scope)&&sameSort(projection.scope)&&Array.isArray(projection.items)&&Object.entries(scope).every(([key,value])=>projection.scope[key]===value)&&(requestedIndex===null||projection.index===requestedIndex))return projection;
 // A bounded snapshot cannot answer a different search or page locally. The
 // control updates immediately, while the shared action fetches its real rows.
 if(state.library?.bounded){
  const resource=section?({search:'sidebarSearch',workspace:'sidebarWorkspace',recent:'sidebarRecent',pinned:'sidebarPinned'})[section]:'chatNavigation';
  const unavailable=state.navigationResourcePending?.[resource]?.status==='retry';
  return {items:[],total:0,index:0,pages:1,start:0,end:0,scope,pending:!unavailable,...(unavailable?{unavailable:true}:{})};
 }
 let chats=filterList(orderedChats(state,workspace,mode,section!=='recent'),filter,chat=>[chat.title||'Untitled conversation',chat.description||'',chat.id,sessionIdentity(chat),chat.workspace,chat.workspaceName]);
 if(section==='pinned')chats=chats.filter(chat=>chat.pinned);
 if(section==='recent'||section==='search')chats=chats.filter(chat=>scope.showAgentCreated||chat.agentCreated!==true||chat.id===selectedSessionId);
 const seconds=ageLimits[age];
 if(seconds)chats=chats.filter(chat=>chat.pinned||chat.recentActivityAt>0&&chat.recentActivityAt>=now-seconds);
 const activityCounts={attention:0,working:0,unread:0,idle:0};
 chats=chats.map(chat=>({...chat,activity:activityFor(chat,state)}));
 for(const chat of chats)activityCounts[chat.activity.kind]=(activityCounts[chat.activity.kind]||0)+1;
 if(statusFilter!=='all')chats=chats.filter(chat=>chat.activity.kind===statusFilter);
 if(grouped){
  chats=chats.map(chat=>({...chat,group:listGroup(chat,group,now)}));
  const compare=(a,b)=>a<b?-1:a>b?1:0;
  chats.sort((a,b)=>a.group.rank-b.group.rank||(group==='workspace'&&!a.pinned&&!b.pinned?(compare(a.group.title.toLowerCase(),b.group.title.toLowerCase())||compare(a.group.id,b.group.id)):0));
  chats=chats.map(({group:{rank,...group},...chat})=>({...chat,group}));
 }
 const pageSize=section?(chats.length>50?40:50):CHAT_PAGE_SIZE;
 const inferred=mode==='all'||section==='recent'?0:Math.floor(Math.max(0,chats.findIndex(chat=>chat.id===selectedSessionId))/pageSize);
 const requested=section==='pinned'?(view.navPinnedPage||0):matches&&Number.isSafeInteger(saved.index)?saved.index:inferred;
 const pages=Math.max(1,Math.ceil(chats.length/pageSize)),index=Math.max(0,Math.min(pages-1,requested));
 const start=index*pageSize,end=Math.min(chats.length,start+pageSize);
 return {items:chats.slice(start,end),total:chats.length,index,pages,start,end,scope,activityCounts};
}
export function subagentCount(state,parent){
 return Number.isSafeInteger(parent?.subagentCount)&&parent.subagentCount>=0?parent.subagentCount:0;
}
export function subagentPage(state,parent){
 const saved=state.view?.subagentHistory||{},filter=saved.filter||'',projection=state.subagentNavigation;
 const requested=Number.isSafeInteger(saved.index)?Math.max(0,saved.index):0;
 const scope={sessionId:parent?.id,filter,index:requested},coverage=state.browserResources;
 if(Array.isArray(coverage)&&coverage.includes('selectedContext')&&coverage.includes('subagentNavigation')
  &&projection?.scope&&Array.isArray(projection.items)&&Object.entries(scope).every(([key,value])=>projection.scope[key]===value)
  &&projection.index===Math.max(0,Math.min(projection.pages-1,requested)))return projection;
 // Only an explicitly covered server page answers this dialog. Selected context
 // is not a child catalog, and omitted/stale rows must never look like no results.
 return {items:[],total:0,unfilteredTotal:subagentCount(state,parent),index:0,pages:1,start:0,end:0,scope,pending:true};
}

// Move one pin relative to another without dropping pins hidden by a filter/page.
export function movePin(ids,source,target){
 if(source===target||!ids.includes(source)||!ids.includes(target))return ids;
 const next=ids.filter(id=>id!==source);
 next.splice(ids.indexOf(target),0,source);
 return next;
}
