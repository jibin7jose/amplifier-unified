import React,{useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {ResourceReservations} from './workspace-resource-reservations';
import {ManagedResources} from './workspace-managed-resources';
const unwrap=value=>value?.result?.accepted!==undefined?value.result:value;
// Keep the existing editors at tab lifetime while the landing page remains keyed
// by destination. Moving this stable portal node preserves edits and pending
// receipts; it never serializes drafts or replays resource commands.
export function WorkspaceResourcesOwner({state,act,children}){
 const [host]=useState(()=>typeof document==='undefined'?null:document.createElement('div'));
 const [connection,setConnection]=useState(null);
 const connect=useCallback((node,workspace,unavailableId)=>{
  if(!host)return ()=>{};
  const entry={node,workspace,unavailableId};
  if(workspace)node.appendChild(host);
  setConnection(entry);
  return ()=>{
   if(host.parentNode===node){
    if(host.contains(document.activeElement))document.activeElement.blur();
    host.remove();
   }
   setConnection(current=>current===entry?null:current);
  };
 },[host]);
 const inDetails=state.view?.workSurface==='workspace'&&(state.view.workWorkspaceTab||'chats')==='details';
 const registered=connection?.workspace&&state.workspaces?.find(row=>row.id===connection.workspace.id&&row.path===connection.workspace.path&&row.available!==false);
 const workspaceId=inDetails&&state.view.workWorkspaceId===registered?.id?registered?.id:undefined;
 return <>{children(connect)}{host&&createPortal(<WorkspaceResources state={state} act={act} workspaceId={workspaceId} unavailableId={inDetails?connection?.unavailableId:undefined}/>,host)}</>;
}
export function WorkspaceResourcesSlot({connect,workspace,unavailableId}){
 const slot=useRef(null);
 useLayoutEffect(()=>connect(slot.current,workspace,unavailableId),[connect,workspace?.id,workspace?.path,unavailableId]);
 return <div ref={slot} />;
}
export function WorkspaceResources({state,act,workspaceId,unavailableId,readOnly=false}){
 const visited=useRef(new Map()),identity=row=>JSON.stringify([row.id,row.path]);
 const workspace=state.workspaces?.find(row=>row.id===workspaceId&&row.available!==false);
 // Browser state carries a bounded registration subset. Absence alone is not
 // removal; only an observed replacement or the resolved unavailable destination
 // invalidates a retained editor. Neither can inherit edits for the old path.
 for(const [key,row] of visited.current){
  const registered=state.workspaces?.find(item=>item.id===row.id);
  if(row.id===unavailableId||registered&&(registered.path!==row.path||registered.available===false))visited.current.delete(key);
  else if(registered)visited.current.set(key,registered);
 }
 if(workspace)visited.current.set(identity(workspace),workspace);
 const visibleKey=workspace&&identity(workspace);
 return <>{[...visited.current].map(([key,row])=><div key={key} hidden={key!==visibleKey}><ResourceEditor workspace={row} act={act} readOnly={readOnly}/><ManagedResources workspace={row} state={state} act={act} active={key===visibleKey} readOnly={readOnly}/><ResourceReservations workspace={row} state={state} act={act} readOnly={readOnly} active={key===visibleKey}/></div>)}</>;
}
function ResourceEditor({workspace,act,readOnly}){
 const [inventory,setInventory]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[adding,setAdding]=useState(false),[draft,setDraft]=useState({kind:'',resourceId:'',owner:'',note:''}),[updating,setUpdating]=useState(null),[evidence,setEvidence]=useState(''),[status,setStatus]=useState('observed_absent');
 const discard=()=>{setAdding(false);setUpdating(null);setDraft({kind:'',resourceId:'',owner:'',note:''});setEvidence('')};
 const dirty=Object.values(draft).some(Boolean)||!!evidence;
 useEffect(()=>{if(!dirty)return;const prevent=event=>{event.preventDefault();event.returnValue=''};window.addEventListener('beforeunload',prevent);return()=>window.removeEventListener('beforeunload',prevent)},[dirty]);
 const run=async(action,args={})=>{const value=unwrap(await act(action,{workspaceId:workspace.id,...args}));if(!value||value.accepted===false)throw Error(value?.error||'Resource inventory unavailable.');return value.result};
 const refresh=async()=>setInventory(await run('workspace.resources.list'));
 useEffect(()=>{let active=true;run('workspace.resources.list').then(value=>{if(active)setInventory(value)}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[workspace.id]);
 const mutate=async(fn)=>{setBusy(true);setError('');try{await fn();await refresh()}catch(err){setError(err.message)}finally{setBusy(false)}};
 if(!workspace)return null;
 return <section className="a-workspace-setup" aria-label="Workspace resources"><h3>External resources · {workspace.name}</h3><p className="a-caption">An inventory, not automatic cleanup. Keep ownership and observed state distinct. Records survive removal of working files.</p>{dirty&&<p role="status">Unsaved resource changes stay with this workspace while the app remains open. Save or cancel before reloading.</p>}{error&&<p role="alert">{error}</p>}<ul>{inventory?.resources.map(row=><li key={row.id}><strong>{row.kind} · {row.resourceId}</strong><p>Owner: {row.owner} · {row.status.replace('_',' ')}{row.note&&' · '+row.note}</p>{row.evidence&&<p>{row.evidence}</p>}{!readOnly&&<button type="button" disabled={busy} onClick={()=>{setUpdating(row);setEvidence('')}}>Record observation</button>}</li>)}</ul>{inventory?.resources.length===0&&<p>No external resources recorded.</p>}{readOnly?<p>Manage resource records in this workspace’s Details tab.</p>:<button type="button" disabled={busy} onClick={()=>{if(adding)discard();else setAdding(true)}}>{adding?'Cancel resource':'Record resource'}</button>}
 {adding&&<form onSubmit={event=>{event.preventDefault();mutate(async()=>{await run('workspace.resources.add',draft);setAdding(false);setDraft({kind:'',resourceId:'',owner:'',note:''})})}}>{[['kind','Resource type'],['resourceId','Resource identifier'],['owner','Resource owner'],['note','Resource note']].map(([key,label])=><label key={key}>{label}<input required={key!=='note'} value={draft[key]} maxLength={1000} onChange={e=>setDraft({...draft,[key]:e.target.value})}/></label>)}<button type="submit" disabled={busy}>Save resource record</button></form>}
 {updating&&<form onSubmit={event=>{event.preventDefault();mutate(async()=>{await run('workspace.resources.update',{id:updating.id,expectedRevision:updating.revision,status,evidence});setUpdating(null);setEvidence('')})}}><label>Observed resource status<select value={status} onChange={e=>setStatus(e.target.value)}><option value="observed_absent">Observed absent — not our teardown</option><option value="reaped">Reaped — our teardown confirmed</option><option value="active">Active</option></select></label><label>Observation evidence<textarea required value={evidence} maxLength={4000} onChange={e=>setEvidence(e.target.value)}/></label><button type="submit" disabled={busy}>Save observation</button><button type="button" onClick={()=>{setUpdating(null);setEvidence('')}}>Cancel observation</button></form>}
 </section>;
}
