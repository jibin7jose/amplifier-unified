import React,{useEffect,useId,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,History,Home,MessageCircle,Folder,Search,Check} from 'lucide-react';
import {destinationKey,sameDestination} from './destination-history.js';

// The icon lists places; holding either arrow keeps chronological step identity.
// Pointer hold remains a mouse/pen shortcut, not a long-touch requirement.
export function DestinationHistory({history,onVisit,onRecent,disabled=false}){
 const [open,setOpen]=useState(null),root=useRef(null),opener=useRef(null),focusReturn=useRef(null),hold=useRef(null),suppressClick=useRef(false),menuId=useId();
 const busyCycle=useRef(0),pointerType=useRef(null);
 const entries=history?.entries||[],recent=history?.recent||[],busy=disabled||!history?.ready||history?.pending;
 const rows=open==='back'?entries.slice(0,history.detached?history.cursor+1:history.cursor).reverse():open==='forward'?entries.slice(history.cursor+1):recent;
 const menuLabel=open==='back'?'Back steps':open==='forward'?'Forward steps':'Recent places';
 const cancelHold=()=>{if(hold.current)clearTimeout(hold.current.timer);hold.current=null};
 const close=(restore=false)=>{cancelHold();setOpen(null);if(restore)opener.current?.focus()};
 const show=(button,kind='recent')=>{cancelHold();opener.current=button;setOpen(kind)};
 useEffect(()=>()=>cancelHold(),[]);
 useEffect(()=>{
  if(busy){
   busyCycle.current++;
   if(root.current?.contains(document.activeElement))focusReturn.current=opener.current||document.activeElement;
   close();
  }else if(focusReturn.current){
   // Never steal focus if the person moved elsewhere while the request ran.
   if(document.activeElement===document.body){
    const target=focusReturn.current;
    if(target?.isConnected&&!target.disabled)target.focus();
    else root.current?.querySelector('[aria-label="Recent places"]')?.focus();
   }
   focusReturn.current=null;
  }
 },[busy]);
 useEffect(()=>{
  if(!open)return;
  const target=root.current?.querySelector('[role="menuitem"]:not(:disabled)')||root.current?.querySelector('[role="menu"]');
  target?.focus();
  const dismiss=event=>{if(!root.current?.contains(event.target))close()};
  document.addEventListener('pointerdown',dismiss);
  return()=>document.removeEventListener('pointerdown',dismiss);
 },[open]);
 const visit=(key,place=false)=>{
  if(!key||busy)return;
  const cycle=busyCycle.current,target=opener.current;
  focusReturn.current=target;close(true);
  const settled=()=>{if(busyCycle.current===cycle&&focusReturn.current===target)focusReturn.current=null};
  Promise.resolve(place?onRecent(key):onVisit(key)).then(settled,settled);
 };
 const keyDown=event=>{
  if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey)pointerType.current=null;
  if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();close(true);return}
  if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
  if(!open){if(event.key==='ArrowDown'&&!busy){
   const button=event.target.closest('button'),kind=button?.dataset.historyDirection||'recent';
   if(button&&!button.disabled){event.preventDefault();show(button,kind)}
  }return}
  const items=[...root.current.querySelectorAll('[role="menuitem"]:not(:disabled)')];if(!items.length)return;
  event.preventDefault();const index=items.indexOf(document.activeElement);
  const next=event.key==='Home'?0:event.key==='End'?items.length-1:event.key==='ArrowDown'?(index+1)%items.length:(index-1+items.length)%items.length;
  items[next].focus();
 };
 const startHold=(event,direction)=>{
  pointerType.current=event.pointerType;
  suppressClick.current=false;cancelHold();
  if(event.pointerType==='touch'||event.button!==0||busy)return;
  const button=event.currentTarget;
  hold.current={x:event.clientX,y:event.clientY,timer:setTimeout(()=>{suppressClick.current=true;show(button,direction)},450)};
 };
 const arrow=(direction,key,Icon)=><button type="button" className="a-icon" aria-label={direction+' in navigation history'} data-history-direction={direction.toLowerCase()} aria-haspopup="menu" aria-expanded={open===direction.toLowerCase()} aria-controls={open===direction.toLowerCase()?menuId:undefined} disabled={busy||!key} onPointerDown={event=>startHold(event,direction.toLowerCase())} onPointerUp={cancelHold} onPointerCancel={()=>{cancelHold();suppressClick.current=false}} onPointerLeave={cancelHold} onPointerMove={event=>{if(hold.current&&Math.hypot(event.clientX-hold.current.x,event.clientY-hold.current.y)>8)cancelHold()}} onContextMenu={event=>{if(busy||!key||event.nativeEvent?.pointerType==='touch'||pointerType.current==='touch')return;event.preventDefault();show(event.currentTarget,direction.toLowerCase())}} onClick={event=>{if(suppressClick.current){suppressClick.current=false;event.preventDefault();return}opener.current=event.currentTarget;visit(key)}}><Icon/></button>;
 const duplicates=label=>rows.filter(row=>row.label===label).length>1;
 return <div className="a-destination-history" ref={root} role="group" aria-label="Navigate visited chats and workspaces" onKeyDown={keyDown} onBlur={event=>{if(event.relatedTarget&&!event.currentTarget.contains(event.relatedTarget))close()}}>
  {arrow('Back',history?.backKey,ArrowLeft)}{arrow('Forward',history?.forwardKey,ArrowRight)}
  <button type="button" className="a-icon" aria-label="Recent places" title="Recent places" aria-haspopup="menu" aria-expanded={open==='recent'} aria-controls={open==='recent'?menuId:undefined} disabled={busy||!recent.length} onClick={event=>open==='recent'?close(true):show(event.currentTarget)}><History/></button>
  {open&&<div className="a-destination-menu" data-private="true">
   <p>{menuLabel}</p>
   {history.memoryOnly&&<p role="status">History is kept only while this page stays open.</p>}
   <div id={menuId} role="menu" aria-label={menuLabel} tabIndex={-1} data-private="true">
   {rows.map(row=>{
    const current=open==='recent'&&!history.detached&&sameDestination(row,history.current)&&(row.tab||null)===(history.current?.tab||null),Icon=row.kind==='home'?Home:row.kind==='chat'?MessageCircle:row.kind==='chats'?Search:Folder;
    const detail=[row.kind==='chat'?row.workspaceLabel:row.kind==='workspace'?row.tab:'',duplicates(row.label)&&row.id?row.id:''].filter(Boolean).join(' · ');
    const place=open==='recent',identity=place?destinationKey(row):row.key;
    return <button type="button" role="menuitem" key={identity} data-visit-key={place?undefined:row.key} data-place-key={place?identity:undefined} data-view-label="Private navigation destination" aria-current={current?'page':undefined} disabled={current} onClick={()=>visit(identity,place)}><Icon/><span><strong>{row.label}</strong>{detail&&<small>{detail}</small>}</span>{current&&<Check/>}</button>;
   })}
   </div>
  </div>}
 </div>;
}
