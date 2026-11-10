import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {cursorStore,portableClientTiming,recapCommitTiming} from './coordination-observations.js';
import {recentActionTimings,recentRequestTimings,download} from './api.js';

export function CoordinationObservations({dispatch,sessionId}){
 const root=useRef(null),epoch=useRef(0),visible=useRef(false),store=useRef(null);
 const commitTiming=useRef(null);
 if(!commitTiming.current||commitTiming.current.sessionId!==sessionId)commitTiming.current={sessionId,timing:recapCommitTiming()};
 if(!store.current){let storage;try{storage=sessionStorage}catch{}store.current=cursorStore(storage)}
 const [shown,setShown]=useState(false),[report,setReport]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[diagnostic,setDiagnostic]=useState(null);
 const painted=()=>visible.current&&document.visibilityState!=='hidden'&&!!root.current?.getClientRects().length;
 const load=async()=>{
  const turn=++epoch.current,previous=store.current.read(sessionId),timing=commitTiming.current.timing;setBusy(true);setError('');
  try{
   const value=await dispatch('coordination.recap',{sessionId,...(previous?{cursor:previous}:{})},{feedback:false});
   const commitSample=timing.received();
   if(!Array.isArray(value?.result?.changes)||typeof value.result.nextCursor!=='string')throw Error('The change recap is unavailable. Refresh to try again.');
   if(turn===epoch.current&&painted())setReport({value:value.result,previous,turn,commitSample,timing});
  }catch(error){if(turn===epoch.current&&painted())setError(error.message)}
  finally{if(turn===epoch.current)setBusy(false)}
 };
 useEffect(()=>{
  setShown(false);setReport(null);setDiagnostic(null);setError('');
  const update=value=>{visible.current=value&&document.visibilityState!=='hidden';setShown(visible.current);if(!visible.current)epoch.current++};
  let intersecting=false;
  const observer=new IntersectionObserver(entries=>{intersecting=entries.some(row=>row.isIntersecting);update(intersecting)});
  const visibility=()=>update(intersecting);
  observer.observe(root.current);document.addEventListener('visibilitychange',visibility);
  return()=>{visible.current=false;epoch.current++;observer.disconnect();document.removeEventListener('visibilitychange',visibility)};
 },[sessionId]);
 useEffect(()=>{if(shown)void load();return()=>{epoch.current++}},[shown,sessionId]);
 useLayoutEffect(()=>{
  const committedAt=performance.now();
  if(report?.turn===epoch.current&&report.timing===commitTiming.current.timing&&painted())report.timing.committed(report.commitSample,committedAt);
 },[report,sessionId]);
 useEffect(()=>{
  if(report?.turn===epoch.current&&painted())store.current.mark(sessionId,report.previous,report.value.nextCursor);
 },[report,sessionId]);
 const inspect=async()=>{
  const turn=epoch.current;setError('');
  try{const reply=await dispatch('coordination.diagnostics',{sessionId},{feedback:false});
   if(turn===epoch.current&&painted())setDiagnostic({...reply.result,client:portableClientTiming(recentActionTimings(),recentRequestTimings(),commitTiming.current.timing.snapshot())});
  }catch(error){if(turn===epoch.current&&painted())setError(error.message)}
 };
 const value=report?.value;
 return <section ref={root} aria-label="Changes since your last view">
  <h4>{value?.cursorStatus==='continued'?'Changes since your last view':'Current work at a glance'}</h4>
  {error&&<p role="alert">{error}</p>}
  {value?.cursorStatus==='reset'&&<p>That comparison is no longer available. Showing the current saved state.</p>}
  {value&&<>{value.changes.length?<ul>{value.changes.map(row=><li key={row.id}><strong>{row.label}</strong> · {row.status}</li>)}</ul>:<p>No saved changes since your last view.</p>}
   {(value.pageGap||value.truncated)&&<p className="a-caption">This view shows recent work. Older updates remain in the related tasks.</p>}
   <p className="a-caption">{value.detail}</p></>}
  <button type="button" className="a-link" disabled={busy} onClick={load}>{value?.remainingChanges?`Show ${Math.min(8,value.remainingChanges)} more changes`:'Refresh change recap'}</button>
  <details onToggle={event=>{if(event.currentTarget.open&&!diagnostic)void inspect()}}><summary>Work phase diagnostics</summary>
   <p className="a-caption">A shareable report of work stages and available timing measurements. Message content, file paths and private drafts are excluded.</p>
   <button type="button" className="a-link" onClick={inspect}>Refresh phase diagnostics</button>
   {diagnostic&&<><textarea aria-label="Portable work phase report" readOnly rows={8} value={JSON.stringify(diagnostic,null,2)}/>
    <button type="button" className="a-soft" onClick={()=>download('amplifier-work-phases.json',diagnostic)}>Save phase report</button></>}
  </details>
 </section>;
}
