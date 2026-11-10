import {SettingsActions} from './settings-layout';
import {ActivityRegion} from './activity-region';
import React,{useEffect,useRef,useState} from 'react';
import {WorkingLabel} from './working-label';
import {Activity,Plus,ArrowLeft,Trash2,Download,RefreshCw} from 'lucide-react';
import {ResultNotice,SettingsGroup} from './settings-ui';

const newDestination=()=>({id:crypto.randomUUID(),name:'Personal server',url:'',enabled:false,streams:['sessions','workers','tools','usage','updates'],workspace:'amplifier-unified',workspacePattern:'*',includePaths:false,authMode:'static',apiKeyEnv:'AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY',authResource:''});
export function DiagnosticsSettings({state,act}){
 const busy=action=>['queued','working','pending'].includes(state.actionStatus?.[action]?.phase);
 const info=state.diagnostics||{},draft=state.view?.diagnosticsDraft||{},cfg=draft.config||info.config,streams=info.streams||[];
 const [capturePending,setCapturePending]=useState({}),[saving,setSaving]=useState(''),[retryPatch,setRetryPatch]=useState(null),[saveError,setSaveError]=useState(''),saveQueue=useRef(Promise.resolve()),sending=useRef(false),alive=useRef(true),latestDraft=useRef(draft);
 latestDraft.current=draft;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 if(!cfg)return null;
 const saveBusy=!!saving||busy('diagnostics.configure');
 const change=patch=>act('view.update',{patch:{diagnosticsDraft:{...draft,...patch}}});
 const config=patch=>change({config:{...cfg,...patch}}),selected=cfg.destinations.find(d=>d.id===draft.destinationId);
 const update=patch=>config({destinations:cfg.destinations.map(d=>d.id===selected.id?{...d,...patch}:d)});
 const persist=async(value={...info.config,destinations:cfg.destinations},removeId=null,patch=null)=>{
  if(sending.current)return;
  sending.current=true;setSaving(removeId?'remove':patch?'auto':'save');if(!patch||retryPatch&&Object.keys(retryPatch).every(key=>Object.hasOwn(patch,key))){setSaveError('');setRetryPatch(null)}
  const submitted=JSON.stringify(draft.config);
  try{
   const receipt=await act('diagnostics.configure',patch?{patch}:{config:value});
   if(receipt?.accepted!==true)throw Error('Save was not confirmed');
   // A later draft or a reopened panel must not be cleared by this receipt.
   const current=latestDraft.current;
   if(alive.current&&JSON.stringify(current.config)===submitted){
    await act('view.update',{patch:{diagnosticsDraft:{...current,config:patch&&current.config?{...current.config,...patch}:null,...(removeId&&current.destinationId===removeId?{destinationId:null}:{})}}});
   }
  }catch{
   if(alive.current){setRetryPatch(patch);setSaveError('Could not save diagnostics settings. Your entries are kept. Try again.');}
  }finally{sending.current=false;if(alive.current)setSaving('');}
 };
 const save=(...args)=>{
  const work=saveQueue.current.catch(()=>{}).then(()=>persist(...args));
  saveQueue.current=work;return work;
 };
 const capture={...info.config,...capturePending};
 const apply=patch=>{
  setCapturePending(current=>({...current,...patch}));
  return save(null,null,patch).finally(()=>setCapturePending(current=>Object.fromEntries(Object.entries(current).filter(([key,value])=>!Object.hasOwn(patch,key)||value!==patch[key]))));
 };
 const result=info.lastResult,delivery=selected&&info.destinations?.find(d=>d.id===selected.id),test=selected&&info.results?.[selected.id];
 const toggle=(current,id,on)=>on?[...current,id]:current.filter(x=>x!==id);
 const pickStreams=(values,onChange,local=false)=><div className="a-diagnostic-streams">{streams.map(s=><label className="a-inline-checkbox" key={s.id}><input type="checkbox" data-action={local?"diagnostics.configure":"view.update"} checked={values.includes(s.id)} disabled={!local&&!cfg.streams.includes(s.id)} onChange={e=>onChange(toggle(values,s.id,e.target.checked))}/><span>{s.label}</span></label>)}</div>;
 return <SettingsGroup id="diagnostics" title="Diagnostics & Context Intelligence" summary="Local capture, selected streams and your servers" state={state} act={act}>
 <p>Keep diagnostic records on this computer, or send selected streams to your own Context Intelligence servers. Each server has its own choices. App forwarding starts only after you save an enabled destination. Bundle exporters have their own settings.</p>
 {selected?<ActivityRegion name={'diagnostic-destination-'+selected.id} busy={test?.phase==='working'}><button className="a-link a-diagnostics-back" data-action="view.update" onClick={()=>change({destinationId:null})}><ArrowLeft/>All destinations</button>
 <fieldset className="a-diagnostics-fields" disabled={saveBusy}><div className="a-form-grid"><label>Name<input data-action="view.update" value={selected.name} onChange={e=>update({name:e.target.value})}/></label><label>Server URL<input type="url" placeholder="https://context.example.com" data-action="view.update" value={selected.url} onChange={e=>update({url:e.target.value})}/></label></div>
 <label className="a-inline-checkbox"><input type="checkbox" data-action="view.update" checked={selected.enabled} onChange={e=>update({enabled:e.target.checked})}/>Send new selected records to this server</label>
 <label>Authentication<select data-action="view.update" value={selected.authMode} onChange={e=>update({authMode:e.target.value})}><option value="static">API key from environment</option><option value="entra">Microsoft Entra</option></select></label>
 {selected.authMode==='static'?<><label>API key environment variable<input data-action="view.update" value={selected.apiKeyEnv} onChange={e=>update({apiKeyEnv:e.target.value})}/></label><button className="a-soft" data-action="diagnostics.environment" onClick={()=>act('diagnostics.environment',{name:selected.apiKeyEnv})}>Check variable</button>{result?.action==='diagnostics.environment'&&result.name===selected.apiKeyEnv&&<ResultNotice {...result}/>}</>:<label>Entra resource URI<input data-action="view.update" value={selected.authResource} placeholder="api://application-id" onChange={e=>update({authResource:e.target.value})}/></label>}
 <h4>Streams for this server</h4>{pickStreams(selected.streams,values=>update({streams:values}))}
 <p className="a-caption">Conversation text may contain private information. Redaction is best effort. Tool arguments, results, reasoning, file contents and canvas HTML are excluded from metadata streams.</p>
 <div className="a-form-grid"><label>Server workspace label<input data-action="view.update" value={selected.workspace} onChange={e=>update({workspace:e.target.value})}/></label><label>Capture from workspace paths<input data-action="view.update" value={selected.workspacePattern} placeholder="* or /Users/me/work/*" onChange={e=>update({workspacePattern:e.target.value})}/></label></div>
 <p className="a-caption">The label organizes records; it is not an access boundary on a shared server. Path filters use * and ? wildcards.</p>
 <label className="a-inline-checkbox"><input type="checkbox" data-action="view.update" checked={selected.includePaths} onChange={e=>update({includePaths:e.target.checked})}/>Include workspace folder paths in remote records</label></fieldset>
 <div className="a-dialog-actions"><SettingsActions><button className="a-primary" data-action="diagnostics.configure" disabled={saveBusy} aria-busy={saving==='save'||undefined} onClick={()=>save()}><WorkingLabel active={saving==='save'} working="Saving…">Save destination</WorkingLabel></button></SettingsActions><button className="a-soft" data-operation-pending={test?.phase==='working'||undefined} aria-busy={test?.phase==='working'||undefined} data-action="diagnostics.test" disabled={saveBusy||!!draft.config||test?.phase==='working'} onClick={()=>act('diagnostics.test',{id:selected.id})}>Test connection</button><button className="a-soft" data-action="diagnostics.retry" disabled={!delivery?.counts?.failed} onClick={()=>act('diagnostics.retry',{id:selected.id})}><RefreshCw/>Retry failed deliveries</button></div>
 <p className="a-caption">Save before testing. A test sends one synthetic event to verify authentication and write access.</p>{!draft.config&&<ResultNotice {...test}/>}
 <p className="a-caption">{delivery?.counts?.accepted||0} accepted · {delivery?.counts?.duplicate||0} recognized retries · {delivery?.counts?.pending||0} queued · {delivery?.counts?.failed||0} failed · {delivery?.counts?.cancelled||0} cancelled</p>
 {delivery?.error&&<ResultNotice phase="error" message={`Delivery needs attention: ${delivery.error.type}${delivery.error.statusCode?' (HTTP '+delivery.error.statusCode+')':''}`}/>}
 <button className="a-soft a-danger" data-action="diagnostics.configure" disabled={saveBusy} aria-busy={saving==='remove'||undefined} onClick={()=>save({...info.config,destinations:cfg.destinations.filter(d=>d.id!==selected.id)},selected.id)}><Trash2/><WorkingLabel active={saving==='remove'} working="Removing…">Remove destination</WorkingLabel></button>
 </ActivityRegion>:<><fieldset className="a-diagnostics-fields" disabled={saveBusy&&saving!=='auto'}><label className="a-inline-checkbox"><input type="checkbox" data-action="diagnostics.configure" checked={capture.enabled} onChange={e=>apply({enabled:e.target.checked})}/>Collect app diagnostics and enable selected forwarding</label>
 <p className="a-caption">Sessions use the community Context Intelligence hook and shared files. The choices below control this app’s metadata index and forwarding; they do not disable the hook.</p>
 <h4>Model request & response details</h4>
 <p role="status"><strong>Saved recording setting: {info.config?.providerRequests?'On':'Off'}</strong></p>
 <label className="a-inline-checkbox"><input type="checkbox" data-action="diagnostics.configure" checked={capture.providerRequests===true} onChange={e=>apply({providerRequests:e.target.checked})}/>Record provider requests and responses for troubleshooting</label>
 <p className="a-caption">This choice is saved automatically. Recording starts when a supported conversation runtime next prepares; it does not change an already-running runtime. Start a new chat to check recording without interrupting current work. Explicit provider opt-outs are kept. Earlier calls cannot be recovered.</p>
 <p className="a-caption">Saved records can contain private messages, instructions, and tool inputs. Recognizable credentials are redacted. Inline images are represented by their type, size, and fingerprint instead of image data; your attachments and model requests are unchanged. Large records have a bounded log preview and a separate downloadable copy, limited to 16 million characters and 64 MiB. Partial captures are labeled. Request and response availability depends on the provider; some adapters expose only a summary. Downloads are stored beside the session log on this host; bundle exporters may include only the preview.</p>
 <h4>Streams to index and make available for forwarding</h4>{pickStreams(capture.streams,values=>apply({streams:values}),true)}
 <div className="a-form-grid"><label>Index days<input type="number" min="1" max="365" value={cfg.retentionDays} data-action="view.update" onChange={e=>config({retentionDays:e.target.value})} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}} onBlur={e=>{if(e.target.checkValidity()&&e.target.value!==''&&Number(e.target.value)!==info.config.retentionDays)apply({retentionDays:Number(e.target.value)})}}/></label><label>Maximum indexed records<input type="number" min="100" max="100000" value={cfg.maxRecords} data-action="view.update" onChange={e=>config({maxRecords:e.target.value})} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}} onBlur={e=>{if(e.target.checkValidity()&&e.target.value!==''&&Number(e.target.value)!==info.config.maxRecords)apply({maxRecords:Number(e.target.value)})}}/></label></div>
 <p className="a-caption">These limits apply to the local index and queued deliveries. Shared Context Intelligence events.jsonl files are preserved. Indexed: {info.local?.storageError&&info.local?.records==null?'unavailable':(info.local?.records??0)+' records'}.{info.local?.dropped||info.local?.outboxDropped||info.local?.expiredPending?` Dropped: ${info.local.dropped||0}; outbox limit: ${info.local.outboxDropped||0}; expired before delivery: ${info.local.expiredPending||0}.`:''}</p>
 {info.local?.configurationError&&<ResultNotice phase="error" message="The saved diagnostics configuration could not be read. Capture and forwarding are disabled; save reviewed settings to restore them."/>}
 {info.local?.storageError&&<ResultNotice phase="error" message="Local diagnostic storage needs attention. Conversations can continue."/>}
 </fieldset>
 <h4>Destinations</h4><div className="a-diagnostic-destinations">{cfg.destinations.map(d=>{const status=info.destinations?.find(row=>row.id===d.id);return <button className="a-settings-group-title" data-action="view.update" key={d.id} onClick={()=>change({destinationId:d.id})}><span><strong>{d.name}</strong><small>{d.enabled?'Sending selected streams':'Disabled'} · {d.streams.length} streams{status?.error?' · Needs attention':''}</small></span><Activity/></button>})}</div>
 <button className="a-soft" data-action="view.update" disabled={saveBusy} onClick={()=>{const d=newDestination();change({config:{...cfg,destinations:[...cfg.destinations,d]},destinationId:d.id})}}><Plus/>Add server</button>
 <p className="a-caption">Saving a changed destination cancels its old pending deliveries. Data already accepted or in flight cannot be recalled. Selecting another stream starts with new records; history is never uploaded automatically.</p>
 <ActivityRegion name="diagnostic-records" busy={busy('diagnostics.records')}><h4>Inspect local records</h4><div className="a-form-grid"><label>Stream filter<input data-action="view.update" value={draft.stream||'*'} onChange={e=>change({stream:e.target.value})} placeholder="* or updates"/></label><label>Session ID (optional)<input data-action="view.update" value={draft.sessionId||''} onChange={e=>change({sessionId:e.target.value})}/></label></div>
 <div className="a-dialog-actions"><button className="a-soft" data-action="diagnostics.records" onClick={()=>act('diagnostics.records',{stream:draft.stream||'*',...(draft.sessionId?{sessionId:draft.sessionId}:{})})}>Read records</button>{result?.action==='diagnostics.records'&&<><button className="a-soft" data-action="diagnostics.export" onClick={()=>act('diagnostics.export')}><Download/>Export this page</button>{result.nextBefore&&<button className="a-soft" data-action="diagnostics.records" onClick={()=>act('diagnostics.records',{stream:draft.stream||'*',...(draft.sessionId?{sessionId:draft.sessionId}:{}),before:result.nextBefore})}>Older records</button>}</>}</div>
 {result?.action==='diagnostics.records'&&<pre className="a-state-view">{JSON.stringify(result.items,null,2)}</pre>}</ActivityRegion>
 </>}
 {saving&&<ResultNotice phase="working" message={saving==='remove'?'Removing destination…':'Saving diagnostics settings…'}/>}
 {saveError&&<><ResultNotice phase="error" message={saveError}/>{retryPatch&&<button type="button" disabled={saveBusy} onClick={()=>apply(retryPatch)}>Retry saving preference</button>}</>}
 {!saving&&!saveError&&result?.action!=='diagnostics.records'&&result?.action!=='diagnostics.test'&&result?.action!=='diagnostics.environment'&&<ResultNotice phase={result?.phase||'ready'} message={result?.message}/>}
 </SettingsGroup>;
}
