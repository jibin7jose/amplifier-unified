import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';

const storageKey='amplifier-model-capacity-pending';
const saved=()=>{try{return JSON.parse(sessionStorage.getItem(storageKey)||'null')}catch{return null}};
export function ModelCapacitySettings({act,navigate=()=>{}}){
 const [report,setReport]=useState(null),[draft,setDraft]=useState(null),[pending,setPending]=useState(saved);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const epoch=useRef(0),mounted=useRef(true),draftRef=useRef(draft);draftRef.current=draft;
 const retain=value=>{setPending(value);try{value?sessionStorage.setItem(storageKey,JSON.stringify(value)):sessionStorage.removeItem(storageKey)}catch{}};
 const load=async()=>{
  const current=++epoch.current;setBusy(true);setError('');
  try{const result=await act('modelCapacity.read',{}, {feedback:false});if(!result?.accepted)throw Error(result?.error||'Could not read model-capacity settings.');
   if(mounted.current&&epoch.current===current){setReport(result.result);if(!draftRef.current)setDraft({...result.result})}
  }catch(e){if(mounted.current&&epoch.current===current)setError(e.message)}finally{if(mounted.current&&epoch.current===current)setBusy(false)}
 };
 useEffect(()=>{mounted.current=true;load();return()=>{mounted.current=false;epoch.current++}},[]);
 const dirty=!!draft&&!!report&&(draft.maxWaitingCalls!==report.maxWaitingCalls||draft.waitTimeoutSeconds!==report.waitTimeoutSeconds);
 useSettingsDraft('model-capacity',{label:'Model capacity',dirty:dirty||!!pending,
  review:()=>navigate('model-capacity'),discard:()=>{setDraft(report?{...report}:null);setError('')}});
 const change=(key,value)=>{setDraft({...draft,[key]:value===''?'':Number(value)});setNotice('')};
 const valid=draft&&Number.isSafeInteger(draft.maxWaitingCalls)&&draft.maxWaitingCalls>=0&&Number.isSafeInteger(draft.waitTimeoutSeconds)&&draft.waitTimeoutSeconds>=0;
 const save=async()=>{
  if(busy)return;const request=pending||{id:crypto.randomUUID(),args:{expectedRevision:draft.revision,maxWaitingCalls:draft.maxWaitingCalls,waitTimeoutSeconds:draft.waitTimeoutSeconds}};
  retain(request);const current=++epoch.current;setBusy(true);setError('');setNotice('');
  try{const response=await act('modelCapacity.set',request.args,{id:request.id,feedback:false});if(!response?.accepted)throw Error(response?.error||'The save was not confirmed.');
   if(mounted.current&&epoch.current===current){retain(null);setReport(response.result);setDraft({...response.result});setNotice('Saved for all chats on this host. Waiting work uses these settings automatically.')}
  }catch(e){if(mounted.current&&epoch.current===current){setError(e.message);if([400,403,409].includes(e.status))retain(null)}}finally{if(mounted.current&&epoch.current===current)setBusy(false)}
 };
 return <section className="a-settings-section" aria-label="Model capacity">
  <p>Applies to all current and new workspaces on this Amplifier host. Busy chats wait for their assignment’s next available model slot and continue automatically.</p>
  {report&&<p role="status">Saved: {report.maxWaitingCalls===0?'no waiting-call count limit':`up to ${report.maxWaitingCalls} waiting calls`} · {report.waitTimeoutSeconds===0?'wait until capacity is available or you stop work':`${report.waitTimeoutSeconds} seconds maximum wait`}.</p>}
  <fieldset disabled={busy||!report||!!pending}>
   <label htmlFor="model-capacity-max-waiting">Maximum waiting model calls (0 means no count limit)</label><input id="model-capacity-max-waiting" type="number" min="0" step="1" value={draft?.maxWaitingCalls??''} onChange={e=>change('maxWaitingCalls',e.target.value)}/>
   <label htmlFor="model-capacity-timeout">Maximum wait in seconds (0 means no time limit)</label><input id="model-capacity-timeout" type="number" min="0" step="1" value={draft?.waitTimeoutSeconds??''} onChange={e=>change('waitTimeoutSeconds',e.target.value)}/>
   <button type="button" className="a-soft" onClick={()=>{setDraft({...draft,maxWaitingCalls:0,waitTimeoutSeconds:0});setNotice('Unlimited waiting selected. Save to apply to all workspaces.')}}>Use unlimited waiting</button>
  </fieldset>
  <p>Spending limits and Stop still apply. These settings do not raise an assignment’s concurrent-call limit or repeat completed work. A timed-out wait has not sent a model request.</p>
  <button type="button" className="a-primary" disabled={busy||!report||(!pending&&(!dirty||!valid||draft.revision!==report.revision))} onClick={save}>{pending?'Check the same save':'Save model-capacity settings'}</button>
  <button type="button" className="a-soft" disabled={busy||!!pending} onClick={load}>Reload saved settings</button>
  {draft&&report&&draft.revision!==report.revision&&<p role="alert">Settings changed elsewhere. Your edits are kept. Use the saved settings before editing again.</p>}
  {dirty&&!pending&&<button type="button" className="a-link" disabled={busy} onClick={()=>setDraft({...report})}>Discard edits and use saved settings</button>}
  {pending&&<p role="status">The save is not confirmed. Check the same save to learn its outcome.</p>}
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
