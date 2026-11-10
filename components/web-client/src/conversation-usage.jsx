import React,{useEffect,useRef,useState} from 'react';
import {UsageDetails} from './usage-details.jsx';
import {useShellContext} from './shell/runtime';
import {metricExport,metricsVisible,selectedMetrics,metricState} from './execution-observability.js';
import {usageMetric} from './conversation-usage.js';

export function ConversationUsage(props){
 const presentation=useShellContext()?.composition?.presentation||{};
 if(!metricsVisible(presentation,'conversation'))return null;
 return <UsageSnapshot key={props.sessionId} {...props} presentation={presentation}/>;
}
function UsageSnapshot({sessionId,act,presentation,compact=false,status}){
 const [snapshot,setSnapshot]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const generation=useRef(0),pending=useRef(false);
 async function refresh(){
  if(pending.current)return;pending.current=true;
  const ticket=++generation.current;setBusy(true);setError('');
  try{
   const response=await act('capacity.read',{sessionId,limit:1});
   if(response?.accepted===false||response?.result?.usage?.sessionId!==sessionId)throw Error('Usage could not be read.');
   if(ticket===generation.current)setSnapshot(response.result);
  }catch(error){if(ticket===generation.current)setError(error.message||'Usage could not be read.')}
  finally{pending.current=false;if(ticket===generation.current)setBusy(false)}
 }
 useEffect(()=>{if(!compact)refresh();return()=>{generation.current++}},[sessionId]);
 const current=snapshot?.usage?.sessionId===sessionId?snapshot:null,usage=current?.usage,metrics=usage?.metrics||{};
 const record={sessionId,calls:usage?.calls,status},selected=selectedMetrics(presentation);
 const details=current&&<>
  <p>{selected.includes('calls')&&<>{usage.calls} recorded {metrics.voiceSeconds?'':'model '}{usage.calls===1?'call':'calls'} · </>}Snapshot {new Date(current.observedAt*1000).toLocaleTimeString()}</p>
  <UsageDetails metrics={metrics} scope="conversation" record={record} observedAt={current.observedAt} coverage={usage.coverage} label="Conversation totals" costLabel="Session cost (USD)"/>
  <details><summary>Model breakdown</summary>{usage.providers.map(row=><section key={row.provider+'|'+row.model}><h4>{row.provider} / {row.model}{selected.includes('calls')&&` · ${row.calls} calls`}</h4><UsageDetails metrics={row.metrics} scope="conversation" label={`${row.provider} / ${row.model} usage`}/></section>)}</details>
  <p className="a-caption">Historical calls without telemetry and provider-internal retries may be missing. Unavailable values are not zero.{usage.excludedUnboundCalls>0?` ${usage.excludedUnboundCalls} records could not be bound to this chat and are excluded.`:''}</p>
  <details><summary>Usage data</summary><p className="a-caption">Only selected metrics are included.</p><pre className="a-state-view">{JSON.stringify(metricExport({metrics,presentation,scope:'conversation',record,observedAt:current.observedAt,coverage:usage.coverage}),null,2)}</pre></details>
 </>;
 const refreshButton=<button type="button" className={compact?'a-link':'a-soft'} data-action="capacity.read" disabled={busy} onClick={refresh}>{busy?'Reading usage…':current?'Refresh usage':'Read usage'}</button>;
 if(compact)return <section className="a-conversation-status" aria-label="Conversation status"><span>{status||'Status unavailable'}</span>{current&&<span>Usage snapshot · {new Date(current.observedAt*1000).toLocaleTimeString()}</span>}{current&&selected.filter(key=>['grossTotalTokens','costUsd'].includes(key)||key==='voiceSeconds'&&metrics.voiceSeconds).map(key=><span key={key}>{key==='voiceSeconds'&&metrics[key]?.value!=null?Number(metrics[key].value.toFixed(1)):usageMetric(metrics[key],key==='costUsd',true)||metricState(metrics[key])}{key==='grossTotalTokens'?' tokens':key==='voiceSeconds'?' voice seconds':''}{metrics[key]?.value!=null&&metricState(metrics[key])?` · ${metricState(metrics[key])}`:''}</span>)}{refreshButton}{current&&<details><summary>Usage snapshot details</summary>{details}<p>Context-window use is unavailable. Cumulative usage is not context size. Queue time and provider-internal retry counts are not reported.</p></details>}{error&&<span role="alert">{error}</span>}</section>;
 return <section aria-label="Conversation usage" className="a-settings-section"><h3>Conversation usage</h3><p className="a-caption">Recorded model calls and voice sessions in this chat and its observed workers. Cumulative tokens are not the current context-window size.</p>{refreshButton}{details}{error&&<p role="alert">{error}</p>}</section>;
}

export function ConversationStatus({session,act}){
 const presentation=useShellContext()?.composition?.presentation||{};
 if(!presentation.conversationStatus||!session)return null;
 if(!metricsVisible(presentation,'conversation'))return <section className="a-conversation-status" aria-label="Conversation status">{session.status||'Status unavailable'}</section>;
 return <ConversationUsage sessionId={session.id} status={session.status} act={act} compact/>;
}
