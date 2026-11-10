import React,{useEffect,useRef,useState} from 'react';
import {hasNavigationResource} from './navigation-demand';

// Read retries use the current resource demand. They never replay a user command
// or establish a polling loop, and obsolete completions cannot update this view.
export function NavigationResourceStatus({state,resource,label,refresh}){
 const pending=state.navigationResourcePending?.[resource];
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const query=Object.fromEntries(Object.entries(state.view||{}).filter(([key])=>key.startsWith('nav')));
 const key=JSON.stringify([state.navigationResourceScope,resource,state.selectedSessionId,state.selectedWorkspaceId,query,pending?.status]);
 const current=useRef(null),mounted=useRef(true),flight=useRef(null);
 current.current=key;
 useEffect(()=>{setError('');setBusy(false);flight.current=null},[key]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 if(!pending)return null;
 const retained=hasNavigationResource(state,resource)||!!state.navigationResourceRetained?.[resource];
 // A routine refresh must not insert a row or announce itself over useful work.
 if(retained&&pending.status!=='retry')return null;
 const retry=async()=>{
  if(flight.current===key)return;
  const requested=key;flight.current=requested;setBusy(true);setError('');
  try{await refresh()}
  catch(failure){if(mounted.current&&current.current===requested)setError(failure.message||'Could not refresh. Please try again.')}
  finally{if(mounted.current&&current.current===requested){flight.current=null;setBusy(false)}}
 };
 return <p className="a-caption" role={pending.status==='retry'?'alert':'status'}>
  {pending.status==='retry'?`Could not update ${label}.`:`Loading ${label}…`}
  {pending.status==='retry'&&<> <button type="button" className="a-link" disabled={busy} onClick={retry} aria-label={`Retry ${label}`}>{busy?'Retrying…':'Retry'}</button></>}
  {error&&<> {error}</>}
 </p>;
}
