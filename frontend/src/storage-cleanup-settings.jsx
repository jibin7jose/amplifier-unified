import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';

const key='amplifier-storage-cleanup-pending';
const pendingSave=()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}};
const gib=1024**3;
import {formatStorageBytes as bytes} from './storage-bytes';
export function StorageCleanupSettings({act,navigate=()=>{}}){
 const [report,setReport]=useState(null),[draft,setDraft]=useState(null),[pending,setPending]=useState(pendingSave),[preview,setPreview]=useState(null);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const mounted=useRef(true),epoch=useRef(0),draftRef=useRef(draft);draftRef.current=draft;
 const retain=value=>{try{value?sessionStorage.setItem(key,JSON.stringify(value)):sessionStorage.removeItem(key)}catch{if(value){setError('This browser could not save a recovery receipt. Nothing was submitted. Enable session storage and try again.');return false}}setPending(value);return true};
 async function load(){const version=++epoch.current;setBusy(true);setError('');try{
  const response=await act('storage.cleanup.read',{}, {feedback:false});if(!response?.accepted)throw Error(response?.error||'Cleanup settings could not be read.');
  if(mounted.current&&version===epoch.current){setReport(response.result);if(!draftRef.current)setDraft(response.result.policy)}
 }catch(e){if(mounted.current&&version===epoch.current)setError(e.message)}finally{if(mounted.current&&version===epoch.current)setBusy(false)}}
 useEffect(()=>{mounted.current=true;load();return()=>{mounted.current=false;epoch.current++}},[]);
 const dirty=!!draft&&!!report&&['enabled','keepDays','maxUnusedBytes'].some(name=>draft[name]!==report.policy[name]);
 useSettingsDraft('storage-cleanup',{label:'Automatic cleanup',dirty:dirty||!!pending,review:()=>navigate('storage'),discard:()=>setDraft(report?.policy||null)});
 const valid=draft&&Number.isSafeInteger(draft.keepDays)&&draft.keepDays>=0&&draft.keepDays<=3650&&Number.isSafeInteger(draft.maxUnusedBytes)&&draft.maxUnusedBytes>=0;
 async function send(action,args){
  if(busy)return;const request=pending||{id:crypto.randomUUID(),action,args};if(!retain(request))return;setBusy(true);setError('');setNotice('');const version=++epoch.current;
  try{const response=await act(request.action,request.args,{id:request.id,feedback:false});if(!response?.accepted){const failure=Error(response?.error||'The request was not confirmed.');failure.status=response?.status;throw failure}
   if(!mounted.current||version!==epoch.current)return;retain(null);
   if(request.action==='storage.cleanup.save'){setReport(response.result);setDraft(response.result.policy);setPreview(null);setNotice('Cleanup settings saved for all workspaces. Active work and saved results stay protected.')}
   else{setPreview(null);setReport(old=>({...old,lastCleanup:response.result}));setNotice(response.result.status==='completed'?`Cleanup completed. Removed ${bytes(response.result.removedFileBytes)} of unused file content.`:'Cleanup is not fully confirmed. Its receipt is kept; the request will not be repeated.')}
  }catch(e){if(mounted.current&&version===epoch.current){setError(e.message);if([400,403,409].includes(e.status))retain(null)}}finally{if(mounted.current&&version===epoch.current)setBusy(false)}
 }
 async function inspect(){if(busy)return;setBusy(true);setError('');const version=++epoch.current;try{
  const response=await act('storage.cleanup.preview',{}, {feedback:false});if(!response?.accepted)throw Error(response?.error||'Unused copies could not be inspected.');
  if(mounted.current&&version===epoch.current)setPreview(response.result);
 }catch(e){if(mounted.current&&version===epoch.current)setError(e.message)}finally{if(mounted.current&&version===epoch.current)setBusy(false)}}
 return <section className="a-settings-section" aria-label="Automatic cleanup"><h4>Automatic cleanup</h4>
  <p>Keep unused app components and test runner versions from accumulating. Your chats, project files, saved test sources and results are always kept.</p>
  {report&&<p className="a-caption">{report.scope}</p>}
  <fieldset disabled={busy||!report||!!pending}>
   <label><input type="checkbox" checked={!!draft?.enabled} onChange={e=>setDraft({...draft,enabled:e.target.checked})}/> Automatically clean up unused copies</label>
   <details><summary>Retention and size</summary>
    <label className="a-field">Keep unused copies for days<input type="number" min="0" max="3650" step="1" value={draft?.keepDays??''} onChange={e=>setDraft({...draft,keepDays:e.target.value===''?'':Number(e.target.value)})}/></label>
    <label className="a-field">Size target for unused copies (GiB)<input type="number" min="0" step="any" value={draft?.maxUnusedBytes===undefined?'':draft.maxUnusedBytes/gib} onChange={e=>setDraft({...draft,maxUnusedBytes:e.target.value===''?'':Math.round(Number(e.target.value)*gib)})}/></label>
    <p className="a-caption">Older unused copies are removed when their retention time ends, or sooner to meet this size target. Zero days keeps no extra waiting period; zero size disables the size target. This does not limit active storage or delete saved work. Checks run in small batches while Amplifier is open.</p>
   </details>
  </fieldset>
  <button className="a-primary" disabled={busy||!report||(!pending&&(!dirty||!valid||draft.revision!==report.policy.revision))} onClick={()=>send('storage.cleanup.save',{expectedRevision:draft.revision,enabled:draft.enabled,keepDays:draft.keepDays,maxUnusedBytes:draft.maxUnusedBytes})}>{pending?'Check the same request':'Save cleanup settings'}</button>
  <button className="a-soft" disabled={busy||!!pending} onClick={load}>Reload cleanup settings</button>
  {draft&&report&&draft.revision!==report.policy.revision&&<p role="alert">Settings changed elsewhere. Your edits are kept. Use the saved settings before editing again.</p>}
  {dirty&&!pending&&<button className="a-link" disabled={busy} onClick={()=>setDraft({...report.policy})}>Discard edits and use saved settings</button>}
  <button className="a-soft" disabled={busy||!!pending} onClick={inspect}>Review unused copies</button>
  {pending&&<p role="status">The last request is not confirmed. Check the same request to learn its outcome.</p>}
  {preview&&<div aria-label="Cleanup preview"><p>This inspection found {bytes(preview.unusedFileBytes)} in proven unused copies; {bytes(preview.selectedFileBytes)} currently selected by your saved policy.</p>
   {preview.observedUnusedFileBytes!==undefined&&<p>Across cleanup batches: {bytes(preview.observedUnusedFileBytes)} in last measured unused copies. {preview.observationNote}</p>}
   <p>Protected copies measured: {preview.protectedMeasurementComplete?'':'at least '}{bytes(preview.protectedFileBytes||0)}.</p><p>{preview.measurementNote}</p>{!preview.complete&&<p>The inspection was limited. Unverified copies are kept. Review again to inspect another batch.</p>}
   <ul>{preview.rows.map(row=><li key={`${row.kind}:${row.id}`}>{row.label}: {bytes(row.fileBytes)} · {row.reason}</li>)}</ul>
   <details><summary>What stays protected ({preview.protectedCount})</summary><ul>{preview.protected.map((row,index)=><li key={`${row.kind}:${row.id||index}`}>{row.label}: {row.reason}</li>)}</ul></details>
   <button className="a-soft" disabled={busy||!!pending||dirty||!preview.selectedFileBytes||preview.policy.revision!==report?.policy.revision} onClick={()=>send('storage.cleanup.run',{expectedRevision:preview.policy.revision})}>Clean unused copies now</button>
  </div>}
  {report?.lastCleanup&&<p className="a-caption">Last cleanup: {report.lastCleanup.status} · {bytes(report.lastCleanup.removedFileBytes||0)} of file content removed. Receipts are retained.</p>}
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
