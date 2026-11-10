import React,{createContext,useContext,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';

export const SettingsLayoutContext=createContext({compact:false,active:true,footer:null});
export function SettingsActions({children,active=true,className=''}){
 const layout=useContext(SettingsLayoutContext);
 if(layout.compact&&!active)return null;
 const content=<div className={'a-dialog-actions '+className}>{children}</div>;
 return layout.compact&&layout.active&&layout.footer&&active?createPortal(content,layout.footer):content;
}
export function useSettingsCompact(root){
 const [compact,setCompact]=useState(()=>typeof window!=='undefined'&&window.matchMedia('(max-width:959px)').matches);
 useLayoutEffect(()=>{
  const node=root.current?.closest('.a-overlay')||root.current;if(!node)return;
  const update=()=>setCompact(node.clientWidth<960);update();
  const observer=new ResizeObserver(update);observer.observe(node);return()=>observer.disconnect();
 },[]);
 return compact;
}

export function useSettingsViewport(root,compact){
 useEffect(()=>{
  const overlay=root.current?.closest('.a-overlay'),viewport=window.visualViewport;
  if(!compact||!overlay||!viewport)return;
  let frame;
  const update=()=>{
   if(viewport.scale!==1)return;
   overlay.style.setProperty('--a-settings-viewport-top',viewport.offsetTop+'px');
   overlay.style.setProperty('--a-settings-viewport-height',viewport.height+'px');
   cancelAnimationFrame(frame);
   frame=requestAnimationFrame(()=>{
    const input=document.activeElement;
    if(!root.current?.contains(input)||!input.matches('input,textarea,select'))return;
    const content=input.closest('.a-settings-content');if(!content)return;
    const field=input.getBoundingClientRect(),area=content.getBoundingClientRect();
    if(field.bottom>area.bottom-16)content.scrollTop+=field.bottom-area.bottom+16;
    else if(field.top<area.top+16)content.scrollTop+=field.top-area.top-16;
   });
  };
  update();viewport.addEventListener('resize',update);viewport.addEventListener('scroll',update);root.current.addEventListener('focusin',update);
  const node=root.current;
  return()=>{cancelAnimationFrame(frame);viewport.removeEventListener('resize',update);viewport.removeEventListener('scroll',update);node.removeEventListener('focusin',update);overlay.style.removeProperty('--a-settings-viewport-top');overlay.style.removeProperty('--a-settings-viewport-height');};
 },[compact]);
}

export function useSettingsHistory({compact,trail,view,act,close,body,navigationRef,confirmClose=()=>Promise.resolve(true),hasUnsaved=()=>false}){
 const latest=useRef(null),session=useRef(null),scrolls=useRef(new Map()),lastKey=useRef(null),scrollRestore=useRef(false);
 const [confirming,setConfirming]=useState(false),closePending=useRef(false);
 latest.current={trail,view,act,close,confirmClose};
 const route=trail.at(-1),key=route.key,visit=useRef(view.settingsRootVisit);
 // The scroll position belongs to the navigation level, not to every state update.
 useEffect(()=>{
  const reset=visit.current!==view.settingsRootVisit;visit.current=view.settingsRootVisit;
  lastKey.current=key;scrollRestore.current=true;
  const frame=requestAnimationFrame(()=>{if(body.current)body.current.scrollTop=reset?0:scrolls.current.get(key)||0;scrollRestore.current=false;});
  return()=>cancelAnimationFrame(frame);
 },[key,view.settingsRootVisit]);
 const rememberScroll=()=>{if(body.current&&!scrollRestore.current)scrolls.current.set(lastKey.current,body.current.scrollTop);};
 useLayoutEffect(()=>{
  if(!compact||typeof window==='undefined')return;
  const marker=history.state?.amplifierSettings;
  const inherited=typeof marker?.token==='string'&&marker.token.startsWith('settings-')&&Number.isInteger(marker.index)&&marker.index>0&&marker.index<10?marker:null;
  const token=inherited?.token||'settings-'+crypto.randomUUID(),original={...history.state};delete original.amplifierSettings;
  const current={token,entries:inherited?[...latest.current.trail]:[],index:inherited?.index||0,closing:false};session.current=current;
  const push=entry=>{current.entries.splice(current.index);current.entries.push(entry);current.index=current.entries.length;history.pushState({...original,amplifierSettings:{token,index:current.index}},'');};
  if(!inherited)latest.current.trail.forEach(push);
  const pop=async event=>{
   if(session.current!==current)return;
   const marker=event.state?.amplifierSettings;
   if(marker?.token!==token){
    if(current.confirming)return;
    if(current.closing){latest.current.close();return;}
    current.confirming=true;setConfirming(true);
    const accepted=await latest.current.confirmClose();current.confirming=false;
    if(session.current!==current)return;
    if(accepted){current.closing=true;latest.current.close();}
    else {for(let index=1;index<=current.index;index++)history.pushState({...original,amplifierSettings:{token,index}},'');}
    setConfirming(false);
    return;
   }
   current.index=marker.index;const entry=current.entries[current.index-1];if(!entry)return;
   // Discard/save can shorten the trail and call history.go to reconcile its
   // index. That pop already describes the current page: publishing it again
   // can queue a Settings reopen behind the user's pending tab close.
   if(entry.key===latest.current.trail.at(-1)?.key)return;
   const orderEditor=Object.keys(entry.navigation).find(name=>entry.navigation[name]?.orderOpen===true);
   // A completed/cancelled order has no draft to reopen with browser Forward.
   if(orderEditor&&!latest.current.view[orderEditor]?.order){history.back();return;}
   const patch={...entry.navigation};
   for(const name of ['providerEditor','routingEditor','bundleManager','moduleEditor','smartToolsEditor','diagnosticsDraft','aiConnectionEditor','workspaceStarterEditor'])if(patch[name])patch[name]={...latest.current.view[name],...patch[name]};
   latest.current.act('view.update',{patch});
  };
  window.addEventListener('popstate',pop);
  return()=>{
   window.removeEventListener('popstate',pop);session.current=null;
   if(history.state?.amplifierSettings?.token===token&&!current.closing)history.go(-current.index);
  };
 },[compact]);
 useLayoutEffect(()=>{
  const current=session.current;if(!compact||!current||current.closing||current.confirming)return;
  const entry=current.entries[current.index-1];
  // A history level represents the current hierarchy, not a stale editor snapshot.
  // Sibling tabs replace their level; deep links populate all missing parents.
  trail.forEach((row,index)=>{current.entries[index]=row;});
  if(current.index>trail.length){history.go(trail.length-current.index);return;}
  if(current.index===trail.length){
   if(entry?.key!==key)history.replaceState({...history.state,amplifierSettings:{token:current.token,index:current.index}},'');
   return;
  }
  while(current.index<trail.length){current.index++;history.pushState({...history.state,amplifierSettings:{token:current.token,index:current.index}},'');}

 },[key,compact,confirming]);
 const back=()=>{
  const current=session.current;
  if(current&&current.index>1){rememberScroll();history.back();}
  else if(trail.length>1){const entry=trail.at(-2),patch={...entry.navigation};for(const name of ['providerEditor','routingEditor','bundleManager','moduleEditor','smartToolsEditor','diagnosticsDraft','aiConnectionEditor','workspaceStarterEditor'])if(patch[name])patch[name]={...view[name],...patch[name]};act('view.update',{patch});}
  else dismiss();
 };
 const dismiss=async()=>{
  if(closePending.current)return;closePending.current=true;
  const current=session.current;if(current)current.confirming=true;setConfirming(true);
  const accepted=await latest.current.confirmClose();
  if(current)current.confirming=false;
  if(accepted){if(current&&!current.closing){current.closing=true;history.go(-current.index);}else close();}
  closePending.current=false;setConfirming(false);
 };
 useEffect(()=>{if(navigationRef)navigationRef.current={close:dismiss,confirmClose,hasUnsaved};return()=>{if(navigationRef)navigationRef.current=null;};});
 return {back,dismiss,rememberScroll};
}
