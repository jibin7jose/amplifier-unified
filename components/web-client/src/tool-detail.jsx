import React,{useEffect,useState} from 'react';
import {request} from './api';

// One bounded saved-log page per disclosure or explicit "look further" click.
// Inspection never starts work, and navigation/closing cancels pending reads.
export function useToolDetail(source,open){
 const fields=source.exchange||source,reference=source.toolDetail||fields.toolDetail;
 const availability=source.contentAvailability||fields.contentAvailability;
 const key=JSON.stringify([source.id,fields.toolObservationId,source.toolObservationId,reference,availability?.status,
  fields.inputDetail?.digest,fields.outputDetail?.digest,fields.errorDetail?.digest]);
 const [result,setResult]=useState(null),[failure,setFailure]=useState(null),[next,setNext]=useState(null),[attempt,setAttempt]=useState(0);
 const current=result?.key===key?result:null,error=failure?.key===key?failure.message:'';
 const cursor=next?.key===key?next:null;
 const needs=reference&&['not-loaded','partial','unverified'].includes(availability?.status);
 useEffect(()=>{
  if(!open||!needs||current&&!cursor)return;
  const controller=new AbortController();setFailure(null);
  const query={...reference,...(cursor?{lookupKey:cursor.lookupKey,before:cursor.before}:{})};
  request('/api/conversation/detail?'+new URLSearchParams(query),{signal:controller.signal})
   .then(value=>{if(!controller.signal.aborted){setResult({key,value});setNext(null)}})
   .catch(error=>{if(!controller.signal.aborted){setFailure({key,message:error.message});setNext(null)}});
  return()=>controller.abort();
 },[open,key,!!needs,attempt,cursor]);
 const item=current?.value.item;
 const payload=item?Object.fromEntries(['input','output','error','inputDetail','outputDetail','errorDetail',
  'delegateRequest','delegateResponse','delegateRequestDetail','delegateResponseDetail','exchange',
  'inputSummary','inputPreview','actionPath','outcomeSummary','contentAvailability','savedObservations','toolDetail'].map(key=>[key,item[key]])):null;
 // A detail read cannot replace newer lifecycle, timing or usage updates.
 const node=item?(source.kind==='worker'?{...source,exchange:item.exchange||item,
  contentAvailability:item.contentAvailability,savedObservations:item.savedObservations}:{...source,...payload}):source;
 return {node,error,loading:!!(open&&needs&&(!current||cursor)&&!error),before:current?.value.before,
  retry:()=>{setResult(null);setAttempt(n=>n+1)},
  earlier:()=>{if(current?.value.before)setNext({key,lookupKey:current.value.lookupKey,before:current.value.before})}};
}

export function ToolDetailStatus({detail}){
 return <>{detail.loading&&<p className="a-caption" role="status">Loading saved action details…</p>}
  {detail.error&&<p role="alert" className="a-execution-load-error">{detail.error} <button type="button" className="a-link" onClick={detail.retry}>Retry saved details</button></p>}
  {detail.before&&<button type="button" className="a-link" disabled={detail.loading} onClick={detail.earlier}>Search older saved details</button>}</>;
}
