import {useLayoutEffect,useState,useSyncExternalStore} from 'react';

// Only in-flight passive reads. Navigation may unmount a row, not its operation.
// Nothing is persisted; reload must not manufacture pending work.
const requests=new Map(),listeners=new Set();
const subscribe=listener=>{listeners.add(listener);return()=>listeners.delete(listener)};
const publish=()=>listeners.forEach(listener=>listener());

// The account/history namespace survives a host restart; the connection identity
// does not. Neither is inferred from the currently selected chat after a wait.
export function deliveryScope(state,session){
 const origin=state?.client?.historyScope||state?.client?.hostInstanceId;
 return origin?JSON.stringify([origin,session?.workspace||null,session?.runtimeSessionId||session?.nativeIdentity||session?.id||null]):null;
}
export function deliveryRecovery(state,session){
 if(!state?.client?.hostInstanceId||!session?.id)return null;
 return {scope:deliveryScope(state,session),
  connection:state.client.hostInstanceId};
}

export function checkedDelivery(result,sessionId,inputId){
 const report=result?.result;
 if(result?.accepted!==true||!report||Array.isArray(report)||
  report.sessionId!==sessionId||report.inputId!==inputId||
  !['accepted','unknown','failed','sending','held','queued','queued_for_update','not_saved'].includes(report.delivery)||
  ('accepted' in report&&report.accepted!==true)||report.error||report.code||
  (report.status!==undefined&&(!Number.isInteger(report.status)||report.status<200||report.status>=300)))
  throw Error('Delivery evidence could not be verified. Nothing was sent again.');
 return report;
}

export function usePendingDeliveryCheck(key){
 const request=useSyncExternalStore(subscribe,()=>requests.get(key)||null,()=>null);
 const [settled,setSettled]=useState(null);
 useLayoutEffect(()=>{
  if(!request)return;
  // The promise can settle between render and layout. Its retained result still
  // reaches this exact subscriber even after removal from the in-flight map.
  request.observers.add(setSettled);
  if(request.outcome)setSettled(request.outcome);
  return()=>request.observers.delete(setSettled);
 },[request]);
 return {pending:!!request,settled,
  run(operation){
   if(requests.has(key))return requests.get(key).promise;
   setSettled(null);
   const next={promise:Promise.resolve().then(operation),observers:new Set()};
   requests.set(key,next);publish();
   const finish=outcome=>{
    next.outcome=outcome;
    next.observers.forEach(observer=>observer(outcome));
    next.observers.clear();
    if(requests.get(key)===next){requests.delete(key);publish()}
   };
   next.promise.then(result=>finish({result}),error=>finish({error:error.message}));
   return next.promise;
  },
 };
}
