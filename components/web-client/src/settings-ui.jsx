import {AttentionBadge,AttentionReview} from './attention';
import React,{useRef,useState,useEffect,createContext,useContext} from 'react';
import {activityRegion,beginRegionActivity} from './activity-feedback';
import {ActivityRegion,useRefreshValue} from './activity-region';
import {WorkingLabel} from './working-label';
import {useOutsideDismiss} from './use-outside-dismiss';
import {CheckCircle2,Info,XCircle,LoaderCircle,ChevronRight,Upload,FolderOpen,FolderPlus,ArrowUp,X,ArrowLeft} from 'lucide-react';
export function ResultNotice({phase,message,detail}){
 const ref=useRef(null);
 const working=['working','queued','pending','checking','staging','validating','activating'].includes(phase),failed=['error','failed'].includes(phase),Icon=working?LoaderCircle:failed?XCircle:phase==='neutral'?Info:CheckCircle2;
 useEffect(()=>working&&message?beginRegionActivity(activityRegion(ref.current)):undefined,[working,!!message]);
 if(!message)return null;
 return <div ref={ref} className={`a-check-result ${working||phase==='neutral'?'pending':failed?'error':'success'}`} role={failed?'alert':'status'} aria-live="polite"><Icon aria-hidden="true" className={working?'a-progress-spinner':undefined}/><div><strong>{message}</strong>{detail&&<small>{detail}</small>}</div></div>;
}
export const SettingsPageContext=createContext(null);
export function SettingsGroup({id,title,summary,state,act,children}){
 const scopedPage=useContext(SettingsPageContext);
 const pages={setup:['loaded-modules','providers','routing','defaults','conversation'],capabilities:['smart-tools','add-bundles','app-bundles','loaded-modules','registries','share-bundle'],maintenance:['ready-conversations','updates','diagnostics','history','permissions','notifications','automation','repair','reset']};
 const page=scopedPage||state.view?.settingsExpanded?.find(key=>!state.view?.settingsSection||pages[state.view.settingsSection]?.includes(key)),open=page===id,heading=useRef();
 useEffect(()=>{if(open&&!scopedPage){heading.current?.focus({preventScroll:true});heading.current?.closest('.a-dialog')?.scrollTo(0,0)}},[open,scopedPage]);
 if(scopedPage)return open?<section className="a-settings-page" data-settings-page={id}><div id={'settings-group-'+id} className="a-settings-page-body">{children}</div></section>:null;
 if(page&&!open)return null;
 const navigate=()=>act('view.update',{patch:{settingsExpanded:open?[]:[id]}});
 return open?<section className="a-settings-page" key={id}><button type="button" className="a-link a-settings-back" data-action="view.update" onClick={navigate}><ArrowLeft/>Back to {({setup:'Setup',capabilities:'Capabilities',maintenance:'Maintenance'})[state.view?.settingsSection||'setup']}</button><h3 ref={heading} tabIndex={-1}>{title}</h3><AttentionReview state={state} act={act} page={id}/><div id={'settings-group-'+id} className="a-settings-page-body">{children}</div></section>:<section className="a-settings-group"><button type="button" className="a-settings-group-title" aria-label={title+(summary?' '+summary:'')} data-action="view.update" onClick={navigate}><span><strong>{title}</strong>{summary&&<small>{summary}</small>}</span><AttentionBadge state={state} page={id}/><ChevronRight aria-hidden="true"/></button></section>;
}

