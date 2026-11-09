import React from 'react';
import {AlertCircle,Check} from 'lucide-react';
import {ReadWhenVisible} from './attention.jsx';
import {updateReleaseSummary,releaseEntries} from './update-release-summary';

function useNotesState(application,context){
 const entries=releaseEntries(application.releaseNotes);
 const missing=application.releaseNotes===undefined;
 const identity=JSON.stringify([context,application.current,application.runningRevision,missing]);
 const [expired,setExpired]=React.useState(null);
 React.useEffect(()=>{
  setExpired(null);
  if(!missing)return;
  // The initial compact snapshot may precede Settings detail demand. This is
  // an arrival grace, not evidence that a network request remains in flight.
  const timer=setTimeout(()=>setExpired(identity),5000);
  return()=>clearTimeout(timer);
 },[identity,missing]);
 const warning=typeof application.releaseNotesWarning==='string'?application.releaseNotesWarning:'';
 return {entries,message:warning||(!missing&&!entries?'Release notes could not be read. Reopen Updates or view the published release.':
  missing?(expired===identity?'Release notes have not arrived. Reopen Updates or view the published release.':'Loading release notes…'):'')};
}

export function UpdateReleaseSummary({updates,application,overview}){
 const summary=updateReleaseSummary(updates,application,overview);
 const notesState=useNotesState(application,[summary.version,summary.stage,updates.pendingApp?.revision,updates.pendingRestart?.revision]);
 return <section className="a-update-release-summary" data-part="update-release-summary" data-stage={summary.stage} aria-labelledby="update-release-heading">
  <h4 id="update-release-heading">{summary.heading}{summary.version&&<>{summary.stage==='before'?' ':' · '}{summary.version}</>}</h4>
  {summary.notes?<><p className="a-release-title">{summary.notes.title}</p><ul>{summary.notes.changes.map((change,index)=><li key={index}>{change}</li>)}</ul></>:
   <p className="a-caption" role="status">{notesState.message||(summary.version?'Release notes for this version are unavailable.':'The update’s release version has not been confirmed. Check the update status for details.')}</p>}
  {summary.notes&&application.releaseNotesWarning&&<p className="a-caption" role="status">{application.releaseNotesWarning}</p>}
  {summary.url&&<a href={summary.url} target="_blank" rel="noreferrer">View published release<span className="a-sr-only"> {summary.version} (opens in a new tab)</span></a>}
 </section>;
}

const identity=(release,notice)=>`release-notice:${release.version}:${notice.id}`;
const version=value=>String(value||'').replace(/^v/,'').split('.').map(Number);
const newer=(a,b)=>{const x=version(a),y=version(b);for(let i=0;i<3;i++){if(x[i]!==y[i])return x[i]>y[i]}return false};
const status=(release,application)=>release.version===application.current?'Installed':newer(release.version,application.current)?'Upcoming':'Earlier release';
function Notice({notice}){
 return <><strong>{notice.title}</strong><p>{notice.detail}</p><p><b>What to do:</b> {notice.action}</p></>;
}

export function ReleaseNotices({application,state,act}){
 const attention=new Map((state.attention?.items||[]).map(item=>[item.id,item]));
 const unread=(releaseEntries(application.releaseNotes)||[]).flatMap(release=>release.notices.map(notice=>({release,notice,item:attention.get(identity(release,notice))}))).filter(({item})=>item);
 if(!unread.length)return null;
 return <section className="a-release-notices" aria-label="High-impact changes">
  <h4><AlertCircle aria-hidden="true"/>High-impact changes</h4>
  <p className="a-caption">Changes that may affect your settings or workflow. Notices are marked read as you view them.</p>
  {unread.map(({release,notice,item})=><article key={item.id} className="a-release-notice">
   <span className="a-caption">Version {release.version} · {status(release,application)}</span>
   <ReadWhenVisible item={item} act={act}><Notice notice={notice}/></ReadWhenVisible>
  </article>)}
 </section>;
}

export function ReleaseHistory({application,state,act}){
 const {entries,message}=useNotesState(application,application.latest);
 const reviewed=new Set((state.attention?.items||[]).filter(item=>item.read).map(item=>item.id));
 // Construct URLs from stable versions, never from release-authored text.
 const tag=/^v?\d+\.\d+\.\d+$/.test(application.latest||'')?application.latest:null;
 return <section className="a-release-history" aria-label="Changelog">
  <details className="a-update-disclosure"><summary>Changelog</summary>
  <div className="a-update-disclosure-body">
  <p className="a-caption">Application release history · installed notes are available offline.</p>
  {application.releaseNotesWarning&&<p className="a-release-notes-warning" role="status">{application.releaseNotesWarning} {tag&&<a href={'https://github.com/microsoft/amplifier-unified/releases/tag/'+tag} target="_blank" rel="noreferrer">View published release</a>}</p>}
  {!entries&&!application.releaseNotesWarning&&<p className="a-caption" role="status">{message}</p>}
  {entries?.length===0&&<p className="a-caption">No release notes are included with this installation.</p>}
  {(entries||[]).map(release=><details key={release.version} className="a-release-entry" open={release.version===application.current||newer(release.version,application.current)}>
   <summary><strong>{release.version} · {release.title}</strong><span className="a-caption">{status(release,application)}</span>{release.notices.length>0&&<span className="a-release-impact">High impact</span>}</summary>
   <ul>{release.changes.map((change,i)=><li key={i}>{change}</li>)}</ul>
   {release.notices.map(notice=><div key={notice.id} className="a-release-notice archived"><ReadWhenVisible item={(state.attention?.items||[]).find(item=>item.id===identity(release,notice))} act={act}><Notice notice={notice}/></ReadWhenVisible>{reviewed.has(identity(release,notice))&&<span className="a-release-reviewed"><Check aria-hidden="true"/>Reviewed</span>}</div>)}
  </details>)}
  </div></details>
 </section>;
}
