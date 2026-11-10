import {request} from './api.js';

async function readPage(url,read,signal,timeoutMs){
 const controller=new AbortController();let timer,abort;
 const stopped=new Promise((_,reject)=>{
  abort=()=>{controller.abort();reject(signal?.reason||new DOMException('Cancelled','AbortError'))};
  if(signal?.aborted){abort();return}
  signal?.addEventListener('abort',abort,{once:true});
  timer=setTimeout(()=>{reject(new Error('Loading saved details took too long. Try again.'));controller.abort()},timeoutMs);
 });
 try{
  if(signal?.aborted)return await stopped;
  return await Promise.race([read(url,{signal:controller.signal}),stopped]);
 }finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
}
export async function readDetail(reference,read=request,signal,{timeoutMs=30000}={}){
 let offset=0,total,characters=0;const chunks=[];
 do{
  const page=await readPage('/api/conversation/detail?'+new URLSearchParams({...reference,offset}),read,signal,timeoutMs);
  if(signal?.aborted)throw signal.reason||new DOMException('Cancelled','AbortError');
  const next=page?.nextOffset;
  if(typeof page?.value!=='string'||next!==null&&(!Number.isSafeInteger(next)||next<=offset||!page.value.length))
   throw new Error('Saved details could not finish loading. Try again.');
  if(page.total!==undefined){
   if(!Number.isSafeInteger(page.total)||page.total<0||total!==undefined&&total!==page.total||next!==null&&next>=page.total)
    throw new Error('Saved details changed while loading. Reopen this step.');
   total=page.total;
  }
  // Host offsets count Unicode code points, not JavaScript UTF-16 units.
  for(const character of page.value)characters++;
  if(next!==null&&next!==characters||next===null&&total!==undefined&&characters!==total)
   throw new Error('Saved details are incomplete. Try again.');
  chunks.push(page.value);offset=next;
 }while(offset!==null);
 return chunks.join('');
}
