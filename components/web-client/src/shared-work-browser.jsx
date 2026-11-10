import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import './shared-work-browser.css';

const filters=[['accepted','Accepted · open'],['all','All'],['attention','Needs attention'],['proposed','Proposals'],['closed','Closed']];
const words=value=>typeof value==='string'?value.replaceAll('_',' '):'Unknown';
const when=value=>typeof value==='number'?new Date(value*1000).toLocaleString():'Time not recorded';
const json=value=>JSON.stringify(value,null,2);
const initialView=()=>({filter:'accepted',query:'',after:null,previous:[],selected:null,summary:null,scroll:0});
function loadView(key){
 try{
  const saved=JSON.parse(sessionStorage.getItem(key)||'null');
  if(!saved||!filters.some(([key])=>key===saved.filter))return initialView();
  return {...initialView(),...saved,query:String(saved.query||'').slice(0,200)};
 }catch{return initialView()}
}
function confirmed(response){
 if(response?.accepted!==true||!response.result)throw Error(response?.error||'Work observation unavailable.');
 return response.result;
}
function validPage(page,workspaceId){
 if(page?.workspaceId!==workspaceId||!Array.isArray(page.items)||!Number.isInteger(page.version)
  ||page.items.some(row=>row.workspaceId!==workspaceId))throw Error('Work page scope or revision is unavailable.');
 return page;
}
// Returned references are data. Construct only this exact, local read vocabulary;
// never dispatch an action, extra argument or workspace chosen by source text.
function readRef(ref,verb,workspaceId,id,identity){
 const args=ref?.args,allowed=['workspaceId','id',...(identity?[identity]:[])];
 return ref?.action==='workspace.work.'+verb&&args?.workspaceId===workspaceId&&args.id===id
  &&Object.keys(args).every(key=>allowed.includes(key))&&(!identity||typeof args[identity]==='string'&&args[identity])
  ?{...args}:null;
}
function useVisible(){
 const [visible,setVisible]=useState(()=>typeof document==='undefined'||!document.hidden);
 useEffect(()=>{if(typeof document==='undefined')return;const change=()=>setVisible(!document.hidden);document.addEventListener('visibilitychange',change);return()=>document.removeEventListener('visibilitychange',change)},[]);
 return visible;
}
// A finite, cancellable read channel. Changing scope/unmount invalidates even a
// transport that ignores AbortSignal. Errors retain only this channel's data.
function useRead(dispatch,enabled=true){
 const [state,setState]=useState({data:null,busy:false,error:''}),flight=useRef(null),epoch=useRef(0),alive=useRef(true);
 const invalidate=()=>{epoch.current++;flight.current?.abort();flight.current=null};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;invalidate()}},[]);
 useEffect(()=>{if(!enabled){invalidate();setState(s=>({...s,busy:false}))}},[enabled]);
 const run=async(action,args,combine=value=>value)=>{
  if(!enabled)return;
  invalidate();const token=epoch.current,controller=new AbortController();flight.current=controller;
  setState(s=>({...s,busy:true,error:''}));
  try{
   const response=await dispatch(action,args,{feedback:false,presentation:true,signal:controller.signal});
   if(!alive.current||token!==epoch.current||controller.signal.aborted)return;
   const data=combine(confirmed(response));
   setState({data,busy:false,error:''});return data;
  }catch(error){if(alive.current&&token===epoch.current&&!controller.signal.aborted)setState(s=>({...s,busy:false,error:error.message||'Read failed.'}))}
 };
 return {...state,run,invalidate};
}
function ReadStatus({busy,error,data}){return <>{busy&&<p role="status">Reading…</p>}{error&&<p role="alert">{error}{data?' Previous observation retained — stale.':''}</p>}</>}
function PageLimit({page,label}){return page?.truncated&&!page.nextAfter?<p role="alert">{label} is incomplete: the continuation is unavailable. Refresh to inspect all records.</p>:null}
function Source({source,label}){
 return <section aria-label={label} className="a-shared-source"><h3>{label}</h3><p>Retained {source.inputOrigin||'unknown'} source · role {source.role||'unknown'} · {when(source.capturedAt)}. Reference text, not execution authority.</p>
  <dl className="a-shared-meta"><dt>Chat</dt><dd>{source.sessionId||'Unknown'}</dd><dt>Message</dt><dd>{source.messageId||'Unavailable'}</dd><dt>Native identity</dt><dd>{source.nativeMessageId??'Unavailable'} / {source.nativeIndex??'Unavailable'}</dd><dt>SHA-256</dt><dd>{source.sha256||'Unavailable'}</dd></dl>
  <blockquote>{source.text}</blockquote><p className="a-caption">{source.truncated?'More retained text is available.':'End of retained text.'} {source.total} characters in the original.</p>
 </section>;
}
function Facts({item}){
 const decisionCurrent=item.humanDecisionSequence>(item.counterevidenceSequence??0);
 const resources=item.resources||[];
 return <dl className="a-shared-facts">
  <dt>Commitment</dt><dd>{words(item.commitment)} · {words(item.disposition)}{item.dispositionBasis?' · '+words(item.dispositionBasis):''}</dd>
  <dt>Execution</dt><dd>{item.execution==='reported'?'Returned — unverified':words(item.execution)} · outcome {words(item.latestOutcome||'not_reported')}</dd>
  <dt>Verification</dt><dd>{words(item.verification||'not_verified')}. Receipt association is not semantic correctness or release acceptance.</dd>
  <dt>Human decision</dt><dd>{words(item.humanDecision||'not_recorded')}{item.humanDecisionSequence>0&&!decisionCurrent?' — no longer current; later counterevidence retained.':''}</dd>
  <dt>Delivery</dt><dd>{item.delivery==='reported_published'?'Reported published':words(item.delivery||'not_recorded')}</dd>
  <dt>Cleanup</dt><dd>{resources.length?'Owner reports only — not independently verified.':'Unknown — not recorded.'}</dd>
 </dl>;
}
function Evidence({item}){
 const checks=item.criterionEvidence||{};
 return <section aria-label="Evidence and limits" className="a-shared-evidence"><h3>Evidence and limits</h3>
  <p>Latest reported outcome: {words(item.latestOutcome||'not_reported')}. Participant reports are not independent verification.</p>
  <p className="a-caption">References apply to this observed promise and candidate. Receipt association is not semantic correctness, human acceptance or shipment. Earlier reports remain in history.</p>
  {item.criteria?.length?<ul>{item.criteria.map(criterion=>{
   const check=checks[criterion.id];
   const complete=check&&['candidate','reviewer','checkReceiptId'].every(key=>typeof check[key]==='string'&&check[key].trim());
   const label=!check?'No qualified check recorded for this criterion.'
    :!complete?'Incomplete check reference — not verified.'
    :check.candidate!==item.candidate?'Different candidate — not current.'
    :check.qualification==='independent_check'?'Independent check recorded.'
    :check.qualification==='owner_check'?'Owner check recorded — not independent.'
    :'Unclassified evidence — not verified.';
   return <li key={criterion.id}><strong>{criterion.id}: {criterion.text}</strong><p>{label}</p>
    {check&&<dl className="a-shared-meta"><dt>Candidate</dt><dd>{check.candidate||'Not recorded'}</dd><dt>Recorded by</dt><dd>{check.reviewer||'Not recorded'}</dd><dt>Check receipt</dt><dd>{check.checkReceiptId||'Not recorded'}</dd></dl>}
   </li>;
  })}</ul>:<p>No acceptance criteria recorded. This does not establish verification.</p>}
 </section>;
}
function EventFacts({event}){
 return <><p>Event {event.id} · sequence {event.sequence} · promise {event.promiseRevision} · {when(event.recordedAt)} · {event.actor?.origin||'unknown'} / {event.actor?.sessionId||'unknown'}</p>
  {event.changedFields?.length>0&&<p>Changed: {event.changedFields.join(', ')}</p>}
  {event.evidence&&<><p>{event.evidence.report}</p><p>Outcome {words(event.evidence.outcome)} · {words(event.evidence.qualification)} · candidate {event.evidence.candidate} · reported by {event.evidence.reportedBy||'unknown'}. Criteria valid at report time: {String(event.evidence.criteriaValidated??'unknown')}. This event does not certify the current promise.</p><pre>{json(event.evidence)}</pre></>}
  {['changes','link','decision','disposition','resource','publication'].filter(key=>event[key]!==undefined).map(key=><div key={key}><h4>{words(key)}</h4><pre>{json(event[key])}</pre></div>)}
 </>;
}
function History({workspaceId,id,dispatch,enabled}){
 const history=useRead(dispatch,enabled),event=useRead(dispatch,enabled),source=useRead(dispatch,enabled);
 const [sourceArgs,setSourceArgs]=useState(null);
 const read=after=>history.run('workspace.work.history',{workspaceId,id,limit:20,...(after?{after}:{})},value=>{
  if(!Array.isArray(value.items))throw Error('History observation unavailable.');
  if(after&&value.snapshotEventCount!==history.data?.snapshotEventCount)throw Error('History snapshot changed; refresh history.');
  return value;
 });
 const openSource=args=>{setSourceArgs(args);return source.run('workspace.work.source',args,value=>{
  if(!value.source||typeof value.source.text!=='string')throw Error('Retained source unavailable.');return value.source;
 })};
 const moreSource=()=>source.run('workspace.work.source',{...sourceArgs,offset:source.data.nextOffset},value=>{
  if(value.source.sha256!==source.data.sha256||value.source.offset!==source.data.nextOffset)throw Error('Source changed; read the retained source again.');
  return {...value.source,text:source.data.text+value.source.text};
 });
 return <section aria-label="Work history" className="a-shared-history"><h3>History and corrections</h3><p>Immutable events keep earlier promises and outcomes. A participant report remains unverified.</p>
  <button type="button" className="a-soft" disabled={history.busy||!enabled} onClick={()=>read()}>Read history</button><ReadStatus {...history}/>
  {history.data&&<><p>History snapshot: {history.data.snapshotEventCount} events. Current count: {history.data.currentEventCount}.{history.data.snapshotIsCurrent===false?' Newer history exists; this page keeps its original snapshot.':''}</p>
   <ol>{history.data.items.map(row=>{
    const args=readRef(row.sourceRef,'source',workspaceId,id,'sourceId'),exact=readRef(row.eventRef,'event',workspaceId,id,'eventId');
    return <li key={row.id}><h4>{words(row.kind)}</h4><EventFacts event={row}/>
     {row.source?.sha256&&<p>{row.source.inputOrigin||'unknown'} source · {row.source.sha256}</p>}
     {row.sourceRef&&<button type="button" className="a-link" disabled={!args||source.busy||!enabled} onClick={()=>openSource(args)}>{row.kind==='create'?'Read original source':'Read correction source'}</button>}
     {!args&&row.sourceRef&&<p>Source reference unavailable for this item.</p>}
     {row.eventRef&&<button type="button" className="a-link" disabled={!exact||event.busy||!enabled} onClick={()=>event.run('workspace.work.event',exact,value=>{if(value.event?.id!==row.id)throw Error('Event identity changed.');return value.event})}>Read event: {row.id}</button>}
    </li>;
   })}</ol><PageLimit page={history.data} label="History"/>
   <button type="button" className="a-soft" disabled={history.busy||!enabled||!history.data.nextAfter} onClick={()=>read(history.data.nextAfter)}>Next history</button>
  </>}
  <ReadStatus {...event}/>{event.data&&<section aria-label="Exact event"><h3>Exact retained event</h3><EventFacts event={event.data}/></section>}
  <ReadStatus {...source}/>{source.data&&<><Source source={source.data} label="Retained event source"/>{source.data.truncated&&<button type="button" className="a-soft" disabled={source.busy||!enabled||source.data.nextOffset==null} onClick={moreSource}>Read more correction source</button>}</>}
 </section>;
}
function WorkDetail({workspaceId,id,summary,dispatch,onOpenChat,onBack,enabled,focusToken,onScroll,scroll=0}){
 const read=useRead(dispatch,enabled),heading=useRef(null),pane=useRef(null),focused=useRef(0),opening=useRef(false),alive=useRef(true);
 const [navigation,setNavigation]=useState({busy:false,error:''});
 const validate=value=>{
  if(value.item?.id!==id||value.item.workspaceId!==workspaceId)throw Error('Work detail belongs to another scope.');
  if(value.item.revision<(summary?.revision||0)||value.item.revision<(read.data?.item.revision||0))throw Error('A newer Work revision was observed. Refresh this detail.');
  return value;
 };
 const refresh=()=>read.run('workspace.work.read',{workspaceId,id},validate);
 useEffect(()=>{if(enabled&&!read.data)refresh()},[enabled]);
 useEffect(()=>()=>{alive.current=false},[]);
 useLayoutEffect(()=>{
  if(read.data&&focusToken!==focused.current){focused.current=focusToken;heading.current?.focus();if(pane.current)pane.current.scrollTop=scroll}
 },[read.data,focusToken]);
 const more=()=>read.run('workspace.work.read',{workspaceId,id,sourceOffset:read.data.originalSource.nextOffset},value=>{
  validate(value);const old=read.data;
  if(value.item.revision!==old.item.revision||value.originalSource.sha256!==old.originalSource.sha256||value.originalSource.offset!==old.originalSource.nextOffset)throw Error('Work changed during source reading. Refresh this detail; earlier text is retained.');
  return {...value,originalSource:{...value.originalSource,text:old.originalSource.text+value.originalSource.text}};
 });
 const open=async sid=>{
  if(opening.current||!onOpenChat)return;opening.current=true;setNavigation({busy:true,error:''});
  try{const response=await onOpenChat(sid);if(alive.current&&response?.accepted!==true)setNavigation({busy:false,error:response?.error||'Chat navigation was not accepted. Your current view is retained.'})}
  catch(error){if(alive.current)setNavigation({busy:false,error:error.message})}
  finally{opening.current=false;if(alive.current)setNavigation(s=>({...s,busy:false}))}
 };
 const value=read.data,item=value?.item,newer=summary?.revision>item?.revision;
 const chats=[...new Set([item?.ownerSessionId,item?.originSessionId,...(item?.links||[]).map(link=>link.sessionId)].filter(Boolean))];
 return <article aria-label="Work details" className="a-shared-detail" ref={pane} onScroll={event=>onScroll?.(event.currentTarget.scrollTop)}>
  <div className="a-shared-actions"><button type="button" className="a-soft" onClick={onBack}>Back to Work</button><button type="button" className="a-link" disabled={read.busy||!enabled} onClick={refresh}>Refresh detail</button></div>
  <ReadStatus {...read}/>{newer&&<p role="status">Newer Work revision {summary.revision} observed. This detail is a previous observation; refresh to compare.</p>}
  {item&&<><h2 ref={heading} tabIndex={-1}>{item.title}</h2><p className="a-caption">{id} · record revision {item.revision} · promise revision {item.promiseRevision} · {when(item.updatedAt)}</p>
   <Facts item={item}/>
   <section><h3>Current promise</h3><p>{item.interpretation}</p><p className="a-caption">Attributed scope interpretation; not an execution grant.</p>
    <h4>Criteria</h4><ul>{(item.criteria||[]).map(row=><li key={row.id}><strong>{row.id}</strong>: {row.text}</li>)}</ul>
    <h4>Constraints</h4><ul>{(item.constraints||[]).map((text,i)=><li key={i}>{text}</li>)}</ul>
    <p>Next action: {item.nextAction||'Not recorded'}</p>{item.blockedReason&&<p>Blocked: {item.blockedReason}</p>}
    {!!item.dependsOn?.length&&<p>Dependencies: {item.dependsOn.join(', ')}</p>}
    <p>Candidate: {item.candidate||'Not recorded'}</p><p>Promise source: {item.promiseEventRef||'Unavailable'} · verification boundary: {item.verificationBoundaryRef||'Unavailable'}</p>
   </section>
   {value.originalSource&&<><Source source={value.originalSource} label="Original source"/>{value.originalSource.truncated&&<button type="button" className="a-soft" disabled={read.busy||!enabled||value.originalSource.nextOffset==null} onClick={more}>Read more source</button>}</>}
   {item.deliveryReport&&<section><h3>Publication report</h3><p>{item.deliveryReport.report}</p><p>Reported by {item.deliveryReport.reportedBy} · {when(item.deliveryReport.recordedAt)} · promise {item.deliveryReport.promiseRevision} · candidate {item.deliveryReport.candidate}</p><p>{item.deliveryReport.destination}</p><ul>{item.deliveryReport.references?.map(ref=><li key={ref}>{ref}</li>)}</ul><p>Participant attribution; not host-observed delivery or independent verification.</p></section>}
   {!!item.resources?.length&&<section><h3>Reported resources</h3><p>Explicit owner reports only; no resource census or teardown implied.</p><ul>{item.resources.map((r,i)=><li key={r.resourceId+':'+i}>{r.resourceId} · {r.kind} · {words(r.reportedState)} · reported by {r.ownerSessionId} · {when(r.recordedAt)}<p>{r.evidenceReference}</p><small>{r.attribution}; independently verified: {String(r.independentlyVerified)}</small></li>)}</ul></section>}
   <Evidence item={item}/>
   <details><summary>Current links and criterion evidence</summary><pre>{json({links:item.links,criterionEvidence:item.criterionEvidence})}</pre></details>
   <History key={id} workspaceId={workspaceId} id={id} dispatch={dispatch} enabled={enabled}/>
   <section><h3>Related chats</h3><p>Inspection does not change your conversation. Opening a chat uses the existing draft and dirty-view guards.</p>{chats.map(sid=><button type="button" className="a-soft" key={sid} aria-label={'Open chat: '+sid} disabled={!onOpenChat||navigation.busy} onClick={()=>open(sid)}>Open chat · {sid}</button>)}{navigation.error&&<p role="alert">{navigation.error}</p>}</section>
  </>}
 </article>;
}
function Coverage({workspaceId,dispatch,enabled}){
 const read=useRead(dispatch,enabled);
 const load=after=>read.run('workspace.work.coverage',{workspaceId,limit:20,...(after?{after}:{})},value=>{
  if(!Array.isArray(value.items))throw Error('Capture coverage is unavailable; historical coverage remains unknown.');return value;
 });
 return <details className="a-shared-coverage"><summary>Capture coverage — historical inventory incomplete</summary><p>This is not a count of missing tasks. No historical import or automatic classification occurs.</p>
  <button type="button" className="a-link" disabled={read.busy||!enabled} onClick={()=>load()}>Read capture coverage</button><ReadStatus {...read}/>
  {read.data&&<><p>{read.data.observedInputs} observed inputs; {read.data.unreviewedInputs} unresolved or unreviewed. Historical coverage: {read.data.historicalCoverage}.</p><p>{read.data.detail}</p>
   <ul>{read.data.items.map(row=><li key={row.id}><p>{row.id} · {row.reviewDisposition} · {row.inputOrigin||'unknown'}</p><p>Chat {row.sessionId} · message {row.messageId} · {when(row.observedAt||row.recordedAt)}</p><p>Saved hash: {row.sourceSha256} · initially observed: {row.observedSourceSha256||row.sourceSha256}</p><p>Source reference: {json(row.readRef)}</p></li>)}</ul><PageLimit page={read.data} label="Capture coverage"/>
   <button type="button" className="a-soft" disabled={read.busy||!enabled||!read.data.nextAfter} onClick={()=>load(read.data.nextAfter)}>Next capture coverage</button>
  </>}
 </details>;
}

