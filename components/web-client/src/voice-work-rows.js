import {isVoiceWorkMessage} from './voice-work-message.js';

export function voiceWorkStream(session){
 if(!session?.streaming)return null;
 const message={id:session.streamingId||'streaming',role:'assistant',source:'amplifier',
  inputId:session.streamingInputId,text:session.streaming,streaming:true};
 return isVoiceWorkMessage(message)?message:null;
}
const owner=turn=>turn.originalTurnId||turn.id;
const identities=message=>[message.id,message.streamId].filter(Boolean);

// Presentation only: exact host input ownership selects a Work segment. Row
// order chooses among segments of that same owner, never between unrelated work.
export function voiceWorkRows(source,data,stream=null){
 const turns=new Map(data.turns.map(turn=>[turn.id,turn]));
 const rows=[...source,...(stream?[{kind:'message',id:stream.id,message:stream}]:[])].map(row=>
  row.kind==='work'?{...row,items:[...row.items]}:row);
 const work=rows.flatMap((row,index)=>row.kind==='work'?[{row,index,
  turns:row.items.flatMap(item=>turns.has(item.turnId)?[turns.get(item.turnId)]:[])}]:[]);
 const removed=new Set();
 for(const [index,row] of rows.entries()){
  if(row.kind!=='message'||!isVoiceWorkMessage(row.message))continue;
  const message=row.message,inputId=message.inputId;
  const matches=inputId?work.flatMap(entry=>entry.turns.filter(turn=>!turn.observationWindow&&
   [turn.inputId,turn.originalTurnId,turn.id].includes(inputId)).map(turn=>({...entry,turn}))):[];
  const owners=new Set(matches.map(match=>owner(match.turn)));
  const target=owners.size===1?(matches.filter(match=>match.index<index).at(-1)||matches[0]):null;
  if(target){
   target.row.items.push({message,ownerTurnId:target.turn.id});target.row.voiceWork=true;
   removed.add(row);
  }else{
   // Missing/paged ownership is not permission to attach to the latest turn.
   // Keep known backend content inspectable under a neutral Work disclosure.
   const id=`voice-work:${inputId||message.id}:${message.id}`;
   rows[index]={kind:'work',id,summaryId:id,voiceWork:true,items:[{message}]};
  }
 }
 const result=[];
 for(const row of rows){
  if(removed.has(row))continue;
  if(row.kind==='work'){
   const ownTurns=row.items.flatMap(item=>turns.has(item.turnId)?[turns.get(item.turnId)]:[]);
   const inputs=[...new Set([...ownTurns.filter(turn=>turn.lifecycle!=='voice').map(turn=>turn.inputId||turn.originalTurnId||turn.id).filter(id=>id?.startsWith('voice:')),
    ...row.items.flatMap(item=>isVoiceWorkMessage(item.message)&&item.message.inputId?[item.message.inputId]:[])])];
   row.voiceWork||=inputs.length>0;
   if(row.voiceWork){
    row.voiceInputIds=inputs;row.summaryId||='turn:'+ownTurns[0]?.id;
    const previous=result.at(-1),previousOwners=new Set(previous?.items?.flatMap(item=>turns.has(item.turnId)?[owner(turns.get(item.turnId))]:[])||[]);
    const ownOwners=new Set(ownTurns.map(owner));
    const sameOwner=ownOwners.size>0&&ownOwners.size===previousOwners.size&&[...ownOwners].every(id=>previousOwners.has(id));
    const sameOrphan=!ownOwners.size&&!previousOwners.size&&inputs.length===1&&previous?.voiceInputIds?.length===1&&inputs[0]===previous.voiceInputIds[0];
    if(previous?.voiceWork&&(sameOwner||sameOrphan)){previous.items.push(...row.items);previous.voiceInputIds=[...new Set([...previous.voiceInputIds,...inputs])];continue}
   }
  }
  result.push(row);
 }
 return result;
}

// A first chunk, its saved streamId, and later loaded execution are one visible
// disclosure. Preserve its open/closed choice without inventing execution IDs.
export function retainVoiceWorkIds(rows,previous=[]){
 const used=new Set();
 return rows.map(row=>{
  if(!row.voiceWork)return row;
  const messages=new Set(row.items.flatMap(item=>item.message?identities(item.message):[]));
  const turns=new Set(row.items.flatMap(item=>item.turnId?[item.turnId]:[]));
  const old=previous.find(candidate=>candidate.voiceWork&&!used.has(candidate.id)&&candidate.items.some(item=>
   item.turnId&&turns.has(item.turnId)||item.message&&identities(item.message).some(id=>messages.has(id))));
  if(!old)return row;
  used.add(old.id);return {...row,id:old.id,summaryId:old.summaryId};
 });
}
