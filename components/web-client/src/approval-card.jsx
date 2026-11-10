import React,{useEffect,useRef,useState} from 'react';
import {Check} from 'lucide-react';

// Private bundled scheduling hint, not permission. Only this exact card path
// leaves the document queue; grant administration keeps its existing ordering.
export function isCapabilityCardAction(action,args,meta){
 return meta?.capabilityCard===true&&typeof args?.sessionId==='string'&&!!args.sessionId
  &&typeof args.id==='string'&&args.id.startsWith('capability:')
  &&(action==='capability.read'||action==='approval.respond'||action==='capability.revoke'&&args.dismissRequest===true);
}

// Client-local ordering only. The host still owns interruption, request hashes
// and permission decisions. A Stop invalidates reads even if its POST is queued.
export function createCapabilityStopFence({request,clientId,timeoutMs=5000}={}){
 const sessions=new Map(),reads=new WeakSet();
 const state=sessionId=>{
  if(!sessions.has(sessionId))sessions.set(sessionId,{epoch:0,stops:new Set()});
  return sessions.get(sessionId);
 };
 // One passive, bounded lookup per retained command at a time. A missing,
 // malformed or failed lookup is still unknown, never evidence of non-acceptance.
 const recover=stop=>{
  if(stop.recovery)return stop.recovery;
  const identity={sessionId:stop.sessionId,commandId:stop.commandId,clientId:stop.clientId};
  const controller=new AbortController();let timer;
  stop.recovery=(async()=>{
   try{
    const receipt=await Promise.race([
     Promise.resolve().then(()=>request('/api/conversation/stop-status',{method:'POST',body:identity,signal:controller.signal})),
     new Promise(resolve=>{timer=setTimeout(()=>{controller.abort();resolve(null)},timeoutMs)}),
    ]);
    if(receipt?.status!=='accepted'||!Object.keys(identity).every(key=>receipt[key]===identity[key]))return;
    if(stop.outcome==='unknown'&&stop.current.stops.has(stop)){
     stop.outcome='accepted';stop.current.stops.delete(stop);
    }
    // Do not reset epochs or upgrade a read that began while this Stop was held.
   }catch{}finally{clearTimeout(timer);stop.recovery=null}
  })();
  return stop.recovery;
 };
 return {
  beginStop(sessionId,commandId){
   const current=state(sessionId),stop={sessionId,commandId,clientId,current,outcome:'pending',recovery:null};
   current.epoch++;current.stops.add(stop);return stop;
  },
  settleStop(stop,outcome){
   if(!stop?.current.stops.has(stop))return; // A late failure cannot undo acceptance.
   stop.outcome=outcome;
   if(outcome==='accepted'||outcome==='not-applied')stop.current.stops.delete(stop);
   // Unknown stays fenced. A newer Stop cannot erase this unresolved command.
  },
  reconcile(sessionId){
   if(!request||!clientId)return Promise.resolve();
   const current=sessionId===undefined?[...sessions.values()]:[sessions.get(sessionId)];
   return Promise.all(current.flatMap(row=>[...(row?.stops||[])])
    .filter(stop=>stop.outcome==='unknown').map(recover));
  },
  beginRead({sessionId,id}){
   const current=state(sessionId);
   return {sessionId,id,current,epoch:current.epoch,eligible:current.stops.size===0};
  },
  finishRead(start,requestHash){
   const token=Object.freeze({...start,requestHash});reads.add(token);return token;
  },
  assertMutation(args,token){
   const current=state(args.sessionId);
   if(!token||!reads.has(token)||token.sessionId!==args.sessionId||token.id!==args.id||
      !token.eligible||token.current!==current||token.epoch!==current.epoch||
      current.stops.size||!token.requestHash||token.requestHash!==args.expectedRequestHash)
    throw Object.assign(Error('Stop was requested or this permission review is no longer current. No decision was sent. Review again after the Stop outcome is confirmed; your note is kept.'),{code:'capability_stop_fenced'});
  },
 };
}

