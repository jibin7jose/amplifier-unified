// Labels may share a provider type; option values always identify a connection.
export function providerOptions(providers){
 const type=row=>row.module||row.id;
 const counts=new Map();
 for(const row of providers)counts.set(type(row),(counts.get(type(row))||0)+1);
 return providers.map(row=>({id:row.id,label:(row.displayName||row.id)+(counts.get(type(row))>1?` (${row.id})`:'')}));
}
