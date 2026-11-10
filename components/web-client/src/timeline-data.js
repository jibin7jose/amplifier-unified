import {visibleWorkers} from './activity.js';
export function executionData(session){
 if(session?.workerSummary?.bounded||session?.execution?.nodes?.length||session?.execution?.turns?.length){const nodes=session.execution.nodes||[],turns=[...(session.execution.turns||[])];for(const node of nodes)if(node.turnId&&!turns.some(turn=>turn.id===node.turnId))turns.push({id:node.turnId});return {nodes,turns,segments:session.execution.segments||[],detailsDeferred:session.execution.detailsDeferred,sessionId:session.id}}
 const workers=visibleWorkers(session?.workers||[]),events=session?.runtimeEvents||[];
 if(!workers.length&&!events.some(e=>e.type==='runtime.tool'||String(e.type).startsWith('tool.')))return {nodes:[],turns:[]};
 const turnId='observed-activity',nodes=[],tools=new Map();
 for(const event of events){
  const kind=event.type==='runtime.tool'?event.phase:String(event.type).replace(/^tool[.:]/,'');
  if(event.type!=='runtime.tool'&&!/^tool[.:]/.test(event.type||''))continue;
  const call=event.callId||event.call_id||event.tool_call_id;if(!call)continue;
  const previous=tools.get(call)||{id:`tool:${call}`,kind:'tool',turnId,label:event.tool||event.tool_name||'Tool call',callId:call,startedAt:event.at||event.time};
  previous.status=['post','end','finished'].includes(kind)?'completed':['error','failed'].includes(kind)?'error':'running';
  previous.actualSessionId=event.actualSessionId||previous.actualSessionId;
  tools.set(call,previous);
 }
 const workersBySession=new Map(workers.map(worker=>[worker.sessionId||worker.id,worker]));
 for(const tool of tools.values()){if(workersBySession.has(tool.actualSessionId))tool.parentId=`worker:${tool.actualSessionId}`;nodes.push(tool)}
 for(const worker of workers){
  const id=worker.sessionId||worker.id,call=worker.callId||worker.call_id;
  const parentWorker=workersBySession.get(worker.parentSessionId);
  nodes.push({id:`worker:${id}`,kind:'worker',turnId,routing:worker.routing,provider:worker.provider,model:worker.model,label:worker.name||worker.agent||worker.title||'Worker',status:worker.status,phase:worker.phase,summary:worker.detail||worker.report||worker.result,summaryDetail:worker.detail?worker.detailDetail:worker.report?worker.reportDetail:worker.resultDetail,startedAt:worker.startedAt,endedAt:worker.endedAt,workerId:worker.id||id,parentId:tools.has(call)?`tool:${call}`:parentWorker?`worker:${parentWorker.sessionId||parentWorker.id}`:null});
 }
 // Older records contain only action observations. A known host lifecycle
 // still owns its outcome; tool errors must not invent a failed turn or settle
 // active work between calls. Independently live children stay inspectable.
 const status=['error','failed','cancelled','interrupted','stopped'].includes(session?.status)?session.status:
  ['working','starting','running','stopping'].includes(session?.status)||nodes.some(n=>['running','working','starting','queued','pending','retrying','idle'].includes(n.status))?'running':'completed';
 return {nodes,turns:[{id:turnId,label:'Recent execution activity',status}]};
}
export function messageTurnId(message,turns){
 if(message.role!=='user')return null;
 const turn=turns.find(turn=>turn.messageId===message.id||turn.userMessageId===message.id||(message.inputId&&(turn.inputId===message.inputId||turn.id===message.inputId)));
 return turn?.id||null;
}
export function turnPlacements(messages,data){
 const after=new Map(),before=[],ids=new Set(messages.map(m=>m.id));
 for(const turn of data.turns){
  let anchor=turn.anchorMessageId;
  if(!Object.hasOwn(turn,'anchorMessageId')){
   const exact=messages.find(m=>m.role==='user'&&((turn.messageId&&turn.messageId===m.id)||(turn.userMessageId&&turn.userMessageId===m.id)||(m.inputId&&(turn.inputId===m.inputId||turn.id===m.inputId))));
   const times=data.nodes.filter(n=>n.turnId===turn.id&&Number.isFinite(n.startedAt)).map(n=>n.startedAt);
   const started=Number.isFinite(turn.startedAt)?turn.startedAt:times.length?Math.min(...times):null;
   anchor=exact?.id||(started!==null?messages.findLast(m=>Number.isFinite(m.createdAt)&&m.createdAt<=started)?.id:null);
  }
  if(anchor&&ids.has(anchor)){if(!after.has(anchor))after.set(anchor,[]);after.get(anchor).push(turn.id)}else before.push(turn.id);
 }
 return {after,before};
}
export function compactTokens(value){return value>=1000000?`${(value/1000000).toFixed(1).replace(/\.0$/,'')}m`:value>=1000?`${(value/1000).toFixed(1).replace(/\.0$/,'')}k`:String(value)}
export const liveStates=new Set(['running','working','starting','queued','pending','retrying','idle']);
export function isRunning(record){return liveStates.has(record?.status||record?.phase)&&!Number.isFinite(record?.endedAt)}
export function workLabel(phase,elapsed,running=false){
 if(running)return `Working${elapsed?` · ${elapsed}`:'…'}`;
 const outcome={stopped:'Stopped',cancelled:'Cancelled',interrupted:'Interrupted',error:'Failed',failed:'Failed'}[phase];
 return outcome?`${outcome}${elapsed?` · ${elapsed}`:''}`:elapsed?`Worked for ${elapsed}`:'Work details';
}
export function elapsedLabel(record,now=Date.now()/1000){
 if(!Number.isFinite(record?.startedAt))return null;
 const end=Number.isFinite(record.endedAt)?record.endedAt:isRunning(record)?now:null;
 if(end===null)return null;
 const duration=Math.max(0,end-record.startedAt),seconds=Math.floor(duration);
 return duration>=60?`${Math.floor(seconds/60)}m ${seconds%60}s`:isRunning(record)?`${seconds}s`:`${Number(duration.toFixed(1))}s`;
}
export function usageLabel(usage){
 if(!usage)return null;
 if(usage.calls===0&&!usage.totalTokens&&!usage.inputTokens&&!usage.outputTokens&&!usage.costUsd&&usage.voiceSeconds==null)return null;
 const reported=usage.totalTokens??((usage.inputTokens!=null||usage.outputTokens!=null)?(usage.inputTokens||0)+(usage.outputTokens||0):null);
 const tokens=usage.grossTotalTokens??(reported===null?null:reported+(usage.cacheWriteTokens||0));
 const type=usage.costType||((usage.costUsd!=null&&usage.unknownCalls===0)?'reported':'unavailable');
 const cost=typeof usage.costUsd==='number'&&type!=='unavailable'?`${type==='estimated'||usage.estimatedCalls>0?'≈':''}$${usage.costUsd<.01?usage.costUsd.toFixed(6).replace(/0+$/,'').replace(/\.$/,'.00'):usage.costUsd.toFixed(usage.costUsd>=1?2:3)}`:null;
 const pieces=[];
 if(tokens!=null&&!(usage.calls>0&&usage.tokenUnknownCalls>=usage.calls))pieces.push(`${compactTokens(tokens)} tokens`);
 if(Number.isFinite(usage.voiceSeconds))pieces.push(`${Number(usage.voiceSeconds.toFixed(1))} voice seconds${usage.voiceIncompleteCalls>0||usage.usageStatus==='incomplete'?' · Incomplete':usage.voicePendingCalls>0||usage.usageStatus==='running'?' · Live':''}`);
 if(cost)pieces.push(cost);
 if(!pieces.length)return null;
 const grossInput=usage.grossInputTokens??(usage.inputTokens!=null?usage.inputTokens+(usage.cacheWriteTokens||0):null);
 const breakdown=[
  grossInput!=null&&(usage.calls==null||usage.tokenUnknownCalls!==usage.calls)?`${grossInput} input tokens (includes cache writes)`:null,
  usage.outputTokens!=null&&(usage.calls==null||usage.tokenUnknownCalls!==usage.calls)?`${usage.outputTokens} output tokens`:null,
  usage.cacheReadTokens?`${usage.cacheReadTokens} cache-read tokens`:null,
  usage.cacheWriteTokens?`${usage.cacheWriteTokens} cache-write tokens`:null,
  cost?(type==='estimated'||usage.estimatedCalls>0?'Includes estimated cost':'Cost reported by the provider'):null
 ].filter(Boolean).join(' · ');
 return {text:pieces.join(' · '),title:breakdown};
}
export function treeForTurn(data,turnId){
 const nodes=data.nodes.filter(node=>node.turnId===turnId).map(node=>({...node}));
 const workerRows=data.nodes.filter(node=>node.kind==='worker'&&node.sessionId),workersById=new Map(workerRows.map(node=>[node.id,node]));
 const counts=new Map();for(const worker of workerRows)counts.set(worker.sessionId,(counts.get(worker.sessionId)||0)+1);
 const workers=new Map(workerRows.filter(node=>counts.get(node.sessionId)===1).map(node=>[node.sessionId,node]));
 for(const node of nodes){const owner=node.delegate||(node.kind==='worker'?node:null);if(owner?.sessionId){for(const ancestor of owner.ancestors||[])workersById.set(ancestor.id,{...workersById.get(ancestor.id),...ancestor});workersById.set(owner.id,{...workersById.get(owner.id),...owner})}}
 const sourceById=new Map(data.nodes.map(node=>[node.id,node]));
 const groups=new Map();
 const ensureGroup=(owner,seen=new Set())=>{
  const id=`delegate:${turnId}:${owner.id}`;
  if(groups.has(id)||seen.has(owner.id))return id;
  seen.add(owner.id);
  const sourceTool=sourceById.get(owner.parentId),parentOwner=workersById.get(owner.parentWorkerId)||workersById.get(sourceTool?.parentId)||workersById.get(sourceTool?.delegate?.id);
  const parentId=parentOwner&&!seen.has(parentOwner.id)?ensureGroup(parentOwner,seen):null;
  groups.set(id,{...owner,id,ownerId:owner.id,workerPhase:owner.status||owner.phase,kind:'worker',label:`Delegate · ${owner.label||'Worker'}`,turnId,parentId,members:[]});
  return id;
 };
 for(const node of nodes){
  if(node.kind==='worker')continue;
  const owner=node.delegate?{...workersById.get(node.delegate.id),...node.delegate}:workersById.get(node.parentId)||workers.get(node.sessionId);if(!owner)continue;
  const id=ensureGroup(owner);
  groups.get(id).members.push(node);node.parentId=id;
 }
 for(const node of nodes)if(node.kind==='worker'&&node.exchange)ensureGroup(node);
 // Delegate headers repeat in each message gap; their usage and timing cover
 // only calls displayed here, never the delegate's entire historical total.
 for(const group of groups.values()){
  const members=group.members;delete group.members;
  const models=[...new Set(members.filter(node=>node.kind==='llm').map(node=>[node.provider,node.model].filter(Boolean).join(' · ')).filter(Boolean))];
  if(models.length){group.provider=undefined;group.model=models.join(', ')}
  const starts=members.map(node=>node.startedAt).filter(Number.isFinite),ends=members.map(node=>node.endedAt).filter(Number.isFinite);
  const running=members.some(isRunning);group.startedAt=starts.length?Math.min(...starts):undefined;
  group.endedAt=running?undefined:ends.length?Math.max(...ends):undefined;
  group.status=undefined;group.phase=running?'running':'completed';group.aggregateUsage=segmentUsage(members);
 }
 const groupedOwners=new Set([...groups.values()].map(group=>group.ownerId));
 const exchanges=new Set([...groups.values(),...nodes.filter(node=>node.kind==='worker')].map(node=>node.exchange?.id).filter(Boolean));
 const visible=nodes.filter(node=>(node.kind!=='worker'||!groupedOwners.has(node.id))&&!(node.kind==='tool'&&exchanges.has(node.id)));
 visible.push(...groups.values());
 const byId=new Map(visible.map(node=>[node.id,node]));
 const children=new Map();for(const node of visible){const parent=node.parentId&&node.parentId!==node.id&&byId.has(node.parentId)?node.parentId:null;if(!children.has(parent))children.set(parent,[]);children.get(parent).push(node)}
 // Orphan and malformed cyclic source records remain inspectable without recursion.
 const roots=children.get(null)||[],covered=new Set();
 const mark=node=>{if(covered.has(node.id))return;covered.add(node.id);for(const child of children.get(node.id)||[])mark(child)};
 roots.forEach(mark);for(const node of visible)if(!covered.has(node.id)){roots.push(node);mark(node)}
 return {roots,children};
}

