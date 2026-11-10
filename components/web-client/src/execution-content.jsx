import React,{useEffect,useMemo,useState} from 'react';
import {Check,Circle,Copy,LoaderCircle} from 'lucide-react';
import {request,download,clientUrl} from './api';
import {providerRecordExport} from './provider-record-export';
import {readDetail} from './detail-read';
import {UsageDetails} from './usage-details.jsx';
import {useShellContext} from './shell/runtime';
import {selectedMetrics,metricsVisible} from './execution-observability.js';
import {detailLinks,elapsedLabel,isRunning} from './timeline-data';
import {textValue,record,contentUnavailable} from './execution-content.js';
import {readablePayload} from './tool-input-preview.js';
import {settingsPatch} from './settings-navigation';
import {WorkNavigationContext} from './work-navigation';
import {JsonPayload} from './json-payload.jsx';

export function useExecutionField(node,field,open){
 const reference=node[field+'Detail'],key=JSON.stringify([node.id,field,reference||node[field]]);
 const [loaded,setLoaded]=useState(null),[failure,setFailure]=useState(null),[attempt,setAttempt]=useState(0);
 const value=loaded?.key===key?loaded.value:node[field],error=failure?.key===key?failure.message:'';
 useEffect(()=>{
  if(!open||!reference||loaded?.key===key)return;
  const controller=new AbortController();setFailure(null);
  readDetail({...reference,complete:'true'},request,controller.signal).then(value=>{if(!controller.signal.aborted)setLoaded({key,value})})
   .catch(e=>{if(!controller.signal.aborted)setFailure({key,message:e.message})});
  return()=>controller.abort();
 },[open,key,attempt,loaded?.key]);
 return {value,error,incomplete:!!reference&&loaded?.key!==key,loading:!!reference&&loaded?.key!==key&&!error,retry:()=>setAttempt(n=>n+1)};
}
function CopyText({text,label,disabled=false}){
 const [status,setStatus]=useState('');
 useEffect(()=>setStatus(''),[text]);
 return <><button type="button" className="a-link" disabled={disabled} aria-label={`Copy ${label.toLowerCase()}`} onClick={async()=>{try{await navigator.clipboard.writeText(text);setStatus('Copied')}catch{setStatus('Select the text to copy it manually.')}}}><Copy/>{status==='Copied'?'Copied':'Copy'}</button>{status&&<span className="a-caption" role="status">{status}</span>}</>;
}
export function ExecutionText({text,partial=false}){
 const [full,setFull]=useState(false),raw=textValue(text),value=useMemo(()=>partial?raw:readablePayload(raw),[raw,partial]),lines=value.split('\n'),long=lines.length>15||value.length>3000,preview=long?lines.slice(0,10).join('\n').slice(0,2000):value;
 useEffect(()=>setFull(false),[value]);
 return <><pre><code>{full?value:preview}{long&&!full?'\n…':''}</code></pre>{long&&!full&&<p className="a-caption a-execution-preview-note">Showing a preview.</p>}{long&&<button type="button" className="a-link a-execution-more" aria-expanded={full} onClick={()=>setFull(!full)}>{full?'Show less':lines.length>15?`Show all ${lines.length} lines`:`Show all ${value.length.toLocaleString()} characters`}</button>}</>;
}
export function ExecutionBlock({label,text,meta,children,copy=true,loading=false}){
 if(text==null&&!children)return null;
 return <section className="a-execution-payload"><header><span>{label}{loading?' · Preview':''}</span>{meta!=null&&<span>{textValue(meta)}</span>}{copy&&<CopyText text={textValue(text)} label={label} disabled={loading}/>}</header>{children||<ExecutionText text={text} partial={loading}/>}</section>;
}
export function FieldStatus({label,field}){
 return field.error?<p role="alert" className="a-execution-load-error">{field.error} <button type="button" className="a-link" onClick={field.retry}>Retry {label.toLowerCase()}</button></p>:field.loading?<small role="status">Loading saved {label.toLowerCase()}… Any text above is a preview.</small>:null;
}
function Patch({action}){
 const [full,setFull]=useState(false),rows=action.rows.filter(row=>row.type!=='header'||!/^\*\*\* (Begin|End) Patch$/.test(row.text));
 return <ExecutionBlock label={action.path||'Patch'} text={action.diff} meta={action.done?'Applied':action.running?'In progress':'Requested changes'}>
  <div className="a-execution-diff" aria-label="File changes">{(full||rows.length<=15?rows:rows.slice(0,10)).map((row,index)=>row.type==='header'?<div key={index} className="a-execution-diff-header">{row.file||row.text}</div>:<div key={index} className={`a-execution-diff-row ${row.type}`}><span className="a-execution-line-number">{row.old}</span><span className="a-execution-line-number">{row.new}</span><span>{row.type==='add'?'+':row.type==='remove'?'−':' '}</span><code>{row.text}</code></div>)}</div>
  {rows.length>15&&<button type="button" className="a-link a-execution-more" aria-expanded={full} onClick={()=>setFull(!full)}>{full?'Show less':`Show all ${rows.length} patch lines`}</button>}
 </ExecutionBlock>;
}
export function ToolContent({node,action,input,output,error}){
 const a=action,body=textValue(a.output),streams=typeof a.out.stdout==='string'||typeof a.out.stderr==='string',hasOutput=output.value!=null;
 let content;
 if(a.kind==='command'||a.kind==='code')content=<><ExecutionBlock label={a.kind==='code'?'Code':'Command'} text={a.command||a.code} meta={a.args.working_directory||a.args.cwd} loading={input.incomplete}/>{hasOutput&&(streams?<><ExecutionBlock label="Output" text={a.out.stdout||'No output'} meta={a.exit!=null?`Exit ${a.exit}`:null} loading={output.incomplete}/>{a.out.stderr&&<ExecutionBlock label="Standard error" text={a.out.stderr} loading={output.incomplete}/>}</>:<ExecutionBlock label="Output" text={body||'No output'} loading={output.incomplete}/>)}</>;
 else if(a.kind==='patch')content=<><Patch action={a}/>{hasOutput&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else if(a.kind==='read'||a.kind==='write')content=<><ExecutionBlock label={a.kind==='read'?'File content':a.done?'Written content':'Requested content'} text={a.kind==='read'&&a.failed?null:a.content||null} loading={a.kind==='read'?output.incomplete:input.incomplete}/>{hasOutput&&(!a.content||a.failed||a.kind==='write')&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else if(a.kind==='tasks')content=<><section className="a-execution-payload"><header><span>{a.done?'Task list':'Requested task list'}</span><span>{a.preview}</span></header><ul className="a-execution-checklist">{a.tasks.map((task,index)=>{const status=typeof task?.status==='string'?task.status:'unknown',Icon=status==='completed'?Check:status==='in_progress'?LoaderCircle:Circle;return <li key={index} data-status={status}><Icon aria-label={status}/><span>{textValue(task?.content||task?.activeForm||task?.title||task)}</span><small>{status.replaceAll('_',' ')}</small></li>})}</ul></section>{hasOutput&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else if(a.kind==='delegate')content=<><ExecutionBlock label="Task sent" text={a.task}/>{hasOutput&&<ExecutionBlock label="Result" text={body} loading={output.incomplete}/>}</>;
 else content=<><ExecutionBlock label={a.kind==='app'?a.target:'Input'} text={input.value} loading={input.incomplete}/><ExecutionBlock label="Result" text={output.value} loading={output.incomplete}/></>;
 const used={command:['command','cmd','working_directory','cwd'],code:['code','working_directory','cwd'],patch:['patch','diff','file_path','path','filename','old_string','old_text','new_string','new_text'],read:['file_path','path','filename'],write:['file_path','path','filename','content','text'],tasks:['todos'],delegate:['agent','name','description','instruction','instructions','prompt','task']}[a.kind];
 const extra=used?Object.fromEntries(Object.entries(a.args).filter(([key])=>!used.includes(key))):{};
 // Typed blocks consume individual fields. Preserve every remaining result
 // field once, including envelope metadata such as artifact URLs.
 const consumed=['command','code'].includes(a.kind)&&streams?['stdout','stderr','returncode','exit_code','exitCode']:a.kind==='read'&&a.content&&!a.failed?[typeof a.out.content==='string'&&a.out.content?'content':'text']:null;
 const remainder=consumed?Object.fromEntries(Object.entries(a.out).filter(([key])=>!consumed.includes(key))):{};
 const envelope=Object.hasOwn(record(a.result),'output')?Object.fromEntries(Object.entries(a.result).filter(([key])=>!['output','success','error'].includes(key))):{};
 const details={...envelope,...remainder};
 return <>{content}{Object.keys(extra).length>0&&<ExecutionBlock label="Arguments" text={textValue(extra)} loading={input.incomplete}/>}<ExecutionBlock label="Result details" text={used&&Object.keys(details).length?textValue(details):null}/><ExecutionBlock label="Error" text={error.value??(a.kind!=='generic'&&a.kind!=='app'&&Object.hasOwn(record(a.result),'output')?a.result.error:null)} loading={error.incomplete}/>{[['Input',input],['Result',output],['Error',error]].map(([label,field])=><FieldStatus key={label} label={label} field={field}/>)}
  {detailLinks(output.value).map(url=><a key={url} className="a-execution-result-link" href={url} target="_blank" rel="noopener noreferrer">{url}</a>)}
  {input.value==null&&output.value==null&&error.value==null&&!input.loading&&!output.loading&&!error.loading&&<small>{a.running?'Waiting for saved action details…':contentUnavailable(node)}</small>}
 </>;
}

const requestLabels={message_count:'Messages',tool_count:'Tools',has_instructions:'Instructions',has_system:'System message',reasoning_enabled:'Reasoning enabled',thinking_enabled:'Thinking enabled',thinking_budget:'Thinking budget',background_mode:'Background mode',stream:'Streaming',max_tokens:'Maximum tokens',max_output_tokens:'Maximum output tokens',temperature:'Temperature',top_p:'Top P',parallel_tool_calls:'Parallel tool calls',tool_choice:'Tool choice',purpose:'Purpose',reasoning_effort:'Reasoning effort'};
export function ModelContent({node,error,now,act}){
 const navigation=React.useContext(WorkNavigationContext);
 const presentation=useShellContext()?.composition?.presentation||{},showElapsed=metricsVisible(presentation,'step')&&selectedMetrics(presentation).includes('elapsedSeconds');
 const formatTime=value=>Number.isFinite(value)?new Date(value*1000).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'long'}):null;
 const facts=[['Provider',node.provider],['Model',node.model],['Status',node.status||node.phase],['Recorded',node.observationWindow?formatTime(node.createdAt):null],['Started',formatTime(node.startedAt)],['Ended',formatTime(node.endedAt)],['Elapsed',showElapsed?elapsedLabel(node,now):null],...Object.entries(node.requestInfo||{}).map(([key,value])=>[requestLabels[key]||key,typeof value==='boolean'?(value?'Yes':'No'):value])].filter(([,value])=>value!=null&&value!=='');

 return <><dl className="a-execution-model-facts">{facts.map(([label,value])=><React.Fragment key={label}><dt>{label}</dt><dd>{String(value)}</dd></React.Fragment>)}</dl>
  <UsageDetails usage={node.usage} scope="step" record={{...node,calls:1}} observedAt={now} label="Model call usage" costLabel="Call cost (USD)"/>
  <ExecutionBlock label="Error" text={error.value} loading={error.incomplete}/><FieldStatus label="Error" field={error}/>
  <small>Provider recordings may contain summaries instead of full payloads.</small>
  {node.recordingsGroup&&navigation?.openRecordings&&(!node.requestDetail||!node.responseDetail)&&<p className="a-caption">Saved recordings may be listed separately. <button type="button" className="a-link" data-action="view.update" onClick={()=>navigation.openRecordings(node.recordingsGroup)}>Show saved recordings</button></p>}
  {['request','response'].map(field=><ModelPayload key={field+':'+(node[field+'Detail']?.digest||'')} node={node} field={field} act={act}/>)}
 </>;
}

function ModelPayload({node,field,act}){
 // This local disclosure deliberately ignores persisted detail preferences:
 // even small payloads and restored expanded calls require an explicit click.
 const [open,setOpen]=useState(false),loaded=useExecutionField(node,field,open);
 const reference=node[field+'Detail'],capture=node[field+'Capture'],recording=node[field+'Recording'],label=field[0].toUpperCase()+field.slice(1);
 if(!reference)return <p className="a-caption">{isRunning(node)?`Waiting for a linked ${field} recording…`:node.observationWindow?`This event has no ${field} payload.`:`This call’s ${field} recording isn’t available here.`} {!isRunning(node)&&field==='request'&&!node.observationWindow&&<>Recording settings save automatically and apply when a supported runtime next prepares. They cannot create recordings for past calls. {act&&<button type="button" className="a-link" data-action="view.update" onClick={()=>act('view.update',{patch:settingsPatch('diagnostics')})}>Open recording settings</button>}</>}</p>;
 return <section className="a-model-payload" aria-label={`Recorded ${field}`}>
  {recording&&<><a className="a-link" download href={clientUrl('/api/conversation/provider-recording?'+new URLSearchParams({sessionId:reference.sessionId,id:reference.id,field,digest:recording.sha256,...(reference.windowKey?{windowKey:reference.windowKey}:{})}))}>Download {recording.truncated?'partial':'full'} recorded {field} (JSON)</a><small>Redacted provider event · {(recording.bytes/1024/1024).toFixed(1)} MB{recording.truncated?' · Capture limit reached; some content is omitted.':''}</small></>}
  <button type="button" className="a-link a-execution-request-toggle" aria-expanded={open} onClick={()=>setOpen(!open)}>{open?'Hide':'View'} {field}{!open&&Number.isFinite(reference.length)&&` · ${reference.length.toLocaleString()} characters`}</button>
  {open&&<>
   {capture?.redacted&&<small>Recognizable credentials redacted.</small>}
   {capture?.truncated&&<small>{recording?'This is a bounded preview. Use the recorded download above for the larger payload.':`This record reached the capture limit; part of the ${field} is omitted.`}</small>}
   <FieldStatus label={label} field={loaded}/>
   {!loaded.incomplete&&loaded.value!=null&&<><button type="button" className="a-link" onClick={()=>{const record=providerRecordExport(node,field,loaded.value);download(record.filename,record.content)}}>Download recorded {field} (JSON)</button><ExecutionBlock label={`Raw ${field}`} text={loaded.value}><JsonPayload text={loaded.value} label={label}/></ExecutionBlock></>}
  </>}
 </section>;
}
