import React,{useEffect,useRef,useState} from 'react';
import {ResultNotice} from './settings-ui';

// A retained failure is not permission to inspect or warm a new runtime.
export function configurationHeld(session){
 const pending=session?.pendingConfiguration;
 return !!session?.configurationBusy||!!pending&&!['ready','cancelled'].includes(pending.phase);
}
export function recoveryMeaning(report){
 const {phase,effect}=report?.state||{};
 if(phase==='ready'&&effect==='mounted')return 'Changes are applied to the loaded session.';
 if(phase==='queued')return 'The settings change is queued and is not mounted yet.';
 if(phase==='applying')return effect==='saved_not_mounted'?'Confirming the saved change in the loaded session. No settings write is repeated.':'Applying the original change. Waiting for loaded-session confirmation.';
 if(effect==='saved_not_mounted')return 'Settings were saved, but the loaded session is not confirmed. This change remains held.';
 if(effect==='unknown')return 'The outcome is not confirmed. No settings write will be repeated without proof.';
 if(effect==='none')return 'No settings write occurred for this operation.';
 if(phase==='cancelled')return 'The queued change was cancelled.';
 return 'The original change remains held until its evidence can be verified.';
}

export function ConfigurationRecovery({session,act}){
 const sid=session.id,pending=session.pendingConfiguration,op=pending?.operationId;
 const [report,setReport]=useState(null),[busy,setBusy]=useState(''),[notice,setNotice]=useState('');
 const alive=useRef(true),serial=useRef(0),sending=useRef(false),attempted=useRef(new Set());
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;serial.current++}},[]);
 async function read(){
  const token=++serial.current;
  setBusy('read');
  try{
   const receipt=await act('configuration.operation.read',{sessionId:sid,...(op?{operationId:op}:{}),includeRecovery:true},{presentation:true,feedback:false});
   if(!alive.current||token!==serial.current)return;
   const value=receipt?.result;
   if(!receipt?.accepted||value?.sessionId!==sid||(op&&value.operationId!==op)||!value?.recovery)throw Error('unconfirmed');
   setReport(value);
   setNotice('');
  }catch{
   if(alive.current&&token===serial.current){setReport(null);setNotice('Status could not be confirmed. Refresh status to read it again; no change was retried.')}
  }finally{if(alive.current&&token===serial.current)setBusy('')}
 }
 useEffect(()=>{if(!sending.current)read()},[sid,op,pending?.revision,pending?.phase,session.interruptionRevision,session.executionRevision]);
 async function mutate(verb){
  const revision=report?.revision,key=verb+':'+revision;
  if(sending.current||busy||!revision||attempted.current.has(key)||!report.recovery[verb==='resume'?'canResume':'canReconcile'])return;
  sending.current=true;attempted.current.add(key);serial.current++;setBusy(verb);setNotice('');
  const id=`configuration-recovery-${op}-${revision}-${verb}`;
  try{
   const receipt=await act('configuration.operation.'+verb,{sessionId:sid,operationId:op,expectedRevision:revision},{id,feedback:false});
   if(!alive.current)return;
   if(!receipt?.accepted)throw Error('unconfirmed');
   // Acceptance (including a duplicate receipt) is not mounted completion.
   await read();
  }catch{
   if(alive.current){setReport(null);setNotice('The request result is not confirmed. Refresh status before any further action. This request will not be sent again.')}
  }finally{sending.current=false;if(alive.current)setBusy('')}
 }
 const stale=pending?.revision&&report?.revision!==pending.revision;
 const disabled=!!busy||!!stale||!report;
 return <section className="a-settings-section" aria-label="Configuration recovery" data-part="configuration-recovery" aria-busy={!!busy}>
  <h4>Original configuration change</h4>
  <p className="a-caption">For “{session.title}”. Status reads do not load modules, start work or reveal the saved configuration.</p>
  <ResultNotice phase={report?.state?.phase==='ready'&&report?.state?.effect==='mounted'?'ready':'neutral'} message={recoveryMeaning(report)}/>
  {report?.recovery?.reason&&report.recovery.reason!==recoveryMeaning(report)&&<p>{report.recovery.reason}</p>}
  {notice&&<ResultNotice phase="error" message={notice}/>}
  {busy&&<p role="status">{busy==='read'?'Reading retained status…':busy==='resume'?'Requesting the original change…':'Checking saved outcome…'}</p>}
  <div className="a-dialog-actions">
   <button type="button" className="a-soft" disabled={!!busy} data-action="configuration.operation.read" onClick={read}>Refresh status</button>
   <button type="button" className="a-soft" disabled={disabled||!report?.recovery.canReconcile||attempted.current.has('reconcile:'+report?.revision)} data-action="configuration.operation.reconcile" onClick={()=>mutate('reconcile')}>Check saved outcome</button>
   <button type="button" className="a-primary" disabled={disabled||!report?.recovery.canResume||attempted.current.has('resume:'+report?.revision)} data-action="configuration.operation.resume" onClick={()=>mutate('resume')}>Resume original change</button>
  </div>
  <p className="a-caption">Resume uses only the original change and permission. Confirmed saved changes continue loaded-session verification without repeating the write. Unknown or partial writes stay held. Stop and permission changes are checked again.</p>
  {report?.operationId&&<details><summary>Operation details</summary><dl><dt>Operation</dt><dd className="a-wrap">{report.operationId}</dd><dt>Revision</dt><dd>{report.revision??'Unverified'}</dd><dt>Attempts</dt><dd>{report.attempts??'Unverified'}</dd></dl>{report.history?.length>0&&<ol>{report.history.map((row,index)=><li key={index}>Revision {row.revision}: {row.phase} / {row.effect}</li>)}</ol>}</details>}
 </section>;
}
