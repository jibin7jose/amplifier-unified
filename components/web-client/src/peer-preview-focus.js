// Explicit Peer dismissal only. This is not a global modal teardown policy.
export function peerFocusScope(state){
 return JSON.stringify([state?.selectedSessionId,state?.selectedWorkspaceId,state?.canvas?.id,
  state?.canvas?.selectedVersion,state?.view?.workSurface,state?.view?.panel,
  state?.navigationPending,state?.view?.draft]);
}
export function peerReturnTarget(trigger,sessionId,messageId){
 const thread=trigger?.closest?.('[data-part="messages"][data-session-id]');
 if(thread?.dataset.sessionId!==sessionId||trigger.closest('[data-message-id]')?.dataset.messageId!==messageId)return null;
 return {thread,trigger,sessionId,messageId};
}
export function deferPeerFocus(origin,isCurrent){
 if(!origin)return ()=>{};
 const {thread,trigger,sessionId,messageId}=origin,doc=thread.ownerDocument,win=doc.defaultView;
 let cancelled=false;
 const cancel=()=>{cancelled=true};
 const events=['pointerdown','keydown','beforeinput','input','focusin'];
 for(const event of events)doc.addEventListener(event,cancel,true);
 const cleanup=()=>{for(const event of events)doc.removeEventListener(event,cancel,true)};
 const frame=win.requestAnimationFrame(()=>{
  cleanup();
  if(cancelled||!isCurrent()||!thread.isConnected||thread.dataset.sessionId!==sessionId||doc.hidden)return;
  // Never steal a newer focus choice, including a private editor's caret.
  const focused=doc.activeElement;
  if(focused&&focused!==doc.body&&focused!==doc.documentElement&&focused!==trigger&&focused.isConnected)return;
  const usable=node=>node?.isConnected&&thread.contains(node)&&!node.disabled&&node.getAttribute('aria-disabled')!=='true'
   &&!node.closest('[inert],[hidden]')&&node.getClientRects().length&&win.getComputedStyle(node).visibility!=='hidden';
  const message=[...thread.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===messageId);
  const target=usable(trigger)?trigger:message?.querySelector('.a-peer-summary');
  if(usable(target)){target.focus({preventScroll:true});return}
  if(!usable(thread))return;
  // A missing/hidden original may still return to its verified current thread,
  // never another message or the composer. Don't change the persistent tab order.
  const temporary=!thread.hasAttribute('tabindex');
  if(temporary)thread.setAttribute('tabindex','-1');
  thread.focus({preventScroll:true});
  if(temporary){
   if(doc.activeElement===thread)thread.addEventListener('blur',()=>thread.removeAttribute('tabindex'),{once:true});
   else thread.removeAttribute('tabindex');
  }
 });
 return ()=>{cancelled=true;cleanup();win.cancelAnimationFrame(frame)};
}