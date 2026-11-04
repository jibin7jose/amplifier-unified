import {conversationHistoryPending} from './history-window.js';
import {OutputSettings} from './outputs';
import {PublishingSettings} from './publishing';
import {ChatOverview} from './work-overview';
import {PermissionHistory,approvalGroups} from './approval-card';
import {NavigationOpen,useModalFocus,navigationExpanded} from './responsive-navigation';
import {CanvasControlsHost,CanvasPrimaryActionsHost,CanvasDocumentRefresh,CanvasVersionLoadProvider} from './canvas-controls';
import {CanvasOverviewSection,useCanvasReading,useCanvasOverviewState} from './canvas-overview-state';
import {CanvasWorkspace} from './canvas-workspace';
import {McpAppVisibilityProvider} from './mcp-app-lifecycle';
import {ShellModules,ShellSlot} from './shell/runtime';
export {ChatRename} from './shell/navigation-components';
import {CanvasTabs,SavedArtifacts,BrowserAddress,chatArtifacts} from './canvas-library';
import React,{useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {WorkNavigationContext} from './work-navigation';
import {Home as HomeIcon,Info,SlidersHorizontal,MoreHorizontal,FolderOpen,FolderPlus,MessageCircle,Plus,PanelLeft,PanelRight,Search,Pencil,Trash2,X,Check,FileText,ChevronRight,Library,Globe,Maximize2,Minimize2,ArrowLeft,Pin,RefreshCw,LoaderCircle,AlertCircle} from 'lucide-react';
import {PaneResizer,usePanelLayout,showingOverview} from './panel-layout';
import {chatPage,visibleWorkspaces} from './chat-navigation';
import {WorkspaceExplorer} from './workspace-explorer';
import {AttentionBadge} from './attention';
import {PathField} from './settings-ui';
import {ChatShortcutScope} from './chat-shortcuts';

const patch=(act,value)=>act('view.update',{patch:value});
export function reopenCanvas(state,act){
 return act('canvas.visibility',{open:true});
}
export function CanvasToggle({state,act,layout}){
 const Icon=(layout||state.view?.layout)==='work'?PanelLeft:PanelRight,canvasOpen=!!state.selectedSessionId&&!!state.canvas?.open;
 return <button id="canvas-toggle" type="button" className="a-icon a-canvas-toggle" disabled={!state.selectedSessionId} title={!state.selectedSessionId?'Send a message to start a chat before opening Canvas':undefined} aria-label={canvasOpen?'Hide canvas':'Open canvas'} aria-expanded={canvasOpen} aria-pressed={canvasOpen} aria-controls="workspace-canvas" data-action="canvas.visibility" onClick={()=>act('canvas.visibility',{open:!state.canvas?.open})}><Icon/></button>;
}
export function WorkspaceRail({state,session,act,selectSession,newSession,shell,footer}){
 const layout=usePanelLayout(state,act);
 const navigation=useContext(WorkNavigationContext),supportsStatus=shell.composition.instances.some(row=>row.package==='builtin.chats');
 const view=state.view||{},pinned=!layout.narrow&&!!view.navPinned,expanded=navigationExpanded(view,layout.narrow),draft=view.workspaceDraft||{};
 const nav=useRef(null),wasNarrow=useRef(layout.narrow),close=()=>patch(act,{navExpanded:false});
 useEffect(()=>{if(layout.narrow&&!wasNarrow.current&&view.navExpanded)close();wasNarrow.current=layout.narrow},[layout.narrow]);
 useModalFocus(nav,layout.narrow&&expanded,close,'#navigation-toggle');
 const workspaces=visibleWorkspaces(state),workspace=workspaces.find(w=>w.id===state.selectedWorkspaceId);
 const add=()=>{(newSession||(()=>act('session.draft',{})))();if(!pinned)patch(act,{navExpanded:false})};
 return <NavigationOpen.Provider value={expanded}><ChatShortcutScope navigation={nav} enabled={expanded&&!view.panel&&!view.toolbarMenuOpen&&!draft.mode&&!view.canvasFocused} scope={[state.selectedSessionId,view.workSurface,view.workWorkspaceId,view.navChatScope].join(':')}>{layout.narrow&&expanded&&<div className="a-navigation-scrim" onClick={close}/>}
 <aside ref={nav} id="workspace-navigation" role={layout.narrow?'dialog':undefined} aria-modal={layout.narrow&&expanded||undefined} aria-owns={layout.narrow&&expanded?'navigation-toggle':undefined} hidden={!expanded} inert={!expanded} className={`a-nav-slot ${pinned?'is-pinned':''} ${expanded?'is-expanded':''}`} data-docked={layout.docked} data-part="navigation" aria-label="Workspaces and chats" onClick={e=>{if(layout.narrow&&e.target.closest('[data-navigation-select],[data-action="session.select"]'))close()}}>
  <div className="a-nav-rail">
   <div className="a-nav-head a-sidebar-modes">{navigation&&<button type="button" className="a-sidebar-home a-icon" aria-label="Home" title="Home" aria-current={view.workSurface==='home'?'page':undefined} onClick={()=>navigation.home(layout.narrow?{navExpanded:false}:{})}><HomeIcon/></button>}<div className="a-sidebar-organizations" role="group" aria-label="Sidebar view">{supportsStatus?['browse','status'].map(mode=><button type="button" key={mode} aria-pressed={(view.navMode||'browse')===mode} onClick={()=>patch(act,{navMode:mode})}>{mode==='browse'?'Browse':'Status'}</button>):<strong>Browse</strong>}</div></div>
   <button className="a-nav-main" type="button" onClick={add} data-action="session.draft" aria-label="New chat" title="New chat"><Plus/><span className="a-nav-reveal">New chat</span></button>
   {navigation&&<button type="button" className="a-nav-search-launch" data-action="view.update" onClick={()=>navigation.browse('chats')}><Search/>Search chats</button>}
   <div className="a-nav-content a-nav-reveal"><ShellModules shell={shell}/></div>{footer}
  </div>
  {layout.docked&&<PaneResizer layout={layout} pane="nav"/>}
 </aside></ChatShortcutScope></NavigationOpen.Provider>;
}

export function SessionHistoryControls({session,messages=session?.messages,act,onLoadEarlier,hideEarlier=false}){
 if(!session)return null;
 const unavailable=session.workspaceAvailable===false,readOnly=session.historyReadOnlyReason;
 const pending=conversationHistoryPending(session,false,messages),earlier=!hideEarlier&&Number(session.sharedHistoryOffset)>0;
 const notices=session.historyActivity?.diagnostics||[],partial=notices.some(item=>['activity_scan_limit','scan_limit','invalid_event','incomplete_event','unreadable_file'].includes(item.code)),recovered=notices.some(item=>item.code==='recovered_backup');
 const retry=()=>act('session.select',{id:session.id});
 const loadEarlier=()=>{onLoadEarlier?.();return act('session.history',{id:session.id,before:session.sharedHistoryOffset,limit:100})};
 // A refresh with retained messages has no loading notice. Even an empty flex
 // child adds a gap and can disturb the independent reading-position owner.
 if(!(pending&&!messages?.length)&&!session.historyError&&!earlier&&!unavailable&&!readOnly&&!partial&&!recovered)return null;
 return <div className="a-session-history" data-part="session-history">
  {(unavailable||readOnly)&&<p role="status"><FolderOpen/>{readOnly||'This workspace folder is unavailable. You can read its saved chats here.'}</p>}
  {recovered&&<p role="status"><AlertCircle/>Showing a recovered history backup. Original files are unchanged.</p>}
  {partial&&<p role="status"><AlertCircle/>Some saved activity is unavailable or outside the loaded window. Conversation text comes from the saved transcript.</p>}
  {pending&&!messages?.length&&<p role="status"><LoaderCircle className="a-progress-spinner"/>Loading conversation…</p>}
  {session.historyError&&<div className="a-session-history-error" role="alert"><AlertCircle/><span>{session.historyError}</span><button type="button" className="a-link" data-action="session.select" disabled={pending} onClick={retry}>Try again</button></div>}
  {earlier&&<button type="button" className="a-soft" data-action="session.history" disabled={pending} onClick={loadEarlier}>{pending?'Loading earlier messages…':'Load earlier messages'}</button>}
 </div>;
}

export {A2UISurface} from './a2ui';
export function AgentCanvas({state,act,dispatch=act,suppressed=false,settingsSurface,permissionHistoryCurrent=()=>false}){
 const settings=!!state.view?.settingsCanvas;
 const canvas=state.canvas||{},view=state.view||{},draft=view.canvasDraft||{},layout=usePanelLayout(state,act),panel=useRef(null),actRef=useRef(act);
 actRef.current=act;
 const [controlsHost,setControlsHost]=useState(null),[actionsHost,setActionsHost]=useState(null),overviewRef=useRef(null);
 const contentRef=useRef(null),readerRef=useRef(null),inspectorRef=useRef(null),controlsInvoker=useRef(null),controlsFocus=useRef(null),controlsRestore=useRef(null);
 const [detailsOverlay,setDetailsOverlay]=useState(true);
 const [overviewDraft,setOverviewDraft]=useCanvasOverviewState(state),overviewState={...state,view:{...view,canvasDraft:overviewDraft}};
 const sectionChange=next=>setOverviewDraft({sections:next.sections});
 const artifacts=chatArtifacts(state),hasContent=!!state.canvasWorkspace?.views?.length&&!canvas.placeholder,hasArtifacts=artifacts.length>0;
 useEffect(()=>{
  if(!canvas.open&&panel.current?.contains(document.activeElement))document.querySelector('.a-canvas-toggle')?.focus({preventScroll:true});
 },[canvas.open]);
 const mounted=useRef(false);
 if(canvas.open)mounted.current=true;
 const focused=!suppressed&&!!state.selectedSessionId&&!!canvas.open&&!!view.canvasFocused,controls=!!view.canvasControlsExpanded;
 const resourceView=state.canvasWorkspace?.views?.find(item=>item.viewId==='primary');
 const changeDraft=value=>patch(act,{canvasDraft:{...draft,...value},...((value.open||value.browser)?{canvasControlsExpanded:true}:{})});
 const overview=showingOverview(state);
 const visible=!suppressed&&!!state.selectedSessionId&&!!canvas.open,covered=visible&&controls&&detailsOverlay;
 // Measure the Canvas, not the window: a narrow split pane also needs the sheet.
 // The observed content box is independent of the inspector's own width.
 useLayoutEffect(()=>{
  const node=contentRef.current;if(!node||!visible)return;
  const measure=()=>{
   const narrow=(panel.current||node).getBoundingClientRect().width<720;
   if(narrow&&readerRef.current?.contains(document.activeElement)){controlsInvoker.current=document.activeElement;controlsFocus.current='close'}
   setDetailsOverlay(narrow);
  };
  measure();const observer=new ResizeObserver(measure);observer.observe(node);return()=>observer.disconnect();
 },[visible]);
 useEffect(()=>{
  const supersede=()=>{controlsFocus.current=null;controlsRestore.current=null};
  document.addEventListener('keydown',supersede,true);document.addEventListener('pointerdown',supersede,true);
  return()=>{document.removeEventListener('keydown',supersede,true);document.removeEventListener('pointerdown',supersede,true)};
 },[]);
 const restoreControlsFocus=()=>{
  const target=controlsInvoker.current;
  if(target?.isConnected&&target.getClientRects().length&&!target.closest('[inert],[hidden]'))target.focus({preventScroll:true});
  else panel.current?.querySelector(overview?'[aria-label="Chat overview"]':'[aria-label="Canvas details"]')?.focus({preventScroll:true});
 };
 const closeControls=()=>{
  controlsFocus.current=null;
  // Restore only after the reader is no longer inert. New input clears this
  // intent; a late receipt must not steal navigation or header-toggle focus.
  controlsRestore.current=inspectorRef.current?.contains(document.activeElement)?state.selectedSessionId:null;
  return patch(act,{canvasControlsExpanded:false});
 };
 const openControls=(target='close')=>{
  controlsInvoker.current=document.activeElement;
  if(controls){inspectorRef.current?.querySelector(target==='version'?'[aria-label="Artifact version"]':'[aria-label="Close canvas details"]')?.focus({preventScroll:true});return}
  controlsFocus.current=target;return patch(act,{canvasControlsExpanded:true});
 };
 useLayoutEffect(()=>{
  if(!visible||!controls){
   controlsFocus.current=null;
   if(visible&&controlsRestore.current===state.selectedSessionId)restoreControlsFocus();
   controlsRestore.current=null;return;
  }
  const target=controlsFocus.current;controlsFocus.current=null;
  // A resize can cover a previously focused reader. Move to the same stable
  // inspector, leaving the header toggle and document navigation available.
  if(target||covered&&readerRef.current?.contains(document.activeElement)){
   if(!target)controlsInvoker.current=document.activeElement;
   inspectorRef.current?.querySelector(target==='version'?'[aria-label="Artifact version"]':'[aria-label="Close canvas details"]')?.focus({preventScroll:true});
  }
 },[controls,covered,visible,state.selectedSessionId]);
 useEffect(()=>{controlsInvoker.current=null;controlsFocus.current=null;controlsRestore.current=null},[state.selectedSessionId]);
 useCanvasReading(overviewRef,state.selectedSessionId,'overview',!!canvas.open&&overview&&!suppressed);
 // The focused scope includes the external header toggle. Reuse its existing
 // Escape owner so Details closes first there too, without a global listener.
 useModalFocus(panel,focused,()=>controls?closeControls():patch(actRef.current,{canvasFocused:false}),'#canvas-toggle');
 useEffect(()=>{if(focused)panel.current?.querySelector('[aria-label="Exit canvas focus"]')?.focus({preventScroll:true})},[focused]);
 useEffect(()=>{
  if(!visible||!overview||!draft.library||!['outputs','publishing'].includes(draft.delivery))return;
  const id=draft.delivery==='outputs'?'review':draft.delivery;
  setOverviewDraft({sections:{...overviewDraft.sections,[id]:true}});
  // Consume the existing navigation target only after its visible destination
  // has been reached. Clearing it earlier cancels this frame via optimistic state.
  const timer=requestAnimationFrame(()=>{const section=panel.current?.querySelector(`[data-overview-section="${id}"]`);section?.scrollIntoView({block:'nearest',behavior:'instant'});section?.querySelector('summary')?.focus({preventScroll:true});patch(actRef.current,{canvasDraft:{...draft,delivery:null}})});
  return()=>cancelAnimationFrame(timer);
 },[visible,overview,state.selectedSessionId,draft.library,draft.delivery]);
 useEffect(()=>{
  if(!visible||!overview||draft.delivery!=='permissions')return;
  const key=draft.permissionHistoryKey;
  setOverviewDraft({sections:{...overviewDraft.sections,permissions:true}});
  // Clear only the still-current target after document commands settle. The
  // dispatch owner derives its draft then; never send this render's stale copy.
  dispatch('view.update',{sessionId:state.selectedSessionId,patch:{}},{consumePermissionHistory:key}).then(receipt=>{
   if(!receipt.accepted)return;
   requestAnimationFrame(()=>{
    if(!permissionHistoryCurrent(key))return;
    const section=panel.current?.querySelector('[data-overview-section="permissions"]');
    if(!section||section.closest('[hidden],[inert]'))return;
    section.scrollIntoView({block:'nearest',behavior:'instant'});
    section.querySelector('summary')?.focus({preventScroll:true});
   });
  }).catch(()=>{});
 },[visible,overview,state.selectedSessionId,draft.delivery,draft.permissionHistoryKey]);
 if(!mounted.current)return null;
 const latestEvent=canvas.events?.at(-1);
 return <CanvasVersionLoadProvider view={resourceView}><CanvasPrimaryActionsHost.Provider value={actionsHost}><CanvasControlsHost.Provider value={controlsHost}><aside ref={panel} id="workspace-canvas" className="a-canvas-panel" hidden={suppressed||!canvas.open||!state.selectedSessionId} inert={suppressed||!canvas.open||!state.selectedSessionId} aria-hidden={suppressed||!canvas.open||!state.selectedSessionId||undefined} data-part="canvas" data-mode={overview?'overview':'artifact'} data-focused={focused} data-controls={controls} data-pinned={!!view.canvasControlsPinned} aria-label="Canvas" role={focused?'dialog':undefined} aria-modal={focused||undefined} aria-owns={focused?'canvas-toggle':undefined} onKeyDown={event=>{
  if(controls&&event.key==='Escape'&&!event.defaultPrevented&&!event.target.closest('dialog[open]')){event.preventDefault();closeControls()}
 }}>

  {!focused&&!layout.overlay&&<PaneResizer layout={layout} pane="canvas"/>}
  <div className="a-canvas-chrome">
   <header className="a-canvas-head"><CanvasTabs state={state} act={act} dispatch={dispatch} overview={overview} onOverview={()=>patch(act,{settingsCanvas:false,canvasControlsExpanded:false,canvasDraft:{...draft,library:true,open:false,browser:false}})}/>
    <button type="button" className="a-icon" aria-label={focused?'Exit canvas focus':'Focus canvas'} aria-pressed={focused} data-action="view.update" onClick={()=>patch(act,{canvasFocused:!focused})}>{focused?<Minimize2/>:<Maximize2/>}</button>
    <button type="button" className="a-soft a-canvas-dismiss" aria-label="Close canvas panel" title="Return to chat — keeps open documents and edits" data-action="canvas.visibility" onClick={()=>act('canvas.visibility',{open:false})}><X aria-hidden="true"/><span>Close Canvas</span></button>
   </header>
  </div>
  <div ref={contentRef} className="a-canvas-content" data-details-overlay={detailsOverlay}>
   <div ref={readerRef} className="a-canvas-reader" inert={covered} aria-hidden={covered||undefined}>
   <div className="a-canvas-artifact-toolbar" hidden={overview||!hasContent}>
    <strong title={canvas.title}>{canvas.title}</strong>
    {resourceView?.versions?.length>0&&<button type="button" className="a-canvas-version-shortcut a-soft" aria-label="Choose artifact version" aria-expanded={controls} aria-controls="canvas-options" onClick={()=>openControls('version')}>v{resourceView.runningVersion??resourceView.selectedVersion??resourceView.latestVersion}{resourceView.selectedVersion!=null?' · Saved':''}</button>}
    {resourceView?.versions?.length>0&&!canvas.app&&!['settings','browser','mcp-app'].includes(canvas.kind)&&<CanvasDocumentRefresh key={state.selectedSessionId+':'+resourceView.resourceId+':'+(resourceView.selectedVersion??'latest')} view={resourceView} dispatch={dispatch}/>}
    <div ref={setActionsHost} className="a-canvas-primary-actions"/>
    <button type="button" className="a-soft a-canvas-details" aria-label="Canvas details" aria-expanded={controls} aria-controls="canvas-options" onClick={()=>controls?closeControls():openControls()}><Info/><span>Details</span></button>
   </div>
  <div className="a-canvas-body">
   {settings&&<div className="a-settings-canvas">{settingsSurface}</div>}
   <div key={state.selectedSessionId??'draft'} hidden={settings} className="a-canvas-session-body">
   {canvas.kind!=='settings'&&!!state.canvasWorkspace?.views?.length&&<McpAppVisibilityProvider visible={!suppressed&&!!canvas.open&&!draft.library&&!settings}><CanvasWorkspace state={state} dispatch={dispatch} hidden={!!draft.library}/></McpAppVisibilityProvider>}
   <div ref={overviewRef} className="a-canvas-overview-page" role="region" aria-label="Chat overview" tabIndex={0} hidden={!overview} inert={!overview}>
    <ChatOverview state={overviewState} act={act} changeDraft={changeDraft} onSectionChange={sectionChange} onFilter={filter=>setOverviewDraft({filter})}/>
    <div className="a-chat-overview"><CanvasOverviewSection id="permissions" title="Permissions history" summary={approvalGroups(state.sessions?.find(row=>row.id===state.selectedSessionId)).history.length} draft={overviewDraft} onChange={sectionChange} defaultOpen={false} lazy><PermissionHistory session={state.sessions?.find(row=>row.id===state.selectedSessionId)}/></CanvasOverviewSection></div>
    <section className="a-canvas-delivery" aria-label="Output actions">
     <CanvasOverviewSection id="review" title="Outputs & review" draft={overviewDraft} onChange={sectionChange} defaultOpen={draft.delivery==='outputs'} lazy><OutputSettings session={state.sessions?.find(row=>row.id===state.selectedSessionId)} state={state} act={act}/></CanvasOverviewSection>
     <CanvasOverviewSection id="publishing" title="Publishing" draft={overviewDraft} onChange={sectionChange} defaultOpen={draft.delivery==='publishing'} lazy><PublishingSettings session={state.sessions?.find(row=>row.id===state.selectedSessionId)} state={state} act={dispatch}/></CanvasOverviewSection>
    </section>
   </div>

  </div>
  </div>
   </div>
   <div className="a-canvas-inspector-backdrop" hidden={!covered} aria-hidden="true" onClick={closeControls}/>
   <aside ref={inspectorRef} id="canvas-options" className="a-canvas-controls a-canvas-inspector" role="region" aria-labelledby="canvas-details-title" hidden={!controls} inert={!controls}>
    <header className="a-canvas-inspector-head"><h2 id="canvas-details-title">Canvas details</h2><button type="button" className="a-icon" aria-label="Close canvas details" onClick={closeControls}><X/></button></header>
    <div className="a-canvas-location"><ShellSlot name="canvas.toolbar"><button type="button" className="a-soft" aria-label="Open a website in canvas" aria-expanded={!!draft.browser} onClick={()=>changeDraft({browser:!draft.browser,open:false})}><Globe/>Website</button><button type="button" className="a-soft" aria-label="Open a file in canvas" aria-expanded={!!draft.open} onClick={()=>changeDraft({open:!draft.open,browser:false})}><FolderOpen/>File</button></ShellSlot></div>
    {draft.browser&&<BrowserAddress state={state} act={act}/>}
    {draft.open&&<form className="a-canvas-file-form" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:draft.kind||'auto',path:draft.path||''})}}><label htmlFor="canvas-file-path">File in this workspace</label><PathField id="canvas-file-path" value={draft.path||''} onChange={path=>changeDraft({path})} state={state} act={act} placeholder="README.md or a full path"/><div className="a-canvas-file-actions"><select aria-label="Canvas file format" value={draft.kind||'auto'} data-action="view.update" onChange={e=>changeDraft({kind:e.target.value})}><option value="auto">Detect automatically</option><option value="html">HTML</option><option value="babylon">3D · Babylon.js</option><option value="mermaid">Mermaid</option><option value="dot">Graphviz DOT</option><option value="json">JSON</option><option value="jsonl">JSONL</option><option value="text">Text</option><option value="markdown">Markdown</option><option value="code">Code</option><option value="image">Image</option></select><button type="submit" className="a-primary" data-action="canvas.show" disabled={!draft.path?.trim()}>Open file<ChevronRight/></button></div></form>}
    <div ref={setControlsHost} data-part="canvas-view-controls" hidden={!!draft.library||!hasContent}/>
   </aside>
  </div>
  {latestEvent&&<div className="a-canvas-event" role="status"><Check/><span>Response recorded · {latestEvent.name}</span><small>The agent can see this response in app state.</small></div>}
 </aside></CanvasControlsHost.Provider></CanvasPrimaryActionsHost.Provider></CanvasVersionLoadProvider>;
}
