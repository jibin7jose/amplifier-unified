import React,{useContext,useEffect,useId,useRef,useState} from 'react';
import {Plus,Copy,Trash2,Search} from 'lucide-react';
import {Collection,CollectionRow} from './settings-collections';
import {SettingsActions,SettingsLayoutContext} from './settings-layout';
import {useSettingsDraft} from './settings-drafts';
export {WorkspaceResources} from './workspace-resource-inventory';

const receipt=value=>value?.result?.accepted!==undefined?value.result:value;
async function run(act,action,args={}){const value=receipt(await act(action,args));if(!value||value.accepted===false)throw Error(value?.error||'Could not save. Your edits are kept.');return value.result}
export function useStarters(state,act){
 const [catalog,setCatalog]=useState(state.workspaceStarters||null),[error,setError]=useState('');
 const bundleRevision=JSON.stringify(state.registeredBundles?.map(row=>[row.value,row.label])||[]);
 useEffect(()=>{let active=true;run(act,'workspace.starters.list').then(value=>{if(active){setCatalog(value);setError('')}}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[bundleRevision]);
 useEffect(()=>{if(state.workspaceStarters)setCatalog(state.workspaceStarters)},[state.workspaceStarters]);
 return {catalog,error,reload:async()=>setCatalog(await run(act,'workspace.starters.list'))};
}
const empty=()=>({name:'',description:'',instructions:'',bundle:'',repositories:[],trackResources:false,scratch:false,rootGit:false});
export function StarterSettings({state,act,dispatch=act,navigate}){
 const layout=useContext(SettingsLayoutContext);
 const {catalog,error:loadError,reload}=useStarters(state,dispatch),[draft,setDraft]=useState(null),[base,setBase]=useState(null),[query,setQuery]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[confirm,setConfirm]=useState(null),id=useId();
 const submitting=useRef(false),navigation=state.view?.workspaceStarterEditor||{};
 const dirty=!!draft&&JSON.stringify(draft)!==JSON.stringify(base);
 const route=patch=>act('view.update',{patch:{workspaceStarterEditor:{...navigation,...patch}}});
 const load=row=>{setDraft(structuredClone(row));setBase(structuredClone(row));setError('');setNotice('')};
 useEffect(()=>{if(!draft&&catalog?.items.length)load(catalog.items.find(row=>row.id===navigation.id)||catalog.items[0])},[catalog]);
 const discard=()=>{setDraft(base?structuredClone(base):null);setError('');setNotice('Edits discarded.');};
 useSettingsDraft('workspace-starter',{dirty,label:'Workspace starter',discard,review:()=>{navigate?.('workspace-starters');route({id:draft?.id||null,detailOpen:true})}});
 const edit=row=>{if(busy)return;if(dirty&&row.id!==draft?.id){setError('Save or cancel the current starter before opening another.');route({detailOpen:true});return}if(row.id!==draft?.id||!draft)load(row);route({id:row.id||null,detailOpen:true})};
 const change=(key,value)=>{setDraft({...draft,[key]:value});setNotice('')};
 const perform=async fn=>{if(submitting.current)return;submitting.current=true;setBusy(true);setError('');try{await fn();await reload()}catch(err){setError(err.message)}finally{submitting.current=false;setBusy(false)}};
 const duplicate=row=>{if(dirty){setError('Save or cancel the current starter first.');return}perform(async()=>{const value=await run(dispatch,'workspace.starters.duplicate',{id:row.id});load(value);route({id:value.id,detailOpen:true});setNotice('Copy created. Customize it below.')})};
 const save=event=>{event.preventDefault();perform(async()=>{const {id:identity,revision,builtIn,...starter}=draft;const value=await run(dispatch,'workspace.starters.save',{starter,...(identity?{id:identity,expectedRevision:revision}:{})});setDraft(value);setBase(value);route({id:value.id,detailOpen:true});setNotice('Saved. Existing workspaces are unchanged.')})};
 const shown=(catalog?.items||[]).filter(row=>(row.name+' '+row.description).toLowerCase().includes(query.toLowerCase()));
 return <section className="a-workspace-starters" data-part="workspace-starters" aria-label="Workspace starters">
  <p className="a-starter-intro">Reusable starting setups for new workspaces. Choose a built-in to inspect it, or duplicate one to make it yours.</p>
  <div className="a-collection-toolbar a-starter-toolbar"><span>{catalog?.items.length||0} starters</span><button type="button" className="a-soft" disabled={busy||dirty} onClick={()=>{load(empty());route({id:null,detailOpen:true})}}><Plus/>New starter</button></div>
  {(loadError||error)&&<p role="alert" className="a-danger">{error||loadError}</p>}
  {!catalog&&!loadError&&<p role="status">Loading starters…</p>}
  <Collection label="starters" detailOpen={!!navigation.detailOpen} onBack={()=>route({detailOpen:false})} list={<><label className="a-list-filter a-starter-search" htmlFor={id+'search'}><Search/><span className="a-sr-only">Find a starter</span><input id={id+'search'} type="search" placeholder="Find a starter" value={query} onChange={e=>setQuery(e.target.value)}/></label>{shown.map(row=><CollectionRow key={row.id} id={row.id} label={row.name} description={row.description} badge={row.builtIn?'Built-in':'Custom'} selected={row.id===draft?.id} onSelect={()=>edit(row)}/>)}{!shown.length&&<p className="a-caption">No matching starters.</p>}</>}>
   {draft?<form id={id+'form'} className="a-starter-editor" aria-label="Starter editor" onSubmit={save}>
    <div className="a-settings-row"><div><h4>{draft.builtIn?draft.name:draft.id?'Edit starter':'New starter'}</h4><p className="a-caption">{draft.builtIn?'Built-in · read-only. Duplicate to customize.':'Custom · changes apply to future workspaces only.'}</p></div>{draft.id&&<button type="button" className="a-soft" disabled={busy||dirty} onClick={()=>duplicate(draft)}><Copy/>Duplicate</button>}</div>
    <fieldset disabled={busy||draft.builtIn}>
     <label htmlFor={id+'name'}>Starter name</label><input id={id+'name'} required maxLength={200} value={draft.name} onChange={e=>change('name',e.target.value)}/>
     <label htmlFor={id+'description'}>Description</label><textarea id={id+'description'} rows={3} maxLength={1000} placeholder="When should someone use this setup?" value={draft.description} onChange={e=>change('description',e.target.value)}/>
     <label htmlFor={id+'bundle'}>Workspace bundle default</label><select id={id+'bundle'} value={draft.bundle} onChange={e=>change('bundle',e.target.value)}><option value="">Inherit app and shared defaults</option>{draft.bundle&&!catalog?.bundles?.some(row=>row.value===draft.bundle)&&<option value={draft.bundle} disabled>Unavailable: {draft.bundle} — choose a configured bundle</option>}{catalog?.bundles?.map(row=><option key={row.value} value={row.value}>{row.label} ({row.value})</option>)}</select>
     <p className="a-caption">For future chats. Explicit bundle/model choices stay intact; no credentials are copied.</p>
    </fieldset>
    <details key={(draft.id||'new')+'repos'} className="a-starter-options"><summary>Repositories <span>{draft.repositories.length?draft.repositories.length+' child checkouts':'None'}</span></summary><p className="a-caption">Projects live inside the workspace, not at its root. Each repository has its own Git history.</p><fieldset disabled={busy||draft.builtIn}>{draft.repositories.map((row,index)=>{const update=(key,value)=>change('repositories',draft.repositories.map((item,i)=>i===index?{...item,[key]:value}:item));return <div className="a-starter-repository" key={index}><label>Git URL<input aria-label={'Git URL '+(index+1)} required value={row.url} placeholder="https://github.com/team/project.git" onChange={e=>update('url',e.target.value)}/></label><label>Child folder<input aria-label={'Repository folder '+(index+1)} value={row.directory} onChange={e=>update('directory',e.target.value)}/></label><label>Branch or tag<input aria-label={'Repository ref '+(index+1)} placeholder="Remote default" value={row.ref} onChange={e=>update('ref',e.target.value)}/></label><button type="button" className="a-link" onClick={()=>change('repositories',draft.repositories.filter((_,i)=>i!==index))}>Remove repository {index+1}</button></div>})}<button type="button" className="a-soft" disabled={draft.repositories.length>=20} onClick={()=>change('repositories',[...draft.repositories,{url:'',directory:'',ref:''}])}><Plus/>Add repository</button></fieldset></details>
    <details key={(draft.id||'new')+'instructions'} className="a-starter-options"><summary>Guidance & working memory <span>{draft.instructions?'Included':'None'}</span></summary><p className="a-caption">Root AGENTS.md explains the workspace. A small .amplifier/AGENTS.md entry point loads it. Existing files are never overwritten.</p><fieldset disabled={busy||draft.builtIn}><label htmlFor={id+'instructions'}>Agent instructions</label><textarea id={id+'instructions'} rows={10} maxLength={16000} value={draft.instructions} onChange={e=>change('instructions',e.target.value)}/><label><input type="checkbox" checked={!!draft.scratch} onChange={e=>change('scratch',e.target.checked)}/>Create SCRATCH.md working memory</label><p className="a-caption">A bounded root orientation file. Concurrent tasks keep their detailed notes separately.</p></fieldset></details>
    <details key={(draft.id||'new')+'resources'} className="a-starter-options"><summary>Resource tracking <span>{draft.trackResources?'Enabled':'Off'}</span></summary><fieldset disabled={busy||draft.builtIn}><label><input type="checkbox" checked={draft.trackResources} onChange={e=>change('trackResources',e.target.checked)}/>Track external resources</label></fieldset><p className="a-caption">Record owners and cleanup evidence. This never tears resources down automatically.</p></details>
    <details key={(draft.id||'new')+'git'} className="a-starter-options"><summary>Local workspace Git <span>{draft.rootGit?'Enabled':'Off'}</span></summary><fieldset disabled={busy||draft.builtIn}><label><input type="checkbox" checked={!!draft.rootGit} onChange={e=>change('rootGit',e.target.checked)}/>Initialize local workspace Git</label></fieldset><p className="a-caption">Checkpoint workspace notes and plans. Child repositories, local settings and sensitive files are excluded. Existing Git metadata is preserved; no commits or remote are created.</p></details>
    {notice&&<p role="status">{notice}</p>}
    <SettingsActions active={!layout.compact||!!navigation.detailOpen}>{!draft.builtIn&&<><button type="submit" form={id+'form'} className="a-primary" disabled={busy||!draft.name.trim()}>Save starter</button><button type="button" className="a-soft" disabled={busy} onClick={discard}>Cancel edits</button></>}{draft.builtIn&&<button type="button" className="a-primary" disabled={busy} onClick={()=>duplicate(draft)}><Copy/>Duplicate to customize</button>}</SettingsActions>
    {!draft.builtIn&&draft.id&&<div className="a-starter-danger"><button type="button" className="a-link a-danger" disabled={busy||dirty} onClick={()=>setConfirm(draft)}><Trash2/>Delete this starter</button><p className="a-caption">Only the definition is deleted. Workspaces and files remain.</p></div>}
   </form>:<p>Select a starter to see what it includes.</p>}
  </Collection>
  {confirm&&<div role="alertdialog" aria-label="Delete starter" className="a-workspace-setup"><p>Delete “{confirm.name}”? Existing workspaces and files stay intact.</p><button type="button" disabled={busy} onClick={()=>perform(async()=>{await run(dispatch,'workspace.starters.remove',{id:confirm.id,expectedRevision:confirm.revision});setDraft(null);setBase(null);setConfirm(null);route({id:null,detailOpen:false})})}>Delete starter</button><button type="button" disabled={busy} onClick={()=>setConfirm(null)}>Cancel deletion</button></div>}
 </section>;
}

export function WorkspaceReadiness({state,act,workspaceId}){
 const workspace=state.workspaces?.find(row=>row.id===(workspaceId||state.selectedWorkspaceId)),projected=workspace?.setup;
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[observed,setObserved]=useState(null);
 const visit=useRef(0),busyRef=useRef(false);
 useEffect(()=>{visit.current++;busyRef.current=false;setBusy(false);setError('');setObserved(null);return()=>{visit.current++}},[workspace?.id]);
 // The command receipt can precede its subscription projection. Keep the
 // newest exact setup observation, scoped to this workspace, for explicit Retry.
 const setup=observed&&projected&&observed.workspaceId===workspace?.id&&observed.setup.id===projected.id&&observed.setup.revision>projected.revision?observed.setup:projected;
 const perform=async action=>{
  if(busyRef.current||!setup)return;
  busyRef.current=true;setBusy(true);setError('');const current=visit.current,owner=workspace.id;
  try{
   const value=await run(act,action,{workspaceId:owner,...(action==='workspace.setup.retry'?{expectedRevision:setup.revision}:{})});
   if(current!==visit.current)return;
   if(action==='workspace.setup.reconcile'&&value?.inspectionStatus!=='complete')throw Error('Inspection is not confirmed. Inspect retained imports again before retrying setup.');
   if(value?.id!==setup.id||!Number.isInteger(value.revision))throw Error('The setup observation changed. Inspect this workspace again.');
   setObserved({workspaceId:owner,setup:value});
  }catch(err){if(current===visit.current)setError(err.message)}
  finally{if(current===visit.current){busyRef.current=false;setBusy(false)}}
 };
 const retry=()=>perform('workspace.setup.retry'),inspect=()=>perform('workspace.setup.reconcile');
 if(!setup)return null;
 return <section className="a-workspace-setup" aria-label="Workspace readiness"><h3>{setup.starter?.name||'Workspace'} setup</h3><p role="status">{setup.status==='ready'?'Ready to work':setup.status==='pending'||setup.status==='running'?'Preparing workspace…':'Some setup steps need attention. Successful work is retained.'}</p>{setup.error&&<p role="alert">{setup.error}</p>}<ul>{setup.rootGit&&<li>Local workspace Git: {setup.rootGit.status}{setup.rootGit.note&&' · '+setup.rootGit.note}</li>}{setup.files?.map(row=><li key={row.path}>{row.path}: {row.status}</li>)}{setup.repositories?.map(row=><li key={row.directory}><strong>{row.directory}</strong>: {row.status}{row.sha&&' · '+row.sha.slice(0,8)}{row.error&&<p>{row.error}</p>}</li>)}</ul>{['partial','failed','interrupted'].includes(setup.status)&&<><button type="button" disabled={busy} onClick={inspect}>Inspect retained imports</button><button type="button" disabled={busy} onClick={retry}>Retry unfinished setup</button></>}{error&&<p role="alert">{error}</p>}<p className="a-caption">No existing repository was updated or merged. Workspace files and chat history are retained.</p></section>;
}
