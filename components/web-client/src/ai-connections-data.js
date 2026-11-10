import providerSources from '../.build-inputs/amplifier_web/provider_sources.json' with {type:'json'};
import services from '../.build-inputs/amplifier_web/ai_services.json' with {type:'json'};
// Shared with the semantic Settings actions on the host.
export const aiServices=services;
// Canonical known modules in microsoft/amplifier-app-cli/provider_sources.py.
const providerLabels=[...aiServices,
 {module:'provider-azure-openai',name:'Azure OpenAI'},
 {module:'provider-ollama',name:'Ollama'},
 {module:'provider-vllm',name:'vLLM'},
];
export const knownProviders=Object.keys(providerSources).map(module=>({...providerLabels.find(row=>row.module===module),module,source:providerSources[module]}));
export function aiService(module){return aiServices.find(s=>s.module===module)||{module,name:module?.replace(/^provider-/,'')||'AI service',auth:'key'};}
export function setupOperation(state,action,id){return state.setup?.operations?.[action+':'+id];}
export function setupPending(operation){return ['working','pending','queued'].includes(operation?.phase);}
export function matchingSetupOperation(state,pending){if(!pending)return null;const op=setupOperation(state,pending.action,pending.id);return op?.commandId===pending.commandId?op:null;}
