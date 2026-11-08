import React,{useEffect,useRef,useState} from 'react';
import './assignment-task-list.css';

const emptyDraft=()=>({title:'',description:'',blockedReason:'',dependsOn:[]});
const load=key=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')||{draft:emptyDraft()}}catch{return {draft:emptyDraft()}}};
const firstPage=(view='active')=>({view,cursors:[null]});
const pageArgs=page=>({view:page.view,...(page.cursors.at(-1)?{after:page.cursors.at(-1)}:{}),limit:8});
const dependencyLabel=(id,labels)=>labels?.[id]||'Prerequisite · '+id;

export function AssignmentTaskList(props){return <TaskList key={props.sessionId} {...props}/>}

function TaskList({dispatch,sessionId,compact=false,enabled=true,onAssignmentChange}){
 const storageKey='amplifier-assignment-tasks:'+sessionId;
 const [saved,setSaved]=useState(()=>load(storageKey)),[view,setView]=useState(null),[open,setOpen]=useState(false);
 const [page,setPage]=useState(firstPage),[error,setError]=useState(''),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0);
 const [selected,setSelected]=useState(()=>saved.draft?.taskId||null),[detail,setDetail]=useState(null),[detailRefresh,setDetailRefresh]=useState(0);
 const [detailError,setDetailError]=useState(''),[formOpen,setFormOpen]=useState(!!saved.draft?.taskId);
 const [visible,setVisible]=useState(()=>typeof document==='undefined'||!document.hidden);
 const alive=useRef(true),inflight=useRef(false),savedRef=useRef(saved),viewRef=useRef(null),observationEpoch=useRef(0),observer=useRef(null),detailObserver=useRef(null),detailSequence=useRef(0),editIntent=useRef(null);
 const invalidate=()=>{editIntent.current=null;observationEpoch.current++;observer.current?.abort();detailObserver.current?.abort()};
 const changePage=next=>{invalidate();setView(null);setError('');setPage(next)};
 const reload=()=>{changePage(firstPage(page.view));setRefresh(n=>n+1)};
 useEffect(()=>()=>{alive.current=false;invalidate()},[]);
 useEffect(()=>{if(typeof document==='undefined')return;const changed=()=>{invalidate();setVisible(!document.hidden)};document.addEventListener('visibilitychange',changed);return()=>document.removeEventListener('visibilitychange',changed)},[]);
 const persist=next=>{savedRef.current=next;setSaved(next);try{sessionStorage.setItem(storageKey,JSON.stringify(next))}catch{setError('This browser could not save the pending task change. Keep this view open until its response is confirmed.')}};
 useEffect(()=>{
  if(!open||!enabled||!visible||busy)return;
  let active=true;const epoch=observationEpoch.current,controller=new AbortController();observer.current=controller;
  (async()=>{
   try{
    const args={sessionId,...pageArgs(page)};
    let response=await dispatch('assignment.tasks.list',args,{feedback:false,presentation:true,signal:controller.signal});
    while(active&&epoch===observationEpoch.current){
     const next=response.result,previous=viewRef.current;
     const older=previous?.assignmentId===next.assignmentId&&previous?.assignmentRevision>next.assignmentRevision;
     if(!older){if(onAssignmentChange?.(next.assignmentControl||null)===false)return;viewRef.current=next;setView(next)}
     response=await dispatch('assignment.tasks.wait',{...args,afterVersion:response.result.version,waitMs:30000},{feedback:false,presentation:true,signal:controller.signal});
    }
   }catch(failure){if(active&&epoch===observationEpoch.current)setError(failure.message)}
  })();
  return()=>{active=false;controller.abort();if(observer.current===controller)observer.current=null};
 },[dispatch,sessionId,open,enabled,visible,busy,refresh,page,onAssignmentChange]);
 const inspect=(id,editing=false)=>{
  detailSequence.current++;detailObserver.current?.abort();editIntent.current=editing?{taskId:id}:null;setSelected(id);setDetail(null);setDetailError('');setDetailRefresh(n=>n+1);
 };
 useEffect(()=>{
  if(!selected||!view?.assignmentId||!open||!enabled||!visible||busy)return;
  let active=true;const epoch=observationEpoch.current,sequence=detailSequence.current,edit=editIntent.current,controller=new AbortController();detailObserver.current=controller;
  setDetail(null);setDetailError('');
  (async()=>{try{
   const response=await dispatch('assignment.tasks.read',{sessionId,taskId:selected},{feedback:false,presentation:true,signal:controller.signal});
   if(!active||epoch!==observationEpoch.current||sequence!==detailSequence.current)return;
   const next=response.result,current=viewRef.current;
   if(current?.assignmentId!==next.assignmentId||current.assignmentRevision>next.assignmentRevision)return;
   if(onAssignmentChange?.(next.assignmentControl||null)===false)return;
   viewRef.current={...current,assignmentRevision:next.assignmentRevision};
   setDetail(next);
   if(edit&&edit===editIntent.current){
    editIntent.current=null;const task=next.task;
    persist({...savedRef.current,draft:{title:task.title,description:task.description,blockedReason:task.blockedReason,dependsOn:task.dependsOn,dependencyLabels:Object.fromEntries(next.dependencies.filter(row=>row.available).map(row=>[row.id,row.title])),taskId:task.id,expectedRevision:task.revision}});setFormOpen(true);
   }
  }catch(failure){if(active&&epoch===observationEpoch.current&&sequence===detailSequence.current){if(editIntent.current===edit)editIntent.current=null;setDetailError(failure.message)}}})();
  return()=>{active=false;controller.abort();if(editIntent.current===edit)editIntent.current=null;if(detailObserver.current===controller)detailObserver.current=null};
 },[dispatch,sessionId,selected,detailRefresh,view?.assignmentId,view?.assignmentRevision,open,enabled,visible,busy,refresh,onAssignmentChange]);
 const tasks=view?.tasks||[],members=new Map((view?.members||[]).map(row=>[row.sessionId,row.label]));
 const member=id=>id===sessionId?'This chat':members.get(id)||'Unavailable member';
 const draft=saved.draft||emptyDraft(),pending=saved.pending;
 const field=(key,value)=>persist({...savedRef.current,draft:{...savedRef.current.draft,[key]:value}});
 const discardDraft=()=>{editIntent.current=null;persist({...savedRef.current,draft:emptyDraft()})};
 const run=async(verb,args)=>{
  if(inflight.current)return;invalidate();inflight.current=true;setBusy(true);setError('');
  const request=savedRef.current.pending||{action:'assignment.tasks.'+verb,args:{sessionId,...args},id:crypto.randomUUID()};
  persist({...savedRef.current,pending:request});
  try{
   const response=await dispatch(request.action,request.args,{id:request.id,feedback:false});
   if(response?.accepted!==true||!response.result?.task||request.args.taskId&&response.result.task.id!==request.args.taskId)throw Error('The task change has no confirmed receipt. Check the same change before retrying.');
   if(alive.current)persist({...savedRef.current,pending:null,draft:request.action.endsWith('.create')||request.action.endsWith('.update')?emptyDraft():savedRef.current.draft});
  }catch(failure){if(alive.current){if(failure.status>=400&&failure.status<500&&failure.status!==408)persist({...savedRef.current,pending:null});setError(failure.message)}}
  finally{inflight.current=false;if(alive.current){setBusy(false);setRefresh(n=>n+1)}}
 };
 const act=(verb,task,extra={})=>run(verb,{taskId:task.id,expectedRevision:task.revision,...extra});
 const blocked=busy||!!pending||!enabled,draftBlocked=blocked||!!editIntent.current,canManage=view?.canManage,active=view?.assignmentStatus==='active';
 const edited=draft.taskId&&(detail?.task.id===draft.taskId?detail.task:tasks.find(task=>task.id===draft.taskId));
 const staleEdit=draft.taskId&&edited&&edited.revision!==draft.expectedRevision;
 const uncheckedEdit=draft.taskId&&!edited;
 return <details className={'a-assignment-tasks'+(compact?' is-compact':'')} onToggle={event=>{if(event.target===event.currentTarget){invalidate();setOpen(event.currentTarget.open)}}}>
  <summary>Shared task list</summary>
  <p>Optional work items for this assignment. Claims record responsibility; they do not start work or lock files.</p>
  <p>Worker activity is observed separately from task readiness. A paused or blocked chat keeps its responsibility.</p>
  {error&&<p role="alert">{error}</p>}
  <button className="a-link" disabled={busy} onClick={reload}>Refresh tasks</button>
  {pending&&<div role="status"><p>A change has no confirmed response. Check that same change before trying another.</p><button className="a-soft" disabled={busy} onClick={()=>run()}>Check the same task change</button><button className="a-link" disabled={busy} onClick={()=>{persist({...savedRef.current,pending:null});setError('');reload()}}>Discard local request and inspect current tasks</button></div>}
  {view&&!view.assignmentId&&<p>This independent chat has no shared task list. Start an assignment or commission a helper to use one.</p>}
  {(view?.assignmentId||viewRef.current?.assignmentId)&&<>
   <PageNavigation page={page} result={view} disabled={busy} onChange={changePage}/>
   {!view&&<p role="status">Loading tasks…</p>}
   {view&&<>
    {!active&&<p role="status">Assignment stopped. Claims stay visible; only explicit release or cancellation is available.</p>}
    {!tasks.length&&<p>{page.cursors.length>1?'No tasks on this page. Go back or refresh to see current tasks.':page.view==='active'?'No active tasks. Completed and cancelled tasks are in Task history.':'No completed or cancelled tasks.'}</p>}
    {view.coverage?.changed&&<p role="status">Tasks changed while you browsed. Refresh tasks to include earlier items.</p>}
    <ul className="a-shared-task-items">{tasks.map(task=>{
     const terminal=['completed','cancelled'].includes(task.status),claim=task.claim,holder=claim?.sessionId===sessionId;
     const status=task.status==='completed'?'Reported complete':task.status==='cancelled'?'Cancelled':claim?claim.status==='current'?'Claimed':'Claim needs review':task.readiness==='blocked'?'Blocked':'Ready';
     return <li key={task.id} data-task-id={task.id}>
      <strong>{task.title}</strong><span className="a-shared-task-status">{status}</span>
      {claim&&<p>{terminal?'Last responsibility: ':'Responsibility: '}{member(claim.sessionId)}{!terminal&&claim.status!=='current'?' — '+claim.detail:''}</p>}
      {!terminal&&task.worker&&<p>Worker: {task.worker.label}{task.worker.blockedReason?' — '+task.worker.blockedReason:''}{task.worker.blockedReasonTruncated?'…':''}</p>}
      {!!task.blockedCount&&<p>Waiting for {task.blockedCount} prerequisite{task.blockedCount===1?'':'s'}. Open task details to inspect them.</p>}
      {task.blockedReason&&<p>Blocked: {task.blockedReason}{task.blockedReasonTruncated?'…':''}</p>}
      <button className="a-link" disabled={busy} onClick={()=>inspect(task.id)}>View task details</button>
      {!terminal&&<div className="a-shared-task-actions">
       {active&&!claim&&task.readiness==='ready'&&<button className="a-soft" disabled={blocked} onClick={()=>act('claim',task)}>Claim for this chat</button>}
       {claim&&(holder||canManage)&&<button className="a-soft" disabled={blocked} onClick={()=>act('release',task,{claimId:claim.id})}>Release claim</button>}
       {holder&&active&&claim.status==='current'&&<CompletionForm disabled={blocked} claimId={claim.id} revision={task.revision} value={saved.completions?.[task.id]} onChange={value=>persist({...savedRef.current,completions:{...savedRef.current.completions,[task.id]:value}})} onComplete={(report,binding)=>run('complete',{taskId:task.id,expectedRevision:binding.revision,claimId:binding.claimId,report})}/>}
       {canManage&&<details><summary>Manage task</summary>
        <p>Releasing, cancelling or reassigning changes responsibility only. Prior file or tool work may continue; use the existing chat or assignment Stop when needed.</p>
        {active&&!claim&&<button className="a-link" disabled={blocked} onClick={()=>inspect(task.id,true)}>Edit task</button>}
        {active&&task.readiness==='ready'&&<ReassignForm key={task.revision} members={view.members} disabled={blocked} current={claim?.sessionId} onAssign={holderSessionId=>act('reassign',task,{holderSessionId})}/>}
        <button className="a-link" disabled={blocked} onClick={()=>act('cancel',task)}>Cancel task</button>
       </details>}
      </div>}
     </li>;
    })}</ul>
   </>}
   {selected&&<TaskDetails detail={detail} error={detailError} member={member} onClose={()=>inspect(null)}/>}
   {canManage&&active&&<details open={formOpen||!!draft.taskId} onToggle={event=>{if(event.target===event.currentTarget)setFormOpen(event.currentTarget.open)}}><summary>{draft.taskId?'Edit shared task':'Add shared task'}</summary>
    <form onSubmit={event=>{event.preventDefault();const content={title:draft.title,description:draft.description,blockedReason:draft.blockedReason,dependsOn:draft.dependsOn};run(draft.taskId?'update':'create',draft.taskId?{...content,taskId:draft.taskId,expectedRevision:draft.expectedRevision}:{...content,expectedAssignmentRevision:view.assignmentRevision})}}>
     <label>Task title<input data-private="true" value={draft.title} maxLength={200} required disabled={draftBlocked} onChange={event=>field('title',event.target.value)}/></label>
     <label>Details<textarea data-private="true" value={draft.description} maxLength={4000} disabled={draftBlocked} onChange={event=>field('description',event.target.value)}/></label>
     <label>Blocked until<input data-private="true" value={draft.blockedReason} maxLength={1000} placeholder="Optional reason" disabled={draftBlocked} onChange={event=>field('blockedReason',event.target.value)}/></label>
     <Prerequisites dispatch={dispatch} sessionId={sessionId} taskId={draft.taskId} selected={draft.dependsOn} labels={draft.dependencyLabels} disabled={draftBlocked} enabled={open&&visible&&enabled&&(formOpen||!!draft.taskId)} revision={view.assignmentRevision} onChange={(ids,labels)=>persist({...savedRef.current,draft:{...savedRef.current.draft,dependsOn:ids,dependencyLabels:labels}})}/>
     {staleEdit&&<p role="alert">This task changed. Your edits are retained; inspect it again before saving.</p>}
     {uncheckedEdit&&<p role="status">Your edits are retained. <button type="button" className="a-link" onClick={()=>inspect(draft.taskId)}>Inspect edited task</button> before saving.</p>}
     <button className="a-soft" disabled={draftBlocked||staleEdit||uncheckedEdit||!draft.title.trim()}>{draft.taskId?'Save task':'Add task'}</button>
     <button type="button" className="a-link" disabled={busy} onClick={discardDraft}>Discard task draft</button>
    </form>
   </details>}
  </>}
 </details>;
}

