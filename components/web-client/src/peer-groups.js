// Presentation only. Canonical sequence, identities and non-peer boundaries win.
export const peerEligible=row=>row.kind==='message'&&row.message?.role==='user'&&
 (row.message.inputOrigin==='peer'||row.message.via==='peer')&&!!row.message.attribution?.peer;
export function peerRuns(rows,artifactMessages=null){
 const result=[];let run=[];
 const flush=()=>{if(run.length)result.push(run.length===1?run[0]:{kind:'peers',id:'peers:'+run[0].id,rows:run});run=[]};
 for(const row of rows){
  // withArtifactRows reserves a slot after every user message, even when the
  // renderer returns null. Only confirmed empty slots are transparent.
  if(row.kind==='artifacts'&&artifactMessages&&!artifactMessages.has(row.message?.id))continue;
  if(peerEligible(row))run.push(row);else{flush();result.push(row)}
 }flush();return result;
}
export function peerAttention(messages){
 const counts=new Map();
 for(const m of messages){
  const status=m.delivery?.status||m.attribution?.peer?.delivery;
  const label=['failed','error','rejected'].includes(status)?'delivery error':
   ['queued','pending','submitting'].includes(status)?'pending delivery':
   ['unknown','held'].includes(status)?'delivery unconfirmed':null;
  if(label)counts.set(label,(counts.get(label)||0)+1);
 }
 return [...counts].map(([label,n])=>`${n} ${label}`);
}
