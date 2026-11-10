// DOM metadata is readable from an isolated browser inspection context. Keep
// only fixed labels and numeric evidence; never resource values or query text.
import {sameScope} from './resources.js';

const reasons=new Set(['bootstrap','demand','visibility','reconnect','notify','resources','command-view','manual','composition','stale','scope-mismatch','error']);
const resources=new Set(['context','customSnapshot','chatNavigation','homeNavigation','pinnedNavigation','recentNavigation','workspaceShortcuts','workspaceExplorer','sidebarSearch','sidebarWorkspace','sidebarRecent','sidebarPinned','workspaceOverview']);
const measures=['changedRecords','unchangedRecords','removedRecords','contextRecords','pageRecords','customRecords'];
const increment=(object,key,amount=1)=>object[key]=Math.min(Number.MAX_SAFE_INTEGER,(object[key]||0)+amount);

// Compare only accepted revision tuples, never values or serialized payloads.
// Instance IDs and scope are used transiently for identity, never reported or
// retained. The server admits at most 64 instances and these 13 resource names.
function revisionChanges(previous,next){
 const sameIdentity=!!previous&&previous.hostInstanceId===next.hostInstanceId&&previous.clientId===next.clientId
  &&previous.bootstrapKey===next.bootstrapKey&&previous.demandId===next.demandId&&sameScope(previous.scope,next.scope);
 const changes={changedRecords:0,unchangedRecords:0,removedRecords:0,contextRecords:0,pageRecords:0,customRecords:0,changedByName:{}};
 for(const id of Object.keys(next.instances||{}).slice(0,64)){
  const entry=next.instances[id],prior=sameIdentity&&previous.instances?.[id];
  for(const name of resources){
   const record=entry.resources?.[name];if(!record)continue;
   const old=prior?.generation===entry.generation&&prior.resources?.[name];
   if(old&&old.revision===record.revision){changes.unchangedRecords++;continue}
   changes.changedRecords++;increment(changes.changedByName,name);
   changes[name==='context'?'contextRecords':name==='customSnapshot'?'customRecords':'pageRecords']++;
  }
 }
 for(const id of Object.keys(previous?.instances||{}).slice(0,64)){
  const entry=previous.instances[id],current=sameIdentity&&next.instances?.[id];
  for(const name of resources)if(entry.resources?.[name]&&(!current||current.generation!==entry.generation||!current.resources?.[name]))changes.removedRecords++;
 }
 return {sameIdentity,...changes};
}

export function createShellDiagnostics(target=globalThis.document?.documentElement){
 const events=[],counts={},resourceRevisions={acceptedEnvelopes:0,unchangedEnvelopes:0,
  changedRecords:0,unchangedRecords:0,removedRecords:0,contextRecords:0,pageRecords:0,customRecords:0,changedByName:{}};
 const record=(reason,fields={})=>{
  if(!reasons.has(reason))return;
  increment(counts,reason);
  const entry={reason,at:Math.round(globalThis.performance?.now?.()||0)};
  for(const key of ['demandId','elapsedMs','serverMs','status','updates','bytes',...measures]){
   const value=fields[key];if(Number.isFinite(value)&&value>=0)entry[key]=Math.min(Math.round(value),Number.MAX_SAFE_INTEGER);
  }
  if(typeof fields.requestId==='string'&&/^[a-f0-9]{32}$/.test(fields.requestId))entry.requestId=fields.requestId;
  if(Array.isArray(fields.resources))entry.resources=[...new Set(fields.resources.filter(value=>resources.has(value)))].slice(0,13);
  if(fields.changedByName){
   entry.changedByName={};
   for(const name of resources){const value=fields.changedByName[name];if(Number.isFinite(value)&&value>0)entry.changedByName[name]=Math.min(Math.round(value),Number.MAX_SAFE_INTEGER)}
  }
  events.push(entry);if(events.length>24)events.shift();
  let value=JSON.stringify({version:2,counts,resourceRevisions,events});
  while(value.length>8192&&events.length){events.shift();value=JSON.stringify({version:2,counts,resourceRevisions,events})}
  target?.setAttribute?.('data-amplifier-shell-diagnostics',value);
 };
 const acceptResources=(previous,next)=>{
  const changes=revisionChanges(previous,next);
  increment(resourceRevisions,'acceptedEnvelopes');
  if(changes.sameIdentity&&!changes.changedRecords&&!changes.removedRecords)increment(resourceRevisions,'unchangedEnvelopes');
  for(const key of measures)increment(resourceRevisions,key,changes[key]);
  for(const [name,count] of Object.entries(changes.changedByName))increment(resourceRevisions.changedByName,name,count);
  record('resources',{demandId:next.demandId,...changes});
 };
 return {record,acceptResources};
}
