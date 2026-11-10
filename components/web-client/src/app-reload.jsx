import React,{createContext,useContext,useEffect,useRef,useState,useSyncExternalStore} from 'react';
import {RefreshCw} from 'lucide-react';
import {frontendBuild} from './feedback-diagnostics';
import {createBuildDiscovery,reloadWhenSaved} from './app-reload.js';
import './app-reload.css';
import {subscribeCanvasReload,hasLocalCanvasEdits,canvasReloadBlocked,canvasReloadMessage} from './canvas-reload-guard.js';

export const AppReloadContext=createContext(null);
export function useAppReload({client,blocked,prepare,canvasWorkspace}){
 const [installed,setInstalled]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const localDirty=useSyncExternalStore(subscribeCanvasReload,hasLocalCanvasEdits,()=>false);
 const workspace=useRef(canvasWorkspace);workspace.current=canvasWorkspace;
 const currentBlocked=useRef(blocked);currentBlocked.current=blocked;
 const reloadBlocked=blocked||(localDirty||canvasWorkspace?.views?.some(view=>view.dirty)?canvasReloadMessage:'');
 const pending=useRef(false),preparation=useRef(prepare);preparation.current=prepare;
 const discovery=useRef(null);
 useEffect(()=>{
  const owner=createBuildDiscovery({loaded:frontendBuild,onInstalled:setInstalled});discovery.current=owner;
  window.addEventListener('focus',owner.wake);window.addEventListener('online',owner.wake);document.addEventListener('visibilitychange',owner.wake);
  return()=>{owner.dispose();discovery.current=null;window.removeEventListener('focus',owner.wake);window.removeEventListener('online',owner.wake);document.removeEventListener('visibilitychange',owner.wake)};
 },[]);
 useEffect(()=>discovery.current?.observe(client),[client]);
 const reload=async()=>{
  if(!installed||reloadBlocked||pending.current)return;
  pending.current=true;setBusy(true);setError('');
  try{await reloadWhenSaved({prepare:()=>preparation.current(),assertReady:()=>{if(currentBlocked.current)throw Error(currentBlocked.current);if(canvasReloadBlocked(workspace.current))throw Error(canvasReloadMessage)},reload:()=>window.location.reload()})}
  catch(e){setError(e.message||'Your changes could not be saved. Try again when connected.');pending.current=false;setBusy(false)}
 };
 return {available:!!installed,version:installed?.version,busy,blocked:reloadBlocked,error,reload};
}
export function AppReloadNotice(){
 const update=useContext(AppReloadContext);if(!update?.available)return null;
 return <aside className="a-reload-notice" data-part="app-reload" aria-label="App update ready">
  <RefreshCw aria-hidden="true"/><div><strong>Update ready—reload to finish</strong><p>{update.blocked||'Load the updated app in this tab. Your chats and saved settings stay in place.'}</p>{update.error&&<p role="alert">{update.error}</p>}</div>
  <button type="button" className="a-primary" disabled={update.busy||!!update.blocked} onClick={update.reload}>{update.busy?'Saving before reload…':'Reload now'}</button>
 </aside>;
}
