import React,{useEffect,useRef,useState} from 'react';
import './coordination.css';
import {WorkspaceReview} from './workspace-review.jsx';
import {WorkspaceAwareness} from './workspace-awareness.jsx';
import {AssignmentControls} from './assignment-controls.jsx';
import {CoordinationObservations} from './coordination-observations.jsx';
import {IntegrationEvidence} from './integration-evidence.jsx';

const keyOf=target=>JSON.stringify([target.sessionId,target.workerId||null]);
const labels={idle:'Ready for follow-up',working:'Working',running:'Working',starting:'Starting',queued:'Queued',stopping:'Interruption requested',completed:'Completed',interrupted:'Outcome unknown',error:'Needs attention',cancelled:'Cancelled',stopped:'Stopped'};
const empty={selected:[],cursors:{},results:{},drafts:{}};

// Selection uses the ordinary navigation owner, including private draft retention.
// A delayed response must not close a newer panel or another chat's visit.
export async function selectWorkspaceChat(dispatch,id,isCurrent){
 const response=await dispatch('session.select',{id});
 if(response?.accepted&&isCurrent(id))await dispatch('view.update',{patch:{panel:null}});
 return response;
}

export function CoordinationPanel({dispatch,sessionId,session,onOpenChat,expectedWorkspace}){
 const [controls,setControls]=useState(false),[scoped,setScoped]=useState(null),[scopeError,setScopeError]=useState('');
 const scopeReady=ready=>{setScoped(ready?sessionId:null);if(ready)setScopeError('');else setControls(false)};
 const scopeInvalid=message=>{setScoped(null);setControls(false);setScopeError(message)};
 return <div className="a-coordination a-workspace-work">
  {sessionId?<RelatedWork key={sessionId} dispatch={dispatch} sessionId={sessionId} session={session} onOpenChat={onOpenChat} expectedWorkspace={expectedWorkspace} onScopeReady={scopeReady} scopeInvalid={!!scopeError}/>:<p>Open a chat to inspect work in its workspace.</p>}
  {scopeError&&<p role="alert">{scopeError}</p>}
  {(!expectedWorkspace||scoped===sessionId)&&<details onToggle={event=>setControls(event.currentTarget.open)}><summary>Follow up or watch work</summary>
   {controls&&sessionId&&<CoordinationTargets dispatch={dispatch} sessionId={sessionId} expectedWorkspace={expectedWorkspace} onScopeInvalid={scopeInvalid}/>}
  </details>}
 </div>;
}