export function detailLinks(text){
 let value;try{value=JSON.parse(text)}catch{return []}
 const urls=new Set();
 function visit(item,depth=0){
  if(!item||typeof item!=='object'||depth>8)return;
  for(const [key,value] of Object.entries(item).slice(0,100)){
   if(urls.size>=8)return;
   if(['url','uri','html_url','web_url','artifact_url'].includes(key)&&typeof value==='string'){
    try{const url=new URL(value);if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password)urls.add(url.href)}catch{}
   }else if(typeof value==='object')visit(value,depth+1);
  }
 }
 visit(value);return [...urls];
}

export function segmentUsage(nodes){
 const calls=nodes.filter(node=>node.kind==='llm'),value={calls:calls.length,pricedCalls:0,unknownCalls:0,estimatedCalls:0,tokenUnknownCalls:0,costPendingCalls:0,tokenPendingCalls:0,costUsd:0};
 value.metricKnownCalls={};
 for(const key of ['inputTokens','outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens','reasoningTokens']){value[key]=0;value.metricKnownCalls[key]=0}
 for(const node of calls){const usage=node.usage||{},pending=isRunning(node);
  if(Number.isFinite(usage.voiceSeconds)&&usage.voiceSeconds>=0){
   value.voiceSeconds=(value.voiceSeconds||0)+usage.voiceSeconds;
   value.voiceKnownCalls=(value.voiceKnownCalls||0)+1;
  }
  if('voiceSeconds' in usage||'usageFinalized' in usage){
   value.voiceSessionCalls=(value.voiceSessionCalls||0)+1;
   value.voiceKnownCalls??=0;
   value.voicePendingCalls=(value.voicePendingCalls||0)+(pending?1:0);
   value.voiceIncompleteCalls=(value.voiceIncompleteCalls||0)+(!pending&&usage.usageFinalized!==true?1:0);
  }
  for(const key of ['inputTokens','outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens','reasoningTokens'])if(Number.isFinite(usage[key])){value[key]+=usage[key];value.metricKnownCalls[key]++}
  if(usage.totalTokens==null&&(usage.inputTokens!=null||usage.outputTokens!=null)){value.totalTokens+=(usage.inputTokens||0)+(usage.outputTokens||0);value.metricKnownCalls.totalTokens++}
  if(usage.totalTokens==null&&usage.inputTokens==null&&usage.outputTokens==null){value.tokenUnknownCalls++;if(pending)value.tokenPendingCalls++}
  if(Number.isFinite(usage.costUsd)){value.costUsd+=usage.costUsd;value.pricedCalls++;if(usage.costType==='estimated')value.estimatedCalls++}else{value.unknownCalls++;if(pending)value.costPendingCalls++}
 }
 value.costType=!value.pricedCalls?'unavailable':value.unknownCalls?'partial':value.estimatedCalls?'estimated':'reported';return value;
}
export function splitWork(messages,data){
 const turns=[],nodes=[];
 for(const turn of data.turns){
  const source=data.nodes.filter(node=>node.turnId===turn.id&&!(node.lifecycle==='background'&&node.label==='Session naming')),groups=new Map();
  let lastGroup,lastAt=-Infinity;
  for(const node of source){
   const at=Number.isFinite(node.startedAt)?node.startedAt:Number.isFinite(node.endedAt)?node.endedAt:turn.startedAt;
   let anchor=turn.anchorMessageId??null;
   if(Object.hasOwn(node,'anchorMessageId'))anchor=node.anchorMessageId;
   else if(Number.isFinite(at))for(const message of messages){if(message.timestampKnown!==false&&Number.isFinite(message.createdAt)&&message.createdAt<=at)anchor=message.id}
   // Explicit anchors are still useful when older logs have no timestamps.
   const id=`${turn.id}@${anchor||'start'}`;
   if(!groups.has(id))groups.set(id,{id,anchor,nodes:[]});
   groups.get(id).nodes.push({...node,turnId:id});
   const order=Number.isFinite(at)?at:-Infinity;
   if(lastGroup===undefined||order>=lastAt){lastAt=order;lastGroup=id}
  }
  for(const segment of data.segments||[])if(segment.turnId===turn.id&&!groups.has(segment.id))groups.set(segment.id,{id:segment.id,anchor:segment.anchorMessageId,nodes:[]});
  if(!groups.size&&isRunning(turn)){
   lastGroup=`${turn.id}@${turn.anchorMessageId||'start'}`;
   groups.set(lastGroup,{id:lastGroup,anchor:turn.anchorMessageId,nodes:[]});
  }
  for(const group of groups.values()){
   const starts=group.nodes.map(node=>node.startedAt).filter(Number.isFinite),ends=group.nodes.map(node=>node.endedAt).filter(Number.isFinite);
   const turnFailure=group.id===lastGroup&&['error','failed','cancelled','interrupted','stopped'].includes(turn.status||turn.phase)?turn.status||turn.phase:null;
   // Tool/worker errors and recovered model attempts stay on their action rows.
   // Only the manager outcome fails a work header. Its current segment remains
   // active between calls, even when every action so far has already settled.
   const running=!turnFailure&&(group.nodes.some(isRunning)||(group.id===lastGroup&&isRunning(turn)));
   if(turnFailure&&Number.isFinite(turn.endedAt))ends.push(turn.endedAt);
   turns.push({...turn,id:group.id,originalTurnId:turn.id,anchorMessageId:group.anchor,startedAt:starts.length?Math.min(...starts):turn.startedAt,
    endedAt:running?undefined:ends.length?Math.max(...ends):turn.endedAt,phase:running?'running':turnFailure||((turn.status||turn.phase)&&(ends.length||turn.endedAt)?'completed':'recorded'),status:undefined,
    aggregateUsage:segmentUsage(group.nodes),nodeCounts:{tools:group.nodes.filter(n=>n.kind==='tool').length,models:group.nodes.filter(n=>n.kind==='llm').length},
    ...(data.segments||[]).find(segment=>segment.id===group.id)});
   nodes.push(...group.nodes);
  }
 }
 return {nodes,turns,detailsDeferred:data.detailsDeferred,sessionId:data.sessionId};
}

