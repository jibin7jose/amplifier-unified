import {useWorkDetail} from './work-detail';
import {useShellContext} from './shell/runtime';
import {detailOpen,toggleDetail} from './interface-detail';
import {DetailText} from './conversation-detail';
import {hostNow} from './api';
import React,{useEffect,useState} from 'react';
import {ChevronRight,GitBranch,Wrench,MessageCircle,Check,Circle,Square,AlertCircle,Terminal,FileText,FilePenLine,ListChecks} from 'lucide-react';
import {isRunning,elapsedLabel,combinedWork,workLabel} from './timeline-data';
import {workStream} from './work-stream.js';
import {actionContent,actionHeading,cleanSummary} from './execution-content.js';
import {useExecutionField,ToolContent,ModelContent} from './execution-content.jsx';
import './execution-content.css';
import {UsageDetails} from './usage-details.jsx';
import {DelegateStreamMessage,DelegateStreamStatus,DelegateStreamSources} from './delegate-stream.jsx';
import {useToolDetail,ToolDetailStatus} from './tool-detail.jsx';
import {interimOpen} from './interim-replies.js';
import {MetricSummary as Usage,MetricsExport} from './execution-observability.jsx';
import {usageMetrics} from './usage-details.js';
import {selectedMetrics,metricsVisible} from './execution-observability.js';


