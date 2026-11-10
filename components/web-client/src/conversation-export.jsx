import React,{useEffect,useRef,useState} from 'react';
import {Copy,Download} from 'lucide-react';
import {request} from './api.js';

// A different conversation gets a new review. Late reads cannot retarget it.
export function ConversationExport(props){return <ExportReview key={props.session.id} {...props}/>}
function ExportReview({session,state,act}){
 const [scope,setScope]=useState('all'),[minimal,setMinimal]=useState(false);
 const [metadata,setMetadata]=useState({includeExecution:false,includeTimestamps:false,includeReferenceIds:true});
 const rows=(session.messages||[]).filter(row=>row.id&&['user','assistant'].includes(row.role)&&!row.streaming);
 const [first,setFirst]=useState(rows[0]?.id||''),[last,setLast]=useState(rows.at(-1)?.id||'');
 const [preview,setPreview]=useState(null),[reading,setReading]=useState(false),[error,setError]=useState('');
 const generation=useRef(0);
 useEffect(()=>()=>{generation.current++},[]);
 const report=state.view?.conversationExport,current=report?.sessionId===session.id?report:null;
 const busy=reading||current?.status==='pending';
 const change=fn=>{generation.current++;setPreview(null);setError('');fn()};
 const review=async()=>{
  const ticket=++generation.current;setReading(true);setError('');setPreview(null);
  try{
   const response=await act('session.export',{id:session.id,format:'markdown',destination:'none',scope,minimal,...metadata,
    ...(scope!=='all'?{fromMessageId:first}:{}),...(scope==='range'?{throughMessageId:last}:{})});
   if(ticket!==generation.current)return;
   if(!response?.result?.snapshotId)throw Error('The export could not be prepared. Please try again.');
   const record=response.result,body=await request(record.url,{cache:'no-store'});
   if(typeof body.content!=='string')throw Error('The reviewed export is unavailable.');
   if(ticket===generation.current)setPreview({...record,text:body.content});
  }catch(e){if(ticket===generation.current)setError(e.message||'The export could not be prepared.')}
  finally{if(ticket===generation.current)setReading(false)}
 };
 const deliver=async destination=>{
  setError('');
  try{await act('session.exportDeliver',{id:session.id,snapshotId:preview.snapshotId,destination})}
  catch(e){setError(e.message||'The export could not be delivered.')}
 };
 const choices=rows.map((row,index)=><option key={row.id+':'+index} value={row.id}>{index+1}. {row.role==='user'?'You':'Amplifier'}: {String(row.text||'').replace(/\s+/g,' ').slice(0,90)}</option>);
 return <div>
  <p className="a-caption">Preview user-visible conversation text before copying or downloading. Full exports include history outside the loaded page. Voice exchanges are included; files are referenced, never embedded.</p>
  <label className="a-field">Export scope<select aria-label="Export scope" value={scope} disabled={busy} onChange={e=>change(()=>setScope(e.target.value))}>
   <option value="all">Full conversation</option><option value="from" disabled={!rows.length}>From a message onward</option><option value="range" disabled={!rows.length}>Selected message range</option>
  </select></label>
  {scope!=='all'&&<><p className="a-caption">Choose boundaries from loaded messages. Load earlier messages in the chat to choose an older starting point.</p>
   <label className="a-field">Starting message<select aria-label="Starting message" value={first} disabled={busy} onChange={e=>change(()=>setFirst(e.target.value))}>{choices}</select></label>
   {scope==='range'&&<label className="a-field">Ending message<select aria-label="Ending message" value={last} disabled={busy} onChange={e=>change(()=>setLast(e.target.value))}>{choices}</select></label>}
  </>}
  <label><input type="checkbox" checked={minimal} disabled={busy} onChange={e=>change(()=>setMinimal(e.target.checked))}/> Minimal context: omit conversation identifiers</label>
  <details><summary>Export details</summary>
   {Object.entries({includeExecution:'Include work summaries (status, counts and duration)',includeTimestamps:'Include message timestamps',includeReferenceIds:'Include attachment and artifact identifiers'}).map(([key,label])=><label key={key} style={{display:'block'}}><input type="checkbox" checked={metadata[key]} disabled={busy} onChange={event=>change(()=>setMetadata({...metadata,[key]:event.target.checked}))}/>{label}</label>)}
   <p className="a-caption">Work summaries cover retained records. Tool inputs, outputs, hidden instructions and private reasoning are excluded.</p>
  </details>
  <div className="a-dialog-actions"><button type="button" className="a-soft" disabled={busy} onClick={review}>{reading?'Preparing preview…':preview?'Refresh preview':'Preview Markdown'}</button>
  </div><details><summary>More formats</summary><button type="button" className="a-link" data-action="session.export" disabled={busy} onClick={()=>act('session.export',{id:session.id})}>Export JSON</button></details>
  {preview&&<section aria-label="Conversation export preview">
   <p>{preview.summary.messageCount} messages · {preview.summary.bytes.toLocaleString()} bytes · {preview.summary.attachmentCount} attachment references · {preview.summary.artifactCount} artifact references</p>
   <p className="a-caption">Captured {new Date(preview.capturedAt).toLocaleString()}. Copy and download use this exact preview, even if the conversation continues.</p>
   {preview.summary.omissions.map(item=><p className="a-caption" key={item}>{item}</p>)}
   <textarea aria-label="Reviewed Markdown" readOnly value={preview.text} rows={10} style={{width:'100%',maxWidth:'100%',boxSizing:'border-box'}}/>
   <div className="a-dialog-actions">
    <button type="button" className="a-soft" data-action="session.exportDeliver" disabled={busy} onClick={()=>deliver('clipboard')}><Copy/>Copy Markdown</button>
    <button type="button" className="a-soft" data-action="session.exportDeliver" disabled={busy} onClick={()=>deliver('download')}><Download/>Download Markdown</button>
   </div>
  </section>}
  {error&&<p role="alert">{error}</p>}
  {current&&<p role={current.status==='error'?'alert':'status'}>{current.status==='pending'?'Delivering reviewed export…':current.message}</p>}
 </div>;
}