// Group rendered neighbors, rather than execution IDs: background continuations
// may have independent turns while occupying the same gap in the conversation.
export function conversationWorkRows(messages,placement){
 const rows=[];
 const append=item=>{let row=rows.at(-1);if(row?.kind!=='work'){row={kind:'work',id:`work:${item.turnId}`,items:[]};rows.push(row)}row.items.push(item)};
 for(const turnId of placement.before)append({turnId});
 for(const message of messages){
  if(!message.observation)rows.push({kind:'message',id:message.id,message});
  for(const turnId of placement.after.get(message.id)||[])append({turnId});
 }
 return rows;
}

export function combinedWork(turns,now){
 const running=turns.some(isRunning),failure=turns.find(turn=>['error','failed','cancelled','interrupted','stopped'].includes(turn.status||turn.phase));
 const intervals=turns.map(turn=>[turn.startedAt,Number.isFinite(turn.endedAt)?turn.endedAt:isRunning(turn)?now:null])
  .filter(([start,end])=>Number.isFinite(start)&&Number.isFinite(end)).sort((a,b)=>a[0]-b[0]);
 let duration=0,end=-Infinity;
 for(const [start,stop] of intervals){duration+=Math.max(0,stop-Math.max(start,end));end=Math.max(end,stop)}
 const usage={calls:0,pricedCalls:0,unknownCalls:0,estimatedCalls:0,tokenUnknownCalls:0,costPendingCalls:0,tokenPendingCalls:0};
 usage.metricKnownCalls={};
 const amounts=['inputTokens','outputTokens','totalTokens','cacheReadTokens','cacheWriteTokens','reasoningTokens','costUsd'];
 for(const turn of turns){
  const value=turn.aggregateUsage||turn.usage||{},calls=value.calls??turn.nodeCounts?.models??0;
  usage.calls+=calls;
  const priced=value.pricedCalls??(Number.isFinite(value.costUsd)&&value.costType!=='unavailable'?calls:0);
  const unknownTokens=value.tokenUnknownCalls??(value.totalTokens==null&&value.inputTokens==null&&value.outputTokens==null?calls:0);
  usage.pricedCalls+=priced;usage.unknownCalls+=value.unknownCalls??Math.max(0,calls-priced);
  usage.estimatedCalls+=value.estimatedCalls??(value.costType==='estimated'?priced:0);
  usage.tokenUnknownCalls+=unknownTokens;
  usage.costPendingCalls+=value.costPendingCalls??(isRunning(turn)?Math.max(0,calls-priced):0);
  usage.tokenPendingCalls+=value.tokenPendingCalls??(isRunning(turn)?unknownTokens:0);
  if(Number.isFinite(value.voiceSeconds)&&value.voiceSeconds>=0){
   usage.voiceSeconds=(usage.voiceSeconds||0)+value.voiceSeconds;
  }
  if('voiceSeconds' in value||'usageFinalized' in value||value.voiceSessionCalls!=null){
   usage.voiceSessionCalls=(usage.voiceSessionCalls||0)+(value.voiceSessionCalls??1);
   usage.voiceKnownCalls=(usage.voiceKnownCalls||0)+(value.voiceKnownCalls??(Number.isFinite(value.voiceSeconds)?1:0));
   usage.voicePendingCalls=(usage.voicePendingCalls||0)+(value.voicePendingCalls??(value.usageStatus==='running'?1:0));
   usage.voiceIncompleteCalls=(usage.voiceIncompleteCalls||0)+(value.voiceIncompleteCalls??(value.usageFinalized===false&&value.usageStatus!=='running'?1:0));
  }
  for(const key of amounts){
   if(Number.isFinite(value[key]))usage[key]=(usage[key]||0)+value[key];
   usage.metricKnownCalls[key]=(usage.metricKnownCalls[key]||0)+(value.metricKnownCalls?.[key]??(Number.isFinite(value[key])?(key==='costUsd'?priced:Math.max(0,calls-unknownTokens)):0));
  }
  if(value.totalTokens==null&&(value.inputTokens!=null||value.outputTokens!=null)){usage.totalTokens=(usage.totalTokens||0)+(value.inputTokens||0)+(value.outputTokens||0);usage.metricKnownCalls.totalTokens+=Math.max(0,calls-unknownTokens)}
 }
 usage.costType=!usage.pricedCalls?'unavailable':usage.unknownCalls?'partial':usage.estimatedCalls?'estimated':'reported';
 return {running,phase:failure?(failure.status||failure.phase):running?'running':turns.every(turn=>['completed','complete','done','success'].includes(turn.status||turn.phase))?'completed':'recorded',
  elapsed:intervals.length?elapsedLabel({startedAt:0,endedAt:duration}):null,elapsedSeconds:intervals.length?duration:null,usage};
}