function CoordinationTargets({dispatch,sessionId,expectedWorkspace,onScopeInvalid}){
 // Session storage is isolated by origin and tab; client IDs rotate on reload.
 const storageKey='amplifier-coordination-v1';
 const [saved,setSaved]=useState(()=>{try{return {...empty,...JSON.parse(sessionStorage.getItem(storageKey)||'null')}}catch{return empty}});
 const inflight=useRef(new Set());
 const savedRef=useRef(saved),[items,setItems]=useState([]),[error,setError]=useState(''),[waitError,setWaitError]=useState(''),[notice,setNotice]=useState(''),[working,setWorking]=useState(null),[waiting,setWaiting]=useState(false);
 const write=update=>{const next=update(savedRef.current);savedRef.current=next;setSaved(next);try{sessionStorage.setItem(storageKey,JSON.stringify(next))}catch{setNotice('This browser could not save delivery cursors. Reports may appear again after reload.')}};
 const refresh=async()=>{try{const source=await dispatch('coordination.context',{sessionId},{feedback:false});if(!source.result.workspace)throw Error('The workspace is unavailable.');if(expectedWorkspace&&source.result.workspace!==expectedWorkspace){const message='This chat no longer belongs to the workspace being reviewed. Refresh the chat list.';setItems([]);onScopeInvalid?.(message);throw Error(message)}const response=await dispatch('coordination.list',{workspace:source.result.workspace,limit:100}, {feedback:false});setItems(response.result.items);if(response.result.truncated)setNotice('Showing the first 100 targets. Agents can address any known conversation directly.')}catch(error){setError(error.message)}};
 useEffect(()=>{refresh()},[]);
 useEffect(()=>{
  if(!saved.selected.length)return;
  let alive=true,retries=0;const controller=new AbortController();
  (async()=>{
   while(alive){
    const targets=saved.selected.map(target=>({...target,...(savedRef.current.cursors[keyOf(target)]?{afterCursor:savedRef.current.cursors[keyOf(target)]}:{})}));
    try{
     setWaiting(true);
     const response=await dispatch('coordination.wait',{targets,waitMs:30000,maxBytes:32768},{feedback:false,signal:controller.signal});
     if(!alive)return;
     retries=0;setWaitError('');
     const result=response.result;
     if(result.errors.length){setWaitError(result.errors.map(row=>row.error).join(' '));return}
     write(previous=>{
      const cursors={...previous.cursors},results={...previous.results};
      for(const row of result.targets){
       const key=keyOf(row.target),retained=results[key]||[],known=new Set(retained.map(item=>item.id));
       results[key]=[...retained,...row.results.filter(item=>!known.has(item.id)).map(item=>({...item,text:item.text.slice(0,4096),textTruncated:item.textTruncated||item.text.length>4096}))].slice(-8);
       cursors[key]=row.nextCursor;
      }
      const keep=new Set([...Object.keys(results).slice(-16),...previous.selected.map(keyOf)]);
      return {...previous,cursors:Object.fromEntries(Object.entries(cursors).filter(([key])=>keep.has(key))),results:Object.fromEntries(Object.entries(results).filter(([key])=>keep.has(key)))};
     });
     setItems(previous=>previous.map(item=>result.targets.find(row=>keyOf(row.target)===keyOf(item.target))||item));
     if(result.targets.some(row=>row.cursorGap))setNotice('Some older reports are no longer retained here. Open the saved conversation for its available history.');
    }catch(error){if(!alive||error.name==='AbortError')return;setWaitError('Waiting to reconnect: '+error.message);await new Promise(resolve=>{const done=()=>{clearTimeout(timer);controller.signal.removeEventListener('abort',done);resolve()};const timer=setTimeout(done,Math.min(15000,1000*2**retries++));controller.signal.addEventListener('abort',done,{once:true})})}
    finally{if(alive)setWaiting(false)}
   }
  })();
  return()=>{alive=false;controller.abort()};
 },[JSON.stringify(saved.selected)]);
 const toggle=target=>write(previous=>{const key=keyOf(target),exists=previous.selected.some(row=>keyOf(row)===key);return {...previous,selected:exists?previous.selected.filter(row=>keyOf(row)!==key):[...previous.selected,target].slice(0,8)}});
 const control=async(item,action)=>{
  const key=keyOf(item.target);if(inflight.current.has(key))return;inflight.current.add(key);
  const text=savedRef.current.drafts[key]||'';setWorking(key);setError('');
  try{
   const response=await dispatch(action,{...item.target,...(action==='coordination.followup'?{text}:{})});
   if(response.delivery==='unknown'){setError('Delivery is unknown. Work was not replayed. Inspect the target before sending another instruction.');return}
   if(action==='coordination.followup')write(previous=>({...previous,drafts:{...previous.drafts,[key]:''}}));
   setNotice(action==='coordination.interrupt'?'Interruption requested. Effects may already have happened.':'Follow-up accepted.');
   await refresh();
  }catch(error){setError(error.message)}finally{inflight.current.delete(key);setWorking(null)}
 };
 return <div className="a-coordination">
  <p>Follow up on a conversation or its workers without switching away from your current draft. Watch up to eight targets for reports or requests for attention.</p>
  <div className="a-dialog-actions"><button className="a-soft" data-action="coordination.list" onClick={refresh}>Refresh targets</button><button className="a-link" disabled={!saved.selected.length} onClick={()=>write(previous=>({...previous,selected:[]}))}>Stop watching</button><span role="status">{waiting?'Waiting for updates…':saved.selected.length?`${saved.selected.length} targets watched`:'Choose targets to watch'}</span></div>
  {error&&<p role="alert" className="a-alert">{error}</p>}{waitError&&<p role="alert" className="a-alert">{waitError}</p>}{notice&&<p role="status">{notice}</p>}
  {items.map(item=>{const key=keyOf(item.target),selected=saved.selected.some(row=>keyOf(row)===key),reports=saved.results[key]||[];return <section key={key} className="a-coordination-target" aria-label={`${item.kind==='worker'?'Worker':'Conversation'}: ${item.title}`}>
   <label><input type="checkbox" checked={selected} disabled={!selected&&saved.selected.length>=8} onChange={()=>toggle(item.target)}/><strong>{item.title}</strong> · {item.kind==='worker'?'Worker':'Conversation'}</label>
   <p className="a-caption">{labels[item.status]||item.status}{item.attention?' · Needs attention':''}</p>
   {reports.map(report=><article key={report.id} data-report-id={report.id}><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{report.text}</p>{report.textTruncated&&<small>Report excerpt. Open the saved conversation for more.</small>}</article>)}
   <details><summary>Follow up</summary><form onSubmit={event=>{event.preventDefault();control(item,'coordination.followup')}}>
    <label>Follow-up for {item.title}<textarea value={saved.drafts[key]||''} disabled={!item.canFollowup||working===key} onChange={event=>write(previous=>({...previous,drafts:{...previous.drafts,[key]:event.target.value}}))}/></label>
    <div className="a-dialog-actions"><button className="a-soft" data-action="coordination.followup" disabled={!item.canFollowup||!saved.drafts[key]?.trim()||working===key}>Send follow-up</button></div>
   </form></details><button type="button" className="a-link a-danger" data-action="coordination.interrupt" disabled={!item.canInterrupt||working===key} onClick={()=>control(item,'coordination.interrupt')}>Interrupt</button>
  </section>})}
 </div>;
}

