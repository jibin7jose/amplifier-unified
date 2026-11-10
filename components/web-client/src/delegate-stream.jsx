import React,{createContext,useContext,useEffect,useState} from 'react';
import {Square} from 'lucide-react';
import {DelegateMessage,RawExchange} from './delegate-exchange.jsx';
import {useToolDetail,ToolDetailStatus} from './tool-detail.jsx';
import {contentUnavailable} from './execution-content.js';
import {isRunning} from './timeline-data.js';
import {delegationRoutingLabel} from './delegation-routing.js';
import {UsageDetails} from './usage-details.jsx';

const failures=new Set(['error','failed','cancelled','interrupted','stopped']);
const SavedExchanges=createContext({});
const sourceKey=node=>JSON.stringify([node.id,node.toolObservationId||node.exchange?.toolObservationId,node.toolDetail||node.exchange?.toolDetail]);
function ExchangeSource({node,id,publish}){
 const detail=useToolDetail(node,true);
 const signature=JSON.stringify([detail.node,detail.loading,detail.error,detail.before]);
 useEffect(()=>{publish(values=>values[id]?.signature===signature?values:{...values,[id]:{signature,detail}})},[id,signature,publish]);
 return null;
}
export function DelegateStreamSources({entries,children}){
 const [values,publish]=useState({});
 const sources=new Map(entries.filter(entry=>entry.kind==='exchange').map(entry=>[sourceKey(entry.node),entry.node]));
 const keys=JSON.stringify([...sources.keys()]);
 useEffect(()=>{const keep=new Set(JSON.parse(keys));publish(values=>{
  const retained=Object.entries(values).filter(([id])=>keep.has(id));
  return retained.length===Object.keys(values).length?values:Object.fromEntries(retained);
 })},[keys]);
 return <SavedExchanges.Provider value={values}>{[...sources].map(([id,node])=><ExchangeSource key={id} id={id} node={node} publish={publish}/>)}{children}</SavedExchanges.Provider>;
}
export function DelegateStreamMessage({entry,expanded,toggle}){
 // Opening Work admits bounded previews through the existing saved-tool reader;
 // full message bodies still require explicit expansion/copy.
 const detail=useContext(SavedExchanges)[sourceKey(entry.node)]?.detail||{node:entry.node},node=detail.node,exchange=node.exchange||node;
 const present=exchange[entry.field]!=null||exchange[entry.field+'Detail'];
 const phase=[node.workerPhase,node.phase,exchange.phase].find(value=>failures.has(value))||node.workerPhase||exchange.phase||node.phase;
 const reply=entry.field==='delegateResponse';
 const missing=reply&&isRunning({phase})?'Waiting for reply…':contentUnavailable(exchange.contentAvailability?exchange:node,reply?'Reply':'Request');
 const receiptId=`exchange:${exchange.id}:receipt`,receiptOpen=expanded.has(receiptId);
 return <div className="a-work-exchange-entry" data-work-entry={entry.id} data-owner-id={entry.owner?.ownerId||entry.owner?.id}>
  {entry.owner?.label&&<p className="a-caption a-work-exchange-owner">{entry.owner.label.replace(/^Delegate · /,'')}</p>}
  {present?<DelegateMessage node={exchange} field={entry.field} label={entry.label} direction={entry.direction} expanded={expanded} toggle={toggle} partial={exchange.partial}/>:<section className="a-delegate-missing" aria-label={entry.label}><header>{entry.label} · {entry.direction}</header><p className="a-caption" role="status">{failures.has(phase)?`Delegate ${phase}. ${missing}`:missing}</p></section>}
  {!reply&&<ToolDetailStatus detail={detail}/>}
  {entry.followup&&<p className="a-caption">{failures.has(phase)?`Follow-up ${phase}; delivery was not confirmed.`:['completed','complete','success'].includes(phase)?'Follow-up recorded; application is not confirmed by its receipt.':'Follow-up attempted; awaiting receipt.'}</p>}
  {entry.receipt&&!reply&&<><button type="button" className="a-link" aria-expanded={receiptOpen} onClick={()=>toggle(receiptId)}>{entry.followup?'Follow-up receipt':'Request receipt'}</button>{receiptOpen&&<RawExchange node={exchange}/>}</>}
  {reply&&present&&exchange.delivery!=='returned'&&!entry.report&&<p className="a-caption">Recorded return; receipt alone does not confirm that the main chat used it.</p>}
  {!reply&&node.savedObservations?.length>0&&<details className="a-execution-metadata"><summary>Related saved records</summary><p className="a-caption">These records could not be linked to this attempt.</p>{node.savedObservations.map(record=><RawExchange key={record.id} node={record}/>)}</details>}
 </div>;
}

export function DelegateStreamStatus({entry,expanded,toggle,act}){
 const node=entry.node,shared=useContext(SavedExchanges)[sourceKey(node)]?.detail;
 const key=`exchange:${node.id}:details`,open=expanded.has(key);
 const contextDetail=useToolDetail(node,open&&!shared),loaded=shared?.node||contextDetail.node;
 const exchange=loaded.exchange||node.exchange||{id:node.ownerId||node.id},phase=node.workerPhase||node.status||node.phase;
 const running=isRunning({phase});
 const name=(node.label||'Delegate').replace(/^Delegate · /,'');
 return <aside className="a-work-delegate-status" aria-label={`${name} status`}>
  <span className="a-caption">{name}</span>
  {failures.has(phase)&&<p className="a-caption" role="status">Worker {phase}. Earlier replies remain available; they do not establish completion.</p>}
  {running&&<><span className="a-caption">{phase==='idle'?'Delegate is available for follow-up.':'Delegate is working.'}</span>{(node.workerId||node.sessionId)&&<button type="button" className="a-link a-danger" data-action="worker.stop" onClick={()=>act('worker.stop',{id:node.workerId||node.sessionId})}><Square/>Stop worker</button>}</>}
  <button type="button" className="a-link" aria-label={`${name} delegation details`} aria-expanded={open} onClick={()=>toggle(key)}>Delegation details</button>
  {open&&<div className="a-delegate-details"><ToolDetailStatus detail={shared||contextDetail}/>
   {entry.context&&<section aria-label="Earlier exchange context"><p className="a-caption">Earlier exchange context, not new messages in this part of the conversation.</p>
    <DelegateMessage node={exchange} field="delegateRequest" label="Original request" direction="Recorded exchange" expanded={expanded} toggle={toggle}/>
    <DelegateMessage node={exchange} field="delegateResponse" label="Recorded reply" direction="Recorded exchange" expanded={expanded} toggle={toggle}/></section>}
   <p className="a-caption">{delegationRoutingLabel(node.routing)}</p><p className="a-caption">{[node.provider,node.model].filter(Boolean).join(' · ')}</p><p className="a-caption">{exchange.toolCallId&&`Call: ${exchange.toolCallId}`}{node.runId&&` · Run: ${node.runId}`}</p>{node.aggregateUsage&&<UsageDetails usage={node.aggregateUsage} scope="step" record={{...node,phase}} label="Delegate usage" costLabel="Delegate cost"/>}<RawExchange node={exchange}/>{node.summary&&<><p className="a-caption">Saved summary, not the original reply.</p><DelegateMessage node={node} field="summary" label="Saved task summary" direction="Delegate" expanded={expanded} toggle={toggle} partial/></>}</div>}
 </aside>;
}
