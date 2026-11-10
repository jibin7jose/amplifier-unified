// Local intent is not shared acceptance. One flight per emoji preserves click
// order; different messages/emoji never wait for each other or a model turn.
export function createReactionController({sessionId,messageId,clientId,send,read,notify=()=>{},save=()=>{},restored=[]}){
 let confirmed={revision:-1,reactions:[]},error='',checking=false,storageError=false,awaitingMount=true;
 const listeners=new Set();
 const pending=new Map(restored.map(row=>[row.emoji,{...row,status:'unknown',restored:true}]));
 const changed=()=>{try{storageError=save([...pending.values()])===false}catch{storageError=true}notify();for(const listener of listeners)listener()};
 function observe(value){
  if(Number.isSafeInteger(value?.annotationRevision)&&value.annotationRevision>=confirmed.revision)
   confirmed={revision:value.annotationRevision,reactions:[...(value.reactions||[])]};
  else if(confirmed.revision<0)confirmed={revision:-1,reactions:[...(value?.reactions||[])]};
 }
 function view(){const selected=new Set(confirmed.reactions);for(const [emoji,row] of pending)(row.desired?selected.add:selected.delete).call(selected,emoji);return {reactions:[...selected],pending:[...pending.keys()],unknown:[...pending.values()].some(row=>row.status==='unknown'),error:error||(storageError?'This browser could not retain reaction recovery. Keep this page open until the save is confirmed.':''),checking}}
 function settle(row,receipt){
  const value=receipt?.result;
  if(receipt?.accepted!==true||value?.sessionId!==sessionId||value.messageId!==messageId||value.reactionActor!=='user'||!Array.isArray(value.reactions)||!Number.isSafeInteger(value.annotationRevision))throw Object.assign(Error('Reaction save could not be confirmed.'),{unconfirmed:true});
  observe(value);
  if(pending.get(row.emoji)!==row)return;
  pending.delete(row.emoji);
  if(row.desired!==row.present){
   if(row.restored)error='Your later reaction change was not sent. Choose it again.';
   else start(row.emoji,row.desired);
  }
  changed();
 }
 async function flight(row){
  try{settle(row,await send(row))}
  catch(e){
   if(pending.get(row.emoji)!==row)return;
   if(e.unconfirmed||!e.status||e.status>=500){row.status='unknown';error='Reaction not confirmed. Check its saved state before trying again.'}
   else {pending.delete(row.emoji);error=e.message||'The reaction was not saved.';if(row.desired!==row.present&&row.desired!==confirmed.reactions.includes(row.emoji))start(row.emoji,row.desired)}
   changed();
  }
 }
 function start(emoji,present){const row={emoji,present,desired:present,id:crypto.randomUUID(),clientId,status:'sending'};pending.set(emoji,row);changed();void flight(row)}
 function set(emoji,present){error='';const row=pending.get(emoji);if(row){row.desired=present;row.restored=false;changed()}else start(emoji,present)}
 async function reconcile(){
  if(checking)return;checking=true;changed();
  try{
   for(const row of [...pending.values()].filter(row=>row.status==='unknown')){
    const value=await read(row);
    if(value?.sessionId!==sessionId||value.messageId!==messageId)throw Error('Reaction state belongs to another message.');
    if(value.status==='accepted'){error='';settle(row,value.receipt);observe({...value.current,annotationRevision:value.revision});}
    else if(value.status==='rejected'&&value.receipt?.accepted===false){
     if(pending.get(row.emoji)!==row)continue;
     observe({...value.current,annotationRevision:value.revision});pending.delete(row.emoji);
     error=value.receipt.error||'The reaction was not saved.';
     if(!row.restored&&row.desired!==row.present&&row.desired!==confirmed.reactions.includes(row.emoji))start(row.emoji,row.desired);
    }
    else error='Reaction outcome is still unknown. No write was repeated.';
   }
  }catch(e){error=e.message||'Reaction state could not be checked.'}
  finally{checking=false;changed()}
 }
 return {observe,view,set,reconcile,acquire:()=>{awaitingMount=true},retained:()=>awaitingMount||pending.size>0||listeners.size>0,
  subscribe:listener=>{awaitingMount=false;listeners.add(listener);return()=>listeners.delete(listener)}};
}

// A page has one writer for each actor/message even when the row is unmounted
// during navigation. Reload uses the journal and only reconciles saved flights.
export function createReactionRegistry(limit=128){
 const entries=new Map();
 return {acquire(owner,create){
  if(entries.has(owner)){const value=entries.get(owner);value.acquire();return value}
  for(const [key,value] of entries){if(entries.size<limit)break;if(!value.retained())entries.delete(key)}
  const value=create();entries.set(owner,value);return value;
 }};
}
export const reactionControllers=createReactionRegistry();
