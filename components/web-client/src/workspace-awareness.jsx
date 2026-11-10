import React,{useEffect,useId,useState} from 'react';

const textFor=intent=>(intent?.paths||[]).map(row=>row.path+(row.kind==='directory'&&!row.path.endsWith('/')?'/':'')).join('\n');

export function WorkspaceAwareness({dispatch,sessionId,context,refresh}){
 const key='amplifier-file-intent-draft:'+sessionId,fieldId=useId();
 const [draft,setDraft]=useState(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[inspected,setInspected]=useState(null);
 const [page,setPage]=useState(null),[offsets,setOffsets]=useState([0]),[paging,setPaging]=useState(false);
 const awareness=context.awareness,own=context.writeIntent;
 const shown=page||awareness;
 const displayPath=(row,peer)=>!peer.executionDirectoryTruncated&&row.path.startsWith(peer.executionDirectory+'/')?row.path.slice(peer.executionDirectory.length+1):row.path;
 useEffect(()=>{setPage(null);setOffsets([0])},[awareness]);
 const loaded=own?.sessionId===sessionId&&awareness?.own.sessionId===sessionId;
 const saveDraft=value=>{setDraft(value);try{if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key)}catch{setNotice('Your draft stays on this page, but this browser could not save it for reload.')}};
 useEffect(()=>{if(!draft)return;const prevent=event=>{event.preventDefault();event.returnValue=''};window.addEventListener('beforeunload',prevent);return()=>window.removeEventListener('beforeunload',prevent)},[draft]);
 const edit=text=>saveDraft({text,revision:draft?.revision??own.revision,executionRevision:draft?.executionRevision??awareness.own.executionRevision,id:crypto.randomUUID()});
 const save=async event=>{
  event.preventDefault();if(busy||!draft||!loaded)return;setBusy(true);setError('');setNotice('');
  try{
   const paths=draft.text.split('\n').map(path=>path.trim()).filter(Boolean).map(path=>({path,kind:path.endsWith('/')?'directory':'file'}));
   const response=await dispatch('coordination.intent',{sessionId,expectedRevision:draft.revision,expectedExecutionRevision:draft.executionRevision,paths},{id:draft.id,feedback:false});
   if(response.result?.outcome==='stale'){setError(response.result.detail);await refresh();return}
   if(response.result?.outcome!=='declared')throw Error('The declaration was not confirmed. Your draft is retained.');
   saveDraft(null);await refresh();setNotice('File declaration saved. It does not lock or change files.');
  }catch(error){setError(error.message)}finally{setBusy(false)}
 };
 const inspect=async sid=>{try{const response=await dispatch('coordination.intent.read',{sessionId:sid},{feedback:false});setInspected(response.result)}catch(error){setError(error.message)}};
 const turnPage=async previous=>{if(paging)return;setPaging(true);try{
  const offset=previous?offsets.at(-2):shown.nextOffset;
  const response=await dispatch('coordination.awareness',{sessionId,offset},{feedback:false});
  setPage(response.result);setOffsets(previous?offsets.slice(0,-1):[...offsets,offset]);
 }catch(error){setError(error.message)}finally{setPaging(false)}};
 return <section aria-label="Working in this workspace" className="a-workspace-awareness">
  <h3>Working here</h3>
  <p className="a-caption">Active chats and declared file plans. Declarations help you coordinate; they do not lock files or prevent overwrites. Activity outside this app is unknown.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {loaded&&<>
   <details><summary>Your execution folder</summary><code>{awareness.own.executionDirectory}</code>{awareness.own.executionDirectoryTruncated&&' (shortened)'}</details>
   {!shown.peers.length&&<p>No other active chats observed in this workspace.</p>}
   {shown.peers.map(peer=><article key={peer.sessionId} data-active-peer={peer.sessionId}>
    <strong>{peer.title||'Untitled chat'}</strong> · {peer.status} · {peer.kind==='root'?(peer.commissionedBy?'Commissioned chat':'Started independently'):'Observed child or internal task'}
    <p>{peer.sameExecutionDirectory?'Shares your execution folder':'Uses a different execution folder'}{peer.declaredOverlap&&' · Declared files overlap'}</p>
    {!peer.sameExecutionDirectory&&<details><summary>Execution folder</summary><code>{peer.executionDirectory}</code>{peer.executionDirectoryTruncated&&' (shortened)'}</details>}
    {peer.intent.status!=='unavailable'&&!peer.intent.pathCount&&<p className="a-caption">No files declared; overlap is unknown.</p>}
    {peer.intent.status==='unavailable'?<p>Declaration unavailable; overlap is unknown.</p>:peer.intent.pathCount>0&&<>
     <ul>{peer.intent.paths.map((row,index)=><li key={index}><code>{displayPath(row,peer)}</code>{row.kind==='directory'?' (folder)':''}{row.truncated?' (shortened)':''}</li>)}</ul>
     <button className="a-link" type="button" onClick={()=>inspect(peer.sessionId)}>View full declaration</button>
    </>}
    {peer.intent.needsReview&&<p>Execution placement changed; this declaration needs review.</p>}
   </article>)}
   {(shown.truncated||offsets.length>1)&&<div className="a-dialog-actions"><span>{shown.totalActivePeers} active peers observed.</span>{offsets.length>1&&<button className="a-link" type="button" disabled={paging} onClick={()=>turnPage(true)}>Previous active chats</button>}{shown.nextOffset!=null&&<button className="a-link" type="button" disabled={paging} onClick={()=>turnPage(false)}>More active chats</button>}</div>}
   {inspected&&<article aria-label="Shared file declaration"><strong>Shared file declaration</strong><ul>{inspected.paths.map((row,index)=><li key={index}><code>{row.resolvedPath}</code> · {row.kind}</li>)}</ul><button className="a-link" type="button" onClick={()=>setInspected(null)}>Close declaration</button></article>}
   <details><summary>Declare files for this chat</summary>
    <p className="a-caption">One path per line, relative to your execution folder or an absolute path in a known working copy. End folder paths with /. Up to 16 paths. An empty declaration clears the previous one.</p>
    {awareness.own.intent.needsReview&&<p role="status">Execution placement changed. Review these paths before saving.</p>}
    {own.status==='unavailable'&&<p role="alert">The previous declaration is unavailable. You can explicitly save a replacement; its old contents are unknown.</p>}
    <form aria-label="Declare planned files" onSubmit={save}>
     <label htmlFor={fieldId}>Planned file or folder paths</label>
     <textarea id={fieldId} maxLength={32000} disabled={busy} value={draft?.text??textFor(own)} onChange={event=>edit(event.target.value)}/>
     <div className="a-dialog-actions"><button className="a-soft" disabled={busy||!draft}>Save file declaration</button>{draft&&<button className="a-link" type="button" disabled={busy} onClick={()=>{saveDraft(null);setError('')}}>Discard draft and use latest</button>}</div>
    </form>
   </details>
  </>}
 </section>;
}
