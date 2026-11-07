// Summarize the authoritative sequence; never infer all-current from the app alone.
function verifiedApplicationResult(updates,app){
 const result=updates.applicationResult;
 if(!result||!['restart-ack','restart-reconcile','restart-superseded'].includes(result.phase)||
  !Number.isFinite(result.verifiedAt)||result.verifiedAt<=0||
  !/^[a-f0-9]{32}$/.test(result.attemptId||'')||!/^[a-f0-9]{40}$/.test(result.revision||'')||
  !result.version||result.version!==app.current||result.revision!==app.runningRevision||
  updates.pendingApp||updates.pendingRestart||updates.pendingReplacement!=null)return null;
 return result;
}

function sequenceOverview(updates={},options={}){
 const app=updates.application||(updates.items||[]).find(i=>i.kind==='app')||{},seq=updates.sequence||{};
 const rows=(updates.items||[]).filter(i=>i.kind!=='app'&&i.id!=='application');
 const issues=rows.filter(i=>['check_failed','local_changes'].includes(i.status));
 const components=Math.max(rows.filter(i=>i.status==='update').length,(seq.included?.available||0)+(seq.other?.available||0));
 const appAvailable=app.status==='update',installable=appAvailable||components>0||!!updates.pendingApp||!!updates.pendingRelease;
 const blocked=appAvailable&&app.canInstall===false||updates.pendingReplacement!=null||!!updates.pendingRestart||!!(seq.stage==='included'&&seq.included?.issues);
 const failed=updates.pendingReplacement!=null||!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart?.requestStatus==='rejected'||app.status==='check_failed'||!!(seq.stage==='included'&&seq.included?.issues);
 const active=['checking','staging','validating','activating'].includes(updates.phase);
 // Failed optional attempts retain their recovery controls, not an activation wait.
 const toolRows=updates.pendingSmartTools||[],unfinishedTools=toolRows.filter(row=>!row.failure);
 const toolResultsIndependent=toolRows.every(row=>row.runnerSource||row.failure);
 const pending=!!(updates.pendingApp||updates.pendingRelease||updates.pendingRestart||unfinishedTools.length);
 const runnerPending=!!unfinishedTools.length&&unfinishedTools.every(row=>row.runnerSource);
 const runnerOnlyPending=runnerPending&&!updates.pendingApp&&!updates.pendingRelease&&!updates.pendingRestart;
 const independentUpdate=runnerOnlyPending&&(appAvailable||rows.some(row=>row.status==='update'));
 const blockingPending=pending&&!independentUpdate;
 const waiting=!failed&&!active&&!updates.pendingRestart&&blockingPending&&!(runnerOnlyPending&&seq.nextStage)&&!(updates.pendingRelease&&updates.plan?.activation==='per-worker'&&!updates.pendingApp&&toolResultsIndependent);
 const blockers=updates.blockers||[],chats=new Set(blockers.filter(row=>row.kind==='conversation'&&row.sessionId).map(row=>row.sessionId));
 const stage=seq.nextStage||seq.stage||'application';
 const batch=updates.diagnostics?.batch;
 const currentBatch=batch?.attemptId===updates.diagnostics?.attemptId?batch:null;
 const applicationResult=verifiedApplicationResult(updates,app),failure=updates.diagnostics?.lastFailure;
 // An old failure or a newer app check must not inherit this batch's outcome.
 const componentFailure=failed&&applicationResult&&currentBatch&&
  ['ecosystem','smart-tools'].includes(currentBatch.kind)&&failure?.kind===currentBatch.kind&&
  failure.attemptId===currentBatch.attemptId&&['failed','interrupted'].includes(failure.status)&&
  Number.isFinite(failure.at)&&failure.at>=applicationResult.verifiedAt&&
  failure.at>=(updates.lastAttempt||0)&&app.status!=='check_failed'&&seq.stage!=='application';
 const progress=updates.probeProgress;
 const currentProgress=currentBatch&&progress?.attemptId===currentBatch.attemptId&&updates.phase==='validating'&&
  Number.isInteger(progress.total)&&Number.isInteger(progress.completed)&&progress.total>=0&&progress.completed>=0&&progress.completed<=progress.total?progress:null;
 const result={stage,installable,blocked,runnerOnlyPending,toolResultsIndependent,active,pending,failed,busy:active||(!failed&&(blockingPending||!!seq.nextStage))||updates.pendingReplacement!=null,waiting,processing:!failed&&!waiting&&(active||blockingPending||!!seq.nextStage),continuing:!!seq.install,currentBatch,currentProgress,applicationResult,componentFailure:!!componentFailure,completedBatches:(updates.diagnostics?.completedBatches||[]).slice(-5),title:'Check for updates',detail:'We’ll check Amplifier and the components it uses.',tone:'neutral'};
 if(componentFailure){
  const names=(currentBatch.components||[]).slice(0,5),count=currentBatch.componentCount||names.length;
  const affected=names.length?` Affected batch: ${names.join(', ')}${count>names.length?` and ${count-names.length} more`:''}.`:'';
  const recheck=(failure.reason||failure.probe?.reason)==='runtime-source-changed';
  return {...result,title:'Some components were not updated',tone:'error',detail:
   (applicationResult.phase==='restart-ack'?'The application update is confirmed;':'The current application is verified;')+' the component update needs attention.'+affected+
   (recheck?' Component sources changed after checking. Check for updates again before retrying.':' Review the component issue below before trying again.')};
 }
 if(failed)return {...result,title:'Updates need attention',tone:'error',detail:updates.pendingReplacement!=null?'The installation needs verification before work can continue. Open details for the recovery steps.':updates.pendingRestart?'The app update needs help finishing its restart. Open details for the next step.':seq.included?.issues?'Some required components could not be updated. Your existing configuration is preserved. Review the issue below.':'The update could not finish. Review the issue below before trying again.'};
 if(updates.initialPreparation&&stage!=='complete'&&!waiting&&(active||seq.nextStage||options.autoInstall!==false))return {...result,title:'Getting Amplifier ready',detail:'Preparing and checking components for your first chat. You can set up your AI connection while this finishes.',tone:'working'};
 if(updates.pendingRestart)return {...result,title:updates.pendingRestart.requestStatus==='uncertain'?'Waiting for restart confirmation':'Restarting Amplifier',detail:'Waiting for a healthy restarted app before continuing with the remaining updates.',tone:'working'};
 if(updates.pendingRelease&&updates.plan?.activation==='per-worker'&&!updates.pendingApp&&toolResultsIndependent)return {...result,title:'Finishing your update',detail:'We’re getting the updated components ready for new work. This continues automatically; active chats can keep going.',tone:'working'};
 if(waiting&&runnerOnlyPending)return {...result,title:'Finishing test runner update',detail:updates.pendingSmartTools.some(row=>row.runnerWaiting==='unknown')?'We’re confirming the isolated check’s result before switching runners. Existing chats and checks can continue.':'The updated runner is waiting for machine capacity or completing its isolated check. Existing chats and checks can continue.',tone:'working'};
 if(waiting)return {...result,title:chats.size&&blockers.every(row=>row.kind==='conversation')?`Waiting for ${chats.size} ${chats.size===1?'chat':'chats'} to finish`:'Waiting for your work to finish',detail:updates.pendingApp?'Your update is ready. Amplifier will restart automatically when active work finishes. Your conversations and drafts will be kept.':'Your update is ready. It will continue automatically when active work and calls finish.',tone:'working'};
 if(active){
  const progressDetail=currentProgress?`${currentProgress.phase==='prepare'?'Preparing':'Checking'} configurations: ${currentProgress.completed} of ${currentProgress.total} completed. Failed checks do not count as completed.`:null;
  return {...result,title:updates.phase==='checking'?'Checking for updates':currentBatch?.kind==='smart-tools'?'Updating Smart Tools':stage==='application'?'Updating Amplifier':stage==='included'?'Updating included components':'Updating other components',detail:progressDetail||updates.detail||(updates.phase==='checking'?(stage==='application'?'Checking for a new app version first.':stage==='included'?'Checking included components…':'Checking other components…'):'Preparing and verifying the update…'),tone:'working'};
 }
 if(seq.nextStage||seq.install&&installable)return {...result,title:'Updating Amplifier',detail:stage==='included'?'Continuing with included components…':'Continuing with other components…',tone:'working'};
 if(installable)return {...result,title:'Updates available',detail:(appAvailable?(String(app.current||'').replace(/^v/,'')===String(app.latest||'').replace(/^v/,'')?'App component updates are ready. ':'A new app version is ready. '):'')+(components?`${components} component ${components===1?'update is':'updates are'} ${appAvailable?'also ':''}ready. `:'')+(options.autoInstall!==false?'Component updates prepare automatically. App restarts wait for work to finish.':'One update request handles the app and all eligible components.'),tone:'available'};
 if(toolRows.some(row=>row.failure))return {...result,title:'Some tools were not updated',detail:'Their current versions remain installed. Review each failed tool below to retry or skip it; other updates can continue.',tone:'error'};
 if(issues.length||seq.included?.issues||seq.other?.issues)return {...result,title:'Some updates need attention',detail:'No installable updates remain, but some checks or local changes need review.',tone:'error'};
 if(seq.stage==='complete')return {...result,title:updates.adoption?.pendingWorkers>0?'Ready for new work':'You’re up to date',detail:updates.adoption?.pendingWorkers>0?'New work uses the updated components. Running conversations switch after their current work finishes. No app restart is needed.':'Amplifier and its eligible components are up to date.',tone:'ready'};
 if(app.status==='release_channel_needed')return {...result,title:'Automatic app updates aren’t configured',detail:'This installation needs a release channel. Component updates and technical details are available below.'};
 if(updates.lastCheck)return {...result,title:'App checked · components not fully checked',detail:'Run a check to confirm the full update status.'};
 return result;
}

