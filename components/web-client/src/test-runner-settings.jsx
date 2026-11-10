import React,{useEffect,useRef,useState} from 'react';
import {useSettingsDraft} from './settings-drafts';
import {TestRunnerBackendLimits} from './test-runner-backend-limits';
import {MachineResources} from './testing-settings';

const unfinished=operation=>!['connected','failed','cancelled','changed'].includes(operation.status);

export function TestRunnerSettings({act,navigate}){
 const [data,setData]=useState(null),[name,setName]=useState(''),[connection,setConnection]=useState(''),[configPath,setConfigPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const pending=useRef(false),action=useRef(act),intent=useRef(null);action.current=act;
 const dirty=!!(name||connection||configPath);
 const clear=()=>{setName('');setConnection('');setConfigPath('')};
 useSettingsDraft('testing-runners',{label:'Connected test machines',dirty,review:()=>navigate('testing'),discard:clear});
 async function load(){const response=await action.current('testing.runners.read',{});if(!response?.accepted||!response.result)throw Error(response?.error||'Test machines could not be read.');return response.result}
 useEffect(()=>{let live=true;load().then(result=>{if(live)setData(result)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 const watching=data?.enrollments?.some(unfinished);
 useEffect(()=>{
  if(!watching)return;
  let live=true,reading=false;
  const timer=setInterval(async()=>{if(pending.current||reading)return;reading=true;try{const result=await load();if(live)setData(result)}catch{/* Keep the last saved progress. Manual refresh remains available. */}finally{reading=false}},2000);
  return()=>{live=false;clearInterval(timer)};
 },[watching]);
 async function refresh(){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{setData(await load())}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}}
 async function change(operation,args,message){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');
  try{const response=await action.current(operation,args);if(!response?.accepted||!response.result)throw Error(response?.error||'The change was not confirmed. Refresh the saved status before trying again.');setData(response.result);setNotice(message)}catch(e){setError(e.message)}finally{pending.current=false;setBusy(false)}
 }
 async function connect(runner){
  if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');
  const operation=runner?'testing.runners.refresh':'testing.runners.register';
  const desired=runner?{id:runner.id}:{name:name.trim(),connection:connection.trim(),configPath:configPath.trim()};
  const key=JSON.stringify([operation,desired]);
  if(intent.current?.key!==key)intent.current={key,requestId:crypto.randomUUID()};
  const requestId=intent.current.requestId;
  try{
   const response=await action.current(operation,{...desired,requestId,expectedRevision:data.revision});
   if(!response?.accepted||!response.result)throw Error(response?.error||'The connection check was not confirmed.');
   setData(response.result);if(!runner)clear();intent.current=null;
   setNotice('Connection check saved. You can keep working or close Settings while it waits and checks.');
  }catch(e){
   let confirmed=false;
   try{const current=await load();setData(current);confirmed=current.enrollments?.some(item=>item.requestId===requestId);if(confirmed){if(!runner)clear();intent.current=null;setNotice('The saved connection check was found. It was not repeated.')}}catch{}
   if(!confirmed)setError(e.message+' Your entries are kept.');
  }finally{pending.current=false;setBusy(false)}
 }
 return <section aria-label="Connected test machines"><h4>Connected test machines</h4><p>Run checks on a Linux test machine from Mac, Windows, WSL or Linux. This computer needs Git and a saved SSH connection; it does not need Docker or a local test environment.</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {data&&<><p className="a-caption">{data.scope}</p>
   {(data.enrollments||[]).map(operation=><div className="a-card" key={operation.id} aria-label={'Connection check: '+operation.name}><strong>{operation.name}</strong><p role="status">{operation.detail}</p>
    {operation.canCancel&&<button className="a-soft" disabled={busy} onClick={()=>change('testing.runners.enrollment.cancel',{id:operation.id},'Cancellation requested. The original check will be cleaned up before cancellation is confirmed.')}>Cancel connection check</button>}
    {operation.canReconcile&&<button className="a-soft" disabled={busy} onClick={()=>change('testing.runners.enrollment.reconcile',{id:operation.id},'Checking the original saved result. No replacement test was requested.')}>Check saved result</button>}
   </div>)}
   {data.runners.length===0?<p>No test machines connected. Connect and check one to start testing remotely.</p>:data.runners.map(runner=><details key={runner.id}><summary>{runner.name} · {runner.enabled?'Allowed for new checks':'Paused for new checks'}</summary><p>Connection: {runner.connection}</p>
    <p>{runner.lastCheck?.status==='passed'?<>Last connection check passed {new Date(runner.lastCheck.checkedAt*1000).toLocaleString()}. Execution, result download and cleanup were verified using a small Python check.</>:'This saved connection has not completed the new connection check yet.'} Availability is checked again before each new job.</p>
    <p className="a-caption">Supported check types: {runner.profiles.join(', ')}. Project dependencies are prepared separately for each requested check.</p>
    <button className="a-soft" disabled={busy} onClick={()=>change('testing.runners.enable',{id:runner.id,enabled:!runner.enabled,expectedRevision:data.revision},'Test machine preference saved. Existing checks keep their original machine.')}>{runner.enabled?'Pause new checks':'Allow new checks'}</button>
    <button className="a-soft" disabled={busy||data.enrollments?.some(item=>unfinished(item)&&item.connection===runner.connection)} onClick={()=>connect(runner)}>Check connection and controller</button>
    <MachineResources act={(operation,args)=>act(operation.replace('testing.machine.','testing.runners.machine.'),{...args,id:runner.id})} navigate={navigate} draftId={'testing-machine-'+runner.id} title={runner.name+' test resources'}/><TestRunnerBackendLimits act={act} navigate={navigate} id={runner.id} name={runner.name}/>
   </details>)}
   <details><summary>Connect a test machine</summary><p>Choose a machine with testing already set up in its Amplifier Settings. Its saved SSH connection must work without an interactive sign-in and its identity must already be trusted. A small, fixed test verifies the connection and removes its test environment before enabling new checks. Your files and credentials are not copied for this check.</p>
    <fieldset disabled={busy}><legend>Test machine connection</legend><label className="a-field">Name to show<input value={name} maxLength={80} onChange={event=>setName(event.target.value)}/></label><label className="a-field">Saved SSH connection<input value={connection} maxLength={200} placeholder="test-machine" onChange={event=>setConnection(event.target.value)}/></label><details><summary>Advanced connection settings</summary><p>The target uses an existing native Linux Incus runner. Amplifier installs its verified controller in that account’s private storage. It does not install a container service or change system settings. Only this account’s SSH access is used; authentication secrets and agent forwarding are not transferred.</p><label className="a-field">Existing runner configuration file<input value={configPath} maxLength={4096} placeholder="Use the machine’s default Amplifier configuration" onChange={event=>setConfigPath(event.target.value)}/></label></details></fieldset>
    <button className="a-primary" disabled={busy||!name.trim()||!connection.trim()} onClick={()=>connect(null)}>Connect and check</button>
   </details>
  </>}
  <button className="a-soft" disabled={busy} onClick={refresh}>Refresh test machines</button>
 </section>;
}
