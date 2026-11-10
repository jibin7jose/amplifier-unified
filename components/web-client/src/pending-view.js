// View changes paint locally while their shared actions keep their normal order.
// Track each request, rather than each field: an older acknowledgement or error
// must never erase a newer choice for the same control.
export function createPendingView(){
 const patches=new Map();
 return {
  add(patch,sessionId){const token=Symbol();patches.set(token,{patch:{...patch},sessionId});return token},
  addCanvas(state,open){const token=Symbol();patches.set(token,{canvas:{open,sessionId:state.selectedSessionId,canvasId:state.canvas?.id,host:state.client?.hostInstanceId}});return token},
  settle(token){patches.delete(token)},
  bindDraft(token,sessionId){const entry=patches.get(token);if(entry&&Object.hasOwn(entry.patch,'draft'))entry.sessionId=sessionId},
  apply(state){
   if(!state||!patches.size)return state;
   let view={...state.view},canvas=state.canvas;
   for(const {patch,sessionId,canvas:pending} of patches.values()){
    // Painting an open panel is not permission to read its server-bound tool.
    // Keep that distinction until every matching visibility action settles,
    // even when an intermediate SSE snapshot already has the desired value.
    if(pending){if(pending.sessionId===state.selectedSessionId&&pending.canvasId===state.canvas?.id&&pending.host===state.client?.hostInstanceId){canvas={...canvas,open:pending.open,visibilityPending:true};if(!pending.open)view.canvasFocused=false}continue}
    const values={...patch};if(sessionId!==undefined&&sessionId!==state.selectedSessionId)for(const key of Object.keys(values))if(key==='draft'||key==='draftDocument'||key.startsWith('canvas'))delete values[key];view={...view,...values}};
   return {...state,view,canvas};
  },
 };
}
