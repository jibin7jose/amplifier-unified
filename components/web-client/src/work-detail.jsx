import {useEffect,useRef,useState} from 'react';
import {request} from './api';

// Closed/off-screen groups do no detail IO. Bound reads across open groups.
let active=0;
const waiting=[];
function drain(){
 while(active<4&&waiting.length){const job=waiting.shift();if(job.signal.aborted){job.reject(new DOMException('Aborted','AbortError'));continue}active++;
  request(job.path,{...job.options,signal:job.signal}).then(job.resolve,job.reject).finally(()=>{active--;drain()});
 }
}
function read(path,signal,options={}){return new Promise((resolve,reject)=>{waiting.push({path,signal,options,resolve,reject});drain()})}

export function useWorkDetail(data,turn,open,automatic=false){
 const element=useRef(null),run=useRef(null),[visible,setVisible]=useState(false),[saved,setSaved]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const key=JSON.stringify([data.sessionId,turn.id,turn.windowMessageId]),current=useRef(key);current.current=key;
 const enabled=!!data.detailsDeferred&&open&&(visible||!automatic)&&turn.detailRevision!=null;
 const page=saved?.key===key?saved:null;
 const latest=useRef(turn.detailRevision);latest.current=turn.detailRevision;
 useEffect(()=>{
  if(!open||!automatic){setVisible(false);return}
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(entries=>setVisible(entries.some(entry=>entry.isIntersecting)),{rootMargin:'400px'});
  if(element.current)observer.observe(element.current);return()=>observer.disconnect();
 },[open,automatic]);
 useEffect(()=>{
  const state={abort:new AbortController(),page:page||null,busy:false,wanted:latest.current,handled:null,timer:null};run.current=state;
  if(!open){state.page=null;setSaved(null)}
  setError('');setBusy(false);
  const live=()=>!state.abort.signal.aborted&&current.current===key;
  state.schedule=()=>{if(!enabled||state.busy||state.timer||!live())return;state.timer=setTimeout(()=>{state.timer=null;state.load()},100)};
  state.load=async(before=null)=>{
   if(!live()||!enabled||state.busy)return;
   state.busy=true;setBusy(true);setError('');const target=state.wanted;
   try{
    const previous=state.page;
    const windowArgs=turn.windowMessageId?{windowMessageId:turn.windowMessageId}:{};
    const args={sessionId:data.sessionId,part:'nodes',group:turn.id,...windowArgs};if(before)args.before=before;
    const sync=previous&&!before;
    const result=sync?await read('/api/conversation/work-sync',state.abort.signal,{method:'POST',body:{sessionId:data.sessionId,group:turn.id,...windowArgs,known:Object.fromEntries(previous.items.map(row=>[row.id,row.detailVersion]))}}):await read('/api/conversation/detail?'+new URLSearchParams(args),state.abort.signal);
    if(!live())return;
    const byId=new Map((previous?.items||[]).map(row=>[row.id,row]));for(const row of result.items)byId.set(row.id,row);
    const items=sync?result.order.map(id=>byId.get(id)):before?[...result.items,...previous.items.filter(row=>!result.items.some(older=>older.id===row.id))]:result.items;
    state.page={key,...result,items};setSaved(state.page);
    if(!before)state.handled=target;
   }catch(e){if(live()){setError(e.message);state.handled=state.wanted}}
   finally{state.busy=false;if(live()){setBusy(false);if(state.handled!==state.wanted)state.schedule()}}
  };
  if(enabled)state.schedule();
  return()=>{clearTimeout(state.timer);state.abort.abort()};
 },[enabled,key,open]);
 useEffect(()=>{const state=run.current;if(state){state.wanted=turn.detailRevision;if(state.handled!==state.wanted)state.schedule()}},[turn.detailRevision]);
 const nodes=data.detailsDeferred?(page?.items||[]).map(node=>({...node,turnId:turn.id})):data.nodes;
 return {element,nodes,busy:enabled&&(!page&&!error||busy),initialLoading:enabled&&!page&&!error,error,before:page?.before,
  reload:()=>run.current?.schedule(),earlier:()=>{if(page?.before)run.current?.load(page.before)}};
}