function ResultFileStatus({response}){
 if(!response.references?.length)return null;
 if(response.artifactVerification==='not_requested')return <p className="a-caption">No file versions saved. These references are citations only.</p>;
 if(response.artifactVerification==='incomplete')return <p role="status">Some file versions were not saved. Check the reference details.</p>;
 return null;
}

export function RequestReturnState({state}){
 if(!state)return null;
 const subscription=state.subscription||{},notification=state.notification||{};
 const registered=subscription.registered===true?'Registered':subscription.registered===false?'Not registered':'Unknown';
 const text=value=>typeof value==='string'?value.replaceAll('_',' '):'unknown';
 return <div data-part="request-return-state" aria-label="Request return status">
  <p>Automatic return: {registered} · {text(subscription.status)}</p>
  {subscription.reason&&<p className="a-caption">{subscription.reason}</p>}
  <dl>
   <dt>Report</dt><dd>{text(state.response?.status)}{state.response?.outcome?' · '+text(state.response.outcome):''}</dd>
   <dt>Terminal qualification</dt><dd>{text(state.qualification?.status)}</dd>
   <dt>Return notification</dt><dd>{text(notification.status)}{notification.status==='accepted'?' (input admitted)':''}</dd>
   <dt>Exact-outcome acknowledgement</dt><dd>{text(state.acknowledgement?.status)}</dd>
   <dt>Saved file evidence</dt><dd>{text(state.artifacts?.status)}</dd>
  </dl>
  {notification.reason&&<p className="a-caption">{notification.reason}</p>}
  {state.observation?.coverage==='bounded'&&<p className="a-caption">Some exact references were omitted from this summary. Inspect the retained report below.</p>}
  <p className="a-caption">Delivery does not prove the requester consumed or accepted the result. Terminal qualification and acknowledgement are not independent artifact verification.</p>
 </div>;
}

