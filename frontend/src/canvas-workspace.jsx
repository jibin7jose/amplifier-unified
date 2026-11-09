import {CanvasControl,CanvasViewControls,useCanvasVersionLoad,canvasLoadSelection,canvasLoadBinding,acceptedCanvasLoad,failedCanvasLoad} from './canvas-controls';
import React,{useEffect,useMemo,useRef,useState} from 'react';
import {request} from './api';
import {CanvasAppViewer} from './canvas-app-viewer';
import {CanvasViewer,GifCanvasViewer} from './canvas-viewer';
import {BrowserPreview} from './canvas-library';
import {McpAppViewer} from './mcp-app-viewer';
import {A2UISurface} from './a2ui';
import './canvas-workspace.css';
import {useCanvasReloadGuard} from './canvas-reload-guard.jsx';
import {hasLocalCanvasEdits} from './canvas-reload-guard.js';
import {ArtifactFileDetails} from './artifact-file-details';
import {ContextInspector} from './context-inspector';

const Surface=({canvas,act})=><A2UISurface surface={canvas.surface} act={act}/>;
// Every built-in format and every contributed renderer enters the same host.
const builtins=Object.fromEntries(['markdown','text','code','json','jsonl','image','pdf','html','babylon','mermaid','dot'].map(kind=>['builtin.canvas.'+kind,CanvasViewer]));
// Native retained references are not new contributed-renderer SDK kinds.
builtins['builtin.canvas.gif']=GifCanvasViewer;
// A native live view, not an HTML artifact or another prompt-body store.
builtins['builtin.canvas.context-inspector']=({canvas,dispatch})=><ContextInspector session={{id:canvas.sessionId}} act={dispatch} open={panel=>dispatch('view.update',{patch:{panel}})}/>;
Object.assign(builtins,{'builtin.canvas.settings':({canvas,dispatch})=><button type="button" className="a-soft" onClick={()=>dispatch('canvas.select',{id:canvas.id})}>Open Settings in Canvas</button>,'builtin.canvas.browser':BrowserPreview,'builtin.canvas.mcp-app':McpAppViewer,'builtin.canvas.a2ui':Surface});
const targetOf=view=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
const factoryCache=new Map();
async function loadRenderer(url){
 if(!factoryCache.has(url))factoryCache.set(url,import(/* @vite-ignore */ url).then(module=>{
  const Component=module.default({React});if(typeof Component!=='function')throw Error('The renderer did not export a component.');return Component;
 }).catch(error=>{factoryCache.delete(url);throw error}));
 return factoryCache.get(url);
}

class RendererBoundary extends React.Component{
 state={error:null};
 static getDerivedStateFromError(error){return {error}}
 componentDidCatch(error){this.props.onError(error.message)}
 render(){return this.state.error?this.props.fallback:this.props.children}
}
function Mounted({report}){useEffect(()=>{report('ready','Renderer mounted')},[report]);return null}

