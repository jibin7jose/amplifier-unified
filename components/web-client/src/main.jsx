import {ApprovalPanel,isCapabilityCardAction,createCapabilityStopFence,stopFailureOutcome} from './approval-card';
import {AutomationHub} from './automation-hub';
import {createArchiveAction} from './archive-action.js';
import {quoteNotice as makeQuoteNotice,quoteNoticeAfterRemoval,quoteNoticeAfterSend,visibleQuoteNotice} from './quote-notice.js';
import {ArchiveContext,ArchiveNotice} from './archive-feedback.jsx';
import {createStateStream} from './state-stream.js';
import {recordActionTiming} from './api.js';
import {useConnectionSetup} from './setup-journey';
import {createChatLinkReceiver} from './chat-deep-link';
import {DictationButton} from './dictation-controls';
import {ChatPlan} from './chat-plan';
import {DependencyWaiting} from './dependency-waiting';
import {WorkProjectionStatus} from './work-projection-status';
import {voiceWorkRows,voiceWorkStream} from './voice-work-rows.js';
import {workProjectionReceipt} from './work-projection-receipt.js';
import {hasEarlierWindow,conversationHistoryPending} from './history-window.js';
import {ChatPages,chatPageTitles} from './chat-pages';
import {MarkdownComposer} from './markdown-composer.jsx';
import {ComposerAdd} from './composer-add.jsx';
import {ButtonTooltips} from './tooltips';
import {ConversationRail} from './conversation-rail.jsx';
import {ConversationStatus} from './conversation-usage.jsx';
import {ConnectionNotice} from './connection-notice.jsx';
import {transportFailure,retainUnconfirmed,afterReconnect} from './connection-notice.js';
import {WorkNavigationContext,workSurface,browsePatch} from './work-navigation';
import {WorkHeader,WorkSurface,AppFooter,ComposerWorkspace,WorkspaceCreation,LiveChatActivity} from './work-shell';
import workShellCss from './work-shell.css?raw';
import mainRefreshCss from './main-refresh.css?raw';
import canvasExperienceCss from './canvas-experience.css?raw';
import compactComposerCss from './compact-composer.css?raw';
import {bindWindowControlsOverlay} from './window-controls-overlay';
import windowControlsOverlayCss from './window-controls-overlay.css?raw';
import './window-controls-overlay.css';
import {AppReloadContext,AppReloadNotice,useAppReload} from './app-reload.jsx';
import {AppearanceSettings} from './appearance-settings';
import {useAppearanceEditor} from './appearance-editor';
import {appearanceCatalogFor,clearAppearanceCatalogs} from './appearance-catalog';
import {appAppearanceState} from './app-appearance-state';
import {homePatch} from './adaptive-work.js';
import {SettingsExperience} from './settings-experience';
import {useGuardedSettingsPanel,useGuardedSettingsSession} from './settings-drafts';
import {useMessageOutbox,outboxMessages} from './message-outbox';
import {clientId,clientUrl,attachClient,draftJournal} from './api';
import {checkpointSurfaces} from './surface-checkpoint';
import {useConversationDetail} from './conversation-detail';
import {readDetail} from './detail-read.js';
import {trackAction,setFeedbackEventStream} from './feedback-diagnostics';
import {ConversationError} from './conversation-controls';
import {useShell,ShellContext,ShellSlot} from './shell/runtime';
import {useThemeScheme,useAppearanceCache,useWindowChrome} from './theme-presentation';
import {ownershipState,actionErrorMessage} from './ownership.js';
import {ArtifactLinks} from './inline-artifacts';
import {withArtifactRows} from './artifact-placement';
import {ReplyPreview,MessageFocus} from './message-interactions';
import {MessageEntry,StreamingMessage,newestMessageIds,completedTurnEnds} from './message-actions';
import {PeerPresentation,PeerRun} from './peer-message';
import {chatArtifacts} from './canvas-library';
import {peerRuns} from './peer-groups.js';
import {usePeerPreview,PeerPreviewCanvas} from './peer-preview';
import {WorkspaceRail,AgentCanvas,SessionHistoryControls} from './shell-panels';
import './app-toolbar.css';
import {useNarrowScreen} from './responsive-navigation';
import {NewChatSetup,newChatSetup} from './new-chat';
import {WorkspaceLayout} from './panel-layout';
import {AttachmentStrip,readAttachment} from './chat-controls';
import {useChatFileDrop} from './chat-file-drop.js';
import {ComposerOptions} from './composer-options';
import {CoordinationPanel,selectWorkspaceChat} from './coordination';
import {ActivityPanel,useReadCompletion} from './attention';
import {ChatDelete} from './chat-delete';
import React,{useState,useEffect,useLayoutEffect,useRef,useCallback,useMemo} from 'react';
import {createRoot} from 'react-dom/client';
import {Phone,MessageCircle,Bell,ArrowUp,X,GitBranch,Download,ChevronRight,AudioLines} from 'lucide-react';
import {request,download,visibleView} from './api';
import {createPendingView} from './pending-view';
import {createSettingsActionQueue,settingsDraftKey,isProviderCatalogRead,isDraftDefaultsRead,isConversationChoiceRead,isAppearanceRead} from './settings-action-queue';
import {applyStateDelta,onlyStateRevisionChanged} from './state-transport';
import {createViewReporter} from './view-reporter';
import {createConversationNavigation,isNavigationAction,usesNavigationQueue} from './conversation-navigation';
import {createDestinationHistory,destinationCommand,destinationKey} from './destination-history.js';
import {onAuthenticationChange} from './api';
import {messageTextForCopy} from './message-copy';
import {deliverConversationExport} from './conversation-export.js';
import {VoiceClient} from './voice';
import {VoiceVisualClient,ComputerVisualClient} from './voice-visual';
import {VoiceVisualControls,SharingIndicator} from './voice-visual.jsx';
import {sessionStatus} from './session-status';
import './markdown-content.css';
import {FeedbackPanel,FeedbackNotice} from './feedback';
import {notificationBody,desktopNotificationsEnabled,notificationMessages} from './notifications';
import {ConversationWork,useInterimReplies} from './interim-replies.jsx';
import {executionData,turnPlacements,splitWork,conversationWorkRows} from './timeline-data';
import {LiveActivity} from './live-activity';
import {resizeComposer,composerPrimaryAction,composerExpanded} from './composer';
import {ComposerOwnership} from './composer-ownership';
import {Questions,QuestionNotice} from './questions';
import {NewChatHandoffs} from './new-chat-handoffs.jsx';
import {createActionFeedback} from './action-feedback';
import './action-feedback.css';
import {createChatScroll} from './chat-scroll';
import {followEarlierHistory} from './history-scroll';
import {isTopLevelChat} from './chat-navigation';
import {SubagentHistory} from './subagent-history';
import {McpAppThemeProvider} from './mcp-app-theme';
const logo='/branding/icons/amplifier-icon-128.png';
import defaultSkin from './unified.css?raw';
import chatPresentationCss from './chat-presentation.css?raw';
import './base.css';
import './unified.css';
import './settings-experience.css';
import responsiveNavigation from './responsive-navigation.css?raw';
import './settings-mobile.css';

