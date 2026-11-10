import {workGroups} from '../work-status.js';
// Only the visible organization requests rows. Home and Status share the
// catalog's query cache, while keeping independent filters and page cursors.
export function navigationResources(state,sidebarVisible=true,supportsStatus=true){
 const view=state?.view||{},surface=view.workSurface||'chat';
 const resources=!sidebarVisible?[]:supportsStatus&&view.navMode==='status'
  ?Object.entries(workGroups).filter(([kind])=>!(view.navStatusCollapsed||[]).includes(kind)).map(([,group])=>group.resource)
  :['pinnedNavigation','recentNavigation','workspaceShortcuts'];
 if(surface==='home')resources.push('workHome','workspaceShortcuts');
 if(surface==='chats')resources.push('sidebarSearch');
 if(surface==='workspace'&&(view.workWorkspaceTab||'chats')==='chats')resources.push('sidebarWorkspace');
 if(surface==='workspaces')resources.push('workspaceExplorer');
 // The visible overview reuses these bounded rows; a closed Canvas asks for none.
 if(surface==='chat'&&state?.canvas?.open&&(view.canvasDraft?.library||state.canvas.placeholder||!state.canvasWorkspace?.views?.length)&&!view.settingsCanvas)
  resources.push('recentNavigation','workspaceShortcuts');
 return [...new Set(resources)].sort();
}

export function hasNavigationResource(snapshot,resource){
 return Array.isArray(snapshot?.navigationResources)&&snapshot.navigationResources.includes(resource);
}
