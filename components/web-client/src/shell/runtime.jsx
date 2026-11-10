import React,{createContext,useContext,useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {request,recentRequestTimings} from '../api';
import {transportFailure} from '../connection-notice.js';
import {WorkspaceManager,ConversationList} from './navigation-components';
import './shell.css';
import {SlotOverflow} from './slot-overflow';
import {navigationSnapshot} from './navigation-snapshot';
import {retainEqual} from './wire';
import {navigationResources} from './navigation-demand';
import {shellScope,resourceSnapshots,matchesResources,retainResourceVersions,pendingSnapshots} from './resources';
import {createShellDiagnostics} from './diagnostics';
import {navigationExpanded,useNarrowScreen} from '../responsive-navigation';

export const ShellContext=createContext(null);
export function useShellContext(){return useContext(ShellContext)}

const builtins={'builtin.workspaces':WorkspaceManager,'builtin.chats':ConversationList};
const defaultComposition={instances:[{id:'workspaces',package:'builtin.workspaces',slot:'navigation',hideWhen:{instanceId:'chats',navChatScope:'all'}},{id:'chats',package:'builtin.chats',slot:'navigation'}],presentation:{}};
const empty=Object.freeze({view:{},workspaces:[],chatNavigation:{items:[],total:0,pages:1,scope:{}},workspaceExplorer:{rows:[]},library:{bounded:true},sharedHistory:{}});
const recovery=typeof location!=='undefined'&&new URLSearchParams(location.search).get('shell')==='recovery';
function freeze(value){if(value&&typeof value==='object'&&!Object.isFrozen(value)){Object.values(value).forEach(freeze);Object.freeze(value)}return value}

// An unmounted instance cannot borrow activation evidence from an older
// generation of the same package. Appearance-only changes retain generation.
function renderedStatuses(current,statuses){
 return Object.fromEntries((current?.resolvedInstances||[]).map(row=>{
  const evidence=statuses[row.id],generation=current.instanceGenerations?.[row.id];
  return [row.id,evidence?.package===row.package&&evidence.generation===generation?evidence.status:'inactive'];
 }));
}

export function useShell(state,dispatch,clientId,displayState=state,connection={}){
 const [data,setData]=useState(null),[error,setError]=useState('');
 const [presentationEdits,setPresentationEdits]=useState([]),presentationQueue=useRef(Promise.resolve());
 const latest=useRef({data:null,bootstrap:null,dispatch}),hosts=useRef(new Map()),inflight=useRef(null);
 const demand=useRef({id:0,key:null}),synced=useRef(0),diagnostics=useRef(null),alive=useRef(true),reconnectAfter=useRef(null);
 const [reconnectTick,setReconnectTick]=useState(0);
 if(!diagnostics.current)diagnostics.current=createShellDiagnostics();
 const [visible,setVisible]=useState(()=>!document.hidden);
 const navigationNarrow=useNarrowScreen('(max-width: 900px)');
 const supportsStatus=!data?.resolvedInstances||data.resolvedInstances.some(row=>row.package==='builtin.chats');
 const resources=visible?navigationResources(state,navigationExpanded(state?.view,navigationNarrow),supportsStatus):[],scope=shellScope(state),demandKey=JSON.stringify([scope,resources]);
 if(demand.current.key!==demandKey)demand.current={id:demand.current.id+1,key:demandKey,scope,resources};
 latest.current={...latest.current,dispatch,state:displayState,authoritative:state,connection,visible,demand:demand.current};
 const publish=useCallback(next=>{
  const snapshots={...next.snapshots};
  for(const host of hosts.current.values()){
   const raw=snapshots[host.id];
   if(!raw||raw.generation!==host.generation)continue;
   if(host.acknowledgedView)snapshots[host.id]={...raw,view:host.acknowledgedView.view,viewRevision:host.acknowledgedView.viewRevision};
  }
  next=retainEqual(latest.current.data,{...next,snapshots});
  latest.current.data=next;setData(next);
  for(const host of hosts.current.values()){
   const raw=next.snapshots?.[host.id]||empty;
   if(raw.generation!==host.generation)continue;
   const snapshot=retainEqual(host.snapshot,navigationSnapshot({...raw,view:{...raw.view,...Object.assign({},...host.pending.map(item=>item.patch))}},latest.current.state,host.scope));
   if(snapshot!==host.snapshot){host.snapshot=freeze(snapshot);host.listeners.forEach(fn=>fn())}
  }
 },[]);
 const receive=useCallback(envelope=>{
  const current=latest.current,bootstrap=current.bootstrap;
  if(!bootstrap||!alive.current)return false;
  const authoritativeHost=current.authoritative?.client?.hostInstanceId;
  if(authoritativeHost&&authoritativeHost!==bootstrap.hostInstanceId&&authoritativeHost!==current.bootstrapObservedHost)return false;
  if(matchesResources(envelope,bootstrap,current.demand))envelope=retainResourceVersions(current.data?.shellResources,envelope);
  const snapshots=resourceSnapshots(bootstrap,envelope,current.demand,current.data?.snapshots);
  if(!snapshots){diagnostics.current.record('stale',{demandId:envelope?.demandId});return false}
  const previousResources=current.data?.shellResources;
  synced.current=current.demand.id;
  for(const host of hosts.current.values()){
   const raw=snapshots[host.id];
   if(raw?.generation===host.generation&&host.acknowledgedView&&(raw.viewRevision||0)>=host.acknowledgedView.viewRevision)host.acknowledgedView=null;
  }
  setError('');current.connection.onRecovered?.();
  publish({...bootstrap,shellResources:envelope,snapshots});
  diagnostics.current.acceptResources(previousResources,envelope);
  if(envelope.error){setError(envelope.error.message);diagnostics.current.record('error',{demandId:envelope.demandId})}
  return true;
 },[publish]);
 const refresh=useCallback((reason='manual')=>{
  const current=latest.current,requested=current.demand;
  const bootstrapKey=current.authoritative?.shellBootstrapKey,hostInstanceId=current.authoritative?.client?.hostInstanceId;
  const hostChanged=hostInstanceId&&hostInstanceId!==current.bootstrap?.hostInstanceId&&hostInstanceId!==current.bootstrapObservedHost;
  const needsBootstrap=reason==='composition'||!current.bootstrap||hostChanged||(bootstrapKey!==current.bootstrap.shellBootstrapKey&&bootstrapKey!==current.bootstrapObservedKey);
  if(needsBootstrap&&!current.visible)return Promise.resolve();
  if(inflight.current)return inflight.current.promise;
  const kind=needsBootstrap?'bootstrap':reason;
  const requestScope={clientId,demandId:requested.id,resources:requested.resources,scope:requested.scope,reason:kind};
  const query=new URLSearchParams({bundled:'1',clientId,demandId:String(requested.id),navigationResources:JSON.stringify(requested.resources),scope:JSON.stringify(requested.scope)});
  if(recovery)query.set('recovery','1');
  diagnostics.current.record(kind,{demandId:requested.id,resources:requested.resources});
  const job={id:requested.id,bootstrapKey,hostInstanceId,controller:new AbortController()};
  job.promise=(needsBootstrap?request('/api/shell?'+query,{signal:job.controller.signal}):request('/api/shell/resources',{method:'POST',body:requestScope,signal:job.controller.signal})).then(next=>{
   if(!alive.current)return;
   if(needsBootstrap){
    if(next.clientId!==clientId||!next.shellBootstrapKey||!next.hostInstanceId||!next.instanceGenerations)throw Error('Invalid shell bootstrap.');
    const currentKey=latest.current.authoritative?.shellBootstrapKey,currentHost=latest.current.authoritative?.client?.hostInstanceId;
    if(currentKey!==bootstrapKey&&next.shellBootstrapKey!==currentKey||currentHost!==hostInstanceId&&next.hostInstanceId!==currentHost){diagnostics.current.record('stale',{demandId:requested.id});return}
    if(latest.current.bootstrap?.hostInstanceId!==next.hostInstanceId)for(const host of hosts.current.values())host.acknowledgedView=null;
    latest.current.bootstrap=next;latest.current.bootstrapObservedKey=bootstrapKey;latest.current.bootstrapObservedHost=hostInstanceId;
    return receive(next.shellResources);
   }else return receive(next);
  }).catch(error=>{
   if(!alive.current||error.name==='AbortError')return;
   if(requested.id!==latest.current.demand.id){diagnostics.current.record('stale',{demandId:requested.id});return}
   diagnostics.current.record(error.status===409?'scope-mismatch':'error',{demandId:requested.id,status:error.status});
   if(transportFailure(error)&&latest.current.data&&latest.current.connection.onError)latest.current.connection.onError(error);
   else setError(error.message);
  }).finally(()=>{
   const timing=recentRequestTimings().filter(row=>row.route===(needsBootstrap?'/api/shell':'/api/shell/resources')).at(-1);
   if(timing)diagnostics.current.record(kind,{demandId:requested.id,...timing});
   inflight.current=null;
   // Only a genuinely newer demand gets a follow-up. Errors and repeated
   // notifications cannot sustain an identical request loop.
   const now=latest.current,newKey=now.authoritative?.shellBootstrapKey,newHost=now.authoritative?.client?.hostInstanceId;
   const changedDescriptor=newKey!==job.bootstrapKey&&newKey!==now.bootstrap?.shellBootstrapKey&&newKey!==now.bootstrapObservedKey
    ||newHost!==job.hostInstanceId&&newHost!==now.bootstrap?.hostInstanceId&&newHost!==now.bootstrapObservedHost;
   if(alive.current&&(changedDescriptor||now.demand.id!==job.id&&synced.current!==now.demand.id))void refresh(changedDescriptor?'composition':'demand');
  });
  inflight.current=job;return job.promise;
 },[clientId,receive]);
 useLayoutEffect(()=>{
  const current=latest.current;
  const host=state?.client?.hostInstanceId;
  if(current.bootstrap&&current.data&&(!matchesResources(current.data.shellResources,current.bootstrap,current.demand)
   ||host&&host!==current.bootstrap.hostInstanceId&&host!==current.bootstrapObservedHost)){
   publish({...current.data,snapshots:pendingSnapshots(current.bootstrap,current.data,current.demand,host)});setError('');
  }
 },[demandKey,state?.client?.hostInstanceId,publish]);
 useEffect(()=>{
  if(!state)return;
  const bootstrap=latest.current.bootstrap;
  const host=state.client?.hostInstanceId;
  const descriptorChanged=bootstrap&&(state.shellBootstrapKey!==bootstrap.shellBootstrapKey&&state.shellBootstrapKey!==latest.current.bootstrapObservedKey
   ||host&&host!==bootstrap.hostInstanceId&&host!==latest.current.bootstrapObservedHost);
  if(!bootstrap||descriptorChanged||synced.current!==demand.current.id)void refresh(!bootstrap?'bootstrap':descriptorChanged?'composition':'demand');
 },[demandKey,state?.shellBootstrapKey,state?.client?.hostInstanceId,refresh]);
 useEffect(()=>{if(state?.shellResources)receive(state.shellResources)},[state?.shellResources,receive,demandKey]);
 useEffect(()=>{
  if(!reconnectAfter.current||state===reconnectAfter.current)return;
  reconnectAfter.current=null;
  if(latest.current.bootstrap&&!state?.shellResources)void refresh('reconnect');
 },[state,reconnectTick,refresh]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;inflight.current?.controller.abort()}},[]);
 // Selection intent is private and immediate; it never causes a catalog GET.
 useLayoutEffect(()=>{
  for(const host of hosts.current.values()){
   const snapshot=navigationSnapshot(host.snapshot,displayState,host.scope);
   if(snapshot!==host.snapshot){host.snapshot=freeze(snapshot);host.listeners.forEach(fn=>fn())}
  }
 },[displayState?.selectedSessionId,displayState?.selectedWorkspaceId,displayState?.navigationPending,displayState?.view?.workSurface,displayState?.view?.workWorkspaceId]);
 useEffect(()=>{
  const onShell=e=>{if(e.detail.shellClientId===clientId)diagnostics.current.record('notify')};
  const visibility=()=>{setVisible(!document.hidden);diagnostics.current.record('visibility')};
  const reconnect=()=>{diagnostics.current.record('reconnect');reconnectAfter.current=latest.current.authoritative;setReconnectTick(value=>value+1)};
  window.addEventListener('amplifier-shell',onShell);window.addEventListener('amplifier-reconnected',reconnect);document.addEventListener('visibilitychange',visibility);
  return()=>{window.removeEventListener('amplifier-shell',onShell);window.removeEventListener('amplifier-reconnected',reconnect);document.removeEventListener('visibilitychange',visibility)};
 },[clientId]);
 const hostFor=useCallback(instance=>{
  const generation=latest.current.data?.instanceGenerations?.[instance.id];
  const key=instance.id+':'+generation;
  if(!hosts.current.has(key)){
   const record={id:instance.id,generation,scope:instance.scope,listeners:new Set(),pending:[],snapshot:freeze(navigationSnapshot(latest.current.data?.snapshots?.[instance.id]||empty,latest.current.state,instance.scope))};
   let queue=Promise.resolve();
   const run=(action,args)=>{
    const pending=action==='shell.view.update'?{patch:args.patch}:null;
    if(pending){record.pending.push(pending);record.snapshot=freeze({...record.snapshot,view:{...record.snapshot.view,...pending.patch}});record.listeners.forEach(fn=>fn())}
    const navigation=action==='shell.command'&&args.action==='session.select',submittedHost=latest.current.bootstrap?.hostInstanceId;
    // Enter the main navigation controller during the gesture, before waiting
    // on earlier shell work. Its request still observes that ordering barrier.
    const immediate=navigation?latest.current.dispatch(action,{...args,generation},{before:queue,
     navigationSession:record.snapshot.sessions?.find(row=>row.id===args.args?.id)
      ||record.snapshot.chatNavigation?.items?.find(row=>row.id===args.args?.id)
      ||record.snapshot.sidebarNavigation?.pinned?.items?.find(row=>row.id===args.args?.id)
      ||record.snapshot.sidebarNavigation?.recent?.items?.find(row=>row.id===args.args?.id)}):null;
    const execute=async()=>{
     const commandHost=immediate?submittedHost:latest.current.bootstrap?.hostInstanceId;
     try{
      const result=await (immediate||latest.current.dispatch(action,{...args,generation}));
      if(action==='shell.command'&&args.action==='session.select'&&!latest.current.state?.view?.navPinned)await latest.current.dispatch('view.update',{patch:{navExpanded:false}});
      const committed=result.result?.shellView;
      if(committed&&commandHost===latest.current.bootstrap?.hostInstanceId
       &&(!latest.current.authoritative?.client?.hostInstanceId||commandHost===latest.current.authoritative.client.hostInstanceId)
       &&committed.instanceId===record.id&&committed.generation===generation&&latest.current.data?.instanceGenerations?.[record.id]===generation
       &&committed.viewRevision>=(latest.current.data?.snapshots?.[record.id]?.viewRevision||0)){
       record.acknowledgedView=committed;
       if(latest.current.data)publish({...latest.current.data,snapshots:{...latest.current.data.snapshots,
        [record.id]:{...latest.current.data.snapshots[record.id],view:committed.view,viewRevision:committed.viewRevision,generation}}});
       diagnostics.current.record('command-view');
      }
      return {accepted:result.accepted,result:result.result};
     }finally{
      if(pending){record.pending=record.pending.filter(item=>item!==pending);if(latest.current.data)publish(latest.current.data)}
     }
    };
    const promise=queue.then(execute,execute);queue=promise.catch(()=>{});return promise;
   };
   record.api=Object.freeze({apiVersion:'1.0',clientId,instanceId:instance.id,
    getSnapshot:()=>record.snapshot,refreshResources:async()=>{if(await refresh('manual')!==true)throw Error('Navigation resources could not be refreshed.');return record.snapshot},subscribe:fn=>{record.listeners.add(fn);return()=>{record.listeners.delete(fn);if(!record.listeners.size&&(latest.current.data?.instanceGenerations?.[instance.id])!==generation)hosts.current.delete(key)}},
    setDirty:dirty=>run('shell.view.update',{clientId,instanceId:instance.id,patch:{},dirty}),
    dispatch:(action,args={})=>action==='view.update'
     ?run('shell.view.update',{clientId,instanceId:instance.id,patch:args.patch||{}})
     :run('shell.command',{clientId,instanceId:instance.id,action,args}),
   });
   hosts.current.set(key,record);
  }
  return hosts.current.get(key).api;
 },[clientId,publish,refresh]);
 const statuses=useRef({}),statusCallbacks=useRef(new Map()),reportTimer=useRef(null);
 const savedComposition=recovery?defaultComposition:data?.effectiveComposition||defaultComposition;
 const composition=presentationEdits.length?{...savedComposition,presentation:{...savedComposition.presentation,...Object.assign({},...presentationEdits.map(edit=>edit.patch))}}:savedComposition;
 const recover=useCallback(async target=>{
  await dispatch('shell.recover',{clientId,target,expectedRevision:latest.current.data?.revision||0});
  await refresh('composition');
  if(recovery)location.href=location.pathname;
 },[clientId,dispatch,refresh]);
 const setPresentation=useCallback(patch=>{
  const edit={patch:{...patch}};
  setPresentationEdits(edits=>[...edits,edit]);
  const execute=async()=>{
   try{
    if(!latest.current.data)await refresh();
    const current=latest.current.data;
    if(!current)throw Error('Shell settings are not available yet.');
    const composition={...current.effectiveComposition,presentation:{...current.effectiveComposition.presentation,...patch}};
    const prepared=await dispatch('shell.changes.prepare',{clientId,expectedRevision:current.revision,composition},{presentation:true});
    await dispatch('shell.changes.apply',{clientId,expectedRevision:current.revision,changeId:prepared.result.id},{presentation:true});
    await refresh('composition');
   }finally{setPresentationEdits(edits=>edits.filter(item=>item!==edit))}
  };
  const result=presentationQueue.current.then(execute,execute);presentationQueue.current=result.catch(()=>{});return result;
 },[clientId,dispatch,refresh]);
 const report=useCallback((instances,message='')=>{
  const current=latest.current.data;if(!current||recovery)return;
  // Reporting bypasses the user's command queue: a slow validation request
  // must not block evidence of already-rendered content.
  return request('/api/actions',{method:'POST',body:{action:'shell.report',args:{clientId,revision:current.revision,previewId:current.preview?.id||null,instances,message}}}).catch(()=>{});
 },[clientId]);
 const statusFor=useCallback(instance=>{
  const generation=latest.current.data?.instanceGenerations?.[instance.id];
  const key=instance.id+':'+instance.package+':'+generation;
  for(const old of statusCallbacks.current.keys())if(old.startsWith(instance.id+':')&&old!==key)statusCallbacks.current.delete(old);
  if(!statusCallbacks.current.has(key)){
   statuses.current[instance.id]={package:instance.package,generation,status:'loading'};
   statusCallbacks.current.set(key,(status,message='')=>{
    // Late cleanup/errors from a replaced package cannot mark its replacement.
    const current=latest.current.data;
    if(!current?.resolvedInstances?.some(row=>row.id===instance.id&&row.package===instance.package)||current.instanceGenerations?.[instance.id]!==generation)return;
    statuses.current[instance.id]={package:instance.package,generation,status};
    clearTimeout(reportTimer.current);
    reportTimer.current=setTimeout(()=>report(renderedStatuses(latest.current.data,statuses.current),message),50);
   });
  }
  return statusCallbacks.current.get(key);
 },[report]);
 useEffect(()=>{
  clearTimeout(reportTimer.current);
  reportTimer.current=setTimeout(()=>report(renderedStatuses(data,statuses.current)),80);
  return()=>clearTimeout(reportTimer.current);
 },[data?.revision,data?.preview?.id,report]);
 return {data,error,composition,hostFor,recover,report,statusFor,refresh,setPresentation,ready:!!data,recovery,clientId};
}

