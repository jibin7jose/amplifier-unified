import React,{useContext,useLayoutEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,SlidersHorizontal,X} from 'lucide-react';
import {WorkNavigationContext} from './work-navigation';
import {focusPosition,stepFocus,startingWorkView,workViews,rememberHomeScroll} from './adaptive-work.js';
import {workGroups} from './work-status';
import './adaptive-work.css';

function readLocal(key,fallback){try{return JSON.parse(sessionStorage.getItem(key))??fallback}catch{return fallback}}
function writeLocal(key,value){try{sessionStorage.setItem(key,JSON.stringify(value))}catch{}}
function readCursor(key){
 const value=readLocal(key,null);
 return value&&typeof value.key==='string'&&Number.isSafeInteger(value.page)&&value.page>=0
  &&Number.isSafeInteger(value.index)&&value.index>=-1&&(value.id===null||typeof value.id==='string')?value:null;
}
export function useLandingLayout(identity,presentation){
 const key='amplifier.work-layout.v1:'+identity;
 const [saved,setSaved]=useState(()=>readLocal(key,null));
 const mode=workViews.includes(saved)?saved:startingWorkView(presentation);
 return [mode,value=>{if(workViews.includes(value)){setSaved(value);writeLocal(key,value)}}];
}

export function WorkViewSwitcher({value,onChange}){
 return <div className="a-work-layout"><span className="a-work-layout-label">Layout</span><div className="a-work-view-switch" role="group" aria-label="Work view">{workViews.map(key=><button key={key} type="button" aria-pressed={value===key} onClick={()=>onChange(key)}>{key[0].toUpperCase()+key.slice(1)}</button>)}</div></div>;
}

export function LandingToolbar({mode,onMode,presentation={},actions,onReset,home=false}){
 const nav=useContext(WorkNavigationContext),trigger=useRef(null),[open,setOpen]=useState(false),id=React.useId();
 const level=presentation.interfaceDetail||presentation.executionDetail||'standard';
 const close=()=>{setOpen(false);trigger.current?.focus()};
 const label=home?'Home appearance':'View appearance';
 return <>
  <div className="a-home-toolbar"><WorkViewSwitcher value={mode} onChange={onMode}/><div className="a-home-tools">{actions}<button ref={trigger} type="button" className="a-home-settings a-soft" aria-label={label} aria-expanded={open} aria-controls={id} onClick={()=>setOpen(!open)}><SlidersHorizontal/><span>Display</span></button></div></div>
  {open&&<section id={id} className="a-home-preferences" aria-label={label} onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();close()}}}>
   <div className="a-home-preferences-heading"><strong>Display detail</strong><button type="button" className="a-icon" aria-label={'Close '+label.toLowerCase()} onClick={close}><X/></button></div>
   <div className="a-home-levels" role="group" aria-label="Appearance detail">{[['minimal','Focused'],['standard','Balanced'],['detailed','Everything']].map(([key,name])=><button type="button" key={key} aria-pressed={level===key} onClick={()=>nav.setPresentation({interfaceDetail:key,executionDetail:key})}>{name}</button>)}</div>
   <label>Default layout<select aria-label="Starting work view" value={presentation.workStartView||'appearance'} onChange={event=>nav.setPresentation({workStartView:event.target.value})}><option value="appearance">Follow display detail</option>{workViews.map(key=><option key={key} value={key}>{key[0].toUpperCase()+key.slice(1)}</option>)}</select></label>
   <button className="a-link" type="button" onClick={()=>{(onReset||onMode)(startingWorkView(presentation));close()}}>Go to starting view<ArrowRight/></button>
   <p className="a-caption">Layout arranges this page. Display detail controls how much information the app shows.</p>
  </section>}
 </>;
}

export function WorkLandingFrame({paneRef,mode,presentation={},title,description,toolbar,children,identity='home',readingKey,ready=true}){
 const own=useRef(null),pane=paneRef||own;
 useLayoutEffect(()=>{if(identity!=='home'&&ready&&pane.current)return rememberHomeScroll(pane.current,readingKey||mode,'amplifier.work-reading.v1:'+identity)},[identity,ready,readingKey,mode]);
 return <div ref={pane} className={'a-adaptive-home'+(identity!=='home'?' a-work-browser':'')} data-view={mode} data-level={presentation.interfaceDetail||presentation.executionDetail||'standard'} data-landing={identity.split(':')[0]} data-part="work-browser"><div className="a-home-content">{toolbar}<div className="a-home-intro"><h1>{title}</h1>{description&&<p>{description}</p>}</div>{children}</div></div>;
}

