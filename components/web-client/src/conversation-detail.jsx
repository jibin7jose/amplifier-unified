import React,{useEffect,useMemo,useRef,useState} from 'react';
import {request} from './api';
import {useVisibleMessageAnnotations} from './message-annotations';
import {Markdown} from './markdown';
import {readDetail} from './detail-read';
import {hasEarlierWindow,returnedWindow,validateHistoryPage} from './history-window';
export {readDetail} from './detail-read';
export function DetailText({text,reference,markdown=false,automatic=false,writingContext,fileContext,userContent=false}){
 const [loaded,setLoaded]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[visible,setVisible]=useState(false);
 const element=useRef(null),abort=useRef(null),key=reference?JSON.stringify(reference):null,current=useRef(key);current.current=key;
 const value=loaded?.key===key?loaded.value:null;
 useEffect(()=>{setLoaded(null);setError('');setBusy(false);return()=>abort.current?.abort()},[key]);
 useEffect(()=>{
  if(!automatic||!reference)return;
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){setVisible(true);observer.disconnect()}},{rootMargin:'400px'});
  if(element.current)observer.observe(element.current);return()=>observer.disconnect();
 },[automatic,key]);
 async function load(){
  const captured=key,controller=new AbortController();abort.current?.abort();abort.current=controller;setBusy(true);setError('');
  try{const full=await readDetail(reference,request,controller.signal);if(!controller.signal.aborted&&current.current===captured)setLoaded({key:captured,value:full})}
  catch(e){if(!controller.signal.aborted&&current.current===captured)setError(e.message)}
  finally{if(!controller.signal.aborted&&current.current===captured)setBusy(false)}
 }
 useEffect(()=>{if(automatic&&visible&&reference)load()},[automatic,visible,key]);
 return <div className="a-detail-text" ref={element}>{markdown?<Markdown text={value??text} mapSource userContent={userContent} writingContext={writingContext} fileContext={fileContext}/>:<p>{value??text}</p>}{reference&&value===null&&(automatic&&!error?<span className="a-caption" role="status">{busy?'Loading complete response…':'Complete response loads when visible.'}</span>:<button type="button" className="a-link" disabled={busy} onClick={load}>{busy?'Loading full text…':error?'Retry loading full text':'Show full text'}</button>)}{error&&<p role="alert">{error}</p>}</div>;
}
const unique=rows=>[...new Map(rows.map(row=>[row.id,row])).values()];
export function useConversationDetail(source,beforeApply){
 const [saved,setSaved]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState(''),[focused,setFocused]=useState(null),[jumpTarget,setJumpTarget]=useState(null),[nativeView,setNativeView]=useState(null);
 const current=useRef(null);
 const sourceKey=source?.id;
 if(current.current?.key!==sourceKey)current.current={key:sourceKey};
 const paging=useRef(null),[frozen,setFrozen]=useState(null);
 const reads=useRef(new Set());
 // A read belongs to the visible chat. Releasing it never cancels an accepted
 // command. Saved-history paging is a disposable read only.
 useEffect(()=>()=>{for(const token of reads.current)if(token.id===sourceKey)token.abort.abort()},[sourceKey]);
 const beginRead=id=>{const token={id,abort:new AbortController()};reads.current.add(token);paging.current=token;return token};
 const finishRead=token=>{reads.current.delete(token);token.abort.abort()};
 const originalSource=source;
 useEffect(()=>()=>{paging.current=null},[]);
 if(frozen&&source&&frozen.id===source.id)source={...source,messages:frozen.messages,
  messageWindow:frozen.messageWindow,execution:frozen.execution,executionWindow:frozen.executionWindow,
  workProjection:frozen.workProjection?{...frozen.workProjection,current:false}:undefined};
 const previous=useRef(source?.messageWindow?.total);
 useEffect(()=>{if(paging.current?.id===originalSource?.id)return;setSaved(null);setError('');setBusy('');setFrozen(null);setNativeView(null)},[originalSource?.id]);
 useEffect(()=>{setFocused(null);setJumpTarget(null)},[originalSource?.id]);
 useEffect(()=>{if(source?.messageWindow?.total<previous.current)setSaved(null);previous.current=source?.messageWindow?.total},[source?.messageWindow?.total]);
 const extra=saved?.id===source?.id?saved:null;
 // Browser deltas preserve unchanged branches. Keep that identity through the
 // annotation/page overlay rather than rebuilding the transcript on draft ticks.
 const currentAnnotations=useMemo(()=>new Map((originalSource?.messages||[]).map(row=>[row.id,{...originalSource.messageAnnotations?.[row.id],reactions:originalSource.messageAnnotations?.[row.id]?.reactions||[],assistantReactions:originalSource.messageAnnotations?.[row.id]?.assistantReactions||[],annotationRevision:originalSource.messageAnnotationRevision||0}])),[originalSource?.messages,originalSource?.messageAnnotations,originalSource?.messageAnnotationRevision]);
 const messages=useMemo(()=>source?.messages.map(row=>currentAnnotations.has(row.id)?{...row,...currentAnnotations.get(row.id)}:row),[source?.messages,currentAnnotations]);
 const projected=useMemo(()=>source?{...source,messages}:source,[source,messages]);
 const expandedMessages=useMemo(()=>extra?unique([...extra.messages,...(messages||[])]).map(row=>currentAnnotations.has(row.id)?{...row,...currentAnnotations.get(row.id)}:row):messages,[extra?.messages,currentAnnotations,messages]);
 const regular=useMemo(()=>source&&extra?{...source,messages:expandedMessages,sharedHistoryUserTurnOffset:extra.userOffset??source.sharedHistoryUserTurnOffset,
  messageWindow:{...source.messageWindow,...(extra.messageWindow||(extra.messages.length?{offset:extra.sourceOffset!=null?Math.max(0,extra.sourceOffset-(source.historyManaged?source.sharedHistoryOffset||0:0)):extra.messageOffset,sourceOffset:extra.sourceOffset,before:extra.messages[0].id}:{}))},
  execution:{...source.execution,nodes:unique([...extra.nodes,...(source.execution?.nodes||[])]),turns:unique([...extra.turns,...(source.execution?.turns||[])]),segments:unique([...(extra.segments||[]),...(source.execution?.segments||[])])},
  executionWindow:{...source.executionWindow,...extra.groupWindow,...(extra.nodes.length?{offset:extra.nodeOffset,before:extra.nodes[0].id}:{})}}:projected,[source,extra,projected,expandedMessages]);
 const focusedHere=focused?.sessionId===source?.id?focused:null;
 const nativeHere=nativeView?.id===source?.id?nativeView:null;
 const windowSession=useMemo(()=>nativeHere?{...projected,messages:nativeHere.messages,historyLoading:false,historyError:null,messageWindow:nativeHere.window,workProjection:undefined,streaming:'',execution:{nodes:[],turns:[],segments:[]},executionWindow:null}:focusedHere?{...projected,messages:focusedHere.messages,historyLoading:false,historyError:null,workProjection:undefined,streaming:'',execution:focusedHere.execution,
  sharedHistoryOffset:0,navigationOffset:focusedHere.sourceOffset??focusedHere.offset,sharedHistoryUserTurnOffset:focusedHere.userOffset,messageWindow:{mode:'focus',status:'ready'},executionWindow:undefined}:regular,[nativeHere,focusedHere,projected,regular]);
 const annotations=useVisibleMessageAnnotations(windowSession,!nativeHere&&!!(focusedHere||frozen||extra));
 const session=useMemo(()=>windowSession?{...windowSession,messages:annotations.messages}:windowSession,[windowSession,annotations.messages]);
 async function around(messageId,{jump=true,isCurrent=()=>true,reference}={}){
  if(paging.current||!originalSource)return false;
  const id=originalSource.id,token=beginRead(id);setBusy('conversation');setError('');
  try{
   // Native display IDs belong to a bounded page, not the canonical navigation
   // index. Reopen the signed page directly, even after its cache was evicted.
   if(messageId.startsWith('native-window:')){
    if(reference?.kind!=='transcript-window'||typeof reference.restoreCursor!=='string'||!reference.restoreCursor)throw Error('This saved reading position is no longer available. Return to latest and load earlier messages.');
    const result=validateHistoryPage(await request('/api/conversation/detail?'+new URLSearchParams({sessionId:id,part:'messages',native:'true',before:reference.restoreCursor}),{signal:token.abort.signal}),{mode:'cursor'});
    if(!result.items.some(row=>row.id===messageId))throw Error('This saved reading position is no longer available. Return to latest and load earlier messages.');
    if(originalId.current===id&&paging.current===token&&isCurrent()){setFocused(null);setNativeView({id,messages:result.items,window:returnedWindow(result)});if(jump)setJumpTarget({id:messageId});return true}
    return false;
   }
   const result=await request('/api/conversation/navigation?'+new URLSearchParams({sessionId:id,messageId,window:'true'}),{signal:token.abort.signal});
   if(!Array.isArray(result?.messages)||!result.messages.some(row=>row.id===messageId))throw Error('This message could not be loaded. Try again.');
   if(originalId.current===id&&paging.current===token&&isCurrent()){setFocused({...result,sessionId:id});if(jump)setJumpTarget({id:messageId});return true}
   return false;
  }catch(error){if(!token.abort.signal.aborted&&originalId.current===id&&paging.current===token&&isCurrent())setError(error.message);return false}
  finally{finishRead(token);if(paging.current===token){paging.current=null;setBusy('')}}
 }
 function latest(jump=true){paging.current?.abort.abort();paging.current=null;setNativeView(null);setFrozen(null);setFocused(null);setSaved(null);setError('');setBusy('');setJumpTarget(jump?{id:'latest'}:null)}
 async function earlier(part){
  if(paging.current||!session)return;
  const id=session.id,token=beginRead(id);
  const key=current.current;
  setBusy('conversation');setError('');setFrozen(session);
  const parts=nativeHere?['messages']:part?[part]:['messages','nodes'];
  const windows=parts.map(part=>({part,window:part==='messages'?session.messageWindow:session.executionWindow})).filter(({window})=>hasEarlierWindow(window));
  try{
   // Commit the paired pages once, after both requests succeed. Current text
   // stays mounted while loading so activity cannot precede its messages.
   const results=await Promise.all(windows.map(async ({part,window})=>({part,window,result:await request('/api/conversation/detail?'+new URLSearchParams({sessionId:id,part:window.part||part,before:window.before,...(nativeHere?{native:'true'}:{})}),{signal:token.abort.signal})})));
   for(const {window,result} of results)validateHistoryPage(result,window);
   if(current.current!==key)return;
   if(paging.current!==token||originalId.current!==id)return;
   beforeApply?.();
   if(nativeHere){
    const result=results[0]?.result;
    if(result)setNativeView(old=>old?.id===id?{...old,messages:unique([...result.items,...old.messages]),window:returnedWindow(result)}:old);
    return;
   }
   setSaved(old=>{
    let previous=old?.id===id?old:{id,messages:[],nodes:[],turns:[]};
    for(const {part,window,result} of results)previous={...previous,[part]:unique([...(window.part==='groups'?[]:result.items),...previous[part],...(part==='messages'?session.messages:[])]),
     ...(part==='messages'?{messageOffset:result.offset,sourceOffset:result.sourceOffset,userOffset:result.userOffset,
      ...(window.mode==='cursor'?{messageWindow:returnedWindow(result)}:{})}:{nodeOffset:result.offset,turns:unique([...(result.turns||[]),...previous.turns]),segments:unique([...(window.part==='groups'?result.items:result.segments||[]),...(previous.segments||[])]),groupWindow:window.part==='groups'?returnedWindow(result):previous.groupWindow})};
    return previous;
   });
  }catch(e){if(!token.abort.signal.aborted&&originalId.current===id)setError(e.message)}finally{finishRead(token);if(paging.current===token){paging.current=null;setBusy('')}}
 }
 async function openNativeHistory(){
  if(paging.current||!originalSource)return;
  const id=originalSource.id,owner=current.current,token=beginRead(id);setBusy('conversation');setError('');
  try{
   const result=validateHistoryPage(await request('/api/conversation/detail?'+new URLSearchParams({sessionId:id,part:'messages',native:'true'}),{signal:token.abort.signal}),{mode:'cursor'});
   if(current.current===owner&&paging.current===token){beforeApply?.();setFocused(null);setNativeView({id,messages:result.items,window:returnedWindow(result)});setJumpTarget({id:'latest'})}
  }catch(e){if(!token.abort.signal.aborted&&current.current===owner)setError(e.message)}
  finally{finishRead(token);if(paging.current===token){paging.current=null;setBusy('')}}
 }
 const originalId=useRef(originalSource?.id);
 if(originalId.current!==originalSource?.id){originalId.current=originalSource?.id;paging.current=null}
 const hasEarlier=hasEarlierWindow(session?.messageWindow)||hasEarlierWindow(session?.executionWindow);
 const gaps=session?.messageWindow?.unavailableRanges?.length||session?.executionWindow?.unavailableRanges?.length;
 const controls=focusedHere?<><div className="a-history-window-controls"><button className="a-soft" disabled={!!busy||!focusedHere.before} onClick={()=>around(focusedHere.before)}>Earlier messages</button><button className="a-soft" disabled={!!busy||!focusedHere.after} onClick={()=>around(focusedHere.after)}>Later messages</button><button className="a-link" disabled={!!busy} onClick={()=>latest()}>Return to latest</button></div>{busy&&<p role="status">Loading conversation…</p>}{error&&<p role="alert">{error}</p>}</>:<>
  {(nativeHere||frozen||extra||error)&&<button className="a-link" type="button" disabled={!!busy} onClick={()=>latest()}>Return to latest</button>}
  {!nativeHere&&originalSource?.nativeContinuation?.status==='available'&&<button className="a-link" type="button" disabled={!!busy} onClick={openNativeHistory}>View saved runtime history</button>}
  {hasEarlier&&<button className="a-soft" type="button" disabled={!!busy} onClick={()=>earlier()}>{busy?'Loading earlier conversation…':'Load earlier conversation'}</button>}
  {!!gaps&&<p role="status">Some saved records could not be shown in this page. Earlier pages remain available.</p>}
  {busy&&!hasEarlier&&<p role="status">Loading conversation…</p>}{error&&<p role="alert">{error}</p>}
 </>;

 const readControls=<>{controls}{annotations.error&&<p role="status">Could not refresh message reactions. <button type="button" className="a-link" onClick={annotations.retry}>Retry reactions</button></p>}</>;
 return {session,controls:readControls,earlier,busy,around,latest,openNativeHistory,focused:!!focusedHere||!!nativeHere||!!frozen,jumpRequest:jumpTarget};
}
