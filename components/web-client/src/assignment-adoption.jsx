import React,{useEffect,useRef,useState} from 'react';

export function AssignmentAdoption({dispatch,sessionId,assignment,busy,onAdopt}){
 const [open,setOpen]=useState(false),[page,setPage]=useState({items:[],offset:0,next:null});
 const [target,setTarget]=useState(''),[review,setReview]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 const epoch=useRef(0),workspace=useRef(null);
 useEffect(()=>{epoch.current++;setReview(null);setLoading(false);setTarget('');return()=>{epoch.current++}},[sessionId,assignment.revision]);
 useEffect(()=>{workspace.current=null},[sessionId]);
 const load=async(offset=0)=>{
  const visit=++epoch.current;setLoading(true);setError('');setReview(null);setTarget('');
  try{
   if(!workspace.current){const context=await dispatch('coordination.context',{sessionId},{feedback:false});workspace.current=context.result.workspace}
   if(visit!==epoch.current)return;
   const response=await dispatch('coordination.list',{workspace:workspace.current,offset,limit:20,includeWorkers:false},{feedback:false});
   if(visit!==epoch.current)return;
   setPage({items:response.result.items.filter(row=>row.target.sessionId!==sessionId&&assignment.members[row.target.sessionId]?.status!=='bound'),offset,next:response.result.nextOffset??null});
  }catch(error){if(visit===epoch.current)setError(error.message)}
  finally{if(visit===epoch.current)setLoading(false)}
 };
 const choose=async id=>{
  const visit=++epoch.current;setTarget(id);setReview(null);setError('');setLoading(true);
  try{
   const response=await dispatch('assignment.adoption',{sessionId,targetSessionId:id},{feedback:false});
   if(visit===epoch.current)setReview(response.result);
  }catch(error){if(visit===epoch.current)setError(error.message)}
  finally{if(visit===epoch.current)setLoading(false)}
 };
 return <details onToggle={event=>{
  if(event.target!==event.currentTarget)return;
  const expanded=event.currentTarget.open;setOpen(expanded);epoch.current++;setLoading(false);setReview(null);
  if(expanded)load();
 }}>
  <summary>Add an existing chat</summary>
  {open&&<>
   <p>Bring a settled chat in this workspace under this manager’s responsibility. Its conversation stays in place.</p>
   <label htmlFor={'assignment-add-'+sessionId}>Chat to add</label>
   <select id={'assignment-add-'+sessionId} value={target} disabled={busy||loading} onChange={event=>choose(event.target.value)}>
    <option value="" disabled>Choose a chat</option>
    {page.items.map(row=><option key={row.target.sessionId} value={row.target.sessionId}>{row.title||'Untitled chat'}</option>)}
   </select>
   <div className="a-dialog-actions">
    <button className="a-link" disabled={busy||loading||page.offset===0} onClick={()=>load(Math.max(0,page.offset-20))}>Previous chats</button>
    <button className="a-link" disabled={busy||loading||page.next===null} onClick={()=>load(page.next)}>Next chats</button>
    <button className="a-link" disabled={busy||loading} onClick={()=>target?choose(target):load(page.offset)}>Refresh</button>
   </div>
   {loading&&<p role="status">Reviewing chats…</p>}
   {error&&<p role="alert">{error}</p>}
   {review&&<><p>{review.effect}</p>{review.reason&&<p role="status">{review.reason}</p>}
    <button className="a-soft" disabled={busy||loading||!review.eligible||review.assignmentRevision!==assignment.revision} onClick={()=>onAdopt({
     sessionId,targetSessionId:review.sessionId,expectedRevision:review.assignmentRevision,expectedTargetRevision:review.revision
    })}>Add chat</button></>}
  </>}
 </details>;
}