function TaskDetails({detail,error,member,onClose}){
 return <section className="a-shared-task-detail" aria-label="Task details">
    <button className="a-link" onClick={onClose}>Close task details</button>
    {error?<p role="alert">{error}</p>:!detail?<p role="status">Loading task details…</p>:<>
     <h4>{detail.task.title}</h4><p>{detail.task.description}</p>
     {detail.task.blockedReason&&<p>Blocked: {detail.task.blockedReason}</p>}
     {!!detail.dependencies.length&&<><p>Prerequisites:</p><ul>{detail.dependencies.map(row=><li key={row.id}>{row.available?row.title:'Unavailable prerequisite · '+row.id}{row.available?' · '+row.status:''}</li>)}</ul></>}
     {detail.task.completion&&<div><p>{detail.task.completion.report}</p><small>Reported by {member(detail.task.completion.reportedBy)}. Artifact verification and integration remain separate.</small>{!!detail.task.completion.references?.length&&<details><summary>Reported references</summary><ul>{detail.task.completion.references.map((value,index)=><li key={index}>{value}</li>)}</ul></details>}</div>}
    </>}
   </section>;
}

function PageNavigation({page,result,disabled,onChange,prefix='tasks'}){
 return <nav className="a-task-pages" aria-label={prefix==='tasks'?'Task pages':'Prerequisite pages'}>
  <button type="button" className="a-soft" aria-pressed={page.view==='active'} disabled={disabled} onClick={()=>onChange(firstPage('active'))}>{prefix==='tasks'?'Active tasks':'Active prerequisites'}</button>
  <button type="button" className="a-soft" aria-pressed={page.view==='history'} disabled={disabled} onClick={()=>onChange(firstPage('history'))}>{prefix==='tasks'?'Task history':'Prerequisite history'}</button>
  <button type="button" className="a-link" disabled={disabled||page.cursors.length===1} onClick={()=>onChange({...page,cursors:page.cursors.slice(0,-1)})}>Previous {prefix}</button>
  <span>Page {page.cursors.length}</span>
  <button type="button" className="a-link" disabled={disabled||!result?.nextAfter} onClick={()=>onChange({...page,cursors:[...page.cursors,result.nextAfter]})}>Next {prefix}</button>
 </nav>;
}

