/** Read-only routing disclosure, shared with the agent-visible runtime catalog. */
export function delegationRoutingLabel(routing){
 if(!routing)return 'Worker routing has not been reported.';
 const sources={inherited_conversation:'This worker inherited the conversation selection.',delegation_preferences:'This worker uses delegation provider preferences.',agent_provider:'This worker uses its agent provider configuration.',agent_model_role:'This worker uses its agent model role.',session_defaults:'This worker uses its session defaults.'};
 const inherit=routing.modelInheritance==='conversation_when_unspecified'?'Workers inherit the conversation model unless their configuration overrides it.':'Workers use bundle and agent model settings.';
 const source=routing.matrixSource==='user'?' from user settings':routing.matrixSource==='bundle'?' from the bundle':'';
 const resolver=routing.resolverActive?` Routing is active${routing.resolverName?` (${routing.resolverName})`:''}${source}.`:'';
 return `${sources[routing.selectionSource]||inherit}${resolver} Other connected providers may be used.`;
}
