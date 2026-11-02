import React,{useEffect,useRef,useState} from 'react';
import {ConfigurationScopeSelect,useConfigurationScope,useConfigurationId} from './configuration-scope';
import {matchingSetupOperation,setupPending} from './ai-connections-data';
import {useSettingsDraft} from './settings-drafts';
import {UIUseSetup} from './ui-use-setup';

export function DecisionModelSettings({state,act,navigate}){
 const data=state.setup?.decisionModels,catalog=data?.catalog||[];
 const [purpose,setPurpose]=useState('browser'),[draft,setDraft]=useState(null),[pending,setPending]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const sending=useRef(false),alive=useRef(true),id=useConfigurationId(),scope=useConfigurationScope(draft?.scope);
 useEffect(()=>{alive.current=true;act('providers.decisionModels',{});return()=>{alive.current=false}},[]);
 const binding=data?.selection?.[purpose]||{mode:'standard'},saved=data?.backends?.[binding.backend]||{};
 const value=draft||{mode:binding.mode==='decision'?'decision':'standard',backend:saved,allowExternalState:binding.allow_external_state===true};
 const definition=catalog.find(row=>row.id===value.backend?.provider),busy=!!pending;
 const edit=patch=>{setDraft({...value,...patch});setNotice('')};
 const choose=identity=>{const row=catalog.find(row=>row.id===identity);edit({backend:row?{provider:identity,model:row.default_model}:{},allowExternalState:false})};
 useSettingsDraft('decision-models',{label:'Decision model preferences',dirty:!!draft,review:()=>navigate('ai-connections'),discard:()=>setDraft(null)});
 const operation=matchingSetupOperation(state,pending);
 useEffect(()=>{
  if(!operation||setupPending(operation))return;
  sending.current=false;setPending(null);
  if(operation.phase==='ready'){setDraft(null);setNotice('Saved for new conversations. Applies to bundles with UI use enabled.');}
  else setError(operation.error||'Could not save. Your choices are kept.');
 },[operation?.phase,operation?.commandId]);
 async function save(){
  if(sending.current)return;
  sending.current=true;setError('');setNotice('');setPending({action:'providers.configureDecisions',id:'',commandId:null});
  try{
   const receipt=await act('providers.configureDecisions',{purpose,mode:value.mode,scope,...(value.mode==='decision'?{backend:value.backend,allowExternalState:value.allowExternalState}: {})});
   if(!alive.current)return;
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'Save could not be confirmed.');
   setPending({action:'providers.configureDecisions',id:'',commandId:receipt.operationId});
  }catch(e){if(alive.current){sending.current=false;setPending(null);setError(e.message)}}
 }
 return <div data-part="decision-model-settings">
  <p>Ordinary models handle browser and computer use by default. A decision model can help choose the next observed action. Tool permissions still apply.</p>
  <UIUseSetup state={state} act={act} kind="decisions"/>
  {!data?<p role="status">{state.setup?.operations?.['providers.decisionModels:']?.phase==='error'?'Could not load decision model choices.':'Loading decision model choices…'} <button className="a-link" onClick={()=>act('providers.decisionModels',{})}>Refresh choices</button></p>:<>
   <label htmlFor={id('decision-purpose')}>Use for</label><select id={id('decision-purpose')} value={purpose} disabled={busy||!!draft} onChange={e=>{setPurpose(e.target.value);setNotice('');setError('')}}><option value="browser">Browser use</option><option value="computer">Computer use</option></select>
   <label htmlFor={id('decision-mode')}>Action selection</label><select id={id('decision-mode')} value={value.mode} disabled={busy} onChange={e=>edit({mode:e.target.value,allowExternalState:false})}><option value="standard">Ordinary model (default)</option><option value="decision" disabled={!catalog.length}>Decision assisted</option></select>
   {!catalog.length&&<p>No saved connection offers decision models. Add a supported provider connection to use one.</p>}
   {value.mode==='decision'&&<>
    <label htmlFor={id('decision-service')}>Provider connection</label><select id={id('decision-service')} value={value.backend?.provider||''} disabled={busy} onChange={e=>choose(e.target.value)}><option value="">Choose a connection</option>{catalog.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</select>
    <label htmlFor={id('decision-model')}>{definition?.model_label||'Decision model'}</label>{definition?.fixed_models?<select id={id('decision-model')} value={value.backend?.model||''} disabled={busy} onChange={e=>edit({backend:{...value.backend,model:e.target.value}})}>{(definition.models||[]).map(model=><option key={model} value={model}>{definition.model_labels?.[model]||model}</option>)}</select>:<input id={id('decision-model')} value={value.backend?.model||''} disabled={busy} onChange={e=>edit({backend:{...value.backend,model:e.target.value}})}/>}
    {definition?.endpoint_optional&&<><label htmlFor={id('decision-endpoint')}>Decision service endpoint (optional)</label><input id={id('decision-endpoint')} type="url" value={value.backend?.endpoint||''} disabled={busy} onChange={e=>edit({backend:{...value.backend,endpoint:e.target.value},allowExternalState:false})}/></>}
    <p className="a-caption">Uses this connection’s saved account and credentials. Browser and computer choices can use different models.</p>
    {definition?.cost_note&&<p data-part="decision-cost-note">{definition.cost_note}</p>}
    <label><input type="checkbox" checked={value.allowExternalState===true} disabled={busy} onChange={e=>edit({allowExternalState:e.target.checked})}/>Allow observations from this surface to be sent to this decision service</label>
   </>}
   <ConfigurationScopeSelect id={id('decision-scope')} value={scope} disabled={busy} onChange={scope=>edit({scope})}/>
   <button type="button" className="a-primary" disabled={busy||!draft||(value.mode==='decision'&&(!definition||!value.backend?.model?.trim()||!value.allowExternalState))} onClick={save}>{busy?'Saving…':'Save selection'}</button>
   {draft&&!busy&&<button type="button" className="a-link" onClick={()=>setDraft(null)}>Discard changes</button>}
  </>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </div>
}
