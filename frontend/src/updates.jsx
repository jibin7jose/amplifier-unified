import {AppReloadNotice} from './app-reload.jsx';
import {AttentionReview,ReadWhenVisible,useReadVisible,FailureDisposition,FailureDispositions} from './attention';
import {updateOverview} from './update-overview';
import {ActivityRegion} from './activity-region';
import {useListFilter} from './list-filter.jsx';
import {ResultNotice} from './settings-ui';
import React from 'react';
import {RefreshCw,Download,Undo2,Check,ArrowUpCircle,AlertCircle,Pin,Clock3,LoaderCircle} from 'lucide-react';
import './updates.css';
import {UpdateDiagnostics,UpdateIssueSummary} from './update-diagnostics.jsx';
import {UpdateSupport} from './update-support.jsx';
import {UpdateRestart} from './update-restart.jsx';
import {SmartToolUpdateResults} from './smart-tool-update-results.jsx';
import {ReleaseHistory,ReleaseNotices,UpdateReleaseSummary} from './release-notes.jsx';
const sourceName=row=>row.label.split('/').pop().replace(/^amplifier-(bundle-|module-)/,'');
const alphabetical=(a,b)=>sourceName(a).localeCompare(sourceName(b),undefined,{sensitivity:'base',numeric:true})||a.label.localeCompare(b.label)||String(a.ref||'').localeCompare(String(b.ref||''))||String(a.current||'').localeCompare(String(b.current||''));
const labels={skipped:'This version was skipped',update:'Update available',current:'Current',pinned:'Fixed version (kept as configured)',check_failed:'Check failed',local_changes:'Local changes',not_checked:'Not checked',release_channel_needed:'Release channel not configured',historical:'Older saved configuration'};
function SourceList({items,state,act,id}){
 const [shown,filter,query]=useListFilter(state,act,id,[...items].sort(alphabetical),row=>[row.label,row.id,row.kind,row.status,row.ref,row.detail,row.usage,...(row.usageEvidence||[]),...(row.members||[]).flatMap(member=>[member.package,member.subdirectory,member.kind])],'Filter update sources');
 return <>{(items.length>6||query)&&filter}<ul id={id+'-list'} className="a-update-list a-source-list">{shown.map(item=>{
 const issue=['check_failed','local_changes'].includes(item.status),Icon=item.status==='historical'?Clock3:issue?AlertCircle:item.status==='update'?ArrowUpCircle:item.status==='pinned'?Pin:Check;
 const unread=state.attention?.items?.some(i=>i.id==='source:'+item.id&&!i.read);
 return <li key={item.id}><div className="a-source-line"><Icon className={issue?'a-source-warning':'a-source-status'} aria-label={labels[item.status]||item.status}><title>{item.detail||labels[item.status]||item.status}</title></Icon>{unread&&<span className="a-unread-dot" aria-label="Unread"/>}<ReadWhenVisible item={state.attention?.items?.find(i=>i.id==='source:'+item.id)} act={act}><strong title={item.label}>{sourceName(item)}</strong></ReadWhenVisible>{item.cacheCopies>1&&<span title="Source cache copies">×{item.cacheCopies}</span>}<span className="a-source-ref">{item.ref||item.kind}</span><code title={item.current&&item.latest?item.current+' → '+item.latest:undefined}>{item.current?.slice(0,7)}{item.latest&&item.latest!==item.current?' → '+item.latest.slice(0,7):''}</code></div>{(item.members?.length>1||item.package)&&<details className="a-source-roles"><summary>{item.packageCount?`${item.packageCount} package${item.packageCount===1?'':'s'} · `:''}Source details</summary><p className="a-caption">{item.label}</p><ul>{(item.members||[item]).map(member=><li key={member.id}><strong>{member.package||'Source cache'}</strong>{member.subdirectory&&<code>{member.subdirectory}</code>}<span>{member.kind}{member.usage==='unknown'?' · Usage unknown':''}</span></li>)}</ul></details>}{item.usage&&<p className="a-caption">{item.usage==='configured'?'Configured: '+(item.usageEvidence||[]).join(', '):item.usage==='installed'?'Installed Smart Tool.':'Usage unknown; it may still be needed.'}</p>}{(issue||item.kind==='history'||item.runnerKind)&&<ResultNotice phase={item.status==='check_failed'?'error':'neutral'} message={item.detail||labels[item.status]}/>}{item.sourceIssues?.length>0&&<ul>{item.sourceIssues.map((entry,index)=><li key={index} style={{overflowWrap:'anywhere'}}><strong>{entry.reference||'Settings'}</strong><p>{entry.reason}</p>{entry.workspace&&<p>{entry.workspace}</p>}{entry.sessionId&&<p>Session ID: <code>{entry.sessionId}</code></p>}{entry.appSessionId&&<button type="button" className="a-soft" data-action="session.select" onClick={async()=>{const result=await act('session.select',{id:entry.appSessionId});if(result&&result.accepted!==false)await act('view.update',{patch:{panel:null}})}}>Open conversation</button>}</li>)}</ul>}</li>;
 })}</ul></>;
}
function ComponentSummary({updates,items,busy}){
 const sequence=updates.sequence||{};
 return <div className="a-update-summary" aria-label="Component update summary">{[['included','Included components'],['other','Other components']].map(([tier,label])=>{
  const rows=items.filter(row=>(row.updateTier||'other')===tier);
  const summary=sequence[tier]||{status:updates.lastCheck?'current':'waiting',available:rows.filter(row=>row.status==='update').length,issues:rows.filter(row=>['check_failed','local_changes'].includes(row.status)).length};
  const checking=busy&&updates.phase==='checking'&&sequence.stage===tier;
  const text=checking?'Checking…':summary.available?`${summary.available} available${summary.missing?` · ${summary.missing} to install`:''}${summary.issues?` · ${summary.issues} need attention`:''}`:summary.status==='waiting'?(sequence.stage==='application'?'After app update':tier==='other'?'After included components':'Not checked'):summary.issues?`${summary.issues} need attention`:summary.protected?`${summary.protected} kept as configured`:'Up to date';
  return <div key={tier}><span>{label}</span><strong>{text}</strong></div>;
 })}</div>;
}
export function UpdateSettings({state,act}){
 const unread=(state.attention?.items||[]).filter(item=>item.page==='updates'&&!item.read),releaseUnread=unread.filter(item=>item.id.startsWith('release-notice:')),componentUnread=unread.filter(item=>!item.id.startsWith('release-notice:'));
 const unresolved=(state.attention?.items||[]).filter(item=>item.page==='updates'&&item.actionRequired);
 const updates=state.updates||{},options=state.settings?.updates||{},replacement=updates.pendingReplacement!=null,busy=replacement||['checking','staging','validating','activating'].includes(updates.phase);
 const intervalHours=options.intervalHours??4,legacyInterval=![1,4,8,24].includes(intervalHours);
 const application=updates.application||(updates.items||[]).find(item=>item.kind==='app')||{};
 const items=(updates.items||[]).filter(item=>item.kind!=='app'&&item.id!=='application');
 const available=items.filter(item=>item.status==='update').sort(alphabetical),issues=items.filter(item=>['check_failed','local_changes'].includes(item.status));
 const unknown=items.filter(item=>item.usage==='unknown'),configuredIssues=issues.filter(item=>item.usage!=='unknown');
 const unknownIssues=unknown.filter(item=>['check_failed','local_changes'].includes(item.status));
 const unknownSummary=[['check_failed','check failed'],['local_changes','with local changes'],['update','with updates'],['pinned','pinned'],['current','current'],['not_checked','not checked']].map(([status,label])=>[unknown.filter(item=>item.status===status).length,label]).filter(([count])=>count).map(([count,label])=>`${count} ${label}`).join(', ');
 const appAvailable=application.status==='update',pending=updates.pendingApp||updates.pendingRelease||updates.pendingRestart||updates.pendingSmartTools?.length;
 const restartFailed=!!updates.pendingRestart&&(!!updates.error||['error','interrupted'].includes(updates.phase)||updates.pendingRestart.requestStatus==='rejected');
 const restartUncertain=updates.pendingRestart?.requestStatus==='uncertain';
 const appState=replacement?'failed':updates.pendingRestart?(restartFailed?'failed':restartUncertain?'restart_pending':'restarting'):updates.pendingApp?(updates.error?'failed':'staged'):application.status==='check_failed'?'failed':application.releaseBehind?'ahead':application.status||'not_checked';
 const appLabels={restarting:'Restarting',restart_pending:'Awaiting restarted host',staged:'Ready to restart',update:'Update available',current:'Latest release installed',failed:'Needs attention',ahead:'Ahead of published release',not_checked:'Not checked',release_channel_needed:'Release channel not configured',historical:'Older saved configuration'};
 const AppIcon=appState==='current'?Check:appState==='update'?ArrowUpCircle:appState==='failed'?AlertCircle:Clock3;
 const appDetail=replacement?(updates.error||updates.detail||'The installation outcome is unknown. Work remains paused until the qualified app and dependencies are verified on a new host.'):updates.pendingRestart?(updates.error||updates.detail||(restartFailed?'The app installed, but its restart did not complete. Restart Amplifier Unified manually to continue.':'The app is installed. Waiting for a healthy restarted host before resuming work.')):updates.pendingApp?updates.error||'The app passed validation and will restart when conversations, worker lanes, smart tools, and calls are idle.':application.detail;

 const resultPhase=restartFailed||updates.error||['error','interrupted'].includes(updates.phase)?'error':updates.pendingRestart||busy?'working':updates.phase==='installed'?'success':'neutral';
 const resultMessage=updates.error||(['error','interrupted'].includes(updates.phase)?updates.detail||'The update did not finish.':busy||pending||updates.phase==='installed'?updates.detail:'');
 const overview=updateOverview(updates,options);
 const statusRef=React.useRef(null);useReadVisible(unread.filter(item=>item.id==='updates:pending'||item.id==='source:application'),act,statusRef);
 const expanded=!!state.view?.maintenanceDraft?.updatesExpanded;
 const controlsBusy=busy||overview.busy,disabledReason=controlsBusy?overview.detail:pending?overview.detail:overview.blocked?'App updates for this development preview are managed by its owner.':'';
 const canRefreshQueued=!!updates.pendingApp&&!updates.pendingApp.featureSelection&&!updates.pendingRelease&&(!updates.pendingSmartTools?.length||updates.pendingSmartTools.every(row=>row.runnerSource))&&!updates.pendingRestart&&!replacement&&!busy;
 const canRefreshRunner=overview.runnerOnlyPending&&!replacement&&!busy;
 const checkDisabled=!!updates.applicationCheckPending||(!canRefreshQueued&&!canRefreshRunner&&(controlsBusy||!!pending));
 const summarizeProgress=overview.tone==='working'&&overview.currentProgress&&!updates.initialPreparation;
 const persistentFailure=unresolved[0]&&!busy&&!pending;
 const simpleDetail=persistentFailure?unresolved[0].detail:summarizeProgress?'We’re preparing and checking the updated components. This continues automatically.':overview.detail;
 const simpleTitle=persistentFailure?unresolved[0].title:summarizeProgress?'Finishing your update':overview.title;
 const change=patch=>act('settings.update',{patch:{updates:patch}});
 return <ActivityRegion as="section" name="updates" busy={overview.processing} className="a-updates" data-part="updates">
  <div ref={statusRef} className="a-app-update a-update-quiet" data-part="application-update" aria-label="Overall update status" role="status" aria-live="polite">
   <div className="a-update-overall">{persistentFailure||overview.tone==='error'?<AlertCircle/>:overview.tone==='ready'?<Check/>:overview.tone==='available'?<ArrowUpCircle/>:overview.processing?<LoaderCircle className="a-progress-spinner" aria-hidden="true"/>:<Clock3/>}<div><h4>{simpleTitle}</h4><p id="update-action-status">{simpleDetail}</p></div></div>
   <p className="a-update-version-line">Amplifier {application.current||'version not reported'}{application.latest&&application.latest.replace(/^v/,'')!==application.current?.replace(/^v/,'')?' · Latest '+application.latest:''}{updates.lastCheck?' · Checked '+new Date(updates.lastCheck*1000).toLocaleString():''}</p>
   {overview.applicationResult&&<p className="a-caption" data-part="confirmed-application-result">{overview.applicationResult.phase==='restart-ack'?'Application updated successfully':'Current application verified'} · {overview.applicationResult.version} · Confirmed {new Date(overview.applicationResult.verifiedAt*1000).toLocaleString()}</p>}
  </div>
  <UpdateReleaseSummary updates={updates} application={application} overview={overview}/>
   {overview.waiting&&!overview.runnerOnlyPending&&!!updates.blockers?.length&&<details className="a-update-work" data-part="update-blockers"><summary>View active work ({updates.blockers.length})</summary><ul>{updates.blockers.map((item,index)=><li key={index}>{item.label}{item.sessionId&&<> — <button type="button" className="a-link" data-action="session.select" onClick={async()=>{const result=await act('session.select',{id:item.sessionId});if(result&&result.accepted!==false)await act('view.update',{patch:{panel:null,workSurface:'chat'}})}}>{item.title||'Open conversation'}</button></>}</li>)}</ul><p className="a-caption">Idle chats alone don’t hold up the update. Background workers, calls, and tools may still need to finish.</p><UpdateRestart updates={updates} act={act}/></details>}
  {unresolved.map(item=><ReadWhenVisible key={item.id} item={item} act={act}><ResultNotice phase="error" message={`${item.title}: ${item.detail}${item.occurrences>1?` (${item.occurrences} occurrences)` :''}`}/><p className="a-caption">Reading this does not resolve it. Diagnostic history is retained below.</p><FailureDisposition item={item} act={act}/></ReadWhenVisible>)}
  <FailureDispositions state={state}/>
  {!unresolved.length&&overview.tone!=='working'&&<ReadWhenVisible item={unread.find(item=>item.id==='updates:error')} act={act}><ResultNotice phase={resultPhase} message={resultPhase==='error'?resultMessage:''}/></ReadWhenVisible>}
  <AppReloadNotice/>
  <SmartToolUpdateResults updates={updates} act={act} busy={busy||!!updates.pendingRestart||!!updates.pendingReplacement}/>
  {application.canInstall===false&&<p className="a-ai-hint" role="status">This is a development preview. App updates are deployed by its owner; this preview cannot replace the installed app.</p>}
  <div className="a-update-controls" data-part="update-controls">
  <div className="a-dialog-actions"><button className={overview.installable?'a-soft':'a-primary'} disabled={checkDisabled} aria-describedby={checkDisabled&&disabledReason?"update-action-status":undefined} data-action="updates.check" onClick={()=>act('updates.check')}><RefreshCw className={overview.processing||updates.applicationCheckPending?"a-progress-spinner":undefined}/><span>{updates.applicationCheckPending?'Checking…':canRefreshQueued?'Check for newer release':canRefreshRunner?'Check for updates':controlsBusy?(overview.waiting?'Update queued':updates.phase==='checking'?'Checking…':'Finishing update…'):pending?'Waiting for update…':'Check for updates'}</span></button>{overview.installable&&!controlsBusy&&<button className="a-primary" disabled={overview.blocked} aria-describedby={disabledReason?"update-action-status":undefined} data-action="updates.install" onClick={()=>act('updates.install')}><Download/><span>{overview.tone==='error'?'Try update again':'Update Amplifier'}</span></button>}{controlsBusy&&<button className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{panel:null}})}>Return to chat</button>}</div>
  {updates.applicationCheckWarning&&<p className="a-caption" role="status">{updates.applicationCheckWarning}</p>}
  {controlsBusy&&<p className="a-caption">{overview.waiting?'You can close Settings. The update stays queued.':updates.pendingRestart?'This page updates automatically as the restart completes.':'You can keep working; this page will update automatically.'}</p>}
  <div className="a-update-simple-options"><label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false&&options.autoInstall!==false} onChange={e=>change(e.target.checked?{autoCheck:true,autoInstall:true}:{autoInstall:false})}/>Keep Amplifier up to date</label><p className="a-caption">Restarts wait until your work is finished.</p></div>

  </div>
  <details className="a-update-disclosure" data-part="important-release-changes"><summary>Important changes</summary><div className="a-update-disclosure-body"><AttentionReview state={state} act={act} page="updates" items={releaseUnread}/><ReleaseNotices application={application} state={state} act={act}/>{!state.attention?.items?.some(item=>item.id.startsWith('release-notice:'))&&<p className="a-caption">No high-impact notices to review. Earlier guidance remains in Changelog.</p>}</div></details>
  <ReleaseHistory application={application} state={state} act={act}/>
  <details className="a-update-disclosure" data-part="ecosystem-update-details" open={overview.tone==='error'||undefined}><summary>{overview.tone==='error'?'Advanced details · review issue':'Advanced details'}</summary><div className="a-update-disclosure-body">
   <AttentionReview state={state} act={act} page="updates" items={componentUnread} showItems/>
   <UpdateIssueSummary state={state}/>
   {(overview.tone==='working'||overview.tone==='available')&&<div className="a-update-progress" aria-label="Update order">{[['application','App'],['included','Included components'],['other','Other components']].map(([id,label],i)=><span key={id} data-active={overview.stage===id}>{i>0&&'→ '}{label}</span>)}</div>}
   {overview.currentProgress&&<p className="a-caption">{overview.currentProgress.phase==='prepare'?'Preparing':'Checking'} configurations: {overview.currentProgress.completed} of {overview.currentProgress.total} completed.</p>}
   {overview.currentBatch&&overview.tone==='working'&&<p className="a-caption" data-part="update-batch">Current batch: {overview.currentBatch.components?.join(', ')||overview.currentBatch.kind}{overview.currentBatch.componentCount>5?` and ${overview.currentBatch.componentCount-5} more`:''}. Started {new Date(overview.currentBatch.startedAt*1000).toLocaleTimeString()}.</p>}
   {!!overview.completedBatches.length&&<details data-part="update-completed-batches"><summary>Installation history</summary><ul>{overview.completedBatches.map(batch=><li key={batch.attemptId}>{batch.tier==='included'?'Included components':batch.kind==='smart-tools'?'Smart Tools':batch.kind==='application'?'App':'Other components'} installed at {new Date(batch.completedAt*1000).toLocaleTimeString()}{batch.components?.length?`: ${batch.components.join(', ')}`:''}.</li>)}</ul><p className="a-caption">Completed batches stay listed while any newly detected updates are prepared.</p></details>}   {updates.adoption?.pendingWorkers>0&&<p className="a-caption">{updates.adoption.pendingWorkers} conversation workers are finishing on earlier components.</p>}
   <p className="a-caption">Fixed versions and local overrides are kept as configured.</p>
  <details className="a-everyday-disclosure"><summary>Update preferences</summary><div className="a-update-options">
   <label><input type="checkbox" data-action="settings.update" checked={options.autoCheck!==false} onChange={e=>change({autoCheck:e.target.checked,...(!e.target.checked?{autoInstall:false}:{})})}/>Automatically check for updates</label>
   <label htmlFor="update-frequency">Check for updates</label><select id="update-frequency" data-action="settings.update" value={legacyInterval?'':intervalHours} aria-describedby={legacyInterval?'update-frequency-saved':undefined} onChange={e=>change({intervalHours:Number(e.target.value)})}>{legacyInterval&&<option value="" disabled>Choose a new interval</option>}<option value="1">Every 1 hour</option><option value="4">Every 4 hours</option><option value="8">Every 8 hours</option><option value="24">Daily</option></select>
   {legacyInterval&&<p id="update-frequency-saved" className="a-caption">Your saved schedule checks every {intervalHours} hours. It stays in effect until you choose a new interval.</p>}
  </div></details>
   <ComponentSummary updates={updates} items={items} busy={busy}/>
   <div className="a-app-update-heading"><h4>App release</h4><span className={'a-app-update-status '+appState}><AppIcon aria-hidden="true"/>{appLabels[appState]||appState}</span></div>
   {appDetail&&(appState==='failed'?<ResultNotice phase="error" message={appDetail}/>:<p className="a-caption a-wrap">{appDetail}</p>)}
   <ResultNotice phase={resultPhase} message={resultPhase==='error'?'':resultMessage}/><UpdateDiagnostics state={state} act={act}/>
  {updates.checkTiming&&<p className="a-caption">Last check: {(updates.checkTiming.elapsedMs/1000).toFixed(1)}s · {updates.checkTiming.requests||0} lookups · {updates.checkTiming.cached||0} cached results</p>}
  <UpdateSupport state={state} act={act}/>
   {updates.canRollback&&<button className="a-soft" disabled={busy||!!pending} data-action="updates.rollback" onClick={()=>act('updates.rollback')}><Undo2/><span>Restore previous components</span></button>}
   <div data-part="available-updates"><h4>Ecosystem updates {available.length>0&&<span className="a-update-count">{available.length} available</span>}</h4>{available.length?<SourceList items={available} state={state} act={act} id="available-updates"/>:<p className="a-caption">{overview.stage!=='complete'?'Remaining components are checked after the preceding update stage.':'No eligible component updates remain.'}</p>}</div>
   {!!configuredIssues.length&&<div><h4>Needs attention</h4><SourceList items={configuredIssues} state={state} act={act} id="update-issues"/></div>}
   {!!unknown.length&&<div><h4>Other cached sources</h4><p className="a-caption">Pinned sources use a fixed revision specified by a bundle, component, or your configuration. Automatic updates keep those versions.</p><p className="a-caption">{unknown.length} sources: {unknownSummary}. Usage is unknown; these files may still be needed and are kept.</p>{!!unknownIssues.length&&<SourceList items={unknownIssues} state={state} act={act} id="unknown-source-issues"/>}</div>}
   {!!items.length&&<div><button className="a-link" aria-expanded={expanded} data-action="view.update" onClick={()=>act('view.update',{patch:{maintenanceDraft:{...state.view?.maintenanceDraft,updatesExpanded:!expanded}}})}>{expanded?'Hide all sources':'Show all '+items.length+' '+(items.length===1?'source':'sources')}</button>{expanded&&<SourceList items={items} state={state} act={act} id="update-sources"/>}</div>}
  </div></details>

  {items.some(item=>item.kind==='history')&&<details className="a-update-disclosure" data-part="historical-source-settings"><summary>Older conversation settings</summary><div className="a-update-disclosure-body"><SourceList items={items.filter(item=>item.kind==='history')} state={state} act={act} id="historical-source-settings"/></div></details>}
 </ActivityRegion>;
}
