import React,{useEffect,useRef} from 'react';
import {Bug,Ellipsis,Palette,ScanEye,Info,Settings,X} from 'lucide-react';
import {AttentionBadge} from './attention';
import './app-toolbar.css';
import {useNarrowScreen,useModalFocus} from './responsive-navigation';

export function FeedbackAction({state,openPanel}){
 return <button type="button" className="a-icon" aria-label="Send feedback" title="Send feedback" data-action="view.update" onClick={()=>openPanel('feedback')}><Bug/><AttentionBadge state={state} section="feedback"/></button>;
}

export function MoreAppActions({state,act,openPanel}){
 const root=useRef(null),trigger=useRef(null),menu=useRef(null),narrow=useNarrowScreen(),compact=useNarrowScreen('(max-width: 1024px)');
 const expanded=!!state.view?.toolbarMenuOpen&&!state.view?.canvasFocused&&!state.view?.panel;
 const close=()=>act('view.update',{patch:{toolbarMenuOpen:false}});
 useModalFocus(menu,narrow&&expanded,close);
 useEffect(()=>{
  if(!expanded)return;
  root.current?.querySelector('.a-toolbar-menu button')?.focus();
  const outside=event=>{if(root.current&&!root.current.contains(event.target))close()};
  const escape=event=>{if(!narrow&&event.key==='Escape'){event.preventDefault();event.stopPropagation();trigger.current?.focus();close()}};
  document.addEventListener('pointerdown',outside);
  document.addEventListener('focusin',outside);
  document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('focusin',outside);document.removeEventListener('keydown',escape)};
 },[expanded,act,narrow]);
 const choose=panel=>{trigger.current?.focus();openPanel(panel)};
 return <div className="a-toolbar-more" ref={root}>
  <button ref={trigger} type="button" className="a-icon" aria-label="More app options" aria-expanded={expanded} aria-controls="app-toolbar-menu" data-action="view.update" onClick={()=>act('view.update',{patch:{toolbarMenuOpen:!expanded}})}><Ellipsis/>{compact&&<AttentionBadge state={state} settings/>}</button>
  {expanded&&<><div className="a-toolbar-scrim" onClick={close}/><div ref={menu} id="app-toolbar-menu" className="a-toolbar-menu" role={narrow?"dialog":"group"} aria-modal={narrow||undefined} aria-label="More app options">
   {narrow&&<button className="a-menu-close" type="button" onClick={close}><X/>Close menu</button>}
   {compact&&<><button type="button" disabled={!state.selectedSessionId} data-action="view.update" onClick={()=>choose('session-details')}><Info/>Chat info</button><button type="button" data-action="view.update" onClick={()=>choose('settings')}><Settings/>Settings<AttentionBadge state={state} settings/></button></>}
   <button type="button" data-action="view.update" onClick={()=>choose('appearance')}><Palette/>Customize appearance</button>
   <button type="button" data-action="view.update" onClick={()=>choose('agent')}><ScanEye/>What the agent sees</button>
  </div></>}
 </div>;
}
