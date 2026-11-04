import {UIUsePanel} from './ui-use';
import React,{useEffect,useRef,useState} from 'react';

const time=value=>new Date(value*1000).toLocaleString();
function Environment({value}){
 return <details className="a-everyday-disclosure"><summary>Technical environment details</summary><p className="a-wrap">{value.host.label} · {value.platform}<br/>Python {value.python.version}<br/><code>{value.python.path}</code><br/>Computer-use package: {value.computerUsePackage.version||value.computerUsePackage.status}</p></details>;
}
export function DesktopReadiness({state,session,act,globalOnly=false}){
 if(globalOnly)session=null;
 const context=JSON.stringify([session?.id,state.voice?.id,state.voice?.status,state.voice?.visual?.id,
  state.computerVisual?.id,state.computerVisual?.available,state.computerVisual?.sessionId,state.updates?.installedAt]);
 const sequence=useRef(0),[report,setReport]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [installPending,setInstallPending]=useState(false),[installError,setInstallError]=useState('');
 useEffect(()=>{sequence.current++;setReport(null);setBusy(false);setError('');return ()=>{sequence.current++}},[context]);
 const current=report?.context===context?report.value:null;
 const worker=current?.worker,control=worker?.computerControl;
 const feature=current?.featureSetup;
 const requests=Object.values(state.updates?.featureResults||{}).filter(row=>row.feature==='native-desktop');
 const result=requests.sort((a,b)=>b.updatedAt-a.updatedAt)[0];
 const featureBusy=installPending||feature?.pending||requests.some(row=>['queued','staging','qualified','activating','restart_pending'].includes(row.phase));
 async function install(){
  if(installPending||!current)return;
  setInstallPending(true);setInstallError('');
  try{
   const receipt=await act('updates.featureInstall',{feature:'native-desktop',hostInstanceId:current.host.instanceId});
   if(!receipt?.accepted||!receipt.requestId)throw Error('The installation request could not be confirmed. Inspect Updates before retrying.');
  }catch(e){setInstallError(e.message)}
  finally{setInstallPending(false)}
 }
 async function check(){
  const request=++sequence.current;setBusy(true);setError('');
  try{
   const receipt=await act('desktop.readiness',session?.id?{sessionId:session.id}:{});
   if(request!==sequence.current)return;
   if(!receipt?.result||receipt.result.sessionId!==(session?.id||null))throw Error('Readiness did not return for this conversation. Check again.');
   setReport({context,value:receipt.result});
  }catch(e){if(request===sequence.current){setError(e.message);setReport(null)}}
  finally{if(request===sequence.current)setBusy(false)}
 }
 const openTools=(tool='',toolArgs='{}')=>act('view.update',{patch:{panel:'chat-models',settingsCanvas:false,runtimeDraft:{tab:'tools',tool,toolArgs}}});
 return <section aria-label="Desktop and browser readiness">
  <UIUsePanel state={state} session={session} act={act}/>
  <p>Check which device and environment supply screen observation and optional computer or browser tools.</p>
  <p>Checking does not enable desktop control, capture a screen, request OS permission, start a conversation runtime or change your model.</p>
  <button className="a-soft" data-action="desktop.readiness" disabled={busy} onClick={check}>{busy?'Checking…':'Check desktop and browser setup'}</button>
  {error&&<p role="alert">{error}</p>}
  {result&&<p role="status">Native feature request: {result.phase.replaceAll('_',' ')}. {result.detail}</p>}
  {installError&&<p role="alert">{installError}</p>}
  {current&&<>
   <p className="a-caption">Checked {time(current.observedAt)}. This is a setup snapshot; check again after changing the host, tools or permissions.</p>
   <section aria-label="Native screen observation"><h4>Native screen observation</h4>
    <p>The serving app host can differ from your browser device and the conversation worker.</p>
    <Environment value={current.host}/>
    <p role="status">Native observation: {current.nativeObservation.available?'ready for explicit consent':current.nativeObservation.code||current.nativeObservation.status}</p>
    {current.nativeObservation.reason&&<p className="a-wrap">{current.nativeObservation.reason}</p>}
    <p>{current.nativeObservation.nextStep}</p>
    {feature?.supported?<>
     {feature.installedExtras.includes('native-desktop')?<p>The native observation package is installed. OS permission and consent for this conversation and browser are separate.</p>:<>
      <p>{feature.detail}</p>
      <button className="a-soft" data-action="updates.featureInstall" disabled={featureBusy||Boolean(installError)} onClick={install}>Install native screen observation</button>
     </>}
    </>:feature?.reason&&<p>{feature.reason}</p>}
    <p className="a-caption">The Python path identifies the executing interpreter. It does not identify which parent application macOS lists in Screen Recording settings.</p>
   </section>
   {!globalOnly&&<><section aria-label="Browser screen observation"><h4>Shared screen source</h4>
    {current.voiceObservation.source?<p>Selected {current.voiceObservation.source.kind}: {current.voiceObservation.source.label}. Permission expires {time(current.voiceObservation.expiresAt)}.</p>:<p>No source is shared for this conversation in this browser.</p>}
    <p>{current.voiceObservation.nextStep}</p><p className="a-caption">{current.voiceObservation.browserContext}</p>
   </section>
   <section aria-label="Conversation computer tools"><h4>Conversation computer and browser tools</h4>
    {worker.status!=='available'?<p>{worker.reason}</p>:<>
     <Environment value={worker}/>
     <p>Computer control: {control.status.replaceAll('_',' ')}. Mounted tools are separate from native screen observation.</p>
     {control.detail&&<p className="a-wrap">{control.detail}</p>}
     <p className="a-wrap">Mounted tools: {worker.mountedTools.join(', ')||'none'}</p>
     <details><summary>Configured tool modules</summary><ul>{worker.modules.map(row=><li key={row.id}>{row.module} ({row.enabled?'enabled':'disabled'})</li>)}</ul></details>
     <p className="a-caption">{worker.browserContext.reason}</p>
     {control.status==='unavailable'&&<><p>The computer-use module could not mount a working backend. Its existing setup tool can discover candidate targets, explicitly activate one, and separately persist that choice.</p><button className="a-soft" data-action="view.update" onClick={()=>openTools('computer_use_unavailable','{"action":"discover"}')}>Open computer target setup</button></>}
     {control.status==='not_mounted'&&<p>Review a computer-use bundle or connect a browser adapter in Smart Tools. A skill or installed library alone does not mount these tools in this conversation.</p>}
     {control.doctorSupported&&<><button className="a-soft" data-action="view.update" onClick={()=>openTools('desktop','{"action":"doctor"}')}>Open desktop doctor</button><p className="a-caption">In Session tools, refresh the catalog and run the prefilled doctor action. It is a prompt-free, no-content diagnostic through normal session policy; policy approval may still be required. It does not clear a halt or prove physical capture/control. Remote facts retain their connection-time ages; guard state is the last recorded state.</p></>}
     {control.status==='mounted'&&!control.doctorSupported&&<p>The mounted desktop tool does not advertise doctor. Review its own schema in Session tools.</p>}
    </>}

   </section>
   </>}
   <section aria-label="Desktop setup actions"><h4>Set up optional tools</h4>
    <p>Use bundle review to add or enable tools, and Smart Tools to inspect an adapter's published schemas, connection and reported account identity. Confirm target and account through that adapter before acting.</p>
    <div className="a-dialog-actions">{current.nextActions.filter(row=>!globalOnly||!['runtime','loaded-modules','share-bundle'].some(page=>row.args?.patch?.settingsExpanded?.includes(page))).map(row=><button className="a-soft" key={row.label} data-action={row.action} onClick={()=>act(row.action,row.args)}>{row.label}</button>)}</div>
    <p><a href="https://github.com/microsoft/amplifier-bundle-computer-use" target="_blank" rel="noreferrer">Computer-use installation and target setup</a></p>
   </section>
  </>}
 </section>;
}
