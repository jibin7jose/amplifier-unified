const duration=seconds=>seconds<60?`${Math.round(seconds)}s`:`${Math.ceil(seconds/60)} min`;
export function durationHint(session,now=Date.now()){
 const hint=session?.turnDurationHint;
 if(!hint||hint.version!==1||hint.basis!=='recent_similar_turns'||hint.scope!=='turn'||hint.sampleCount<5||
  ![hint.startedAt,hint.lowerSeconds,hint.upperSeconds].every(Number.isFinite)||
  !['working','running','busy'].includes(session.status)||
  ['capacity','workers','waiting-workers','stopping','interrupted','approval','question'].includes(session.activity?.phase)||
  session.workers?.some(row=>['running','working','pending','queued','starting','retrying'].includes(row.status))||
  session.approvals?.length||session.questions?.some(row=>row.status==='pending'))return null;
 const elapsed=now/1000-hint.startedAt;
 return {text:elapsed>hint.upperSeconds?'Taking longer than recent similar turns':`Estimated turn: ${duration(hint.lowerSeconds)}–${duration(hint.upperSeconds)}`,
  detail:`Based on ${hint.sampleCount} recent completed turns in this chat with the same connection, model, effort and request-size bucket. A historical range, not a deadline or an estimate for the whole task. New tools and delegated work can take longer.`};
}
