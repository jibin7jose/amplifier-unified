import React from 'react';
import {Copy,Download} from 'lucide-react';
import {updateClientDiagnostics} from './update-diagnostics.js';

export function UpdateSupport({state,act}){
 const [report,setReport]=React.useState(null),[busy,setBusy]=React.useState(false),[notice,setNotice]=React.useState(''),[manual,setManual]=React.useState(false);
 async function collect(){
  const client=updateClientDiagnostics(state);
  const reply=await act('updates.diagnostics');
  if(!reply?.accepted||!reply.result)throw Error('Diagnostics could not be collected. Try again.');
  const value={...reply.result,client};setReport(value);return value;
 }
 async function deliver(destination){
  setBusy(true);setNotice('Collecting diagnostics…');setManual(false);
  try{
   const content=JSON.stringify(await collect(),null,2);
   if(destination==='copy'){
    try{await navigator.clipboard.writeText(content);setNotice('Diagnostics copied. You can paste them into your support chat.');}
    catch{setManual(true);setNotice('Clipboard unavailable. Copy the report below or save it as a file.');}
   }else{
    const url=URL.createObjectURL(new Blob([content],{type:'application/json'})),link=document.createElement('a');
    link.href=url;link.download='amplifier-update-diagnostics.json';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    setNotice('Diagnostic report saved.');
   }
  }catch(error){setNotice(error.message)}finally{setBusy(false)}
 }
 return <div data-part="update-support">
  <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="updates.diagnostics" onClick={()=>deliver('copy')}><Copy/><span>Copy diagnostics</span></button><button type="button" className="a-soft" disabled={busy} data-action="updates.diagnostics" onClick={()=>deliver('save')}><Download/><span>Save diagnostics</span></button></div>
  <p className="a-caption">Collect update errors, timings, versions and component source differences. Excludes credentials, chat content and private paths.</p>
  {notice&&<p role="status" className="a-caption">{notice}</p>}
  {manual&&report&&<textarea aria-label="Update diagnostic report" readOnly rows={6} value={JSON.stringify(report,null,2)}/>}

 </div>;
}
