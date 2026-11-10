import React,{useEffect,useRef,useState,useId,useContext} from 'react';
import {Copy,Check,ExternalLink} from 'lucide-react';
import {SettingsLayoutContext} from './settings-layout';
import {safeLoginUrl} from './setup-data';
import {ChatGPTAccount} from './chatgpt-sign-in';

export function DeviceSignIn({login,onCancel,onRetry,busy=false,chooseModel=false,onEnablePlan,onWelcomeDismiss,welcomeDismissed=false}){
 const {active}=useContext(SettingsLayoutContext),codeId=useId();
 const field=useRef(null),attempted=useRef(''),[copied,setCopied]=useState(false),[manual,setManual]=useState(false);
 const plan=login.authMode==='chatgpt_plan';
 const code=login.deviceCode||'',url=safeLoginUrl(login.url),waiting=['starting','waiting'].includes(login.status);
 async function copy(){
  try{if(!navigator.clipboard?.writeText)throw Error();await navigator.clipboard.writeText(code);setCopied(true);setManual(false)}
  catch{setCopied(false);setManual(true)}
 }
 useEffect(()=>{if(active&&code&&attempted.current!==code){attempted.current=code;copy()}},[code,active]);
 if(login.status==='completed')return <><p className="a-signin-success" role="status"><Check/>{plan&&!login.account?.planEnabled?'Signed in. Plan access needs permission.':chooseModel?'Signed in. Choose your model below.':'Signed in successfully.'}</p>{plan&&<ChatGPTAccount account={login.account}/>} {plan&&login.account?.connected&&!login.account?.planEnabled&&onEnablePlan&&<button className="a-primary" disabled={busy} onClick={onEnablePlan}>Enable ChatGPT plan access</button>}{login.showPlanWelcome&&!welcomeDismissed&&<div className="a-selection-card" role="status"><strong>You’re using your ChatGPT plan</strong><p>Usage from this connection counts toward your ChatGPT plan.</p>{onWelcomeDismiss&&<button className="a-soft" onClick={onWelcomeDismiss}>Got it</button>}</div>}</>;
 return <div className="a-device-signin">
  {waiting?<>
   {plan?<><p role="status">{url?'Finish signing in on this host’s browser.':'Preparing secure sign-in…'}</p>{url&&<a className="a-primary" href={url} target="_blank" rel="noopener noreferrer">Continue with ChatGPT<ExternalLink/></a>}<p className="a-caption">Waiting for sign-in. This page will continue automatically.</p></>:code?<><label htmlFor={codeId}>1. Copy your sign-in code</label><div className="a-device-code"><input id={codeId} ref={field} readOnly value={code} spellCheck={false} onFocus={e=>e.target.select()} onClick={e=>e.currentTarget.select()}/><button className="a-soft" type="button" aria-label="Copy sign-in code" onClick={copy}>{copied?<Check/>:<Copy/>}{copied?'Copied':'Copy'}</button></div><p className="a-caption" role="status">{copied?'Copied to your clipboard.':manual?'Select the code to copy it, or use the Copy button.':'Keep this code ready for the next step.'}</p></>:<p role="status">Getting your sign-in code…</p>}
   {code&&url&&<><p className="a-device-step">2. Open ChatGPT and paste the code</p><a className="a-primary" href={url} target="_blank" rel="noopener noreferrer">Open ChatGPT<ExternalLink/></a><p className="a-caption" role="status">Waiting for sign-in. This page will continue automatically.</p></>}
   <button className="a-link" type="button" disabled={busy} data-action="providers.loginCancel" onClick={onCancel}>Cancel sign-in</button>
  </>:<><p role={login.error?'alert':'status'}>{login.error||'Sign-in was cancelled. You can try again.'}</p><button className="a-primary" disabled={busy} data-action="providers.login" onClick={onRetry}>Try sign-in again</button></>}
 </div>;
}