// This is the presentation owner for both the overall operation and the app row.
// Actionable attention comes from the host's recovery owner; rendering never
// settles an occurrence or infers recovery from a successful unrelated check.
export function updateOverview(updates={},options={},attentionItems=[]){
 const overview=sequenceOverview(updates,options);
 const app=updates.application||(updates.items||[]).find(item=>item.kind==='app')||{};
 const replacement=updates.pendingReplacement!=null;
 const restartFailed=!!updates.pendingRestart&&(!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart.requestStatus==='rejected');
 const appState=replacement?'failed':updates.pendingRestart?(restartFailed?'failed':updates.pendingRestart.requestStatus==='uncertain'?'restart_pending':'restarting'):updates.pendingApp?(updates.error?'failed':'staged'):app.status==='check_failed'?'failed':app.releaseBehind?'ahead':app.status||'not_checked';
 const appLabels={restarting:'Restarting',restart_pending:'Awaiting restarted host',staged:'Ready to restart',update:'Update available',current:'Latest release installed',failed:'Needs attention',ahead:'Ahead of published release',not_checked:'Not checked',release_channel_needed:'Release channel not configured',historical:'Older saved configuration'};
 const appDetail=replacement?(updates.error||updates.detail||'The installation outcome is unknown. Work remains paused until the qualified app and dependencies are verified on a new host.'):updates.pendingRestart?(updates.error||updates.detail||(restartFailed?'The app installed, but its restart did not complete. Restart Amplifier Unified manually to continue.':'The app is installed. Waiting for a healthy restarted host before resuming work.')):updates.pendingApp?updates.error||'The app passed validation and will restart when conversations, worker lanes, smart tools, and calls are idle.':app.detail;
 const notice={phase:restartFailed||updates.error||['error','interrupted'].includes(updates.phase)?'error':updates.pendingRestart||overview.active||replacement?'working':updates.phase==='installed'?'success':'neutral',
  message:updates.error||(['error','interrupted'].includes(updates.phase)?updates.detail||'The update did not finish.':overview.active||replacement||overview.pending||updates.phase==='installed'?updates.detail:'')};
 const attention=attentionItems.filter(item=>item.id==='updates:current'&&item.actionRequired);
 const result={...overview,attention,application:{state:appState,label:appLabels[appState]||appState,detail:appDetail},notice};
 // The bundled host owns current health. Historical receipts and read badges
 // cannot override it or independently classify the installation.
 const current=updates.currentStatus;
 if(current&&(!overview.active&&!overview.pending||current.actionRequired)){
  const tone={current:'ready',working:'working',waiting:'working',retrying:'working',
   available:'available',protected:'error','action-required':'error',unverified:'error',unchecked:'neutral'}[current.status]||'neutral';
  return {...result,title:current.title,detail:current.detail,tone,current,
   failed:!!current.actionRequired,busy:overview.busy,processing:!current.actionRequired&&['working','retrying'].includes(current.status)};
 }
 if(overview.tone==='working'&&overview.currentProgress&&!updates.initialPreparation){
  return {...result,title:'Finishing your update',detail:'We’re preparing and checking the updated components. This continues automatically.'};
 }
 return result;
}

export function updateBatchLabel(batch){
 const names=(batch.components||[]).slice(0,5),count=batch.componentCount||names.length;
 return (names.join(', ')||batch.kind)+(count>names.length?` and ${count-names.length} more`:'');
}
