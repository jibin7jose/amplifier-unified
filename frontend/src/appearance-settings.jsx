import React,{useContext,useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {Check,Download,Upload,Palette,Sun,Moon,Monitor,FolderOpen,RefreshCw} from 'lucide-react';
import {SettingsLayoutContext} from './settings-layout';
import {FileDrop} from './settings-ui';
import {WorkingLabel} from './working-label';
import {ThemeDecorationControl,useThemeScheme} from './theme-presentation';
import {clearAppearancePreview,previewAppearance} from './appearance-preview';
import {appearanceImportFileArgs,appearanceMutationTarget} from './appearance-library';
import {appearanceCatalogFor} from './appearance-catalog';
import {AppearanceThumbnail} from './appearance-thumbnail';
import './appearance-settings.css';
import {ObservabilityPreferences} from './execution-observability.jsx';

const operationLabels={choose:'Loading appearance…',save:'Saving appearance…',update:'Updating saved appearance…',delete:'Deleting saved appearance…',preview:'Preparing preview…',apply:'Applying appearance…',revert:'Reverting appearance…',import:'Importing appearance…',export:'Exporting appearance…',reset:'Restoring original appearance…',change:'Saving appearance preferences…'};

export function AppearanceSettings({state,shell,act,editor:editorState,preview,previewSnapshot,setPreview,defaultSkin,simple=false,advancedOnly=false,onAdvanced}){
 const {active}=useContext(SettingsLayoutContext);
 const catalog=appearanceCatalogFor(state.client?.hostInstanceId);
 const cached=useSyncExternalStore(catalog.subscribe,catalog.getSnapshot,catalog.getSnapshot);
 const library=cached.library||{items:[],errors:[],directory:''},libraryError=cached.error;
 const [selected,setSelected]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [operation,setOperation]=useState(null),submitting=useRef(false),feedbackOwner=useRef(null);
 // The app's tab-private owner survives Settings unmounts. The mutable editor
 // and the exact host-validated preview snapshot deliberately remain separate.
 const {draft,setDraft,compareAndSetDraft,storageError}=editorState;
 const {entry:editing,name,css,description}=draft;
 const confirmedPreview=preview&&previewSnapshot?previewSnapshot:null;
 const [deleteTarget,setDeleteTarget]=useState(null),editor=useRef(null);
 const api=useRef(act),alive=useRef(true),generation=useRef(0),previewing=useRef(preview);api.current=act;previewing.current=preview;
 const presentation=shell.composition.presentation,scheme=presentation.scheme||state.view?.scheme||'system';
 const thumbnailMode=useThemeScheme(scheme);
 const refresh=(force=true)=>catalog.refresh(api.current,{force});
 useEffect(()=>{if(!active){alive.current=false;return}alive.current=true;submitting.current=false;setBusy(false);setOperation(null);refresh(false).catch(()=>{});const timer=setInterval(()=>refresh().catch(()=>{}),30000);return()=>{alive.current=false;generation.current++;clearInterval(timer);previewing.current=false;clearAppearancePreview(setPreview,api.current).catch(()=>{})}},[active,catalog]);
 async function run(fn,key='change'){
  if(submitting.current)return;
  const current=generation.current,token={},isCurrent=()=>alive.current&&current===generation.current;
  submitting.current=token;feedbackOwner.current=token;setBusy(true);setOperation(key);setError('');setNotice('');
  try{return await fn(isCurrent)}catch(e){
   // Keep a failure for the same draft while its section is hidden. A newer
   // draft or submission owns its own feedback, even if this receipt is late.
   // Returning to the section releases its abandoned lock, not its right to
   // report failure. Only a newer submission or draft supersedes that feedback.
   if(feedbackOwner.current===token&&(isCurrent()||compareAndSetDraft(draft,current=>current)))setError(e.message);
  }
  finally{if(submitting.current===token){submitting.current=false;if(isCurrent()){setBusy(false);setOperation(null)}}}
 }
 async function choose(item){
  if(submitting.current)return;
  const current=++generation.current;
  await run(async()=>{const {result}=await api.current('theme.read',{id:item.id});if(!alive.current||current!==generation.current)return;setSelected(item.id);setDraft({entry:result,name:result.name,css:result.css,description:result.description||'',dirty:false});setDeleteTarget(null);if(simple){const receipt=await api.current('theme.apply',{name:result.name,css:result.css});if(receipt?.accepted===false)throw Error(receipt.error||'Could not save appearance.');setPreview(false);setNotice(result.name+' saved.')}else await previewAppearance(setPreview,api.current,{name:result.name,css:result.css},()=>alive.current&&current===generation.current)},'choose');
 }
 async function startPreview(){const current=generation.current;await run(()=>previewAppearance(setPreview,api.current,{name,css},()=>alive.current&&current===generation.current),'preview')}
 async function apply(){await run(async()=>{await api.current('theme.apply',{name,css});setPreview(false);setNotice(name+' applied.')},'apply')}
 async function applyPreview(){if(!confirmedPreview)return;const snapshot={...confirmedPreview};await run(async()=>{await api.current('theme.apply',snapshot);setPreview(false);setNotice(snapshot.name+' applied.')},'apply')}
 async function cancelPreview(){await run(()=>clearAppearancePreview(setPreview,api.current),'revert')}
 async function revert(){await run(async()=>{await clearAppearancePreview(setPreview,api.current);setDraft({entry:null,name:state.theme?.name||'Amplifier Unified',css:state.theme?.css||defaultSkin,description:'',dirty:false});setSelected(null);setDeleteTarget(null)},'revert')}
 async function changeDraft(patch){
  const next={...draft,...patch,dirty:true};setDraft(next);setDeleteTarget(null);
  previewing.current=false;setPreview(false);
  // Preserve the existing client-scoped draft projection for agent/UI readback.
  // It is not the editor's identity owner and cannot replace its target.
  await api.current('view.update',{patch:{themeDraft:next.css,themeDraftName:next.name,themePreview:false}});
 }
 async function save(update=false){await run(async isCurrent=>{
  const args={name,css,description,...(update?appearanceMutationTarget(editing):{})};
  const receipt=await api.current(update?'theme.update':'theme.save',args);
  if(receipt?.accepted!==true||!receipt.result?.id)throw Error(receipt?.error||'Saving this appearance was not confirmed. Check the library before trying again.');
  catalog.invalidate();
  const adopted=compareAndSetDraft(draft,{...draft,entry:receipt.result,dirty:false});
  // A confirmed library mutation still stands if the editor has moved on.
  // Only the unchanged draft may adopt its target; old UI instances stay silent.
  if(!isCurrent())return;
  if(adopted){setSelected(receipt.result.id);setDeleteTarget(null)}
  await refresh();
  if(isCurrent())setNotice(update?'Library entry updated. Apply separately to change the current appearance.':'Saved to your appearance library. Apply separately to use it.');
 },update?'update':'save')}
 async function remove(){await run(async isCurrent=>{
  const target=appearanceMutationTarget(deleteTarget),receipt=await api.current('theme.delete',target);
  if(receipt?.accepted!==true)throw Error(receipt?.error||'Deleting this appearance was not confirmed. Check the library before trying again.');
  catalog.invalidate();
  const adopted=draft.entry?.id===target.id&&draft.entry?.fingerprint===target.expectedFingerprint&&compareAndSetDraft(draft,{...draft,entry:null,dirty:true});
  if(!isCurrent())return;
  if(adopted){setSelected(null);setDeleteTarget(null)}
  await refresh();
  if(isCurrent())setNotice('Deleted from your library. The applied appearance is unchanged.');
 },'delete')}
 function edit(){if(editor.current)editor.current.open=true;editor.current?.querySelector('input')?.focus()}
 async function upload(file){
  if(!file)return;return run(async isCurrent=>{
   const receipt=await api.current('theme.import',await appearanceImportFileArgs(file));
   if(receipt?.accepted!==true||!receipt.result?.id||typeof receipt.result.name!=='string')throw Error(receipt?.error||'Importing this appearance was not confirmed. Check the library before trying again.');
   catalog.invalidate();
   if(!isCurrent())return receipt.result;
   await refresh();
   if(isCurrent())setNotice(receipt.result.name+' added to your library. Select it to preview.');
   return receipt.result;
  },'import');
 }
 const chosenId=selected||editing?.id||(state.theme?.name==='Amplifier Unified'?'builtin:default':library.items.find(item=>item.name===state.theme?.name)?.id);
 const setPresentation=patch=>run(()=>shell.setPresentation(patch));
 return <div className="a-appearance-settings" data-appearance-simple={simple} data-appearance-advanced={advancedOnly}>
  <div className="a-appearance-intro"><div>{simple?<p>Choose a look. Your choice is saved automatically.</p>:<><p>Make this space yours.</p><small>Choose an appearance to try it. Apply when it feels right.</small></>}</div>{!simple&&<span className="a-appearance-current"><Palette size={14}/>{confirmedPreview?'Previewing '+confirmedPreview.name:state.theme?.name||'Amplifier'}</span>}</div>
  {simple&&<h4 className="a-appearance-label">Look & feel</h4>}
  {advancedOnly&&<h3>Included & saved appearances</h3>}
  <div className="a-appearance-gallery" aria-label="Appearance library">
   {library.items.map(item=><button type="button" key={item.id} className="a-appearance-card" aria-label={(simple?'Use ':'Preview ')+item.name} aria-pressed={chosenId===item.id} disabled={busy} data-action={simple?"theme.apply":"theme.preview"} onClick={()=>choose(item)}>
    <AppearanceThumbnail item={item} mode={thumbnailMode} catalog={catalog} act={act}/><div className="a-appearance-card-caption"><div><strong>{item.name}</strong>{!simple&&<small>{item.tag}</small>}</div>{chosenId===item.id?<Check/>:<Palette/>}</div>{!simple&&<p>{item.description}</p>}
   </button>)}
   {!library.items.length&&!libraryError&&<p role="status">Loading appearances…</p>}
  </div>
  {!simple&&editing&&<div className="a-dialog-actions" aria-label="Selected appearance actions">
   <button type="button" className="a-soft" disabled={busy} onClick={edit}>Edit {editing.name}</button>
   <button type="button" className="a-soft" disabled={busy} data-action="theme.export" onClick={()=>run(()=>api.current('theme.export',{id:editing.id}),'export')}><Download/>Export {editing.name}</button>
   <button type="button" className="a-soft" disabled={busy} data-action="theme.export" onClick={()=>run(()=>api.current('theme.export',{id:editing.id,format:'zip'}),'export')}><Download/>Export {editing.name} as ZIP</button>
   {editing.source==='saved'&&<button type="button" className="a-soft" disabled={busy} onClick={()=>setDeleteTarget(editing)}>Delete {editing.name}</button>}
  </div>}
  {!simple&&deleteTarget&&<div role="group" aria-label="Delete appearance confirmation"><p>Delete “{deleteTarget.name}” from the shared library? This cannot be undone. The applied appearance stays unchanged. Export it first to keep a copy.</p><div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} onClick={()=>setDeleteTarget(null)}>Cancel deletion</button><button type="button" className="a-primary" disabled={busy} data-action="theme.delete" onClick={remove}><WorkingLabel active={operation==='delete'} working="Deleting…">Confirm delete</WorkingLabel></button></div></div>}
  {confirmedPreview&&<div className="a-appearance-preview" role="status"><span>{'Previewing '+confirmedPreview.name}</span><div><button type="button" className="a-soft" disabled={busy} onClick={cancelPreview}>Cancel preview</button><button type="button" className="a-primary" disabled={busy} data-action="theme.apply" onClick={applyPreview}><Check/>{simple?'Apply appearance':'Use '+confirmedPreview.name}</button></div></div>}
  {busy&&<p role="status" className="a-appearance-operation">{operationLabels[operation]}</p>}
  {(error||libraryError||storageError)&&<p role="alert" className="a-danger">{error||libraryError||storageError}</p>}{notice&&<p role="status" className="a-appearance-notice"><Check size={15}/>{notice}</p>}
  <section className="a-appearance-options">{simple?<div className="a-appearance-color"><label htmlFor="appearance-color-mode">Color mode</label><select id="appearance-color-mode" value={scheme} disabled={busy} data-action="shell.changes.apply" onChange={event=>setPresentation({scheme:event.target.value})}><option value="system">Device</option><option value="light">Light</option><option value="dark">Dark</option></select></div>:<div className="a-appearance-option"><div><h3>Color mode</h3><p>Keep a favorite look throughout the day.</p></div><div className="a-appearance-modes" role="group" aria-label="Color mode">{[['light','Light',Sun],['dark','Dark',Moon],['system','Device',Monitor]].map(([value,label,Icon])=><button type="button" key={value} disabled={busy} aria-pressed={scheme===value} data-action="shell.changes.apply" onClick={()=>setPresentation({scheme:value})}><Icon/>{label}</button>)}</div></div>}
   <ObservabilityPreferences presentation={presentation} setPresentation={setPresentation} busy={busy}/>
   <fieldset className="a-detail-level"><legend>How much would you like to see?</legend><p>Choose a calmer workspace or keep more information at hand. You can always open details.</p><div className="a-detail-choices">{[['minimal','Focused','Quiet lists and simple work summaries. Extra information stays in details.'],['standard','Balanced','Useful context and work totals, with deeper details one click away.'],['detailed','Everything','Work activity opens automatically. Counts and list actions stay visible.']].map(([value,label,description])=><label key={value} data-selected={(presentation.interfaceDetail||presentation.executionDetail||'standard')===value}><input type="radio" name="interface-detail" value={value} checked={(presentation.interfaceDetail||presentation.executionDetail||'standard')===value} disabled={busy} data-action="shell.changes.apply" onChange={()=>setPresentation({interfaceDetail:value,executionDetail:value})}/><span><strong>{label}</strong><small>{description}</small></span></label>)}</div></fieldset>
   <div className="a-appearance-option"><div><label htmlFor="work-start-view">Starting view</label><p>Focused starts with Focus, Balanced with Brief, and Everything with Studio. Switch views anytime. Changing this keeps your open work in place.</p></div><select id="work-start-view" value={presentation.workStartView||'appearance'} disabled={busy} data-action="shell.changes.apply" onChange={event=>setPresentation({workStartView:event.target.value})}><option value="appearance">Follow appearance</option><option value="focus">Focus</option><option value="brief">Brief</option><option value="studio">Studio</option></select></div>
   <div className="a-appearance-option"><div><label htmlFor="plan-placement">Plan location</label><p>Choose where the agent’s task list appears.</p></div><select id="plan-placement" value={presentation.planPlacement||'composer'} disabled={busy} data-action="shell.changes.apply" onChange={event=>setPresentation({planPlacement:event.target.value})}><option value="composer">Above composer</option><option value="inline">Inline in chat</option><option value="active">Above composer while working</option><option value="overview">Chat overview only</option></select></div>
   <div className="a-appearance-option"><div><label htmlFor="collapse-interim-replies">Collapse interim replies <small>Experimental</small></label><p id="collapse-interim-help">After a turn’s final answer is ready, move earlier updates into work details. Nothing is deleted. Collapsed at every detail level unless you open the details. Applies to this browser.</p></div><input id="collapse-interim-replies" type="checkbox" aria-describedby="collapse-interim-help" checked={presentation.collapseInterimReplies===true} disabled={busy} data-action="shell.changes.apply" onChange={event=>setPresentation({collapseInterimReplies:event.target.checked})}/></div>
   <details className="a-everyday-disclosure" open={!simple||undefined}><summary>Layout & background</summary><div>
   <div className="a-appearance-option"><div><h3>Background</h3><p>Decorative artwork stays behind your content.</p></div><ThemeDecorationControl shell={shell} onError={setError}/></div>
   <div className="a-appearance-option"><div><label htmlFor="layout">Layout</label><p>Choose where the conversation and Canvas sit.</p></div><select id="layout" value={presentation.layout||state.view?.layout||'balanced'} disabled={busy} data-action="shell.changes.apply" onChange={e=>setPresentation({layout:e.target.value})}><option value="balanced">Canvas on the right</option><option value="conversation">More room for conversation</option><option value="work">Canvas on the left</option></select></div>

   </div></details>
  </section>
  {!simple&&<section className="a-appearance-import"><div><h3><Upload/>Your appearance library</h3><p>Upload a CSS appearance or ZIP package once. It will stay here across restarts and be available to every connected device. Imports do not apply or replace other appearances.</p></div><FileDrop id="theme-import" disabled={busy} accept=".css,.zip,text/css,application/zip" action="theme.import" label="Upload appearance" workingLabel="Uploading appearance…" hint="Drop a CSS or ZIP file here · CSS up to 1 MB · ZIP up to 4 MB" onFile={upload}/>{library.errors.map(row=><p className="a-danger" key={row.name}>{row.name}: {row.message}</p>)}<details className="a-appearance-folder"><summary><FolderOpen/>Theme folder</summary><p>CSS files and ZIP packages placed here appear in the library automatically.</p><code>{library.directory}</code></details></section>}
  {!simple&&<details ref={editor} className="a-appearance-advanced">
   <summary>Advanced customization</summary>
   <p>Edit CSS, preview locally, then save a copy or update the selected library entry. Saving never applies your edits. Exports use the selected library entry or committed current appearance, not this draft.</p>
   {editing&&<p>Editing: <strong>{editing.name}</strong>. {editing.source==='saved'?'Update keeps this library entry’s identity.':'Included appearances are read-only. Save your changes as a copy.'}</p>}
   <label htmlFor="theme-name">Appearance name</label><input id="theme-name" maxLength={100} disabled={busy} value={name} data-action="view.update" onChange={e=>changeDraft({name:e.target.value}).catch(e=>setError(e.message))}/>
   <label htmlFor="theme-description">Description</label><input id="theme-description" maxLength={240} disabled={busy} value={description} onChange={e=>changeDraft({description:e.target.value}).catch(e=>setError(e.message))}/>
   <label htmlFor="theme-css">CSS</label><textarea id="theme-css" disabled={busy} spellCheck={false} className="a-css-editor" value={css} data-action="view.update" onChange={e=>changeDraft({css:e.target.value}).catch(e=>setError(e.message))}/>
   <div className="a-dialog-actions">
    <button type="button" className="a-primary" disabled={busy} data-action="theme.save" aria-busy={operation==='save'||undefined} data-operation-pending={operation==='save'||undefined} onClick={()=>save(false)}><WorkingLabel active={operation==='save'} working="Saving…">Save as copy</WorkingLabel></button>
    {editing?.source==='saved'&&<button type="button" className="a-soft" disabled={busy} data-action="theme.update" aria-busy={operation==='update'||undefined} data-operation-pending={operation==='update'||undefined} onClick={()=>save(true)}><WorkingLabel active={operation==='update'} working="Updating…">Update saved appearance</WorkingLabel></button>}
    <button type="button" className="a-soft" disabled={busy} data-action="theme.preview" onClick={startPreview} aria-busy={operation==='preview'||undefined} data-operation-pending={operation==='preview'||undefined}><WorkingLabel active={operation==='preview'} working="Preparing…">Preview</WorkingLabel></button>
    <button type="button" className="a-soft" disabled={busy} data-action="theme.apply" aria-busy={operation==='apply'||undefined} data-operation-pending={operation==='apply'||undefined} onClick={apply}><WorkingLabel active={operation==='apply'} working="Applying…">Apply appearance</WorkingLabel></button>
    <button type="button" className="a-soft" disabled={busy} aria-busy={operation==='revert'||undefined} data-operation-pending={operation==='revert'||undefined} onClick={revert}><WorkingLabel active={operation==='revert'} working="Reverting…">Revert edits</WorkingLabel></button>
    <button type="button" className="a-soft" disabled={busy} data-action="theme.export" aria-busy={operation==='export'||undefined} data-operation-pending={operation==='export'||undefined} onClick={()=>run(()=>api.current('theme.export',{}),'export')}><Download/>Export current appearance</button>
    <button type="button" className="a-soft" disabled={busy} data-action="theme.export" aria-busy={operation==='export'||undefined} data-operation-pending={operation==='export'||undefined} onClick={()=>run(()=>api.current('theme.export',{format:'zip'}),'export')}><Download/>Export current appearance as ZIP</button>
   </div>
  </details>}
  {simple&&<div className="a-everyday-footer"><button className="a-link" data-action="view.update" onClick={onAdvanced}>Save, import & export themes</button></div>}
  {!simple&&<button type="button" className="a-link a-appearance-reset" disabled={busy} data-action="theme.reset" onClick={()=>run(async()=>{await api.current('theme.reset',{});setPreview(false);setSelected(null);setDraft({entry:null,name:'Amplifier Unified',css:defaultSkin,description:'',dirty:false});setDeleteTarget(null)},'reset')} aria-busy={operation==='reset'||undefined} data-operation-pending={operation==='reset'||undefined}><RefreshCw/>Restore original appearance</button>}
 </div>;
}
