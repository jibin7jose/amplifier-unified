import {useCallback,useRef,useState} from 'react';
const key='amplifier.messageOutbox.v1';
export function readOutbox(storage){
 try{return JSON.parse(storage?.getItem(key)||'[]').filter(row=>row&&typeof row.id==='string'&&typeof row.commandId==='string'&&(row.sessionId===null||typeof row.sessionId==='string')&&typeof row.text==='string'&&Array.isArray(row.attachmentIds)&&row.attachmentIds.every(id=>typeof id==='string')&&['sending','failed','unknown'].includes(row.status)).map(row=>({...row,status:row.status==='sending'?'unknown':row.status}));}catch{return []}
}
export function outboxMessages(messages,entries,sessionId){
 const pending=entries.filter(row=>row.sessionId===(sessionId??null));
 const confirmed=messages.map(message=>{const entry=pending.find(row=>row.commandId===message.inputId);return entry&&!['accepted','queued_for_update','held'].includes(message.delivery?.status)?{...message,localDelivery:{...entry,...(['failed','sending','queued'].includes(message.delivery?.status)?{status:message.delivery.status}:{})}}:message});
 return [...confirmed,...pending.filter(row=>!messages.some(message=>message.inputId===row.commandId)).map(row=>({id:row.id,inputId:row.commandId,role:'user',text:row.text,via:row.via,createdAt:row.createdAt,attachments:row.attachments,replyTo:row.replyTo,localDelivery:row}))];
}
export function useMessageOutbox(){
 const [entries,setEntries]=useState(()=>{try{return readOutbox(sessionStorage)}catch{return []}}),current=useRef(entries),[storageError,setStorageError]=useState(false);
 const update=useCallback((id,patch)=>{
  const rows=current.current,found=rows.some(row=>row.id===id);
  if(!found&&patch!==null&&!patch.commandId)return; // A late reply cannot recreate an already acknowledged entry.
  const next=patch===null?rows.filter(row=>row.id!==id):found?rows.map(row=>row.id===id?{...row,...patch}:row):[...rows,{id,...patch}];
  current.current=next;setEntries(next);try{sessionStorage.setItem(key,JSON.stringify(next));setStorageError(false)}catch{setStorageError(true)}
  return next.find(row=>row.id===id);
 },[]);
 const acknowledge=useCallback((sessionId,inputId,receipt)=>{
  if(!sessionId||!inputId||receipt?.delivery!=='accepted')return;
  for(const entry of current.current)
   if(entry.sessionId===sessionId&&entry.commandId===inputId)update(entry.id,null);
 },[update]);
 return {entries,current,update,acknowledge,storageError};
}
