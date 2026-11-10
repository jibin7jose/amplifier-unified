import React,{useEffect,useRef,useState} from 'react';

export function AccessMode({session,act,globalOnly=false}){
 const [report,setReport]=useState(null),[scope,setScope]=useState(globalOnly?'app':'session'),[mode,setMode]=useState('inherit');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const generation=useRef(0);
 const run=async action=>{
  const current=++generation.current;setBusy(true);setError('');setNotice('');
  let timer;
  try{
   const result=await Promise.race([
    act(action,{...(globalOnly?{scope:'app'}:{sessionId:session.id}),...(action==='access.set'?{scope,mode,expectedRevision:report.revision}:{})}),
    new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('The result is not confirmed. Reload access settings to check; this change will not be repeated.')),15000)})]);
   if(current!==generation.current)return;
   if(!result?.accepted)throw Error(result?.error||'Could not read access settings.');
   setReport(result.result);setMode(result.result.overrides[scope].mode);
   if(action==='access.set')setNotice('Access preference saved. Pending permission requests use this choice. No new work was started.');
  }catch(e){if(current===generation.current){setError(e.message);setReport(null)}}
  finally{clearTimeout(timer);if(current===generation.current)setBusy(false)}
 };
 useEffect(()=>{setReport(null);run('access.get');return()=>{generation.current++}},[session?.id,globalOnly]);
 return <section className="a-settings-section" aria-label="Access and approvals">
  {globalOnly&&<p>Default for all existing and new chats across every workspace on this Amplifier host. Explicit chat and workspace choices still take precedence.</p>}
  <p>Full access lets Amplifier use files, tools and configuration without asking for each permission. Enable it for work you trust.</p>
  {report&&<p role="status">Current mode: <strong>{report.mode==='full'?'Full access':'Ask when needed'}</strong> · {({session:'This chat',workspace:'Workspace default',app:'App default',default:'Default',inherited:'Source task settings',unavailable:'Source settings unavailable'})[report.source]}</p>}
  <fieldset className="a-permissions-fields" disabled={busy||!report}>
   {!globalOnly&&<label>Apply to <select aria-label="Access scope" value={scope} onChange={e=>{setScope(e.target.value);setMode(report.overrides[e.target.value].mode)}}>
    <option value="session">This chat</option><option value="workspace">This workspace</option><option value="app">App default</option>
   </select></label>}
   <label>Access mode <select aria-label="Access mode" value={mode} onChange={e=>setMode(e.target.value)}>
    <option value="inherit">{scope==='app'?'Use default (Ask when needed)':'Use inherited default'}</option>
    <option value="ask">Ask when needed</option><option value="full">Full access</option>
   </select></label>
   <button type="button" className="a-primary" onClick={()=>run('access.set')}>Save access preference</button>
  </fieldset>
  <button type="button" className="a-soft" disabled={busy} onClick={()=>run('access.get')}>Reload access settings</button>
  <p className="a-caption">Saved until you change it, including after Stop or restart. More specific chat choices override workspace and app defaults. Operating system permissions, account sign-ins and task ownership still apply.</p>
  {(session?.fileAccessPending||report?.fileAccessPending)&&<p role="status">File access changes apply after existing work and child workers finish. Approvals use the saved mode now.</p>}
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
