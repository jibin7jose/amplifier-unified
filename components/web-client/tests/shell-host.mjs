// Public host fixtures for the extracted builtins. Production snapshots come
// from the bounded /api/shell projection; these small fixtures are synthetic.
export function shellFor(state,act){
 const navigationResources=['chatNavigation','homeNavigation','pinnedNavigation','recentNavigation','workspaceShortcuts','workspaceExplorer','sidebarSearch','sidebarWorkspace','sidebarRecent','sidebarPinned','workspaceOverview'].sort();
 const composition={instances:[{id:'workspaces',package:'builtin.workspaces',slot:'navigation',hideWhen:{instanceId:'chats',navChatScope:'all'}},{id:'chats',package:'builtin.chats',slot:'navigation'}],presentation:{}};
 const snapshots=Object.fromEntries(composition.instances.map(instance=>{
  const draft=state.view?.workspaceDraft||{},chat=draft.mode?.startsWith('chat-');
  const view={...state.view,workspaceDraft:(instance.id==='chats'?chat:!chat)?draft:{}};
  return [instance.id,{...state,navigationResources,view}];
 }));
 const slots=Object.fromEntries(Object.entries({'app.actions':'builtin.app-actions','app.status':'builtin.app-status','conversation.header':'builtin.conversation-header','composer.actions':'builtin.composer-actions','canvas.toolbar':'builtin.canvas-toolbar','settings.appearance':'builtin.settings-appearance'}).map(([slot,packageId])=>[slot,{default:packageId}]));
 const resolvedInstances=[...composition.instances,...Object.entries(slots).map(([slot,value])=>({id:'core.'+slot,slot,package:value.default}))];
 return {ready:true,composition,data:{revision:0,composition,effectiveComposition:composition,resolvedInstances,slots,snapshots,packages:{}},recover:()=>{},report:()=>{},statusFor:()=>()=>{},
  hostFor:instance=>({apiVersion:'1.0',clientId:'test-client',instanceId:instance.id,subscribe:()=>()=>{},getSnapshot:()=>snapshots[instance.id],dispatch:act,setDirty:async()=>{}})};
}
