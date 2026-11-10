import React,{useRef,useState} from 'react';
import './new-chat-handoffs.css';

function HandoffCard({row,dispatch}){
 const [read,setRead]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[unconfirmed,setUnconfirmed]=useState(false);
 const running=useRef(false);
 const receipt=read&&read.revision>row.revision?read:row;
 const pending=receipt.status==='pending';
 async function perform(action){
  if(running.current)return;
  running.current=true;setBusy(true);setError('');
  if(action==='confirm')setUnconfirmed(true);
  try{
   const args={requestId:receipt.requestId,...(action==='confirm'?{reviewHash:receipt.reviewHash}:{})};
   const response=await dispatch('session.handoff.'+action,args,{feedback:false});
   if(response?.accepted!==true||!response.result)throw Error('The result could not be confirmed. Check status before taking another action.');
   setRead(response.result);setUnconfirmed(false);
  }catch(e){setError(e.message||'The result could not be confirmed. Check status before taking another action.')}
  finally{running.current=false;setBusy(false)}
 }
 async function open(){
  if(running.current)return;running.current=true;setBusy(true);setError('');
  try{await dispatch('session.select',{id:receipt.targetSessionId})}
  catch(e){setError(e.message)}finally{running.current=false;setBusy(false)}
 }
 const model=[receipt.selection?.instance,receipt.selection?.model,receipt.selection?.effort].filter(Boolean).join(' · ')||'Workspace / bundle default';
 const review=<div className="a-handoff-review">
  <dl><dt>Chat</dt><dd>{receipt.title}</dd><dt>Folder</dt><dd>{receipt.workspace}</dd><dt>Bundle</dt><dd>{receipt.bundle}</dd><dt>Model</dt><dd>{model}</dd></dl>
  <p className="a-caption">Uses this folder’s configuration and file access.</p>
  <div className="a-handoff-brief"><strong>Opening message</strong><pre>{receipt.text}</pre></div>
 </div>;
 return <article className="a-handoff" data-handoff-id={receipt.requestId} aria-label="Create a new chat">
  {pending?<><h3>Create a new chat</h3>{review}</>:<details><summary>{receipt.title}</summary>{review}</details>}
  <p role="status">{receipt.message}</p>
  <div className="a-dialog-actions">
   {pending&&!unconfirmed&&<><button type="button" className="a-primary" data-action="session.handoff.confirm" disabled={busy} onClick={()=>perform('confirm')}>Create and start</button><button type="button" className="a-soft" data-action="session.handoff.cancel" disabled={busy} onClick={()=>perform('cancel')}>Not now</button></>}
   {(unconfirmed||!['pending','cancelled'].includes(receipt.status))&&<button type="button" className="a-link" data-action="session.handoff.read" disabled={busy} onClick={()=>perform('read')}>Check status</button>}
   {receipt.creation==='created'&&receipt.targetSessionId&&<button type="button" className="a-link" data-action="session.select" disabled={busy} onClick={open}>Open chat</button>}
  </div>
  {error&&<p role="alert">{error}</p>}
 </article>;
}

export function NewChatHandoffs({state,dispatch}){
 const rows=(state.handoffs||[]).filter(row=>row.sourceSessionId===state.selectedSessionId);
 if(!rows.length)return null;
 return <section className="a-handoffs" aria-label="New chat requests">{rows.map(row=><HandoffCard key={row.requestId} row={row} dispatch={dispatch}/>)}</section>;
}
