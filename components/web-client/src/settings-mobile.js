import {settingsLocation,settingsPatch,settingsParent,settingsTitle} from './settings-navigation.js';
import {moduleRows} from './module-config.js';

// History contains navigation only. Drafts, credentials and configuration are
// never copied into browser history or restored from an old history entry.
export const settingsIndexPatch={panel:'settings',settingsSection:'index',settingsExpanded:[]};
export function settingsBaseNavigation(page,sections){
 const navigation={...settingsPatch(page,sections)};
 if(['ai-connections','overview'].includes(page))navigation.aiConnectionEditor={step:'list'};
 if(page==='diagnostics')navigation.diagnosticsDraft={destinationId:null};
 if(page==='providers')navigation.providerEditor={detailOpen:false,orderOpen:false};
 if(page==='routing')navigation.routingEditor={detailOpen:false,candidateOpen:false,orderOpen:false};
 if(page==='workspace-starters')navigation.workspaceStarterEditor={detailOpen:false};
 if(page==='app-bundles')navigation.bundleManager={detailOpen:false,orderOpen:false};
 if(page==='loaded-modules')navigation.moduleEditor={detailOpen:false};
 if(['smart-tools','tool-connections'].includes(page))navigation.smartToolsEditor={page:'home',catalogDetail:false};
 return navigation;
}
export function mergeSettingsNavigation(view,navigation){
 const patch={...navigation};
 for(const key of ['providerEditor','routingEditor','bundleManager','moduleEditor','smartToolsEditor','diagnosticsDraft','aiConnectionEditor','workspaceStarterEditor'])if(key in patch)patch[key]={...view[key],...patch[key]};
 return patch;
}
export function settingsTrail(view={},state={},sections){
 const {page,section}=settingsLocation(view,sections),trail=[{key:'index',title:'Settings',navigation:settingsIndexPatch}];
 if(view.settingsSection==='index'&&view.panel!=='appearance')return trail;
 const base=settingsBaseNavigation(page,sections);
 const parent=settingsParent(page,sections);
 if(parent)trail.push({key:parent,title:section.title,navigation:settingsBaseNavigation(parent,sections)});
 trail.push({key:page,title:settingsTitle(page,sections),navigation:base});
 const add=(key,title,editor,patch)=>trail.push({key,title,navigation:{...base,[editor]:{...base[editor],...patch}}});
 if(['ai-connections','overview'].includes(page)){
  const d=view.aiConnectionEditor||{},step=d.step||'list';
  if(!['list','images'].includes(step)){add('ai/'+step,step==='services'?'Connect AI':step==='model'?'Choose model':'AI connection','aiConnectionEditor',{step});}
 }else if(page==='diagnostics'){
  const d=view.diagnosticsDraft||{},cfg=d.config||state.diagnostics?.config;
  const selected=cfg?.destinations?.find(row=>row.id===d.destinationId);
  if(selected)add('diagnostics/destination',selected.name||'Destination','diagnosticsDraft',{destinationId:selected.id});
 }else if(page==='providers'){
  const d=view.providerEditor||{};
  if(d.order&&d.orderOpen!==false)add('providers/order','Preference order','providerEditor',{orderOpen:true});
  else if(d.detailOpen)add('providers/detail',d.id||'Add connection','providerEditor',{detailOpen:true});
 }else if(page==='routing'){
  const d=view.routingEditor||{},role=d.role||'general';
  if(d.detailOpen||(d.order&&d.orderOpen!==false)){
   add('routing/role/'+role,role,'routingEditor',{role,detailOpen:true});
   if(d.order&&d.orderOpen!==false)add('routing/order/'+role,'Preference order','routingEditor',{role,detailOpen:true,orderOpen:true});
   else if(d.candidateOpen)add('routing/choice/'+role,'Choice '+((d.candidate||0)+1),'routingEditor',{role,detailOpen:true,candidateOpen:true});
  }
 }else if(page==='workspace-starters'){
  const d=view.workspaceStarterEditor||{};
  if(d.detailOpen)add('starters/detail',state.workspaceStarters?.items?.find(row=>row.id===d.id)?.name||'New starter','workspaceStarterEditor',{id:d.id,detailOpen:true});
 }else if(page==='app-bundles'){
  const d=view.bundleManager||{},entries=Array.isArray(state.bundles)?state.bundles:state.bundles?.entries||[];
  if(d.order&&d.orderOpen!==false)add('bundles/order','Composition order','bundleManager',{orderOpen:true});
  else if(d.detailOpen)add('bundles/detail',entries.find(row=>row.id===d.entryId)?.name||'Bundle','bundleManager',{detailOpen:true});
 }else if(page==='loaded-modules'){
  const d=view.moduleEditor||{};if(d.detailOpen)add('modules/detail',(()=>{try{return moduleRows(JSON.parse(d.text)).find(row=>row.key===d.selectedKey)?.id}catch{return null}})()||'Module','moduleEditor',{detailOpen:true});
 }else if(['smart-tools','tool-connections'].includes(page)){
  const d=view.smartToolsEditor||{},p=d.page||'home';
  if(p==='catalog'){
   add('tools/catalog','Catalog','smartToolsEditor',{page:'catalog'});
   if(d.catalogDetail)add('tools/catalog/detail',state.smartTools?.catalog?.find(row=>row.id===d.catalogId)?.name||'Tool details','smartToolsEditor',{page:'catalog',catalogDetail:true});
  }else if(p!=='home'){
   if(p==='source'&&d.returnPage==='catalog')add('tools/catalog','Catalog','smartToolsEditor',{page:'catalog'});
   if(p==='tool')add('tools/server',state.smartTools?.servers?.find(row=>row.id===d.serverId)?.name||'Connection','smartToolsEditor',{page:'server'});
   add('tools/'+p,({source:'Install from source',connection:'Connection settings',server:'Connection',tool:d.toolName||'Tool'})[p]||'Smart Tools','smartToolsEditor',{page:p});
  }
 }
 return trail;
}
