export function moduleRows(plan){
 const rows=[];
 for(const section of ['providers','tools','hooks'])for(const [index,entry]of (Array.isArray(plan?.[section])?plan[section]:[]).entries()){
  rows.push({key:`${section}:${index}`,section,index,module:entry.module||'Unnamed module',id:entry.instance_id||entry.id||entry.module,enabled:entry.enabled!==false,source:entry.source,config:entry.config||{},entry});
 }
 for(const section of ['orchestrator','context']){const entry=plan?.session?.[section];if(entry)rows.push({key:`session:${section}`,section,index:section,module:typeof entry==='string'?entry:entry.module,id:entry.id||entry.module||entry,enabled:entry.enabled!==false,source:entry.source,config:entry.config||{},entry,required:true})}
 return rows;
}
export function updateModule(plan,key,patch){
 const copy=structuredClone(plan),row=moduleRows(copy).find(row=>row.key===key);
 if(!row)throw new Error('Select a module to edit.');
 const entry=typeof row.entry==='string'?{module:row.entry}:row.entry;
 const updated={...entry,...patch};
 if(row.required){if(patch.enabled===false)throw new Error('The session requires an orchestrator and a context module.');copy.session[row.section]=updated}else copy[row.section][row.index]=updated;
 return copy;
}
export function parsePlan(text){
 const value=JSON.parse(text);
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Configuration must be a JSON object.');
 return value;
}
