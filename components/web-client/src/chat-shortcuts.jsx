import React,{createContext,useContext,useEffect,useRef,useState} from 'react';

const ChatShortcuts=createContext([]);
const chord=e=>e.altKey&&e.shiftKey&&!e.ctrlKey&&!e.metaKey;
const editing=node=>!!node?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="combobox"]');
const visible=node=>!!node?.isConnected&&!node.closest('[hidden],[inert]')&&node.getClientRects().length>0;

// One gesture snapshots the currently rendered list. No library reads, polling,
// or changing numeric targets when a background update reorders the sidebar.
export function ChatShortcutScope({navigation,enabled,scope,children}){
 const [targets,setTargets]=useState([]),cancelRef=useRef(()=>{});
 useEffect(()=>{cancelRef.current()},[scope]);
 useEffect(()=>{
  if(!enabled||typeof window==='undefined'||typeof document==='undefined')return;
  let timer=null,held=false,armed=false,frozen=[];
  const cancel=()=>{clearTimeout(timer);timer=null;held=false;armed=false;frozen=[];setTargets([])};
  cancelRef.current=cancel;
  const blocked=()=>!visible(navigation.current)||editing(document.activeElement)||
   [...document.querySelectorAll('[role="dialog"],dialog[open],[role="menu"],[aria-modal="true"]')].some(node=>node!==navigation.current&&visible(node));
  const down=e=>{
   if(e.key==='Escape'){if(held){e.preventDefault();cancel()}return}
   if(e.isComposing||e.key==='Process'||!chord(e)||blocked()){cancel();return}
   const digit=/^Digit([1-9])$/.exec(e.code)?.[1];
   if(digit){
    if(!armed||e.repeat)return;
    const target=frozen[Number(digit)-1];
    if(!target)return;
    e.preventDefault();e.stopPropagation();
    // Re-resolve by ID, never by position, and use the normal guarded click.
    const button=[...navigation.current.querySelectorAll('.a-nav-chat[data-session-id]')]
     .find(row=>row.dataset.sessionId===target.id&&visible(row))?.querySelector('.a-nav-chat-select');
    cancel();if(visible(button)&&!button.disabled)button.click();return;
   }
   if(held||e.repeat||!['Alt','Shift'].includes(e.key))return;
   frozen=[...navigation.current.querySelectorAll('.a-nav-chat[data-session-id]')]
    .filter(row=>visible(row)&&row.querySelector('.a-nav-chat-select:not(:disabled)'))
    .map(row=>({id:row.dataset.sessionId,title:row.querySelector('.a-nav-chat-select').getAttribute('aria-label')}))
    .filter((row,index,rows)=>rows.findIndex(other=>other.id===row.id)===index).slice(0,9);
   if(!frozen.length)return;
   held=true;timer=setTimeout(()=>{if(blocked()){cancel();return}armed=true;setTargets(frozen)},900);
  };
  const up=e=>{if(!chord(e))cancel()};
  const focus=()=>{if(blocked())cancel()};
  const hidden=()=>{if(document.hidden)cancel()};
  window.addEventListener('keydown',down,true);window.addEventListener('keyup',up,true);
  window.addEventListener('blur',cancel);document.addEventListener('visibilitychange',hidden);document.addEventListener('focusin',focus);
  return()=>{cancel();cancelRef.current=()=>{};window.removeEventListener('keydown',down,true);window.removeEventListener('keyup',up,true);window.removeEventListener('blur',cancel);document.removeEventListener('visibilitychange',hidden);document.removeEventListener('focusin',focus)};
 },[navigation,enabled]);
 return <ChatShortcuts.Provider value={targets}>{children}<span className="a-sr-only" role="status">{targets.length?'Chat shortcuts. Hold Alt and Shift, then press a number from 1 to '+targets.length+'.':''}</span></ChatShortcuts.Provider>;
}

export function ChatShortcutBadge({id}){
 const targets=useContext(ChatShortcuts),index=targets.findIndex(target=>target.id===id);
 return index<0?null:<kbd className="a-chat-shortcut" aria-label={'Chat shortcut Alt Shift '+(index+1)}>{index+1}</kbd>;
}
