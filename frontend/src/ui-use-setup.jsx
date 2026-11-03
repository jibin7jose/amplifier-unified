import React,{useEffect,useRef,useState} from 'react';
import {settingsPatch} from './settings-navigation';

const pendingPhases=new Set(['queued','staging','qualified','activating','restart_pending']);
const labels={queued:'Queued',staging:'Preparing',qualified:'Checks passed',activating:'Installing',restart_pending:'Waiting for idle restart',installed:'Installed',already_installed:'Ready',failed:'Could not install',error:'Could not install',interrupted:'Interrupted'};
const requestKey=kind=>'amplifier.uiUseSetup.request.'+kind;
function savedRequest(kind){
 try{const value=JSON.parse(sessionStorage.getItem(requestKey(kind))||'null');return value&&typeof value.id==='string'&&value.id&&typeof value.args?.feature==='string'&&typeof value.args?.hostInstanceId==='string'?value:null}catch{return null}
}

export function UIUseSetup({state,act,kind,report,onConnect,connectionDisabled=false}){
 const [loaded,setLoaded]=useState(report||null),[pending,setPending]=useState(()=>savedRequest(kind)),[choice,setChoice]=useState(()=>savedRequest(kind)?.args.feature||null),[sending,setSending]=useState(false),[checking,setChecking]=useState(false),[error,setError]=useState('');
 const alive=useRef(true),request=useRef(false),checkSequence=useRef(0);
 const receipts=state.updates?.featureResults||{},changes=JSON.stringify(receipts);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;checkSequence.current++}},[]);
 useEffect(()=>{if(report)setLoaded(report)},[report]);
 async function checkSupport(){
  const sequence=++checkSequence.current;setChecking(true);
  try{
   const response=await fetch('/api/ui-use/v1/state');if(!response.ok)throw Error('Could not check optional support.');
   const value=await response.json();if(alive.current&&sequence===checkSequence.current){setLoaded(value);setError('')}
  }catch(e){if(alive.current&&sequence===checkSequence.current)setError(e.message)}
  finally{if(alive.current&&sequence===checkSequence.current)setChecking(false)}
 }
 useEffect(()=>{if(!report)checkSupport()},[!!report,changes,state.updates?.installedAt]);
 const matching=pending&&receipts[pending.id];
 useEffect(()=>{
  if(!pending||!matching)return;
  setError('');
  if(!pendingPhases.has(matching.phase)){
   try{if(savedRequest(kind)?.id===pending.id)sessionStorage.removeItem(requestKey(kind))}catch{}
   setPending(null);
  }
 },[pending?.id,matching?.phase,kind]);
 const current=loaded||report,setup=current?.featureSetup;
 const features=(setup?.features||[]).filter(row=>kind==='browser'?row.id==='managed-browser':row.id.startsWith('decisions-'));
 const feature=kind==='browser'?'managed-browser':choice||features.find(row=>row.installed)?.id||features[0]?.id;
 const selected=features.find(row=>row.id===feature),results=Object.values(receipts).filter(row=>row.feature===feature).sort((a,b)=>b.updatedAt-a.updatedAt),result=results[0];
 const busy=sending||checking||setup?.pending||Object.values(receipts).some(row=>pendingPhases.has(row.phase));
 async function install(reconcile=false){
  if(request.current||checking||(!reconcile&&(busy||pending||!setup?.supported||!current?.hostInstanceId)))return;
  const retained=pending||savedRequest(kind);
  if(!reconcile&&retained){setPending(retained);if(kind!=='browser')setChoice(retained.args.feature);return}
  const attempt=reconcile?retained:{id:crypto.randomUUID(),args:{feature,hostInstanceId:current.hostInstanceId}};
  if(!attempt)return;
  request.current=true;setSending(true);setError('');
  try{
   // Save before sending so a lost reply or remount cannot create a second
   // installation request. Reconciliation reuses both identity and exact args.
   sessionStorage.setItem(requestKey(kind),JSON.stringify(attempt));setPending(attempt);
   const receipt=await act('updates.featureInstall',attempt.args,{id:attempt.id,propagateError:true});
   if(!receipt?.accepted||receipt.requestId!==attempt.id)throw Error('The request could not be confirmed. Check this installation request before starting another.');
  }catch(e){
   // act() also waits for state after acceptance. A rejected state refresh is
   // not a rejected installation; only the action endpoint can prove refusal.
   const rejected=e.requestPath==='/api/actions'&&e.status>=400&&e.status<500&&e.status!==408&&!e.unconfirmed&&!e.receipt;
   if(rejected){try{if(savedRequest(kind)?.id===attempt.id)sessionStorage.removeItem(requestKey(kind))}catch{}if(alive.current)setPending(null)}
   if(alive.current)setError(e.message);
  }
  finally{request.current=false;if(alive.current)setSending(false)}
 }
 return <details className="a-everyday-disclosure" data-part="ui-use-setup">
  <summary>{kind==='browser'?'Add browser support':'Add a decision provider'}</summary>
  <p>{kind==='browser'?'Installs a private browser on the Amplifier host. You can also connect a browser on your own device.':'Install support for the provider you want, then choose its connection and model. Local support connects to a model service you already run; it does not download a model.'}</p>
  {!current?<p role="status">{error||'Checking installed support…'}</p>:!setup?.supported?<p>{setup?.reason||'Ask the service owner to add this support.'}</p>:<>
   {kind!=='browser'&&<label>Provider support <select aria-label="Decision provider support" value={feature||''} disabled={busy||Boolean(pending)} onChange={e=>{setChoice(e.target.value);setError('')}}>{features.map(row=><option value={row.id} key={row.id}>{row.label}{row.installed?' · installed':''}</option>)}</select></label>}
   {selected?.installed&&<p>Support is installed.{kind==='browser'?' Check or repair its browser files below.':' Next, connect an account and choose where to use its decision model.'}</p>}
   {kind!=='browser'&&selected?.installed&&onConnect&&selected.providerModule&&<button type="button" className="a-primary" disabled={busy||Boolean(pending)||connectionDisabled} onClick={()=>onConnect(selected.providerModule)}>Connect this provider</button>}
   <button className="a-soft" disabled={busy||!selected||Boolean(pending)} onClick={()=>kind!=='browser'&&selected?.installed?checkSupport():install()}>{busy?'Preparing support…':kind==='browser'&&selected?.installed?'Check browser installation':selected?.installed?'Check installed support':kind==='browser'?'Install Amplifier browser':'Install provider support'}</button>
   <p className="a-caption">Keeps your existing setup. Any app restart waits until work is idle.{kind!=='browser'?' Installing support does not connect an account or change your model.':''}</p>
  </>}
  {result&&<p role="status">{labels[result.phase]||result.phase.replaceAll('_',' ')}. {kind!=='browser'&&['installed','already_installed'].includes(result.phase)?'Connect an account to configure its decision model.':result.detail}</p>}
  {current&&error&&<p role="alert">{error}</p>}
  {pending&&!matching&&<><p role="status">The installation request is awaiting confirmation. Checking it uses the same request and does not start a second installation.</p><button className="a-link" disabled={sending||checking} onClick={()=>install(true)}>Check installation request</button></>}
  {error&&<button className="a-link" disabled={sending||checking} onClick={checkSupport}>Check support again</button>}
  <button className="a-link" onClick={()=>act('view.update',{patch:settingsPatch('updates')})}>Open Updates</button>
 </details>;
}