const icons={call:Phone,text:Bell,chat:MessageCircle};
const pretty=v=>JSON.stringify(v,null,2);
const nowLabel=value=>{try{return new Date(typeof value==='number'&&value<1e12?value*1000:value).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}catch{return ''}};
function App(){
 const archiveRevision=useRef(0),archiveAction=useRef(null),[archiveFeedback,setArchiveFeedback]=useState({notice:null,pending:[]});
 const canvasInputRevision=useRef(0),permissionHistoryFocus=useRef(null);
 const capabilityCardQueue=useRef(Promise.resolve());
 const capabilityStopFence=useRef(null);
 if(!capabilityStopFence.current)capabilityStopFence.current=createCapabilityStopFence({request,clientId});
 useEffect(()=>{
  const supersede=()=>{canvasInputRevision.current++};
  for(const name of ['pointerdown','keydown','input'])document.addEventListener(name,supersede,true);
  return()=>{for(const name of ['pointerdown','keydown','input'])document.removeEventListener(name,supersede,true)};
 },[]);
 const narrow=useNarrowScreen();
 const [creatingWorkspace,setCreatingWorkspace]=useState(false);
 const actionFeedback=useRef(createActionFeedback()),outsidePointer=useRef(false),panelReturnFocus=useRef(null),settingsNavigation=useRef(null),workspaceSettingsNavigation=useRef(null);
 const dictationControl=useRef(null);
 // Cleared on every transport open/error; populated only by this stream's
 // authoritative baseline (then its valid delta chain), not action/GET state.
 const [streamClient,setStreamClient]=useState(null);
 const [destinationTrail,setDestinationTrail]=useState(null),destinationHistory=useRef(null);
 if(!destinationHistory.current)destinationHistory.current=createDestinationHistory({changed:setDestinationTrail});
 useEffect(()=>onAuthenticationChange(scope=>{destinationHistory.current.invalidate(scope);clearAppearanceCatalogs();setBootAttempt(value=>value+1)}),[]);
 const [state,setState]=useState(null),[catalog,setCatalog]=useState([]),[error,setError]=useState(''),[connected,setConnected]=useState(false),[busy,setBusy]=useState(false),[bootAttempt,setBootAttempt]=useState(0),[draft,setDraft]=useState(''),[workerDraft,setWorkerDraft]=useState(''),[themeDraft,setThemeDraft]=useState(defaultSkin),[themeName,setThemeName]=useState('Amplifier Unified'),[preview,setPreview]=useState(false),[agentAction,setAgentAction]=useState('view.update'),[agentArgs,setAgentArgs]=useState('{"patch":{"mode":"chat"}}'),[voice,setVoice]=useState({status:'idle'}),[uploading,setUploading]=useState(false);
 const appearanceEditor=useAppearanceEditor(state?.theme,defaultSkin);
 const visualClient=useRef(null),computerClient=useRef(null);const [visual,setVisual]=useState({status:'idle'}),[computerVisual,setComputerVisual]=useState({status:'idle'});
 const startingCall=useRef(false),[voiceStarting,setVoiceStarting]=useState(false);
 const outbox=useMessageOutbox(),deliveries=useRef(new Set()),sendQueue=useRef(Promise.resolve()),draftSaves=useRef(new WeakMap());
 const draftJournalEntries=useRef(new WeakMap()),[draftStorageError,setDraftStorageError]=useState(false);
 const referenceDraftLock=useRef(false),[referencingDraft,setReferencingDraft]=useState(false);
 const quoteApi=useRef(null),addingQuote=useRef(false),[quoteNotice,setQuoteNotice]=useState(null),[quoteReturn,setQuoteReturn]=useState(null);
 const workerSubmission=useRef({busy:false,revision:0}),[workerSending,setWorkerSending]=useState(false);
 const [awayFromBottom,setAwayFromBottom]=useState(false);
 const questionReviewState=useState({});
 const creatingSession=useRef(null),uploadQueue=useRef(Promise.resolve()),uploadCount=useRef(0),fileInput=useRef(null),messagesPane=useRef(null),stickToBottom=useRef(true),chatScroll=useRef(null),historyScrollAnchor=useRef(null),composerRef=useRef(null),root=useRef(null),latest=useRef(null),voiceClient=useRef(null),messagesEnd=useRef(null),draftTimer=useRef(),stagedDraft=useRef(null),stagedDraftPayload=useRef(null),lastNotify=useRef(new Set()),loadedTheme=useRef(null),lastDraft=useRef(''),canvasVisibilityQueue=useRef(Promise.resolve()),commandQueue=useRef(Promise.resolve()),voiceControlQueue=useRef(Promise.resolve()),navigationQueue=useRef(Promise.resolve()),canvasDirtyBarrier=useRef(null),reviewQueue=useRef(Promise.resolve()),stateListeners=useRef(new Set()),seenEffects=useRef(new Set()),effectHandler=useRef(()=>{}),pendingView=useRef(createPendingView()),serverState=useRef(null),conversationNavigation=useRef(createConversationNavigation());
 const [connectionFailure,setConnectionFailure]=useState(null);
 const chatLinkReceiver=useRef(null),[chatLink,setChatLink]=useState(null);
 const reportError=useCallback(error=>{
  if(transportFailure(error)&&latest.current)setConnectionFailure(previous=>retainUnconfirmed(previous,error));
  else setError(actionErrorMessage(error));
 },[]);
 const reconnected=useCallback(()=>{setConnected(true);setConnectionFailure(afterReconnect)},[]);
 const handleEffects=useCallback(effects=>{
  for(const effect of effects||[]){
   if(effect.id&&seenEffects.current.has(effect.id))continue;
   if(effect.id)seenEffects.current.add(effect.id);
   if(effect.createdAt&&Date.now()-new Date(typeof effect.createdAt==='number'&&effect.createdAt<1e12?effect.createdAt*1000:effect.createdAt).getTime()>60000)continue;
   Promise.resolve().then(async()=>{
    if(effect.type==='download')download(effect.filename||'amplifier-export.json',effect.content??effect.data,effect.mimeType||effect.mime||'application/json');
    if(effect.type==='conversation.export')await deliverConversationExport(effect,{request,download,clipboard:navigator.clipboard});
    if(effect.type==='message.copy'){
     let status='ready',message='Copied Markdown';
     try{await navigator.clipboard.writeText(await messageTextForCopy(latest.current,effect,request))}
     catch(error){status='error';message='Could not copy: '+error.message}
     await request('/api/actions',{method:'POST',body:{action:'message.copyResult',args:{requestId:effect.requestId,status,message}}});
    }
    if(effect.type==='download.url'){const link=document.createElement('a');link.href=effect.url;link.download=effect.filename;link.click()}
    if(effect.type==='browser.open')window.open(effect.url,'_blank','noopener,noreferrer');
    if(['clipboard.write','clipboard.url'].includes(effect.type)){
     try{let content=effect.content;if(effect.type==='clipboard.url'){const response=await fetch(effect.url);if(!response.ok)throw Error('Source unavailable');content=await response.text()}await navigator.clipboard.writeText(content);await request('/api/actions',{method:'POST',body:{action:'canvas.report',args:{id:effect.canvasId,part:'clipboard',status:'ready',message:'Source copied'}}})}
     catch(e){await request('/api/actions',{method:'POST',body:{action:'canvas.report',args:{id:effect.canvasId,part:'clipboard',status:'error',message:'Clipboard unavailable: '+e.message}}});throw e}
    }
    if(effect.type==='call.start')await voiceClient.current.start(effect.args||{});
    if(effect.type==='canvas.checkpoint')await checkpointSurfaces(effect.sessionId,effect.id);
    if(effect.type==='voice.visual.capture')await visualClient.current?.capture(effect);
    if(effect.type==='computer.visual.capture')await computerClient.current?.capture(effect);
    if(effect.type==='call.end')await voiceClient.current.control('call.end',effect.args||{});
    if(effect.type==='call.resumeAudio')voiceClient.current.callAudio.resumePlayback();
    if(effect.type==='call.keepAwake')voiceClient.current.setKeepAwake(effect.enabled??effect.args?.enabled??true);
    if(effect.type==='call.mute')await voiceClient.current.control('call.mute',effect.args||{});
    if(['notification.request','notification-permission'].includes(effect.type)&&'Notification'in window){const permission=await Notification.requestPermission();window.dispatchEvent(new Event('amplifier:notification-permission'));await request('/api/view',{method:'POST',body:{clientId,notificationPermission:permission}})}
   }).catch(e=>reportError(e));
  }
 },[]);
 effectHandler.current=handleEffects;
 const stateWaiters=useRef(new Set()),stateRecovery=useRef(null);
 const enqueueSettings=useRef(createSettingsActionQueue()),providerReadQueue=useRef(Promise.resolve()),handledActionResults=useRef(new WeakSet());
 const acceptState=useCallback((next,historyOptions={})=>{
  if(!next||typeof next!=='object')return;
  if(serverState.current && next.client?.hostInstanceId===serverState.current.client?.hostInstanceId && next.revision<serverState.current.revision)return;
  const unchangedPresentation=!historyOptions.baseline&&next.view?.panel!=='agent'&&onlyStateRevisionChanged(serverState.current,next);
  if(!latest.current){for(const effect of next.deviceCommands||[])if(effect.id)seenEffects.current.add(effect.id);for(const m of notificationMessages(next))lastNotify.current.add(m.id)}
  else effectHandler.current(next.deviceCommands);
  const previousSession=latest.current?.sessions?.find(s=>s.id===latest.current.selectedSessionId),nextSession=next.sessions?.find(s=>s.id===next.selectedSessionId);
  if(previousSession?.id===nextSession?.id&&Number(nextSession?.sharedHistoryOffset)<Number(previousSession?.sharedHistoryOffset)&&messagesPane.current){
   const pane=messagesPane.current,top=pane.getBoundingClientRect().top,anchor=[...pane.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);
   if(anchor){historyScrollAnchor.current={sessionId:nextSession.id,messageId:anchor.dataset.messageId,top:anchor.getBoundingClientRect().top};stickToBottom.current=false}
  }
  serverState.current=next;conversationNavigation.current.remember(next);
  destinationHistory.current.observe(next,historyOptions);
  for(const wake of stateWaiters.current)wake(next);
  latest.current=conversationNavigation.current.apply(next);if(!document.hidden&&!unchangedPresentation)setState(pendingView.current.apply(latest.current));stateListeners.current.forEach(fn=>fn(pendingView.current.apply(latest.current)));
 },[]);
 useEffect(()=>{const resume=()=>{if(!document.hidden&&latest.current)setState(pendingView.current.apply(latest.current))};document.addEventListener('visibilitychange',resume);return()=>document.removeEventListener('visibilitychange',resume)},[]);
 const awaitState=useCallback(result=>{
  if(result.stateRevision===undefined)return Promise.resolve(); // Older hosts and test fixtures.
  const reached=next=>next?.client?.hostInstanceId===result.hostInstanceId&&next.revision>=result.stateRevision;
  if(reached(serverState.current))return Promise.resolve();
  return new Promise((resolve,reject)=>{
   const done=()=>{clearTimeout(timer);stateWaiters.current.delete(wake);resolve()};
   const wake=next=>{if(reached(next))done()};
   // Lost/disconnected streams recover once for all waiting actions, never poll.
   const timer=setTimeout(()=>{
    stateWaiters.current.delete(wake);
    if(!stateRecovery.current)stateRecovery.current=request('/api/state').then(acceptState).finally(()=>{stateRecovery.current=null});
    stateRecovery.current.then(resolve,reject);
   },1500);
   stateWaiters.current.add(wake);
  });
 },[acceptState]);
 const dispatch=useCallback((action,args={},meta={})=>{
  const capabilityCard=isCapabilityCardAction(action,args,meta);
  const capabilityRead=capabilityCard&&action==='capability.read'?capabilityStopFence.current.beginRead(args):null;
  // Capture eligibility BEFORE passive recovery: this preflight cannot become
  // permission to send a decision merely because it reconciled an unknown Stop.
  const capabilityRecovery=action==='capability.read'?capabilityStopFence.current.reconcile(args.sessionId??latest.current?.selectedSessionId):null;
  const capabilityMutation=capabilityCard&&action!=='capability.read';
  if(capabilityMutation){
   try{capabilityStopFence.current.assertMutation(args,meta.capabilityRead)}catch(error){return Promise.reject(error)}
  }
  const permissionHistory=action==='view.update'&&args.patch?.canvasDraft?.delivery==='permissions';
  if(permissionHistory){
   const key=crypto.randomUUID();
   permissionHistoryFocus.current={key,sessionId:args.sessionId??latest.current?.selectedSessionId,revision:canvasInputRevision.current};
   args={...args,patch:{...args.patch,canvasDraft:{...args.patch.canvasDraft,permissionHistoryKey:key}}};
  }else if(action==='canvas.select'||action==='canvas.visibility'||isNavigationAction(action,args))canvasInputRevision.current++;
  const chatReference=action==='session.select'&&(args.chatReference||args.peerPreview);
  if(chatReference&&meta.chatReferenceIntent!==undefined&&meta.chatReferenceIntent!==archiveRevision.current)
   return Promise.resolve({accepted:false,superseded:true,error:'A newer navigation kept this chat reference from opening.'});
  if(meta.archiveIsCurrent&&!meta.archiveIsCurrent())return Promise.resolve({accepted:false});
  if(!meta.archiveIsCurrent&&!(chatReference&&meta.chatReferenceIntent!==undefined)&&(isNavigationAction(action,args)||action==='session.create'||action==='view.update'&&['panel','workWorkspaceTab'].some(key=>Object.hasOwn(args.patch||{},key))))archiveRevision.current++;
  if(chatReference)meta={...meta,chatReferenceIntent:meta.chatReferenceIntent??archiveRevision.current};
  if(meta.chatLink&&!meta.chatLinkIsCurrent())return Promise.resolve({accepted:false});
  const presentationOnly=['canvas.select','canvas.show'].includes(action)&&args.presentation&&args.presentation!=='canvas'&&args.id!==latest.current?.canvas?.id;
  const leavingSettings=!!meta.historyVisit||!!meta.recentPlace||!presentationOnly&&(['peer.preview','session.select','session.draft','workspace.select','session.create'].includes(action)||action==='canvas.visibility'&&args.open===false||['canvas.select','canvas.tabClose','canvas.show','canvas.openFile','canvas.openAttachment','canvas.openGif'].includes(action));
  if(leavingSettings&&!meta.settingsConfirmed&&settingsNavigation.current?.hasUnsaved())return settingsNavigation.current.confirmClose().then(accepted=>accepted?dispatch(action,args,{...meta,settingsConfirmed:true}):{accepted:false,superseded:!!chatReference&&meta.chatReferenceIntent!==archiveRevision.current,error:'Settings changes were kept.'});
  const workspaceGuard=workspaceSettingsNavigation.current;
  if(!meta.workspaceSettingsConfirmed&&workspaceGuard?.hasUnsaved()&&(meta.historyVisit||meta.recentPlace||workspaceGuard.leaving(action,args)))
   return workspaceGuard.confirmClose().then(accepted=>accepted?dispatch(action,args,{...meta,workspaceSettingsConfirmed:true}):{accepted:false,error:'Workspace settings changes were kept.'});
  if(!meta.chatLink&&(['session.select','session.draft','workspace.select','session.create'].includes(action)||action==='shell.command'&&['session.select','session.draft','workspace.select','session.create'].includes(args.action)))chatLinkReceiver.current?.dismiss();
  if(action==='call.resumeAudio')voiceClient.current?.callAudio.resumePlayback(); // Preserve a click's user activation.
  if(action==='call.start')args={attemptId:crypto.randomUUID(),...args};
  const localVoiceControl=action==='call.mute'||action==='call.end';
  if(localVoiceControl){
   const scope=voiceClient.current?.scope();
   if(!scope)return Promise.resolve({accepted:true,result:{ignored:true}});
   args={...scope,...args,controlId:args.controlId||crypto.randomUUID()};
   // Device privacy controls take effect before either HTTP or the command queue.
   // The shared action still records the same scoped intent for other clients.
   voiceClient.current.control(action,args).catch(reportError);
  }
  if(action==='message.reply'&&!meta.quotePrepared){
   if(addingQuote.current||!quoteApi.current||args.sessionId!==latest.current?.selectedSessionId)return Promise.reject(Error('Choose an available message in this conversation; a quote may already be pending.'));
   const api=quoteApi.current,target=api.capture(),sessionId=args.sessionId;
   addingQuote.current=true;setQuoteNotice(makeQuoteNotice(sessionId,'Adding quote…'));
   return (async()=>{
    let prepared={...args,snapshotOnly:true};
    const source=meta.quoteDetail?await readDetail(meta.quoteDetail,async(url,options)=>{
     const page=await request(url,options);
     if(page.total>12000)throw Error('Choose a passage of at most 12,000 characters. Nothing was shortened.');
     return page;
    }):meta.quoteSource;
    if(typeof source==='string'){
     const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source));
     const digest=[...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');
     prepared=args.selection?{...prepared,selection:{...args.selection,sourceDigest:digest}}:{...prepared,expectedSourceDigest:digest};
    }
    return dispatch(action,prepared,{...meta,quotePrepared:true});
   })().then(result=>{
    if(api!==quoteApi.current||latest.current?.selectedSessionId!==sessionId)throw Error('The conversation changed. The quote was not added.');
    api.insert(result.result.quote,target);setQuoteNotice(makeQuoteNotice(sessionId,'Quote added. Earlier quotes, commentary and attachments kept.'));return result;
   }).catch(error=>{setQuoteNotice(makeQuoteNotice(sessionId,error.message));throw error}).finally(()=>{addingQuote.current=false;});
  }
  const referenceAction=action==='canvas.reference'||action==='canvas.views.command'&&args.action==='canvas.reference';
  if(referenceAction&&!meta.referencePrepared){
   if(referenceDraftLock.current)return Promise.reject(Error('A reference is already being added.'));
   const current=pendingView.current.apply(latest.current),sessionId=current?.selectedSessionId,expectedDraft=current?.view?.draft||'';
   const inner=action==='canvas.views.command'?args.args:args;
   referenceDraftLock.current=true;setReferencingDraft(true);clearTimeout(draftTimer.current);
   const payload=stagedDraftPayload.current,token=stagedDraft.current;
   return (async()=>{
    if(token&&payload)await saveDraft(payload,token);
    if(latest.current?.selectedSessionId!==sessionId)throw Error('The conversation changed. Your draft was kept; select the text again.');
    const prepared={...inner,sessionId:inner.sessionId??sessionId,...(action==='message.reply'?{}:{expectedDraft:inner.expectedDraft??expectedDraft})};
    return await dispatch(action,action==='canvas.views.command'?{...args,args:prepared}:prepared,{...meta,referencePrepared:true});
   })().finally(()=>{referenceDraftLock.current=false;setReferencingDraft(false)});
  }
  if(['conversation.send','conversation.stop','worker.spawn','worker.stop','worker.steer','approval.respond','attachment.add','attachment.remove'].includes(action))args={sessionId:latest.current?.selectedSessionId,...args};
  // This is synchronous request intent, before the command can wait on Canvas.
  // Reads may still reconcile display; later mutations cannot overtake this Stop.
  const stop=action==='conversation.stop'&&args.sessionId
   ?capabilityStopFence.current.beginStop(args.sessionId,meta.id||crypto.randomUUID()):null;
  if(stop)meta={...meta,id:stop.commandId};
  let stopPostStarted=false;
  if(action==='conversation.send'&&args.expectedConfigurationRevision===undefined){const target=latest.current?.sessions?.find(row=>row.id===args.sessionId);args={...args,expectedConfigurationRevision:target?.nextMessageRevision||0}}
  if(action==='conversation.send'&&!meta.checkpointed)return checkpointSurfaces(args.sessionId).then(()=>dispatch(action,args,{...meta,checkpointed:true}));
  if(action==='view.update'&&Object.keys(args.patch||{}).some(key=>key==='draft'||key.startsWith('canvas')))args={sessionId:latest.current?.selectedSessionId,...args};
  const selectedId=action==='session.select'?args.id:action==='shell.command'&&args.action==='session.select'?args.args?.id:null;
  const openingDraft=action==='session.draft'||action==='shell.command'&&args.action==='session.draft';
  // References, like external links, keep source controls/editors mounted until
  // acceptance. The same navigation queue and Settings/Canvas guards still own
  // the transition; rejection must not destroy the control that shows it.
  const navigationToken=openingDraft?conversationNavigation.current.beginDraft():selectedId&&!meta.chatLink&&!chatReference&&!canvasDirtyBarrier.current?conversationNavigation.current.begin(pendingView.current.apply(latest.current),selectedId,meta.navigationSession):null;
  if(navigationToken&&serverState.current){latest.current=conversationNavigation.current.apply(serverState.current);setState(pendingView.current.apply(latest.current))}
  if(action==='canvas.visibility')args={sessionId:latest.current?.selectedSessionId??null,canvasId:latest.current?.canvas?.id??null,...args};
  const pending=action==='canvas.visibility'?pendingView.current.addCanvas(latest.current,args.open):action==='view.update'?(meta.pendingViewToken??pendingView.current.add(args.patch||{},args.sessionId)):null;
  if(pending&&latest.current)setState(pendingView.current.apply(latest.current));
  // Mute stays reversible while host bookkeeping is pending. Hangup renders
  // its own ending state as soon as local media has been silenced.
  const settleTracking=trackAction(),settleFeedback=meta.feedback===false||localVoiceControl?()=>{}:actionFeedback.current.begin(action==='shell.command'?args.action:action);
  const dirtyBarrier=canvasDirtyBarrier.current;
  const execute=async()=>{
   if(meta.before)await meta.before;
   if(capabilityRecovery)await capabilityRecovery;
   if(meta.consumePermissionHistory){
    // This is bundled presentation housekeeping, never a saved stale draft.
    // Resolve after queued document changes; an older effect cannot restore Overview.
    const current=serverState.current,draft=current?.view?.canvasDraft;
    if(current?.selectedSessionId!==args.sessionId||!draft?.library||draft.delivery!=='permissions'||draft.permissionHistoryKey!==meta.consumePermissionHistory)
     return {accepted:false,superseded:true};
    args={sessionId:args.sessionId,patch:{canvasDraft:{...draft,delivery:null}}};
   }
   // Navigation and answers have separate queues; neither may overtake an
   // already declared renderer edit when its transition can remove the view.
   if((navigation||['question.answer','question.cancel','question.supersede','canvas.views.loadVersion'].includes(action))&&dirtyBarrier)await dirtyBarrier;
   if(capabilityMutation)capabilityStopFence.current.assertMutation(args,meta.capabilityRead);
   if(meta.chatLink&&!meta.chatLinkIsCurrent())return {accepted:false};
   if(meta.archiveIsCurrent&&!meta.archiveIsCurrent())return {accepted:false};
   if(chatReference&&(meta.chatReferenceIntent!==archiveRevision.current||
     latest.current?.selectedSessionId!==chatReference.sourceSessionId))
    return {accepted:false,superseded:true,error:'The view changed. Open the reference from its original reply.'};
   // Begin only when this queued command executes, after local/settings guards.
   // No file-open/create action is replayed by a history traversal.
   const historyVisit=meta.historyVisit?destinationHistory.current.begin(meta.historyVisit):meta.recentPlace?destinationHistory.current.beginRecent(destinationKey(meta.recentPlace.entry),meta.recentPlace.entry,meta.recentPlace.scope):null;
   if((meta.historyVisit||meta.recentPlace)&&!historyVisit)return {accepted:false};
   try{
    let result;
    try{
     if(stop)stopPostStarted=true;
     result=await request('/api/actions',{signal:meta.signal,method:'POST',body:{action,args,id:meta.id||crypto.randomUUID(),...(meta.expectedRevision!==undefined?{expectedRevision:meta.expectedRevision}:{})}});
    }catch(error){
     if(stop){
      capabilityStopFence.current.settleStop(stop,stopFailureOutcome(error,stopPostStarted));
      void capabilityStopFence.current.reconcile(stop.sessionId);
     }
     throw error;
    }
    if(stop){
     capabilityStopFence.current.settleStop(stop,result?.accepted===true?'accepted':'unknown');
     void capabilityStopFence.current.reconcile(stop.sessionId);
    }
    // An exact positive delivery receipt is independent of the bounded message
    // window and of a following state read. Never treat generic acceptance as delivery.
    if(action==='conversation.delivery'&&result.accepted===true){
     outbox.acknowledge(args.sessionId,args.inputId,result.result);
     if(result.result?.delivery==='accepted'){
      try{sessionStorage.removeItem('amplifier.deliveryWarning.v1:'+JSON.stringify([args.sessionId,args.inputId]))}catch{}
     }
    }
    if(historyVisit)destinationHistory.current.finish(result);
    try{
     if(result.state)acceptState(result.state);
     // Exact annotation and read-only preview receipts settle locally. Navigation
     // still waits for its authoritative frame and exact history confirmation.
     else if(!['message.reaction','peer.preview','context.inspect'].includes(action))await awaitState(result);
    }catch(error){
     // An accepted reset is not a refusal when only its following read fails.
     // Keep the exact receipt; the Canvas owner offers a read, never a replay.
     if(action==='canvas.views.loadVersion'&&result.accepted===true)
      return {...result,stateConfirmation:{status:'unconfirmed',message:error.message}};
     throw error;
    }
    if(historyVisit){
     // awaitState's recovery may return without the receipt's revision. The
     // history model requires its own exact route/host/revision evidence.
     if(destinationHistory.current.snapshot().pending)destinationHistory.current.finish(null);
     const confirmed=await historyVisit.confirmed;
     if(!confirmed)throw Error('Navigation was not confirmed. History was kept; no action was retried.');
    }
    return capabilityRead&&result.result?.request
     ?{...result,result:{...result.result,capabilityRead:capabilityStopFence.current.finishRead(capabilityRead,result.result.request.requestHash)}}
     :result;
   }catch(error){if(historyVisit)destinationHistory.current.finish(null);throw error}
  };
  // Browse and chat selections share intent order. A later optimistic browse
  // stays pending until an earlier selection can no longer replace its scope.
  const navigation=isNavigationAction(action,args);
  // Reviewing exact item fingerprints is independent of send admission and view changes.
  const exactReview=action==='attention.read'&&Array.isArray(args.ids)&&args.ids.length>0&&args.ids.every(id=>typeof args.fingerprints?.[id]==='string');
  const defaultsRead=isDraftDefaultsRead(action,meta),choiceRead=isConversationChoiceRead(action,args,meta),catalogRead=isProviderCatalogRead(action,meta)||defaultsRead||choiceRead;
  const appearanceRead=isAppearanceRead(action,meta);
  // Optional plan summaries have their own exact read result; they must not
  // hold up browsing or ordered mutations while canonical files are read.
  const planRead=action==='session.plans'||action==='context.inspect';
  // Reveal joins document ordering AND visibility ordering without merging their
  // queues globally. Capture the prior visibility tail before publishing this one.
  const after=permissionHistory?canvasVisibilityQueue.current:(catalogRead&&!choiceRead||appearanceRead)?commandQueue.current:null;
  // A read-only Canvas reveal must not hold a pending card's guarded read or
  // decision. Keep those individual calls ordered, independent of Canvas work.
  const queue=capabilityCard?capabilityCardQueue:usesNavigationQueue(action,args)?navigationQueue:action==='call.mute'?voiceControlQueue:localVoiceControl||action.startsWith('coordination.')||meta.presentation||defaultsRead||choiceRead||planRead||appearanceRead?{current:Promise.resolve()}:action==='canvas.visibility'?canvasVisibilityQueue:exactReview?reviewQueue:['conversation.send','message.edit','question.answer'].includes(action)?sendQueue:catalogRead?providerReadQueue:commandQueue;
  const queuedAt=performance.now();
  const timedExecute=()=>{const startedAt=performance.now();return Promise.resolve().then(execute).finally(()=>recordActionTiming(action,queuedAt,startedAt))};
  const promise=enqueueSettings.current(queue,timedExecute,settingsDraftKey(action,args,meta),after).then(result=>{
   // Each caller owns an optimistic token even when several waiting editor
   // snapshots share the final request. Settle all callers after its receipt.
   if(pending)pendingView.current.settle(pending);
   if(navigationToken){
    conversationNavigation.current.settle(navigationToken);
    latest.current=conversationNavigation.current.apply(serverState.current);
    setState(pendingView.current.apply(latest.current));
   }
   if(pending&&latest.current)setState(pendingView.current.apply(latest.current));
   if(chatReference&&result.accepted===true)requestAnimationFrame(()=>{
    if(meta.chatReferenceIntent!==archiveRevision.current||latest.current?.selectedSessionId!==args.id)return;
    const messages=root.current?.querySelector('[data-part="messages"]');
    if(messages?.dataset.sessionId!==args.id)return;
    if(args.chatReference){
     const heading=root.current?.querySelector('[data-part="session-heading"]');
     if(heading?.dataset.sessionId===args.id)heading.focus({preventScroll:true});
    }else{messages.setAttribute('tabindex','-1');messages.focus({preventScroll:true})}
   });
   if(!handledActionResults.current.has(result)){handledActionResults.current.add(result);handleEffects(result.effects)}
   return result;
  }).catch(error=>{if(stop&&!stopPostStarted)capabilityStopFence.current.settleStop(stop,'not-applied');if(navigationToken){conversationNavigation.current.settle(navigationToken);if(serverState.current){latest.current=conversationNavigation.current.apply(serverState.current);setState(pendingView.current.apply(latest.current))}}if(error.state)acceptState(error.state);if(pending){pendingView.current.settle(pending);if(latest.current)setState(pendingView.current.apply(latest.current))}if(chatReference&&meta.chatReferenceIntent!==archiveRevision.current)error.navigationSuperseded=true;throw error}).finally(()=>{settleTracking();settleFeedback()});
  queue.current=promise.catch(()=>{});
  if(permissionHistory)canvasVisibilityQueue.current=queue.current;
  if(action==='canvas.views.dirty'){
   canvasDirtyBarrier.current=promise;
   const settled=()=>{if(canvasDirtyBarrier.current===promise)canvasDirtyBarrier.current=null};
   promise.then(settled,settled);
  }
  return promise;
 },[acceptState,handleEffects,awaitState]);
 useEffect(()=>{
  for(const entry of outbox.entries){
   if(entry.sessionId||!entry.creation?.id)continue;
   const created=state?.sessions?.find(row=>row.creationCommandId===entry.creation.id);
   if(created)outbox.update(entry.id,{sessionId:created.id});
  }
 },[state?.sessions,outbox.entries]);
 // Shell snapshots are server-scoped. An optimistic browse patch can arrive
 // before its queued action; refresh only after the server accepts that scope.
 const shell=useShell(serverState.current,dispatch,clientId,state,{onError:reportError,onRecovered:()=>setConnectionFailure(afterReconnect)});
 useEffect(()=>root.current?actionFeedback.current.attach(root.current):undefined,[!!state,shell.ready]);
 const act=useCallback((name,args={},meta={})=>dispatch(name,args,meta).catch(e=>{if(meta.propagateError)throw e;return reportError(e)}),[dispatch,reportError]);
 // Warm only the small catalog after attachment, without blocking chat startup
 // or reading full appearance CSS. Settings observes this same page-local owner.
 useEffect(()=>{
  const host=state?.client?.hostInstanceId;
  if(host)appearanceCatalogFor(host).refresh(dispatch).catch(()=>{});
 },[state?.client?.hostInstanceId,dispatch]);
 const peerPreview=usePeerPreview(state,dispatch),[peerOpen,setPeerOpen]=useState({});
 const peerPresentation=useMemo(()=>({open:peerOpen,setOpen:setPeerOpen,preview:peerPreview.open}),[peerOpen,peerPreview.open]);
 useEffect(()=>{
  if(!chatLinkReceiver.current)chatLinkReceiver.current=createChatLinkReceiver({location,history,
   launchQueue:window.launchQueue,onChange:setChatLink});
  if(!state)return;
  return chatLinkReceiver.current.bind((id,isCurrent)=>dispatch('session.select',{id},{chatLink:true,chatLinkIsCurrent:isCurrent}));
 },[!!state,dispatch,reportError]);
 const viewReporter=useRef(null);
 if(!viewReporter.current)viewReporter.current=createViewReporter(body=>request('/api/view',{method:'POST',body}));
 const publishView=useCallback(()=>{if(latest.current&&!document.hidden)viewReporter.current({...visibleView(root.current,clientId,{interfaceOnly:true}),sessionId:latest.current.selectedSessionId??null,voice:voiceClient.current?.state,notificationPermission:'Notification'in window?Notification.permission:'unsupported'});},[]);
 const streamRecovery=useRef({delay:2000,lastWake:-Infinity});
 useEffect(()=>{
  let alive=true,source,retryTimer,restarting=false,wasHidden=document.hidden;const controller=new AbortController();setError('');setStreamClient(null);
  // Reconnect only the transport. Keep React, private drafts and Canvas mounts.
  const restart=()=>{
   if(!alive||restarting||document.hidden||!latest.current)return;
   restarting=true;clearTimeout(retryTimer);source?.close();setFeedbackEventStream('connecting');setConnected(false);setBootAttempt(value=>value+1);
  };
  const wake=()=>{
   if(document.hidden||!latest.current||Date.now()-streamRecovery.current.lastWake<1000)return;
   streamRecovery.current.lastWake=Date.now();restart();
  };
  const visible=()=>{const returned=wasHidden&&!document.hidden;wasHidden=document.hidden;if(returned)wake()};
  const restored=event=>{if(event.persisted)wake()};
  window.addEventListener('online',wake);window.addEventListener('pageshow',restored);document.addEventListener('visibilitychange',visible);
  const timer=setTimeout(()=>{controller.abort();if(alive&&!latest.current)setError('The workspace is taking too long to respond. You can retry the connection.')},10000);
  attachClient(controller.signal).then(attachment=>{
   if(!alive)return;
   // The stream supplies the authoritative baseline. A separate state GET would
   // duplicate it and race it; explicit recovery still uses /api/state.
   request('/api/actions',{signal:controller.signal}).then(actions=>{if(alive)setCatalog(Array.isArray(actions)?actions:actions.actions||[])}).catch(()=>{});
   let streamState,historyBaseline=true;
   setFeedbackEventStream('connecting');source=createStateStream(clientUrl('/api/events?transport=delta-v1'),attachment.transports);source.addEventListener('state',e=>{if(!alive)return;try{if(e.decodeError)throw e.decodeError;const initial=!latest.current;streamState=e.data;acceptState(streamState,{baseline:historyBaseline});historyBaseline=false;setStreamClient(streamState.client?.id===clientId?streamState.client:null);streamRecovery.current.delay=2000;reconnected();if(initial)setError('');clearTimeout(timer)}catch{}});
   source.addEventListener('state-delta',e=>{if(!alive)return;try{if(e.decodeError)throw e.decodeError;streamState=applyStateDelta(streamState,e.data);acceptState(streamState);setStreamClient(streamState.client?.id===clientId?streamState.client:null);reconnected()}catch{source.close();setFeedbackEventStream('closed');setConnected(false);setStreamClient(null);setBootAttempt(value=>value+1)}});
   source.addEventListener('shell',e=>{try{if(e.decodeError)throw e.decodeError;window.dispatchEvent(new CustomEvent('amplifier-shell',{detail:e.data}))}catch{}});
   source.onopen=()=>{historyBaseline=true;streamState=undefined;setStreamClient(null);setFeedbackEventStream('open');reconnected();void capabilityStopFence.current.reconcile();viewReporter.current.invalidate();publishView();window.dispatchEvent(new Event('amplifier-reconnected'))};source.onerror=()=>{
    if(!alive||restarting)return;
    setFeedbackEventStream(source.readyState===2?'closed':'reconnecting');setConnected(false);setStreamClient(null);
    // CONNECTING is owned by EventSource's native retry. CLOSED cannot recover
    // by itself; retry at a bounded rate after a previously loaded chat.
    if(source.readyState===2&&latest.current&&!document.hidden&&!retryTimer){
     retryTimer=setTimeout(restart,streamRecovery.current.delay);streamRecovery.current.delay=Math.min(streamRecovery.current.delay*2,30000);
    }
   };
  }).catch(e=>{clearTimeout(timer);if(alive&&e.name!=='AbortError')reportError(e)});
  return()=>{alive=false;clearTimeout(timer);clearTimeout(retryTimer);controller.abort();source?.close();window.removeEventListener('online',wake);window.removeEventListener('pageshow',restored);document.removeEventListener('visibilitychange',visible);setFeedbackEventStream('closed')};
 },[acceptState,bootAttempt,reconnected,reportError]);
 useEffect(()=>{window.amplifier=Object.freeze({shellClientId:clientId,getShellState:()=>shell.data,getState:()=>({...pendingView.current.apply(latest.current),renderedView:visibleView(root.current,clientId)}),getActions:()=>catalog,dispatch,subscribe:fn=>{stateListeners.current.add(fn);return()=>stateListeners.current.delete(fn)}});return()=>{delete window.amplifier}},[catalog,dispatch,shell.data]);
 useEffect(()=>{const timer=setTimeout(publishView,300);return()=>clearTimeout(timer)},[state,draft,themeDraft,preview,voice,publishView,shell.data]);
 useEffect(()=>{let timer;const schedule=()=>{clearTimeout(timer);timer=setTimeout(publishView,200)};document.addEventListener('visibilitychange',schedule);document.addEventListener('selectionchange',schedule);document.addEventListener('focusin',schedule);document.addEventListener('input',schedule);window.addEventListener('resize',schedule);return()=>{clearTimeout(timer);document.removeEventListener('visibilitychange',schedule);document.removeEventListener('selectionchange',schedule);document.removeEventListener('focusin',schedule);document.removeEventListener('input',schedule);window.removeEventListener('resize',schedule)}},[publishView]);
 useEffect(()=>{if(!state)return;const serverDraft=state.view?.draft||'';if(serverDraft!==lastDraft.current){setDraft(serverDraft);lastDraft.current=serverDraft}},[state?.view?.draft,state?.selectedSessionId]);
 useEffect(()=>{if(!state)return;const key=state.theme?.name+'::'+state.theme?.css;if(key!==loadedTheme.current){const changed=loadedTheme.current!==null;setThemeDraft(state.theme?.css||defaultSkin);setThemeName(state.theme?.name||'Amplifier Unified');setPreview(false);loadedTheme.current=key;if(changed&&state.view?.themePreview)dispatch('view.update',{patch:{themePreview:false}}).catch(reportError)}},[state?.theme]);
 useEffect(()=>{const v=state?.view;if(!v)return;if(v.workerDraft!==undefined)setWorkerDraft(v.workerDraft);if(v.themeDraft!==undefined)setThemeDraft(v.themeDraft);if(v.themeDraftName!==undefined)setThemeName(v.themeDraftName);if(v.themePreview!==undefined)setPreview(v.themePreview);if(v.agentAction!==undefined)setAgentAction(v.agentAction);if(v.agentArgs!==undefined)setAgentArgs(v.agentArgs)},[state?.view?.workerDraft,state?.view?.themeDraft,state?.view?.themeDraftName,state?.view?.themePreview,state?.view?.agentAction,state?.view?.agentArgs]);
 const detail=useConversationDetail(state?.sessions?.find(s=>s.id===state.selectedSessionId),()=>{stickToBottom.current=false;const pane=messagesPane.current;if(!pane)return;const top=pane.getBoundingClientRect().top,anchor=[...pane.querySelectorAll('[data-message-id]')].find(node=>node.getBoundingClientRect().bottom>top);if(anchor)historyScrollAnchor.current={sessionId:state.selectedSessionId,messageId:anchor.dataset.messageId,top:anchor.getBoundingClientRect().top}},dispatch);
 useEffect(()=>{if(detail.jumpRequest){const frame=requestAnimationFrame(()=>detail.jumpRequest.id==='latest'?chatScroll.current?.reveal():chatScroll.current?.jump(detail.jumpRequest.id));return()=>cancelAnimationFrame(frame)}},[detail.jumpRequest]);
 const session=detail.session,view=state?.view||{},mode=view.mode||'chat',activity=sessionStatus(session),working=activity.busy;
 const messages=useMemo(()=>outboxMessages(session?.messages||[],outbox.entries,session?.id),[session?.messages,outbox.entries,session?.id]);
 const historyPending=conversationHistoryPending(session,state?.navigationPending,messages),ownership=ownershipState(session),executionUnavailable=session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||ownership.blocked;
 // Pending New chat fences input without replacing the current transcript.
 const historyDisplayPending=conversationHistoryPending(session,!!state?.navigationPending&&!state?.draftNavigationPending,messages);
 const settingsSession=useGuardedSettingsSession(session,settingsNavigation,id=>dispatch(id?'session.select':'session.draft',id?{id}:{},{settingsConfirmed:true}));
 const settingsRoute=useGuardedSettingsPanel(state?.canvas?.kind==='settings'&&state.canvas.open&&!view.canvasDraft?.library&&!view.panel?'settings-canvas':view.panel,settingsNavigation,route=>act('view.update',{patch:{panel:route==='settings-canvas'?null:route,settingsCanvas:route==='settings-canvas'}}));
 const canvasSettingsVisible=settingsRoute==='settings-canvas',panel=canvasSettingsVisible?null:settingsRoute;
 useEffect(()=>{
  const surface=workSurface(state),workspace=state?.workspaces?.find(row=>row.id===state.view?.workWorkspaceId);
  const title=surface==='home'?'':surface==='workspaces'?'All workspaces':surface==='chats'?'All chats':surface==='workspace'?workspace?.name||'Workspace':session?.title?.trim();
  document.title=title?title+' - Amplifier':'Amplifier';
 },[session?.id,session?.title,state?.view?.workSurface,state?.view?.workWorkspaceId,state?.workspaces]);
 useReadCompletion(state,act,messagesPane,shell.ready);
 const execution=useMemo(()=>splitWork(messages,executionData(session)),[messages,session?.id,session?.execution,session?.workers,session?.workerSummary?.bounded,session?.runtimeEvents,session?.status]);
 // Draft/setup/settings surfaces own provider discovery. Reading an existing
 // conversation must not refresh a workspace's complete provider inventory.
 useConnectionSetup(state,session,act);
 const availableAttachments=(session?.draftAttachments||state?.draftAttachments||[]).filter(file=>!outbox.entries.some(row=>row.sessionId===session?.id&&row.attachmentIds.includes(file.id)));
 useEffect(()=>{for(const entry of outbox.entries){const saved=state?.sessions?.find(row=>row.id===entry.sessionId)?.messages?.find(row=>row.inputId===entry.commandId);if(['accepted','queued_for_update','held'].includes(saved?.delivery?.status))outbox.update(entry.id,null)}},[state]);
 const turnEnds=useMemo(()=>completedTurnEnds(session),[session?.messages,session?.sharedHistoryUserTurnOffset,session?.execution?.turns,session?.status]);
 const workPlacement=useMemo(()=>turnPlacements(messages,execution),[messages,execution]),newest=useMemo(()=>newestMessageIds(messages),[messages]);
 const voiceStream=useMemo(()=>voiceWorkStream(session),[session?.streaming,session?.streamingId,session?.streamingInputId]);
 const rawConversationRows=useMemo(()=>withArtifactRows(voiceWorkRows(conversationWorkRows(messages,workPlacement),execution,voiceStream)),[messages,workPlacement,execution,voiceStream]);
 const interim=useInterimReplies(rawConversationRows,execution,shell.composition?.presentation?.collapseInterimReplies===true,session?.id,messagesPane,state?.view?.executionExpanded,act);
 const artifactBoundaries=useMemo(()=>new Set([
  ...chatArtifacts(state||{}).flatMap(row=>(row.publications||[{messageId:row.messageId}]).map(link=>link.messageId)),
  ...(session?.execution?.imageGeneration||[]).map(job=>job.messageId),
 ]),[state?.canvasArtifacts,state?.selectedSessionId,state?.selectedWorkspaceId,session?.execution?.imageGeneration]);
 const conversationRows=useMemo(()=>peerRuns(interim.rows,artifactBoundaries),[interim.rows,artifactBoundaries]);
 const [recordingTarget,setRecordingTarget]=useState(null);
 useLayoutEffect(()=>{
  if(!recordingTarget)return;
  if(recordingTarget.sessionId!==session?.id){setRecordingTarget(null);return}
  const element=document.getElementById('work-'+recordingTarget.group);
  if(element){element.scrollIntoView({block:'center'});setRecordingTarget(null)}
 },[recordingTarget,session?.id,state?.view?.executionExpanded]);
 const openRecordings=async requested=>{
  const observations=new Set(execution.turns.filter(turn=>turn.observationWindow).map(turn=>turn.id));
  const candidates=conversationRows.filter(row=>row.kind==='work'&&row.items.some(item=>observations.has(item.turnId)));
  const row=candidates.find(row=>row.items.some(item=>item.turnId===requested))||candidates.at(-1);
  if(!row)return;
  const group=row.items.some(item=>item.turnId===requested)?requested:row.items.filter(item=>observations.has(item.turnId)).at(-1).turnId;
  const expanded=new Set(state.view?.executionExpanded||[]);
  for(const key of [row.summaryId||(row.interim?row.id:'work:'+row.items[0].turnId),'turn:'+group]){expanded.delete('closed:'+key);expanded.add(key)}
  await act('view.update',{patch:{executionExpanded:[...expanded]}});
  setRecordingTarget({sessionId:session?.id,group});
 };

 const readingWindowLoader=useRef(null),readingReference=useRef(null);
 readingReference.current=(id,messageId)=>session?.id===id?messages.find(row=>row.id===messageId)?.sourceReference:null;
 readingWindowLoader.current=async(id,messageId,isCurrent,reference)=>{
  if(session?.id!==id)return false;
  if(await interim.ensureVisible(messageId))return true;
  return detail.around(messageId,{jump:false,isCurrent,reference});
 };
 useLayoutEffect(()=>{if(!messagesPane.current)return;const scroll=createChatScroll(messagesPane.current,stickToBottom,setAwayFromBottom,{loadAnchor:(...args)=>readingWindowLoader.current?.(...args),readingReference:(...args)=>readingReference.current?.(...args)});chatScroll.current=scroll;return()=>{scroll.dispose();chatScroll.current=null}},[!!state,shell.ready]);
 useLayoutEffect(()=>{chatScroll.current?.select(session?.id??null,!historyPending&&!state?.navigationPending)},[session?.id,historyPending,state?.navigationPending,shell.ready]);
 useLayoutEffect(()=>{
  const owned=voice.status==='connected'&&state?.voice?.status==='connected'&&voice.id&&voice.id===state?.voice?.id;
  chatScroll.current?.setCall(owned?voice.id:null,owned?state.voice.sessionId:null);
 },[voice.id,voice.status,state?.voice?.id,state?.voice?.status,state?.voice?.sessionId,!!state,shell.ready]);
 useEffect(()=>{
  const pane=messagesPane.current;if(!pane||!session)return;
  return followEarlierHistory(pane,()=>!detail.busy&&!session.historyLoading&&(hasEarlierWindow(session.messageWindow)||(session.messageWindow?.mode!=='cursor'&&session.sharedHistoryOffset>0)),()=>{
   stickToBottom.current=false;
   return detail.earlier();
  });
 },[session?.id,session?.messageWindow,session?.sharedHistoryOffset,session?.historyLoading,detail.busy,act,shell.ready]);
 useLayoutEffect(()=>{const saved=historyScrollAnchor.current;if(!saved||!messagesPane.current)return;historyScrollAnchor.current=null;if(saved.sessionId!==session?.id)return;const pane=messagesPane.current,anchor=[...(pane?.querySelectorAll('[data-message-id]')||[])].find(node=>node.dataset.messageId===saved.messageId);if(anchor)pane.scrollTop+=anchor.getBoundingClientRect().top-saved.top},[session?.id,session?.sharedHistoryOffset,session?.messageWindow,session?.executionWindow,shell.ready]);
 // Initial Work and genuine retry notices occupy space above the transcript.
 // Preserve reading intent in the same commit when those notices change.
 useLayoutEffect(()=>{chatScroll.current?.settleLayout()},[session?.workProjection?.status,session?.id,shell.ready]);
 useLayoutEffect(()=>{resizeComposer(composerRef.current);chatScroll.current?.update()},[draft,state?.selectedSessionId,shell.ready]);
 useEffect(()=>{const resize=()=>resizeComposer(composerRef.current);window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize)},[]);
 useEffect(()=>{
  if(!state)return;
  for(const m of notificationMessages(state)){
   if(lastNotify.current.has(m.id))continue;
   lastNotify.current.add(m.id);
   if(desktopNotificationsEnabled(state.notificationSettings) && connected && 'Notification'in window && Notification.permission==='granted' && (document.hidden||m.sessionId!==state.selectedSessionId)){
    const notice=new Notification('Amplifier',{body:notificationBody(m,state.notificationSettings),icon:logo,tag:m.id});
    notice.onclick=()=>{window.focus();act('session.select',{id:m.sessionId});act('view.update',{patch:{mode:'text'}})};
   }
  }
 },[state,connected,act]);
 useEffect(()=>{voiceClient.current=new VoiceClient({request,onAction:(name,args)=>dispatch(name,args),onState:value=>{setVoice(value);if(value.status!=='connected')visualClient.current?.stop()},onError:reportError});visualClient.current=new VoiceVisualClient({request,onState:setVisual,getVoice:()=>({...voiceClient.current?.state,sessionId:latest.current?.voice?.sessionId})});computerClient.current=new ComputerVisualClient({request,onState:setComputerVisual,getSession:()=>latest.current?.selectedSessionId});return()=>{computerClient.current?.dispose();visualClient.current?.dispose();voiceClient.current?.dispose()}},[]);
 useEffect(()=>{computerClient.current?.sync(null,connected,state?.computerVisual)},[connected,state?.computerVisual,state?.selectedSessionId]);
 useEffect(()=>{visualClient.current?.sync(voice,connected,state?.voice?.visual)},[voice,connected,state?.voice?.visual]);
 useEffect(()=>{voiceClient.current?.sync(state?.voice,connected)},[state?.voice,connected,voice.id]);
 const modeChange=m=>act('view.update',{patch:{mode:m}});
 const open=p=>{if(p==='new-session'){newChat();return;}if(!latest.current?.view?.panel)panelReturnFocus.current=document.activeElement;setError('');act('view.update',{patch:{panel:p,toolbarMenuOpen:false,...(p==='settings'&&window.matchMedia('(max-width:959px)').matches?{settingsSection:'index',settingsExpanded:[]}: {})}})};
 useEffect(()=>{
  const shortcut=event=>{
   if(event.key!==','||event.altKey||event.shiftKey||event.isComposing)return;
   const mac=/Mac|iPhone|iPad/.test(navigator.platform||'');
   if(!(mac?event.metaKey&&!event.ctrlKey:event.ctrlKey&&!event.metaKey))return;
   event.preventDefault();
   if(!latest.current)return;
   if(!latest.current.view?.panel){
    const active=document.activeElement;
    // Add's menu unmounts when Settings takes ownership. Return to its lasting
    // trigger, not a detached menu row, only after the Settings guard closes.
    panelReturnFocus.current=active?.closest('.a-composer-add-panel')?root.current?.querySelector('.a-compose-add>button[aria-haspopup="dialog"]')||active:active;
   }
   act('view.update',{patch:{panel:'settings',toolbarMenuOpen:false,
    ...(window.matchMedia('(max-width:959px)').matches?{settingsSection:'index',settingsExpanded:[]}: {})}});
  };
  window.addEventListener('keydown',shortcut);
  return()=>window.removeEventListener('keydown',shortcut);
 },[act]);
 const dismissPanel=()=>{setPreview(false);act('view.update',{patch:{panel:null,themePreview:false}})};
 const close=()=>settingsNavigation.current?settingsNavigation.current.close():dismissPanel();
 useEffect(()=>{
  if(!panel||!shell.ready)return;
  const previous=panelReturnFocus.current||document.activeElement;
  const dialog=root.current?.querySelector('.a-overlay [role=dialog]');
  if(!dialog)return;
  const controls=()=>[...dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],summary')].filter(el=>el.getClientRects().length);
  if(!dialog.contains(document.activeElement)||!document.activeElement?.getClientRects().length)controls()[0]?.focus();
  const handler=e=>{
   if(e.key==='Escape'){if(settingsNavigation.current)settingsNavigation.current.close();else {setPreview(false);act('view.update',{patch:{panel:null,themePreview:false}})}}
   if(e.key==='Tab'){const items=controls(),first=items[0],last=items.at(-1);if(!items.includes(document.activeElement)){e.preventDefault();(e.shiftKey?last:first)?.focus()}else if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}}
  };
  window.addEventListener('keydown',handler);
  return()=>{window.removeEventListener('keydown',handler);panelReturnFocus.current=null;if(previous?.isConnected)previous.focus()};
 },[panel,act,shell.ready]);
 function preserveOtherDraft(sessionId){
  const payload=stagedDraftPayload.current;
  if(!stagedDraft.current||payload?.sessionId===sessionId)return;
  // A new chat's edit/send must not cancel the previous chat's unsaved debounce.
  // Queue its explicitly bound save without blocking independent navigation.
  clearTimeout(draftTimer.current);saveDraft(payload,stagedDraft.current).catch(e=>reportError(e));
  stagedDraft.current=null;
 }
 function saveDraft(payload,token){
  if(draftSaves.current.has(payload))return draftSaves.current.get(payload);
  const save=(async()=>{
  // Autosave may become ready before the first conversation has an ID. Keep its
  // original optimistic position, then save against the conversation it created.
  if(payload.sessionId===null&&creatingSession.current){const current=await creatingSession.current;payload.sessionId=current.id;pendingView.current.bindDraft(token,current.id);draftJournal.bind(draftJournalEntries.current.get(payload),current.id)}
  try{const result=await dispatch('view.update',payload,{pendingViewToken:token});if(draftJournal.acknowledge(draftJournalEntries.current.get(payload)))setDraftStorageError(false);return result}
  catch(error){
   // A failed autosave must leave the current unsent text available for retry.
   if(stagedDraft.current===token){stagedDraft.current=pendingView.current.add(payload.patch,payload.sessionId);if(latest.current)setState(pendingView.current.apply(latest.current))}
   throw error;
  }
  finally{if(stagedDraft.current===token)stagedDraft.current=null}
  })();
  draftSaves.current.set(payload,save);save.catch(()=>draftSaves.current.delete(payload));return save;
 }
 function editDraft(value,documentValue){
  if(referenceDraftLock.current||latest.current?.draftNavigationPending)return;
  if(documentValue===undefined&&value&&quoteApi.current?.document()){
   if(value.startsWith(lastDraft.current)){try{quoteApi.current.append(value.slice(lastDraft.current.length));}catch(error){reportError(error)}return;}
   setError('Quoted content was kept. Edit its commentary in the composer.');return;
  }
  documentValue??=null;
  const sessionId=latest.current?.selectedSessionId??null;preserveOtherDraft(sessionId);setDraft(value);lastDraft.current=value;pendingView.current.settle(stagedDraft.current);const patch={draft:value,draftDocument:documentValue},token=pendingView.current.add(patch,sessionId),payload={patch,sessionId};const journalEntry=draftJournal.stage(sessionId,value,documentValue);draftJournalEntries.current.set(payload,journalEntry);setDraftStorageError(!journalEntry.persisted);stagedDraft.current=token;stagedDraftPayload.current=payload;if(latest.current)setState(pendingView.current.apply(latest.current));clearTimeout(draftTimer.current);draftTimer.current=setTimeout(()=>{saveDraft(payload,token).catch(e=>reportError(e))},220)
 }
 async function newChat(workspace){preserveOtherDraft(null);stickToBottom.current=true;await act('session.draft',workspace&&typeof workspace==='object'?workspace:workspace?{workspace}:{})}
 function creationSetup(){return {...newChatSetup(pendingView.current.apply(latest.current)),expectedDraftId:latest.current?.attachmentDraftId}}
 async function ensureSession(creation){const current=!creation&&latest.current?.sessions?.find(row=>row.id===latest.current?.selectedSessionId);if(current)return current;if(!creatingSession.current)creatingSession.current=dispatch('session.create',{...(creation?.setup||creationSetup()),fromDraft:true},{id:creation?.id}).catch(error=>{const created=creation?.id&&latest.current?.sessions?.find(row=>row.creationCommandId===creation.id);if(created)return {sessionId:created.id};throw error}).then(result=>{
  const created={id:result.sessionId||result.state.selectedSessionId},payload=stagedDraftPayload.current;
  if(stagedDraft.current&&payload?.sessionId===null){payload.sessionId=created.id;pendingView.current.bindDraft(stagedDraft.current,created.id);draftJournal.bind(draftJournalEntries.current.get(payload),created.id);clearTimeout(draftTimer.current);saveDraft(payload,stagedDraft.current).catch(e=>reportError(e));if(latest.current)setState(pendingView.current.apply(latest.current))}
  return created;
 }).finally(()=>{creatingSession.current=null});return creatingSession.current}
 function addFiles(files){
  if(!files?.length||executionUnavailable||historyPending)return;
  const target=latest.current?.selectedSessionId??null,selected=Array.from(files),expectedDraftId=target===null?latest.current?.attachmentDraftId:undefined;
  const admittedClient=latest.current?.client?.id,admittedHost=latest.current?.client?.hostInstanceId;
  if(target===null&&!expectedDraftId){setError('The new-chat draft is not ready. Try attaching again after it loads.');return;}
  uploadCount.current++;setUploading(true);setError('');
  const run=async()=>{let index=0;try{
   for(;index<selected.length;index++){
    const file=selected[index],base64=await readAttachment(file);
    if(admittedClient!==latest.current?.client?.id||admittedHost!==latest.current?.client?.hostInstanceId)throw Error('The connection changed before this file was uploaded. Choose the remaining files again in the intended chat.');
    await dispatch('attachment.add',{sessionId:target,...(expectedDraftId?{expectedDraftId}:{}),name:file.name,base64,...(file.attachmentSource?{source:file.attachmentSource}:{})});
   }
  }catch(error){
   // Keep uncertain transport outcomes in the existing recovery flow. For a
   // known file failure, explain which selection stopped this upload batch.
   if(transportFailure(error))reportError(error);
   else{const remaining=selected.length-index-1;setError(`${selected[index].name}: ${actionErrorMessage(error)}${remaining?` ${remaining} remaining file${remaining===1?' was':'s were'} not uploaded.`:''}`)}
  }finally{uploadCount.current--;setUploading(uploadCount.current>0)}};
  uploadQueue.current=uploadQueue.current.then(run,run);
 }
 const chatDrop=useChatFileDrop({ownerKey:JSON.stringify([session?.id??null,state?.attachmentDraftId,state?.selectedWorkspaceId]),enabled:!!state&&workSurface(state)==='chat'&&!executionUnavailable&&!historyPending&&!referencingDraft,onFiles:addFiles,onError:setError});
 async function deliver(entry){
  if(deliveries.current.has(entry.id))return;
  deliveries.current.add(entry.id);outbox.update(entry.id,{status:'sending',error:''});
  try{
   if(!entry.sessionId){const current=await ensureSession(entry.creation);entry=outbox.update(entry.id,{sessionId:current.id});}
   const result=await dispatch('conversation.send',{sessionId:entry.sessionId,text:entry.text,...(entry.document?{document:entry.document}:{}),...(entry.allowNextTurn?{allowNextTurn:true}:{}),...(entry.expectedGenerationId?{expectedGenerationId:entry.expectedGenerationId}:{}),...(Number.isInteger(entry.expectedConfigurationRevision)?{expectedConfigurationRevision:entry.expectedConfigurationRevision}:{}),...(entry.replyTo?.id?{replyId:entry.replyTo.id}:{}),attachmentIds:entry.attachmentIds,via:entry.via,preserveDraft:true},{id:entry.commandId,feedback:false});
   outbox.update(entry.id,['sending','unknown'].includes(result.delivery)?{status:'unknown',error:'Delivery has not been confirmed. Checking again will not run it twice.'}:null);
  }catch(error){
   const received=latest.current?.sessions?.find(row=>row.id===entry.sessionId)?.messages?.some(row=>row.inputId===entry.commandId&&['accepted','queued_for_update','held'].includes(row.delivery?.status));
   if(received&&error.code!=='session_busy')outbox.update(entry.id,null);
   else outbox.update(entry.id,{status:error.receipt?.delivery==='failed'||error.status>=400&&error.status<500&&error.status!==408?'failed':'unknown',error:actionErrorMessage(error)});
  }finally{deliveries.current.delete(entry.id)}
 }
 function discardMessage(message){
  const entry=outbox.current.current.find(row=>row.id===message.localDelivery?.id);
  if(!entry||entry.status!=='failed'||deliveries.current.has(entry.id))return;
  const saved=latest.current?.sessions?.find(row=>row.id===entry.sessionId)?.messages?.some(row=>row.inputId===entry.commandId);
  if(saved)return;
  outbox.update(entry.id,null);
  if(latest.current?.view?.messageEdit?.messageId===message.id)act('view.update',{patch:{messageEdit:null}});
 }
 async function retryMessage(message,text){
  const previous=outbox.current.current.find(row=>row.id===(message.localDelivery?.id||message.id));if(!previous||previous.status==='sending')return;
  if(previous.document&&text!==undefined){setError('Retry this quoted message unchanged, or discard it and compose a correction.');return;}
  if(previous.status==='unknown'&&text!==undefined)return;
  const creation=previous.status==='failed'&&!previous.sessionId?{id:crypto.randomUUID(),setup:creationSetup()}:previous.creation;
  const currentSession=latest.current?.sessions?.find(row=>row.id===previous.sessionId),currentGeneration=currentSession?.collaborationGeneration;
  const next=outbox.update(previous.id,{creation,...(previous.status==='failed'?{commandId:crypto.randomUUID(),allowNextTurn:true,expectedGenerationId:currentGeneration&&!currentGeneration.terminal?currentGeneration.id:null,expectedConfigurationRevision:currentSession?.nextMessageRevision??0,...(text!==undefined?{text}:{})}:{}),status:'sending',error:''});
  if(text!==undefined)act('view.update',{patch:{messageEdit:null}});
  await deliver(next);
 }
 async function send(e){
  dictationControl.current?.cancel();
  e?.preventDefault();if(referenceDraftLock.current)return;if((!draft.trim()&&!availableAttachments.length)||uploadCount.current||busy||newChatPending||historyPending||executionUnavailable)return;
  preserveOtherDraft(session?.id??null);
  const submittedText=draft,editorDocument=quoteApi.current?.document(),id=crypto.randomUUID(),attachments=availableAttachments;
  const submittedDocument=editorDocument?.segments.some(part=>part.type==='quote')?editorDocument:null;
  const activeGeneration=session?.collaborationGeneration;
  const expectedGenerationId=activeGeneration&&!activeGeneration.terminal?activeGeneration.id:null;
  if(!submittedText.trim()&&!attachments.length)return;
  if(detail.focused)detail.latest(false);
  chatScroll.current?.submitted(id);setError('');setQuoteNotice(notice=>quoteNoticeAfterSend(notice,session?.id));editDraft('');clearTimeout(draftTimer.current);
  const blankToken=stagedDraft.current;
  let entry=outbox.update(id,{commandId:id,sessionId:session?.id??null,allowNextTurn:true,...(expectedGenerationId?{expectedGenerationId}:{}),...(session?{expectedConfigurationRevision:session.nextMessageRevision??0}:{}),...(!session?{creation:{id:crypto.randomUUID(),setup:creationSetup()}}:{}),text:submittedText,...(submittedDocument?{document:submittedDocument,replyTo:{version:2,segments:submittedDocument.segments}}:state.view?.messageReply?{replyTo:state.view.messageReply}:{}),via:mode==='text'?'text':'chat',attachmentIds:attachments.map(file=>file.id),attachments,status:'sending',createdAt:Date.now()/1000});
  try{
   const clearingDraft=saveDraft(stagedDraftPayload.current,blankToken);
   // The outbox already owns this exact submitted text. A slow/ lost draft
   // receipt must not prevent delivery to an existing chat. Draft saves keep
   // their own ordered queue; the send preserves any newer server draft.
   if(session)clearingDraft.catch(error=>reportError(error));
   else await clearingDraft;
   const current=session||await ensureSession(entry.creation);entry=outbox.update(id,{sessionId:current.id});
   await deliver(entry);
  }catch(error){outbox.update(id,{status:error.receipt?.delivery==='failed'||error.status>=400&&error.status<500&&error.status!==408?'failed':'unknown',error:actionErrorMessage(error)});}
 }

 async function submitWorker(event){
  event.preventDefault();if(workerSubmission.current.busy||!workerDraft.trim())return;
  const revision=workerSubmission.current.revision,sessionId=latest.current?.selectedSessionId,instruction=workerDraft;
  workerSubmission.current.busy=true;setWorkerSending(true);setError('');
  try{
   await dispatch('worker.spawn',{sessionId,instruction});
   // An agent can replace this shared draft while the submission is pending.
   const currentDraft=pendingView.current.apply(latest.current)?.view?.workerDraft;
   if(workerSubmission.current.revision===revision&&latest.current?.selectedSessionId===sessionId&&currentDraft===instruction){
    setWorkerDraft('');
    await dispatch('view.update',{patch:{workerDraft:'',...(latest.current?.view?.panel==='worker'?{panel:null}:{})}});
   }
  }catch(error){reportError(error)}
  finally{workerSubmission.current.busy=false;setWorkerSending(false)}
 }
 async function startCall(){dictationControl.current?.cancel();if(state.voiceConfiguration?.available===false){await dispatch('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['voice']}});return}if(startingCall.current)return;startingCall.current=true;setVoiceStarting(true);setError('');try{await ensureSession();await dispatch('view.update',{patch:{mode:'call'}});await dispatch('call.start',{})}catch(e){reportError(e)}finally{startingCall.current=false;setVoiceStarting(false)}}
 const callActive=!['idle','ended','error'].includes(voice.status||'idle');
 // Preview is an exact host-validated client snapshot, not the mutable editor.
 // A committed change invalidates it immediately, before synchronization effects.
 const committedThemeKey=state?.theme?.name+'::'+state?.theme?.css;
 const previewSnapshot=preview&&loadedTheme.current===committedThemeKey&&state?.view?.themePreview&&typeof state.view.themeDraft==='string'
  ?{name:state.view.themeDraftName||'Custom skin',css:state.view.themeDraft}:null;
 const activeCss=previewSnapshot?.css??state?.theme?.css??'';
 const presentation=shell.composition.presentation;
 const requestedScheme=presentation.scheme||view.scheme;
 const scheme=requestedScheme==='system'?'light dark':requestedScheme||'light';
 const themeScheme=useThemeScheme(requestedScheme);
 useWindowChrome({root,state,shell,mode:themeScheme,css:activeCss});
 useAppearanceCache({root,state,shell,scheme:requestedScheme,mode:themeScheme,preview,css:activeCss});
 useLayoutEffect(()=>bindWindowControlsOverlay(root.current),[shell.ready,!!state]);
 const newChatPending=!session&&outbox.entries.some(row=>row.sessionId===null&&['sending','unknown'].includes(row.status));
 const responseActive=working||(session?.workers||[]).some(worker=>['queued','starting','working','running','stopping'].includes(worker.status));
 const sendingHere=outbox.entries.some(row=>row.sessionId===(session?.id??null)&&row.status==='sending');
 const primaryAction=composerPrimaryAction({draft,attachments:availableAttachments,uploading,working,responseActive,stopping:session?.status==='stopping',voiceStatus:voice.status,voiceStarting,busy,newChatPending,sending:sendingHere,historyPending,executionUnavailable});
 const appReload=useAppReload({client:streamClient,canvasWorkspace:state?.canvasWorkspace,blocked:callActive||voiceStarting?'Finish your voice call, then reload.':uploading||sendingHere||busy?'Wait for the current upload or send to finish.':'',prepare:async()=>{
  // Before a first state arrives there is no mounted editor to checkpoint.
  // The existing local draft journal remains intact across this recovery reload.
  if(!latest.current)return;
  await Promise.all([navigationQueue.current,commandQueue.current,capabilityCardQueue.current,sendQueue.current,uploadQueue.current]);
  await checkpointSurfaces(latest.current?.selectedSessionId);
  // Flush the composer before navigating away, including a just-typed debounce.
  clearTimeout(draftTimer.current);
  if(stagedDraft.current)await saveDraft(stagedDraftPayload.current,stagedDraft.current);
  const sessionId=latest.current?.selectedSessionId??null,value=lastDraft.current;
  await dispatch('view.update',{sessionId,patch:{draft:value}});
  if(value!==lastDraft.current||sessionId!==(latest.current?.selectedSessionId??null))throw Error('Your draft changed while saving. Select Reload now again when you’re ready.');
 }});
 const settingsState={...state,view:{...state?.view,settingsCanvas:canvasSettingsVisible}};
 const settingsAct=(name,args={},meta={})=>act(name,name==='view.update'&&canvasSettingsVisible&&args.patch?.panel==='settings'?{...args,patch:{...args.patch,panel:null,settingsCanvas:true}}:args,meta);
 const settingsSurface=<SettingsExperience state={settingsState} session={settingsSession} act={settingsAct} dispatch={dispatch} open={open} close={()=>canvasSettingsVisible?act('canvas.tabClose',{id:state.canvas.id}):dismissPanel()} navigationRef={settingsNavigation} appearance={<AppearanceSettings state={state} shell={shell} act={dispatch} editor={appearanceEditor} preview={!!previewSnapshot} previewSnapshot={previewSnapshot} setPreview={setPreview} defaultSkin={defaultSkin}/>}/>;
 const browsing=workSurface(state)!=='chat';
 const browse=(surface,id=null)=>act('view.update',{patch:{...browsePatch(surface,id),navExpanded:false}});
 const visitHistory=async key=>{
  const entry=destinationHistory.current.snapshot().entries.find(row=>row.key===key);
  if(!entry||destinationHistory.current.snapshot().pending)return;
  const [action,args]=destinationCommand(entry);
  try{await dispatch(action,args,{historyVisit:key})}catch(error){reportError(error)}
 };
 const visitRecent=async key=>{
  const entry=destinationHistory.current.snapshot().recent.find(row=>destinationKey(row)===key);
  if(!entry||destinationHistory.current.snapshot().pending)return;
  const [action,args]=destinationCommand(entry);
  try{await dispatch(action,args,{recentPlace:{entry,scope:serverState.current?.client?.historyScope}})}catch(error){reportError(error)}
 };
 if(!archiveAction.current)archiveAction.current=createArchiveAction({
  getState:()=>pendingView.current.apply(latest.current),getRevision:()=>archiveRevision.current,dispatch,
  onChange:setArchiveFeedback,
  navigate:async(patch,isCurrent,isActive)=>{
   if(!isCurrent()||settingsNavigation.current?.hasUnsaved())return {accepted:false};
   const revision=archiveRevision.current,id=latest.current?.selectedSessionId;
   // The browse itself paints an optimistic surface, so its queued guard uses
   // intent revision rather than comparing that surface with the old chat.
   const ownsNavigation=()=>isActive()&&archiveRevision.current===revision&&latest.current?.selectedSessionId===id;
   const result=await dispatch('view.update',{patch},{archiveIsCurrent:ownsNavigation});
   if(result.accepted&&ownsNavigation())requestAnimationFrame(()=>{
    if(!ownsNavigation())return;
    const heading=root.current?.querySelector('[data-part="work-browser"] h1');
    if(heading){heading.tabIndex=-1;heading.focus()}
   });
   return result;
  },
 });
 const archiveValue={...archiveFeedback,run:archiveAction.current.run,dismiss:archiveAction.current.dismiss};
 const workNavigation={browse,create:()=>setCreatingWorkspace(true),newChat,openRecordings,history:destinationTrail,visitHistory,visitRecent,
  sidebarView:view,updateSidebar:patch=>act('view.update',{patch}),
  home:(extra={})=>act('view.update',{patch:homePatch(presentation,extra,view)}),
  setPresentation:patch=>shell.setPresentation(patch).catch(reportError)};
 const previewVisible=!!peerPreview.active&&!browsing;
 const layoutState=browsing?{...settingsState,canvas:{...state?.canvas,open:false}}:previewVisible?{...settingsState,peerPreview:true,canvas:{...state?.canvas,open:true}}:settingsState;
 const renderMessage=(row,options={})=><MessageEntry key={row.id} message={row.message} session={session||{id:null}} state={state} act={act} stamp={nowLabel} working={working} forkTurn={turnEnds.get(row.id)} newest={newest.has(row.id)} retry={retryMessage} discard={discardMessage} dispatch={dispatch} {...options}/>;
 const renderRow=row=>row.kind==='artifacts'?<ArtifactLinks key={row.id} state={state} message={row.message} act={act} dispatch={dispatch}/>:row.kind==='work'?<ConversationWork key={row.id} row={row} data={execution} state={state} act={act} dispatch={dispatch} session={session} working={working} renderMessage={renderMessage}/>:row.kind==='peers'?<PeerRun key={row.id} row={row} session={session} state={state} renderMessage={renderMessage}/>:renderMessage(row);
 if(!state||!shell.ready)return <AppReloadContext.Provider value={appReload}><div className="boot"><AppReloadNotice/><img src={logo}/><h1>Amplifier</h1><p>{error||shell.error||'Connecting to your workspace…'}</p>{(error||shell.error)&&<><button onClick={()=>{setBootAttempt(value=>value+1);shell.refresh()}}>Retry connection</button><p><a href="?shell=recovery">Open recovery mode</a></p></>}</div></AppReloadContext.Provider>;
 return <PeerPresentation.Provider value={peerPresentation}><ArchiveContext.Provider value={archiveValue}><WorkNavigationContext.Provider value={workNavigation}><ShellContext.Provider value={shell}><AppReloadContext.Provider value={appReload}><div id="amp-one" {...appAppearanceState(state,connected,streamClient!==null)} data-work-surface={workSurface(state)} ref={root} data-layout={presentation.layout||view.layout||'balanced'} data-interface-detail={presentation.interfaceDetail||presentation.executionDetail||'standard'} data-execution-detail={presentation.interfaceDetail||presentation.executionDetail||'standard'} data-density={presentation.density||'comfortable'} style={{colorScheme:scheme,...(presentation.accent?{'--a-accent':presentation.accent}:{})}} data-theme-scheme={themeScheme} data-decorations={presentation.decorations===false?'off':'on'} data-part="app">
  {activeCss&&<style>{activeCss}</style>}<ButtonTooltips rootRef={root}/>
  <style>{responsiveNavigation}</style><style>{workShellCss}</style><style>{windowControlsOverlayCss}</style><style>{chatPresentationCss}</style><style>{mainRefreshCss}</style><style>{canvasExperienceCss}</style><style>{compactComposerCss}</style>
  <div className="a-window-chrome-blend" aria-hidden="true"/>

  <WorkHeader state={state} session={session} act={act} open={open} narrow={narrow} presentation={presentation} fileDropHandlers={browsing?undefined:chatDrop.handlers}/>
  <AppReloadNotice/>
  {!panel&&<ArchiveNotice value={archiveValue}/>}
  {chatLink?.status==='failed'&&<div className="a-alert" role="alert" data-chat-link-failure><span>Couldn’t open chat: {actionErrorMessage(chatLink.error)}</span><button type="button" onClick={()=>chatLinkReceiver.current.retry()}>Retry opening chat</button><button type="button" aria-label="Dismiss chat link" onClick={()=>chatLinkReceiver.current.dismiss()}><X/></button></div>}
  <ConnectionNotice connected={connected} updates={state.updates} failure={connectionFailure} onRetry={()=>setBootAttempt(value=>value+1)} onDismiss={()=>setConnectionFailure(null)}/>
  {error&&<div className="a-alert" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={()=>{setError('');act('view.update',{patch:{notice:null}})}} data-action="view.update"><X/></button></div>}
  {!browsing&&<><ConversationStatus session={session} act={act}/><ConversationError state={state} session={session} act={act}/></>}{!state.runtime?.available&&<div className="a-alert a-runtime"><span><strong>Connect Amplifier to get started.</strong> {state.runtime?.error||'The Amplifier runtime is not installed. Install this app with its runtime dependencies, then relaunch.'}</span><button className="a-link" onClick={()=>open('settings')} data-action="view.update">Setup details <ChevronRight/></button></div>}
  <LiveChatActivity voice={voice} callSession={state.sessions.find(row=>row.id===state.voice?.sessionId)} callActive={callActive} act={act}><SharingIndicator sessionId={session?.id} visual={computerVisual} client={computerClient} dispatch={dispatch} onError={reportError}/></LiveChatActivity>
  <WorkspaceLayout appearanceScheme={themeScheme} appearanceCss={activeCss} state={layoutState} act={act} presentation={presentation}><WorkspaceRail footer={<AppFooter state={state} act={act} connected={connected} open={open}/>} shell={shell} state={state} session={session} act={act} selectSession={id=>act('session.select',{id})} newSession={newChat}/><section className={`a-conversation ${messages.length||historyDisplayPending||session?.historyError||executionUnavailable?'has-messages':'is-empty'}`} data-part="conversation" aria-label="Shared conversation" {...chatDrop.handlers}>{chatDrop.active&&<div className="a-chat-file-overlay" data-part="chat-file-drop" role="status"><div><strong>Drop to attach to {session?.title||'this draft'}</strong><p>Files join this draft. Nothing is sent.</p></div></div>}<WorkSurface shell={shell} state={state} act={act} dispatch={dispatch} navigationRef={workspaceSettingsNavigation}/>
   <MessageFocus session={session} focus={state.view?.messageFocus} detail={detail} ensureVisible={interim.ensureVisible}/><div className="a-messages" ref={messagesPane} data-part="messages" data-view-source={session?"conversation":undefined} data-session-id={session?.id??undefined} data-host-instance-id={state.client?.hostInstanceId} data-work-projection={workProjectionReceipt(session,state.client?.hostInstanceId)} role="log" aria-label="Conversation messages" aria-live="polite" aria-busy={historyDisplayPending||!!detail.busy}>{historyDisplayPending?<SessionHistoryControls session={{...session,historyLoading:!session?.historyError, messages:[],sharedHistoryOffset:0,historyActivity:null}} act={act}/>:<><div className="a-session-history">{detail.controls}</div><WorkProjectionStatus session={session} messages={messages}/><SessionHistoryControls session={session?.messageWindow?.offset>0?{...session,sharedHistoryOffset:0}:session} messages={messages} act={act} hideEarlier/>{session?.parentId&&<div className="a-chat-origin"><GitBranch/><span>{!isTopLevelChat(session)?'Subagent conversation':session.editOrigin?'Continued from an edited message':'Forked conversation'}</span><button type="button" className="a-link" data-action="session.select" disabled={!state.sessions.some(s=>s.id===session.parentId)} onClick={()=>act('session.select',{id:session.parentId})}>{!isTopLevelChat(session)?'Open parent chat':'Open original chat'}</button></div>}{!session&&conversationRows.length===0&&<NewChatSetup draft={draft} onChoose={value=>{if(lastDraft.current.trim())return;editDraft(value);composerRef.current?.focus()}}/>} {conversationRows.length===0&&!historyDisplayPending&&!session?.historyError&&!executionUnavailable?session&&<div className="a-empty"><img src={logo} alt=""/><h2>What shall we work on?</h2><p>Bring an idea, a question, or a file.</p></div>:conversationRows.map(renderRow)}{!voiceStream&&<StreamingMessage session={session} state={state}/>}{presentation.planPlacement==='inline'&&<ChatPlan session={session} state={state} act={act} placement="inline"/>}<Questions key={`questions:${session?.id}`} session={session} state={state} dispatch={dispatch} reviewState={questionReviewState}/><NewChatHandoffs key={`handoffs:${session?.id}`} state={state} dispatch={dispatch}/></>}<div ref={messagesEnd}/></div>
   {session&&!historyDisplayPending&&<ConversationRail paneRef={messagesPane} sessionId={session.id} messages={messages} onJump={id=>messages.some(row=>row.id===id)?chatScroll.current?.jump(id):detail.around(id)}/>}
   {(awayFromBottom||detail.focused)&&<div className="a-chat-jump"><button type="button" className="a-soft" aria-label="Jump to latest messages" onClick={()=>detail.focused?detail.latest():chatScroll.current?.reveal()}>↓ Latest messages</button></div>}
   <LiveActivity session={state.sessions.find(row=>row.id===session?.id)||session}/>
   {!historyDisplayPending&&<DependencyWaiting key={'waiting:'+session?.id} sessionId={session?.id} activity={state.selectedActivity} act={act}/>}
   <ApprovalPanel key={session?.id} session={session} act={act} dispatch={dispatch} canvasDraft={state.view?.canvasDraft} freshnessKey={`${connected}:${session?.interruptionRevision}:${session?.executionRevision}`}/>
   {(!presentation.planPlacement||presentation.planPlacement==='composer'||presentation.planPlacement==='active'&&responseActive)&&!historyDisplayPending&&<ChatPlan session={session} state={state} act={act}/> }
   <QuestionNotice session={session} paneRef={messagesPane}/>
   <form className="a-composer a-compact-composer" data-part="composer" data-action="conversation.send" onSubmit={send}>
    {draftStorageError&&<p role="alert" className="a-upload-status">This browser could not keep a recovery copy of your draft. Keep this tab open until it is saved.</p>}
    {outbox.storageError&&outbox.entries.length>0&&<p role="alert" className="a-upload-status">This browser could not save the pending message. Keep this tab open until delivery is confirmed.</p>}
    <ComposerOwnership session={session} runtimeAvailable={state.runtime?.available!==false} dispatch={dispatch}>
    {!session&&<ComposerWorkspace state={state} act={act}/>}
    <div className="a-compose-surface" data-part="composer-surface" data-expanded={composerExpanded({draft,attachments:availableAttachments,quote:state.view?.messageReply||state.view?.draftDocument?.segments?.some(part=>part.type==='quote')})}>
    <div className="a-compose-writing">
    <AttachmentStrip privateDraft sessionId={session?.id??null} items={availableAttachments} remove={id=>act('attachment.remove',{sessionId:session?.id??null,id})}/>{uploading&&<p role="status" className="a-upload-status">Uploading attachments…</p>}
    <ReplyPreview quote={state.view?.messageReply} sessionId={session?.id} dispatch={dispatch}/>
    <MarkdownComposer key={session?.id??'new-chat'} editorRef={composerRef} quoteApi={quoteApi} value={draft} document={state.view?.draftDocument} onChange={editDraft} onSend={send} onQuoteRemoved={left=>setQuoteNotice(notice=>quoteNoticeAfterRemoval(notice,session?.id,left))} onReveal={async(quote,button)=>{
     const sid=session?.id,owner=quoteApi.current;
     if(quote.sessionId!==sid)throw Error('This source is from another chat. Cross-chat quotation is not available.');
     const top=messagesPane.current?.scrollTop;
     const find=()=>[...document.querySelectorAll('[data-message-id]')].find(row=>row.dataset.messageId===quote.messageId);
     let node=find();if(!node){await interim.ensureVisible?.(quote.messageId);node=find();}
     if(!node){const found=await detail.around(quote.messageId,{isCurrent:()=>latest.current?.selectedSessionId===sid&&quoteApi.current===owner});if(!found)throw Error('Original unavailable. The saved quotation remains.');}
     if(latest.current?.selectedSessionId!==sid||quoteApi.current!==owner)return;
     setQuoteReturn({sid,top,button});if(node){node.tabIndex=-1;node.scrollIntoView({block:'center'});node.focus({preventScroll:true});}
     const source=session.messages?.find(row=>row.id===quote.messageId);
     if(!source||source.textDetail)return 'Saved quotation retained; source version could not be compared.';
     const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source.text||''));
     const digest=[...new Uint8Array(bytes)].map(n=>n.toString(16).padStart(2,'0')).join('');
     return digest===quote.sourceDigest?'Original revealed. Saved quotation is unchanged.':'Source changed since quoting. The earlier snapshot is retained.';
    }} onFiles={addFiles} disabled={executionUnavailable} readOnly={referencingDraft||!!state?.draftNavigationPending} pasteDisabled={historyPending}/>
    {visibleQuoteNotice(quoteNotice,session?.id)&&<small role="status">{visibleQuoteNotice(quoteNotice,session?.id)}</small>}
    {quoteReturn&&quoteReturn.sid===session?.id&&<button type="button" className="a-link" onClick={()=>{if(messagesPane.current)messagesPane.current.scrollTop=quoteReturn.top;const button=quoteReturn.button;setQuoteReturn(null);if(button?.isConnected&&button.getClientRects().length)button.focus({preventScroll:true});else composerRef.current?.focus({preventScroll:true});}}>Return to reading position</button>}
    </div>
    <div role="status" aria-live="polite" aria-atomic="true" className="a-sr-only">{sendingHere?'Sending message…':''}</div>
    <ComposerAdd sessionId={session?.id} panel={settingsRoute||null} disabled={uploading||executionUnavailable||historyPending} accessDisabled={browsing} onFiles={kind=>{fileInput.current.accept=kind==='image'?'image/*':'';fileInput.current.click()}} onAccess={()=>open('chat-write-files')}><input ref={fileInput} type="file" multiple className="a-file-input" aria-label="Attach files" data-action="attachment.add" onChange={e=>{addFiles([...e.target.files]);e.target.value=''}}/></ComposerAdd>
    <div className="a-compose-end"><DictationButton key={session?.id??'dictation-new'} draft={draft} onChange={value=>{if((latest.current?.selectedSessionId??null)===(session?.id??null))editDraft(value)}} controlRef={dictationControl} disabled={executionUnavailable||historyPending||referencingDraft||sendingHere||browsing||callActive}/><button type={primaryAction.action==='conversation.send'?'submit':'button'} className="a-send" data-part="composer-primary-action" data-action={primaryAction.action} aria-label={primaryAction.action==='call.start'&&state.voiceConfiguration?.available===false?'Set up voice':primaryAction.label} title={primaryAction.action==='call.start'&&state.voiceConfiguration?.available===false?'Set up voice in Settings':primaryAction.label} disabled={primaryAction.disabled||(referencingDraft&&primaryAction.action==='conversation.send')} onClick={primaryAction.action==='call.start'?startCall:primaryAction.action==='call.end'?()=>act('call.end'):primaryAction.action==='conversation.stop'?()=>act('conversation.stop',{sessionId:session.id}):undefined}>{primaryAction.icon==='voice'?<AudioLines/>:primaryAction.icon==='cancel'?<X/>:<ArrowUp/>}</button></div>
    </div>
    <div className="a-compose-configuration">
     {session&&responseActive&&!callActive&&primaryAction.action!=='conversation.stop'&&<button type="button" className="a-compose-stop" aria-label="Stop response" data-action="conversation.stop" disabled={session.status==='stopping'||executionUnavailable} onClick={()=>act('conversation.stop',{sessionId:session.id})}>{session.status==='stopping'?'Stopping…':'Stop'}</button>}
     <ShellSlot name="composer.actions"><ComposerOptions state={state} session={session} act={act} working={working} detail={presentation.interfaceDetail||presentation.executionDetail}/></ShellSlot>
    </div>
    </ComposerOwnership>
   </form>
  </section><McpAppThemeProvider scheme={requestedScheme}><AgentCanvas suppressed={browsing||previewVisible} state={settingsState} act={act} dispatch={dispatch} settingsSurface={settingsSurface} permissionHistoryCurrent={key=>{
   const intent=permissionHistoryFocus.current,current=latest.current;
   return intent?.key===key&&intent.revision===canvasInputRevision.current&&intent.sessionId===current?.selectedSessionId&&current?.canvas?.open&&current?.view?.canvasDraft?.library&&!current?.view?.settingsCanvas;
  }}/></McpAppThemeProvider>
  {previewVisible&&<PeerPreviewCanvas key={peerPreview.active.source.sourceId} active={peerPreview.active} onClose={peerPreview.close} state={layoutState} dispatch={dispatch}/>}</WorkspaceLayout>
  {creatingWorkspace&&<WorkspaceCreation state={state} act={act} onClose={()=>setCreatingWorkspace(false)}/>}
  <FeedbackNotice state={state} act={act}/>
  {panel&&<div className={`a-overlay ${panel==='session-details'?'a-chat-info-overlay':''} ${(panel==='settings'||panel==='appearance')?'a-settings-overlay a-settings-redesign-overlay':''}`} data-part="overlay" onPointerDown={e=>{outsidePointer.current=e.target===e.currentTarget}} onClick={e=>{if(outsidePointer.current&&e.target===e.currentTarget)close()}}><section role="dialog" aria-modal="true" aria-labelledby="panel-title" className={`a-dialog ${panel==='appearance'||panel==='agent'||panel==='runtime'||panel==='coordination'||panel==='automation'||Object.hasOwn(chatPageTitles,panel)||panel==='settings'?'wide':''}`} data-part="dialog"><div className="a-dialog-head"><h2 id="panel-title">{{appearance:'Settings',agent:'What the agent sees',settings:'Settings',activity:'Ready for you',automation:'Automation',feedback:'Send feedback',worker:'Give it a worker lane','delete-session':'Delete chat?',runtime:'Advanced',...chatPageTitles,'subagent-history':'Subagent history',coordination:'Chat coordination review'}[panel]||panel}</h2>{(panel==='settings'||panel==='appearance')&&<div className="a-settings-header-assistance" data-settings-help/>}<button className="a-icon" data-action="view.update" aria-label="Close panel" onClick={close}><X/></button></div>
   <ArchiveNotice value={archiveValue}/>
   {error&&<div className="a-alert" role="alert"><span>{error}</span><button type="button" aria-label="Dismiss error" onClick={()=>setError('')}><X/></button></div>}
   {(Object.hasOwn(chatPageTitles,panel)||panel==='runtime')&&<ChatPages key={session?.id||'draft'} page={panel} state={state} session={session} act={act} dispatch={dispatch} open={open} working={working} questionReviewState={questionReviewState} computerControls={<VoiceVisualControls sessionId={session?.id} visual={computerVisual} client={computerClient} dispatch={dispatch} onError={reportError}/>}/>}
    {panel==='coordination'&&<CoordinationPanel key={session?.id} dispatch={dispatch} sessionId={session?.id} session={session} onOpenChat={id=>selectWorkspaceChat(dispatch,id,target=>latest.current?.selectedSessionId===target&&latest.current?.view?.panel==='coordination')}/>}
   {panel==='subagent-history'&&<SubagentHistory state={state} session={session} act={act}/>}
   {panel==='automation'&&<AutomationHub state={state} dispatch={dispatch} isCurrent={id=>latest.current?.selectedSessionId===id&&pendingView.current.apply(latest.current)?.view?.panel==='automation'}/>}
   {panel==='activity'&&<ActivityPanel state={state} act={dispatch}/>}
   {panel==='feedback'&&<FeedbackPanel state={state} act={dispatch}/>}

   {panel==='worker'&&<form data-action="worker.spawn" onSubmit={submitWorker}><p>Tell the worker what to investigate or build. Results return to this conversation.</p><label htmlFor="worker-instruction">Work to delegate</label><textarea id="worker-instruction" value={workerDraft} data-action="view.update" onChange={e=>{workerSubmission.current.revision++;setWorkerDraft(e.target.value);act('view.update',{patch:{workerDraft:e.target.value}})}} placeholder="Review the project and propose a focused implementation plan…"/><div className="a-dialog-actions"><button className="a-primary" disabled={workerSending||!workerDraft.trim()}><GitBranch/>{workerSending?'Starting…':'Start worker'}</button></div></form>}

   {panel==='agent'&&<><p>Every app control uses the same action interface. The agent can read the selected conversation, worker lanes, open panels, and nonprivate controls. Unsent messages, message edits and attachments are excluded from ordinary readback. When you ask for help with a draft, the agent can inspect that draft in this chat by explicitly selecting its browser.</p><div className="a-form-grid"><div><label>Local browser state (includes private input)</label><pre data-private="true" className="a-state-view">{pretty({...state,devices:Object.fromEntries(Object.entries(state.devices||{}).map(([id,device])=>[id,{clientId:device.clientId,viewport:device.viewport,controls:device.controls?.length,visibleTextLength:device.visibleText?.length,voice:device.voice,notificationPermission:device.notificationPermission}])),deviceCommands:(state.deviceCommands||[]).map(({id,type,origin,createdAt})=>({id,type,origin,createdAt})),theme:{name:state.theme?.name,css:`${state.theme?.css?.length||0} characters`},view:{...view,themeDraft:view.themeDraft?`${view.themeDraft.length} characters`:undefined}})}</pre></div><div><label>Visible controls</label><pre data-private="true" className="a-state-view">{pretty(visibleView(root.current,clientId).controls?.map(c=>({label:c.label,action:c.action,disabled:c.disabled})))}</pre></div></div><label htmlFor="agent-action">Run an app action</label><select id="agent-action" value={agentAction} data-action="view.update" onChange={e=>{setAgentAction(e.target.value);act('view.update',{patch:{agentAction:e.target.value}})}}>{catalog.map(a=><option key={a.name||a.action} value={a.name||a.action}>{a.name||a.action}</option>)}</select><label htmlFor="agent-args">Arguments (JSON)</label><textarea id="agent-args" className="a-css-editor" style={{minHeight:100}} value={agentArgs} data-action="view.update" onChange={e=>{setAgentArgs(e.target.value);act('view.update',{patch:{agentArgs:e.target.value}})}}/><div className="a-dialog-actions"><button className="a-primary" data-action={agentAction} onClick={()=>{try{act(agentAction,JSON.parse(agentArgs))}catch(e){reportError(e)}}}>Run action</button><button className="a-soft" data-action="state.export" onClick={()=>act('state.export')}><Download/>Export app state</button></div><p className="a-caption">Also available to integrations as window.amplifier.getState(), getActions(), and dispatch().</p></>}
   {panel==='delete-session'&&<ChatDelete key={session?.id} id={session?.id} act={dispatch} cancel={close}/>}{(panel==='settings'||panel==='appearance')&&settingsSurface }
  </section></div>}
 </div></AppReloadContext.Provider></ShellContext.Provider></WorkNavigationContext.Provider></ArchiveContext.Provider></PeerPresentation.Provider>;
}
createRoot(document.getElementById('root')).render(<App/>);
