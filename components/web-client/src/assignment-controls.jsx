import React,{useCallback,useRef,useState} from 'react';
import {AssignmentTaskList} from './assignment-task-list.jsx';
import {AssignmentAdoption} from './assignment-adoption.jsx';

export function AssignmentControls({dispatch,sessionId,memberLabels={}}){
 const [expanded,setExpanded]=useState(false);
 const label=id=>id===sessionId?'This conversation':memberLabels[id]||'Member chat · '+id;
 const metric=row=>row?.value==null?'Unknown':String(row.value)+(row.status==='known'?'':` (${row.status})`);
 const key='amplifier-assignment-draft:'+sessionId;
 const [value,setValue]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const valueRef=useRef(null),observationEpoch=useRef(0),[observationVersion,setObservationVersion]=useState(0);
 const invalidate=useCallback(()=>{const epoch=++observationEpoch.current;setObservationVersion(epoch)},[]);
 const applyAssignment=useCallback(assignment=>{
  if(observationVersion!==observationEpoch.current)return false;
  const current=valueRef.current;if(!current)return false;
  if(current.assignment?.id===assignment?.id&&current.assignment?.revision>assignment?.revision)return false;
  const next={...current,assignment};valueRef.current=next;setValue(next);
  if(current.assignment?.id!==assignment?.id)invalidate();
  return true;
 },[observationVersion,invalidate]);
 const [integrationOwner,setIntegrationOwner]=useState(null);
 const [retiringMember,setRetiringMember]=useState('');
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}});
 const save=value=>{setDraft(value);try{value?sessionStorage.setItem(key,JSON.stringify(value)):sessionStorage.removeItem(key)}catch{}};
 const read=async()=>{const epoch=observationEpoch.current;try{
  const response=await dispatch('assignment.read',{sessionId},{feedback:false});
  if(epoch!==observationEpoch.current)return;
  const current=valueRef.current?.assignment,next=response.result.assignment;
  if(current?.id===next?.id&&current?.revision>next?.revision)return;
  valueRef.current=response.result;setValue(response.result);invalidate();
 }catch(error){if(epoch===observationEpoch.current)setError(error.message)}};
 const assignment=value?.assignment,limits=draft?.limits||assignment?.limits;
 const change=(name,val)=>save({id:crypto.randomUUID(),revision:draft?.revision??assignment.revision,limits:{...limits,[name]:val}});
 const run=async(action,args,identity)=>{
  if(busy)return;invalidate();setBusy(true);setError('');
  // Retain the exact action and identity through an uncertain response/reload.
  const pending=draft?.pending||{action,args,id:identity||crypto.randomUUID()};
  save({...draft,pending});
  try{
   const response=await dispatch(pending.action,pending.args,{id:pending.id,feedback:false});
   if(response?.accepted!==true)throw Error('The change has no confirmed response. Check the same change before trying another.');
   save(null);await read();
  }catch(error){
   // Membership changes reject before committing, or roll back together.
   // A confirmed refusal needs a fresh review, not an "unknown" retry loop.
   if(['assignment.adopt','assignment.retire'].includes(pending.action)&&[400,403,409].includes(error.status)){save(null);await read()}
   setError(error.message);
  }finally{setBusy(false)}
 };
 return <details data-review-target="assignment" className="a-assignment" onToggle={event=>{if(event.target!==event.currentTarget)return;invalidate();setExpanded(event.currentTarget.open);if(event.currentTarget.open&&!value)read()}}>
  <summary>Shared tasks and assignment</summary>
  <p>Manage this chat and its helpers together. Existing chats stay independent until you explicitly add them.</p>
  {error&&<p role="alert">{error}</p>}
  <button className="a-link" disabled={busy} onClick={read}>Refresh assignment</button>
  {draft?.pending&&<p role="status">A change has no confirmed response. <button className="a-link" disabled={busy} onClick={()=>run()}>Check the same change</button></p>}
  {value&&!assignment&&<><p>This chat has no assignment. Commissioning a helper starts one automatically.</p><button className="a-soft" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.start',{sessionId})}>Start assignment</button></>}
  {assignment&&<>
   <p role="status">Assignment {assignment.status} · {Object.values(assignment.members).filter(row=>['bound','reserved'].includes(row.status)).length} current members · {Object.keys(assignment.members).length} retained members · {value.usage?.calls??0} recorded calls</p>
   <p>Total tokens: {metric(value.usage?.metrics.grossTotalTokens)} · Cost (USD): {metric(value.usage?.metrics.costUsd)} · {value.inFlightCalls??0} calls in progress</p>
   <p>Integration responsibility: {assignment.integration?.responsibleSessionId?label(assignment.integration.responsibleSessionId):'Not assigned'}</p>
   {!!value.independentWork?.requests.length&&<p role="status">{value.independentWork.detail}</p>}
   {assignment.status==='stopped'&&<><p>This assignment is stopped. Resuming keeps its chats, shared tasks and budgets, and allows new requests. Previous queued work stays stopped; nothing is sent automatically. Separately paused chat tasks and recorded budget pauses stay paused. Review those in chat controls.</p>{assignment.ownerSessionId===sessionId?<button className="a-primary" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.resume',{sessionId,expectedRevision:assignment.revision})}>Resume assignment</button>:<p>Resume this assignment from its commissioning chat: {label(assignment.ownerSessionId)}.</p>}</>}
   {assignment.ownerSessionId===sessionId&&assignment.status==='active'&&<>
    <AssignmentAdoption key={sessionId} dispatch={dispatch} sessionId={sessionId} assignment={assignment} busy={busy||!!draft?.pending} onAdopt={args=>run('assignment.adopt',args)}/>
    <details><summary>Finish a member’s participation</summary><p>Release a finished chat’s member slot while retaining its history, files and recorded usage. Running or unfinished work must settle first. You can add the same chat again for a new follow-up.</p><label htmlFor={'retire-member-'+sessionId}>Finished conversation</label><select id={'retire-member-'+sessionId} value={retiringMember} disabled={busy||!!draft?.pending} onChange={event=>setRetiringMember(event.target.value)}><option value="">Choose a finished chat</option>{Object.entries(assignment.members).filter(([id,member])=>id!==sessionId&&member.status==='bound').map(([id])=><option key={id} value={id}>{label(id)}</option>)}</select><button className="a-soft" disabled={busy||!!draft?.pending||assignment.members[retiringMember]?.status!=='bound'} onClick={()=>run('assignment.retire',{sessionId,targetSessionId:retiringMember,expectedRevision:assignment.revision})}>Retire finished member</button></details>
    <button className="a-soft" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.stop',{sessionId,expectedRevision:assignment.revision})}>Stop assignment</button>
    <details><summary>Integration responsibility</summary><p>Name the member responsible when combining this assignment’s work. Independent chats keep their own authority. This does not start execution.</p><label htmlFor={"integration-owner-"+sessionId}>Responsible conversation</label><select id={"integration-owner-"+sessionId} value={integrationOwner??assignment.integration?.responsibleSessionId??''} disabled={busy||!!draft?.pending} onChange={event=>setIntegrationOwner(event.target.value)}><option value="">Not assigned</option>{Object.entries(assignment.members).filter(([,member])=>member.status==='bound').map(([id])=><option key={id} value={id}>{label(id)}</option>)}</select><button className="a-soft" disabled={busy||!!draft?.pending} data-action="assignment.integration" onClick={()=>run('assignment.integration',{sessionId,expectedRevision:assignment.revision,responsibleSessionId:(integrationOwner??assignment.integration?.responsibleSessionId)||null})}>Save integration responsibility</button></details>
    <details><summary>Assignment limits</summary><p>Busy model calls wait automatically. Spending limits pause future supported calls. Reservations are allowances per call; actual usage and already running effects can exceed them. Saving does not restart work.</p>
     <p>Leave helper chats, concurrent calls or current members blank for no count limit. Existing saved limits stay in effect until you change them. Retiring finished participation keeps the assignment organized without deleting history or resetting spending. Stop and other per-chat and machine limits still apply.</p>
     {[['maxOpenTasks','Open helper chats'],['maxConcurrentCalls','Concurrent model calls'],['maxMembers','Current assignment members'],['maxTotalTokens','Total token limit'],['reserveTokens','Token allowance per call'],['maxCostUsd','Cost limit (USD)'],['reserveCostUsd','Cost allowance per call (USD)']].map(([name,label])=><label key={name}>{label}<input type="number" min={name.startsWith('reserve')?0:name.includes('Usd')?.000001:1} step={name.includes('Usd')?'any':1} value={limits[name]??''} disabled={busy||!!draft?.pending} onChange={event=>change(name,event.target.value===''?null:Number(event.target.value))}/></label>)}
     <label>When settled usage is unknown<select value={limits.unknownPolicy} disabled={busy||!!draft?.pending} onChange={event=>change('unknownPolicy',event.target.value)}><option value="pause">Pause future calls</option><option value="allow">Allow with incomplete totals</option></select></label>
     <label><input type="checkbox" checked={limits.includeEstimatedCost} disabled={busy||!!draft?.pending} onChange={event=>change('includeEstimatedCost',event.target.checked)}/>Include attributed cost estimates</label>
     {draft?.revision!=null&&draft.revision!==assignment.revision&&<p role="alert">Assignment changed. Your edits are retained; use the latest limits before saving again.</p>}
     <button className="a-soft" disabled={busy||!!draft?.pending||!draft?.limits||draft.revision!==assignment.revision} onClick={()=>run('assignment.set',{sessionId,expectedRevision:draft.revision,...draft.limits},draft.id)}>Save assignment limits</button>
    </details>
   </>}
   {draft&&<button className="a-link" disabled={busy} onClick={()=>{save(null);setError('')}}>Discard draft and use latest</button>}
   <AssignmentTaskList dispatch={dispatch} sessionId={sessionId} compact enabled={expanded&&!busy} onAssignmentChange={applyAssignment}/>
   <details><summary>Membership and accounting details</summary><p>Assignment: <code>{assignment.id}</code> · Revision: {assignment.revision}</p><p>Commissioning owner: {label(assignment.ownerSessionId)} · <code>{assignment.ownerSessionId}</code></p><p>{value.enforcement}</p><pre>{JSON.stringify(assignment.members,null,2)}</pre><p>{value.usage?.coverage}</p><button className="a-link" disabled={busy||!!draft?.pending} onClick={()=>run('assignment.detach',{sessionId,expectedRevision:assignment.revision})}>Detach this settled chat</button></details>
  </>}
 </details>;
}
