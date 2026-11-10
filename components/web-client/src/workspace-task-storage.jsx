import React,{useEffect,useId,useRef,useState} from 'react';

const kinds={'scratch-directory':'Scratch folder','browser-profile':'Browser profile folder','sqlite-database':'SQLite database'};
export function TaskStorage({workspaceId,sessionId,initial,act,active,readOnly}){
 const formId=useId(),visit=useRef(0),inFlight=useRef(false),mounted=useRef(false),key='amplifier-task-storage:'+workspaceId+':'+sessionId;
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key))||{kind:'scratch-directory',label:'',pending:null}}catch{return {kind:'scratch-directory',label:'',pending:null}}});
 const [page,setPage]=useState(initial||{items:[],nextOffset:null}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const pending=useRef(draft.pending);
 const retain=(value,required=false)=>{try{sessionStorage.setItem(key,JSON.stringify(value))}catch{if(required)return false}pending.current=value.pending;if(mounted.current)setDraft(value);return true};
 const result=outer=>{const value=outer?.result?.accepted!==undefined?outer.result:outer;if(!value||value.accepted===false)throw Error(value?.error||'Task storage could not be read.');return value.result};
 const refresh=async(offset=0)=>{
  const turn=++visit.current;setBusy(true);setError('');
  try{const value=result(await act('workspace.resources.read',{workspaceId,sessionId,offset,limit:25}));if(turn===visit.current)setPage(value)}
  catch(e){if(turn===visit.current)setError(e.message)}finally{if(turn===visit.current)setBusy(false)}
 };
 useEffect(()=>{setPage(initial||{items:[],nextOffset:null});setBusy(false);return()=>{visit.current++}},[initial,active]);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 const mutate=async(action,args={})=>{
  if(inFlight.current||!active||readOnly)return;
  const request=pending.current||{action,args:{workspaceId,sessionId,...args},id:crypto.randomUUID()};
  if(!retain({...draft,pending:request},true)){setError('This tab cannot save a recovery receipt. Allow browser storage before changing task storage. Nothing was sent.');return}
  const turn=++visit.current;inFlight.current=true;setBusy(true);setError('');
  try{
   result(await act(request.action,request.args,{id:request.id}));
   // A response from a replaced panel must not erase its successor's receipt.
   let cleared=true;
   try{const retained=JSON.parse(sessionStorage.getItem(key));if(retained?.pending?.id===request.id)cleared=retain({...retained,label:request.action.endsWith('.provision')?'':retained.label,pending:null},true)}catch{cleared=false}
   if(mounted.current&&turn===visit.current){await refresh();if(!cleared)setError('The response was received, but this tab could not clear its saved request. Check the same storage change after browser storage is available.')}
  }catch(e){if(turn===visit.current)setError(e.message||'No confirmed response. Check the same request.')}
  finally{inFlight.current=false;if(mounted.current)setBusy(false)}
 };
 return <section aria-label="Task storage">
  <h4>Task storage</h4>
  <p>Create a separate folder or local database inside this chat’s working folder. It uses the folder’s existing access and is excluded from ordinary Git snapshots. Explicit file restrictions still apply.</p>
  {draft.pending&&<p role="status">A storage change has no confirmed response. {!readOnly&&<button className="a-link" disabled={busy||!active} onClick={()=>mutate()}>Check the same storage change</button>} <button className="a-link" disabled={busy} onClick={()=>retain({...draft,pending:null})}>Dismiss retry</button> Files and receipts are retained.</p>}
  {error&&<p role="alert">{error}</p>}
  {page.items.length===0&&<p>No task storage on this page.</p>}
  <ul>{page.items.map(row=><li key={row.id} data-task-resource={row.id} style={{overflowWrap:'anywhere'}}>
   <strong>{row.label||kinds[row.kind]} · {row.status}</strong>
   <p>{row.path}</p>
   <p>File-tool access: {row.access?.status||'unknown'}{row.observation&&' · Files '+row.observation}</p>
   {row.access?.status==='denied'&&<p>{row.placement==='app-storage'?'This older allocation is outside the working folder. Create new task storage for new work; existing files remain here.':'A configured file restriction blocks this location. Review the chat’s file-access settings before writing here.'}</p>}
   <details><summary>Ownership and access details</summary><pre className="a-state-view">{JSON.stringify(row,null,2)}</pre></details>
   {!readOnly&&row.status!=='retired'&&<button className="a-soft" disabled={busy||!!draft.pending||!active} onClick={()=>mutate('workspace.resources.retire',{id:row.id,expectedRevision:row.revision})}>Retire and retain files</button>}
  </li>)}</ul>
  <button className="a-soft" disabled={busy||!active} onClick={()=>refresh()}>Refresh task storage</button>
  {page.nextOffset!=null&&<button className="a-soft" disabled={busy||!active} onClick={()=>refresh(page.nextOffset)}>More task storage</button>}
  {!readOnly&&<form aria-label="Create task storage" onSubmit={event=>{event.preventDefault();mutate('workspace.resources.provision',{kind:draft.kind,label:draft.label})}}>
   <label htmlFor={formId+'-kind'}>Storage type</label><select id={formId+'-kind'} value={draft.kind} disabled={busy||!!draft.pending} onChange={event=>retain({...draft,kind:event.target.value})}>{Object.entries(kinds).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
   <label htmlFor={formId+'-label'}>Storage label (optional)</label><input id={formId+'-label'} maxLength={120} value={draft.label} disabled={busy||!!draft.pending} onChange={event=>retain({...draft,label:event.target.value})}/>
   <button className="a-soft" type="submit" disabled={busy||!!draft.pending||!active}>Create task storage</button>
  </form>}
  <p>A profile folder does not launch or confine a browser. SQLite is a local file, not a database server. Retirement retains all contents and does not stop processes or reclaim disk space.</p>
 </section>;
}
