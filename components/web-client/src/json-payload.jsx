import React,{useMemo,useState} from 'react';
import './json-payload.css';

const PAGE=50,PREVIEW=240,TEXT_PAGE=4000;
function JsonValue({value,label}){
 const [open,setOpen]=useState(false),[shown,setShown]=useState(PAGE),[characters,setCharacters]=useState(PREVIEW);
 const container=value!==null&&typeof value==='object',array=Array.isArray(value);
 const keys=useMemo(()=>container&&!array?Object.keys(value):[],[value,container,array]);
 if(!container){
  const long=typeof value==='string'&&value.length>characters;
  const visible=typeof value==='string'?JSON.stringify(value.slice(0,characters)):JSON.stringify(value);
  return <div className="a-json-value"><span className="a-json-key">{label}: </span><code>{visible}{long?'…':''}</code>{long&&<button type="button" className="a-link" onClick={()=>setCharacters(n=>n+TEXT_PAGE)}>Show more text ({(value.length-characters).toLocaleString()} characters left)</button>}</div>;
 }
 const count=array?value.length:keys.length;
 return <div className="a-json-branch">
  <button type="button" className="a-json-toggle" aria-expanded={open} onClick={()=>setOpen(!open)}><span aria-hidden="true">{open?'▾':'▸'}</span> <span className="a-json-key">{label}</span> <span className="a-json-count">{array?'Array':'Object'} · {count.toLocaleString()} {array?(count===1?'item':'items'):(count===1?'field':'fields')}</span></button>
  {open&&<div className="a-json-children">
   {Array.from({length:Math.min(shown,count)},(_,index)=>{const key=array?index:keys[index];return <JsonValue key={key} label={array?`[${key}]`:key} value={value[key]}/>})}
   {count===0&&<code>{array?'[]':'{}'}</code>}
   {shown<count&&<button type="button" className="a-link" onClick={()=>setShown(n=>n+PAGE)}>Show next {Math.min(PAGE,count-shown)} of {count-shown} remaining</button>}
  </div>}
 </div>;
}

export function JsonPayload({text,label}){
 const parsed=useMemo(()=>{try{return {value:JSON.parse(text)}}catch{return {invalid:true}}},[text]);
 return <div className="a-json-viewer" role="group" aria-label={`${label} payload`}>
  {parsed.invalid&&<small>This recording is text rather than complete JSON.</small>}
  <JsonValue label={label} value={parsed.invalid?text:parsed.value}/>
 </div>;
}
