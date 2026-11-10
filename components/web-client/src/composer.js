/** Presentation only: keep short prompts compact; give rich/long content full width. */
export function composerExpanded({draft='',attachments=[],quote=null}={}){
 return !!quote||attachments.length>0||draft.includes('\n')||draft.length>80;
}

/** Reserve readable conversation space after ALL non-editor rows, not just vh. */
export function quoteComposerHeightBudget({viewportHeight,contentBottom,messagesTop,messagesBottom,composerBottom,composerMarginBottom=0,editorHeight}){
 const chrome=composerBottom+composerMarginBottom-messagesBottom-editorHeight;
 return Math.max(44,Math.min(400,viewportHeight*.35,Math.floor(contentBottom-messagesTop-chrome-80)));
}

function quoteComposerMaxHeight(element){
 const conversation=element.closest?.('.a-conversation'),form=element.closest?.('.a-composer');
 const messages=conversation?.querySelector('.a-messages'),win=element.ownerDocument?.defaultView;
 if(!win||!form||!messages||!element.getClientRects().length)return 'min(400px, 35dvh)';
 const conversationBox=conversation.getBoundingClientRect(),editorBox=element.getBoundingClientRect();
 if(!conversationBox.height||!editorBox.height)return 'min(400px, 35dvh)';
 const style=win.getComputedStyle(conversation),formStyle=win.getComputedStyle(form);
 const number=value=>Number.parseFloat(value)||0,viewport=win.visualViewport;
 const viewportHeight=viewport?.height||win.innerHeight,viewportBottom=(viewport?.offsetTop||0)+viewportHeight;
 const contentBottom=Math.min(conversationBox.bottom-number(style.borderBottomWidth)-number(style.paddingBottom),viewportBottom);
 const messageBox=messages.getBoundingClientRect();
 const limit=quoteComposerHeightBudget({viewportHeight,contentBottom,messagesTop:messageBox.top,messagesBottom:messageBox.bottom,
  composerBottom:form.getBoundingClientRect().bottom,composerMarginBottom:number(formStyle.marginBottom),editorHeight:editorBox.height});
 return `${limit}px`;
}

/** Re-measure after any draft update, including agent-origin updates. */
export function resizeComposer(element){
 if(!element)return;
 // Measure before changing the bound. Subtracting the current editor height
 // keeps this stable even after messages reach their padding floor/overflow.
 const maxHeight=element.querySelector?.('[data-quote-instance]')?quoteComposerMaxHeight(element):'180px';
 element.style.minHeight='44px';
 if(element.style.maxHeight!==maxHeight)element.style.maxHeight=maxHeight;
 element.style.height='auto';
 // Rich text blocks size naturally; only textareas need scrollHeight sizing.
 if(element.hasAttribute?.('contenteditable')){element.style.overflowY='auto';return;}
 const height=Math.max(44,Math.min(180,element.scrollHeight));
 element.style.height=`${height}px`;
 element.style.overflowY=element.scrollHeight>180?'auto':'hidden';
 element.style.resize='none';
}

/** Layout-only changes (font size, uploads, Latest, notices) also consume space. */
export function observeComposerLayout(element){
 const conversation=element?.closest?.('.a-conversation'),win=element?.ownerDocument?.defaultView;
 if(!conversation||!win?.ResizeObserver||!win?.MutationObserver)return ()=>{};
 let frame=null,disposed=false;
 const schedule=()=>{
  if(disposed||frame!==null)return;
  frame=win.requestAnimationFrame(()=>{frame=null;if(!disposed)resizeComposer(element)});
 };
 const resize=new win.ResizeObserver(schedule),children=new Set();
 resize.observe(conversation);resize.observe(element);
 const sync=()=>{
  const next=new Set(conversation.children);
  for(const child of children)if(!next.has(child)){resize.unobserve(child);children.delete(child)}
  for(const child of next)if(!children.has(child)){children.add(child);resize.observe(child)}
  schedule();
 };
 const mutation=new win.MutationObserver(sync);
 // Observe only row additions/removals, not transcript streaming or own styles.
 mutation.observe(conversation,{childList:true});sync();
 win.addEventListener('resize',schedule);win.visualViewport?.addEventListener('resize',schedule);win.visualViewport?.addEventListener('scroll',schedule);
 return()=>{disposed=true;resize.disconnect();mutation.disconnect();if(frame!==null)win.cancelAnimationFrame(frame);win.removeEventListener('resize',schedule);win.visualViewport?.removeEventListener('resize',schedule);win.visualViewport?.removeEventListener('scroll',schedule)};
}

/** Content wins over voice/Stop; a pending upload must never become a voice launch. */
export function composerPrimaryAction({draft='',attachments=[],uploading=false,working=false,responseActive=false,stopping=false,voiceStatus='idle',voiceStarting=false,busy=false,newChatPending=false,sending=false,historyPending=false,executionUnavailable=false}={}){
 const content=!!draft.trim()||attachments.length>0;
 if(content||uploading)return {action:'conversation.send',label:'Send message',icon:'send',disabled:!content||busy||newChatPending||uploading||historyPending||executionUnavailable};
 if(!['idle','ended','error'].includes(voiceStatus))return {action:'call.end',label:'End voice call',icon:'cancel',disabled:voiceStatus==='ending'};
 if(responseActive)return {action:'conversation.stop',label:'Stop response',icon:'cancel',disabled:stopping||executionUnavailable};
 if(newChatPending||sending)return {action:'conversation.send',label:'Send message',icon:'send',disabled:true};
 return {action:'call.start',label:'Start voice call',icon:'voice',disabled:busy||voiceStarting||historyPending||executionUnavailable};
}
