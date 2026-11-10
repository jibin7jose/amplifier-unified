// Opening a native notification is an explicit navigation in this browser only.
// Never accept commands, credentials, or an external redirect from the link.
export function chatDeepLink(search) {
 const id=new URLSearchParams(search).get('chat');
 return id&&id.length<=200&&!/[\x00-\x1f\x7f]/.test(id)?id:null;
}

// The URL is the durable, client-local intent. Only the newest navigation matters;
// failures wait for an explicit retry, never a reconnect-driven execution replay.
export function createChatLinkReceiver({location,history,launchQueue,onChange=()=>{}}) {
 let select=null,intent=null,running=false,firstLaunch=true;
 const initialURL=location.href;
 function publish(){onChange(intent?{id:intent.id,status:intent.status,error:intent.error}:null)}
 function writeTarget(id){
  const url=new URL(location.href);
  if(id)url.searchParams.set('chat',id);else url.searchParams.delete('chat');
  if(url.href!==location.href)history.replaceState(history.state,'',url);
 }
 function dismiss(){intent=null;writeTarget(null);publish()}
 async function drain(){
  if(running||!select)return;
  running=true;
  try{while(select&&intent?.status==='pending'){
   const current=intent;current.status='opening';publish();
   try{
    const result=await select(current.id,()=>intent===current);
    if(intent!==current)continue; // A newer link or user navigation superseded it.
    if(!result?.accepted)throw Error(result?.error||'The chat could not be opened.');
    dismiss();
   }catch(error){
    if(intent!==current)continue;
    current.status='failed';current.error=error;publish();
   }
  }}finally{running=false}
 }
 function receive(target){
  let url;try{url=new URL(target,location.href)}catch{return}
  const id=chatDeepLink(url.search);
  if(url.origin!==location.origin||url.pathname!==location.pathname||!id)return;
  if(intent?.id===id&&intent.status!=='failed')return;
  writeTarget(id);intent={id,status:'pending',error:null};publish();void drain();
 }
 receive(initialURL);
 launchQueue?.setConsumer?.(params=>{
  const duplicate=firstLaunch&&params.targetURL===initialURL;firstLaunch=false;
  if(params.targetURL&&!duplicate)receive(params.targetURL);
 });
 return {
  bind(callback){select=callback;void drain();return()=>{if(select===callback)select=null}},
  receive,dismiss,
  retry(){if(intent?.status==='failed'){intent.status='pending';intent.error=null;publish();void drain()}}
 };
}
