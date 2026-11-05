import {AttentionBadge} from './attention';
import React from 'react';
import {ChevronRight,ArrowRight,Layers,ShieldCheck,FolderOpen,Brain,Monitor,Archive,Activity} from 'lucide-react';
import {advancedGroups,settingsTitle} from './settings-navigation';
import './settings-everyday.css';

export function SettingsLink({title,description,onClick,Icon=ArrowRight,page,count,disabled=false}){
 return <button type="button" className="a-everyday-row" disabled={disabled} data-action="view.update" data-settings-destination={page} onClick={onClick}>{Icon!==ArrowRight&&<Icon aria-hidden="true"/>}<span><strong>{title}</strong>{description&&<small>{description}</small>}</span><AttentionBadge count={count}/><ChevronRight aria-hidden="true"/></button>;
}
export function PrivacySettings({navigate,state}){
 const rows=[['access-approvals','Access & approvals','Set Full access or Ask when needed across all workspaces.',ShieldCheck],['storage','Storage','See disk usage and review workspace cleanup.',Archive],['workspaces','Files & folders','Choose where new workspaces are created.',FolderOpen],['permissions','File access','Review additional allowed and blocked folders.',ShieldCheck],['desktop','Screen & computer access','Check the host, browser, and permission status.',Monitor],['history','Saved work','Import, export, and review cleanup.',Archive],['diagnostics','App diagnostic capture','Review recorded data and forwarding destinations.',Activity]];
 return <><p className="a-everyday-intro">Your saved work, memory, and access choices.</p><div className="a-everyday-list">{rows.filter(row=>row[0]!=='diagnostics').map(([page,title,description,Icon])=><SettingsLink key={page} count={state.attention?.pages?.[page]} {...{page,title,description,Icon}} onClick={()=>navigate(page)}/>)}</div><h4>Diagnostics</h4><div className="a-everyday-list"><SettingsLink page="diagnostics" count={state.attention?.pages?.diagnostics} title="App diagnostic capture" description="Review collected records and forwarding." Icon={Activity} onClick={()=>navigate('diagnostics')}/></div><div className="a-everyday-footer"><SettingsLink page="repair" count={state.attention?.pages?.repair} title="Backups & recovery" description="Create a private backup or troubleshoot this app." onClick={()=>navigate('repair')}/></div></>;
}
export function AdvancedSettings({sections,navigate,state}){
 const contributed=sections.flatMap(section=>section.pages).filter(([id])=>id.startsWith('shell:'));
 const groups=[
  {title:'Model rules',description:'Routing profiles, roles, and model preference.',page:'routing'},
  {title:'Provider configuration',description:'Instance names, credentials, and custom providers.',page:'providers'},
  {title:'Bundles & modules',description:'Composition, discovery, defaults, and registries.',pages:['app-bundles','add-bundles','defaults','registries']},
  {title:'Tool connections',description:'MCP connections, installation sources, and authentication.',page:'tool-connections'},
  {title:'Custom appearance',description:'Import, export, theme folders, and custom CSS.',page:'custom-appearance'},
  {title:'Performance & runtime',description:'Ready conversations and terminal access.',pages:['model-capacity','ready-conversations','automation','install-app']},
  {title:'Troubleshooting & recovery',description:'Diagnostics, backups, repair, and reset options.',pages:['diagnostics','repair','reset']},
 ];
 return <><p className="a-everyday-intro">The full configuration, when you need it.</p><div className="a-everyday-list">{groups.map(group=>group.page?<SettingsLink key={group.title} title={group.title} description={group.description} page={group.page} count={state.attention?.pages?.[group.page]} onClick={()=>navigate(group.page)}/>:<details className="a-advanced-group" key={group.title}><summary><span><strong>{group.title}</strong><small>{group.description}</small></span><AttentionBadge count={group.pages.reduce((sum,page)=>sum+(state.attention?.pages?.[page]||0),0)}/><ChevronRight/></summary><div>{group.pages.map(page=><SettingsLink key={page} page={page} count={state.attention?.pages?.[page]} title={settingsTitle(page,sections)} onClick={()=>navigate(page)}/>)}</div></details>)}</div>{contributed.length>0&&<><h4>Extension settings</h4><div className="a-everyday-list">{contributed.map(([page,title])=><SettingsLink key={page} page={page} count={state.attention?.pages?.[page]} title={title} Icon={Layers} onClick={()=>navigate(page)}/>)}</div></>}</>;
}
