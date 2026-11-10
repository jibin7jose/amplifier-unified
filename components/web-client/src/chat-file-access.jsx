import React,{useEffect,useRef,useState} from 'react';
import {PathField,ResultNotice} from './settings-ui';
import {request as apiRequest} from './api';

export async function readPermissionCompletion(id,{signal,read=apiRequest}={}){
 const path='/managementResults/'+id.replaceAll('~','~0').replaceAll('/','~1');
 const result=await read('/api/state/detail?'+new URLSearchParams({path,limit:'2'}),{signal});
 if(result.path!==path||result.type!=='object'||result.nextOffset!==null)return null;
 const value=Object.fromEntries((result.items||[]).map(item=>[item.key,item.value]));
 return ['ready','error'].includes(value.phase)?value:null;
}

function Paths({label,values}){
 return <div><strong>{label}</strong>{values?.length?<ul>{values.map(value=><li className="a-wrap" key={value}>{value}</li>)}</ul>:<p>None added.</p>}</div>;
}

export function ChatFileAccess({state,session,act,readCompletion=readPermissionCompletion}){
 const [report,setReport]=useState(null),[allowed,setAllowed]=useState(''),[denied,setDenied]=useState('');
 const [pending,setPending]=useState(null),[sending,setSending]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [needsPreview,setNeedsPreview]=useState(false);
 const request=useRef(null),alive=useRef(true);
 const clear=attempt=>{clearTimeout(attempt?.timer);attempt?.controller?.abort();};
 const settle=attempt=>{
  if(!alive.current||request.current!==attempt)return false;
  clear(attempt);request.current=null;setPending(null);setSending(false);return true;
 };
 const unknown=attempt=>{
  if(!settle(attempt))return;
  setNeedsPreview(true);setError('This request’s result is not confirmed. It may still finish. Your entries are kept. Reload preview before another change; no save or reset will be repeated.');
 };
 const reconcile=async attempt=>{
  if(request.current!==attempt)return;
  if(!attempt.id){unknown(attempt);return}
  attempt.controller=new AbortController();
  attempt.timer=setTimeout(()=>unknown(attempt),5000);
  try{
   // Read only this accepted command. Do not replay its action or fetch the
   // full command-history map when another tab replaces the streamed status.
   const result=await readCompletion(attempt.id,{signal:attempt.controller.signal});
   if(!result){unknown(attempt);return}
   if(!settle(attempt))return;
   setNeedsPreview(true);
   if(result.phase==='error')setError((result.error||'The request did not finish.')+' Your entries are kept. Reload preview before another change.');
   else setNotice((attempt.action==='permissions.save'?'Chat write overrides saved.':attempt.action==='permissions.reset'?'Workspace defaults restored for this chat.':'The preview request finished.')+' Reload preview to check current settings. Your entries are kept.');
  }catch{unknown(attempt)}
 };
 const run=async(action)=>{
  if(request.current)return;
  const attempt={action,keepEdits:action==='permissions.get'&&needsPreview&&!!report};
  request.current=attempt;setSending(true);setError('');setNotice('');
  // Bound both a lost HTTP acknowledgement and a superseded stream result.
  attempt.timer=setTimeout(()=>reconcile(attempt),15000);
  try{
   const args={sessionId:session.id,scope:'session',...(action==='permissions.get'?{}:{expectedRevision:report.revision}),
    ...(action==='permissions.save'?{allowed:allowed.split('\n').map(v=>v.trim()).filter(Boolean),denied:denied.split('\n').map(v=>v.trim()).filter(Boolean)}:{})};
   const receipt=await act(action,args);
   if(!alive.current||request.current!==attempt)return;
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'Could not update file settings. Your entries are kept.');
   attempt.id=receipt.operationId;setPending(attempt);
  }catch(e){if(settle(attempt)){setNeedsPreview(true);setError(e.message)}}
  finally{if(alive.current&&request.current===attempt)setSending(false)}
 };
 useEffect(()=>{alive.current=true;run('permissions.get');return()=>{alive.current=false;clear(request.current);request.current=null}},[session.id]);
 // The browser receives current action status, not the full command-history
 // map. Match acceptance exactly; another client's operation cannot settle it.
 const completion=pending&&state.actionStatus?.[pending.action];
 useEffect(()=>{
  if(!pending||completion?.commandId!==pending.id||!['ready','error'].includes(completion.phase))return;
  if(!settle(pending))return;
  if(completion.phase==='error'){setNeedsPreview(true);setError(completion.error||'Could not update file settings. Reload the preview before retrying.');return}
  const value=state.permissions;
  if(value?.scope!=='session'||value.sessionId!==session.id||value.commandId!==pending.id){setNeedsPreview(true);setError('The file-settings view changed. Reload the preview. Your entries are kept.');return}
  setReport(value);setNeedsPreview(false);
  if(!pending.keepEdits){setAllowed(value.allowed.join('\n'));setDenied(value.denied.join('\n'))}
  setNotice(pending.keepEdits?'Preview refreshed. Your entries are kept; review them before saving.':value.detail||'');
 },[pending,completion,state.permissions,session.id]);
 const busy=sending||!!pending;
 const ready=report&&report.sessionId===session.id;
 return <section className="a-settings-section">
  <p>These write settings apply only to this chat. Reading files, shared project settings, and other chats stay unchanged.</p>
  <p className="a-caption">File tools always include the workspace unless denied or restricted by the bundle. Denied folders take precedence. Tools and child workers may have stricter limits; shell commands have separate policies.</p>
  <button type="button" className="a-soft" data-action="permissions.get" disabled={busy} onClick={()=>run('permissions.get')}>Reload preview</button>
  {ready&&<>
   <p>{report.overridden.length?'This chat has its own write overrides.':'This chat inherits workspace write defaults.'}</p>
   <fieldset className="a-permissions-fields" disabled={busy}>
    <label htmlFor="chat-write-allowed">Allowed folders (one per line)</label>
    <PathField id="chat-write-allowed" multiline directory state={state} act={act} value={allowed} onChange={setAllowed}/>
    <label htmlFor="chat-write-denied">Denied folders (one per line)</label>
    <PathField id="chat-write-denied" multiline directory state={state} act={act} value={denied} onChange={setDenied}/>
   </fieldset>
   <button type="button" className="a-primary" data-action="permissions.save" disabled={busy||needsPreview} onClick={()=>run('permissions.save')}>Save for this chat</button>
   <section aria-label="Workspace defaults preview">
    <h3>Workspace defaults after reset</h3>
    <Paths label="Allowed folders" values={report.inherited.allowed}/>
    <Paths label="Denied folders" values={report.inherited.denied}/>
    <p>Returning to defaults removes this chat’s write overrides. This can allow more files, or fewer. Other tool and bundle restrictions still apply.</p>
    <button type="button" className="a-soft" data-action="permissions.reset" disabled={busy||needsPreview||!report.overridden.length} onClick={()=>run('permissions.reset')}>Return to workspace defaults</button>
   </section>
  </>}
  <p className="a-caption">Saved changes take effect after active work finishes, before this chat’s next message. They do not start a new turn.</p>
  {session.fileAccessPending&&<p role="status">Write settings are pending. Existing work keeps its current access until it finishes. Finish or stop existing child work before sending another message. No new turn has started.</p>}
  {busy&&<ResultNotice phase="working" message="Updating file settings…"/>}
  {error&&<ResultNotice phase="error" message={error}/>}
  {notice&&<ResultNotice phase="ready" message={notice}/>}
 </section>;
}
