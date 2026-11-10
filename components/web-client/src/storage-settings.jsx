import React,{useEffect,useRef,useState} from 'react';
import {ChevronRight,ArrowLeft,HardDrive,Folder,MessagesSquare,Archive,Database,Settings} from 'lucide-react';
import {ChatDelete} from './chat-delete';
import {StorageCleanupSettings} from './storage-cleanup-settings';
import {SavedTestStorage} from './saved-test-storage';
import './storage-settings.css';
import {formatStorageBytes as bytes} from './storage-bytes';
function WorkspaceCleanupResult({result}){return <p role="status">{result.status==='completed'?`Workspace files deleted (${bytes(result.deletedBytes)} of file content). Chat history was retained.`:result.message||'Cleanup was interrupted; inspect the recorded folder before proceeding.'}{result.status!=='completed'&&<code style={{display:'block',overflowWrap:'anywhere'}}>{result.cleanupPath}</code>}</p>}
export function WorkspaceCleanup({id,act,onDone}){
 const [review,setReview]=useState(null),[path,setPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState(null);
 async function run(confirm=false){setBusy(true);setError('');try{
  const response=await act(confirm?'workspace.deleteFiles':'workspace.deleteFilesPreview',confirm?{id,confirmationToken:review.token,confirmationPath:path}:{id});
  if(!response?.accepted||!response.result)throw Error(response?.error||'Workspace cleanup could not complete. Check the reported error and review again.');
  if(confirm){setResult(response.result);onDone?.(response.result)}else{setReview(response.result);setPath('')}
 }catch(e){setError(e.message||'The folder could not be checked.')}finally{setBusy(false)}}
 return <section aria-label="Workspace file cleanup">
  {!result&&<button type="button" disabled={busy} onClick={()=>run()}>Review workspace file cleanup</button>}
  {error&&<p role="alert">{error}</p>}
  {review&&!result&&<div><p>{review.description}</p><p><strong>{review.managed?'App-managed workspace':'User-selected project folder'}</strong> · {bytes(review.inventory.fileBytes)} in {review.inventory.fileCount} files. {review.chatCount} chats will be retained and archived.</p><code style={{overflowWrap:'anywhere'}}>{review.path}</code><label className="a-field">Type the exact folder path to confirm permanent deletion<input aria-label="Confirm workspace folder path" value={path} onChange={e=>setPath(e.target.value)} disabled={busy}/></label><button type="button" className="a-danger" disabled={busy||path!==review.path} onClick={()=>run(true)}>Permanently delete workspace files</button></div>}
  {result&&!onDone&&<WorkspaceCleanupResult result={result}/>}
 </section>;
}
const categories={
 workspaces:{icon:Folder,color:'#277f98',title:'Review workspace folders',help:'Remove a project folder you no longer need. Its chats and saved outputs are retained. Active work, overlapping folders and CLI configuration are protected.'},
 sessions:{icon:MessagesSquare,color:'#8662c5',title:'Review chats you no longer need',help:'Delete an app-managed chat and its owned files together. Review exactly what will be removed before confirming. You can also remove a project’s saved sessions or an individual session from shared CLI history. Project working files and CLI configuration are kept.'},
 runtime:{icon:HardDrive,color:'#b98131',title:'Keep your working installation',help:'These files run Amplifier. Runtime versions may still be used by workers or recovery, so they cannot be deleted from this screen.'},
 backups:{icon:Archive,color:'#b15b82',title:'Review older backups',help:'Remove an older private backup after reviewing it. Your newest backup is kept. Reset recovery copies stay protected.'},
 cache:{icon:Database,color:'#478879',title:'Inspect caches and logs',help:'Some caches and logs are in use by running components. Automatic cleanup covers only proven unused component and test runner copies. Inspect other sizes below.'},
 other:{icon:Settings,color:'#77808d',title:'Keep your settings and records',help:'Configuration, credentials and application records are protected. Use the other categories to review files you can remove.'}
};
const size=row=>row.pending?(row.bytes?`${bytes(row.bytes)} so far`:'Measuring…'):row.bytes==null?'Not measured':`${row.incomplete?'At least ':''}${bytes(row.bytes)}`;
function BackupCleanup({id,act,close,onDone}){
 const [review,setReview]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[result,setResult]=useState(null);
 const pending=useRef(false);
 async function run(confirm=false){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{
  const response=await act(confirm?'storage.backupDelete':'storage.backupDeletePreview',confirm?{id,confirmationToken:review.token}:{id});
  if(!response?.accepted||!response.result)throw Error(response?.error||'Backup cleanup could not complete.');
  if(confirm){setResult(response.result);onDone?.()}else setReview(response.result);
 }catch(e){setError(e.message);setReview(null)}finally{pending.current=false;setBusy(false)}}
 return <section aria-label="Backup cleanup"><h4>Review backup removal</h4>{error&&<p role="alert">{error}</p>}
 {result?<p role="status">{result.status==='completed'?`Backup removed (${bytes(result.deletedBytes)}).`:result.message}{result.cleanupPath&&result.status!=='completed'&&<code>{result.cleanupPath}</code>}</p>:review?<><p>{review.description}</p><p>{bytes(review.inventory.fileBytes)} in {review.inventory.fileCount} files.</p><code>{review.path}</code><p><strong>This cannot be undone.</strong></p><button className="a-soft a-danger" disabled={busy} onClick={()=>run(true)}>Permanently delete backup</button></>:<button className="a-soft" disabled={busy} onClick={()=>run()}>Review backup contents</button>}
 <button className="a-soft" disabled={busy} onClick={close}>{result?'Close':'Keep backup'}</button></section>;
}
export function StorageSettings({act,onPrivacy,navigate}){
 const [args,setArgs]=useState({}),[trail,setTrail]=useState([]),[data,setData]=useState(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0),[filter,setFilter]=useState(''),[count,setCount]=useState(5),[sort,setSort]=useState('size'),[cleanupOnly,setCleanupOnly]=useState(false),[review,setReview]=useState(null),[notice,setNotice]=useState(''),[workspaceResult,setWorkspaceResult]=useState(null);
 const key=JSON.stringify(args),action=useRef(act),heading=useRef(null),shown=useRef(key),reviewPanel=useRef(null),cache=useRef(new Map()),epoch=useRef(0),requested=useRef(null),snapshot=useRef(null);action.current=act;
 useEffect(()=>{if(data&&shown.current!==key){shown.current=key;heading.current?.scrollIntoView({block:'start'})}},[data,key]);
 useEffect(()=>{if(review)reviewPanel.current?.scrollIntoView({block:'nearest'})},[review]);
 useEffect(()=>{
  let live=true,timer,scanId,scanning=false;setError('');const previous=requested.current;requested.current=key;
  const saved=cache.current.get(key);if(saved){setData(saved);if(!saved.measuring)return}
  if(previous!==key)setData(null);const version=epoch.current;
  const cancel=()=>{if(scanId&&scanning)action.current('storage.inspect',{...args,scanId,cancel:true}).catch(()=>{})};
  async function read(){try{
   const response=await action.current('storage.inspect',{...args,progressive:true,...(scanId?{scanId}:snapshot.current?{fromScanId:snapshot.current}:{})});
   if(!response?.result)throw Error(response?.error||'Storage could not be read.');
   scanId=response.result.scanId;
   scanning=!!response.result.scanning;
   if(!live||version!==epoch.current){cancel();return}
   snapshot.current=scanId;
   if(response.result.measurementError)throw Error(response.result.measurementError);
   setData(response.result);
   if(response.result.scanning)timer=setTimeout(read,350);
   else cache.current.set(key,response.result);
  }catch(e){if(live)setError(e.message||'Storage could not be read.')}}
  read();return()=>{live=false;clearTimeout(timer);cancel()};
 },[key,refresh]);
 function invalidate(){epoch.current++;snapshot.current=null;cache.current.clear();setRefresh(n=>n+1)}
 function reset(){setWorkspaceResult(null);setNotice('');setSort('size');setCount(5);setFilter('');setCleanupOnly(false);setReview(null)}
 function go(next){setTrail(old=>[...old,{args,label:data?.label||'Storage'}]);setData(cache.current.get(JSON.stringify(next))||null);setArgs(next);reset()}
 function jump(index){setData(cache.current.get(JSON.stringify(trail[index].args))||null);setArgs(trail[index].args);setTrail(old=>old.slice(0,index));reset()}
 const overview=!args.category&&!args.scope,category=categories[args.category],cleanupable=row=>!!(row.historySelection||row.workspaceId||row.sessionId||(row.backupId&&!row.protected));
 const filtered=(data?.rows||[]).filter(row=>row.label.toLowerCase().includes(filter.toLowerCase())&&(!cleanupOnly||cleanupable(row))).sort((a,b)=>sort==='name'?a.label.localeCompare(b.label):(b.bytes||0)-(a.bytes||0));
 const rows=filtered.slice(0,count),total=data?.bytes||0;
 let offset=0;const stops=(data?.rows||[]).map(row=>{const start=offset;offset+=total?100*(row.bytes||0)/total:0;return `${categories[row.id]?.color||'#77808d'} ${start}% ${offset}%`});
 function open(row){if(overview)go({category:row.id});else if(row.destination)go(row.destination);else if(args.rootId)go({...args,path:row.path});else go({category:args.category,rootId:row.id})}
 function done(){setReview(null)}
 function workspaceDeleted(result){setWorkspaceResult(result);invalidate()}
 function historyDeleted(result){invalidate();const selection=review?.historySelection;if(selection?.nativeId&&args.path===`${selection.project}/sessions/${selection.nativeId}`&&trail.length)jump(trail.length-1);setNotice(result?.cleanupPending?'The sessions were removed. Some file cleanup is still pending.':'Session data deleted. Project working files were kept.')}
 return <section className="a-storage" aria-label="Storage usage">
 {notice&&<p role="status">{notice}</p>}
 {workspaceResult&&<WorkspaceCleanupResult result={workspaceResult}/>}
 {overview&&<StorageCleanupSettings act={act} navigate={navigate}/>}
  {overview&&<SavedTestStorage act={act}/>}
 <nav className="a-storage-breadcrumbs" aria-label="Storage navigation">{onPrivacy&&<><button className="a-link" onClick={onPrivacy}>Privacy &amp; files</button><ChevronRight size={14}/></>}{trail.map((entry,index)=><React.Fragment key={index}><button className="a-link" onClick={()=>jump(index)}>{index===0?'Storage':entry.label}</button><ChevronRight size={14}/></React.Fragment>)}<span aria-current="page">{overview?'Storage':data?.label||'Loading…'}</span></nav>
 <div className="a-storage-heading" ref={heading}><div><h3>{overview?'Storage on this host':data?.label||category?.title||'Storage'}</h3></div><button className="a-soft" disabled={!data&&!error} onClick={invalidate}>Refresh storage</button></div>
 {error&&<p role="alert">{error}</p>}{!data&&!error?<p role="status">Opening storage…</p>:data&&<>
 {data.workspaceScope&&(overview||args.category==='workspaces')&&<p className="a-caption">{data.workspaceScope.description} <span>Workspace root: </span><code style={{overflowWrap:'anywhere'}}>{data.workspaceScope.root}</code></p>}
 {data.scanning&&<p role="status">Measuring sizes in the background. You can open a category now.</p>}
 {data.measuredAt&&!data.scanning&&<p className="a-caption">Measured at {new Date(data.measuredAt*1000).toLocaleTimeString()}. Refresh to check for changes.</p>}
 {overview?<><div className="a-storage-summary"><div className="a-storage-donut" aria-hidden="true" style={{background:total?`conic-gradient(${stops.join(',')})`:'var(--a-border)'}}><div><strong>{bytes(total)}</strong><span>{data.incomplete?'measured so far':'of file content'}</span></div></div><div><h4>See where your space is going</h4><p>Choose a category to review cleanup options and find its largest items.</p><p className="a-caption">{data.description}</p></div></div>
 <ul className="a-storage-categories">{data.rows.map(row=>{const meta=categories[row.id],Icon=meta.icon;return <li key={row.id}><button onClick={()=>open(row)}><Icon style={{color:meta.color}} size={22}/><span className="a-storage-name"><strong>{row.label}</strong><span className="a-caption">{row.description}</span></span><span className="a-storage-size">{size(row)}</span><ChevronRight size={18}/><span className="a-storage-bar"><span style={{width:`${total?100*row.bytes/total:0}%`,background:meta.color}}/></span></button></li>})}</ul></>:<>
 <p className="a-storage-total"><strong>{size(data)}</strong>{data.path&&<code>{data.path}</code>}</p>
 {!args.rootId&&!args.sessionId&&category&&<div className="a-storage-cleanup"><h4>{category.title}</h4><p>{category.help}</p>{['workspaces','sessions','backups'].includes(args.category)&&<button className="a-soft" aria-pressed={cleanupOnly} onClick={()=>{setCleanupOnly(v=>!v);setCount(5)}}>{cleanupOnly?'Show all items':'Show items I can review for cleanup'}</button>}</div>}
 {args.rootId&&!args.path&&data.workspaceId&&<WorkspaceCleanup key={data.workspaceId} id={data.workspaceId} act={act} onDone={workspaceDeleted}/>}
 {data.historySelection&&<button className="a-soft" onClick={()=>setReview({historySelection:data.historySelection})}>{data.historySelection.nativeId?'Review session deletion':'Review deletion of all project sessions'}</button>}
 {data.sessionId&&<button className="a-soft" onClick={()=>setReview({sessionId:data.sessionId})}>Review chat cleanup</button>}
 {review&&<div className="a-storage-review" ref={reviewPanel}>{review.historySelection?<ChatDelete key={JSON.stringify(review.historySelection)} historySelection={review.historySelection} act={act} cancel={done} onDeleted={historyDeleted}/>:review.sessionId?<ChatDelete key={review.sessionId} id={review.sessionId} act={act} cancel={done} onDeleted={invalidate}/>:review.backupId?<BackupCleanup key={review.backupId} id={review.backupId} act={act} close={done} onDone={invalidate}/>:<><WorkspaceCleanup key={review.workspaceId} id={review.workspaceId} act={act} onDone={workspaceDeleted}/><button className="a-soft" onClick={done}>Close review</button></>}</div>}
 <h4 className="a-storage-list-heading">{cleanupOnly?'Review cleanup':'Largest items'}</h4>
 <div className="a-storage-filters"><label className="a-field">Find an item<input type="search" aria-label="Filter storage" value={filter} onChange={e=>{setFilter(e.target.value);setCount(5)}}/></label><label className="a-field">Sort<select aria-label="Sort storage" value={sort} onChange={e=>setSort(e.target.value)}><option value="size">Largest first</option><option value="name">Name</option></select></label></div>
 <ul className="a-storage-items">{rows.map(row=><li key={row.id}><div className="a-storage-item-line">{row.directory?<button className="a-link a-storage-item-name" onClick={()=>open(row)}>{row.label}<ChevronRight size={16}/></button>:<span className="a-storage-item-name">{row.label}</span>}<span className="a-storage-size">{size(row)}</span></div><div className="a-storage-bar"><span style={{width:`${total?Math.min(100,100*(row.bytes||0)/total):0}%`,background:category?.color||'var(--a-accent)'}}/></div>{row.measurementNote&&<p className="a-caption">{row.measurementNote}</p>}<div className="a-storage-item-actions">{row.protected&&<span className="a-caption">{row.backupId?'Newest backup · kept':'Read only'}</span>}{row.archived&&<span className="a-caption">Archived</span>}{cleanupable(row)&&<button className="a-link" onClick={()=>setReview(row)}>Review cleanup<span className="a-storage-sr"> for {row.label}</span></button>}</div></li>)}</ul>
 {!rows.length&&!data.scanning&&<p>{cleanupOnly?'No items available for cleanup in this category.':'No items to show.'}</p>}{filtered.length>count&&<button className="a-soft" onClick={()=>setCount(n=>n+10)}>Show more ({filtered.length-count} remaining)</button>}
 </>}
 {data.incomplete&&!data.scanning&&<p className="a-caption">Some locations could not be fully measured. Totals are lower bounds; unreadable files, links and scan limits can leave data uncounted.</p>}
 <p className="a-caption">Archiving hides work; it does not free storage. Deleting your work always starts with a review. Automatic cleanup uses your saved policy for proven unused code copies only.</p>
 </>}</section>;
}
