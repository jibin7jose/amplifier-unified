import React,{useEffect,useId,useRef,useState} from 'react';
import {useWorkspaceChats,WorkspaceChatChoices} from './workspace-chat-choices';
import {TaskStorage} from './workspace-task-storage';
import {TaskEnvironments} from './workspace-test-environments';

export function ManagedResources({workspace,state,act,active,readOnly}){
 const selectId=useId(),visit=useRef(0);
 const [opened,setOpened]=useState(false),[page,setPage]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [cursors,setCursors]=useState({});
 const choices=useWorkspaceChats(workspace,active&&opened,state.selectedSessionId),sid=choices.selectedId;
 const refresh=async(next={})=>{
  const current=++visit.current;setBusy(true);setError('');
  try{
   const outer=await act('workspace.resources.managed',{workspaceId:workspace.id,sessionId:sid,limit:25,...next});
   const value=outer?.result?.accepted!==undefined?outer.result:outer;
   if(!value||value.accepted===false)throw Error(value?.error||'Managed resources unavailable.');
   if(current===visit.current){setPage(value.result);setCursors(next)}
  }catch(err){if(current===visit.current)setError(err.message)}
  finally{if(current===visit.current)setBusy(false)}
 };
 useEffect(()=>{
  setPage(null);setError('');setBusy(false);setCursors({});
  if(active&&opened&&sid)refresh();
  return()=>{visit.current++};
 },[active,opened,workspace.id,sid]);
 const owner=choices.selected?.title||'Conversation';
 return <details className="a-workspace-setup" onToggle={event=>{if(!event.target||event.target===event.currentTarget)setOpened(event.currentTarget.open)}}>
  <summary>Managed resources</summary>
  <section aria-label="Managed resources">
   <p>Inspect resources recorded for one chat. Refreshing observes their owners; it does not start, stop or reserve anything.</p>
   <WorkspaceChatChoices choices={choices} id={selectId} label="Resources for chat"/>
   <button className="a-soft" type="button" disabled={busy||!sid} onClick={()=>refresh()}>Refresh managed resources</button>
   {error&&<p role="alert">{error}</p>}{busy&&<p role="status">Reading resource owners…</p>}
   {page&&<>
    <h4>Local publishing</h4>
    {page.publishing.items.length===0&&<p>No local publishing listeners on this page.</p>}
    <ul>{page.publishing.items.map(row=><li key={row.kind+row.id} data-managed-resource={row.id} style={{overflowWrap:'anywhere'}}>
     <strong>{row.kind==='preview'?'Static preview':'Published site'} · {row.status}</strong>
     <p>Owner: {owner} · Publishing</p>
     {row.port!=null&&<p>Bound port: {row.port}</p>}
     {row.url&&<p><a href={row.url} target="_blank" rel="noreferrer">Open local preview</a></p>}
     {row.releaseId&&<p>Release: {row.releaseId}</p>}
     {row.releaseDirectory&&<p>Served directory: {row.releaseDirectory}</p>}
    </li>)}</ul>
    {page.publishing.nextCursor&&<button className="a-soft" type="button" disabled={busy} onClick={()=>refresh({...cursors,publishingBefore:page.publishing.nextCursor})}>More publishing resources</button>}
    <h4>Recorded processes and kernels</h4>
    <p>These are the latest retained lifecycle observations, not a new process health check. Process working directories are unavailable here. Use the chat’s Operations controls to inspect or stop owned work.</p>
    {page.operations.items.length===0&&<p>No recorded operations on this page.</p>}
    <ul>{page.operations.items.map(row=><li key={row.id} data-managed-operation={row.id} style={{overflowWrap:'anywhere'}}>
     <strong>{row.kind} · {row.state.replaceAll('_',' ')}</strong><p>Owner: {owner} · Operations</p>
     <p>Operation: {row.id}{row.pid!=null&&' · PID '+row.pid}{row.returncode!=null&&' · Exit '+row.returncode}</p>
    </li>)}</ul>
    {page.operations.nextCursor&&<button className="a-soft" type="button" disabled={busy} onClick={()=>refresh({...cursors,operationsBefore:page.operations.nextCursor})}>More recorded operations</button>}
    <TaskStorage key={sid} workspaceId={workspace.id} sessionId={sid} initial={page.storage} act={act} active={active&&opened} readOnly={readOnly}/>
    <TaskEnvironments key={'environment:'+sid} workspaceId={workspace.id} sessionId={sid} act={act} active={active&&opened} readOnly={readOnly}/>
    <p>Dynamic application preview ports, remote publishing targets and external processes are not inspected here. Cooperative reservations below do not establish physical isolation.</p>
   </>}
  </section>
 </details>;
}
