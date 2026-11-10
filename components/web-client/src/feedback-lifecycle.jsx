import React,{useEffect,useRef,useState} from 'react';

export function FeedbackReconcile({state,act}){
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[request,setRequest]=useState(null);
 const pending=useRef(false),rows=(state.feedback?.requests||[]).filter(row=>row.status==='unknown');
 const result=(state.feedback?.followups||[]).find(row=>row.requestId===request);
 async function check(feedbackId){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');
  const requestId=crypto.randomUUID();setRequest(requestId);
  try{await act('feedback.reconcile',{requestId,feedbackId})}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Unconfirmed feedback delivery">
  {!!rows.length&&<><h3>Unconfirmed delivery</h3><p>Check for the original report using its saved identity. Checking never sends it again.</p>{rows.map(row=><div key={row.requestId}><strong>{row.title}</strong><button type="button" className="a-soft" data-action="feedback.reconcile" disabled={busy||['queued','sending'].includes(result?.status)} onClick={()=>check(row.requestId)}>Check delivery</button></div>)}</>}
  {result&&<p role="status">{result.message}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}

export {FeedbackCorrection} from './feedback-edits';

export function FeedbackStateControl({state,report,act}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const sending=useRef(false),saved=state.view?.feedbackLifecycleDraft;
 const pending=saved?.feedbackId===report.feedbackId?saved:null;
 const result=(state.feedback?.followups||[]).find(row=>row.requestId===pending?.requestId);
 const unresolved=pending&&!['submitted','failed','unknown'].includes(result?.status);
 async function change(){
  if(sending.current)return;sending.current=true;setBusy(true);setError('');
  const action=report.state==='closed'?'feedback.reopen':'feedback.close';
  const payload=unresolved?pending:{action,requestId:crypto.randomUUID(),feedbackId:report.feedbackId,expectedRevision:report.revision};
  try{
   const response=await act('view.update',{patch:{feedbackLifecycleDraft:{...payload,readRequestId:report.requestId}}});
   if(response?.accepted===false)throw Error('Could not save the change request.');
   const {action,readRequestId,...args}=payload;await act(action,args);
  }catch(error){setError(error.message||'The state change was not acknowledged. Check this same request.')}
  finally{sending.current=false;setBusy(false)}
 }
 const stale=result&&['submitted','unknown'].includes(result.status)&&report.requestId===pending?.readRequestId;
 return <section aria-label="Feedback report state">
  <button type="button" className="a-soft" data-action={report.state==='closed'?'feedback.reopen':'feedback.close'} disabled={busy||['queued','sending'].includes(result?.status)||stale} onClick={change}>{unresolved?'Check state change':report.state==='closed'?'Reopen my report':'Close my report'}</button>
  {result&&<p role="status">{result.message} Refresh the report to review its current state.</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