// Classify only the Stop action POST, never a subsequent state/recovery error.
// session_busy is checked before the Stop branch (service.py ownership guard).
// Generic accepted:false/4xx/5xx and transport/parse/abort errors prove no such thing.
export const stopFailureOutcome=(error,postStarted)=>!postStarted||
 (error?.code==='session_busy'&&error.status===409)?'not-applied':'unknown';

function CapabilityApprovalCard({approval,sessionId,dispatch,freshnessKey}){
 const [snapshot,setSnapshot]=useState(null),[comment,setComment]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[review,setReview]=useState(false);
 const sequence=useRef(0),locked=useRef(false),mounted=useRef(true);
 const cardDispatch=(action,args,readToken)=>dispatch(action,args,{capabilityCard:true,...(readToken?{capabilityRead:readToken}:{})});
 const read=async()=>{
  const ticket=++sequence.current;
  const receipt=await cardDispatch('capability.read',{sessionId,id:approval.id});
  if(!receipt?.accepted||!receipt.result?.request)throw Error('Current permission could not be checked. No decision was sent.');
  if(mounted.current&&ticket===sequence.current)setSnapshot(receipt.result);
  return receipt.result;
 };
 useEffect(()=>{
  mounted.current=true;
  const refresh=()=>read().catch(e=>mounted.current&&setError(e.message));
  refresh();window.addEventListener('focus',refresh);
  return()=>{mounted.current=false;sequence.current++;window.removeEventListener('focus',refresh)};
 },[sessionId,approval.id,approval.status,approval.dismissedAt,freshnessKey]);
 const request=snapshot?.request;
 useEffect(()=>{
  if(!request?.canDecide||!request.expiresAt)return;
  const timer=setTimeout(()=>read().catch(e=>setError(e.message)),Math.min(2147483647,Math.max(0,request.expiresAt*1000-Date.now()+20)));
  return()=>clearTimeout(timer);
 },[request?.requestHash,request?.expiresAt]);
 const perform=async decision=>{
  if(locked.current||!request)return;
  locked.current=true;setBusy(true);setError('');
  try{
   // A passive preflight never turns a click on old text into approval of new text.
   const preflight=await read(),current=preflight.request;
   if(!mounted.current)return;
   if(current.requestHash!==request.requestHash)throw Error('This request changed. Review its current state before deciding. Your note is kept.');
   if(decision==='dismiss'){
    if(!current.canDismiss)throw Error('This request cannot be dismissed as an old request.');
    const receipt=await cardDispatch('capability.revoke',{sessionId,id:approval.id,dismissRequest:true,expectedRevision:current.revision,expectedRequestHash:current.requestHash,...(comment.trim()?{comment:comment.trim()}:{})},preflight.capabilityRead);
    if(!receipt?.accepted)throw Error('Dismissal was not confirmed. Nothing was retried.');
   }else{
    if(!current.canDecide)throw Error('This request is no longer awaiting a decision. Your note is kept.');
    const receipt=await cardDispatch('approval.respond',{sessionId,id:approval.id,decision,expectedRequestHash:current.requestHash,...(comment.trim()?{comment:comment.trim()}:{})},preflight.capabilityRead);
    if(!receipt?.accepted)throw Error('The decision was not confirmed. Your note is kept; nothing was retried.');
    if(receipt.decisionApplied===false)setError('No decision was applied. The current request state is shown below; your note is kept.');
   }
   await read();
  }catch(e){
   if(mounted.current)setError(e.message);
   // Reconcile display only. Never repeat an uncertain decision.
   try{await read()}catch{}
  }finally{locked.current=false;if(mounted.current)setBusy(false)}
 };
 return <div data-approval-id={approval.id}>
  <strong>{request?.canDecide?'Allow configuration changes?':'Configuration request'}</strong>
  <p>{request?.reason||approval.prompt||approval.description}</p>
  <p role="status">{request?.detail||approval.detail||'Checking current permission…'}</p>
  {approval.deliveryError&&<p role="status">{approval.deliveryError}</p>}
  {request?.effects?.length>0?<div><ul>{request.effects.map(effect=><li key={effect}>{snapshot.effects[effect]||effect}</li>)}</ul><p>Scopes: {request.scopes.join(', ')}. This conversation only, until Stop, revocation, or expiry.</p></div>:approval.details&&<pre>{typeof approval.details==='string'?approval.details:JSON.stringify(approval.details,null,2)}</pre>}
  {request&&request.status!=='unavailable'&&<p>{request.expiresAt?`Expires: ${new Date(request.expiresAt*1000).toLocaleString()}`:'No time-based expiry was specified.'}</p>}
  {(request?.canDecide||comment)&&<label className="a-field">Note to Amplifier (optional)
   <textarea aria-label="Note to Amplifier (optional)" maxLength={4000} rows={3} value={comment} disabled={busy}
    onChange={e=>setComment(e.target.value)}/>
  </label>}
  {!request?.canDecide&&comment&&<p className="a-caption">Dismissal keeps this note in permission history only. It is not sent to Amplifier.</p>}
  {error&&<p role="alert">{error}</p>}
  <div className="a-dialog-actions">
   {request?.canDecide&&<><button className="a-primary" data-action="approval.respond" disabled={busy} onClick={()=>perform('approve')}><Check/>Allow</button><button className="a-soft" data-action="approval.respond" disabled={busy} onClick={()=>perform('deny')}>Deny</button></>}
   {request?.canDismiss&&<button className="a-soft" data-action="capability.revoke" disabled={busy} onClick={()=>perform('dismiss')}>Dismiss old request</button>}
   <button className="a-link" data-action="capability.read" disabled={busy} onClick={async()=>{setReview(true);try{await read();setError('')}catch(e){setError(e.message)}}}>Review current scope</button>
  </div>
  {review&&snapshot&&<section aria-label="Current configuration scope">
   <h3>Current configuration scope</h3>
   <p>{snapshot.accessMode==='full'?'Full access is selected for this chat. An expired request does not turn it off.':snapshot.request.scopeCovered==null?'Coverage cannot be confirmed from the available scope and permission records.':snapshot.request.scopeCovered?'Existing permission covers the changes listed in this request. No new permission is needed for that scope.':'No current permission covers all changes listed in this request.'}</p>
   <p>This review does not request permission, start work, or repeat an earlier operation. Dismissal does not undo any earlier effects.</p>
   <ul>{snapshot.grants.filter(grant=>grant.usable).map(grant=><li key={grant.id}>{grant.effects.map(effect=>snapshot.effects[effect]||effect).join('; ')} — {grant.scopes.join(', ')}</li>)}</ul>
   {snapshot.truncated&&<p>Showing the newest {snapshot.limit} saved grants. Coverage checks current applicable grants separately; an incomplete check stays unconfirmed.</p>}
   <button className="a-link" onClick={()=>setReview(false)}>Close scope review</button>
  </section>}
 </div>;
}

