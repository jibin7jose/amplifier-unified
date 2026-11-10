import React,{createContext,useContext} from 'react';
import {Archive,ArchiveRestore,X} from 'lucide-react';

export const ArchiveContext=createContext(null);
export function ArchiveButton({chat,archived=false,children,...props}){
 const archive=useContext(ArchiveContext),busy=archive?.pending.includes(chat.id),blocked=archive?.pending.length>0;
 return <button type="button" {...props} data-action={archived?'session.restore':'session.archive'} aria-disabled={blocked||undefined} aria-busy={busy||undefined} onClick={()=>{if(!blocked)archive.run(chat,!archived)}}>{archived?<ArchiveRestore/>:<Archive/>}{busy?'Saving…':children|| (archived?'Restore':'Archive')}</button>;
}
export function ArchiveNotice({value}){
 const notice=value.notice;if(!notice)return null;
 return <div className="a-alert" style={notice.phase==='error'?undefined:{color:'var(--a-ink)',background:'var(--a-soft)',borderColor:'var(--a-line)'}} data-archive-notice role={notice.phase==='error'?'alert':'status'} aria-live={notice.phase==='error'?'assertive':'polite'} aria-atomic="true">
  <span>{notice.text}{notice.title&&<> · {notice.title}</>}</span>
  {notice.phase==='success'&&notice.archive&&<button type="button" className="a-link" data-action="session.restore" disabled={value.pending.includes(notice.id)} onClick={()=>value.run({id:notice.id,title:notice.title},false)}>Undo</button>}
  {notice.phase!=='pending'&&<button type="button" className="a-icon" aria-label="Dismiss archive notification" onClick={value.dismiss}><X/></button>}
 </div>;
}