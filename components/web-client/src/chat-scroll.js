// Reading positions contain identifiers and offsets only, never message text.
const STORAGE_KEY='amplifier.chat-reading.v1';
export function readingPositions(storage){
 let rows={};try{rows=JSON.parse(storage?.getItem(STORAGE_KEY)||'{}')}catch{}
 if(!rows||typeof rows!=='object'||Array.isArray(rows))rows={};
 return {
  get(id){return rows[id]},
  set(id,value){if(!id)return;rows[id]={...value,seenAt:Date.now()};rows=Object.fromEntries(Object.entries(rows).sort((a,b)=>b[1].seenAt-a[1].seenAt).slice(0,100));try{storage?.setItem(STORAGE_KEY,JSON.stringify(rows))}catch{}},
 };
}
export function createChatScroll(pane,following={current:true},onAway=()=>{},{loadAnchor,readingReference}={}){
 const document=pane.ownerDocument,view=document.defaultView;
 // This owner preserves message anchors; browser anchoring can otherwise move
 // the reader independently when incoming transcript rows replace the tail.
 const previousOverflowAnchor=pane.style.overflowAnchor;pane.style.overflowAnchor='none';
 const gutterProperties=['--_a-transcript-gutter-left','--_a-transcript-gutter-right'];
 const previousGutterProperties=new Map(gutterProperties.map(name=>[name,{value:pane.style.getPropertyValue(name),priority:pane.style.getPropertyPriority(name)}]));
 let measuredGutterBox=null;
 const updateGutterInsets=()=>{
  const box=`${pane.offsetWidth}:${pane.clientWidth}:${pane.clientLeft}`;
  if(box===measuredGutterBox)return;
  measuredGutterBox=box;
  // clientLeft includes an RTL left scrollbar; subtract borders to keep only the gutter.
  const style=view.getComputedStyle(pane),borderLeft=parseFloat(style.borderLeftWidth)||0,borderRight=parseFloat(style.borderRightWidth)||0;
  const left=Math.max(0,pane.clientLeft-borderLeft),right=Math.max(0,pane.offsetWidth-pane.clientLeft-pane.clientWidth-borderRight);
  const set=(name,value)=>{const cssValue=`${Math.ceil(value*1000)/1000}px`;if(pane.style.getPropertyValue(name)!==cssValue||pane.style.getPropertyPriority(name))pane.style.setProperty(name,cssValue)};
  set(gutterProperties[0],left);set(gutterProperties[1],right);
 };
 const restoreGutterInsets=()=>{for(const [name,{value,priority}] of previousGutterProperties){if(value)pane.style.setProperty(name,value,priority);else pane.style.removeProperty(name)}};
 let storage;try{storage=view.sessionStorage}catch{}
 const positions=readingPositions(storage);
 let frame=0,disposed=false,sessionId=null,ready=false,restore=null,submitted=null,snapshot=null,saveTimer=0,initial=false,resuming=false;
 // A sandboxed preview can be the host activeElement without matching CSS
 // :focus/:focus-within. Observe host focus only, never the embedded document.
 // Keep this frame separate: focus observation must not move or save anchors.
 let iframeFocusFrame=0,iframeFocused=false;
 const clearIframeFocus=()=>{view.cancelAnimationFrame(iframeFocusFrame);iframeFocusFrame=0;iframeFocused=false;delete pane.dataset.transcriptIframeFocus};
 const flushIframeFocus=()=>{
  iframeFocusFrame=0;if(disposed)return;
  const active=document.activeElement;
  const next=!!(ready&&pane.isConnected&&active?.localName==='iframe'&&pane.contains(active));
  if(next===iframeFocused)return;
  iframeFocused=next;
  if(next)pane.dataset.transcriptIframeFocus='true';else delete pane.dataset.transcriptIframeFocus;
 };
 const queueIframeFocus=()=>{if(ready&&!disposed&&!iframeFocusFrame)iframeFocusFrame=view.requestAnimationFrame(flushIframeFocus)};
 let callId=null,callSessionId=null,callPaused=false;
 let anchorLoad=null,attemptedAnchor=null,unresolvedPosition=null;
 const followingCall=()=>!!callId&&callSessionId===sessionId&&!callPaused;
 // Heights are integer CSS pixels; scrollTop can be fractional after scaling.
 // This is geometry rounding, not a proximity threshold for reading intent.
 const atBottom=()=>Math.abs(pane.scrollHeight-pane.clientHeight-pane.scrollTop)<=1;
 const messages=()=>[...pane.querySelectorAll('[data-message-id]')];
 const clearOverflow=()=>{delete pane.dataset.overflowAbove;delete pane.dataset.overflowBelow};
 const capture=()=>{
  const top=pane.getBoundingClientRect().top,rows=messages();
  const anchor=rows.find(node=>node.getBoundingClientRect().bottom>top)||rows.at(-1);
  const messageId=anchor?.dataset.messageId,reference=readingReference?.(sessionId,messageId);
  snapshot={top:pane.scrollTop,messageId,offset:anchor?anchor.getBoundingClientRect().top-top:0,
   ...(reference?.kind==='transcript-window'&&typeof reference.restoreCursor==='string'?{reference:{kind:reference.kind,restoreCursor:reference.restoreCursor}}:{})};
 };
 // A reply anchor is different from deliberate scrollback. Preserve that intent
 // across chat switches, even when the reply had not arrived before leaving.
 const persist=()=>{view.clearTimeout(saveTimer);if(ready&&(unresolvedPosition||snapshot))positions.set(sessionId,unresolvedPosition||{...snapshot,submittedId:following.current?submitted:null})};
 const report=()=>{const remaining=pane.scrollHeight-pane.scrollTop-pane.clientHeight;if(ready){pane.dataset.overflowAbove=String(pane.scrollTop>8);pane.dataset.overflowBelow=String(remaining>1);updateGutterInsets()}else clearOverflow();onAway(remaining>80);if(ready){capture();view.clearTimeout(saveTimer);saveTimer=view.setTimeout(persist,200)}};
 const restorePosition=position=>{
  const anchor=messages().find(node=>node.dataset.messageId===position.messageId);
  if(anchor)pane.scrollTop+=anchor.getBoundingClientRect().top-pane.getBoundingClientRect().top-position.offset;
  else pane.scrollTop=Math.min(position.top||0,pane.scrollHeight);
 };
 const flush=()=>{
  frame=0;if(disposed||!ready)return;
  if(followingCall()){
   anchorLoad=null;unresolvedPosition=null;restore=null;initial=false;submitted=null;resuming=false;following.current=false;pane.scrollTop=pane.scrollHeight;
  }else if(restore){
   if(restore.messageId&&!messages().length)return;
   if(restore.messageId&&!messages().some(node=>node.dataset.messageId===restore.messageId)&&loadAnchor&&attemptedAnchor!==restore.messageId){
    const token={sessionId,messageId:restore.messageId,reference:restore.reference};anchorLoad=token;attemptedAnchor=token.messageId;unresolvedPosition=restore;
    const current=()=>!disposed&&anchorLoad===token;
    // A successful loader queues a render; it does not guarantee that React
    // has committed its DOM yet. Keep restoration until the anchor mounts.
    Promise.resolve().then(()=>current()&&loadAnchor(token.sessionId,token.messageId,current,token.reference)).catch(()=>false).then(queued=>{if(current()){if(queued!==true)anchorLoad=null;update()}});
    return;
   }
   const anchorReady=messages().some(node=>node.dataset.messageId===restore.messageId);
   if(anchorLoad&&!anchorReady)return;
   if(anchorReady){anchorLoad=null;unresolvedPosition=null}
   restorePosition(restore);
   restore=null;initial=false;
  }else if(submitted&&following.current){
   const anchor=messages().find(node=>node.dataset.inputId===submitted||node.dataset.messageId===submitted);
   if(anchor){resuming=false;const end=pane.scrollTop+anchor.getBoundingClientRect().bottom-pane.getBoundingClientRect().top;pane.scrollTop=Math.max(0,end-16)}
   else if(resuming&&messages().length){resuming=false;submitted=null;following.current=false;pane.scrollTop=pane.scrollHeight}
  }else if(initial){pane.scrollTop=pane.scrollHeight;initial=false;following.current=false}
  else if(snapshot)restorePosition(snapshot);
  report();
 };
 const update=()=>{if(!frame&&!disposed)frame=view.requestAnimationFrame(flush)};
 const stop=()=>{if(callId&&callSessionId===sessionId)callPaused=true;anchorLoad=null;unresolvedPosition=null;submitted=null;following.current=false;restore=null;initial=false};
 // Clicking/selecting transcript content is not scrollback. Actual scrollbar
 // moves are detected by scroll(); wheel/touch/keyboard express reading intent.
 const wheel=e=>{
  if(followingCall()&&(!e.deltaY||e.deltaY>0&&atBottom()||e.deltaY<0&&pane.scrollTop<=0))return;
  stop();
 };
 const keyDown=e=>{if(!e.target.closest('input,textarea,select,[contenteditable="true"]')&&['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))stop()};
 // Our own moves capture their final position synchronously in flush(). A
 // different position here came from outside this controller, including the
 // browser revealing a focused control in a sandboxed iframe. Those moves do
 // not necessarily send wheel/pointer events through the parent document.
 const scroll=()=>{
  if(!ready||restore||snapshot&&pane.scrollTop===snapshot.top)return;
  // Removing a notice (or enlarging the viewport) can clamp the old bottom
  // before our layout frame runs. That does not cancel call or submitted intent.
  // Wheel/touch/key intent has already stopped following; other external
  // scrolls, including browser focus moves, retain their existing behavior.
  const limit=Math.max(0,pane.scrollHeight-pane.clientHeight);
  if(snapshot&&(followingCall()||(submitted&&following.current))&&snapshot.top>limit&&atBottom()){update();return}
  if(snapshot)stop();report();
 };
 const resize=new view.ResizeObserver(update),observed=new Set();
 const observeChildren=()=>{
  for(const child of observed)if(child.parentElement!==pane){resize.unobserve(child);observed.delete(child)}
  for(const child of pane.children)if(!observed.has(child)){resize.observe(child);observed.add(child)}
 };
 const mutations=new view.MutationObserver(()=>{observeChildren();update();queueIframeFocus()});
 resize.observe(pane);observeChildren();mutations.observe(pane,{subtree:true,childList:true,characterData:true});
 pane.addEventListener('scroll',scroll,{passive:true});pane.addEventListener('wheel',wheel,{passive:true});pane.addEventListener('touchmove',stop,{passive:true});pane.addEventListener('keydown',keyDown);view.addEventListener('pagehide',persist);
 document.addEventListener('focusin',queueIframeFocus);document.addEventListener('focusout',queueIframeFocus);view.addEventListener('blur',queueIframeFocus);view.addEventListener('focus',queueIframeFocus);
 return {
  update,
  // React-owned layout changes can arrive after this frame's observer flush.
  // Settle them before paint; ordinary stream/resize updates stay batched.
  settleLayout(){view.cancelAnimationFrame(frame);flush()},
  setCall(id,conversationId){
   if(id===callId&&conversationId===callSessionId)return;
   callId=id;callSessionId=conversationId;callPaused=false;update();
  },
  select(id,isReady=true){
   if(id!==sessionId){
    clearIframeFocus();persist();clearOverflow();const sendingNewChat=sessionId===null&&submitted;
    sessionId=id;snapshot=null;restore=positions.get(id)||null;anchorLoad=null;attemptedAnchor=null;unresolvedPosition=null;
    submitted=sendingNewChat||(typeof restore?.submittedId==='string'?restore.submittedId:null);
    resuming=!!submitted&&!sendingNewChat;
    following.current=!!submitted;
    if(submitted)restore=null;
    initial=!restore&&!submitted;onAway(false);
   }
   ready=isReady;if(!ready){clearOverflow();clearIframeFocus()}else queueIframeFocus();update();
  },
  submitted(id){if(callId&&callSessionId===sessionId)callPaused=false;anchorLoad=null;unresolvedPosition=null;submitted=id;resuming=false;restore=null;initial=false;following.current=true;update()},
  jump(id){stop();const node=messages().find(row=>row.dataset.messageId===id);if(node){node.tabIndex=-1;pane.scrollTop+=node.getBoundingClientRect().top-pane.getBoundingClientRect().top-16;node.focus({preventScroll:true});report()}},
  reveal(){stop();if(callId&&callSessionId===sessionId)callPaused=false;pane.scrollTop=pane.scrollHeight;report()},
  dispose(){persist();disposed=true;clearOverflow();clearIframeFocus();restoreGutterInsets();pane.style.overflowAnchor=previousOverflowAnchor;view.cancelAnimationFrame(frame);view.clearTimeout(saveTimer);resize.disconnect();mutations.disconnect();pane.removeEventListener('scroll',scroll);pane.removeEventListener('wheel',wheel);pane.removeEventListener('touchmove',stop);pane.removeEventListener('keydown',keyDown);view.removeEventListener('pagehide',persist);document.removeEventListener('focusin',queueIframeFocus);document.removeEventListener('focusout',queueIframeFocus);view.removeEventListener('blur',queueIframeFocus);view.removeEventListener('focus',queueIframeFocus)},
 };
}
