const busyStatuses=new Set(['working','running','busy','starting','stopping','retrying']);
const workerStatuses=new Set(['working','running','busy','starting','pending','queued','preparing','retrying']);
export function timestamp(value){
 if(typeof value==='number')return value<1e12?value*1000:value;
 const date=Date.parse(value);return Number.isFinite(date)?date:null;
}
export function formatElapsed(milliseconds){
 const seconds=Math.max(0,Math.floor(milliseconds/1000));
 return seconds<60?`${seconds}s`:`${Math.floor(seconds/60)}m ${String(seconds%60).padStart(2,'0')}s`;
}
const terminalJobs={'job.returned':'completed','job.failed':'error','job.cancelled':'cancelled'};
export function visibleWorkers(workers=[]){
 const rows=new Map(),finished=new Map();
 for(const [index,worker]of workers.entries()){
  const key=worker.callId||worker.call_id||worker.id||`worker-${index}`,previous=rows.get(key);
  if(worker.kind==='job'){const terminal=terminalJobs[worker.event]||(['completed','cancelled','error','failed','stopped','interrupted'].includes(worker.status)?worker.status:null);if(terminal)finished.set(key,terminal)}
  // Keep the child session identity for stop/steer, but a finished job is the
  // authoritative outcome for this call, including after late progress events.
  if(!previous||worker.kind==='session'||previous.kind!=='session')rows.set(key,worker);
 }
 return [...rows].map(([key,worker])=>finished.has(key)?{...worker,status:finished.get(key),phase:finished.get(key)}:worker);
}
/** Derive public execution activity, never model reasoning or tool inputs/results. */
export function liveActivity(session,now=Date.now()){
 if(!session)return null;
 const busy=busyStatuses.has(session.status);
 const workers=visibleWorkers(session.workers||[]).filter(w=>workerStatuses.has(w.status));
 const source=session.activity;
 const active=new Map();
 let lastTool=null;
 if(source?.activeTools){
  for(const tool of source.activeTools)active.set(tool.callId||tool.call_id||tool.tool,{tool:tool.tool||tool.name||'tool',startedAt:tool.startedAt});
 }else if(busy){
  const pending=new Map();
  for(const event of session.runtimeEvents||[]){
   const type=event.type==='runtime.tool'?`tool.${event.phase}`:event.type||event.event;
   const id=event.callId||event.call_id||event.tool_call_id||event.job_id||event.tool;
   const name=event.tool||event.tool_name||event.name;
   if(name&&/^(tool[.:]|job\.)/.test(type||''))lastTool=name;
   if(['tool.pre','tool:pre','tool.start','tool.started'].includes(type))pending.set(`tool:${id}`,{id,tool:name||'tool',startedAt:event.time||event.createdAt});
   if(['tool.post','tool:post','tool.error','tool:error','tool.end','tool.finished'].includes(type))pending.delete(`tool:${id}`);
   if(type==='job.queued')pending.set(`job:${id}`,{id,tool:name||'delegate',startedAt:event.time||event.createdAt});
   if(['job.returned','job.failed','job.cancelled','job.canceled','job.error'].includes(type))pending.delete(`job:${id}`);
   if(['session.closed','generation.detached'].includes(type))pending.clear();
  }
  for(const item of pending.values())active.set(item.id,item);
 }
 if(!busy&&!workers.length)return null;
 const tools=[...active.values()];
 const counts=new Map();for(const tool of tools)counts.set(tool.tool,(counts.get(tool.tool)||0)+1);
 let label,phase;
 if(session.status==='starting'){label='Preparing your Amplifier session…';phase='preparing'}
 else if(session.status==='stopping'){label='Stopping the current work…';phase='stopping'}
 else if(tools.length){label=source?`Running ${tools.length} ${tools.length===1?'tool':'tools'}`:`${tools.length} ${tools.length===1?'tool call':'tool calls'} pending`;phase='tools'}
 else if(workers.length){label=`${workers.length} ${workers.length===1?'worker lane is':'worker lanes are'} active`;phase='workers'}
 else {label=session.streaming?'Writing a response…':'Composing a response…';phase='composing'}
 if(source?.phase)phase=source.phase;
 if(typeof source?.label==='string'&&source.label.trim())label=source.label;
 const lastUser=[...(session.messages||[])].reverse().find(m=>m.role==='user');
 const startedAt=timestamp(source?.startedAt)||timestamp(lastUser?.createdAt)||Math.min(...tools.map(t=>timestamp(t.startedAt)).filter(Boolean));
 const elapsed=Number.isFinite(startedAt)?formatElapsed(now-startedAt):null;
 const toolLabels=[...counts].map(([name,count])=>count>1?`${name} × ${count}`:name);
 if(!lastTool&&source?.lastEvent?.tool)lastTool=source.lastEvent.tool;
 return {phase,label,elapsed,toolLabels,workerCount:workers.length,lastTool:tools.length?null:lastTool};
}
