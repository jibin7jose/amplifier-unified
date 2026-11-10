import React,{useEffect,useReducer,useRef,useState} from 'react';
import './publishing.css';
import {useCanvasSectionStatus} from './canvas-overview-state';

// Only submitted requests survive closing this panel. They stay private to this
// browser page and are never written into conversation drafts or shared views.
const requests=new Map();
function requestState(sid){if(!requests.has(sid))requests.set(sid,{pending:null,busy:false,listeners:new Set()});return requests.get(sid)}
function notify(record){record.listeners.forEach(fn=>fn())}
const description=value=>typeof value==='string'?value:value?.message||JSON.stringify(value||'The action failed.');
const actionError=(response,fallback)=>Object.assign(Error(response?.error?description(response.error):fallback),{code:response?.code,state:response?.state,receipt:response?.receipt,status:response?.status});
const uncertain=value=>value?.code==='unknown_outcome'||['unknown','running'].includes(value?.state)||['unknown','running'].includes(value?.receipt?.state);
const safeLink=value=>{try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:null}catch{return null}};

const emptyTarget={targetId:'',label:'',hostname:'',username:'',python:'',socketPath:'',expectedBind:'127.0.0.1'};
export function PublishingSettings({session,act}){
 const sid=session?.id,record=requestState(sid),[,render]=useReducer(value=>value+1,0);
 const initialTarget=record.pending?.args.targetId||'loopback',targetRef=useRef(initialTarget);
 const [targetId,setTargetId]=useState(initialTarget),[registry,setRegistry]=useState(null),[targetDraft,setTargetDraft]=useState({...emptyTarget}),[targetDraftRevision,setTargetDraftRevision]=useState(0);
 const [data,setData]=useState(null),[siteId,setSiteId]=useState(record.pending?.args.siteId||''),[sourcePath,setSourcePath]=useState(''),[releaseId,setReleaseId]=useState(record.pending?.args.releaseId||''),[note,setNote]=useState('');
 const [reading,setReading]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[inspection,setInspection]=useState(null);
 const mounted=useRef(true),readFlight=useRef(false);
 useEffect(()=>{mounted.current=true;record.listeners.add(render);return()=>{mounted.current=false;record.listeners.delete(render)}},[record]);
 const update=fn=>{if(mounted.current)fn()};
 const sites=data?.sites||[],releases=data?.releases||[],selected=sites.find(row=>row.id===siteId),release=releases.find(row=>row.id===releaseId&&row.siteId===siteId);
 const targets=registry?.targets||[],currentTarget=targets.find(row=>row.id===targetId);
 const availability=data?.availability||registry?.availability,unavailable=availability?.available===false;
 const busy=record.busy||reading,locked=busy||!!record.pending,siteReleases=releases.filter(row=>row.siteId===siteId);
 useCanvasSectionStatus(error?'Needs attention':record.pending?'Outcome pending':busy?'Inspecting…':data?`${sites.length} sites`:'');
 const targetReady=registry?.selectedTargetId===targetId&&(currentTarget?.kind==='loopback'||currentTarget?.inspection?.revision===currentTarget?.revision&&registry.selectedTargetRevision===currentTarget.revision&&registry.selectedServiceId===currentTarget.inspection?.serviceId);
 const lifecycleLocked=locked||!targetReady,accessPolicy=data?.target?.accessPolicy||currentTarget?.inspection?.capabilities?.accessPolicy;
 function displayTarget(value){if(targetRef.current!==value){targetRef.current=value;update(()=>{setTargetId(value);setData(null);setSiteId('');setReleaseId('');setNote('');setInspection(null)})}}
 async function read(action,args={},target=targetRef.current){
  const response=await act(action,{sessionId:sid,...(!action.startsWith('publishing.target.')?{targetId:target}:{}),...args});
  if(!response?.accepted)throw actionError(response,'The server did not confirm this request.');
  return response.result;
 }
 async function refresh({reconcile=false,followSelection=false,target=targetRef.current}={}){
  const saved=await read('publishing.target.list');update(()=>setRegistry(saved));
  if(followSelection)target=record.pending?.args.targetId||saved.selectedTargetId||'loopback';
  displayTarget(target);
  if(record.pending?.action.startsWith('publishing.target.'))return saved;
  const value=await read('publishing.list',{},target);update(()=>setData(value));
  if(reconcile&&record.pending&&record.pending.args.targetId===target){
   const receipt=value.receipts?.find(row=>row.requestId===record.pending.args.requestId);
   if(['unknown','running'].includes(receipt?.state)){
    record.pending={...record.pending,receipt};notify(record);
    const historical=value.historicalOnly===true||value.liveness==='unconfirmed'||value.availability?.available===false;
    update(()=>setNotice(historical?`Historical saved state: ${receipt.state}; current progress is unconfirmed. Nothing was replayed.`:receipt.state==='unknown'?'The saved receipt records an unknown outcome. Nothing was replayed.':'The saved receipt is still running. Nothing was replayed.'));
   }else if(receipt&&['succeeded','failed'].includes(receipt.state)){
    record.pending=null;notify(record);
    update(()=>receipt.state==='failed'?setError(description(receipt.error)):setNotice('The saved receipt confirms this request succeeded.'));
   }
  }
  return value;
 }
 async function inspect(action='publishing.list',reconcile=true){
  if(record.busy||readFlight.current)return;
  readFlight.current=true;setReading(true);setError('');
  try{if(action==='publishing.list')await refresh({reconcile,followSelection:true});else{setInspection({action,value:await read(action,{siteId})});if(action==='publishing.status')await refresh()}}
  catch(e){setError(e.message)}finally{readFlight.current=false;setReading(false)}
 }
 useEffect(()=>{if(sid)inspect('publishing.list',false)},[sid]);
 async function submit(action,args,retry=false){
  if(unavailable){setError(availability.reason);return}
  if(record.busy||readFlight.current||(!retry&&record.pending))return;
  const targetAction=action?.startsWith('publishing.target.');
  if(!retry&&!targetAction&&!targetReady){setError('Inspect and select this exact target before starting another publishing action.');return}
  const command=retry?record.pending:{action,args:{sessionId:sid,...(!targetAction?{targetId:targetRef.current,...(currentTarget?.kind==='ssh'?{targetRevision:currentTarget.revision,serviceId:currentTarget.inspection.serviceId}:{}),requestId:crypto.randomUUID()}:{}),...args}};
  if(!command)return;
  const isTarget=command.action.startsWith('publishing.target.');
  record.pending=command;record.busy=true;notify(record);setError('');setNotice('');
  try{
   const response=await act(command.action,command.args);
   if(!response?.accepted)throw actionError(response,'The connection ended before the outcome was confirmed.');
   const result=response.result;
   if(result?.effect==='none'&&result?.accepted===false){
    if(!retry)record.pending=null;
    update(()=>setError(result.reason||'Publishing is unavailable. No effect was admitted.'));
    return;
   }
   if(isTarget){
    if(!Array.isArray(result?.targets))throw Error('The server returned no complete target state.');
    record.pending=null;update(()=>{setRegistry(result);setNotice(command.action==='publishing.target.save'?'Target saved. No connection was made.':command.action==='publishing.target.inspect'?'Target identity and capabilities inspected.':command.action==='publishing.target.remove'?'Target configuration removed. Existing remote sites were not stopped.':'Publishing target selected.')});
    if(command.action==='publishing.target.save')update(()=>{setTargetDraftRevision(result.targets.find(row=>row.id===command.args.targetId)?.revision??0);if(command.args.targetId===targetRef.current)setData(null)});
    if(command.action==='publishing.target.select'){displayTarget(result.selectedTargetId);await refresh({target:result.selectedTargetId})}
   }else{
    if(!result||(command.action==='publishing.build'?!result.id:!['succeeded','failed','unknown','running'].includes(result.state)))throw Error('The server returned no complete publishing receipt.');
    if(result?.state==='unknown'||result?.state==='running'){record.pending={...command,receipt:result};update(()=>setError(description(result.error||'The outcome is not confirmed. Inspect receipts before continuing.')))}
    else if(result?.state==='failed'){record.pending=null;update(()=>setError(description(result.error)))}
    else{record.pending=null;update(()=>{setNotice(command.action.slice(11)+' completed.');if(command.action==='publishing.build'){setSiteId(result.siteId);setReleaseId(result.id);setNote('')}})}
    try{await refresh({target:command.args.targetId})}catch(e){update(()=>setError('The action returned, but the list could not refresh. '+e.message))}
   }
  }catch(e){
   // Exact target identity and arguments stay bound to the pending command, even if
   // another client changes the selected target while the response is missing.
   // A rejection status cannot override uncertainty from an earlier attempt.
   // Only a final receipt can resolve a lifecycle retry whose response was lost.
   // Target configuration controls use their separate revision contract.
   const finalReceipt=['succeeded','failed'].includes(e.receipt?.state);
   if(uncertain(e)||(retry&&command.args.requestId&&!finalReceipt)){
    if(['unknown','running'].includes(e.receipt?.state))record.pending={...command,receipt:e.receipt};
    update(()=>setError(e.message+' Keep this request unchanged until its outcome is known.'));
   }else if(finalReceipt||(e.status>=400&&e.status<500&&e.status!==408)){
    record.pending=null;update(()=>e.receipt?.state==='succeeded'?setNotice('The saved receipt confirms this request succeeded.'):setError(e.message));
    try{if(isTarget){const value=await read('publishing.target.list');update(()=>setRegistry(value))}else await refresh({target:command.args.targetId})}catch{}
   }else update(()=>setError(e.message+' Keep this request unchanged until its outcome is known.'));
  }finally{record.busy=false;notify(record)}
 }
 function acknowledgeUnknown(){if(record.busy||record.pending?.receipt?.state!=='unknown')return;record.pending=null;notify(record);setError('');setNotice('Unknown outcome acknowledged. Inspect the current site status. Any site marked unknown requires an explicit stop or remove action.')}
 function chooseSite(value){setSiteId(value);setReleaseId('');setNote('');setInspection(null)}
 function editTarget(row){setTargetDraftRevision(row.revision);setTargetDraft(Object.fromEntries(Object.keys(emptyTarget).map(key=>[key,key==='targetId'?row.id:row[key]||emptyTarget[key]])))}
 function saveTarget(event){event.preventDefault();submit('publishing.target.save',{...targetDraft,...(!targetDraft.username?{username:undefined}:{}),expectedRevision:targetDraftRevision})}
 if(!sid)return <p>Choose a conversation to manage its published sites.</p>;
 if(unavailable)return <section className="a-publishing" aria-label="Managed publishing">
  <h3>Publishing</h3><p role="status">{availability.reason}</p>
  <p>Retained receipts are unchanged historical evidence. Saved URLs and running states do not prove current listeners or progress.</p>
  <button type="button" className="a-soft" disabled={busy} data-action="publishing.list" onClick={()=>inspect()}>Read retained publishing</button>
  {targets.map(target=><button type="button" className="a-soft" disabled={busy} key={target.id} onClick={()=>refresh({target:target.id}).catch(e=>setError(e.message))}>Read retained {target.label}</button>)}
  {record.pending&&<p>Request outcome remains unconfirmed: {record.pending.action} · <code>{record.pending.args.requestId}</code>. Nothing is retried.</p>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <details><summary>Retained publishing evidence (liveness unconfirmed)</summary><pre className="a-state-view">{JSON.stringify(data||registry,null,2)}</pre></details>
 </section>;
 return <section className="a-publishing" aria-label="Managed publishing">
  <h3>Publishing</h3><p>Save a built site as an immutable release, preview it, record your review, and explicitly deploy that version.</p>
  <div className="a-selection-card" aria-label="Publishing target"><strong>{currentTarget?.label||data?.target?.host?.label||'Local server'}</strong><p>Target: <code>{targetId}</code>{registry?.selectedTargetId!==targetId&&registry&&' · task selection: '+registry.selectedTargetId}</p><p>Access: {accessPolicy||(targetId==='loopback'?'loopback-only':'Not inspected')}</p>{registry&&!targetReady&&<p>Inspect and select this exact target before starting another publishing action.</p>}<p>{data?.target?.detail||'URLs belong to the publishing service host.'}</p><p className="a-caption">A loopback URL opens only on the service host. No tunnel, service installation, credentials, or public route is created here.</p></div>
  <details className="a-publishing-targets"><summary>Publishing targets for this task</summary><div aria-label="Publishing targets">
   {targets.map(target=>{const checked=target.inspection?.revision===target.revision?target.inspection:null,capabilities=checked?.capabilities;return <div className="a-selection-card" key={target.id} aria-label={'Target '+target.id}><h4>{target.label}</h4><p><code>{target.id}</code> · revision {target.revision}{registry.selectedTargetId===target.id?(registry.selectedTargetRevision===target.revision?' · selected':' · configuration changed; inspect and select again'):''}</p>{target.kind==='ssh'&&<><p>Configured SSH destination: {target.username?target.username+'@':''}{target.hostname}</p><p>Expected bind: <code>{target.expectedBind}</code>. Access policy is verified by inspection of the service.</p>{checked?<dl><dt>Inspected service</dt><dd>{checked.serviceId}</dd><dt>Actual bind</dt><dd>{capabilities?.bind}</dd><dt>Access policy</dt><dd>{capabilities?.accessPolicy}</dd><dt>Authentication</dt><dd>{capabilities?.authentication}</dd></dl>:<p>Not inspected. Service identity and access policy are unverified.</p>}</>}
    <div className="a-dialog-actions">{target.kind==='ssh'&&<button type="button" className="a-soft" disabled={locked} data-action="publishing.target.inspect" onClick={()=>submit('publishing.target.inspect',{targetId:target.id,expectedRevision:target.revision})}>Inspect target</button>}<button type="button" className="a-soft" disabled={locked||(target.kind==='loopback'&&registry.selectedTargetId===target.id)||(target.kind==='ssh'&&!checked)} data-action="publishing.target.select" onClick={()=>submit('publishing.target.select',{targetId:target.id,expectedRevision:target.revision,...(checked?{serviceId:checked.serviceId}:{})})}>Select target</button>{target.kind==='ssh'&&<><button type="button" className="a-soft" disabled={locked} onClick={()=>editTarget(target)}>Edit target</button><button type="button" className="a-soft a-danger" disabled={locked||registry.selectedTargetId===target.id} data-action="publishing.target.remove" onClick={()=>submit('publishing.target.remove',{targetId:target.id,expectedRevision:target.revision})}>Remove target configuration</button></>}</div>
   </div>})}
   <h4>Save a target configuration</h4><p>Use an existing SSH account and running publishing service. Saving these paths does not connect or install anything. Only unused configurations can be removed; configurations with publishing records remain available for control and audit. Removing a configuration does not stop sites on that service.</p>
   <form onSubmit={saveTarget}>{[['targetId','Target ID'],['label','Target label'],['hostname','SSH hostname'],['username','SSH username (optional)'],['python','Remote Python path'],['socketPath','Remote service socket'],['expectedBind','Expected bind address']].map(([key,label])=><React.Fragment key={key}><label htmlFor={'publishing-target-'+key}>{label}</label><input id={'publishing-target-'+key} value={targetDraft[key]} required={key!=='username'} disabled={locked} onChange={event=>{if(key==='targetId')setTargetDraftRevision(0);setTargetDraft(old=>({...old,[key]:event.target.value}))}}/></React.Fragment>)}<button className="a-primary" disabled={locked} data-action="publishing.target.save">Save target configuration</button><button type="button" className="a-soft" disabled={locked} onClick={()=>{setTargetDraft({...emptyTarget});setTargetDraftRevision(0)}}>Clear target form</button></form>
  </div></details>
  <button type="button" className="a-soft" data-action="publishing.list" disabled={busy} onClick={()=>inspect()}>Inspect publishing</button>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {record.pending&&<div className="a-selection-card" aria-label="Unconfirmed publishing request"><strong>{record.busy?'Request in progress':record.pending.receipt?.state==='unknown'?'Recorded unknown outcome':'Request outcome unconfirmed'}</strong><p>{record.pending.action} · target <code>{record.pending.args.targetId}</code> · {record.pending.args.siteId||record.pending.args.releaseId}</p>{record.pending.args.requestId&&<code>{record.pending.args.requestId}</code>}<p>The exact submitted arguments are retained while this browser page stays open. Inspect publishing to check the saved receipt, or retry this same request. Nothing is retried automatically.</p>{record.pending.receipt?.state==='unknown'?<><p>{description(record.pending.receipt.error)}</p><button type="button" className="a-soft" disabled={busy} onClick={acknowledgeUnknown}>Acknowledge unknown outcome</button><p>Acknowledgement dismisses this pending request only. It does not repeat or resolve the operation; any site marked unknown requires an explicit stop or remove action.</p></>:<button type="button" className="a-soft" disabled={busy} data-action={record.pending.action} onClick={()=>submit(null,null,true)}>Retry exact request</button>}</div>}
  <h4>Sites</h4>{data&&!sites.length&&<p>No sites yet. Capture a release below to get started.</p>}
  <div className="a-publishing-sites">{sites.map(site=><button type="button" className="a-selection-card" key={site.id} disabled={lifecycleLocked} aria-pressed={siteId===site.id} onClick={()=>chooseSite(site.id)}><strong>{site.id}</strong><span>{site.status} · revision {site.revision}</span></button>)}</div>
  <h4>Capture built output</h4><p>Build with your existing project tools first, then enter its output folder relative to this task’s execution folder. Capturing preserves the current files as a new release.</p>
  <form onSubmit={e=>{e.preventDefault();submit('publishing.build',{siteId:siteId.trim(),sourcePath:sourcePath.trim()})}}>
   <label htmlFor="publishing-site">Site ID</label><input id="publishing-site" value={siteId} disabled={lifecycleLocked} maxLength={63} pattern="[a-z0-9][a-z0-9-]{0,62}" onChange={e=>chooseSite(e.target.value)} placeholder="my-site"/>
   <label htmlFor="publishing-source">Built output folder</label><input id="publishing-source" value={sourcePath} disabled={lifecycleLocked} onChange={e=>setSourcePath(e.target.value)} placeholder="dist"/>
   <button className="a-primary" data-action="publishing.build" disabled={lifecycleLocked||!siteId.trim()||!sourcePath.trim()}>Capture release</button>
  </form>
  {siteId&&<><h4>Saved releases for {siteId}</h4><label htmlFor="publishing-release">Release to preview, review, or deploy</label><select id="publishing-release" value={release?.id||''} disabled={lifecycleLocked} onChange={e=>{setReleaseId(e.target.value);setNote('')}}><option value="">Choose a saved release</option>{siteReleases.map(row=><option key={row.id} value={row.id}>{row.createdAt} · {row.id}</option>)}</select></>}
  {release&&<div className="a-selection-card" aria-label="Selected release"><h4>Exact release</h4><dl><dt>Release ID</dt><dd>{release.id}</dd><dt>Manifest digest</dt><dd>{release.manifestDigest}</dd><dt>Files</dt><dd>{release.files?.length||0} · {release.totalBytes||0} bytes</dd><dt>Preview</dt><dd>{release.previewStatus||'none'}</dd><dt>Review</dt><dd>{release.review?`Reviewed: ${release.review.note}`:'Not reviewed'}</dd></dl>
   <details><summary>Release files</summary><ul>{release.files?.map(file=><li key={file.path}><strong>{file.path}</strong> · {file.size} bytes<br/><code>{file.sha256}</code></li>)}</ul></details>
   <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={lifecycleLocked} data-action="publishing.preview" onClick={()=>submit('publishing.preview',{releaseId:release.id})}>Start preview</button>{release.previewStatus==='running'&&safeLink(release.previewUrl)&&<a href={safeLink(release.previewUrl)} target="_blank" rel="noopener noreferrer">Open exact release preview</a>}</div>
   <form onSubmit={e=>{e.preventDefault();submit('publishing.review',{releaseId:release.id,note:note.trim()})}}><label htmlFor="publishing-note">Review note for this exact release</label><textarea id="publishing-note" value={note} disabled={lifecycleLocked} maxLength={4000} onChange={e=>setNote(e.target.value)}/><button className="a-soft" data-action="publishing.review" disabled={lifecycleLocked||!note.trim()}>Record review</button></form>
   <p>Deploy release <code>{release.id}</code> to <strong>{siteId}</strong> on target <strong>{targetId}</strong> with <strong>{data?.target?.accessPolicy||currentTarget?.inspection?.capabilities?.accessPolicy||'unverified'}</strong> access.</p><button type="button" className="a-primary" disabled={lifecycleLocked||!release.review||selected?.status==='unknown'||!accessPolicy} data-action="publishing.deploy" onClick={()=>submit('publishing.deploy',{siteId,releaseId:release.id,expectedRevision:selected?.revision??0})}>Deploy reviewed release</button>
  </div>}
  {selected&&<div className="a-selection-card" aria-label="Site status"><h4>{selected.id}</h4><dl><dt>Status</dt><dd>{selected.status}</dd><dt>Revision</dt><dd>{selected.revision}</dd><dt>Access policy</dt><dd>{selected.accessPolicy}</dd><dt>Deployed release</dt><dd>{selected.releaseId||'None'}</dd><dt>Previous release</dt><dd>{selected.previousReleaseId||'None'}</dd></dl>{safeLink(selected.url)&&<p><a href={safeLink(selected.url)} target="_blank" rel="noopener noreferrer">Open deployed site</a><br/><code>{selected.url}</code></p>}
   <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="publishing.status" onClick={()=>inspect('publishing.status')}>Check site status</button><button type="button" className="a-soft" disabled={busy} data-action="publishing.logs" onClick={()=>inspect('publishing.logs')}>Read site logs</button></div>
   {selected.previousReleaseId&&<><p>Rollback restores release <code>{selected.previousReleaseId}</code>.</p><button type="button" className="a-soft" disabled={lifecycleLocked} data-action="publishing.rollback" onClick={()=>submit('publishing.rollback',{siteId,releaseId:selected.previousReleaseId,expectedRevision:selected.revision})}>Roll back to previous release</button></>}
   <p>Stopping or removing this site preserves its saved releases and receipts.</p><div className="a-dialog-actions"><button type="button" className="a-soft" disabled={lifecycleLocked||['stopped','removed'].includes(selected.status)} data-action="publishing.stop" onClick={()=>submit('publishing.stop',{siteId,expectedRevision:selected.revision})}>Stop site</button><button type="button" className="a-soft a-danger" disabled={lifecycleLocked||selected.status==='removed'} data-action="publishing.remove" onClick={()=>submit('publishing.remove',{siteId,expectedRevision:selected.revision})}>Remove site</button></div>
  </div>}
  {inspection&&<div aria-label="Publishing inspection"><h4>{inspection.action==='publishing.logs'?'Site logs':'Current site status'}</h4><pre className="a-state-view">{JSON.stringify(inspection.value,null,2)}</pre></div>}
  <details><summary>Durable publishing receipts ({data?.receipts?.length||0})</summary><div aria-label="Publishing receipts">{data?.receipts?.map(receipt=><div className="a-selection-card" key={receipt.id}><strong>{receipt.action} · {receipt.state}</strong><p>{receipt.siteId} · {receipt.createdAt}</p><p>Request: <code>{receipt.requestId}</code></p>{receipt.releaseId&&<p>Release: <code>{receipt.releaseId}</code></p>}{receipt.error&&<p>{description(receipt.error)}</p>}</div>)}</div></details>
 </section>;
}
