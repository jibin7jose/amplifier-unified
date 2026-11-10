import React,{useEffect,useRef,useState} from 'react';

const statuses={working:'Working',running:'Working',starting:'Starting',ready:'Preparing',queued:'Queued',pending:'Pending',stopping:'Stop requested',idle:'Waiting for your next message',completed:'Completed',interrupted:'Interrupted; outcome needs review',error:'Needs attention',failed:'Needs attention',cancelled:'Cancelled',stopped:'Stopped'};
const time=value=>value?new Date(value*1000).toLocaleString():'Time unavailable';

export function reviewSteps(row){
 const steps=[];
 if(row.approvals||row.questions)steps.push('Review pending decisions');
 if(row.unverifiedApprovals)steps.push('Open the chat to reconcile saved permission requests');
 if(row.expiredApprovals)steps.push('Review permissions that expired after the app restarted');
 if(row.handoffs?.items?.length)steps.push('Reconcile the unfinished move before changing its location');
 if(row.configurationPending||row.configurationBusy)steps.push('Review pending settings or file permissions');
 if(row.historyReadOnlyReason)steps.push('Inspect the saved chat’s continuation limitation');
 if(row.workspaceAvailable===false)steps.push('Check the unavailable workspace folder');
 if(row.executionUnverified)steps.push('Confirm whether the previously active work is still running');
 if(['interrupted','error','failed'].includes(row.status))steps.push('Inspect prior effects before repeating any work');
 if(!steps.length)steps.push(['working','running','starting','ready','queued','pending','stopping'].includes(row.status)?'Let existing work settle or open the chat to steer it':'Review its latest result and give it a current objective');
 return steps;
}

