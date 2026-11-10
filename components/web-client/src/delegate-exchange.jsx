import React,{useState} from 'react';
import {Copy} from 'lucide-react';
import {Markdown} from './markdown';
import {useExecutionField,ExecutionBlock,FieldStatus} from './execution-content.jsx';
import {readDetail} from './detail-read';
import {request} from './api';

export function DelegateMessage({node,field,label,direction,expanded,toggle,partial=false}){
 const id=`exchange:${node.id}:${field}`,full=expanded.has(id);
 const loaded=useExecutionField(node,field,full),[copying,setCopying]=useState(false),[copied,setCopied]=useState('');
 const value=loaded.value,reference=node[field+'Detail'];
 if(value==null&&!reference)return null;
 const long=!!reference||String(value).length>1200||String(value).split('\n').length>10;
 const preview=String(value??'').split('\n').slice(0,10).join('\n').slice(0,1200);
 async function copy(){
  setCopying(true);setCopied('');
  try{const text=loaded.incomplete?await readDetail({...reference,complete:'true'},request):String(value??'');await navigator.clipboard.writeText(text);setCopied('Copied')}
  catch(e){setCopied(`Copy failed: ${e.message}`)}finally{setCopying(false)}
 }
 return <section className="a-execution-payload a-delegate-message" aria-label={label}>
  <header><span>{label}</span><span>{direction}</span><button type="button" className="a-link" aria-label={`Copy ${partial?'recorded portion of ':''}${label.toLowerCase()}`} disabled={copying} onClick={copy}><Copy/>{copying?'Copying…':'Copy'}</button></header>
  <div className="a-delegate-markdown"><Markdown text={full?String(value??''):preview} userContent/></div>
  {partial&&<p className="a-caption">Partial recording; only the retained portion is available.</p>}
  {long&&<button type="button" className="a-link a-execution-more" aria-expanded={full} onClick={()=>toggle(id)}>{full?'Show less':`Show full ${field==='delegateRequest'?'request':'response'}`}</button>}
  {full&&<FieldStatus label={label} field={loaded}/>}
  {copied&&<p className="a-caption" role="status">{copied}</p>}
 </section>;
}

export function RawExchange({node}){
 const input=useExecutionField(node,'input',true),output=useExecutionField(node,'output',true),error=useExecutionField(node,'error',true);
 return <>{[['Input',input],['Result',output],['Error',error]].map(([label,field])=><React.Fragment key={label}><ExecutionBlock label={label} text={field.value} loading={field.incomplete}/><FieldStatus label={label} field={field}/></React.Fragment>)}</>;
}
