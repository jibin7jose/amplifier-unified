import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';

const GiB=1024**3;
const fields=[['maxRunning','Maximum active test environments'],['maxInstances','Maximum total test environments'],['minFreeBytes','Minimum free disk space (GiB)']];
const form=data=>Object.fromEntries(fields.map(([key])=>[key,data.limits[key]===undefined?'':String(key==='minFreeBytes'?data.limits[key]/GiB:data.limits[key])]));
const same=(a,b)=>fields.every(([key])=>a[key]===b[key]);
function limits(draft){
 const value=Object.fromEntries(fields.filter(([key])=>draft[key]!=='').map(([key])=>[key,Number(draft[key])*(key==='minFreeBytes'?GiB:1)]));
 if(Object.values(value).some(item=>!Number.isSafeInteger(item)||item<0))throw Error('Use non-negative whole counts and disk space that resolves to a whole number of bytes.');
 return value;
}

export function TestRunnerBackendLimits({act,navigate,id,name}){
 const [data,setData]=useState(null),[draft,setDraft]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const pending=useRef(false),action=useRef(act);action.current=act;
 const accept=value=>{setData(value);setDraft(form(value))};
 const dirty=!!data&&!same(draft,form(data));
 useSettingsDraft('testing-runner-capacity-'+id,{label:(name||'Testing machine')+' environment limits',dirty,review:()=>navigate?.('testing'),discard:()=>data&&setDraft(form(data))});
 async function load(){const response=await action.current('testing.runners.capacity.read',{id});if(!response?.accepted||!response.result)throw Error(response?.error||'Could not read this machine’s test environment limits.');return response.result}
 useEffect(()=>{let live=true;setData(null);setDraft(null);setError('');setNotice('');load().then(value=>{if(live)accept(value)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[id]);
 async function refresh(){if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');try{accept(await load())}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}}
 async function save(){
  if(pending.current||!data)return;
  let desired;try{desired=limits(draft)}catch(e){setError(e.message);return}
  pending.current=true;setBusy(true);setError('');setNotice('');
  try{
   const response=await action.current('testing.runners.capacity.save',{id,expectedRevision:data.revision,limits:desired});
   if(!response?.accepted||!response.result)throw Error(response?.error||'The save was not confirmed.');
   accept(response.result);setNotice('Environment limits saved. Active checks continue with their original machine.');
  }catch(e){
   let confirmed=false;
   try{const current=await load();if(same(current.limits,desired)){accept(current);setNotice('Current limits match your change. The save was not repeated.');confirmed=true}}catch{}
   if(!confirmed)setError(e.message+' Your edits are kept; refresh to review the current limits.');
  }finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label={(name||'Testing machine')+' environment limits'}>
  <h5>Test environment limits</h5><p>These limits control how many environments this testing machine can accept. CPU and memory budgets are set separately.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {data&&<><p className="a-caption">{data.scope}</p><p>{data.behavior}</p>
   <details><summary>Advanced environment limits</summary><p>Leave a field empty to use this connection’s configured default. Set 0 to disable that limit. Lowering limits does not stop running checks.</p>
    <fieldset disabled={busy}><legend>Limits on {name||'this machine'}</legend>{fields.map(([key,label])=><label className="a-field" key={key}>{label}<input aria-label={(name||'Testing machine')+': '+label} type="number" min="0" step={key==='minFreeBytes'?'any':'1'} value={draft[key]} placeholder={String(key==='minFreeBytes'?data.defaults[key]/GiB:data.defaults[key])} onChange={e=>setDraft(value=>({...value,[key]:e.target.value}))}/><small>Effective: {data.effective[key]===0?'No limit':key==='minFreeBytes'?data.effective[key]/GiB+' GiB':data.effective[key]}</small></label>)}</fieldset>
   </details>
   <button className="a-primary" type="button" disabled={busy||!dirty} onClick={save}>Save environment limits</button>
  </>}
  <button className="a-soft" type="button" disabled={busy} onClick={refresh}>{data?'Discard environment edits and refresh':'Reload environment limits'}</button>
 </section>;
}
