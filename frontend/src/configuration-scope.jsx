import React,{createContext,useContext,useRef} from 'react';

const ConfigurationContext=createContext({kind:'global'});
const editorKeys=['aiConnectionEditor','imageSettingsEditor','providerEditor','routingEditor','registryDraft','settingsFilters'];
export function useConfigurationId(){
 const target=useContext(ConfigurationContext);
 return id=>target.kind==='workspace'?'workspace-'+target.workspaceId+'-'+id:id;
}
export function useConfigurationScope(value){
 const target=useContext(ConfigurationContext);
 return target.kind==='global'?'global':value==='project'?'project':'local';
}
export function ConfigurationScopeSelect({id,value,onChange,disabled}){
 const scope=useConfigurationScope(value);
 if(scope==='global')return <p className="a-caption">Applies to all workspaces. Workspace overrides are in each workspace’s Details tab.</p>;
 return <><label htmlFor={id}>Save in</label><select id={id} value={scope} disabled={disabled} data-action="view.update" onChange={event=>onChange(event.target.value)}><option value="local">This workspace on this host</option><option value="project">This project (shareable configuration)</option></select></>;
}

// Each surface has separate drafts and only displays configuration read for its
// explicit owner. Changing the selected chat never changes that owner.
export function ConfigurationBoundary({state,act,target={kind:'global'},children}){
 const identity=JSON.stringify(target),key=target.kind==='global'?'global':'workspace:'+target.workspaceId;
 const cache=useRef({});
 for(const field of ['setup','registry'])if(JSON.stringify(state[field]?.configurationTarget)===identity)cache.current[field]={...cache.current[field],...state[field]};
 const saved=state.view?.configurationDrafts||{},drafts=saved[key]||{};
 const view={...state.view};if(target.kind==='workspace')for(const name of editorKeys)view[name]=drafts[name];
 const dispatch=(action,args={},meta={})=>{
  if(/^(providers|routing|modules|sources)\./.test(action)||['settings.connection.start','settings.connection.select'].includes(action)){
   const {sessionId,workspace,location,nativeProject,...values}=args;
   return act(action,{...values,scope:target.kind==='global'?'global':values.scope==='project'?'project':'local',configurationTarget:target},meta);
  }
  if(action==='view.update'&&target.kind==='workspace'){
   const patch={...args.patch},next={...drafts};let changed=false;
   for(const name of editorKeys)if(Object.hasOwn(patch,name)){next[name]=patch[name];delete patch[name];changed=true;}
   if(changed)patch.configurationDrafts={...saved,[key]:next};
   return act(action,{...args,patch},meta);
  }
  return act(action,args,meta);
 };
 return <ConfigurationContext.Provider value={target}>{children({state:{...state,view,actionStatus:Object.fromEntries(Object.entries(state.actionStatus||{}).filter(([action,op])=>!(/^(providers|routing|modules|sources)\./.test(action)||['settings.connection.start','settings.connection.select'].includes(action))||JSON.stringify(op.configurationTarget)===identity)),setup:{...cache.current.setup,operations:Object.fromEntries(Object.entries(state.setup?.operations||{}).filter(([,op])=>JSON.stringify(op.configurationTarget)===identity).map(([name,op])=>[name.split('\n').at(-1),op]))},registry:cache.current.registry||{}},session:null,act:dispatch})}</ConfigurationContext.Provider>;
}
