import {CanvasControl} from './canvas-controls';
import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {FileText,Globe,Settings,X,ExternalLink,RotateCw,ArrowRight,Library,PanelsTopLeft} from 'lucide-react';
import {filterList} from './list-filter';
import {useCanvasReading} from './canvas-overview-state';
const inChat=(state,row)=>row.sessionId===state.selectedSessionId&&row.workspaceId===state.selectedWorkspaceId;
export function chatArtifacts(state){return (state.canvasArtifacts||[]).filter(r=>inChat(state,r))}
const artifactDescription=row=>row.kind==='browser'?'Saved address':row.kind==='settings'?'Live settings':row.kind==='context-inspector'?'Live request inspector':row.app?'Interactive surface':'Saved snapshot';
// Native close removes modal inertness and restores its previous focus first.
// A React state update alone does neither until the passive effect has run.
export function closeCanvasDialog(dialog,focus){
 if(dialog?.open)dialog.close();
 requestAnimationFrame(focus);
}
export function CanvasTabs({state,act,dispatch=act,overview=false,onOverview}){
 const rows=chatArtifacts(state).filter(r=>r.tabOpen),strip=useRef(null),tabs=useRef(null),dialog=useRef(null),trigger=useRef(null);
 const [scrolled,setScrolled]=useState(false),[open,setOpen]=useState(false),[query,setQuery]=useState(''),[error,setError]=useState(''),[pending,setPending]=useState(false);
 const [focusId,setFocusId]=useState(null),selectedId=overview?'overview':state.canvas?.id;
 const tabFocus=focusId==='overview'||rows.some(row=>row.id===focusId)?focusId:selectedId;
 useEffect(()=>{const focused=tabs.current?.querySelector('[role=tab]:focus');setFocusId(focused?focused.dataset.artifactId||'overview':selectedId)},[selectedId,state.selectedSessionId]);
 const focusIntent=useRef(0),dialogGeneration=useRef(0);
 useEffect(()=>{
  const advance=()=>{focusIntent.current++};
  document.addEventListener('keydown',advance,true);document.addEventListener('pointerdown',advance,true);
  return()=>{advance();document.removeEventListener('keydown',advance,true);document.removeEventListener('pointerdown',advance,true)};
 },[]);
 const current=useRef(state);current.current=state;
 useCanvasReading(strip,state.selectedSessionId,'tabs',!!state.canvas?.open);
 const reveal=id=>{const node=strip.current,tab=[...(node?.querySelectorAll('[role=tab]')||[])].find(el=>el.dataset.artifactId===id);if(!tab)return;const outer=node.getBoundingClientRect(),inner=(tab.closest?.('.a-canvas-document-entry')||tab).getBoundingClientRect();if(inner.left<outer.left)node.scrollLeft+=inner.left-outer.left;else if(inner.right>outer.right)node.scrollLeft+=Math.min(inner.left-outer.left,inner.right-outer.right)};
 useLayoutEffect(()=>{if(state.canvas?.open&&!overview)reveal(state.canvas?.id)},[state.canvas?.id]);
 useLayoutEffect(()=>{if(strip.current)setScrolled(strip.current.scrollLeft>4)},[state.selectedSessionId,state.canvas?.open]);
 useEffect(()=>{if(open&&!dialog.current?.open)dialog.current?.showModal();else if(!open&&dialog.current?.open)dialog.current.close()},[open]);
 useEffect(()=>{focusIntent.current++;dialogGeneration.current++;setOpen(false);setQuery('');setError('')},[state.selectedSessionId,state.selectedWorkspaceId]);
 const close=()=>{const intent=++focusIntent.current;dialogGeneration.current++;setOpen(false);closeCanvasDialog(dialog.current,()=>{if(focusIntent.current===intent)trigger.current?.focus({preventScroll:true})})};
 async function choose(row){
  if(pending)return;setPending(true);setError('');const sessionId=state.selectedSessionId,fromDialog=!!dialog.current?.open,intent=focusIntent.current,generation=dialogGeneration.current;
  try{
   const result=await dispatch('canvas.select',{id:row.id,...(row.id===state.canvas?.id&&state.canvas.selectedVersion!=null?{version:state.canvas.selectedVersion}:{})});
   if(current.current.selectedSessionId!==sessionId)return;
   if(!result?.accepted){setError(String(result?.error?.message||result?.error||'The document was not changed. Your current view was kept.'));return}
   // Direct tabs already have focus. A delayed acceptance must not overtake
   // Home/arrow navigation; only the still-current modal interaction restores it.
   if(!fromDialog||dialogGeneration.current!==generation||!dialog.current?.open)return;
   setOpen(false);closeCanvasDialog(dialog.current,()=>{
    const latest=current.current;
    // The receipt is accepted; its React state commit may follow this frame.
    // The requested tab already exists. Do not gate focus on that later commit.
    if(focusIntent.current!==intent||latest.selectedSessionId!==sessionId||dialog.current?.open||!latest.canvas?.open)return;
    reveal(row.id);[...(strip.current?.querySelectorAll('[role=tab]')||[])].find(el=>el.dataset.artifactId===row.id)?.focus({preventScroll:true});
   });
  }catch(err){setError(err.message)}finally{setPending(false)}
 }
 async function remove(row){
  if(pending)return;setPending(true);setError('');
  const sessionId=state.selectedSessionId,workspaceId=state.selectedWorkspaceId,intent=focusIntent.current,generation=dialogGeneration.current,fromDialog=!!dialog.current?.open;
  // The receipt may change selection, but it does not own a later interaction.
  // Fence both errors and deferred focus, including dismiss/reopen of the list.
  const ownsInteraction=()=>focusIntent.current===intent&&dialogGeneration.current===generation&&
   current.current.selectedSessionId===sessionId&&current.current.selectedWorkspaceId===workspaceId&&
   !!current.current.canvas?.open&&!!dialog.current?.open===fromDialog;
  try{
   const result=await dispatch('canvas.tabClose',{id:row.id});
   if(!ownsInteraction())return;
   if(result?.accepted)requestAnimationFrame(()=>{
    if(!ownsInteraction())return;
    if(fromDialog)dialog.current?.querySelector('input,button')?.focus({preventScroll:true});
    else tabs.current?.querySelector('[aria-selected=true]')?.focus({preventScroll:true});
   });
   else setError(String(result?.error?.message||result?.error||'The tab was not closed. Your current view was kept.'));
  }catch(err){if(ownsInteraction())setError(err.message)}finally{setPending(false)}
 }
 const filtered=filterList(rows,query,row=>[row.title,row.kind]);
 const keyboard=event=>{
  const target=event.target.closest('[role=tab]');if(!target)return;
  const list=[...tabs.current.querySelectorAll('[role=tab]')],index=list.indexOf(target);
  if(event.key==='Delete'&&target.dataset.artifactId){event.preventDefault();const row=rows.find(row=>row.id===target.dataset.artifactId);if(row)remove(row);return}
  const next=event.key==='ArrowRight'?(index+1)%list.length:event.key==='ArrowLeft'?(index+list.length-1)%list.length:event.key==='Home'?0:event.key==='End'?list.length-1:null;
  if(next===null)return;event.preventDefault();setFocusId(list[next].dataset.artifactId||'overview');list[next].focus({preventScroll:true});if(list[next].dataset.artifactId)reveal(list[next].dataset.artifactId);
 };
 return <><div ref={tabs} className="a-canvas-navigation" role="tablist" aria-label="Canvas views" data-scrolled={scrolled} onKeyDown={keyboard}>
  <div className="a-canvas-overview-slot"><button type="button" className="a-chat-overview-trigger" role="tab" aria-label="Chat overview" aria-selected={overview} tabIndex={tabFocus==='overview'?0:-1} onClick={()=>{setError('');onOverview?.()}}><Library aria-hidden="true"/><span>Overview</span></button></div>
  <div ref={strip} className="a-canvas-document-tabs" onScroll={event=>setScrolled(event.currentTarget.scrollLeft>4)}>{rows.map(row=><div key={row.id} className="a-canvas-document-entry" role="presentation">
   <button type="button" role="tab" className="a-canvas-document-tab" aria-selected={!overview&&row.id===state.canvas?.id} tabIndex={tabFocus===row.id?0:-1} data-artifact-id={row.id} title={row.title} data-action="canvas.select" onClick={()=>choose(row)} onMouseDown={event=>{if(event.button===1)event.preventDefault()}} onAuxClick={event=>{if(event.button===1){event.preventDefault();remove(row)}}}>{row.kind==='browser'?<Globe/>:row.kind==='settings'?<Settings/>:<FileText/>}<span>{row.title}</span></button>
   <button type="button" className="a-icon a-canvas-tab-close" aria-label={`Close tab ${row.title}`} title={`Close tab ${row.title} — keeps saved output`} data-action="canvas.tabClose" aria-disabled={pending} onClick={()=>remove(row)}><X aria-hidden="true"/></button>
  </div>)}</div>
 </div><button ref={trigger} type="button" className="a-open-documents a-soft" aria-label={`Open documents, ${rows.length} open`} aria-haspopup="dialog" aria-expanded={open} onClick={()=>{dialogGeneration.current++;setOpen(true)}}><PanelsTopLeft aria-hidden="true"/><span>Open documents</span><small>{rows.length}</small></button>
 <dialog ref={dialog} className="a-open-documents-dialog" aria-label="Open documents" onClick={event=>{if(event.target===event.currentTarget){const r=event.currentTarget.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)close()}}} onCancel={event=>{event.preventDefault();close()}} onClose={()=>setOpen(false)}>
  <header><h2>Open documents</h2><button type="button" className="a-icon" aria-label="Close open documents" onClick={close}><X/></button></header>
  <input type="search" aria-label="Find an open document" placeholder="Find an open document…" value={query} onChange={event=>setQuery(event.target.value)}/>
  {error&&<p role="alert">{error}</p>}
  <ul>{filtered.map(row=><li key={row.id} aria-current={!overview&&row.id===state.canvas?.id?'true':undefined}><button type="button" onClick={()=>choose(row)} disabled={pending}><FileText/><span><strong>{row.title}</strong><small>{row.kind}{!overview&&row.id===state.canvas?.id?' · Selected':''}</small></span></button><button type="button" className="a-icon" disabled={pending} aria-label={`Close tab ${row.title}`} onClick={()=>remove(row)}><X/></button></li>)}</ul>
  {!filtered.length&&<p>{rows.length?'No matching open documents.':'No documents are open. Saved outputs remain in Overview.'}</p>}
  <p className="a-caption">{rows.length} open · {chatArtifacts(state).length} saved outputs</p>
 </dialog>{error&&!open&&<p role="alert" className="a-canvas-navigation-error">{error}</p>}</>;
}
export function SavedArtifacts({state,act,compact=false,onFilter}){
 const draft=state.view?.canvasDraft||{},rows=chatArtifacts(state),filtered=filterList(rows,draft.filter||'',r=>[r.title,r.kind,r.path||'',r.url||'']);
 return <section className="a-canvas-library" aria-label="Saved canvas artifacts">{!compact&&<h3>Saved in this chat <small>{rows.length}</small></h3>}{(!compact||rows.length>5||draft.filter)&&<input type="search" aria-label="Filter saved artifacts" placeholder="Find artifacts · * ? patterns" value={draft.filter||''} onChange={e=>onFilter?onFilter(e.target.value):act('view.update',{patch:{canvasDraft:{...draft,filter:e.target.value}}})}/>} {filtered.slice().reverse().map(r=><button key={r.id} type="button" className="a-saved-artifact" data-action="canvas.select" onClick={()=>act('canvas.select',{id:r.id})}>{r.kind==='browser'?<Globe/>:r.kind==='settings'?<Settings/>:<FileText/>}<span><strong>{r.title}</strong><small>{r.kind} · {artifactDescription(r)}</small></span><ArrowRight/></button>)}{!filtered.length&&<p>{rows.length?'No matching artifacts.':'Files and visuals you open will stay here, even after closing their tabs.'}</p>}</section>;
}
export function BrowserAddress({state,act}){
 const draft=state.view?.canvasDraft||{},[url,setUrl]=useState(draft.url||''),pending=useRef(null);
 useEffect(()=>{if(pending.current===null||draft.url===pending.current){setUrl(draft.url||'');pending.current=null}},[draft.url]);
 return <form className="a-canvas-address" onSubmit={e=>{e.preventDefault();act('canvas.show',{kind:'browser',url})}}><label htmlFor="canvas-browser-url">App or website address</label><div><input id="canvas-browser-url" type="url" required placeholder="http://localhost:3000" value={url} data-action="view.update" onChange={e=>{setUrl(e.target.value);pending.current=e.target.value;act('view.update',{patch:{canvasDraft:{...draft,url:e.target.value}}})}}/><button className="a-primary" type="submit" data-action="canvas.show" disabled={!url.trim()}><ArrowRight/>Open</button></div></form>;
}
export function browserPreviewPolicy(url,hostUrl){
 const target=new URL(url),host=new URL(hostUrl);
 const loopback=name=>name==='localhost'||name.endsWith('.localhost')||name==='[::1]'||/^127\.\d+\.\d+\.\d+$/.test(name);
 const blocked=host.protocol==='https:'&&target.protocol==='http:'&&!loopback(target.hostname);
 const localWarning=loopback(target.hostname)&&!loopback(host.hostname);
 return {blocked,localWarning,message:blocked?'This HTTP page cannot be embedded inside the HTTPS app. Open it in your browser, or serve the page over HTTPS.':localWarning?'This address points to the device viewing the app. For a service running on the Amplifier host, use that host’s LAN or Tailnet address.':'Some sites block embedding or require a separate browser tab. The app cannot verify the contents of this external frame.'};
}
export function BrowserPreview({canvas,act}){
 const policy=browserPreviewPolicy(canvas.url,typeof location==='undefined'?'http://localhost':location.href),action=useRef(act);action.current=act;
 useEffect(()=>{
  const report=(status,message)=>action.current('canvas.report',{id:canvas.id,part:'preview',status,message});
  if(policy.blocked){report('error',policy.message);return}
  report('pending','Opening external preview; page contents cannot be verified.');
  const timer=setTimeout(()=>report('unverified','Preview could not be verified. If it is blank, open it in your browser or check the address and site embedding policy.'),12000);
  return()=>clearTimeout(timer);
 },[canvas.id,canvas.view?.reload,policy.blocked,policy.message]);
 return <div className="a-browser-preview"><CanvasControl><div className="a-canvas-toolbar"><Globe/><span className="a-browser-url" title={canvas.url}>{canvas.url}</span><button type="button" className="a-icon" aria-label="Reload browser preview" data-action="canvas.view" onClick={()=>act('canvas.view',{id:canvas.id,patch:{reload:Date.now()}})}><RotateCw/></button></div></CanvasControl>
 {policy.blocked?<div className="a-browser-blocked" role="alert"><Globe/><h3>Open this page in your browser</h3><p>{policy.message}</p><a className="a-soft" data-action="canvas.openExternal" href={canvas.url} target="_blank" rel="noopener noreferrer">Open in browser <ExternalLink size={16}/></a></div>:<iframe key={`${canvas.id}-${canvas.view?.reload||0}`} title={canvas.title||'App preview'} className="a-canvas-html" sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" src={canvas.url} onLoad={()=>act('canvas.report',{id:canvas.id,part:'preview',status:'unverified',message:'Frame navigation finished. Browser security prevents confirming whether this external page loaded or permits embedding.'})}/>}
 <div className="a-browser-help"><a data-action="canvas.openExternal" href={canvas.url} target="_blank" rel="noopener noreferrer">Open in browser <ExternalLink size={13}/></a><button className="a-link" type="button" data-action="canvas.view" aria-expanded={!!canvas.view?.help} onClick={()=>act('canvas.view',{id:canvas.id,patch:{help:!canvas.view?.help}})}>Preview help</button>{(canvas.view?.help||policy.localWarning)&&<p>{policy.message} The service must stay running and be reachable from this device. Authentication, frame policies, redirects, and sandbox restrictions can require opening it separately.</p>}</div></div>;
}
