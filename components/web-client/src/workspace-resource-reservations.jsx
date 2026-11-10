import React,{useEffect,useId,useRef,useState} from 'react';
import {useWorkspaceChats,WorkspaceChatChoices} from './workspace-chat-choices';

export function ResourceReservations({workspace,state,act,active,readOnly}){
 const formId=useId();
 const [opened,setOpened]=useState(false),[page,setPage]=useState(null),[offset,setOffset]=useState(0),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [draft,setDraft]=useState({resourceKey:'',mode:'exclusive',note:''}),[editing,setEditing]=useState(null),[evidence,setEvidence]=useState('');
 const visit=useRef(0);
 const choices=useWorkspaceChats(workspace,active&&opened,state.selectedSessionId),chats=choices.options;
 const dirty=!!(draft.resourceKey||draft.note||evidence);
 useEffect(()=>{if(!dirty)return;const prevent=event=>{event.preventDefault();event.returnValue=''};window.addEventListener('beforeunload',prevent);return()=>window.removeEventListener('beforeunload',prevent)},[dirty]);
 const run=async(action,args={})=>{
  const outer=await act('workspace.resources.'+action,{workspaceId:workspace.id,...args});
  const response=outer?.result?.accepted!==undefined?outer.result:outer;
  if(!response||response.accepted===false)throw Error(response?.error||'Resource reservations unavailable.');
  const result=response.result;
  if(['conflict','limit','stale'].includes(result?.outcome))throw Error(result.detail);
  return result;
 };
 const refresh=async(next=offset)=>{const current=++visit.current;const value=await run('reservations',{offset:next,limit:25});if(visit.current===current){setPage(value);setOffset(next)}};
 useEffect(()=>{
  if(!active||!opened)return;
  refresh().catch(err=>setError(err.message));
  return()=>{visit.current++};
 },[active,opened,workspace.id]);
 const mutate=async(fn)=>{setBusy(true);setError('');try{await fn();await refresh(0)}catch(err){setError(err.message)}finally{setBusy(false)}};
 return <details className="a-workspace-setup" onToggle={event=>setOpened(event.currentTarget.open)}>
  <summary>Resource reservations</summary>
  <section aria-label="Resource reservations">
   <p>Reserve a named resource before using it, such as a preview or test database. Use the same key across chats. Reservations coordinate participating chats in this app; they do not create, stop or lock the resource itself.</p>
   <button className="a-soft" type="button" disabled={busy} onClick={()=>refresh().catch(err=>setError(err.message))}>Refresh reservations</button>
   {error&&<p role="alert">{error}</p>}
   {dirty&&<p role="status">Unsaved reservation details stay with this workspace until you save or reload.</p>}
   {page?.items.length===0&&<p>No resources reserved.</p>}
   <ul>{page?.items.map(row=><li key={row.id} data-reservation-id={row.id}>
    <strong>{row.resourceKey}</strong>
    <p>{row.mode==='shared'?'Shared':'Exclusive'} · {row.status==='uncertain'?'Needs reconciliation; still reserved':'Reserved'} · {chats.find(chat=>chat.id===row.owner.sessionId)?.title||'Retained conversation'}</p>
    {row.note&&<p>{row.note}</p>}{row.evidence&&<p>{row.evidence}</p>}
    {!readOnly&&<button className="a-soft" type="button" disabled={busy||!!editing} onClick={()=>{setEditing(row);setEvidence('')}}>Reconcile reservation</button>}
   </li>)}</ul>
   <div className="a-dialog-actions">
    {offset>0&&<button className="a-soft" type="button" disabled={busy} onClick={()=>refresh(Math.max(0,offset-25)).catch(err=>setError(err.message))}>Previous reservations</button>}
    {page?.nextOffset!=null&&<button className="a-soft" type="button" disabled={busy} onClick={()=>refresh(page.nextOffset).catch(err=>setError(err.message))}>More reservations</button>}
   </div>
   {!readOnly&&editing&&<form aria-label="Reconcile reservation" onSubmit={event=>event.preventDefault()}>
    <p>Inspect <strong>{editing.resourceKey}</strong> before releasing it. Disconnecting or stopping a chat does not free its resource.</p>
    <label htmlFor={formId+'-evidence'}>Reservation evidence</label><textarea id={formId+'-evidence'} disabled={busy} required maxLength={4000} value={evidence} onChange={event=>setEvidence(event.target.value)}/>
    <div className="a-dialog-actions">{[['release','Release reservation'],['uncertain','Keep reserved as uncertain']].map(([action,label])=><button className="a-soft" key={action} type="button" disabled={busy||!evidence.trim()} onClick={()=>mutate(async()=>{
     await run(action,{id:editing.id,expectedRevision:editing.revision,evidence});setEditing(null);setEvidence('');
    })}>{label}</button>)}<button className="a-soft" type="button" disabled={busy} onClick={()=>{setEditing(null);setEvidence('')}}>Cancel reconciliation</button></div>
   </form>}
   {!readOnly&&<form aria-label="Reserve a resource" onSubmit={event=>{event.preventDefault();if(!choices.selectedId)return;mutate(async()=>{
    await run('reserve',{...draft,sessionId:choices.selectedId});setDraft({...draft,resourceKey:'',note:''});
   })}}>
    <WorkspaceChatChoices choices={choices} id={formId+'-chat'} label="Reserve for chat" disabled={busy}/>
    <label htmlFor={formId+'-key'}>Resource key</label><input id={formId+'-key'} disabled={busy} required maxLength={500} placeholder="preview:my-project" value={draft.resourceKey} onChange={event=>setDraft({...draft,resourceKey:event.target.value})}/>
    <label htmlFor={formId+'-sharing'}>Resource sharing</label><select id={formId+'-sharing'} disabled={busy} value={draft.mode} onChange={event=>setDraft({...draft,mode:event.target.value})}><option value="exclusive">One owner</option><option value="shared">Explicitly shared</option></select>
    <label htmlFor={formId+'-note'}>Reservation note</label><input id={formId+'-note'} disabled={busy} maxLength={1000} value={draft.note} onChange={event=>setDraft({...draft,note:event.target.value})}/>
    <button className="a-primary" type="submit" disabled={busy||!choices.selectedId||!draft.resourceKey.trim()}>Reserve resource</button>
   </form>}
  </section>
 </details>;
}
