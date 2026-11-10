import React,{useEffect,useRef,useState} from 'react';

function TextVersion({label,value}){
 return <details><summary>{label}</summary><strong>{value.title}</strong><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{value.body}</pre></details>;
}

export function FeedbackCorrection({state,report,act}){
 const shared=state.view?.feedbackCorrectionDraft;
 const [draft,setDraft]=useState(shared||null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[confirmed,setConfirmed]=useState(false);
 const current=useRef(draft),tail=useRef(Promise.resolve()),sending=useRef(false),timer=useRef(null),dirty=useRef(false);
 useEffect(()=>()=>{clearTimeout(timer.current);if(dirty.current)void save(current.current).catch(()=>{})},[]);
 useEffect(()=>{if(!dirty.current&&!sending.current){current.current=shared||null;setDraft(shared||null)}},[shared]);
 const result=(state.feedback?.followups||[]).find(row=>row.requestId===draft?.pending?.requestId);
 const review=state.feedback?.editReview,comparison=review?.review;
 const reviewed=review?.status==='completed'&&review.requestId===draft?.reviewRequestId&&review.feedbackId===report.feedbackId&&comparison?.proposed.title===draft?.title&&comparison?.proposed.body===draft?.body&&!review.consumedBy;
 const same=draft?.feedbackId===report.feedbackId,terminal=['submitted','unknown','failed'].includes(result?.status);
 useEffect(()=>setConfirmed(false),[draft?.reviewRequestId,review?.requestId,draft?.title,draft?.body]);
 function save(value){
  clearTimeout(timer.current);dirty.current=true;current.current=value;setDraft(value);
  const job=tail.current.catch(()=>{}).then(()=>act('view.update',{patch:{feedbackCorrectionDraft:value}})).then(response=>{if(response?.accepted!==true)throw Error('The correction draft could not be saved.');if(current.current===value)dirty.current=false});
  tail.current=job;job.catch(error=>setError(error.message));return job;
 }
 function edit(patch){setConfirmed(false);dirty.current=true;current.current={...current.current,...patch,reviewRequestId:null};setDraft(current.current);clearTimeout(timer.current);timer.current=setTimeout(()=>void save(current.current).catch(()=>{}),250)}
 function start(){setError('');void save({feedbackId:report.feedbackId,title:report.editableTitle,body:report.editableBody,readRequestId:report.requestId}).catch(()=>{})}
 async function reviewChanges(){
  if(sending.current)return;sending.current=true;setBusy(true);setError('');setConfirmed(false);
  try{
   const d=current.current,requestId=crypto.randomUUID();
   await save({...d,reviewRequestId:requestId});
   const response=await act('feedback.edits.review',{requestId,feedbackId:d.feedbackId,readRequestId:d.readRequestId||report.requestId,title:d.title,body:d.body});
   if(response?.accepted!==true)throw Error('The review was not acknowledged. Review changes again before publishing.');
  }catch(error){setError(error.message)}finally{sending.current=false;setBusy(false)}
 }
 async function submit(event){
  event.preventDefault();if(sending.current||terminal||(!current.current.pending&&(!reviewed||!confirmed)))return;
  sending.current=true;setBusy(true);setError('');
  try{
   const d=current.current,payload=d.pending||{requestId:crypto.randomUUID(),feedbackId:d.feedbackId,title:d.title,body:d.body,expectedRevision:comparison.latest.revision,reviewRequestId:review.requestId};
   await save({...d,pending:payload});const response=await act('feedback.update',payload);
   if(response?.accepted!==true)throw Error('Delivery was not acknowledged. Check only this same request.');
  }catch(error){setError(error.message||'Delivery was not acknowledged. Check only this same request.')}
  finally{sending.current=false;setBusy(false)}
 }
 if(!same)return <><button type="button" className="a-soft" onClick={start} disabled={!!draft?.body}>Draft a correction</button>{draft?.body&&<p>A correction for another report is saved. Return to that report before starting another.</p>}</>;
 return <form onSubmit={submit} aria-label="Feedback correction">
  <h4>Review changes</h4><p>Publishing adds a correction comment. Automatic replacement of the original report is unavailable because concurrent edits cannot be protected. The original report and maintainer edits stay intact.</p>
  <label htmlFor="feedback-corrected-title">Corrected title</label><input id="feedback-corrected-title" maxLength={200} required disabled={!!draft.pending||busy} value={draft.title} onChange={event=>edit({title:event.target.value})} onBlur={()=>void save(current.current).catch(()=>{})}/>
  <label htmlFor="feedback-corrected-body">Corrected description</label><textarea id="feedback-corrected-body" maxLength={16000} required disabled={!!draft.pending||busy} value={draft.body} onChange={event=>edit({body:event.target.value})} onBlur={()=>void save(current.current).catch(()=>{})}/>
  {!draft.pending&&<button type="button" className="a-soft" data-action="feedback.edits.review" disabled={busy||['queued','sending'].includes(review?.status)||!draft.title.trim()||!draft.body.trim()} onClick={reviewChanges}>Review changes</button>}
  {review&&review.requestId===draft.reviewRequestId&&<p role="status">{review.message}</p>}
  {reviewed&&<section aria-label="Change comparison">
   {comparison.changedSinceRead&&<p role="alert">The report changed since you opened it. Review the latest version before publishing a correction.</p>}
   <TextVersion label="Originally submitted" value={comparison.original}/>
   {comparison.changedSinceRead&&<><TextVersion label="Previously viewed on GitHub" value={comparison.previous}/>{comparison.previous.textTruncated&&<p>The previous preview was shortened. The latest version below is complete.</p>}</>}
   <TextVersion label="Latest on GitHub" value={comparison.latest}/>
   <TextVersion label="Proposed correction comment" value={comparison.proposed}/>
   <label><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>I reviewed the latest report and want to publish this correction comment.</label>
  </section>}
  {!terminal&&<button className="a-primary" data-action="feedback.update" disabled={busy||['queued','sending'].includes(result?.status)||(!draft.pending&&(!reviewed||!confirmed))}>{draft.pending?'Check correction status':'Publish correction comment'}</button>}
  <p>To edit the original text yourself, <a href={report.url} target="_blank" rel="noopener noreferrer">Open on GitHub</a>. Review any newer changes there before saving.</p>
  <p>New files use the separate attachment review below. Publishing a correction does not upload or attach them.</p>
  {result&&<p role="status">{result.message}</p>}{result?.commentUrl&&<a href={result.commentUrl} target="_blank" rel="noopener noreferrer">View correction</a>}
  {(terminal||!draft.pending)&&<button type="button" className="a-soft" disabled={busy} onClick={()=>void save(null).catch(()=>{})}>Close correction draft</button>}
  {error&&<p role="alert">{error}</p>}
 </form>;
}