class ModuleBoundary extends React.Component{
 state={error:null};
 static getDerivedStateFromError(error){return {error}}
 componentDidCatch(error){this.props.report('error',error.message)}
 render(){return this.state.error?<p role="alert">This component could not render. {this.state.error.message}</p>:this.props.children}
}
function Ready({View,host,report,workspaceHost,paired}){
 useEffect(()=>{report('ready')},[View,report]);
 return <View host={host} workspaceHost={workspaceHost} paired={paired}/>;
}
function ModuleInstance({instance,metadata,host,report,workspaceHost,paired}){
 const [loaded,setLoaded]=useState(null),[error,setError]=useState('');
 const builtin=builtins[instance.package];
 useEffect(()=>{
  let alive=true;setError('');
  if(builtin)return;
  if(!metadata?.url){setError(metadata?.error||'Package is unavailable.');report('error');return}
  report('loading');
  import(/* @vite-ignore */ metadata.url).then(module=>{
   const View=module.default({React});if(typeof View!=='function')throw Error('Invalid module factory.');
   if(alive)setLoaded({package:instance.package,View});
  }).catch(error=>{if(alive){setError(error.message);report('error',error.message)}});
  return()=>{alive=false};
 },[instance.package,metadata?.url,metadata?.error,builtin,report]);
 const View=builtin||(loaded?.package===instance.package?loaded.View:null);
 if(error)return <p role="alert">Component unavailable: {error}</p>;
 if(!View)return <p role="status">Loading component…</p>;
 return <ModuleBoundary key={instance.package+':'+host.getSnapshot().generation} report={report}><Ready View={View} host={host} report={report} workspaceHost={workspaceHost} paired={paired}/></ModuleBoundary>;
}
export function ShellModules({shell}){
 const instances=shell.composition.instances.filter(item=>item.slot==='navigation');
 if(!shell.ready)return <p role="status">Loading navigation…</p>;
 return <>
  {(shell.error||shell.recovery)&&<p role="alert">{shell.recovery?'Recovery mode: optional components are disabled.':shell.error}</p>}
  <div className="a-shell-modules">{instances.map((instance,index)=><section className="a-shell-instance" data-shell-instance={instance.id} data-shell-package={instance.package} hidden={!!instance.hideWhen&&!shell.data?.snapshots?.[instance.id]?.view?.workspaceDraft?.mode&&shell.data?.snapshots?.[instance.hideWhen.instanceId]?.view?.navChatScope===instance.hideWhen.navChatScope} style={{order:index}} key={instance.id}>
   <ModuleInstance instance={instance} metadata={shell.data?.packages?.[instance.package]} host={shell.hostFor(instance)} report={shell.statusFor(instance)}
    paired={instance.package==='builtin.workspaces'&&shell.composition.instances.some(item=>item.id===instance.hideWhen?.instanceId&&item.package==='builtin.chats')}
    workspaceHost={instance.package==='builtin.chats'&&shell.composition.instances.some(item=>item.package==='builtin.workspaces'&&item.hideWhen?.instanceId===instance.id)?shell.hostFor(shell.composition.instances.find(item=>item.package==='builtin.workspaces'&&item.hideWhen?.instanceId===instance.id)):undefined}/>

  </section>)}</div>
  <details className="a-shell-recovery"><summary>Shell controls</summary><button type="button" onClick={()=>shell.recover('lastGood')}>Restore last working shell</button><button type="button" onClick={()=>shell.recover('default')}>Restore default shell</button><a href="?shell=recovery">Open recovery mode</a></details>
 </>;
}

