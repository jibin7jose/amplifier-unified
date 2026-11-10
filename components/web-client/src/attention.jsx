import React from 'react';
export function AttentionBadge({state={},section,page,count:explicit,settings=false}){
 const count=explicit??(settings?state.attention?.settingsUnread:page?state.attention?.pages?.[page]:section?state.attention?.sections?.[section]:(state.attention?.badgeUnread??state.attention?.unread));
 const unresolved=state.attention?.items?.some(i=>i.actionRequired&&(!page||i.page===page)&&(!section||i.section===section));
 return count>0?<span className="a-attention-badge" role="status" aria-label={`${count} ${unresolved?'items needing attention':'unread items'}`}>{count}</span>:null;
}
// A notice is read only after its rendered content is visible in the focused tab.
// Exact fingerprints prevent a late acknowledgement from hiding a newer notice.
export function useReadVisible(items,act,ref){
 const key=JSON.stringify(items.filter(i=>i&&!i.read).map(i=>[i.id,i.fingerprint]));
 React.useEffect(()=>{
  const pending=items.filter(i=>i&&!i.read),element=ref.current;if(!pending.length||!element)return;
  let timer,intersects=false,sent=false;
  const visible=()=>intersects&&!document.hidden&&document.hasFocus()&&element.getClientRects().length>0&&!element.closest('[inert], [hidden], details:not([open])');
  const check=()=>{clearTimeout(timer);if(!sent&&visible())timer=setTimeout(()=>{if(visible()){sent=true;Promise.resolve(readItems(act,pending)).catch(()=>{sent=false})}},750)};
  const observer=new IntersectionObserver(entries=>{intersects=entries[0].isIntersecting;check()});
  observer.observe(element);document.addEventListener('visibilitychange',check);window.addEventListener('focus',check);window.addEventListener('blur',check);
  return()=>{clearTimeout(timer);observer.disconnect();document.removeEventListener('visibilitychange',check);window.removeEventListener('focus',check);window.removeEventListener('blur',check)};
 },[key,act,ref]);
}
export function ReadWhenVisible({item,act,children,className}){
 const ref=React.useRef(null);useReadVisible(item?[item]:[],act,ref);
 return <div ref={ref} className={className}>{children}</div>;
}
export function AttentionReview({state,act,page,items:provided,showItems=false}){
 const items=provided||(state.attention?.items||[]).filter(i=>i.page===page&&!i.sessionId);
 if(!items.length||page==='updates')return null;
 return <div className="a-attention-review">{items.map(item=><ReadWhenVisible key={item.id} item={item} act={act}><p><strong>{item.title}</strong>{item.detail&&<> · {item.detail}</>}</p></ReadWhenVisible>)}</div>;
}

export const readItems=(act,items)=>act('attention.read',{ids:items.map(i=>i.id),fingerprints:Object.fromEntries(items.map(i=>[i.id,i.fingerprint]))});
// Read state is separate from errors, approvals and unanswered questions.
export const unreadChatItems=(state,id)=>(state.attention?.items||[]).filter(item=>
 item.sessionId===id&&!item.read&&(item.id==='completion:'+id||item.id==='unread:'+id));
