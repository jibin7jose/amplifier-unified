import React,{useEffect,useRef,useState} from 'react';
import {Share2,Copy} from 'lucide-react';

export function LibraryFilters({state,act}){
 const view=state.view||{},organization=state.conversationOrganization||{};
 return <div aria-label="Conversation library filters"><label>Conversations<select aria-label="Active or archived conversations" value={view.navArchive||'active'} data-action="view.update" onChange={e=>act('view.update',{patch:{navArchive:e.target.value}})}><option value="active">Active</option><option value="archived">Archived ({organization.archivedCount||0})</option><option value="all">Active and archived</option></select></label></div>;
}
export function ConversationSharing({session,act}){
 const [preview,setPreview]=useState(null),[links,setLinks]=useState([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[expiry,setExpiry]=useState(604800);
 const generation=useRef(0),[nextOffset,setNextOffset]=useState(null);
 useEffect(()=>{generation.current++;setPreview(null);setLinks([]);setNextOffset(null);setError('');setBusy(false);return ()=>{generation.current++}},[session.id]);
 async function run(action,args,apply){
  const current=generation.current;setBusy(true);setError('');
  try{const receipt=await act(action,args);if(!receipt?.result)throw Error('The snapshot action did not return a result.');if(current===generation.current)apply(receipt.result)}
  catch(e){if(current===generation.current)setError(e.message)}finally{if(current===generation.current)setBusy(false)}
 }
 const upsert=row=>setLinks(current=>[row,...current.filter(item=>item.id!==row.id)]);
 const list=(offset=0)=>run('session.shareList',{sessionId:session.id,offset},result=>{setLinks(current=>offset?[...current,...result.items.filter(row=>!current.some(item=>item.id===row.id))]:result.items);setNextOffset(result.nextOffset)});
 return <section className="a-settings-section"><h3>Share a snapshot</h3>
  <p>A snapshot includes visible conversation text and voice exchanges. File contents and interactive artifacts are not embedded.</p>
  <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} data-action="session.sharePreview" onClick={()=>run('session.sharePreview',{sessionId:session.id},setPreview)}><Share2/>Preview snapshot</button><button type="button" className="a-soft" disabled={busy} data-action="session.shareList" onClick={()=>list()}>Show shared snapshots</button></div>
  {preview&&<><pre className="a-state-view" aria-label="Snapshot preview" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:320}}>{preview.text}</pre>{preview.nextOffset!==null&&<button type="button" className="a-link" disabled={busy} data-action="session.shareRead" onClick={()=>run('session.shareRead',{id:preview.id,offset:preview.nextOffset},page=>setPreview(current=>({...current,text:current.text+page.text,nextOffset:page.nextOffset})))}>Read more of this snapshot</button>}
   <p>Anyone with the link can read this snapshot while this host is reachable. Later edits stay private to the original chat. You can revoke the link.</p>
   <label htmlFor="share-expiry">Link expires after</label><select id="share-expiry" value={expiry} onChange={e=>setExpiry(Number(e.target.value))}><option value={86400}>One day</option><option value={604800}>One week</option><option value={2592000}>Thirty days</option></select>
   <button type="button" className="a-primary" disabled={busy} data-action="session.shareCreate" onClick={()=>run('session.shareCreate',{id:preview.id,contentHash:preview.contentHash,visibility:'anyone_with_link',expiresInSeconds:expiry},result=>{upsert(result);setPreview(null)})}>Create share link</button>
  </>}
  {links.map(row=><div className="a-selection-card" key={row.id}><strong>{row.title}</strong><p>{row.status}{row.expiresAt?' · expires '+new Date(row.expiresAt*1000).toLocaleString():''}</p>{row.path&&<><label htmlFor={'share-'+row.id}>Snapshot link</label><input id={'share-'+row.id} readOnly value={new URL(row.path,window.location.origin).href}/><button type="button" className="a-soft" onClick={()=>navigator.clipboard.writeText(new URL(row.path,window.location.origin).href).catch(()=>setError('Copy was unavailable. Select the link and copy it.'))}><Copy/>Copy link</button><button type="button" className="a-soft" disabled={busy} data-action="session.shareRevoke" onClick={()=>run('session.shareRevoke',{id:row.id},upsert)}>Revoke link</button></>}</div>)}
  {nextOffset!==null&&<button type="button" className="a-soft" disabled={busy} data-action="session.shareList" onClick={()=>list(nextOffset)}>Load older snapshots</button>}
  {error&&<p role="alert" className="a-danger">{error}</p>}
 </section>;
}
