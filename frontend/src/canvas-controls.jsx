import React,{createContext,useContext,useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {hasLocalCanvasEdits} from './canvas-reload-guard.js';

// Controls share one stable surface. Pinning changes its layout, never the viewers.
export const CanvasControlsHost=createContext(undefined);
// Primary common actions have their own stable compact row. Inline viewers keep
// their existing controls: absence of this context is not a second toolbar.
export const CanvasPrimaryActionsHost=createContext(undefined);
const CanvasVersionLoadHost=createContext(null);
export function CanvasVersionLoadProvider({children,view}){
 const state=useState(null),pending=useRef(false);
 const [load,setLoad]=state,accepted=load?.accepted?.result;
 useEffect(()=>{
  if(accepted&&view?.resourceId===accepted.resourceId&&view?.resourceRevision===accepted.resourceRevision&&view?.generation===accepted.generation)setLoad(null);
 },[accepted,view?.resourceId,view?.resourceRevision,view?.generation]);
 return <CanvasVersionLoadHost.Provider value={{state,pending}}>{children}</CanvasVersionLoadHost.Provider>;
}
export function useCanvasVersionLoad(){
 const shared=useContext(CanvasVersionLoadHost),state=useState(null),pending=useRef(false);
 return shared||{state,pending};
}
export const canvasLoadSelection=view=>JSON.stringify([view.resourceId,view.selectedVersion]);
export const canvasLoadBinding=view=>JSON.stringify([view.resourceId,view.resourceRevision,view.generation]);
export const acceptedCanvasLoad=receipt=>receipt?.stateConfirmation?.status==='unconfirmed'
 ?{needsCheck:true,accepted:receipt,error:`Load v${receipt.result.runningVersion} was accepted. The displayed version could not be confirmed. Refresh to check before another Load.`}:null;
export const failedCanvasLoad=error=>({needsCheck:!!error.unconfirmed,error:error.unconfirmed?'Version load completion is unconfirmed. Refresh to check the current version before another Load.':`${error.message} The current preview is retained.`});
// Refresh is a saved-state read, not a renderer reload or a selection command.
// A same-version check must retain live HTML and native-renderer input.
export function CanvasDocumentRefresh({view,dispatch}){
 const [check,setCheck]=useState(null),pending=useRef(false);
 const {state:[load,setLoad],pending:loading}=useCanvasVersionLoad();
 const selection=canvasLoadSelection(view);
 const versionOf=value=>JSON.stringify([value.resourceRevision,value.latestVersion]);
 const current=useRef(selection);current.current=selection;
 const binding=canvasLoadBinding(view);
 const shown=check?.selection===selection?check:null;
 const historical=view.selectedVersion!=null&&view.selectedVersion<view.latestVersion;
 const identity=view.previewRetained?`Viewing v${view.runningVersion}`:view.selectedVersion==null?`Latest · Version ${view.runningVersion??view.latestVersion}`:`Version ${view.selectedVersion} · Saved`;
 const freshness=view.previewRetained?`v${view.latestVersion} available · preview retained`:historical?`Historical · Version ${view.latestVersion} available`:'Current saved content';
 const loadVersion=async()=>{
  if(loading.current||load?.selection===selection&&load?.needsCheck)return;
  if(hasLocalCanvasEdits()){setLoad({selection,binding,error:'Save or cancel unfinished Canvas edits before loading another version. The current preview is retained.'});return}
  const target=Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]));
  loading.current=true;setLoad({selection,binding,pending:true});
  try{
   const result=await dispatch('canvas.views.loadVersion',{...target,version:view.latestVersion});
   if(!result?.accepted)throw Error('The version load was not accepted.');
   if(current.current===selection){const feedback=acceptedCanvasLoad(result);setLoad(feedback?{selection,binding,...feedback}:null)}
  }catch(error){if(current.current===selection)setLoad({selection,binding,...failedCanvasLoad(error)})}
  finally{loading.current=false}
 };
 const shownLoad=load?.selection===selection&&(load.pending||load.needsCheck||load.binding===binding)?load:null;
 const refresh=async()=>{
  if(pending.current)return;
  pending.current=true;setCheck({selection,pending:true});
  try{
   const receipt=await dispatch('canvas.views.inspect',{});
   const observed=receipt?.result?.views?.find(item=>item.viewId===view.viewId);
   if(!observed||observed.resourceId!==view.resourceId||observed.selectedVersion!==view.selectedVersion)throw Error('The selected document changed. Check the document now open.');
   if(current.current===selection){setCheck({selection,checkedVersion:versionOf(observed)});setLoad(value=>value?.needsCheck?null:value)}
  }catch(error){if(current.current===selection)setCheck({selection,error:error.message})}
  finally{pending.current=false}
 };
 return <>
  <button type="button" className="a-soft a-canvas-refresh" aria-label="Refresh document" title="Check saved content and versions without reloading the preview or reading the source file" aria-disabled={!!shown?.pending} onClick={refresh}>Refresh</button>
  <div className="a-canvas-freshness" role={shown?.error?'alert':'status'} aria-live="polite">
   {shown?.pending?'Checking saved content…':shown?.error?`Could not refresh: ${shown.error} ${shownLoad?.needsCheck?'The Load outcome still needs a saved-state check.':'The open document is retained.'}`:shownLoad?.needsCheck?'Saved preview status needs confirmation':`${shown?.checkedVersion===versionOf(view)?'Checked · ':''}${identity} · ${freshness}`}
   {view.previewRetained&&<button type="button" className="a-soft a-canvas-load-version" aria-disabled={!!shownLoad?.pending||!!shownLoad?.needsCheck} onClick={loadVersion}>{shownLoad?.pending?'Loading saved version…':`Load v${view.latestVersion} — resets preview and runs its code`}</button>}
   {shownLoad?.error&&<p role={shownLoad.source==='details'?undefined:'alert'}>{shownLoad.error}</p>}
  </div>
 </>;
}
export const usePrimaryCanvas=()=>useContext(CanvasPrimaryActionsHost)!==undefined;
export function CanvasPrimaryAction({children}){
 const host=useContext(CanvasPrimaryActionsHost);
 return host===undefined?<CanvasControl>{children}</CanvasControl>:host?createPortal(children,host):null;
}
export function CanvasControl({children,inline=false}){
 const host=useContext(CanvasControlsHost);
 return inline||host===undefined?children:host?createPortal(children,host):null;
}
export function CanvasViewControls({children,label}){
 const host=useContext(CanvasControlsHost),[target,setTarget]=useState(null);
 if(host===undefined)return children;
 return <><CanvasControl><div ref={setTarget} className="a-canvas-view-tools" role="group" aria-label={label}/></CanvasControl><CanvasControlsHost.Provider value={target}>{children}</CanvasControlsHost.Provider></>;
}
