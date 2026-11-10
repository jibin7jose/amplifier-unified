import {conversationHistoryPending} from './history-window.js';
import {SettingsActions} from './settings-layout';
import {useOutsideDismiss} from './use-outside-dismiss';
import React,{useState,useEffect,useRef,useId} from 'react';
import {Layers,ChevronDown,ChevronRight,X} from 'lucide-react';
import {BundlePicker,BundleCatalogStatus,ResultNotice} from './settings-ui';
import {newChatSetup,draftDefaults} from './new-chat';
import {useComposerPopover} from './composer-popover';
import {nextMessageBlocked,useNextMessageConfiguration} from './chat-controls';

export const bundleLabel=(state,value)=>state.registeredBundles?.find(row=>row.value===value)?.label||value||'Bundle';
const EMPTY={};
export function BundleControl(props){return props.compact?<CompactBundleControl {...props}/>:<DetailedBundleControl {...props}/>}
function CompactBundleControl({state,session,act,working,embedded=false,nextMessageControl,page,onNavigate}){
 const descriptionId=useId();
 const setup=newChatSetup(state),defaults=draftDefaults(state),shared=state.view?.composerBundle||EMPTY;
 const nextMessage=useNextMessageConfiguration(session,act,nextMessageControl);
 const [draft,setDraft]=useState(shared),[pending,setPending]=useState(false),[error,setError]=useState(''),guard=useRef(false);
 useEffect(()=>setDraft(shared),[shared]);
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{composerBundle:next}})};
 const current=session?.pendingBundleSelection?.bundle||session?.bundle||setup.bundle||defaults.bundle||(state.bundleDefaults?.workspacePath===setup.workspace?state.bundleDefaults.effective:'');
 const open=embedded||(draft.open&&draft.sessionId===(session?.id||null)),popover=useRef(null),{style:position,panelRef,renderPopover}=useComposerPopover(open&&!embedded,popover);
 useOutsideDismiss(open&&!embedded,popover,()=>edit({open:false}),panelRef);
 useEffect(()=>{if(!state.registeredBundles?.length)act('bundles.list',{})},[]);
 const catalogPhase=state.actionStatus?.['bundles.list']?.phase,rows=state.registeredBundles||[],unavailable=!!state.navigationPending||session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||conversationHistoryPending(session);
 const blocked=unavailable||pending||nextMessage.pending||nextMessageBlocked(session,working);
 async function choose(bundle,resetModel=false){
  if(guard.current||blocked)return;guard.current=true;setPending(true);setError('');edit({bundle});
  try{if(session)await nextMessage.configure({bundle,resetModel});else await act('view.update',{patch:{newSessionDraft:{...setup,bundle}}})}catch(e){setError(e.message)}finally{guard.current=false;setPending(false)}
 }
 const pendingBundle=session?.pendingBundleSelection?.bundle;
 const failedBundle=pendingBundle&&session?.nextMessageFailure?.code==='incompatible-model'&&session.nextMessageFailure.bundle===pendingBundle?pendingBundle:null;
 const previewBundle=session?.bundlePreview&&session.bundlePreview.bundle===draft.bundle&&session.bundlePreview.modelCompatible===false&&session?.bundleChange?.phase!=='ready'?session.bundlePreview.bundle:null;
 const resetBundle=failedBundle||previewBundle;
 const renderPanel=embedded?content=>content:renderPopover;
 return <div className="a-model-control a-bundle-control" ref={popover}>
  {embedded&&onNavigate&&<button type="button" className="a-composer-option" data-tooltip="off" data-composer-option="bundle" aria-label="Choose agent bundle" aria-describedby={descriptionId} hidden={page!=='overview'} onClick={()=>onNavigate('bundle')}><span id={descriptionId}>Bundle · {current?bundleLabel(state,current):'Choose a bundle'}</span><ChevronRight/></button>}
  {!embedded&&<button type="button" className="a-model-trigger a-bundle-trigger" aria-label="Conversation bundle" disabled={!!unavailable} aria-expanded={!!open} data-action="view.update" onClick={()=>edit({open:!open,sessionId:session?.id||null,bundle:current})}><Layers/><span>{current?bundleLabel(state,current):rows.length?'Choose a bundle':catalogPhase==='ready'?'No bundle choices':defaults.phase==='error'||catalogPhase==='error'?'Bundle unavailable':'Loading bundle…'}</span>{session?.pendingBundleSelection&&<small>Next message</small>}<ChevronDown/></button>}
  {open&&renderPanel(<section ref={panelRef} data-composer-page="bundle" hidden={!!onNavigate&&page!=='bundle'} className={embedded?"a-embedded-bundle":"a-model-popover a-compact-popover"} style={embedded?undefined:position} aria-label="Choose conversation bundle">{embedded?<label className={onNavigate?'a-sr-only':undefined} htmlFor="composer-options-bundle">Agent bundle</label>:<div className="a-settings-row"><strong>Conversation bundle</strong><button type="button" className="a-icon" aria-label="Close bundle settings" data-action="view.update" onClick={()=>edit({open:false})}><X/></button></div>}
   {!onNavigate&&<p className="a-caption" role="status">{pending?'Saving Next message settings…':session?.pendingBundleSelection?'Next message · Saved; not applied yet.':'Next message · Changes apply when you send.'}</p>}
   <select id={embedded?"composer-options-bundle":undefined} aria-label="Registered root bundles" value={current||''} disabled={blocked} data-action={session?'conversation.configure':'view.update'} onChange={e=>choose(e.target.value)}>{!current&&<option value="" disabled>{rows.length?'Choose a bundle':catalogPhase==='ready'?'No bundle choices':catalogPhase==='error'?'Bundle choices unavailable':'Loading bundles…'}</option>}{current&&!rows.some(row=>row.value===current)&&<option value={current}>{bundleLabel(state,current)}</option>}{rows.map(row=><option key={row.value} value={row.value}>{row.label||row.value}</option>)}</select>
   {rows.find(row=>row.value===current)?.description&&<p className="a-caption a-composer-bundle-description">{rows.find(row=>row.value===current).description}</p>}
   <BundleCatalogStatus state={state} act={act} value={current} disabled={blocked}/>
   {resetBundle&&<><p>Your pinned model is unavailable in this bundle.</p><button type="button" className="a-soft" disabled={blocked} onClick={()=>choose(resetBundle,true)}>Use the new bundle’s model</button></>}
   {(error||(!onNavigate&&session?.nextMessageError)||session?.bundleChange?.phase==='error')&&<small className="a-danger" role="status">{error||(!onNavigate&&session?.nextMessageError)||session.bundleChange.error}</small>}
  </section>)}
  {onNavigate&&page!=='bundle'&&(error||resetBundle)&&<small className="a-danger" role="status">{error||'Your pinned model is unavailable in this bundle. Open Agent bundle to review.'}</small>}
 </div>;
}
function DetailedBundleControl({state,session,act,working}){
 const shared=state.view?.composerBundle||EMPTY,[draft,setDraft]=useState(shared),[submitting,setSubmitting]=useState(null),[previewing,setPreviewing]=useState(false),[error,setError]=useState(''),submittingRef=useRef(false),previewRef=useRef(false);
 const nextMessage=useNextMessageConfiguration(session,act);
 const draftRevision=useRef({sessionId:session?.id,revision:session?.nextMessageRevision||0});
 if(draftRevision.current.sessionId!==session?.id)draftRevision.current={sessionId:session?.id,revision:session?.nextMessageRevision||0};
 useEffect(()=>setDraft(shared),[shared]);
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{composerBundle:next}})};
 const setup=newChatSetup(state),current=session?.pendingBundleSelection?.bundle||session?.bundle||setup.bundle||state.bundleDefaults?.effective||state.settings?.bundle||'work',open=draft.open&&draft.sessionId===(session?.id||null);
 const popover=useRef(null);useOutsideDismiss(open,popover,()=>edit({open:false}));
 const operation=session?.bundleChange,preview=session?.bundlePreview;
 // Preview reads may overlap saves; neither completion owns the other's guard.
 const previewPending=previewing||(operation?.phase==='working'&&operation.action==='bundle.preview');
 const pending=!!submitting||!!session?.configurationBusy||(operation?.phase==='working'&&operation.action!=='bundle.preview');
 const ready=preview?.bundle===draft.bundle&&preview?.previewId;
 const unavailable=!!state.navigationPending||session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||conversationHistoryPending(session);
 const blocked=nextMessageBlocked(session,working),disabled=blocked||pending||unavailable||!draft.bundle?.trim();
 const needsModel=ready&&!preview.modelCompatible&&!draft.resetModel;
 const activeAction=submitting||(previewPending?'bundle.preview':operation?.action);
 const progress=activeAction==='bundle.preview'?'Previewing bundle changes…':activeAction==='bundle.fork'?'Creating a conversation with this bundle…':'Saving Next message settings…';
 async function run(action){
  const isPreview=action==='bundle.preview';
  if(submittingRef.current||disabled||((isPreview||action==='bundle.fork')&&(previewRef.current||previewPending))||(action==='bundle.fork'&&working))return;
  if(isPreview){previewRef.current=true;setPreviewing(true)}else{submittingRef.current=true;setSubmitting(action)}
  setError('');
  try{
   if(action==='conversation.configure')await nextMessage.configure({bundle:draft.bundle,resetModel:!!draft.resetModel},draftRevision.current.revision);
   else await act(action,action==='view.update'?{patch:{newSessionDraft:{...newChatSetup(state),bundle:draft.bundle},composerBundle:{},panel:null}}:{sessionId:session.id,bundle:draft.bundle,...(action!=='bundle.preview'?{...(ready?{previewId:preview.previewId}:{}),resetModel:!!draft.resetModel}:{})});
  }catch(error){setError(error.message)}
  finally{if(isPreview){previewRef.current=false;setPreviewing(false)}else{submittingRef.current=false;setSubmitting(null)}}
 }
 return <div className="a-model-control a-bundle-control" ref={popover}>
  <button type="button" className="a-model-trigger a-bundle-trigger" aria-label="Conversation bundle" disabled={unavailable} aria-expanded={!!open} data-action="view.update" onClick={()=>{draftRevision.current={sessionId:session?.id,revision:session?.nextMessageRevision||0};setError('');edit({open:!open,sessionId:session?.id||null,bundle:current,resetModel:false})}}><Layers/><span>{!session&&!setup.bundle?'Workspace default':bundleLabel(state,current)}</span>{session?.pendingBundleSelection&&<small>Next message</small>}<ChevronDown/></button>
  {open&&<section className="a-model-popover a-bundle-popover" aria-label="Choose conversation bundle">
   <div className="a-settings-row"><strong>Conversation bundle</strong><button type="button" className="a-icon" aria-label="Close bundle settings" data-action="view.update" onClick={()=>edit({open:false})}><X/></button></div>
   <p>Choose the tools, agents, and instructions for this conversation.</p>
   <BundlePicker id="conversation-bundle" value={draft.bundle||current} disabled={blocked||pending||unavailable} onChange={bundle=>edit({bundle,resetModel:false})} state={state} act={act}/>
   {session?<>
    <p className="a-caption">Next message · Save a bundle without starting the conversation, or preview its changes first. Applying it on your next message keeps history and compatible model choices, and resets previous module edits, modes, and budget overrides.</p>
    {ready&&<div className="a-bundle-changes" aria-label="Bundle changes">
     {Object.entries(preview.changes||{}).map(([kind,change])=><div key={kind}><strong>{kind[0].toUpperCase()+kind.slice(1)}</strong>{'after'in change?<p>{change.before||'None'} → {change.after||'None'}</p>:<>{change.added?.length>0&&<p>Add: {change.added.join(', ')}</p>}{change.removed?.length>0&&<p>Remove: {change.removed.join(', ')}</p>}{!change.added?.length&&!change.removed?.length&&<p>Same set; settings may change.</p>}</>}</div>)}
     <p>Instructions are replaced. Enabled app capabilities and shared settings still apply.</p>
     {preview.appCapabilities?.length>0&&<details><summary>Added app capabilities ({preview.appCapabilities.length})</summary><ul>{preview.appCapabilities.map(value=><li key={value} className="a-wrap">{value}</li>)}</ul></details>}
     {!preview.modelCompatible&&<><p>Your pinned model is unavailable in this bundle.</p><label><input type="checkbox" checked={!!draft.resetModel} data-action="view.update" onChange={e=>edit({resetModel:e.target.checked})}/> Use the new bundle’s model</label></>}
    </div>}
    <div className="a-dialog-actions">
     <button type="button" className="a-primary" disabled={disabled||needsModel||(draft.bundle===current&&!draft.resetModel)} data-action="conversation.configure" onClick={()=>run('conversation.configure')}>Save for Next message</button>
     <button type="button" className="a-soft" disabled={disabled||previewPending} data-action="bundle.preview" onClick={()=>run('bundle.preview')}>Preview changes</button>
     <button type="button" className="a-soft" disabled={disabled||previewPending||working||needsModel} data-action="bundle.fork" onClick={()=>run('bundle.fork')}>Fork with this bundle</button>
    </div>
    {blocked&&<p>Resolve held configuration or ownership before saving Next message settings. Active work alone does not block these choices.</p>}
    {(pending||previewPending||error||session?.nextMessageError||session?.pendingBundleSelection||operation)&&<ResultNotice phase={pending||previewPending?'working':error||session?.nextMessageError?'error':session?.pendingBundleSelection?'ready':operation?.phase} message={pending||previewPending?progress:error||session?.nextMessageError||(session?.pendingBundleSelection?'Next message settings saved; not applied yet.':operation?.error||(operation?.phase==='ready'?operation.action==='bundle.preview'?'Preview ready':operation.action==='bundle.fork'?'Fork created':'Bundle applied':''))}/>}
   </>:<div className="a-dialog-actions"><button type="button" className="a-primary" disabled={disabled} data-action="view.update" onClick={()=>run('view.update')}>Use bundle</button><button type="button" className="a-soft" disabled={pending||!setup.bundle} data-action="view.update" onClick={()=>act('view.update',{patch:{newSessionDraft:{...setup,bundle:''},composerBundle:{}}})}>Use workspace default</button></div>}
  </section>}
 </div>;
}

