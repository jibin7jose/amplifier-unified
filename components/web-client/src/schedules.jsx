import React,{createContext,useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';

const parts=(value,timezone)=>Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(row=>[row.type,row.value]));
const localFields=(value,timezone)=>{const p=parts(value,timezone);return {startDate:`${p.year}-${p.month}-${p.day}`,localTime:`${p.hour}:${p.minute}`}};
const emptyDraft=()=>{
 const timezone=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
 return {prompt:'',destination:'same_task',newTaskTitle:'Scheduled task',newTaskMaxTurns:1,kind:'monitor',recurrence:'daily',timezone,...localFields(Date.now()+3600000,timezone),intervalMinutes:60,weekdays:[0],missedRunPolicy:'latest',notificationPolicy:'changes'};
};
const fromRecord=record=>({prompt:record.prompt,destination:record.destination||'same_task',newTaskTitle:record.newTaskTitle||'Scheduled task',newTaskMaxTurns:record.newTaskMaxTurns||1,kind:record.kind,recurrence:record.spec.kind,timezone:record.spec.timezone,...(record.spec.startAt?localFields(record.spec.startAt,record.spec.timezone):{startDate:record.spec.startDate,localTime:record.spec.localTime}),intervalMinutes:(record.spec.intervalSeconds||3600)/60,weekdays:[...(record.spec.weekdays||[0])],missedRunPolicy:record.missedRunPolicy,notificationPolicy:record.notificationPolicy});
const configuration=draft=>({prompt:draft.prompt,destination:draft.destination,...(draft.destination==='new_task'?{newTaskTitle:draft.newTaskTitle,newTaskMaxTurns:Number(draft.newTaskMaxTurns)}:{}),kind:draft.kind,missedRunPolicy:draft.missedRunPolicy,notificationPolicy:draft.notificationPolicy,spec:{kind:draft.recurrence,timezone:draft.timezone,startDate:draft.startDate,localTime:draft.localTime,...(draft.recurrence==='interval'?{intervalSeconds:Number(draft.intervalMinutes)*60}:{}),...(draft.recurrence==='weekly'?{weekdays:draft.weekdays}:{})}});
const unknown=record=>record?.runs?.some(run=>run.phase==='unknown');
const missedLabel=value=>value==='skip'?'Skip occurrences more than a minute late':'Run only the latest missed occurrence';
const notifyLabel=value=>value==='changes'?'Changes and failures':value==='always'?'Every result and failure':'Failures only';
const repeats=spec=>spec.kind==='interval'?`Every ${spec.intervalSeconds/60} minutes`:spec.kind==='weekly'?`Weekly: ${(spec.weekdays||[]).map(day=>['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][day]).join(', ')} at ${spec.localTime}`:spec.kind==='once'?`Once: ${spec.startAt||`${spec.startDate} ${spec.localTime}`}`:`Daily at ${spec.localTime}`;
const startDate=spec=>spec.startAt?localFields(spec.startAt,spec.timezone).startDate:spec.startDate;
// Private browser-tab drafts only: no backend writes, preview hashes or authority.
// Remounting a panel restores details, never a previous confirmation.
const retainedDrafts=new Map();
const targetDrafts=new Map();
function rememberDraft(owner,value){
 const drafts=targetDrafts.get(owner)||new Map();
 drafts.set(value.editing?.id||null,value);targetDrafts.set(owner,drafts);retainedDrafts.set(owner,value);
 return value;
}
// Explicit Create intent is local to this browser tab and owner. It cannot
// carry a schedule ID or activation preview. Keep other target drafts intact.
export function prepareNewSchedule(owner){
 return rememberDraft(owner,targetDrafts.get(owner)?.get(null)||{draft:emptyDraft(),editing:null});
}
export const ScheduleVisibilityContext=createContext(true);

export function ScheduleControls({session,act,focused=false}){
 const visible=useContext(ScheduleVisibilityContext);
 return session&&visible?<OwnerScheduleControls key={session.id} session={session} act={act} focused={focused}/>:null;
}

