import React from 'react';

const reasons={
 'managed-runner-qualification-failed':'The updated test runner did not finish its isolated check. Existing checks keep their original runner and retained evidence.',
 'managed-runner-selection-changed':'The selected test runner changed while this version was checked. The newer selection is unchanged.',
 'managed-runner-package-changed':'The candidate’s installed files changed. It was not selected; the current runner is unchanged.',
 'smart-tool-startup-failed':'The new version could not start its configured connection. Its existing connection is unchanged.',
 'missing-configured-environment':'A configured connection credential is unavailable. Check the tool’s connection settings before retrying.',
 'smart-tool-update-failed':'This tool could not finish updating. Review its diagnostic receipt before retrying.'
};

export function SmartToolUpdateResults({updates,act,busy}){
 const failures=(updates.pendingSmartTools||[]).filter(item=>item.failure);
 const waiting=(updates.pendingSmartTools||[]).filter(item=>item.runnerUpdate&&!item.failure);
 if(!failures.length&&!waiting.length)return null;
 const completed=[...new Set((updates.diagnostics?.events||[]).filter(event=>event.attemptId===updates.diagnostics?.attemptId&&event.phase==='smart-tool-activate'&&event.status==='succeeded').map(event=>event.component||event.installationId).filter(Boolean))];
 return <section aria-label="Smart Tool update results" data-part="smart-tool-update-results">
  <h4>Smart Tool updates</h4>
  {!!completed.length&&<p>Updated: {completed.join(', ')}.</p>}
  {waiting.map(item=><p key={item.resultId||item.previous} role="status">{item.runnerWaiting==='unknown'?'The test runner check has an unconfirmed result. Its original work and evidence are retained while confirmation is checked.':'The updated test runner is waiting for capacity or completing its isolated check.'} Existing chats and checks can continue.</p>)}
  <ul>{failures.map(item=><li key={item.resultId||item.previous}>
   <strong>{item.runnerSource?'Test runner update':item.component||item.previous}</strong>
   <p>{reasons[item.failure.reason]||reasons['smart-tool-update-failed']}</p>
   <div className="a-dialog-actions">
    <button type="button" className="a-soft" disabled={busy} data-action="updates.smartToolRetry" onClick={()=>act('updates.smartToolRetry',{id:item.resultId||item.previous})}>Retry this tool</button>
    <button type="button" className="a-soft" disabled={busy} data-action="updates.smartToolSkip" onClick={()=>act('updates.smartToolSkip',{id:item.resultId||item.previous})}>Skip this version</button>
   </div>
  </li>)}</ul>
 </section>;
}