// Capture only the items the user saw; arrivals during a bulk review stay unread.
export async function reviewItems(act,items){
 for(let offset=0;offset<items.length;offset+=500)await readItems(act,items.slice(offset,offset+500));
}
export function CompletionBadgePreference({state,act}){
 const [pending,setPending]=React.useState(null),[desired,setDesired]=React.useState(null),[error,setError]=React.useState('');
 const busy=React.useRef(false),operation=state.actionStatus?.['notifications.save'];
 React.useEffect(()=>{
  if(!pending||operation?.commandId!==pending||!['ready','error'].includes(operation.phase))return;
  busy.current=false;setPending(null);setDesired(null);if(operation.phase==='error')setError(operation.error||'Could not save your preference. Try again.');
 },[pending,operation?.commandId,operation?.phase]);
 async function change(completionBadge){
  if(busy.current)return;busy.current=true;setDesired(completionBadge);setError('');
  try{const result=await act('notifications.save',{patch:{completionBadge}});if(!result?.accepted||!result.operationId)throw Error('Could not save your preference. Try again.');setPending(result.operationId)}
  catch(e){busy.current=false;setPending(null);setDesired(null);setError(e.message)}
 }
 return <div className="a-completion-preference"><label className="a-inline-checkbox"><input type="checkbox" checked={desired??state.notificationSettings?.completionBadge??false} disabled={desired!==null} data-action="notifications.save" onChange={e=>change(e.target.checked)}/>Count finished responses in the app badge</label>{desired!==null&&<span role="status">Saving…</span>}{error&&<p role="alert">{error}</p>}</div>;
}
export function FailureDisposition({item,act}){
 const [review,setReview]=React.useState(null),[busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
 const sending=React.useRef(false),trigger=React.useRef(null),confirm=React.useRef(null);
 React.useEffect(()=>{if(review)confirm.current?.focus()},[review]);
 if(!item.dispositionOccurrence)return null;
 function cancel(){if(sending.current)return;setReview(null);setError('');trigger.current?.focus()}
 async function close(){
  if(sending.current)return;sending.current=true;setBusy(true);setError('');
  try{
   const response=await act('updates.failureDisposition',{id:review.id,expectedOccurrence:review.occurrence,disposition:'acknowledged-uncertainty'});
   if(response?.result?.status!=='acknowledged-uncertainty'||response.result.recoveryVerified!==false)throw Error('Closure was not confirmed. Inspect the current notice before trying again.');
   setReview(null);
  }catch(e){setError(e.message||'Closure was not confirmed. Inspect the current notice before trying again.')}
  finally{sending.current=false;setBusy(false)}
 }
 return <><button ref={trigger} type="button" className="a-soft" aria-expanded={!!review} disabled={busy} onClick={()=>{setError('');setReview({id:item.id,occurrence:item.dispositionOccurrence,title:item.title,detail:item.detail,occurrences:item.occurrences})}}>Close unresolved notice</button>
 {review&&<section className="a-failure-disposition" aria-label="Confirm uncertainty disposition" onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();cancel()}}}>
  <strong>Close with recovery unverified?</strong><p>{review.title} · {review.occurrences} {review.occurrences===1?'occurrence':'occurrences'}</p><p className="a-caption">{review.detail}</p>
  <p>This closes only this reviewed occurrence in Needs action. It does not verify recovery, retry an update, or remove diagnostic history. Current errors, safety gates, and new failures still need attention.</p>
  {review.occurrence!==item.dispositionOccurrence&&<p role="status">A newer occurrence arrived. Cancel and review the current notice.</p>}
  {error&&<p role="alert">{error}</p>}
  <div className="a-inbox-group-head"><button ref={confirm} type="button" className="a-soft" disabled={busy} data-action="updates.failureDisposition" onClick={close}>{busy?'Closing…':'Close with recovery unverified'}</button><button type="button" className="a-link" disabled={busy} onClick={cancel}>Cancel</button></div>
 </section>}</>;
}
export function FailureDispositions({state}){
 const receipts=state.updates?.diagnostics?.failureDispositions||[];
 if(!receipts.length)return null;
 return <details className="a-failure-dispositions"><summary>Closed notices · recovery unverified ({receipts.length})</summary>
 <p className="a-caption">Explicitly acknowledged uncertainty, not verified recovery. Recent dispositions are retained here; original receipts remain in the bounded diagnostic history.</p>
 <ul>{[...receipts].reverse().map(row=><li key={row.id}>{row.target==='updates:failure-overflow'?'Additional update failures':'Application update'} · {row.occurrences} {row.occurrences===1?'occurrence':'occurrences'} · Closed with recovery unverified · {new Date(row.at*1000).toLocaleString()}</li>)}</ul></details>;
}
export function ActivityPanel({state,act}){
 const [showReviewed,setShowReviewed]=React.useState(false),[limit,setLimit]=React.useState(10),[busy,setBusy]=React.useState(false),[error,setError]=React.useState('');
 const reviewing=React.useRef(false);
 const all=state.attention?.items||[],unread=all.filter(i=>!i.read&&!i.informational),reviewable=unread.filter(i=>i.reviewable!==false);
 const unresolved=all.filter(i=>i.actionRequired),information=all.filter(i=>i.informational);
 const finished=unread.filter(i=>i.id.startsWith('completion:')).sort((a,b)=>(b.completedAt||0)-(a.completedAt||0));
 const needsReview=unread.filter(i=>!i.actionRequired&&!i.id.startsWith('completion:')),reviewed=all.filter(i=>i.read&&!i.actionRequired&&!i.informational);
 async function review(items){
  if(reviewing.current||!items.length)return;reviewing.current=true;setBusy(true);setError('');
  try{await reviewItems(act,items)}catch(e){setError(e.message||'Could not mark these items reviewed. Try again.')}finally{reviewing.current=false;setBusy(false)}
 }
 async function open(item){
  try{
   if(item.sessionId){await act('session.select',{id:item.sessionId});await act('view.update',{patch:{panel:null}})}
   else if(item.requestId){await act('view.update',{patch:{panel:'feedback'}});await readItems(act,[item]);}
   else await act('view.update',{patch:{panel:'settings',settingsSection:item.section,settingsExpanded:[item.page]}});
  }catch(e){setError(e.message)}
 }
 function row(item){return <div className="a-attention-item" key={item.id}><button className="a-attention-open" type="button" data-action={item.sessionId?'session.select':'view.update'} onClick={()=>open(item)}><strong>{item.label||item.title}</strong>{!item.id.startsWith('completion:')&&<span>{item.label?item.title:item.detail}</span>}{item.actionRequired&&<span>{item.read?'Reviewed · still unresolved':'Unresolved'}{item.occurrences>1?` · ${item.occurrences} occurrences`:''}</span>}</button>{!item.read&&!item.informational&&item.reviewable!==false&&<button className="a-soft a-attention-dismiss" type="button" disabled={busy} aria-label={`Mark reviewed: ${item.label||item.title}`} onClick={()=>review([item])}>Mark reviewed</button>}<FailureDisposition item={item} act={act}/></div>}
 return <section className="a-activity-inbox" aria-label="Activity to review">
 <div className="a-inbox-toolbar"><p>Review updates here without opening every chat.</p><button className="a-soft" type="button" disabled={busy||!reviewable.length} onClick={()=>review(reviewable)}>{busy?'Marking reviewed…':'Mark all reviewed'}</button></div>
 <p className="a-caption">Marking reviewed records what you have seen. Update failures stay here until matching recovery or an explicit uncertainty disposition. Questions and approvals still wait for your answer.</p>
 {error&&<p role="alert">{error}</p>}
 {unresolved.length>0&&<section aria-label="Needs action"><h4>Needs action <span className="a-caption">{unresolved.length}</span></h4>{unresolved.map(row)}</section>}
 {needsReview.length>0&&<section aria-label="To review"><h4>To review <span className="a-caption">{needsReview.length}</span></h4>{needsReview.map(row)}</section>}
 {finished.length>0&&<section aria-label="Finished responses"><div className="a-inbox-group-head"><h4>Finished responses <span className="a-caption">{finished.length}</span></h4><button className="a-link" disabled={busy} onClick={()=>review(finished)}>Mark responses reviewed</button></div>{finished.slice(0,limit).map(row)}{finished.length>limit&&<button className="a-link" onClick={()=>setLimit(limit+10)}>Show more responses ({finished.length-limit} remaining)</button>}</section>}
 {!unread.length&&!unresolved.length&&<p className="a-inbox-empty">All caught up.</p>}
 <CompletionBadgePreference {...{state,act}}/>
 <FailureDispositions state={state}/>
 {reviewed.length>0&&<><button className="a-link" type="button" onClick={()=>setShowReviewed(!showReviewed)}>{showReviewed?'Hide reviewed items':'Show reviewed items'}</button>{showReviewed&&<section aria-label="Reviewed items">{reviewed.slice(0,limit).map(row)}{reviewed.length>limit&&<button className="a-link" onClick={()=>setLimit(limit+10)}>Show more reviewed items</button>}</section>}</>}
 {information.length>0&&<details><summary>News and receipts</summary><p className="a-caption">For reference; these do not count in the app or Settings badges.</p>{information.slice(0,limit).map(row)}{information.length>limit&&<button className="a-link" onClick={()=>setLimit(limit+10)}>Show more news and receipts</button>}</details>}
 </section>;
}

