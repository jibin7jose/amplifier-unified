import {treeForTurn,isRunning} from './timeline-data.js';

const hasField=(node,field)=>node?.[field]!=null||!!node?.[field+'Detail'];
const workerLabel=node=>`Delegate${node?.label?` · ${node.label.replace(/^Delegate · /,'')}`:''}`;

// A display-only flattening of already admitted ownership. Never bind a saved
// call to a run, reconstruct a turn, or change usage while arranging the stream.
export function workStream(data,turnId){
 const tree=treeForTurn(data,turnId),entries=[],statuses=[],undatedReplies=[],seen=new Set(),exchanges=new Set();
 const workers=new Map(),source=new Map(data.nodes.map(node=>[node.id,node]));
 const workerSessions=new Set(data.nodes.flatMap(node=>[node.kind==='worker'&&node.sessionId,node.delegate?.sessionId]).filter(Boolean));
 const sourceActor=node=>{
  const rootId=node?.rootSessionId||data.sessionId,sid=node?.sessionId;
  if(sid&&rootId&&sid===rootId)return 'Main chat';
  if(sid&&(workerSessions.has(sid)||rootId&&sid!==rootId))return 'Worker (owner unavailable)';
  return 'Sender unavailable';
 };
 const local=new Set(data.nodes.filter(node=>node.turnId===turnId).map(node=>node.id));
 // treeForTurn guards cycles; use a separate bounded walk for arbitrary roots.
 const visited=new Set();
 const index=node=>{if(visited.has(node.id))return;visited.add(node.id);if(node.kind==='worker')workers.set(node.id,node);for(const child of tree.children.get(node.id)||[])index(child)};
 tree.roots.forEach(index);
 const append=entry=>entries.push({...entry,sequence:entries.length});
 const addExchange=(node,owner,{followup=false,report=false}={})=>{
  const exchange=node.exchange||node,key=exchange.id||node.id;
  const control=/(^|[.:])live_worker$/.test(node.tool||node.label||'');
  // Repeated owner headers are context, not another sent/returned message.
  // Their exchange remains explicitly inspectable in Delegation details.
  if(node.kind==='worker'&&!local.has(key)&&!local.has(node.ownerId||node.id))return;
  if(exchanges.has(key))return;
  exchanges.add(key);
  const parent=owner&&workers.get(owner.parentId);
  const unverified=node.association==='unverified';
  const senders=followup&&node.sessionId?[...workers.values()].filter(worker=>worker.sessionId===node.sessionId):[];
  const sourceParent=source.get(node.id)?.parentId;
  const sender=senders.find(worker=>(worker.ownerId||worker.id)===sourceParent)||(senders.length===1?senders[0]:null);
  const rootId=node.rootSessionId||data.sessionId;
  const followupFrom=sender?workerLabel(sender):senders.length>1||workerSessions.has(node.sessionId)?'Worker (owner unavailable)':rootId&&node.sessionId===rootId?'Main chat':'Sender unavailable';
  const recipient=followup&&node.exchangeOwnerId&&(owner?.ownerId||owner?.id)===node.exchangeOwnerId?owner:null;
  // A worker header describes the recipient, but an unspawned delegate tool
  // remains under its caller. Missing ancestry is not evidence of Main chat.
  const caller=node.kind==='tool'&&!followup&&owner?.sessionId===node.sessionId?owner:parent;
  const requestSource=report?source.get(owner?.exchange?.id)||owner?.exchange:
   source.get(key)||exchange;
  const from=unverified?'Recorded sender':followup?followupFrom:caller?workerLabel(caller):sourceActor(requestSource);
  const to=unverified?'Delegate (association unverified)':followup?(recipient?workerLabel(recipient):'Delegate (recipient unavailable)'):parent&&(node.kind==='worker'||report)?workerLabel(owner):'Delegate';
  const sourceNode=node.kind==='worker'?node:{...node,exchange};
  const running=isRunning({phase:node.workerPhase||node.phase||exchange.phase});
  const request=hasField(exchange,'delegateRequest'),response=hasField(exchange,'delegateResponse');
  // The first request may need the existing explicit saved-tool lookup. Reports
  // never manufacture a matching request or use a worker summary as the reply.
  if(request||!report&&!unverified){
   append({id:`${key}:delegateRequest`,kind:'exchange',field:'delegateRequest',node:sourceNode,
    owner,followup,receipt:node.kind==='tool',label:unverified?'Recorded request':followup?'Follow-up':'Request',direction:`${from} → ${to}`,
    at:unverified?node.createdAt:exchange.startedAt??source.get(key)?.startedAt??node.startedAt});
  }
  if(!control&&(response||!running&&!report&&!followup&&!unverified&&exchange.delivery!=='separate')){
   const entry={id:`${key}:delegateResponse`,kind:'exchange',field:'delegateResponse',node:sourceNode,
    owner,report,label:unverified?'Recorded reply':'Reply',direction:`${to} → ${from}`,
    at:unverified?node.createdAt:exchange.endedAt??source.get(key)?.endedAt??(report?node.startedAt:node.endedAt)};
   if(!response&&!Number.isFinite(entry.at))undatedReplies.push(entry);else append(entry);
  }
 };
 const walk=(node,owner=null)=>{
  if(seen.has(node.id))return;seen.add(node.id);
  if(node.kind==='worker'){
   if(node.exchange)addExchange(node,node);
   statuses.push({id:`${node.id}:status`,kind:'worker-status',node,owner:node,
    context:!!node.exchange&&!local.has(node.exchange.id)&&!local.has(node.ownerId||node.id)});
   for(const child of tree.children.get(node.id)||[])walk(child,node);
  }else if(node.exchange||hasField(node,'delegateRequest')||hasField(node,'delegateResponse')||
    (/(^|[.:])live_worker$/.test(node.tool||node.label||'')&&['message','steer'].includes(node.inputSummary?.action))){
   addExchange(node,owner||node.delegate,{followup:node.kind==='tool'&&!/(^|[.:])delegate$/.test(node.tool||node.label||''),report:node.kind==='exchange'});
   for(const child of tree.children.get(node.id)||[])walk(child,owner);
  }else{
   const actor=owner?workerLabel(owner):node.sessionId&&node.rootSessionId&&node.sessionId!==node.rootSessionId?'Worker (owner unavailable)':'Main chat';
   append({id:node.id,kind:'operation',node,owner,actor,at:node.startedAt??node.endedAt});
   for(const child of tree.children.get(node.id)||[])walk(child,owner);
  }
 };
 tree.roots.forEach(node=>walk(node));
 // Unknown dates are boundaries, not invented zero timestamps. Sort only
 // consecutive known-date runs; ties preserve source order.
 let start=0;
 for(let end=0;end<=entries.length;end++)if(end===entries.length||!Number.isFinite(entries[end].at)){
  entries.splice(start,end-start,...entries.slice(start,end).sort((a,b)=>a.at-b.at||a.sequence-b.sequence));
  start=end+1;
 }
 return {entries:[...entries,...undatedReplies,...statuses],tree};
}
