export const chatSettingsPages={"conversation": "session-details", "runtime": "chat-advanced", "loaded-modules": "chat-models", "share-bundle": "chat-models", "recall": "chat-memory"};
// Unknown or retired saved destinations fall back without migrating configuration.
export const settingsSections=[
 {id:'ai',title:'AI connections',scope:'Across workspaces on this Amplifier host',pages:[['ai-connections','AI connections'],['overview','AI connections']]},
 {id:'smart-tools',title:'Smart Tools',scope:'This Amplifier host',pages:[['smart-tools','Smart Tools']]},
 {id:'appearance',title:'Appearance',scope:'This interface',pages:[['appearance','Appearance']]},
 {id:'workspaces',title:'Workspaces',scope:'New workspaces on this host',pages:[['workspaces','Workspaces'],['workspace-starters','Workspace starters']]},
 {id:'voice',title:'Voice',scope:'Future voice connections',pages:[['voice','Voice']]},
 {id:'notifications',title:'Notifications',scope:'This app',pages:[['notifications','Notifications']]},
 {id:'privacy',title:'Privacy & files',scope:'Your work and access choices',pages:[['privacy','Privacy & files'],['access-approvals','Access & approvals'],['permissions','File access'],['desktop','Screen & computer access'],['diagnostics','App diagnostic capture'],['history','Saved work'],['storage','Storage']]},
 {id:'testing',title:'Testing & resources',scope:'Across workspaces on this Amplifier installation',pages:[['testing','Testing & resources']]},
 {id:'updates',title:'Updates',scope:'This Amplifier host',pages:[['updates','Updates']]},
 {id:'advanced',title:'Advanced',scope:'Full configuration',pages:[['advanced','Advanced'],['model-capacity','Model capacity'],['providers','Provider configuration'],['routing','Model rules'],['app-bundles','Configured bundles'],['add-bundles','Discover bundles'],['defaults','Conversation defaults'],['registries','Module & source registries'],['tool-connections','Tool connections'],['custom-appearance','Custom appearance'],['repair','Backup & repair'],['reset','Advanced recovery'],['ready-conversations','Ready conversations'],['automation','Terminal & automation'],['install-app','Install app'],]},
];
export const advancedGroups=[
 {title:'AI configuration',pages:['providers','routing']},
 {title:'Bundles & modules',pages:['app-bundles','add-bundles','defaults','registries']},
 {title:'Tools & appearance',pages:['tool-connections','custom-appearance']},
 {title:'Performance & support',pages:['ready-conversations','repair','reset','automation','install-app']},
];
const legacySections={setup:['overview','ai-connections','advanced','appearance','voice','providers','routing','defaults','conversation','install-app','runtime','custom-appearance'],capabilities:['smart-tools','tool-connections','app-bundles','add-bundles','loaded-modules','registries','share-bundle'],maintenance:['privacy','ready-conversations','updates','diagnostics','history','recall','outputs','publishing','permissions','notifications','automation','repair','reset']};
export function settingsLocation(view={},sections=settingsSections){
 const pages=new Set(sections.flatMap(section=>section.pages.map(([page])=>page)));
 const page=view.panel==='appearance'?'appearance':view.settingsExpanded?.find?.(page=>pages.has(page))||({capabilities:'app-bundles',maintenance:'updates'})[view.settingsSection]||'ai-connections';
 return {page,section:sections.find(section=>section.pages.some(([key])=>key===page))};
}
export function settingsPatch(page,sections=settingsSections){
 if(chatSettingsPages[page])return {panel:chatSettingsPages[page],settingsCanvas:false};
 if(['outputs','publishing'].includes(page))return {panel:'settings',settingsExpanded:[page]};
 if(!sections.some(section=>section.pages.some(([id])=>id===page)))throw new Error('Unknown settings page');
 return {panel:'settings',settingsSection:Object.keys(legacySections).find(key=>legacySections[key].includes(page))||'setup',settingsExpanded:[page]};
}
export function settingsUnread(state,section){return section.pages.reduce((sum,[page])=>sum+(state.attention?.pages?.[page]||0),0);}
export function settingsTitle(page,sections=settingsSections){return sections.flatMap(s=>s.pages).find(([id])=>id===page)?.[1]||'Settings';}
export function settingsParent(page,sections=settingsSections){const section=sections.find(s=>s.pages.some(([id])=>id===page));return ['privacy','advanced','workspaces'].includes(section?.id)&&page!==section.id?section.id:null;}
