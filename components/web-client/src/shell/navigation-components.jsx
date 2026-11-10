import {WorkspacePlans,WorkspacePlan} from '../workspace-plans';
import {revealNavigationTitles} from '../navigation-title';
import {useChatTitle} from '../use-chat-title';
import {WorkNavigationContext,workSurface} from '../work-navigation';
import {ArchiveButton} from '../archive-feedback.jsx';
import React,{useEffect,useLayoutEffect,useRef,useState,useContext,useId} from 'react';
import {MessageCircle,Search,Pencil,Trash2,X,Check,ChevronRight,Pin,RefreshCw,LoaderCircle,AlertCircle,ArrowLeft,ArrowUpRight,Folder,MoreHorizontal,ArrowUp,ArrowDown,Plus} from 'lucide-react';
import {chatPage,visibleWorkspaces,movePin,recentLimit,recentPageMatches} from '../chat-navigation';
import {followLaterHistory} from '../history-scroll';
import {request} from '../api';
import {NavigationRow,NavigationStatus,ActivityTime,CopyDetail,WorkspaceDetails,useActivityClock,navigationDetailsContext} from '../navigation-details';
import {activityFor,relativeActivity,compactParent,sessionIdentity} from '../navigation-presentation';
import {ReorderList} from '../reorder-list';
import '../sidebar-navigation.css';
import {WorkspaceForm} from '../workspace-setup';
import {WorkspaceExplorer,WorkspaceRow} from '../workspace-explorer';
import {LibraryFilters} from '../conversation-library';
import {ChatDelete} from '../chat-delete';
import {readItems,unreadChatItems} from '../attention';
import {useNavigation} from '../../.build-inputs/packages/shell-sdk/index.js';
import {waitForSnapshot} from './wait-for-snapshot';
import {hasNavigationResource} from './navigation-demand';
import {NavigationResourceStatus} from './navigation-resource-status';
import {usePinOrderSettlement,pinOrderPending,pinOrderScope} from './pin-order-settlement';
import {sidebarReadingSnapshot} from './resources';
import {ChatShortcutBadge} from '../chat-shortcuts';
import {workGroups} from '../work-status';
import {LandingCollection,WorkStatusFilters} from '../work-landing';
const patch=(act,value)=>act('view.update',{patch:value});
export function ChatRename({chat,act,cancel,inputId="nav-workspace-name"}){
 const [name,setName]=useState(chat.title||''),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const submitting=useRef(false),dirty=useRef(false);
 const naming=chat.naming?.status==='working';
 const busy=chat.configurationBusy;
 const automatic=chat.autoName??(chat.titleSource!=='manual'&&chat.nativeNameSource!=='manual');
 useEffect(()=>{if(!dirty.current)setName(chat.title||'')},[chat.title]);
 const submit=async e=>{
  e.preventDefault();
  const title=name.trim();
  if(!title||submitting.current)return;
  submitting.current=true;setSaving(true);setError('');
  try{
   const result=await act('session.rename',{id:chat.id,title});
   if(result&&result.accepted!==false)cancel();
   else setError(result?.error||'Could not rename. Your text is kept; try again.');
  }catch(error){setError(error.message||'Could not rename. Please try again.')}
  finally{submitting.current=false;setSaving(false)}
 };
 async function generate(){
  if(submitting.current||naming||busy||name!==(chat.title||''))return;
  submitting.current=true;setSaving(true);setError('');
  try{
   const result=await act('session.naming',{id:chat.id,regenerate:true});
   if(!result||result.accepted===false)throw Error(result?.error||'Could not generate a name. Your current name is kept.');
  }catch(error){setError(error.message)}finally{submitting.current=false;setSaving(false)}
 }
 return <form aria-busy={saving||naming} className="a-nav-chat a-nav-chat-rename" data-session-id={chat.id} onSubmit={submit}>
  <input id={inputId} maxLength={200} aria-label={`New name for ${chat.title||'conversation'}`} value={name} disabled={saving} required autoFocus onChange={e=>{dirty.current=e.target.value!==(chat.title||'');setName(e.target.value);setError('')}}/>
  <button type="submit" className="a-icon a-nav-chat-edit" aria-label="Save conversation name" disabled={saving||!name.trim()} data-action="session.rename"><Check/></button>
  <button type="button" className="a-icon a-nav-chat-edit" aria-label="Cancel conversation rename" disabled={saving} data-action="view.update" onClick={cancel}><X/></button>
  <button type="button" className="a-link" data-action="session.naming" disabled={saving||naming||busy||name!==(chat.title||'')} onClick={generate}><RefreshCw/>{naming?'Naming…':'Auto name now'}</button>
  <small className="a-nav-name-help">Generate one name. Future automatic naming stays {automatic?'on':'off'}.{busy?' Finish changing the conversation configuration first.':name!==(chat.title||'')?' Save or cancel your edit first.':''}</small>
  {naming&&<small role="status">Generating chat name…</small>}
  {(error||chat.naming?.error)&&<small role="alert" className="a-danger">{error||chat.naming.error}</small>}
 </form>;
}
export function useNavigationController(host,kind){
 const state=useNavigation(React,host),act=host.dispatch,prefix=host.instanceId==='workspaces'?'nav-workspace':'nav-'+host.instanceId;
 const view=state.view||{},savedDraft=view.workspaceDraft||{},draft=(kind==='chats'?savedDraft.mode?.startsWith('chat-'):!savedDraft.mode?.startsWith('chat-'))?savedDraft:{};
 const form=useRef(null),draftRef=useRef(draft),draftVersion=useRef(0),submitting=useRef(false);
 const [saving,setSaving]=useState(false),[formError,setFormError]=useState('');
 useEffect(()=>{draftRef.current=draft},[draft]);
 const history=state.sharedHistory||{},refreshing=!!(history.loading||history.refreshing);
 const workspaces=visibleWorkspaces(state),workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId);
 const workspaceCount=state.library?.bounded?state.library.workspaceCount:workspaces.length;
 const allChats=view.navChatScope==='all';
 const setDraft=value=>{draftVersion.current++;draftRef.current=value;setFormError('');patch(act,{workspaceDraft:value})};
 const updateDraft=value=>setDraft({...draft,...value});
 const choose=id=>act('session.select',{id});

 const submit=async e=>{
  e.preventDefault();if(submitting.current)return;const version=draftVersion.current;submitting.current=true;setSaving(true);setFormError('');
  try{
   let result;
   if(draft.mode==='add')result=await act('workspace.create',{path:draft.path||'',...(draft.name?.trim()?{name:draft.name.trim()}:{})});
   else if(draft.mode==='rename')result=await act('workspace.rename',{id:draft.id,name:draft.name||''});
   else if(draft.mode==='chat-rename')result=await act('session.rename',{id:draft.id,title:draft.name||''});
   else if(draft.mode==='remove')result=await act('workspace.remove',{id:draft.id});
   if(result&&result.accepted!==false){if(draftVersion.current===version)setDraft({});}
   else setFormError(result?.error||'Could not save. Check the folder or name and try again.');
  }catch(error){setFormError(error.message||'Could not save. Please try again.')}
  finally{submitting.current=false;setSaving(false)}
 };
 useEffect(()=>{if(!draft.mode)return;form.current?.scrollIntoView?.({block:'nearest'});form.current?.querySelector?.('input')?.focus()},[draft.mode,draft.id]);
 return {host,state,act,refreshResources:host.refreshResources,prefix,view,draft,form,saving,formError,history,refreshing,workspace,workspaceCount,allChats,setDraft,updateDraft,choose,submit};
}
export function NavigationEditor({model,deleteOwner=true}){
 const editorId=useId();
 const {state,act,draft,form,saving,formError,setDraft,updateDraft,submit}=model;
 // A hidden browser and the sidebar share this draft. Only its active surface
 // may obtain a deletion token; a second preview invalidates the first token.
 if(draft.mode==='chat-delete'&&!deleteOwner)return null;
 if(draft.mode==='add')return <WorkspaceForm state={state} act={act} onDone={()=>setDraft({})} onCancel={()=>setDraft({})}/>;
 return <>{draft.mode==='chat-delete'&&<div className="a-nav-form"><strong>Delete chat?</strong><ChatDelete key={draft.id} id={draft.id} act={act} cancel={()=>setDraft({})}/></div>}    {draft.mode&&draft.mode!=='chat-rename'&&draft.mode!=='chat-delete'&&<form ref={form} className="a-nav-form" aria-busy={saving} onSubmit={submit}>
     <div className="a-nav-form-title"><strong>{draft.mode==='rename'?'Rename workspace':'Remove workspace?'}</strong><button className="a-icon" type="button" aria-label="Cancel navigation edit" data-action="view.update" onClick={()=>setDraft({})}><X/></button></div>
     {draft.mode==='rename'?<><label htmlFor={editorId+'-name'}>Name</label><input id={editorId+'-name'} maxLength={200} value={draft.name||''} required data-action="view.update" onChange={e=>updateDraft({name:e.target.value})}/></>:<p>Remove {draft.name} from this list? Its files and chats will stay on disk.</p>}
     <button type="submit" disabled={saving} className={draft.mode==='remove'?'a-soft a-danger':'a-primary'} data-action={draft.mode==='rename'?'workspace.rename':draft.mode==='remove'?'workspace.remove':'session.rename'}>{saving?'Saving…':draft.mode==='remove'?'Remove registration':'Save name'}</button>
     {formError&&<p role="alert" className="a-danger">{formError}</p>}
    </form>}
</>;
}
export function WorkspaceManager({host,paired=false}){
 const model=useNavigationController(host,'workspaces');
 const {state,act,allChats,workspace,workspaceCount,setDraft}=model;
 if(paired)return <NavigationEditor model={model}/>;
 return <><NavigationEditor model={model}/>    {!allChats&&<WorkspaceExplorer state={state} act={act}/>}
    {!allChats&&workspace&&<div className="a-nav-workspace-path"><span className="a-workspace-full-path" title={workspace.path||'Workspace folder unavailable'}>{workspace.path||'Workspace folder unavailable'}</span><button type="button" className="a-icon" aria-label="Rename workspace" data-action="view.update" onClick={()=>setDraft({mode:'rename',id:workspace.id,name:workspace.name})}><Pencil/></button><button type="button" className="a-icon" aria-label="Remove workspace registration" disabled={workspaceCount<2} data-action="view.update" onClick={()=>setDraft({mode:'remove',id:workspace.id,name:workspace.name})}><Trash2/></button></div>}
</>;
}
function PairedWorkspaceBrowser({host,onOpen}){
 const model=useNavigationController(host,'workspaces');
 return <WorkspaceExplorer state={model.state} act={model.act} onOpen={onOpen} onEdit={model.setDraft} heading={false} previewLimit={8}/>;
}
export function ChatDetails({chat,model,now,close}){
 const {state,act,draft,setDraft,choose,prefix}=model,activity=activityFor(chat,state);
 const title=chat.title||'Untitled conversation',managed=chat.location?.kind==='managed';
 const mixed=state.pinnedItemIds,pins=mixed||state.pinnedSessionIds||[],pinKey=mixed?'session:'+chat.id:chat.id,pinIndex=pins.indexOf(pinKey),orderAction=mixed?'navigation.pinOrder':'session.pinOrder';
 const [pinError,setPinError]=useState(''),[reviewError,setReviewError]=useState(''),[reviewing,setReviewing]=useState(false);
 const [pinning,setPinning]=useState(false),pinFlight=useRef(false),pinOwner=useRef(null),pinAlive=useRef(true);
 const pinContext=chat.id+'|'+navigationDetailsContext(state);pinOwner.current=pinContext;
 useEffect(()=>{pinAlive.current=true;return()=>{pinAlive.current=false}},[]);
 useEffect(()=>setPinError(''),[pinContext]);
 const togglePin=async()=>{
  if(pinFlight.current)return;pinFlight.current=true;setPinning(true);setPinError('');
  const owner=pinContext,target={id:chat.id,pinned:!chat.pinned};
  try{
   const response=await act('session.pin',target);
   const receipt=response?.result?.accepted!==undefined?response.result:response;
   if(receipt?.accepted!==true)throw Error(receipt?.error||'The pin change could not be confirmed. Review this chat’s current pin state.');
   if(pinAlive.current&&pinOwner.current===owner)close(true);
  }catch(error){if(pinAlive.current&&pinOwner.current===owner)setPinError(error.message||'The pin change could not be confirmed.')}
  finally{pinFlight.current=false;if(pinAlive.current)setPinning(false)}
 };
 const [readError,setReadError]=useState(''),[reading,setReading]=useState(false),readFlight=useRef(false);
 const unread=unreadChatItems(state,chat.id);
 const markedUnread=unread.length>0||activity.kind==='unread';
 const readKnown=!state.library?.bounded||Object.hasOwn(state.attention?.sessions||{},chat.id);
 const readUnavailable=!readKnown||(markedUnread&&!unread.length);
 const changeRead=async()=>{
  if(readFlight.current||readUnavailable)return;readFlight.current=true;setReading(true);setReadError('');
  try{if(unread.length)await readItems(act,unread);else await act('session.markUnread',{id:chat.id});close()}
  catch(error){setReadError(error.message||'Could not change read state. Please try again.')}
  finally{readFlight.current=false;setReading(false)}
 };
 const errorItem=state.attention?.items?.find(item=>item.id==='session:'+chat.id&&item.sessionId===chat.id&&!item.read);
 const review=async()=>{if(reviewing||!errorItem)return;setReviewing(true);setReviewError('');try{await readItems(act,[errorItem])}catch(error){setReviewError(error.message||'Could not mark this error reviewed.')}finally{setReviewing(false)}};
 const move=async direction=>{try{setPinError('');const response=await act(orderAction,{ids:movePin(pins,pinKey,pins[pinIndex+direction])});const receipt=response?.result?.accepted!==undefined?response.result:response;if(receipt?.accepted===false)throw Error(receipt.error||'Could not reorder pins.')}catch(error){setPinError(error.message)}};
 return <><div className="a-navigation-detail-heading"><MessageCircle/>Chat details</div><h3>{title}</h3>
  {chat.titlePreviewSource==='first-message'&&<p className="a-caption">Preview from the first message. Rename this chat to give it a permanent name.</p>}
  <div className="a-navigation-detail-status"><NavigationStatus activity={activity}/><strong>{activity.label}</strong></div>
  <dl><dt>Last activity</dt><dd>{relativeActivity(chat.recentActivityAt,now).long}</dd><dt>Workspace</dt><dd>{managed?'No workspace':chat.workspaceName||chat.workspace?.split(/[\\/]/).filter(Boolean).at(-1)}</dd></dl>
  <CopyDetail label={managed?"Chat files":"Full workspace path"} value={chat.workspace||'Unavailable'}/>
  <CopyDetail label="Session ID" value={sessionIdentity(chat)}/>
  <p className="a-caption">{managed?'Files stored in this chat’s managed folder.':'Discovered in this folder’s Amplifier history. Chats started from the CLI or other tools can appear here too.'}</p>
  {draft.mode==='chat-rename'&&draft.id===chat.id?<ChatRename inputId={prefix+'-name'} chat={chat} act={act} cancel={()=>setDraft({})}/>:<div className="a-navigation-actions">
   <button type="button" className="a-link" data-action="session.select" onClick={()=>{close();choose(chat.id)}}><ArrowUpRight/>Open chat</button>
   {errorItem&&<button type="button" className="a-link" data-action="attention.read" disabled={reviewing} onClick={review}>{reviewing?'Marking reviewed…':'Mark error reviewed'}</button>}
   {reviewError&&<span role="alert">{reviewError}</span>}
   <button type="button" aria-label={`${chat.pinned?'Unpin':'Pin'} ${title}`} aria-pressed={!!chat.pinned} aria-busy={pinning||undefined} disabled={pinning} data-action="session.pin" onClick={togglePin}><Pin/>{chat.pinned?'Unpin':'Pin'}</button>
   {chat.pinned&&<><button type="button" aria-label={'Move '+title+' up'} data-action={orderAction} disabled={pinIndex<1} onClick={()=>move(-1)}><ArrowUp/>Move up</button><button type="button" aria-label={'Move '+title+' down'} data-action={orderAction} disabled={pinIndex<0||pinIndex===pins.length-1} onClick={()=>move(1)}><ArrowDown/>Move down</button></>}
   {pinError&&<span role="alert">{pinError}</span>}
   <button type="button" aria-label={'Rename '+title} data-action="view.update" onClick={()=>setDraft({mode:'chat-rename',id:chat.id,name:title})}><Pencil/>Rename</button>
   <button type="button" disabled={reading||readUnavailable} data-action={markedUnread?'attention.read':'session.markUnread'} onClick={changeRead}>{reading?'Saving…':markedUnread?'Mark read':'Mark unread'}</button>
   {readError&&<span role="alert">{readError}</span>}
   <ArchiveButton aria-label={(chat.archived?'Restore ':'Archive ')+title} chat={chat} archived={chat.archived}/>
   {managed&&!chat.parentId&&<button type="button" className="a-danger" aria-label={'Delete '+title} data-action="view.update" onClick={()=>{close();setDraft({mode:'chat-delete',id:chat.id,name:title})}}><Trash2/>Delete</button>}
  </div>}
 </>;
}
function SidebarSection({id,title,count,model,actions,children}){
 const contentId=React.useId(),collapsed=model.view.navSectionsCollapsed||[],closed=collapsed.includes(id);
 return <section className="a-sidebar-section" aria-label={title==='Pinned'?'Pinned items':title==='Recent'?'Recent chats':title} data-sidebar-section={id}>
  <div className="a-sidebar-section-heading"><button type="button" aria-expanded={!closed} aria-controls={contentId} data-action="view.update" onClick={()=>patch(model.act,{navSectionsCollapsed:closed?collapsed.filter(key=>key!==id):[...collapsed,id]})}><span>{title}</span><ChevronRight className={closed?'':'is-open'}/>{count!=null&&<small>{count}</small>}</button>{actions}</div>
  <div id={contentId} hidden={closed}>{children}</div>
 </section>;
}
export function ChatRow({chat,model,now,showLocation=true,handle,floating=false,landing=false,overview=false}){
 const preview=useChatTitle(chat);
 chat={...chat,title:preview.title,titlePreviewSource:preview.source};
 const {state,choose}=model,title=chat.title,activity=activityFor(chat,state);
 const selected=workSurface(state)==='chat'&&chat.id===state.selectedSessionId;
 const paths=!!state.settings?.workspaces?.showPaths&&showLocation;
 const hasWorkspace=showLocation&&!!chat.workspaceId&&chat.location?.kind!=='managed';
 const location=chat.location?.kind==='managed'?'No workspace':paths?chat.workspace:chat.workspaceName||chat.workspaceLabel||chat.workspace;
 const content=<>{handle}<button className="a-nav-chat-select" type="button" data-tooltip="off" onPointerEnter={revealNavigationTitles} onFocus={revealNavigationTitles} data-navigation-select data-action="session.select" aria-current={selected?'page':undefined} aria-label={title} onClick={()=>choose(chat.id)}><NavigationStatus activity={activity}/><span className="a-nav-chat-label"><span ref={preview.ref} data-scroll-title className="a-nav-chat-title"><span>{title}</span></span>{(!landing||showLocation)&&<small className="a-nav-chat-workspace" title={showLocation?chat.workspace:undefined}>{showLocation?location:activity.label}</small>}{landing&&chat.description&&<span className="a-landing-description">{chat.description}</span>}{landing&&<small className="a-landing-status">{activity.kind==='idle'?'Saved conversation':activity.label}</small>}</span><ActivityTime at={chat.recentActivityAt} now={now}/></button></>;
 const className='a-nav-chat '+(selected?'is-selected':'')+(landing?' a-landing-chat':'')+(overview?' a-overview-chat':'');
 // The drag preview uses the exact same contents without interactive flyouts.
 if(floating)return <div className={className} data-show-paths={paths} data-has-workspace={hasWorkspace}>{content}<MoreHorizontal className="a-navigation-more"/></div>;
 return <NavigationRow contextKey={navigationDetailsContext(state)} className={className} data-show-paths={paths} data-has-workspace={hasWorkspace} data-session-id={chat.id} label={title} details={({close})=><ChatDetails chat={chat} model={model} now={now} close={close}/>}>{content}<ChatShortcutBadge id={chat.id}/></NavigationRow>;
}
function ChatPagination({page,onChange,label='conversations'}){
 if(page.pages<2)return null;
 return <div className="a-nav-pagination"><span>{page.start+1}–{page.end} of {page.total}</span><div><button type="button" className="a-link" data-action="view.update" aria-label={'Show previous '+label} disabled={page.index===0} onClick={()=>onChange(page.index-1)}>Previous</button><button type="button" className="a-link" data-action="view.update" aria-label={'Show more '+label} disabled={page.index===page.pages-1} onClick={()=>onChange(page.index+1)}>More<ChevronRight/></button></div></div>;
}

