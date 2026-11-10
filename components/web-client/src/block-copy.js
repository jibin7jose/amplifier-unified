import React,{useEffect,useId,useRef,useState} from 'react';
import {Copy,Check,LoaderCircle,AlertCircle} from 'lucide-react';
import {writeClipboardText,clipboardNotice} from './clipboard-copy.js';

export function CopyControl({text,label='Copy block',identity='',unavailable='',onResult,compact=true}){
 const id=useId(),scope=useRef(null),mounted=useRef(true),[result,setResult]=useState(null);
 if(!scope.current||scope.current.text!==text||scope.current.identity!==identity)scope.current={text,identity,busy:false};
 const current=scope.current,shown=result?.scope===current?result:null;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 async function copy(){
  if(current.busy||unavailable)return;
  current.busy=true;setResult({scope:current,message:'Copying…',pending:true});
  try{
   // Capture and dispatch this version now, never read a later prop after await.
   await writeClipboardText(current.text);
   if(mounted.current&&scope.current===current){setResult({scope:current,message:'Copied.',pending:false});onResult?.('ready','Copied canvas source.')}
  }catch(error){
   if(mounted.current&&scope.current===current){setResult({scope:current,message:clipboardNotice(error),pending:false});onResult?.('error',clipboardNotice(error))}
  }finally{current.busy=false}
 }
 return React.createElement('div',{className:'a-block-copy-control'+(compact?' is-compact':''),'data-feedback':unavailable?'error':shown?.pending?'pending':shown?.message==='Copied.'?'copied':shown?'error':undefined},
  React.createElement('button',{type:'button',className:compact?'a-icon':'a-soft','data-tooltip':'off',onClick:copy,disabled:!!unavailable||!!shown?.pending,'aria-label':label,'aria-describedby':id,'aria-busy':!!shown?.pending},compact?React.createElement(shown?.pending?LoaderCircle:shown?.message==='Copied.'?Check:shown?AlertCircle:Copy,{'aria-hidden':true,className:shown?.pending?'a-progress-spinner':undefined}):shown?.pending?'Copying…':label),
  React.createElement('span',{id,role:'status','aria-live':'polite','aria-atomic':true,className:'a-block-copy-notice'},unavailable||shown?.message||''));
}

export function BlockCopy({text,label,identity,unavailable,children,compact=true}){
 return React.createElement('div',{className:'a-block-copy'+(compact?' a-copy-frame':''),'data-copy-block':true},
  React.createElement(CopyControl,{text,label,identity,unavailable,compact}),children);
}

