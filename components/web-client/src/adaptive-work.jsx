import React,{useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowRight,Search,Plus,CheckCircle2,Inbox,Folder,X} from 'lucide-react';
import {WorkNavigationContext} from './work-navigation';
import {useNavigationController} from './shell/navigation-components';
import {NavigationResourceStatus} from './shell/navigation-resource-status';
import {hasNavigationResource} from './shell/navigation-demand';
import {useChatTitle} from './use-chat-title';
import {useActivityClock} from './navigation-details';
import {readItems,unreadChatItems} from './attention';
import {relativeActivity,activityFor} from './navigation-presentation';
import {startingWorkView,workspaceChatQuery,currentWorkspaceChatQuery,currentWorkPage,currentWorkContext,workReadingKey,rememberHomeScroll,readFocusCursor,saveFocusCursor,focusPosition,stepFocus} from './adaptive-work.js';
import {LandingToolbar,WorkLandingFrame,WorkStatusFilters,FocusNavigation} from './work-landing';
export {WorkViewSwitcher} from './work-landing';
import {workGroups as groups,workAction} from './work-status';


function WorkCard({chat,kind,state,act,hero=false,compact=false}){
 const title=useChatTitle(chat).title,activity=activityFor(chat,state),now=useActivityClock();
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const receipts=unreadChatItems(state,chat.id);
 const at=chat.recentActivityAt||chat.navigationActivityAt||chat.createdAt,time=relativeActivity(at,now);
 const verb=workAction(kind,activity);
 async function review(){if(!receipts.length||busy)return;setBusy(true);setError('');try{await readItems(act,receipts)}catch(error){setError(error.message)}finally{setBusy(false)}}
 return <article className={'a-work-card'+(hero?' is-hero':'')+(compact?' is-compact':'')} data-session-id={chat.id} data-kind={kind}>
  <div className="a-work-card-meta"><span>{chat.location?.kind==='managed'?'No workspace':chat.workspaceName||'Workspace'}</span>{at>0&&<time dateTime={new Date(at*1000).toISOString()} title={'Last activity: '+time.long}>Updated {time.short==='now'?'just now':time.short+' ago'}</time>}</div>
  <button className="a-work-card-open" type="button" onClick={()=>act('session.select',{id:chat.id})} data-action="session.select"><h3>{title}</h3>{chat.description&&<p>{chat.description}</p>}<span className={'a-work-card-cta'+(hero?' a-primary':'')}>{verb}<ArrowRight size={16}/></span></button>
  <div className="a-work-card-foot"><span className="a-work-status" data-kind={activity.kind}><i/>{activity.kind==='idle'?'Saved conversation':activity.label}</span>{receipts.length>0&&<button type="button" disabled={busy} aria-label={'Mark response reviewed: '+title} onClick={review}>{busy?'Saving…':'Mark read'}<CheckCircle2 size={14}/></button>}</div>
  {error&&<p role="alert">{error}</p>}
 </article>;
}

export function AdaptiveWorkHome({host,state,act,presentation}){
 const model=useNavigationController(host,'chats');
 return <AdaptiveWorkChats model={model} state={state} act={act} presentation={presentation}/>;
}

