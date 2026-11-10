import {useEffect,useSyncExternalStore} from 'react';

// Admission belongs to the saved input, not to a mounted transcript row.
// Keep only unsettled requests; scrolling/navigation must not unlock a retry.
const requests=new Map(),listeners=new Set();
const publish=()=>listeners.forEach(listener=>listener());
const subscribe=listener=>{listeners.add(listener);return()=>listeners.delete(listener)};

export function usePendingMessageEdit(key,onError){
 const request=useSyncExternalStore(subscribe,()=>requests.get(key)||null,()=>null);
 useEffect(()=>{
  if(!request)return;
  request.observers.add(onError);
  return()=>request.observers.delete(onError);
 },[request,onError]);
 return {
  pending:!!request,
  isPending:()=>requests.has(key),
  run(operation){
   if(requests.has(key))return null;
   const next={observers:new Set()};
   requests.set(key,next);publish();
   return Promise.resolve().then(operation).catch(error=>{
    next.observers.forEach(observer=>observer(error.message));throw error;
   }).finally(()=>{
    if(requests.get(key)===next){requests.delete(key);publish()}
   });
  },
 };
}
