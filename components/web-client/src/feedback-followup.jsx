import React,{useEffect,useRef,useState} from 'react';
import {ResultNotice} from './settings-ui';
import {FeedbackCorrection,FeedbackStateControl} from './feedback-lifecycle';

export function FeedbackFollowup({state,act}){
 const reports=(state.feedback?.requests||[]).filter(row=>row.status==='submitted');
 const shared=state.view?.feedbackFollowupDraft;
 const [draft,setDraft]=useState(shared||{feedbackId:'',body:''}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const current=useRef(draft),dirty=useRef(false),sending=useRef(false),timer=useRef(null),saving=useRef(null);
 useEffect(()=>()=>{clearTimeout(timer.current);if(dirty.current)void flush().catch(()=>{})},[]);
 useEffect(()=>{if(shared&&!dirty.current&&!sending.current){current.current=shared;setDraft(shared)}},[shared]);
 const pending=draft.pending,result=pending&&(state.feedback?.followups||[]).find(row=>row.requestId===pending.requestId);
 const receipt=result||(state.feedback?.followups||[]).find(row=>row.requestId===draft.submittedRequestId&&row.feedbackId===draft.feedbackId&&row.status==='submitted');
 useEffect(()=>{
  if(result?.status!=='submitted'||current.current.pending?.requestId!==result.requestId)return;
  setError('');
  void save({...current.current,feedbackId:current.current.feedbackId,body:'',pending:undefined,submittedRequestId:result.requestId}).catch(()=>setError('The comment was sent, but the cleared draft could not be saved. Reconnect and try again.'));
 },[result?.status,result?.requestId]);
 const reading=(state.feedback?.followups||[]).find(row=>row.feedbackId===draft.feedbackId&&row.requestId===state.feedback?.readRequestId);
 const report=state.feedback?.report?.feedbackId===draft.feedbackId?state.feedback.report:null;
 const working=busy||['queued','sending'].includes(result?.status),terminal=['submitted','unknown','failed'].includes(result?.status);
 function edit(patch){dirty.current=true;current.current={...current.current,...patch};setDraft(current.current);clearTimeout(timer.current);timer.current=setTimeout(()=>flush().catch(()=>setError('The draft could not be saved.')),250)}
 async function flush(){
  clearTimeout(timer.current);
  if(saving.current){await saving.current;return flush()}
  if(!dirty.current)return;
  const value=current.current,job=act('view.update',{patch:{feedbackFollowupDraft:value}});saving.current=job;
  try{const response=await job;if(response?.accepted!==true)throw Error('The draft could not be saved.');if(current.current===value)dirty.current=false}
  finally{saving.current=null}
  if(dirty.current)return flush();
 }
 async function save(value){current.current=value;dirty.current=true;setDraft(value);await flush()}
 async function read(page=1){setError('');try{await save(current.current);await act('feedback.get',{requestId:crypto.randomUUID(),feedbackId:current.current.feedbackId,page})}catch{setError('Could not load the report. Check the connection and try again.')}}
 async function send(event){
  event.preventDefault();if(sending.current||working||terminal)return;
  sending.current=true;setBusy(true);setError('');
  const payload=current.current.pending||{requestId:crypto.randomUUID(),feedbackId:current.current.feedbackId,body:current.current.body};
  const frozen={...current.current,pending:payload};current.current=frozen;setDraft(frozen);
  try{await save(frozen);await act('feedback.comment',payload)}
  catch{setError('The comment was not acknowledged. Check the same request below; it will not post twice.')}
  finally{sending.current=false;setBusy(false)}
 }
 async function newComment(){const next={...current.current,feedbackId:draft.feedbackId,body:'',pending:undefined,submittedRequestId:undefined};try{await save(next);current.current=next;setDraft(next);setError('')}catch{setError('Could not start a new comment.')}}
 async function saveFiles(feedbackId,value){await save({...current.current,fileAdditions:{...current.current.fileAdditions,[feedbackId]:value}})}
 if(!reports.length)return null;
 return <section className="a-feedback-followup" aria-label="Feedback follow-up">
  <h3>Follow up on feedback</h3>
  <label htmlFor="feedback-report">Submitted report</label>
  <select id="feedback-report" data-action="view.update" disabled={!!pending||busy} value={draft.feedbackId} onChange={event=>edit({feedbackId:event.target.value})}><option value="">Choose a report</option>{reports.map(row=><option key={row.requestId} value={row.requestId}>{row.title}</option>)}</select>
  <button type="button" className="a-soft" data-action="feedback.get" disabled={!draft.feedbackId||busy||['queued','sending'].includes(reading?.status)} onClick={()=>read()}>Refresh report</button>
  {reading&&<ResultNotice phase={reading.status==='failed'?'error':reading.status==='completed'?'success':'working'} message={reading.message}/>}
  {report&&<div className="a-feedback-report"><h4>{report.title} · {report.state}</h4><a href={report.url} target="_blank" rel="noopener noreferrer">Open issue on GitHub</a><pre>{report.body}</pre>{report.comments.map(comment=><div key={comment.id}><strong>{comment.author.login}</strong><small> {comment.createdAt}</small><pre>{comment.body}</pre></div>)}<div>{report.page>1&&<button type="button" className="a-link" data-action="feedback.get" onClick={()=>read(report.page-1)}>Previous comments</button>}{report.hasMore&&<button type="button" className="a-link" data-action="feedback.get" onClick={()=>read(report.page+1)}>More comments</button>}</div></div>}
  {report&&<FeedbackCorrection key={`correction:${report.feedbackId}`} state={state} report={report} act={act}/>}
  {report&&<FeedbackStateControl state={state} report={report} act={act}/>}
  {draft.feedbackId&&<FeedbackFiles key={`files:${draft.feedbackId}`} feedbackId={draft.feedbackId} state={state} act={act} saved={draft.fileAdditions?.[draft.feedbackId]} save={value=>saveFiles(draft.feedbackId,value)}/>}
  <form onSubmit={send} data-action="feedback.comment">
   <label htmlFor="feedback-comment">Add a comment</label><textarea id="feedback-comment" data-action="view.update" maxLength={16000} required disabled={!!pending||busy} value={pending?.body??draft.body} onChange={event=>edit({body:event.target.value})} onBlur={()=>save(current.current).catch(()=>setError('The draft could not be saved.'))}/>
   <p className="a-caption">Only this text is added to the selected report, using the host’s GitHub sign-in. The report must belong to that account.</p>
   {receipt&&<ResultNotice phase={receipt.status==='submitted'?'success':['unknown','failed'].includes(receipt.status)?'error':'working'} message={receipt.message}/>}
   {error&&<ResultNotice phase="error" message={error}/>}
   {receipt?.commentUrl&&<a href={receipt.commentUrl} target="_blank" rel="noopener noreferrer">View comment</a>}
   {result?.status==='unknown'&&<a href={result.url} target="_blank" rel="noopener noreferrer">Check the issue before starting another comment</a>}
   {!terminal&&<button type="submit" className="a-primary" data-action="feedback.comment" disabled={working||!draft.feedbackId||!draft.body.trim()}>{working?'Sending…':pending?'Check comment status':'Send comment'}</button>}
   {terminal&&<button type="button" className="a-soft" onClick={newComment}>New comment</button>}
  </form>
 </section>;
}

const encodeFollowupFile=file=>new Promise((resolve,reject)=>{
 const reader=new FileReader();reader.onerror=()=>reject(Error('Could not read the file.'));
 reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file);
});
const fileBinding=async file=>({
 name:file.name,mime:file.type,size:file.size,
 sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),n=>n.toString(16).padStart(2,'0')).join('')
});