// Keep every original line ending. Positions refer to the selected source,
// not the normalized mdast value or the rendered DOM.
function lines(raw){
 return [...raw.matchAll(/([^\r\n]*)(\r\n|\n|\r|$)/g)].filter(match=>match[0]).map(match=>({body:match[1],eol:match[2],offset:match.index}));
}
const lineStart=(source,offset)=>Math.max(source.lastIndexOf('\n',offset-1),source.lastIndexOf('\r',offset-1))+1;
const columns=(text,column)=>{for(const char of text)column+=char==='\t'?4-column%4:1;return column};
function whitespace({body,column}){
 const prefix=/^[ \t]*/.exec(body)[0];
 return columns(prefix,column)-column;
}
function indent(state,count){
 let {body,column}=state,index=0,removed=0;
 while(index<body.length&&removed<count&&/[ \t]/.test(body[index])){
  const width=body[index]==='\t'?4-column%4:1;
  const used=Math.min(width,count-removed);
  index++;column+=used;removed+=used;
  // A tab spanning the container boundary has structural and content columns.
  // Retain the content columns; untouched content tabs stay byte-for-byte.
  if(used<width)return {body:' '.repeat(width-used)+body.slice(index),column,removed};
 }
 return {body:body.slice(index),column,removed};
}
function unquote(state){
 const prefix=/^[ \t]*>/.exec(state.body);
 if(!prefix||columns(prefix[0].slice(0,-1),state.column)-state.column>3)return state;
 const next={body:state.body.slice(prefix[0].length),column:columns(prefix[0],state.column)};
 return indent(next,1);
}
function stripContainers(body,offset,containers){
 let state={body,column:0};
 for(const container of containers){
  if(container.type==='blockquote')state=unquote(state);
  else if(offset===container.offset){
   state={body:state.body.slice(container.marker.length),column:columns(container.marker,state.column)};
   state=indent(state,container.padding);
  }else{
   const next=indent(state,container.width);
   // CommonMark allows blank and lazy paragraph continuations without the
   // item's indentation. Do not eat their inner Markdown as a guessed prefix.
   if(next.removed===container.width||/^[ \t]*$/.test(state.body))state=next;
  }
 }
 return state;
}
function sourceContainers(source,ancestors){
 const containers=[];
 for(const parent of ancestors){
  if(parent.type==='blockquote'){containers.push({type:parent.type});continue}
  if(parent.type!=='listItem')continue;
  const start=parent.position?.start?.offset;
  if(!Number.isInteger(start))return null;
  const offset=lineStart(source,start),body=source.slice(offset).split(/\r\n|\n|\r/,1)[0];
  const state=stripContainers(body,offset,containers);
  const marker=/^([ \t]*)(?:[*+-]|\d{1,9}[.)])(?=[ \t]|$)/.exec(state.body);
  if(!marker||columns(marker[1],state.column)-state.column>3)return null;
  const column=columns(marker[0],state.column),rest=state.body.slice(marker[0].length);
  const spacing=whitespace({body:rest,column});
  // W + N, with one padding column for an empty item or >4-column spacing.
  const padding=/[^ \t]/.test(rest)&&spacing>=1&&spacing<=4?spacing:1;
  containers.push({type:parent.type,offset,marker:marker[0],padding,width:column-state.column+padding});
 }
 return containers;
}
export function blockPayload(source,node,ancestors=[]){
 const start=node.position?.start?.offset,end=node.position?.end?.offset;
 if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<start||end>source.length)return {unavailable:'Exact block source is unavailable.'};
 const containers=sourceContainers(source,ancestors);
 if(!containers)return {unavailable:'Exact block container source is unavailable.'};
 const beginning=lineStart(source,start);
 const after=/^(?:\r\n|\n|\r)/.exec(source.slice(end,end+2))?.[0]||'';
 const selected=lines(source.slice(beginning,end)+after).map(line=>({...line,...stripContainers(line.body,beginning+line.offset,containers)}));
 if(node.type==='blockquote'){
  // Remove this container's marker only; nested > syntax belongs to its
  // inner Markdown. A nested block's own control removes its ancestors too.
  return {text:selected.map(line=>unquote(line).body+line.eol).join('')};
 }
 if(node.type==='table')return {text:selected.map(line=>line.body+line.eol).join('')};
 if(node.type!=='code')return {unavailable:'This is not a copyable block.'};
 const opening=/^ {0,3}(`{3,}|~{3,})/.exec(selected[0]?.body||'');
 if(opening){
  const fence=opening[1],last=selected.at(-1)?.body||'';
  const close=new RegExp('^ {0,3}'+fence[0]+'{'+fence.length+',}[ \\t]*$');
  const closed=selected.length>1&&close.test(last);
  return {text:selected.slice(1,closed?-1:undefined).map(line=>line.body+line.eol).join('')};
 }
 const body=selected.map(line=>{const next=indent(line,4);return {body:next.removed<4&&/[^ \t]/.test(line.body)?null:next.body,eol:line.eol}});
 if(body.some(line=>line.body===null))return {unavailable:'Exact indented block source is unavailable. Select the block text to copy it.'};
 return {text:body.map(line=>line.body+line.eol).join('')};
}

// A per-render map keeps payloads out of HTML attributes and preserves the
// existing Canvas source-span plugin unchanged.
export function copySourceBlocks(source,payloads){
 return ()=>tree=>{
  const visit=(node,ancestors)=>{
   if(['code','blockquote','table'].includes(node.type))payloads.set(node.position?.start?.offset,blockPayload(source,node,ancestors));
   for(const child of node.children||[])visit(child,[...ancestors,node]);
  };
  visit(tree,[]);
 };
}