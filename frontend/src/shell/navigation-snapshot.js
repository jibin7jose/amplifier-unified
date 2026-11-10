// Chat selection and workspace browsing are independent. Keep the response's
// scope so a late shell fetch cannot masquerade as the newly requested page.
export function navigationSnapshot(snapshot,state,scope,sharedAge=true){
 if(!state)return snapshot;
 const resolvedWorkspaceId=Object.hasOwn(snapshot,'resolvedWorkspaceId')?snapshot.resolvedWorkspaceId:snapshot.selectedWorkspaceId;
 const pinned=scope?.mode==='pinned';
 const surface=state.view?.workSurface||'chat';
 const workspaceId=pinned?scope.workspaceId:surface==='workspace'?state.view.workWorkspaceId:state.selectedWorkspaceId;
 const browseId=pinned?null:state.view?.workWorkspaceId??null;
 const pending=!!state.navigationPending,workspacePending=workspaceId!==resolvedWorkspaceId;
 const navAge=sharedAge?(state.view?.navAge||'all'):snapshot.view?.navAge;
 // Aggregates have no chat-page scope. Preserve their response witness rather
 // than relabeling old counts when the shared slider changes ahead of a read.
 if(sharedAge){
  const stale=['workspaceExplorer','workspaceShortcuts'].filter(name=>
   snapshot[name]!==undefined&&snapshot.navigationResourceAges?.[name]!==navAge);
  if(stale.length){
   snapshot={...snapshot,navigationResources:(snapshot.navigationResources||[]).filter(name=>!stale.includes(name)),
    navigationResourcePending:{...snapshot.navigationResourcePending},navigationResourceRetained:{...snapshot.navigationResourceRetained}};
   for(const name of stale){
    delete snapshot[name];delete snapshot.navigationResourceRetained[name];
    snapshot.navigationResourcePending[name]={status:'pending'};
   }
  }
 }
 if(snapshot.selectedSessionId===state.selectedSessionId&&snapshot.selectedWorkspaceId===workspaceId&&!!snapshot.navigationPending===pending&&snapshot.navigationWorkspacePending===workspacePending&&snapshot.view?.workSurface===surface&&snapshot.view?.workWorkspaceId===browseId&&(snapshot.view?.navAge||'all')===navAge)return snapshot;
 return {...snapshot,resolvedWorkspaceId,selectedSessionId:state.selectedSessionId,selectedWorkspaceId:workspaceId,navigationPending:pending,navigationWorkspacePending:workspacePending,view:{...snapshot.view,workSurface:surface,workWorkspaceId:browseId,navAge}};
}
