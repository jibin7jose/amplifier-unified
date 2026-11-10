// Preview labels never rename saved chats or trigger model calls.
export function needsTitlePreview(chat){
 return !!chat&&!['manual','generated'].includes(chat.titleSource)&&!['manual','generated'].includes(chat.nativeNameSource)&&/^Conversation [a-zA-Z0-9-]{8}$/.test(chat.title||'');
}
export function titlePreviewKey(chat){
 return JSON.stringify([chat.id,chat.workspace,chat.title]);
}
export function untitledLabel(chat){
 const at=chat.createdAt;
 return typeof at==='number'&&at>0&&Number.isFinite(at)?'Untitled chat · '+new Date(at*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'Untitled chat';
}
export function createTitlePreviewCache(load,{limit=512,concurrency=3,ttl=60000}={}){
 const cache=new Map(),queue=[];let active=0;
 function pump(){
  while(active<concurrency&&queue.length){
   const job=queue.shift();active++;
   Promise.resolve().then(()=>load(job.chat)).then(job.resolve,()=>job.resolve(null)).finally(()=>{active--;pump()});
  }
 }
 return chat=>{
  const key=titlePreviewKey(chat),old=cache.get(key);
  if(old&&old.until>Date.now())return old.promise;
  const promise=new Promise(resolve=>queue.push({chat,resolve}));
  cache.set(key,{promise,until:Date.now()+ttl});
  while(cache.size>limit)cache.delete(cache.keys().next().value);
  pump();return promise;
 };
}
