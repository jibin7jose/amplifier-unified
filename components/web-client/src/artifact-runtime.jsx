import React,{useEffect,useRef,useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {ActivityRegion} from './activity-region';

export function ArtifactRuntime({sessionId,act}){
 const [report,setReport]=useState(null),[pending,setPending]=useState(false),[error,setError]=useState('');
 const request=useRef(0);
 useEffect(()=>{request.current++;setReport(null);setPending(false);setError('');return ()=>{request.current++}},[sessionId]);
 async function inspect(verifyImports=false){
  const id=++request.current;setPending(true);setError('');
  try{
   const receipt=await act('runtime.dependencies',{sessionId,...(verifyImports?{verifyImports:true}:{})});
   if(request.current===id){if(!receipt?.result)throw Error('Runtime inspection did not return a result.');setReport(receipt.result)}
  }catch(e){if(request.current===id)setError(e.message)}
  finally{if(request.current===id)setPending(false)}
 }
 return <ActivityRegion as="section" name="runtime.dependencies" busy={pending} className="a-settings-section">
  <h3>Document and data tools</h3>
  <p>Check the tools available for documents, spreadsheets, PDFs and plots.</p>
  <button className="a-soft" data-action="runtime.dependencies" disabled={pending} onClick={()=>inspect()}><RefreshCw/>Inspect document tools</button>
  <button className="a-soft" data-action="runtime.dependencies" disabled={pending} onClick={()=>inspect(true)}>Check library imports</button>
  {error&&<p role="alert" className="a-danger">{error}</p>}
  {report&&<><p className="a-caption">Availability is separate from a successful render, visual review or formula calculation.</p>
   {Object.entries(report).map(([scope,environment])=><section key={scope} aria-label={scope==='host'?'App environment':'Conversation environment'}>
    <h4>{scope==='host'?'App environment':'Conversation environment'}</h4>
    {environment.status!=='available'?<p>{environment.reason||'Environment not available.'}</p>:<>
     <p className="a-wrap">Python {environment.python.version}<br/><code>{environment.python.path}</code></p>
     <ul className="a-catalog-list">{environment.python.packages.map(pkg=><li key={pkg.name}><strong>{pkg.name}</strong> — {pkg.version||pkg.status}{pkg.importStatus&&` · Import ${pkg.importStatus}`}</li>)}</ul>
     {Object.entries(environment.executables).map(([name,tool])=><p key={name} className="a-wrap"><strong>{{node:'Node.js',libreoffice:'LibreOffice',pdftoppm:'PDF renderer'}[name]||name}</strong> — {tool.version||tool.status}{tool.path&&<><br/><code>{tool.path}</code></>}{tool.reason&&<><br/>{tool.reason}</>}</p>)}
    </>}
   </section>)}
  </>}
 </ActivityRegion>;
}
