export function changedFrontend(loaded,installed){
 return validFrontend(loaded)&&validFrontend(installed)&&(loaded.id!==installed.id||loaded.version!==installed.version);
}

function validFrontend(value){
 return typeof value?.id==='string'&&/^[a-f0-9]{12,64}$/.test(value.id)
  &&typeof value.version==='string'&&value.version.length>0&&value.version.length<=60;
}

async function readInstalledFrontend(signal){
 const response=await fetch('/build.json',{cache:'no-store',credentials:'same-origin',signal});
 if(!response.ok)return null;
 const reader=response.body.getReader(),chunks=[];let size=0;
 try{
  while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>1024)throw Error('Build identity exceeds its limit.');chunks.push(value)}
 }finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
 const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length}
 return JSON.parse(new TextDecoder().decode(bytes));
}

// Installed interface discovery, not the server's scheduled published-release
// check. A current stream baseline owns identity; HTTP is failure recovery only.
export function createBuildDiscovery({loaded,onInstalled,read=readInstalledFrontend,
 now=Date.now,setTimer=setTimeout,clearTimer=clearTimeout,visible=()=>!document.hidden}){
 let stopped=false,healthy=false,hadBaseline=false,timer,request,generation=0,attempt=0;
 let due=now()+10000,installed=null;
 const enabled=validFrontend(loaded);
 const publish=value=>{
  const next=changedFrontend(loaded,value)?value:null;
  if(installed?.id!==next?.id||installed?.version!==next?.version){installed=next;onInstalled(next)}
 };
 const cancel=()=>{generation++;request?.controller.abort();if(request)clearTimer(request.timeout);request=null};
 const schedule=()=>{
  clearTimer(timer);timer=undefined;
  if(enabled&&!stopped&&!healthy&&!installed&&!request&&visible())timer=setTimer(check,Math.max(0,due-now()));
 };
 async function check(){
  timer=undefined;
  if(stopped||healthy||installed||request||!visible())return;
  const job={generation:++generation,controller:new AbortController()};request=job;
  due=now()+[60000,120000,300000][Math.min(attempt++,2)];
  job.timeout=setTimer(()=>job.controller.abort(),5000);
  try{
   const value=await read(job.controller.signal);
   if(!stopped&&!healthy&&job.generation===generation&&!job.controller.signal.aborted)publish(value);
  }catch{}finally{
   clearTimer(job.timeout);
   if(request===job){request=null;schedule()}
  }
 }
 schedule();
 return {
  observe(client){
   const frontend=client?.build?.frontend;
   const valid=client?.kind==='web'&&typeof client.hostInstanceId==='string'&&client.hostInstanceId.length>0&&validFrontend(frontend);
   if(valid){healthy=true;hadBaseline=true;attempt=0;cancel();publish(frontend);schedule()}
   else if(healthy){healthy=false;due=now()+(hadBaseline?60000:10000);schedule()}
  },
  wake(){if(!visible())cancel();schedule()},
  dispose(){stopped=true;clearTimer(timer);cancel()},
 };
}

export async function reloadWhenSaved({prepare,reload,assertReady=()=>{}}){
 assertReady();
 await prepare();
 // Preparation may yield while a surface receives another local edit.
 assertReady();
 reload();
}
