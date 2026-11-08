import {revealNavigationTitles} from './navigation-title';
import {useRegionActivity} from './activity-region';
import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronRight,Folder,FolderDot,MoreHorizontal,Search,FolderPlus,Pencil,Trash2,MessageCircle,MessageSquarePlus,Pin,PinOff,ArrowUp,ArrowDown} from 'lucide-react';
import {NavigationRow,NavigationStatus,WorkspaceDetails,useActivityClock,navigationDetailsContext} from './navigation-details';
import {workspaceContext,parentPath} from './navigation-presentation';
import {movePin} from './chat-navigation';
import {LandingCollection} from './work-landing';

export function WorkspaceRow({row,state,act,now,open,newChat,browse,onEdit,compact=false,pinnedShortcut=false,handle,floating=false,landing=false}){
 const workspace=!!row.workspaceId;
 const count=(workspace?row.chatCount:row.descendantWorkspaceCount)||0,noun=workspace?(count===1?'chat':'chats'):(count===1?'workspace':'workspaces');
 const selectedWorkspace=state.view?.workSurface==='workspace'?state.view.workWorkspaceId||state.selectedWorkspaceId:null;
 const selected=workspace&&row.workspaceId===selectedWorkspace;
 const canDraft=workspace&&row.available!==false&&typeof row.path==='string'&&row.path.length>0&&row.path.length<=4000&&!row.path.includes('\0')&&(/^(\/|[a-zA-Z]:[\\/]|\\\\)/.test(row.path))&&!row.path.split(/[\\/]/).includes('..');
 const browseLabel='Browse '+row.path,pinKey='workspace:'+row.workspaceId;
 const mixed=state.pinnedItemIds, pins=mixed||state.pinnedWorkspaceIds||[],key=mixed?pinKey:row.workspaceId,index=pins.indexOf(key),orderAction=mixed?'navigation.pinOrder':'workspace.pinOrder';
 const [error,setError]=useState('');
 const move=async offset=>{try{setError('');const result=await act(orderAction,{ids:movePin(pins,key,pins[index+offset])});const receipt=result?.result?.accepted!==undefined?result.result:result;if(receipt?.accepted===false)throw Error(receipt.error||'Could not reorder pins.')}catch(err){setError(err.message||'Could not reorder pins.')}};
 const counts=row.activityCounts||{},summary=counts.attention?{kind:'attention',attentionType:counts.error?'error':counts.decision?'decision':counts.blocked?'blocked':'error',label:`${counts.attention} chats need attention`}:counts.working?{kind:'working',label:`${counts.working} chats working`}:counts.waiting?{kind:'waiting',label:`${counts.waiting} chats waiting`}:row.unread?{kind:'unread',label:`${row.unread} chats with unread activity`}:counts.paused?{kind:'paused',label:`${counts.paused} chats paused`}:null;
 const content=<>{handle}
  <button type="button" className="a-workspace-select" data-tooltip="off" onPointerEnter={revealNavigationTitles} onFocus={revealNavigationTitles} data-navigation-select data-action={workspace?'workspace.select':'view.update'} aria-label={workspace?'Open chats in '+row.path:browseLabel} aria-pressed={workspace?selected:undefined} onClick={()=>workspace?open(row):browse(row.path)}>
   {summary?<NavigationStatus activity={summary}/>:<span className="a-navigation-status" aria-hidden="true">{workspace?<FolderDot/>:<Folder/>}</span>}<span className="a-workspace-label"><span data-scroll-title><span>{row.customName||row.name}</span></span><small className="a-workspace-result-path" data-scroll-title><span>{state.settings?.workspaces?.showPaths?row.path:'in '+workspaceContext(row)}</span></small>{landing&&<small className="a-landing-status">{workspace&&summary?summary.label:`${count} ${noun}`}</small>}</span>
   {!pinnedShortcut&&<span className="a-workspace-count" aria-label={`${count} ${noun}`}>{count}</span>}{!workspace&&row.canBrowse&&<ChevronRight className="a-workspace-arrow"/>}
  </button>
  {!compact&&workspace&&row.canBrowse&&<button type="button" className="a-workspace-drill" data-action="view.update" aria-label={browseLabel} title={`${row.descendantWorkspaceCount} nested workspaces`} onClick={()=>browse(row.path)}><ChevronRight/></button>}
  {compact&&workspace&&<button type="button" className="a-icon a-workspace-new-chat" data-action="session.draft" aria-label={'New chat in '+row.path} title={canDraft?'New chat in '+row.path:'Workspace folder unavailable'} disabled={!canDraft} onClick={e=>{e.stopPropagation();newChat(row)}}><MessageSquarePlus/></button>}
 </>;
 const className=`a-workspace-row ${landing?'a-landing-workspace ':''}${workspace?'is-workspace':''} ${selected?'is-selected':''}`;
 if(floating)return <div className={className} data-show-paths={!!state.settings?.workspaces?.showPaths}>{content}<MoreHorizontal className="a-navigation-more"/></div>;
 if(!workspace)return <div className={className} data-show-paths={!!state.settings?.workspaces?.showPaths} data-workspace-path={row.path}>{content}</div>;
 return <NavigationRow contextKey={navigationDetailsContext(state)} className={className} data-show-paths={!!state.settings?.workspaces?.showPaths} data-workspace-path={row.path} data-qualified={!row.customName&&row.pathLabel?.includes('/')} label={row.customName||row.name} details={({close})=><WorkspaceDetails row={row} now={now} actions={<>
  <button type="button" className="a-link" data-action="workspace.select" onClick={()=>{close();open(row)}}><MessageCircle/>Open chats</button>
  <button type="button" data-action={row.archived?'workspace.restore':'workspace.archive'} onClick={()=>{close();act(row.archived?'workspace.restore':'workspace.archive',{id:row.workspaceId})}}>{row.archived?'Restore workspace':'Archive workspace'}</button>
  <button type="button" data-action="workspace.pin" aria-label={(row.pinned?'Unpin workspace ':'Pin workspace ')+row.path} onClick={()=>{close();act('workspace.pin',{id:row.workspaceId,pinned:!row.pinned})}}>{row.pinned?<PinOff/>:<Pin/>}{row.pinned?'Unpin workspace':'Pin workspace'}</button>
  {row.pinned&&<><button type="button" data-action={orderAction} aria-label={'Move workspace up '+row.path} disabled={index<=0} onClick={()=>move(-1)}><ArrowUp/>Move up</button><button type="button" data-action={orderAction} aria-label={'Move workspace down '+row.path} disabled={index<0||index>=pins.length-1} onClick={()=>move(1)}><ArrowDown/>Move down</button>{error&&<span role="alert">{error}</span>}</>}
  <button type="button" data-action="view.update" onClick={()=>{close();browse(row.parentPath??parentPath(row.path))}}><Folder/>Browse parent</button>
  {onEdit&&<><button type="button" data-action="view.update" onClick={()=>{close();onEdit({mode:'rename',id:row.workspaceId,name:row.customName||row.name})}}><Pencil/>Rename</button><button type="button" className="a-danger" disabled={state.library?.workspaceCount<2} data-action="view.update" onClick={()=>{close();onEdit({mode:'remove',id:row.workspaceId,name:row.customName||row.name})}}><Trash2/>Remove</button></>}
 </>}/>} >{content}</NavigationRow>;
}

