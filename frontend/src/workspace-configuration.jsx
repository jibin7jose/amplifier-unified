import React,{useLayoutEffect} from 'react';
import {createPortal} from 'react-dom';
import {ConfigurationBoundary} from './configuration-scope';
import {AIConnections} from './ai-connections';
import {ProviderSettings,RoutingSettings} from './setup';
import {RegistrySettings} from './registry';
import {SettingsPageContext} from './settings-ui';
import {SettingsDraftProvider,useSettingsDrafts,useGuardedSettingsDraft} from './settings-drafts';

function GuardedWorkspaceSettings({state,restore,children}){
 const defaults={workSurface:'chat',workWorkspaceId:null,workWorkspaceTab:'chats',workWorkspaceSettings:null,panel:null,settingsCanvas:false};
 const requested=Object.fromEntries(Object.entries(defaults).map(([key,value])=>[key,state.view?.[key]??value]));
 const view=useGuardedSettingsDraft(requested,JSON.stringify(requested),restore);
 return children({...state,view:{...state.view,...view}});
}

// This owner stays mounted above workspace/tab navigation, preserving private
// inputs while an external navigation request waits for the user's decision.
export function WorkspaceSettingsBoundary({state,act,dispatch,navigationRef,children}){
 const drafts=useSettingsDrafts();
 useLayoutEffect(()=>{
  if(!navigationRef)return;
  const leaving=(action,args={})=>{
   if(action==='shell.command')return leaving(args.action,args.args);
   if(['session.select','session.draft','session.create','workspace.select','settings.open'].includes(action))return true;
   return action==='view.update'&&['workSurface','workWorkspaceId','workWorkspaceTab','workWorkspaceSettings','panel','settingsCanvas'].some(key=>Object.hasOwn(args.patch||{},key)&&args.patch[key]!==state.view?.[key]);
  };
  const guard={hasUnsaved:drafts.hasUnsaved,confirmClose:drafts.confirmClose,leaving};
  navigationRef.current=guard;
  return()=>{if(navigationRef.current===guard)navigationRef.current=null};
 });
 // External shared navigation still uses the retained editor's recovery path.
 // Its already-decided restoration must not reopen the pre-dispatch prompt.
 const restore=patch=>dispatch('view.update',{patch},{workspaceSettingsConfirmed:true});
 return <SettingsDraftProvider value={drafts.context}>
  <GuardedWorkspaceSettings state={state} restore={restore}>{children}</GuardedWorkspaceSettings>
  {drafts.dialog&&createPortal(drafts.dialog,document.getElementById('amp-one'))}
 </SettingsDraftProvider>;
}

export function WorkspaceConfiguration({workspace,state,act}){
 const page=state.view?.workWorkspaceSettings;
 const setPage=value=>act('view.update',{patch:{workWorkspaceSettings:value}});
 const pages=[['ai-connections','AI connections'],['routing','Model rules'],['registries','Modules & sources']];
 return <section aria-label="Workspace configuration" className="a-work-details">
  <h3>Workspace settings</h3><p>Overrides for {workspace.name}. Global defaults continue to apply where no override is saved. Existing chats keep their configuration.</p>
  <div className="a-work-tabs" role="group" aria-label="Workspace settings pages">{pages.map(([id,label])=><button key={id} type="button" aria-pressed={page===id} onClick={()=>setPage(page===id?null:id)}>{label}</button>)}</div>
  {page&&<ConfigurationBoundary key={workspace.id} state={state} act={act} target={{kind:'workspace',workspaceId:workspace.id}}>{props=><SettingsPageContext.Provider value={page}>
   {page==='ai-connections'?<AIConnections {...props} navigate={setPage}/>:page==='providers'?<ProviderSettings {...props} navigate={setPage}/>:page==='routing'?<RoutingSettings {...props}/>:<RegistrySettings {...props}/>}
  </SettingsPageContext.Provider>}</ConfigurationBoundary>}
 </section>;
}
