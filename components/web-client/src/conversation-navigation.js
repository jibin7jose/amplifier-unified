// A bounded per-client display cache. Server state and execution stay authoritative.
export function isNavigationAction(action,args={}){
 if(action==='shell.command')return isNavigationAction(args.action,args.args);
 return ['session.select','session.draft','workspace.select'].includes(action)
  ||action==='view.update'&&['workSurface','workWorkspaceId'].some(key=>Object.hasOwn(args.patch||{},key));
}

// Drawer display changes share navigation ordering without becoming a new
// destination or invalidating a pending archive/reference navigation intent.
export function usesNavigationQueue(action,args={}){
 if(action==='shell.command')return usesNavigationQueue(args.action,args.args);
 return isNavigationAction(action,args)
  ||action==='view.update'&&['navExpanded','navPinned'].some(key=>Object.hasOwn(args.patch||{},key));
}

export function createConversationNavigation(limit=32){
 const cache=new Map(),pending=new Map();let epoch;
 const selected=state=>state?.sessions?.find(row=>row.id===state.selectedSessionId);
 function remember(state){
  const nextEpoch=state?.client?.hostInstanceId;
  if(epoch!==undefined&&nextEpoch!==epoch){cache.clear();pending.clear()}
  epoch=nextEpoch;
  const row=selected(state);if(!row)return;
  cache.delete(row.id);cache.set(row.id,{session:{id:row.id,workspaceId:row.workspaceId,title:row.title,draft:row.draft}});
  while(cache.size>limit)cache.delete(cache.keys().next().value);
 }
 function begin(state,id,hint){
  // A dirty renderer must remain mounted until the server accepts navigation.
  if(state?.canvasWorkspace?.views?.some(row=>row.dirty))return null;
  const row=state?.sessions?.find(row=>row.id===id)||cache.get(id)?.session||(hint?.id===id?hint:null);
  if(!row)return null;
  const current=selected(state);
  if(current)remember({...state,sessions:state.sessions.map(row=>row.id===current.id?{...row,draft:state.view?.draft||''}:row)});
  const token=Symbol();pending.set(token,{id,row});return token;
 }
 function beginDraft(){
  // Keep the current destination mounted until the host confirms the draft,
  // but do not let its composer accept work for the destination being left.
  const token=Symbol();pending.set(token,{draft:true});return token;
 }
 function apply(state){
  if(!state||!pending.size)return state;
  const {id,row,draft}=Array.from(pending.values()).at(-1);
  if(draft)return {...state,navigationPending:true,draftNavigationPending:true};
  if(state.selectedSessionId===id)return {...state,navigationPending:true,view:{...state.view,workSurface:'chat'}};
  const saved=cache.get(id),summary=state.sessions?.find(row=>row.id===id)||row;
  const {messages,workers,workerSummary,approvals,execution,streaming,historyActivity,messageWindow,executionWindow,runtimeEvents,generations,messageAnnotations,messageQuotes,...metadata}=summary;
  const session={...metadata,draft:saved?.session.draft??summary.draft,historyLoading:true,messages:[],workers:[],approvals:[]};
  return {...state,selectedSessionId:id,selectedWorkspaceId:session.workspaceId??state.selectedWorkspaceId,navigationPending:true,
   sessions:[...(state.sessions||[]).filter(row=>row.id!==id),session],
   view:{...state.view,workSurface:'chat',draft:session.draft||'',draftDocument:null},
   canvas:{open:false},canvasWorkspace:{views:[]},canvasArtifacts:[]};
 }
 return {remember,begin,beginDraft,apply,settle:token=>pending.delete(token)};
}
