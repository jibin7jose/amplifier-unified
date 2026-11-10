import {feedbackDiagnostics} from './feedback-diagnostics.js';

// Capture what was displayed when the user clicked, before collecting the
// server report can reconcile state. Never include raw state, details or URLs.
export function updateClientDiagnostics(state,environment=globalThis){
 const updates=state?.updates||{},sequence=updates.sequence||{};
 const phase=['idle','checking','available','checked','staging','validating','staged','app-staged','activating','installed','error','interrupted'].includes(updates.phase)?updates.phase:'unknown';
 const stage=value=>['application','included','other','complete'].includes(value)?value:null;
 const result={capturedAt:Date.now()/1000,...feedbackDiagnostics(state,environment),
  updates:{phase,stage:stage(sequence.stage),nextStage:stage(sequence.nextStage),installRequested:sequence.install===true,hasError:!!updates.error}};
 for(const [name,value] of Object.entries({revision:state?.revision,lastCheck:updates.lastCheck,lastAttempt:updates.lastAttempt,available:updates.available})){
  if(typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<1e15)result.updates[name]=value;
 }
 return result;
}
