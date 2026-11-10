import React,{useRef,useState} from 'react';
import {IntegrationValidation} from './integration-validation';

export const repositoryName=path=>path?.split(/[\\/]/).filter(Boolean).pop()||'Repository';
export function repositoryChoices(session){
 const rows=[{path:session.workingDirectory||session.workspace,label:'Current execution folder'},
  ...(session.worktrees||[]).filter(row=>row.status==='ready').map(row=>({path:row.path,label:'Separate copy'})),
  {path:session.worktreeInspection?.repository?.root,label:'Last inspected repository'},
  {path:session.workspace,label:'Home folder'}];
 return [...new Map(rows.filter(row=>row.path).map(row=>[row.path,row])).values()];
}
export function IntegrationEvidence({session,act}){
 const [repositories,setRepositories]=useState([]),[repository,setRepository]=useState(''),[custom,setCustom]=useState('');
 const [dependencies,setDependencies]=useState('[]'),[manual,setManual]=useState('[]'),[purpose,setPurpose]=useState(''),[candidate,setCandidate]=useState(null),[result,setResult]=useState(null),[receipts,setReceipts]=useState([]),[next,setNext]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const inFlight=useRef(false),choices=repositoryChoices(session),source=session.worktreeInspection?.repository;
 const selected=repository||choices[0]?.path||'custom';
 const remember=value=>{setCandidate(value);try{sessionStorage.setItem('amplifier-integration-candidate:'+session.id,value.id)}catch{}};
 const run=async(action,args={})=>{if(inFlight.current)return;inFlight.current=true;setBusy(true);setError('');try{
  const response=await act(action,{sessionId:session.id,...args});
  if(!response?.accepted||!response.result)throw Error(response?.error||'No confirmed observation. Read saved observations before trying again.');
  const value=response.result;
  if(action==='integration.list'){setReceipts(args.before?prior=>[...prior,...value.items]:value.items);setNext(value.nextBefore)}
  else if(action==='worktree.inspect'){if(!value.repository?.sourceRevision)throw Error('This folder did not return an exact repository version.');addSource(value.repository)}
  else {setResult(value);if(value.kind==='capture')remember(value)}
  return value;
 }catch(e){setError(e.message||'Inspection did not finish. Read saved observations before trying again.')}finally{inFlight.current=false;setBusy(false)}};
 const addSource=value=>{if(repositories.length>=8&&!repositories.some(row=>row.repositoryPath===value.root)){setError('Choose at most eight repositories. Remove one before adding another.');return}setRepositories(prior=>[...prior.filter(row=>row.repositoryPath!==value.root),{repositoryPath:value.root,sourceRevision:value.sourceRevision,branch:value.branch,dirty:value.dirty}]);setError('')};
 const deps=()=>{const value=JSON.parse(dependencies);if(!Array.isArray(value))throw Error('Dependency expectations must be a JSON array.');return value};
 const inspect=()=>{try{return run('runtime.provenance',{modules:deps().map(row=>row.module)})}catch(e){setError(e.message)}};
 const capture=()=>{try{return run('integration.capture',{purpose,repositories:repositories.map(({repositoryPath,sourceRevision})=>({repositoryPath,sourceRevision})),dependencies:deps()})}catch(e){setError(e.message)}};
 const importManual=()=>{try{const rows=JSON.parse(manual);if(!Array.isArray(rows)||rows.length>8||rows.some(row=>typeof row.repositoryPath!=='string'||!row.repositoryPath||typeof row.sourceRevision!=='string'||!row.sourceRevision)||new Set(rows.map(row=>row.repositoryPath)).size!==rows.length)throw Error('Use up to eight distinct repository paths with exact source revisions.');setRepositories(rows.map(({repositoryPath,sourceRevision})=>({repositoryPath,sourceRevision})));setError('')}catch(e){setError(e.message)}};
 const restore=event=>{if(event.target!==event.currentTarget||!event.currentTarget.open||candidate||busy)return;try{const id=sessionStorage.getItem('amplifier-integration-candidate:'+session.id);if(id)void run('integration.read',{id})}catch{}};
 return <details data-review-target="integration" className="a-integration-work" data-part="integration-evidence" onToggle={restore}><summary>Combine and check work</summary>
  <p>Choose the repositories to check together. Save their current versions, run your checks, then collect the results. This does not merge files or publish anything.</p>
  <label htmlFor="candidate-repository">Repository or working copy</label><select id="candidate-repository" value={selected} disabled={busy} onChange={e=>setRepository(e.target.value)}>{choices.map(row=><option key={row.path} value={row.path}>{repositoryName(row.path)} · {row.label}{choices.filter(other=>repositoryName(other.path)===repositoryName(row.path)).length>1?' · '+row.path:''}</option>)}<option value="custom">Another repository folder…</option></select>
  {selected==='custom'?<><label htmlFor="candidate-folder">Repository folder</label><input id="candidate-folder" value={custom} disabled={busy} onChange={e=>setCustom(e.target.value)} placeholder="Folder containing the repository"/></>:<details><summary>Selected folder</summary><code>{selected}</code></details>}
  <button className="a-soft" disabled={busy||selected==='custom'&&!custom.trim()} data-action="worktree.inspect" onClick={()=>run('worktree.inspect',{repositoryPath:selected==='custom'?custom.trim():selected})}>Inspect and add repository</button>
  {source&&<button className="a-link" disabled={busy||repositories.length>=8&&!repositories.some(row=>row.repositoryPath===source.root)} onClick={()=>addSource(source)}>Add inspected repository</button>}
  <p className="a-caption">Inspections do not change files. Versions stay fixed until you explicitly inspect and add the repository again.</p>
  {repositories.map((row,index)=><article className="a-integration-repository" key={row.repositoryPath}><strong>{repositoryName(row.repositoryPath)}</strong> · {row.branch||'Exact source selected'}{row.dirty===true?' · Includes uncommitted changes':''}<details><summary>Folder and selected version{repositories.filter(other=>repositoryName(other.repositoryPath)===repositoryName(row.repositoryPath)).length>1?' · copy '+(index+1):''}</summary><code>{row.repositoryPath}</code><p><code>{row.sourceRevision}</code></p></details><button className="a-link" disabled={busy} onClick={()=>setRepositories(prior=>prior.filter(other=>other.repositoryPath!==row.repositoryPath))}>Remove {repositoryName(row.repositoryPath)}{repositories.filter(other=>repositoryName(other.repositoryPath)===repositoryName(row.repositoryPath)).length>1?' copy '+(index+1):''}</button></article>)}
  <label htmlFor="candidate-purpose">What are you checking?</label><input id="candidate-purpose" maxLength={500} value={purpose} onChange={e=>setPurpose(e.target.value)} placeholder="For example, app and shared library changes"/>
  <div className="a-dialog-actions"><button className="a-soft" disabled={busy||!repositories.length} data-action="integration.capture" onClick={capture}>Save candidate observation</button><button className="a-soft" disabled={busy} data-action="integration.list" onClick={()=>run('integration.list')}>Load saved observations</button></div>
  {candidate&&<section aria-label="Selected candidate" data-candidate-id={candidate.id}><h4>{candidate.purpose||'Selected candidate'}</h4><p>{candidate.repositories.length} repositories · Saved source match: {candidate.status}</p><button className="a-soft" disabled={busy} data-action="integration.check" onClick={()=>run('integration.check',{id:candidate.id})}>Recheck this candidate</button><details><summary>Candidate identity and exact versions</summary><p className="a-state-view">Receipt: {candidate.id}</p><pre className="a-state-view">{JSON.stringify(candidate.repositories,null,2)}</pre></details></section>}
  {busy&&<p role="status">Inspecting…</p>}
  {result&&<article className="a-selection-card" data-part="integration-result"><p role="status">{{capture:'Candidate',check:'Recheck',validation:'Validation request',collection:'Combined result'}[result.kind]||'Loaded sources'}: {result.kind==='validation'?result.phase:result.status}</p><p className="a-caption">Matching files does not prove a successful test or the exact bytes imported earlier.</p>{result.captureId&&result.captureId!==candidate?.id&&<button className="a-link" disabled={busy} onClick={()=>run('integration.read',{id:result.captureId})}>Select this observation’s candidate</button>}<details><summary>Observation details</summary><p className="a-state-view">Receipt: {result.id}</p><pre className="a-state-view">{JSON.stringify(result,null,2)}</pre></details></article>}
  {receipts.length>0&&<details open><summary>Saved observations</summary>{receipts.map(row=><article className="a-selection-card" data-observation-id={row.id} key={row.id}><p>{row.purpose||{capture:'Candidate',check:'Source recheck',validation:'Validation run',collection:'Combined result'}[row.kind]||'Source observation'} · {row.status}{row.capturedAt?' · '+new Date(row.capturedAt*1000).toLocaleString():''}</p><button className="a-soft" disabled={busy} data-action="integration.read" onClick={()=>run('integration.read',{id:row.id})}>{row.kind==='capture'?'Select candidate':'View observation'}</button><details><summary>Observation identity</summary><code>{row.id}</code></details></article>)}</details>}
  {next!=null&&<button className="a-soft" disabled={busy} data-action="integration.list" onClick={()=>run('integration.list',{before:next})}>Load older observations</button>}
  <IntegrationValidation key={session.id} session={session} act={act} candidate={candidate}/>
  <details><summary>Advanced source and import expectations</summary>
   <p>Manual expectations replace the selection above only when applied. They must name exact versions; no file inspection is implied.</p><label htmlFor="candidate-repositories">Repository expectations (JSON array)</label><textarea id="candidate-repositories" className="a-state-view" spellCheck={false} value={manual} onChange={e=>setManual(e.target.value)}/><button className="a-link" disabled={busy} onClick={importManual}>Apply manual repository expectations</button>
   <label htmlFor="candidate-dependencies">Dependency expectations (JSON array)</label><textarea id="candidate-dependencies" className="a-state-view" spellCheck={false} value={dependencies} onChange={e=>setDependencies(e.target.value)}/>
   <p className="a-caption">Each dependency names module, repositoryPath, relative path and expected sha256. Only already-loaded modules can be observed. An empty list checks repository versions only.</p><button className="a-soft" disabled={busy} data-action="runtime.provenance" onClick={inspect}>Inspect loaded sources</button>
  </details>
  {error&&<p role="alert">{error}</p>}
 </details>;
}
