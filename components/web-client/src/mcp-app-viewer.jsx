import {CanvasControl} from './canvas-controls';
import {McpContractReview} from './mcp-contract-review';
import {clientUrl} from './api';
import React,{useEffect,useRef,useState} from 'react';
import {AppBridge,PostMessageTransport} from '@modelcontextprotocol/ext-apps/app-bridge';
import {AlertCircle,Check,Loader,RefreshCw} from 'lucide-react';
import {request} from './api';
import {useMcpAppTheme} from './mcp-app-theme';
import {MCP_VISIBILITY,useMcpAppVisibility} from './mcp-app-lifecycle';
import {createMcpReadGate} from './mcp-app-reads';

// The iframe can call tools only through its saved server binding. No host
// credentials, app_control handle, or server selector crosses postMessage.
async function command(action,args,signal){
 const accepted=await request('/api/actions',{method:'POST',body:{id:crypto.randomUUID(),action,args},signal});
 if(!accepted.operationId)return accepted;
 const deadline=Date.now()+310000;
 while(Date.now()<deadline){
  if(signal?.aborted)throw new Error('This view closed. Tool work has not been cancelled.');
  const op=await request(`/api/smart-tools/operations/${encodeURIComponent(accepted.operationId)}`,{signal});
  if(op.status==='completed')return op.result;
  if(op.status==='failed'&&op.result?.isError)return op.result;
  if(['failed','interrupted'].includes(op.status))throw new Error(op.error||'Tool work was interrupted; it was not replayed.');
  await new Promise(resolve=>setTimeout(resolve,350));
 }
 throw new Error('No completion received. Inspect Smart Tools activity before retrying.');
}

const viewTarget=canvas=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].filter(key=>canvas[key]!=null).map(key=>[key,canvas[key]]));
const viewUrl=(canvas,path,params={})=>{const query=new URLSearchParams({...viewTarget(canvas),...params}).toString();return `/api/canvas/${encodeURIComponent(canvas.id)}/${path}${query?'?'+query:''}`};

async function callTool(canvas,params,signal){
 const id=crypto.randomUUID(),deadline=Date.now()+310000;
 let op=await request(viewUrl(canvas,'tools/call'),{method:'POST',body:{id,expectedRevision:canvas.revision||1,name:params.name,arguments:params.arguments||{}},signal});
 // A long-running call may outlive the HTTP wait. Inspect the same receipt;
 // never retry the mutation if the connection itself was lost.
 while(['pending','running','queued'].includes(op.status)){
  if(signal?.aborted||Date.now()>=deadline)throw new Error('No completion received. Inspect Smart Tools activity before retrying; input was not replayed.');
  await new Promise(resolve=>setTimeout(resolve,350));
  op=await request(`/api/smart-tools/operations/${encodeURIComponent(id)}`,{signal});
 }
 if(op.status==='completed'||op.result?.isError)return op.result;
 throw new Error(op.error||'Tool work was interrupted; it was not replayed.');
}

function useHostTheme(scheme){
 const media=()=>window.matchMedia('(prefers-color-scheme: dark)');
 const [systemTheme,setSystemTheme]=useState(()=>media().matches?'dark':'light');
 useEffect(()=>{
  if(scheme!=='system')return;
  const query=media(),change=event=>setSystemTheme(event.matches?'dark':'light');
  setSystemTheme(query.matches?'dark':'light');
  query.addEventListener('change',change);
  return()=>query.removeEventListener('change',change);
 },[scheme]);
 return scheme==='dark'||scheme==='light'?scheme:systemTheme;
}

