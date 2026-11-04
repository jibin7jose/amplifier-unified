import {feedbackDiagnostics} from './feedback-diagnostics';
import React,{useEffect,useRef,useState} from 'react';
import {ExternalLink,Send,Plus,Paperclip,File,X,Image as ImageIcon} from 'lucide-react';
import {ResultNotice} from './settings-ui';
import './feedback.css';
import {readItems} from './attention';
import {FeedbackFollowup} from './feedback-followup';
import {FeedbackExcerpt} from './feedback-excerpt';
import {FeedbackReconcile} from './feedback-lifecycle';

const empty=()=>({title:'',body:'',category:'bug',includeDiagnostics:true,attachments:[],confirmExcerpts:false,confirmedExcerpts:[],uploadDestination:null,confirmedFiles:[]});
const sizeLabel=bytes=>bytes<1024?`${bytes} B`:bytes<1024*1024?`${Math.ceil(bytes/1024)} KB`:`${(bytes/(1024*1024)).toFixed(1)} MB`;
const encodeFile=file=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('Could not read this file.'));reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.readAsDataURL(file)});
export function FeedbackPanel({state,act}){
 const shared=state.view?.feedbackDraft;
 const selectedSession=useRef(state.selectedSessionId);selectedSession.current=state.selectedSessionId;
 const alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 const [draft,setDraft]=useState(()=>shared||empty()),[busy,setBusy]=useState(false),[error,setError]=useState(''),[uploading,setUploading]=useState(false),[dragging,setDragging]=useState(false),[retryUpload,setRetryUpload]=useState(null);
 const current=useRef(draft),submitting=useRef(false),fileInput=useRef(null),staging=useRef(false),dirty=useRef(false),timer=useRef(null),saving=useRef(null);
 useEffect(()=>()=>{clearTimeout(timer.current);if(dirty.current)void flush().catch(()=>{})},[]);
 useEffect(()=>{if(shared&&!dirty.current&&!submitting.current){current.current=shared;setDraft(shared)}},[shared]);
 const requests=state.feedback?.requests||[],pending=draft.pending;
 const result=pending&&requests.find(item=>item.requestId===pending.requestId);
 const shown=pending||draft;
 useEffect(()=>{if(result&&['submitted','failed','unknown'].includes(result.status))setError('')},[result?.status]);
 useEffect(()=>{
  if(result?.status!=='submitted'||current.current.pending?.requestId!==result.requestId)return;
  setRetryUpload(null);
  void save(empty()).catch(()=>setError('Feedback was sent, but the cleared draft could not be saved. Reconnect and try again.'));
 },[result?.status,result?.requestId]);
 const working=busy||['queued','sending'].includes(result?.status),done=['submitted','failed','unknown'].includes(result?.status);
 const frozen=!!pending||uploading,preview=draft.previewId,selected=(draft.attachments||[]).filter(row=>!pending||pending.attachmentIds?.includes(row.id));
 const issues=state.feedback?.issuesUrl||'https://github.com/microsoft/amplifier-unified/issues';
 async function flush(){
  clearTimeout(timer.current);
  if(saving.current){await saving.current;return flush()}
  if(!dirty.current)return;
  const value=current.current;
  const job=act('view.update',{patch:{feedbackDraft:value}});saving.current=job;
  try{const result=await job;if(!result||result.accepted===false)throw Error('The draft could not be saved.');if(current.current===value)dirty.current=false}
  finally{saving.current=null}
  if(dirty.current)return flush();
 }
 function save(next){current.current=next;dirty.current=true;setDraft(next);return flush()}
 function edit(patch){setError('');current.current={...current.current,...patch};dirty.current=true;setDraft(current.current);clearTimeout(timer.current);timer.current=setTimeout(()=>flush().catch(()=>setError('The draft could not be saved. Reconnect and try again.')),250)}
 function setPreview(id){edit({previewId:id})}
 async function stageExcerpt(args){
  if(staging.current||current.current.pending)throw Error('Wait for the current attachment or submission to finish.');
  staging.current=true;setUploading(true);
  try{
   await flush();
   if(selectedSession.current!==args.id)throw Error('The selected conversation changed. Review its excerpt again.');
   const response=await act('feedback.excerpt.stage',args);
   if(!response?.result)throw Error('The excerpt was not attached. Check the same review again.');
   if(alive.current&&selectedSession.current===args.id)acceptDraft(response);
  }finally{staging.current=false;setUploading(false)}
 }
 function acceptDraft(response){const next=response?.state?.view?.feedbackDraft;if(next){const changed=JSON.stringify(next.attachments)!==JSON.stringify(current.current.attachments);current.current={...current.current,attachments:next.attachments,...(changed?{confirmExcerpts:false,confirmedExcerpts:[],uploadDestination:null,confirmedFiles:[]}: {})};setDraft(current.current);if(changed){dirty.current=true;void flush().catch(()=>setError('The updated attachment list could not be saved.'))}}}
 async function stageFiles(files,retry){
  if(staging.current||current.current.pending)return;
  staging.current=true;setUploading(true);setError('');setDragging(false);
  try{
   await flush();
   if(retry){acceptDraft(await act('feedback.attachment.add',retry));setRetryUpload(null)}
   for(const file of files){
    if(!file.size||file.size>8*1024*1024)throw new Error('Choose nonempty files up to 8 MB each.');
    const request={requestId:crypto.randomUUID(),name:file.name||'pasted-image.png',base64:await encodeFile(file)};
    try{acceptDraft(await act('feedback.attachment.add',request))}
    catch(error){setRetryUpload(request);throw error}
   }
  }catch(error){setError(error?.message||'The file could not be added. Check the draft and retry; it will not be added twice.')}
  finally{staging.current=false;setUploading(false)}
 }
 async function removeFile(id){if(frozen)return;setError('');try{await flush();acceptDraft(await act('feedback.attachment.remove',{id}));if(preview===id)setPreview(null)}catch(error){setError(error?.message||'The attachment could not be removed.')}}
 function paste(event){const files=Array.from(event.clipboardData?.files||[]);if(files.length){event.preventDefault();event.stopPropagation();if(!frozen)stageFiles(files)}}
 function drop(event){if(!event.dataTransfer?.types?.includes('Files'))return;event.preventDefault();event.stopPropagation();setDragging(false);if(!frozen)stageFiles(Array.from(event.dataTransfer.files||[]))}
 async function submit(event){
  event.preventDefault();if(submitting.current||staging.current||working||done||retryUpload)return;
  submitting.current=true;setBusy(true);setError('');
  // Freeze both text and identity before the first await. A lost response must
  // never generate a second GitHub issue or submit newly edited text as a retry.
  const source=current.current,payload=source.pending||{requestId:crypto.randomUUID(),title:source.title,body:source.body,category:source.category,includeDiagnostics:source.includeDiagnostics!==false,...(source.includeDiagnostics!==false?{deviceDiagnostics:feedbackDiagnostics(state)}:{}),confirmExcerpts:source.confirmExcerpts===true,confirmedExcerpts:source.confirmedExcerpts||[],...(source.uploadDestination?{uploadDestination:source.uploadDestination,confirmedFiles:source.confirmedFiles||[]}:{}),attachmentIds:(source.attachments||[]).map(row=>row.id)};
  try{await save({...source,pending:payload});await act('feedback.submit',payload)}
  catch{setError('The submission was not acknowledged. Check its status using the same request below; this will not post it twice.')}
  finally{submitting.current=false;setBusy(false)}
 }
 async function startNew(){if(busy||uploading)return;setError('');setRetryUpload(null);try{await save(empty())}catch{setError('Could not start a new draft. Reconnect and try again.')}}
 return <section className={`a-feedback${dragging?' is-dragging':''}`} data-part="feedback" onPaste={paste} onDrop={drop} onDragOver={event=>{if(event.dataTransfer?.types?.includes('Files')){event.preventDefault();event.stopPropagation();if(!frozen)setDragging(true)}}} onDragLeave={event=>{if(!event.currentTarget.contains(event.relatedTarget))setDragging(false)}}>
  <p>Send a bug report, idea, or question to <a href={issues} target="_blank" rel="noopener noreferrer">microsoft/amplifier-unified</a>. Uses the host’s GitHub sign-in.</p>
  {(result||error)&&<div className="a-feedback-result"><ResultNotice phase={result?.status==='submitted'?'success':['failed','unknown'].includes(result?.status)?'error':'working'} message={result?.message}/>{error&&<ResultNotice phase="error" message={error}/>} {result?.url&&<a className="a-feedback-link" href={result.url} target="_blank" rel="noopener noreferrer">View issue <ExternalLink size={14}/></a>} {result?.status==='unknown'&&<><a href={issues} target="_blank" rel="noopener noreferrer">Check repository issues <ExternalLink size={14}/></a>{result.attachmentsUrl&&<a href={result.attachmentsUrl} target="_blank" rel="noopener noreferrer">Check attachment branch <ExternalLink size={14}/></a>}</>}</div>}
  <form onSubmit={submit} data-action="feedback.submit">
   <label htmlFor="feedback-category">Type</label><select id="feedback-category" data-action="view.update" value={shown.category||'bug'} disabled={frozen} onChange={e=>edit({category:e.target.value})}><option value="bug">Bug report</option><option value="idea">Feature idea</option><option value="question">Question</option><option value="other">Other feedback</option></select>
   <label htmlFor="feedback-title">Title</label><input id="feedback-title" data-action="view.update" maxLength={200} required disabled={frozen} value={shown.title||''} onChange={e=>edit({title:e.target.value})} placeholder="A short description"/>
   <label htmlFor="feedback-body">Details</label><textarea id="feedback-body" data-action="view.update" maxLength={16000} rows={4} required disabled={frozen} value={shown.body||''} onChange={e=>edit({body:e.target.value})} placeholder="What happened, what you expected, or what you’d like to do…" aria-describedby={shown.category==='bug'?'feedback-reproduction-help':undefined}/>{shown.category==='bug'&&<p id="feedback-reproduction-help" className="a-caption">What action triggered it? Was this a new chat or an existing one? Describe what you expected, what happened, and whether it repeats. Leave out private conversation text and credentials.</p>}
   <div className="a-feedback-files" aria-label="Feedback attachments">
    <input ref={fileInput} type="file" multiple hidden disabled={frozen} aria-label="Choose feedback files" onChange={event=>{const files=Array.from(event.target.files||[]);event.target.value='';stageFiles(files)}}/>
    {!pending&&<button type="button" className="a-soft a-feedback-add-files" data-action="feedback.attachment.add" disabled={uploading||!!retryUpload} onClick={()=>fileInput.current?.click()}><Paperclip size={16}/>{uploading?'Adding files…':'Add files or images'}</button>}
    {!pending&&<p className="a-caption">Or drop files here, or paste an image. Up to 8 files, 8 MB each, 24 MB total.</p>}
    {!!selected.length&&<ul>{selected.map(row=><li key={row.id}>
     <button type="button" className="a-feedback-file-preview" data-action="view.update" disabled={uploading} aria-label={`Preview ${row.name}`} onClick={()=>setPreview(preview===row.id?null:row.id)}>{row.mime?.startsWith('image/')&&/^[a-f0-9]{32}$/.test(row.id)?<img src={`/api/attachments/${row.id}`} alt=""/>:<File size={22}/>}<span><strong>{row.name}</strong><small>{sizeLabel(row.size)} · {pending?'Included':row.excerpt?`Reviewed excerpt · ${row.excerpt.visibility}`:'Ready to include'}</small></span></button>
     {!pending&&<button type="button" className="a-icon" data-action="feedback.attachment.remove" aria-label={`Remove ${row.name}`} disabled={uploading} onClick={()=>removeFile(row.id)}><X size={16}/></button>}
    </li>)}</ul>}
    {selected.filter(row=>row.id===preview&&/^[a-f0-9]{32}$/.test(row.id)).map(row=><div className="a-feedback-preview" key={row.id}>{row.mime?.startsWith('image/')?<img src={`/api/attachments/${row.id}`} alt={`Preview of ${row.name}`}/>:<File size={32}/>}<a href={`/api/attachments/${row.id}`} download={row.name}>{row.mime?.startsWith('image/')?<ImageIcon size={14}/>:<File size={14}/>}Open {row.name}</a></div>)}
    {retryUpload&&<div className="a-feedback-upload-retry"><button type="button" className="a-soft" disabled={uploading} onClick={()=>stageFiles([],retryUpload)}>Check attachment: {retryUpload.name}</button><button type="button" className="a-soft" disabled={uploading} onClick={()=>{setRetryUpload(null);setError('')}}>Stop retrying</button></div>}
   </div>
   {selected.some(row=>!row.excerpt)&&<div>
    {!pending&&<button type="button" disabled={frozen} onClick={async()=>{try{await flush();const response=await act('feedback.destination',{});const hashes=new Map((response.files||[]).map(row=>[row.id,row.sha256]));edit({uploadDestination:response.result,confirmedFiles:[],attachments:current.current.attachments.map(row=>hashes.has(row.id)?{...row,sha256:hashes.get(row.id)}:row)})}catch{setError('Could not check the file destination. Nothing was uploaded.')}}}>Review file destination</button>}
    {shown.uploadDestination&&<><p>Files will be stored in {shown.uploadDestination.repository} · {shown.uploadDestination.visibility==='public'?'Public — anyone can read these files':'Private — repository access required'}. Files remain in repository history. Visibility can change later; removing local files does not remove uploaded files.</p>
     <label><input type="checkbox" disabled={frozen} checked={JSON.stringify(shown.confirmedFiles||[])===JSON.stringify(selected.filter(row=>!row.excerpt).map(row=>({id:row.id,sha256:row.sha256})))} onChange={event=>edit({confirmedFiles:event.target.checked?selected.filter(row=>!row.excerpt).map(row=>({id:row.id,sha256:row.sha256})):[]})}/>Send the exact files listed above to this destination when I submit.</label></>}
   </div>}
   {!pending&&<FeedbackExcerpt state={state} act={act} frozen={frozen} onStage={stageExcerpt}/>}
   {selected.some(row=>row.excerpt)&&<label className="a-feedback-checkbox"><input type="checkbox" checked={shown.confirmExcerpts===true} disabled={frozen} onChange={e=>edit({confirmExcerpts:e.target.checked,confirmedExcerpts:e.target.checked?selected.filter(row=>row.excerpt).map(row=>({id:row.id,sha256:row.excerpt.sha256})):[]})}/>Include the reviewed conversation excerpts listed above when I send this feedback.</label>}
   <label className="a-feedback-checkbox"><input type="checkbox" data-action="view.update" checked={shown.includeDiagnostics!==false} disabled={frozen} onChange={e=>edit({includeDiagnostics:e.target.checked})}/>Include reproduction diagnostics</label>
   {shown.includeDiagnostics!==false&&<FeedbackDiagnostics key={result?`receipt:${result.requestId}`:state.selectedSessionId||'current'} state={state} act={act} requestId={result?.requestId} device={pending?.deviceDiagnostics}/> }
   <p className="a-caption">Your text, selected diagnostics and the files listed above are sent when you submit. Files stay in repository history and are linked from the issue. Reviewed excerpts use the visibility shown during review; ordinary files use the destination and visibility you explicitly review; your GitHub sign-in needs repository Contents write access. Chats, paths, provider settings, and credentials are not attached automatically.</p>
   <div className="a-dialog-actions">
    {!done&&<button type="submit" className="a-primary" data-action="feedback.submit" disabled={working||uploading||!!retryUpload||!pending&&selected.some(row=>!row.excerpt)&&(!shown.uploadDestination||JSON.stringify(shown.confirmedFiles||[])!==JSON.stringify(selected.filter(row=>!row.excerpt).map(row=>({id:row.id,sha256:row.sha256}))))||selected.some(row=>row.excerpt)&&!shown.confirmExcerpts||!shown.title?.trim()||!shown.body?.trim()}><Send/>{working?'Sending…':pending?'Check submission':'Send feedback'}</button>}
    {(done||pending&&!busy)&&<button type="button" className="a-soft" data-action="view.update" onClick={startNew}><Plus/>New feedback</button>}
   </div>
  </form>
  {!!requests.filter(item=>item.requestId!==pending?.requestId).length&&<div className="a-feedback-recent"><h3>Submissions</h3>{requests.filter(item=>item.requestId!==pending?.requestId).map(item=><div key={item.requestId} className="a-feedback-receipt"><strong>{item.title}</strong><ResultNotice phase={item.status==='submitted'?'success':['failed','unknown'].includes(item.status)?'error':'working'} message={item.message}/>{item.url&&<a href={item.url} target="_blank" rel="noopener noreferrer">View issue <ExternalLink size={14}/></a>}<FeedbackDiagnostics state={state} act={act} requestId={item.requestId}/>{item.status==='unknown'&&<a href={issues} target="_blank" rel="noopener noreferrer">Check repository issues</a>}{(state.attention?.items||[]).filter(i=>i.requestId===item.requestId&&!i.read).map(i=><button key={i.id} className="a-link" type="button" data-action="attention.read" onClick={()=>readItems(act,[i])}>Mark reviewed</button>)}</div>)}</div>}
  <FeedbackReconcile state={state} act={act}/>
  <FeedbackFollowup state={state} act={act}/>
 </section>;
}


