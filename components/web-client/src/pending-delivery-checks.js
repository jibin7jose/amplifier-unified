import {useLayoutEffect,useState,useSyncExternalStore} from 'react';

// Only in-flight passive reads. Navigation may unmount a row, not its operation.
// Nothing is persisted; reload must not manufacture pending work.
const requests=new Map(),listeners=new Set();
const subscribe=listener=>{listeners.add(listener);return()=>listeners.delete(listener)};
const publish=()=>listeners.forEach(listener=>listener());

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
