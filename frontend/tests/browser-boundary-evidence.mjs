// Test-only optional evidence. A deadline is not cancellation and needs a
// responsive Node event loop; no process, UI action or retry is owned here.
import {bounded} from './chat-reference-harness.mjs';

export const BOUNDARY_OBSERVATION_MS=500;

export async function boundaryRead(read){
 const pending=Promise.resolve().then(read).then(
  value=>({status:'observed',value}),()=>({status:'unavailable'}),
 );
 try{return await bounded(pending,BOUNDARY_OBSERVATION_MS,'Optional boundary evidence deadline')}
 catch{return {status:'timed-out'}}
}

export async function closeBoundaryHandle(handle){
 const events=await boundaryRead(()=>handle.evaluate(value=>value.finish()));
 const disposal=await boundaryRead(()=>handle.dispose());
 return {events,disposal:{status:disposal.status}};
}

export async function openBoundaryHandle(read){
 const pending=Promise.resolve().then(read);
 const result=await boundaryRead(()=>pending);
 if(result.status!=='observed'){
  // Setup may have installed passive listeners even though its reply is late.
  // Retire a late handle once, boundedly, without rewriting the saved outcome.
  void pending.then(handle=>closeBoundaryHandle(handle),()=>{}).catch(()=>{});
 }
 return result;
}

export function boundaryIdentity(actual,expected){
 return Object.fromEntries(Object.entries(expected).map(([key,value])=>
  [key,value==null||actual[key]==null?null:actual[key]===value]));
}

const matches=row=>row.identityMatch&&Object.values(row.identityMatch).every(value=>value===true);
export function boundaryRequests(rows,action,uncertain){
 const candidates=rows.filter(row=>row.action===action);
 const matching=candidates.filter(matches).length;
 const incomplete=candidates.filter(row=>!row.identityMatch||Object.values(row.identityMatch).some(value=>value===null)).length;
 return {status:matching?'matching-observed':incomplete?'incomplete-unknown':uncertain?'unknown':
  candidates.length?'mismatched-observed':'none-observed',
  candidates:candidates.length,matching,incomplete,complete:!uncertain};
}

export function boundaryLease(resource,rows,uncertain){
 const leases=rows.filter(row=>row.action==='canvas.views.inline'&&matches(row));
 const complete=lease=>lease.responseState==='observed'&&lease.response?.accepted===true
  &&typeof lease.response.result?.viewId==='string'&&typeof lease.response.result?.resourceId==='string'
  &&typeof lease.response.result?.resourceRevision==='string'
  &&Number.isSafeInteger(lease.response.result?.generation);
 if(!resource.args?.viewId||!resource.args.resourceRevision||!Number.isSafeInteger(resource.args.generation))return null;
 const equal=lease=>lease.response.result.viewId===resource.args.viewId
  &&lease.response.result.resourceId===resource.args.resourceId
  &&lease.response.result.resourceRevision===resource.args.resourceRevision
  &&lease.response.result.generation===resource.args.generation;
 if(leases.some(lease=>complete(lease)&&equal(lease)))return true;
 // No exact lease, a missing/pending/unreadable receipt or incomplete capture
 // cannot establish a different lease. Preserve uncertainty, not false mismatch.
 return leases.length&&!uncertain&&leases.every(complete)?false:null;
}