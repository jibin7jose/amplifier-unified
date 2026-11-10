// Chat selection and workspace browsing are independent. Keep the response's
// scope so a late shell fetch cannot masquerade as the newly requested page.
export function navigationSnapshot(snapshot,state,scope){
 if(!state)return snapshot;
 const resolvedWorkspaceId=Object.hasOwn(snapshot,'resolvedWorkspaceId')?snapshot.resolvedWorkspaceId:snapshot.selectedWorkspaceId;
 const pinned=scope?.mode==='pinned';
 const surface=state.view?.workSurface||'chat';
 const workspaceId=pinned?scope.workspaceId:surface==='workspace'?state.view.workWorkspaceId:state.selectedWorkspaceId;
 const browseId=pinned?null:state.view?.workWorkspaceId??null;
 const pending=!!state.navigationPending,workspacePending=workspaceId!==resolvedWorkspaceId;
 if(snapshot.selectedSessionId===state.selectedSessionId&&snapshot.selectedWorkspaceId===workspaceId&&!!snapshot.navigationPending===pending&&snapshot.navigationWorkspacePending===workspacePending&&snapshot.view?.workSurface===surface&&snapshot.view?.workWorkspaceId===browseId)return snapshot;
 return {...snapshot,resolvedWorkspaceId,selectedSessionId:state.selectedSessionId,selectedWorkspaceId:workspaceId,navigationPending:pending,navigationWorkspacePending:workspacePending,view:{...snapshot.view,workSurface:surface,workWorkspaceId:browseId}};
}