export function useRecentShortcuts(host,state,act){
 const initial={limit:recentLimit(state.recentNavigation?.scope?.limit??state.view?.navRecentLimit),showAgentCreated:state.view?.navShowAgentCreated===true};
 const [settings,setSettings]=useState(initial),[page,setPage]=useState(state.recentNavigation||{items:[],total:0,remaining:0,limit:initial.limit});
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const ownerFor=current=>JSON.stringify([current?.navigationResourceScope?.[0],current?.navigationResourceScope?.[1],current?.generation]);
 const owner=ownerFor(state);
 const acceptedOwner=useRef(owner),observedOwner=useRef(owner);
 const accepted=useRef(page),intent=useRef(null),sequence=useRef(0),mounted=useRef(true),waiting=useRef(false),uncertain=useRef(false),readWait=useRef(null);
 const latest=useRef(null);latest.current={host,state,act};
 const scopeFor=current=>{
  const mode=current.sidebarNavigation?.pinned?.scope?.mode||'all';
  return {...(current.recentScope||{mode,workspaceId:mode==='all'?null:current.selectedWorkspaceId??null,filter:''}),
   ...(current.recentScope?{
    clientId:latest.current.host.clientId??current.recentScope.clientId,
    instanceId:latest.current.host.instanceId??current.recentScope.instanceId,
    generation:current.generation??current.recentScope.generation
   }:{}),
   selectedSessionId:current.selectedSessionId??null};
 };
 const desired=(value,current=latest.current.state)=>({...scopeFor(current),...value});
 const sameQuery=(scope,current)=>Object.entries(scope).every(([key,value])=>key==='selectedSessionId'||scopeFor(current)[key]===value);
 const newer=candidate=>acceptedOwner.current!==owner||(candidate?.scope?.viewRevision??0)>=(accepted.current?.scope?.viewRevision??0)
  &&((candidate?.scope?.viewRevision??0)>(accepted.current?.scope?.viewRevision??0)
   ||(candidate?.dataRevision??0)>=(accepted.current?.dataRevision??0));
 const accept=candidate=>{
  candidate={...candidate,items:[...new Map(candidate.items.map(row=>[row.id,row])).values()]};
  acceptedOwner.current=owner;accepted.current=candidate;uncertain.current=false;setPage(candidate);
  setSettings({limit:candidate.scope.limit,showAgentCreated:candidate.scope.showAgentCreated});
 };
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;sequence.current++;readWait.current?.abort()}},[]);
 useLayoutEffect(()=>{
  if(observedOwner.current===owner)return;
  observedOwner.current=owner;sequence.current++;readWait.current?.abort();waiting.current=false;
  intent.current=null;uncertain.current=false;setBusy(false);setError('');
 },[owner]);
 useLayoutEffect(()=>{
  const candidate=state.recentNavigation;
  if(waiting.current||!candidate||state.navigationResourceRetained?.recentNavigation||!newer(candidate))return;
  const pending=intent.current,wanted=pending?.wanted||{
   limit:recentLimit(state.view?.navRecentLimit??candidate.scope.limit),
   showAgentCreated:state.view?.navShowAgentCreated===true
  };
  if(recentPageMatches(candidate,desired(wanted))
   &&(!pending||sameQuery(pending.scope,state)
    &&candidate.scope.viewRevision>=pending.minRevision)){
   accept(candidate);
   if(intent.current){intent.current=null;setError('')}
  }
 },[state.recentNavigation,state.recentScope,state.selectedSessionId,state.navigationResourceRetained,owner,busy]);
 const change=async value=>{
  // This ref is set synchronously, before React renders or dispatch can await.
  // Scroll, fallback, toggle and Retry must all share the same flight.
  if(waiting.current||uncertain.current)return;
  const wanted={limit:accepted.current.limit,showAgentCreated:accepted.current.scope?.showAgentCreated,...value},ticket=++sequence.current;
  const requestOwner=ownerFor(latest.current.state);
 const scope=scopeFor(latest.current.state),minRevision=(accepted.current.scope?.viewRevision??0)+1;
  intent.current={wanted,scope,minRevision};waiting.current=true;setSettings(wanted);setBusy(true);setError('');
  try{
   const receipt=await patch(act,{navRecentLimit:wanted.limit,navShowAgentCreated:wanted.showAgentCreated});
   if(receipt?.accepted===false)throw Error(receipt.error||'Recent settings were not saved.');
   if(!mounted.current||ticket!==sequence.current)return;
   const controller=new AbortController();readWait.current=controller;
   const candidate=await waitForSnapshot(latest.current.host,current=>{
    if(ownerFor(current)!==requestOwner||!sameQuery(scope,current))
     throw Error('Recent changed during the read. The previous chats are kept.');
    const page=current.recentNavigation;
    return recentPageMatches(page,desired(wanted,current))
     &&!current.navigationResourceRetained?.recentNavigation
     &&page.scope.viewRevision>=minRevision&&newer(page)?page:null;
   },{signal:controller.signal});
   if(!mounted.current||ticket!==sequence.current)return;
   accept(candidate);intent.current=null;
  }catch(error){if(mounted.current&&ticket===sequence.current){uncertain.current=true;setError(error.message||'Recent could not be refreshed. The previous chats are kept.')}}
  finally{if(mounted.current&&ticket===sequence.current){waiting.current=false;setBusy(false)}}
 };
 const retry=async()=>{
  // Reconcile only. Never replay an uncertain view write (or task/input work).
  if(waiting.current)return;
  const ticket=++sequence.current;waiting.current=true;setBusy(true);
  const scope=scopeFor(latest.current.state),requestOwner=ownerFor(latest.current.state);
  try{
   let snapshot=await host.refreshResources();
   if(!mounted.current||ticket!==sequence.current)return;
   if(!snapshot?.recentNavigation||snapshot.navigationResourcePending?.recentNavigation){
    const controller=new AbortController();readWait.current=controller;
    snapshot=await waitForSnapshot(latest.current.host,current=>{
     if(ownerFor(current)!==requestOwner||!sameQuery(scope,current))throw Error('Recent changed during the read. The previous chats are kept.');
     if(current.navigationResourcePending?.recentNavigation?.status==='retry')throw Error('Recent could not be refreshed. The previous chats are kept.');
     const candidate=current.recentNavigation;
     return !current.navigationResourceRetained?.recentNavigation&&!current.navigationResourcePending?.recentNavigation
      &&recentPageMatches(candidate,desired({limit:recentLimit(current.view?.navRecentLimit??candidate?.scope?.limit),
       showAgentCreated:current.view?.navShowAgentCreated===true},current))&&newer(candidate)?current:null;
    },{signal:controller.signal});
   }
   if(!mounted.current||ticket!==sequence.current)return;
   const current=latest.current.host.getSnapshot(),read=snapshot?.recentNavigation,live=current.recentNavigation;
   // A shell snapshot received during this read may already be newer. Prefer
   // that evidence, never restore the delayed response over it.
   const after=(a,b)=>(a?.scope?.viewRevision??0)>(b?.scope?.viewRevision??0)
    ||(a?.scope?.viewRevision??0)===(b?.scope?.viewRevision??0)&&(a?.dataRevision??0)>(b?.dataRevision??0);
   const savedSettings=(value,snapshot)=>({
    limit:snapshot?.view?.navRecentLimit??value?.scope?.limit,
    showAgentCreated:snapshot?.view&&Object.hasOwn(snapshot.view,'navShowAgentCreated')
     ?snapshot.view.navShowAgentCreated===true:value?.scope?.showAgentCreated
   });
   const matching=(value,snapshot)=>recentPageMatches(value,desired(savedSettings(value,snapshot),current));
   const useLive=matching(live,current)&&(!matching(read,snapshot)||after(live,read));
   const candidate=useLive?live:read,saved=savedSettings(candidate,useLive?current:snapshot);
   if(ownerFor(current)!==requestOwner||ownerFor(useLive?current:snapshot)!==requestOwner
    ||!recentPageMatches(candidate,desired(saved,current))
    ||!sameQuery(scope,current)||current.navigationResourceRetained?.recentNavigation||!newer(candidate))
    throw Error('Recent changed during the read. Retry to read the current list.');
   const unsaved=intent.current&&!recentPageMatches(candidate,desired(intent.current.wanted,current));
   accept(candidate);intent.current=null;
   setError(unsaved?'The setting was not saved. Choose it again; no request was replayed.':'');
  }catch(error){if(mounted.current&&ticket===sequence.current)setError(error.message||'Recent is unavailable. The previous chats are kept.')}
  finally{if(mounted.current&&ticket===sequence.current){waiting.current=false;setBusy(false)}}
 };
 const queryPending=state.navigationResourcePending?.recentNavigation;
 const matching=acceptedOwner.current===owner&&page.scope?.showAgentCreated===(state.view?.navShowAgentCreated===true)
  &&Object.entries(scopeFor(state)).every(([key,value])=>key==='selectedSessionId'||page.scope?.[key]===value);
 const visiblePage=matching?page:{items:[],total:0,remaining:0,limit:settings.limit};
 return {page:visiblePage,settings,busy:busy||!error&&(queryPending?.status==='pending'||!matching&&!queryPending),
  error,blocked:uncertain.current||!!queryPending,change,retry};
}

