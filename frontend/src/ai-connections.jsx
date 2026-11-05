import {DecisionModelSettings} from './decision-model-settings';
import {ConfigurationScopeSelect,useConfigurationScope,useConfigurationId} from './configuration-scope';
import {AttentionBadge} from './attention';
import {ProviderKeyPreview} from './provider-key-preview';
import {DeviceSignIn} from './device-sign-in';
import {ChatGPTSignInChoice,chatGPTMode} from './chatgpt-sign-in';
import React,{useEffect,useRef,useState,useContext} from 'react';
import {Plus,ArrowLeft,RefreshCw,Sparkles,ExternalLink} from 'lucide-react';
import {SettingsLink} from './settings-everyday';
import {SettingsActions,SettingsLayoutContext} from './settings-layout';
import {useSettingsDraft,useGuardedSettingsDraft} from './settings-drafts';
import {ResultNotice} from './settings-ui';
import {ModelSelect} from './model-select';
import {modelOptions} from './setup-data';
import {ConfigurationDiagnostics} from './registry';
import {AIConnectionDetail} from './ai-connection-detail';
import {providerOptions} from './provider-options';
import {aiServices,aiService,matchingSetupOperation,setupPending} from './ai-connections-data';

export function AIConnections({state,session,act,navigate,onReturnToChat}){
 const [decisionSettingsOpened,setDecisionSettingsOpened]=useState(false);
 const layout=useContext(SettingsLayoutContext);
 const setup=state.setup||{},providers=(setup.providers||[]).filter(p=>p.enabled!==false);
 const services=[...aiServices,...(setup.decisionModels?.services||[]).filter(s=>!aiServices.some(existing=>existing.module===s.module))];
 const serviceFor=(module,config)=>{
  const choice=services.find(s=>s.module===module)||aiService(module);
  // A conditional decision-only setup choice must not relabel an existing
  // chat account from the same provider or change its save/test workflow.
  return choice.decisionOnlyWhen&&config?{...choice,decisionOnly:config.chat_enabled===false}:choice;
 };
 const labels=new Map(providerOptions(providers.map(p=>({...p,info:{id:p.module,display_name:serviceFor(p.module).name}}))).map(row=>[row.id,row.label]));
 const editorKey='aiConnectionEditor';
 const configId=useConfigurationId();
 const requested=state.view?.[editorKey]||{};
 const identity=value=>configId('connection')+':'+(value.id||'')+':'+(value.module||'');
 const d=useGuardedSettingsDraft(requested,identity(requested),draft=>act('view.update',{patch:{[editorKey]:draft}}));
 const step=d.step==='images'?'list':d.step||'list',selected=providers.find(p=>p.id===d.id),service=serviceFor(d.module,selected?.config);
 const scope=useConfigurationScope(d.scope);
 const authMode=chatGPTMode(d.authMode||selected?.config?.auth_mode);
 const loginArgs={id:d.id,authMode,scope:scope};
 const draftIdentity=identity(d);
 const [privateKey,setPrivateKey]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[pending,setPending]=useState(null),[completed,setCompleted]=useState(false),[transitionPending,setTransitionPending]=useState(false);
 // An agent may select a different connection while this component stays
 // mounted. A private key belongs only to the exact editor that received it.
 const key=privateKey?.identity===draftIdentity?privateKey.value:'';
 const setKey=value=>setPrivateKey({identity:draftIdentity,value});
 const editor=useRef(requested),transition=useRef(0);editor.current=requested;
 const sending=useRef(false),context=useRef(session?.workspace),requestVersion=useRef(0),alive=useRef(true);
 const edit=patch=>act('view.update',{patch:{[editorKey]:{...d,...patch}}});
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;requestVersion.current++}},[]);
 useEffect(()=>{requestVersion.current++;setPrivateKey(null);setCompleted(false);setPending(null);setError('');setNotice('');sending.current=false;},[draftIdentity]);
 useEffect(()=>{if(context.current!==session?.workspace){requestVersion.current++;context.current=session?.workspace;setCompleted(false);setKey('');setPending(null);sending.current=false;edit({step:'list',id:'',model:''});}act('providers.list',session?{sessionId:session.id}:{});act('providers.decisionModels',session?{sessionId:session.id}:{});act('routing.list');},[session?.workspace]);
 async function run(action,args={},next=''){
  if(sending.current)return;
  sending.current=true;const version=++requestVersion.current;setError('');setNotice('');setPending({action,id:args.id||args.module||'',commandId:null,next});
  try{
   const receipt=await act(action,{...(session&&!['providers.loginCancel','providers.loginStatus'].includes(action)?{sessionId:session.id}:{}),...args});
   if(!alive.current||version!==requestVersion.current)return;
   if(!receipt?.accepted||!receipt.operationId)throw Error(receipt?.error||'The request could not be confirmed. Please try again.');
   setPending({action,id:args.id||args.module||'',commandId:receipt.operationId,next});
  }catch(e){if(alive.current&&version===requestVersion.current){setError(e.message);setPending(null);sending.current=false;}}
 }
 const credentialOperation=setup.operations?.['providers.credentials:'+d.module];
 const operation=matchingSetupOperation(state,pending),busy=!!pending||transitionPending||setupPending(credentialOperation);
 const listOperation=setup.operations?.['providers.list:'];
 const providersLoaded=!!setup.providersLoadedAt;
 const loadError=listOperation?.phase==='error'?listOperation.error:' ';
 useEffect(()=>{
  if(!operation||setupPending(operation))return;
  const next=pending.next,action=pending.action;setPending(null);sending.current=false;
  if(operation.phase==='error'){setError(operation.error||'The request did not finish. Your choices are kept.');return;}
  if(operation.phase!=='ready')return;
  if(action==='providers.save'){
   setKey('');edit({saved:true,...(next==='models'?{step:'model'}:{})});
   if(next==='signin')run('providers.login',loginArgs);
   else if(next==='models')run('providers.models',{id:d.id});
   else if(next==='decisions'){edit({step:'decisions',saved:true});setNotice('Connection saved.');}
  }else if(action==='providers.remove'){
   setCompleted(false);setKey('');edit({step:'list',id:'',saved:false});setNotice('Connection removed. Chats that used it will need a replacement connection when resumed.');
  }else if(action==='providers.finishSetup'){
   setCompleted(true);edit({step:'list',saved:false});setNotice(setup.setupCompletion?.routingSelected?'Connection saved. Balanced model rules are ready for new conversations.':'Model saved. Existing model rules are unchanged.');
  }else if(action==='providers.models'){setNotice('Model list checked. No chat request was sent.');}
 },[operation?.commandId,operation?.phase]);
 const login=setup.login?.providerId===d.id?setup.login:null;
 useEffect(()=>{
  if(layout.active&&step==='connect'&&d.saved&&login?.status==='completed'&&(login.authMode!=='chatgpt_plan'||login.account?.planEnabled)&&login.loginId!==d.previousLoginId&&!busy){
   edit({step:'model'});run('providers.models',{id:d.id});
  }
 },[step,d.saved,login?.status,login?.loginId,busy,layout.active]);
 const catalog=setup.providerCatalogs?.[d.id],models=modelOptions(catalog?.models||setup.modelCatalogs?.[d.id]||[]);
 const modelBusy=setupPending(setup.operations?.['providers.models:'+d.id])||catalog?.phase==='working';
 const credential=!setupPending(credentialOperation)&&setup.credentialCheck?.module===d.module?setup.credentialCheck:null;
 const credentialMode=d.credentialMode==='auto'?(credential?.githubCliAvailable?'github-cli':credential?.available?'environment':'private'):(d.credentialMode||'private');
 const scanCredentials=module=>{if(serviceFor(module).auth!=='signin')run('providers.credentials',{module});};
 async function changeConnection(action,args={}){
  if(sending.current||transitionPending)return;
  const version=++transition.current,previousId=d.id;setTransitionPending(true);sending.current=true;setError('');setNotice('');setCompleted(false);setKey('');
  try{
   const receipt=await act(action,{scope,...args});
   if(!alive.current||version!==transition.current)return;
   if(!receipt?.accepted)throw Error(receipt?.error||'The connection editor could not be opened. Please try again.');
   const selected=receipt.result?.editor,check=receipt.result?.credentialCheck;
   if(editor.current.id!==previousId&&editor.current.id!==selected?.id)return;
   if(check?.operationId&&check.action==='providers.credentials')setPending({action:check.action,id:args.module,commandId:check.operationId,next:''});
  }catch(e){if(alive.current&&version===transition.current)setError(e.message)}
  finally{if(alive.current&&version===transition.current){setTransitionPending(false);sending.current=false}}
 }
 const choose=s=>changeConnection('settings.connection.select',{module:s.module});
 const connectDecision=module=>module?choose({module}):changeConnection('settings.connection.start');
 const needsEndpoint=service.auth==='optional-key'||service.endpoint===true;
 const endpointKey=service.endpoint_config_key||'base_url',savedEndpoint=selected?.config?.[endpointKey]||'';
 const saveConnection=()=>run('providers.save',{id:d.id,module:d.module,config:{...selected?.config,...(service.decisionOnly?{chat_enabled:false}:{}),...(needsEndpoint?{[endpointKey]:d.baseUrl?.trim()}: {})},scope:scope,...(service.auth==='signin'?{}:credentialMode==='environment'?{apiKeyEnv:credential?.envVar}:credentialMode==='github-cli'?{useGitHubCli:true}:{apiKey:key})},service.decisionOnly?'decisions':service.auth==='signin'?'signin':'models');
 const back=()=>{setError('');edit({step:['services','detail','decisions'].includes(step)?'list':step==='model'?'detail':'services'});};
 const advanced=()=>navigate('providers');
 const unsaved=!!key||(step==='connect'&&!!d.baseUrl&&d.baseUrl!==savedEndpoint)||(step==='model'&&!!d.model&&d.model!==(selected?.config?.default_model||selected?.config?.model||''));
 useSettingsDraft('ai-connections',{label:'AI connection setup',dirty:unsaved,
  review:()=>{navigate('ai-connections');edit({step})},
  discard:()=>{setKey('');setError('');if(identity(editor.current)===draftIdentity)edit({step:'list',id:'',module:'',model:'',baseUrl:'',saved:false})}});
 return <section data-part="ai-connections" className="a-ai-form">
  {step==='list'?<>
   <p className="a-everyday-intro">Connect the AI services Amplifier can use for your work.</p>
   {completed&&onReturnToChat&&<div className="a-ai-welcome"><h4>Your connection is saved</h4><p>Return to chat to write a message or attach a file. Your unfinished message is kept.</p><button type="button" className="a-primary" disabled={busy} onClick={onReturnToChat}>Return to chat</button></div>}
   {!providersLoaded&&!providers.length?<div role="status">{listOperation?.phase==='error'?<><p>Could not load AI connections. {loadError}</p><button type="button" className="a-soft" onClick={()=>act('providers.list',{})}>Retry loading connections</button></>:'Loading AI connections…'}</div>:!providers.length?<div className="a-ai-welcome"><Sparkles aria-hidden="true"/><h4>Start with one connection</h4><p>Choose a service, connect your account, and choose a model. You can add more later.</p><button className="a-primary" data-action="settings.connection.start" disabled={busy} onClick={()=>changeConnection('settings.connection.start')}><Plus/>Connect AI</button></div>:<><div className="a-everyday-list">{providers.map(p=><SettingsLink key={p.id} disabled={busy} title={<>{labels.get(p.id)}{p.authenticationRequired&&<AttentionBadge count={1}/>}</>} description={(serviceFor(p.module,p.config||{}).decisionOnly?'Decision models':p.config?.default_model||p.config?.model||'Choose a model')+' · '+(p.authenticationRequired?'Sign-in needed':p.accountConnected?'Account connected':p.credentialsConfigured?'Credentials available':p.keySource==='provider-managed'?'Account sign-in':'Connection needs setup')+(p.credential?.preview?.masked?' · Key '+p.credential.preview.masked:'')} onClick={()=>{setError('');setNotice('');edit({step:'detail',id:p.id,module:p.module,authMode:chatGPTMode(p.config?.auth_mode),model:p.config?.default_model||p.config?.model||'',scope,baseUrl:p.config?.[serviceFor(p.module,p.config||{}).endpoint_config_key||'base_url']||'',previousLoginId:setup.login?.loginId,saved:true,initializeRouting:false})}}/>)}</div><button className="a-soft" data-action="settings.connection.start" disabled={busy} onClick={()=>changeConnection('settings.connection.start')}><Plus/>Connect another service</button></>}
   {setup.active&&providers.length>0&&<div className="a-ai-hint">Your model rules are saved. <button className="a-link" data-action="view.update" onClick={()=>navigate('routing')}>Advanced model rules</button></div>}
   <details className="a-everyday-disclosure" onToggle={event=>{if(event.currentTarget.open)setDecisionSettingsOpened(true)}}><summary>Decision models (optional)</summary>{decisionSettingsOpened&&<DecisionModelSettings state={state} act={act} navigate={navigate} onConnect={connectDecision} labels={labels}/>}</details>
   <ConfigurationDiagnostics state={state} act={act} scope={scope} navigate={navigate}/>
   <div className="a-everyday-footer"><p className="a-caption">Connections are stored on the Amplifier host. Existing conversations keep their settings.</p><button className="a-link" data-action="view.update" onClick={advanced}>Advanced connection settings<ExternalLink/></button></div>
  </>:<>
   <button className="a-link" data-action="view.update" disabled={busy} onClick={back}><ArrowLeft/>Back</button>
   {step==='services'?<><h4>Which service would you like to use?</h4><div className="a-everyday-list">{services.map(s=><SettingsLink key={s.module} disabled={busy} title={s.name} description={s.description} onClick={()=>choose(s)}/>)}</div><button className="a-link" data-action="view.update" onClick={advanced}>Other provider or custom endpoint</button></>:<>
    <p className="a-ai-step">{step==='connect'?'1 · Connect':step==='model'?'2 · Choose a model':'Saved connection'}</p><h4>{labels.get(d.id)||service.name}</h4>
    {step==='detail'?<AIConnectionDetail selected={selected} service={service} state={state} session={session} act={act} busy={busy}
     onModel={()=>{edit({step:'model',settingDefault:true});run('providers.models',{id:d.id})}}
     onDecisions={()=>edit({step:'decisions'})}
     onCredentials={()=>{edit({step:'connect',previousLoginId:login?.loginId,credentialMode:'private'});scanCredentials(d.module)}}
     onRefresh={()=>run('providers.list',{refresh:true})} onCheck={()=>run('providers.test',{id:d.id})}
     onRemove={()=>run('providers.remove',{id:d.id,scope:scope})} onAdvanced={advanced}/>
    :step==='decisions'?<>{notice&&<ResultNotice phase="ready" message={notice}/>}<DecisionModelSettings state={state} act={act} navigate={navigate} onConnect={connectDecision} labels={labels}/></>:step==='connect'?<>
     {service.auth==='signin'?<>
      {login&&login.loginId!==d.previousLoginId?<DeviceSignIn login={login} busy={busy} onCancel={()=>run('providers.loginCancel',{id:d.id})} onRetry={()=>run('providers.login',loginArgs)} onEnablePlan={()=>run('providers.login',{...loginArgs,enablePlan:true})}/>:<><ChatGPTSignInChoice mode={authMode} onChange={authMode=>edit({authMode})} disabled={busy}/><SettingsActions><button className="a-primary" data-action={selected?'providers.login':'providers.save'} disabled={busy} onClick={()=>selected?run('providers.login',loginArgs):saveConnection()}>{busy?'Getting ready…':authMode==='chatgpt_plan'?'Continue with ChatGPT':'Get sign-in code'}</button></SettingsActions></>}
     </>:<>
      {needsEndpoint&&<><label htmlFor={configId('ai-base-url')}>API base URL</label><input id={configId('ai-base-url')} type="url" placeholder={service.endpoint_placeholder||'http://localhost:1234/v1'} value={d.baseUrl||''} disabled={busy} onChange={e=>edit({baseUrl:e.target.value})}/><p className="a-caption">{service.endpoint_hint||(service.decisionOnly?'The decision service must already be running at an address reachable from the Amplifier host. Use its full decision endpoint.':'Use an address reachable from the computer running Amplifier. Include /v1 if your service requires it.')}</p></>}
      {(credential?.available||credential?.githubCliAvailable)&&<fieldset className="a-credential-options"><legend>How would you like to connect?</legend>
       {credential.available&&<label><input type="radio" name="ai-credential" checked={credentialMode==='environment'} onChange={()=>{setKey('');edit({credentialMode:'environment'})}}/><span><strong>Use the key already on this host</strong><small>Found {credential.envVar}. Only its name is saved; the key stays in the environment.</small></span></label>}
       {credential.githubCliAvailable&&<label><input type="radio" name="ai-credential" checked={credentialMode==='github-cli'} onChange={()=>{setKey('');edit({credentialMode:'github-cli'})}}/><span><strong>Use GitHub CLI sign-in</strong><small>Use the host’s signed-in GitHub account. Its token is saved privately for this connection.</small></span></label>}
       <label><input type="radio" name="ai-credential" checked={credentialMode==='private'} onChange={()=>edit({credentialMode:'private'})}/><span><strong>Use a different key</strong><small>Connect another account or enter a key yourself.</small></span></label>
      </fieldset>}
      {credentialMode==='environment'&&<ProviderKeyPreview credential={credential}/>}
      {credentialMode==='private'&&<><label htmlFor={configId('ai-private-key')}>{service.auth==='token'?'GitHub token':service.auth==='optional-key'?'API key (optional)':'API key'}</label><input id={configId('ai-private-key')} type="password" autoComplete="off" spellCheck={false} value={key} onChange={e=>setKey(e.target.value)} disabled={busy}/><p className="a-caption">{service.credential_hint||(service.auth==='optional-key'?'Leave blank if your local service does not need a key.':'Stored privately for this connection. Other accounts keep their own keys.')}</p></>}
      {service.auth==='token'&&!credential?.githubCliAvailable&&!credential?.available&&<details className="a-everyday-disclosure"><summary>Help connecting GitHub Copilot</summary><div><p>Sign in to GitHub CLI on the computer running Amplifier, then check again. Or use a GitHub token for an account with Copilot access.</p><a href="https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli" target="_blank" rel="noreferrer">GitHub setup guide</a><button className="a-link" disabled={busy} onClick={()=>scanCredentials(d.module)}>Check host sign-in again</button></div></details>}
      <SettingsActions><button className="a-primary" data-action="providers.save" disabled={busy||(needsEndpoint&&!/^https?:\/\/[^\s]+$/.test(d.baseUrl||''))||(credentialMode==='environment'?!credential?.available:credentialMode==='github-cli'?!credential?.githubCliAvailable:service.auth!=='optional-key'&&!key.trim())} onClick={saveConnection}>{busy?'Connecting…':service.decisionOnly?'Save connection':'Save and check connection'}</button></SettingsActions>
     </>}
    </>:step==='model'?<>
     {login?.status==='completed'&&<DeviceSignIn login={login} chooseModel welcomeDismissed={d.welcomedLoginId===login.loginId} onWelcomeDismiss={()=>edit({welcomedLoginId:login.loginId})}/>}<p>Choose from the models available through this connection.</p>
     {models.length>0?<><label htmlFor={configId('ai-model')}>Model</label><ModelSelect id={configId('ai-model')} label="Model" value={d.model||''} catalogKey={d.id} entry={{...catalog,models,phase:'ready'}} state={state} act={act} disabled={busy} onChange={model=>{edit({model});if(d.settingDefault&&model.trim())run('providers.finishSetup',{id:d.id,model,scope,initializeRouting:false})}}/></>:<p role="status">{modelBusy?'Loading models…':'No model list is available yet. Retry or enter a model ID under More options.'}</p>}
     {catalog?.phase==='error'&&<ResultNotice phase="error" message={catalog.error||'Could not refresh the model list. Saved choices are kept.'}/>}
     <button className="a-link" data-action="providers.models" disabled={busy||modelBusy} onClick={()=>run('providers.models',{id:d.id,refresh:true})}><RefreshCw/>Refresh models</button>
     <div className="a-ai-hint">{d.initializeRouting?'Balanced model rules choose suitable models for each task. This connection’s default is used when no rule applies.':'This changes the connection’s default model. Existing model rules may choose a different model.'}</div>
     {(!d.settingDefault||!!error||!models.some(row=>(row.value||row.id)===d.model))&&<SettingsActions><button className="a-primary" disabled={busy||!d.model?.trim()} data-action="providers.finishSetup" onClick={()=>run('providers.finishSetup',{id:d.id,model:d.model,scope:scope,initializeRouting:!!d.initializeRouting})}>{busy?'Saving…':d.settingDefault?(error?'Retry model change':'Save model'):'Finish setup'}</button></SettingsActions>}
    </>:null}
    <details className="a-everyday-disclosure"><summary>More options</summary><div><ConfigurationScopeSelect id={configId('ai-scope')} value={scope} disabled={busy} onChange={scope=>edit({scope})}/>{step==='model'&&<><label htmlFor={configId('ai-model-manual')}>Model ID</label><input id={configId('ai-model-manual')} value={d.model||''} disabled={busy} data-action="view.update" onChange={e=>edit({model:e.target.value})}/></>}<button className="a-link" data-action="view.update" onClick={advanced}>Full provider configuration</button></div></details>
   </>}
  </>}
  {(error||credentialOperation?.phase==='error')&&<ResultNotice phase="error" message={error||credentialOperation.error||'Could not check credentials on this host.'}/>}
  {notice&&step!=='decisions'&&<ResultNotice phase="ready" message={notice}/>}
  {busy&&<ResultNotice phase="working" message="Working on this connection…"/>}
  {!busy&&setup.test?.providerId===d.id&&step==='detail'&&<ResultNotice phase={setup.test.reachable===false?'error':'ready'} message={setup.test.reachable===false?'Connection check failed.':'Model catalog check passed.'}/>}
 </section>;
}