export function FeedbackDiagnostics({state,act,requestId,device,downloadable=false}){
 const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const loading=useRef(false),loaded=useRef(false),alive=useRef(true);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 async function load(){
  if(loading.current)return;
  loading.current=true;setBusy(true);setError('');
  try{
   const response=await act('feedback.diagnostics',requestId?{requestId}:{deviceDiagnostics:device||feedbackDiagnostics(state)});
   if(!response?.result)throw Error('Unavailable');
   if(alive.current){setResult(response.result);loaded.current=true}
  }catch{if(alive.current)setError('Diagnostics could not be loaded. Try Refresh.')}
  finally{loading.current=false;if(alive.current)setBusy(false)}
 }
 return <details data-part="feedback-diagnostics" onToggle={event=>{if(event.currentTarget.open&&!loaded.current)void load()}}>
  <summary>{requestId?'Submitted diagnostics':'Preview reproduction diagnostics'}</summary>
  <p className="a-caption">{requestId?'This is the snapshot saved when this submission was accepted.':'Loads only when opened. This is a current preview; live status may change before you submit.'} Host active component generation does not prove which code a running worker loaded. Reported online views counts device reports, not live event-stream connections. No message text, workspace paths, raw logs, or credentials.</p>
  {busy&&<p role="status">Loading diagnostics…</p>}
  {error&&<p role="alert">{error}</p>}
  {result&&(result.diagnostics?<pre className="a-state-view">{JSON.stringify(result.diagnostics,null,2)}</pre>:<p>No diagnostics were saved with this submission.</p>)}
  <button type="button" className="a-soft" data-action="feedback.diagnostics" disabled={busy} onClick={()=>void load()}>Refresh diagnostics</button>
  {downloadable&&result?.diagnostics&&<div className="a-dialog-actions"><button type="button" className="a-soft" onClick={async()=>{try{await navigator.clipboard.writeText(JSON.stringify(result.diagnostics,null,2));setNotice('Diagnostics copied')}catch{setError('Clipboard unavailable. Select the report above and copy it.')}}}>Copy report</button><button type="button" className="a-soft" onClick={()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(result.diagnostics,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='amplifier-diagnostics.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setNotice('Report saved')}}>Save report</button></div>}
  {notice&&<p role="status">{notice}</p>}
 </details>;
}


export function FeedbackNotice({state,act}){
 if(['feedback','activity'].includes(state.view?.panel))return null;
 const items=(state.attention?.items||[]).filter(i=>i.requestId&&!i.read&&!i.informational&&i.status!=='submitted');
 const pending=state.feedback?.requests?.find(r=>['queued','sending'].includes(r.status));
 const item=items[0],receipt=pending||state.feedback?.requests?.find(r=>r.requestId===item?.requestId);
 if(!receipt)return null;
 return <aside className="a-feedback-notice" aria-label="Feedback status"><div role="status"><ResultNotice phase={pending?'working':receipt.status==='submitted'?'success':'error'} message={pending?'Feedback received — sending in the background.':receipt.message}/></div><div className="a-feedback-notice-actions"><button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:'feedback'}})}>View {pending?'status':'feedback'}</button>{receipt.url&&<a href={receipt.url} target="_blank" rel="noopener noreferrer">View issue <ExternalLink size={14}/></a>}{!pending&&item&&<button type="button" className="a-icon" aria-label="Dismiss feedback notification" data-action="attention.read" onClick={()=>readItems(act,[item])}><X size={16}/></button>}</div></aside>;
}