// File selection, review and pending intent are separate from the initial draft.
// Receipts are host-owned; switching reports never retargets an accepted upload.
export function FeedbackFiles({feedbackId,state,act,saved,save}){
 const [value,setValue]=useState(saved||{comment:''}),[busy,setBusy]=useState(false),[error,setError]=useState(''),[confirmed,setConfirmed]=useState(false);
 const current=useRef(value),active=useRef(false),stageFiles=useRef(new Map());
 useEffect(()=>{if(saved&&!active.current){current.current=saved;setValue(saved)}},[saved]);
 const receipts=(state.feedback?.additions||[]).filter(row=>row.feedbackId===feedbackId);
 const files=state.feedback?.attachmentDrafts?.[feedbackId]||[];
 const recovery=state.feedback?.attachmentRecovery?.[feedbackId];
 const clientId=state.client?.id;
 const inheritedIds=new Set([...receipts.filter(row=>row.readOnly).map(row=>row.requestId),
  ...(recovery?.files||[]).map(row=>row.requestId),...(recovery?.hints||[]).map(row=>row.requestId)]);
 // A writable owner label cannot turn an inherited exact ID into a live draft.
 const inheritedDraft=!!recovery?.resumed&&(value.ownerClientId!==clientId||
  inheritedIds.has(value.pending?.requestId)||inheritedIds.has(value.reviewRequestId)||
  (value.staging||[]).some(row=>inheritedIds.has(row.requestId)));
 const blocked=!!recovery?.blocked;
 const review=receipts.find(row=>row.requestId===value.reviewRequestId&&row.action==='feedback.attachments.review');
 const pending=value.pending;
 const result=receipts.find(row=>row.requestId===pending?.requestId)
   ||receipts.find(row=>row.action==='feedback.attachments.add'&&['queued','sending','unknown','partial'].includes(row.status));
 const staging=inheritedDraft?[]:value.staging||[];
 const frozen=blocked||inheritedDraft||!!pending||!!result||!!staging.length;
 const history=receipts.filter(row=>row.action==='feedback.attachments.add'&&row.requestId!==result?.requestId);
 const actual=JSON.stringify(files.map(({id,name,mime,size,sha256})=>({id,name,mime,size,sha256})).sort((a,b)=>a.id.localeCompare(b.id)));
 const reviewed=JSON.stringify(review?.review?.manifest);
 const ready=!review?.readOnly&&!inheritedDraft&&!blocked&&review?.status==='completed'&&actual===reviewed&&!review.consumedBy;
 useEffect(()=>setConfirmed(false),[actual,reviewed,review?.requestId]);
 useEffect(()=>{
  if(active.current||inheritedDraft||!current.current.staging?.length)return;
  const remaining=current.current.staging.filter(intent=>!(state.feedback?.stagingReceipts||[]).some(row=>
   row.requestId===intent.requestId&&row.feedbackId===intent.feedbackId&&row.sha256===intent.sha256&&row.size===intent.size));
  if(remaining.length===current.current.staging.length)return;
  const kept=new Set(remaining.map(row=>row.requestId));
  for(const id of stageFiles.current.keys())if(!kept.has(id))stageFiles.current.delete(id);
  void remember({...current.current,staging:remaining}).catch(()=>setError('Staging was observed, but its cleared intent could not be saved. Reconnect to read the same saved state.'));
 },[state.feedback?.stagingReceipts,busy,saved,inheritedDraft]);
 async function remember(next){const owned={...next,ownerClientId:clientId};current.current=owned;setValue(owned);await save(owned)}
 async function perform(job){if(active.current)return;active.current=true;setBusy(true);setError('');try{await job()}catch{setError('The operation was not acknowledged. Keep this exact request; an uncertain upload must not be started again.')}finally{active.current=false;setBusy(false)}}
 async function stage(event){
  const input=event.target,chosen=Array.from(input.files||[]);
  if(frozen||!chosen.length)return;
  await perform(async()=>{
   if(chosen.length+files.length>8||chosen.some(file=>file.size<=0||file.size>8*1024*1024)||
      chosen.reduce((sum,file)=>sum+file.size,0)+files.reduce((sum,file)=>sum+file.size,0)>24*1024*1024)
    throw Error('Choose up to 8 nonempty files, 8 MiB each, 24 MiB total.');
   const intents=[];
   for(const file of chosen){
    const intent={requestId:crypto.randomUUID(),feedbackId,...await fileBinding(file)};
    intents.push(intent);stageFiles.current.set(intent.requestId,file);
   }
   // Await the durable acknowledgement of the COMPLETE submitted-name/byte
   // binding before any POST. Browser MIME never supplies host authority.
   // File objects stay in this live client's memory, never in shared state.
   await remember({...current.current,reviewRequestId:undefined,staging:intents});
   input.value='';
   await sendStaged();
  });
 }
 async function sendStaged(){
  if(inheritedDraft||blocked)throw Error('Inherited staging is read only.');
  // All entry points, including same-ID checks after a failed save, must
  // establish the durable complete hint before the first staging POST.
  await remember({...current.current});
  for(const intent of [...(current.current.staging||[])]){
   const file=stageFiles.current.get(intent.requestId);
   if(!file)throw Error('Reselect the exact intended files to check staging.');
   const binding=await fileBinding(file);
   if(['name','mime','size','sha256'].some(key=>binding[key]!==intent[key]))throw Error('The selected file changed.');
   const response=await act('feedback.attachment.add',{requestId:intent.requestId,feedbackId:intent.feedbackId,
    name:intent.name,base64:await encodeFollowupFile(file)});
   if(!response||response.accepted===false)throw Error('Staging was not acknowledged.');
   await remember({...current.current,staging:current.current.staging.filter(row=>row.requestId!==intent.requestId)});
   stageFiles.current.delete(intent.requestId);
  }
 }
 async function reselect(event){
  const input=event.target,chosen=Array.from(input.files||[]);
  if(inheritedDraft||blocked||pending||result||!staging.length||!chosen.length)return;
  await perform(async()=>{
   const intended=[...(current.current.staging||[])],matched=new Map();
   if(chosen.length!==intended.length)throw Error('Reselect the complete pending file selection.');
   for(const file of chosen){
    const binding=await fileBinding(file);
    const intent=intended.find(row=>!matched.has(row.requestId)&&['name','mime','size','sha256'].every(key=>row[key]===binding[key]));
    if(!intent)throw Error('The file does not match the saved selection.');
    matched.set(intent.requestId,file);
   }
   for(const [id,file] of matched)stageFiles.current.set(id,file);
   input.value='';await sendStaged();
  });
 }
 async function remove(id){if(frozen)return;await perform(async()=>{await remember({...current.current,reviewRequestId:undefined});await act('feedback.attachment.remove',{feedbackId,id})})}
 async function reviewFiles(){if(frozen)return;await perform(async()=>{
  const requestId=crypto.randomUUID();await remember({...current.current,reviewRequestId:requestId});
  await act('feedback.attachments.review',{requestId,feedbackId});
 })}
 async function addFiles(){
  if(inheritedDraft||blocked||review?.readOnly)return;
  if(!pending&&(!ready||!confirmed)||result&&['unknown','partial','submitted','failed'].includes(result.status))return;
  await perform(async()=>{
   const payload=current.current.pending||{requestId:crypto.randomUUID(),feedbackId,reviewRequestId:review.requestId,
    confirmedFiles:review.review.manifest.map(({id,sha256})=>({id,sha256})),comment:current.current.comment||''};
   await remember({...current.current,pending:payload});
   await act('feedback.attachments.add',payload);
  });
 }
 async function check(){await perform(async()=>{await act('feedback.attachments.reconcile',{requestId:crypto.randomUUID(),feedbackId,additionRequestId:result.requestId})})}
 return <section aria-label="Add files to submitted feedback">
  <h4>Add ordinary files to this report</h4>
  <p className="a-caption">Separate from new feedback and chat attachments. Up to 8 files, 8 MiB each, 24 MiB total. Excerpts are not supported here.</p>
  {recovery?.resumed&&<div role="region" aria-label="Read-only attachment recovery">
   <p>Read-only recovery from an exact attachment snapshot. Saved files and receipts are readable; editable-file reuse and copied review consent are not supported. No upload, removal, re-selection or continuation is performed by recovery.</p>
   {blocked&&<p>New additions to this report are blocked while captured delivery or acceptance remains uncertain. Clearing this draft cannot remove that uncertainty. Another report remains independent.</p>}
   <ul>{(recovery.files||[]).map(row=><li key={row.requestId}><a href={row.url} target="_blank" rel="noopener noreferrer">Saved {row.name} (read only)</a> · {row.mime} · {row.size} bytes
    <code style={{display:'block',overflowWrap:'anywhere'}}>SHA256 {row.sha256}</code></li>)}</ul>
   <ul>{(recovery.hints||[]).map(row=><li key={`${row.action}:${row.requestId}`}>{row.requestId}: acceptance not observed. This hint is not a file link or permission to send.</li>)}</ul>
  </div>}
  <label>Follow-up files<input type="file" multiple disabled={busy||frozen} onChange={stage}/></label>
  {!!staging.length&&<div role="region" aria-label="Unacknowledged file staging">
   <p>These staging IDs and file hashes are retained. Reconnect reads host receipts; it does not restage files. File objects do not survive reload. If no receipt appears, reselect the exact pending files to check the same IDs.</p>
   <ul>{staging.map(row=><li key={row.requestId}>{row.name} · {row.mime||'unspecified type'} · {row.size} bytes
    <code style={{display:'block',overflowWrap:'anywhere'}}>SHA256 {row.sha256}</code></li>)}</ul>
   <label>Reselect exact pending files<input type="file" multiple disabled={busy||!!pending||!!result} onChange={reselect}/></label>
   <button type="button" disabled={busy||!!pending||!!result||staging.some(row=>!stageFiles.current.has(row.requestId))}
    onClick={()=>perform(sendStaged)}>Check same staging request</button>
  </div>}
  <ul>{files.map(row=><li key={row.id}><a href={row.url} target="_blank" rel="noopener noreferrer">{row.name}</a> · {row.mime} · {row.size} bytes
   <code style={{display:'block',overflowWrap:'anywhere'}}>SHA256 {row.sha256}</code>
   {!frozen&&<button type="button" disabled={busy} onClick={()=>remove(row.id)} aria-label={`Remove follow-up ${row.name}`}>Remove</button>}
  </li>)}</ul>
  <label>File addition comment (optional)<textarea aria-label="File addition comment (optional)" maxLength={16000} disabled={busy||frozen} value={pending?.comment??value.comment??''}
   onChange={event=>{const next={...current.current,comment:event.target.value};current.current=next;setValue(next)}}
   onBlur={()=>remember(current.current).catch(()=>setError('The file comment draft could not be saved.'))}/></label>
  {!frozen&&<button type="button" data-action="feedback.attachments.review" disabled={busy||!files.length||review?.status==='queued'||review?.status==='sending'} onClick={reviewFiles}>Review file destination</button>}
  {review&&<ResultNotice phase={review.status==='failed'?'error':review.status==='completed'?'success':'working'} message={review.message}/>}
  {review?.review&&<div aria-label="Frozen file review">
   <a href={review.review.url} target="_blank" rel="noopener noreferrer">{review.review.url}</a>
   <p>Repository {review.review.repository.full_name} (ID {review.review.repository.id}) · {review.review.repository.private?'Private':'Public — anyone can read'} · Issue ID {review.review.issueId}</p>
   <p>GitHub account {review.review.account.login} (ID {review.review.account.id})</p>
   <ul>{review.review.manifest.map(row=><li key={row.id}>{row.name} · {row.mime} · {row.size} bytes
    <code style={{display:'block',overflowWrap:'anywhere'}}>SHA256 {row.sha256}</code></li>)}</ul>
   <p>{review.review.disclosure}</p>
   {!frozen&&<label><input type="checkbox" checked={confirmed} disabled={!ready||busy} onChange={event=>setConfirmed(event.target.checked)}/>I confirm these exact files and hashes for this issue and its displayed visibility and understand the retention limit.</label>}
  </div>}
  {!frozen&&<button type="button" data-action="feedback.attachments.add" disabled={busy||!ready||!confirmed} onClick={addFiles}>Add reviewed files</button>}
  {pending&&!result&&!inheritedDraft&&!blocked&&<button type="button" disabled={busy} onClick={addFiles}>Check same file request</button>}
  {result&&<ResultNotice phase={result.status==='submitted'?'success':['unknown','partial','failed'].includes(result.status)?'error':'working'} message={result.message}/>}
  {result?.phases&&<ul>{result.phases.map(row=><li key={row.phaseId}>{row.phaseId.split(':').slice(1).join(':')}: {row.status}</li>)}</ul>}
  {result?.filesStored&&<p>Files stored; comment delivery: {result.commentStatus}. Stored history is not deleted by removing local files.</p>}
  {result?.attachments?.map(row=><p key={row.id}><a href={row.url} target="_blank" rel="noopener noreferrer">Stored {row.name}</a></p>)}
  {result?.commentUrl&&<a href={result.commentUrl} target="_blank" rel="noopener noreferrer">View file addition comment</a>}
  {result&&['unknown','partial'].includes(result.status)&&<button type="button" data-action="feedback.attachments.reconcile" disabled={busy} onClick={check}>Check file delivery (read only)</button>}
  {!blocked&&(inheritedDraft||result&&['submitted','failed'].includes(result.status))&&<button type="button" disabled={busy} onClick={()=>remember({comment:''}).catch(()=>setError('The new file draft could not be saved.'))}>Start a new file draft</button>}
  {!!history.length&&<div role="region" aria-label="Saved file addition history">
   <h5>Saved file addition history (read only)</h5>
   {history.map(row=><div key={row.requestId}><p>{row.requestId}: {row.status} · {row.message}</p>
    {row.attachments?.map(file=><p key={file.id}><a href={file.url} target="_blank" rel="noopener noreferrer">History stored {file.name}</a></p>)}
    {row.commentUrl&&<a href={row.commentUrl} target="_blank" rel="noopener noreferrer">History file addition comment</a>}
   </div>)}
  </div>}
  {error&&<ResultNotice phase="error" message={error}/>}
 </section>;
}
