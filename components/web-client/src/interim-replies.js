import {isRunning} from './timeline-data.js';
import {isVoiceWorkMessage} from './voice-work-message.js';

// These are host-qualified presentation facts, not a last-bubble heuristic.
export function interimResponse(message){
 const value=message?.response;
 return message?.role==='assistant'&&!message.partial&&!message.streaming&&!message.observation&&
  value?.version===1&&value.status==='completed'&&value.phase==='interim'&&
  value.generationId===message.generationId&&Array.isArray(value.finalMessageIds)&&value.finalMessageIds.length&&
  !value.finalMessageIds.includes(message.id)?value:null;
}

export function foldInterimRows(rows,data,enabled,{deferred=new Set(),revealId=null}={}){
 if(enabled!==true)return rows;
 const turns=new Map(data.turns.map(turn=>[turn.id,turn])),result=[];
 let group=null;
 const compatible=(items,response)=>items.every(item=>{
  const turn=turns.get(item.turnId);
  return turn&&!isRunning(turn)&&!['failed','error','cancelled','interrupted'].includes(turn.phase||turn.status)&&
   response.inputIds?.includes(turn.originalTurnId||turn.id);
 });
 for(const row of rows){
  // Voice work is placed by exact input ownership before this optional fold.
  const response=row.kind==='message'&&!isVoiceWorkMessage(row.message)&&interimResponse(row.message);
  if(response&&!deferred.has(response.generationId)){
   if(!group||group.generationId!==response.generationId){
    const previous=result.at(-1),work=previous?.kind==='work'&&!previous.interim&&!previous.voiceWork&&compatible(previous.items,response)?result.pop():null;
    group={kind:'work',interim:true,id:`interim:${response.generationId}:${row.id}`,
     generationId:response.generationId,response,items:[...(work?.items||[])],outputs:[],reveal:false};
    result.push(group);
   }
   group.items.push({message:row.message});
   if(row.id===revealId)group.reveal=true;
  }else if(row.kind==='artifacts'&&group?.items.some(item=>item.message?.id===row.message.id)){
   // Keep the original output association outside the collapsed prose.
   group.outputs.push(row);
  }else if(row.kind==='work'&&!row.voiceWork&&group&&compatible(row.items,group.response)){
   group.items.push(...row.items);
  }else{
   group=null;result.push(row);
  }
 }
 return result;
}

export function interimOpen(expanded,row){
 if(row.reveal||expanded.has(row.id))return true;
 if(expanded.has('closed:'+row.id))return false;
 // A deliberate open of an existing work section survives its regrouping.
 return row.items.some(item=>item.message?expanded.has('interim-message:'+item.message.id):
  item.turnId&&(expanded.has('turn:'+item.turnId)||expanded.has('work:'+item.turnId)));
}

export function retainInterimIds(rows,previous=[]){
 const used=new Set();
 return rows.map(row=>{
  if(!row.interim)return row;
  const ids=new Set(row.items.flatMap(item=>item.message?[item.message.id]:[]));
  const old=previous.find(candidate=>candidate.interim&&!used.has(candidate.id)&&
   candidate.items.some(item=>item.message&&ids.has(item.message.id)));
  const id=old?.id||row.id;used.add(id);
  return id===row.id?row:{...row,id};
 });
}

export function groupForMessage(rows,id){
 return rows.find(row=>(row.interim||row.voiceWork)&&row.items.some(item=>item.message&&(item.message.id===id||item.message.nativeMessageId===id)));
}