export function AdaptiveWorkChats({model,state,act,presentation={},workspaceId=null,workspaceView,onWorkspaceView}){
 const nav=useContext(WorkNavigationContext),view=workspaceId?{...workspaceView,navAge:state.view?.navAge||'all'}:state.view||{};
 const pinnedScope=workspaceId||model.state.workHomePinnedScope,value=model.state.workHome,ready=(!workspaceId||currentWorkspaceChatQuery(state.view?.workWorkspaceChats,view,workspaceId))&&hasNavigationResource(model.state,'workHome')&&currentWorkPage(value,view,pinnedScope);
 const level=presentation.interfaceDetail||presentation.executionDetail||'standard',mode=view.workHomeView||startingWorkView(presentation);
 const scope=pinnedScope||view.workHomeScope||'all',filter=view.workHomeFilter||'all',query=view.workHomeQuery||'';
 const context=currentWorkContext(model.state.workHomeContext,view,pinnedScope),workspace=context?.workspace;
 const pane=useRef(null),identity=workspaceId?'workspace:'+workspaceId+':chats':'home';
 const focusStorageKey=workspaceId?'amplifier.work-focus.v1:'+identity:undefined;
 const [search,setSearch]=useState(query),timer=useRef(null),pendingSearch=useRef(null),flush=useRef(null),searchInput=useRef(null);
 const [focusCursor,setFocusCursor]=useState(()=>readFocusCursor(globalThis.window,focusStorageKey)),focusPending=useRef(null),focusRestore=useRef(null);
 useEffect(()=>{setSearch(query)},[query]);useEffect(()=>()=>flush.current?.(),[]);
 const patch=values=>workspaceId?onWorkspaceView(values):act('view.update',{patch:values});
 const requestedQuery=workspaceId?JSON.stringify(workspaceChatQuery(view,workspaceId)):null;
 useEffect(()=>{
  // A late request from another workspace is never used as this tab's query.
  // Reassert only the active bounded request, not any saved browser choices.
  if(workspaceId&&!currentWorkspaceChatQuery(state.view?.workWorkspaceChats,view,workspaceId))
   act('view.update',{patch:{workWorkspaceChats:JSON.parse(requestedQuery)}});
 },[requestedQuery,state.view?.workWorkspaceChats]);
 const readingKey=workReadingKey({...view,workHomeScope:scope},mode);
 useLayoutEffect(()=>{
  const scrollPane=workspaceId?pane.current?.closest('.a-adaptive-home'):pane.current;
  if(ready&&scrollPane)return rememberHomeScroll(scrollPane,readingKey,workspaceId?'amplifier.work-reading.v1:'+identity:undefined);
 },[ready,readingKey,identity]);
 useEffect(()=>{if(!view.workHomeView)patch({workHomeView:mode,...(!workspaceId?{navPinned:level==='detailed'}:{})})},[view.workHomeView]);
 const flushSearch=()=>{clearTimeout(timer.current);const value=pendingSearch.current;pendingSearch.current=null;if(value!==null)patch({workHomeQuery:value,workHomePages:{}})};
 flush.current=flushSearch;
 const chooseScope=scope=>{if(pinnedScope)return;clearTimeout(timer.current);pendingSearch.current=null;setSearch('');patch({workHomeScope:scope,workHomeQuery:'',workHomePages:{}})};
 const changeSearch=value=>{setSearch(value);pendingSearch.current=value;clearTimeout(timer.current);timer.current=setTimeout(flushSearch,220)};
 const pages=ready?value.groups:{},total=Object.values(pages).reduce((sum,page)=>sum+page.total,0);
 const stateForCards=model.state;
 const projects=[...new Map([...(model.state.workspaceShortcuts||[]).map(row=>({...row,id:row.workspaceId||row.id,name:row.customName||row.name})),...(workspace?[workspace]:[])].filter(row=>row.id&&row.name).map(row=>[row.id,row])).values()];
 const setFilter=key=>patch({workHomeFilter:key});
 // Search/page loading must not disable a click as the search field blurs.
 // Creation needs the confirmed project, not a matching page of search results.
 const canStart=!!context&&!context.unavailable&&(scope==='all'||scope==='managed'||!!workspace?.path);
 const newChat=()=>{if(canStart)return nav.newChat(scope==='all'||scope==='managed'?{location:{kind:'managed'}}:{workspaceId:workspace.id,workspace:workspace.path,location:{kind:'workspace'}})};
 function pageGroup(kind,limit,{featured=false,compact=false}={}){
  const page=pages[kind];if(!page)return null;
  const items=limit?page.items.slice(0,limit):page.items;
  return <section className="a-work-group" key={kind} data-group={kind} aria-label={groups[kind].title}><div className="a-work-group-heading"><h2>{groups[kind].title}</h2>{kind!=='idle'&&<span>{page.total}</span>}{limit&&page.total>limit&&<button type="button" aria-label={'See all '+groups[kind].title.toLowerCase()} onClick={()=>setFilter(kind)}>{kind==='idle'?'Browse saved chats':'See all'}<ArrowRight size={14}/></button>}</div>
   <div className="a-work-group-items">{items.map((chat,index)=><WorkCard key={chat.id} chat={chat} kind={kind} state={stateForCards} act={act} compact={compact||featured&&index>0}/>)}</div>
   {!page.total&&<p className="a-work-empty">{groups[kind].empty}</p>}
   {!limit&&page.pages>1&&<div className="a-work-pagination"><button type="button" disabled={!page.index} onClick={()=>patch({workHomePages:{...view.workHomePages,[kind]:page.index-1}})}>Previous</button><span>{page.start+1}–{page.end} of {page.total}</span><button type="button" disabled={page.index+1>=page.pages} onClick={()=>patch({workHomePages:{...view.workHomePages,[kind]:page.index+1}})}>Next</button></div>}
  </section>;
 }
 const focusKey=JSON.stringify([scope,view.workHomeScope||'all',query,view.navAge||'all']);
 const savedFocus=focusCursor?.key===focusKey?focusCursor:null;
 const focusKind=filter!=='all'?filter:pages[savedFocus?.kind]?.items.length?savedFocus.kind:Object.keys(groups).find(key=>pages[key]?.items.length);
 const focusPage=pages[focusKind],focus=focusPosition(focusPage,savedFocus?.kind===focusKind?savedFocus:null),focusChat=focus&&focusPage.items[focus.index];
 const focusMode=mode==='focus';
 const rememberFocus=value=>{setFocusCursor(value);saveFocusCursor(window,value,focusStorageKey)};
 useLayoutEffect(()=>{
  // Committed search/scope changes start a pass even with no result or while
  // another Home mode is visible. Otherwise returning revives the old key.
  if(focusCursor&&focusCursor.key!==focusKey){
   focusPending.current=null;focusRestore.current=null;rememberFocus(null);
  }
  if(!ready||!focusMode||!focus)return;
  // Do not overwrite the destination cursor while a page request is settling.
  if(focusPending.current?.key!==focusKey)focusRestore.current=null;
  if(focusPending.current?.key===focusKey&&focusPending.current.page!==(view.workHomePages?.[focusKind]||0))return;
  focusPending.current=null;
  const next={key:focusKey,kind:focusKind,...focus};
  if(JSON.stringify(next)!==JSON.stringify(focusCursor))rememberFocus(next);
  if(focusRestore.current){pane.current?.querySelector(`[data-focus-move="${focusRestore.current}"]`)?.focus();focusRestore.current=null}
 },[ready,focusMode,focusKey,focusKind,focus?.page,focus?.index,focus?.id]);
 const moveFocus=(direction,restart=false)=>{
  const next=restart?{page:0,index:0,id:focusPage.index===0?focusPage.items[0]?.id:null}:stepFocus(focusPage,focus,direction);
  if(!next)return;
  rememberFocus({key:focusKey,kind:focusKind,...next});
  if(next.page!==focusPage.index){
   focusPending.current={key:focusKey,page:next.page};focusRestore.current=direction>0?'next':'previous';
   patch({workHomePages:{...view.workHomePages,[focusKind]:next.page}});
  }
 };
 const lastFocus=focus&&focusPage.start+focus.index+1===focusPage.total;
 const filters=ready&&<WorkStatusFilters value={filter} counts={Object.fromEntries(Object.entries(pages).map(([key,page])=>[key,page.total]))} onChange={setFilter}/>;
 const toolbar=<LandingToolbar home={!workspaceId} mode={mode} onMode={workHomeView=>patch({workHomeView})} presentation={presentation} onReset={workHomeView=>patch({workHomeView,workHomeFilter:'all'})} actions={<button type="button" className="a-home-create a-soft" aria-label="New chat" disabled={!canStart} onClick={newChat}><Plus/><span>New chat</span></button>}/>;
 const content=<>
   <div className="a-home-query">
    <label className="a-home-search"><Search size={18}/><input ref={searchInput} type="search" aria-label="Search your work" placeholder="Find your work…" maxLength={500} value={search} onChange={event=>changeSearch(event.target.value)} onBlur={flushSearch}/>{search&&<button type="button" className="a-icon" aria-label="Clear work search" onClick={()=>{changeSearch('');searchInput.current?.focus()}}><X size={16}/></button>}</label>
    {!workspaceId&&<select aria-label="Work scope" value={scope} disabled={!!pinnedScope} onChange={event=>chooseScope(event.target.value)}>{pinnedScope?<option value={scope}>{projects.find(row=>row.id===scope)?.name||'Unavailable workspace'}</option>:<><option value="all">All work</option><option value="managed">No workspace</option>{projects.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</>}</select>}
   </div>
   {!workspaceId&&workspace&&!context.unavailable&&<div className="a-home-project"><Folder/><strong>{workspace.name}</strong><button type="button" onClick={()=>nav.browse('workspace',workspace.id)}>Open workspace<ArrowRight/></button></div>}
   <NavigationResourceStatus state={model.state} resource="workHome" label="your work" refresh={model.refreshResources}/>
   {!ready&&!model.state.navigationResourcePending?.workHome&&<p role="status">Loading your work…</p>}
   {ready&&value.unavailable?<p role="alert">This workspace is unavailable. {!pinnedScope&&<button type="button" onClick={()=>chooseScope('all')}>Show all work</button>}</p>:ready&&<>
    {filters}
    {!total?<div className="a-home-welcome"><Inbox size={32}/><h2>{query?'No matching work.':'A fresh place to begin.'}</h2><p>{query?'Try another phrase or a different scope.':'Bring a question, an idea, or a project. You can organize it as it grows.'}</p><button type="button" className="a-primary" onClick={()=>query?changeSearch(''):newChat()}>{query?'Clear search':'Start a chat'}<ArrowRight/></button></div>:
     focusMode?<div className="a-home-focus"><div className="a-focus-caption"><h2>{groups[focusKind].title}</h2>{focusPage.total>1&&<button type="button" onClick={()=>patch({workHomeView:'brief',workHomeFilter:focusKind})}>See all {focusPage.total}<ArrowRight size={15}/></button>}</div>{focusChat?<><WorkCard key={focusChat.id} chat={focusChat} kind={focusKind} state={stateForCards} act={act} hero/>
      {focusPage.total>1&&<FocusNavigation position={focusPage.start+focus.index+1} total={focusPage.total} previousDisabled={!focusPage.start&&!focus.index} onPrevious={()=>moveFocus(-1)} onNext={()=>moveFocus(1,lastFocus)} nextLabel={lastFocus?'Start again':'Next'} name={focusChat.title}/>}
</>:<p className="a-work-empty">{groups[focusKind].empty}</p>}
     </div>:
     filter!=='all'?<div className="a-home-filtered">{pageGroup(filter)}</div>:
     <>{mode==='brief'&&!pages.attention.total&&!pages.unread.total?<div className="a-home-clear"><CheckCircle2/><div><h2>You’re up to date.</h2><p>Nothing needs your attention, and there are no unread responses.</p></div></div>:<div className={mode==='brief'?'a-home-brief':'a-home-studio'}>
      {pageGroup('attention',mode==='brief'?3:4,{featured:mode==='brief'})}
      {pageGroup('unread',mode==='brief'?3:4,{featured:mode==='brief'})}
      {mode==='studio'&&pageGroup('working',4)}
     </div>}{mode==='brief'&&pages.working.total>0&&<div className="a-home-running">{pageGroup('working',3,{compact:true})}</div>}
     {pages.waiting?.total>0&&<div className="a-home-recent">{pageGroup('waiting',mode==='brief'?3:4,{compact:true})}</div>}
     {pages.paused?.total>0&&<div className="a-home-recent">{pageGroup('paused',mode==='brief'?3:4,{compact:true})}</div>}
     <div className="a-home-recent">{pageGroup('idle',mode==='brief'?4:6,{compact:true})}</div></>}
   </>}
 </>;
 return workspaceId?<section ref={pane} className="a-workspace-chats" aria-label="Workspace chats" data-view={mode}>{toolbar}{content}</section>:<WorkLandingFrame paneRef={pane} mode={mode} presentation={presentation}
  title={mode==='focus'?'One thing at a time.':mode==='brief'?'Your work, at a glance.':'Everything in motion.'}
  description={level!=='minimal'?(mode==='focus'?'Begin with what needs you.':mode==='brief'?'What needs you, what’s ready, and where you left off.':'Follow the work and find your next step.'):null}
  toolbar={toolbar}>{content}</WorkLandingFrame>;
}