function RecentShortcuts({host,model,now}){
 const {page,busy,error,blocked,change,retry}=useRecentShortcuts(host,model.state,model.act);
 const root=useRef(null),more=useRef(null),anchor=useRef(null),focused=useRef(null),follower=useRef(null),latest=useRef(null);
 const capture=()=>{
  const scroll=root.current?.closest('.a-nav-content');
  if(!scroll)return;
  const top=scroll.getBoundingClientRect().top;
  const row=[...root.current.querySelectorAll('[data-session-id]')].find(row=>row.getBoundingClientRect().bottom>top);
  anchor.current={scroll,scrollTop:scroll.scrollTop,rowId:row?.dataset.sessionId,offset:row?.getBoundingClientRect().top,focus:document.activeElement};
 };
 useLayoutEffect(()=>{
  const saved=anchor.current;
  if(busy||!root.current)return;
  const displaced=!document.activeElement?.isConnected||document.activeElement===document.body;
  const restore=move=>follower.current?follower.current.suppress(move):move();
  if(!saved){
   restore(()=>{});
   if(displaced&&focused.current&&!focused.current.isConnected)(more.current||root.current)?.focus({preventScroll:true});
   return;
  }
  const row=[...root.current.querySelectorAll('[data-session-id]')].find(row=>row.dataset.sessionId===saved.rowId);
  restore(()=>{saved.scroll.scrollTop=row&&saved.offset!=null?saved.scroll.scrollTop+row.getBoundingClientRect().top-saved.offset:saved.scrollTop});
  if(displaced&&saved.focus&&!saved.focus.isConnected)(more.current||root.current)?.focus({preventScroll:true});
  anchor.current=null;
 },[page,busy]);
 const update=value=>{capture();return change(value)};
 const load=()=>{if(!busy&&!blocked&&page.remaining>0)return update({limit:page.limit+20})};
 latest.current={load,canLoad:()=>!busy&&!blocked&&!error&&page.remaining>0
  &&document.visibilityState!=='hidden'&&!!root.current?.getClientRects().length
  &&!root.current.closest('[hidden],[inert],[aria-hidden="true"]')};
 useEffect(()=>{
  const scroll=root.current?.closest('.a-nav-content');if(!scroll)return;
  const following=followLaterHistory(scroll,()=>latest.current.canLoad(),()=>latest.current.load());
  follower.current=following;
  return()=>{following.dispose();follower.current=null};
 },[host]);
 return <div ref={root} tabIndex={-1} className="a-recent-shortcuts" aria-busy={busy} onFocusCapture={e=>{focused.current=e.target}} onBlurCapture={e=>{if(e.relatedTarget&&!root.current?.contains(e.relatedTarget))focused.current=null}}>
  {!error&&<NavigationResourceStatus state={model.state} resource="recentNavigation" label="recent chats" refresh={model.refreshResources}/>}
  {page.items.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now}/>)}
  {!page.items.length&&!busy&&!error&&!model.state.navigationResourcePending?.recentNavigation&&<p className="a-nav-empty">Your recent chats appear here.</p>}
  {(page.remaining>0||busy||error)&&<div className="a-recent-controls">
   {busy&&!model.state.navigationResourcePending?.recentNavigation&&<small role="status">Loading recent chats…</small>}
   {page.remaining>0&&<button ref={more} type="button" className="a-sidebar-all a-link a-recent-control" style={{minHeight:44}} aria-disabled={busy||blocked} data-action="view.update" onClick={load}>Load older chats</button>}
   {error&&<p role="alert">{error} <button type="button" className="a-link a-recent-control" aria-disabled={busy} onClick={()=>{if(busy)return;capture();return retry()}}>Retry</button></p>}
  </div>}
 </div>;
}
export function PinnedChats({page,model,now}){
 const [error,setError]=useState('');
 const items=page.items.map(chat=>({...chat,label:chat.title||'Untitled conversation'})),visible=items.map(chat=>chat.id);
 const reorder=async(next,{id,to})=>{
  setError('');
  try{
   const response=await model.act('session.pinOrder',{ids:movePin(model.state.pinnedSessionIds||[],id,visible[to])});
   const receipt=response?.result?.accepted!==undefined?response.result:response;
   if(receipt?.accepted===false)throw Error(receipt.error||'Could not reorder pins.');
  }catch(err){setError(err.message||'Could not reorder pins. Please try again.');throw err}
 };
 return <><ReorderList items={items} ids={visible} onChange={reorder} className="a-pinned-chats" floatingClassName="a-pinned-drag" action="session.pinOrder" renderItem={(chat,{handle,floating})=><ChatRow chat={chat} model={model} now={now} handle={handle} floating={floating}/>}/>
  {error&&<p role="alert" className="a-danger">{error}</p>}
  {!page.total&&<p className="a-nav-empty">Pin a chat from its actions menu to keep it here.</p>}
  <ChatPagination page={page} label="pinned chats" onChange={index=>patch(model.act,{navPinnedPage:index})}/>
 </>;
}
export function PinnedItems({page,model,workspaceModel=model,now,navigation,canEdit=true}){
 const [error,setError]=useState(null),[reading,setReading]=useState(false);
 const owner=useRef({alive:true,version:0,read:null});
 useEffect(()=>{const current=owner.current;current.alive=true;return()=>{current.alive=false;current.version++;current.read?.controller.abort()}},[model.host]);
 const settle=usePinOrderSettlement(model.host);
 const items=page.items.map(item=>({...item,label:item.kind==='workspace'?(item.row.customName||item.row.name):(item.row.title||'Untitled conversation')})),visible=items.map(item=>item.id);
 const reorder=async(next,{id,basis,target,contextKey})=>{
  const version=++owner.current.version;owner.current.read?.controller.abort();setError(null);setReading(false);
  try{
   // A host publication may precede React's next render. Never combine its new
   // total vector with this render's old page/drag target.
   const current=model.host?.getSnapshot()||model.state;
   if(pinOrderPending(current)||pinOrderScope(current)!==contextKey
    ||current.pinnedNavigation?.items?.map(item=>item.id).join('\0')!==basis.join('\0'))
    throw Error('The pinned list is updating. Move the item again when it is ready.');
   const previous=[...(current.pinnedItemIds||[])],expected=movePin(previous,id,target);
   await settle(()=>model.act('navigation.pinOrder',{ids:expected}),previous,expected,next);
   return {orderKey:expected.join('\0')};
  }catch(err){if(err.name!=='AbortError'&&owner.current.alive&&owner.current.version===version)setError({message:err.message||'Could not reorder pins. Please try again.',read:err.pinOrderDisplay===true,previous:err.pinOrderPrevious,expected:err.pinOrderExpected,visible:err.pinOrderVisible});throw err}
 };
 const refresh=async()=>{
  if(owner.current.read)return;
  const token={version:owner.current.version,scope:pinOrderScope(model.host.getSnapshot()),controller:new AbortController(),error};owner.current.read=token;setReading(true);
  const current=()=>owner.current.alive&&owner.current.version===token.version&&pinOrderScope(model.host.getSnapshot())===token.scope;
  try{
   // One read, one observer/deadline. HTTP can return while the cold page is
   // pending; only the saved order's completed projection settles this error.
   // In particular, joining a pre-save read cannot confirm the old coherent page.
   const select=snapshot=>{
    if(!current())throw new DOMException('Pinned read superseded','AbortError');
    if(!pinOrderPending(snapshot)&&snapshot.pinnedItemIds?.join('\0')!==token.error.previous?.join('\0')
     &&snapshot.pinnedItemIds?.join('\0')!==token.error.expected?.join('\0'))
     throw Object.assign(Error('Pins changed elsewhere. The current order is shown.'),{superseded:true});
    return !pinOrderPending(snapshot)&&snapshot.pinnedItemIds?.join('\0')===token.error.expected?.join('\0')
     &&snapshot.pinnedNavigation?.items?.map(item=>item.id).join('\0')===token.error.visible?.join('\0')?snapshot:null;
   };
   if(!select(model.host.getSnapshot())){
    const waiting=waitForSnapshot(model.host,select,{signal:token.controller.signal,
     timeoutMessage:'Could not confirm the saved pin order. No pin command was repeated. Refresh the pinned list again.'});
    await Promise.race([waiting,Promise.resolve().then(()=>model.host.refreshResources()).then(()=>waiting)]);
   }
   if(current())setError(null);
  }catch(err){if(err.name!=='AbortError'&&current())setError({...token.error,message:err.message||'Could not refresh the pinned list.',read:!err.superseded})}
  finally{token.controller.abort();if(owner.current.read===token)owner.current.read=null;if(owner.current.alive&&owner.current.version===token.version){setReading(false);if(!current())setError(null)}}
 };
 const browse=async path=>{
  navigation?.browse('workspaces');
  await patch(workspaceModel.act,{navWorkspacePath:path,navWorkspaceMode:'folders',navWorkspaceFilter:'',navWorkspacePage:1});
  if(!navigation)return patch(model.act,{navWorkspaceList:true});
 };
 const open=row=>navigation?navigation.browse('workspace',row.workspaceId):workspaceModel.act('workspace.select',{id:row.workspaceId});
 const newChat=row=>workspaceModel.act('session.draft',{workspace:row.path,workspaceId:row.workspaceId,location:{kind:'workspace'}});
 return <><ReorderList items={items} ids={visible} busy={pinOrderPending(model.state)} queueChanges contextKey={pinOrderScope(model.state)} onQueueDiscard={()=>setError(value=>value?{...value,message:value.message+' Later queued moves were not sent.'}:value)} onQueueFull={()=>setError({message:'Move queue is full. Wait for the saved order before moving another item.',read:false})} orderKey={(model.state.pinnedItemIds||[]).join('\0')} onChange={reorder} className="a-pinned-chats" floatingClassName="a-pinned-drag" action="navigation.pinOrder" renderItem={(item,{handle,floating})=>item.kind==='workspace'?<WorkspaceRow row={item.row} state={workspaceModel.state} act={workspaceModel.act} now={now} {...{handle,floating,browse,open,newChat}} compact pinnedShortcut onEdit={canEdit?workspaceModel.setDraft:undefined}/>:<ChatRow chat={item.row} model={model} now={now} handle={handle} floating={floating}/>}/>
  {error&&<p role="alert" className="a-danger">{error.message}{error.read&&model.host?.refreshResources&&<> <button type="button" className="a-link" disabled={reading} onClick={refresh}>{reading?'Reading pins…':'Refresh pinned list'}</button></>}</p>}
  <ChatPagination page={page} label="pinned items" onChange={index=>patch(model.act,{navPinnedPage:index})}/>
 </>;
}
function LandingChats({page,model,now,showLocation,planAct,layout,identity,onPage}){
 const {selectedSessionId,clientId,instanceId,generation,...query}=page.scope;
 return <LandingCollection page={page} layout={layout} identity={identity} queryKey={query} onPage={onPage} className="a-nav-chats" wrap={planAct?(nodes,rows)=><WorkspacePlans ids={rows.map(chat=>chat.id).slice(0,50)} act={planAct}>{nodes}</WorkspacePlans>:undefined}>{chat=><div className="a-landing-chat-item"><ChatRow chat={chat} model={model} now={now} showLocation={showLocation} landing/>{planAct&&<WorkspacePlan id={chat.id}/>}</div>}</LandingCollection>;
}
export function ChatSearch({page,model,view,viewAct,now,layout,identity='chats'}){
 const root=useRef(null),input=useRef(null);
 const counts=page.activityCounts||{};
 const active=(view.navArchive||'active')!=='active'||(view.navStatusFilter||'all')!=='all'||view.navLocationFilter==='managed';
 useEffect(()=>{input.current?.focus()},[]);
 const update=value=>patch(viewAct,value);
 const clear=()=>{update({navFilter:'',navArchive:'active',navStatusFilter:'all',navLocationFilter:'all'});input.current?.focus()};
 const keyboard=event=>{
  if(event.key==='Escape'&&event.target===input.current){event.preventDefault();if(view.navFilter)update({navFilter:''});return}
  if(!['ArrowDown','ArrowUp'].includes(event.key)||!(event.target===input.current||event.target.matches('.a-nav-chat-select')))return;
  const rows=[...root.current.querySelectorAll('.a-nav-chat-select')],index=rows.indexOf(event.target);
  event.preventDefault();
  const next=event.key==='ArrowDown'?index+1:index-1;
  (next<0?input.current:rows[Math.min(next,rows.length-1)])?.focus();
 };
 return <section className="a-chat-search-page" aria-label="Chat search" ref={root} onKeyDown={keyboard}>
  <div className="a-nav-search"><Search/><input ref={input} type="search" aria-label="Search chats" placeholder="Search chat names, workspaces and descriptions" maxLength={500} value={view.navFilter||''} data-action="view.update" onChange={e=>update({navFilter:e.target.value})}/>{view.navFilter&&<button className="a-icon" type="button" aria-label="Clear search" onClick={()=>{update({navFilter:''});input.current?.focus()}}><X/></button>}</div>
  <div className="a-search-tools">
   <details className="a-search-filters"><summary>Filters{active?' · active':''}</summary><div className="a-search-filter-fields">
    <LibraryFilters state={{...model.state,view}} act={viewAct}/>
    <label>Location<select aria-label="Filter chats by location" value={view.navLocationFilter||'all'} onChange={e=>update({navLocationFilter:e.target.value})}><option value="all">All locations</option><option value="managed">No workspace</option></select></label>
    {!layout&&<label>Status<select aria-label="Filter chats by status" value={view.navStatusFilter||'all'} onChange={e=>update({navStatusFilter:e.target.value})}><option value="all">Any status</option><option value="attention">Needs attention{counts.attention?` (${counts.attention})`:''}</option><option value="working">Working{counts.working?` (${counts.working})`:''}</option><option value="unread">Ready for you</option><option value="idle">Pick up again</option></select></label>}
    <small>Use * and ? for patterns in names and paths.</small>
   </div></details>
   <label className="a-search-sort">Sort<select aria-label="Sort conversations" value={view.navSort||'activity'} onChange={e=>update({navSort:e.target.value})}><option value="activity">Recent activity</option><option value="created">Newest created</option><option value="name">Name</option></select></label>
  </div>
  <div className="a-search-result-summary"><span role="status">{page.unavailable?'Search unavailable':page.pending?'Searching…':`${page.total} ${page.total===1?'chat':'chats'}${view.navFilter?' found':''}`}</span>{active&&<button className="a-link" type="button" onClick={()=>update({navArchive:'active',navStatusFilter:'all',navLocationFilter:'all'})}>Clear filters</button>}</div>
  {layout&&<WorkStatusFilters value={view.navStatusFilter||'all'} counts={counts} onChange={navStatusFilter=>update({navStatusFilter})}/>}
  <div aria-label="Search results">{layout?<LandingChats page={page} model={model} now={now} showLocation layout={layout} identity={identity} onPage={index=>update({navChatPage:{...page.scope,index}})}/>:<div className="a-nav-chats" aria-busy={!!page.pending}>{page.items.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now} showLocation/>)}</div>}</div>
  {!page.total&&!page.pending&&!page.unavailable&&<div className="a-search-empty"><h2>No chats found</h2><p>Try another name or workspace, or clear your filters.</p><button type="button" className="a-soft" onClick={clear}>Clear search and filters</button></div>}
  {layout!=='focus'&&<ChatPagination page={page} onChange={index=>update({navChatPage:{...page.scope,index}})}/>}
 </section>;
}
export function ChatList({page,model,view,viewAct,now,showLocation,planAct,layout,identity}){
 const counts=page.activityCounts||{};
 return <>
  <div className="a-nav-search"><Search/><input aria-label="Filter conversations" maxLength={500} type="search" value={view.navFilter||''} placeholder={showLocation?'Find chats or paths · * ?':'Find chats · * ? patterns'} data-action="view.update" onChange={e=>patch(viewAct,{navFilter:e.target.value})}/></div>
  <details className="a-sidebar-filters"><summary>Filters{view.navFilter||view.navArchive&&view.navArchive!=='active'||view.navStatusFilter&&view.navStatusFilter!=='all'||view.navLocationFilter==='managed'?' · active':''}</summary>
   <LibraryFilters state={{...model.state,view}} act={viewAct}/>
   <label className="a-managed-chat-filter">Sort<select aria-label="Sort conversations" value={view.navSort||'activity'} data-action="view.update" onChange={e=>patch(viewAct,{navSort:e.target.value})}><option value="activity">Recent activity</option><option value="created">Newest created</option><option value="name">Name</option></select></label>
   {showLocation&&<label className="a-managed-chat-filter">Location<select aria-label="Filter chats by location" value={view.navLocationFilter||'all'} data-action="view.update" onChange={e=>patch(viewAct,{navLocationFilter:e.target.value})}><option value="all">All locations</option><option value="managed">No workspace</option></select></label>}
   {!layout&&<div className="a-navigation-filters" role="group" aria-label="Conversation activity filters">
    <button type="button" aria-pressed={view.navStatusFilter==='attention'} data-action="view.update" onClick={()=>patch(viewAct,{navStatusFilter:view.navStatusFilter==='attention'?'all':'attention'})}><AlertCircle/>{counts.attention??0} need attention</button>
    <button type="button" aria-pressed={view.navStatusFilter==='working'} data-action="view.update" onClick={()=>patch(viewAct,{navStatusFilter:view.navStatusFilter==='working'?'all':'working'})}><LoaderCircle/>{counts.working??0} working</button>
    {view.navStatusFilter&&view.navStatusFilter!=='all'&&<button type="button" className="a-link" data-action="view.update" onClick={()=>patch(viewAct,{navStatusFilter:'all'})}>Clear</button>}
   </div>}
  </details>
  {page.pending&&<p role="status">Loading conversations…</p>}
  {layout&&<WorkStatusFilters value={view.navStatusFilter||'all'} counts={counts} onChange={navStatusFilter=>patch(viewAct,{navStatusFilter})}/>}
  {layout?<LandingChats {...{page,model,now,showLocation,planAct,layout,identity}} onPage={index=>patch(viewAct,{navChatPage:{...page.scope,index}})}/>:<WorkspacePlans ids={page.items.map(chat=>chat.id).slice(0,50)} act={planAct}><div className="a-nav-chats">{page.items.map(chat=><div key={chat.id}><ChatRow chat={chat} model={model} now={now} showLocation={showLocation}/>{planAct&&<WorkspacePlan id={chat.id}/>}</div>)}</div></WorkspacePlans>}
  {!page.total&&!page.pending&&!page.unavailable&&<p className="a-nav-empty">{view.navFilter||view.navStatusFilter&&view.navStatusFilter!=='all'?'No matching chats.':'Your conversations will appear here.'}</p>}
  {layout!=='focus'&&<ChatPagination page={page} onChange={index=>patch(viewAct,{navChatPage:{...page.scope,index}})}/>}
 </>;
}
const HOME_FILTERS={navArchive:'active',navCollection:null,navFilter:'',navStatusFilter:'all',navLocationFilter:'all',navSort:'activity'};
function LegacyConversationList({host,workspaceHost}){
 const model=useNavigationController(host,'chats'),{state,act,view,workspace,history,refreshing}=model;
 const workspaceModel=useNavigationController(workspaceHost||host,'workspaces'),workspaceState=workspaceModel.state,now=useActivityClock(),workspaceAct=workspaceHost?.dispatch||act;
 const [creating,setCreating]=useState(false),createButton=useRef(null);
 const closeCreate=()=>{setCreating(false);requestAnimationFrame(()=>createButton.current?.focus())};
 const sidebar=state.sidebarNavigation;
 const homeView={...HOME_FILTERS,navChatScope:sidebar?.pinned?.scope.mode||'all',navPinnedPage:view.navPinnedPage};
 const recentView={...HOME_FILTERS,...sidebar?.recentView,...view.navRecentView,navShowAgentCreated:view.navShowAgentCreated===true};
 const pins=state.pinnedNavigation||chatPage({...state,view:homeView},workspace,{section:'pinned'});
 const recent=chatPage({...state,view:{...recentView,navChatScope:homeView.navChatScope}},workspace,{section:'recent'});
 const workspaceChats=chatPage({...state,view:{...view,navChatScope:'workspace'}},workspace,{section:'workspace'});
 const recentAct=(name,args)=>{
  if(name!=='view.update')return act(name,args);
  const next={...recentView,...args.patch};
  if(!Object.hasOwn(args.patch,'navChatPage'))delete next.navChatPage;
  return patch(act,{navRecentView:next});
 };
 const browsing=view.navWorkspaceList!==false||!workspace;
 const browseWorkspaces=()=>patch(act,{navWorkspaceList:true});
 const openWorkspace=()=>patch(act,{navWorkspaceList:false,navChatScope:'workspace',navFilter:'',navStatusFilter:'all',navArchive:'active',navLocationFilter:'all'});
 const selectedWorkspace=workspaceState.workspaceExplorer?.selected;
 return <div className="a-sidebar-navigation">
  <NavigationEditor model={model}/>
  {!workspaceHost&&<NavigationEditor model={workspaceModel}/>}
  {!!pins.total&&<SidebarSection id="pinned" title="Pinned" count={pins.total} model={model}>{state.pinnedNavigation?<PinnedItems page={pins} model={model} workspaceModel={workspaceModel} canEdit={!!workspaceHost||!model.draft.mode} now={now}/>:<PinnedChats page={pins} model={model} now={now}/>}</SidebarSection>}
  <SidebarSection id="workspaces" title="Workspaces" count={workspaceState.workspaceExplorer?.totalWorkspaces} model={model} actions={<button ref={createButton} type="button" className="a-icon" aria-label="New workspace" data-action="view.update" onClick={()=>{setCreating(true);patch(act,{navSectionsCollapsed:(view.navSectionsCollapsed||[]).filter(key=>key!=='workspaces')})}}><Plus/></button>}>
   {creating&&<WorkspaceForm state={workspaceState} act={workspaceAct} onDone={closeCreate} onCancel={closeCreate}/>}
   {browsing?(workspaceHost?<PairedWorkspaceBrowser host={workspaceHost} onOpen={openWorkspace}/>:<WorkspaceExplorer state={state} act={act} onOpen={openWorkspace} onEdit={model.setDraft} heading={false} previewLimit={8}/>):<div className="a-workspace-chat-view" aria-label={'Chats in '+workspace.name}>
    <button type="button" className="a-navigation-back" data-action="view.update" onClick={browseWorkspaces}><ArrowLeft/>All workspaces</button>
    <NavigationRow contextKey={navigationDetailsContext(state)} className="a-navigation-workspace" label={workspace.name} details={({close})=><WorkspaceDetails row={selectedWorkspace||{...workspace,chatCount:workspaceChats.total}} now={now} actions={<><button type="button" data-action="view.update" onClick={()=>{close();workspaceAct('view.update',{patch:{workspaceDraft:{mode:'rename',id:workspace.id,name:workspace.name}}})}}><Pencil/>Rename</button><button type="button" className="a-danger" disabled={workspaceState.library?.workspaceCount<2} data-action="view.update" onClick={()=>{close();workspaceAct('view.update',{patch:{workspaceDraft:{mode:'remove',id:workspace.id,name:workspace.name}}})}}><Trash2/>Remove registration</button></>}/>}><Folder/><div><strong>{workspace.name}</strong><small title={workspace.path}>{compactParent(workspace.path)}</small></div></NavigationRow>
    <div className="a-sidebar-workspace-actions"><span>{workspaceChats.total} chats</span><button type="button" className="a-link" aria-label={'New chat in '+workspace.name} data-action="session.draft" onClick={()=>act('session.draft',{workspace:workspace.path})}><Plus/>New chat</button></div>
    <ChatList page={workspaceChats} model={model} view={view} viewAct={act} now={now} showLocation={false}/>
   </div>}
  </SidebarSection>
  <SidebarSection id="recent" title="Recent" count={recent.total} model={model} actions={<button type="button" className="a-icon" aria-label="Refresh workspaces and chats" data-action="history.refresh" disabled={refreshing} onClick={()=>act('history.refresh',{})}><RefreshCw className={refreshing?'a-progress-spinner':undefined}/></button>}>
   <ChatList page={recent} model={model} view={recentView} viewAct={recentAct} now={now} showLocation/>
  </SidebarSection>
  {history.loading&&!recent.items.length&&!workspaceChats.items.length&&<p role="status">Finding existing chats…</p>}{history.error&&<p role="alert" className="a-danger">{history.error}</p>}
 </div>;
}

