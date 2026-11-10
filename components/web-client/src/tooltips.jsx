import React,{useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import './tooltips.css';

// Shared presentation for explicit anchors as well as the app's button layer.
// The top layer avoids clipping in scrolling panels while inheriting theme tokens.
export function Tooltip({anchor,children}){
 const ref=useRef(null),id=useId();
 useLayoutEffect(()=>{
  const node=ref.current;if(!anchor||!node)return;
  node.showPopover?.();
  const place=()=>{
   const box=anchor.getBoundingClientRect(),tip=node.getBoundingClientRect(),gap=8;
   node.style.left=Math.max(gap,Math.min(window.innerWidth-tip.width-gap,box.left+(box.width-tip.width)/2))+'px';
   node.style.top=Math.max(gap,Math.min(window.innerHeight-tip.height-gap,box.top-tip.height-gap>=gap?box.top-tip.height-gap:box.bottom+gap))+'px';
  };
  const ids=new Set((anchor.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean));ids.add(id);anchor.setAttribute('aria-describedby',[...ids].join(' '));
  place();const resize=new ResizeObserver(place);resize.observe(node);resize.observe(anchor);
  return()=>{resize.disconnect();const ids=(anchor.getAttribute('aria-describedby')||'').split(/\s+/).filter(value=>value&&value!==id);if(ids.length)anchor.setAttribute('aria-describedby',ids.join(' '));else anchor.removeAttribute('aria-describedby');node.hidePopover?.()};
 },[anchor,children,id]);
 return <div ref={ref} id={id} role="tooltip" popover="manual" className="a-tooltip">{children}</div>;
}

// Delegate once at the application boundary, including dynamically loaded controls.
// Explicit text is data-tooltip or title; otherwise icon buttons use their label.
export function ButtonTooltips({rootRef}){
 const [active,setActive]=useState(null);
 useEffect(()=>{
  const root=rootRef.current;if(!root)return;
  let current=null;
  const nativeTitles=new WeakMap();
  const clear=()=>{
   current=null;setActive(null);
  };
  const show=event=>{
   if(event.pointerType==='touch')return;
   const target=event.target.closest?.('button,[role="button"],a[href]');
   if(!target||!root.contains(target)||target.closest('[data-tooltip="off"]'))return;
   if(target===current?.target)return;
   clear();
   const authoredTitle=target.getAttribute('title');
   if(authoredTitle!==null)nativeTitles.set(target,authoredTitle);
   const title=nativeTitles.get(target);
   const text=target.dataset.tooltip||title||((target.classList.contains('a-icon')||target.querySelector('svg'))?target.getAttribute('aria-label'):null);
   if(!text||target.getAttribute('aria-expanded')==='true')return;
   if(!target.hasAttribute('aria-label')&&!target.textContent.trim())target.setAttribute('aria-label',text);
   current={target,title};target.removeAttribute('title');setActive({target,text});
  };
  const leave=event=>{if(current?.target.contains(event.target)&&!current.target.contains(event.relatedTarget))clear()};
  const key=event=>{if(event.key==='Escape')clear()};
  const changed=new MutationObserver(()=>{
   if(!current)return;
   const target=current.target;
   if(!target.isConnected||target.getAttribute('aria-expanded')==='true'){clear();return}
   const title=target.getAttribute('title');
   if(title!==null){current.title=title;nativeTitles.set(target,title);target.removeAttribute('title')}
   const text=target.dataset.tooltip||current.title||target.getAttribute('aria-label');
   setActive(previous=>previous?.target===target&&previous.text===text?previous:{target,text});
  });changed.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['title','aria-label','aria-expanded','data-tooltip']});
  root.addEventListener('pointerover',show,true);root.addEventListener('pointerout',leave,true);root.addEventListener('focusin',show,true);root.addEventListener('focusout',leave,true);root.addEventListener('pointerdown',clear,true);root.addEventListener('keydown',key,true);
  window.addEventListener('scroll',clear,true);window.addEventListener('resize',clear);
  return()=>{clear();changed.disconnect();root.removeEventListener('pointerover',show,true);root.removeEventListener('pointerout',leave,true);root.removeEventListener('focusin',show,true);root.removeEventListener('focusout',leave,true);root.removeEventListener('pointerdown',clear,true);root.removeEventListener('keydown',key,true);window.removeEventListener('scroll',clear,true);window.removeEventListener('resize',clear)};
 },[rootRef]);
 return active?<Tooltip anchor={active.target}>{active.text}</Tooltip>:null;
}
