import React,{useEffect,useRef,useState} from 'react';

export function RecallSettings({session,act}){
 const [query,setQuery]=useState(''),[scope,setScope]=useState('workspace'),[coverage,setCoverage]=useState(null),[results,setResults]=useState(null),[source,setSource]=useState(null),[notes,setNotes]=useState([]),[nextNotes,setNextNotes]=useState(null),[text,setText]=useState(''),[memoryScope,setMemoryScope]=useState('task'),[memoryView,setMemoryView]=useState('available'),[editing,setEditing]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[personalization,setPersonalization]=useState(null);
 const alive=useRef(true),inFlight=useRef(false),statusRequested=useRef(null),sid=session?.id,activeSid=useRef(sid);
 activeSid.current=sid;
 useEffect(()=>{statusRequested.current=null;setResults(null);setSource(null);setNotes([]);setNextNotes(null);setEditing(null);setText('');setCoverage(null);setPersonalization(null);setError('')},[sid]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 async function run(action,args,apply){
  if(inFlight.current)return;
  inFlight.current=true;setBusy(true);setError('');
  try{const response=await act(action,{sessionId:sid,...args});if(!response?.accepted)throw Error('The request was not accepted.');if(alive.current&&activeSid.current===sid)apply?.(response.result);return response.result}
  catch(e){if(alive.current)setError(e.message)}finally{inFlight.current=false;if(alive.current)setBusy(false)}
 }
 useEffect(()=>{
  if(!sid||coverage?.status!=='indexing')return;
  const timer=setInterval(()=>run('recall.status',{},setCoverage),750);
  return()=>clearInterval(timer);
 },[sid,coverage]);
 useEffect(()=>{if(sid&&!busy&&!inFlight.current&&statusRequested.current!==sid){statusRequested.current=sid;run('memory.status',{},setPersonalization)}},[sid,busy]);
 useEffect(()=>{if(!personalization?.running)return;const timer=setInterval(()=>run('memory.status',{},setPersonalization),750);return()=>clearInterval(timer)},[sid,personalization?.running]);
 const configure=patch=>run('memory.configure',{expectedRevision:personalization.settings.revision,...patch},setPersonalization);
 const search=(offset=0)=>run('recall.search',{query,scope,offset},value=>{setResults(old=>offset?{...value,items:[...old.items,...value.items]}:value);setCoverage(value.coverage)});
 const list=(offset=0,scope=memoryView)=>run('memory.list',{scope,offset},value=>{setNotes(old=>offset?[...old,...value.items]:value.items);setNextNotes(value.nextOffset)});
 const read=row=>run('recall.read',{sourceSessionId:row.sessionId,messageId:row.messageId,sourceRevision:row.sourceRevision},setSource);
 const save=async event=>{event.preventDefault();const value=await run(editing?'memory.update':'memory.create',{...(editing?{id:editing.id,expectedRevision:editing.revision,allScopes:memoryView==='all'}:{scope:memoryScope}),text});if(value){setText('');setEditing(null);await list()}};
 if(!sid)return <p>Open a conversation to manage memory for its workspace. Your existing memory settings are kept.</p>;
 return <>
  <section className="a-settings-section" aria-label="Automatic memory"><h3>Automatic memory</h3><p>Workspace references can capture your stated preferences, settled decisions, and approaches you said worked. Review their exact sources and correct them when needed.</p>
   {personalization&&<>
    <label><input type="checkbox" checked={personalization.settings.contribute} disabled={busy} data-action="memory.configure" onChange={e=>configure({contribute:e.target.checked})}/>Save useful details from this workspace</label>
    <p>Saving memory uses your chosen AI models and may incur provider charges. Review saved details to correct mistakes.</p>
    <label><input type="checkbox" checked={personalization.settings.use} disabled={busy} data-action="memory.configure" onChange={e=>configure({use:e.target.checked})}/>Use saved memory in this workspace</label>
    <label><input type="checkbox" checked={personalization.settings.excludedSessions.includes(sid)} disabled={busy} data-action="memory.configure" onChange={e=>configure({excludedSessions:e.target.checked?[...personalization.settings.excludedSessions,sid]:personalization.settings.excludedSessions.filter(id=>id!==sid)})}/>Exclude this conversation from contribution and automatic use</label>
    <details className="a-everyday-disclosure"><summary>Memory activity & limits</summary><div><p>Up to {personalization.settings.maxCallsPerDay} model attempts per day, 16,000 source characters and 4,096 output tokens per attempt. Only attributable user statements qualify.</p><label htmlFor="memory-call-limit">Maximum model attempts per day</label><input id="memory-call-limit" type="number" min={1} max={10} disabled={busy} value={personalization.settings.maxCallsPerDay} onChange={e=>{const n=Number(e.target.value);if(n>=1&&n<=10)configure({maxCallsPerDay:n})}}/>
    <button type="button" className="a-soft" disabled={busy||personalization.running||!personalization.settings.contribute} data-action="memory.consolidate" onClick={()=>run('memory.consolidate',{},setPersonalization)}>Consolidate eligible past conversations</button>
    <p role="status">{personalization.running?'Consolidating…':'No consolidation running'}. Saved references remain inspectable when contribution or use is off.</p>
    {personalization.activity&&<p>{personalization.activity.status}{personalization.activity.reason?' · '+personalization.activity.reason:''}</p>}
    {personalization.attempts.map(row=><p key={row.id}>Conversation {row.sessionId}: {row.status}{row.saved?` · ${row.saved.length} saved`:''}{row.reason?` · ${row.reason}`:''}{row.model?` · ${row.model}`:''}</p>)}
    {personalization.lastContext&&<div aria-label="Last memory context"><strong>Last context supplied</strong>{personalization.lastContext.items.length?personalization.lastContext.items.map(row=><p key={row.id}>{row.id} · revision {row.revision} · matched {row.matchTerms.join(', ')}</p>):<p>No relevant memory was selected.</p>}</div>}</div></details>
   </>}
   <button type="button" className="a-soft" disabled={busy} data-action="memory.status" onClick={()=>run('memory.status',{},setPersonalization)}>Refresh memory activity</button>
  </section>
  <details className="a-everyday-disclosure"><summary>Find past work</summary><section className="a-settings-section" aria-label="Indexed recall"><p>Search saved conversation text. Reading a result leaves your current conversation and draft in place.</p>
   <button type="button" className="a-soft" disabled={busy||coverage?.status==='indexing'} data-action="recall.refresh" onClick={()=>run('recall.refresh',{},setCoverage)}>Update search index</button>
   {coverage&&<p role="status">{coverage.status} · {coverage.indexed} of {coverage.total??'unknown'} conversations indexed{coverage.errorCount?` · ${coverage.errorCount} unavailable`:''}. {coverage.checkedAt?'Last checked '+new Date(coverage.checkedAt*1000).toLocaleString():''}</p>}
   <form onSubmit={e=>{e.preventDefault();search()}}><label htmlFor="recall-query">Search conversations, tasks and outputs</label><input id="recall-query" value={query} maxLength={500} onChange={e=>setQuery(e.target.value)}/><label htmlFor="recall-scope">Search scope</label><select id="recall-scope" value={scope} onChange={e=>setScope(e.target.value)}><option value="task">This conversation</option><option value="workspace">This workspace</option><option value="all">All registered conversations</option></select><button type="submit" className="a-primary" data-action="recall.search" disabled={busy||!query.trim()}>Search past work</button></form>
   {results&&<><p>Matches contain all search terms. Coverage and source dates matter; an empty partial search does not prove nothing exists.</p>{results.items.map(row=><div className="a-selection-card" key={row.sessionId+row.messageId}><strong>{row.session.title}</strong><p>{row.snippet}</p><small>{row.role}{row.via==='call'?' · voice':''}</small><button type="button" className="a-soft" disabled={busy} data-action="recall.read" onClick={()=>read(row)}>Read source</button></div>)}{results.nextOffset!==null&&<button type="button" className="a-soft" disabled={busy} onClick={()=>search(results.nextOffset)}>More matches</button>}</>}
   {source&&<div className="a-selection-card" aria-label="Verified source"><strong>Source verified for this read</strong><pre className="a-state-view" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{source.text}</pre><small>Conversation {source.sessionId} · {source.sourceKind==='message'?'message':'record'} {source.recordId||source.messageId}</small>{source.nextOffset!=null&&<button type="button" className="a-soft" disabled={busy} data-action="recall.read" onClick={()=>run('recall.read',{sourceSessionId:source.sessionId,messageId:source.messageId,sourceRevision:source.sourceRevision,offset:source.nextOffset},value=>setSource(old=>({...value,text:old.text+value.text})))}>Read more</button>}</div>}
  </section></details>
  <section className="a-settings-section" aria-label="Saved memory"><h3>Saved memory</h3><p>Notes are reference material. They do not grant permission or start work. Automatic contribution and use are separately controlled above.</p>
   <label htmlFor="memory-view">Inspect scope</label><select id="memory-view" disabled={busy} value={memoryView} onChange={e=>{setMemoryView(e.target.value);list(0,e.target.value)}}><option value="available">Notes for this work</option><option value="all">All saved notes, including removed chats</option></select>
   <button type="button" className="a-soft" disabled={busy} data-action="memory.list" onClick={()=>list()}>Inspect saved memory</button>
   <form onSubmit={save}><label htmlFor="memory-text">Memory note</label><textarea id="memory-text" disabled={busy} value={text} maxLength={8000} rows={4} onChange={e=>setText(e.target.value)}/>{!editing&&<><label htmlFor="memory-scope">Remember for</label><select id="memory-scope" disabled={busy} value={memoryScope} onChange={e=>setMemoryScope(e.target.value)}><option value="task">This conversation</option><option value="workspace">This workspace</option><option value="global">All work</option></select></>}<button type="submit" className="a-primary" data-action={editing?'memory.update':'memory.create'} disabled={busy||!text.trim()}>{editing?'Save correction':'Save memory'}</button>{editing&&<button type="button" className="a-soft" onClick={()=>{setEditing(null);setText('')}}>Cancel edit</button>}</form>
   {notes.map(note=><div key={note.id} className="a-selection-card"><strong>{note.scope} · revision {note.revision}</strong>{note.supersededBy&&<p>Superseded by {note.supersededBy}; excluded from automatic context.</p>}<p style={{whiteSpace:'pre-wrap'}}>{note.text}</p><small>Saved through {note.provenance.origin}{note.provenance.wording?' · '+note.provenance.wording:''}</small>{note.source?.quote&&<blockquote>{note.source.quote}</blockquote>}<div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="memory.read" onClick={()=>run('memory.read',{id:note.id,allScopes:memoryView==='all'},value=>{setEditing(value);setText(value.text)})}>Inspect or correct</button><button type="button" className="a-soft" disabled={busy} data-action="memory.delete" onClick={async()=>{if(await run('memory.delete',{id:note.id,expectedRevision:note.revision,allScopes:memoryView==='all'})){if(editing?.id===note.id){setEditing(null);setText('')}await list()}}}>Delete memory</button></div>{note.source&&<button type="button" className="a-link" disabled={busy} onClick={()=>note.source.kind==='attributed-user'?run('memory.source',{id:note.id},setSource):read(note.source)}>Read original source</button>}</div>)}
   {nextNotes!==null&&<button type="button" className="a-soft" disabled={busy} onClick={()=>list(nextNotes)}>More saved notes</button>}
   {editing&&<details><summary>Retained revisions ({editing.versions.length}, up to 50)</summary>{editing.versions.map(version=><p key={version.revision}>Revision {version.revision}: {version.text}</p>)}</details>}
   <p>Deleting removes the saved note and its retained revisions. Original chats and existing private backups remain.</p>
  </section>
  {error&&<p role="alert" className="a-danger">{error}</p>}
 </>;
}
