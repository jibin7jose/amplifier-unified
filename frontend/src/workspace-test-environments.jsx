import React,{useEffect,useId,useRef,useState} from 'react';

function unwrap(outer){
 const value=outer?.result?.accepted!==undefined?outer.result:outer;
 if(!value||value.accepted===false){const error=Error(value?.error||'The environment request was not confirmed.');error.confirmed=value?.accepted===false;throw error}
 return value.result;
}

function storageSize(value){
 if(!Number.isSafeInteger(value)||value<0)return null;
 const units=['B','KiB','MiB','GiB'];let size=value,unit=0;
 while(size>=1024&&unit<units.length-1){size/=1024;unit++}
 return `${size.toLocaleString(undefined,{maximumFractionDigits:1})} ${units[unit]}`;
}

const removalStates=new Set(['admitting','running','completed','failed','outcome_unknown']);
const exactKeys=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const boundedRemoval=(value,bound)=>new TextEncoder().encode(JSON.stringify(value)).length<=bound;
// Bundled producer: environment_retention.validation_diagnostic. Diagnostics
// explain retained evidence; they never authorize cleanup or relax identity.
const verificationReasons=new Set(['invalid_header','metadata_limit','name_limit','sparse_entry',
 'missing_terminator','trailing_data','incomplete_footer','unsafe_path','archive_type','archive_limit',
 'member_limit','selection_mismatch','selected_size_limit','missing_root','duplicate_entry','invalid_size',
 'unsupported_type','ancestor_traversal','ancestor_replaced','symbolic_target','hard_target','symbolic_cycle',
 'receipt_mismatch','archive_format']);
const verificationTypes=new Set(['file','directory','symbolic','hard','fifo','character','block','sparse','other']);
function removalVerification(value){
 if(exactKeys(value,['status']))return ['not_retained','unverified'].includes(value.status);
 if(!value||typeof value!=='object'||Array.isArray(value)||value.status!=='recorded'||value.version!==1||
  !verificationReasons.has(value.reasonCode)||
  !exactKeys(value,['status','version','reasonCode',...(Object.hasOwn(value,'memberType')?['memberType']:[]),
   ...(Object.hasOwn(value,'relativePath')?['relativePath']:[])]))return false;
 if(Object.hasOwn(value,'memberType')&&!verificationTypes.has(value.memberType))return false;
 if(Object.hasOwn(value,'relativePath')){
  const path=value.relativePath;
  if(typeof path!=='string'||path.length>256||path.match(/^(?:workspace|artifacts)(?:\/[A-Za-z0-9_.-]+)*$/)?.[0]!==path||
   path.split('/').some(part=>part==='.'||part==='..'))return false;
 }
 return true;
}
function removalPage(value,target,offset){
 if(!exactKeys(value,['version','sessionId','id','revision','items','nextOffset'])||value.version!==1||
  value.sessionId!==target.sessionId||value.id!==target.id||value.revision!==target.expectedRevision||
  !Array.isArray(value.items)||value.items.length>20||!boundedRemoval(value,16384)||
  !(value.nextOffset===null||(value.items.length===20&&Number.isSafeInteger(value.nextOffset)&&value.nextOffset===offset+20))||
  value.items.some(row=>!exactKeys(row,['originalRequestId','state','binding'])||
   typeof row.originalRequestId!=='string'||!row.originalRequestId||row.originalRequestId.length>200||
   !removalStates.has(row.state)||!['resource','unbound'].includes(row.binding))||
  new Set(value.items.map(row=>row.originalRequestId)).size!==value.items.length)
  throw Error('Removal discovery changed or does not match this environment. Inspect it again.');
 return value;
}
function removalResult(value,target){
 if(!exactKeys(value,['version','sessionId','id','revision','original','status','code','executor','physical','continuationAvailable','riskDecisionAvailable','verification'])||
  value.version!==1||value.sessionId!==target.sessionId||value.id!==target.id||value.revision!==target.expectedRevision||
  !exactKeys(value.original,['requestId','state'])||value.original.requestId!==target.originalRequestId||
  !removalStates.has(value.original.state)||value.status!=='held'||
  !['original_binding_missing','original_binding_unverified'].includes(value.code)||
  value.executor!=='unobserved'||value.physical!=='not_observed'||value.continuationAvailable!==false||
  value.riskDecisionAvailable!==false||!removalVerification(value.verification)||!boundedRemoval(value,4096))
  throw Error('Removal result changed or does not match this environment. Inspect it again.');
 return value;
}

