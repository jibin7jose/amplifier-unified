import {UIUseShortcut} from './ui-use';
import {WorkspaceWork,SharedWorkBrowser} from './workspace-work';
import {ArchiveButton} from './archive-feedback.jsx';
import {WorkspaceConfiguration,WorkspaceSettingsBoundary} from './workspace-configuration';
import {useChatTitle} from './use-chat-title';
import React,{useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Home as HomeIcon,Folder,Plus,Search,ChevronRight,ArrowLeft,Settings,Info,MoreHorizontal,MessageCircle,Pin,Archive,Bug,Pencil,Share2,ChevronDown,Trash2,ScanEye,PanelLeft,FileText,Copy,X,AudioLines,Mic,MicOff,Bell,SlidersHorizontal,CalendarClock} from 'lucide-react';
import {WorkNavigationContext,workSurface} from './work-navigation';
import {AdaptiveWorkHome} from './adaptive-work.jsx';
import {WorkLandingFrame,LandingToolbar,useLandingLayout} from './work-landing';
import {useNavigationController,ChatList,NavigationEditor,ChatSearch} from './shell/navigation-components';
import {WorkspaceCleanup} from './storage-settings';
import {WorkspaceExplorer} from './workspace-explorer';
import {WorkspaceForm,WorkspacePicker} from './workspace-setup';
import {WorkspaceReadiness} from './workspace-starters';
import {WorkspaceResourcesOwner,WorkspaceResourcesSlot} from './workspace-resource-inventory';
import {WorkspaceBundleDefault} from './bundle-controls';
import {newChatSetup} from './new-chat';
import {useActivityClock,CopyDetail} from './navigation-details';
import {useModalFocus,useNarrowScreen,navigationExpanded} from './responsive-navigation';
import {ShellSlot} from './shell/runtime';
import {CanvasToggle} from './shell-panels';
import {AttentionBadge} from './attention';
import {chatPage,isTopLevelChat} from './chat-navigation';
import {sessionStatus} from './session-status';
import {ownershipState} from './ownership';
import {hasNavigationResource} from './shell/navigation-demand';
import {NavigationResourceStatus} from './shell/navigation-resource-status';
import {DestinationHistory} from './destination-history.jsx';

