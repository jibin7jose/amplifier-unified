import React,{createContext,useContext,useEffect,useId,useState} from 'react';
import {ChevronRight,GitBranch,Copy,Check} from 'lucide-react';
import {readDetail} from './conversation-detail';
import {MessageText} from './message-text';
import {QuoteCard,MessageInteractions} from './message-interactions';
import {AttachmentStrip} from './chat-controls';
import {peerAttention} from './peer-groups.js';
import './peer-message.css';

export const PeerPresentation=createContext(null);
export const isPeerMessage=message=>message.role==='user'&&(message.inputOrigin==='peer'||message.via==='peer');
function useDisclosure(key){
 const presentation=useContext(PeerPresentation),[local,setLocal]=useState(false);
 return presentation?[!!presentation.open[key],value=>presentation.setOpen(old=>({...old,[key]:typeof value==='function'?value(!!old[key]):value}))]:[local,setLocal];
}
export function PeerRun({row,session,state,renderMessage}){
 const presentation=useContext(PeerPresentation),ids=row.rows.map(r=>r.message.id),bodyId=useId();
 const known=ids.map(id=>presentation.open['group:'+session.id+':'+id]).filter(v=>v!==undefined);
 const open=known.length?known.some(Boolean):ids.some(id=>presentation.open[session.id+':'+id]);
 const setOpen=value=>presentation.setOpen(old=>({...old,...Object.fromEntries(ids.map(id=>['group:'+session.id+':'+id,typeof value==='function'?value(open):value]))}));
 const sources=new Map(row.rows.flatMap(r=>r.message.attribution?.peer?.available?[[r.message.attribution.peer.sourceId,r.message.attribution.peer]]:[]));
 const unavailable=row.rows.filter(r=>!r.message.attribution?.peer?.available).length;
 const focus=state.view?.messageFocus;
 useEffect(()=>{if(focus?.sessionId===session.id&&row.rows.some(r=>[r.message.id,r.message.nativeMessageId].includes(focus.messageId)))setOpen(true)},[focus?.revision]);
 const attention=peerAttention(row.rows.map(r=>r.message));
 return <section className="a-peer-run" data-peer-run={row.id}>
  <button className="a-peer-run-summary" type="button" aria-expanded={open} aria-controls={bodyId} onClick={()=>setOpen(v=>!v)}><ChevronRight className={open?'open':''}/><span>{ids.length} messages{sources.size>0?` · ${sources.size} ${unavailable?'available ':''}${sources.size===1?'chat':'chats'}`:''}</span><small>Peer messages</small></button>
  <p className="a-peer-sources">{[...sources.values()].slice(0,2).map(source=>[source.title,source.workspaceLabel].filter(Boolean).join(' — ')).join(' · ')}{sources.size>2?` · +${sources.size-2} more`:''}{sources.size===0?'Source chats unavailable':''}</p>
  {unavailable>0&&sources.size>0&&<p className="a-peer-sources">{unavailable} {unavailable===1?'message has an unavailable source':'messages have unavailable sources'}</p>}
  {attention.length>0&&<p className="a-peer-attention" role="status">{attention.join(' · ')}</p>}
  <div id={bodyId} hidden={!open}>{row.rows.map(r=>renderMessage(r))}</div>
 </section>;
}
export function PeerMessage({message,session,state,act,dispatch,date,label,attribution,newest}){
 const presentation=useContext(PeerPresentation),[open,setOpen]=useDisclosure(session.id+':'+message.id),bodyId=useId();
 const [copying,setCopying]=useState(false),[copied,setCopied]=useState(false),[error,setError]=useState(''),[opening,setOpening]=useState(false);
 const focus=state.view?.messageFocus,peer=message.attribution?.peer;
 const focused=focus?.sessionId===session.id&&[message.id,message.nativeMessageId].includes(focus.messageId);
 useEffect(()=>{if(focused)setOpen(true)},[focused,focus?.revision]);
 const receipt=state.view?.messageCopy,copyResult=receipt?.sessionId===session.id&&receipt?.messageId===message.id?receipt:null;
 async function copy(){
  setCopying(true);setError('');
  try{if(!message.displayOnly)await act('message.copy',{sessionId:session.id,messageId:message.id});
   else{await navigator.clipboard.writeText(message.textDetail?await readDetail(message.textDetail):message.text||'');setCopied(true)}}
  catch(e){setError(e.message)}finally{setCopying(false)}
 }
 async function preview(){if(opening)return;setOpening(true);setError('');try{await presentation.preview({peer,sourceSessionId:session.id,messageId:message.id})}catch(e){setError(e.message)}finally{setOpening(false)}}
 return <article className="a-message a-external-message a-peer-message" data-message-id={message.id} data-input-id={message.inputId||message.commandId} data-newest={newest||undefined} aria-label={label}>
  <div className="a-peer-header">
   <button type="button" className="a-peer-summary" aria-label={`Expand or collapse peer message ${message.id}`} aria-controls={bodyId} aria-expanded={open} onClick={()=>setOpen(value=>!value)}><ChevronRight className={open?'open':''}/><GitBranch/><span>{peer?'Message from':attribution||'Message from another chat'}</span></button>
   {peer&&(peer.available&&presentation?<button type="button" className="a-link a-peer-source" disabled={opening} aria-label={`Preview source chat: ${peer.title}`} onClick={preview}>{opening?'Opening preview…':peer.title}</button>:<span className="a-caption">Source chat unavailable</span>)}
   {peer?.available&&peer.workspaceLabel&&<small className="a-peer-workspace">{peer.workspaceLabel}</small>}
   {peer?.archived&&<small>Archived</small>}
   {date&&<time className="a-message-date" aria-label={date.full} dateTime={date.iso}>{date.text}</time>}
  </div>
  {attribution&&<p className="a-message-attribution">{attribution}</p>}
  {peerAttention([message]).length>0&&<p className="a-peer-attention">{peerAttention([message]).join(' · ')}</p>}
  {open&&<div id={bodyId} className="a-peer-content">
   <div className="a-message-body">
   <QuoteCard quote={message.replyTo} sessionId={session.id} dispatch={dispatch}/>
   <AttachmentStrip items={message.attachments} act={act} sessionId={session.id}/>
   <MessageText text={message.text||''} reference={message.textDetail} role="user" sessionId={session.id} messageId={message.id} workspace={session.workspace} act={act} displayOnly={message.displayOnly}/>
   </div>
   <div className="a-message-actions"><button type="button" className="a-icon" aria-label="Copy peer message as Markdown" title="Copy original message" disabled={copying} onClick={copy}>{copied||copyResult?.status==='ready'?<Check/>:<Copy/>}</button>{!message.displayOnly&&<MessageInteractions message={message} sessionId={session.id} dispatch={dispatch}/>}</div>
   {(copied||copyResult?.status==='ready')&&<p role="status" className="a-caption">Copied Markdown</p>}
  </div>}
  {(error||copyResult?.status==='error')&&<p role="alert">{error||copyResult.message||'Could not copy this message.'}</p>}
 </article>;
}