export function BundleDefaults({state,act}){
 const values=state.bundleDefaults||{},shared=state.view?.bundleDefaultsDraft||EMPTY;
 const [draft,setDraft]=useState(shared),[saving,setSaving]=useState(false),[saveError,setSaveError]=useState(''),savingRef=useRef(false);
 useEffect(()=>setDraft(shared),[shared]);
 const scope=['app','shared'].includes(draft.scope)?draft.scope:'app',value=draft.scope==='workspace'?values.app||'':draft.bundle??values[scope]??'';
 const edit=patch=>{const next={...draft,...patch};setDraft(next);act('view.update',{patch:{bundleDefaultsDraft:next}})};
 async function save(bundle){if(savingRef.current)return;savingRef.current=true;setSaving(true);setSaveError('');try{const receipt=await act('bundle.default',{scope,bundle});if(receipt?.accepted!==true)throw Error(receipt?.error||'Could not save the default. Try again.');return true}catch(error){setSaveError(error.message);return false}finally{savingRef.current=false;setSaving(false)}}
 return <div><label htmlFor="bundle-default-scope">Default applies to</label><select id="bundle-default-scope" disabled={saving} value={scope} data-action="view.update" onChange={e=>edit({scope:e.target.value,bundle:values[e.target.value]||''})}><option value="app">This Unified app</option><option value="shared">Shared Amplifier settings (including CLI)</option></select><label htmlFor="default-bundle">Default bundle for new conversations</label><BundlePicker id="default-bundle" value={value} state={state} act={act} disabled={saving} onChange={bundle=>edit({scope,bundle})} onSelect={bundle=>{edit({scope,bundle});save(bundle)}}/><SettingsActions>{state.view?.bundleSources?.includes('default-bundle')&&<button type="button" className="a-primary" disabled={saving||!value.trim()} data-action="bundle.default" onClick={()=>save(value.trim())}>Save custom source</button>}<button type="button" className="a-soft" disabled={saving} data-action="bundle.default" onClick={async()=>{if(await save(null))edit({scope,bundle:''})}}>Use inherited default</button></SettingsActions><p role="alert">{saveError}</p><p className="a-caption">Workspace overrides are managed in each workspace’s Details tab. Existing conversations keep their selected bundles.</p></div>;
}