export function approvalGroups(session){
 const approvals=session?.approvals||[];
 const pending=approvals.filter(row=>row.status==='pending'||!row.status);
 const retired=approvals.filter(row=>row.id?.startsWith('capability:')&&row.status==='expired'&&!row.dismissedAt);
 const history=approvals.filter(row=>row.id?.startsWith('capability:')&&!pending.includes(row)&&!retired.includes(row));
 return {pending,retired,history};
}

// Read the same retained rows as chat, in their saved order. No grant lookup,
// runtime hydration or decision controls belong in this presentation.
export function PermissionHistory({session}){
 const {history}=approvalGroups(session);
 return <div className="a-permission-history">
  <p className="a-caption">Saved permission decisions for this chat. Reading history does not request permission or restart work.</p>
  {!history.length&&<p>No permission history yet.</p>}
  {history.map(row=><article key={row.id} data-permission-history-id={row.id}><strong>{row.dismissedAt?'Dismissed old request':row.status==='allow'?'Permission allowed':row.status==='deny'||row.status==='denied'?'Permission denied':row.status}</strong><p>{row.prompt}</p>{row.detail&&<p>{row.detail}</p>}{row.comment&&<p>Note: {row.comment}</p>}{row.dismissalComment&&<p>History-only note: {row.dismissalComment}</p>}</article>)}
 </div>;
}

