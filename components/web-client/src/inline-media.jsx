import React,{useEffect,useRef,useState} from 'react';
import * as api from './api';
import {GifPlayer} from './gif-player';
import {MediaReporter} from './media-reporter';
import './inline-media.css';

let observationGeneration=0;
export function validateMediaMetadata(value,sessionId){
 const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
 const text=(value,limit)=>typeof value==='string'&&value.length>0&&value.length<=limit;
 const malformed=detail=>{throw new Error(`Malformed media metadata: ${detail}. Retry media metadata.`)};
 if(!object(value)||!Array.isArray(value.items)||value.items.length>20||
  typeof value.overflow!=='boolean'||!Number.isSafeInteger(value.mediaRevision)||value.mediaRevision<0)
  malformed('expected a bounded items array, boolean overflow and nonnegative media revision');
 const ids=new Set();
 for(const item of value.items){
  if(!object(item)||!text(item.id,100)||ids.has(item.id)||item.sessionId!==sessionId||
   !Number.isSafeInteger(item.version)||item.version<1||
   typeof item.sha256!=='string'||!/^[a-f0-9]{64}$/.test(item.sha256))
   malformed('invalid or duplicate media identity, session, version or SHA-256');
  if(typeof item.title!=='string'||[...item.title].length>200||!text(item.filename,4000))malformed('invalid media title or filename');
  if(!object(item.media)||!['image/gif','video/mp4'].includes(item.media.mimeType))
   malformed('expected GIF or MP4 metadata');
  if(item.media.mimeType==='image/gif'&&item.media.loopCount!==null&&
   (!Number.isSafeInteger(item.media.loopCount)||item.media.loopCount<0||item.media.loopCount>65535))
   malformed('invalid GIF loop count');
  ids.add(item.id);
 }
 return value;
}
function mediaUrl(item,messageId,download=false){
 const query=new URLSearchParams({sessionId:item.sessionId,messageId,version:String(item.version),sha256:item.sha256});
 if(download)query.set('download','1');
 return api.clientUrl(`/api/outputs/${encodeURIComponent(item.id)}/media?${query}`);
}