function Prerequisites({dispatch,sessionId,taskId,selected,labels,disabled,enabled,revision,onChange}){
 const [page,setPage]=useState(firstPage),[result,setResult]=useState(null),[error,setError]=useState('');
 useEffect(()=>{
  if(!enabled||disabled)return;
  let active=true;const controller=new AbortController();setResult(null);setError('');
  dispatch('assignment.tasks.list',{sessionId,...pageArgs(page)},{feedback:false,presentation:true,signal:controller.signal}).then(response=>{if(active)setResult(response.result)}).catch(failure=>{if(active)setError(failure.message)});
  return()=>{active=false;controller.abort()};
 },[dispatch,sessionId,page,enabled,disabled,revision]);
 const choose=(id,checked,title)=>{const ids=checked?[...selected,id]:selected.filter(value=>value!==id);onChange(ids,Object.fromEntries(ids.map(value=>[value,value===id&&title?title:labels?.[value]||''])))};
 return <fieldset disabled={disabled}><legend>Wait for reported completion</legend>
  {selected.length?<ul aria-label="Selected prerequisites">{selected.map(id=><li key={id}>{dependencyLabel(id,labels)} <button type="button" className="a-link" onClick={()=>choose(id,false)}>Remove prerequisite {dependencyLabel(id,labels)}</button></li>)}</ul>:<p>No prerequisites selected.</p>}
  <PageNavigation page={page} result={result} disabled={disabled} onChange={setPage} prefix="prerequisites"/>
  {error&&<p role="alert">{error}</p>}
  {result?.coverage?.changed&&<p>Prerequisites changed while you browsed. Choose the view again to refresh from the first page.</p>}
  {(result?.tasks||[]).filter(task=>task.id!==taskId).map(task=><label key={task.id}><input type="checkbox" checked={selected.includes(task.id)} onChange={event=>choose(task.id,event.target.checked,task.title)}/>{task.title}</label>)}
 </fieldset>;
}

