import React,{useRef,useState} from 'react';
import {RefreshCw,Trash2} from 'lucide-react';
import {ChatGPTAccount} from './chatgpt-sign-in';
import {ProviderKeyPreview} from './provider-key-preview';
import {ProviderMessageTest} from './provider-message-test';
import {connectionTestability} from './setup-data';

export function ConnectionPreference({id,providers,labels,busy,onChange}){
 const flight=useRef(false),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const enabled=providers.filter(row=>row.enabled!==false),chat=enabled.filter(row=>row.config?.chat_enabled!==false);
 if(chat.length<2)return null;
 return <div className="a-connection-capability"><div><label htmlFor={id}>Preferred chat connection</label><p className="a-caption">Used when model rules do not choose another connection. Existing chats keep their choices.</p>{error&&<p role="alert">{error}</p>}</div><select id={id} disabled={busy||saving} data-action="providers.reorder" value={chat[0].id} onChange={async event=>{
  const chosen=event.target.value;
  if(busy||flight.current||!chat.some(row=>row.id===chosen)||chosen===chat[0].id)return;
  flight.current=true;setSaving(true);setError('');
  try{await onChange({ids:[chosen,...enabled.filter(row=>row.id!==chosen).map(row=>row.id)],expectedIds:enabled.map(row=>row.id)})}
  catch(error){setError(error.message||'The preference was not confirmed. Review the saved order before trying again.')}
  finally{flight.current=false;setSaving(false)}
 }}>{chat.map(row=><option key={row.id} value={row.id}>{labels?.get(row.id)||row.displayName||row.id}</option>)}</select></div>;
}

export function AIConnectionDetail({selected,service,state,session,act,busy,onModel,onDecisions,onCredentials,onRefresh,onCheck,onRemove,onAdvanced}){
 const entry=state.setup?.providerCatalogs?.[selected?.id],check=connectionTestability(state.modelCatalogs?.[entry?.sharedCatalogKey]||entry);
 return <>
  {service.auth==='signin'&&<ChatGPTAccount account={selected?.account}/>}
  <p role={selected?.authenticationRequired?'alert':undefined}>{selected?.authenticationRequired?'Reconnect this account to continue.':selected?.accountConnected||selected?.credentialsConfigured?'Connected. Choose how Amplifier uses this account.':'Add credentials to use this connection.'}</p>
  <div className="a-connection-capabilities">
   {service.decisionOnly?<div className="a-connection-capability"><div><strong>Decision models</strong><p>Choose actions for browser and computer tasks.</p><small>Enable this connection separately for each surface. Your ordinary chat model still plans the work.</small></div><button type="button" className="a-soft" disabled={busy} onClick={onDecisions}>Choose decision preferences</button></div>:<>
   <div className="a-connection-capability"><div><strong>Chat</strong><p>{selected?.config?.default_model||selected?.config?.model||'Choose a model'}</p></div><button type="button" className="a-soft" disabled={busy} onClick={onModel}>Choose model</button></div>
   </>}
  </div>
  <details className="a-everyday-disclosure"><summary>Account and connection checks</summary><div>
   <ProviderKeyPreview credential={selected?.credential}/>
   <div className="a-dialog-actions">
    <button type="button" className="a-soft" disabled={busy} onClick={onCredentials}>{service.auth==='signin'?(selected?.accountConnected?'Reconnect account':'Sign in'):'Change credentials'}</button>
    {!service.decisionOnly&&<button type="button" className="a-soft" disabled={busy||selected?.enabled===false||!check.supported} onClick={onCheck}>Check connection</button>}
    <button type="button" className="a-link" disabled={busy} onClick={onRefresh}><RefreshCw/>Refresh credentials</button>
   </div>
   {service.decisionOnly?<p className="a-caption">Saving credentials does not test this service. Its first UI-use task reports the selected model, actual results and any fallback.</p>:<><p className="a-caption">{check.reason}</p>
   <ProviderMessageTest key={selected?.id} state={state} id={selected?.id} sessionId={session?.id} act={act} disabled={busy}/></>}
  </div></details>
  <div className="a-dialog-actions a-connection-secondary-actions"><button type="button" className="a-link" onClick={onAdvanced}>Full connection settings</button><button type="button" className="a-link a-danger" disabled={busy} onClick={onRemove}><Trash2/>Remove connection</button></div>
 </>;
}
