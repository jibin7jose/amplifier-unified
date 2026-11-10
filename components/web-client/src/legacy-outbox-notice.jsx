import React,{useId,useState} from 'react';

const key='amplifier.legacyOutboxNotice.v1';

// Presentation only: never copy message bodies or alter the delivery journal.
// Compare the exact set, not a count, so a new unbound entry resurfaces.
function retainedSet(entries){
 return entries.filter(row=>!row.scope).map(row=>JSON.stringify([row.id,row.commandId,row.sessionId,row.createdAt??null])).sort();
}

export function LegacyOutboxNotice({entries}){
 const [hidden,setHidden]=useState(()=>{try{return sessionStorage.getItem(key)||''}catch{return ''}});
 const [storageError,setStorageError]=useState(false),descriptionId=useId();
 const retained=retainedSet(entries),identity=JSON.stringify(retained);
 const expanded=hidden!==identity;
 function toggle(){
  const next=expanded?identity:'';
  setHidden(next);
  try{sessionStorage.setItem(key,next);setStorageError(false)}catch{setStorageError(true)}
 }
 if(!retained.length)return null;
 return <div className="a-upload-status" data-part="legacy-outbox-notice">
  {expanded&&<p id={descriptionId} role="status">This tab retains earlier message copies with no verified host identity. Their delivery is unknown. Nothing was sent again.</p>}
  <button type="button" className="a-link" aria-expanded={expanded} aria-controls={expanded?descriptionId:undefined} onClick={toggle}>
   {expanded?'Hide notice':`Retained copies (${retained.length})`}
  </button>
  {storageError&&<p role="status">The notice preference could not be saved. It may reappear after reload. Message copies are unchanged.</p>}
 </div>;
}
