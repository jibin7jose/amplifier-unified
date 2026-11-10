import {useEffect,useRef,useState} from 'react';
import {request} from './api';
import {createTitlePreviewCache,needsTitlePreview,titlePreviewKey,untitledLabel} from './chat-title-preview';
const preview=createTitlePreviewCache(chat=>request('/api/actions',{method:'POST',body:{action:'session.titlePreview',args:{sessionId:chat.id}}}).then(value=>value.result));
export function useChatTitle(chat,{eager=false}={}){
 const ref=useRef(null),[result,setResult]=useState(null),needed=needsTitlePreview(chat),key=chat?titlePreviewKey(chat):'';
 useEffect(()=>{
  if(!needed)return;
  let live=true,started=false;
  const load=()=>{if(started)return;started=true;preview(chat).then(value=>{if(live)setResult({key,value})})};
  let observer;
  if(eager)load();
  else if(ref.current&&typeof IntersectionObserver!=='undefined'){
   observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();load()}});
   observer.observe(ref.current);
  }
  return()=>{live=false;observer?.disconnect()};
 },[needed,key,eager,chat?.recentActivityAt]);
 const value=result?.key===key?result.value:null;
 return {ref,title:needed?(value?.title||untitledLabel(chat)):(chat?.title||'New chat'),source:needed?(value?.source||'unnamed'):'saved'};
}
