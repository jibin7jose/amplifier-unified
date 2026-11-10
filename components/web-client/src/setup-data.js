export function providerConfig(text){
 const value=JSON.parse(text);
 if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Provider configuration must be a JSON object.');
 function check(node){
  if(!node||typeof node!=='object')return;
  for(const[key,item]of Object.entries(node)){
   if(/api.?key|password|secret|(?:access|refresh|github|auth).?token/i.test(key)&&typeof item==='string'&&!/^\$\{[A-Za-z_][A-Za-z0-9_]*\}$/.test(item)&&!/^\[redacted\]$/i.test(item))throw new Error('Enter credentials in the private API key field, or use an environment reference in configuration.');
   check(item);
  }
 }
 check(value);
 return value;
}
export function modelOptions(value){
 const models=Array.isArray(value)?value:value?.models||[];
 return models.map(model=>typeof model==='string'?{id:model,name:model}:{id:model.id||model.name||model.model,name:model.display_name||model.displayName||model.name||model.id||model.model}).filter(model=>model.id);
}
export function connectionTestability(catalog){
 const metadata=catalog?.metadata,origin=metadata?.modelListInfo?.source||catalog?.provenance;
 if(metadata?.supports?.test===false||['configured','static','simulated','unsupported'].includes(origin)){
  const label={configured:'Configured models',static:'Static recommendations',simulated:'Simulated models'}[origin]||'This catalog';
  return {supported:false,reason:`${label} do not verify connectivity. This provider has no API catalog check.`};
 }
 return {supported:true,reason:'Checking verifies the API model catalog without sending a chat request.'};
}
export function updateRole(matrix,role,patch){
 return {...matrix,roles:{...matrix?.roles,[role]:{...matrix?.roles?.[role],...patch}}};
}
export function updateCandidate(matrix,role,index,patch){
 const candidates=[...(matrix?.roles?.[role]?.candidates||[])];
 candidates[index]={...candidates[index],...patch};
 return updateRole(matrix,role,{candidates});
}

export function blankRouting(name='my-routing'){
 return {name,roles:{general:{description:'General conversation and work',candidates:[{provider:'',model:''}]},fast:{description:'Quick and lightweight work',candidates:[{provider:'',model:''}]}}};
}
export function safeLoginUrl(value){
 try{const url=new URL(value);if(url.protocol!=='https:'||url.username||url.password||url.hash)return null;for(const key of url.searchParams.keys())if(/^(id_token_hint|access_token|refresh_token|id_token|code|client_secret)$/i.test(key))return null;return url.href}catch{return null}
}

export function providerFields(metadata,config={}){
 const fields=metadata?.configSchema?.fields||metadata?.info?.config_fields||[];
 return fields.filter(field=>field.id&&field.field_type!=='secret'&&!/api.?key|password|secret|(?:access|refresh|github|auth).?token/i.test(field.id)&&!['model','default_model'].includes(field.id)).filter(field=>{
  if(field.requires_model&&!config.default_model&&!config.model)return false;
  return Object.entries(field.show_when||{}).every(([key,expected])=>{
   const actual=String(config[key]??'').toLowerCase(),value=String(expected).toLowerCase();
   if(value.startsWith('matches:')){try{return new RegExp(String(expected).slice(8),'i').test(actual)}catch{return false}}
   for(const[prefix,test] of [['not_contains:',v=>!actual.includes(v)],['contains:',v=>actual.includes(v)],['not_startswith:',v=>!actual.startsWith(v)],['startswith:',v=>actual.startsWith(v)]])if(value.startsWith(prefix))return test(value.slice(prefix.length));
   return actual===value;
  });
 });
}

// Candidate positions are meaningful only for the exact choices that opened
// the order draft. Refresh a stale draft before allowing it to be applied.
export function currentRoutingOrder(candidates,role,order){
 return !!order&&order.role===role&&order.source===JSON.stringify(candidates)&&Array.isArray(order.ids)&&order.ids.length===candidates.length&&new Set(order.ids).size===candidates.length&&order.ids.every(id=>candidates.some((_,index)=>String(index)===id));
}
export function routingOrderDraft(candidates,role,previous){
 if(currentRoutingOrder(candidates,role,previous))return previous;
 return {role,ids:candidates.map((_,index)=>String(index)),source:JSON.stringify(candidates),notice:previous?.role===role?'The choices changed. Review the current order before saving.':''};
}
