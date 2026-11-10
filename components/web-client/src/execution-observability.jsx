import React,{useState} from 'react';
import {useShellContext} from './shell/runtime';
import {usageMetric} from './conversation-usage.js';
import {usageMetrics,cachedPercent} from './usage-details.js';
import {compactTokens} from './timeline-data.js';
import {metricChoices,selectedMetrics,metricsVisible,metricExport,metricState} from './execution-observability.js';
import './execution-observability.css';

export function ObservabilityPreferences({presentation,setPresentation,busy}){
 const selected=selectedMetrics(presentation);
 return <details className="a-observability-preferences"><summary>Execution metrics</summary>
  <p>Choose metrics independently of the overall detail level. Work status, errors and stop controls remain available.</p>
  <fieldset disabled={busy}><legend>Show metrics in</legend>{[['step','Individual steps'],['turn','Work summaries'],['conversation','Conversation totals']].map(([scope,label])=><label key={scope}><input type="checkbox" checked={metricsVisible(presentation,scope)} data-action="shell.changes.apply" onChange={e=>setPresentation({executionVisibility:{...presentation.executionVisibility,[scope]:e.target.checked}})}/>{label}</label>)}</fieldset>
  <fieldset disabled={busy}><legend>Metrics to show and export</legend>{metricChoices.map(([key,label])=><label key={key}><input type="checkbox" checked={selected.includes(key)} data-action="shell.changes.apply" onChange={e=>setPresentation({executionMetrics:e.target.checked?[...selected,key]:selected.filter(item=>item!==key)})}/>{label}</label>)}</fieldset>
  <label><input type="checkbox" checked={!!presentation.conversationStatus} disabled={busy} data-action="shell.changes.apply" onChange={e=>setPresentation({conversationStatus:e.target.checked})}/>Compact conversation status</label>
  <p className="a-caption">Reasoning tokens are counts only. Current context-window size, queue time and internal retries are unavailable unless separately reported; cumulative usage is not context size.</p>
 </details>;
}

export function MetricsExport({metrics,scope,record,observedAt,coverage}){
 const presentation=useShellContext()?.composition?.presentation||{},[feedback,setFeedback]=useState('');
 const value=()=>JSON.stringify(metricExport({metrics,presentation,scope,record,observedAt,coverage}),null,2);
 async function copy(){try{await navigator.clipboard.writeText(value());setFeedback('Metrics copied')}catch{setFeedback('Clipboard unavailable. Download the selected metrics instead.')}}
 function download(){const url=URL.createObjectURL(new Blob([value()+'\n'],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=`${scope}-metrics.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),0);setFeedback('Metrics downloaded')}
 if(!metricsVisible(presentation,scope))return null;
 return <div className="a-metric-export"><button type="button" className="a-link" onClick={copy}>Copy selected metrics</button><button type="button" className="a-link" onClick={download}>Download selected metrics</button>{feedback&&<small role="status">{feedback}</small>}</div>;
}

export function MetricSummary({usage,scope='step',pending=false}){
 const presentation=useShellContext()?.composition?.presentation||{};
 if(!metricsVisible(presentation,scope)||!usage)return null;
 if(presentation.executionVisibility?.[scope]===undefined&&(presentation.interfaceDetail||presentation.executionDetail)==='minimal')return null;
 const metrics=usageMetrics(usage),selected=selectedMetrics(presentation);
 const rows=['grossTotalTokens','voiceSeconds','costUsd'].filter(key=>selected.includes(key)).map(key=>({key,metric:metrics[key]})).filter(row=>row.metric?.value!=null);
 const breakdown=[],state=(metric,voice=false)=>[...new Set([pending&&!voice?'Live':null,metricState(metric)].filter(Boolean))].join(' · ');
 if(selected.includes('grossInputTokens')&&metrics.grossInputTokens.value!=null){const cached=selected.includes('cacheReadTokens')?cachedPercent(metrics):null;breakdown.push(`In ${compactTokens(metrics.grossInputTokens.value)}${cached?` (${cached} cached)`:''}${state(metrics.grossInputTokens)?` · ${state(metrics.grossInputTokens)}`:''}`)}
 if(selected.includes('outputTokens')&&metrics.outputTokens.value!=null)breakdown.push(`Out ${compactTokens(metrics.outputTokens.value)}${state(metrics.outputTokens)?` · ${state(metrics.outputTokens)}`:''}`);
 return rows.length||breakdown.length?<span className="a-execution-usage" data-metrics-explicit={presentation.executionVisibility?.[scope]===true} data-metrics-only-breakdown={rows.length===0}>{rows.map(({key,metric},index)=><React.Fragment key={key}>{index>0?' · ':''}{key==='costUsd'?usageMetric({...metric,estimatedCalls:0},true,true):key==='voiceSeconds'?Number(metric.value.toFixed(1)):compactTokens(metric.value)}{key==='grossTotalTokens'?' tokens':key==='voiceSeconds'?' voice seconds':''}{state(metric,key==='voiceSeconds')&&<small className="a-metric-state">{state(metric,key==='voiceSeconds')}</small>}</React.Fragment>)}{breakdown.length>0&&<small className="a-usage-breakdown">{breakdown.join(' · ')}</small>}</span>:null;
}