export function McpAppViewer({canvas,act,connectionKey}){
 const frame=useRef(null),current=useRef(canvas),bridgeRef=useRef(null),hostContext=useRef(null),themeRef=useRef(),[status,setStatus]=useState({phase:'loading',text:'Connecting tool view…'});
 const connection=useRef(null),booted=useRef(false),[recovery,setRecovery]=useState(null),[recovering,setRecovering]=useState(false);
 const renderReport=useRef(null),resetCatalog=useRef(null),refreshConnection=useRef(null),lastConnection=useRef(null);
 const rememberConnection=value=>{
  const previous=connection.current;connection.current=value;setRecovery(value);
  if(booted.current&&previous&&previous.status!=='ready'&&value?.status==='ready'){
   // The allowed lists become available again. Notify the existing app using
   // SDK catalog notifications; never replay input/results or remount its draft.
   resetCatalog.current?.();
   const bridge=bridgeRef.current;
   if(bridge)Promise.all([bridge.sendToolListChanged(),bridge.sendResourceListChanged()]).catch(error=>renderReport.current?.('error',error.message));
  }
 };
 const renderIdentity=canvas.renderIdentity||JSON.stringify([canvas.resourceRevision,canvas.generation]);
 const theme=useHostTheme(useMcpAppTheme());
 const visible=useMcpAppVisibility(canvas.open!==false&&!canvas.visibilityPending),visibleRef=useRef(visible);
 visibleRef.current=visible;
 current.current=canvas;
 themeRef.current=theme;
 useEffect(()=>{
  const bridge=bridgeRef.current,previous=hostContext.current;
  if(!bridge||!previous||previous.theme===theme&&previous[MCP_VISIBILITY]===visible)return;
  const next={...previous,theme,[MCP_VISIBILITY]:visible};
  hostContext.current=next;
  bridge.setHostContext(next);
 },[theme,visible]);
 useEffect(()=>{
  const controller=new AbortController();let bridge,live=true,initialized=false,lastReport='',availabilityRevision=0,latestAvailability;
  const reads=createMcpReadGate({isVisible:()=>visibleRef.current});
  let catalog;
  const clearCatalog=()=>{catalog=undefined};resetCatalog.current=clearCatalog;
  const listTools=()=>current.current.readOnlyVersion?Promise.resolve({tools:[]}):catalog||(catalog=request(viewUrl(current.current,'tools'),{signal:controller.signal}).catch(error=>{catalog=null;throw error}));
  const report=(phase,text)=>{if(!live||lastReport===phase+text)return;lastReport=phase+text;setStatus({phase,text});act('canvas.report',{id:canvas.id,part:'mcp-app',status:phase==='ready'?'ready':phase==='error'?'error':'pending',message:text})};
  renderReport.current=report;
  const inspectConnection=()=>{
   const check=++availabilityRevision;
   latestAvailability=(async()=>{try{
    const availability=await request(viewUrl(current.current,'status'),{signal:controller.signal});
    if(live&&check===availabilityRevision){
     rememberConnection(availability);
     if(initialized)report(['ready','saved_version'].includes(availability.status)?'ready':'error',availability.message);
     clearCatalog();
    }
    return availability;
   }catch(error){if(live&&check===availabilityRevision)report('error',error.message);throw error}})();
   return latestAvailability;
  };
  refreshConnection.current=inspectConnection;
  const start=async()=>{
   let pending=inspectConnection(),availability;
   while(live){
    try{availability=await pending}catch(error){if(pending===latestAvailability)throw error}
    if(pending===latestAvailability)break;pending=latestAvailability;
   }
   if(!live)return;
   if(availability.source!=='available'){report('error',availability.message);return}
   hostContext.current={theme:themeRef.current,[MCP_VISIBILITY]:visibleRef.current,displayMode:'inline',availableDisplayModes:['inline'],locale:navigator.language};
   bridge=new AppBridge(null,{name:'Amplifier Unified',version:'0.6.0'},
    {serverTools:{listChanged:true},serverResources:{listChanged:true},updateModelContext:{text:{},structuredContent:{}},sandbox:{permissions:{},csp:{connectDomains:[],resourceDomains:[],frameDomains:['blob:'],baseUriDomains:[]}}},
    {hostContext:hostContext.current});
   bridge.oncalltool=async params=>{
    const callAvailabilityRevision=availabilityRevision;
    try{
     if(current.current.readOnlyVersion)throw Error('This saved version is read-only. Select Latest for live features.');
     // Metadata is an optimization hint, not a new admission dependency. The
     // server still checks grants and schemas when an unknown call is sent.
     const tools=await listTools().catch(error=>{if(controller.signal.aborted)throw error;return {tools:[]}});
     const definition=tools.tools?.find(tool=>tool.name===params.name);
     const run=()=>callTool(current.current,params,controller.signal);
     const readOnly=definition?.annotations?.readOnlyHint===true&&definition?.annotations?.destructiveHint!==true;
     const result=await (readOnly?reads.run(params,run):run());
     // The tool owns its progress UI. Routine calls (including typing) must
     // not toggle host controls or publish a render report on every batch.
     if(result?.isError)report('error','The tool reported an error. See its result below.');
     else if(callAvailabilityRevision===availabilityRevision&&connection.current?.status==='ready')report('ready','Tool view connected');
     return result;
    }catch(error){if(!error.backgroundReadPaused)report('error',error.message);throw error}
   };
   bridge.onlisttools=listTools;
   const resource=(kind,params={})=>request(viewUrl(current.current,'resources',{kind,...params}),{signal:controller.signal});
   bridge.onreadresource=params=>resource('read',{uri:params.uri});
   bridge.onlistresources=params=>resource('list',params?.cursor?{cursor:params.cursor}:{});
   bridge.onlistresourcetemplates=params=>resource('templates',params?.cursor?{cursor:params.cursor}:{});
   bridge.onupdatemodelcontext=async context=>{
    if(current.current.readOnlyVersion)return {};
    await command('smartTools.context',{canvasId:canvas.id,expectedRevision:current.current.revision||1,context},controller.signal);return {};
   };
   bridge.oninitialized=async()=>{
    initialized=true;booted.current=true;
    try{
     await bridge.sendToolInput({arguments:current.current.mcp?.toolArguments||{}});
     const operationId=current.current.mcp?.operationId;
     const result=current.current.mcp?.savedResult?(await request(viewUrl(canvas,'result'),{signal:controller.signal})).result:operationId?(await request(`/api/smart-tools/operations/${encodeURIComponent(operationId)}`,{signal:controller.signal})).result:current.current.mcp?.result;
     if(result)await bridge.sendToolResult(result);
     const availability=connection.current;
     report(['ready','saved_version'].includes(availability?.status)?'ready':'error',availability?.message||'Tool view connected');
    }catch(error){report('error',error.message)}
   };
   bridge.onerror=error=>report('error',error.message);
   await bridge.connect(new PostMessageTransport(frame.current.contentWindow,frame.current.contentWindow));
   // Connect before navigation so even a fast inline App.initialize is heard.
   if(live){
    bridgeRef.current=bridge;
    const next={...hostContext.current,theme:themeRef.current,[MCP_VISIBILITY]:visibleRef.current};
    hostContext.current=next;
    bridge.setHostContext(next);
    frame.current.src=clientUrl(viewUrl(canvas,'document'));
   }
  };
  booted.current=false;rememberConnection(null);
  report('loading','Checking saved tool view…');start().catch(error=>report('error',error.message));
  const timeout=setTimeout(()=>{if(live&&!initialized)report('error',connection.current?.source==='available'?'The saved tool document did not initialize. Reload it to retry. Its scripts must work within the self-contained MCP App sandbox.':connection.current?.message||'The saved tool document could not be checked. Retry when the host is available.')},15000);
  return()=>{live=false;clearTimeout(timeout);reads.close();controller.abort();if(renderReport.current===report)renderReport.current=null;if(refreshConnection.current===inspectConnection)refreshConnection.current=null;if(resetCatalog.current===clearCatalog)resetCatalog.current=null;if(bridgeRef.current===bridge)bridgeRef.current=null;hostContext.current=null;bridge?.close().catch(()=>{})};
 },[canvas.id,renderIdentity,canvas.view?.reload]);
 // A second client or a backgrounded phone can keep its iframe mounted while
 // the shared connection changes. Refresh status only; never reconnect/replay.
 useEffect(()=>{
  const identity=JSON.stringify([canvas.id,renderIdentity,canvas.view?.reload]);
  const authority=JSON.stringify(viewTarget(canvas));
  const previous=lastConnection.current;
  lastConnection.current={identity,authority,connectionKey,visible};
  if(visible&&previous?.identity===identity&&(previous.connectionKey!==connectionKey||previous.authority!==authority||!previous.visible))refreshConnection.current?.().catch(()=>{});
 },[connectionKey,visible,canvas.id,renderIdentity,canvas.resourceRevision,canvas.generation,canvas.view?.reload]);
 const reconnect=async reviewedContract=>{
  setRecovering(true);
  try{
   const available=await request(viewUrl(current.current,'status'));rememberConnection(available);
   if(!available.canReconnect)throw new Error(available.message);
   const result=await command('smartTools.reconnectView',{canvasId:canvas.id,expectedBindingRevision:available.bindingRevision,...(reviewedContract?{reviewedContract}:{})});
   rememberConnection(result);
   if(result.status==='ready')resetCatalog.current?.();
   renderReport.current?.(result.status==='ready'&&booted.current?'ready':'error',result.status==='ready'&&!booted.current?'Tool reconnected. Reload the saved view to retry initialization.':result.message);
  }catch(error){
   try{rememberConnection(await request(viewUrl(current.current,'status')))}catch{}
   renderReport.current?.('error',error.message);
  }
  finally{setRecovering(false)}
 };
 return <div className="a-mcp-app-viewer" style={{display:'flex',flexDirection:'column',height:'100%',minHeight:0}}>
  <CanvasControl inline={status.phase==='error'}><div data-phase={status.phase} className={`a-mcp-status a-canvas-result ${status.phase==='error'?'error':status.phase==='ready'?'success':''}`} role="status">
   {status.phase==='error'?<AlertCircle/>:status.phase==='ready'?<Check/>:<Loader className="a-progress-spinner"/>}<span>{status.text}</span>
   {!canvas.readOnlyVersion&&<button type="button" className="a-icon" aria-label={recovering?'Reconnecting tool view…':'Reconnect tool view'} title={recovery?.canReconnect===false?recovery.message:'Reconnect without resetting unfinished inputs'} data-action="smartTools.reconnectView" disabled={recovering||recovery?.canReconnect===false} onClick={()=>reconnect()}><RefreshCw/></button>}
   {status.phase==='error'&&recovery?.source==='available'&&<button type="button" className="a-link" style={{whiteSpace:'nowrap'}} title="Reloading resets unfinished inputs in this tool view" data-action="canvas.view" onClick={()=>{if(window.confirm('Reload this saved tool view? Unfinished input inside it will be reset. Previous tool calls will not be replayed.'))act('canvas.view',{id:canvas.id,patch:{reload:Date.now()}})}}>Reload saved view</button>}
  </div></CanvasControl>
  <CanvasControl inline={recovery?.status!=='ready'}><McpContractReview canvas={canvas} recovery={recovery} command={command} onApplied={()=>refreshConnection.current?.()}/></CanvasControl>
  {canvas.sharedToolView&&<p className="a-caption">This view shares tool work with the original conversation.</p>}
  <iframe ref={frame} title={canvas.title||'Interactive tool'} className="a-canvas-html" style={{flex:1,minHeight:0}} sandbox="allow-scripts allow-downloads" referrerPolicy="no-referrer"/>
 </div>;
}
