import React,{useEffect,useId,useRef,useState} from 'react';
import {SlidersHorizontal,Activity} from 'lucide-react';

// Same mounted controls at every width; mobile changes only their presentation.
export function SlotOverflow({name,children}){
 const [open,setOpen]=useState(false),root=useRef(null),button=useRef(null),id=useId();
 const label=name==='app.status'?'Additional status':'Additional controls',Icon=name==='app.status'?Activity:SlidersHorizontal;
 useEffect(()=>{
  if(!open)return;
  const outside=event=>{if(!root.current?.contains(event.target))setOpen(false)};
  document.addEventListener('pointerdown',outside);
  return()=>document.removeEventListener('pointerdown',outside);
 },[open]);
 useEffect(()=>{
  const query=matchMedia('(max-width:760px)');
  const changed=()=>{if(query.matches&&root.current?.contains(document.activeElement)&&document.activeElement!==button.current)setOpen(true)};
  query.addEventListener('change',changed);return()=>query.removeEventListener('change',changed);
 },[]);
 return <div className="a-slot-overflow" data-open={open} ref={root} onKeyDown={event=>{if(event.key==='Escape'&&open){event.stopPropagation();event.preventDefault();setOpen(false);button.current?.focus()}}}>
  <button ref={button} type="button" className="a-icon a-slot-overflow-toggle" aria-label={label} title={label} aria-expanded={open} aria-controls={id} onClick={()=>setOpen(value=>!value)}><Icon/></button>
  <div className="a-slot-overflow-items" id={id} role="group" aria-label={label}>{children}</div>
 </div>;
}
