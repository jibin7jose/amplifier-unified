import {useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
// Container queries make the conversation a containing block for fixed children.
// Mount menus at the shell root so viewport coordinates remain viewport-relative.
export function useComposerPopover(open,ref){
 const panelRef=useRef(null),[style,setStyle]=useState({visibility:'hidden'}),[host,setHost]=useState(null);
 // On a restored open menu, the shell root may not exist during render yet.
 // Never fall back to an inline fixed child of the conversation container:
 // those viewport coordinates would be interpreted relative to that container.
 useLayoutEffect(()=>{if(open&&typeof document!=='undefined')setHost(document.getElementById('amp-one'))},[open]);
 useLayoutEffect(()=>{
  if(!open||!host)return;
  const panel=panelRef.current,anchor=ref.current?.querySelector('button'),header=host.querySelector('.a-work-header');
  if(!panel||!anchor)return;
  const place=()=>{
   const viewport=window.visualViewport,left=viewport?.offsetLeft||0,top=viewport?.offsetTop||0;
   const viewportWidth=viewport?.width||window.innerWidth,viewportHeight=viewport?.height||window.innerHeight;
   // Keep persistent navigation reachable when a long phone menu must scroll.
   const safeTop=Math.max(top+12,header?.getBoundingClientRect().bottom||0);
   const a=anchor.getBoundingClientRect(),actions=anchor.closest('.a-composer')?.querySelector('.a-compose-add');
   // A menu above the external model trigger must not cover Add/Send/Stop.
   // Rich drafts can still scroll behind it; their action row stays reachable.
   const bottom=Math.min(top+viewportHeight-12,actions?actions.getBoundingClientRect().top-10:a.top-10);
   const width=Math.min(340,viewportWidth-24),maxHeight=Math.max(0,bottom-safeTop);
   const height=Math.min(panel.getBoundingClientRect().height,maxHeight);
   setStyle({width,maxHeight,left:Math.max(left+12,Math.min(a.left,left+viewportWidth-width-12)),top:Math.max(safeTop,bottom-height),visibility:'visible'});
  };
  place();const observer=new ResizeObserver(place);observer.observe(panel);if(header)observer.observe(header);
  window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  window.visualViewport?.addEventListener('resize',place);window.visualViewport?.addEventListener('scroll',place);
  return()=>{observer.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);window.visualViewport?.removeEventListener('resize',place);window.visualViewport?.removeEventListener('scroll',place)};
 },[open,ref,host]);
 // Non-DOM renderers retain the component tree; browsers still wait for the
 // shell host and must never render a fixed menu inline in the conversation.
 const renderPopover=content=>typeof document==='undefined'?content:host?createPortal(content,host):null;
 return {style,panelRef,renderPopover};
}
