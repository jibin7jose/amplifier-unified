import React,{useEffect,useRef,useState} from 'react';
import {formatStorageBytes as bytes} from './storage-bytes';

export function SavedTestStorage({act}){
 const [data,setData]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const pending=useRef(false),epoch=useRef(0);
 useEffect(()=>()=>{epoch.current++},[]);
 async function read(before){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');const version=++epoch.current;
  try{
   const response=await act('storage.cleanup.evidence',before?{before}:{},{feedback:false});
   if(!response?.accepted||!response.result)throw Error(response?.error||'Saved test sizes could not be read.');
   if(version===epoch.current)setData(response.result);
  }catch(e){if(version===epoch.current)setError(e.message)}
  finally{pending.current=false;if(version===epoch.current)setBusy(false)}
 }
 const size=value=>value===null?'Unavailable':bytes(value);
 return <section className="a-settings-section" aria-label="Saved test evidence"><h4>Saved test evidence</h4>
  <p>Tests keep their original source, the final workspace and results for later review. These files are separate from unused app copies.</p>
  <button className="a-soft" disabled={busy} onClick={()=>read()}>{data?'Refresh recent test sizes':'Review saved test sizes'}</button>
  {error&&<p role="alert">{error}</p>}{busy&&<p role="status">Reading saved sizes…</p>}
  {data&&<><p>{bytes(data.recordedBytes)} recorded across {data.accountedCount} tests on this page. {data.unaccountedCount>0&&`${data.unaccountedCount} other ${data.unaccountedCount===1?'test has':'tests have'} incomplete size records.`}</p>
   <p className="a-caption">{data.note} This page shows up to {data.pageSize} tests; it is not an all-workspace total.</p>
   {data.items.length===0?<p>No saved test size records in this page.</p>:<ul>{data.items.map(item=><li key={item.position}><details>
    <summary>{item.label}{item.createdAt?` · ${new Date(item.createdAt*1000).toLocaleString()}`:''} · {size(item.totalBytes)}</summary>
    <p>Original source: {size(item.sourceBytes)}.</p>
    {item.workspaceAvailability==='expired'&&<p>Temporary workspace copy expired under its saved policy. Sizes below describe the original export; source and test results remain retained.</p>}
    {item.combinedArchive?<p>Final workspace and results are included in a full environment archive: {size(item.otherArchiveBytes)}. Their separate sizes were not recorded.</p>:<p>Final workspace: {size(item.workspaceBytes)}. Test results: {size(item.resultBytes)}.</p>}
    {item.custody&&<p className="a-caption">{item.custody}</p>}
    {item.detail&&<p>{item.detail}</p>}<p className="a-caption">Test reference: {item.id}</p>
   </details></li>)}</ul>}
   {data.nextBefore&&<button className="a-soft" disabled={busy} onClick={()=>read(data.nextBefore)}>Show earlier tests</button>}
  </>}
 </section>;
}
