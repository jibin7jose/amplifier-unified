// Presentation over the existing receipt totals, never another usage accumulator.
export const metricChoices = [
 ['elapsedSeconds','Elapsed time'],['grossInputTokens','Input tokens'],['outputTokens','Output tokens'],
 ['grossTotalTokens','Total tokens'],['cacheReadTokens','Cache reads'],['cacheWriteTokens','Cache writes'],
 ['reasoningTokens','Reasoning tokens'],['voiceSeconds','Voice seconds'],['costUsd','Cost'],['calls','Recorded call count'],
];
export const metricKeys=metricChoices.map(([key])=>key);
export function selectedMetrics(presentation={}){
 return Array.isArray(presentation.executionMetrics)?presentation.executionMetrics.filter(key=>metricKeys.includes(key)):metricKeys;
}
export function metricsVisible(presentation={},scope='step'){
 return presentation.executionVisibility?.[scope]??true;
}
export function metricState(metric){
 if(metric?.value==null)return metric?.incompleteCalls>0?'Incomplete':metric?.status==='pending'?'Pending':metric?.status==='empty'?'No recorded calls':'Unavailable';
 return [metric.incompleteCalls>0?'Incomplete':metric.status==='partial'?'Partial':null,metric.status==='pending'||metric.pendingCalls>0?'Live':null,metric.estimatedCalls>0?'Includes estimates':null].filter(Boolean).join(' · ');
}
export function metricExport({metrics={},presentation={},scope,record={},observedAt=null,coverage='Observed receipts only'}){
 const selected=selectedMetrics(presentation),data={};
 for(const key of selected){
  let row=metrics[key];
  if(key==='elapsedSeconds'&&Number.isFinite(record.startedAt)){
   const live=!Number.isFinite(record.endedAt)&&['running','working','starting','queued','pending','retrying','idle'].includes(record.status||record.phase);
   const end=record.endedAt??(live?observedAt:null);
   if(Number.isFinite(end))row={value:Math.max(0,end-record.startedAt),status:live?'pending':'known',unit:'seconds'};
  }
  if(key==='elapsedSeconds'&&Number.isFinite(record.elapsedSeconds))row={value:record.elapsedSeconds,status:record.phase==='running'?'pending':'known',unit:'seconds'};
  if(key==='calls'&&Number.isFinite(record.calls))row={value:record.calls,status:'known',unit:'calls'};
  // Explicit allowlist excludes payloads, request options, reasoning text and credentials.
  data[key]=Object.fromEntries(Object.entries(row||{value:null,status:'unknown'}).filter(([key])=>['value','status','unit','knownCalls','pendingCalls','incompleteCalls','unknownCalls','estimatedCalls','estimatedValue','source'].includes(key)));
 }
 const identity=Object.fromEntries(['id','sessionId','rootSessionId','turnId','workerId','provider','model','status','phase','startedAt','endedAt'].filter(key=>record[key]!=null).map(key=>[key,record[key]]));
 return {schema:'execution-metrics-v1',scope,observedAt,identity,metrics:data,coverage};
}
