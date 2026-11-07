import React,{useEffect,useRef,useState} from 'react';
import {RefreshCw,Layers,ArrowLeft} from 'lucide-react';
import {CopyDetail} from './navigation-details';
import './context-inspector.css';

const colors={instructions:'#8465d8',conversation:'#338bca',summary:'#269886',reminders:'#bc8540',tools:'#bc6098'};
const numeric=value=>Number.isFinite(value)&&value>=0;
const fmt=value=>numeric(value)?value.toLocaleString():'Unavailable';
const percent=(part,total)=>numeric(part)&&total>0?`${(100*part/total).toFixed(1)}%`:'Unavailable';
const sourceName=item=>{
 const label=item.sourceLabel||item.label||'Captured item',logical=label.split(' → ')[0];
 return logical.startsWith('__unified_')?label.split(/[\\/]/).at(-1):logical.includes('/')?logical.split(/[\\/]/).at(-1):logical;
};
const sourceSummary=item=>(item.sourceLabel||'').split(' → ')[0];
const evidenceText=(call,key,value)=>call?.limitEvidence?.[key]?.reason||(numeric(value)?'Captured with this request.':'Not captured for this request; current settings are not historical evidence.');
function Limit({call,name,field,value}){return <><dt>{name}</dt><dd><strong>{fmt(value)}</strong><small>{evidenceText(call,field,value)}</small></dd></>;}
// Client-local navigation only, never retained prompt bodies or shared state.
// Bounded to recently viewed chats; opening an expired exact request may fail.
const selections=new Map();
function Share({value,total,label,color}){
 return <div className="a-context-share"><strong>{percent(value,total)}</strong><span>{label}</span><div className="a-context-track" aria-hidden="true"><i style={{width:numeric(value)&&total>0?`${100*value/total}%`:'0%',background:color}}/></div></div>;
}

export function ContextEntry({act,session}){
 if(!session)return null;
 return <button className="a-context-entry" type="button" onClick={()=>openContextInspector(act,session)}><Layers/><span><strong>Context inspector</strong><small>Last observed request · usage & contents</small></span></button>;
}
export const openContextInspector=(act,session)=>act('canvas.show',{kind:'context-inspector',sessionId:session.id});

