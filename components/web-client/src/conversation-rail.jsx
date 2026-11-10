import React,{useLayoutEffect,useMemo,useRef,useState} from 'react';
import {Bookmark} from 'lucide-react';
import './conversation-rail.css';
import {observeVisibleTurns} from './conversation-rail-viewport';
const key='amplifier.message-bookmarks.v1';
function savedBookmarks(){try{const rows=JSON.parse(localStorage.getItem(key)||'{}');return rows&&typeof rows==='object'&&!Array.isArray(rows)?Object.fromEntries(Object.entries(rows).filter(([,ids])=>Array.isArray(ids)&&ids.every(id=>typeof id==='string'))):{}}catch{return {}}}
export function ConversationRail({sessionId,messages,onJump,paneRef}){
 const [hover,setHover]=useState(null),[bookmarks,setBookmarks]=useState(savedBookmarks);
 // Navigation describes the requested reading window. Opening or hovering it
 // must not index the rest of the transcript or fetch text already on screen.
 const turns=useMemo(()=>messages.filter(message=>message.role==='user'&&!message.observation).slice(-100).map(message=>{
  const index=messages.indexOf(message),end=messages.findIndex((row,i)=>i>index&&row.role==='user'&&!row.observation);
  const next=messages.slice(index+1,end<0?undefined:end).find(row=>row.role==='assistant'&&!row.observation);
  return {id:message.id,text:message.text||'Attached message',reply:next?.text||''};
 }),[messages]);
 const railRef=useRef(null),previewRef=useRef(null),[visible,setVisible]=useState([]),[previewTop,setPreviewTop]=useState(0);
 const turnIds=turns.map(turn=>turn.id).join('\n');
 useLayoutEffect(()=>{setVisible([]);const pane=paneRef?.current;if(pane)return observeVisibleTurns(pane,turns.map(turn=>turn.id),setVisible)},[sessionId,turnIds,paneRef]);
 const active=hover?.sessionId===sessionId?hover.index:null,row=turns[active],marked=bookmarks[sessionId]||[];
 useLayoutEffect(()=>{
  const rail=railRef.current,preview=previewRef.current,mark=rail?.querySelectorAll('.a-rail-mark')[active];
  if(!row||!rail||!preview||!mark)return;
  const place=()=>{
   const bounds=rail.getBoundingClientRect(),anchor=mark.getBoundingClientRect(),container=rail.parentElement.getBoundingClientRect();
   const lower=Math.max(8,container.top+8),upper=Math.min(window.innerHeight-8,container.bottom-8)-preview.offsetHeight;
   const top=Math.max(lower,Math.min(upper,anchor.top+anchor.height/2-preview.offsetHeight/2));
   setPreviewTop(top-bounds.top);
  };
  place();const resize=new ResizeObserver(place);[rail,preview,rail.parentElement].forEach(node=>resize.observe(node));
  window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  return()=>{resize.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true)};
 },[active,sessionId,turnIds,row?.text,row?.reply]);
 if(!turns.length)return null;
 function toggle(id){
  const ids=marked.includes(id)?marked.filter(value=>value!==id):[...marked,id].slice(-100);
  const next={...bookmarks,[sessionId]:ids};const entries=Object.entries(next).slice(-100);
  setBookmarks(Object.fromEntries(entries));try{localStorage.setItem(key,JSON.stringify(Object.fromEntries(entries)))}catch{}
 }
 return <nav ref={railRef} className="a-conversation-rail" aria-label="Chat navigator" onMouseLeave={()=>setHover(null)} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setHover(null)}} onKeyDown={event=>{if(event.key==='Escape')setHover(null)}}>
  <div className="a-rail-marks" data-tooltip="off" onScroll={event=>{event.currentTarget.dataset.overflowAbove=String(event.currentTarget.scrollTop>3)}}>{turns.map((turn,index)=><button type="button" key={turn.id} className="a-rail-mark" aria-label={`Jump to message ${index+1}: ${(turn.text||'').slice(0,60)}`} tabIndex={index===(active??0)?0:-1} onKeyDown={event=>{const next=event.key==='ArrowDown'?Math.min(turns.length-1,index+1):event.key==='ArrowUp'?Math.max(0,index-1):event.key==='Home'?0:event.key==='End'?turns.length-1:null;if(next!==null){event.preventDefault();event.currentTarget.parentElement.children[next]?.focus()}}} data-visible={visible.includes(turn.id)} data-turn-id={turn.id} data-bookmarked={marked.includes(turn.id)} style={{'--rail-scale':active===null?1:1+Math.max(0,4-Math.abs(index-active))*.8}} onMouseEnter={()=>setHover({sessionId,index})} onFocus={()=>setHover({sessionId,index})} onClick={()=>onJump(turn.id)}><span/></button>)}</div>
  {row&&<div ref={previewRef} className="a-rail-preview" style={{top:previewTop}}>
   <div><button type="button" className="a-rail-preview-jump" onClick={()=>onJump(row.id)}>{row.text?.slice(0,180)||'Loading message preview…'}</button><button type="button" className="a-icon" aria-label={marked.includes(row.id)?'Remove message bookmark':'Bookmark message'} aria-pressed={marked.includes(row.id)} title="Saved in this browser" onClick={()=>toggle(row.id)}><Bookmark fill={marked.includes(row.id)?'currentColor':'none'}/></button></div>
   {row.reply&&<p>{row.reply.slice(0,280)}</p>}
  </div>}
 </nav>;
}
