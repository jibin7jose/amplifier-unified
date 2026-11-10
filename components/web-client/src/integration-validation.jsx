import React,{useEffect,useRef,useState} from 'react';

const basename=path=>path?.split(/[\\/]/).filter(Boolean).pop()||'Repository';
export function IntegrationValidation({session,act,candidate}){
 const key='amplifier-integration-validation:'+session.id;
 const [pending,setPending]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}});
 const [repository,setRepository]=useState(''),[command,setCommand]=useState(''),[timeout,setTimeout]=useState(300),[runs,setRuns]=useState([]),[choices,setChoices]=useState({}),[ids,setIds]=useState(''),[results,setResults]=useState('[]'),[value,setValue]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[next,setNext]=useState(null);
 const inflight=useRef(false),scope=useRef(candidate?.id),visit=useRef(0);scope.current=candidate?.id;
 useEffect(()=>{visit.current++;setIds('');setResults('[]');setNext(null);setValue(null);setError('');return()=>{visit.current++}},[candidate?.id]);
 const retain=request=>{setPending(request);try{request?sessionStorage.setItem(key,JSON.stringify(request)):sessionStorage.removeItem(key)}catch{}};
 const addRun=run=>{setRuns(previous=>[...previous.filter(row=>row.id!==run.id),run].slice(-64));setChoices(previous=>({...previous,[run.captureId]:{...previous[run.captureId],[run.repositoryPath]:run.id}}))};
 const run=async(action,args)=>{if(inflight.current)return;const request=pending||{action,args:{sessionId:session.id,...args},id:crypto.randomUUID()};retain(request);inflight.current=true;setBusy(true);setError('');try{
  const response=await act(request.action,request.args,{id:request.id});
  if(!response?.accepted||!response.result)throw Error('No confirmed response. Check the same request before starting another validation.');
  retain(null);if(response.result.captureId===scope.current)setValue(response.result);
  if(response.result.kind==='validation')addRun(response.result);
 }catch(e){setError(e.message)}finally{inflight.current=false;setBusy(false)}};
 const load=async before=>{if(inflight.current||!candidate)return;inflight.current=true;setBusy(true);setError('');const current=visit.current,captureId=candidate.id;try{
  const page=await act('integration.list',{sessionId:session.id,...(before?{before}:{})},{feedback:false});
  if(current!==visit.current)return;
  if(!page?.accepted)throw Error('Saved runs are unavailable.');
  const summaries=page.result.items.filter(row=>row.kind==='validation'&&row.captureId===captureId);
  const loaded=await Promise.all(summaries.map(row=>act('integration.read',{sessionId:session.id,id:row.id},{feedback:false})));
  if(current!==visit.current)return;
  if(loaded.some(row=>!row?.accepted))throw Error('Some saved runs could not be read.');
  setRuns(previous=>[...new Map([...previous,...loaded.map(row=>row.result)].map(row=>[row.id,row])).values()].slice(-64));setNext(page.result.nextBefore??null);
 }catch(e){if(current===visit.current)setError(e.message)}finally{inflight.current=false;setBusy(false)}};
 const selected= candidate?.repositories?.some(row=>row.repositoryPath===repository)?repository:candidate?.repositories?.[0]?.repositoryPath||'';
 const visibleRuns=runs.filter(row=>row.captureId===candidate?.id),selectedIds=Object.values(choices[candidate?.id]||{}).filter(id=>visibleRuns.some(row=>row.id===id));
 const collect=()=>{try{const declared=JSON.parse(results);if(!Array.isArray(declared))throw Error('Result declarations must be an array.');return run('integration.collect',{captureId:candidate.id,validationIds:ids.trim()?ids.split(/\s+/).filter(Boolean):selectedIds,results:declared})}catch(e){setError(e.message)}};
 return <details data-part="integration-validation"><summary>Run checks and collect results</summary>
  <p>Run a command against this saved candidate. Shell permissions still apply. Shared assignments must name the member responsible for integration. Inspection alone runs no command.</p>
  {pending&&<div role="status">A request has no confirmed response. <button className="a-soft" disabled={busy} onClick={()=>run()}>Check the same integration request</button> <button className="a-link" disabled={busy} onClick={()=>retain(null)}>Dismiss saved retry</button> Dismissal does not cancel admitted work.<details><summary>Pending request details</summary><pre className="a-state-view">{JSON.stringify(pending,null,2)}</pre></details></div>}
  {candidate?.kind==='capture'?<>
   <label htmlFor="validation-repository">Repository to validate</label><select id="validation-repository" value={selected} disabled={busy||!!pending} onChange={event=>setRepository(event.target.value)}>{candidate.repositories.map(row=><option key={row.repositoryPath} value={row.repositoryPath}>{basename(row.repositoryPath)}{candidate.repositories.filter(other=>basename(other.repositoryPath)===basename(row.repositoryPath)).length>1?' · '+row.repositoryPath:''}</option>)}</select>
   <details><summary>Validation folder</summary><code>{selected}</code></details>
   <label htmlFor="validation-command">Validation command</label><textarea id="validation-command" value={command} maxLength={16000} disabled={busy||!!pending} spellCheck={false} onChange={event=>setCommand(event.target.value)} placeholder="For example, npm test"/>
   <details><summary>Command timeout</summary><label htmlFor="validation-timeout">Command timeout (seconds)</label><input id="validation-timeout" type="number" min={1} max={3600} value={timeout} disabled={busy||!!pending} onChange={event=>setTimeout(Number(event.target.value))}/></details>
   <button className="a-soft" data-action="integration.validate" disabled={busy||!!pending||!command.trim()} onClick={()=>run('integration.validate',{captureId:candidate.id,repositoryPath:selected,command,timeout})}>Run validation command</button>
   <h4>Choose results to collect</h4><p className="a-caption">Choose one run per repository. Collect checks the native outcome and current source versions; it does not rerun commands. Unchecked repositories remain unvalidated.</p>
   <button className="a-link" disabled={busy} onClick={()=>load()}>Load saved runs for this candidate</button>
   {candidate.repositories.map(repo=><fieldset key={repo.repositoryPath}><legend>{basename(repo.repositoryPath)}</legend><details><summary>Repository folder</summary><code>{repo.repositoryPath}</code></details>
    {!visibleRuns.some(row=>row.repositoryPath===repo.repositoryPath)&&<p>No loaded validation runs for this repository.</p>}
    {visibleRuns.filter(row=>row.repositoryPath===repo.repositoryPath).map(row=><div key={row.id}><label className="a-integration-run"><input type="radio" name={'validation-run:'+repo.repositoryPath} disabled={busy||!!pending} checked={choices[candidate.id]?.[repo.repositoryPath]===row.id} onChange={()=>setChoices(previous=>({...previous,[candidate.id]:{...previous[candidate.id],[repo.repositoryPath]:row.id}}))}/><span><code>{row.command}</code><small>{row.phase?.replaceAll('_',' ')||row.status}{row.capturedAt?' · '+new Date(row.capturedAt*1000).toLocaleString():''} · Collect for terminal evidence</small></span></label><details><summary>Run identity</summary><code>{row.id}</code></details></div>)}
    {choices[candidate.id]?.[repo.repositoryPath]&&<button className="a-link" disabled={busy||!!pending} onClick={()=>setChoices(previous=>{const selection={...previous[candidate.id]};delete selection[repo.repositoryPath];return {...previous,[candidate.id]:selection}})}>Leave this repository unvalidated</button>}
   </fieldset>)}
   {next!=null&&<button className="a-link" disabled={busy} onClick={()=>load(next)}>Load older saved runs</button>}
   <button className="a-soft" data-action="integration.collect" disabled={busy||!!pending||!selectedIds.length&&!ids.trim()} onClick={collect}>Collect validation results</button>
   <details><summary>Advanced collection inputs</summary><label htmlFor="validation-ids">Validation receipt IDs (overrides the selected runs)</label><textarea id="validation-ids" className="a-state-view" value={ids} disabled={busy||!!pending} onChange={event=>setIds(event.target.value)}/><p>Leave blank to collect the selected rows. The server verifies every run belongs to this exact candidate.</p>
    <details><summary>Result files and publication reports</summary><p>Optional array of repositoryPath, references (local path@sha256:digest), and publication (status and reference). Publication is your report; it does not publish anything or confirm a remote release.</p><textarea aria-label="Integration result declarations" className="a-state-view" spellCheck={false} value={results} disabled={busy||!!pending} onChange={event=>setResults(event.target.value)}/></details>
   </details>
  </>:<p>Read or save a candidate observation above to choose its exact repositories.</p>}
  {busy&&<p role="status">Preparing integration evidence…</p>}
  {value&&<article className="a-selection-card" data-part="validation-result"><p role="status">{value.kind==='collection'?'Combined result':'Validation request'}: {value.kind==='collection'?value.status:value.phase}</p><p>Results cover only your selected commands. They do not certify test quality, complete isolation or publication.</p>
   {value.repositories?.map(row=><div key={row.repositoryPath}><p><strong>{basename(row.repositoryPath)}</strong></p><p>{row.status.replaceAll('_',' ')}{row.operation?.returncode!=null?' · Exit code '+row.operation.returncode:''} · Publication: {row.publication.status.replaceAll('_',' ')} ({row.publication.evidence.replaceAll('_',' ')})</p>{row.invocation?.command&&<p><code>{row.invocation.command}</code></p>}{row.operation?.detail&&<p>{row.operation.detail}</p>}{row.artifacts.map(item=>item.downloadUrl?<a className="a-link" key={item.reference} href={item.downloadUrl}>Download saved result</a>:<p key={item.reference}>{item.status}: {item.reference}</p>)}<details><summary>Repository identity</summary><p className="a-state-view">{row.repositoryPath}</p></details></div>)}
   <details><summary>Invocation and evidence details</summary><p className="a-state-view">Receipt: {value.id}</p><pre className="a-state-view">{JSON.stringify(value,null,2)}</pre></details>
  </article>}
  {error&&<p role="alert">{error}</p>}
 </details>;
}
