// Optional diagnostics must not prevent rethrow/cleanup after a failed journey.
// Deadlines need a responsive Node event loop; expiry does NOT cancel the read.
import {bounded} from './chat-reference-harness.mjs';

export const OPTIONAL_OBSERVATION_MS=500;

export async function observeOptional(read){
 // Handle rejection immediately, including one arriving after deadline expiry.
 // Only the winner returns facts; late settlement cannot update a saved record.
 const observed=Promise.resolve().then(read).then(
  value=>({status:'observed',value}),
  ()=>({status:'unavailable'}),
 );
 try{return await bounded(observed,OPTIONAL_OBSERVATION_MS,'Optional observation deadline')}
 catch{return {status:'timed-out'}}
}

export function createSelectionEvidence(){
 const observations=[],pages=new WeakMap(),failures=new WeakMap();
 let omitted=0,attempt=0,page=0;
 function binding(target,helper,stage,selector){
  let owner=pages.get(target);
  if(!owner){owner={pageId:++page};pages.set(target,owner)}
  return owner.binding=Object.freeze({pageId:owner.pageId,attempt:++attempt,helper,stage,selector});
 }
 function retain(phase,facts){
  if(observations.length===16){observations.shift();omitted++}
  const observation={phase,...facts};observations.push(observation);return observation;
 }
 async function observeFailure(target,error,failureStage=null){
  const key=error!==null&&(typeof error==='object'||typeof error==='function')?error:null;
  let retained=key&&failures.get(key);
  if(retained){
   // Deferred errors retain their original page/attempt, including unavailable
   // evidence. Another page or later probe cannot replace the original facts.
   if(retained.failureStage===null&&failureStage!==null)retained={...retained,failureStage};
   if(observations.at(-1)!==retained){
    const {phase,...facts}=retained;
    failures.set(key,retain(phase,facts));
   }
   return;
  }
  // Capture outside the page and BEFORE the optional read's first await.
  const expected=pages.get(target)?.binding;
  let facts={...(expected??{pageId:null,attempt:null,helper:null,stage:'no-selection-attempt',selector:null}),
   available:false,reason:expected?'observation_unavailable':'attempt_unbound'};
  if(expected){
   const result=await observeOptional(()=>target.evaluate(()=>window.multiQuoteSelectionProbe?.()??null));
   if(result.status==='timed-out'){
    facts={...expected,available:false,reason:'observation_deadline',budgetMs:OPTIONAL_OBSERVATION_MS};
   }else if(result.status==='observed'){
    try{
     const observed=result.value;
     facts=observed&&['pageId','attempt','helper','stage','selector'].every(field=>observed[field]===expected[field])?
      observed:{...expected,available:false,reason:observed?'probe_not_bound':'probe_unavailable'};
    }catch{}
   }
  }
  const observation=retain(error?.name==='TimeoutError'?'selection-timeout':'selection-failed',{...facts,failureStage});
  if(key)failures.set(key,observation);
 }
 return {observations,binding,retain,observeFailure,get omitted(){return omitted}};
}