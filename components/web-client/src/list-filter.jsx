import React,{useEffect,useState} from 'react';
import {Search,X} from 'lucide-react';
import {filterList} from './list-filter';
export function useListFilter(state,act,id,items,fields,label,{simple=false}={}){
 const shared=state.view?.settingsFilters?.[id]||'',[query,setQuery]=useState(shared);
 useEffect(()=>setQuery(shared),[shared]);
 const rows=filterList(items,query,fields);
 const change=value=>{setQuery(value);act('view.update',{patch:{settingsFilters:{...state.view?.settingsFilters,[id]:value}}})};
 const control=<div className="a-list-filter"><label htmlFor={'filter-'+id}><Search/>{label}</label><div className="a-inline-form"><input id={'filter-'+id} type="search" value={query} placeholder={simple?label:"Search or pattern, e.g. *openai*"} data-action="view.update" onChange={e=>change(e.target.value)}/>{query&&<button type="button" className="a-icon" aria-label={'Clear '+label.toLowerCase()} data-action="view.update" onClick={()=>change('')}><X/></button>}</div><small role="status">{rows.length} of {items.length}{query&&!rows.length?' · No matches':''}{!simple&&<> · Text search or wildcards: * ? [abc] [!abc]</>}</small></div>;
 return [rows,control,query];
}
