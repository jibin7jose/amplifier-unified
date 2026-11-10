import React,{useEffect,useId,useState} from 'react';
import {ChevronRight,FileText} from 'lucide-react';
import {voiceWorkLabel} from './voice-work-message.js';
import './voice-work-message.css';

export function VoiceWorkMessage({message,session,state,date,newest,embedded=false,children}){
 const id=useId(),focus=state.view?.messageFocus;
 const focused=focus?.sessionId===session.id&&[message.id,message.nativeMessageId].filter(Boolean).includes(focus.messageId);
 const [open,setOpen]=useState(focused);
 useEffect(()=>{if(focused)setOpen(true)},[focused,focus?.revision]);
 const label=voiceWorkLabel(message);
 if(embedded)return <article className="a-message a-voice-work-message" data-message-id={message.streaming?undefined:message.id} data-stream-id={message.streaming?message.id:undefined} data-input-id={message.inputId} data-newest={newest||undefined} aria-label={label}>
  <header className="a-voice-work-caption"><span>{label} · From your voice request</span>{date&&<time className="a-message-date" aria-label={date.full} dateTime={date.iso}>{date.text}</time>}</header>
  {children}
 </article>;
 return <article className="a-message a-voice-work" data-message-id={message.id} data-input-id={message.inputId} data-newest={newest||undefined} aria-label={label}>
  <button type="button" id={id+'-summary'} className="a-voice-work-summary" aria-expanded={open} aria-controls={id+'-body'} onClick={()=>setOpen(value=>!value)}>
   <ChevronRight className={open?'open':''}/><FileText/>
   <span><span className="a-voice-work-label">{label}</span><span className="a-voice-work-origin">From your voice request</span></span>
   {date&&<time className="a-message-date" aria-label={date.full} dateTime={date.iso}>{date.text}</time>}
  </button>
  {open&&<div id={id+'-body'} role="region" aria-labelledby={id+'-summary'} className="a-voice-work-content">{children}</div>}
 </article>;
}
