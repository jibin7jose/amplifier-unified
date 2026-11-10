import React,{useEffect,useRef,useState} from 'react';
import {ConfigurationScopeSelect,useConfigurationScope,useConfigurationId} from './configuration-scope';
import {matchingSetupOperation,setupPending} from './ai-connections-data';
import {useSettingsDraft} from './settings-drafts';
import {UIUseSetup} from './ui-use-setup';

function preference(data,purpose){
 const binding=data?.selection?.[purpose]||{},saved=data?.backends?.[binding.backend]||{};
 return {mode:binding.mode==='decision'?'decision':'standard',backend:binding.mode==='decision'?{provider:saved.provider,model:saved.model,...(saved.endpoint?{endpoint:saved.endpoint}:{})}:{},allowExternalState:binding.allow_external_state===true};
}

export function DecisionModelSettings({state,act,navigate,onConnect,labels}){
 const data=state.setup?.decisionModels,catalog=data?.catalog||[];
 const [chosenPurpose,setPurpose]=useState(null),[draft,setDraft]=useState(null),[pending,setPending]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [requestingChoices,setRequestingChoices]=useState(false),[choiceRequestError,setChoiceRequestError]=useState('');
 const sending=useRef(false),alive=useRef(true),id=useConfigurationId(),scope=useConfigurationScope(draft?.scope);
 // Check current choices on open; saves already publish their updated catalog.
 useEffect(()=>{alive.current=true;refreshChoices();return()=>{alive.current=false}},[]);
 const choicesOperation=state.setup?.operations?.['providers.decisionModels:'];
 const loadingChoices=requestingChoices||setupPending(choicesOperation);
 const choicesError=choiceRequestError||state.setup?.decisionModelsError||(choicesOperation?.phase==='error'?(choicesOperation.error||'Please try again.'):'');
 async function refreshChoices(){
  setRequestingChoices(true);setChoiceRequestError('');
  try{
   const receipt=await act('providers.decisionModels',{});
   if(!receipt?.accepted)throw Error(receipt?.error||'Could not request decision model choices.');
  }catch(e){if(alive.current)setChoiceRequestError(e.message)}
  finally{if(alive.current)setRequestingChoices(false)}
 }
 const browser=preference(data,'browser'),computer=preference(data,'computer'),same=JSON.stringify(browser)===JSON.stringify(computer);
 const purpose=chosenPurpose||(same?'both':'browser'),saved=purpose==='computer'?computer:browser;
 const value=draft||{...saved,allowExternalState:purpose==='both'&&!same?false:saved.allowExternalState};
 const definition=catalog.find(row=>row.id===value.backend?.provider),busy=!!pending;
 const connectionLabel=row=>labels?.get(row.id)||row.label;
 const summary=value=>{const row=catalog.find(row=>row.id===value.backend?.provider);return value.mode==='standard'?'Ordinary model':`${row?connectionLabel(row):'Unavailable connection'} · ${value.backend?.model||'Model unavailable'}`};
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
  <UIUseSetup state={state} act={act} kind="decisions" onConnect={onConnect} connectionDisabled={busy||!!draft}/>
  {(!data||loadingChoices||choicesError)&&<p role={choicesError?'alert':'status'}>{choicesError?`Could not load decision model choices. ${choicesError}`:loadingChoices?'Updating decision model choices…':'Loading decision model choices…'} <button type="button" className="a-link" disabled={loadingChoices} onClick={refreshChoices}>Refresh choices</button></p>}
  {data&&<>
   {!catalog.length&&!loadingChoices&&!choicesError&&<div data-part="decision-connect"><p>Connect a provider to choose a decision model. Installing support does not connect an account.</p>{onConnect&&(data.services?.length?data.services.map(service=><button key={service.module} type="button" className="a-soft" disabled={busy||!!draft} onClick={()=>onConnect(service.module)}>Connect {service.name}</button>):<button type="button" className="a-link" disabled={busy||!!draft} onClick={()=>onConnect()}>Add an AI connection</button>)}</div>}
   <dl data-part="decision-current" aria-label="Saved choices for new chats"><dt>Browser use</dt><dd>{summary(browser)}</dd><dt>Computer use</dt><dd>{summary(computer)}</dd></dl>
   <label htmlFor={id('decision-purpose')}>Use for</label><select id={id('decision-purpose')} value={purpose} disabled={busy||!!draft} onChange={e=>{const next=e.target.value;setPurpose(next);if(next==='both'&&!same)setDraft({...value,allowExternalState:false});setNotice('');setError('')}}><option value="both">Browser and computer</option><option value="browser">Browser only</option><option value="computer">Computer only</option></select>
   <p className="a-caption">Both can use decision models. Save the same choice for both, or configure each separately.{purpose==='both'&&!same?' Saving will replace both existing selections.':''}</p>
   <label htmlFor={id('decision-mode')}>Action selection</label><select id={id('decision-mode')} value={value.mode} disabled={busy} onChange={e=>edit({mode:e.target.value,...(e.target.value==='decision'&&!value.backend?.provider&&catalog.length===1?{backend:{provider:catalog[0].id,model:catalog[0].default_model}}:{}),allowExternalState:false})}><option value="standard">Ordinary model (default)</option><option value="decision" disabled={!catalog.length}>Decision assisted{!catalog.length?(loadingChoices?' (loading choices…)':choicesError?' (choices unavailable)':' (connect a provider first)'):''}</option></select>
   {value.mode==='decision'&&<>
    <label htmlFor={id('decision-service')}>Provider connection</label><select id={id('decision-service')} value={value.backend?.provider||''} disabled={busy} onChange={e=>choose(e.target.value)}><option value="">Choose a connection</option>{catalog.map(row=><option key={row.id} value={row.id}>{connectionLabel(row)}</option>)}</select>
    <label htmlFor={id('decision-model')}>{definition?.model_label||'Decision model'}</label>{definition?.fixed_models?<select id={id('decision-model')} value={value.backend?.model||''} disabled={busy} onChange={e=>edit({backend:{...value.backend,model:e.target.value}})}>{(definition.models||[]).map(model=><option key={model} value={model}>{definition.model_labels?.[model]||model}</option>)}</select>:<input id={id('decision-model')} value={value.backend?.model||''} disabled={busy} onChange={e=>edit({backend:{...value.backend,model:e.target.value}})}/>}
    {definition?.endpoint_optional&&<><label htmlFor={id('decision-endpoint')}>Decision service endpoint (optional)</label><input id={id('decision-endpoint')} type="url" value={value.backend?.endpoint||''} disabled={busy} onChange={e=>edit({backend:{...value.backend,endpoint:e.target.value},allowExternalState:false})}/></>}
    <p className="a-caption">Uses this connection’s saved account and credentials. Browser and computer choices can use different models.</p>
    {definition?.cost_note&&<p data-part="decision-cost-note">{definition.cost_note}</p>}
    <label><input type="checkbox" checked={value.allowExternalState===true} disabled={busy} onChange={e=>edit({allowExternalState:e.target.checked})}/>Allow observations from {purpose==='both'?'browser and computer':purpose==='browser'?'the browser':'the computer'} to be sent to this decision service</label>
   </>}
   <ConfigurationScopeSelect id={id('decision-scope')} value={scope} disabled={busy} onChange={scope=>edit({scope})}/>
   <button type="button" className="a-primary" disabled={busy||!draft||(value.mode==='decision'&&(!definition||!value.backend?.model?.trim()||!value.allowExternalState))} onClick={save}>{busy?'Saving…':'Save selection'}</button>
   {draft&&!busy&&<button type="button" className="a-link" onClick={()=>setDraft(null)}>Discard changes</button>}
  </>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </div>
}
