import React,{useEffect,useRef,useState} from 'react';
import {CoordinationPanel} from './coordination';
export {SharedWorkBrowser} from './shared-work-browser';

export function WorkspaceWork({workspace,session,dispatch,onOpenChat}){
 const [open,setOpen]=useState(false);
 return <details className="a-workspace-work-entry" onToggle={event=>{if(event.target===event.currentTarget)setOpen(event.currentTarget.open)}}><summary>Chat coordination review</summary>
  {open&&<WorkspaceWorkInspection key={workspace.id} workspace={workspace} session={session} dispatch={dispatch} onOpenChat={onOpenChat}/>}
 </details>;
}

export function WorkspaceWorkInspection({workspace,session,dispatch,onOpenChat}){
 const [inspected,setInspected]=useState(()=>session?.workspace===workspace.path?session:null);
 const [page,setPage]=useState({items:[],offset:0,next:null}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const visit=useRef(0);
 const read=async(offset=0)=>{
  const current=++visit.current;setBusy(true);setError('');
  try{
   const response=await dispatch('coordination.list',{workspace:workspace.path,offset,limit:20,includeWorkers:false},{feedback:false});
   if(current!==visit.current)return;
   if(response?.accepted!==true)throw Error(response?.error||'Workspace chats could not be read.');
   setPage({items:response.result.items,offset,next:response.result.nextOffset??null});
  }catch(error){if(current===visit.current)setError(error.message)}
  finally{if(current===visit.current)setBusy(false)}
 };
 useEffect(()=>{read();return()=>{visit.current++}},[workspace.id,workspace.path]);
 const choices=[...new Map([...(inspected?[inspected]:[]),...page.items.map(row=>({id:row.target.sessionId,title:row.title,workspace:workspace.path}))].map(row=>[row.id,row])).values()];
 const inspect=id=>{const row=choices.find(row=>row.id===id);if(row)setInspected(session?.id===id&&session.workspace===workspace.path?session:row)};
 return <section aria-label="Inspect workspace work">
  <p>Choose a chat to review the work around it. Your open conversation and draft stay in place.</p>
  <label htmlFor="workspace-work-chat">Review from chat</label><select id="workspace-work-chat" value={inspected?.id||''} disabled={busy&&!choices.length} onChange={event=>inspect(event.target.value)}><option value="" disabled>Choose a chat</option>{choices.map(row=><option key={row.id} value={row.id}>{row.title||'Untitled chat'}{choices.some(other=>other.id!==row.id&&other.title===row.title)?' · '+row.id:''}</option>)}</select>
  <div className="a-dialog-actions"><button type="button" className="a-link" disabled={busy} data-action="coordination.list" onClick={()=>read(page.offset)}>Refresh chats</button><button type="button" className="a-link" disabled={busy||page.offset===0} onClick={()=>read(Math.max(0,page.offset-20))}>Previous chats</button><button type="button" className="a-link" disabled={busy||page.next===null} onClick={()=>read(page.next)}>Next chats</button></div>
  {busy&&<p role="status">Loading workspace chats…</p>}{error&&<p role="alert">{error}</p>}{!busy&&!error&&!choices.length&&<p>No saved chats in this workspace yet.</p>}
  {inspected&&<><p className="a-caption">Reviewing from {inspected.title||'Untitled chat'}.</p><CoordinationPanel key={inspected.id} dispatch={dispatch} sessionId={inspected.id} session={inspected} expectedWorkspace={workspace.path} onOpenChat={onOpenChat}/></>}
 </section>;
}