export function MediaFigure({item,messageId,reportMedia,dispatch,openInCanvas=false}){
 const figure=useRef(null),canvas=useRef(null),video=useRef(null),player=useRef(null),reporter=useRef(()=>{});
 const [status,setStatus]=useState({render:'loading',playback:'paused',detail:''});
 const opening=useRef(false),openRequest=useRef(null),mounted=useRef(false),[openPending,setOpenPending]=useState(false),[openError,setOpenError]=useState('');
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 async function openGif(){
  if(opening.current||!mounted.current)return;
  opening.current=true;setOpenPending(true);setOpenError('');
  openRequest.current ||= {id:crypto.randomUUID(),
   args:{sessionId:item.sessionId,messageId,id:item.id,version:item.version,sha256:item.sha256}};
  try{
   const result=await dispatch('canvas.openGif',openRequest.current.args,{id:openRequest.current.id});
   if(result?.accepted===false){
    // A local Settings cancellation is not a receipt for an earlier unknown delivery.
    if(mounted.current)setOpenError(result.error||'The retained GIF could not be opened in Canvas.');
    return;
   }
   openRequest.current=null;
  }catch(error){
   // An uncertain transport outcome must retry the identical command.
   if(!error.unconfirmed)openRequest.current=null;
   if(mounted.current)setOpenError(error.message||'The retained GIF could not be opened in Canvas.');
  }finally{opening.current=false;if(mounted.current)setOpenPending(false)}
 }
 useEffect(()=>{
  let disposed=false,presentedFrames=0,mediaTime=0,videoCallback,ownedPlayer;
  // React clears DOM refs before passive cleanup. Own the actual mounted
  // target here, not the ref's later (possibly null) value.
  const target=video.current,canvasTarget=canvas.current;
  const generation=observationGeneration=Math.max(observationGeneration+1,Date.now()*1000);
  const abort=new AbortController(),url=mediaUrl(item,messageId);
  const notifications=new MediaReporter({generation,
   onState:({renderState:render,playback,detail})=>setStatus({render,playback,detail}),
   // Canvas owns its view evidence, never the inline generation.
   send:payload=>reportMedia?reportMedia(payload):api.request('/api/actions',{method:'POST',signal:abort.signal,body:{
    action:'outputs.mediaReport',args:{
     sessionId:item.sessionId,messageId,id:item.id,version:item.version,sha256:item.sha256,...payload
    }}})
  });
  const report=(render,playback,detail='')=>{
   if(disposed)return;
   notifications.report({renderState:render,playback,presentedFrames,mediaTime,detail});
  };
  reporter.current=report;report('loading','paused');
  const pause=()=>{ownedPlayer?.pause();target?.pause()};
  const motion=matchMedia('(prefers-reduced-motion: reduce)');
  const onMotion=()=>{if(motion.matches)pause()},onVisibility=()=>{if(document.hidden)pause()};
  document.addEventListener('visibilitychange',onVisibility);motion.addEventListener('change',onMotion);
  const visibility=globalThis.IntersectionObserver?new IntersectionObserver(rows=>{
   if(!rows.some(row=>row.isIntersecting))pause();
  }):null;
  if(figure.current)visibility?.observe(figure.current);
  if(item.media.mimeType==='video/mp4'&&target?.requestVideoFrameCallback){
   const presented=(_now,metadata)=>{
    if(disposed)return;presentedFrames++;mediaTime=metadata.mediaTime;
    report('ready',target.paused?'paused':'playing');
    videoCallback=target.requestVideoFrameCallback(presented);
   };
   videoCallback=target.requestVideoFrameCallback(presented);
  }
  if(item.media.mimeType==='image/gif'){
   if(!globalThis.ImageDecoder)report('error','paused','GIF playback is unsupported here. Download the original.');
   else void (async()=>{
    let decoder;
    try{
     const response=await fetch(url,{credentials:'same-origin',signal:abort.signal});
     if(!response.ok)throw new Error(`Media is unavailable (${response.status}).`);
     const data=await response.arrayBuffer();if(disposed)return;
     decoder=new ImageDecoder({data,type:'image/gif',preferAnimation:true});
     const owned=new GifPlayer({decoder,loopCount:item.media.loopCount,report,
      draw:frame=>{if(!canvasTarget||disposed)return;
       canvasTarget.width=frame.displayWidth;canvasTarget.height=frame.displayHeight;
       const context=canvasTarget.getContext('2d');context.clearRect(0,0,canvasTarget.width,canvasTarget.height);context.drawImage(frame,0,0);
       presentedFrames++;mediaTime=(frame.timestamp||0)/1_000_000;
      }});
     ownedPlayer=owned;player.current=owned;await owned.ready();
     if(disposed)owned.dispose();
    }catch(error){decoder?.close();if(!disposed&&error.name!=='AbortError')report('error','paused',error.message)}
   })();
  }
  return ()=>{
   if(disposed)return;disposed=true;notifications.dispose();reporter.current=()=>{};abort.abort();
   ownedPlayer?.dispose();player.current=null;
   if(target){
    if(videoCallback!==undefined)target.cancelVideoFrameCallback?.(videoCallback);
    target.pause();target.removeAttribute('src');target.load();
   }
   document.removeEventListener('visibilitychange',onVisibility);motion.removeEventListener('change',onMotion);
   visibility?.disconnect();
  };
 },[item.id,item.version,item.sha256,messageId,reportMedia]);
 return <figure ref={figure} data-media-id={item.id} data-media-state={status.render} data-playback={status.playback}>
  {item.media.mimeType==='image/gif'?<canvas ref={canvas} aria-label={item.title}/>:<video
   ref={video} src={mediaUrl(item,messageId)} controls playsInline muted preload="metadata" aria-label={item.title}
   onLoadedData={()=>reporter.current('ready','paused')}
   onPlay={()=>reporter.current('ready','playing')} onPause={()=>reporter.current('ready','paused')}
   onEnded={()=>reporter.current('ready','ended')} onError={()=>reporter.current('error','paused','Video cannot be decoded. Download the original.')}/>}
  <figcaption>{item.title}</figcaption>
  <div className="media-controls">
   {item.media.mimeType==='image/gif'&&status.render==='ready'&&<button type="button" className="a-soft"
    disabled={status.playback==='ended'}
    onClick={()=>status.playback==='playing'?player.current?.pause():player.current?.play()}>
    {status.playback==='ended'?'GIF ended':status.playback==='playing'?'Pause GIF':'Play GIF'}</button>}
   <a href={mediaUrl(item,messageId,true)} download={item.filename}>Download original</a>
   {openInCanvas&&item.media.mimeType==='image/gif'&&<button type="button" className="a-soft"
    data-action="canvas.openGif" disabled={openPending} aria-busy={openPending||undefined} onClick={openGif}>
    {openPending?'Opening in Canvas…':'Open in Canvas'}</button>}
   <span role="status">{status.detail||`${status.render} · ${status.playback}`}</span>
   {openError&&<span role="alert">{openError}</span>}
  </div>
 </figure>;
}

