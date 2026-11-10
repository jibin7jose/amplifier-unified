import React,{useEffect,useId,useRef,useState} from 'react';
import {FolderOpen,Folder,Search,Plus,Check,MessageCircle,ArrowLeft,X} from 'lucide-react';
import {PathField} from './settings-ui';
import {useStarters} from './workspace-starters';
import {useSettingsDraft} from './settings-drafts';
import './workspace-setup.css';

// Built-in shell modules wrap action receipts once; all journeys use the same
// host actions, including the draft's workspace chooser and agent callers.
const receipt=value=>value?.result?.accepted!==undefined?value.result:value;
export function WorkspaceForm({state,act,mode='create',fromDraft=false,onDone,onCancel}){
 const [kind,setKind]=useState(mode),[name,setName]=useState(''),[path,setPath]=useState(''),[root,setRoot]=useState(''),[plan,setPlan]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const {catalog,error:starterError}=useStarters(state,act),[starterId,setStarterId]=useState('blank');
 const starter=catalog?.items.find(row=>row.id===starterId);
 const submitting=useRef(false),form=useRef(null),id=useId();
 useEffect(()=>{form.current?.querySelector('input')?.focus()},[kind]);
 const edit=setter=>value=>{setter(value);setPlan(null);setError('')};
 const run=async(action,args)=>{const response=receipt(await act(action,args));if(!response||response.accepted===false)throw Error(response?.error||'Could not save. Your choices are kept.');return response.result};
 const submit=async event=>{
  event.preventDefault();if(submitting.current)return;submitting.current=true;setBusy(true);setError('');
  try{
   if(kind==='attach'){
    const result=await run('workspace.add',{path,fromDraft});onDone?.(result);return;
   }
   const prepared=plan||await run('workspace.prepare',{name,starterId,...(root.trim()?{root:root.trim()}:{})});
   setPlan(prepared);
   if(prepared.disposition!=='create')return;
   const result=await run('workspace.create',{planId:prepared.planId,fromDraft});onDone?.(result);
  }catch(err){setError(err.message)}finally{submitting.current=false;setBusy(false)}
 };
 const useExisting=async()=>{
  if(submitting.current)return;submitting.current=true;setBusy(true);setError('');
  try{const result=await run('workspace.add',{path:plan.path,fromDraft});onDone?.(result)}catch(err){setError(err.message)}finally{submitting.current=false;setBusy(false)}
 };
 return <form ref={form} className="a-workspace-setup" onSubmit={submit} aria-label={kind==='attach'?'Use existing folder':'Create workspace'} aria-busy={busy}>
  <div className="a-workspace-setup-heading"><h3>{kind==='attach'?'Use an existing folder':'New workspace'}</h3><button type="button" className="a-icon" aria-label="Cancel workspace setup" disabled={busy} onClick={onCancel}><X/></button></div>
  {kind==='create'?<>
   <label htmlFor={id+'-name'}>Workspace name</label><input id={id+'-name'} value={name} maxLength={200} required disabled={busy} placeholder="e.g. Launch plan" onChange={e=>edit(setName)(e.target.value)}/>
   <label htmlFor={id+'-starter'}>Start from…</label><select id={id+'-starter'} value={starterId} disabled={busy} onChange={e=>edit(setStarterId)(e.target.value)}>{(catalog?.items||[{id:'blank',name:'Blank'}]).map(row=><option key={row.id} value={row.id}>{row.name}</option>)}</select>
   <p className="a-caption">{starter?.description||'An empty folder. Use your existing defaults.'} {starter?.repositories.length>0&&`${starter.repositories.length} repositories will be downloaded as child checkouts.`} {starter?.rootGit?'Local workspace Git will be initialized for notes, without commits.':'No workspace-root Git repository is created.'}</p>
   <p className="a-caption">Customize starters in Settings → Workspaces → Manage starters. Existing folders are never scaffolded by attachment.</p>
   {starterError&&<p role="alert">{starterError} Blank creation remains available.</p>}
   <details><summary>More options</summary><label htmlFor={id+'-root'}>Create in</label><PathField id={id+'-root'} value={root} placeholder={state.workspaceDefaults?.root||'Default workspace folder'} directory state={state} act={act} onChange={edit(setRoot)}/><p className="a-caption">A new folder will be created here. Change the default in Settings → Workspaces.</p></details>
  </>:<><label htmlFor={id+'-folder'}>Folder on {state.workspaceDefaults?.hostLabel||'this host'}</label><PathField id={id+'-folder'} value={path} directory state={state} act={act} onChange={edit(setPath)} placeholder="~/dev/my-project"/><p className="a-caption">Work with files here. Existing Amplifier chats will appear automatically.</p></>}
  {plan&&plan.disposition!=='create'&&<p role="status">{plan.disposition==='open'?'This folder already has a workspace.':plan.disposition==='attach'?'This folder already exists. Use it as a workspace?':'A file already uses this name. Choose another name.'}<span className="a-workspace-destination">{plan.path}</span></p>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
  <div className="a-workspace-setup-actions"><button type="button" className="a-link" disabled={busy} onClick={onCancel}>Cancel</button>{plan&&['open','attach'].includes(plan.disposition)?<button type="button" className="a-primary" disabled={busy} onClick={useExisting}>{plan.disposition==='open'?'Open workspace':'Use folder'}</button>:<button type="submit" className="a-primary" disabled={busy||!(kind==='create'?name.trim():path.trim())}>{busy?'Saving…':kind==='create'?'Create workspace':'Use folder'}</button>}</div>
  <button type="button" className="a-link" disabled={busy} onClick={()=>{setKind(kind==='create'?'attach':'create');setPlan(null);setError('')}}>{kind==='create'?'Use an existing folder':'Create a new workspace'}</button>
 </form>;
}

export function WorkspacePicker({state,act,setup,onChange}){
 const [mode,setMode]=useState(null),[rows,setRows]=useState([]),[query,setQuery]=useState(''),[error,setError]=useState(''),[more,setMore]=useState(false),[page,setPage]=useState(0),[loading,setLoading]=useState(true),[total,setTotal]=useState(0),[retry,setRetry]=useState(0),[saving,setSaving]=useState(false);
 const search=useRef(null),submitting=useRef(false),list=useRef(null),id=useId(),pageSize=40;
 useEffect(()=>{if(!mode)search.current?.focus()},[mode]);
 useEffect(()=>{
  if(mode)return;
  let current=true;setLoading(true);setError('');setRows([]);
  const timer=setTimeout(()=>{
   Promise.resolve(act('workspace.list',{query,offset:page*pageSize,limit:pageSize})).then(value=>{
    const result=receipt(value);if(!result?.accepted)throw Error(result?.error||'Could not load workspaces.');
    if(current){setRows(result.result.items);setMore(result.result.nextOffset!=null);setTotal(result.result.total||0);if(list.current)list.current.scrollTop=0}
   }).catch(err=>{if(current)setError(err.message)}).finally(()=>{if(current)setLoading(false)});
  },query?150:0);
  return()=>{current=false;clearTimeout(timer)};
 },[query,page,state.workspaces?.length,mode,retry]);
 const choose=async path=>{
  if(submitting.current)return;submitting.current=true;setSaving(true);setError('');
  try{await onChange({workspace:path,location:{kind:path?'workspace':'managed'}})}catch(err){setError(err.message)}finally{submitting.current=false;setSaving(false)}
 };
 const selected=setup.location?.kind==='managed'?'':setup.workspace||'';
 const parent=path=>{const parts=path.split('/').filter(Boolean);parts.pop();return parts.length>3?'…/'+parts.slice(-3).join('/'):'/'+parts.join('/')};
 if(mode)return <div className="a-workspace-picker"><button type="button" className="a-link a-picker-back" onClick={()=>setMode(null)}><ArrowLeft/>Back to workspaces</button><h3>{mode==='create'?'New workspace':'Use an existing folder'}</h3><WorkspaceForm state={state} act={act} mode={mode} fromDraft onCancel={()=>setMode(null)} onDone={result=>choose(result.path)}/>{error&&<p role="alert">{error}</p>}</div>;
 return <div className="a-workspace-picker" aria-busy={saving}>
  <button type="button" className="a-picker-choice" aria-pressed={!selected} disabled={saving} onClick={()=>choose('')}><MessageCircle/><span><strong>No workspace</strong><small>Keep this conversation on its own.</small></span>{!selected&&<Check/>}</button>
  <label className="a-picker-search" htmlFor={id+'-search'}><Search/><input ref={search} id={id+'-search'} type="search" maxLength={500} aria-label="Find a workspace" placeholder="Search workspaces by name or folder" value={query} onChange={e=>{setPage(0);setQuery(e.target.value)}}/></label>
  <div ref={list} className="a-picker-results" aria-label="Workspaces" aria-busy={loading}>
   {loading?<p role="status">Finding workspaces…</p>:error?<p role="alert">{error} <button type="button" className="a-link" onClick={()=>setRetry(retry+1)}>Try again</button></p>:rows.length?<ul>{rows.map(row=><li key={row.id}><button type="button" className="a-picker-choice" aria-label={row.name+' · '+row.path} aria-pressed={selected===row.path} disabled={saving} title={row.path} onClick={()=>choose(row.path)}><Folder/><span><strong>{row.name}</strong><small>{parent(row.path)}</small></span>{selected===row.path&&<Check/>}</button></li>)}</ul>:<p role="status">{query?'No matching workspaces. Try a name or part of its folder path.':'No workspaces yet. Create one or use an existing folder below.'}</p>}
  </div>
  {!loading&&!error&&(page>0||more)&&<div className="a-picker-pages"><span>{page*pageSize+1}–{page*pageSize+rows.length} of {total}</span><button type="button" className="a-link" disabled={!page} onClick={()=>setPage(page-1)}>Previous</button><button type="button" className="a-link" disabled={!more} onClick={()=>setPage(page+1)}>Next</button></div>}
  <div className="a-picker-actions"><button type="button" disabled={saving} onClick={()=>setMode('create')}><Plus/>New workspace</button><button type="button" disabled={saving} onClick={()=>setMode('attach')}><FolderOpen/>Use existing folder</button></div>
 </div>;
}

export function WorkspaceSettings({state,act,dispatch=act,navigate}){
 const [root,setRoot]=useState(state.settings?.workspaces?.defaultRoot||''),[status,setStatus]=useState(''),[busy,setBusy]=useState(false);
 const save=async e=>{e.preventDefault();setBusy(true);try{const result=receipt(await dispatch('settings.update',{patch:{workspaces:{defaultRoot:root.trim()}}}));if(!result||result.accepted===false)throw Error(result?.error||'Could not save.');setStatus('Saved. Existing workspaces stay where they are.')}catch(err){setStatus(err.message)}finally{setBusy(false)}};
 useSettingsDraft('workspace-root',{dirty:root!==(state.settings?.workspaces?.defaultRoot||''),label:'Workspace folder default',discard:()=>setRoot(state.settings?.workspaces?.defaultRoot||''),review:()=>navigate?.('workspaces')});
 return <><form className="a-workspace-setup" onSubmit={save}><h3>Where new workspaces live</h3><p>Name a workspace and Amplifier creates its folder here.</p><label htmlFor="default-workspace-root">Default workspace folder on {state.workspaceDefaults?.hostLabel||'this host'}</label><PathField id="default-workspace-root" value={root} directory state={state} act={act} onChange={setRoot} placeholder={state.workspaceDefaults?.root}/><p className="a-caption">Leave blank to use Amplifier’s workspace folder. This only affects new workspaces.</p><button className="a-primary" disabled={busy}>Save</button>{status&&<p role="status">{status}</p>}<label><input type="checkbox" checked={!!state.settings?.workspaces?.showPaths} onChange={e=>act('settings.update',{patch:{workspaces:{showPaths:e.target.checked}}})}/>Show folder paths in the sidebar</label></form><section className="a-workspace-setup"><h3>Starting setups</h3><p>Choose a built-in starter when you create a workspace. For reusable team setups, manage your own copies separately.</p><button type="button" className="a-link" onClick={()=>navigate?.('workspace-starters')}>Manage starters →</button><p className="a-caption">Advanced customization · affects new workspaces only.</p></section></>;
}
