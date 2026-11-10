// Only mounted surfaces own these leases. This is local input protection, not
// persistence or permission to replace a canonical dirty view.
const owners=new Set(),listeners=new Set();
const changed=()=>listeners.forEach(listener=>listener());
export const subscribeCanvasReload=listener=>{listeners.add(listener);return()=>listeners.delete(listener)};
export const hasLocalCanvasEdits=()=>[...owners].some(owner=>owner.blocked());
export const canvasReloadBlocked=workspace=>hasLocalCanvasEdits()||!!workspace?.views?.some(view=>view.dirty);
export const canvasReloadMessage='Save or discard your unfinished Canvas edits before reloading.';
export function createCanvasReloadGuard(){
 let active=false,retired=false,dirty=false,sequence=0,pending=0,lifetime=0;
 const owner={
  blocked:()=>dirty||pending>0,
  mount(){active=true;retired=false;owners.add(owner);changed();return()=>{active=false;retired=true;lifetime++;sequence++;pending=0;owners.delete(owner);changed()}},
  declare(value,write){
   if(retired)return Promise.reject(Error('This Canvas viewer is no longer mounted.'));
   const request=++sequence,epoch=lifetime;pending++;dirty=true; if(active)changed();
   const finish=success=>{if(retired||epoch!==lifetime)return;pending=Math.max(0,pending-1);if(request===sequence)dirty=!success||!!value;if(active)changed()};
   let result;try{result=write()}catch(error){finish(false);return Promise.reject(error)}
   return Promise.resolve(result).then(value=>{finish(true);return value},error=>{finish(false);throw error});
  },
 };
 return owner;
}
