// A panel can unmount while its preview request is in flight. Serialize only
// this client's appearance previews and cleanup, including across panel mounts.
const pending=new WeakMap();
function ordered(owner,operation){
 const previous=pending.get(owner)||Promise.resolve();
 const task=previous.catch(()=>{}).then(operation);
 pending.set(owner,task);
 return task.finally(()=>{if(pending.get(owner)===task)pending.delete(owner)});
}
export function clearAppearancePreview(setPreview,act){
 setPreview(false);
 return ordered(setPreview,()=>act('view.update',{patch:{themePreview:false}}));
}
export function previewAppearance(setPreview,act,args,isCurrent){
 return ordered(setPreview,async()=>{
  if(!isCurrent())return;
  try{
   const receipt=await act('theme.preview',args);
   if(receipt?.accepted===false)throw Error(receipt.error||'Could not preview appearance.');
   if(isCurrent())setPreview(true);
   return receipt;
  }finally{
   // Even a lost/rejected response may arrive after navigation. End only this
   // queued preview before a later appearance request is allowed to start.
   if(!isCurrent())await act('view.update',{patch:{themePreview:false}});
  }
 });
}
