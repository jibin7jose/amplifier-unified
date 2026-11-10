import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
import {SettingsActions} from './settings-layout';
import {TestRunnerSettings} from './test-runner-settings';
import {ExecutionLimits} from './testing-execution-settings';

const gib=1024**3;
const fields=[['maxRunning','Running test environments',1],['maxInstances','Running and retained environments',1],['minFreeBytes','Minimum free disk space (GiB)',gib]];
const sourceNames={global:'Global setting',runner:'Runner override',configured:'Runner configuration','backend-default':'Backend default'};
const same=(a,b)=>JSON.stringify(Object.entries(a).sort())===JSON.stringify(Object.entries(b).sort());
const inputs=values=>Object.fromEntries(fields.map(([key,,unit])=>[key,values[key]===undefined?'':String(values[key]/unit)]));
function values(draft){
 const result={};
 for(const [key,,unit] of fields){
  if(draft[key]==='')continue;
  const value=Number(draft[key])*unit;
  if(!Number.isFinite(value)||value<0||!Number.isSafeInteger(value))throw Error('Enter whole environment counts and a nonnegative disk size.');
  result[key]=value;
 }
 return result;
}
function Limits({label,draft,setDraft,disabled}){
 return <fieldset disabled={disabled}><legend>{label}</legend>{fields.map(([key,title,unit])=><label className="a-field" key={key}>{title}
  <input type="number" min="0" step={unit===1?'1':'any'} inputMode={unit===1?'numeric':'decimal'} value={draft[key]} placeholder="Inherit" onChange={event=>setDraft(old=>({...old,[key]:event.target.value}))}/>
 </label>)}</fieldset>;
}
export function TestingSettings({act,navigate}){
 const [data,setData]=useState(null),[defaults,setDefaults]=useState(inputs({})),[overrides,setOverrides]=useState(inputs({})),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const pending=useRef(false),action=useRef(act);action.current=act;
 const dirty=!!data&&(JSON.stringify(defaults)!==JSON.stringify(inputs(data.defaults))||JSON.stringify(overrides)!==JSON.stringify(inputs(data.runnerOverrides)));
 function accept(result){setData(result);setDefaults(inputs(result.defaults));setOverrides(inputs(result.runnerOverrides))}
 useSettingsDraft('testing-capacity',{label:'Testing & resources',dirty,review:()=>navigate('testing'),discard:()=>{if(data)accept(data)}});
 useEffect(()=>{let live=true;action.current('testing.capacity.read',{}).then(response=>{
  if(!response?.accepted||!response.result)throw Error(response?.error||'Testing settings could not be read.');
  if(live)accept(response.result);
 }).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 async function save(){
  if(pending.current)return;
  pending.current=true;setBusy(true);setError('');setNotice('');
  let intended;
  try{
   intended={expectedRevision:data.revision,defaults:values(defaults),...(data.runnerId?{runnerId:data.runnerId,runnerOverrides:values(overrides)}:{})};
   const response=await action.current('testing.capacity.save',intended);
   if(!response?.accepted||!response.result)throw Error(response?.error||'Testing settings could not be saved.');
   accept(response.result);setNotice('Testing limits saved. Waiting checks will reconsider capacity. Work already admitted keeps running.');
  }catch(e){
   // A lost acknowledgement is not evidence of failure. Read the saved
   // values once; never replay the mutation to discover its outcome.
   let confirmed=false;
   if(intended)try{
    const response=await action.current('testing.capacity.read',{}),current=response?.result;
    if(response?.accepted&&current&&same(current.defaults,intended.defaults)&&(!intended.runnerId||(current.runnerId===intended.runnerId&&same(current.runnerOverrides,intended.runnerOverrides)))){
     accept(current);setNotice('Current testing settings match your changes. No settings were replayed.');confirmed=true;
    }
   }catch{}
   if(!confirmed)setError(e.message+' Your edits are kept. Refresh to review the current saved values.');
  }finally{pending.current=false;setBusy(false)}
 }
 async function refresh(){
  if(pending.current)return;
  pending.current=true;setBusy(true);setError('');
  try{
   const response=await action.current('testing.capacity.read',{});
   if(!response?.accepted||!response.result)throw Error(response?.error||'Testing settings could not be refreshed.');
   // Refresh updates the comparison token but never silently rebases an edited
   // runner form onto a different connection.
   accept(response.result);setNotice('Current settings loaded.');
  }catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Testing and resources">
  <p>Choose how much isolated testing Amplifier can run across your workspaces. Higher limits allow more work at once when the machine has enough resources.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!data?<button className="a-soft" disabled={busy} onClick={refresh}>Reload testing settings</button>:<>
   <p>{data.scope}</p>
   <h4>{data.runner.backend?`${data.runner.backend==='incus'?'Incus':'Docker'} test runner`:'Test runner not ready'}</h4>
   <p>{data.runner.description}</p>
   <dl>{fields.map(([key,title,unit])=><React.Fragment key={key}><dt>{title}</dt><dd>{data.effective[key]===0?'No limit':data.effective[key]/unit} <small>· {sourceNames[data.sources[key]]||'Global default'}</small></dd></React.Fragment>)}</dl>
   <details><summary>Advanced limits</summary>
    <p>Zero disables a limit. Leave a field empty to inherit the runner configuration or backend default. Lowering a limit prevents new admissions until there is room; it does not stop active work.</p>
    <Limits label="Global defaults" draft={defaults} setDraft={setDefaults} disabled={busy}/>
    {data.runnerId&&<details><summary>Overrides for this runner connection</summary>
     <p>Optional values for this configured connection only. Empty fields use the global defaults. A different connection, including an alias, has its own override; these are not machine-wide settings.</p>
     <Limits label="Runner overrides" draft={overrides} setDraft={setOverrides} disabled={busy}/>
    </details>}
    <p className="a-caption">{data.limitsScope} Machine resource budgets are shown below. Queue and profile limits are available in Test execution limits.</p>
   </details>
   <SettingsActions><button className="a-primary" disabled={busy||!dirty} onClick={save}>Save testing limits</button><button className="a-soft" disabled={busy} onClick={refresh}>{dirty?'Discard changes and reload':'Refresh settings'}</button></SettingsActions>
  </>}
  <MachineResources act={act} navigate={navigate}/>
  <TestRunnerSettings act={act} navigate={navigate}/>
  <ExecutionLimits act={act} navigate={navigate}/>
 </section>;
}

const machineFields=[['maxReservedCpu','Reserved CPU cores',1],['maxReservedMemoryMiB','Reserved memory (GiB)',1024],['minAvailableMemoryMiB','Keep memory available (GiB)',1024],['minFreeBytes','Keep home disk space free (GiB)',gib],['maxLoadPercent','Maximum CPU load (%)',1],['maxPreparing','Concurrent dependency builds',1]];
const machineInputs=value=>Object.fromEntries(machineFields.map(([key,,unit])=>[key,value[key]===undefined?'':String(value[key]/unit)]));
export function MachineResources({act,navigate,draftId='testing-machine',title='This machine’s test resources'}){
 const [data,setData]=useState(null),[draft,setDraft]=useState(machineInputs({})),[busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
 const pending=useRef(false),action=useRef(act);action.current=act;
 const dirty=!!data?.supported&&JSON.stringify(draft)!==JSON.stringify(machineInputs(data.limits));
 function accept(current){setData(current);if(current.supported)setDraft(machineInputs(current.limits))}
 useSettingsDraft(draftId,{label:'Machine test resources',dirty,review:()=>navigate('testing'),discard:()=>{if(data)accept(data)}});
 async function load(){const response=await action.current('testing.machine.read',{});if(!response?.accepted||!response.result)throw Error(response?.error||'Machine resources could not be read.');return response.result}
 useEffect(()=>{let live=true;load().then(current=>{if(live)accept(current)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 async function refresh(){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{accept(await load())}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}}
 async function save(){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setMessage('');let intended;
  try{
   const limits={};for(const [key,,unit] of machineFields){if(draft[key]==='')continue;const value=Number(draft[key])*unit;if(!Number.isSafeInteger(value)||value<0)throw Error('Enter nonnegative resource amounts.');limits[key]=value}
   intended={expectedRevision:data.revision,limits};
   const response=await action.current('testing.machine.save',intended);if(!response?.accepted||!response.result)throw Error(response?.error||'Machine limits could not be saved.');
   accept(response.result);setMessage('Machine limits saved. Active work keeps running. Waiting checks will reconsider capacity.');
  }catch(e){let confirmed=false;if(intended)try{const current=await load();if(current.supported&&same(current.limits,intended.limits)){accept(current);setMessage('Current machine limits match your changes. No settings were replayed.');confirmed=true}}catch{}if(!confirmed)setError(e.message+' Your edits are kept.');}
  finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Shared machine resources"><h4>{title}</h4>
  {error&&<p role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
  {data?.supported?<><p>{data.scope}</p><p>Reserved: {data.reserved.cpus} CPU cores and {(data.reserved.memoryMiB/1024).toFixed(1)} GiB memory across {data.reserved.count} checks. {data.reserved.preparing} dependency builds are admitted.</p>
   <p>Currently available: {(data.observed.availableMemoryMiB/1024).toFixed(1)} GiB memory and {(data.observed.freeBytes/gib).toFixed(1)} GiB disk space. These observations can change as other apps work.</p>
   <details><summary>Advanced machine budgets</summary><p>These settings are shared by cooperating local installations using this account. Empty uses the automatic value; zero disables that limit. CPU reservations allow oversubscription, so a core limit is not a measurement of current CPU use. Home disk space protects this account’s local storage; the runner’s disk limit separately protects container storage. Lowering a budget never stops admitted work.</p>
    <fieldset disabled={busy}><legend>Machine-wide limits for this account</legend>{machineFields.map(([key,label,unit])=><label className="a-field" key={key}>{label}<input type="number" min="0" step={unit===1?'1':'any'} value={draft[key]} placeholder={String(data.effective[key]/unit)} onChange={event=>setDraft(old=>({...old,[key]:event.target.value}))}/><small>Effective: {data.effective[key]===0?'No limit':data.effective[key]/unit} · {data.limits[key]===undefined?'Automatic':'Saved setting'}</small></label>)}</fieldset>
   </details><button className="a-primary" disabled={busy||!dirty} onClick={save}>Save machine budgets</button></>:<p>{data?.reason||'Loading machine resource information…'}</p>}
  <button className="a-soft" disabled={busy} onClick={refresh}>{dirty?'Discard machine edits and refresh':'Refresh machine resources'}</button>
 </section>;
}