// A visit owns its observations. Closing, refreshing or switching the source
// invalidates late replies without changing accepted work or private drafts.
export function WorkspaceReview({dispatch,sessionId,workspace,requests=[],onOpenChat,onShowSection}){
 const visit=useRef(0),busyRef=useRef(false),runtimeVisit=useRef(0),openingRef=useRef(false),inspectingRef=useRef(false),openingVisit=useRef(0);
 const [value,setValue]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [pages,setPages]=useState([null]),[opening,setOpening]=useState(null),[worker,setWorker]=useState(null),[inspecting,setInspecting]=useState(false);
 const read=async(after=null,nextPages=[null])=>{
  if(busyRef.current)return;busyRef.current=true;setBusy(true);setError('');
  const current=++visit.current;
  try{
   const response=await dispatch('coordination.review',{sessionId,limit:20,...(after?{after}:{})},{feedback:false});
   if(current!==visit.current)return;
   if(!response?.accepted||response.result?.sessionId!==sessionId||workspace&&response.result?.workspace!==workspace)throw Error('The review scope changed. Refresh this workspace before continuing.');
   setValue(response.result);setPages(nextPages);
  }catch(error){if(current===visit.current)setError(error.message)}
  finally{if(current===visit.current){busyRef.current=false;setBusy(false)}}
 };
 useEffect(()=>{setValue(null);setWorker(null);setInspecting(false);inspectingRef.current=false;setOpening(null);openingRef.current=false;setPages([null]);busyRef.current=false;void read();return()=>{visit.current++;runtimeVisit.current++;openingVisit.current++}},[sessionId,workspace]);
 const inspectWorker=async()=>{
  if(inspectingRef.current)return;inspectingRef.current=true;const current=++runtimeVisit.current;setInspecting(true);setError('');
  try{const response=await dispatch('runtime.provenance',{sessionId},{feedback:false});if(current!==runtimeVisit.current)return;if(!response?.accepted||!response.result)throw Error('The worker observation was not confirmed.');setWorker({...response.result,reviewedAt:Date.now()/1000})}
  catch(error){if(current===runtimeVisit.current)setError(error.message)}finally{if(current===runtimeVisit.current){inspectingRef.current=false;setInspecting(false)}}
 };
 const open=async id=>{if(openingRef.current||!onOpenChat)return;openingRef.current=true;setOpening(id);setError('');const current=++openingVisit.current;try{const response=await onOpenChat(id);if(response?.accepted!==true)throw Error(response?.error||'The chat could not be opened. Your current view was kept.')}catch(error){if(current===openingVisit.current)setError(error.message)}finally{if(current===openingVisit.current){openingRef.current=false;setOpening(null)}}};
 return <section className="a-workspace-review" aria-label="Review before continuing">
  <p>Check what changed, resolve unfinished decisions, then give each chat its next objective. Reviewing does not resume work or change anyone’s responsibility.</p>
  <button type="button" className="a-link" disabled={busy} data-action="coordination.review" onClick={()=>read()}>Refresh review</button>
  {busy&&<p role="status">Reading saved workspace observations…</p>}{error&&<p role="alert">{error}</p>}
  {value&&<>
   <p className="a-caption">Observed {time(value.capturedAt)}. This page shows {value.items.length} saved chats; ongoing work can change after observation.</p>
   <section aria-label="Chats to review"><h3>Review each chat</h3>
    {!value.items.length&&<p>No chats on this page. Refresh to read current workspace membership.</p>}
    {value.items.map(row=><article key={row.sessionId} data-review-chat={row.sessionId}>
     <h4>{row.title}{row.sessionId===sessionId?' (this chat)':''}</h4>
     <p>{row.executionUnverified?'Previously active; current execution unverified':statuses[row.status]||row.status}{row.activityObserved?'':' · Saved status'} · {row.commissioned?'Commissioned chat':'Started independently'}</p>
     <ul>{reviewSteps(row).map(step=><li key={step}>{step}.</li>)}</ul>
     {!!row.unverifiedApprovals&&<p>{row.unverifiedApprovals} saved permission requests have no observed live waiter. They may have expired after restart.</p>}
     {!!row.expiredApprovals&&<p>{row.expiredApprovals} earlier permission requests expired. Inspect the saved work before asking again.</p>}
     {!!(row.approvals||row.questions)&&<p>{row.approvals} pending approvals · {row.questions} pending questions</p>}
     {row.responseAt&&<p className="a-caption">A response finished {time(row.responseAt)}. This does not establish that its proposed changes were merged or deployed.</p>}
     <button type="button" className="a-soft" data-action="session.select" disabled={opening!==null||!onOpenChat} onClick={()=>open(row.sessionId)} aria-label={'Open chat to review: '+row.title}>{opening===row.sessionId?'Opening…':'Open chat to review'}</button>
     <details><summary>Saved location and decisions</summary><p><code>{row.executionDirectory||'Execution folder unavailable'}</code></p><p>Saved placement revision: {row.executionRevision??'Unavailable'}. Files and branch changes were not inspected.</p>{row.historyReadOnlyReason&&<p>{row.historyReadOnlyReason}</p>}{row.handoffs?.items?.map(item=><p key={item.id}>Move {item.phase==='unknown'?'outcome unknown':'pending'} · {time(item.updatedAt)}</p>)}{row.handoffs?.available===false&&<p>Older move details are not available in this saved summary. Open the chat’s working copies to inspect them.</p>}{row.handoffs?.historyLimited&&<p>Only a bounded set of move observations was reviewed. Open working copies for older moves.</p>}{row.handoffs?.truncated&&<p>More unfinished moves are retained. Open the chat’s working copies to inspect them.</p>}<code>{row.sessionId}</code></details>
    </article>)}
    <div className="a-dialog-actions">{pages.length>1&&<button className="a-link" disabled={busy} onClick={()=>read(pages.at(-2),pages.slice(0,-1))}>Previous review page</button>}{value.nextAfter&&<button className="a-link" disabled={busy} onClick={()=>read(value.nextAfter,[...pages,value.nextAfter])}>Next review page</button>}</div>
   </section>
   <section aria-label="Responsibility to review"><h3>Review shared responsibility</h3>
    {value.claims.status==='unassigned'?<p>This chat has no shared assignment. Independent chats keep their own work and controls.</p>:value.claims.status==='unavailable'?<p>Assignment responsibility is unavailable. Inspect the existing assignment before changing or repeating work.</p>:<>
     {value.claims.status!=='active'&&<p>The assignment is {value.claims.status}; reviewing does not reopen it.</p>}
     {value.claims.items.map(item=><p key={item.taskId}><strong>{item.title}</strong> · {item.detail}</p>)}
     {!value.claims.total&&<p>No stale claims observed in this chat’s assignment. A retained claim is responsibility, not proof that a worker is running.</p>}
     {value.claims.truncated&&<p>Showing eight of {value.claims.total} claims needing review.</p>}
    </>}
    <button type="button" className="a-link" onClick={()=>onShowSection?.('assignment')}>Review shared tasks</button>
    <p className="a-caption">Claims do not lock files. Keep overlapping source changes in separate working copies and review existing effects before release or reassignment.</p>
   </section>
   <section aria-label="Results and source review"><h3>Review results and source changes</h3>
    <p>Read the latest saved response before continuing. Old notes about approvals, PRs or releases may need a fresh check with their source.</p>
    {!!requests.length&&<p>{Math.min(requests.length,32)} recent requests are available for this chat under Attention and requests. A reported success and qualified terminal evidence have different meanings.</p>}
    {value.sourceObservations.map(item=><p key={item.id}>Saved {{capture:'candidate',check:'source check',collection:'combined result'}[item.kind]||'observation'}: {item.status||'Unknown'} · {time(item.capturedAt)}</p>)}
    {!value.sourceObservations.length&&<p>No retained source-check observations found for this chat.</p>}
    <p className="a-caption">These are past observations. Current files, external PR status and test results were not rechecked.</p>
    <button type="button" className="a-link" onClick={()=>onShowSection?.('integration')}>Review source checks</button>
   </section>
   <details><summary>App update and worker details</summary>
    <p>Serving app: {value.installedApp.serverVersion}. Its version does not establish which code existing workers loaded.</p>
    <p>Worker adoption has not been verified. Inspecting observes an already running worker; it does not start or refresh one.</p>
    <button type="button" className="a-link" data-action="runtime.provenance" disabled={inspecting} onClick={inspectWorker}>{inspecting?'Inspecting loaded sources…':'Inspect this chat’s loaded sources'}</button>
    {worker&&<><p role="status">Loaded-source observation: {worker.status} · {time(worker.reviewedAt)}{worker.reason?' · '+worker.reason:''}</p><p>Current file hashes do not prove the exact bytes imported earlier. This observation is not an automatic release-adoption check.</p><details><summary>Exact worker observation</summary><pre className="a-state-view">{JSON.stringify(worker,null,2)}</pre></details></>}
    <details><summary>Serving frontend identity</summary><code>{value.installedApp.frontend.id}</code><p>{value.installedApp.frontend.version}</p></details>
   </details>
  </>}
 </section>;
}