export function FileDrop({id,accept,disabled,action,onFile,label='Choose file',workingLabel='Processing file…',hint}){
 const input=useRef(),[over,setOver]=useState(false),[filename,setFilename]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),receiving=useRef(false);
 async function receive(file){if(!file||disabled||receiving.current)return;receiving.current=true;setBusy(true);setError('');setFilename(file.name);try{await onFile(file)}catch(error){setError(error.message)}finally{receiving.current=false;setBusy(false)}}
 return <ActivityRegion busy={busy} name={id} className={`a-file-drop ${over?'drag-over':''}`} onDragOver={e=>{e.preventDefault();if(!disabled&&!busy)setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);receive(e.dataTransfer.files?.[0])}}><Upload aria-hidden="true"/><div><button type="button" className="a-soft" disabled={disabled||busy} data-operation-pending={busy||undefined} aria-busy={busy||undefined} data-action={action} onClick={()=>input.current?.click()}><WorkingLabel active={busy} working={workingLabel}>{label}</WorkingLabel></button><small>{filename||hint||'Drop a file here, or browse to choose one.'}</small></div><input ref={input} id={id} className="a-file-input" type="file" accept={accept} disabled={disabled||busy} data-action={action} aria-label={label} onChange={e=>{receive(e.target.files?.[0]);e.target.value=''}}/>{error&&<ResultNotice phase="error" message={error}/>}</ActivityRegion>;
}
export function droppedLocation(transfer){
 const text=(transfer.getData('text/uri-list')||transfer.getData('text/plain')||'').split('\n').find(line=>line.trim()&&!line.startsWith('#'))?.trim();
 if(!text)return null;
 if(text.startsWith('file://')){try{return decodeURIComponent(new URL(text).pathname)}catch{return null}}
 return text;
}
export function CreateFolder({id,parent,act,status,listing,onCreated}){
 const [open,setOpen]=useState(false),[name,setName]=useState(''),[pending,setPending]=useState(null),[error,setError]=useState('');
 const submitting=useRef(false);
 useEffect(()=>{setName('');setOpen(false);setError('')},[parent]);
 useEffect(()=>{
  if(!pending||status?.commandId!==pending)return;
  if(status.phase==='error'){setError(status.error||'Could not create the folder.');setPending(null);submitting.current=false;}
  if(status.phase==='ready'&&listing?.createdBy===pending){setPending(null);submitting.current=false;setName('');setOpen(false);onCreated(listing.path)}
 },[pending,status?.commandId,status?.phase,listing?.createdBy]);
 async function create(){
  if(!name.trim()||submitting.current)return;
  submitting.current=true;setError('');setPending('sending');
  try{const receipt=await act('locations.create',{controlId:id,path:parent,name:name.trim()});
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'Folder creation was not accepted.');
   setPending(receipt.operationId);
  }catch(e){submitting.current=false;setPending(null);setError(e.message)}
 }
 return <div className="a-create-folder">
  {!open?<button type="button" className="a-soft" data-action="view.update" disabled={!!pending} onClick={()=>setOpen(true)}><FolderPlus/>New folder</button>:<><label htmlFor={id+'-folder-name'}>New folder name</label><div className="a-inline-form"><input id={id+'-folder-name'} value={name} maxLength={255} autoFocus disabled={!!pending} onChange={e=>setName(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();create()}}}/><button type="button" className="a-soft" data-action="locations.create" disabled={!!pending||!name.trim()} onClick={create}>{pending?'Creating…':'Create folder'}</button><button type="button" className="a-soft" disabled={!!pending} onClick={()=>{setOpen(false);setName('');setError('')}}>Cancel</button></div></>}
  {error&&<ResultNotice phase="error" message={error}/>}
 </div>;
}
export function PathField({id,value,onChange,state,act,placeholder,action='view.update',directory=false,multiline=false}){
 const[over,setOver]=useState(false),[error,setError]=useState(''),[pathDraft,setPathDraft]=useState(''),[requesting,setRequesting]=useState(false);
 const requestingRef=useRef(false);
 const picker=state.view?.locationPicker,open=picker?.controlId===id,incoming=state.locationListing?.controlId===id?state.locationListing:null;
 useEffect(()=>{if(open)setPathDraft(picker.path??incoming?.path??'')},[open,picker?.path,incoming?.path]);
 const pickerRoot=useRef(null);useOutsideDismiss(open,pickerRoot,()=>act('view.update',{patch:{locationPicker:null}}));
 const status=state.actionStatus?.['locations.list'],op=status?.target?.controlId===id?status:null;
 const busy=requesting||['working','queued'].includes(op?.phase),listing=useRefreshValue(id,incoming,busy);
 async function browse(path){
  if(requestingRef.current||busy)return;
  requestingRef.current=true;setRequesting(true);setPathDraft(path||'');setError('');
  try{
   act('view.update',{patch:{locationPicker:{controlId:id,path:path||''}}});
   const receipt=await act('locations.list',{controlId:id,...(path?{path}:{}),directoriesOnly:directory});
   if(receipt?.accepted!==true)setError('Could not load locations. Try again.');
  }catch{setError('Could not load locations. Try again.')}
  finally{requestingRef.current=false;setRequesting(false)}
 }
 const choose=path=>{onChange(multiline?[value,path].filter(Boolean).join('\n'):path);act('view.update',{patch:{locationPicker:null}})};
 const Input=multiline?'textarea':'input';
 return <div ref={pickerRoot} className={`a-path-field ${over?'drag-over':''}`} onDragOver={e=>{e.preventDefault();setOver(true)}} onDragLeave={()=>setOver(false)} onDrop={e=>{e.preventDefault();setOver(false);const path=droppedLocation(e.dataTransfer);if(path){setError('');onChange(multiline?[value,path].filter(Boolean).join('\n'):path)}else setError('Your browser hides the full path of dropped files. Use Browse, or drop a copied file or folder path.')}}><div className="a-inline-form"><Input id={id} value={value} placeholder={placeholder} data-action={action} onChange={e=>onChange(e.target.value)}/><button type="button" className="a-soft" data-action="locations.list" disabled={busy} aria-busy={busy||undefined} onClick={()=>browse(!multiline&&value?.startsWith('/')?value:undefined)}><FolderOpen/>Browse</button></div>{error&&<ResultNotice phase="error" message={error}/>} {open&&<ActivityRegion busy={busy} name={id+'-locations'} className="a-location-picker"><div className="a-settings-row"><strong>{directory?'Choose a folder':'Choose a file or folder'}</strong><button type="button" className="a-icon" aria-label="Close location picker" data-action="view.update" onClick={()=>act('view.update',{patch:{locationPicker:null}})}><X/></button></div><label htmlFor={id+'-browse-path'}>Folder path</label><div className="a-inline-form"><input id={id+'-browse-path'} disabled={busy} value={pathDraft} placeholder="Type a path, e.g. ~/Projects" data-action="view.update" onChange={e=>{setPathDraft(e.target.value);act('view.update',{patch:{locationPicker:{...picker,path:e.target.value}}})}} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();browse(e.currentTarget.value)}}}/><button type="button" className="a-soft" data-action="locations.list" disabled={busy} aria-busy={busy||undefined} data-operation-pending={busy||undefined} onClick={()=>browse(pathDraft)}><WorkingLabel active={busy} working="Loading…">Go</WorkingLabel></button></div>{op?.phase==='error'&&<ResultNotice phase="error" message={op.error}/>} {busy&&<ResultNotice phase="working" message="Loading locations…"/>}{listing&&<><div className="a-settings-row"><button type="button" className="a-soft" data-action="locations.list" disabled={busy||listing.parent===listing.path} onClick={()=>browse(listing.parent)}><ArrowUp/>Up</button><code className="a-wrap">{listing.path}</code></div><CreateFolder id={id} parent={listing.path} act={act} status={state.actionStatus?.['locations.create']} listing={incoming} onCreated={path=>{setPathDraft(path);act('view.update',{patch:{locationPicker:{controlId:id,path}}})}}/><div className="a-location-list">{listing.entries.map(entry=><button type="button" className="a-location-entry" key={entry.path} disabled={busy&&entry.directory} data-action={entry.directory?'locations.list':action} onClick={()=>entry.directory?browse(entry.path):choose(entry.path)}>{entry.directory&&<FolderOpen/>}{entry.name}{entry.directory&&<ChevronRight/>}</button>)}{!listing.entries.length&&<p>No {directory?'folders':'files'} here.</p>}</div>{listing.truncated&&<p>Showing the first 300 items. Enter a more specific path above.</p>}<button type="button" className="a-primary" data-action={action} onClick={()=>choose(listing.path)}>Use this folder</button></>}</ActivityRegion>}</div>;
}
export function BundleCatalogStatus({state,act,value,disabled=false}){
 const [retryError,setRetryError]=useState(''),[retrying,setRetrying]=useState(false),guard=useRef(false);
 const status=state.actionStatus?.['bundles.list'],busy=['queued','working'].includes(status?.phase);
 useEffect(()=>{setRetryError('')},[status?.phase,status?.error]);
 async function retry(){if(guard.current)return;guard.current=true;setRetrying(true);setRetryError('');try{const receipt=await act('bundles.list',{});if(!receipt||receipt.accepted===false)throw Error(receipt?.error||'Bundle choices could not be loaded. Try again.')}catch(error){setRetryError(error.message)}finally{guard.current=false;setRetrying(false)}}
 if(busy)return <p className="a-caption" role="status">Loading bundle choices…</p>;
 if(status?.phase==='error')return <div><p className="a-danger" role="alert">{retryError||status.error||'Bundle choices could not be loaded.'}</p><button type="button" className="a-soft" disabled={disabled||retrying} data-action="bundles.list" onClick={retry}>Retry bundle catalog</button></div>;
 if(status?.phase==='ready'&&value&&!state.registeredBundles?.some(row=>row.value===value))return <p className="a-caption" role="status">The saved bundle “{value}” is not in the current catalog. It stays selected until you choose another bundle.</p>;
 if(status?.phase==='ready'&&!state.registeredBundles?.length)return <p className="a-caption" role="status">No registered bundle choices are available.</p>;
 return null;
}
export function BundlePicker({id,value,onChange,onSelect,disabled=false,state,act,action='view.update'}){
 const advanced=state.view?.bundleSources?.includes(id)||false;
 const setAdvanced=value=>act('view.update',{patch:{bundleSources:value?[...new Set([...(state.view?.bundleSources||[]),id])]:(state.view?.bundleSources||[]).filter(key=>key!==id)}});
 useEffect(()=>{act('bundles.list',{})},[]);
 const bundles=state.registeredBundles||[{name:'work',label:'Work',value:'work'}];
 const selected=bundles.find(row=>row.value===value);
 return <ActivityRegion busy={['queued','working'].includes(state.actionStatus?.['bundles.list']?.phase)} name={'bundles-'+id} className="a-bundle-picker"><select id={id} disabled={disabled} aria-label="Registered root bundles" value={selected?value:':custom:'} data-action={action} onChange={e=>{if(e.target.value===':custom:')setAdvanced(true);else (onSelect||onChange)(e.target.value)}}><option value=":custom:">{value&&!selected?value:'Choose a bundle'}</option>{bundles.map(row=><option value={row.value} key={row.value}>{row.label||row.name}</option>)}</select>{selected?.description&&<p className="a-caption">{selected.description}</p>}<BundleCatalogStatus state={state} act={act} value={value} disabled={disabled}/><button type="button" className="a-link" aria-expanded={advanced} data-action="view.update" onClick={()=>setAdvanced(!advanced)}>{advanced?'Hide source':'Use a bundle source or file'}</button>{advanced&&<PathField action={action} id={id+'-source'} value={value} onChange={onChange} state={state} act={act} placeholder="Bundle name, git URL or file path"/>}</ActivityRegion>;
}