function StatusGroup({kind,model,navigation,now}){
 const group=workGroups[kind],view=navigation.sidebarView,id=useId(),root=useRef(null),restore=useRef(null);
 const collapsed=view.navStatusCollapsed||[],expanded=view.navStatusExpanded||[],closed=collapsed.includes(kind),all=expanded.includes(kind);
 const page=model.state[group.resource],requested=view.navStatusPages?.[kind]||0;
 const ready=hasNavigationResource(model.state,group.resource)&&page?.requestedIndex===requested
  &&page.workScope===(model.state.workHomePinnedScope||'all');
 const update=navigation.updateSidebar;
 useLayoutEffect(()=>{if(ready&&restore.current){root.current?.querySelector(`[data-status-page="${restore.current}"]`)?.focus();restore.current=null}},[ready,page?.index]);
 const turn=(index,direction)=>{restore.current=direction;update({navStatusPages:{...view.navStatusPages,[kind]:index}})};
 return <section ref={root} className="a-sidebar-section a-status-group" aria-label={group.title} data-status-group={kind}>
  <div className="a-sidebar-section-heading"><button type="button" aria-expanded={!closed} aria-controls={id} onClick={()=>update({navStatusCollapsed:closed?collapsed.filter(key=>key!==kind):[...collapsed,kind]})}><span>{group.title}</span><ChevronRight className={closed?'':'is-open'}/>{!closed&&ready&&kind!=='idle'&&<small>{page.total}</small>}</button></div>
  <div id={id} hidden={closed}>
   {!closed&&<><NavigationResourceStatus state={model.state} resource={group.resource} label={group.title.toLowerCase()} refresh={model.refreshResources}/>
    {!ready&&!model.state.navigationResourcePending?.[group.resource]&&<p role="status" className="a-nav-empty">Loading chats…</p>}
    {ready&&(page.unavailable?<p className="a-nav-empty" role="alert">This workspace is unavailable.</p>:<>
     {(all?page.items:page.items.slice(0,2)).map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now}/>)}
     {!page.total&&<p className="a-nav-empty">{group.empty}</p>}
     {page.total>2&&<button className="a-sidebar-all a-link" type="button" aria-label={(all?'Show fewer ':'Show more ')+group.title.toLowerCase()+' chats'} onClick={()=>update({navStatusExpanded:all?expanded.filter(key=>key!==kind):[...expanded,kind]})}>{all?'Show fewer':'Show more'}<ChevronRight/></button>}
     {all&&page.pages>1&&<div className="a-nav-pagination" aria-label={group.title+' pages'}><span>{page.start+1}–{page.end} of {page.total}</span><div><button type="button" className="a-link" data-status-page="previous" disabled={!page.index} aria-label={'Previous '+group.title.toLowerCase()+' page'} onClick={()=>turn(page.index-1,'previous')}>Previous</button><button type="button" className="a-link" data-status-page="next" disabled={page.index+1>=page.pages} aria-label={'Next '+group.title.toLowerCase()+' page'} onClick={()=>turn(page.index+1,'next')}>Next<ChevronRight/></button></div></div>}
    </>)}
   </>}
  </div>
 </section>;
}