export function WorkspaceBundleDefault({workspace,state,act}){
 const [values,setValues]=useState(null),[value,setValue]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const inspect=async()=>{const receipt=await act('workspace.bundleDefault.inspect',{workspaceId:workspace.id});if(!receipt?.accepted)throw Error(receipt?.error||'Default unavailable.');return receipt.result};
 useEffect(()=>{let live=true;inspect().then(result=>{if(live){setValues(result);setValue(result.workspace||'')}}).catch(err=>{if(live)setError(err.message)});return()=>{live=false}},[workspace.id]);
 async function save(bundle){setBusy(true);setError('');try{const result=await act('bundle.default',{scope:'workspace',bundle,workspace:workspace.path});if(!result?.accepted)throw Error(result?.error||'Could not save.');const next=await inspect();setValues(next);setValue(next.workspace||'')}catch(err){setError(err.message)}finally{setBusy(false)}}
 return <section aria-label="Workspace bundle default"><h3>Bundle for new chats in this workspace</h3><BundlePicker id={'workspace-bundle-'+workspace.id} value={value} state={state} act={act} disabled={busy||!values} onChange={setValue} onSelect={bundle=>{setValue(bundle);save(bundle)}}/>{state.view?.bundleSources?.includes('workspace-bundle-'+workspace.id)&&<button type="button" disabled={busy||!values||!value.trim()} onClick={()=>save(value.trim())}>Save custom source</button>}<button type="button" disabled={busy||!values} onClick={()=>save(null)}>Use inherited workspace default</button>{values&&<p className="a-caption">New chats use {bundleLabel(state,values.effective)}. Existing chats keep their bundle.</p>}{error&&<p role="alert">{error}</p>}</section>;
}
