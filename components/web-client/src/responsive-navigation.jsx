import React,{createContext,useEffect,useLayoutEffect,useRef,useState} from 'react';

export const NavigationOpen=createContext(true);
const inertOwners=new WeakMap();
export const navigationExpanded=(view={},narrow=false)=>(!narrow&&!!view.navPinned||!!view.navExpanded)
 &&(!narrow||!view.canvasFocused&&!view.panel&&!view.toolbarMenuOpen);

export function useNarrowScreen(query='(max-width: 760px)'){
 const [matches,setMatches]=useState(()=>typeof matchMedia==='function'&&matchMedia(query).matches);
 useEffect(()=>{if(typeof matchMedia!=='function')return;const media=matchMedia(query),change=()=>setMatches(media.matches);change();media.addEventListener('change',change);return()=>media.removeEventListener('change',change)},[query]);
 return matches;
}
// Modal sheets and drawers retain their place in the app's DOM. Inert siblings
// prevent both keyboard and assistive-technology navigation into the background.
export function useModalFocus(ref,enabled,onClose,includeSelector=null){
 const close=useRef(onClose),returnFocus=useRef(null);close.current=onClose;
 // Release the old modal during the commit, before newly revealed siblings get
 // their declarative inert state. A passive cleanup can re-inert an open Canvas.
 useLayoutEffect(()=>{
  returnFocus.current=null;
  const panel=ref.current;if(!enabled||!panel)return;
  const previous=document.activeElement,hidden=[];
  returnFocus.current=previous;
  const included=includeSelector?[...document.querySelectorAll(includeSelector)]:[];
  const hide=sibling=>{
   if(included.includes(sibling))return;
   if(included.some(node=>sibling.contains(node))){for(const child of sibling.children)hide(child);return}
   const held=inertOwners.get(sibling)||{count:0,original:sibling.inert};held.count++;inertOwners.set(sibling,held);hidden.push(sibling);sibling.inert=true;
  };
  let branch=panel;
  while(branch?.parentElement&&branch.id!=='amp-one'){
   for(const sibling of branch.parentElement.children){if(sibling===branch||sibling.tagName==='STYLE'||sibling.matches('.a-navigation-scrim,.a-details-scrim,.a-toolbar-scrim'))continue;hide(sibling)}
   branch=branch.parentElement;
  }
  const candidates=()=>[...included,...panel.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,a[href],[tabindex="0"]')].filter(el=>el.tabIndex!==-1&&el.getClientRects().length&&!el.closest('[inert]'));
  candidates().find(node=>panel.contains(node))?.focus({preventScroll:true});
  const key=event=>{
   if(event.defaultPrevented)return;
   // A native top-layer dialog (open documents or a guard) owns its Escape/Tab.
   if(document.activeElement?.closest('dialog[open]'))return;
   const nested=document.activeElement?.closest('.a-navigation-flyout');
   if(nested&&nested!==panel)return;
   if(event.key==='Escape'){event.preventDefault();close.current();return}
   if(event.key!=='Tab')return;
   const list=candidates(),first=list[0],last=list.at(-1);
   if(!first){event.preventDefault();return}
   if(event.shiftKey&&(document.activeElement===first||!list.includes(document.activeElement))){event.preventDefault();last.focus()}
   else if(!event.shiftKey&&(document.activeElement===last||!list.includes(document.activeElement))){event.preventDefault();first.focus()}
  };
  document.addEventListener('keydown',key);
  return()=>{document.removeEventListener('keydown',key);for(const el of hidden){const held=inertOwners.get(el);if(held&&!--held.count){el.inert=held.original;inertOwners.delete(el)}}};
 },[enabled,ref,includeSelector]);
 // React restores the pre-commit active element after layout cleanups. Return
 // focus in passive cleanup instead, without delaying the inert-state release.
 // Capture this modal's opener, not a subsequent render's returnFocus value.
 useEffect(()=>{
  if(!enabled)return;
  const previous=returnFocus.current;
  return()=>{if(previous?.isConnected&&!previous.closest('[inert]'))previous.focus?.({preventScroll:true})};
 },[enabled,ref,includeSelector]);
}
export function MobileChatTitle({state,session}){
 const workspace=state.workspaces?.find(row=>row.id===state.selectedWorkspaceId);
 const path=session?.workspace||workspace?.path||'',parts=path.split(/[\\/]/).filter(Boolean);
 const label=workspace?.customName||workspace?.name||parts.at(-1);
 return <div className="a-mobile-chat-title"><strong title={session?.title||'New chat'}>{session?.title||'New chat'}</strong>{label&&<small title={path}>{label}{parts.length>1&&` · ${parts.at(-2)}`}</small>}</div>;
}