export function Renderer({view,canvas,dispatch,recovery,connectionKey}){
 const reloadGuard=useCanvasReloadGuard();
 const targetKey=JSON.stringify(targetOf(view));
 const target=useMemo(()=>JSON.parse(targetKey),[targetKey]);
 const listeners=useRef(new Set()),snapshot=useMemo(()=>({visible:!!canvas.open,viewId:view.viewId,resource:{id:canvas.id,kind:canvas.kind,title:canvas.title,content:canvas.content,surface:canvas.surface,url:canvas.url,path:canvas.path,revision:view.resourceRevision},view:canvas.view||{}}),[canvas,view.viewId,view.resourceRevision]);
 const latest=useRef(snapshot);latest.current=snapshot;
 useEffect(()=>{listeners.current.forEach(listener=>listener())},[snapshot]);
 const host=useMemo(()=>Object.freeze({apiVersion:'1.0',instanceId:target.viewId,viewId:target.viewId,
  getSnapshot:()=>latest.current,subscribe:listener=>{listeners.current.add(listener);return()=>listeners.current.delete(listener)},
  readSource:()=>request(`/api/canvas/views/${target.viewId}/source?`+new URLSearchParams(target)).then(result=>result.content),
  dispatch:(action,args={})=>{
   const mapped={'view.update':'canvas.view','view.report':'canvas.report'}[action];
   if(!mapped)return Promise.reject(Error('Unsupported renderer capability.'));
   return dispatch('canvas.views.command',{...target,action:mapped,args});
  },setDirty:dirty=>reloadGuard.declare(!!dirty,()=>dispatch('canvas.views.dirty',{...target,dirty:!!dirty}))
 }),[target,dispatch,reloadGuard]);
 const report=useMemo(()=> (status,message)=>{
  const result=dispatch('canvas.views.status',{...target,status,message:String(message).slice(0,2000)});
  result.catch(()=>{});return result; // Observe ignored mount reports; preserve settlement for media.
 },[target,dispatch]);
 const bypassed=recovery&&!view.renderer.startsWith('builtin.');
 const reportMounted=useMemo(()=>(status,message)=>report(bypassed||!view.available?'error':status,bypassed?'Recovery mode is displaying the standard viewer.':!view.available?'The saved renderer is unavailable; displaying the standard viewer.':message),[report,bypassed,view.available]);
 const act=useMemo(()=> (action,args)=>{
  const result=dispatch('canvas.views.command',{...target,action,args});
  if(action==='canvas.report'){result.catch(()=>{});return result}
  return action==='canvas.reference'?result:result.catch(()=>{});
 },[target,dispatch]);
 const builtin='builtin.canvas.'+canvas.kind,Default=builtins[builtin];
 const choice=view.choices.find(choice=>choice.id===view.renderer);
 const selected=recovery||!choice?builtin:view.renderer;
 const [external,setExternal]=useState(null),[error,setError]=useState('');
 useEffect(()=>{
  let current=true;setExternal(null);setError('');
  if(builtins[selected])return;
  report('loading','Loading renderer');
  loadRenderer(choice.url).then(Component=>{if(current)setExternal(()=>Component)}).catch(error=>{if(current){setError(error.message);report('error',error.message)}});
  return()=>{current=false};
 },[selected,choice?.url,report]);
 const fail=message=>{setError(message);report('error',message)};
 const fallback=Default?<Default canvas={canvas} act={act} dispatch={dispatch} reportStatus={reportMounted} connectionKey={connectionKey}/>:<p role="alert">No standard viewer supports this artifact.</p>;
 const Builtin=builtins[selected],Component=external;
 return <>
  {(error||!choice||recovery)&&<div role="status" className="a-renderer-notice">{recovery?'Recovery mode uses standard viewers.':error?`This renderer needs attention: ${error}`:'The saved renderer is unavailable. Showing the standard viewer; your artifact is retained.'}<button type="button" className="a-link" onClick={()=>dispatch('canvas.views.recover',target)}>Use standard viewer</button></div>}
  {Builtin?<><Builtin canvas={canvas} act={act} dispatch={dispatch} reportStatus={reportMounted} connectionKey={connectionKey}/>{canvas.kind!=='gif'&&<Mounted report={reportMounted}/>}</>:Component?<RendererBoundary onError={fail} fallback={fallback}><Component host={host}/><Mounted report={reportMounted}/></RendererBoundary>:error?fallback:<p role="status">Loading viewer…</p>}
 </>;
}

