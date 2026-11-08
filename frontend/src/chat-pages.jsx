import {RecallSettings} from './recall';
import {MaintenanceSettings} from './maintenance';
import {ChatFileAccess} from './chat-file-access';
import {AccessMode} from './access-mode';
import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,ChevronRight} from 'lucide-react';
import {ConversationName,ConversationDetails,ConversationFailure} from './conversation-controls';
import {ConversationUsage} from './conversation-usage.jsx';
import {ConversationExport} from './conversation-export.jsx';
import {ConversationSharing} from './conversation-library';
import {CopyDetail} from './navigation-details';
import {sessionIdentity} from './navigation-presentation';
import {Questions} from './questions';
import {FeedbackDiagnostics,FeedbackPanel} from './feedback';
import {settingsPatch} from './settings-navigation';
import {RuntimeSettings} from './runtime-settings';
import {ScheduleVisibilityContext} from './schedules';
import {DesktopReadiness} from './desktop-readiness';
import {BundleControl} from './bundle-controls';
import {BundleSettings} from './bundles';
import {CapacityControls} from './capacity';
import {SettingsPageContext} from './settings-ui';
import {SubagentHistoryButton} from './subagent-history';
import {openContextInspector} from './context-inspector';
import './chat-pages.css';

export const chatPageTitles={
 'chat-error':'Error details','session-details':'Chat info','chat-rename':'Rename','chat-share':'Share or save',
 'chat-help':'Help & diagnostics','chat-advanced':'Advanced',
 'chat-work':'Work & automation','chat-models':'Model & tools',
 'chat-memory':'Memory & past work','chat-files':'Project file access','chat-write-files':'Access & approvals','chat-context':'Context & recovery','chat-computer':'Computer access',
};
const groups=[
 ['chat-work','Work & automation','Saved tasks, goals, schedules, workers and execution folders','direction'],
 ['chat-models','Model & tools','Tools, skills, bundles and budgets','tools'],
 ['chat-context','Context & recovery','Context limits, provider support and recovery','limits'],
 ['chat-computer','Computer access','Screen sources, permission and capture','computer'],
];
function Info({session,state,act,open,questionReviewState}){
 const [report,setReport]=useState(null),[error,setError]=useState('');
 useEffect(()=>{let alive=true;act('session.inspect',{id:session.id}).then(response=>{
  if(!alive)return;
  if(response?.accepted===false)throw Error(response.error||'Could not read locations.');
  setReport(response?.result||null);
 }).catch(error=>{if(alive)setError(error.message)});return()=>{alive=false}},[session.id]);
 const identity=sessionIdentity(session);
 return <div className="a-chat-info">
  <CopyDetail label="Session ID" value={identity}/>
  {(session.failure||session.error||session.moduleFailures?.length>0)&&<ConversationFailure session={session}/>}
  <dl className="a-chat-facts"><div><dt>Status</dt><dd>{session.status}</dd></div>{session.createdAt&&<div><dt>Created</dt><dd>{new Date(session.createdAt*1000).toLocaleString()}</dd></div>}{session.bundle&&<div><dt>Bundle</dt><dd>{session.bundle}</dd></div>}</dl>
  {session.workspace&&<CopyDetail label="Workspace / saved settings home" value={session.workspace}/>}
  <button type="button" className="a-link" onClick={()=>openContextInspector(act,session)}>Inspect last observed model request</button>
  <details><summary>Call receipts &amp; provider quota</summary><CapacityControls session={session} act={act} readOnly/></details>
  <details><summary>Locations &amp; identifiers</summary>
   {identity!==session.id&&<CopyDetail label="App record ID" value={session.id}/>}
   {report?.historyDirectory&&<CopyDetail label="History folder on disk" value={report.historyDirectory}/>}
   {(report?.executionDirectory||session.workingDirectory||session.workspace)&&<CopyDetail label="Execution folder" value={report?.executionDirectory||session.workingDirectory||session.workspace}/>}
   {error&&<p role="alert">{error}</p>}
  </details>
  <details><summary>Questions &amp; answers</summary><Questions session={session} state={state} dispatch={act} reviewState={questionReviewState} history/></details>
  <button type="button" className="a-link" onClick={()=>open('chat-write-files')}>Access &amp; approvals</button>
  <ConversationUsage sessionId={session.id} act={act}/>
 </div>;
}
function Help({state,act,open}){
 return <><p>Review a diagnostic report to share with support, or report a problem.</p>
  <FeedbackDiagnostics state={state} act={act} downloadable/>
  <div className="a-dialog-actions"><button type="button" className="a-soft" onClick={()=>open('feedback')}>Report a problem</button><button type="button" className="a-soft" onClick={()=>act('view.update',{patch:settingsPatch('diagnostics')})}>Diagnostic recording settings</button></div>
 </>;
}
function ErrorFeedback({state,act}){
 const [opened,setOpened]=useState(false);
 return <details onToggle={event=>setOpened(event.currentTarget.open)}><summary>Report this problem</summary><p>Describe what happened here. Diagnostics are optional; nothing is submitted until you choose a delivery action.</p>{opened&&<FeedbackPanel state={state} act={act}/>}</details>;
}
// Keep already opened forms mounted while navigating within Chat. This preserves
// unsaved local edits without opening or loading controls the user has not visited.
export function ChatPages({page,state,session,act,dispatch=act,open,working,computerControls,questionReviewState}){
 const visited=useRef(new Set());visited.current.add(page);
 const legacyTab=state.view?.runtimeDraft?.tab||'overview';
 const group=groups.find(row=>row[0]===page)||(page==='runtime'?groups.find(row=>row[3]===legacyTab)||groups[1]:null);
 const runtimeSeen=useRef(new Set());if(group)runtimeSeen.current.add(group[0]);
 const show=key=>visited.current.has(key);
 const back=<button type="button" className="a-link a-chat-back" onClick={()=>open('chat-advanced')}><ArrowLeft/>Back to Advanced</button>;
 if(!session)return <p>Select a chat to see its information and options.</p>;
 return <div className="a-chat-pages">
  {show('chat-error')&&<div hidden={page!=='chat-error'}><ConversationDetails key={session.id} session={session} act={dispatch} focused open={open}/>{page==='chat-error'&&<ErrorFeedback state={state} act={dispatch}/>}</div>}
  {show('session-details')&&<div hidden={page!=='session-details'}><Info key={session.id} session={session} state={state} act={dispatch} open={open} questionReviewState={questionReviewState}/></div>}
  {show('chat-rename')&&<div hidden={page!=='chat-rename'}><ConversationName session={session} act={dispatch} details={false}/></div>}
  {show('chat-share')&&<div hidden={page!=='chat-share'}><details open><summary>Save to a file</summary><ConversationExport session={session} state={state} act={act}/></details><details><summary>Share a copy</summary><ConversationSharing session={session} act={act}/></details></div>}
  {show('chat-help')&&<div hidden={page!=='chat-help'}><Help state={state} act={act} open={open}/></div>}
  {show('chat-memory')&&<div hidden={page!=='chat-memory'}><RecallSettings session={session} act={act}/></div>}
  {show('chat-write-files')&&<div hidden={page!=='chat-write-files'}><AccessMode key={'access-'+session.id} session={session} act={act}/><details><summary>File restrictions when asking for permission</summary><ChatFileAccess key={session.id} state={state} session={session} act={act}/></details></div>}
  {show('chat-files')&&<div hidden={page!=='chat-files'}><p>Shared by chats in this workspace. To change only this chat, use <button type="button" className="a-link" onClick={()=>open('chat-write-files')}>Access &amp; approvals</button>.</p><SettingsPageContext.Provider value="permissions"><MaintenanceSettings state={state} act={act} projectOnly/></SettingsPageContext.Provider></div>}
  {page==='chat-advanced'&&<nav className="a-chat-destinations" aria-label="Advanced chat options"><button type="button" onClick={()=>open('chat-memory')}>Memory &amp; past work<ChevronRight/></button><button type="button" onClick={()=>open('chat-write-files')}>Access &amp; approvals<ChevronRight/></button><button type="button" onClick={()=>open('chat-files')}>Project file access<ChevronRight/></button>{groups.map(([id,title,description])=><button type="button" key={id} onClick={()=>open(id)}><span><strong>{title}</strong><small>{description}</small></span><ChevronRight/></button>)}</nav>}
  {groups.filter(row=>runtimeSeen.current.has(row[0])).map(activeGroup=><div key={activeGroup[0]} hidden={group?.[0]!==activeGroup[0]}>{back}{page==='runtime'&&<h3>{activeGroup[1]}</h3>}<ScheduleVisibilityContext.Provider value={group?.[0]===activeGroup[0]}><RuntimeSettings state={state} session={session} act={act} scheduleAct={dispatch} group={activeGroup[3]} computerControls={computerControls} extras={<>
   {activeGroup[3]==='direction'&&<section className="a-settings-section"><h3>Tasks &amp; workers</h3><div className="a-dialog-actions"><SubagentHistoryButton state={state} session={session} act={act}/><button type="button" className="a-soft" onClick={()=>open('coordination')}>Tasks and workers</button><button type="button" className="a-soft" onClick={()=>open('worker')}>Delegate work</button></div></section>}
   {activeGroup[3]==='tools'&&<><details><summary>Conversation bundle</summary><BundleControl state={state} session={session} act={act} working={working}/></details><details><summary>Conversation modules</summary><SettingsPageContext.Provider value="loaded-modules"><BundleSettings state={state} session={session} act={act}/></SettingsPageContext.Provider></details><details><summary>Save &amp; share a bundle</summary><SettingsPageContext.Provider value="share-bundle"><BundleSettings state={state} session={session} act={act}/></SettingsPageContext.Provider></details></>}
   {activeGroup[3]==='computer'&&<DesktopReadiness state={state} session={session} act={act}/>}
   {activeGroup[3]==='limits'&&<details><summary>Recovery &amp; recorded errors</summary><ConversationDetails session={session} act={act}/></details>}
  </>}/></ScheduleVisibilityContext.Provider></div>)}
 </div>;
}