export function ContextInspector({session,act,open}){
 const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[category,setCategory]=useState(null),[query,setQuery]=useState(''),[selected,setSelected]=useState(null),[body,setBody]=useState(null),[bodyBusy,setBodyBusy]=useState(false),[bodyError,setBodyError]=useState('');
 const requestEpoch=useRef(0),bodyEpoch=useRef(0),readerTitle=useRef(null),categoryButton=useRef(null),search=useRef(null),initial=useRef(selections.get(session.id)),pageOffset=useRef(0);
 const read=async args=>{const response=await act('context.inspect',{sessionId:session.id,...args});if(response?.accepted===false)throw Error(response.error?.message||response.error||'Request inspection failed.');if(!response?.result)throw Error('No inspection result was returned.');return response.result};
 const refresh=async(callId,restore)=>{
  const epoch=++requestEpoch.current;++bodyEpoch.current;setBusy(true);setError('');setBodyBusy(false);
  try{const next=await read(callId?{callId}:{});if(epoch!==requestEpoch.current)return;setResult(next);setSelected(null);setBody(null);setBodyError('');setCategory(restore?.category||null);setQuery(restore?.query||'');pageOffset.current=0;if(restore?.itemId!=null){const item=next.items?.find(i=>i.id===restore.itemId);if(item)select(item,restore.offset||0,next.selected?.callId)}}
  catch(error){if(epoch===requestEpoch.current)setError(error.message)}
  finally{if(epoch===requestEpoch.current)setBusy(false)}
 };
 useEffect(()=>{refresh(initial.current?.callId,initial.current);return()=>{++requestEpoch.current;++bodyEpoch.current}},[session.id]);
 useEffect(()=>{if(!result?.selected)return;selections.delete(session.id);selections.set(session.id,{callId:result.selected.callId,category,query,itemId:selected?.id,offset:pageOffset.current});if(selections.size>16)selections.delete(selections.keys().next().value)},[session.id,result,category,query,selected,body]);
 useEffect(()=>{const title=readerTitle.current;if(selected&&title&&title.closest('.a-context-inspector')?.clientWidth<650){title.focus({preventScroll:true});title.scrollIntoView({block:'start',behavior:'instant'})}},[selected?.id]);
 const select=async(item,offset=0,exactCallId)=>{
  const epoch=++bodyEpoch.current,callId=exactCallId||result?.selected?.callId;pageOffset.current=offset;
  setSelected(item);setBody(null);setBodyError('');setBodyBusy(!!item.bodyAvailable);
  if(!item.bodyAvailable){setBodyBusy(false);return}
  try{const next=await read({callId,itemId:item.id,offset});if(epoch===bodyEpoch.current){setBody(next);readerTitle.current?.focus({preventScroll:true})}}
  catch(error){if(epoch===bodyEpoch.current)setBodyError(error.message)}
  finally{if(epoch===bodyEpoch.current)setBodyBusy(false)}
 };
 const choose=id=>{setCategory(id);setSelected(null);setBody(null);++bodyEpoch.current;setBodyBusy(false);setBodyError('')};
 const back=()=>{setSelected(null);setBody(null);++bodyEpoch.current;setBodyBusy(false);requestAnimationFrame(()=>categoryButton.current?.focus())};
 const manifest=result?.manifest||{},call=result?.selected,limits=call?.limits||{},usage=call?.usage||{},total=manifest.estimatedTextTokens,categories=manifest.categories||[],items=result?.items||[],group=categories.find(c=>c.id===category),itemGroup=categories.find(c=>c.id===selected?.category);
 const shown=items.filter(i=>(!category||i.category===category)&&(!query||`${i.label} ${i.sourceLabel||''}`.toLowerCase().includes(query.toLowerCase())));
 const input=usage.grossInputTokens??usage.inputTokens,window=limits.modelWindow,reserve=limits.reservedOutput,remaining=numeric(window)&&numeric(input)&&numeric(reserve)?Math.max(0,window-input-reserve):null;
 return <div className="a-context-inspector" data-selected-call={call?.callId||''} onKeyDown={event=>{if(event.key==='/'&&!['INPUT','TEXTAREA'].includes(event.target.tagName)){event.preventDefault();if(selected&&event.currentTarget.clientWidth<650){back();requestAnimationFrame(()=>search.current?.focus())}else search.current?.focus()}}}>
  <header className="a-context-top"><div><p className="a-context-eyebrow">MODEL INPUT · READ ONLY</p><p>Explore a captured host request, not current files, unsent drafts or all saved history.</p></div><button type="button" className="a-soft" onClick={()=>refresh()} disabled={busy}><RefreshCw/>{busy?'Refreshing…':'Refresh'}</button></header>
  {error&&<p role="alert">{error} <button className="a-link" onClick={()=>refresh()}>Try again</button></p>}
  {!result&&busy&&<p role="status">Reading the retained request observation…</p>}
  {result&&<>
   <div className="a-context-request"><label>Request<select aria-label="Observed request" value={call?.callId||''} onChange={event=>refresh(event.target.value)} disabled={busy}><option value="">Last completed foreground request</option>{(result.requests||[]).map(r=><option value={r.callId} key={r.callId}>{new Date(r.startedAt*1000).toLocaleTimeString()} · {r.phase} · {r.model||'unknown model'}</option>)}</select></label><small>{result.boundary}</small></div>
   {!call?<section className="a-context-box" role="status"><h3>No completed request observation</h3><p>{result.reason||'This runtime has not retained a completed foreground request. Unknown is not zero; saved history cannot prove what was sent.'}</p>{result.latest&&<p>Latest attempt: {result.latest.phase} · {new Date(result.latest.startedAt*1000).toLocaleString()}</p>}<p>Opening this view does not start a model or enable recording.</p></section>:<>
    <section className="a-context-box a-context-capacity" aria-label="Request capacity"><p className="a-context-eyebrow">LAST OBSERVED · {call.phase.toUpperCase()}</p><div className="a-context-metric"><strong>{fmt(input)}</strong><span>input tokens <small>provider reported</small></span></div><p>{call.model||'Unknown model'} · {call.provider||'Unknown provider'} · {new Date(call.startedAt*1000).toLocaleString()}</p><code className="a-context-identity">{call.callId}</code>
     {result.latest?.callId!==call.callId&&<p className="a-context-notice">A newer request is {result.latest.phase}. This view remains bound to the selected request.</p>}
     {numeric(window)?<>{numeric(input)&&window>0&&<div className="a-context-capacity-track" role="img" aria-label={`${fmt(input)} input tokens of ${fmt(window)} model window; output reserve ${fmt(reserve)}; remaining ${fmt(remaining)}`}><i style={{width:`${Math.min(100,100*input/window)}%`}}/>{numeric(reserve)&&<b style={{width:`${Math.max(0,Math.min(100-100*input/window,100*reserve/window))}%`}}/>}</div>}<p>Model window {fmt(window)} · reserved output {fmt(reserve)} · remaining window {fmt(remaining)}</p>{!numeric(input)&&<p>Input usage unavailable. No capacity percentage is inferred.</p>}</>:<p>Model window: unavailable. No capacity percentage is inferred.</p>}
     <small>Cached input {fmt(usage.cacheReadTokens)} tokens · cached tokens still occupy context.</small>
     {!numeric(window)&&<small>{evidenceText(call,'modelWindow',window)}</small>}
     <details><summary>Limits & counting evidence</summary><dl className="a-context-facts"><Limit call={call} name="Model context window" field="modelWindow" value={window}/><Limit call={call} name="Request input allowance" field="requestInputAllowance" value={limits.requestInputAllowance}/><Limit call={call} name="Reserved output" field="reservedOutput" value={reserve}/><Limit call={call} name="Configured context budget" field="configuredContextBudget" value={limits.configuredContextBudget}/><Limit call={call} name="Compaction threshold" field="compactionThreshold" value={limits.compactionThreshold}/><Limit call={call} name="Existing provider count receipt" field="providerCount" value={call.budget?.measurement?.input_tokens}/><Limit call={call} name="Existing provider fit estimate" field="providerEstimate" value={call.budget?.estimated_input_tokens}/></dl><p>Provider counts, fit estimates and reported usage are separate evidence. No count endpoint is called by this inspector.</p></details>
    </section>
    <details className="a-context-method"><summary>How are sizes measured?</summary><p>Colored proportions use {manifest.method||'a local text heuristic'}. Each share uses the sum of known text estimates—not the provider input total. Non-English text and code can tokenize differently. This is not a model tokenizer.</p><p>Captured wrapper paths are source labels, not proof of a filesystem version. Hashes describe recorded copies. No current source files are reread.</p><p>{result.retention} Body recording: {call.bodyRecording?'enabled at capture; current consent also applies':'off at capture; only generic noncontent measurements retained'}.</p></details>
    {categories.length>0?<section className="a-context-box" aria-label="Text composition"><div className="a-context-composition-head"><div><h3>Text composition</h3><p>Denominator: {fmt(total)} estimated text tokens</p></div><strong>≈ {fmt(total)}<small>local heuristic</small></strong></div>
     <div className="a-context-stack" aria-label="Estimated category proportions">{categories.filter(c=>c.tokens>0&&total>0).map(c=><button type="button" key={c.id} title={`${c.label}: approximately ${fmt(c.tokens)} tokens, ${percent(c.tokens,total)} of known text`} aria-label={`${c.label}: ${percent(c.tokens,total)} of known text`} onClick={()=>choose(c.id)} style={{width:`${100*c.tokens/total}%`,backgroundColor:colors[c.id]}}/>)}</div>
     <div className="a-context-legend">{categories.map(c=><button type="button" key={c.id} aria-pressed={category===c.id} onClick={()=>choose(c.id)}><i aria-hidden="true" style={{background:colors[c.id]}}/><span>{c.label}<small>≈ {fmt(c.tokens)} · {percent(c.tokens,total)} of known text{c.unknownItems>0?` · ${c.unknownItems} unsized`:''}</small></span></button>)}</div>
     <div className="a-context-unknown"><strong>Outside the estimate · unscaled</strong><p>{manifest.unscaled}</p>{manifest.bounded&&<p>The observation reached a size/item bound; some context is omitted.</p>}</div>
    </section>:<p className="a-context-notice">{manifest.reason||'Category attribution is unavailable for this request.'}</p>}
    {categories.length>0&&<div className="a-context-explore" data-item-selected={!!selected}>
     <section className="a-context-box a-context-nav" aria-label="Context categories and items"><nav aria-label="Context breadcrumb"><button type="button" className="a-link" onClick={()=>choose(null)}>All categories</button>{group&&<> / {group.label}</>}</nav><h3>{group?.label||'Choose a category'}</h3><label>Find item or source<input ref={search} type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="Search names and recorded source labels…"/></label><p role="status">{shown.length} matching items · percentages keep the same denominator</p>
      {!category&&!query?categories.map(c=><button type="button" className="a-context-category" key={c.id} onClick={()=>choose(c.id)}><i style={{background:colors[c.id]}}/><span>{c.label}<small>{c.items} items · {c.unknownItems} unsized</small></span><strong>≈ {fmt(c.tokens)}</strong></button>):shown.length?shown.map((i,index)=><button ref={index===0?categoryButton:undefined} type="button" key={i.id} className="a-context-item" aria-pressed={selected?.id===i.id} onClick={()=>select(i)}><span>{sourceName(i)}</span>{i.sourceLabel&&<small className="a-context-source-summary" title={sourceSummary(i)}>{sourceSummary(i)}</small>}<small>{numeric(i.tokens)?`≈ ${fmt(i.tokens)} tokens · ${percent(i.tokens,total)} of known text`:'Size unavailable'}{!i.bodyAvailable?' · content unavailable':''}</small><div className="a-context-track" aria-hidden="true"><i style={{background:colors[i.category],width:numeric(i.tokens)&&total>0?`${100*i.tokens/total}%`:'0%'}}/></div></button>):<p>No matching items. Try another name.</p>}
     </section>
     <article className="a-context-box a-context-reader" aria-label="Captured item reader">{!selected?<div className="a-context-reader-empty"><Layers/><h3>Choose where to look closer</h3><p>Open a category, then an item. Read only the captured text you select.</p><p>Unknown sizes are never presented as zero.</p></div>:<>
      <button type="button" className="a-link" onClick={back}><ArrowLeft/>Back to items</button><p className="a-context-eyebrow">{itemGroup?.label}</p><h3 ref={readerTitle} tabIndex="-1">{sourceName(selected)}</h3>{selected.sourceLabel&&<p className="a-context-source-summary" title={sourceSummary(selected)}>{sourceSummary(selected)}</p>}<p>{numeric(selected.tokens)?`≈ ${fmt(selected.tokens)} tokens · local heuristic`:'Token size unavailable'}</p>
      <Share value={selected.tokens} total={itemGroup?.tokens} label="of known text in this category" color={colors[selected.category]}/><Share value={selected.tokens} total={total} label="of all known text in this request" color={colors[selected.category]}/><p>Actual full-request share: unavailable</p>
      {bodyBusy&&<p role="status">Reading selected captured text…</p>}{bodyError&&<p role="alert">{bodyError} <button type="button" className="a-link" onClick={()=>select(selected)}>Retry selected item</button></p>}
      {!selected.bodyAvailable&&<p className="a-context-notice">{selected.bodyUnavailable} No current file is substituted.</p>}
      {body?.text!=null&&<><pre tabIndex="0" aria-label="Captured text">{body.text}</pre><div className="a-context-pagination"><button type="button" className="a-soft" disabled={!body.offset} onClick={()=>select(selected,Math.max(0,body.offset-6000))}>Previous page</button><small>{body.offset+1}–{body.endOffset} of {body.characters} characters</small><button type="button" className="a-soft" disabled={body.nextOffset==null} onClick={()=>select(selected,body.nextOffset)}>Next page</button></div></>}{body&&body.text==null&&<p role="status">{body.reason}</p>}
      <details><summary>Provenance, bytes & measurement</summary>{selected.sourceLabel&&<CopyDetail label="Recorded source label" value={selected.sourceLabel}/>}<dl className="a-context-facts"><dt>Source evidence</dt><dd>{selected.sourceKind||'Observed request position, not a current file'}</dd><dt>Captured copy SHA-256</dt><dd>{selected.sha256||'Unavailable'}</dd><dt>Captured bytes</dt><dd>{fmt(selected.bytes)}</dd><dt>Text characters</dt><dd>{fmt(selected.characters)}</dd><dt>Method</dt><dd>{selected.method==='local_heuristic'?manifest.method:'Unavailable for this item'}</dd></dl>{selected.note&&<p>{selected.note}</p>}</details>
     </>}</article>
    </div>}
   </>}
   <footer className="a-context-footer"><span>Read only · no provider calls</span><button type="button" className="a-link" onClick={()=>open('chat-context')}>Context & recovery settings</button></footer>
  </>}
 </div>;
}
