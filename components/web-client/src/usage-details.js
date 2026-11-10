import {compactTokens} from './timeline-data.js';

const count=value=>Number.isFinite(value)&&value>=0;
export function usageMetrics(usage={}){
 usage=usage||{};
 const calls=usage.calls??1,metrics={};
 for(const key of ['inputTokens','outputTokens','totalTokens','grossInputTokens','grossTotalTokens','cacheReadTokens','cacheWriteTokens','reasoningTokens','costUsd']){
  const known=usage.metricKnownCalls?.[key];
  const missing=key==='costUsd'?usage.costType==='unavailable':usage.tokenUnknownCalls===calls;
  const pending=key==='costUsd'?usage.costPendingCalls:usage.tokenPendingCalls;
  const value=!missing&&known!==0&&count(usage[key])?usage[key]:null;
  metrics[key]={value,status:value==null?(pending>0?'pending':'unknown'):(known!=null&&known<calls)||(key==='costUsd'?usage.costType==='partial':usage.tokenUnknownCalls>0)?'partial':'known',estimatedCalls:key==='costUsd'?(usage.estimatedCalls||(usage.costType==='estimated'?1:0)):0};
 }
 // Core input already includes cache reads; output already includes reasoning.
 // Only cache writes are additional tokens (the CLI uses the same convention).
 if(metrics.grossInputTokens.value==null&&metrics.inputTokens.value!=null)metrics.grossInputTokens={...metrics.inputTokens,value:metrics.inputTokens.value+(metrics.cacheWriteTokens.value||0)};
 if(metrics.grossTotalTokens.value==null){
  const input=metrics.grossInputTokens,output=metrics.outputTokens,total=metrics.totalTokens;
  if(input.value!=null&&output.value!=null)metrics.grossTotalTokens={value:input.value+output.value,status:[input,output].some(row=>row.status==='partial')?'partial':'known'};
  else if(total.value!=null)metrics.grossTotalTokens={...total,value:total.value+(metrics.cacheWriteTokens.value||0)};
 }
 if(count(usage.voiceSeconds)||usage.voiceKnownCalls!=null||usage.voicePendingCalls!=null||usage.voiceIncompleteCalls!=null||usage.usageStatus!=null){
  const value=count(usage.voiceSeconds)?usage.voiceSeconds:null;
  const pendingCalls=usage.voicePendingCalls??(usage.usageStatus==='running'?1:0);
  const incompleteCalls=usage.voiceIncompleteCalls??(usage.usageFinalized===false&&usage.usageStatus!=='running'?1:0);
  const knownCalls=usage.voiceKnownCalls??(value==null?0:1);
  metrics.voiceSeconds={value,unit:'seconds',knownCalls,pendingCalls,incompleteCalls,
   status:incompleteCalls>0||value!=null&&knownCalls<(usage.voiceSessionCalls??1)?'partial':pendingCalls>0?'pending':value==null?'unknown':'known'};
 }
 return metrics;
}

export function cachedPercent(metrics,key='cacheReadTokens'){
 const input=metrics.grossInputTokens,read=metrics[key];
 // Ratios of differently covered sums would imply a misleading cache hit rate.
 if(input?.status!=='known'||read?.status!=='known'||!count(input.value)||!count(read.value)||input.value<=0||read.value>input.value)return null;
 const percent=read.value/input.value*100;
 return percent>0&&percent<1?'<1%':`${Math.floor(percent)}%`;
}

export function compactBreakdown(usage){
 if(!usage)return '';
 const metrics=usageMetrics(usage),cached=cachedPercent(metrics),parts=[];
 if(metrics.grossInputTokens.value!=null)parts.push(`In ${compactTokens(metrics.grossInputTokens.value)}${cached!=null?` (${cached} cached)`:''}`);
 if(metrics.outputTokens.value!=null)parts.push(`Out ${compactTokens(metrics.outputTokens.value)}`);
 return parts.join(' · ');
}

export function usageRows(metrics,costLabel='Cost (USD)',{includeUnavailable=false}={}){
 return [
  ['grossInputTokens','Input tokens'],['cacheReadTokens','Read from cache'],['cacheWriteTokens','Written to cache'],
  ['outputTokens','Output tokens'],['reasoningTokens','Reasoning tokens'],['grossTotalTokens','Total tokens'],['voiceSeconds','Voice seconds'],['costUsd',costLabel]
 ].filter(([key])=>key!=='voiceSeconds'||metrics.voiceSeconds).map(([key,label])=>({key,label,metric:metrics[key]||{value:null,status:'unknown'}})).filter(row=>includeUnavailable||row.key==='voiceSeconds'||count(row.metric?.value));
}
