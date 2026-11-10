// Both directions require a current user gesture. Restoration, anchoring and
// resizing can move the scroll offset without requesting any more history.
export function followEarlierHistory(pane,canLoad,load){
 return followHistory(pane,canLoad,load,-1).dispose;
}

// Recent grows downwards. A scroll event alone is not user intent: resize,
// browser anchoring, focus and row restoration all generate the same event.
export function followLaterHistory(pane,canLoad,load){
 return followHistory(pane,canLoad,load,1);
}
function followHistory(pane,canLoad,load,direction){
 let previous=pane.scrollTop,busy=false,disposed=false,armed=false,frame=null,touch=null,pointer=null;
 const document=pane.ownerDocument,window=document?.defaultView;
 const disarm=()=>{
  armed=false;previous=pane.scrollTop;
  if(frame!==null)window?.cancelAnimationFrame?.(frame);
  frame=null;
 };
 const reset=()=>{disarm();touch=null;pointer=null};
 const arm=()=>{
  // A passive wheel can arrive after the compositor has already moved the
  // scroll offset, before its scroll event reaches JS. Keep the last observed
  // position so arming does not erase that movement.
  const observed=previous;disarm();previous=observed;
  if(disposed||busy||!canLoad())return;
  armed=true;
  // Unused input cannot remain permission for a later anchor/focus scroll.
  frame=window?.requestAnimationFrame?.(()=>{frame=null;disarm()})??null;
 };
 const input=event=>{
  if(event.type==='wheel'){
   if(!(event.deltaY*direction>0)||event.ctrlKey||Math.abs(event.deltaX||0)>Math.abs(event.deltaY)){reset();return}
  }
  if(event.type==='keydown'){
   const keys=direction===1?['ArrowDown','PageDown','End']:['ArrowUp','PageUp','Home'];
   const space=event.key===' '&&!!event.shiftKey===(direction===-1);
   if(!(keys.includes(event.key)||space)
    ||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey&&!space
    ||event.target?.closest?.('input,textarea,select,[contenteditable="true"]')
    ||event.key===' '&&event.target!==pane){reset();return}
  }
  if(event.type==='touchstart'){
   reset();
   const first=event.touches?.length===1&&event.touches[0];
   if(first)touch={id:first.identifier,y:first.clientY};
   return;
  }
  if(event.type==='touchmove'){
   const next=event.touches?.length===1&&event.touches[0],last=touch;
   disarm();
   if(!next||!last||next.identifier!==last.id){touch=null;return}
   touch={id:next.identifier,y:next.clientY};
   if(!((last.y-next.clientY)*direction>0))return;
  }
  if(event.type==='pointerdown'){
   reset();
   const rect=pane.getBoundingClientRect?.();
   // Only the real scrollbar track/thumb, not blank content or a touch tap.
   if(event.target!==pane||event.pointerType!=='mouse'||event.button!==0||!rect
    ||pane.offsetWidth<=pane.clientWidth||event.clientX<rect.left+pane.clientWidth)return;
   pointer={id:event.pointerId,y:event.clientY};
   const thumb=pane.clientHeight*pane.clientHeight/pane.scrollHeight;
   const thumbTop=pane.scrollTop*(pane.clientHeight-thumb)/(pane.scrollHeight-pane.clientHeight);
   if(!(direction===1?event.clientY-rect.top>thumbTop+thumb:event.clientY-rect.top<thumbTop))return;
  }
  if(event.type==='pointermove'){
   const last=pointer;disarm();
   if(!last||event.pointerId!==last.id||!(event.buttons&1)){pointer=null;return}
   pointer={id:last.id,y:event.clientY};
   if(!((event.clientY-last.y)*direction>0))return;
  }
  arm();
 };
 const scroll=()=>{
  const top=pane.scrollTop,forward=(top-previous)*direction>0;previous=top;
  const intended=armed;disarm();
  const distance=direction===1?pane.scrollHeight-pane.clientHeight-top:top;
  if(disposed||busy||!intended||!forward||distance>120||!canLoad())return;
  busy=true;Promise.resolve().then(()=>{if(!disposed&&canLoad())return load()})
   .catch(()=>{}).finally(()=>{busy=false;reset()});
 };
 const events=['wheel','touchstart','touchmove','keydown','pointerdown','pointermove'];
 const resets=['focusin','focusout','pointerdown','click','keydown','touchstart','visibilitychange'];
 const ends=['touchend','touchcancel'],pointerEnds=['pointerup','pointercancel'];
 const endPointer=()=>{disarm();pointer=null};
 for(const event of events)pane.addEventListener(event,input,{passive:true});
 for(const event of resets)document?.addEventListener?.(event,reset,true);
 for(const event of ends)document?.addEventListener?.(event,reset,true);
 for(const event of pointerEnds)document?.addEventListener?.(event,endPointer,true);
 pane.addEventListener('scroll',scroll,{passive:true});
 window?.addEventListener('resize',reset,{passive:true});
 window?.addEventListener('blur',reset);
 return {
  // Clear before and after the assignment, including asynchronously delivered
  // scroll events. Only a subsequent real input may re-arm this follower.
  suppress:move=>{reset();try{return move()}finally{reset()}},
  dispose:()=>{disposed=true;reset();pane.removeEventListener('scroll',scroll);window?.removeEventListener('resize',reset);
   window?.removeEventListener('blur',reset);
   for(const event of events)pane.removeEventListener(event,input);
   for(const event of resets)document?.removeEventListener?.(event,reset,true);
   for(const event of ends)document?.removeEventListener?.(event,reset,true);
   for(const event of pointerEnds)document?.removeEventListener?.(event,endPointer,true)}
 };
}
