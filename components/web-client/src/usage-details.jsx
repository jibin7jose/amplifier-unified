import React,{useState} from 'react';
import {useShellContext} from './shell/runtime';
import {usageMetric} from './conversation-usage.js';
import {cachedPercent,usageMetrics,usageRows} from './usage-details.js';
import {selectedMetrics,metricsVisible,metricState,metricExport} from './execution-observability.js';
import {MetricsExport} from './execution-observability.jsx';

export function UsageDetails({usage,metrics=usageMetrics(usage),label='Token usage',costLabel='Cost (USD)',scope=null,record,observedAt,coverage}){
 const shell=useShellContext(),presentation=shell?.composition?.presentation;
 const level=presentation?.interfaceDetail||presentation?.executionDetail||'standard';
 const [expanded,setExpanded]=useState(null),open=expanded??level==='detailed';
 const selected=selectedMetrics(scope?presentation:{}),cached=selected.includes('cacheReadTokens')?cachedPercent(metrics):null;
 const extra=record?metricExport({metrics,presentation,scope,record,observedAt}).metrics:{};
 const rows=[...usageRows(metrics,costLabel,{includeUnavailable:!!record}),...record?[['elapsedSeconds','Elapsed seconds'],['calls','Recorded calls']].map(([key,label])=>({key,label,metric:extra[key]})):[]].filter(row=>selected.includes(row.key));
 const primary=new Set(['grossInputTokens','outputTokens','grossTotalTokens','voiceSeconds','costUsd','elapsedSeconds','calls']);
 const primaryRows=rows.filter(row=>primary.has(row.key));
 const accountingRows=rows.filter(row=>!primary.has(row.key)||row.key==='costUsd').map(row=>row.key==='costUsd'?{...row,label:'Cost accounting (USD)'}:row);
 if(scope&&!metricsVisible(presentation,scope)||!scope&&!rows.length)return null;
 const grid=(items,compact=false)=><dl className="a-usage-grid">{items.map(({key,label,metric})=>{
  const percent=key==='grossInputTokens'?cached:['cacheReadTokens','cacheWriteTokens'].includes(key)?cachedPercent(metrics,key):null;
  return <div key={key}><dt>{label}</dt><dd>{metric?.value==null?metricState(metric):['elapsedSeconds','voiceSeconds'].includes(key)?Number(metric.value.toFixed(1)):usageMetric({...metric,estimatedCalls:0},key==='costUsd',compact)}{metric?.value!=null&&metricState(metric)&&<small>{metricState(metric)}</small>}{percent!=null&&<small>{percent} {key==='grossInputTokens'?'cached':'of input'}</small>}</dd></div>;
 })}</dl>;
 return <section className="a-usage-details" aria-label={label}>
  {primaryRows.length>0&&grid(primaryRows,true)}
  {rows.some(row=>row.key==='voiceSeconds')&&<p className="a-caption">Voice seconds are reported separately from tokens. Incomplete totals retain the last observed duration.</p>}
  {record&&<MetricsExport metrics={metrics} scope={scope} record={record} observedAt={observedAt} coverage={coverage}/>}
  {!rows.length&&<p className="a-caption">No metrics selected.</p>}
  {accountingRows.length>0&&<details className="a-usage-accounting" open={open} onToggle={event=>{if(event.currentTarget.open!==open)setExpanded(event.currentTarget.open)}}>
   <summary>Cache and accounting details</summary>
   {grid(accountingRows)}
   <p className="a-caption">Input includes cache reads and writes. Total = input + output. Reasoning is included in output.{metrics.costUsd?.value!=null&&!(metrics.costUsd.estimatedCalls>0)&&' Cost reported by the provider.'}</p>
  </details>}
 </section>;
}
