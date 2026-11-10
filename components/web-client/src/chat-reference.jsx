import React,{useEffect,useId,useMemo,useRef,useState} from 'react';
import {ArrowRight,MessageSquare} from 'lucide-react';
import * as api from './api';
import './chat-reference.css';

const referenceFields=new Set([
 'id','sessionId','messageId','version','revision','targetSessionId',
 'title','originalTitle','workspace','archived','available',
]);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=(value,limit)=>typeof value==='string'&&value.trim().length>0&&
 [...value].length<=limit&&!/[\u0000-\u001f\u007f-\u009f]/u.test(value);

export function validateChatReferenceMetadata(value,sessionId,messageId){
 const malformed=detail=>{throw new Error(`Malformed chat reference metadata: ${detail}.`)};
 if(!object(value)||!Array.isArray(value.items)||value.items.length>20||
  typeof value.overflow!=='boolean'||
  Object.keys(value).some(key=>!['items','overflow'].includes(key)))
  malformed('expected a bounded items array and boolean overflow');
 const ids=new Set();
 for(const item of value.items){
  if(!object(item)||Object.keys(item).some(key=>!referenceFields.has(key))||
   !text(item.id,100)||ids.has(item.id)||
   !text(item.sessionId,200)||item.sessionId!==sessionId||
   !text(item.messageId,200)||item.messageId!==messageId||
   !text(item.targetSessionId,200)||
   !Number.isSafeInteger(item.version)||item.version<1||
   !Number.isSafeInteger(item.revision)||item.revision<1)
   malformed('invalid or duplicate identity, source, version or revision');
  if(!text(item.title,200)||!text(item.originalTitle,200)||!text(item.workspace,200)||
   typeof item.archived!=='boolean'||typeof item.available!=='boolean')
   malformed('invalid labels or availability');
  ids.add(item.id);
 }
 return value;
}

function ChatReferenceCard({item,sessionId,messageId,revision,blocked,dispatch}){
 const helpId=useId(),errorId=useId(),renameId=useId(),button=useRef(null),mounted=useRef(false);
 const opening=useRef(false),request=useRef(null),returnFocus=useRef(null);
 const [pending,setPending]=useState(false),[error,setError]=useState('');
 const scope=JSON.stringify([sessionId,messageId,revision]);
 const latest=useRef(scope);latest.current=scope;
 useEffect(()=>{
  mounted.current=true;
  return ()=>{
   mounted.current=false;request.current=null;returnFocus.current=null;
  };
 },[]);
 useEffect(()=>{
  if(!pending&&returnFocus.current){
   const current=returnFocus.current===latest.current;returnFocus.current=null;
   if(current&&button.current?.isConnected)button.current.focus({preventScroll:true});
  }
 },[pending,error]);

 async function openChat(){
  if(opening.current||blocked||!item.available||!mounted.current)return;
  // A lost delivery is not permission to navigate again, even if a newer local
  // intent left the host revision unchanged. Use ordinary navigation to inspect.
  if(request.current?.unconfirmed){
   setError('Opening was not confirmed. Check the destination in the chat list; this request will not be replayed.');
   return;
  }
  opening.current=true;setPending(true);setError('');
  request.current ||= {
   id:crypto.randomUUID(),scope,unconfirmed:false,
   args:{id:item.targetSessionId,chatReference:{
    id:item.id,version:item.version,sourceSessionId:sessionId,messageId,
    expectedRevision:item.revision,
   }},
  };
  const command=request.current;
  const current=()=>mounted.current&&latest.current===command.scope;
  let failed=false;
  try{
   // This is the ordinary guarded dispatcher. Metadata cannot supply an action,
   // URL, navigation callback, or additional command arguments.
   const receipt=await dispatch('session.select',command.args,{id:command.id});
   if(receipt?.accepted===true){
    if(request.current===command)request.current=null;
   }else{
    failed=!receipt?.superseded;
    if(receipt?.accepted===false){
     // A local Settings cancellation does not resolve earlier unknown delivery.
     if(!command.unconfirmed&&request.current===command)request.current=null;
     if(current())setError(typeof receipt.error==='string'?receipt.error:'The chat was not opened.');
    }else{
     command.unconfirmed=true;
     if(current())setError('Opening the chat was not confirmed. No automatic retry will run.');
    }
   }
  }catch(cause){
   failed=!cause?.navigationSuperseded;
   if(cause?.unconfirmed||!Number.isInteger(cause?.status))command.unconfirmed=true;
   else if(request.current===command)request.current=null;
   if(current())setError(typeof cause?.message==='string'?cause.message:'Opening the chat was not confirmed.');
  }finally{
   opening.current=false;
   if(mounted.current){
    returnFocus.current=failed&&current()?command.scope:null;
    setPending(false);
   }
  }
 }

 const action=item.archived?'Open archived chat':'Open chat';
 const disabled=blocked||pending||!item.available;
 const renamed=item.available&&item.originalTitle!==item.title;
 return <div className="a-chat-reference" data-chat-reference-id={item.id}>
  <button ref={button} type="button" className="a-chat-reference-button"
   data-action="session.select" disabled={!item.available} aria-disabled={disabled||undefined}
   aria-busy={pending||undefined} data-operation-pending={pending||undefined}
   aria-label={`${item.available?action:'Chat unavailable'}: ${item.title} · ${item.workspace}`}
   aria-describedby={`${helpId}${renamed?` ${renameId}`:''}${error?` ${errorId}`:''}`} onClick={openChat}>
   <MessageSquare className="a-chat-reference-icon" aria-hidden="true"/>
   <span className="a-chat-reference-copy">
    <span className="a-chat-reference-action">{!item.available?'Chat unavailable':pending?'Opening chat…':action}</span>
    <span className="a-chat-reference-title">{item.title}</span>
    <span className="a-chat-reference-workspace">{item.workspace}</span>
   </span>
   <ArrowRight className="a-chat-reference-arrow" aria-hidden="true"/>
  </button>
  <p id={helpId} className="a-chat-reference-help">
   {!item.available?'This chat is unavailable. No other chat will be opened.':
    item.archived?'Opens here for viewing. The chat stays archived; no message is sent or work started.':
     'Opens here. Does not send a message or start work.'}
  </p>
  {renamed&&<p id={renameId} className="a-chat-reference-help a-chat-reference-original">
   Originally shared as “<bdi>{item.originalTitle}</bdi>”. Same chat, current title.
  </p>}
  {error&&<p id={errorId} className="a-chat-reference-error" role="alert">{error}</p>}
 </div>;
}

