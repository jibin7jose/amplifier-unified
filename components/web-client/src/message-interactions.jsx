import React,{useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {Reply,Smile,X} from 'lucide-react';
import {Markdown} from './markdown';
import {clientId,request} from './api';
import {createReactionController,reactionControllers} from './message-reactions';
import {selectedQuote} from './message-quote-selection.js';
import './message-interactions.css';

const reactions=[['👍','Thumbs up'],['❤️','Heart'],['😂','Laugh'],['🎉','Celebrate'],['😮','Surprised'],['😢','Sad'],['👎','Thumbs down'],['✅','Check']];

export function AssistantReactions({message}){
 const selected=reactions.filter(([emoji])=>message.assistantReactions?.includes(emoji));
 if(!selected.length)return null;
 return <div className="a-assistant-reactions" aria-label="Amplifier reactions">{selected.map(([emoji,label])=><span key={emoji} className="a-assistant-reaction" aria-label={`Amplifier reacted: ${label}`} title={`Amplifier reacted: ${label}`}><span aria-hidden="true">{emoji}</span><small>Amplifier</small></span>)}</div>;
}

const author=quote=>quote.speaker||(quote.role==='user'?'You':'Amplifier');
const sameRange=(a,b)=>a&&b&&a.startContainer===b.startContainer&&a.startOffset===b.startOffset&&a.endContainer===b.endContainer&&a.endOffset===b.endOffset;

export function QuotedDocument({document,sessionId,dispatch}){
 return <div className="a-quoted-document">{document.segments.map((part,index)=>part.type==='quote'?
  <section key={part.instanceId} data-sent-quote={part.instanceId} className="a-composer-quote"><QuoteCard quote={part.quote} sessionId={sessionId} dispatch={dispatch}/><pre className="a-composer-quote-text">{part.quote.excerpt}</pre><small>Historical reference · Full saved text</small></section>:
  <Markdown key={index} text={part.text} userContent/>)}</div>;
}

export function QuoteCard({quote,sessionId,dispatch}){
 const [error,setError]=useState('');
 if(!quote)return null;
 const here=(quote.navigationSessionId||quote.sessionId)===sessionId;
 return <blockquote className="a-reply-quote"><button type="button" className="a-quoted-source" data-action="message.reveal" title={here?'Go to original message':'Open the original message in its conversation'} onClick={async()=>{setError('');try{const target=quote.navigationSessionId||quote.sessionId;await dispatch('message.reveal',{sessionId:target,messageId:quote.messageId,...(quote.kind?{loadedOnly:true}:{})})}catch(e){setError(e.message)}}}><strong>{author(quote)}</strong>{!quote.kind&&<span>{quote.excerpt}{quote.truncated?'…':''}</span>}</button>{!here&&<small>Quoted from the original conversation</small>}{error&&<small role="status">{error}</small>}</blockquote>;
}

export function ReplyPreview({quote,sessionId,dispatch}){
 const [error,setError]=useState('');
 if(!quote||quote.sessionId!==sessionId)return null;
 return <div className="a-reply-preview" aria-label="Quoted reply"><div><strong>Replying to {author(quote)}</strong><p>{quote.excerpt}{quote.truncated?'…':''}</p></div><button type="button" className="a-icon" aria-label="Remove quoted reply" data-action="message.replyClear" onClick={async()=>{setError('');try{await dispatch('message.replyClear',{sessionId,expectedReplyId:quote.id})}catch(e){setError(e.message)}}}><X/></button>{error&&<small role="alert">{error}</small>}</div>;
}

export function MessageInteractions({message,sessionId,dispatch}){
 const [open,setOpen]=useState(false),[below,setBelow]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const owner=JSON.stringify([sessionId,message.id]),current=useRef(owner),controller=useRef(null),[,refresh]=useState(0);current.current=owner;
 const anchor=useRef(null),selectionTool=useRef(null),[selected,setSelected]=useState(null);
 const selectedRef=useRef(null),activeSelection=useRef(null),dismissedSelection=useRef(null);selectedRef.current=selected;
 useEffect(()=>{
  if(typeof document==='undefined')return;
  const update=event=>{
   let selection=window.getSelection();const article=anchor.current?.closest('[data-message-id]');
   if(selectionTool.current?.contains(document.activeElement)&&selectedRef.current){
    if(event?.type==='selectionchange')return;
    const range=selectedRef.current.range;selection={isCollapsed:range.collapsed,rangeCount:1,getRangeAt:()=>range};
   }
   if(!selection||selection.isCollapsed||selection.rangeCount!==1){activeSelection.current=null;dismissedSelection.current=null;setSelected(null);return;}
   const range=selection.getRangeAt(0);
   if(dismissedSelection.current&&!sameRange(dismissedSelection.current.range,range))dismissedSelection.current=null;
   const result=selectedQuote(article,selection);
   if(!result){activeSelection.current=null;setSelected(null);return;}
   const previous=activeSelection.current,same=previous&&previous.source===result.source&&sameRange(previous.range,range);
   const intent={...result,token:same?previous.token:{},range:range.cloneRange()};activeSelection.current=intent;
   // A completed/dismissed action must not be revived by layout scroll events
   // while the unchanged source text is still selected in the browser.
   if(dismissedSelection.current&&dismissedSelection.current.source===result.source){setSelected(null);return;}
   const rect=range.getBoundingClientRect(),pane=article.closest('.a-messages')?.getBoundingClientRect();
   if(pane&&(rect.bottom<pane.top||rect.top>pane.bottom)){setSelected(null);return;}
   setSelected({...intent,left:Math.max(8,Math.min(rect.left,window.innerWidth-224)),top:Math.max(8,Math.min(rect.bottom+8,window.innerHeight-(result.error?104:56)))});
  };
  const dismiss=event=>{if(event.key==='Escape'){if(selectedRef.current)dismissedSelection.current=selectedRef.current;setSelected(null);}};
  const reselect=event=>{if(anchor.current?.closest('[data-message-id]')?.querySelector('.a-detail-text')?.contains(event.target)){activeSelection.current=null;dismissedSelection.current=null;}};
  document.addEventListener('selectionchange',update);document.addEventListener('keydown',dismiss);document.addEventListener('pointerdown',reselect,true);window.addEventListener('scroll',update,true);window.addEventListener('resize',update);
  return()=>{document.removeEventListener('selectionchange',update);document.removeEventListener('keydown',dismiss);document.removeEventListener('pointerdown',reselect,true);window.removeEventListener('scroll',update,true);window.removeEventListener('resize',update);};
 },[owner,message.text]);
 if(controller.current?.owner!==owner){
  const key='amplifier.reactions.v1:'+owner;
  let restored=[];try{restored=JSON.parse(sessionStorage.getItem(key)||'[]').filter(row=>typeof row.id==='string'&&typeof row.clientId==='string'&&reactions.some(([emoji])=>emoji===row.emoji)&&typeof row.present==='boolean'&&typeof row.desired==='boolean').slice(0,8)}catch{}
  controller.current={owner,value:reactionControllers.acquire(owner,()=>createReactionController({sessionId,messageId:message.id,clientId,restored,
   save:rows=>{try{if(rows.length)sessionStorage.setItem(key,JSON.stringify(rows));else sessionStorage.removeItem(key);return true}catch{return false}},
   send:row=>dispatch('message.reaction',{sessionId,messageId:message.id,emoji:row.emoji,present:row.present},{id:row.id,feedback:false,presentation:true}),
   read:row=>request('/api/conversation/reaction-status',{method:'POST',body:{sessionId,messageId:message.id,commandId:row.id,clientId:row.clientId,emoji:row.emoji,present:row.present}})
  }))};
 }
 const reaction=controller.current.value;reaction.observe(message);
 useEffect(()=>{current.current=owner;return()=>{if(current.current===owner)current.current=null}},[owner]);
 useEffect(()=>{
  const unsubscribe=reaction.subscribe(()=>refresh(value=>value+1));
  if(reaction.view().unknown)void reaction.reconcile();
  const online=()=>{if(reaction.view().unknown)void reaction.reconcile()};
  globalThis.addEventListener?.('online',online);return()=>{unsubscribe();globalThis.removeEventListener?.('online',online)};
 },[reaction]);
 const state=reaction.view(),chosen=state.reactions;
 async function action(name,args){
  if(busy)return;setBusy(true);setError('');
  try{await dispatch(name,{sessionId,messageId:message.id,...args},name==='message.reply'?{quoteSource:message.text,quoteDetail:message.textDetail}:{});}
  catch(e){if(current.current===owner)setError(e.message)}finally{if(current.current===owner)setBusy(false)}
 }
 const addPassage=(source,selection)=>{if(current.current!==owner)return Promise.reject(Error('Conversation changed; select the passage again.'));return dispatch('message.reply',{sessionId,messageId:message.id,selection},{quoteSource:source});};
 const quoteSelected=async()=>{
  const captured=selected;
  try{
   if(captured.error)throw Error(captured.error+' Nothing was added.');
   const range=captured.range,article=anchor.current?.closest('[data-message-id]');
   const fresh=selectedQuote(article,{isCollapsed:range.collapsed,rangeCount:1,getRangeAt:()=>range});
   if(!fresh||fresh.error||fresh.source!==captured.source||JSON.stringify(fresh.spans)!==JSON.stringify(captured.spans))throw Error('The source or selection changed. Select the passage again.');
   await addPassage(fresh.source,{spans:fresh.spans});
   // Visibility is not selection ownership: the toolbar may be offscreen while
   // this intent is still current. A new gesture selecting identical text is B,
   // not permission for A's completion to dismiss it.
   if(activeSelection.current?.token===captured.token)dismissedSelection.current=captured;
   setSelected(current=>current?.token===captured.token?null:current);
  }catch(error){setError(error.message);}
 };
 return <><button ref={anchor} type="button" className="a-icon" aria-label="Reply to message" title="Quote whole message" data-action="message.reply" disabled={busy} onMouseDown={event=>{if(event.button===0)event.preventDefault();}} onClick={()=>action('message.reply',{})}><Reply/></button>
 {selected&&createPortal(<div ref={selectionTool} className="a-quote-selection" style={{left:selected.left,top:selected.top}} onMouseDown={event=>event.preventDefault()}>
  {selected.error?<span className="a-quote-selection-error" role="status">{selected.error} Nothing was added.</span>:
   <button type="button" disabled={busy} title="Add this exact passage without leaving your place" onClick={quoteSelected}>Quote selection</button>}
 </div>,anchor.current?.closest('#amp-one')||document.body)}
 <div className="a-reaction-control" onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setOpen(false)}} onKeyDown={e=>{if(e.key==='Escape'){setOpen(false);e.currentTarget.querySelector('button')?.focus()}}}>
  <button type="button" className="a-icon" aria-label="React to message" title="React to message" aria-expanded={open} onClick={e=>{const pane=e.currentTarget.closest('.a-messages')?.getBoundingClientRect();setBelow(e.currentTarget.getBoundingClientRect().top-(pane?.top||0)<110);setOpen(!open)}}><Smile/></button>
  {open&&<div className="a-reaction-picker" data-below={below} role="group" aria-label="Choose reaction">{reactions.map(([emoji,label])=><button key={emoji} type="button" aria-label={label} aria-pressed={chosen.includes(emoji)} data-action="message.reaction" onClick={e=>{reaction.set(emoji,!chosen.includes(emoji));setOpen(false);e.currentTarget.closest('.a-reaction-control')?.querySelector('button')?.focus({preventScroll:true})}}>{emoji}</button>)}</div>}
 </div>{chosen.map(emoji=><button key={emoji} type="button" className="a-reaction-chip" aria-label={`Remove ${reactions.find(r=>r[0]===emoji)?.[1]||emoji} reaction`} aria-pressed="true" title={state.pending.includes(emoji)?'Reaction not yet confirmed':'Remove reaction'} data-pending={state.pending.includes(emoji)||undefined} data-action="message.reaction" onClick={()=>reaction.set(emoji,false)}>{emoji}</button>)}<span className="a-sr-only" role="status">{state.unknown?'Reaction not confirmed':state.pending.length?'Saving reaction':''}</span>{(error||state.error||state.unknown)&&<span className="a-message-interaction-error" role="alert">{error||state.error||'Reaction not confirmed.'}{state.unknown&&<button type="button" disabled={state.checking} onClick={()=>reaction.reconcile()}>Check saved reaction</button>}</span>}</>;
}

export function MessageFocus({session,focus,detail,ensureVisible}){
 const attempted=useRef(''),completed=useRef('');
 useEffect(()=>{
  if(!focus||focus.sessionId!==session?.id||detail.busy)return;
  const request=`${focus.sessionId}:${focus.revision}`;
  if(completed.current===request)return;
  const key=`${request}:${session.messageWindow?.offset??0}`;
  const node=[...document.querySelectorAll('[data-message-id]')].find(row=>row.dataset.messageId===focus.messageId);
  if(node){completed.current=request;node.tabIndex=-1;node.scrollIntoView({block:'center'});node.focus({preventScroll:true});return;}
  let active=true;
  Promise.resolve(ensureVisible?.(focus.messageId)).then(opened=>{
   if(!active||opened||attempted.current===key)return;
   attempted.current=key;
   if(detail.around)detail.around(focus.messageId);
   else if(session.messageWindow?.offset>0)detail.earlier('messages');
  }).catch(()=>{});
  return()=>{active=false};
 },[session?.id,session?.messages?.length,session?.messageWindow?.offset,focus?.revision,focus?.sessionId,detail.busy,ensureVisible]);
 return null;
}