export function WorkStatusFilters({value='all',counts={},onChange,overview='Overview'}){
 return <div className="a-home-filter-bar"><div className="a-home-filters" role="group" aria-label="Filter work"><button type="button" aria-pressed={value==='all'} onClick={()=>onChange('all')}>{overview}</button>{Object.entries(workGroups).map(([key,group])=><button key={key} type="button" aria-pressed={value===key} title={`${counts[key]||0} ${group.meaning} in this scope`} onClick={()=>onChange(key)}>{group.title}{key!=='idle'&&<span>{counts[key]||0}</span>}</button>)}</div>{value!=='all'&&<button type="button" className="a-active-work-filter" aria-label="Clear status filter" onClick={()=>onChange('all')}>Showing: {workGroups[value]?.title||value}<X size={14}/></button>}</div>;
}

export function FocusNavigation({position,total,onPrevious,onNext,previousDisabled,nextLabel='Next',label='Browse Focus items',name}){
 return <nav className="a-focus-navigation" aria-label={label}><button type="button" data-focus-move="previous" aria-label="Previous Focus item" disabled={previousDisabled} onClick={onPrevious}><ArrowLeft size={16}/>Previous</button><span role="status" aria-live="polite" aria-atomic="true">{position} of {total}{name&&<span className="a-sr-only"> · {name}</span>}</span><button type="button" data-focus-move="next" aria-label={nextLabel==='Next'?'Next Focus item':'Start Focus pass again'} title="Browse without marking read or changing the work" onClick={onNext}>{nextLabel}<ArrowRight size={16}/></button></nav>;
}

// Presentation of one already bounded catalog page. No hidden-page loading,
// classification, acknowledgements or workspace/session mutations live here.
export function LandingCollection({page,layout,identity,queryKey,onPage,children,wrap,className=''}){
 const storageKey='amplifier.work-pass.v1:'+identity;
 const [cursor,setCursor]=useState(()=>readCursor(storageKey)),pending=useRef(null),restore=useRef(true),root=useRef(null);
 const items=page.items.map(row=>({...row,id:row.id||row.workspaceId||row.path}));
 const normalized={...page,items},key=JSON.stringify(Array.isArray(queryKey)?queryKey:Object.keys(queryKey).sort().map(name=>[name,queryKey[name]]));
 const focus=focusPosition(normalized,cursor?.key===key?cursor:null);
 const save=value=>{setCursor(value);writeLocal(storageKey,value)};
 useLayoutEffect(()=>{
  if(cursor&&cursor.key!==key){pending.current=null;save(null)}
  if(page.pending)return;
  // The catalog's page hint includes the selected chat. Re-entering Focus may
  // therefore need one ordinary bounded page request to restore this visit.
  if(restore.current){
   restore.current=false;
   const savedPage=Math.max(0,Math.min(cursor?.page||0,page.pages-1));
   if(layout==='focus'&&cursor?.key===key&&savedPage!==page.index){pending.current={page:savedPage,direction:0};onPage(savedPage);return}
  }
  if(layout!=='focus'||!focus)return;
  if(pending.current&&Math.min(pending.current.page,page.pages-1)!==page.index)return;
  const next={key,...focus};if(JSON.stringify(next)!==JSON.stringify(cursor))save(next);
  if(pending.current){if(pending.current.direction)root.current?.querySelector(`[data-focus-move="${pending.current.direction>0?'next':'previous'}"]`)?.focus();pending.current=null}
 },[layout,key,page.pending,page.pages,focus?.id,focus?.page,focus?.index]);
 const move=(direction,restart=false)=>{
  const next=restart?{page:0,index:0,id:page.index===0?items[0]?.id:null}:stepFocus(normalized,focus,direction);
  if(!next)return;save({key,...next});
  if(next.page!==page.index){pending.current={page:next.page,direction};onPage(next.page)}
 };
 const last=focus&&page.start+focus.index+1===page.total;
 const visible=layout==='focus'?(focus?[page.items[focus.index]]:[]):page.items;
 const rendered=visible.map(row=><React.Fragment key={row.id||row.workspaceId||row.path}>{children(row)}</React.Fragment>);
 return <div ref={root} className={'a-landing-collection '+className} data-layout={layout||'list'} aria-busy={!!page.pending}><div className="a-landing-items">{wrap?wrap(rendered,visible):rendered}</div>{layout==='focus'&&focus&&page.total>1&&<FocusNavigation position={page.start+focus.index+1} total={page.total} previousDisabled={!page.start&&!focus.index} onPrevious={()=>move(-1)} onNext={()=>move(1,last)} nextLabel={last?'Start again':'Next'} name={page.items[focus.index].title||page.items[focus.index].name}/>}</div>;
}