export function SharedWorkBrowser(props){return <WorkList key={[props.workspace.id,props.workspace.path,props.clientId,props.hostInstanceId].join(':')} {...props}/>}
function WorkList({workspace,dispatch,onOpenChat,hostInstanceId=''}){
 // sessionStorage is tab-private (a duplicate gets a copy, never shared writes).
 const key='amplifier-work-view:'+hostInstanceId+':'+workspace.id;
 const [saved,setSaved]=useState(()=>loadView(key)),savedRef=useRef(saved);
 const [page,setPage]=useState(null),pageRef=useRef(null),[busy,setBusy]=useState(true),[error,setError]=useState(''),[storageError,setStorageError]=useState('');
 const [refresh,setRefresh]=useState(0),[focusToken,setFocusToken]=useState(0),visible=useVisible();
 const epoch=useRef(0),observer=useRef(null),list=useRef(null),buttons=useRef(new Map()),backFocus=useRef(null);
 const persist=next=>{savedRef.current=next;setSaved(next);try{sessionStorage.setItem(key,JSON.stringify(next))}catch{setStorageError('Browser view preferences could not be saved; keep this tab open.')}};
 useEffect(()=>()=>{try{sessionStorage.setItem(key,JSON.stringify(savedRef.current))}catch{/* View-only preferences; no command or draft can be lost. */}},[key]);
 const invalidate=()=>{epoch.current++;observer.current?.abort()};
 const change=patch=>{invalidate();pageRef.current=null;setPage(null);setError('');persist({...savedRef.current,...patch,selected:null,summary:null,scroll:0,detailScroll:0})};
 const reload=()=>{invalidate();setRefresh(n=>n+1)};
 useEffect(()=>{
  if(!visible){invalidate();setBusy(false);return}
  const token=++epoch.current,controller=new AbortController();observer.current=controller;let active=true,timer,releaseTimer;
  const current=()=>active&&!controller.signal.aborted&&token===epoch.current;
  const args={workspaceId:workspace.id,filter:saved.filter,query:saved.query,limit:20,...(saved.after?{after:saved.after}:{})};
  setBusy(true);setError('');
  (async()=>{
   try{
    let response=await dispatch('workspace.work.list',args,{feedback:false,presentation:true,signal:controller.signal});
    while(current()){
     const value=validPage(confirmed(response),workspace.id),old=pageRef.current;
     if(old&&value.version<old.version)throw Error('An older Work revision was returned. Refresh the list.');
     pageRef.current=value;setPage(value);setBusy(false);
     if(!/^[a-f0-9]{64}$/.test(value.observationVersion))throw Error('Observation version unavailable; refresh to receive updates.');
     response=await dispatch('workspace.work.wait',{...args,afterVersion:value.observationVersion,waitMs:30000},{feedback:false,presentation:true,signal:controller.signal});
     if(!current())return;
     // A refusal stops observation; it is never replaced with an empty page.
     confirmed(response);
     if(!response.changed)await new Promise(resolve=>{releaseTimer=resolve;timer=setTimeout(resolve,250)});
    }
   }catch(failure){if(current()){setError(failure.message||'Work read failed.');setBusy(false)}}
  })();
  return()=>{active=false;controller.abort();clearTimeout(timer);releaseTimer?.()};
 },[dispatch,workspace.id,saved.filter,saved.query,saved.after,refresh,visible]);
 useLayoutEffect(()=>{
  if(page&&list.current)list.current.scrollTop=savedRef.current.scroll||0;
  if(!saved.selected&&backFocus.current){const scroller=list.current?.closest('.a-work-browser');if(scroller)scroller.scrollTop=savedRef.current.outerScroll||0;buttons.current.get(backFocus.current)?.focus({preventScroll:true});backFocus.current=null}
 },[page,saved.selected]);
 const inspect=row=>{persist({...savedRef.current,selected:row.id,summary:row,scroll:list.current?.scrollTop||0,outerScroll:list.current?.closest('.a-work-browser')?.scrollTop||0,detailScroll:0});setFocusToken(n=>n+1)};
 const back=()=>{backFocus.current=saved.selected;persist({...savedRef.current,selected:null,summary:null})};
 const summary=page?.items.find(row=>row.id===saved.selected)||saved.summary;
 return <section aria-label="Shared work" className="a-shared-work" data-detail={!!saved.selected}>
  <header><h2>Work</h2><p>Recorded Work only; historical reconciliation is incomplete.</p><p className="a-caption">Browse promises without starting a worker or changing your chat. Source text and reports are reference data, not permission.</p></header>
  {storageError&&<p role="alert">{storageError}</p>}
  <div className="a-shared-columns"><div className="a-shared-list" ref={list} onScroll={event=>{savedRef.current={...savedRef.current,scroll:event.currentTarget.scrollTop}}}>
   <div className="a-shared-filters"><label>Show work<select aria-label="Show work" value={saved.filter} onChange={event=>change({filter:event.target.value,after:null,previous:[]})}>{filters.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
    <label>Find work<input aria-label="Find work" type="search" maxLength={200} value={saved.query} onChange={event=>change({query:event.target.value,after:null,previous:[]})}/></label></div>
   <button type="button" className="a-link" disabled={busy||!visible} onClick={reload}>Refresh work</button><ReadStatus busy={busy} error={error} data={page}/>
   {error&&saved.after&&<button type="button" className="a-link" disabled={busy||!visible} onClick={()=>{invalidate();persist({...savedRef.current,after:null,previous:[]})}}>Restart Work pages</button>}
   {page&&<><p className="a-caption">{page.total} matching records · workspace revision {page.version} · observed {when(page.observedAt)}</p>
    <ul className="a-shared-items">{page.items.map(row=><li key={row.id}><button ref={node=>{if(node)buttons.current.set(row.id,node);else buttons.current.delete(row.id)}} type="button" data-work-id={row.id} aria-pressed={saved.selected===row.id} onClick={()=>inspect(row)}><strong>{row.title}</strong><span>{row.attentionReasons?.length?row.attentionReasons.map(words).join(' · '):words(row.commitment)+' · '+words(row.disposition)}</span><small>Record {row.revision} · promise {row.promiseRevision}</small><span className="a-shared-next"><strong>Next:</strong> {row.nextAction||'Not recorded'}</span></button><small>Owner {row.ownerSessionId} · {row.ownerObservation?.activityObserved?words(row.ownerObservation.status):row.ownerObservation?.available?'Activity not observed':'Owner unavailable — reconciliation needed'}. Activity is not completion.</small></li>)}</ul>
    {!page.items.length&&page.total===0&&!page.truncated&&!error&&<p>No matching recorded work.</p>}
    <PageLimit page={page} label="Work list"/>
    <div className="a-shared-actions"><button type="button" className="a-soft" disabled={busy||!visible||!saved.previous.length} onClick={()=>{invalidate();persist({...savedRef.current,after:saved.previous.at(-1),previous:saved.previous.slice(0,-1)})}}>Previous work</button>
     <button type="button" className="a-soft" disabled={busy||!visible||!page.nextAfter} onClick={()=>{invalidate();persist({...savedRef.current,after:page.nextAfter,previous:[...saved.previous,saved.after]})}}>Next work</button>
    </div>
   </>}
   <Coverage workspaceId={workspace.id} dispatch={dispatch} enabled={visible}/>
  </div>
  {saved.selected?<WorkDetail key={saved.selected} workspaceId={workspace.id} id={saved.selected} summary={summary} dispatch={dispatch} enabled={visible} onOpenChat={onOpenChat} onBack={back} focusToken={focusToken} scroll={saved.detailScroll||0} onScroll={scroll=>{savedRef.current={...savedRef.current,detailScroll:scroll}}}/>:<p className="a-shared-placeholder">Choose a promise to inspect its source, current facts and retained history.</p>}
  </div>
 </section>;
}
