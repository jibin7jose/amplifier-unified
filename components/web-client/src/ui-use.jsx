import React,{useEffect,useRef,useState} from 'react';
import {Monitor} from 'lucide-react';
import {settingsPatch} from './settings-navigation';
import {UIUseSetup} from './ui-use-setup';
import './ui-use.css';
const endpoint=(name,sid)=>'/api/ui-use/v1/'+name+(sid?'?'+new URLSearchParams({sessionId:sid}):'');
export function UIUsePanel({state,session,act}){
 const sid=session?.id||state.selectedSessionId;
 const [report,setReport]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[url,setUrl]=useState(''),[scope,setScope]=useState(sid?'chat':'global'),[typing,setTyping]=useState(''),[watch,setWatch]=useState(false),[frame,setFrame]=useState(0),[desktopFrame,setDesktopFrame]=useState(null);
 const version=useRef(0),active=useRef(sid);active.current=sid;
 useEffect(()=>{let disposed=false;setReport(null);setWatch(false);setError('');setBusy(false);setDesktopFrame(null);setScope(sid?'chat':'global');async function load(){try{const response=await fetch(endpoint('state',sid));if(!response.ok)throw Error(await response.text());const value=await response.json();if(!disposed)setReport(value)}catch(e){if(!disposed)setError(e.message)}}load();const id=setInterval(load,4000);return()=>{disposed=true;clearInterval(id);version.current++}},[sid]);
 useEffect(()=>{if(!watch)return;const id=setInterval(()=>setFrame(x=>x+1),1200);return()=>clearInterval(id)},[watch]);
 async function run(name,args={}){
  if(busy&&name!=='stop')return;const request=++version.current;setBusy(true);setError('');
  try{const response=await fetch(endpoint(name,sid),{method:'POST',headers:{'Content-Type':'application/json','X-Connection-CSRF':report.csrf},body:JSON.stringify(args)});if(!response.ok)throw Error(await response.text());const value=await response.json();if(request!==version.current||active.current!==sid)return;
   if(value.status&&['blocked','denied','unknown'].includes(value.status))throw Error(value.recovery||value.reason);
   const fresh=await fetch(endpoint('state',sid));if(!fresh.ok)throw Error('Could not refresh browser status.');const current=await fresh.json();if(request===version.current)setReport(current);
  }catch(e){if(request===version.current)setError(e.message)}finally{if(request===version.current)setBusy(false)}
 }
 async function canvas(){await act('canvas.show',{kind:'settings',sessionId:sid});await act('view.update',{patch:{...settingsPatch('desktop'),settingsCanvas:true,panel:null}})}
 const selected=report?.targets.find(t=>t.id===report.target),progress=report?.progress;
 return <section className="a-ui-use" aria-label="Browser and computer tasks">
  <h3>Browser &amp; computer</h3><p>Ask for a task in chat. Amplifier can work in its own browser or the tab you connect on another device.</p>
  {!report?<p role="status">{error||'Checking available browsers…'}</p>:<>
   <label>Save preference for <select aria-label="UI preference scope" value={scope} onChange={e=>setScope(e.target.value)} disabled={busy}><option value="global">All chats</option>{sid&&<><option value="workspace">This workspace</option><option value="chat">This chat</option></>}</select></label>
   <label>Browser <select aria-label="Browser target" value={report.preferences.target} disabled={busy} onChange={e=>run('preferences',{scope,target:e.target.value})}>
    {!report.targets.some(t=>t.id===report.preferences.target)&&<option value={report.preferences.target}>Previously selected browser · unavailable</option>}
    {report.targets.map(t=><option key={t.id} value={t.id} disabled={!t.ready}>{t.label}{t.ready?'':' · unavailable'}</option>)}
   </select></label><p className="a-caption">{selected?.detail||'The selected browser is unavailable. Reconnect it or choose a different browser explicitly.'}</p>
   <label>Action selection <select aria-label="UI model selection" disabled={busy} value={report.preferences.selection} onChange={e=>run('preferences',{scope,selection:e.target.value})}><option value="configured">Prefer configured decision model</option><option value="standard">Use ordinary chat model</option></select></label>
   <button className="a-link" disabled={busy} onClick={()=>run('preferences',{scope,reset:true})}>{scope==='global'?'Restore defaults':'Use inherited preferences'}</button><p className="a-caption">Chat preferences override workspace preferences, which override all-chat preferences.</p>
   <p className="a-caption">Uses your ordinary model when no decision model is configured or a decision cannot choose reliably.</p>
   <div className="a-dialog-actions"><a className="a-soft" href="/connect" target="_blank" rel="noopener">Connect a browser or device</a><button className="a-link" onClick={()=>act('view.update',{patch:settingsPatch('ai-connections')})}>AI connections &amp; decision models</button></div>
   <UIUseSetup state={state} act={act} kind="browser" report={report}/>
   {sid&&<>
    <p role="status"><strong>{report.stopped?'Agent paused':!selected?.ready?'Browser unavailable':report.opened||report.target!=='managed'?'Ready':'Ready to open a website'}</strong> · {selected?.label||report.target}</p>
    {report.url&&<p className="a-caption a-wrap">{report.url}</p>}
    <div className="a-dialog-actions"><button className="a-soft" disabled={busy&&report.stopped} onClick={()=>run(report.stopped?'resume':'stop')}>{report.stopped?'Resume agent access':'Stop agent access'}</button><button className="a-soft" onClick={canvas}>Open controls in canvas</button></div>
    {report.target==='managed'&&<><form onSubmit={e=>{e.preventDefault();run('browser',{action:'open',url,target:'managed'})}}><label>Open a website <input aria-label="Website address" value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://example.com" type="url" required/></label><button className="a-soft" disabled={busy||report.stopped}>Open</button></form>
     {report.opened&&<><button className="a-link" disabled={busy} onClick={()=>{setWatch(false);run('close')}}>Close this browser</button><button className="a-soft" onClick={()=>setWatch(v=>!v)}>{watch?'Hide live browser':'Watch & interact'}</button>{watch&&<div className="a-ui-browser"><p className="a-caption">Clicking or typing pauses the agent so you can sign in or take over. Resume when ready.</p><img alt="Live Amplifier browser. Click to interact." src={endpoint('frame',sid)+'&frame='+frame} onClick={e=>{const r=e.currentTarget.getBoundingClientRect();run('interact',{action:'click',x:Math.floor((e.clientX-r.left)*1280/r.width),y:Math.floor((e.clientY-r.top)*900/r.height)})}}/><form onSubmit={e=>{e.preventDefault();run('interact',{action:'type',text:typing});setTyping('')}}><input data-private="true" aria-label="Type in browser" type="password" autoComplete="off" value={typing} onChange={e=>setTyping(e.target.value)} placeholder="Text for the focused field"/><button className="a-soft" disabled={busy||!typing}>Type</button></form><div className="a-dialog-actions">{['Tab','Enter','Escape','Backspace'].map(key=><button className="a-soft" key={key} disabled={busy} onClick={()=>run('interact',{action:'key',key})}>{key}</button>)}{['up','down'].map(direction=><button className="a-soft" key={direction} onClick={()=>run('interact',{action:'scroll',direction})}>Scroll {direction}</button>)}</div></div>}</>}
    </>}
    {report.target!=='managed'&&selected?.fullTab&&<><button className="a-soft" onClick={()=>{setError('');setWatch(v=>!v)}}>{watch?'Hide live browser':'Watch connected browser'}</button>{watch&&<img className="a-ui-desktop" alt="Live connected browser" src={endpoint('frame',sid)+'&frame='+frame} onError={()=>{setWatch(false);setError('The live view could not refresh. Check that the connected browser is online and its tab is still selected, then choose Watch connected browser to retry. Agent access has not moved to another browser.')}}/>}</>}
    <details className="a-everyday-disclosure"><summary>Computer desktop</summary><p>The desktop configured for this chat. This may be the server, not this browser device.</p><button className="a-soft" onClick={()=>setDesktopFrame(Date.now())}>Capture desktop</button>{desktopFrame&&<img className="a-ui-desktop" alt="Configured computer desktop snapshot" src={endpoint('desktop-frame',sid)+'&frame='+desktopFrame} onError={()=>setError('Desktop capture is unavailable. Start a chat with a configured computer executor, or use screen sharing for guidance.')}/>}</details>
    {progress?.decision?.backend&&!Object.keys(progress.decision.cost||{}).length&&<p className="a-caption">The decision service did not report a price. Chat totals may not include its cost.</p>}
    {progress&&<details className="a-everyday-disclosure"><summary>Latest UI action · {progress.effective_selector||progress.selector||progress.surface}{progress.execution?' · '+progress.execution:''}</summary><pre>{JSON.stringify(progress,null,2)}</pre></details>}
   </>}
   <p className="a-caption">{report.computer.detail} A shared screen supplies context; it does not grant control. Managed-device sign-in may require your connected browser.</p>
   {error&&<p role="alert">{error}</p>}
  </>}
 </section>;
}
export function UIUseShortcut({sessionId,act}){
 if(!sessionId)return null;
 return <button className="a-icon" aria-label="Browser & computer" title="Browser & computer" onClick={()=>act('view.update',{patch:{panel:'chat-computer'}})}><Monitor/></button>;
}
