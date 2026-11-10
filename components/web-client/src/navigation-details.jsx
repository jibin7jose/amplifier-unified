import React,{useContext,useEffect,useId,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {AlertCircle,CircleHelp,PauseCircle,Clock3,Check,Copy,FolderOpen,LoaderCircle,MessageCircle,MoreHorizontal} from 'lucide-react';
import {relativeActivity,attentionTone} from './navigation-presentation';
import './navigation-details.css';
import {NavigationOpen,useNarrowScreen,useModalFocus} from './responsive-navigation';

export function useActivityClock(){
 const [now,setNow]=useState(()=>Date.now()/1000);
 useEffect(()=>{if(typeof window==='undefined')return;const timer=setInterval(()=>setNow(Date.now()/1000),30000);return()=>clearInterval(timer)},[]);
 return now;
}
export function NavigationStatus({activity}){
 const tone=attentionTone(activity);
 return <span className="a-navigation-status" data-attention={tone} data-kind={activity.kind} aria-label={activity.label} title={activity.label}>{activity.kind==='working'?<LoaderCircle className="a-progress-spinner"/>:activity.kind==='waiting'?<Clock3/>:activity.kind==='paused'?<PauseCircle/>:activity.kind==='attention'?(tone==='decision'?<CircleHelp/>:<AlertCircle/>):activity.kind==='unread'?<span className="a-navigation-unread"/>:<MessageCircle/>}</span>;
}
export function ActivityTime({at,now}){
 const age=relativeActivity(at,now);
 return <span className="a-navigation-age" data-view-source="activity-time" title={age.long} aria-label={'Last activity: '+age.long}>{age.short}</span>;
}
export function CopyDetail({label,value}){
 const [status,setStatus]=useState('');
 useEffect(()=>setStatus(''),[value]);
 return <div className="a-navigation-copy"><div><span>{label}</span><button type="button" className="a-icon" aria-label={'Copy '+label.toLowerCase()} onClick={async()=>{try{await navigator.clipboard.writeText(value);setStatus('Copied')}catch{setStatus('Select the text and copy it manually.')}}}>{status==='Copied'?<Check/>:<Copy/>}</button></div><code>{value}</code>{status&&<small role="status">{status}</small>}</div>;
}
export function WorkspaceDetails({row,now,actions,countsPending=false}){
 const counts=row.activityCounts||{};
 return <><div className="a-navigation-detail-heading"><FolderOpen/><span>Workspace details</span></div><h3>{row.customName||row.name}</h3>{countsPending?<p role="status">Updating workspace activity…</p>:<dl><dt>Chats</dt><dd>{row.chatCount??0}</dd><dt>Activity</dt><dd>{counts.working||0} working · {counts.attention||0} need attention{counts.waiting>0&&` · ${counts.waiting} waiting`}{counts.paused>0&&` · ${counts.paused} paused`}</dd><dt>Last activity</dt><dd>{relativeActivity(row.recentActivityAt,now).long}</dd></dl>}<CopyDetail label="Full workspace path" value={row.path}/>{(row.nameQualifier||!row.customName&&row.pathLabel?.includes('/'))&&<p className="a-caption">Folder context distinguishes workspaces with the same name.</p>}{actions&&<div className="a-navigation-actions">{actions}</div>}</>;
}

// Only navigation identity dismisses a peek; activity/title updates keep it usable.
export function navigationDetailsContext(state){
 const view=state.view||{},surface=view.workSurface||'chat';
 return JSON.stringify([state.selectedSessionId??null,state.selectedWorkspaceId??null,surface,
  surface==='workspace'?view.workWorkspaceId??null:null]);
}

// Portals stay inside the rail's outer slot, outside its scroll/clip container.
// Peeking is local presentation only: it never selects a chat or changes recency.
export function NavigationRow({className='',label,children,details,expanded=false,contextKey,...props}){
 const narrow=useNarrowScreen(),navigationOpen=useContext(NavigationOpen);
 const row=useRef(null),panel=useRef(null),trigger=useRef(null),opening=useRef(null),closing=useRef(null),focusRequested=useRef(false),id=useId();
 const [open,setOpen]=useState(false),[locked,setLocked]=useState(false),[position,setPosition]=useState({left:0,top:0});
 const clear=()=>{clearTimeout(opening.current);clearTimeout(closing.current)};
 const close=(focus=false)=>{clear();focusRequested.current=false;setOpen(false);setLocked(false);if(focus)trigger.current?.focus({preventScroll:true})};
 useModalFocus(panel,navigationOpen&&narrow&&open,()=>close(true));
 const show=(lock=false)=>{
  clear();
  if(row.current?.closest('[data-reordering="true"]'))return;
  if(!lock&&document.querySelector('.a-navigation-flyout[data-locked="true"]'))return;
  document.dispatchEvent(new CustomEvent('amplifier-navigation-details',{detail:id}));
  focusRequested.current=lock;setLocked(lock);setOpen(true);
 };
 useEffect(()=>()=>clear(),[]);
 useEffect(()=>{if(!navigationOpen)close()},[navigationOpen]);
 useLayoutEffect(()=>{close()},[contextKey]);
 useEffect(()=>{if(expanded)show(true)},[expanded]);
 useEffect(()=>{
  if(!open)return;
  const dismiss=e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(locked)}};
  const outside=e=>{if(!row.current?.contains(e.target)&&!panel.current?.contains(e.target))close()};
  const other=e=>{if(e.detail!==id)close()};
  document.addEventListener('keydown',dismiss,true);document.addEventListener('pointerdown',outside);document.addEventListener('amplifier-navigation-details',other);
  return()=>{document.removeEventListener('keydown',dismiss,true);document.removeEventListener('pointerdown',outside);document.removeEventListener('amplifier-navigation-details',other)};
 },[open,locked,id]);
 useEffect(()=>{if(open&&locked&&focusRequested.current){focusRequested.current=false;panel.current?.querySelector('button')?.focus({preventScroll:true})}},[open,locked]);
 useLayoutEffect(()=>{
  if(!open||!panel.current||!row.current)return;
  const place=()=>{
   const anchor=row.current.closest('.a-nav-slot')?row.current:trigger.current||row.current;
   const r=anchor.getBoundingClientRect(),p=panel.current.getBoundingClientRect(),margin=10;
   const right=r.right+8,left=right+p.width<=innerWidth-margin?right:Math.max(margin,r.left-p.width-8);
   const next={left:Math.min(left,innerWidth-p.width-margin),top:Math.max(margin,Math.min(r.top,innerHeight-p.height-margin))};
   setPosition(previous=>previous.left===next.left&&previous.top===next.top?previous:next);
  };
  place();const observer=new ResizeObserver(place);observer.observe(panel.current);observer.observe(row.current);
  window.addEventListener('resize',place);document.addEventListener('scroll',place,true);
  return()=>{observer.disconnect();window.removeEventListener('resize',place);document.removeEventListener('scroll',place,true)};
 },[open,details]);
 const leave=e=>{
  if(e.relatedTarget instanceof Node&&(row.current?.contains(e.relatedTarget)||panel.current?.contains(e.relatedTarget)))return;
  clearTimeout(opening.current);if(!locked)closing.current=setTimeout(()=>close(),250);
 };
 const target=row.current?.closest('.a-nav-slot')||(typeof document!=='undefined'?document.getElementById('amp-one'):null);
 return <div {...props} ref={row} className={className+(open?' is-peeking':'')} onPointerEnter={e=>{clearTimeout(closing.current);if(!narrow&&e.pointerType!=='touch'&&!open)opening.current=setTimeout(()=>show(),550)}} onPointerLeave={leave} onClick={e=>{if(e.target.closest('[data-navigation-select]'))close()}}>
  {children}
  <button ref={trigger} type="button" className="a-icon a-navigation-more" aria-label={'Details and actions for '+label} aria-expanded={open} aria-controls={open?id:undefined} onClick={()=>open&&locked?close():show(true)}><MoreHorizontal/></button>
  {open&&target&&createPortal(<>{narrow&&<div className="a-details-scrim" onClick={()=>close(true)}/>}<section ref={panel} id={id} role="dialog" aria-modal={narrow||undefined} aria-label={'Details for '+label} className="a-navigation-flyout" data-locked={locked} style={position} onPointerEnter={()=>clearTimeout(closing.current)} onPointerLeave={leave} onFocus={()=>{clearTimeout(closing.current);if(!locked)setLocked(true)}}>
   {details({close})}
  </section></>,target)}
 </div>;
}
