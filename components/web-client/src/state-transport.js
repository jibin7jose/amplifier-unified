// The stream baseline is separate from action/GET snapshots: an HTTP response
// may arrive ahead of the stream without invalidating that stream's patch chain.
export function applyStateDelta(previous, delta){
 if(!previous||previous.revision!==delta.baseRevision)throw Error('State stream needs a fresh snapshot.');
 const replace=(value,path,patch)=>{
  if(!path.length)return patch.value;
  const [key,...rest]=path;
  const copy=Array.isArray(value)?[...value]:{...value};
  if(!rest.length&&patch.remove)delete copy[key];
  else Object.defineProperty(copy,key,{value:replace(Object.hasOwn(value||{},key)?value[key]:undefined,rest,patch),enumerable:true,configurable:true,writable:true});
  return copy;
 };
 let next=previous;
 for(const patch of delta.changes)next=replace(next,patch.path,patch);
 if(next.revision!==delta.revision)throw Error('State stream revision mismatch.');
 return next;
}

// Delta application preserves untouched branches. A causal revision still
// advances readback and command waiters, but need not repaint an unchanged UI.
// Fresh baselines and changed/deleted fields deliberately take the normal path.
export function onlyStateRevisionChanged(previous,next){
 if(!previous?.client?.hostInstanceId||previous.client.hostInstanceId!==next?.client?.hostInstanceId
  ||!Number.isSafeInteger(previous.revision)||!Number.isSafeInteger(next.revision)||next.revision<previous.revision)return false;
 const before=Object.keys(previous),after=Object.keys(next);
 return before.length===after.length&&after.every(key=>Object.hasOwn(previous,key)
  &&(key==='revision'||Object.is(previous[key],next[key])));
}