export function InlineMedia({sessionId,messageId,mediaRevision=0,linkedMediaAvailable,configurationBusy=false,dispatch}){
 const anchor=useRef(null),[visible,setVisible]=useState(false),[result,setResult]=useState(null),[error,setError]=useState(null),[retry,setRetry]=useState(0);
 const owns=value=>value?.sessionId===sessionId&&value?.messageId===messageId;
 const current=linkedMediaAvailable===true&&owns(result)?result.value:null;
 const failure=!configurationBusy&&linkedMediaAvailable===true&&owns(error)?error.message:'';
 useEffect(()=>{
  if(!globalThis.IntersectionObserver){setVisible(true);return}
  const observer=new IntersectionObserver(rows=>setVisible(rows.some(row=>row.isIntersecting)),{rootMargin:'200px'});
  if(anchor.current)observer.observe(anchor.current);return ()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  if(linkedMediaAvailable===false){setResult(null);setError(null)}
  if(configurationBusy)setError(null);
  // Only the selected host projection can establish registry availability.
  // Missing coverage is pending, never authority to scan a transcript.
  // Bundle replacement temporarily rewrites native transcript indices. Keep
  // already-qualified media, but defer discovery until its identity is stable.
  if(configurationBusy||!visible||linkedMediaAvailable!==true)return;let disposed=false;const abort=new AbortController();
  const query=new URLSearchParams({sessionId,messageId});
  setError(null);
  api.request(`/api/outputs/media?${query}`,{signal:abort.signal}).then(value=>{
   if(!disposed)setResult({sessionId,messageId,value:validateMediaMetadata(value,sessionId)});
  }).catch(error=>{if(!disposed&&error.name!=='AbortError')setError({sessionId,messageId,message:error.message})});
  return ()=>{disposed=true;abort.abort()};
 },[sessionId,messageId,mediaRevision,linkedMediaAvailable,configurationBusy,visible,retry]);
 return <div ref={anchor} className="a-inline-media">
  {current?.items.map(item=><MediaFigure key={`${item.id}:${item.version}:${item.sha256}`} item={item} messageId={messageId} dispatch={dispatch} openInCanvas/>)}
  {failure&&<p role="alert">{failure} <button type="button" onClick={()=>setRetry(value=>value+1)}>Retry media metadata</button></p>}
  {current?.overflow&&<p>Only the first 20 media attachments are displayed.</p>}
 </div>;
}
