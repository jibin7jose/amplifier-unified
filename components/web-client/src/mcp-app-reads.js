const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;

// Only the caller's explicitly read-only tool definitions enter this gate.
// Mutations keep their own request IDs and never share promises or receipts.
export function createMcpReadGate({limit=2,maxQueued=4,isVisible=()=>true}={}){
 const pending=new Map(),queue=[];let running=0,closed=false;
 const pause=message=>Object.assign(new Error(message),{backgroundReadPaused:true});
 const unavailable=()=>pause(closed?'This tool view closed.':'Background reads are paused. Refresh when the tool view is visible.');
 const drain=()=>{
  while(queue.length&&running<limit){
   const item=queue.shift();
   if(closed||!isVisible()){item.reject(unavailable());continue}
   running++;
   Promise.resolve().then(item.run).then(item.resolve,item.reject).finally(()=>{running--;drain()});
  }
 };
 return {
  run(params,run){
   if(closed||!isVisible())return Promise.reject(unavailable());
   const key=JSON.stringify(canonical({name:params.name,arguments:params.arguments||{}}));
   if(pending.has(key))return pending.get(key);
   if(running>=limit&&queue.length>=maxQueued)return Promise.reject(pause('Too many background reads. Wait for the current refresh to finish.'));
   const promise=new Promise((resolve,reject)=>{queue.push({run,resolve,reject});drain()}).finally(()=>pending.delete(key));
   pending.set(key,promise);return promise;
  },
  close(){closed=true;while(queue.length)queue.shift().reject(unavailable())},
 };
}
