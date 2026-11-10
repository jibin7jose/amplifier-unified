import React,{useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {X,RefreshCw,ArrowUpRight,Copy} from 'lucide-react';
import {request} from './api';
import {DetailText,readDetail} from './conversation-detail';
import {usePanelLayout,PaneResizer} from './panel-layout';
import {useModalFocus} from './responsive-navigation';
import {messageTime} from './message-time';
import {Markdown} from './markdown';
import {prependPeerPage,refreshPeerPages} from './peer-preview-pages.js';
import {deferPeerFocus,peerFocusScope,peerReturnTarget} from './peer-preview-focus.js';
import './peer-preview.css';

export function usePeerPreview(state,dispatch){
 const [active,setActive]=useState(null),intent=useRef(0),current=useRef(state);current.current=state;
 const activeRef=useRef(active),dismissal=useRef(null);activeRef.current=active;
 useEffect(()=>{intent.current++;dismissal.current=null;setActive(null)},[state?.selectedSessionId,state?.canvas?.id,state?.canvas?.selectedVersion,state?.view?.workSurface]);
 const close=useCallback((expected,restoreFocus=false)=>{
  if(activeRef.current!==expected)return;
  const token=++intent.current;
  dismissal.current=restoreFocus?{token,origin:expected.returnTarget,scope:peerFocusScope(current.current)}:null;
  setActive(null);
 },[]);
 // Child modal layout cleanup releases inertness before this post-dismissal
 // frame. Implicit teardown and successful Open chat never request a fallback.
 useLayoutEffect(()=>{
  if(active||!dismissal.current)return;
  const saved=dismissal.current;dismissal.current=null;
  return deferPeerFocus(saved.origin,()=>intent.current===saved.token&&!current.current?.navigationPending
   &&current.current?.selectedSessionId===saved.origin?.sessionId&&peerFocusScope(current.current)===saved.scope);
 },[active]);
 const open=useCallback(async({peer,sourceSessionId,messageId})=>{
  const token=++intent.current,trigger=document.activeElement,returnTarget=peerReturnTarget(trigger,sourceSessionId,messageId);
  if(!peer?.available||!peer.proof)throw Error('Source chat unavailable.');
  const result=await dispatch('peer.preview',{proof:peer.proof,sourceSessionId});
  if(result?.accepted!==true||token!==intent.current||current.current?.selectedSessionId!==sourceSessionId)return;
  setActive(old=>old?.source.sourceId===peer.sourceId?{...old,proof:peer.proof,sourceSessionId,messageId,trigger,returnTarget}:{source:result.result,proof:peer.proof,sourceSessionId,messageId,trigger,returnTarget});
 },[dispatch]);
 return {active:active?.sourceSessionId===state?.selectedSessionId?active:null,open,close};
}
function PreviewMessage({message,proof,onUnavailable}){
 const [error,setError]=useState(''),[copied,setCopied]=useState(false),[copying,setCopying]=useState(false),[loaded,setLoaded]=useState(null),[loading,setLoading]=useState(false);
 const contentKey=message.textDetail?.digest||message.text,current=useRef(contentKey);current.current=contentKey;
 const full=loaded?.key===contentKey?loaded.value:null;
 const date=message.timestampKnown!==false&&Number.isFinite(message.createdAt)?messageTime(message.createdAt):null;
 async function text(){await request('/api/peer/preview?'+new URLSearchParams({proof,metadata:'true'}));return message.textDetail?await readDetail(message.textDetail):message.text||''}
 function failed(e){setError(e.message);if([403,404].includes(e.status))onUnavailable(e)}
 async function copy(){setCopying(true);setError('');try{await navigator.clipboard.writeText(await text());setCopied(true)}catch(e){failed(e)}finally{setCopying(false)}}
 async function load(){const key=contentKey;setLoading(true);setError('');try{const value=await text();if(current.current===key)setLoaded({key,value})}catch(e){failed(e)}finally{setLoading(false)}}
 return <article data-preview-message-id={message.id} className="a-peer-preview-message"><header>{message.attribution?.caption||(message.role==='assistant'?'Amplifier':message.inputOrigin==='peer'?'Peer message':'User')} {date&&<time dateTime={date.iso} title={date.full}>{date.text}</time>}</header>
  <Markdown text={full??message.text??''} userContent/>
  {message.textDetail&&full===null&&<button type="button" className="a-link" disabled={loading} onClick={load}>{loading?'Loading full text…':'Show full text'}</button>}
  <div className="a-message-actions"><button className="a-icon" type="button" aria-label="Copy preview original as Markdown" disabled={copying} onClick={copy}><Copy/></button>{copied&&<span role="status">Copied Markdown</span>}</div>{error&&<p role="alert">{error}</p>}
 </article>;
}
export function PeerPreviewCanvas({active,onClose,state,dispatch}){
 const panel=useRef(null),scroll=useRef(null),read=useRef(null),version=useRef(0),selection=useRef(null),layout=usePanelLayout(state,dispatch);
 const [snapshot,setSnapshot]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[unavailable,setUnavailable]=useState(false),[opening,setOpening]=useState(false),[unknown,setUnknown]=useState(false);
 const [metadata,setMetadata]=useState(active.source);
 const source=active.source,identity=source.sourceId,latest=useRef(active);latest.current=active;
 const close=useCallback(()=>{read.current?.abort();onClose(active,true)},[onClose,active]);
 useModalFocus(panel,layout.overlay,close);
 useEffect(()=>{panel.current?.querySelector('[aria-label="Close source preview"]')?.focus({preventScroll:true});return()=>{read.current?.abort();version.current++}},[]);
 function anchor(){const pane=scroll.current;if(!pane)return null;const top=pane.getBoundingClientRect().top,node=[...pane.querySelectorAll('[data-preview-message-id]')].find(n=>n.getBoundingClientRect().bottom>top);return node?{id:node.dataset.previewMessageId,offset:node.getBoundingClientRect().top-top}:null}
 useLayoutEffect(()=>{const saved=selection.current,pane=scroll.current;if(saved&&pane){const node=[...pane.querySelectorAll('[data-preview-message-id]')].find(n=>n.dataset.previewMessageId===saved.id);if(node)pane.scrollTop+=node.getBoundingClientRect().top-pane.getBoundingClientRect().top-saved.offset;selection.current=null}},[snapshot]);
 async function load(earlier=false){
  read.current?.abort();const controller=new AbortController(),token=++version.current;read.current=controller;setBusy(true);setError('');
  try{const fetchPage=before=>request('/api/peer/preview?'+new URLSearchParams({proof:latest.current.proof,...(before?{before}:{})}),{signal:controller.signal});
   const result=earlier?prependPeerPage(snapshot,await fetchPage(snapshot.page.before)):await refreshPeerPages(snapshot,fetchPage);
   if(controller.signal.aborted||token!==version.current)return;
   selection.current=anchor();setUnavailable(false);
   setSnapshot(result);
  }catch(e){if(!controller.signal.aborted&&token===version.current){if([403,404].includes(e.status)){setUnavailable(true);setSnapshot(null)}setError(e.message)}}finally{if(token===version.current)setBusy(false)}
 }
 useEffect(()=>{setSnapshot(null);setUnavailable(false);setUnknown(false);load();return()=>{version.current++;read.current?.abort()}},[identity]);
 // Revalidate the exact currently displayed original's host-derived binding.
 // A source can leave the visible navigation page; its recipient attribution
 // still changes on revocation. Never poll or auto-follow transcript bodies.
 const catalogRevision=state.chatNavigation,workspaces=state.workspaces;
 const currentPeer=state.sessions?.find(s=>s.id===active.sourceSessionId)?.messages?.find(m=>m.id===active.messageId)?.attribution?.peer;
 const bindingRevision=JSON.stringify(currentPeer?[currentPeer.available,currentPeer.sourceId,currentPeer.title,currentPeer.workspaceLabel,currentPeer.location,currentPeer.archived,currentPeer.proof]:null);
 const failClosed=useCallback(e=>{version.current++;read.current?.abort();setUnavailable(true);setSnapshot(null);setError(e.message)},[]);
 useEffect(()=>{
  const controller=new AbortController();
  request('/api/peer/preview?'+new URLSearchParams({proof:latest.current.proof,metadata:'true'}),{signal:controller.signal}).then(result=>{if(!controller.signal.aborted)setMetadata(result.source)}).catch(e=>{if(!controller.signal.aborted){if([403,404].includes(e.status))failClosed(e);else setError('Source access could not be rechecked. Refresh before opening the chat.')}});
  return()=>controller.abort();
 },[bindingRevision,catalogRevision,workspaces,failClosed]);
 async function engage(){if(opening||unknown||unavailable)return;setOpening(true);setError('');try{const receipt=await dispatch('session.select',{id:identity,peerPreview:{proof:latest.current.proof,sourceSessionId:active.sourceSessionId}},{id:crypto.randomUUID()});if(receipt?.accepted===true)onClose(active);else if(!receipt?.superseded)setError(receipt?.error||'The chat was not opened.')}catch(e){if(e.unconfirmed||!Number.isInteger(e.status))setUnknown(true);setError(e.message)}finally{setOpening(false)}}
 return <aside className="a-canvas-panel a-peer-preview-panel" data-part="canvas" data-mode="artifact" data-focused={false} ref={panel} role={layout.overlay?'dialog':'region'} aria-modal={layout.overlay||undefined} aria-label="Read-only source chat preview" onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();close()}}}>
  {!layout.overlay&&<PaneResizer layout={layout} pane="canvas"/>}
  <header className="a-canvas-head"><div><strong>{unavailable?'Source chat unavailable':metadata.title}</strong><small>{!unavailable&&metadata.workspaceLabel&&<><span data-part="peer-workspace">{metadata.workspaceLabel}</span> · </>}Read only{!unavailable&&metadata.archived?' · Archived':''}</small></div><button type="button" className="a-soft a-peer-preview-close" data-tooltip="off" aria-label="Close source preview" onClick={close}><X aria-hidden="true"/><span>Close</span></button></header>
  <div className="a-peer-preview-toolbar"><button className="a-soft" type="button" disabled={busy||unavailable} onClick={()=>load()}><RefreshCw/>Refresh</button><button className="a-soft" type="button" disabled={!snapshot||opening||unavailable||unknown} onClick={engage}><ArrowUpRight/>{opening?'Opening…':'Open chat'}</button></div>
  <p className="a-caption a-peer-snapshot-status" role="status">{snapshot?`Snapshot · ${new Date(snapshot.capturedAt*1000).toLocaleString()} · ${snapshot.source.status} at capture · not following`:busy?'Loading bounded source history…':'No source snapshot available.'}</p>
  {error&&<p className="a-peer-preview-error" role="alert">{error}{snapshot?' Previous snapshot retained; currentness unknown.':''}{unknown?' Opening was not confirmed; no automatic retry will run.':''}</p>}
  <div className="a-peer-preview-scroll" ref={scroll} aria-busy={busy}>
   {snapshot?.page.hasEarlier&&<button type="button" className="a-link" disabled={busy} onClick={()=>load(true)}>Load earlier messages</button>}
   {!!snapshot?.page.unavailableRanges?.length&&<p role="status">Some source ranges are unavailable. Nothing was reconstructed.</p>}
   {snapshot?.messages.map(m=><PreviewMessage key={m.id} message={m} proof={active.proof} onUnavailable={failClosed}/>)}
  </div>
 </aside>;
}
