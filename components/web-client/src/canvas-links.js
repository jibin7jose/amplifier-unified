import React,{useRef,useState} from 'react';

// Only this exact saved-reference contract is actionable. It cannot encode an
// arbitrary host operation, navigate conversations, fetch a URL or execute work.
export function canvasReference(value){
 if(typeof value!=='string'||value.length>600||!value.startsWith('amplifier-canvas://artifact/'))return null;
 try{
  const url=new URL(value),id=url.pathname.slice(1),sessionId=url.searchParams.get('session'),raw=url.searchParams.get('version');
  if(url.host!=='artifact'||url.username||url.password||url.hash||!/^[-\w]{1,100}$/.test(id)||!/^[-\w]{1,200}$/.test(sessionId||''))return null;
  if([...url.searchParams.keys()].sort().join(',')!=='session,version'||!/^\d+$/.test(raw||''))return null;
  const version=Number(raw);if(!Number.isSafeInteger(version)||version<1)return null;
  return {id,sessionId,version};
 }catch{return null}
}

export function CanvasReferenceLink({reference,context,children}){
 const [busy,setBusy]=useState(false),[notice,setNotice]=useState(''),pending=useRef(false);
 async function open(){
  if(pending.current)return;
  if(reference.sessionId!==context.sessionId){setNotice('This artifact belongs to another chat. Open the reference from its original chat.');return}
  pending.current=true;setBusy(true);setNotice('');
  try{await context.act('canvas.select',reference)}
  catch(error){setNotice(error.message||'This saved artifact version is unavailable.')}
  finally{pending.current=false;setBusy(false)}
 }
 return React.createElement('span',{className:'a-file-reference'},React.createElement('button',{
  type:'button',className:'a-link',disabled:busy,'data-action':'canvas.select',title:'Open saved version '+reference.version+' in Canvas',onClick:open},children),
  notice&&React.createElement('span',{role:'status',className:'a-caption'},' '+notice));
}
