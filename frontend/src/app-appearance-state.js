// Presentation only. These classes never authorize, disable, focus or start work.
// Unknown/disconnected activity is not an assertion that the host is idle.
// An open transport alone does not qualify the retained snapshot as current.
export function appAppearanceState(state,connected,baselineReady){
 const known=connected===true&&baselineReady===true&&typeof state?.appActivity?.active==='boolean';
 const activity=known?(state.appActivity.active?'active':'idle'):'unknown';
 const attention=Number.isInteger(state?.attention?.unread)?state.attention.unread>0:false;
 return {
  className:['a-chat-shell','a-approachable-shell',
   activity==='active'?'a-app-active':activity==='idle'?'a-app-idle':'a-app-activity-unknown',
   ...(attention?['a-app-attention']:[]),...(!connected?['a-app-disconnected']:[])].join(' '),
  'data-app-activity':activity,
  'data-app-attention':attention?'unread':'none',
  'data-app-connection':connected?'connected':'disconnected',
 };
}
