import React from 'react';
import {AlertCircle,Check,ChevronDown,ChevronRight,Clock3} from 'lucide-react';
const phaseLabels={'candidate-install':'Prepare application','candidate-probe':'Check candidate package','candidate-version':'Check candidate version','candidate-record':'Save candidate validation','target-discovery':'Find active installation','runtime-close':'Stop idle runtimes','recovery-record':'Save recovery reference','replacement-install':'Install application','replacement-probe':'Check installed package','replacement-version':'Check installed version','restart-configuration':'Prepare restart settings','restart-helper-file':'Prepare restart helper','restart-helper':'Start restart helper','service-restart':'Restart system service','service-restart-request':'Request service restart','restart-request':'Request restart','restart-readiness':'Check restarted host readiness','restart-ack':'Confirm restarted version','restart-reconcile':'Verify current installation','activation-validation':'Check staged release','ecosystem-copy':'Prepare ecosystem copy','ecosystem-source-preflight':'Check cached source changes','ecosystem-runtime-preflight':'Check installed worker sources','ecosystem-runtime-lock':'Resolve worker dependencies','ecosystem-runtime-policy':'Prepare worker installer policy','ecosystem-runtime-freeze':'Record qualified worker','ecosystem-runtime-freeze-install':'Verify recorded worker','ecosystem-validation':'Validate ecosystem','ecosystem-fetch':'Fetch source update','ecosystem-checkout':'Switch staged source','ecosystem-probe':'Check bundles and modules','ecosystem-prepare':'Prepare bundle dependencies','ecosystem-compatibility':'Check frozen bundle compatibility','smart-tools-partial':'Healthy Smart Tools updated','smart-tool-stage':'Prepare Smart Tool update','smart-tool-activate':'Verify and activate Smart Tool','ecosystem-activation':'Activate ecosystem','ecosystem-stage':'Prepare ecosystem','stage':'Prepare application update'};
const recoveryGuidance={'runtime-installed-source-mismatch':'A component changed to a different source during update preparation. Amplifier kept the current components active. Check for an app update before retrying; if this happens again, share the update details.','smart-tool-startup-failed':'The updated Smart Tool could not complete its configured connection check. Review its required dependencies and connection settings.','smart-tool-update-failed':'This Smart Tool could not finish updating. Other ready tools can continue.','missing-configured-environment':'A saved configuration refers to an environment value that is missing. Review AI connections and configured environment values before retrying.','incompatible-conversation-loop':'A configured conversation profile uses an unsupported orchestrator. Choose a profile using loop-streaming or loop-live in Bundles & modules.','host-component-conflict':'App behavior module identities conflict. Review duplicate module declarations and instance IDs in Bundles & modules; the current ecosystem is preserved.','module-prepare-failed':'A module could not be prepared in the new worker. The current ecosystem is preserved; review its dependencies before retrying.','bundle-not-found':'A configured bundle could not be found. Review its registration in Bundles & modules.','bundle-load-failed':'A configured bundle could not be loaded. Review its source and dependencies in Bundles & modules.','bundle-validation-failed':'A configured bundle did not pass validation. Review its configuration before retrying.','bundle-dependency-failed':'A bundle dependency could not be prepared. Review its source and dependency configuration.','protected-runtime-source':'Local source changes or an explicit dependency override were preserved. Review this module in Bundles & modules and preserve its source configuration before checking for updates again.','runtime-source-changed':'Worker sources changed while checking. Let source changes finish, then check for updates again.'};
recoveryGuidance['runtime-source-constraint']='A package source declaration could not be reconciled with the selected component. The current components remain active. This does not establish local edits. Share the update diagnostics for review; do not remove pins or caches to bypass this check.';
const statuses={started:'Running',accepted:'Accepted',rejected:'Rejected',uncertain:'Unconfirmed',succeeded:'Succeeded',failed:'Failed',interrupted:'Interrupted'};
const failedStatuses=['failed','interrupted','rejected'];
const label=phase=>phaseLabels[phase]||phase?.replaceAll('-',' ')||'Update';
export function diagnosticReceipt(diagnostics,reconciliation){
 const fields=['id','at','attemptId','kind','phase','status','revision','commandId','durationMs','exitCode','stdoutBytes','stderrBytes','errorType','timedOut','expectedVersion','observedVersion','expectedRevision','observedRevision','reason','package','component','installationId','targetInstallationId','osError','failureLocation'];
 const clean=event=>{
  if(!event)return undefined;
  const result=Object.fromEntries(fields.filter(key=>event[key]!==undefined).map(key=>[key,event[key]]));
  if(Array.isArray(event.resolutionMismatches))result.resolutionMismatches=[...new Set(event.resolutionMismatches.filter(value=>['source','repository','ref','subdirectory','revision'].includes(value)))].sort();
  if(event.probe)result.probe=Object.fromEntries(['ok','isolated','packageInEnvironment','frontendPresent','loginAvailable','standalone','providersPresent','cliAbsent','dependenciesPrepared','stage','errorType','version','pythonVersion','reason','package','failureLocation','osError','profileIndex'].filter(key=>event.probe[key]!==undefined).map(key=>[key,event.probe[key]]));
  return result;
 };
 const verified=reconciliation?Object.fromEntries(['attemptId','version','revision','verifiedAt'].filter(key=>reconciliation[key]!==undefined).map(key=>[key,reconciliation[key]])):undefined;
 const failureDispositions=(diagnostics.failureDispositions||[]).slice(-20).map(row=>({id:row.id,at:row.at,target:row.target,occurrence:row.occurrence,occurrences:row.occurrences,status:'acknowledged-uncertainty',recoveryVerified:false,firstFailure:clean(row.firstFailure),latestFailure:clean(row.latestFailure)}));
 return JSON.stringify({attemptId:diagnostics.attemptId,lastFailure:clean(diagnostics.lastFailure),reconciliation:verified,events:(diagnostics.events||[]).slice(-50).map(clean),failureDispositions},null,2);
}
export function reconciledFailure(updates){
 const receipt=updates.reconciliation,failure=updates.diagnostics?.lastFailure;
 const application=updates.application||(updates.items||[]).find(item=>item.kind==='app')||{};
 return !!(failure?.attemptId&&receipt?.attemptId===failure.attemptId&&Number.isFinite(receipt.verifiedAt)&&receipt.verifiedAt>0&&
  typeof receipt.version==='string'&&receipt.version===application.current&&
  (!application.runningRevision||receipt.revision===application.runningRevision)&&
  !updates.error&&!updates.pendingRestart&&!updates.pendingApp&&updates.pendingReplacement==null);
}
function facts(event){
 const parts=[];
 if(event.component||event.installationId)parts.push(event.component||event.installationId);
 if(event.package||event.probe?.package)parts.push(event.package||event.probe.package);
 if(event.osError||event.probe?.osError)parts.push(event.osError||event.probe.osError);
 if(recoveryGuidance[event.reason||event.probe?.reason])parts.push(recoveryGuidance[event.reason||event.probe?.reason]);
 if(event.probe?.errorType)parts.push(event.probe.errorType);
 else if(event.errorType)parts.push(event.errorType);
 if(event.probe?.stage&&event.probe.stage!=='complete')parts.push('Probe: '+event.probe.stage);
 if(event.probe?.profileIndex)parts.push('Conversation profile '+event.probe.profileIndex);
 if(event.timedOut)parts.push('Timed out');
 if(event.exitCode!==undefined)parts.push('Exit '+event.exitCode);
 if(event.expectedVersion)parts.push('Expected '+event.expectedVersion);
 if(event.observedVersion)parts.push('Found '+event.observedVersion);
 if(event.expectedRevision)parts.push('Expected revision '+event.expectedRevision.slice(0,12));
 if(event.observedRevision)parts.push('Found revision '+event.observedRevision.slice(0,12));
 if(event.durationMs!==undefined)parts.push(event.durationMs<1000?event.durationMs+' ms':(event.durationMs/1000).toFixed(1)+' s');
 return parts.join(' · ');
}
export function UpdateIssueSummary({state}){
 const updates=state.updates||{},failure=updates.diagnostics?.lastFailure;
 if(!failure||reconciledFailure(updates))return null;
 const prior=failure.attemptId&&updates.diagnostics?.attemptId&&failure.attemptId!==updates.diagnostics.attemptId;
 if(prior&&!updates.error&&['checking','staging','validating','staged','activating'].includes(updates.phase))return <p className="a-caption" role="status">A previous attempt failed. This is a different update attempt; its progress is shown above. The earlier failure remains in update details.</p>;
 return <div className="a-update-failure" role="alert"><AlertCircle aria-hidden="true"/><div><strong>Last update did not finish: {label(failure.phase)}</strong><p>{failure.phase==='target-discovery'?'This installation cannot be replaced by the in-app updater. Ask the person who runs Amplifier to update this installation.':facts(failure)||'Open Component updates & details for the failure receipt.'}</p></div></div>;
}
export function UpdateDiagnostics({state,act}){
 const updates=state.updates||{},diagnostics=updates.diagnostics||{},failure=diagnostics.lastFailure,events=diagnostics.events||[];
 if(!events.length&&!failure)return null;
 const expanded=!!state.view?.maintenanceDraft?.updateDiagnosticsExpanded;
 const selected=events.filter(event=>event.attemptId===diagnostics.attemptId);
 const rows=selected.filter(event=>event.status!=='started'||!selected.some(later=>later.commandId&&later.commandId===event.commandId&&later.status!=='started'));
 const resolved=reconciledFailure(updates);
 const firstFailure=events.find(event=>event.attemptId===(failure?.attemptId||diagnostics.attemptId)&&failedStatuses.includes(event.status))||failure;
 const FailureIcon=resolved?Check:AlertCircle;
 return <div className="a-update-diagnostics" data-part="update-diagnostics">
  {failure&&<div className={'a-update-failure'+(resolved?' resolved':'')} role="status"><FailureIcon aria-hidden="true"/><div><strong>{resolved?'Previous update issue resolved':'Last update issue'}: {label(failure.phase)}</strong><p>{resolved?'The current installation is verified healthy. The original failure remains in the receipt for reference.':facts(failure)||'The phase did not finish. Review the receipt before retrying.'}</p><span className="a-caption">Attempt <code>{failure.attemptId||'Not recorded'}</code></span></div></div>}
  <button type="button" className="a-link a-update-diagnostics-toggle" data-action="view.update" aria-expanded={expanded} onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updateDiagnosticsExpanded:!expanded}}})}>{expanded?<ChevronDown/>:<ChevronRight/>}{expanded?'Hide update details':'View update details'}</button>
  {expanded&&<div className="a-update-diagnostic-details">
   {firstFailure&&firstFailure.id!==failure?.id&&<p className="a-caption">{resolved?'First historical issue':'First recorded issue'}: {label(firstFailure.phase)} · {facts(firstFailure)}</p>}
   {diagnostics.attemptId&&<p className="a-caption">Latest attempt <code>{diagnostics.attemptId}</code></p>}
   <ol className="a-update-phase-list">{rows.map(event=>{const failed=failedStatuses.includes(event.status),historical=resolved&&failed&&event.attemptId===failure.attemptId,Icon=event.status==='succeeded'?Check:failed?AlertCircle:Clock3;return <li key={event.id} className={event.status+(historical?' historical':'')}><div><Icon aria-hidden="true"/><span>{label(event.phase)}</span><span className="a-update-phase-status">{statuses[event.status]||event.status}{historical?' (historical)':''}</span></div>{facts(event)&&<p className="a-caption">{facts(event)}</p>}</li>})}</ol>
   <label className="a-caption" htmlFor="update-diagnostic-receipt">Sanitized receipt — select and copy to share</label><textarea id="update-diagnostic-receipt" className="a-update-receipt" readOnly rows={5} spellCheck={false} value={diagnosticReceipt(diagnostics,updates.reconciliation)}/>
   <p className="a-caption">Includes phase, timing, exit code and probe results. Command output, credentials, environment values and local paths are excluded.</p>
  </div>}
 </div>;
}
