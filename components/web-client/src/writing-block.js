import React,{useState} from 'react';

export function WritingBlock({part,context,render}){
 const [text,setText]=useState(part.text),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[saved,setSaved]=useState(null);
 async function save(){if(busy)return;setBusy(true);setNotice('');try{const result=await context.act('outputs.write',{sessionId:context.sessionId,messageId:context.messageId,title:part.subject||part.variant.replaceAll('_',' '),variant:part.variant,content:text,...(part.subject?{subject:part.subject}:{}),...(saved?{parentId:saved}:{})});if(!result?.accepted)throw Error('The writing version was not saved.');setSaved(result.result.id);setNotice('Saved a writing version.');setEditing(false)}catch(e){setNotice(e.message)}finally{setBusy(false)}}
 const h=React.createElement;
 const button=(label,click,props={})=>h('button',{type:'button',className:'a-soft',disabled:busy,onClick:click,...props},label);
 async function copy(){try{await navigator.clipboard.writeText(text);setNotice('Copied writing.')}catch{setNotice('Clipboard is unavailable. Select the text to copy it.')}}
 return h('section',{className:'a-selection-card a-writing-block','aria-label':'Reusable writing'},
  h('strong',null,part.subject||part.variant.replaceAll('_',' ')),
  ...['recipient','cc','bcc'].filter(key=>part[key]).map(key=>h('p',{key},({recipient:'To',cc:'Cc',bcc:'Bcc'})[key]+': '+part[key])),
  editing?h('textarea',{'aria-label':'Edit writing copy',rows:8,value:text,disabled:busy,onChange:e=>setText(e.target.value)}):render(text),
  h('div',{className:'a-dialog-actions'},button('Copy writing',copy),button(editing?'Preview copy':'Edit copy',()=>setEditing(value=>!value)),
   context&&button('Save writing version',save,{className:'a-primary',disabled:busy||!text.trim(),'data-action':'outputs.write'})),
  notice&&h('p',{role:'status'},notice));
}
