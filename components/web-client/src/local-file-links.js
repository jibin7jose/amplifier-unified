import React,{useRef,useState} from 'react';

// Recognition is deliberately narrower than URL recognition. The server owns
// workspace, symlink, existence and preview-size checks on an explicit click.
export function localFilePath(value){
 if(typeof value!=='string'||!value||value.length>4000||/[\x00-\x1f\x7f]/.test(value)||value.startsWith('#')||value.startsWith('//'))return null;
 let path=value.replace(/:\d+(?::\d+)?$/,'');
 if(/^file:/i.test(path)){
  try{const url=new URL(path);if(url.hostname&&url.hostname!=='localhost'||url.username||url.password)return null;path=url.pathname}catch{return null}
 }else if(/^[a-z][a-z\d+.-]*:/i.test(path))return null;
 path=path.split(/[?#]/,1)[0];
 try{path=decodeURIComponent(path)}catch{return null}
 if(/[\x00-\x1f\x7f\\]/.test(path)||path.startsWith('//')||/^www\./i.test(path)||/^[a-z][a-z\d+.-]*:/i.test(path))return null;
 path=path.replace(/:\d+(?::\d+)?$/,'');
 if(!/[^/]\.[a-z\d_-]{1,20}$/i.test(path))return null;
 return path;
}

export function LocalFileLink({path,context,children}){
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),pending=useRef(false);
 async function open(){
  if(pending.current)return;pending.current=true;setBusy(true);setNotice('');
  try{
   const response=await context.act('canvas.openFile',{sessionId:context.sessionId,workspace:context.workspace,path});
   if(response?.result?.status!=='opened')setNotice(response?.result?.message||'This file could not be opened.');
  }catch{setNotice('This file could not be opened.')}
  finally{pending.current=false;setBusy(false)}
 }
 return React.createElement('span',{className:'a-file-reference'},
  React.createElement('button',{type:'button',className:'a-link',disabled:busy,'data-action':'canvas.openFile',title:'Open in Canvas: '+path,onClick:open},children),
  notice&&React.createElement('span',{role:'status',className:'a-caption'},' '+notice));
}

export function remarkLocalFiles(){
 return tree=>{
  let remaining=100;
  function visit(node){
   if(!node.children||['link','image','code','html'].includes(node.type))return;
   node.children=node.children.flatMap(child=>{
    if(!remaining)return [child];
    if(child.type==='inlineCode'){
     const path=localFilePath(child.value);
     if(path){remaining--;return [{type:'link',url:path,children:[child]}]}
    }
    if(child.type==='text'){
     const parts=[];let end=0;
     const pattern=/(?<![\w:/%@])(?:\.{1,2}\/|\/)?(?:[\p{L}\p{N}_.@+-]+\/)+[\p{L}\p{N}_.@+-]+(?::\d+(?::\d+)?)?/gu;
     for(const match of child.value.matchAll(pattern)){
      const raw=match[0].replace(/[.,;!]+$/,'');
      if(!remaining||!localFilePath(raw))continue;
      if(match.index>end)parts.push({type:'text',value:child.value.slice(end,match.index)});
      parts.push({type:'link',url:raw,children:[{type:'text',value:raw}]});
      end=match.index+raw.length;remaining--;
     }
     if(end){if(end<child.value.length)parts.push({type:'text',value:child.value.slice(end)});return parts}
    }
    visit(child);return [child];
   });
  }
  visit(tree);
 };
}
