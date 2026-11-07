import React,{useEffect,useId,useRef,useState} from 'react';
import {RefreshCw,Layers,ArrowLeft,ChevronDown,ChevronUp} from 'lucide-react';
import {CopyDetail} from './navigation-details';
import {CanvasDetailsButton} from './canvas-controls';
import './context-inspector.css';

const colors={instructions:'#8465d8',conversation:'#338bca',summary:'#269886',reminders:'#bc8540',tools:'#bc6098'};
const numeric=value=>Number.isFinite(value)&&value>=0;
const fmt=value=>numeric(value)?value.toLocaleString():'Unavailable';
const percent=(part,total)=>numeric(part)&&total>0?`${(100*part/total).toFixed(1)}%`:'Unavailable';
const sourceName=item=>{
 const label=item.sourceLabel||item.label||'Captured item',logical=label.split(' → ')[0].replace(/^@[^:]+:/,'');
 return logical.startsWith('__unified_')?label.split(/[\\/]/).at(-1):logical.includes('/')?logical.split(/[\\/]/).at(-1):logical;
};
const sourceSummary=item=>(item.sourceLabel||'').split(' → ')[0];
const evidenceText=(call,key,value)=>call?.limitEvidence?.[key]?.reason||(numeric(value)?'Captured with this request.':'Not captured for this request; current settings are not historical evidence.');
export function ContextLimits({call}){
 const limits=call.limits||{},budget=call.budget||{},groups=new Map();
 const labels={not_captured:'Not captured',not_reported:'Not reported',unsupported:'Unsupported',automatic:'Automatic',binding_bounded:'Association bounded',binding_unsupported:'Association unsupported',ambiguous:'Ambiguous',request_changed:'Request changed',unmatched_preflight:'Not associated'};
 for(const [field,name,value] of [
  ['modelWindow','Model window',limits.modelWindow],['requestInputAllowance','Input allowance',limits.requestInputAllowance],
  ['reservedOutput','Output reserve',limits.reservedOutput],['configuredContextBudget','Configured budget',limits.configuredContextBudget],
  ['compactionThreshold','Compaction threshold',limits.compactionThreshold],['providerCount','Provider count receipt',budget.measurement?.input_tokens],
  ['providerEstimate','Provider fit estimate',budget.estimated_input_tokens],
 ]){
  const evidence=call.limitEvidence?.[field],reason=evidenceText(call,field,value);
  const status=numeric(value)?fmt(value):labels[evidence?.status]||(['requestInputAllowance','providerCount','providerEstimate'].includes(field)?labels[call.budgetStatus]:null)||'Unknown';
  const key=numeric(value)?field:`${status}:${reason}`;
  const group=groups.get(key)||{names:[],status,reason,source:evidence?.source};group.names.push(name);groups.set(key,group);
 }
 return <dl className="a-context-facts">{[...groups].map(([key,group])=><React.Fragment key={key}><dt>{group.names.join(' · ')}</dt><dd><strong>{group.status}</strong><small>{group.reason}{group.source&&<> Source: {group.source}.</>}</small></dd></React.Fragment>)}</dl>;
}
// Client-local navigation only, never retained prompt bodies or shared state.
// Bounded to recently viewed chats; opening an expired exact request may fail.
const selections=new Map();
function Share({value,total,label}){
 return <span className="a-context-share"><strong>{percent(value,total)}</strong> {label}</span>;
}

export function ContextEntry({act,session}){
 if(!session)return null;
 return <button className="a-context-entry" type="button" onClick={()=>openContextInspector(act,session)}><Layers/><span><strong>Context inspector</strong><small>Last observed request · usage & contents</small></span></button>;
}
export const openContextInspector=(act,session)=>act('canvas.show',{kind:'context-inspector',sessionId:session.id});

