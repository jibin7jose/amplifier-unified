// Wait only for the committed read projection. No command is retried and no
// extra request is issued while the existing state stream catches up.
export function waitForSnapshot(host,select,{signal,timeout=10000,timeoutMessage='Recent could not be refreshed. The previous chats are kept.'}={}){
 return new Promise((resolve,reject)=>{
  let finished=false,unsubscribe=()=>{},timer;
  const finish=(error,value)=>{if(finished)return;finished=true;clearTimeout(timer);unsubscribe();signal?.removeEventListener('abort',abort);error?reject(error):resolve(value)};
  const abort=()=>finish(new DOMException('Snapshot wait cancelled','AbortError'));
  const check=()=>{try{const value=select(host.getSnapshot());if(value)finish(null,value)}catch(error){finish(error)}};
  if(signal?.aborted){abort();return}
  signal?.addEventListener('abort',abort,{once:true});
  timer=setTimeout(()=>finish(Error(timeoutMessage)),timeout);
  unsubscribe=host.subscribe(check);if(finished)unsubscribe();else check();
 });
}