export function ApprovalPanel({session,act,dispatch,freshnessKey,canvasDraft}){
 const {pending,retired,history}=approvalGroups(session);
 const [error,setError]=useState(''),[opening,setOpening]=useState(false);
 const openHistory=async()=>{
  setOpening(true);setError('');
  try{
   const receipt=await dispatch('view.update',{sessionId:session.id,patch:{settingsCanvas:false,canvasControlsExpanded:false,canvasDraft:{...canvasDraft,library:true,open:false,browser:false,delivery:'permissions'}}});
   if(!receipt?.accepted)throw Error(receipt?.error||'Permission history could not be opened. Nothing was retried.');
  }catch(e){setError(e.message)}finally{setOpening(false)}
 };
 if(!pending.length&&!retired.length&&!history.length)return null;
 return <>
 {(pending.length>0||retired.length>0)&&<section className="a-card a-approvals" data-part="approvals">
  <div className="a-card-header"><h2>{pending.length?'Needs your attention':'Previous permission requests'}</h2></div>
  {[...pending,...retired].map(approval=><ApprovalCard key={`${session.id}:${approval.id}`} approval={approval} sessionId={session.id} act={act} dispatch={dispatch} freshnessKey={freshnessKey}/>)}
 </section>}
 {history.length>0&&<div className="a-permission-history-entry"><button type="button" className="a-link" aria-controls="workspace-canvas" disabled={opening} onClick={openHistory}>Permissions history ({history.length})</button>{error&&<p role="alert">{error}</p>}</div>}
 </>;
}

export function ApprovalCard({approval,sessionId,act,dispatch,freshnessKey}){
 if(approval.id?.startsWith('capability:'))return <CapabilityApprovalCard {...{approval,sessionId,dispatch,freshnessKey}}/>;
 return <NativeApprovalCard {...{approval,sessionId,act}}/>;
}

function NativeApprovalCard({approval,sessionId,act}){
 const [comment,setComment]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const respond=async decision=>{
  if(busy)return;
  setBusy(true);setError('');
  try{
   const result=await act('approval.respond',{sessionId,id:approval.id,decision,...(comment.trim()?{comment:comment.trim()}:{})});
   if(result?.accepted===false)throw Error(result.error||'The decision was not confirmed. Your note is kept.');
  }catch(e){setError(e.message)}finally{setBusy(false)}
 };
 return <div data-approval-id={approval.id}>
  <strong>{approval.title||approval.tool||'Approval requested'}</strong>
  <p>{approval.prompt||approval.message||approval.description}</p>
  {approval.deliveryError&&<p role="status">{approval.deliveryError}</p>}
  {approval.details&&<pre>{typeof approval.details==='string'?approval.details:JSON.stringify(approval.details,null,2)}</pre>}
  <label className="a-field">Note to Amplifier (optional)
   <textarea aria-label="Note to Amplifier (optional)" maxLength={4000} rows={3} value={comment} disabled={busy}
    onChange={e=>setComment(e.target.value)} placeholder="Explain your decision or suggest what to do next…"/>
  </label>
  <p className="a-caption">Your note goes with either decision. Choose Deny if the proposed action needs to change.</p>
  {error&&<p role="alert">{error}</p>}
  <div className="a-dialog-actions"><button className="a-primary" data-action="approval.respond" disabled={busy} onClick={()=>respond('approve')}><Check/>Allow</button><button className="a-soft" data-action="approval.respond" disabled={busy} onClick={()=>respond('deny')}>Deny</button></div>
 </div>;
}
