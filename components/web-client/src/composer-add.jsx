import React,{useEffect,useRef,useState,useId} from 'react';
import {Plus,X,Paperclip,Image,Shield} from 'lucide-react';
import {useComposerPopover} from './composer-popover';
import {useOutsideDismiss} from './use-outside-dismiss';

/** Presentation only. The caller retains attachment intake and access ownership. */
export function ComposerAdd({children,sessionId,panel=null,disabled=false,accessDisabled=false,onFiles,onAccess}){
 const descriptionId=useId();
 const [open,setOpen]=useState(false),anchor=useRef(null),requested=useRef(false),openedPanel=useRef(null);
 // A newer surface (including keyboard-opened Settings) owns Escape and focus.
 // Hide synchronously; the effect also forgets the old local open intent.
 // Canvas Settings is non-modal: a later explicit Add click may use the still
 // visible composer. A newly opened surface must still discard the old menu.
 const blocked=!!panel&&panel!=='settings-canvas';
 const visible=open&&!blocked&&openedPanel.current===panel;
 const {style,panelRef,renderPopover}=useComposerPopover(visible,anchor);
 const close=reason=>{setOpen(false);requested.current=false;if(reason!=='outside')anchor.current?.querySelector('button')?.focus()};
 useOutsideDismiss(visible,anchor,close,panelRef);
 useEffect(()=>{setOpen(false);requested.current=false},[sessionId,disabled,panel]);
 useEffect(()=>{if(visible&&style.visibility==='visible'&&requested.current){requested.current=false;panelRef.current?.querySelector('[data-add-choice]')?.focus()}},[visible,style.visibility]);
 const choose=kind=>{close();onFiles(kind)};
 return <div className="a-compose-add" ref={anchor}>{children}
  <button type="button" className="a-icon" aria-label="Add attachments" title="Files, images and chat access" aria-haspopup="dialog" aria-expanded={visible} disabled={disabled||blocked} onClick={()=>{if(disabled||blocked)return;if(visible)close();else{openedPanel.current=panel;requested.current=true;setOpen(true)}}}><Plus/></button>
  {visible&&renderPopover(<section ref={panelRef} style={style} className="a-model-popover a-compact-popover a-composer-options-panel a-composer-add-panel" role="dialog" aria-label="Add to your message">
   <div className="a-settings-row a-composer-options-heading"><strong>Add to your message</strong><button type="button" className="a-icon" aria-label="Close attachment menu" onClick={close}><X/></button></div>
   <button type="button" className="a-composer-option" data-tooltip="off" aria-label="Attach files" aria-describedby={`${descriptionId}-files`} data-add-choice onClick={()=>choose('files')}><Paperclip/><span>Attach files<small id={`${descriptionId}-files`}>Files and images · up to 32 MB each</small></span></button>
   <button type="button" className="a-composer-option" data-tooltip="off" aria-label="Add an image" onClick={()=>choose('image')}><Image/><span>Add an image</span></button>
   <button type="button" className="a-composer-option" data-tooltip="off" aria-label="Chat access" aria-describedby={!sessionId||accessDisabled?`${descriptionId}-access`:undefined} disabled={!sessionId||accessDisabled} onClick={()=>{close();onAccess()}}><Shield/><span>Chat access{(!sessionId||accessDisabled)&&<small id={`${descriptionId}-access`}>{!sessionId?'Available after this chat starts':'Return to this chat to review access'}</small>}</span></button>
  </section>)}
 </div>;
}