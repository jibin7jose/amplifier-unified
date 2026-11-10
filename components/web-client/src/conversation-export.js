// Consume a frozen snapshot, never the currently selected/paged conversation.
export async function deliverConversationExport(effect,{request,download,clipboard}){
 let status='ready',message=effect.destination==='clipboard'?'Copied conversation Markdown':'Markdown download started';
 try{
  const snapshot=await request(effect.url);
  if(effect.destination==='clipboard')await clipboard.writeText(snapshot.content);
  else download(snapshot.filename,snapshot.content,snapshot.mimeType);
 }catch(error){status='error';message='Could not export: '+error.message}
 await request('/api/actions',{method:'POST',body:{action:'session.exportResult',args:{requestId:effect.requestId,status,message}}});
}
