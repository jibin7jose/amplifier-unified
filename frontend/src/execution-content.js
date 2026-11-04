import {boundedPreview,retainedInputPreview} from './tool-input-preview.js';
// Interpret only retained public tool fields. Unknown shapes stay inspectable.
export const textValue=value=>typeof value==='string'?value:value==null?'':JSON.stringify(value,null,2);
export function parsed(value){if(typeof value!=='string')return value;try{return JSON.parse(value)}catch{return value}}
export const record=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
export function resultValue(value){const row=record(value);return Object.hasOwn(row,'output')?row.output:value}
export function cleanSummary(value){
 const text=typeof value==='string'?value:'';
 return text.replace(/^Tool completed\. Expand any delegated actions below for their progress and results\.\s*/,'');
}
const first=(row,keys)=>keys.map(key=>row[key]).find(value=>typeof value==='string'&&value.length)||'';
const concise=value=>textValue(value).replace(/\s+/g,' ').slice(0,180);
export function actionContent(node,inputText=node.input,outputText=node.output){
 const input=parsed(inputText),args=record(input),result=parsed(outputText),output=resultValue(result),out=record(output);
 const tool=String(node.tool||node.label||'Tool'),name=tool.toLowerCase().replace(/.*[.:/]/,''),phase=node.status||node.phase;
 const running=['running','working','starting','pending','queued','retrying'].includes(phase);
 const failed=['error','failed','cancelled','interrupted','stopped'].includes(phase)||record(result).success===false;
 const done=['completed','complete','success','done'].includes(phase)&&!failed;
 const path=first(args,['file_path','path','filename']);
 const command=first(args,['command','cmd']),code=first(args,['code']);
 const patch=first(args,['patch','diff'])||first(out,['diff','patch'])||(/apply_patch/.test(name)&&typeof input==='string'?input:'');
 const before=first(args,['old_string','old_text']),after=first(args,['new_string','new_text']);
 const tasks=Array.isArray(args.todos)?args.todos:Array.isArray(out.todos)?out.todos:null;
 const summary=cleanSummary(node.summary||node.detail),fallback=summary.replace(new RegExp(`^(?:Completed|Running|Failed) ${tool.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}(?: · )?`),'');
 const exit=out.returncode??out.exit_code??out.exitCode;
 const verb=(past,active,other)=>done?past:running?active:other;
 const base={tool,input,args,result,output,out,phase,running,failed,done,path,exit,preview:concise(fallback)};
 if(command)return {...base,kind:'command',title:verb('Ran','Running','Run'),target:command,command,preview:typeof out.stdout==='string'?concise(out.stdout.trim().split('\n').filter(Boolean).at(-1))+(exit!=null?` · exit ${exit}`:''):concise(output)};
 if(code)return {...base,kind:'code',title:verb('Ran','Running','Run'),target:code,code};
 if(patch||(/edit_file|apply_patch/.test(name)&&(before||after))){
  const diff=patch||['@@',...before.split('\n').map(line=>'-'+line),...after.split('\n').map(line=>'+'+line)].join('\n');
  const rows=diffRows(diff),fileRows=rows.filter(row=>row.file),files=[...new Set(fileRows.map(row=>row.file))];
  const title=fileRows.length&&fileRows.every(row=>row.operation==='Delete')?verb('Deleted','Deleting','Delete'):fileRows.length&&fileRows.every(row=>row.operation==='Add')?verb('Created','Creating','Create'):verb('Edited','Editing','Edit');
  return {...base,kind:'patch',title,target:path||files.join(', ')||tool,diff,rows,added:rows.filter(row=>row.type==='add').length,removed:rows.filter(row=>row.type==='remove').length,preview:concise(output)};
 }
 if(tasks)return {...base,kind:'tasks',title:verb('Updated task list','Updating task list','Task list update'),tasks,preview:`${tasks.filter(task=>task?.status==='completed').length} of ${tasks.length} complete`};
 if(/read(_file)?$/.test(name)&&path)return {...base,kind:'read',title:verb('Read','Reading','Read'),target:path,content:typeof output==='string'?output:first(out,['content','text']),preview:args.offset!=null?`From line ${args.offset}${args.limit!=null?` · up to ${args.limit} lines`:''}`:concise(fallback)};
 if(/write(_file)?$/.test(name)&&path)return {...base,kind:'write',title:verb('Wrote','Writing','Write'),target:path,content:first(args,['content','text'])};
 if(typeof args.action==='string'||typeof args.operation==='string'){
  const nested=name==='app_control'&&args.operation==='dispatch'?first(record(args.args),['action']):'';
  return {...base,kind:name==='app_control'?'app':'generic',title:name==='app_control'?'App action':tool,target:nested?`dispatch → ${nested}`:typeof args.action==='string'?args.action:args.operation,preview:concise(output)};
 }
 if(/delegate|task|agent/.test(name)&&first(args,['instruction','instructions','prompt','task']))return {...base,kind:'delegate',title:verb('Delegated','Delegating','Delegate'),target:first(args,['agent','name','description']),task:first(args,['instruction','instructions','prompt','task'])};
 return {...base,kind:'generic',title:tool,preview:concise(fallback)};
}

