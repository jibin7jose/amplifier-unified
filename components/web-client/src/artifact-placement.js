// Keep a user-anchored output with its originating request, after that request's
// visible work and responses. Do not promote it ahead of the work that made it.
export function withArtifactRows(rows){
 const result=[];let origin=null;
 const append=message=>result.push({kind:'artifacts',id:`artifacts:${message.id}`,message});
 for(const row of rows){
  if(row.kind==='message'&&row.message.role==='user'){
   if(origin)append(origin);
   origin=row.message;
  }
  result.push(row);
  if(row.kind==='message'&&row.message.role!=='user')append(row.message);
 }
 if(origin)append(origin);
 return result;
}