export function WorkDialog({label,onClose,children,initialFocus}){
 const ref=useRef(null);useModalFocus(ref,true,onClose);
 useEffect(()=>{if(initialFocus)ref.current?.querySelector(initialFocus)?.focus()},[initialFocus]);
 return createPortal(<div className="a-work-dialog-backdrop" onSubmit={e=>e.stopPropagation()} onPaste={e=>e.stopPropagation()} onDrop={e=>e.stopPropagation()} onClick={e=>{if(e.target===e.currentTarget)onClose()}}><section ref={ref} role="dialog" aria-modal="true" aria-label={label} className="a-work-dialog"><div className="a-work-dialog-head"><strong>{label}</strong><button type="button" className="a-icon" aria-label={'Close '+label} onClick={onClose}><X/></button></div>{children}</section></div>,document.getElementById('amp-one'));
}
export function ComposerWorkspace({state,act}){
 const [open,setOpen]=useState(false),trigger=useRef(null),setup=newChatSetup(state),workspace=state.workspaces?.find(row=>row.path===setup.workspace);
 const close=()=>{setOpen(false);requestAnimationFrame(()=>trigger.current?.focus())};
 return <><button type="button" ref={trigger} className="a-composer-workspace" aria-label="Choose workspace" aria-expanded={open} onClick={event=>{event.currentTarget.focus();setOpen(true)}}><Folder/><span>{setup.location?.kind==='managed'||!setup.workspace?'No workspace':workspace?.name||setup.workspace.split('/').at(-1)}</span><ChevronRight/></button>{open&&<WorkDialog initialFocus='input[type="search"]' label="Choose a workspace" onClose={close}><WorkspacePicker state={state} act={act} setup={setup} onChange={async value=>{const result=await act('view.update',{patch:{newSessionDraft:{...setup,...value}}});if(!result?.accepted)throw Error(result?.error||'Could not select this workspace.');close()}}/></WorkDialog>}</>;
}
export function WorkspaceCreation({state,act,onClose}){
 const nav=useContext(WorkNavigationContext);
 return <WorkDialog label="Workspace" onClose={onClose}><WorkspaceForm state={state} act={act} fromDraft={!state.selectedSessionId} onCancel={onClose} onDone={result=>{onClose();if(result?.workspaceId)nav.browse('workspace',result.workspaceId);else nav.browse('workspaces')}}/></WorkDialog>;
}
function ActionMenu({label,children,icon=<MoreHorizontal/>,badge,caption}){
 const ref=useRef(null),[open,setOpen]=useState(false);
 useEffect(()=>{if(!open)return;ref.current?.querySelector('.a-work-menu-items button')?.focus();const dismiss=e=>{if(!ref.current?.contains(e.target))setOpen(false)};const escape=e=>{if(e.key==='Escape'){setOpen(false);ref.current?.querySelector('button')?.focus()}};document.addEventListener('pointerdown',dismiss);document.addEventListener('keydown',escape);return()=>{document.removeEventListener('pointerdown',dismiss);document.removeEventListener('keydown',escape)}},[open]);
 return <div className="a-work-menu" ref={ref} onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))setOpen(false)}}><button type="button" className={caption?'a-soft a-chat-menu-trigger':'a-icon'} aria-label={label} aria-expanded={open} onClick={()=>setOpen(!open)}>{icon}{caption&&<><span>{caption}</span><ChevronDown/></>}{badge}</button>{open&&<div className="a-work-menu-items" role="group" aria-label={label} onClickCapture={()=>ref.current?.querySelector('button')?.focus()} onClick={e=>{if(e.target.closest('button'))setOpen(false)}}>{children}</div>}</div>;
}
export function AppFooter({state,connected,open,act}){
 return <div className="a-work-footer">
  <button type="button" className="a-footer-action" onClick={()=>open('automation')}><CalendarClock/><span>Automation</span></button>
  <button type="button" className="a-footer-action" title="Activity and unread notifications" onClick={()=>open('activity')}><Bell/><span>Activity</span><AttentionBadge state={state}/></button>
  <button type="button" className="a-footer-action" onClick={()=>open('settings')}><Settings/><span>Settings</span><AttentionBadge state={state} settings/></button>
  <div className="a-footer-status"><ShellSlot name="app.status"><span className={`a-dot ${connected?'':'pending'}`} title={connected?'Connected':'Reconnecting'}/></ShellSlot><span>{connected?'Connected':'Reconnecting…'}</span><ShellSlot name="app.actions"><ActionMenu label="App options"><button onClick={()=>open('feedback')}><Bug/>Send feedback</button><button onClick={()=>open('agent')}><ScanEye/>What the agent sees</button></ActionMenu></ShellSlot></div>
 </div>;
}
export function WorkHeader({state,session,act,open,narrow,presentation,fileDropHandlers}){
 const nav=useContext(WorkNavigationContext),surface=workSurface(state),browsing=surface!=='chat';
 const displayTitle=useChatTitle(session,{eager:!browsing}).title;
 const setup=!session&&!browsing?newChatSetup(state):null;
 const workspace=state.workspaces?.find(row=>setup?setup.location?.kind!=='managed'&&row.path===setup.workspace:row.id===(browsing?state.view.workWorkspaceId:state.selectedWorkspaceId));
 const navigationNarrow=useNarrowScreen('(max-width: 900px)');
 const navOpen=navigationExpanded(state.view,navigationNarrow),header=useRef(null);
 useLayoutEffect(()=>{
  const node=header.current;if(!node)return;
  const measure=()=>node.closest('#amp-one')?.style.setProperty('--a-navigation-top',`${node.getBoundingClientRect().bottom}px`);
  measure();const observer=new ResizeObserver(measure);observer.observe(node);window.addEventListener('resize',measure);
  return()=>{observer.disconnect();window.removeEventListener('resize',measure)};
 },[]);
 const title=surface==='home'?'Home':surface==='workspaces'?'All workspaces':surface==='chats'?'All chats':surface==='workspace'?workspace?.name||'Workspace':displayTitle;
 return <header ref={header} className="a-work-header" data-part="header" inert={!!state.view?.panel} {...fileDropHandlers}>
  <button type="button" className="a-brand a-work-brand" aria-label="Amplifier Home" title="Amplifier Home" onClick={()=>nav.home({navExpanded:false})}><img src="/branding/icons/amplifier-icon-128.png" alt=""/><span>Amplifier</span></button>
  <button id="navigation-toggle" type="button" className="a-icon a-work-nav-toggle" aria-label={navOpen?'Close navigation':'Open navigation'} aria-expanded={navOpen} aria-controls="workspace-navigation" data-action="view.update" onClick={()=>act('view.update',{patch:{navExpanded:!navOpen,...(!navigationNarrow?{navPinned:!navOpen}:{}),toolbarMenuOpen:false}})}><PanelLeft/>{!navOpen&&<AttentionBadge state={state}/>}</button>
  <nav className="a-site-navigation" aria-label="Site navigation">
   <button type="button" className="a-icon a-work-home-nav" aria-label="Home" title="Home" aria-current={surface==='home'?'page':undefined} onClick={()=>nav.home({navExpanded:false})}><HomeIcon/></button>
   <DestinationHistory history={nav.history} onVisit={nav.visitHistory} onRecent={nav.visitRecent} disabled={!!state.navigationPending}/>
  </nav>
  <ShellSlot name="conversation.header">{surface!=='home'&&<nav className="a-work-heading" aria-label="Breadcrumb">{surface==='chat'&&workspace&&session?.location?.kind!=='managed'&&<><button type="button" className="a-work-crumb" onClick={()=>nav.browse('workspace',workspace.id)}><Folder/>{workspace.name}</button><ChevronRight aria-hidden="true"/></>}<strong title={title} aria-current="page" {...(surface==='chat'&&session?{role:'heading','aria-level':1,'data-part':'session-heading','data-session-id':session.id,tabIndex:-1}:{})}>{title}</strong></nav>}</ShellSlot>
  <div className="a-work-header-actions">
   {!browsing&&session&&<><UIUseShortcut sessionId={session.id} act={act}/><ActionMenu label="Chat" icon={<MessageCircle/>}>
    <button onClick={()=>open('chat-rename')}><Pencil/>Rename</button>
    <button data-action="session.pin" onClick={()=>act('session.pin',{id:session.id,pinned:!(state.pinnedSessionIds||[]).includes(session.id)})}><Pin/>{(state.pinnedSessionIds||[]).includes(session.id)?'Unpin':'Pin'}</button>
    <button onClick={()=>open('chat-share')}><Share2/>Share or save</button>
    <button onClick={()=>open('session-details')}><Info/>Chat info</button>
    <button onClick={()=>open('chat-help')}><Bug/>Help &amp; diagnostics</button>
    <button onClick={()=>open('chat-advanced')}><Settings/>Advanced<ChevronRight/></button>
    <hr/>
    <ArchiveButton chat={session} archived={Object.hasOwn(state.conversationOrganization?.archived||{},session.id)}/>
    {session.location?.kind==='managed'&&isTopLevelChat(session)&&<button className="a-danger" onClick={()=>open('delete-session')}><Trash2/>Delete</button>}
   </ActionMenu><CanvasToggle state={state} act={act} layout={presentation.layout}/></>}
  </div>
 </header>;
}
export function WorkspaceFiles({workspace,state,act}){
 const [path,setPath]=useState(workspace.path),[error,setError]=useState(''),[loading,setLoading]=useState(false),[copied,setCopied]=useState('');
 const [opening,setOpening]=useState(null),pending=useRef(false),navigation=useRef(null);
 // Ignore notices from an obsolete visit. File opening and destination
 // presentation are one host action, never a navigation after its receipt.
 useLayoutEffect(()=>{const visit={};navigation.current=visit;setError('');return()=>{navigation.current=null}},[state.selectedSessionId,state.selectedWorkspaceId,state.navigationPending,state.view?.workSurface,state.view?.workWorkspaceId,state.view?.workWorkspaceTab,state.view?.panel,workspace.id,path]);
 const controlId='workspace-files-'+workspace.id,listing=state.locationListing?.controlId===controlId?state.locationListing:null;
 const status=state.actionStatus?.['locations.list'],operation=status?.target?.controlId===controlId?status:null;
 const busy=loading||['queued','working'].includes(operation?.phase),failure=error||(operation?.phase==='error'?operation.error:'');
 useEffect(()=>{let live=true;setLoading(true);setError('');Promise.resolve(act('locations.list',{controlId,path,directoriesOnly:false})).then(result=>{if(result?.accepted===false&&live)setError(result.error||'Could not list files.')}).catch(e=>{if(live)setError(e.message)}).finally(()=>{if(live)setLoading(false)});return()=>{live=false}},[path,workspace.id]);
 const selected=state.sessions?.find(row=>row.id===state.selectedSessionId),canOpen=selected?.workspace===workspace.path;
 const title=selected?.title||'Untitled chat',duplicate=canOpen&&state.sessions.some(row=>row.id!==selected.id&&row.workspace===workspace.path&&(row.title||'Untitled chat')===title);
 const destination=`Open in “${title}”${duplicate?' · '+selected.id:''}`;
 const browseFolder=next=>{if(!pending.current)setPath(next)};
 async function openFile(file){
  if(!canOpen||state.navigationPending||pending.current)return;
  const visit=navigation.current,target={sessionId:selected.id,workspace:workspace.path,path:file.path,showConversation:true};
  pending.current=true;setOpening(file.path);setError('');
  try{
   const result=await act('canvas.openFile',target);
   if(navigation.current!==visit)return;
   if(result?.accepted!==true||result.result?.status!=='opened')setError(result?.result?.message||result?.error||'This file could not be opened.');
  }catch(error){if(navigation.current===visit)setError(error.message||'This file could not be opened.')}
  finally{pending.current=false;if(navigation.current)setOpening(null)}
 }
 return <section className="a-work-files" aria-label="Workspace files"><p className="a-caption">Files on {state.workspaceDefaults?.hostLabel||'this host'}. Viewing this list does not add files to a message.</p><div className="a-work-file-path"><button className="a-icon" type="button" disabled={path===workspace.path||!!opening} aria-label="Parent folder" onClick={()=>browseFolder(listing?.parent||workspace.path)}><ArrowLeft/></button><code>{path}</code></div>{busy&&<p role="status">Loading files…</p>}{failure&&<p role="alert">{failure}</p>}{listing?.path===path&&listing.entries.map(row=><div className="a-work-file-row" key={row.path}>{row.directory?<Folder/>:<FileText/>}<button type="button" disabled={!!opening||!row.directory&&(!canOpen||state.navigationPending)} data-action={row.directory?undefined:'canvas.openFile'} aria-busy={opening===row.path||undefined} onClick={()=>row.directory?browseFolder(row.path):openFile(row)}><span>{row.name}</span>{!row.directory&&canOpen&&<small>{opening===row.path?'Opening…':destination}</small>}</button><button type="button" className="a-icon" aria-label={'Copy path for '+row.name} onClick={()=>navigator.clipboard.writeText(row.path).then(()=>setCopied(row.name),()=>setError('Clipboard unavailable.'))}><Copy/></button></div>)}{listing?.truncated&&<p>Showing the first 300 entries. Browse a subfolder to see more.</p>}{!canOpen&&<p className="a-caption">Open a chat in this workspace to preview a file beside it.</p>}{copied&&<p role="status">Path copied: {copied}</p>}</section>;
}
function WorkBrowser({host,workspaceHost,state,act,dispatch,presentation,resourceSlot}){
 const model=useNavigationController(host,'chats'),workspaceModel=useNavigationController(workspaceHost||host,'workspaces'),now=useActivityClock(),nav=useContext(WorkNavigationContext);
 const surface=workSurface(state),workspace=state.workspaces?.find(row=>row.id===state.view.workWorkspaceId)||workspaceModel.workspace,tab=state.view.workWorkspaceTab||'chats',active=!['chat','home'].includes(surface);
 const identity=surface+(surface==='workspace'?':'+state.view.workWorkspaceId:'');
 const [mode,setMode]=useLandingLayout(identity,presentation);
 const workspacePending=surface==='workspace'&&(workspaceModel.state.navigationWorkspacePending||model.state.navigationWorkspacePending);
 const setTab=tab=>act('view.update',{patch:{workWorkspaceTab:tab}});
 const view=surface==='chats'?{...model.view,...model.state.sidebarNavigation?.recentView,...model.view.navRecentView,navChatScope:'all'}:{...model.view,navChatScope:'workspace'};
 const page=surface==='chats'||surface==='workspace'&&tab==='chats'?chatPage({...model.state,view},model.workspace,{section:surface==='chats'?'search':'workspace'}):null;
 const pageResource=surface==='chats'?'sidebarSearch':'sidebarWorkspace';
 const viewAct=(name,args)=>{if(name!=='view.update'||surface!=='chats')return model.act(name,args);const next={...model.state.sidebarNavigation?.recentView,...model.view.navRecentView,...args.patch};if(!Object.hasOwn(args.patch,'navChatPage'))delete next.navChatPage;return model.act(name,{patch:{navRecentView:next}})};
 const title=surface==='workspace'?workspacePending?'Loading workspace…':workspace?.name||'Workspace unavailable':surface==='workspaces'?'All workspaces':'All chats';
 const description=surface==='workspace'?'Your chats, files, and related work in one place.':surface==='workspaces'?'Find a workspace, browse its folders, or start something new.':'Find a conversation or see what needs your attention.';
 const readingKey=JSON.stringify([mode,tab,view.navFilter,view.navStatusFilter,view.navArchive,view.navSort,view.navGroup,view.navAge,view.navLocationFilter,view.navShowAgentCreated,page?.index,workspaceModel.state.workspaceExplorer?.page,workspaceModel.state.workspaceExplorer?.filter,workspaceModel.state.workspaceExplorer?.mode,workspaceModel.state.workspaceExplorer?.archive,workspaceModel.state.workspaceExplorer?.path]);
 return <WorkLandingFrame mode={mode} presentation={presentation} identity={identity} readingKey={readingKey} ready={!page?.pending&&!workspacePending&&(surface!=='workspaces'||hasNavigationResource(workspaceModel.state,'workspaceExplorer'))} title={title} description={description}
  toolbar={<LandingToolbar mode={mode} onMode={setMode} presentation={presentation} actions={<button type="button" className="a-home-create a-soft" aria-label={surface==='workspaces'?'New workspace':'New chat'} disabled={surface==='workspace'&&(workspacePending||!workspace)} onClick={()=>surface==='workspaces'?nav.create():nav.newChat(surface==='workspace'?workspace?.path:undefined)}><Plus/><span>{surface==='workspaces'?'New workspace':'New chat'}</span></button>}/>}
 ><NavigationEditor model={model} deleteOwner={active}/><NavigationEditor model={workspaceModel}/>
 <WorkspaceResourcesSlot connect={resourceSlot} workspace={surface==='workspace'&&tab==='details'&&!workspacePending&&workspace?.available!==false?workspace:undefined} unavailableId={surface==='workspace'&&tab==='details'&&!workspacePending&&(!workspace||workspace.available===false)?state.view.workWorkspaceId:undefined}/>{active&&<>
  {surface==='workspace'&&workspace&&tab==='details'&&<WorkspaceBundleDefault key={workspace.id} workspace={workspace} state={state} act={act}/>}
  {surface==='workspaces'?<><NavigationResourceStatus state={workspaceModel.state} resource="workspaceExplorer" label="workspaces" refresh={workspaceModel.refreshResources}/>{hasNavigationResource(workspaceModel.state,'workspaceExplorer')?<WorkspaceExplorer state={workspaceModel.state} act={workspaceModel.act} onEdit={workspaceModel.setDraft} heading={false} layout={mode} identity={identity} onSelect={row=>nav.browse('workspace',row.workspaceId)}/>:!workspaceModel.state.navigationResourcePending?.workspaceExplorer&&<p role="status">Loading workspaces…</p>}</>:<>
   {surface==='workspace'&&workspace&&!workspacePending&&tab==='details'&&<WorkspaceWork key={workspace.id} workspace={workspace} session={state.sessions?.find(row=>row.id===state.selectedSessionId)} dispatch={dispatch} onOpenChat={id=>dispatch('session.select',{id})}/>}
   {surface==='workspace'&&workspace&&tab==='details'&&<WorkspaceReadiness state={state} act={act} workspaceId={workspace.id}/>}
   {surface==='workspace'&&workspace&&<><div className="a-work-tabs" role="group" aria-label="Workspace view">{['chats','work','files','details'].map(key=><button key={key} type="button" aria-pressed={tab===key} data-action="view.update" onClick={()=>setTab(key)}>{key[0].toUpperCase()+key.slice(1)}</button>)}</div>{tab==='details'&&<div className="a-work-details"><CopyDetail label="Folder" value={workspace.path}/><p>Chats are discovered from native Amplifier history for this folder. Files stay in place and are visible from your terminal.</p><button className="a-soft" onClick={()=>workspaceModel.setDraft({mode:'rename',id:workspace.id,name:workspace.name})}>Rename workspace</button><WorkspaceCleanup key={workspace.id} id={workspace.id} act={act}/><WorkspaceConfiguration key={workspace.id} workspace={workspace} state={state} act={act}/></div>}{tab==='files'&&<WorkspaceFiles key={workspace.id} workspace={workspace} state={state} act={act}/>}
    {tab==='work'&&!workspacePending&&<SharedWorkBrowser workspace={workspace} clientId={state.client?.id} hostInstanceId={state.client?.hostInstanceId} dispatch={dispatch} onOpenChat={id=>dispatch('session.select',{id})}/>}</>}

   {page&&<NavigationResourceStatus state={model.state} resource={pageResource} label={surface==='chats'?'chat search':'workspace chats'} refresh={model.refreshResources}/>}
   {surface==='workspace'&&workspacePending?<p role="status">Loading workspace…</p>:surface==='workspace'&&!workspace?<p>This workspace is no longer available. Choose another workspace from the sidebar.</p>:surface==='chats'?<ChatSearch page={page} model={model} view={view} viewAct={viewAct} now={now} layout={mode} identity={identity}/>:tab==='chats'&&<ChatList layout={mode} identity={identity} planAct={surface==='workspace'?act:undefined} page={page} model={model} view={view} viewAct={viewAct} now={now} showLocation={surface==='chats'}/>}
  </>}
 </>}</WorkLandingFrame>;
}
export function WorkSurface({shell,state,act,dispatch,navigationRef}){
 const chats=shell.composition.instances.find(row=>row.package==='builtin.chats'),workspaces=shell.composition.instances.find(row=>row.package==='builtin.workspaces');
 if(!chats)return <div className="a-work-browser"><p>Your custom navigation is available in the sidebar. This browser uses the standard Chats component.</p><a href="?shell=recovery">Open with standard navigation</a></div>;
 return <WorkspaceSettingsBoundary state={state} act={act} dispatch={dispatch} navigationRef={navigationRef}>{visible=><WorkspaceResourcesOwner key={visible.client?.id||'local'} state={visible} act={act}>{resourceSlot=><div className="a-work-browser-host" hidden={workSurface(visible)==='chat'}>{workSurface(visible)==='home'?<AdaptiveWorkHome host={shell.hostFor(chats)} state={visible} act={act} presentation={shell.composition.presentation}/>:<WorkBrowser key={workSurface(visible)+':'+(visible.view.workWorkspaceId||'')} presentation={shell.composition.presentation} host={shell.hostFor(chats)} workspaceHost={workspaces?shell.hostFor(workspaces):undefined} state={visible} act={act} dispatch={dispatch} resourceSlot={resourceSlot}/>}</div>}</WorkspaceResourcesOwner>}</WorkspaceSettingsBoundary>;
}