export function ContextInspector({session,act,open}){
 const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[category,setCategory]=useState(null),[query,setQuery]=useState(''),[selected,setSelected]=useState(null),[body,setBody]=useState(null),[bodyBusy,setBodyBusy]=useState(false),[bodyError,setBodyError]=useState('');
 const [summaryExpanded,setSummaryExpanded]=useState(false),[measureExpanded,setMeasureExpanded]=useState(false),summaryId=useId(),measureId=useId();
 const requestEpoch=useRef(0),requestPending=useRef(false),bodyEpoch=useRef(0),readerTitle=useRef(null),reader=useRef(null),itemList=useRef(null),search=useRef(null),initial=useRef(selections.get(session.id)),pageOffset=useRef(0),returnItem=useRef(null);
 const read=async args=>{const response=await act('context.inspect',{sessionId:session.id,...args});if(response?.accepted===false)throw Error(response.error?.message||response.error||'Request inspection failed.');if(!response?.result)throw Error('No inspection result was returned.');return response.result};
 const refresh=async(callId,restore)=>{
  if(requestPending.current)return;requestPending.current=true;
  const epoch=++requestEpoch.current;++bodyEpoch.current;setBusy(true);setError('');setBodyBusy(false);
  try{const next=await read(callId?{callId}:{});if(epoch!==requestEpoch.current)return;setResult(next);setSelected(null);setBody(null);setBodyError('');setCategory(restore?.category||null);setQuery(restore?.query||'');pageOffset.current=0;if(restore?.itemId!=null){const item=next.items?.find(i=>i.id===restore.itemId);if(item)select(item,restore.offset||0,next.selected?.callId)}}
  catch(error){if(epoch===requestEpoch.current)setError(error.message)}
  finally{if(epoch===requestEpoch.current){requestPending.current=false;setBusy(false)}}
 };
 useEffect(()=>{refresh(initial.current?.callId,initial.current);return()=>{++requestEpoch.current;++bodyEpoch.current}},[session.id]);
 useEffect(()=>{if(!result?.selected)return;selections.delete(session.id);selections.set(session.id,{callId:result.selected.callId,category,query,itemId:selected?.id,offset:pageOffset.current});if(selections.size>16)selections.delete(selections.keys().next().value)},[session.id,result,category,query,selected,body]);
 useEffect(()=>{if(selected){reader.current?.scrollTo({top:0});readerTitle.current?.focus({preventScroll:true})}},[selected?.id,body?.offset]);
 useEffect(()=>{itemList.current?.scrollTo({top:0})},[result?.selected?.callId,category,query]);
 useEffect(()=>{setSummaryExpanded(false);setMeasureExpanded(false)},[selected?.id]);
 const select=async(item,offset=0,exactCallId)=>{
  const epoch=++bodyEpoch.current,callId=exactCallId||result?.selected?.callId;pageOffset.current=offset;
  setSelected(item);setBody(null);setBodyError('');setBodyBusy(!!item.bodyAvailable);
  if(!item.bodyAvailable){setBodyBusy(false);return}
  try{const next=await read({callId,itemId:item.id,offset});if(epoch===bodyEpoch.current){setBody(next);readerTitle.current?.focus({preventScroll:true})}}
  catch(error){if(epoch===bodyEpoch.current)setBodyError(error.message)}
  finally{if(epoch===bodyEpoch.current)setBodyBusy(false)}
 };
 const choose=id=>{setCategory(id);setSelected(null);setBody(null);++bodyEpoch.current;setBodyBusy(false);setBodyError('')};
 const back=()=>{const id=selected?.id;setSelected(null);setBody(null);++bodyEpoch.current;setBodyBusy(false);requestAnimationFrame(()=>{
  const target=returnItem.current?.isConnected&&returnItem.current.dataset.itemId===String(id)?returnItem.current:[...(itemList.current?.querySelectorAll('[data-item-id]')||[])].find(node=>node.dataset.itemId===String(id));
  (target||search.current)?.focus({preventScroll:true});
  target?.scrollIntoView({block:'nearest',behavior:'instant'});
 })};
 const manifest=result?.manifest||{},call=result?.selected,limits=call?.limits||{},usage=call?.usage||{},total=manifest.estimatedTextTokens,categories=manifest.categories||[],items=result?.items||[],group=categories.find(c=>c.id===category),itemGroup=categories.find(c=>c.id===selected?.category);
 const shown=items.filter(i=>(!category||i.category===category)&&(!query||`${i.label} ${i.sourceLabel||''}`.toLowerCase().includes(query.toLowerCase())));
 const input=usage.grossInputTokens??usage.inputTokens,window=limits.modelWindow,reserve=limits.reservedOutput,knownWindow=numeric(window)&&window>0,remaining=knownWindow&&numeric(input)&&numeric(reserve)?window-input-reserve:null;
 const capacityBalance=remaining==null?'remaining unknown':remaining<0?`${fmt(-remaining)} over window`:`${fmt(remaining)} remaining`;
 return <div className="a-context-inspector" data-selected-call={call?.callId||''} data-reading={!!selected} onKeyDown={event=>{if(event.key==='/'&&!['INPUT','TEXTAREA'].includes(event.target.tagName)){event.preventDefault();if(selected&&event.currentTarget.clientWidth<650){back();requestAnimationFrame(()=>search.current?.focus())}else search.current?.focus()}}}>
  <div className="a-context-summary" data-expanded={summaryExpanded}>
  {selected&&<button type="button" className="a-context-summary-toggle" aria-label={summaryExpanded?'Collapse request summary':'Expand request summary'} aria-describedby={`${summaryId}-brief`} aria-expanded={summaryExpanded} aria-controls={summaryId} onClick={()=>setSummaryExpanded(value=>!value)}><span id={`${summaryId}-brief`}>{numeric(input)?`${fmt(input)} input`:'Input unknown'} · {knownWindow?`${fmt(window)} window`:'window unknown'}</span>{summaryExpanded?<ChevronUp aria-hidden="true"/>:<ChevronDown aria-hidden="true"/>}</button>}
  <div id={summaryId} className="a-context-summary-full">
  <header className="a-context-top"><label className="a-context-request"><span>Observed request</span><select aria-label="Observed request" value={call?.callId||''} onChange={event=>{if(requestPending.current){event.target.value=call?.callId||'';return}refresh(event.target.value)}} aria-disabled={busy} aria-busy={busy}><option value="">Last completed foreground request</option>{(result?.requests||[]).map(r=><option value={r.callId} key={r.callId}>{new Date(r.startedAt*1000).toLocaleTimeString()} · {r.phase} · {r.model||'unknown model'}</option>)}</select></label><button type="button" className="a-icon" aria-label="Refresh" title="Refresh retained observation — no model call" onClick={()=>refresh()} aria-disabled={busy} aria-busy={busy}><RefreshCw/></button><CanvasDetailsButton/></header>
  {error&&<p role="alert">{error} <button className="a-link" onClick={()=>refresh()}>Try again</button></p>}
  {!result&&busy&&<p role="status">Reading the retained request observation…</p>}
  {call&&<>
   <section className="a-context-capacity" aria-label="Request capacity">
    <div className="a-context-metric"><strong>{numeric(input)?fmt(input):'Unknown'}</strong><span>input tokens<small>provider reported</small></span></div>
    <div className="a-context-window" data-window-known={knownWindow}>
     {knownWindow?<><strong>Model window {fmt(window)}</strong>{numeric(input)?<><div className="a-context-capacity-track" role="img" aria-label={`${fmt(input)} reported input tokens of ${fmt(window)} model window; output reserve ${fmt(reserve)}; ${capacityBalance}`}><i style={{width:`${Math.min(100,100*input/window)}%`}}/>{numeric(reserve)&&<b style={{width:`${Math.max(0,Math.min(100-100*input/window,100*reserve/window))}%`}}/>}</div><small>{percent(input,window)} reported input · reserve {numeric(reserve)?fmt(reserve):'unknown'} · {capacityBalance}</small></>:<small>Input usage unavailable. No capacity percentage is inferred.</small>}</>:<><strong>Model window unknown</strong><small>No captured denominator — capacity cannot be calculated.</small></>}
    </div>
   </section>
   <details className="a-context-evidence"><summary>Request & measurement details{!knownWindow?' · limits incomplete':''}</summary>
    <p>{call.model||'Unknown model'} · {call.provider||'Unknown provider'} · {call.phase} · {new Date(call.startedAt*1000).toLocaleString()}</p><code className="a-context-identity">{call.callId}</code>
    {result.latest&&result.latest.callId!==call.callId&&<p>A newer request is {result.latest.phase}. This view remains bound to the selected request.</p>}
    <ContextLimits call={call}/>
    <p>Cached input {fmt(usage.cacheReadTokens)} tokens; cached tokens still occupy context. Reported input includes cache writes when supplied. The capacity comparison is reported usage plus a captured reserve, not an exact provider-wire count or fit guarantee.</p>
    <p>Counts, fit estimates and reported usage are separate evidence. This inspector calls no count endpoint.</p>
    <h3>Text composition</h3><p>≈ {fmt(total)} estimated text tokens · {manifest.method||'local heuristic'}. Category and item shares use known text, not provider input. Unknown content is unscaled, not zero.</p>
    <div className="a-context-stack" aria-label="Estimated category proportions">{categories.filter(c=>c.tokens>0&&total>0).map(c=><button type="button" key={c.id} title={`${c.label}: approximately ${fmt(c.tokens)} tokens, ${percent(c.tokens,total)} of known text`} aria-label={`${c.label}: ${percent(c.tokens,total)} of known text`} onClick={()=>choose(c.id)} style={{width:`${100*c.tokens/total}%`,backgroundColor:colors[c.id]}}/>)}</div>
    <div className="a-context-legend">{categories.map(c=><button type="button" key={c.id} aria-pressed={category===c.id} onClick={()=>choose(c.id)}><i aria-hidden="true" style={{background:colors[c.id]}}/><span>{c.label}<small>≈ {fmt(c.tokens)} · {percent(c.tokens,total)} of known text{c.unknownItems>0?` · ${c.unknownItems} unsized`:''}</small></span></button>)}</div>
    <p>{manifest.unscaled}</p>{manifest.bounded&&<p>The observation reached a size/item bound; some context is omitted.</p>}
    <p>{result.boundary} Captured source labels do not prove filesystem versions. No current files, drafts or saved history are reread.</p>
    <p>{result.retention} Body recording: {call.bodyRecording?'enabled at capture; current consent also applies':'off at capture; only generic noncontent measurements retained'}.</p>
   </details>
  </>}
  </div>
  {selected&&error&&<p className="a-context-summary-error" role="alert">{error} Expand request summary to retry.</p>}
  </div>
  {result&&!call&&<section className="a-context-empty" role="status"><h3>No completed request observation</h3><p>{result.reason||'This runtime has not retained a completed foreground request. Unknown is not zero; saved history cannot prove what was sent.'}</p>{result.latest&&<p>Latest attempt: {result.latest.phase} · {new Date(result.latest.startedAt*1000).toLocaleString()}</p>}<p>Opening this view does not start a model or enable recording.</p></section>}
  {call&&categories.length===0&&<p className="a-context-notice">{manifest.reason||'Category attribution is unavailable for this request.'}</p>}
  {call&&categories.length>0&&<div className="a-context-explore" data-item-selected={!!selected}>
     <section className="a-context-nav" aria-label="Context categories and items">
      <div className="a-context-nav-head"><nav aria-label="Context breadcrumb"><button type="button" className="a-link" onClick={()=>choose(null)}>All categories</button>{group&&<span> / {group.label}</span>}</nav><label>Find item or source<input ref={search} type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Search captured items"/></label><small role="status">{shown.length} {shown.length===1?'item':'items'} · shares of ≈ {fmt(total)} known text tokens</small></div>
      <div className="a-context-items" ref={itemList} tabIndex={0} role="region" aria-label="Captured items">
      {!category&&!query?categories.map(c=><button type="button" className="a-context-category" key={c.id} onClick={()=>choose(c.id)}><i style={{background:colors[c.id]}}/><span>{c.label}<small>{c.items} items{c.unknownItems>0?` · ${c.unknownItems} unsized`:''}</small></span><strong>{percent(c.tokens,total)}<small>known text</small></strong></button>):shown.length?shown.map(i=><button type="button" key={i.id} className="a-context-item" data-item-id={i.id} aria-pressed={selected?.id===i.id} onClick={event=>{returnItem.current=event.currentTarget;select(i)}}><span>{sourceName(i)}</span>{i.sourceLabel&&<small className="a-context-source-summary" title={sourceSummary(i)}>{sourceSummary(i)}</small>}<small>{numeric(i.tokens)?`≈ ${fmt(i.tokens)} tokens · ${percent(i.tokens,total)} of known text`:'Size unavailable'}{!i.bodyAvailable?' · content unavailable':''}</small></button>):<p>No matching items. Try another name.</p>}
      </div>
     </section>
     <article ref={reader} className="a-context-reader" role="region" tabIndex={0} aria-label="Captured item reader">{!selected?<div className="a-context-reader-empty"><Layers/><h3>Choose a captured item</h3><p>Open a category, then an item to read its captured text.</p><p>Unknown sizes are never presented as zero.</p></div>:<>
      <div className="a-context-reader-nav"><button type="button" className="a-link" onClick={back}><ArrowLeft/>Back to items</button><h3 ref={readerTitle} tabIndex="-1" title={sourceSummary(selected)||selected.label}>{sourceName(selected)}</h3></div>
      <div className="a-context-reader-metadata" data-expanded={measureExpanded}>
       <button type="button" className="a-context-measure-toggle a-link" aria-label={measureExpanded?'Hide item measurements':'Show item measurements'} aria-expanded={measureExpanded} aria-controls={measureId} onClick={()=>setMeasureExpanded(value=>!value)}>{numeric(selected.tokens)?`≈ ${fmt(selected.tokens)} tokens`:'Size unknown'} · measurements {measureExpanded?<ChevronUp aria-hidden="true"/>:<ChevronDown aria-hidden="true"/>}</button>
       <div id={measureId} className="a-context-measure-full">{selected.sourceLabel&&<p className="a-context-source-summary" title={sourceSummary(selected)}>{sourceSummary(selected)}</p>}<p className="a-context-reader-measure">{numeric(selected.tokens)?`≈ ${fmt(selected.tokens)} tokens · local heuristic`:'Token size unavailable'}</p>
       <div className="a-context-shares"><Share value={selected.tokens} total={itemGroup?.tokens} label="of known text in this category"/><Share value={selected.tokens} total={total} label="of all known text in this request"/></div></div>
      </div>
      {bodyBusy&&<p role="status">Reading selected captured text…</p>}{bodyError&&<p role="alert">{bodyError} <button type="button" className="a-link" onClick={()=>select(selected)}>Retry selected item</button></p>}
      {!selected.bodyAvailable&&<p className="a-context-notice">{selected.bodyUnavailable} No current file is substituted.</p>}
      {body?.text!=null&&<><pre aria-label="Captured text">{body.text}</pre>{(body.offset>0||body.nextOffset!=null)&&<div className="a-context-pagination"><button type="button" className="a-soft" disabled={!body.offset} onClick={()=>select(selected,Math.max(0,body.offset-6000))}>Previous page</button><small>{body.offset+1}–{body.endOffset} of {body.characters} characters</small><button type="button" className="a-soft" disabled={body.nextOffset==null} onClick={()=>select(selected,body.nextOffset)}>Next page</button></div>}</>}{body&&body.text==null&&<p role="status">{body.reason}</p>}
      <details><summary>Provenance, bytes & measurement</summary>{selected.sourceLabel&&<CopyDetail label="Recorded source label" value={selected.sourceLabel}/>}<dl className="a-context-facts"><dt>Source evidence</dt><dd>{selected.sourceKind||'Observed request position, not a current file'}</dd><dt>Captured copy SHA-256</dt><dd>{selected.sha256||'Unavailable'}</dd><dt>Captured bytes</dt><dd>{fmt(selected.bytes)}</dd><dt>Text characters</dt><dd>{fmt(selected.characters)}</dd><dt>Method</dt><dd>{selected.method==='local_heuristic'?manifest.method:'Unavailable for this item'}</dd></dl><p>Actual full-request share is unknown. Category and request shares above use only known text.</p>{selected.note&&<p>{selected.note}</p>}</details>
     </>}</article>
    </div>}
   <footer className="a-context-footer"><span>Read only · no provider calls</span><button type="button" className="a-link" onClick={()=>open('chat-context')}>Context settings</button></footer>
 </div>;
}