// Summary hints are presentation only. Never pass them to the expanded payload
// renderer or its copy controls as if they were complete saved arguments.
export function actionHeading(node,inputText=node.input,outputText=node.output,errorText=node.error){
 const input=record(typeof inputText==='string'&&inputText.length>16384?null:parsed(inputText)),hints=record(node.inputSummary);
 const safeInput=Object.keys(input).length?input:Object.keys(hints).length?hints:typeof inputText==='string'&&inputText.length>16384?null:inputText;
 const action=actionContent(node,safeInput,outputText);
 const generic=typeof node.inputPreview==='string'?boundedPreview(node.inputPreview):retainedInputPreview(inputText);
 const actionPath=typeof node.actionPath==='string'&&node.actionPath?node.actionPath:'';
 const bounded=value=>{const text=textValue(value).replace(/\s+/g,' ').trim();return text.length>180?text.slice(0,179)+'…':text};
 const result=record(parsed(outputText)),out=record(resultValue(result)),outcome=record(node.outcomeSummary);
 const exit=out.returncode??out.exit_code??out.exitCode??outcome.exitCode;
 const error=parsed(errorText)??result.error??out.error??outcome.error;
 const message=typeof error==='string'?error:first(record(error),['message','error_message','error']);
 const failure=bounded(message||(action.failed||exit!=null&&exit!==0?first(out,['stderr']).trim().split('\n').filter(Boolean).at(-1):''));
 const detail=[failure,exit!=null?`Exit ${exit}`:''].filter(Boolean).join(' · ');
 return {...action,target:bounded(action.kind==='app'&&actionPath?actionPath:action.target||action.task||(action.kind==='generic'?generic:'')),detail,failed:!!failure||exit!=null&&exit!==0,
  preview:bounded(action.preview)};
}

export function contentUnavailable(node,label='Action content'){
 const state=node.contentAvailability?.status;
 if(state==='ambiguous')return `${label} could not be matched to this call. Other saved work may still be available.`;
 if(state==='unverified')return `${label} is available only as related saved records; its exact attempt is unconfirmed.`;
 if(state==='not-loaded')return `${label} is not in the loaded saved work.`;
 return `${label} is not available in the saved record.`;
}

export function diffRows(text){
 let oldLine=null,newLine=null,inHunk=false,oldRemaining=null,newRemaining=null;
 const reset=()=>{oldLine=newLine=oldRemaining=newRemaining=null;inHunk=false};
 return String(text).split('\n').map(text=>{
  const file=text.match(/^\*\*\* (Update|Add|Delete) File: (.+)$/);
  if(file){reset();return {type:'header',text,file:file[2],operation:file[1]}}
  if(/^(?:diff --git |\*\*\*)/.test(text)){reset();return {type:'header',text}}
  if(!inHunk&&/^(?:--- |\+\+\+ )/.test(text))return {type:'header',text,...(text.startsWith('+++ ')&&text.slice(4)!=='/dev/null'?{file:text.slice(4).replace(/^b\//,'')}: {})};
  const hunk=text.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
  if(text.startsWith('@@')){
   oldLine=hunk?Number(hunk[1]):null;newLine=hunk?Number(hunk[3]):null;
   oldRemaining=hunk?Number(hunk[2]??1):null;newRemaining=hunk?Number(hunk[4]??1):null;
   inHunk=true;return {type:'header',text};
  }
  let row;
  if(text.startsWith('+')){row={type:'add',old:null,new:newLine===null?null:newLine++,text:text.slice(1)};if(newRemaining!==null)newRemaining--}
  else if(text.startsWith('-')){row={type:'remove',old:oldLine===null?null:oldLine++,new:null,text:text.slice(1)};if(oldRemaining!==null)oldRemaining--}
  else if(text.startsWith(' ')){row={type:'context',old:oldLine===null?null:oldLine++,new:newLine===null?null:newLine++,text:text.slice(1)};if(oldRemaining!==null)oldRemaining--;if(newRemaining!==null)newRemaining--}
  else return {type:'header',text};
  inHunk=!(oldRemaining===0&&newRemaining===0);
  return row;
 });
}
