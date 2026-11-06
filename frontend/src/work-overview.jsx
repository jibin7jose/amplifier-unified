import React from 'react';
import {ChatPlan} from './chat-plan';
import {sessionStatus} from './session-status';
import {FileText,FolderOpen,Globe,GitBranch,Activity,Paperclip,Folder} from 'lucide-react';
import {SavedArtifacts,chatArtifacts} from './canvas-library';
import {SubagentHistoryButton} from './subagent-history';
import {CanvasOverviewSection} from './canvas-overview-state';
import {ChatRow} from './shell/navigation-components';
import {WorkspaceRow} from './workspace-explorer';
import {useActivityClock} from './navigation-details';
import {ContextEntry} from './context-inspector';

export function ChatOverview({state,act,changeDraft,onSectionChange=changeDraft,onFilter}){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId),outputs=chatArtifacts(state),draft=state.view?.canvasDraft||{},now=useActivityClock();
 const sources=[...new Map((session?.messages||[]).flatMap(row=>row.attachments||[]).map(row=>[row.id||row.name,row])).values()];
 const workers=session?.workers||[],activeWorkers=workers.filter(row=>['queued','starting','working','running','stopping'].includes(row.status));
 const finished=workers.filter(row=>!activeWorkers.includes(row)),total=session?.workerSummary?.total??workers.length,finishedCount=session?.workerSummary?.finished??finished.length,recent=session?.workerSummary?.recentFinished??finished.slice(-5);
 const attention=workers.filter(row=>['error','failed','blocked','awaiting_human'].includes(row.status)).length;
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 const workspaceRow=state.workspaceShortcuts?.find(row=>row.workspaceId===state.selectedWorkspaceId);
 // Reuse the already bounded navigation projection, never hydrate peer histories.
 const page=state.recentNavigation||{},chats=(page.items||[]).filter(row=>row.workspaceId===state.selectedWorkspaceId||row.workspace===workspace?.path).slice(0,4);
 const workspaceOpen=()=>act('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'chats'}});
 const workspaceBrowse=path=>act('view.update',{patch:{workSurface:'workspaces',navWorkspacePath:path}});
 const model={state,act,now,draft:state.view?.workspaceDraft||{},prefix:'overview-chat',choose:id=>act('session.select',{id}),setDraft:workspaceDraft=>act('view.update',{patch:{workspaceDraft}})};
 const section={draft,onChange:onSectionChange};
 return <div className="a-chat-overview">
  <ChatPlan session={session} state={state} act={act} placement="overview" draft={draft} onSectionChange={onSectionChange}/>
  <ContextEntry session={session} act={act}/>
  <CanvasOverviewSection {...section} id="outputs" title="Outputs" icon={FileText} summary={`${outputs.length} saved`}>
   {outputs.length?<SavedArtifacts state={state} act={act} compact onFilter={onFilter}/>:<p>Files and visuals from this chat will appear here.</p>}
   <div className="a-overview-actions"><button className="a-link" type="button" onClick={()=>changeDraft({open:true,browser:false,library:true})}><FolderOpen/>Open file</button><button className="a-link" type="button" onClick={()=>changeDraft({browser:true,open:false,library:true})}><Globe/>Open website</button></div>
  </CanvasOverviewSection>
  {!!sources.length&&<CanvasOverviewSection {...section} id="sources" title="Sources" icon={Paperclip} summary={sources.length}><ul>{sources.map(row=><li key={row.id||row.name}>{row.name||'Attachment'}</li>)}</ul><small>Attachments in loaded messages.</small></CanvasOverviewSection>}
  {!!total&&<CanvasOverviewSection {...section} id="agents" title="Agents" icon={GitBranch} summary={attention?`${attention} needs attention`:activeWorkers.length?`${activeWorkers.length} working`:`${finishedCount} finished`}>
   <ul>{activeWorkers.map(row=><li key={row.id}>{row.name||row.instruction||'Worker'} <small>{row.status}</small></li>)}</ul>
   {attention>0&&<p role="status">An agent observation needs attention. Its outcome is retained below.</p>}
   <details className="a-overview-history"><summary>{finishedCount} finished observations</summary><ul>{recent.map(row=><li key={row.id}>{row.name||'Worker'} · {row.status}</li>)}</ul><SubagentHistoryButton state={state} session={session} act={act}/></details>
  </CanvasOverviewSection>}
  {workspace&&<CanvasOverviewSection {...section} id="workspace" title="Workspace" icon={Folder} summary={workspace.name}>
   {workspaceRow?<WorkspaceRow row={workspaceRow} state={state} act={act} now={now} open={workspaceOpen} browse={workspaceBrowse} landing/>:<button type="button" className="a-link" onClick={workspaceOpen}>{workspace.name}</button>}
   {chats.length>0&&<div className="a-overview-chats" aria-label="Recent chats in this workspace">{chats.map(chat=><ChatRow key={chat.id} chat={chat} model={model} now={now} showLocation={false} overview/>)}</div>}
   <div className="a-overview-actions"><button className="a-link" type="button" onClick={workspaceOpen}>View workspace chats</button><button className="a-link" type="button" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:'coordination'}})}>Chat coordination review</button></div>
  </CanvasOverviewSection>}
  <CanvasOverviewSection {...section} id="activity" title="Activity" icon={Activity} summary={sessionStatus(session).label}><p>{sessionStatus(session).label}</p><button className="a-link" type="button" onClick={()=>act('view.update',{patch:{panel:'session-details'}})}>Chat info</button></CanvasOverviewSection>
 </div>;
}