function Status({status}){return status==='stopped'?<Square aria-label="Stopped"/>:['error','failed','cancelled','interrupted'].includes(status)?<AlertCircle className="a-execution-warning" aria-label={status}/>:['complete','completed','success','done'].includes(status)?<Check className="a-execution-complete" aria-label="Completed"/>:isRunning({phase:status})?<span className="a-execution-dot" aria-label={status}/>:<Circle aria-label={status||'Recorded'}/>}
const stamp=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString():null;
function ExecutionNode({node:sourceNode,depth=0,ancestors=[],tree,act,now,expanded,toggle,detail:level,actor,ownerId,flat=false}){
 const savedDetail=useToolDetail(sourceNode,expanded.has(sourceNode.id)),node=savedDetail.node;
 const shell=useShellContext(),detail=level||shell?.composition?.presentation?.interfaceDetail||shell?.composition?.presentation?.executionDetail||'standard';
 const presentation=shell?.composition?.presentation||{},show=metricsVisible(presentation,'step'),fields=selectedMetrics(presentation);
 const tool=node.kind==='tool',worker=node.kind==='worker',children=flat?[]:tree.children.get(node.id)||[],summary=cleanSummary(node.summary||node.detail);
 const collapsible=tool||node.kind==='llm'||node.kind==='worker'||!!summary||children.length>15,open=!collapsible||expanded.has(node.id);
 const Header=collapsible?'button':'div';
 const input=useExecutionField(node,'input',open&&tool),output=useExecutionField(node,'output',open&&tool),error=useExecutionField(node,'error',open);
 if(depth>20||ancestors.includes(node.id))return null;
 const action=tool?actionContent(node,input.value,output.value):null;
 const heading=tool?actionHeading(node,input.value,output.value,error.value):null;
 const Icon=node.kind==='worker'?GitBranch:node.kind==='llm'?MessageCircle:({command:Terminal,code:Terminal,read:FileText,write:FilePenLine,patch:FilePenLine,tasks:ListChecks,delegate:GitBranch}[heading?.kind]||Wrench);
 const phase=node.workerPhase||node.status||node.phase,elapsed=show&&fields.includes('elapsedSeconds')?elapsedLabel(node,now):null,usage=node.kind==='llm'?node.usage:node.kind==='worker'?node.aggregateUsage:null;
 const label=heading?(heading.title===heading.tool?heading.tool:`${heading.title} · ${heading.tool}`):node.label||node.model||node.kind,model=[node.provider,node.model].filter(Boolean).join(' · ');
 const steps=children;
 const activity=steps.length>0&&<div className="a-execution-children">{steps.map(child=><ExecutionNode key={child.id} node={child} depth={depth+1} ancestors={[...ancestors,node.id]} tree={tree} act={act} now={now} expanded={expanded} toggle={toggle} detail={detail}/>)}</div>;
 return <div className="a-execution-node" data-kind={node.kind} data-node-id={node.id} data-work-entry={flat?node.id:undefined} data-owner-id={ownerId} data-action-kind={heading?.kind}>
  <Header className="a-execution-line a-execution-action-line" {...(collapsible?{type:"button",'data-action':'view.update','data-view-label':[label,heading?.target,model,heading?.detail,phase].filter(Boolean).join(' · '),'aria-expanded':open,onClick:()=>toggle(node.id)}:{})}>
   {collapsible&&<ChevronRight className={`a-execution-chevron ${open?'open':''}`}/>}<Icon/>
   <span className="a-execution-label" title={[label,heading?.target,model,heading?.detail||heading?.preview||summary].filter(Boolean).join(' · ')}><span>{label}{heading?.target&&<> <code>{heading.target}</code></>}{['llm','worker'].includes(node.kind)&&model&&<> · {model}</>}{!heading?.target&&heading?.preview&&<> · {heading.preview}</>}{heading?.kind==='patch'&&(heading.added>0||heading.removed>0)&&<span className="a-execution-counts"><span>+{heading.added}</span><span>−{heading.removed}</span></span>}</span>{heading?.detail&&<small className={heading.failed?'a-execution-failure-hint':''}>{heading.detail}</small>}</span>
   {actor&&<span className="a-work-actor">{actor}</span>}<span className="a-execution-accounting"><span className="a-execution-phase" title={[node.toolCallId&&`Call: ${node.toolCallId}`,stamp(node.startedAt),stamp(node.endedAt)].filter(Boolean).join(" · ")}>{isRunning(node)||['error','failed','cancelled','interrupted','stopped'].includes(phase)?`${phase}${elapsed?' · ':''}`:''}{elapsed}</span><Usage usage={usage} pending={['llm','worker'].includes(node.kind)&&isRunning(node)}/></span><Status status={phase}/>
  </Header>
  {open&&<div className="a-execution-body">
   <ToolDetailStatus detail={savedDetail}/>
   {node.savedObservations?.length>0&&<section className="a-related-saved-records" aria-label="Related saved records"><p className="a-caption">Related saved records. The app cannot confirm which attempt they belong to.</p>{node.savedObservations.map(observation=><ExecutionNode key={observation.id} node={observation} depth={depth+1} ancestors={[...ancestors,node.id]} tree={tree} act={act} now={now} expanded={expanded} toggle={toggle} detail={detail}/>)}</section>}
   {worker&&isRunning({phase:node.workerPhase||node.phase})&&(node.workerId||node.sessionId)&&<button className="a-link a-danger" data-action="worker.stop" onClick={()=>act('worker.stop',{id:node.workerId||node.sessionId})}><Square/>Stop worker</button>}
   {tool?<ToolContent node={node} action={action} input={input} output={output} error={error}/>:node.kind==='llm'?<ModelContent node={node} error={error} now={now} act={act}/>:summary&&<DetailText text={summary} reference={node.summaryDetail||node.detailDetail} automatic markdown/>}
   {node.kind!=='llm'&&<MetricsExport scope="step" record={{...node,phase,calls:worker?usage?.calls:undefined}} metrics={usageMetrics(usage)} observedAt={now}/>}
   {activity}
  </div>}
 </div>;
}
export function TurnTimeline({data,turnId,state,act,embedded=false,automatic=false}){
 const shell=useShellContext(),detail=shell?.composition?.presentation?.interfaceDetail||shell?.composition?.presentation?.executionDetail||'standard';
 const presentation=shell?.composition?.presentation||{},show=metricsVisible(presentation,'turn'),fields=selectedMetrics(presentation);
 const turn=data.turns.find(turn=>turn.id===turnId)||{id:turnId,label:'Execution'};
 const expanded=new Set(state.view?.executionExpanded||[]),summaryId=`turn:${turnId}`;
 const open=embedded||detailOpen(expanded,summaryId,detail),loaded=useWorkDetail(data,turn,open,embedded?automatic:detail==='detailed'&&!expanded.has(summaryId));
 const {entries,tree}=workStream({...data,nodes:loaded.nodes},turnId),nodes=loaded.nodes.filter(node=>node.turnId===turnId),ticking=isRunning(turn)||nodes.some(isRunning);
 const [now,setNow]=useState(()=>hostNow());
 useEffect(()=>{if(!ticking)return;setNow(hostNow());const timer=setInterval(()=>setNow(hostNow()),1000);return()=>clearInterval(timer)},[ticking]);
 if(!tree.roots.length&&!turn.startedAt&&!turn.detailRevision)return null;
 const toggle=id=>{const defaulted=id===summaryId||id.startsWith('activity:');const next=defaulted?toggleDetail(expanded,id,detail):new Set(expanded);if(!defaulted){next.has(id)?next.delete(id):next.add(id)}act('view.update',{patch:{executionExpanded:[...next]}})};
 const tools=turn.nodeCounts?.tools??nodes.filter(node=>node.kind==='tool').length,models=turn.nodeCounts?.models??nodes.filter(node=>node.kind==='llm').length;
 const observation=turn.observationWindow===true,partial=observation||turn.partialWindow===true;
 const elapsed=show&&fields.includes('elapsedSeconds')?elapsedLabel(turn,now):null,label=observation?'Recent saved work':workLabel(turn.status||turn.phase,elapsed,isRunning(turn));
 // The host already owns execution timing. Keep automatic interface reports
 // stable while the visible elapsed label ticks, retaining real outcome changes.
 const reportLabel=isRunning(turn)&&!observation?workLabel(turn.status||turn.phase,null,true):label;
 return <section id={'work-'+turnId} ref={loaded.element} className="a-execution-turn a-execution-content" data-part="execution" data-turn-id={turn.originalTurnId||turnId} data-group-id={turnId} aria-label="Execution details">
  {!embedded&&<button type="button" className="a-execution-line a-execution-turn-line" data-action="view.update" data-view-label={`${reportLabel} · expand work details`} aria-expanded={open} onClick={()=>toggle(summaryId)}><ChevronRight className={`a-execution-chevron ${open?'open':''}`}/><span className="a-execution-label">{label}</span>{!partial&&<><Usage usage={turn.aggregateUsage||turn.usage} scope="turn" pending={isRunning(turn)}/>{show&&fields.includes('calls')&&<span className="a-execution-call-counts" data-metrics-selected={Array.isArray(presentation.executionMetrics)}>{tools} {tools===1?'tool call':'tool calls'} · {models} {models===1?'model call':'model calls'}</span>}</>}<Status status={turn.status||turn.phase}/></button>}
  {open&&observation&&<p className="a-caption">Recorded events from this page. Full conversation totals and earlier activity are not included.</p>}
  {open&&!partial&&!embedded&&(turn.aggregateUsage||turn.usage)?.calls>0&&<UsageDetails usage={turn.aggregateUsage||turn.usage} scope="turn" record={{sessionId:state.selectedSessionId,...turn,turnId:turn.originalTurnId||turnId,calls:(turn.aggregateUsage||turn.usage)?.calls}} observedAt={now} label="Work usage" costLabel="Work cost (USD)"/>}
  {open&&loaded.initialLoading&&<p className="a-caption" role="status">Loading work details…</p>}
  {open&&loaded.error&&<p role="alert">{loaded.error} <button className="a-link" onClick={loaded.reload}>Retry loading work</button></p>}
  {open&&loaded.before&&<button className="a-link" disabled={loaded.busy} onClick={loaded.earlier}>Load earlier steps</button>}
  {open&&entries.length>0&&<DelegateStreamSources entries={entries}><div className="a-execution-roots a-work-stream" data-part="work-stream">{entries.map(entry=>
   entry.kind==='exchange'?<DelegateStreamMessage key={entry.id} entry={entry} expanded={expanded} toggle={toggle}/>:
   entry.kind==='worker-status'?<DelegateStreamStatus key={entry.id} entry={entry} expanded={expanded} toggle={toggle} act={act}/>:
   <ExecutionNode key={entry.id} node={entry.node} tree={tree} act={act} now={now} expanded={expanded} toggle={toggle} actor={entry.actor} ownerId={entry.owner?.ownerId||entry.owner?.id||entry.node.sessionId} flat/>
  )}</div></DelegateStreamSources>}
 </section>;
}