// The built-in presentation remains a registered fallback for legacy records.
// Contributions never own the surrounding conversation, draft or canvas nodes.
function BuiltinSlot({children,report}){
 useEffect(()=>{report('ready')},[report]);
 return children;
}
export function ShellSlot({name,instanceId,children}){
 const shell=useShellContext();
 if(!shell)return children||null;
 const instances=(shell.data?.resolvedInstances||[]).filter(row=>row.slot===name&&(!instanceId||row.id===instanceId));
 if(!instances.length)return null;
 const nodes=instances.map(instance=>({builtin:instance.package===shell.data?.slots?.[name]?.default,
  element:instance.package===shell.data?.slots?.[name]?.default
   ?<ModuleBoundary key={instance.id+':'+shell.data?.instanceGenerations?.[instance.id]} report={shell.statusFor(instance)}><BuiltinSlot report={shell.statusFor(instance)}>{children}</BuiltinSlot></ModuleBoundary>
   :<div className="a-shell-contribution" data-shell-component={instance.id} data-shell-slot={name} key={instance.id}><ModuleInstance instance={instance} metadata={shell.data?.packages?.[instance.package]} host={shell.hostFor(instance)} report={shell.statusFor(instance)}/></div>}));
 if(['app.actions','app.status'].includes(name)&&nodes.some(row=>!row.builtin))return <>{nodes.filter(row=>row.builtin).map(row=>row.element)}<SlotOverflow name={name}>{nodes.filter(row=>!row.builtin).map(row=>row.element)}</SlotOverflow></>;
 return nodes.map(row=>row.element);
}