// Live controls stay outside the conversation, which is hidden during browsing.
export function LiveChatActivity({voice={},callSession,callActive,act,children}){
 const callWorking=sessionStatus(callSession).busy||(callSession?.workers||[]).some(worker=>['queued','starting','working','running','stopping'].includes(worker.status));
 const callUnavailable=callSession?.workspaceAvailable===false||!!callSession?.historyReadOnlyReason||ownershipState(callSession).blocked;
 return <div className="a-work-live-controls">
  {callActive&&<div className="a-call-strip" data-part="voice"><AudioLines/><span>{voice.model||'Voice'}{voice.voice?' · '+voice.voice:''} · {voice.status}</span><button className="a-icon" aria-label={voice.muted?'Unmute microphone':'Mute microphone'} title={voice.muted?'Unmute microphone':'Mute microphone; replies and work continue'} data-action="call.mute" disabled={voice.status==='ending'} onClick={()=>act('call.mute',{muted:!voice.muted})}>{voice.muted?<MicOff/>:<Mic/>}</button><button className="a-soft" title="End audio; work continues" data-action="call.end" disabled={voice.status==='ending'} onClick={()=>act('call.end')}>End call</button>{callWorking&&callSession&&<button type="button" className="a-soft" data-action="conversation.stop" title={'Stop work in '+(callSession.title||'this call’s conversation')+'; keep the call connected'} disabled={callSession.status==='stopping'||callUnavailable} onClick={()=>act('conversation.stop',{sessionId:callSession.id})}>{callSession.status==='stopping'?'Stopping work…':'Stop work'}</button>}{voice.controlStatus==='reconnecting'&&<small role="status">Reconnecting to Amplifier. Mute and End call remain available.</small>}{voice.mediaStatus==='reconnecting'&&<small role="status">Voice audio is reconnecting.</small>}{voice.muted&&<small role="status">Microphone muted. Replies and work continue.</small>}<button className="a-link" data-action="call.keepAwake" aria-pressed={voice.keepAwake!==false} onClick={()=>act('call.keepAwake',{enabled:voice.keepAwake===false})}>{voice.wakeLock==='active'?'Screen staying awake':'Keep screen awake'}</button>{voice.keepAwake!==false&&['unsupported','unavailable','released'].includes(voice.wakeLock)&&<small role="status">Screen may lock. Keep this page visible; battery-saving settings can prevent staying awake.</small>}{voice.audioSessionState==='interrupted'&&<small role="status">Call audio interrupted by your device. Waiting for audio to become available.</small>}{voice.microphoneEnded?<small role="status">Microphone stopped. End this call and start again.</small>:voice.microphoneInterrupted&&<small role="status">Microphone temporarily unavailable.</small>}{voice.playbackBlocked&&<small role="status">Voice playback paused. <button type="button" className="a-link" data-action="call.resumeAudio" onClick={()=>act('call.resumeAudio')}>Resume audio</button></small>}{voice.fallbackReason&&<small>{voice.fallbackReason}</small>}</div>}
  {children}
 </div>;
}