// The server supplies the same bounded folder projection to users and agents.
// Browsing changes only the view; workspace.select changes the active project.
export function WorkspaceExplorer({state,act,onOpen,onEdit,heading=true,previewLimit=Infinity,compact=false,onSelect,layout,identity='workspaces'}){
 const now=useActivityClock();
 const explorer=state.workspaceExplorer||{},rows=compact?(state.workspaceShortcuts||[]):explorer.rows||[],crumbs=explorer.breadcrumbs||[];
 const [showAll,setShowAll]=useState(false);
 const preview=!showAll&&!explorer.filter&&explorer.mode==='recent'&&(explorer.page||1)===1;
 const shownRows=preview?rows.slice(0,previewLimit):rows;
 const [query,setQuery]=useState(explorer.filter||''),pendingQuery=useRef(null);
 useEffect(()=>{pendingQuery.current=null;setQuery(explorer.filter||'')},[state.selectedWorkspaceId]);
 useEffect(()=>{if(pendingQuery.current===null||pendingQuery.current===explorer.filter){pendingQuery.current=null;setQuery(explorer.filter||'')}},[explorer.filter]);
 const [requests,setRequests]=useState(0),region=useRef(null);useRegionActivity(region,requests>0||!!state.sharedHistory?.loading);
 const patch=async value=>{setRequests(count=>count+1);try{return await act('view.update',{patch:value})}finally{setRequests(count=>count-1)}};
 const browse=path=>{pendingQuery.current=null;setQuery('');return patch({navWorkspacePath:path,navWorkspaceFilter:'',navWorkspacePage:1,navWorkspaceAncestorsOpen:false,navWorkspaceMode:'folders'})};
 const search=value=>{
  pendingQuery.current=value;setQuery(value);
  Promise.resolve(patch({navWorkspaceFilter:value,navWorkspacePage:1})).then(result=>{
   if(pendingQuery.current!==value)return;
   if(!result||result.accepted===false){pendingQuery.current=null;setQuery(explorer.filter||'')}
   else pendingQuery.current=null;
  }).catch(()=>{if(pendingQuery.current===value){pendingQuery.current=null;setQuery(explorer.filter||'')}});
 };
 const open=async row=>{if(onSelect)return onSelect(row);const result=await act('workspace.select',{id:row.workspaceId});if(result&&result.accepted!==false)await onOpen?.()};
 const newChat=async row=>{const result=await act('session.draft',{workspace:row.path,workspaceId:row.workspaceId,location:{kind:'workspace'}});if(result&&result.accepted!==false)await onOpen?.()};
 const previous=crumbs.length>3?crumbs.slice(0,-2):[],visible=previous.length?crumbs.slice(-2):crumbs;
 const page=explorer.page||1,pages=explorer.pages||1;
 const crumbLabel=crumb=>crumb.name===crumb.path?(crumb.path.replace(/[\\/]+$/,'').split(/[\\/]/).at(-1)||crumb.name):crumb.name;
 return <section ref={region} data-activity-region="workspace-folders" className="a-workspace-explorer" data-part="workspace-explorer" data-compact={compact} data-show-paths={!!state.settings?.workspaces?.showPaths} aria-label="Workspace folders">
  {!compact&&<div className="a-workspace-modes" role="group" aria-label="Workspace list"><button type="button" aria-pressed={explorer.mode==='recent'} data-action="view.update" onClick={()=>patch({navWorkspaceMode:'recent',navWorkspacePage:1})}>Recent</button><button type="button" aria-pressed={!compact&&explorer.mode!=='recent'} data-action="view.update" onClick={()=>patch({navWorkspaceMode:'folders',navWorkspacePage:1})}>Browse folders</button></div>}
  {!compact&&<label className="a-field">Workspaces<select aria-label="Workspace archive filter" value={explorer.archive||'active'} onChange={e=>patch({navWorkspaceArchive:e.target.value,navWorkspaceMode:'recent',navWorkspacePage:1})}><option value="active">Active</option><option value="archived">Archived</option><option value="all">All workspaces</option></select></label>}
  {heading&&!compact&&<div className="a-workspace-explorer-heading"><strong>Workspaces</strong><span>{explorer.totalWorkspaces||0}</span></div>}
  {!compact&&<div className="a-nav-search a-workspace-search"><Search/><input type="search" aria-label="Filter workspaces" placeholder="Find workspace · * ? patterns" value={query} data-action="view.update" onChange={e=>search(e.target.value)}/></div>}
  {!compact&&explorer.mode!=='recent'&&!explorer.filter&&<div className="a-workspace-location">
   <button type="button" className="a-icon" aria-label="Go to parent workspace folder" disabled={explorer.parentPath==null} data-action="view.update" onClick={()=>browse(explorer.parentPath)}><ArrowLeft/></button>
   <nav className="a-workspace-breadcrumbs" aria-label="Workspace folder path">
    {previous.length>0&&<div className="a-workspace-ancestors" data-open={!!state.view?.navWorkspaceAncestorsOpen}><button type="button" className="a-workspace-ancestor-toggle" aria-label="Show ancestor folders" aria-expanded={!!state.view?.navWorkspaceAncestorsOpen} data-action="view.update" onClick={()=>patch({navWorkspaceAncestorsOpen:!state.view?.navWorkspaceAncestorsOpen})}><MoreHorizontal/></button>{state.view?.navWorkspaceAncestorsOpen&&<div>{previous.map(crumb=><button key={crumb.path} type="button" data-action="view.update" aria-label={'Browse '+(crumb.path||'all workspace folders')} onClick={()=>browse(crumb.path)}>{crumb.path||'All workspace folders'}</button>)}</div>}</div>}
    {visible.map((crumb,index)=><React.Fragment key={crumb.path}>{(index>0||previous.length>0)&&<ChevronRight aria-hidden="true"/>}<button type="button" data-action="view.update" aria-current={index===visible.length-1?'location':undefined} title={crumb.path||'All workspace folders'} aria-label={'Browse '+(crumb.path||'all workspace folders')} onClick={()=>browse(crumb.path)}>{crumbLabel(crumb)}</button></React.Fragment>)}
   </nav>
  </div>}
  {layout&&!compact?<LandingCollection className="a-workspace-folders" layout={layout} identity={identity} queryKey={[explorer.filter,explorer.mode,explorer.archive,explorer.path]} page={{items:shownRows,total:explorer.totalRows||0,index:page-1,pages,start:explorer.start||0,end:explorer.end||shownRows.length}} onPage={index=>patch({navWorkspacePage:index+1})}>{row=><WorkspaceRow {...{row,state,act,now,open,newChat,browse,onEdit}} landing/>}</LandingCollection>:<div className="a-workspace-folders">{shownRows.map(row=><WorkspaceRow key={row.path} {...{row,state,act,now,open,newChat,browse,onEdit,compact}}/>)}</div>}
  {shownRows.length<rows.length&&<button type="button" className="a-link" onClick={()=>setShowAll(true)}>More workspaces<ChevronRight/></button>}
  {!rows.length&&!state.sharedHistory?.loading&&<p className="a-nav-empty">{explorer.filter?'No matching workspaces.':'Create a workspace to start a chat.'}</p>}
  {!compact&&layout!=='focus'&&pages>1&&shownRows.length===rows.length&&<div className="a-nav-pagination a-workspace-pagination"><span>Page {page} of {pages}</span><div><button type="button" className="a-link" aria-label="Show previous workspace folders" data-action="view.update" disabled={page<=1} onClick={()=>patch({navWorkspacePage:page-1})}>Previous</button><button type="button" className="a-link" aria-label="Show more workspace folders" data-action="view.update" disabled={page>=pages} onClick={()=>patch({navWorkspacePage:page+1})}>More<ChevronRight/></button></div></div>}
 </section>;
}
