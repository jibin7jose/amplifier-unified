import React,{useEffect,useRef,useState} from 'react';
import {prepareNewSchedule} from './schedules';
import './automation-hub.css';

const label=value=>({active:'Scheduled',due:'Due — not yet admitted',blocked:'Blocked',needs_review:'Needs review',paused:'Paused',cancelled:'Cancelled',completed:'Completed',failed:'Failed',unknown:'Unknown outcome',accepted:'Input accepted',running:'Running',owner_unavailable:'Owner unavailable',owner_stopped:'Owner stopped',owner_stopping:'Owner stopping',owner_interrupted:'Owner interrupted',owner_error:'Owner needs attention'}[value]||value);
const when=(seconds,zone)=>seconds?new Date(seconds*1000).toLocaleString(undefined,zone?{timeZone:zone}:{}):'No future occurrence';
const recurrence=spec=>spec.kind==='interval'?`Every ${spec.intervalSeconds/60} ${spec.intervalSeconds===60?'minute':'minutes'}`:spec.kind==='once'?`Once, ${new Date(spec.startAt).toLocaleString(undefined,{timeZone:spec.timezone})}`:spec.kind==='weekly'?`${spec.weekdays.map(day=>['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][day]).join(', ')} at ${spec.localTime}`:`Daily at ${spec.localTime}`;
const model=selection=>selection?[selection.instance,selection.model,selection.effort].filter(Boolean).join(' · '):'Bundle defaults';
const attention=row=>['blocked','needs_review','owner_unavailable','owner_stopped','owner_stopping','owner_interrupted','owner_error'].includes(row.displayState)||['failed','unknown'].includes(row.lastRun?.phase);
const upcoming=row=>['active','due'].includes(row.displayState)&&Number.isFinite(row.nextDue)&&row.lastRun?.phase!=='unknown';
const nextText=row=>upcoming(row)?when(row.nextDue,row.spec?.timezone):['active','due'].includes(row.displayState)&&row.nextDue==null?'No further occurrence':({paused:'Future runs paused',cancelled:'Future runs cancelled',completed:'No future occurrence'}[row.status]||'Review required before future runs');
const heading=prompt=>{const line=(prompt||'Scheduled work').split('\n').find(line=>line.trim())||'Scheduled work';return line.length>100?line.slice(0,97)+'…':line};
export function AutomationHub({state,dispatch,isCurrent}){
 const [page,setPage]=useState(null),[filter,setFilter]=useState('current'),[offset,setOffset]=useState(0),[busy,setBusy]=useState(false),[error,setError]=useState(''),[review,setReview]=useState(null),[ack,setAck]=useState(false);
 const [attentionOpen,setAttentionOpen]=useState(true);
 const version=useRef(0),active=useRef(true),mutation=useRef(false),reviewHeading=useRef(null),reviewOpener=useRef(null);
 const currentChat=state.sessions?.find(row=>row.id===state.selectedSessionId);
 const items=page?.items||[],needsAttention=items.filter(attention),ordinary=items.filter(row=>!attention(row));
 const next=items.filter(upcoming).sort((a,b)=>a.nextDue-b.nextDue)[0];
 useEffect(()=>{if(review)reviewHeading.current?.focus()},[review]);
 useEffect(()=>{active.current=true;return()=>{active.current=false;version.current++}},[]);
 async function request(action,args){const value=await dispatch(action,args,{feedback:false});if(value?.accepted!==true)throw Error(value?.error||'The result could not be confirmed. Refresh before trying again.');return value.result}
 async function refresh(start=offset,scope=filter){
  const current=++version.current;setBusy(true);setError('');
  try{const result=await request('schedule.overview',{offset:start,limit:30,status:scope});if(active.current&&current===version.current)setPage(result)}
  catch(e){if(active.current&&current===version.current)setError(e.message)}finally{if(active.current&&current===version.current)setBusy(false)}
 }
 useEffect(()=>{refresh(offset,filter)},[offset,filter]);
 function changePage(start,scope=filter){setPage(null);setReview(null);setOffset(start);setFilter(scope)}
 function closeReview(){setReview(null);reviewOpener.current?.focus()}
 async function manage(id,create=false){
  if(mutation.current)return;mutation.current=true;setBusy(true);setError('');
  try{if(id!==state.selectedSessionId)await request('session.select',{id});if(active.current&&isCurrent(id)){if(create)prepareNewSchedule(id);await request('view.update',{patch:{panel:'chat-work',runtimeDraft:{section:'schedules'}}})}}
  catch(e){if(active.current)setError(e.message)}finally{mutation.current=false;if(active.current)setBusy(false)}
 }
 async function preview(row,event){
  reviewOpener.current=event?.currentTarget;
  if(mutation.current)return;mutation.current=true;setBusy(true);setError('');setReview(null);setAck(false);
  try{const value=await request('schedule.repairPreview',{sessionId:row.sessionId,id:row.id,expectedRevision:row.revision});if(active.current)setReview({row,...value})}
  catch(e){if(active.current)setError(e.message)}finally{mutation.current=false;if(active.current)setBusy(false)}
 }
 async function resume(){
  if(mutation.current||!review)return;mutation.current=true;setBusy(true);setError('');
  // Close the one-use review even if its response is lost. A fresh read/preview
  // is required; never leave a blind mutation retry button behind.
  const value=review;setReview(null);
  try{await request('schedule.repairResume',{sessionId:value.row.sessionId,id:value.row.id,expectedRevision:value.scheduleRevision,previewHash:value.previewHash,acknowledgeStop:ack});await refresh()}
  catch(e){if(active.current)setError(e.message)}finally{mutation.current=false;if(active.current)setBusy(false)}
 }
 function workspace(owner){return <span data-part="automation-workspace">{owner?.workspace||'Workspace unavailable'}</span>}
 function card(row){return <article className="a-automation-row" key={row.id} data-automation-id={row.id} data-run-state={row.lastRun?.phase||'not_run'}>
  <div className="a-automation-identity"><span className="a-automation-state" data-part="automation-schedule-state" data-state={row.displayState}>{label(row.displayState)}</span><h3>{heading(row.prompt)}</h3><p className="a-caption">{recurrence(row.spec)} · {row.spec?.timezone}</p></div>
  <dl className="a-automation-owner"><div><dt>Owning chat</dt><dd>{row.owner?.title||'Unavailable chat'}</dd><dt>Workspace</dt><dd>{workspace(row.owner)}</dd></div><div><dt>Next occurrence</dt><dd>{nextText(row)}</dd></div></dl>
  <dl className="a-automation-outcome"><div><dt>Last run</dt><dd><span className="a-automation-state" data-part="automation-run-state" data-state={row.lastRun?.phase||'not_run'}>{row.lastRun?label(row.lastRun.phase):'Not run yet'}</span>{row.lastRun&&<span className="a-caption a-automation-run-time">{when(row.lastRun.dueAt,row.spec?.timezone)}</span>}</dd></div></dl>
  <div className="a-automation-actions a-automation-row-actions"><button className="a-soft" disabled={busy||!row.owner} onClick={()=>manage(row.sessionId)}>Open owning chat</button>{row.owner&&row.destination==='new_task'&&row.status!=='cancelled'&&row.lastRun?.phase!=='unknown'&&<button className="a-soft" disabled={busy} onClick={event=>preview(row,event)}>Review future runs</button>}</div>
  {row.reason&&<p className="a-automation-reason a-automation-row-detail">{row.reason}</p>}
  {row.lastRun?.phase==='unknown'&&<p className="a-automation-row-detail">Inspect the prior run in its owning chat. Its input will not be replayed.</p>}
  <details className="a-automation-row-detail"><summary>Instructions &amp; run details</summary><p>{row.prompt}</p><p>{row.destination==='new_task'?'New task for each run':'Continues the owning task'}</p>{row.lastRun&&<p>{row.lastRun.detail}</p>}</details>
 </article>}
 return <section className="a-automation" aria-label="Automation overview" aria-busy={busy}>
  <header className="a-automation-heading"><div><h3>Upcoming &amp; attention</h3><p>See what is planned and what needs you.</p></div><div><button className="a-primary" disabled={busy||!state.selectedSessionId} onClick={()=>manage(state.selectedSessionId,true)}>Create schedule</button><p className="a-caption">In {currentChat?.title||'the current chat'}<br/>{workspace(currentChat)}</p></div></header>
  <div className="a-automation-actions a-automation-toolbar"><button className="a-soft" disabled={busy} onClick={()=>{setReview(null);refresh()}}>Refresh automations</button><label>Show <select aria-label="Show automations" value={filter} disabled={busy} onChange={event=>changePage(0,event.target.value)}><option value="current">Current (not cancelled)</option><option value="all">All schedules</option></select></label></div>
  {!state.selectedSessionId&&<p>Choose or create a chat to add scheduled work.</p>}
  {error&&<p role="alert">{error}</p>}
  {page&&<p className="a-caption">Schedule snapshot · {when(page.readAt)} · {page.total} schedules</p>}
  {!page&&!error&&<p role="status">Loading automations…</p>}
  {page?.items.length===0&&<div className="a-automation-empty"><h3>Give a routine a schedule</h3><p>For example, summarize decisions and open risks each weekday. Choose the instructions and time, then review before saving.</p><p className="a-caption">No saved schedules in this view. Each schedule stays with its owning chat.</p></div>}
  {items.length>0&&<section className="a-automation-next" data-part="automation-next" aria-label="Next on this page"><p className="a-caption">On this page · next occurrence</p>{next?<><h3>{heading(next.prompt)}</h3><p className="a-automation-time">{when(next.nextDue,next.spec?.timezone)} · {next.spec?.timezone}</p><p>{next.owner?.title||'Unavailable chat'} · {workspace(next.owner)}</p><p><span className="a-automation-state" data-state={next.displayState}>{label(next.displayState)}</span> <span className="a-caption">Planned time is not confirmation that work started.</span></p></>:<><h3>No future runs enabled on this page</h3><p>Review the schedule state below. Other pages may contain upcoming work.</p></>}</section>}
  {review&&<section className="a-selection-card a-automation-review" aria-label="Review future scheduled work"><h3 ref={reviewHeading} tabIndex={-1}>Review future scheduled work</h3><p>{review.binding.prompt}</p><dl><dt>Owning chat</dt><dd>{review.row.owner?.title||'Untitled chat'}</dd><dt>Workspace</dt><dd>{review.binding.newTaskConfiguration.workspace}</dd><dt>Bundle</dt><dd>{review.binding.newTaskConfiguration.bundle}</dd><dt>Model</dt><dd>{model(review.binding.newTaskConfiguration.selection)}</dd><dt>Destination</dt><dd>New task for each run · {review.binding.newTaskTitle}</dd><dt>Maximum turns</dt><dd>{review.binding.newTaskMaxTurns}</dd><dt>Repeats</dt><dd>{recurrence(review.binding.spec)} · {review.binding.spec.timezone}</dd><dt>Missed runs</dt><dd>{review.binding.missedRunPolicy==='skip'?'Skip occurrences more than a minute late':'Run only the latest missed occurrence'}</dd><dt>Notifications</dt><dd>{review.binding.notificationPolicy==='changes'?'Changes and failures':review.binding.notificationPolicy==='always'?'Every result and failure':'Failures only'}</dd></dl><p>Upcoming occurrences ({review.binding.spec.timezone}):</p><ul>{review.occurrences.map(item=><li key={item.utc}>{when(item.dueAt,review.binding.spec.timezone)}</li>)}</ul><p>Only future runs are enabled. This does not restart the owning chat or replay previous work. Normal file and tool permissions still apply.</p>{review.requiresStopAcknowledgement&&<label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/> Allow future scheduled tasks after my Stop</label>}<div className="a-automation-actions"><button className="a-primary" disabled={busy||!review.occurrences.length||(review.requiresStopAcknowledgement&&!ack)} onClick={resume}>Resume future runs</button><button className="a-soft" disabled={busy} onClick={closeReview}>Not now</button></div></section>}
  {items.length>0&&<section className="a-automation-attention" aria-label="Needs attention"><h3>Needs attention <small>· {needsAttention.length} on this page</small></h3>{needsAttention.length>0?<details className="a-automation-exceptions" open={attentionOpen} onToggle={event=>setAttentionOpen(event.currentTarget.open)}><summary>Review {needsAttention.length} exceptions on this page</summary><div className="a-automation-list">{needsAttention.map(card)}</div></details>:<p className="a-caption">No schedules need attention on this page.</p>}</section>}
  {ordinary.length>0&&<section aria-label="Other schedules on this page"><h3>{needsAttention.length?'Other schedules':'Schedules'} <small>· on this page</small></h3><div className="a-automation-list">{ordinary.map(card)}</div></section>}
  {page&&(offset>0||page.hasMore)&&<div className="a-automation-actions"><button className="a-soft" disabled={busy||offset===0} onClick={()=>changePage(Math.max(0,offset-30))}>Previous schedules</button><span>{page.total?`${page.offset+1}–${page.offset+page.items.length} of ${page.total}`:'0 schedules'}</span><button className="a-soft" disabled={busy||!page.hasMore} onClick={()=>changePage(offset+30)}>More schedules</button></div>}
 </section>
}
