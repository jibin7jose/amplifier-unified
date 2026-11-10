import React from 'react';

export function UpdateRestart({updates,act}){
 const target=updates.forceRestartTarget;
 const key=JSON.stringify(target||null);
 const [confirmation,setConfirmation]=React.useState(null),[submitting,setSubmitting]=React.useState(false),[error,setError]=React.useState('');
 const [request,setRequest]=React.useState(null);
 const button=React.useRef(null),cancelButton=React.useRef(null),restoreFocus=React.useRef(false);
 const current=confirmation?.key===key;
 const latest=updates.application?.latest?.replace(/^v/,'');
 const cancel=()=>{restoreFocus.current=true;setConfirmation(null);setError('')};
 React.useEffect(()=>{
  if(confirmation)cancelButton.current?.focus();
  else if(restoreFocus.current){restoreFocus.current=false;button.current?.focus()}
 },[confirmation]);
 React.useEffect(()=>{
  const result=updates.forceRestartResult;
  if(request&&result?.requestId===request.id&&result.status==='rejected'){
   setSubmitting(false);setError(result.detail);setRequest(null);
  }else if(request&&request.key!==key){setSubmitting(false);setRequest(null)}
 },[request,key,updates.forceRestartResult]);
 const confirm=async()=>{
  if(!current||submitting)return;
  setSubmitting(true);setError('');
  try{
   const result=await act('updates.forceRestart',{...confirmation.target,confirm:true});
   if(result?.accepted===false)throw Error(result.error||'The restart was not accepted. Review the current update.');
   setRequest({id:result?.requestId,key:confirmation.key});
   setConfirmation(null);
  }catch(error){setError(error?.message||'The restart could not be confirmed. Check update status before trying again.');setSubmitting(false)}
 };
 if(!target&&!confirmation)return null;
 return <div className="a-update-force-restart">
  {target&&!confirmation&&<button ref={button} type="button" className="a-soft" disabled={submitting} onClick={()=>{setError('');setConfirmation({key,target:{...target},version:updates.pendingApp?.latest?.replace(/^v/,'')})}}>Force restart…</button>}
  {confirmation&&<div role="alertdialog" aria-label="Confirm update restart" onKeyDown={event=>{if(event.key==='Escape'&&!submitting){event.stopPropagation();cancel()}}}>
   <p><strong>Interrupt active work and restart?</strong></p>
   <p>{confirmation.version?`This restarts into prepared Amplifier ${confirmation.version}.`:'This restarts into the prepared update.'}</p>
   {current&&latest&&confirmation.version&&latest!==confirmation.version&&<p>Amplifier {latest} is available, but this confirmation uses the prepared version above. Cancel and let the newer release finish preparing to use it instead.</p>}
   <p>Running chats, background workers, tools, and calls will be interrupted. Saved conversation history and drafts will be kept. Interrupted work will not be replayed automatically.</p>
   <p>Unresolved checkout handoffs will still require inspection after the restart.</p>
   {!current&&<p role="alert">The staged update or host changed. Cancel and review the current update before confirming again.</p>}
   <button type="button" className="a-primary" data-action="updates.forceRestart" disabled={!current||submitting} onClick={confirm}>{submitting?'Restart requested…':'Interrupt work and restart'}</button>
   <button ref={cancelButton} type="button" className="a-soft" disabled={submitting} onClick={cancel}>Cancel</button>
  </div>}
  {error&&<p role="alert">{error}</p>}
 </div>;
}
