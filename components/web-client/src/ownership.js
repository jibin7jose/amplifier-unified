/** Ownership is reported by the host; opening a view never requests takeover. */
export function ownershipState(session) {
  // History eligibility is independent of a previous writer's lock. An old
  // ownership notice must not offer takeover of a child as a root conversation.
  if(session?.historyReadOnlyReason&&['blocked','yielded'].includes(session?.ownership?.status))return {blocked:true,canTakeover:false,
    label:session.sessionKind==='worker'?'Worker history':'Read-only history',
    detail:session.historyReadOnlyReason};
  const state=session?.ownership, source=state?.source||session?.lockOwner?.app||'another application';
  switch(state?.status){
    case 'blocked': return state.reason==='takeover-failed'
      ?{blocked:true,canTakeover:true,retry:true,label:'Could not continue here',detail:state.detail||'The takeover did not complete. Your draft is kept. Try again to unlock the message box.'}
      :{blocked:true,canTakeover:true,label:`In use by ${source}`,detail:state.detail||(state.supportsTakeover===false?'This running interface does not support takeover requests. Close it, then choose Continue here.':'Continue here to take over and unlock the message box. Your draft is kept.')};
    case 'yielded': return {blocked:true,canTakeover:true,label:`Continued in ${source}`,detail:'This view stays connected. Choose Continue here when you want to resume.'};
    case 'yielding': return {blocked:true,canTakeover:false,label:`Moving to ${source}`,detail:'Finishing current work and saving the session…'};
    case 'taking-over': return {blocked:true,canTakeover:false,label:'Taking over…',detail:'Waiting for the current owner to finish, then loading your session.'};
    case 'yield-failed': return {blocked:true,canTakeover:false,label:'The session could not be released',detail:state.detail||'Saving or cleanup needs attention. Ownership is retained.'};
    default:return {blocked:false,canTakeover:false};
  }
}

// Ownership conflicts already have a conversation-scoped action and notice.
export function actionErrorMessage(error){return error.code==='session_busy'?'':error.message}
