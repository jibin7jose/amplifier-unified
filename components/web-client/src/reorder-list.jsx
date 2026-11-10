import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {GripVertical} from 'lucide-react';
import './reorder-list.css';

export function moveItem(ids,id,to){if(!ids.includes(id))return ids;const next=ids.filter(key=>key!==id);next.splice(Math.max(0,Math.min(to,next.length)),0,id);return next;}
const MAX_QUEUED_MOVES=32;

// Both immediate actions and Settings drafts use this pointer/keyboard control.
// Consumers own persistence; cancelled drags never call onChange.
export function ReorderList({items,ids,onChange,busy=false,queueChanges=false,onQueueDiscard,onQueueFull,contextKey='',renderItem,className='',rowClassName='',floatingClassName='',action='view.update',savedHint='',onDraggingChange,orderKey=ids.join('\0')}){
 const root=useRef(null),drag=useRef(null),rects=useRef(new Map()),pending=useRef(false),alive=useRef(true);
 const queued=useRef([]),optimistic=useRef(null),latest=useRef(null);
 latest.current={onChange,onQueueDiscard,onQueueFull,contextKey};
 const [preview,setPreview]=useState(null),[floating,setFloating]=useState(null),[announcement,announce]=useState('');
 const rows=new Map(items.map(item=>[item.id,item]));
 const shown=preview&&preview.length===ids.length&&preview.every(id=>ids.includes(id))?preview:ids;
 const blocked=()=>pending.current?!queueChanges:busy;
 const full=()=>queued.current.length>=MAX_QUEUED_MOVES;
 const reportFull=()=>{announce('Move queue is full. Wait for the saved order before moving another item.');latest.current.onQueueFull?.()};
 const focus=(id,owner=globalThis.document?.activeElement)=>globalThis.requestAnimationFrame?.(()=>{if(globalThis.document?.activeElement===owner)root.current?.querySelector(`[data-order-id="${CSS.escape(id)}"] .a-reorder-grip`)?.focus({preventScroll:true})});
 const capture=()=>{if(root.current)rects.current=new Map([...root.current.querySelectorAll('[data-order-id]')].map(node=>[node.dataset.orderId,node.getBoundingClientRect()]));};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;drag.current=null;queued.current=[]}},[]);
 useLayoutEffect(()=>{
  if(!root.current||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  for(const node of root.current.querySelectorAll('[data-order-id]')){
   const before=rects.current.get(node.dataset.orderId);node.getAnimations().forEach(animation=>animation.cancel());const after=node.getBoundingClientRect();
   if(before&&node.dataset.orderId!==drag.current?.id&&before.top!==after.top)node.animate([{transform:`translateY(${before.top-after.top}px)`},{transform:'translateY(0)'}],{duration:150,easing:'ease-out'});
  }
 },[shown.join('\0')]);
 const commit=async(next,id,basis=optimistic.current||ids)=>{
  if(blocked())return;
  if(full()){reportFull();return;}
  if(next.join('\0')===basis.join('\0')){if(!pending.current)setPreview(null);return;}
  const to=next.indexOf(id),focusOwner=globalThis.document?.activeElement;
  queued.current.push({next,id,to,basis:[...basis],target:basis[to],contextKey,focusOwner});
  if(full())reportFull();
  optimistic.current=next;setPreview(next);
  if(pending.current)return;
  pending.current=true;let last;
  try{
   while(alive.current&&queued.current.length){
    last=queued.current.shift();
    if(last.contextKey!==latest.current.contextKey)throw Error('The list changed. Queued moves were not sent.');
    const committed=await latest.current.onChange(last.next,{id:last.id,to:last.to,basis:last.basis,target:last.target,contextKey:last.contextKey});
    // A held next gesture may start against the optimistic order before React
    // renders this exact local commit. Advance only its matching order witness.
    if(drag.current&&drag.current.contextKey===last.contextKey&&drag.current.original.join('\0')===last.next.join('\0')&&committed?.orderKey!==undefined)
     drag.current.orderKey=committed.orderKey;
   }
   if(alive.current&&last)announce(`${rows.get(last.id)?.label} moved to position ${last.to+1}.${savedHint?' '+savedHint:''}`);
  }
  catch(error){const discarded=queued.current.length+(drag.current?.active?1:0);queued.current=[];optimistic.current=null;if(drag.current)finish(true);if(alive.current){if(discarded)latest.current.onQueueDiscard?.(discarded,error);announce((error.message||'Could not change order.')+(discarded?' Later queued moves were not sent.':''))}}
  // A later receipt must not move focus back after the user left this control.
  finally{pending.current=false;optimistic.current=null;if(alive.current){if(!drag.current)setPreview(null);if(last)focus(last.id,last.focusOwner)}}
 };
 const finish=cancel=>{
  const current=drag.current;if(!current)return;capture();drag.current=null;setFloating(null);onDraggingChange?.(false);
  if(root.current?.hasPointerCapture(current.pointerId))root.current.releasePointerCapture(current.pointerId);
  if(current.active&&!cancel&&current.valid)void commit(current.preview,current.id,current.original);else{setPreview(optimistic.current);announce('Reorder cancelled.');focus(current.id)}
 };
 useEffect(()=>{if(typeof document==='undefined')return;const key=e=>{if(e.key==='Escape'&&drag.current){e.preventDefault();e.stopPropagation();finish(true)}};document.addEventListener('keydown',key,true);return()=>document.removeEventListener('keydown',key,true)});
 // A concurrent pin/unpin, stale page or external order change invalidates a held drag.
 useEffect(()=>{if(drag.current&&(drag.current.contextKey!==contextKey||!(queueChanges&&pending.current)&&(busy||drag.current.original.join('\0')!==ids.join('\0')||drag.current.orderKey!==orderKey)))finish(true)},[ids.join('\0'),orderKey,busy,contextKey]);
 const down=(event,id)=>{
  if(blocked()||event.button!==0)return;if(full()){reportFull();return;}event.preventDefault();
  document.dispatchEvent(new CustomEvent('amplifier-navigation-details',{detail:'reordering'}));
  const node=event.currentTarget.closest('[data-order-id]'),box=node.getBoundingClientRect(),bounds=root.current.getBoundingClientRect();
  let scroll=root.current.parentElement;while(scroll&&!(scroll.scrollHeight>scroll.clientHeight&&/(auto|scroll)/.test(getComputedStyle(scroll).overflowY)))scroll=scroll.parentElement;
  const nodes=[...root.current.querySelectorAll('[data-order-id]')];
  const basis=optimistic.current||ids;
  drag.current={id,pointerId:event.pointerId,x:event.clientX,y:event.clientY,box,bounds,scroll,scrollTop:scroll?.scrollTop||0,centers:nodes.map(n=>{const r=n.getBoundingClientRect();return(r.top+r.bottom)/2}),original:[...basis],orderKey,contextKey,preview:[...basis],active:false,valid:true};
  root.current.setPointerCapture(event.pointerId);
 };
 const moving=event=>{
  const current=drag.current;if(!current)return;
  if(!current.active&&Math.hypot(event.clientX-current.x,event.clientY-current.y)<5)return;
  if(!current.active)onDraggingChange?.(true);current.active=true;event.preventDefault();const {box,bounds,scroll}=current;
  if(scroll){const area=scroll.getBoundingClientRect();if(event.clientY<area.top+35)scroll.scrollTop-=12;else if(event.clientY>area.bottom-35)scroll.scrollTop+=12}
  const delta=(scroll?.scrollTop||0)-current.scrollTop;
  current.valid=event.clientX>=bounds.left-24&&event.clientX<=bounds.right+24&&event.clientY>=bounds.top-delta-28&&event.clientY<=bounds.bottom-delta+28;
  let to=0,best=Infinity;current.centers.forEach((y,index)=>{const distance=Math.abs(event.clientY+delta-y);if(distance<best){best=distance;to=index}});
  const next=moveItem(current.original,current.id,to);capture();current.preview=next;setPreview(next);
  setFloating({id:current.id,left:Math.max(0,Math.min(box.left+8,innerWidth-box.width)),top:event.clientY-(current.y-box.top),width:box.width,valid:current.valid});
 };
 const move=(id,to)=>{capture();const basis=optimistic.current||ids;void commit(moveItem(basis,id,to),id,basis)};
 const handle=(item,isFloating=false)=><button type="button" className="a-icon a-order-grip a-reorder-grip" aria-disabled={blocked()||full()||undefined} draggable={false} data-action={action} aria-label={'Reorder '+item.label} title="Drag, or use Up/Down (Alt + Up/Down also works)" tabIndex={isFloating?-1:0} onPointerDown={e=>down(e,item.id)} onKeyDown={e=>{if(['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();move(item.id,(optimistic.current||ids).indexOf(item.id)+(e.key==='ArrowUp'?-1:1))}}}><GripVertical/></button>;
 const floatingItem=floating&&rows.get(floating.id),target=root.current?.closest('.a-settings-experience,.a-nav-slot')||root.current;
 return <><div className={'a-reorder-list '+className} ref={root} data-reordering={!!floating||undefined} onPointerMove={moving} onPointerUp={()=>finish(false)} onPointerCancel={()=>finish(true)} onLostPointerCapture={()=>{if(drag.current)finish(true)}} onDragStart={e=>e.preventDefault()}>
  {shown.map((id,index)=>{const item=rows.get(id)||{id,label:id};return <div key={id} data-order-id={id} className={`a-reorder-item ${rowClassName}${floating?.id===id?' a-order-placeholder':''}`}>
   {floating?.id===id&&floating.valid&&<span className="a-order-insertion" aria-hidden="true"/>}
   {renderItem(item,{handle:handle(item),index,move:to=>move(id,to),floating:false})}
  </div>})}
 </div>{floatingItem&&target&&createPortal(<div className={'a-order-floating a-reorder-floating '+floatingClassName} aria-hidden="true" inert style={{left:floating.left,top:floating.top,width:floating.width}}>{renderItem(floatingItem,{handle:handle(floatingItem,true),index:shown.indexOf(floating.id),move:()=>{},floating:true})}</div>,target)}
 <span className="a-sr-only" role="status" aria-live="polite">{announcement}</span></>;
}