function QuietSidebar({host,workspaceHost,navigation}){
 const controller=useNavigationController(host,'chats'),now=useActivityClock();
 const workspaceController=useNavigationController(workspaceHost||host,'workspaces');
 const model={...controller,state:sidebarReadingSnapshot(controller.state)};
 const workspaceModel={...workspaceController,state:sidebarReadingSnapshot(workspaceController.state)},workspaceState=workspaceModel.state;
 const pins=model.state.pinnedNavigation||model.state.sidebarNavigation?.pinned||{items:[],total:0,pages:1};
 const status=navigation.sidebarView.navMode==='status';
 return <div className={'a-sidebar-navigation a-quiet-sidebar'+(status?' a-status-sidebar':'')}>
  <NavigationEditor model={model} deleteOwner={(model.view.workSurface||'chat')==='chat'}/>
  {!workspaceHost&&<NavigationEditor model={workspaceModel}/>}
  {status?Object.keys(workGroups).map(kind=><StatusGroup key={kind} kind={kind} model={model} navigation={navigation} now={now}/>):<>
  {(!!pins.total||model.state.navigationResourcePending?.pinnedNavigation)&&<SidebarSection id="pinned" title="Pinned" model={model}><NavigationResourceStatus state={model.state} resource="pinnedNavigation" label="pinned chats and workspaces" refresh={model.refreshResources}/>{model.state.pinnedNavigation?<PinnedItems page={pins} model={model} workspaceModel={workspaceModel} canEdit={!!workspaceHost||!model.draft.mode} now={now} navigation={navigation}/>:<PinnedChats page={pins} model={model} now={now}/>}</SidebarSection>}
  <SidebarSection id="workspaces" title="Workspaces" model={model} actions={<button type="button" className="a-icon" aria-label="New workspace" onClick={()=>navigation.create()}><Plus/></button>}>
   <NavigationResourceStatus state={workspaceState} resource="workspaceShortcuts" label="workspaces" refresh={workspaceModel.refreshResources}/>
   {(hasNavigationResource(workspaceState,'workspaceShortcuts')||workspaceState.navigationResourceRetained?.workspaceShortcuts)?<WorkspaceExplorer state={workspaceState} act={workspaceHost?.dispatch||model.act} compact heading={false} onSelect={row=>navigation.browse('workspace',row.workspaceId)}/>:!workspaceState.navigationResourcePending?.workspaceShortcuts&&<p role="status">Loading workspaces…</p>}
   <button type="button" className="a-sidebar-all a-link" data-action="view.update" onClick={()=>navigation.browse('workspaces')}>All workspaces<ChevronRight/></button>
  </SidebarSection>
  <SidebarSection id="recent" title="Recent chats" model={model} actions={<>
   {model.history.error&&<span role="alert">{model.history.error}</span>}
   <button type="button" className="a-icon" aria-label="Refresh workspaces and chats" data-action="history.refresh" disabled={model.refreshing} onClick={()=>model.act('history.refresh',{})}><RefreshCw className={model.refreshing?'a-progress-spinner':undefined}/></button>
  </>}>
   <RecentShortcuts host={host} model={model} now={now}/>
  </SidebarSection>
  </>}
 </div>;
}
export function ConversationList(props){
 const navigation=useContext(WorkNavigationContext);
 return navigation?<QuietSidebar {...props} navigation={navigation}/>:<LegacyConversationList {...props}/>;
}