export function TaskEnvironments({workspaceId,sessionId,act,active,readOnly}){
 const form=useId(),visit=useRef(0),inFlight=useRef(false),live=useRef(active),mounted=useRef(false);
 const key='amplifier-test-environment:'+workspaceId+':'+sessionId;
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key))||{profileId:'linux-python',command:'',pending:null}}catch{return {profileId:'linux-python',command:'',pending:null}}});
 const [page,setPage]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[request,setRequest]=useState(null),[removing,setRemoving]=useState(null),[fullCopy,setFullCopy]=useState(false),[checkOutput,setCheckOutput]=useState(null);
 const [inspection,setInspection]=useState(null),inspectionEpoch=useRef(0),pageScope=useRef(null),current=useRef(null);
 current.current={key,active,page};
 const pending=useRef(draft.pending);
 const retain=(value,required=false)=>{try{sessionStorage.setItem(key,JSON.stringify(value))}catch{if(required)return false}pending.current=value.pending;if(mounted.current)setDraft(value);return true};
 const refresh=async(offset=page?.offset||0)=>{
  inspectionEpoch.current++;setInspection(null);
  const token=++visit.current;
  try{const value=unwrap(await act('workspace.environments.read',{workspaceId,sessionId,offset}));if(live.current&&token===visit.current){inspectionEpoch.current++;setInspection(null);pageScope.current=key;setPage(value);setRequest(null);setError('')}}
  catch(e){if(live.current&&token===visit.current)setError(e.message)}
 };
 // Hidden panels still own pending requests; only unmount ends their UI lifetime.
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{live.current=active;if(active)refresh(0);return()=>{live.current=false;visit.current++}},[active,workspaceId,sessionId]);
 useEffect(()=>{inspectionEpoch.current++;setInspection(null);return()=>{inspectionEpoch.current++}},[active,workspaceId,sessionId]);
 const waiting=(page?.checks||[]).some(row=>['queued','starting','preparing','checking','stopping','removing'].includes(row.status))||['accepted','running'].includes(page?.setup?.operation?.state)||['accepted','running'].includes(page?.setup?.operation?.cleanup?.state);
 useEffect(()=>{if(!active||!waiting)return;const timer=setInterval(()=>{if(!inFlight.current)refresh()},5000);return()=>clearInterval(timer)},[active,waiting,workspaceId,sessionId,page?.offset]);
 const mutate=async(action,args={})=>{
  if(inFlight.current||!active||readOnly)return;
  const saved=pending.current||{action,args:{workspaceId,sessionId,...args,requestId:args.requestId||crypto.randomUUID()},id:crypto.randomUUID()};
  if(!retain({...draft,pending:saved},true)){setError('This tab cannot save a recovery receipt. Allow browser storage or use another tab before changing the environment. Nothing was sent.');return}
  // A late response from an old mount must not clear a newer saved request.
  const clearPending=()=>{try{const retained=JSON.parse(sessionStorage.getItem(key));if(retained?.pending?.id!==saved.id)return true;return retain({...retained,pending:null},true)}catch{return false}};
  inFlight.current=true;setBusy(true);setError('');
  try{
   const result=unwrap(await act(saved.action,saved.args,{id:saved.id}));
   const cleared=clearPending();
   if(live.current){setRequest(result.request||null);setRemoving(null);await refresh();if(!cleared)setError('The response was received, but this tab could not clear its saved request. Check the same request after browser storage is available.')}
  }catch(e){if(e.confirmed)clearPending();if(live.current)setError(e.message||'No confirmed response. Check the same request before trying another change.')}
  finally{inFlight.current=false;if(mounted.current)setBusy(false)}
 };
 const cancelCheck=async(requestId)=>{
  if(inFlight.current||!active||readOnly)return;
  inFlight.current=true;setBusy(true);setError('');
  const visible=()=>live.current&&current.current.key===key;
  // Cancellation is idempotent by check ID; an older request keeps its own recovery receipt.
  try{unwrap(await act('workspace.tests.cancel',{workspaceId,sessionId,requestId}));if(visible())await refresh()}
  catch(e){if(visible()){await refresh();if(visible())setError(e.message||'Cancellation was not confirmed. Check the current status before trying again.')}}
  finally{inFlight.current=false;if(mounted.current)setBusy(false)}
 };
 const rows=page?.items||[],capability=page?.capability,setup=page?.setup,setupOwned=!setup?.operation?.sessionId||(setup.operation.sessionId===sessionId&&setup.operation.workspaceId===workspaceId),profiles=capability?.profiles||[],profile=profiles.find(row=>row.id===draft.profileId)||profiles[0],controlsBlocked=busy||!!draft.pending||!active||readOnly,blocked=controlsBlocked||capability?.status!=='ready';
 const last=request||rows.map(row=>row.lastOperation).filter(Boolean).sort((a,b)=>(b.updatedAt||0)-(a.updatedAt||0))[0];
 const output=last?.result?.result;
 const readCheck=async(id,offset=0)=>{
  const token=visit.current;
  try{const value=unwrap(await act('workspace.tests.result',{workspaceId,sessionId,requestId:id,offset}));if(live.current&&token===visit.current)setCheckOutput({id,...value})}
  catch(e){if(live.current&&token===visit.current)setError(e.message)}
 };
 const inspectable=row=>active&&pageScope.current===key&&(!row.sessionId||row.sessionId===sessionId)&&(!row.workspaceId||row.workspaceId===workspaceId)&&Number.isSafeInteger(row.revision)&&row.revision>0;
 const inspectRemoval=async(row,originalRequestId=null,offset=0)=>{
  if(!inspectable(row)||busy)return;
  if(originalRequestId!==null&&(!inspection||inspection.key!==key||inspection.id!==row.id||
   inspection.revision!==row.revision||!inspection.items.some(item=>item.binding==='resource'&&item.originalRequestId===originalRequestId)))return;
  const token=visit.current,epoch=++inspectionEpoch.current,target={workspaceId,sessionId,id:row.id,expectedRevision:row.revision};
  const previous=inspection?.key===key&&inspection.id===row.id&&inspection.revision===row.revision?inspection:null;
  setInspection({key,id:row.id,revision:row.revision,items:previous?.items||[],nextOffset:previous?.nextOffset??null,result:null,error:'',busy:true,token});
  const stillCurrent=()=>mounted.current&&current.current.active&&current.current.key===key&&
   token===visit.current&&epoch===inspectionEpoch.current&&pageScope.current===key&&
   current.current.page?.items?.some(item=>item.id===row.id&&item.revision===row.revision&&
    (!item.sessionId||item.sessionId===sessionId)&&(!item.workspaceId||item.workspaceId===workspaceId));
  try{
   const result=unwrap(await act(originalRequestId===null?'workspace.environments.removal.list':'workspace.environments.removal.read',
    originalRequestId===null?{...target,...(offset?{offset,limit:20}:{})}:{...target,originalRequestId}));
   if(!stillCurrent())return;
   if(originalRequestId===null){
    const value=removalPage(result,target,offset);
    setInspection({key,id:row.id,revision:row.revision,items:value.items,nextOffset:value.nextOffset,result:null,error:'',busy:false,token});
   }else{
    const value=removalResult(result,{...target,originalRequestId});
    setInspection({key,id:row.id,revision:row.revision,items:previous.items,nextOffset:previous.nextOffset,result:value,error:'',busy:false,token});
   }
  }catch(e){
   if(stillCurrent())setInspection({key,id:row.id,revision:row.revision,items:originalRequestId===null?[]:previous.items,
    nextOffset:originalRequestId===null?null:previous.nextOffset,result:null,error:e.message||'The passive read was not confirmed. Read it again explicitly.',busy:false,token});
  }
 };
 return <section aria-label="Test environments" data-part="test-environments">
  <h4>Test environments</h4>
  <p>Run checks in a separate environment for this chat. Starting uses a snapshot of its working folder. Stop keeps environment data. Removal first saves the working folder and artifacts, then deletes the containers and their volumes.</p>
  {error&&<p role="alert">{error}</p>}
  {busy&&<p role="status">Waiting for the environment request…</p>}
  {draft.pending&&<p role="status">This tab has a saved request to check. <button type="button" className="a-link" disabled={busy||!active||readOnly} onClick={()=>mutate()}>Check the same environment request</button></p>}
  {capability&&capability.status!=='ready'&&<p>{capability.reason||'Test environments are not available on this host yet.'}</p>}
  {setup&&<section aria-label="Test runner setup">
   {setup.operation&&<p role="status">Runner setup: {setup.operation.state?.replaceAll('_',' ')}. {setup.operation.detail}</p>}
   {setup.status!=='ready'&&<>
    <p>{setup.reason}</p>
    <p>Use Docker for new ordinary checks with the managed Smart Tool runner. Choose Incus only for a specialized native workflow. Existing environments keep their original backend.</p>
    {!readOnly&&setup.backends?.filter(row=>row.available).map(backend=><button key={backend.id} type="button" className="a-soft" disabled={controlsBlocked||['accepted','running','outcome_unknown'].includes(setup.operation?.state)} onClick={()=>mutate('workspace.environments.setup',{backend:backend.id})}>Set up with {backend.label}</button>)}
    {!setup.backends?.some(row=>row.available)&&setup.backends?.map(backend=><p key={backend.id}>{backend.label}: {backend.reason}</p>)}
    <p>Setup downloads the approved runner and base image and checks an isolated environment. It uses an already installed local test service. Project preparation may download locked dependencies; the checks themselves run offline.</p>
   </>}
   {setup.operation?.cleanup&&<p role="status">Setup cleanup: {setup.operation.cleanup.state?.replaceAll('_',' ')}. {setup.operation.cleanup.detail}</p>}
   {['outcome_unknown','failed'].includes(setup.operation?.state)&&<>
    {!setupOwned&&<p>Open the chat that started setup to manage this request.</p>}
    <p>The previous setup did not finish. Its saved resources have been kept; inspect its result before starting again.</p>
    <button type="button" className="a-soft" disabled={controlsBlocked||!setupOwned} onClick={()=>mutate('workspace.environments.setup.reconcile',{requestId:setup.requestId})}>Check setup result</button>
    {!readOnly&&setup.operation.canComplete&&<button type="button" className="a-soft" disabled={controlsBlocked||!setupOwned} onClick={()=>mutate('workspace.environments.setup',{requestId:setup.requestId,backend:setup.operation.backend})}>Finish verified setup</button>}
    {!readOnly&&<button type="button" className="a-soft" disabled={controlsBlocked||!setupOwned||!!setup.operation.cleanup} onClick={()=>mutate('workspace.environments.setup.cleanup',{requestId:setup.requestId})}>Clean up interrupted setup</button>}
   </>}
  </section>}
  {page?.preparation&&<p role="status">Project preparation: {page.preparation.status?.replaceAll('_',' ')}. {page.preparation.phase} {page.preparation.reason}</p>}
  {capability?.source&&<p style={{overflowWrap:'anywhere'}}>Working folder: {capability.source}</p>}
  {(page?.checks||[]).length>0&&<section aria-label="Queued checks"><h5>Queued checks</h5>
   <ul>{page.checks.map(check=><li key={check.id}>
    <p role="status">Check: {(check.recoveredOutcome?.outcome||check.status)?.replaceAll('_',' ')}</p>
    {check.status==='queued'&&<p>Waiting for room to run. You can keep working; this request will start automatically when capacity is available.</p>}
    {check.capacity?.blockers?.some(row=>row.code==='storage_pressure')&&check.status==='queued'&&<p>The test host needs more free storage before this check can start.</p>}
    {check.recoveredOutcome?<p>Cleanup recovered. The environment was removed; the check was not repeated.</p>:check.detail&&<p>{check.detail}</p>}
    {check.cancelRequested&&['starting','preparing','checking','stopping','removing'].includes(check.status)&&<p>Cancellation requested. The runner is stopping the check and will retain its evidence before cleanup.</p>}
    {check.check&&<button type="button" className="a-link" disabled={busy||!active} onClick={()=>readCheck(check.id)}>Read check result</button>}
    {checkOutput?.id===check.id&&<div aria-label="Saved check result">
     <p>Recorded request: {checkOutput.operation?.state?.replaceAll('_',' ')}</p>
     {checkOutput.exitCode!=null&&<p>Exit code: {checkOutput.exitCode}</p>}
     <pre className="a-state-view">{checkOutput.text||'No recorded text output.'}</pre>
     {checkOutput.truncatedAtSource&&<p>The command output reached its capture limit.</p>}
     {checkOutput.nextOffset!=null&&<button type="button" className="a-link" onClick={()=>readCheck(check.id,checkOutput.nextOffset)}>Next output page</button>}
    </div>}
    {!readOnly&&['queued','starting','preparing','checking'].includes(check.status)&&!check.cancelRequested&&<button type="button" className="a-soft" disabled={busy||!active} onClick={()=>cancelCheck(check.id)}>Cancel check</button>}
    <details><summary>Check details</summary><pre className="a-state-view">{JSON.stringify(check,null,2)}</pre></details>
   </li>)}</ul>
  </section>}
  {!readOnly&&capability?.status==='ready'&&<form aria-label="Queue isolated check" onSubmit={event=>{event.preventDefault();mutate('workspace.tests.submit',{profileId:profile.id,command:draft.command,timeoutSeconds:profile.defaultTimeoutSeconds,independent:draft.independent===true,notify:false})}}>
   <label htmlFor={form+'-queued-profile'}>Check environment</label><select id={form+'-queued-profile'} disabled={blocked} value={profile?.id||''} onChange={event=>retain({...draft,profileId:event.target.value})}>{profiles.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</select>
   <label htmlFor={form+'-queued-command'}>Check command</label><textarea id={form+'-queued-command'} value={draft.command} maxLength={16000} disabled={blocked} placeholder="python -m unittest discover" onChange={event=>retain({...draft,command:event.target.value})}/>
   <p>Runs once in a snapshot of the working folder taken when the check starts. The host saves the working folder and artifacts before removing the test environment. {profile?.defaultTimeoutSeconds?`The check has a ${Math.ceil(profile.defaultTimeoutSeconds/60)}-minute limit.`:''} {profile?.preparationRequired?'The first check prepares this project’s locked dependencies before running; preparation may download packages.':''}</p>
   <label><input type="checkbox" checked={draft.independent===true} disabled={blocked} onChange={event=>retain({...draft,independent:event.target.checked})}/> This check can run independently of other checks</label><p>Independent checks may overlap when the global limits and test machine have room. Leave this off if the check must wait for earlier results.</p>
   <button type="submit" className="a-soft" disabled={blocked||!draft.command.trim()||!profile?.defaultTimeoutSeconds}>Queue check</button>
  </form>}
  {page&&rows.length===0&&<p>No test environment for this chat.</p>}
  <ul>{rows.map(row=>{const cleanupBlocked=controlsBlocked||row.recovery?.available!==true,failedCleanup=['workspace.environments.remove','workspace.environments.finishCleanup'].includes(row.lastOperation?.action)&&row.lastOperation?.state==='failed'&&row.lastOperation?.result?.effectAdmitted===false&&['preserving','verifying'].includes(row.lastOperation?.result?.phase)&&['preservation_failed','archive_verification_failed'].includes(row.lastOperation?.result?.code)?row.lastOperation:null,accounting=row.retention?.accounting,savedBytes=accounting?.verified?storageSize(accounting.totalBytes):null;return <li key={row.id} data-test-environment={row.id} style={{overflowWrap:'anywhere'}}>
   <strong>{row.label||'Test environment'}</strong><p>Environment {row.id} · {row.testMachine?.name||'Local test runner'}</p><p role="status">{row.observation?.status==='unverified'?'Last known environment':'Environment'}: {row.status?.replaceAll('_',' ')||'unknown'}</p>
   {row.reason&&<p>{row.reason}</p>}
   {row.preparation&&<p role="status">Dependency preparation: {row.preparation.status?.replaceAll('_',' ')}. {row.preparation.phase} {row.preparation.reason}</p>}
   {row.observation?.status==='unverified'&&<p>Current state could not be verified. This is a saved observation.</p>}
   {['outcome_unknown','unknown'].includes(row.status)&&<p>The previous outcome is uncertain. Inspect the environment before taking another action.</p>}
   {row.previousCleanup&&<p>Earlier cleanup: {row.previousCleanup.state?.replaceAll('_',' ')}. Its original receipt is retained.</p>}
   {row.lastOperation?.result?.phase&&<p>Cleanup phase: {row.lastOperation.result.phase}. {row.lastOperation.result.effectAdmitted===false?'The environment was not removed.':''}</p>}
   {row.lastRecovery&&<p role="status">Last recovery check: {row.lastRecovery.state?.replaceAll('_',' ')||'unknown'}. {row.lastRecovery.message||'The original request result is kept separately.'}</p>}
   {row.lastArtifactExport&&<p role="status">Artifact recovery: {row.lastArtifactExport.state?.replaceAll('_',' ')||'unknown'}. {row.lastArtifactExport.message}</p>}
   {row.artifactExport?.verified&&<p>Artifact evidence saved: {storageSize(row.artifactExport.archiveBytes)}. This copy does not include the working folder or authorize removal.</p>}
   {savedBytes!==null&&<p>Latest saved copy: {savedBytes}. {accounting.policy==='full'?'Includes a complete environment export.':'Includes the source snapshot, working folder and artifacts.'} These files stay saved until deliberately removed.</p>}
   {accounting?.archiveOnly&&<p>The saved archives preserve link metadata without following links. Restore them only in an isolated environment.</p>}
   {row.status==='removed'&&savedBytes===null&&<p>Previously saved files and results are kept. Their storage size has not been verified.</p>}
   <details><summary>Environment details</summary><pre className="a-state-view">{JSON.stringify(row,null,2)}</pre></details>
   <button type="button" className="a-soft" disabled={!inspectable(row)||busy||inspection?.busy&&inspection.id===row.id} onClick={()=>inspectRemoval(row)}>Inspect original removal</button>
   {active&&inspection?.key===key&&inspection.id===row.id&&inspection.revision===row.revision&&inspection.token===visit.current&&<section aria-label="Original removal inspection">
    {inspection.busy&&<p role="status">Reading retained removal records…</p>}
    {inspection.error&&<p role="alert">{inspection.error}</p>}
    {!inspection.busy&&!inspection.error&&inspection.items.length===0&&<p>No original removal request is recorded for this environment.</p>}
    <ul>{inspection.items.map(item=><li key={item.originalRequestId}>
     {item.binding==='resource'?<button type="button" className="a-link" disabled={inspection.busy} onClick={()=>inspectRemoval(row,item.originalRequestId)}>Read original removal {item.originalRequestId}</button>:
      <p>Request {item.originalRequestId} is not associated with this environment. Its outcome remains held.</p>}
    </li>)}</ul>
    {inspection.nextOffset!==null&&<button type="button" className="a-link" disabled={inspection.busy} onClick={()=>inspectRemoval(row,null,inspection.nextOffset)}>More removal requests</button>}
    {inspection.result&&<div role="status">
     <p>Removal remains held. Original request: {inspection.result.original.requestId}</p>
     <p>Recorded outcome: {inspection.result.original.state.replaceAll('_',' ')}.</p>
     <p>{inspection.result.code==='original_binding_missing'?'The original resource binding is missing.':'The original binding evidence is incomplete.'} No removal was repeated.</p>
     <p>The executor and current physical environment were not observed. Inspection does not authorize continuation or removal.</p>
    </div>}
   </section>}
   {!readOnly&&row.status!=='removed'&&<>
    <button type="button" className="a-soft" disabled={cleanupBlocked} onClick={()=>mutate('workspace.environments.reconcile',{id:row.id,expectedRevision:row.revision})}>Check and recover</button>
    <p>Checks the current environment without rerunning commands. Releases ownership only when its absence is confirmed.</p>
    {row.recovery?.available===false&&<p>{row.recovery.reason||'The original environment connection is unavailable. Its files and ownership are kept.'}</p>}
    <form aria-label="Run environment check" onSubmit={event=>{event.preventDefault();mutate('workspace.environments.execute',{id:row.id,expectedRevision:row.revision,command:draft.command,timeoutSeconds:60})}}>
     <label htmlFor={form+'-command-'+row.id}>Check command</label><textarea id={form+'-command-'+row.id} maxLength={16000} placeholder="python -m unittest discover" value={draft.command} disabled={blocked} onChange={event=>retain({...draft,command:event.target.value})}/>
     <button type="submit" className="a-soft" disabled={blocked||!draft.command.trim()||!['running','ready'].includes(row.status)}>Run check</button>
    </form>
    <button type="button" className="a-soft" disabled={cleanupBlocked||['stopped','not-started'].includes(row.status)} onClick={()=>mutate('workspace.environments.stop',{id:row.id,expectedRevision:row.revision})}>Stop and keep data</button>
    <button type="button" className="a-soft" disabled={cleanupBlocked||row.status!=='stopped'||row.lastArtifactExport?.state==='outcome_unknown'} onClick={()=>mutate('workspace.environments.exportArtifacts',{id:row.id,expectedRevision:row.revision})}>Save artifact evidence</button>
    {row.lastArtifactExport?.state==='outcome_unknown'&&<p>The artifact export is uncertain. Its original request and the stopped environment are retained; it will not be repeated automatically.</p>}
    {failedCleanup&&<div><p>Finish the incomplete cleanup by saving the complete working folder and artifacts, then verifying removal. The test and original request will not run again.</p><button type="button" className="a-soft" disabled={cleanupBlocked||!['stopped','not-started'].includes(row.status)} onClick={()=>mutate('workspace.environments.finishCleanup',{id:row.id,expectedRevision:row.revision,previousRequestId:failedCleanup.requestId})}>Preserve files and finish cleanup</button></div>}
    {removing!==row.id?<button type="button" className="a-soft" disabled={cleanupBlocked||!['stopped','not-started'].includes(row.status)} onClick={()=>{setRemoving(row.id);setFullCopy(row.retention?.mode==='full')}}>Remove environment…</button>:<div role="group" aria-label="Confirm environment removal">
     <p>{fullCopy?'A complete environment export and saved check results will be retained.':'The working folder, artifacts and saved check results will be retained.'} The environment will be removed only after those files are saved. {!fullCopy?'Files elsewhere in the container are not retained.':''}</p>
     {row.retention?.fullAvailable&&<details><summary>Save more files</summary><label><input type="checkbox" checked={fullCopy} disabled={cleanupBlocked} onChange={event=>setFullCopy(event.target.checked)}/> Keep a complete copy of the environment</label><p>This uses more storage. Choose it if the smaller export could not preserve your files, or if you need files outside the working folder and artifacts.</p></details>}
     <button type="button" className="a-soft" disabled={cleanupBlocked} onClick={()=>mutate('workspace.environments.remove',{id:row.id,expectedRevision:row.revision,...(row.retention?.fullAvailable?{retentionMode:fullCopy?'full':'files'}:{})})}>Save files and remove environment</button>
     <button type="button" className="a-soft" disabled={busy} onClick={()=>setRemoving(null)}>Keep environment</button>
    </div>}
   </>}
  </li>})}</ul>
  {page?.offset>0&&<button type="button" className="a-link" disabled={busy||!active} onClick={()=>refresh(Math.max(0,page.offset-10))}>Newer environments</button>}
  {page?.nextOffset!=null&&<button type="button" className="a-link" disabled={busy||!active} onClick={()=>refresh(page.nextOffset)}>Older environments</button>}
  {last&&<div data-part="environment-result">
   <p role="status">Last request: {last.state?.replaceAll('_',' ')||'unknown'}</p><p>Request: {last.requestId}{last.resourceId?` · Environment: ${last.resourceId}`:''}</p>
   {output?.exit_code!=null&&<p>Exit code: {output.exit_code}</p>}
   {output?.stdout&&<pre className="a-state-view">{output.stdout}</pre>}
   {output?.stderr&&<pre className="a-state-view">{output.stderr}</pre>}
   {(output?.truncated||output?.stdout_truncated||output?.stderr_truncated)&&<p>The displayed output reached its size limit.</p>}
   {last.state==='outcome_unknown'&&<p>The original command result is uncertain. Checking its request does not run it again. A later recovery check reports the environment’s current state separately.</p>}
   <details><summary>Request details</summary><pre className="a-state-view">{JSON.stringify(last,null,2)}</pre></details>
  </div>}
  <button type="button" className="a-soft" disabled={busy||!active} onClick={()=>refresh()}>Refresh test environments</button>
  {!readOnly&&capability?.status==='ready'&&!waiting&&(capability.maxEnvironmentsPerChat===0||(capability.ownedEnvironments??rows.filter(row=>row.status!=='removed').length)<(capability.maxEnvironmentsPerChat??1))&&<form aria-label="Start test environment" onSubmit={event=>{event.preventDefault();mutate('workspace.environments.start',{profileId:profile?.id||draft.profileId})}}>
   <label htmlFor={form+'-profile'}>Environment</label><select id={form+'-profile'} disabled={blocked} value={profile?.id||draft.profileId} onChange={event=>retain({...draft,profileId:event.target.value})}>{profiles.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</select>
   <button type="submit" className="a-soft" disabled={blocked}>Start test environment</button>
  </form>}
 </section>;
}
