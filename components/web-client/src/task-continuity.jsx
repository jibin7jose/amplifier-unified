import React,{useLayoutEffect,useRef,useState} from 'react';

const stamp=value=>JSON.stringify([value.task?.id,value.task?.revision,value.task?.appliedRevision,value.task?.status]);
const taskRevision=task=>JSON.stringify([task?.id,task?.revision]);
const textFields=new Set(['objective','blockedReason']);
const listFields=new Set(['constraints','questionIds','operationIds','artifactRefs','completionEvidence','corrections']);
const newer=(a,b)=>a&&b&&a.id===b.id&&(a.revision>b.revision||a.revision===b.revision&&(a.appliedRevision??0)>(b.appliedRevision??0));
function currentSnapshot(published,observed){
 if(!observed)return published;
 if(stamp(published)===observed.base)return observed.value;
 if(observed.value.committedUnavailable){
  const receipt=observed.value.receipt;
  if(!receipt||published.task?.id!==receipt.taskId||!Number.isInteger(receipt.revision)||!Number.isInteger(published.task.revision)||
   published.task.revision<receipt.revision)return observed.value;
  return published;
 }
 if(published.task?.id!==observed.value.task?.id||newer(published.task,observed.value.task))return published;
 return observed.value;
}

export function TaskContinuity({state,session,act}){
 // A visit owns its private result and edits. A -> B -> A must not revive A's
 // earlier request, and a host restart must not reuse a former owner's view.
 return <TaskContinuityView key={JSON.stringify([state.client?.hostInstanceId,session.id])} state={state} session={session} act={act}/>;
}
function TaskContinuityView({state,session,act}){
 const published=state.runtimeControl?.[session.id]?.['task.get']??{task:session.task??null,continuity:session.continuity};
 const [observed,setObserved]=useState(null),[details,setDetails]=useState(null);
 const snapshot=currentSnapshot(published,observed),detailKey=taskRevision(snapshot.task),continuity=snapshot.continuity;
 // Exact details belong to this reader and task revision, never ordinary state.
 const loaded=details?.key===detailKey?details.values:{};
 const task=snapshot.task?{...snapshot.task,...loaded}:null;
 const missingRefs=(snapshot.detailRefs||[]).filter(ref=>!Object.hasOwn(loaded,ref.args?.field));
 const detailRequired=!!snapshot.detailRequired&&(!snapshot.detailRefs?.length||missingRefs.length>0);
 const [objective,setObjective]=useState(''),[constraints,setConstraints]=useState(''),[correction,setCorrection]=useState(''),[reason,setReason]=useState(''),[evidence,setEvidence]=useState(''),[pending,setPending]=useState(false),[error,setError]=useState('');
 const alive=useRef(false),busy=useRef(false),latest=useRef(null);
 latest.current={snapshot,published,task,detailKey,detailRequired};
 useLayoutEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 useLayoutEffect(()=>{setDetails(null)},[detailKey]);
 const unavailable=snapshot.status==='unavailable',readOnly=unavailable||!!error;
 const run=async(action,args={})=>{
  if(busy.current||action!=='task.get'&&readOnly)return;
  const start=latest.current;
  if(action==='task.complete'&&(start.detailRequired||typeof start.task?.objective!=='string'||!start.task.objective.trim()))return;
  busy.current=true;setPending(true);setError('');
  try{
   const response=await act(action,{sessionId:session.id,...(action==='task.get'?{}:{expectedRevision:start.snapshot.task?.revision??0}),...args});
   if(!alive.current)return;
   if(response?.accepted!==true)throw Error(response?.error||'The saved task response was not confirmed. Load it again before making changes.');
   const value=response.result;
   if(!value||!Object.hasOwn(value,'task')||value.status&&!['available','unavailable'].includes(value.status)||
    value.task!==null&&(!value.task||typeof value.task.id!=='string'||!Number.isInteger(value.task.revision)||value.task.revision<1))throw Error('The saved task response is unavailable.');
   const owner=value.historyRef?.args?.sessionId??value.currentRef?.args?.sessionId;
   if(owner&&![session.id,session.runtimeSessionId,session.nativeIdentity].includes(owner))throw Error('The task response belongs to another chat.');
   const now=latest.current.snapshot;
   if(value.snapshotIsCurrent===false||newer(now.task,value.task)||
    stamp(now)!==stamp(start.snapshot)&&(value.status==='unavailable'||now.task?.id!==value.task?.id)
   )throw Error('The saved task changed while loading. The newer observation was kept; load again before making changes.');
   // Never publish a cold projection (including null/omitted fields) into
   // session.task or runtimeControl. Only this explicit reader retains it.
   setObserved({base:stamp(latest.current.published),value:{
    status:value.status||'available',task:value.task,continuity:value.continuity,
    continuationSupported:value.continuationSupported,detailRequired:value.detailRequired,
    detailRefs:value.detailRefs,committedUnavailable:value.status==='unavailable'&&!!value.receipt,
    receipt:value.receipt?{taskId:value.receipt.taskId,revision:value.receipt.revision}:null,
   }});
  }catch(e){if(alive.current)setError(String(e.message||e).slice(0,500))}
  finally{busy.current=false;if(alive.current)setPending(false)}
 };
 const loadDetails=async()=>{
  if(busy.current||unavailable)return;
  const start=latest.current,values={...loaded};
  busy.current=true;setPending(true);setError('');
  try{
   if(!missingRefs.length)throw Error('The missing task details are unavailable. Load the saved task again.');
   for(const ref of missingRefs){
    const args=ref.args,field=args?.field;
    if(ref.action!=='task.detail'||!args||!textFields.has(field)&&!listFields.has(field)||
     args.taskId!==start.task.id||args.expectedRevision!==start.task.revision||args.historyRevision!=null||
     ![session.id,session.runtimeSessionId,session.nativeIdentity].includes(args.sessionId))throw Error('The task detail reference changed. Load the saved task again.');
    const chunks=[];let offset=0,first=null;
    do{
     if(!alive.current)return;
     if(latest.current.detailKey!==start.detailKey||latest.current.snapshot.status==='unavailable')throw Error('The saved task changed while loading details. Load the saved task again.');
     const response=await act('task.detail',{...args,offset,limit:2000});
     if(!alive.current)return;
     if(latest.current.detailKey!==start.detailKey||latest.current.snapshot.status==='unavailable')throw Error('The saved task changed while loading details. Load the saved task again.');
     if(response?.accepted!==true)throw Error(response?.error||'The task details could not be loaded. Try loading them again.');
     const page=response.result;
     if(!page||page.taskId!==start.task.id||page.revision!==start.task.revision||page.field!==field||page.historyRevision!=null||
      page.offset!==offset||typeof page.text!=='string'||!Number.isSafeInteger(page.total)||page.total<0||typeof page.sha256!=='string'||
      first&&(page.total!==first.total||page.sha256!==first.sha256))throw Error('The task detail page changed. Load the saved task again.');
     // Server offsets count Unicode characters, not JavaScript UTF-16 units.
     const end=offset+Array.from(page.text).length;
     if(end>page.total||page.truncated!==(end<page.total)||
      (page.truncated?(end<=offset||page.nextOffset!==end):page.nextOffset!==null))throw Error('The task detail page is incomplete. Try loading the details again.');
     first??=page;chunks.push(page.text);offset=page.nextOffset;
    }while(offset!==null);
    const value=textFields.has(field)?chunks.join(''):JSON.parse(chunks.join(''));
    if(listFields.has(field)&&(!Array.isArray(value)||!value.every(row=>field==='corrections'?typeof row?.text==='string':typeof row==='string')))throw Error('The task detail could not be displayed. Load the saved task again.');
    values[field]=value;
   }
   setDetails({key:start.detailKey,values});
  }catch(e){if(alive.current)setError(String(e.message||e).slice(0,500))}
  finally{busy.current=false;if(alive.current)setPending(false)}
 };
 const open=task&&task.status!=='completed';
 const missing=missingRefs.map(ref=>ref.args?.field).filter(Boolean);
 const linked=key=>task[key]===null?`Not loaded (${task[key+'Count']??'unknown'})`:task[key]?.join(', ')||'None';
 return <section className="a-settings-section" data-part="task-continuity" aria-label="Saved task">
  <h3>Saved task</h3><p>Keep the objective, corrections, and evidence across restarts and long conversations. Saving a task does not send a message.</p>
  <button className="a-soft" disabled={pending} data-action="task.get" onClick={()=>run('task.get')}>Load saved task</button>
  {unavailable&&<p role="alert">{snapshot.committedUnavailable?'The task change was recorded, but its current state is unavailable.':'Saved task state is unavailable; this does not mean there is no task.'} Load again before making changes.</p>}
  {snapshot.status==='available'&&!task&&<p role="status">No saved task is recorded.</p>}
  {task&&<div className="a-selection-card"><p><strong>{task.objective??'Task objective not loaded'}</strong></p><p role="status">Task {task.status} · revision {task.revision}{task.appliedRevision!==task.revision?' · applies at the next request':''}</p>{task.constraints?.length>0&&<ul>{task.constraints.map((text,i)=><li key={i}>{text}</li>)}</ul>}{task.status==='blocked'&&task.blockedReason&&<p>Waiting because: {task.blockedReason}</p>}{task.corrections?.length>0&&<details><summary>Saved corrections ({task.corrections.length})</summary><ul>{task.corrections.map((row,i)=><li key={i}>{row.text}</li>)}</ul></details>}{task.completionEvidence?.length>0&&<ul>{task.completionEvidence.map((text,i)=><li key={i}>{text}</li>)}</ul>}<details><summary>Linked questions, operations, and artifacts</summary>{['questionIds','operationIds','artifactRefs'].map(key=><p key={key}>{key==='questionIds'?'Questions':key==='operationIds'?'Operations':'Artifacts'}: {linked(key)}</p>)}</details>{detailRequired&&<><p role="status">Some details are not loaded in this bounded view{missing.length?': '+missing.join(', '):'.'}{'. Completion requires the complete task details.'}</p><button className="a-soft" data-action="task.detail" disabled={pending||unavailable} onClick={loadDetails}>Load missing details</button></>}</div>}
  {!open&&!readOnly&&<><label htmlFor="task-objective">Task objective</label><textarea id="task-objective" value={objective} onChange={e=>setObjective(e.target.value)}/><label htmlFor="task-constraints">Constraints (one per line)</label><textarea id="task-constraints" value={constraints} onChange={e=>setConstraints(e.target.value)}/><button className="a-primary" data-action="task.create" disabled={pending||!objective.trim()} onClick={()=>run('task.create',{objective:objective.trim(),constraints:constraints.split('\n').map(s=>s.trim()).filter(Boolean)})}>Save task</button></>}
  {open&&<><label htmlFor="task-correction">Correction to retain</label><textarea id="task-correction" value={correction} onChange={e=>setCorrection(e.target.value)}/><button className="a-soft" data-action="task.update" disabled={pending||readOnly||!correction.trim()} onClick={()=>run('task.update',{correction:correction.trim()})}>Save correction</button><div className="a-dialog-actions">{task.status==='active'?<button className="a-soft" data-action="task.pause" disabled={pending||readOnly} onClick={()=>run('task.pause')}>Pause task</button>:<button className="a-primary" data-action="task.resume" disabled={pending||readOnly} onClick={()=>run('task.resume')}>Resume task</button>}</div><p className="a-caption">Pause stops future automatic continuation. Current work can finish. Resume does not send a message or answer a pending question.</p><details><summary>Record a blocker or completion</summary><label htmlFor="task-blocker">What is blocking this task?</label><input id="task-blocker" value={reason} onChange={e=>setReason(e.target.value)}/><button className="a-soft" data-action="task.block" disabled={pending||readOnly||!reason.trim()} onClick={()=>run('task.block',{reason:reason.trim()})}>Mark blocked</button><label htmlFor="task-evidence">Evidence that the objective is complete (one per line)</label><textarea id="task-evidence" value={evidence} onChange={e=>setEvidence(e.target.value)}/><button className="a-soft" data-action="task.complete" disabled={pending||readOnly||detailRequired||typeof task.objective!=='string'||!task.objective.trim()||!evidence.trim()} onClick={()=>run('task.complete',{condition:task.objective,evidence:evidence.split('\n').map(s=>s.trim()).filter(Boolean)})}>Complete task with evidence</button></details></>}
  {task&&snapshot.continuationSupported===false&&<p role="alert">This runtime cannot automatically continue saved tasks. Update its task support before resuming.</p>}
  {continuity&&<p className="a-caption">Conversation continuity: {continuity.status==='not_observed_cold'?'not observed for this cold read':continuity.supported?continuity.status:'not available in this context module'}{continuity.reason?` — ${continuity.reason}`:''}. The original transcript remains available.</p>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
 </section>
}
