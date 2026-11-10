import {useEffect,useMemo,useRef,useState} from 'react';
import {request} from './api';

// Historical reader windows remain immutable. Observe only the current viewport's
// canonical rows and overlay their small annotation records, never whole history.
export function useVisibleMessageAnnotations(session,enabled){
 const id=session?.id,revision=session?.messageAnnotationRevision||0;
 const key=JSON.stringify((session?.messages||[]).filter(row=>!row.displayOnly).map(row=>row.id));
 const [saved,setSaved]=useState(null),[error,setError]=useState(null),[retry,setRetry]=useState(0);
 const owner=useRef(id);owner.current=id;
 useEffect(()=>{
  if(!enabled||!id||typeof document==='undefined'||!globalThis.IntersectionObserver)return;
  const pane=document.querySelector('.a-messages');if(!pane)return;
  const eligible=new Set(JSON.parse(key)),visible=new Set();let timer,abort,signature='',disposed=false;
  setError(null);
  const read=async()=>{
   timer=null;const messageIds=[...visible].sort().slice(0,100),next=JSON.stringify([revision,messageIds]);
   if(!messageIds.length||next===signature)return;
   signature=next;abort?.abort();const controller=new AbortController();abort=controller;
   try{
    const value=await request('/api/conversation/annotations',{method:'POST',body:{sessionId:id,messageIds},signal:controller.signal});
    if(disposed||controller.signal.aborted||owner.current!==id)return;
    if(value.sessionId!==id||!Number.isSafeInteger(value.revision)||value.revision<revision||!value.items||messageIds.some(messageId=>!Array.isArray(value.items[messageId]?.reactions)))throw Error('Message reactions could not be refreshed.');
    setSaved({id,revision:value.revision,items:Object.fromEntries(messageIds.map(messageId=>[messageId,{reactions:[...value.items[messageId].reactions],assistantReactions:[...(value.items[messageId].assistantReactions||[])],annotationRevision:value.revision}]))});setError(null);
   }catch(e){if(!disposed&&!controller.signal.aborted&&owner.current===id)setError({id,message:e.message||'Message reactions could not be refreshed.'})}
  };
  const observer=new IntersectionObserver(entries=>{
   for(const entry of entries){const identity=entry.target.dataset.messageId;if(entry.isIntersecting)visible.add(identity);else visible.delete(identity)}
   clearTimeout(timer);timer=setTimeout(read,40);
  },{root:pane,rootMargin:'200px'});
  for(const element of pane.querySelectorAll('[data-message-id]'))if(eligible.has(element.dataset.messageId))observer.observe(element);
  return()=>{disposed=true;observer.disconnect();clearTimeout(timer);abort?.abort()};
 },[enabled,id,revision,key,retry]);
 const messages=useMemo(()=>enabled&&saved?.id===id?(session?.messages||[]).map(row=>saved.items[row.id]?{...row,...saved.items[row.id]}:row):session?.messages,[enabled,id,session?.messages,saved]);
 return {messages,error:enabled&&error?.id===id?error.message:'',retry:()=>setRetry(value=>value+1)};
}
