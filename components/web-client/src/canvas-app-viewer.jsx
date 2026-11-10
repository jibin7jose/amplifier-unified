import React,{useEffect,useRef,useState} from 'react';
import {clientUrl,request} from './api';
import {registerSurfaceCheckpoint} from './surface-checkpoint';
import './canvas-app-viewer.css';
import {CanvasControl} from './canvas-controls';
import {useCanvasReloadGuard} from './canvas-reload-guard.jsx';

const targetOf=canvas=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,canvas[key]]));
const tokens=['bg','surface','soft','ink','muted','line','accent','tint','green','danger'];
function hostTheme(){
 const root=document.getElementById('amp-one'),style=getComputedStyle(root||document.documentElement);
 return {version:1,tokens:Object.fromEntries(tokens.map(name=>[name,style.getPropertyValue('--a-'+name).trim()])),
  scheme:style.colorScheme==='dark'?'dark':style.colorScheme==='light'?'light':matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light',
  reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,contrast:matchMedia('(prefers-contrast: more)').matches?'more':'normal'};
}

export function CanvasAppViewer({canvas,dispatch}){
 const reloadGuard=useCanvasReloadGuard();
 const [mounted,setMounted]=useState(canvas),[error,setError]=useState(''),[renderError,setRenderError]=useState(''),[dirty,setDirty]=useState(false),[review,setReview]=useState(null);
 const frame=useRef(),latest=useRef(canvas),shown=useRef(mounted),dirtyRef=useRef(false),busy=useRef(0),editVersion=useRef(0),channel=useRef(crypto.randomUUID()),seen=useRef(new Map()),dirtyWrites=useRef(Promise.resolve()),sent=useRef(),reported=useRef();
 if(canvas.app.revision!==latest.current.app.revision||canvas.app.stateRevision>=latest.current.app.stateRevision)latest.current=canvas;
 shown.current=mounted;
 const app=canvas.app;
 const checkpoints=useRef(new Map());
 const mount=value=>{editVersion.current=0;channel.current=crypto.randomUUID();seen.current.clear();setReview(null);setError('');setRenderError('');sent.current=null;reported.current=null;setMounted(value)};
 const send=(force=false)=>{
  const current=latest.current;
  if(current.app.revision!==shown.current.app.revision)return;
  const snapshot={id:current.id,app:current.app,visible:!!current.open,theme:hostTheme()};
  const key=JSON.stringify([channel.current,current.app.revision,current.app.stateRevision,snapshot.visible,snapshot.theme]);
  if(!force&&key===sent.current)return;
  sent.current=key;
  frame.current?.contentWindow?.postMessage({type:'canvas-app-host',id:current.id,channel:channel.current,snapshot},'*');
 };
 const declare=(value,version=editVersion.current)=>{
  if(version!==editVersion.current)return Promise.resolve();
  dirtyRef.current=value;setDirty(value);
  const target=targetOf(shown.current),epoch=channel.current;
  const write=reloadGuard.declare(value,()=>dirtyWrites.current.catch(()=>{}).then(()=>dispatch('canvas.views.dirty',{...target,dirty:value,editVersion:version})));
  dirtyWrites.current=write;
  return write.catch(e=>{if(epoch===channel.current){dirtyRef.current=true;setDirty(true);setError(e.message)}throw e});
 };
 useEffect(()=>{
  if(canvas.app.revision!==mounted.app.revision&&!dirtyRef.current&&!busy.current){mount(canvas)}
  else send();
 },[canvas]);
 useEffect(()=>{
  const unregister=registerSurfaceCheckpoint(canvas.sessionId,identity=>new Promise(resolve=>{
   const requestId=identity||crypto.randomUUID(),timer=setTimeout(()=>{checkpoints.current.delete(requestId);resolve()},900);
   checkpoints.current.set(requestId,()=>{clearTimeout(timer);resolve()});
   frame.current?.contentWindow?.postMessage({type:'canvas-app-host',id:latest.current.id,channel:channel.current,checkpoint:requestId},'*');
  }));
  const receive=async event=>{
   const data=event.data;
   if(event.source!==frame.current?.contentWindow||data?.type!=='canvas-app'||data.id!==latest.current.id)return;
   if(data.op==='ready'){send(true);return}
   if(data.channel!==channel.current)return;
   if(data.op==='observation'){
    const value=data.observation;
    if(!value||JSON.stringify(value).length>480000)return;
    try{await request('/api/actions',{method:'POST',body:{action:'canvas.views.observe',args:{...value,...targetOf(shown.current),...(data.checkpoint?{checkpointId:data.checkpoint}:{})},id:crypto.randomUUID()}})}catch{}
    checkpoints.current.get(data.checkpoint)?.();checkpoints.current.delete(data.checkpoint);return;
   }
   if(data.op==='status'){
    const status=data.status==='error'?'error':'ready',message=String(data.message||'').slice(0,1000),key=status+':'+message;
    if(key===reported.current)return;reported.current=key;
    dispatch('canvas.views.status',{...targetOf(shown.current),status,message}).catch(()=>{});
    setRenderError(status==='error'?message:'');return;
   }
   if(data.op==='editing'&&latest.current.readOnlyVersion)return;
   if(data.op==='editing'){if(Number.isSafeInteger(data.editVersion)&&data.editVersion>editVersion.current){editVersion.current=data.editVersion;declare(true).catch(()=>{})}return}
   if(!['state','event','request','dirty'].includes(data.op)||typeof data.requestId!=='string'||data.requestId.length>80)return;
   const reply=extra=>frame.current?.contentWindow?.postMessage({type:'canvas-app-host',id:latest.current.id,channel:channel.current,requestId:data.requestId,...extra},'*');
   if(latest.current.readOnlyVersion){reply({error:'This saved version is read-only. Select Latest to interact.'});return}
   if(JSON.stringify(data).length>320000){reply({error:'This change exceeds the surface message size limit.'});return}
   if(data.revision!==shown.current.app.revision){reply({error:'This design changed. Inspect the current surface before retrying.'});return}
   if(seen.current.has(data.requestId)){const previous=seen.current.get(data.requestId);if(previous)reply(previous);return}
   if(seen.current.size>=128){const oldest=[...seen.current].find(([,value])=>value!==null);if(oldest)seen.current.delete(oldest[0]);else{reply({error:'Wait for pending surface changes to finish.'});return}}
   seen.current.set(data.requestId,null);busy.current++;const editing=data.editVersion,epoch=channel.current;
   try{
    let result;
    if(data.op==='dirty'){await declare(!!data.args?.dirty,editing);result={app:latest.current.app}}
    else{
     const allowed=data.op==='state'?{patch:data.args?.patch}:data.op==='event'?{name:data.args?.name,payload:data.args?.payload}:{name:data.args?.name,input:data.args?.input};
     result=(await dispatch('canvas.apps.'+data.op,{...allowed,id:shown.current.id,sessionId:shown.current.sessionId,
      expectedRevision:shown.current.app.revision,expectedStateRevision:data.stateRevision}, {id:channel.current+':'+data.requestId})).result;
     if(result.app.revision===latest.current.app.revision&&result.app.stateRevision>=latest.current.app.stateRevision)
      latest.current={...latest.current,app:result.app};
     if((data.op==='state'||data.op==='event')&&Number.isSafeInteger(data.commit)&&data.commit===editing&&editing===editVersion.current)await declare(false,editing);
    }
    if(epoch!==channel.current)return;
    const response={snapshot:{id:latest.current.id,app:result.app,theme:hostTheme()}};seen.current.set(data.requestId,response);reply(response);setError('');
   }catch(e){if(epoch!==channel.current)return;const response={error:e.message};seen.current.set(data.requestId,response);reply(response);setError(e.message)}
   finally{busy.current--;if(epoch===channel.current&&!busy.current&&!dirtyRef.current&&latest.current.app.revision!==shown.current.app.revision)mount(latest.current)}
  };
  window.addEventListener('message',receive);
  let scheduled;
  const refresh=()=>{cancelAnimationFrame(scheduled);scheduled=requestAnimationFrame(()=>send())};
  const observer=new MutationObserver(refresh);observer.observe(document.head,{subtree:true,childList:true,characterData:true});
  const root=document.getElementById('amp-one');if(root)observer.observe(root,{attributes:true,subtree:true,childList:true,characterData:true});
  const queries=['prefers-color-scheme: dark','prefers-reduced-motion: reduce','prefers-contrast: more'].map(q=>matchMedia('('+q+')'));
  queries.forEach(q=>q.addEventListener('change',refresh));
  return()=>{unregister();for(const done of checkpoints.current.values())done();checkpoints.current.clear();window.removeEventListener('message',receive);observer.disconnect();cancelAnimationFrame(scheduled);queries.forEach(q=>q.removeEventListener('change',refresh))};
 },[dispatch]);
 const args=()=>({id:canvas.id,sessionId:canvas.sessionId,expectedRevision:latest.current.app.revision,expectedStateRevision:latest.current.app.stateRevision});
 const run=async(action,extra)=>{try{const result=await dispatch(action,{...args(),...extra});setError('');return result}catch(e){setError(e.message)}};
 const preview=async request=>{const result=await dispatch('canvas.apps.inspect',{id:canvas.id,sessionId:canvas.sessionId,requestId:request.id});setReview({request,input:result.result.requestInput})};
 const url=clientUrl(`/api/canvas/${mounted.id}/app-host?`+new URLSearchParams(targetOf(mounted)));
 return <section className="a-canvas-app" aria-label="Interactive conversation surface">
  {dirty&&<p className="a-canvas-unsaved" role="status">Unsaved input</p>}
  <CanvasControl><div className="a-canvas-app-bar"><span>Revision {app.revision}</span>
   <details><summary>History & shared state</summary><p>Restoring a design keeps compatible current inputs and never repeats actions.</p>
    {app.versions.map(v=><button type="button" key={v.version} disabled={v.version===app.revision||dirty||canvas.readOnlyVersion} onClick={()=>run('canvas.apps.restore',{version:v.version})}>Restore revision {v.version}</button>)}
    <pre aria-label="Shared surface state">{JSON.stringify(app.state,null,2)}</pre>
   </details>
  </div></CanvasControl>
  {(error||renderError)&&<div className="a-canvas-app-errors" role="alert">{renderError&&<p>{renderError}</p>}{error&&error!==renderError&&<p>{error}</p>}</div>}
  {mounted.app.revision!==app.revision&&<p role="status">A new design is ready. Your unfinished input is still here. <button type="button" onClick={async()=>{const version=editVersion.current,epoch=channel.current;try{await reloadGuard.declare(false,()=>dispatch('canvas.views.dirty',{...targetOf(latest.current),dirty:false}));if(version!==editVersion.current||epoch!==channel.current)return;dirtyRef.current=false;setDirty(false);mount(latest.current)}catch(e){setError(e.message)}}}>Discard unfinished input and load revision {app.revision}</button></p>}
  {app.requests.filter(r=>r.status==='pending').map(r=><div className="a-canvas-app-request" key={r.id}><span>{r.action==='theme.preview'?'Preview on this device':r.action==='theme.apply'?'Apply to the shared shell':'Revert this device’s last theme change'}: {r.summary}</span><button type="button" onClick={()=>preview(r).catch(e=>setError(e.message))}>Review theme change</button><button type="button" onClick={()=>run('canvas.apps.resolve',{requestId:r.id,approve:false})}>Decline</button></div>)}
  {review&&app.requests.some(r=>r.id===review.request.id&&r.status==='pending')&&<section className="a-canvas-app-review" aria-label="Review requested theme change"><strong>{review.request.summary}</strong><p>{review.request.action==='theme.apply'?'Applying changes the shell for all connected clients.':'This action uses this device’s theme controls.'}</p><pre>{review.input.css||'End the preview, or undo the last applied theme if it is still current.'}</pre><button type="button" onClick={async()=>{if(await run('canvas.apps.resolve',{requestId:review.request.id,approve:true}))setReview(null)}}>Approve theme change</button><button type="button" onClick={()=>setReview(null)}>Back</button></section>}
  {canvas.readOnlyVersion&&<p role="status">Saved design, read only.{canvas.historicalStateUnavailable?' Inputs were not recorded for this older design.':' Inputs show the state saved when this version was created.'}</p>}
  <iframe key={mounted.app.revision} title={canvas.title} ref={frame} src={url} sandbox="allow-scripts allow-same-origin" onLoad={()=>send(true)}/>
 </section>;
}
