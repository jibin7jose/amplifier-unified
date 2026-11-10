import React,{useEffect,useRef,useState} from 'react';

export function WorkerRetentionSettings({state,act}){
 const policy=state.runtime?.retention;
 const [count,setCount]=useState('32'),[hours,setHours]=useState('12'),[saving,setSaving]=useState(false),[error,setError]=useState('');
 const queue=useRef(Promise.resolve());
 useEffect(()=>{if(policy)setCount(String(policy.max_warm_workers))},[policy?.max_warm_workers]);
 useEffect(()=>{if(policy)setHours(String(policy.idle_timeout_hours))},[policy?.idle_timeout_hours]);
 if(!policy)return null;
 function save(patch){
  setSaving(true);setError('');
  const next=queue.current.catch(()=>{}).then(async()=>{const receipt=await act('runtime.retention.update',{patch});if(receipt?.accepted!==true)throw Error(receipt?.error||'Could not save readiness settings. Try again.');});
  queue.current=next;
  next.catch(e=>setError(e.message)).finally(()=>{if(queue.current===next)setSaving(false)});
 }
 const commit=(event,key)=>{if(event.target.value!==''&&event.target.checkValidity()&&Number(event.target.value)!==policy[key])save({[key]:Number(event.target.value)})};
 return <section aria-label="Ready conversations">
  <p>Keep recently used conversations ready for their next message. These limits apply to idle conversations on this host; work in progress and pending interactions stay protected.</p>
  <label htmlFor="warm-worker-count">Conversations to keep ready</label><input id="warm-worker-count" type="number" min="0" step="1" value={count} onChange={event=>setCount(event.target.value)} onBlur={event=>commit(event,'max_warm_workers')} onKeyDown={event=>{if(event.key==='Enter')event.currentTarget.blur()}}/>
  <label htmlFor="warm-worker-hours">Hours to keep an idle conversation ready</label><input id="warm-worker-hours" type="number" min="0" step="any" value={hours} onChange={event=>setHours(event.target.value)} onBlur={event=>commit(event,'idle_timeout_hours')} onKeyDown={event=>{if(event.key==='Enter')event.currentTarget.blur()}}/>
  <p className="a-caption">Changes save automatically. Number fields save when you leave the field or press Enter. Setting either limit to zero releases idle conversations as soon as they settle without starting new work. Saved history remains available.</p>
  {saving&&<p role="status">Saving readiness settings…</p>}{error&&<p role="alert">{error}</p>}
 </section>;
}
