import React,{useCallback,useEffect,useRef,useState} from 'react';
import {request} from './api';

// A picker owns one bounded page and one explicit choice. Browsing another page
// never changes the resource command's owner, and closing stops read interest.
export function useWorkspaceChats(workspace,active,preferredId){
 const scope=JSON.stringify([workspace.id,workspace.path]),current=useRef(0),pending=useRef(null);
 const [page,setPage]=useState(null),[choice,setChoice]=useState(null),[status,setStatus]=useState({});
 const preferred=useRef(preferredId);preferred.current=preferredId;
 const load=useCallback(async(offset=0)=>{
  pending.current?.abort();const controller=new AbortController(),visit=++current.current;pending.current=controller;
  setStatus({scope,loading:true,error:''});
  try{
   const query=new URLSearchParams({workspace:workspace.path,kind:'root',offset:String(offset),limit:'50'});
   const value=await request('/api/sessions?'+query,{signal:controller.signal});
   if(controller.signal.aborted||visit!==current.current)return;
   setPage({scope,offset,items:value.items,nextOffset:value.nextOffset});
   setChoice(prior=>prior?.scope===scope&&prior.row?prior:{scope,row:value.items.find(row=>row.id===preferred.current)||value.items[0]||null});
   setStatus({scope,loading:false,error:''});
  }catch(error){if(!controller.signal.aborted&&visit===current.current)setStatus({scope,loading:false,error:error.message})}
 },[scope,workspace.path]);
 useEffect(()=>{
  if(active)load();
  return()=>{current.current++;pending.current?.abort()};
 },[active,load]);
 const visible=page?.scope===scope?page:null,selected=choice?.scope===scope?choice.row:null;
 const items=visible?.items||[],options=selected&&!items.some(row=>row.id===selected.id)?[selected,...items]:items;
 return {options,selectedId:selected?.id,selected,offset:visible?.offset||0,nextOffset:visible?.nextOffset,
  loading:status.scope===scope?status.loading:active,error:status.scope===scope?status.error:'',ready:!!visible,
  select:id=>{const row=options.find(item=>item.id===id);if(row)setChoice({scope,row})},
  first:()=>load(0),more:()=>{if(visible?.nextOffset!=null)load(visible.nextOffset)}};
}

export function WorkspaceChatChoices({choices,id,label,disabled=false}){
 return <>
  <label htmlFor={id}>{label}</label><select id={id} disabled={disabled||choices.loading||!choices.selectedId} value={choices.selectedId||''} onChange={event=>choices.select(event.target.value)}>
   {!choices.selectedId&&<option value="">Choose a chat</option>}
   {choices.options.map(row=><option key={row.id} value={row.id}>{row.title||'Untitled chat'}</option>)}
  </select>
  {choices.loading&&<p role="status">Loading workspace chats…</p>}
  {choices.error&&<p role="alert">{choices.error}</p>}
  {choices.ready&&!choices.options.length&&!choices.loading&&!choices.error&&<p>No chats in this workspace.</p>}
  <div className="a-dialog-actions">
   <button className="a-soft" type="button" disabled={disabled||choices.loading} onClick={choices.first}>{choices.offset?'First chats':'Refresh chats'}</button>
   {choices.nextOffset!=null&&<button className="a-soft" type="button" disabled={disabled||choices.loading} onClick={choices.more}>More chats</button>}
  </div>
 </>;
}
