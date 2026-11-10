import React,{useState} from 'react';
import {Download,MessageSquare} from 'lucide-react';

export function ImageActions({canvas,dispatch,compact=false}){
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const target=()=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,canvas[key]]));
 const run=async action=>{
  if(busy)return;setBusy(true);setNotice('');
  try{
   await dispatch(action==='draft'?'canvas.views.imageDraft':'canvas.views.command',action==='draft'?target():{...target(),action:'canvas.download',args:{}});
   if(action==='draft')setNotice('Image added to your draft. Add your instructions, then send.');
  }catch(error){setNotice(error.message)}finally{setBusy(false)}
 };
 return <div className="a-image-actions"><button type="button" className="a-soft" aria-label="Download image" disabled={busy} onClick={()=>run('download')}><Download/>{!compact&&'Download image'}</button><button type="button" className="a-soft" aria-label={busy?'Working…':'Use in a follow-up'} disabled={busy} onClick={()=>run('draft')}><MessageSquare/>{!compact&&(busy?'Working…':'Use in a follow-up')}</button>{notice&&<p role="status">{notice}</p>}</div>;
}
