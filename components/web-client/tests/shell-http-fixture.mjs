// Protocol adapter for small, deliberately synthetic API browser fixtures.
// It does not model catalog discovery or replace production projection tests.
import assert from 'node:assert/strict';
import {shellFor} from './shell-host.mjs';
import {shellScope,sameScope} from '../src/shell/resources.js';

export function createShellHttpFixture(readState){
 const hostInstanceId='fixture-shell-host',bootstrapKey='fixture-shell-bootstrap';
 let clientId,registration;
 const page=input=>({items:input.sessions||[],total:input.sessions?.length||0,pages:1,page:1,
  scope:{mode:'all',workspaceId:input.selectedWorkspaceId,filter:''}});
 function envelope(input){
  if(!registration||!sameScope(registration.scope,shellScope(input)))return undefined;
  const descriptor=shellFor(input,()=>{}).data,instances={};
  for(const instance of descriptor.composition.instances){
   const snapshot=descriptor.snapshots[instance.id];
   const context={view:snapshot.view,viewRevision:input.revision,selectedSessionId:input.selectedSessionId,
    recentScope:{mode:'all',filter:'',clientId,instanceId:instance.id,generation:0,workspaceId:input.selectedWorkspaceId},
    selectedWorkspaceId:input.selectedWorkspaceId,workspaces:input.workspaces||[],settings:input.settings||{},
    library:{bounded:true},sidebarNavigation:{recentView:{}},attention:input.attention||{},
    locationListing:input.locationListing,actionStatus:input.actionStatus};
   const resources={context:{revision:input.revision,value:context}};
   for(const name of registration.resources){
    let value;
    if(name==='workspaceShortcuts')value={workspaceShortcuts:[]};
    else if(name==='pinnedNavigation')value={pinnedNavigation:{items:[],total:0}};
    else if(name==='workspaceExplorer')value={workspaceExplorer:input.workspaceExplorer||{rows:[],pages:1,page:1}};
    else if(name.startsWith('sidebar'))value={sidebarNavigation:{[name.slice(7).toLowerCase()]:page(input)}};
    else value={[name]:input[name]||page(input)};
    if(name==='recentNavigation')value={...value,recentShortcuts:value.recentNavigation.items};
    resources[name]={revision:input.revision,value};
   }
   instances[instance.id]={generation:0,stateRevision:input.revision,pending:{},resources};
  }
  return {hostInstanceId,clientId,bootstrapKey,demandId:registration.demandId,
   scope:registration.scope,instances};
 }
 function state(){
  const input=readState(),resources=envelope(input);
  return {...input,client:{id:clientId,kind:'web',hostInstanceId},shellBootstrapKey:bootstrapKey,
   ...(resources?{shellResources:resources}:{})};
 }
 function response(request){
  const url=new URL(request.url()),path=url.pathname;
  if(path==='/api/clients/attach'){
   clientId=request.postDataJSON().clientId;registration=undefined;
   return {clientId,kind:'web',hostInstanceId,transports:[]};
  }
  if(path!=='/api/shell'&&path!=='/api/shell/resources')return undefined;
  const demand=path==='/api/shell/resources'?request.postDataJSON():{
   clientId:url.searchParams.get('clientId'),demandId:Number(url.searchParams.get('demandId')),
   resources:JSON.parse(url.searchParams.get('navigationResources')),scope:JSON.parse(url.searchParams.get('scope'))};
  assert.equal(demand.clientId,clientId);assert.ok(Number.isSafeInteger(demand.demandId)&&demand.demandId>0);
  assert.ok(Array.isArray(demand.resources));assert.ok(sameScope(demand.scope,shellScope(readState())));
  registration=demand;
  const input=readState(),shellResources=envelope(input);
  if(path==='/api/shell/resources')return shellResources;
  const descriptor=shellFor(input,()=>{}).data;
  return {...descriptor,snapshots:{},clientId,hostInstanceId,shellBootstrapKey:bootstrapKey,
   instanceGenerations:Object.fromEntries(descriptor.resolvedInstances.map(row=>[row.id,0])),shellResources};
 }
 return {state,response};
}

// Install with page.addInitScript. Initial state must follow client attachment,
// and every later injected frame uses the same adapter as HTTP action receipts.
export function installFixtureEvents(){
 const sources=new Set();
 window.EventSource=class extends EventTarget{
  constructor(){super();this.readyState=0;sources.add(this);queueMicrotask(async()=>{
   const state=await fetch('/api/state').then(response=>response.json());
   if(this.readyState===2)return;
   this.readyState=1;this.onopen?.();this.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)}));
  });}
  close(){this.readyState=2;sources.delete(this);}
 };
 window.emitState=window.emitFixtureState=state=>sources.forEach(source=>
  source.dispatchEvent(new MessageEvent('state',{data:JSON.stringify(state)})));
}