function RequestCancellation({dispatch,sessionId,request,onChanged}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[unknown,setUnknown]=useState(false);
 const operation=useRef(null),inflight=useRef(false);
 const cancellation=request.cancellation;
 const report=request.response||{};
 const finished=report.status==='sealed'&&report.kind==='result'&&['success','failed','cancelled','declined'].includes(report.outcome);
 const canCancel=request.senderSessionId===sessionId&&!cancellation&&!finished&&['queued','pending_steer','submitting','accepted','applied','unknown'].includes(request.delivery);
 const queued=['queued','pending_steer'].includes(request.delivery);
 const cancel=async()=>{
  if(inflight.current)return;
  inflight.current=true;setBusy(true);setError('');
  // Retain exactly one operation across a lost acknowledgement; do not turn
  // a retry into another request or send the original work again.
  operation.current??={id:crypto.randomUUID(),args:{sessionId,requestId:request.requestId,reason:note}};
  try{
   const answer=await dispatch('coordination.cancel',operation.current.args,{id:operation.current.id,feedback:false});
   if(answer?.accepted!==true)throw Error(answer?.error||'Cancellation could not be confirmed.');
   setUnknown(false);await onChanged();
  }catch(error){setUnknown(true);setError('Cancellation could not be confirmed. Refresh or check again; the original work was not resent. '+error.message)}
  finally{inflight.current=false;setBusy(false)}
 };
 return <div data-part="request-cancellation">
  {cancellation&&<p role="status">{cancellation.detail}</p>}
  {canCancel&&<details><summary>{queued?'Withdraw this request':'Ask to stop this request'}</summary>
   <p>{queued?'Remove this request from the queue. Other work can continue.':'Ask the recipient to stop only this request at its next check. Work may continue until it responds.'}</p>
   <label>Optional guidance<textarea value={note} disabled={busy||unknown} maxLength={1000} onChange={event=>setNote(event.target.value)}/></label>
   <button className="a-link" disabled={busy} data-action="coordination.cancel" onClick={cancel}>{busy?'Sending…':unknown?'Check cancellation again':queued?'Withdraw request':'Request cancellation'}</button>
  </details>}
  {error&&<p role="alert">{error}</p>}
 </div>;
}

