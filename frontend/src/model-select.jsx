import React,{useRef,useState} from 'react';
import {useRegionActivity} from './activity-region';
import {modelOptions} from './setup-data';
import {useListFilter} from './list-filter.jsx';

const originNotices={configured:'Configured models — not checked against the service.',static:'Static recommendations — not a live inventory.',simulated:'Simulated models — test responses only.'};
export function ModelOriginNotice({entry}){
 const notice=originNotices[entry?.provenance];
 return notice?<small role="status" data-model-origin={entry.provenance}>{notice}</small>:null;
}

// Only the host's exact connection entry supplies options. Saved/default IDs
// are displayed separately and never promoted into discovery results.
export function ModelSelect({id,label='Model',value='',onChange,onCommit,entry,models:provided,state,act,disabled=false,placeholder='Enter an exact model or deployment ID',editable=false}){
 const loading=entry?.phase==='working'||entry?.phase==='queued',region=useRef(null),edited=useRef(false),identity=useRef(entry?.sharedCatalogKey);
 const [exactScope,setExactScope]=useState(null),scope=id+':'+(entry?.sharedCatalogKey??'');
 if(identity.current!==entry?.sharedCatalogKey){edited.current=false;identity.current=entry?.sharedCatalogKey}
 const models=modelOptions(entry?.models??provided??[]);
 const manual=editable||exactScope===scope||entry?.supported===false||entry?.phase==='error'||(!loading&&!models.length);
 const listed=models.some(row=>row.id===value);
 const originNotice=originNotices[entry?.provenance];
 const listName=originNotice?'catalog':'discovered list';
 useRegionActivity(region,loading);
 const [shown,filter]=useListFilter(state,act,'model-picker-'+id,models,row=>[row.id,row.name],'Search '+label.toLowerCase(),{simple:true});
 return <div className="a-model-select" ref={region} data-activity-region="models">
  <ModelOriginNotice entry={entry}/>
  {value&&!listed&&<small role="status">Saved selection: {value} (not in the {listName})</small>}
  {models.length>0&&<>{filter}<select id={manual?id+'-catalog':id} aria-label={manual?'Choose '+label.toLowerCase()+' from catalog':label} value={shown.some(row=>row.id===value)?value:''} disabled={disabled} data-action="view.update" onChange={e=>{if(e.target.value){onChange(e.target.value);onCommit?.(e.target.value)}}}>
   <option value="">{originNotice?'Choose a model…':'Choose a discovered model…'}</option>{shown.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
  </select></>}
  {!manual&&<button type="button" disabled={disabled} onClick={()=>setExactScope(scope)}>Enter an exact model ID</button>}
  {manual?<><label htmlFor={id}>Exact model ID</label><input id={id} aria-label={label} value={value} disabled={disabled} placeholder={placeholder} data-action="view.update" onChange={e=>{edited.current=true;onChange(e.target.value)}} onBlur={e=>{if(edited.current&&e.target.value.trim()){edited.current=false;onCommit?.(e.target.value.trim())}}} onKeyDown={e=>{if(e.key==='Enter'&&e.currentTarget.value.trim()){e.preventDefault();edited.current=false;onCommit?.(e.currentTarget.value.trim())}}}/><small>Manual ID — not verified by discovery. The connection checks it when you send.</small></>:!models.length&&<small role="status">Loading provider models…</small>}
  {entry?.error&&<small className="a-danger" role="status">{entry.error}</small>}
  {entry?.phase==='error'&&!entry.error&&<small className="a-danger" role="status">Model list unavailable. Enter a model ID or refresh this connection.</small>}
  {entry?.loadedAt!=null&&<small>Last successful refresh: {new Date(entry.loadedAt*1000).toLocaleString()}</small>}
  {entry?.stale&&<small role="status">Model list is stale. Refresh to check this connection.</small>}
  {entry?.phase==='ready'&&!models.length&&<small>{entry.supported===false?'This connection does not supply verified model discovery.':'This connection returned no models.'}</small>}
 </div>;
}
