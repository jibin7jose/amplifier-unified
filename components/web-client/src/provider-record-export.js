// Export only the explicitly loaded provider event, never reconstructed input.
export function providerRecordExport(node,field,text){
 if(!['request','response'].includes(field)||text==null)throw Error('Load a recorded request or response first.');
 const capture=node[field+'Capture']||{},reference=node[field+'Detail']||{};
 let payload=text,format='text';
 if(typeof text==='string'){try{payload=JSON.parse(text);format='json'}catch{}}
 const content={schemaVersion:1,kind:`provider.${field}`,sessionId:node.sessionId??null,callId:node.id,
  provider:node.provider??null,model:node.model??null,startedAt:node.startedAt??null,endedAt:node.endedAt??null,
  capture:{redacted:typeof capture.redacted==='boolean'?capture.redacted:null,
   truncated:typeof capture.truncated==='boolean'?capture.truncated:null,
   limitCharacters:capture.limit_chars??null,recordDigest:reference.digest??null,
   note:'Saved provider event. May contain a provider summary; omitted capture content cannot be recovered by exporting.'},
  format,payload};
 const id=String(node.id||'call').replace(/[^a-zA-Z0-9_-]/g,'-').slice(0,100);
 return {filename:`amplifier-${id}-${field}${capture.truncated?'-partial':''}.json`,content};
}
