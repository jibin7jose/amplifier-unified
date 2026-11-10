import {useEffect,useRef,useState} from 'react';
import {draftDefaultsKey} from './new-chat';

// A read receipt is not a resolved catalog. Retry discarded/missing results
// and host changes; never permanently latch a draft as already requested.
export function useDraftDiscovery({enabled,setup,defaults,configurationRevision,hostInstanceId,act,needsProviders}){
 const [attempt,retry]=useState(0),[status,setStatus]=useState({}),latest=useRef(),requested=useRef();
 latest.current={act,needsProviders};
 const key=JSON.stringify([draftDefaultsKey(setup),configurationRevision,hostInstanceId]);
 const managed=setup.location?.kind==='managed';
 useEffect(()=>{
  if(!enabled||defaults.phase==='ready'||typeof window==='undefined')return;
  const reconnect=()=>retry(value=>value+1);
  window.addEventListener('amplifier-reconnected',reconnect);
  return()=>window.removeEventListener('amplifier-reconnected',reconnect);
 },[enabled,defaults.phase]);
 useEffect(()=>{
  if(!enabled||(!setup.workspace&&!managed))return;
  if(defaults.phase==='ready'){
   requested.current=null;
   if(!latest.current.needsProviders)return;
   const timer=setTimeout(()=>Promise.resolve().then(()=>latest.current.act('providers.list',{workspace:setup.workspace,...(managed?{location:{kind:'managed'}}:{})})).catch(()=>{}),250);
   return()=>clearTimeout(timer);
  }
  if(defaults.phase==='error'&&(!attempt||requested.current?.key===key&&requested.current?.attempt===attempt))return;
  requested.current={key,attempt};
  let live=true;
  setStatus({key,attempt});
  const slow=setTimeout(()=>{if(live)setStatus({key,attempt,delayed:true})},15000);
  const timer=setTimeout(()=>{
   const {act,needsProviders}=latest.current,location=managed?{location:{kind:'managed'}}:{};
   Promise.resolve().then(()=>act('configuration.defaults',{workspace:setup.workspace,bundle:setup.bundle||'',...location}))
    .then(receipt=>{if(!receipt||receipt.accepted===false)throw Error('Could not load model settings. Try again.')})
    .catch(error=>{if(live){clearTimeout(slow);setStatus({key,attempt,error:error.message||'Could not load model settings. Try again.'})}});
   if(needsProviders)Promise.resolve().then(()=>act('providers.list',{workspace:setup.workspace,...location})).catch(()=>{});
  },250);
  return()=>{live=false;clearTimeout(timer);clearTimeout(slow)};
 },[enabled,key,defaults.phase,attempt]);
 const current=status.key===key&&status.attempt===attempt&&defaults.phase!=='ready'?status:{};
 return {...current,retry:()=>retry(value=>value+1)};
}
