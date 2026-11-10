import React,{useEffect,useState} from 'react';
import {RefreshCw,X} from 'lucide-react';
import {connectionNotice,RECONNECT_GRACE_MS} from './connection-notice.js';
import './app-reload.css';
export function ConnectionNotice({connected,updates,failure,onRetry,onDismiss}){
 const interrupted=!connected||!!failure&&!failure.unconfirmed;
 const [delayed,setDelayed]=useState(false);
 useEffect(()=>{setDelayed(false);if(!interrupted)return;const timer=setTimeout(()=>setDelayed(true),RECONNECT_GRACE_MS);return()=>clearTimeout(timer)},[interrupted]);
 const notice=connectionNotice({connected,updates,failure,delayed});
 if(!notice)return null;
 return <aside className={notice.error?"a-reload-notice a-connection-error":"a-reload-notice"} data-part="connection-notice" role={notice.error?"alert":"status"} aria-live="polite">
  <RefreshCw aria-hidden="true"/><div><strong>{notice.title}</strong><p>{notice.detail}</p></div>
  {notice.retry&&<button type="button" className="a-soft" onClick={onRetry}>Retry connection</button>}
  {notice.dismiss&&<button type="button" className="a-icon" aria-label="Dismiss connection notice" onClick={onDismiss}><X/></button>}
 </aside>;
}