export function ChatReferences({sessionId,messageId,revision='',available,configurationBusy=false,dispatch}){
 const anchor=useRef(null),[visible,setVisible]=useState(false);
 const [result,setResult]=useState(null),[error,setError]=useState(null),[retry,setRetry]=useState(0);
 const owns=value=>value?.sessionId===sessionId&&value?.messageId===messageId;
 // A configuration/visibility round trip needs fresh metadata even if the host
 // token did not change. Identity makes old results inert before effects run.
 const metadataScope=useMemo(()=>({sessionId,messageId,revision,retry,available,configurationBusy,visible}),
  [sessionId,messageId,revision,retry,available,configurationBusy,visible]);
 const current=available===true&&owns(result)?result.value:null;
 const fresh=result?.scope===metadataScope;
 const failure=!configurationBusy&&available===true&&owns(error)&&error.scope===metadataScope?error.message:'';
 useEffect(()=>{
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(rows=>setVisible(rows.some(row=>row.isIntersecting)),{rootMargin:'200px'});
  if(anchor.current)observer.observe(anchor.current);
  return ()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  const {sessionId,messageId,available,configurationBusy,visible}=metadataScope;
  if(available!==true){setResult(null);setError(null)}
  if(configurationBusy)setError(null);
  // Host coverage, not a transcript scan, decides whether discovery is needed.
  // Keep existing cards inert during refresh or configuration replacement.
  if(configurationBusy||!visible||available!==true)return;
  let disposed=false;
  const abort=new AbortController();
  setError(null);
  api.request('/api/actions',{method:'POST',signal:abort.signal,body:{
   action:'outputs.chatReferences',args:{sessionId,messageId},
  }}).then(receipt=>{
   if(disposed)return;
   if(receipt?.accepted!==true)throw new Error('Chat reference discovery was not confirmed.');
   const value=validateChatReferenceMetadata(receipt.result,sessionId,messageId);
   setResult({sessionId,messageId,scope:metadataScope,value});
  }).catch(cause=>{
   if(!disposed&&cause?.name!=='AbortError')
    setError({sessionId,messageId,scope:metadataScope,
     message:typeof cause?.message==='string'?cause.message:'Chat references could not be read.'});
  });
  return ()=>{disposed=true;abort.abort()};
 },[metadataScope]);
 return <div ref={anchor} className="a-chat-references">
  {current?.items.map(item=><ChatReferenceCard
   key={JSON.stringify([sessionId,messageId,item.id,item.version,item.revision])}
   item={item} sessionId={sessionId} messageId={messageId} revision={revision}
   blocked={configurationBusy||!fresh||!!failure} dispatch={dispatch}/>)}
  {!!current?.items.length&&!failure&&configurationBusy&&
   <p className="a-chat-reference-help" role="status">Chat settings are changing. Open chat is paused.</p>}
  {failure&&<div className="a-chat-reference-failure">
   <p className="a-chat-reference-error" role="alert">{failure}</p>
   <button type="button" className="a-soft" data-action="outputs.chatReferences"
    onClick={()=>setRetry(value=>value+1)}>Retry chat references</button>
  </div>}
  {current?.overflow&&<p className="a-chat-reference-help">Only the first 20 chat references are displayed.</p>}
 </div>;
}