function OwnerScheduleControls({session,act,focused}){
 const owner=session.id;
 const [editor,setEditor]=useState(()=>retainedDrafts.get(owner)||{draft:emptyDraft(),editing:null});
 const {draft,editing}=editor;
 const [open,setOpen]=useState(focused),[review,setReview]=useState(null),[busy,setBusy]=useState(false);
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[evidence,setEvidence]=useState({}),[focusRevision,setFocusRevision]=useState(0);
 const mounted=useRef(true),pending=useRef(false),version=useRef(0),editorRef=useRef(editor),reviewRef=useRef(null),consumed=useRef(new Set());
 const promptRef=useRef(null),triggerRef=useRef(null),editorHeading=useRef(null),reviewHeading=useRef(null),latest=useRef(null);
 const records=session.schedules||[];
 const selected=editing?records.find(record=>record.id===editing.id):null;
 const ownerIdentity=JSON.stringify([owner,session.executionRevision,session.interruptionRevision,session.workspace,session.workingDirectory,session.bundle,session.selection,session.task?.id,session.task?.revision,session.configurationBusy,session.fileAccessPending]);
 const context=JSON.stringify([ownerIdentity,editing?.id,selected?.revision,selected?.status,selected?.runs?.filter(run=>run.phase==='unknown').map(run=>[run.id,run.revision])]);
 latest.current={records,ownerIdentity,context};
 const clearReview=()=>{reviewRef.current=null;setReview(null)};
 const invalidate=()=>{version.current++;clearReview()};
 const remember=value=>{editorRef.current=value;rememberDraft(owner,value);setEditor(value)};
 const edit=patch=>{if(!mounted.current||blockedReason())return;invalidate();remember({...editorRef.current,draft:{...editorRef.current.draft,...patch}});setNotice('')};
 function blockedReason(target=editorRef.current.editing){
  if(!target)return '';
  const row=latest.current.records.find(record=>record.id===target.id);
  if(!row||row.sessionId!==owner)return 'Refresh schedules to inspect this schedule in its owning chat.';
  if(unknown(row))return 'Reconcile the unknown prior run below before editing or activating this schedule. Nothing will be replayed.';
  if(row.status==='cancelled')return 'This schedule is cancelled. Create a new schedule explicitly.';
  if(row.revision!==target.revision)return 'This schedule changed. Use Review schedule to load its latest saved details.';
  return '';
 }
 const blocked=blockedReason();
 const visibleReview=review&&review.version===version.current&&review.context===context&&!blocked?review:null;
 useLayoutEffect(()=>{mounted.current=true;return()=>{mounted.current=false;version.current++;reviewRef.current=null}},[]);
 useLayoutEffect(()=>{clearReview()},[context]);
 useLayoutEffect(()=>{if(open)(blocked?editorHeading.current:promptRef.current)?.focus();else if(focusRevision)triggerRef.current?.focus()},[open,focusRevision]);
 useLayoutEffect(()=>{if(visibleReview)reviewHeading.current?.focus()},[visibleReview]);

 async function request(action,args={},onSuccess,ownerBound=true){
  if(!mounted.current||pending.current)return;
  pending.current=true;setBusy(true);setError('');
  try{
   const response=await act(action,ownerBound?{...args,sessionId:owner}:args);
   if(response?.accepted!==true)throw Error(response?.error?.message||response?.error||'The result could not be confirmed. Refresh schedules and inspect the result before trying again.');
   if(mounted.current)onSuccess?.(response.result);
   return response;
  }catch(cause){if(mounted.current)setError(cause.message||String(cause))}
  finally{pending.current=false;if(mounted.current)setBusy(false)}
 }
 // This read is passive: entering the editor never prepares or starts a runtime.
 useEffect(()=>{if(focused)request('schedule.list')},[focused]);
 function openEditor(){
  if(!mounted.current)return;
  invalidate();setOpen(true);setNotice('');setError('');setFocusRevision(value=>value+1);
 }
 function createSchedule(){
  if(!mounted.current)return;
  remember(prepareNewSchedule(owner));
  openEditor();
 }
 function cancel(){
  if(!mounted.current)return;
  invalidate();setOpen(false);setNotice('Unsaved details kept in this tab');setFocusRevision(value=>value+1);
 }
 function load(record){
  if(!mounted.current||pending.current||record.sessionId!==owner||unknown(record)||record.status==='cancelled')return;
  const current=latest.current.records.find(row=>row.id===record.id);
  if(current?.revision!==record.revision||unknown(current))return;
  invalidate();
  const retained=editorRef.current.editing;
  if(retained?.id!==record.id||retained?.revision!==record.revision){
   const cached=targetDrafts.get(owner)?.get(record.id);
   remember(cached?.editing?.revision===record.revision?cached:{draft:fromRecord(record),editing:{id:record.id,revision:record.revision}});
  }
  setOpen(true);setNotice('');setError('');setFocusRevision(value=>value+1);
 }
 async function preview(){
  if(!mounted.current||pending.current||!open||blockedReason()||!editorRef.current.draft.prompt.trim())return;
  clearReview();const current=version.current,identity=latest.current.context,value=editorRef.current;
  const config=configuration(value.draft);
  await request('schedule.preview',config,result=>{
   if(current!==version.current||identity!==latest.current.context||blockedReason())return;
   if(!result?.previewHash||result.binding?.sessionId!==owner)throw Error('The schedule review did not identify this owning chat. Refresh schedules before reviewing again.');
   const confirmed={...result,version:current,context:identity,config,editing:value.editing};
   reviewRef.current=confirmed;setReview(confirmed);
  });
 }
 async function save(){
  const value=reviewRef.current;
  if(!mounted.current||pending.current||!value||value.version!==version.current||value.context!==latest.current.context||blockedReason()||!value.occurrences?.length)return;
  // One-use confirmation is consumed synchronously, even if the response is lost.
  // The ref gate also covers two clicks before React commits disabled state.
  clearReview();const current=version.current,identity=latest.current.ownerIdentity;
  await request(value.editing?'schedule.update':'schedule.create',{...value.config,...(value.editing?{id:value.editing.id}:{}),expectedRevision:value.editing?.revision??0,previewHash:value.previewHash},()=>{
   if(current!==version.current||identity!==latest.current.ownerIdentity)return;
   targetDrafts.get(owner)?.delete(value.editing?.id||null);remember(prepareNewSchedule(owner));
   setOpen(false);setNotice('Schedule saved. Check its current state below.');setFocusRevision(value=>value+1);
  });
 }
 const recordKey=record=>`schedule:${record.id}:${record.revision}`;
 function mutateRecord(action,record){
  if(!mounted.current||pending.current||record.sessionId!==owner||consumed.current.has(recordKey(record)))return;
  const current=latest.current.records.find(row=>row.id===record.id);
  if(current?.revision!==record.revision)return;
  consumed.current.add(recordKey(record));invalidate();
  return request(action,{id:record.id,expectedRevision:record.revision});
 }
 function reconcile(record,row,resolution){
  const key=`run:${row.id}:${row.revision}`;
  if(!mounted.current||pending.current||record.sessionId!==owner||consumed.current.has(key)||!evidence[row.id]?.trim())return;
  const current=latest.current.records.find(item=>item.id===record.id)?.runs?.find(item=>item.id===row.id);
  if(current?.revision!==row.revision||current.phase!=='unknown')return;
  consumed.current.add(key);invalidate();
  return request('schedule.reconcile',{runId:row.id,expectedRevision:row.revision,resolution,evidence:evidence[row.id]});
 }

 return <section className="a-settings-section a-schedule-editor" aria-label="Scheduled work" data-part="schedules">
  <h3>Scheduled work</h3>
  <p className="a-schedule-editor-owner">Owning chat: <strong>{session.title||'Untitled chat'}</strong></p>
  <p className="a-caption">Workspace: <span data-part="schedule-owner-workspace">{session.workspace||'Unavailable'}</span></p>
  <p>Save instructions for this chat to run later. Each run uses normal tool permissions. Stops and changes to the reviewed configuration require schedule review.</p>
  <div className="a-dialog-actions">
   <button type="button" ref={editing?undefined:triggerRef} className="a-primary" onClick={createSchedule} aria-expanded={!editing&&open} aria-controls="schedule-editor-form">Create schedule</button>
   {editing&&!open&&<button type="button" ref={triggerRef} className="a-soft" onClick={openEditor} aria-expanded={open} aria-controls="schedule-editor-form">Continue editing schedule</button>}
   <button type="button" className="a-soft" data-action="schedule.list" disabled={busy} onClick={()=>request('schedule.list')}>Refresh schedules</button>
  </div>
  {error&&<p role="alert">{error}</p>}
  {notice&&<p role="status">{notice}</p>}
  {busy&&<p role="status">Waiting for the schedule receipt…</p>}
  {open&&<div id="schedule-editor-form" className="a-schedule-editor-form">
   <h4 ref={editorHeading} tabIndex={-1}>{editing?'Review this schedule':'New schedule'}</h4>
   {editing&&<p className="a-caption">Editing saved revision {editing.revision}. Saving activates future runs; it never replays past work.</p>}
   {blocked&&<p role="alert">{blocked}</p>}
   <fieldset className="a-schedule-editor-fields" disabled={!!blocked}><legend>Schedule details</legend>
   <label htmlFor="schedule-prompt">Saved instructions</label>
   <textarea ref={promptRef} id="schedule-prompt" value={draft.prompt} onChange={e=>edit({prompt:e.target.value})} placeholder="What should this chat do when the schedule runs?"/>
   <fieldset className="a-schedule-editor-time"><legend>When to run</legend>
    <div className="a-form-grid">
     <div><label htmlFor="schedule-recurrence">Repeat</label><select id="schedule-recurrence" value={draft.recurrence} onChange={e=>edit({recurrence:e.target.value})}><option value="once">Once</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="interval">At an interval</option></select></div>
     <div><label htmlFor="schedule-date">Start date</label><input id="schedule-date" type="date" value={draft.startDate} onChange={e=>edit({startDate:e.target.value})}/></div>
     <div><label htmlFor="schedule-time">Local time</label><input id="schedule-time" type="time" value={draft.localTime} onChange={e=>edit({localTime:e.target.value})}/></div>
     <div><label htmlFor="schedule-zone">Timezone</label><input id="schedule-zone" value={draft.timezone} onChange={e=>edit({timezone:e.target.value})}/></div>
    </div>
    {draft.recurrence==='interval'&&<><label htmlFor="schedule-interval">Repeat every (minutes)</label><input id="schedule-interval" type="number" min="1" value={draft.intervalMinutes} onChange={e=>edit({intervalMinutes:e.target.value})}/></>}
    {draft.recurrence==='weekly'&&<fieldset><legend>Days of the week</legend>{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map((label,index)=><label className="a-inline-checkbox" key={label}><input type="checkbox" checked={draft.weekdays.includes(index)} onChange={e=>edit({weekdays:e.target.checked?[...draft.weekdays,index]:draft.weekdays.filter(day=>day!==index)})}/>{label}</label>)}</fieldset>}
   </fieldset>
   <fieldset className="a-schedule-editor-destination"><legend>Where to run</legend>
    <label htmlFor="schedule-destination">Where to run</label><select id="schedule-destination" value={draft.destination} onChange={e=>edit({destination:e.target.value})}><option value="same_task">Continue this task</option><option value="new_task">Create a new task for each run</option></select>
    {draft.destination==='new_task'?<>
     <label htmlFor="schedule-title">New task title</label><input id="schedule-title" value={draft.newTaskTitle} onChange={e=>edit({newTaskTitle:e.target.value})}/>
     <label htmlFor="schedule-turns">Maximum turns in each new task</label><input id="schedule-turns" type="number" min="1" max="100" value={draft.newTaskMaxTurns} onChange={e=>edit({newTaskMaxTurns:e.target.value})}/>
     <p className="a-caption">Uses this chat’s reviewed workspace, bundle and model configuration. Starts fresh without copying its conversation or objective.</p>
    </>:<p className="a-caption">Continues this chat’s saved task. Save an explicit task before reviewing its schedule.</p>}
   </fieldset>
   <details className="a-schedule-editor-policies"><summary>Advanced policies</summary>
    <label htmlFor="schedule-kind">Purpose</label><select id="schedule-kind" value={draft.kind} onChange={e=>edit({kind:e.target.value})}><option value="monitor">Monitor changes</option><option value="task">Work on the saved instructions</option></select>
    <label htmlFor="schedule-missed">If the app misses a run</label><select id="schedule-missed" value={draft.missedRunPolicy} onChange={e=>edit({missedRunPolicy:e.target.value})}><option value="latest">Run only the latest missed occurrence</option><option value="skip">Skip occurrences more than a minute late</option></select>
    <label htmlFor="schedule-notify">Notify me</label><select id="schedule-notify" value={draft.notificationPolicy} onChange={e=>edit({notificationPolicy:e.target.value})}><option value="changes">For changes and failures</option><option value="always">After every result and failure</option><option value="failures_only">For failures only</option></select>
   </details>
   <p className="a-caption">{draft.kind==='monitor'?'Monitor changes':'Work on the saved instructions'} · {missedLabel(draft.missedRunPolicy)} · Notifications: {notifyLabel(draft.notificationPolicy)}</p>
   </fieldset>
   <div className="a-dialog-actions">
    <button type="button" className="a-soft" disabled={busy||!!blocked||!draft.prompt.trim()} data-action="schedule.preview" onClick={preview}>Preview schedule</button>
    <button type="button" className="a-soft" onClick={cancel}>Cancel</button>
   </div>
   {visibleReview&&<div className="a-selection-card a-schedule-editor-review" data-part="schedule-preview">
    <h4 ref={reviewHeading} tabIndex={-1}>Confirm scheduled work</h4>
    <p>{visibleReview.binding.prompt}</p>
    <p>Owning chat: {session.title||'Untitled chat'}</p>
    <p>Workspace: {session.workspace}</p>
    {visibleReview.binding.destination==='new_task'?<div data-part="schedule-destination-preview">
     <p>Create a new task for each run: {visibleReview.binding.newTaskTitle}</p>
     <p>Workspace: {visibleReview.binding.newTaskConfiguration.workspace}</p>
     <p>Bundle: {visibleReview.binding.newTaskConfiguration.bundle}</p>
     <p>Model configuration: {JSON.stringify(visibleReview.binding.newTaskConfiguration.selection||'Bundle defaults')}</p>
     <p>Maximum turns: {visibleReview.binding.newTaskMaxTurns}</p>
    </div>:<p>Continue this saved task.</p>}
    <dl>
     <dt>Purpose</dt><dd>{visibleReview.binding.kind==='monitor'?'Monitor changes':'Work on the saved instructions'}</dd>
     <dt>Repeats</dt><dd>{repeats(visibleReview.binding.spec)} ({visibleReview.binding.spec.timezone})</dd>
     <dt>Start date</dt><dd>{startDate(visibleReview.binding.spec)} ({visibleReview.binding.spec.timezone})</dd>
     <dt>Missed runs</dt><dd>{missedLabel(visibleReview.binding.missedRunPolicy)}</dd>
     <dt>Notifications</dt><dd>{notifyLabel(visibleReview.binding.notificationPolicy)}</dd>
    </dl>
    <p>Upcoming occurrences ({visibleReview.binding.spec.timezone}):</p>
    <ul>{visibleReview.occurrences.map(row=><li key={row.utc}>{new Date(row.dueAt*1000).toLocaleString(undefined,{timeZone:visibleReview.binding.spec.timezone})} ({visibleReview.binding.spec.timezone})</li>)}</ul>
    <p className="a-caption">{visibleReview.daylightSaving}</p>
    <p>Approval applies to this saved prompt and schedule. Normal tool permissions still apply during each run.</p>
    <button type="button" className="a-primary" data-action={editing?'schedule.update':'schedule.create'} disabled={busy||!visibleReview.occurrences.length} onClick={save}>Save and activate schedule</button>
   </div>}
  </div>}
  <div className="a-schedule-editor-list">
   {records.length===0&&<p>No saved schedules in this chat.</p>}
   {records.map(record=><article className="a-selection-card" key={record.id} data-schedule-id={record.id}>
    <p><strong>{record.prompt}</strong></p>
    <p>{record.destination==='new_task'?'Creates a new task for each run':'Continues this saved task'}</p>
    <p role="status">Schedule {record.status.replaceAll('_',' ')}</p>
    {record.nextDue&&record.status==='active'&&<p>Next: {new Date(record.nextDue*1000).toLocaleString(undefined,{timeZone:record.spec.timezone})} ({record.spec.timezone})</p>}
    {record.status==='needs_review'&&record.reviewReason&&<p role="alert">{record.reviewReason}</p>}
    {unknown(record)&&<p role="alert">A prior run has an unknown outcome. Inspect its evidence below before editing or activating future runs. Nothing will be replayed.</p>}
    <p className="a-caption">Notifications: {notifyLabel(record.notificationPolicy)} · Missed runs: {missedLabel(record.missedRunPolicy)}</p>
    {record.status!=='cancelled'&&<>
     <div className="a-dialog-actions">
      <button type="button" className="a-soft" disabled={busy||unknown(record)||record.sessionId!==owner} onClick={()=>load(record)}>Review schedule</button>
      {record.status==='active'&&<button type="button" className="a-soft" data-action="schedule.pause" disabled={busy||consumed.current.has(recordKey(record))||record.sessionId!==owner} onClick={()=>mutateRecord('schedule.pause',record)}>Pause schedule</button>}
      <button type="button" className="a-soft" data-action="schedule.cancel" disabled={busy||consumed.current.has(recordKey(record))||record.sessionId!==owner} onClick={()=>mutateRecord('schedule.cancel',record)}>Cancel schedule</button>
     </div>
     <p className="a-caption">Pause and Cancel affect future runs only. Already admitted work can finish; neither action undoes it.</p>
    </>}
    {record.runs?.length>0&&<details><summary>Recent runs ({record.runs.length})</summary>{record.runs.map(row=><div key={row.id} data-run-id={row.id}>
     <p><strong>{row.phase}</strong> · {new Date(row.dueAt*1000).toLocaleString()}</p><p>{row.detail}</p>
     {row.destinationSessionId&&<><p className="a-caption">Reserved task: {row.destinationSessionId}</p>{row.creationConfirmed&&<button type="button" className="a-soft" data-action="session.select" disabled={busy} onClick={()=>request('session.select',{id:row.destinationSessionId},undefined,false)}>Open new task</button>}</>}
     {row.notificationDecision&&<p className="a-caption">{row.notificationDecision.notify?'Notification requested':'Stayed quiet'} · evidence: {row.notificationDecision.source}</p>}
     {row.report&&<p className="a-caption">{row.report.source==='agent_report'?'Agent-reported evidence (not independent proof)':'Reported evidence'}: {row.report.detail}{row.report.values!==undefined&&<code>{JSON.stringify(row.report.values)}</code>}</p>}
     {row.reconciliation&&<p className="a-caption">Reconciliation evidence: {row.reconciliation.evidence}</p>}
     {row.phase==='unknown'&&<div>
      <p>Reconcile this run using observed evidence. This does not replay its input or resume the schedule.</p>
      <label>Reconciliation evidence<textarea aria-label="Reconciliation evidence" value={evidence[row.id]||''} onChange={e=>setEvidence(value=>({...value,[row.id]:e.target.value}))}/></label>
      <div className="a-dialog-actions">{['completed','abandoned'].map(resolution=><button type="button" key={resolution} className="a-soft" data-action="schedule.reconcile" disabled={busy||!evidence[row.id]?.trim()||consumed.current.has(`run:${row.id}:${row.revision}`)||record.sessionId!==owner} onClick={()=>reconcile(record,row,resolution)}>Mark run {resolution}</button>)}</div>
     </div>}
    </div>)}</details>}
   </article>)}
  </div>
 </section>;
}
