import React,{createContext,useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ChevronRight} from 'lucide-react';

// Reuse the Canvas draft shape, but keep new disclosure/search choices in the
// originating tab, like Home's layout. Navigation still uses the host's actions.
export const overviewSectionOpen=(draft,id,fallback=true)=>typeof draft?.sections?.[id]==='boolean'?draft.sections[id]:fallback;
export const overviewSectionPatch=(draft,id,open)=>({...draft,sections:{...draft?.sections,[id]:!!open}});
const localKey='amplifier.canvas-overview.v1';
const SectionStatus=createContext(null);
export function useCanvasSectionStatus(status){
 const report=useContext(SectionStatus);
 useEffect(()=>{report?.(status||'');return()=>report?.('')},[report,status]);
}
export function useCanvasOverviewState(state){
 const [records,setRecords]=useState(()=>{try{const value=JSON.parse(sessionStorage.getItem(localKey)||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{}}catch{return {}}});
 const key=JSON.stringify([state.selectedWorkspaceId,state.selectedSessionId]),draft=state.view?.canvasDraft||{};
 const update=value=>setRecords(previous=>{
  const next=Object.fromEntries([...Object.entries(previous).filter(([id])=>id!==key).slice(-99),[key,{...previous[key],...value}]]);
  try{sessionStorage.setItem(localKey,JSON.stringify(next))}catch{}
  return next;
 });
 return [{...draft,...records[key]},update];
}
export function CanvasOverviewSection({id,title,summary,icon:Icon,draft,onChange,defaultOpen=true,lazy=false,children}){
 const open=overviewSectionOpen(draft,id,defaultOpen),[visited,setVisited]=useState(open),body=useRef(null),heading=useRef(null);
 const [status,setStatus]=useState('');
 return <details className="a-overview-section" data-overview-section={id} open={open} onToggle={event=>{
  if(event.target!==event.currentTarget)return;
  const next=event.currentTarget.open;
  if(next)setVisited(true);
  else if(body.current?.contains(document.activeElement))heading.current?.focus({preventScroll:true});
  if(next!==open)onChange(overviewSectionPatch(draft,id,next));
 }}><summary ref={heading}>{Icon&&<Icon aria-hidden="true"/>}<h3>{title}</h3>{(status||summary!=null)&&<span className="a-overview-summary" role={status?'status':undefined}>{status||summary}</span>}<ChevronRight className="a-overview-chevron" aria-hidden="true"/></summary><div ref={body} className="a-overview-section-body"><SectionStatus.Provider value={setStatus}>{(!lazy||open||visited)&&children}</SectionStatus.Provider></div></details>;
}

// Reading offsets are client-local presentation, like landing-page reading position.
// Keep them in this tab's existing session-storage scope, never send scroll events.
export function useCanvasReading(ref,sessionId,name,visible){
 const saved=useRef(new Map());
 useLayoutEffect(()=>{
  const node=ref.current;if(!node||!sessionId)return;
  const key='amplifier.canvas-reading.v1:'+sessionId+':'+name;
  let offset=saved.current.get(key);
  if(offset===undefined)try{offset=JSON.parse(sessionStorage.getItem(key)||'null')}catch{}
  if(visible)node[name==='tabs'?'scrollLeft':'scrollTop']=Number.isFinite(offset)&&offset>=0?offset:0;
  let timer;
  const persist=()=>{try{sessionStorage.setItem(key,JSON.stringify(saved.current.get(key)))}catch{}};
  const remember=()=>{
   if(!visible||!node.getClientRects().length)return;
   const value=node[name==='tabs'?'scrollLeft':'scrollTop'];
   saved.current.set(key,value);
   if(saved.current.size>100)saved.current.delete(saved.current.keys().next().value);
   clearTimeout(timer);timer=setTimeout(persist,200);
  };
  node.addEventListener('scroll',remember,{passive:true});
  window.addEventListener('pagehide',persist);
  return()=>{clearTimeout(timer);if(saved.current.has(key))persist();node.removeEventListener('scroll',remember);window.removeEventListener('pagehide',persist)};
 },[ref,sessionId,name,visible]);
}
