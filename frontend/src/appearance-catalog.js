// Page-local, host-scoped reads. Metadata is small; full CSS is loaded only for
// visible thumbnails or an explicit selection, never in the startup catalog.
export function createAppearanceCatalog({now=Date.now,freshMs=30000,maxBodies=24}={}){
 let snapshot={library:null,error:'',loading:false},pending=null,epoch=0,identity=0,loadedAt=-Infinity;
 const listeners=new Set(),bodies=new Map();
 const publish=patch=>{snapshot={...snapshot,...patch};for(const fn of listeners)fn()};
 const getSnapshot=()=>snapshot,subscribe=fn=>{listeners.add(fn);return()=>listeners.delete(fn)};
 function invalidate(){epoch++;pending=null;loadedAt=-Infinity;publish({loading:false})}
 function clear(){epoch++;identity++;pending=null;loadedAt=-Infinity;bodies.clear();publish({library:null,error:'',loading:false})}
 function refresh(act,{force=false}={}){
  if(pending&&!force)return pending;
  if(!force&&snapshot.library&&now()-loadedAt<freshMs)return Promise.resolve(snapshot.library);
  const current=++epoch;
  publish({loading:true});
  const task=Promise.resolve().then(()=>act('theme.list',{})).then(receipt=>{
   if(receipt?.accepted!==true||!Array.isArray(receipt.result?.items)||!Array.isArray(receipt.result?.errors))
    throw Error('The appearance library is unavailable. Try again.');
   if(current===epoch){loadedAt=now();publish({library:receipt.result,error:''})}
   return receipt.result;
  }).catch(error=>{
   if(current===epoch)publish({error:error.message});
   throw error;
  }).finally(()=>{if(current===epoch){pending=null;publish({loading:false})}});
  pending=task;return task;
 }
 function read(item,act){
  const key=JSON.stringify([item.id,item.fingerprint]);
  if(bodies.has(key)){const value=bodies.get(key);bodies.delete(key);bodies.set(key,value);return value}
  const captured=identity;
  const task=Promise.resolve().then(()=>act('theme.read',{id:item.id})).then(receipt=>{
   if(captured!==identity)throw Error('The appearance connection changed.');
   const entry=receipt?.result;
   if(receipt?.accepted!==true||typeof entry?.css!=='string'||entry.fingerprint!==item.fingerprint)
    throw Error('This appearance changed. Refresh its preview.');
   return entry;
  }).catch(error=>{if(bodies.get(key)===task)bodies.delete(key);throw error});
  bodies.set(key,task);
  while(bodies.size>maxBodies)bodies.delete(bodies.keys().next().value);
  return task;
 }
 return {getSnapshot,subscribe,refresh,read,invalidate,clear};
}
const catalogs=new Map();
export function appearanceCatalogFor(scope){
 const key=String(scope||'unattached');
 if(!catalogs.has(key))catalogs.set(key,createAppearanceCatalog());
 return catalogs.get(key);
}
export function clearAppearanceCatalogs(){
 for(const catalog of catalogs.values())catalog.clear();
 catalogs.clear();
}