// Viewing the end of a chat in a focused tab acknowledges that exact completion.
// Selection alone (by an agent, another device, or a hidden tab) never does.
export function completionToRead(state){
 const items=state?.attention?.items||[];
 return items.find(item=>item.id==='unread:'+state.selectedSessionId&&!item.read&&item.visit===state.view?.unreadVisit)
  ||items.find(item=>item.id==='completion:'+state.selectedSessionId&&!item.read);
}
export function useReadCompletion(state,act,pane,ready=true){
 const item=completionToRead(state);
 const blocked=!!state?.view?.panel||!!state?.view?.canvasFocused||!!state?.view?.navExpanded&&matchMedia('(max-width:760px)').matches||!!state?.canvas?.open&&matchMedia('(max-width:760px)').matches;
 React.useEffect(()=>{
  if(!ready||!item||blocked||!pane.current)return;
  const element=pane.current;let timer;
  const visible=()=>!document.hidden&&document.hasFocus()&&element.clientHeight>0&&getComputedStyle(element).visibility!=='hidden'&&!element.closest('[inert]')&&element.scrollHeight-element.scrollTop-element.clientHeight<40;
  const check=()=>{clearTimeout(timer);if(visible())timer=setTimeout(()=>{if(visible())readItems(act,[item])},900)};
  element.addEventListener('scroll',check,{passive:true});document.addEventListener('visibilitychange',check);window.addEventListener('focus',check);window.addEventListener('blur',check);
  const resize=new ResizeObserver(check);resize.observe(element);for(const child of element.children)resize.observe(child);
  check();return()=>{clearTimeout(timer);resize.disconnect();element.removeEventListener('scroll',check);document.removeEventListener('visibilitychange',check);window.removeEventListener('focus',check);window.removeEventListener('blur',check)};
 },[item?.id,item?.fingerprint,blocked,act,pane,ready]);
}