export function RelatedWork({dispatch,sessionId,session,onOpenChat,expectedWorkspace,onScopeReady,scopeInvalid=false}){
 const visit=useRef(0),openingRef=useRef(false),sectionRef=useRef(null),refreshFocus=useRef(null);
 const [review,setReview]=useState(false);
 const [opening,setOpening]=useState(null);
 const [refreshing,setRefreshing]=useState(false);
 const [next,setNext]=useState(null),[recap,setRecap]=useState(false);
 const [items,setItems]=useState([]),[context,setContext]=useState({requests:[]}),[error,setError]=useState(''),[inspected,setInspected]=useState({}),[original,setOriginal]=useState(null);
 useEffect(()=>{
  if(refreshing)return;
  const focused=refreshFocus.current;refreshFocus.current=null;
  if(focused?.visit===visit.current&&sectionRef.current?.contains(focused.element)&&document.activeElement===document.body)focused.element.focus({preventScroll:true});
 },[refreshing]);
 const refresh=async()=>{
  const current=++visit.current;setError('');setRefreshing(true);
  refreshFocus.current=sectionRef.current?.contains(document.activeElement)?{visit:current,element:document.activeElement}:null;
  // Keep the verified view mounted while checking again. Unmounting discards
  // child acknowledgments and disclosure state after their own save/refresh.
  // The region is inert until verified; a failed read discards stale scope.
  if(expectedWorkspace)onScopeReady?.(false);
  try{
   const state=await dispatch('coordination.context',{sessionId},{feedback:false});
   if(current!==visit.current)return;
   if(state?.accepted!==true||!state.result)throw Error(state?.error||'Workspace work could not be read. Refresh to try again.');
   if(!state.result.workspace)throw Error('The workspace is unavailable.');
   if(expectedWorkspace&&state.result.workspace!==expectedWorkspace)throw Error('This chat no longer belongs to the workspace being reviewed. Refresh the chat list.');
   const peers=await dispatch('coordination.list',{workspace:state.result.workspace,limit:20,includeWorkers:false},{feedback:false});
   if(current!==visit.current)return;
   if(peers?.accepted!==true||!Array.isArray(peers.result?.items))throw Error(peers?.error||'Workspace chats could not be read. Refresh to try again.');
   setContext(state.result);setInspected({});setItems(peers.result.items);setNext(peers.result.nextOffset??null);onScopeReady?.(true);
  }catch(error){
   if(current===visit.current&&expectedWorkspace){setContext({requests:[]});setItems([]);setNext(null);setInspected({});setOriginal(null);setReview(false);setRecap(false)}
   throw error;
  }finally{if(current===visit.current)setRefreshing(false)}
 };
 useEffect(()=>{setInspected({});setOriginal(null);setContext({requests:[]});setItems([]);setNext(null);setError('');refresh().catch(error=>{if(visit.current===current)setError(error.message)});const current=visit.current;return()=>{visit.current++}},[sessionId,expectedWorkspace]);
 const more=async()=>{const current=visit.current;try{const response=await dispatch('coordination.list',{workspace:context.workspace,offset:next,limit:20,includeWorkers:false},{feedback:false});if(current!==visit.current)return;setItems(previous=>[...new Map([...previous,...response.result.items].map(row=>[row.target.sessionId,row])).values()]);setNext(response.result.nextOffset??null)}catch(error){if(current===visit.current)setError(error.message)}};
 const inspectResult=async requestId=>{const current=visit.current;try{const value=await dispatch('coordination.result',{requestId},{feedback:false});if(current!==visit.current)return;setInspected(previous=>({...previous,[requestId]:value.result}))}catch(error){if(current===visit.current)setError(error.message)}};
 // Only exact messages linked by these requests use the broader owned-history read.
 // Workspace discovery and awareness keep their existing scope.
 const reveal=async(sid,messageId)=>{const current=visit.current;try{const response=await dispatch('coordination.read',{sessionId:sid,messageId,scope:'all',textLimit:4000},{feedback:false});if(current!==visit.current)return;setOriginal(response.result.message)}catch(error){if(current===visit.current)setError(error.message)}};
 const openArtifact=async(requestId,reference,responseRevision)=>{
  const [path,expectedSha256]=reference.split(/@sha256:/);
  try{const value=await dispatch('canvas.openFile',{sessionId,workspace:context.workspace,path,expectedSha256,referenceRequestId:requestId,...(responseRevision?{referenceResponseRevision:responseRevision}:{})});if(value.result?.status!=='opened')setError(value.result?.message||'The referenced artifact is unavailable.')}catch(error){setError(error.message)}
 };
 const openChat=async id=>{
  if(openingRef.current||!onOpenChat)return;
  openingRef.current=true;setOpening(id);setError('');const current=visit.current;
  try{const response=await onOpenChat(id);if(response?.accepted!==true)throw Error(response?.error||'The chat could not be opened. Your current view was kept.')}
  catch(error){if(current===visit.current)setError(error.message)}
  finally{openingRef.current=false;if(current===visit.current)setOpening(null)}
 };
 const memberLabels=Object.fromEntries(items.map(row=>[row.target.sessionId,row.title||'Untitled chat']));
 memberLabels[sessionId]=session?.title||memberLabels[sessionId]||'This chat';
 const requestName=row=>{
  const outgoing=row.senderSessionId===sessionId,sid=outgoing?row.target.sessionId:row.senderSessionId,recorded=outgoing?row.target:row.sender;
  return memberLabels[sid]||(recorded?.sessionId===sid?[recorded.title||'Untitled chat',recorded.workspaceLabel].filter(Boolean).join(' · '):'Related chat');
 };
 const attention=items.filter(row=>row.attention);
 const showSection=name=>{const target=sectionRef.current?.querySelector('[data-review-target="'+name+'"]');if(target){target.open=true;target.scrollIntoView?.({block:'nearest'})}};
 return <section ref={sectionRef} className="a-coordination-related" aria-label="Workspace work overview" inert={refreshing}>
  <p>See what is happening around this chat. Inspecting keeps your conversation and draft in place.</p>
  {error&&<p role="alert">{error}</p>}
  <button className="a-link" onClick={()=>refresh().catch(error=>setError(error.message))}>Refresh workspace work</button>
  {(!expectedWorkspace||(!scopeInvalid&&context.workspace===expectedWorkspace))&&<>
  <details onToggle={event=>{if(event.target===event.currentTarget)setReview(event.currentTarget.open)}}><summary>Review before continuing</summary>{review&&<WorkspaceReview key={'review:'+sessionId} dispatch={dispatch} sessionId={sessionId} workspace={context.workspace} requests={context.requests} onOpenChat={onOpenChat} onShowSection={showSection}/>}</details>
  <WorkspaceAwareness key={'workspace:'+sessionId} dispatch={dispatch} sessionId={sessionId} context={context} refresh={refresh}/>
  <section aria-label="Attention and requests"><h3>Attention and requests</h3>
   {attention.map(row=><p key={row.target.sessionId}><strong>{row.title||'Untitled chat'}</strong> · {labels[row.status]||row.status}{!['error','interrupted','cancelled','stopped'].includes(row.status)&&' · Needs attention'}</p>)}
   {!attention.length&&!context.requests.length&&<p>No attention requests in the loaded workspace page or this chat’s recent requests.</p>}
   {context.requests.slice(0,8).map(row=>{const response=[row.response,row.terminalOutcome].filter(Boolean).sort((a,b)=>(b.outcomeRevision||0)-(a.outcomeRevision||0))[0];return <p key={row.requestId}><strong>{requestName(row)}</strong> · {row.senderSessionId===sessionId?'Request sent':'Request received'} · {response?(response.outcome==='success'?(response.qualified?'Completed with terminal evidence':'Success reported; not qualified'):response.outcome?.replaceAll('_',' ')||response.status):(['accepted','applied','submitting'].includes(row.delivery)?'Awaiting a result':row.delivery)}</p>})}
   <p className="a-caption">{context.requests.length>8?`${context.requests.length} recent requests retained; showing the latest eight. `:''}Recent requests are limited to this chat. A question or deferral keeps the request open; a failed or unknown result is not success.</p>
  </section>
  <AssignmentControls key={'assignment:'+sessionId} dispatch={dispatch} sessionId={sessionId} memberLabels={memberLabels}/>
  {session&&<IntegrationEvidence key={'integration:'+sessionId} session={session} act={dispatch}/>}
  <details onToggle={event=>setRecap(event.currentTarget.open)}><summary>Changes since your last view and diagnostics</summary>{recap&&<CoordinationObservations key={'observations:'+sessionId} dispatch={dispatch} sessionId={sessionId}/>}</details>
  <details><summary>Workspace chat list</summary>
   {items.map(row=><div key={row.target.sessionId} data-workspace-chat-id={row.target.sessionId}>{row.title||'Untitled chat'}{row.target.sessionId===sessionId?' (this chat)':''} · {labels[row.status]||row.status} · {row.collaboration?'Commissioned chat':'Started independently'} <button type="button" className="a-link" data-action="session.select" aria-label={'Open chat: '+(row.title||'Untitled chat')} disabled={opening!==null||!onOpenChat} onClick={()=>openChat(row.target.sessionId)}>{opening===row.target.sessionId?'Opening…':'Open chat'}</button><details><summary>Chat identity</summary><code>{row.target.sessionId}</code></details></div>)}
   {next!==null&&<button className="a-link" onClick={more}>Load more workspace chats</button>}
  </details>
  <details><summary>Exact requests, messages and saved results</summary>
  <p className="a-caption">Inspect retained evidence explicitly. Qualification links a report to a completed turn; inspect artifacts independently.</p>
  {original&&<article aria-label="Original message"><strong>{original.role} · {original.sessionId}</strong><p style={{whiteSpace:'pre-wrap'}}>{original.text}</p>{original.truncated&&<small>Bounded excerpt; original retained.</small>}<button className="a-link" onClick={()=>setOriginal(null)}>Close original message</button></article>}
  {context.requests.map(row=>{const result=inspected[row.requestId],receipt=result?.receipt||row,response=[receipt.response,receipt.terminalOutcome].filter(Boolean).sort((a,b)=>(b.outcomeRevision||0)-(a.outcomeRevision||0))[0];return <article key={row.requestId} data-request-id={row.requestId}>
   <small>{row.senderSessionId===sessionId?'Sent to':'Received from'} {requestName(row)} · {row.delivery}</small><details><summary>Request identity</summary><code>{row.requestId}</code></details>
   <button className="a-link" onClick={()=>inspectResult(row.requestId)}>Inspect exact result</button>
   <button className="a-link" onClick={()=>reveal(row.target.sessionId,row.messageId)}>Open submitted message</button>
   <RequestReturnState state={result?.returnState}/>
   <RequestCancellation dispatch={dispatch} sessionId={sessionId} request={receipt} onChanged={refresh}/>
   {response&&<div data-part="exact-result"><p>Result: {response.status} · {response.outcome} · {response.qualified?'Qualified terminal evidence':'Not qualified'}</p><p>{response.text}</p>{response.detail&&<p role="alert">{response.detail}</p>}
    {response.terminalMessageId&&response.status==='sealed'&&<button className="a-link" data-message-id={response.terminalMessageId} onClick={()=>reveal(row.target.sessionId,response.terminalMessageId)}>Open exact terminal message</button>}
    <ResultFileStatus response={response}/>
    {(response.references||[]).map(reference=>{const artifact=response.artifacts?.find(item=>item.reference===reference);return <p key={reference}><code>{reference}</code>{artifact&&<small> · {artifact.status==='retained'?'Exact version saved':artifact.detail||artifact.status}</small>}{((!artifact&&!response.artifactVerification)||artifact?.status==='retained')&&/^[^@\n]+@sha256:[a-f0-9]{64}$/.test(reference)&&!reference.includes('://')&&<button className="a-link" onClick={()=>openArtifact(row.requestId,reference,response.responseRevision)}>Open referenced artifact</button>}{artifact?.status==='retained'&&<a className="a-link" href={'/api/outputs/'+artifact.outputId+'/content'} download>Download saved version</a>}</p>})}
    {(response.messageIds||[]).map(messageId=><button key={messageId} className="a-link" onClick={()=>reveal(row.target.sessionId,messageId)}>Open evidence message {messageId}</button>)}
   </div>}{result?.revisions?.some(item=>item.responseRevision!==receipt.response?.responseRevision)&&<details>
    <summary>Earlier replies</summary>
    {result.revisions.filter(item=>item.responseRevision!==receipt.response?.responseRevision).map(item=><div key={item.responseRevision} data-response-revision={item.responseRevision}>
     <p>Reply {item.responseRevision} · {item.outcome} · {item.qualified?'Qualified terminal evidence':'Not qualified'}</p><p>{item.text}</p>
     <ResultFileStatus response={item}/>
     {(item.artifacts||[]).filter(artifact=>artifact.status!=='retained').map(artifact=><p key={artifact.reference}><code>{artifact.reference}</code> · {artifact.detail||artifact.status}</p>)}
     {(item.artifacts||[]).filter(artifact=>artifact.status==='retained').map(artifact=><p key={artifact.outputId}>
      <button className="a-link" onClick={()=>openArtifact(row.requestId,artifact.reference,item.responseRevision)}>Open saved reply {item.responseRevision}</button>
      <a className="a-link" href={'/api/outputs/'+artifact.outputId+'/content'} download>Download saved reply {item.responseRevision}</a>
     </p>)}
    </div>)}
   </details>}{result&&<p className="a-caption">{result.detail}</p>}
  </article>})}
 </details>
 </>}
 </section>;
}
