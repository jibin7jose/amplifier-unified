import React,{useEffect,useRef,useState} from 'react';
import {Download} from 'lucide-react';
import {clientUrl} from './api';
import {CopyControl} from './block-copy';

const identityOf=({id,version,sessionId})=>JSON.stringify([id,version,sessionId]);
async function inspect(target,dispatch){
 const response=await dispatch('canvas.versions.inspect',target);
 const value=response?.result;
 if(!response?.accepted||value?.id!==target.id||value?.version!==target.version)throw Error('This saved file version is unavailable.');
 return value;
}

// A saved-version download is not a renderer lease. Fetching as bytes also makes
// an HTTP refusal visible instead of navigating to an error page (or HTML).
export function SavedFileDownload({target,dispatch,label='Download saved file',compact=false}){
 return <DownloadControl key={identityOf(target)} {...{target,dispatch,label,compact}}/>;
}
function DownloadControl({target,dispatch,label,compact}){
 const pending=useRef(null),mounted=useRef(true),[status,setStatus]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;pending.current?.abort()}},[]);
 async function save(){
  if(pending.current)return;
  const controller=new AbortController();pending.current=controller;setBusy(true);setStatus('Preparing download…');
  try{
   const value=await inspect(target,dispatch);
   if(!mounted.current)return;
   if(!value.download)throw Error('This saved artifact has no downloadable file.');
   const response=await fetch(clientUrl(value.download.url),{credentials:'same-origin',signal:controller.signal,redirect:'error'});
   if(!response.ok)throw Error(`The saved file could not be downloaded (${response.status}).`);
   // Require the guarded attachment response; never save an authentication page.
   if(!response.headers.get('Content-Disposition')?.startsWith('attachment;'))throw Error('The server did not return a saved file.');
   const blob=await response.blob();
   if(!mounted.current||controller.signal.aborted)return;
   const url=URL.createObjectURL(blob),link=document.createElement('a');
   link.href=url;link.download=value.download.filename;link.click();
   setTimeout(()=>URL.revokeObjectURL(url),1000);
   setStatus('Download started. Check your browser’s downloads.');
  }catch(error){if(mounted.current)setStatus(error.message||'The saved file could not be downloaded.')}
  finally{pending.current=null;if(mounted.current)setBusy(false)}
 }
 return <div className="a-saved-file-download" data-compact={compact?'true':undefined}><button type="button" className={compact?'a-icon':'a-soft'} aria-label={label} aria-busy={busy} disabled={busy} onClick={save}><Download aria-hidden="true" focusable="false"/><span>{compact?'Download':label}</span></button><span role="status" aria-live="polite">{status}</span></div>;
}

// One path/details owner for compact cards and the existing Canvas inspector.
// Paths describe retained provenance, not proof that the original file exists.
export function ArtifactFileDetails({target,dispatch,placement,children}){
 return <FileDetails key={identityOf(target)} {...{target,dispatch,placement}}>{children}</FileDetails>;
}
function FileDetails({target,dispatch,placement,children}){
 const [result,setResult]=useState(null),[error,setError]=useState(''),[open,setOpen]=useState(false),mounted=useRef(true);
 const scope=useRef(null);
 if(!scope.current||scope.current.placement!==placement)scope.current={placement};
 const current=scope.current,value=result?.scope===current?result.value:null;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{
  if(!open)return;
  let active=true;setError('');setResult(null);
  inspect(target,dispatch).then(value=>{if(active&&mounted.current&&scope.current===current)setResult({scope:current,value})})
   .catch(error=>{if(active&&mounted.current&&scope.current===current)setError(error.message)});
  return()=>{active=false};
 },[open,current]);
 const paths=value?.filePaths||{},identity=identityOf(target);
 return <details className="a-canvas-file-path" onToggle={event=>setOpen(event.currentTarget.open)}>
  <summary>File details</summary>
  <div>{error?<p role="alert">{error}</p>:!value?<p role="status">Loading file details…</p>:<>
   <p>Saved version {target.version}</p>
   {paths.absolute?<>
    <p>Original source location on the app server. The current file has not been checked.</p>
    <code>{paths.absolute}</code>
    <CopyControl text={paths.absolute} label="Copy full path" identity={identity} compact={false}/>
    {paths.base&&<p>Relative to conversation working folder:<code>{paths.base}</code>{paths.workspace&&paths.workspace!==paths.base&&<>Workspace:<code>{paths.workspace}</code></>}</p>}
    {paths.relative?<><code>{paths.relative}</code><CopyControl text={paths.relative} label="Copy relative path" identity={identity+':'+paths.base} compact={false}/></>:<p>No relative path: the source is outside the working folder or its base is unavailable.</p>}
    {children}
   </>:<p>Saved in this chat; no backing filesystem path.</p>}
   {value.download?<SavedFileDownload target={target} dispatch={dispatch}/>:<p>Download is unavailable from file details for this artifact type. Use its viewer controls.</p>}
  </>}</div>
 </details>;
}