function CompletionForm({disabled,claimId,revision,value,onChange,onComplete}){
 const report=value?.report||'',binding=value?.binding||{claimId,revision};
 const stale=binding.claimId!==claimId||binding.revision!==revision;
 return <details><summary>Report completion</summary><form onSubmit={event=>{event.preventDefault();if(!stale)onComplete(report,binding)}}><label>Completion report<textarea data-private="true" required maxLength={4000} value={report} disabled={disabled} onChange={event=>onChange({report:event.target.value,binding})}/></label><p>This reports the holder’s result; it does not verify artifacts or run dependent tasks.</p>{stale&&<p role="alert">Responsibility changed. Your report is retained for the earlier claim. Review it before using it for the current claim. <button type="button" className="a-link" disabled={disabled} onClick={()=>onChange({report,binding:{claimId,revision}})}>Use reviewed report for current claim</button></p>}<button className="a-soft" disabled={disabled||stale||!report.trim()}>Mark reported complete</button></form></details>;
}
function ReassignForm({members,disabled,current,onAssign}){
 const [holder,setHolder]=useState(current||'');
 return <form onSubmit={event=>{event.preventDefault();onAssign(holder)}}><label>Assign responsibility<select value={holder} disabled={disabled} onChange={event=>setHolder(event.target.value)}><option value="">Choose a member</option>{members.map(row=><option key={row.sessionId} value={row.sessionId}>{row.label}</option>)}</select></label><button className="a-soft" disabled={disabled||!holder}>Reassign task</button></form>;
}