export function WorkTimeline({items,data,state,act,row,renderMessage}){
 const shell=useShellContext(),detail=shell?.composition?.presentation?.interfaceDetail||shell?.composition?.presentation?.executionDetail||'standard';
 const presentation=shell?.composition?.presentation||{},show=metricsVisible(presentation,'turn'),fields=selectedMetrics(presentation);
 const turns=items.filter(item=>item.turnId).map(item=>data.turns.find(turn=>turn.id===item.turnId)).filter(Boolean);
 const firstTurn=items.find(item=>item.turnId)?.turnId;
 const expanded=new Set(state.view?.executionExpanded||[]),summaryId=row?.summaryId||(row?.interim?row.id:`work:${firstTurn}`);
 const open=row?.interim?interimOpen(expanded,row):detailOpen(expanded,summaryId,detail),ticking=turns.some(isRunning);
 const [now,setNow]=useState(()=>hostNow());
 useEffect(()=>{if(!ticking)return;setNow(hostNow());const timer=setInterval(()=>setNow(hostNow()),1000);return()=>clearInterval(timer)},[ticking]);
 if(items.length===1&&!row?.interim&&!row?.voiceWork)return <TurnTimeline data={data} turnId={firstTurn} state={state} act={act}/>;
 const observation=turns.some(turn=>turn.observationWindow===true),partial=turns.some(turn=>turn.observationWindow||turn.partialWindow);
 const summary=turns.length?combinedWork(turns,now):{phase:'recorded',running:false,usage:{calls:0}},label=observation?'Saved work pages':workLabel(summary.phase,show&&fields.includes('elapsedSeconds')?summary.elapsed:null,summary.running);
 const reportLabel=summary.running&&!observation?workLabel(summary.phase,null,true):label;
 const updates=items.filter(item=>item.message).length;
 const toggle=()=>{
  const next=row?.interim?new Set(expanded):toggleDetail(expanded,summaryId,detail);
  if(row?.interim){
   next.delete(summaryId);next.delete('closed:'+summaryId);next.add(open?'closed:'+summaryId:summaryId);
   for(const item of items)if(item.message){const key='interim-message:'+item.message.id;open?next.delete(key):next.add(key)}
  }
  act('view.update',{patch:{executionExpanded:[...next]}});
 };
 return <section className="a-execution-turn a-execution-content a-work-group" data-part="execution-group" data-group-id={row?.voiceWork?firstTurn||row.id:undefined} data-voice-work-group={row?.voiceWork||undefined} data-work-input-id={row?.voiceInputIds?.length===1?row.voiceInputIds[0]:undefined} data-interim-group={row?.interim?row.id:undefined} aria-label="Work between messages">
  <button type="button" className="a-execution-line a-execution-turn-line" data-action="view.update" data-view-label={`${reportLabel}${updates>0?` · ${updates} ${updates===1?'update':'updates'}`:''} · expand work details`} aria-expanded={open} onClick={toggle}>
   <ChevronRight className={`a-execution-chevron ${open?'open':''}`}/><span className="a-execution-label">{label}{updates>0&&` · ${updates} ${updates===1?'update':'updates'}`}</span>{!partial&&<Usage usage={summary.usage} scope="turn" pending={summary.running}/>}<Status status={summary.phase}/>
  </button>
  {open&&<div className="a-work-group-items">{!partial&&summary.usage.calls>0&&<UsageDetails usage={summary.usage} scope="turn" record={{id:summaryId,sessionId:state.selectedSessionId,phase:summary.phase,elapsedSeconds:summary.elapsedSeconds,calls:summary.usage.calls}} observedAt={now} label="Work usage" costLabel="Work cost (USD)"/>}{items.map(item=>item.message?<React.Fragment key={item.message.id}>{renderMessage?.(item.message)}</React.Fragment>:<TurnTimeline key={item.turnId} data={data} turnId={item.turnId} state={state} act={act} embedded automatic={detail==='detailed'&&!expanded.has(summaryId)}/>)}</div>}
 </section>;
}
