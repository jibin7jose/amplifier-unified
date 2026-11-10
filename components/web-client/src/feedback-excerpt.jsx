import React,{useEffect,useRef,useState} from 'react';

export function FeedbackExcerpt({state,act,frozen,onStage}){
 const session=state.sessions?.find(row=>row.id===state.selectedSessionId);
 return session?<Excerpt key={session.id} {...{session,act,frozen,onStage}}/>:null;
}
function Excerpt({session,act,frozen,onStage}){
 const rows=(session.messages||[]).filter(row=>row.id&&['user','assistant'].includes(row.role)&&!row.streaming);
 const [open,setOpen]=useState(false),[first,setFirst]=useState(rows.at(-1)?.id||''),[last,setLast]=useState(rows.at(-1)?.id||'');
 const [review,setReview]=useState(null),[text,setText]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [disclosure,setDisclosure]=useState(false),[extra,setExtra]=useState(false),[staged,setStaged]=useState(false);
 const ticket=useRef(0),retry=useRef(null);
 useEffect(()=>()=>{ticket.current++},[]);
 function reset(){ticket.current++;setReview(null);setDisclosure(false);setExtra(false);setError('');setStaged(false);retry.current=null}
 async function prepare(edited=false){
  const current=++ticket.current;setBusy(true);setError('');setDisclosure(false);setExtra(false);retry.current=null;
  try{
   const snapshot=edited?review.snapshotId:(await act('session.export',{id:session.id,format:'markdown',destination:'none',scope:'range',minimal:true,fromMessageId:first,throughMessageId:last}))?.result?.snapshotId;
   if(!snapshot)throw Error('The selected excerpt could not be prepared.');
   const response=await act('feedback.excerpt.review',{id:session.id,snapshotId:snapshot,...(edited?{text}:{})});
   if(!response?.result)throw Error('The excerpt or destination could not be checked. Nothing was sent.');
   if(current===ticket.current){setReview(response.result);setText(response.result.text);setStaged(false)}
  }catch(e){if(current===ticket.current)setError(e.message)}finally{if(current===ticket.current)setBusy(false)}
 }
 async function stage(){
  const current=++ticket.current;setBusy(true);setError('');
  retry.current ||= {id:session.id,reviewId:review.reviewId,requestId:crypto.randomUUID(),acknowledgeDisclosure:true,acknowledgeWarnings:extra};
  try{await onStage(retry.current);if(current===ticket.current){setStaged(true);retry.current=null}}
  catch(e){if(current===ticket.current)setError(e.message||'The attachment was not acknowledged. Check again using the same review.')}
  finally{if(current===ticket.current)setBusy(false)}
 }
 const dirty=review&&text!==review.text,locked=frozen||busy;
 const options=rows.map((row,index)=><option key={row.id+index} value={row.id}>{index+1}. {row.role==='user'?'You':'Amplifier'}: {String(row.text||'').replace(/\s+/g,' ').slice(0,80)}</option>);
 return <section aria-label="Optional conversation excerpt">
  <button type="button" className="a-soft" disabled={locked} onClick={()=>setOpen(!open)}>{open?'Hide excerpt review':'Attach transcript excerpt'}</button>
  {open&&<>
   <p className="a-caption">Optional. Starts with one message; choose only the context needed. Hidden instructions, tool payloads and file contents are excluded. Nothing uploads until you send feedback.</p>
   <label>Excerpt start<select aria-label="Excerpt start" value={first} disabled={locked} onChange={e=>{reset();setFirst(e.target.value)}}>{options}</select></label>
   <label>Excerpt end<select aria-label="Excerpt end" value={last} disabled={locked} onChange={e=>{reset();setLast(e.target.value)}}>{options}</select></label>
   <button type="button" className="a-soft" disabled={locked||!first||!last} onClick={()=>prepare()}>Preview selected excerpt</button>
   {review&&<div aria-label="Reviewed feedback excerpt">
    <p><strong>{review.visibility==='public'?'Publicly visible on GitHub':'Private GitHub repository'}</strong>: {review.repository}. Submitted content may remain in repository history and cannot be retracted through this app.</p>
    <p>{review.summary.messageCount} source messages · {review.bytes.toLocaleString()} bytes · {review.filename}</p>
    <p className="a-caption">{review.redactions.length?review.redactions.map(row=>`${row.count} ${row.kind.toLowerCase()} redaction(s)`).join(' · '):'No additional redactions in this review.'}</p>
    {review.warnings.map(warning=><p className="a-caption" key={warning}>{warning}</p>)}
    <textarea aria-label="Edit feedback excerpt" rows={8} maxLength={64000} value={text} disabled={locked||staged} onChange={e=>{ticket.current++;retry.current=null;setText(e.target.value);setDisclosure(false);setExtra(false)}}/>
    {dirty&&<button type="button" className="a-soft" disabled={locked} onClick={()=>prepare(true)}>Review edited excerpt</button>}
    <label className="a-feedback-checkbox"><input type="checkbox" checked={disclosure} disabled={locked||dirty||staged} onChange={e=>setDisclosure(e.target.checked)}/>I reviewed this exact text and agree to share it with {review.visibility==='public'?'the public':'people with access to this repository'}, where it may be retained.</label>
    {review.requiresExtraReview&&<label className="a-feedback-checkbox"><input type="checkbox" checked={extra} disabled={locked||dirty||staged} onChange={e=>setExtra(e.target.checked)}/>I reviewed the scope, sensitive-content warnings and automatic redactions.</label>}
    <button type="button" className="a-soft" disabled={locked||dirty||!disclosure||review.requiresExtraReview&&!extra||staged} onClick={stage}>{staged?'Excerpt attached locally':'Attach reviewed excerpt'}</button>
   </div>}
   {busy&&<p role="status">Preparing the local excerpt and checking its destination…</p>}
   {error&&<p role="alert">{error}</p>}
  </>}
 </section>;
}