function ResourceView({view,state,dispatch,recovery}){
 const [error,setError]=useState(''),[choice,setChoice]=useState(null),[choosing,setChoosing]=useState(false),choicePending=useRef(false);
 const {state:[load,setLoad],pending:loadPending}=useCanvasVersionLoad();
 const selection=canvasLoadSelection(view),binding=canvasLoadBinding(view);
 const unresolved=load?.selection===selection&&load.needsCheck;
 const canvas=useMemo(()=>state.canvas?{...state.canvas,...(view.app?{app:view.app}:{}),...targetOf(view),renderIdentity:view.renderIdentity,view:view.view||state.canvas.view,renderReports:view.renderReports,quality:view.quality}:null,[state.canvas,view]);
 const server=state.smartTools?.servers?.find(row=>row.id===canvas?.mcp?.serverId);
 const connectionKey=JSON.stringify([server?.status,server?.catalogState,server?.catalogRevision,server?.account,server?.accountBinding,canvas?.mcp?.catalogRevision,canvas?.mcp?.contractFingerprint]);
 const run=async(action,args)=>{try{const result=await dispatch(action,args);setError(result?.result?.status==='deferred'?result.result.reason:'')}catch(error){setError(error.message)}};
 const choose=async(version,confirmed=false)=>{
  if(choicePending.current||loadPending.current||unresolved)return;
  if(hasLocalCanvasEdits()){setError('Save or cancel unfinished Canvas edits before changing the preview.');return}
  const executable=['html','babylon'].includes(canvas?.kind)&&!canvas?.app;
  if(executable&&!confirmed&&(version??view.latestVersion)!==view.runningVersion){
   setChoice({id:view.resourceId,version,exactVersion:version??view.latestVersion,target:targetOf(view)});return;
  }
  choicePending.current=true;setChoosing(true);
  if(confirmed){loadPending.current=true;setLoad({selection,binding,pending:true,source:'details'})}
  try{
   const result=confirmed
    ?await dispatch('canvas.views.loadVersion',{...choice.target,version:choice.exactVersion,followLatest:choice.version===null})
    :await dispatch('canvas.select',{id:view.resourceId,...(version!=null?{version}:{})});
   if(!result?.accepted)throw Error('The saved version was not selected.');
   if(confirmed){const feedback=acceptedCanvasLoad(result);setLoad(feedback?{selection,binding,source:'details',...feedback}:null)}
   setChoice(null);setError('');
  }catch(error){
   if(confirmed){const feedback=failedCanvasLoad(error);setLoad({selection,binding,source:'details',...feedback});if(feedback.needsCheck)setChoice(null)}
   else setError(error.message);
  }
  finally{choicePending.current=false;setChoosing(false);if(confirmed)loadPending.current=false}
 };
 useEffect(()=>{setChoice(null);setError('')},[view.resourceId,view.resourceRevision,view.generation]);
 if(canvas?.kind==='context-inspector')return <section className="a-resource-view" data-canvas-view={view.viewId} aria-label="Context inspector view"><div className="a-resource-renderer"><Renderer key={view.resourceId} view={view} canvas={canvas} dispatch={dispatch} recovery={recovery}/></div></section>;
 return <CanvasViewControls label="Artifact controls"><section className="a-resource-view" data-canvas-view={view.viewId} aria-label="Artifact view">
  <CanvasControl><div className="a-resource-view-controls a-canvas-toolbar">
   <strong>{canvas?.title}</strong>
   {!!view.versions?.length&&<><label>Version<select aria-label="Artifact version" disabled={!!unresolved} value={view.selectedVersion??'latest'} onChange={event=>choose(event.target.value==='latest'?null:Number(event.target.value))}>
    <option value="latest">Latest · Version {view.latestVersion}</option>
    {view.versions.slice().reverse().map(version=><option key={version.version} value={version.version}>Version {version.version}{version.createdAt?' · '+new Date(version.createdAt*1000).toLocaleString():''}</option>)}
   </select></label>{view.selectedVersion!=null&&<><button type="button" className="a-link" onClick={()=>choose(null)}>Go to Latest</button>{canvas?.app&&<button type="button" className="a-soft" onClick={()=>run('canvas.apps.restore',{id:view.resourceId,sessionId:canvas.sessionId,version:view.selectedVersion,expectedRevision:view.latestVersion,expectedStateRevision:view.latestStateRevision})}>Restore as new version</button>}{!canvas?.app&&!['mcp-app','browser','gif'].includes(canvas?.kind)&&<button type="button" className="a-soft" onClick={()=>run('canvas.versions.restore',{id:view.resourceId,sessionId:canvas.sessionId,version:view.selectedVersion,expectedRevision:view.latestVersion})}>Restore as new version</button>}</>}</>}
   {choice?.id===view.resourceId&&<div role="group" aria-label="Confirm saved preview version"><p>Loading v{choice.exactVersion} resets the current preview, discards its local input and runs the saved code.</p><button type="button" className="a-soft" disabled={choosing} onClick={()=>setChoice(null)}>Keep current preview</button><button type="button" className="a-soft" disabled={choosing} onClick={()=>choose(choice.exactVersion,true)}>Load v{choice.exactVersion} — resets preview and runs its code</button></div>}
   <label>Open with<select aria-label="Open with" value={view.renderer} onFocus={()=>dispatch('canvas.views.inspect',{}).catch(()=>{})} onChange={event=>run('canvas.views.renderer',{...targetOf(view),renderer:event.target.value})}>
    {!view.available&&<option value={view.renderer}>Unavailable renderer</option>}
    {view.choices.map(choice=><option key={choice.id} value={choice.id}>{choice.label}</option>)}
   </select></label>
   <ArtifactFileDetails target={{id:view.resourceId,version:view.runningVersion??view.selectedVersion??view.latestVersion,sessionId:canvas?.sessionId}} placement={JSON.stringify([view.filePaths?.base,view.filePaths?.workspace])} dispatch={dispatch}>
    {view.fileReveal&&<><p>On {view.fileReveal.host.label}</p><p>{view.fileReveal.detail}</p>{view.fileReveal.available?<button type="button" onClick={()=>run('canvas.views.reveal',{...targetOf(view),hostId:view.fileReveal.host.id,instanceId:view.fileReveal.instanceId})}>{view.fileReveal.label} on {view.fileReveal.host.label}</button>:<p>No supported desktop file manager is available on this host.</p>}</>}
   </ArtifactFileDetails>
  </div></CanvasControl>
  {view.selectedVersion!=null&&<p role="status" className="a-renderer-notice">{canvas?.kind==='browser'?'Saved address · Website is live':'Saved version · Read only'}{canvas?.kind==='mcp-app'&&<button type="button" className="a-link" onClick={()=>run('canvas.select',{id:view.resourceId})}>Open live view</button>}</p>}
  {error&&<p role="alert" className="a-renderer-notice">{error}</p>}
  {load?.selection===selection&&load.source==='details'&&load.error&&(load.needsCheck||load.binding===binding)&&<CanvasControl><p role="alert" className="a-renderer-notice">{load.error}{load.needsCheck&&' Close Canvas details to use Refresh.'}</p></CanvasControl>}
  <div className="a-resource-renderer">{canvas?.app?<CanvasAppViewer key={view.resourceId+':'+(view.selectedVersion??'latest')} canvas={canvas} dispatch={dispatch}/>:canvas?<Renderer key={view.resourceId+':'+(view.renderIdentity||view.resourceRevision+':'+view.generation)} view={view} canvas={canvas} dispatch={dispatch} recovery={recovery} connectionKey={connectionKey}/>:<p role="status">Loading saved artifact…</p>}</div>
 </section></CanvasViewControls>;
}

export function CanvasWorkspace({state,dispatch,hidden=false}){
 const retained=useRef(new Map()),allViews=(state.canvasWorkspace?.views||[]).filter(view=>view.viewId==='primary'&&!state.canvas?._presentationDisposed);
 // Do not initialize a newly selected hidden artifact. Retain the mounted
 // viewer while Library is open so local edits survive passive navigation.
 for(const id of retained.current.keys())if(!allViews.some(view=>view.viewId===id)||(state.canvas?.kind==='gif'&&!state.canvas.open))retained.current.delete(id);
 const views=allViews.filter(view=>{
  if(state.canvas?.open)retained.current.set(view.viewId,view.resourceId);
  return retained.current.get(view.viewId)===view.resourceId;
 });
 const recovery=typeof location!=='undefined'&&new URLSearchParams(location.search).get('shell')==='recovery';
 // Passive Library navigation must not destroy renderer-local edits or frames.
 return <div className="a-canvas-workspace" hidden={hidden} inert={hidden}>{views.map(view=><ResourceView key={view.viewId} view={view} state={state} dispatch={dispatch} recovery={recovery}/>)}</div>;
}
