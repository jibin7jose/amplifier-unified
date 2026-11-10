import React,{useEffect,useRef,useState} from 'react';
import {LockKeyhole,Loader} from 'lucide-react';
import {ownershipState,actionErrorMessage} from './ownership.js';
import './composer-ownership.css';

/** Gate only the composer: reading history and navigating remain available. */
export function ComposerOwnership({session,runtimeAvailable=true,dispatch,children}){
 const ownership=ownershipState(session),id=session?.id;
 const [request,setRequest]=useState(null),[failure,setFailure]=useState(null);
 const gate=useRef(null),pending=useRef(null),requested=useRef(null),currentId=useRef(id);
 currentId.current=id;
 const requesting=request===id,blocked=ownership.blocked||requesting;
 const unavailable=session?.workspaceAvailable===false?'This workspace is unavailable. Restore access to it before continuing.':session?.historyReadOnlyReason||(!runtimeAvailable?'The Amplifier runtime is unavailable. Check Settings before trying again.':'');
 const error=failure&&failure.id===id?failure.message:'';
 useEffect(()=>setFailure(null),[id,session?.ownership?.status,session?.ownership?.reason,session?.ownership?.detail]);
 useEffect(()=>{
  if(blocked||requested.current!==id)return;
  requested.current=null;
  // Restore the typing position after an explicit takeover, without stealing
  // focus if the user moved to history, navigation, or a settings panel.
  if(document.activeElement===document.body||gate.current?.contains(document.activeElement))gate.current?.querySelector('[aria-label="Message Amplifier"]')?.focus();
 },[blocked,id]);
 async function takeOver(){
  if(pending.current===id||!ownership.canTakeover||unavailable)return;
  const target=id;pending.current=target;requested.current=target;setRequest(target);setFailure(null);
  try{await dispatch('session.takeover',{id:target})}
  catch(error){if(currentId.current===target)setFailure({id:target,message:actionErrorMessage(error)})}
  finally{if(pending.current===target)pending.current=null;setRequest(value=>value===target?null:value)}
 }
 const waiting=requesting||session?.ownership?.status==='taking-over';
 return <div className={`a-composer-access ${blocked?'is-blocked':''}`} ref={gate}>
  <fieldset className="a-composer-fields" disabled={blocked} inert={blocked}>{children}</fieldset>
  {blocked&&<section className="a-composer-lock" aria-label="Conversation access" aria-busy={waiting}>
   <div className="a-composer-lock-copy" role="status" aria-live="polite" aria-atomic="true">
    {waiting?<Loader className="a-progress-spinner" aria-hidden="true"/>:<LockKeyhole aria-hidden="true"/>}
    <div><strong>{waiting?'Taking over…':ownership.label}</strong><p>{waiting?'Waiting for ownership, then loading your session. Your draft is kept.':ownership.detail}</p>{unavailable&&unavailable!==ownership.detail&&<p>{unavailable}</p>}</div>
   </div>
   {error&&<p className="a-composer-lock-error" role="alert">{error} Your draft is kept. Try again when the connection is available.</p>}
   {ownership.canTakeover&&<button type="button" className="a-primary" data-action="session.takeover" disabled={requesting||!!unavailable} onClick={takeOver}>{requesting?'Taking over…':ownership.retry?'Try again':'Continue here'}</button>}
  </section>}
 </div>;
}
