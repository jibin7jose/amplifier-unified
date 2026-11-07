import {conversationHistoryPending} from './history-window.js';
import {configurationHeld} from './configuration-recovery';
import {ownershipState} from './ownership.js';
import {delegationRoutingLabel} from './delegation-routing.js';
import {useOutsideDismiss} from './use-outside-dismiss';
import React,{useEffect,useState,useRef,useId} from 'react';
import {ChevronDown,X,Paperclip,Download} from 'lucide-react';
import {providerFields} from './setup-data';
import {ModelSelect} from './model-select';
import {newChatSetup,draftDefaults} from './new-chat';
import {useComposerPopover} from './composer-popover';
import {providerOptions} from './provider-options';
import {useDraftDiscovery} from './use-draft-discovery';
import {AttachmentPreview} from './attachment-preview';
import {imageAttachment} from './attachment-view.js';
const EMPTY={};
export function AttachmentStrip({items=[],remove,act,sessionId,privateDraft=false}){
 const [error,setError]=useState(''),[pending,setPending]=useState(null),[image,setImage]=useState(null);
 useEffect(()=>{if(image&&(image.sessionId!==sessionId||!items.some(file=>file.id===image.file.id)))setImage(null)},[sessionId,items,image]);
 const showImage=(file,event)=>setImage({file,sessionId,opener:event.currentTarget});
 const imageVisible=image&&image.sessionId===sessionId&&items.some(file=>file.id===image.file.id);
 async function preview(file){
  if(pending)return;setPending(file.id);setError('');
  try{await act('canvas.openAttachment',{sessionId,id:file.id})}
  catch(error){setError(error.message||'Could not open this attachment. You can still download it.')}
  finally{setPending(null)}
 }
 if(!items.length)return null;
 return <div className="a-attachments" data-private={privateDraft||undefined}>{items.map(file=><div className="a-attachment" key={file.id} data-attachment-id={file.id}>{imageAttachment(file)?<button type="button" className="a-attachment-preview-button" aria-haspopup="dialog" aria-label={'Preview '+file.name} onClick={e=>showImage(file,e)}><img src={file.url} alt={file.name}/></button>:<Paperclip/>}{imageAttachment(file)?<button type="button" className="a-attachment-preview-button a-attachment-preview-name" aria-haspopup="dialog" aria-label={'Preview '+file.name} title={file.name} onClick={e=>showImage(file,e)}>{file.name}<small>{Math.ceil(file.size/1024)} KB</small></button>:act&&sessionId&&file.mime==='text/plain'&&file.size<=1000000?<button type="button" className="a-link" disabled={!!pending} data-action="canvas.openAttachment" onClick={()=>preview(file)}>{file.source==='clipboard-text'?(file.preview||'Pasted text'):file.name}<small>{Math.ceil(file.size/1024)} KB</small></button>:<a href={file.url} target="_blank" rel="noopener noreferrer" title={file.name}>{file.source==='clipboard-text'?(file.preview||'Pasted text'):file.name}<small>{file.source==='clipboard-text'?'Pasted text · ':''}{Math.ceil(file.size/1024)} KB</small></a>}{act&&sessionId&&<a href={file.url} download={file.name} aria-label={"Download "+file.name}><Download size={14}/></a>}{remove&&<button type="button" className="a-icon" aria-label={'Remove '+file.name} data-action="attachment.remove" onClick={()=>remove(file.id)}><X/></button>}</div>)}{error&&<span role="alert">{error}</span>}{imageVisible&&<AttachmentPreview key={image.file.id} {...image} privateDraft={privateDraft} onClose={()=>setImage(null)}/>}</div>;
}
function configuredEffort(row,model){
 const field=providerFields(row,{model,default_model:model}).find(field=>field.id==='reasoning_effort');
 return row?.effort||row?.config?.reasoning_effort||row?.info?.defaults?.reasoning_effort||field?.default||'';
}
export function nextMessageBlocked(session,working=false){
 const generation=session?.collaborationGeneration;
 return !!(configurationHeld(session)||ownershipState(session).blocked||
  (generation?.id&&!generation.terminal)||
  (session?.messages||[]).some(row=>row.delivery?.status==='sending')||
  (session?.workers||[]).some(row=>['queued','starting','working','running','stopping'].includes(row.status))||
  ['working','running','busy','stopping'].includes(session?.status)||(working&&session?.status!=='starting'));
}
// Bridge only our acknowledged saves while their state update is in flight.
// Never replace an action's captured revision with a later poll/client revision.
export function useNextMessageConfiguration(session,act,sharedControl=null){
 const sessionId=session?.id,revision=session?.nextMessageRevision||0,owner=useRef(null);
 const [saving,setSaving]=useState(null);
 if(!owner.current||owner.current.sessionId!==sessionId)owner.current={sessionId,pending:false,ack:null};
 async function configure(patch,capturedRevision=revision){
  const request=owner.current;
  if(request.sessionId!==sessionId||request.pending)return false;
  const ack=request.ack,expectedRevision=ack&&capturedRevision>=ack.from&&capturedRevision<=ack.to?ack.to:capturedRevision;
  request.pending=true;setSaving(request);
  try{
   const receipt=await act('conversation.configure',{sessionId,expectedRevision,...patch});
   const next=receipt?.result?.revision;
   if(receipt?.accepted!==true||!Number.isInteger(next)||next!==expectedRevision+1)
    throw Error(receipt?.error||'Next message settings were not confirmed. Review the saved choices before trying again.');
   request.ack={from:ack?.to===expectedRevision?ack.from:expectedRevision,to:next};
   return true;
  }finally{request.pending=false;setSaving(current=>current===request?null:current)}
 }
 return sharedControl||{configure,pending:saving===owner.current};
}
export function ModelControl({state,session,act,working,embedded=false,compact=false,children,nextMessageControl,detail='standard'}){
 const descriptionId=useId();
 const setup=newChatSetup(state),isDraft=!session,sessionId=session?.id||null,defaults=draftDefaults(state);
 const nextMessage=useNextMessageConfiguration(session,act,nextMessageControl);
 // A different bundle has different defaults. Until its probe resolves, do not
 // present the previous bundle's connection as the next request's destination.
 const configurationRevision=state.configurationRevision||0,nextMessageRevision=session?.nextMessageRevision||0;
 const resolvedDefaults=defaults;
 const checkingDefaults=isDraft&&defaults.phase!=='ready'&&defaults.phase!=='error';
 const unavailable=!!state.navigationPending||session?.workspaceAvailable===false||!!session?.historyReadOnlyReason||conversationHistoryPending(session);
 const blocked=unavailable||nextMessageBlocked(session,working)||nextMessage.pending;
 const shared=state.view?.composerModel||EMPTY,[draft,setDraft]=useState(shared),[error,setError]=useState(''),effortPending=useRef(null);
 useEffect(()=>{
  const pending=effortPending.current;
  // A poll can replace an equal view object during a slider gesture. Preserve
  // that gesture only within its original chat/provider/model selection.
  if(pending&&pending.sessionId===sessionId&&pending.instance===shared.instance&&pending.model===shared.model)
   setDraft({...shared,effort:pending.effort});
  else{effortPending.current=null;setDraft(shared)}
 },[shared,sessionId]);
 const edit=patch=>{const next={...draft,...patch,...(embedded?{open:true,sessionId}:{})};setDraft(next);act('view.update',{patch:{composerModel:next}})};
 const managed=setup.location?.kind==='managed';
 const location=managed?{location:{kind:'managed'}}:{};
 const draftCatalog=state.setup?.providersRequestedWorkspace===setup.workspace&&(state.setup?.providersLocation?.kind==='managed')===managed?state.setup:EMPTY;
 const cachedProviders=(draftCatalog.providers||[]).filter(row=>row.enabled!==false);
 const controls=state.runtimeControl?.[sessionId]||{},catalog=controls['configuration.catalog'];
 const catalogIsCurrent=catalog?.configurationRevision===configurationRevision&&(catalog?.nextMessageRevision||0)===nextMessageRevision;
 const rosterIsCurrent=catalog?.configurationRevision===configurationRevision;
 const pendingBundle=session?.pendingBundleSelection;
 const providers=isDraft?cachedProviders:(rosterIsCurrent?catalog.providers||[]:[]);
 const pinned=isDraft?!!setup.selection?.model:(catalog?.pinned??!!(session?.runtimeReport?.selection||session?.selection));
 const initial=!catalog&&!session?.runtimeReport?session?.initialModel:undefined;
 const effective=isDraft?(pinned?setup.selection:resolvedDefaults.effective||{}):(session?.pendingModelSelection||
  (pendingBundle?(!pendingBundle.resetModel&&session?.selection)||(catalogIsCurrent&&catalog?.effective)||{}:
   session?.selection||(catalogIsCurrent&&catalog?.effective)||session?.runtimeReport?.effective_selection||initial||{}));
 const lastCall=[...(session?.execution?.nodes||[])].reverse().find(n=>n.kind==='llm'&&(n.sessionId===sessionId||(!n.sessionId&&!n.parentId)));
 const noProviders=(isDraft?defaults.phase==='ready':rosterIsCurrent&&Array.isArray(catalog?.providers))&&!providers.length;
 const model=effective.model||(!isDraft&&!pendingBundle?lastCall?.model:'')||(noProviders?'Set up a model':pendingBundle?'Bundle default':defaults.phase==='error'?'Model unavailable':!isDraft?'Choose a model':'Loading model…');
 const provider=effective.instance||effective.id||(!isDraft&&!pendingBundle?lastCall?.provider:'')||'';
 const effectiveProvider=providers.find(row=>row.id===provider),effectiveMetadata=state.modelCatalogs?.[effectiveProvider?.sharedCatalogKey]?.metadata,effectiveEffort=effective.effort||configuredEffort(effectiveProvider,model)||configuredEffort(effectiveMetadata,model);
 const options=providerOptions(providers);
 const providerLabel=options.find(row=>row.id===provider)?.label||provider;
 const modelAndEffort=effectiveEffort?`${model} (${effectiveEffort})`:model;
 const usingChatGPTPlan=effectiveProvider?.info?.defaults?.auth_mode==='chatgpt_plan'||(isDraft&&effectiveProvider?.config?.auth_mode==='chatgpt_plan');
 const modelLabel=providerLabel?`${providerLabel} · ${modelAndEffort}`:modelAndEffort;
 const open=embedded||(draft.open&&(draft.sessionId??null)===sessionId),popover=useRef(null),{style:position,panelRef,renderPopover}=useComposerPopover(open&&!embedded,popover);
 // The narrow-screen menu is locally mounted, but catalog demand is shared
 // with the server just like the desktop popover.
 useEffect(()=>{
  if(!embedded)return;
  act('view.update',{patch:{composerModel:{...shared,open:true,sessionId}}});
  return()=>{act('view.update',{patch:{composerModel:{open:false,sessionId}}})};
 },[embedded,sessionId]);
 const focusRequested=useRef(false);
 const close=reason=>{focusRequested.current=false;edit({open:false});if(compact&&reason!=='outside')popover.current?.querySelector('button')?.focus()};
 useOutsideDismiss(open&&!embedded,popover,close,panelRef);
 useEffect(()=>{
  if(compact&&open&&position.visibility==='visible'&&focusRequested.current){
   focusRequested.current=false;
   const panel=panelRef.current;
   (panel?.querySelector('#chat-provider:not(:disabled)')||panel?.querySelector('button:not(:disabled)'))?.focus();
  }
 },[compact,open,position.visibility]);
 useEffect(()=>{focusRequested.current=false},[sessionId]);
 const previousPanel=useRef(state.view?.panel);
 useEffect(()=>{
  if(previousPanel.current===state.view?.panel)return;
  previousPanel.current=state.view?.panel;
  if(compact&&open)close('outside');
 },[state.view?.panel]);
 const selected=providers.find(row=>row.id===draft.instance);
 const draftEntry=draftCatalog.providerCatalogs?.[draft.instance];
 const binding=!isDraft&&catalog?.connectionBindings?.[draft.instance];
 const entry=binding?.eligible===false?null:state.modelCatalogs?.[isDraft?draftEntry?.sharedCatalogKey:selected?.sharedCatalogKey]||(isDraft?draftEntry:null);
 const metadata=entry?.metadata||selected||{};
 const choices=(providerFields(metadata,{model:draft.model,default_model:draft.model}).find(field=>field.id==='reasoning_effort')?.choices||[]).map(row=>typeof row==='object'?row.value:row);
 const effort=draft.effort||configuredEffort(selected,draft.model)||configuredEffort(metadata,draft.model);
 const touched=useRef(false),draftRevision=useRef({sessionId,revision:nextMessageRevision});
 if(draftRevision.current.sessionId!==sessionId){touched.current=false;draftRevision.current={sessionId,revision:nextMessageRevision}}
 const discovery=useDraftDiscovery({enabled:isDraft,setup,defaults,configurationRevision,hostInstanceId:state.hostInstanceId,act,needsProviders:draftCatalog===EMPTY});
 useEffect(()=>{
  if(open&&!touched.current){draftRevision.current={sessionId,revision:nextMessageRevision};const instance=effective.instance||effective.id||draft.instance||'',row=providers.find(p=>p.id===instance);edit({instance,model:effective.model||draft.model||row?.info?.defaults?.model||'',effort:effectiveEffort||draft.effort||''})}
 },[open,sessionId,nextMessageRevision,providers.map(row=>row.id).join('|'),effective.instance,effective.id,effective.model,effectiveEffort]);
 function show(){touched.current=false;draftRevision.current={sessionId,revision:nextMessageRevision};effortPending.current=null;setError('');if(open){close();return}
  focusRequested.current=compact;
  edit({open:true,sessionId,instance:provider,model:effective.model||'',effort:effectiveEffort||''});
 }
 async function apply(patch,capturedRevision=draftRevision.current.revision){
  if(blocked)return;
  touched.current=true;const next={...draft,...patch};edit(patch);setError('');
  if(!providers.some(row=>row.id===next.instance)||!next.model||catalog?.connectionBindings?.[next.instance]?.eligible===false)return;
  const selection={instance:next.instance,model:next.model,...(next.effort?{effort:next.effort}:{})};
  try{if(isDraft)await act('view.update',{patch:{newSessionDraft:{...setup,selection}}});
   else await nextMessage.configure({selection},capturedRevision);
  }catch(e){setError(e.message)}
 }
 function chooseProvider(value){
  if(!value||blocked)return;
  const row=providers.find(row=>row.id===value);
  if(!row)return;touched.current=true;edit({instance:value,model:'',effort:''});
  if(isDraft&&!draftCatalog.providerCatalogs?.[value])act('providers.models',{id:value,workspace:setup.workspace,...location});
  else if(!isDraft&&!state.modelCatalogs?.[row.sharedCatalogKey])act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 }
 function previewEffort(event){if(blocked)return;const value=choices[Number(event.currentTarget.value)];touched.current=true;effortPending.current={sessionId,instance:draft.instance,model:draft.model,effort:value,revision:draftRevision.current.revision};setDraft({...draft,effort:value});}
 function commitEffort(){const pending=effortPending.current;effortPending.current=null;if(pending&&pending.sessionId===sessionId&&pending.instance===draft.instance&&pending.model===draft.model&&choices.includes(pending.effort))apply({effort:pending.effort},pending.revision);}
 const catalogRequest=useRef('');
 const missingCatalogs=providers.filter(row=>row.sharedCatalogKey&&!state.modelCatalogs?.[row.sharedCatalogKey]).map(row=>row.sharedCatalogKey).join('|');
 useEffect(()=>{
  if(!open||!session||unavailable)return;
  const key=sessionId+'|'+configurationRevision+'|'+nextMessageRevision+'|'+missingCatalogs;
  if(catalogRequest.current===key||catalogIsCurrent&&!missingCatalogs)return;
  catalogRequest.current=key;act('runtime.control',{sessionId,operation:'configuration.catalog',args:{}});
 },[open,sessionId,configurationRevision,nextMessageRevision,unavailable,missingCatalogs]);
 const failure=error||session?.nextMessageError||(isDraft?(discovery.error||defaults.error):(rosterIsCurrent&&catalog?.error));
 const delayed=isDraft&&checkingDefaults&&discovery.delayed;
 const renderPanel=embedded?content=>content:renderPopover;
 return <div className="a-model-control" ref={popover}>
  {!embedded&&<button type="button" className="a-model-trigger" aria-label="Model and reasoning settings" aria-haspopup={compact?'dialog':undefined} disabled={unavailable} aria-expanded={!!open} aria-busy={checkingDefaults||undefined} title={checkingDefaults?`${modelLabel} · Checking bundle settings…`:modelLabel} data-action="view.update" onClick={show}><span>{!effective.model&&(delayed||failure)?'Model settings unavailable':compact?modelAndEffort:modelLabel}</span>{(session?.pendingModelSelection||pendingBundle)&&<small>Next message</small>}<ChevronDown/></button>}
  {open&&renderPanel(<section ref={panelRef} className={embedded?"a-embedded-model":`a-model-popover a-compact-popover${compact?' a-composer-options-panel':''}`} style={embedded?undefined:position} role="dialog" aria-label="Model for this chat" aria-describedby={`${descriptionId}-heading`}>
   <div className="a-settings-row a-composer-options-heading"><strong id={`${descriptionId}-heading`}>Model for this chat</strong>{!embedded&&<button type="button" className="a-icon" aria-label={compact?'Close chat options':'Close model settings'} data-action="view.update" onClick={close}><X/></button>}</div>
   <p className="a-caption">Selected: {modelLabel}</p>
   {binding?.eligible===false&&<p role="status">{binding.reason} The existing selection is preserved.</p>}
   <div data-composer-page="provider">
   <label htmlFor="chat-provider">Connection{!isDraft&&catalog?.connectionScope&&<small> · {catalog.connectionScope} settings</small>}</label><select id="chat-provider" value={selected?draft.instance:''} data-action={isDraft?'view.update':'conversation.configure'} disabled={blocked} onChange={e=>chooseProvider(e.target.value)}>
    <option value="" disabled>{providers.length?'Choose a connection':noProviders?'No connections configured':failure||delayed?'Connections unavailable':'Loading connections…'}</option>{options.map(row=><option key={row.id} value={row.id} disabled={providers.find(p=>p.id===row.id)?.config?.chat_enabled===false}>{row.label}</option>)}
   </select>
   {draft.instance&&!selected&&<small>Saved connection {draft.instance} is not available in this configuration scope.</small>}
   </div>
   <div data-composer-page="model">
   <label htmlFor="chat-model">Model</label><ModelSelect key={draft.instance} id="chat-model" label="Conversation model" value={draft.model||''} entry={entry} state={state} act={act} disabled={blocked||!selected||binding?.eligible===false} onChange={model=>{touched.current=true;edit({model})}} onCommit={model=>apply({model,effort:draft.model===model?draft.effort:''})}/>
   </div>
   <div data-composer-page="effort">
   {choices.length>0?<><label htmlFor="chat-effort">Reasoning effort <strong>{effort||'Choose effort'}</strong></label>{effort&&!choices.includes(effort)&&<small>Saved effort {effort} is not advertised for this model. Choose an available effort to replace it.</small>}<input id="chat-effort" type="range" min="0" max={choices.length-1} step="1" value={Math.max(0,choices.indexOf(effort))} aria-valuetext={choices.includes(effort)?effort:'Choose effort; saved value is not advertised'} disabled={blocked||!draft.model} data-action={isDraft?'view.update':'conversation.configure'} onChange={previewEffort} onPointerUp={commitEffort} onKeyUp={commitEffort} onBlur={commitEffort}/><div className="a-effort-labels"><span>{choices[0]}</span><span>{choices.at(-1)}</span></div></>:<small>{entry?.metadata||selected?.configSchema||selected?.info?.config_fields?'Reasoning effort is not configurable for this model.':failure||delayed||entry?.phase==='error'?'Model capabilities unavailable.':'Loading model capabilities…'}{effort&&` Saved effort: ${effort}.`}</small>}
   </div>
   <div className="a-dialog-actions"><button type="button" className="a-link" disabled={!selected} onClick={()=>isDraft?act('providers.models',{id:draft.instance,workspace:setup.workspace,...location,refresh:true}):act('runtime.control',{sessionId,operation:'configuration.catalog',args:{refresh:true,instance:draft.instance}})}>Refresh this connection</button><button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:{composerModel:{...draft,open:false},panel:'settings',settingsSection:'setup',settingsExpanded:['ai-connections'],aiConnectionEditor:{...(state.view?.aiConnectionEditor||{}),step:'list'}}})}>Manage connections</button></div>
   <details><summary>Advanced</summary>{!isDraft&&<p className="a-muted" data-part="delegation-routing">{delegationRoutingLabel(catalog?.delegationRouting||session?.runtimeReport?.delegationRouting)}</p>}{children}</details>
   {usingChatGPTPlan&&<p className="a-caption">Using ChatGPT plan · <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage</a></p>}
   {catalog?.selectionIssue&&<p role="status">{catalog.selectionIssue}</p>}
   {catalog?.defaultError&&<p role="status">{catalog.defaultError}</p>}
   {isDraft&&(failure||delayed)&&<div className="a-discovery-recovery" role="status">{delayed&&!failure&&<p>Model settings are taking longer than expected. You can retry without refreshing or losing your draft.</p>}<button type="button" className="a-soft" onClick={discovery.retry}>Retry model settings</button></div>}
   {failure&&<small className="a-danger" role="status">{failure}</small>}
   <p className="a-caption" role="status">{nextMessage.pending?'Saving Next message settings…':!isDraft&&(session?.pendingModelSelection||pendingBundle)?'Next message · Saved; not applied yet.':'Next message · Changes apply when you send.'}</p>
  </section>)}
 </div>;
}
export function readAttachment(file){return new Promise((resolve,reject)=>{if(file.size>32*1024*1024||!file.size){reject(new Error('Choose a nonempty file up to 32 MB.'));return}const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.onabort=()=>reject(new Error('Reading '+file.name+' was cancelled. Nothing was uploaded.'));reader.readAsDataURL(file)})}
