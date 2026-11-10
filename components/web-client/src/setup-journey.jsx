import React,{useEffect,useRef,useState} from 'react';
import {Sparkles,MessageSquare,Settings,LoaderCircle} from 'lucide-react';
import {settingsPatch} from './settings-navigation';
import {newChatSetup} from './new-chat';
import './setup-journey.css';
import {fitPanels} from './panel-layout';

export function useConnectionSetup(state,session,act){
 const prompted=useRef(null);
 const setup=state?.setup||{},workspace=session?.workspace||state?.settings?.workspace;
 const loaded=setup.providersLoadedAt&&(setup.providersWorkspace===workspace||!session&&setup.providersLocation?.kind==='managed');
 const any=setup.providers?.some(row=>row.enabled!==false);
 const discovering=!session||['welcome','connect'].includes(state?.view?.setupJourney);
 useEffect(()=>{if(!discovering)return;const revisit=()=>{act('providers.list',session?{sessionId:session.id}:{})};window.addEventListener('focus',revisit);return()=>window.removeEventListener('focus',revisit)},[session?.id,discovering]);
 useEffect(()=>{
  if(!loaded)return;
  if(any&&state?.view?.setupJourney!=='welcome'){prompted.current=null;return;}
  if(prompted.current===workspace)return;
  prompted.current=workspace;
  act('view.update',{patch:{...settingsPatch('ai-connections'),settingsCanvas:false,setupJourney:'connect'}});
 },[loaded,any,workspace,state?.view?.setupJourney,setup.providersLoadedAt]);
}

export const assistedSetupPrompt='Help me finish setting up Amplifier. Show me how you can help with settings, then offer a quick tour or let me start my own chat.';

export function AssistedSettingsButton({state,dispatch,disabled=false,onBeforeStart,label='Help me with settings',compact=false}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 const request=useRef(null),running=useRef(false);
 async function start(){
  if(running.current||disabled)return;
  if(onBeforeStart&&!(await onBeforeStart()))return;
  running.current=true;setBusy(true);setError('');
  const intent=request.current||(request.current={id:crypto.randomUUID(),setup:newChatSetup(state)});
  try{
   const created=await dispatch('session.create',{...intent.setup,title:'Getting started with Amplifier'}, {id:intent.id});
   const id=created.sessionId||created.state?.selectedSessionId;
   if(!created.accepted||!id)throw Error(created.error||'Could not create the setup chat.');
   await dispatch('session.select',{id});
   const sent=await dispatch('conversation.send',{sessionId:id,text:assistedSetupPrompt,via:'chat',preserveDraft:true},{id:intent.id+'-welcome'});
   if(!sent.accepted)throw Error(sent.error||'The setup message was not accepted.');
   // Seed the preferred split once for this new chat. Subsequent resizing,
   // settings navigation, and reloads use the normal persisted pane width.
   const host=document.querySelector('#amp-one .a-layout');
   const canvasWidth=fitPanels({available:host?.clientWidth||1200,gap:host?parseFloat(getComputedStyle(host).columnGap)||0:12,canvasOpen:true,canvasWidth:16384}).canvas;
   await dispatch('view.update',{patch:{...settingsPatch('ai-connections'),panel:null,setupJourney:'assisted',workSurface:'chat',navPinned:false,navExpanded:false,canvasWidth:Math.round(canvasWidth)}});
   const opened=await dispatch('canvas.show',{kind:'settings',sessionId:id});
   if(!opened.accepted)throw Error(opened.error||'Could not open Settings.');
   request.current=null;
  }catch(e){setError(e.message)}finally{running.current=false;setBusy(false)}
 }
 return <><button type="button" className={compact?"a-icon":"a-soft"} aria-label={busy?'Opening setup chat…':label} title={label} disabled={disabled||busy} onClick={start}><Sparkles/>{!compact&&(busy?'Opening setup chat…':label)}</button>{error&&<p role="alert">{error} Retry uses the same setup chat.</p>}</>;
}
export function SetupJourney({state,act,dispatch,onBeforeStart}){
 const journey=state.view?.setupJourney,providers=(state.setup?.providers||[]).filter(p=>p.enabled!==false),ready=providers.some(p=>!p.authenticationRequired&&(p.config?.default_model||p.config?.model));
 const updates=state.updates||{},preparing=updates.initialPreparation&&updates.sequence?.stage!=='complete',failed=updates.error||['error','interrupted'].includes(updates.phase);
 if(!['welcome','connect'].includes(journey))return null;
 return <section className="a-setup-journey" aria-label="Get started"><h3>{ready?'Your first connection is ready':'Welcome to Amplifier'}</h3><p>{ready?'Finish setting up with your assistant, or jump straight into your own work.':'Connect one AI service to start. You can use an existing account and add more services later.'}</p>
  {preparing&&<p role="status">{!failed&&<LoaderCircle className="a-progress-spinner"/>}{failed?'Some components need attention. Open Updates for details.':'Getting Amplifier ready. You can set up your connection while components prepare.'}</p>}
  {ready&&<div className="a-dialog-actions"><AssistedSettingsButton state={state} dispatch={dispatch} onBeforeStart={onBeforeStart} label="Set up with the assistant"/><button type="button" className="a-primary" onClick={async()=>{if(onBeforeStart&&!(await onBeforeStart()))return;await act('view.update',{patch:{panel:null,settingsCanvas:false,setupJourney:'done'}});await act('session.draft',{})}}><MessageSquare/>Start a chat</button></div>}
  <p className="a-caption">{ready?'Optional tour: files, Canvas, workspaces, plans, voice, and images.':'Keys and sign-in details stay in connection settings.'}</p>
 </section>;
}
