// WebSockets do not occupy the browser's six HTTP/1.1 request slots. Keep each
// page's private client identity and the same snapshot/delta event contract.
// Internal events carry decoded data; the server's JSON wire stays unchanged.
export function createStateStream(path,transports,{Socket=globalThis.WebSocket,Source=globalThis.EventSource,origin=globalThis.location?.origin}={}){
 const source=new EventTarget(),events=['state','state-delta','shell'];let closed=false;
 const emit=(event,data,decodeError)=>source.dispatchEvent(Object.assign(new MessageEvent(event,{data}),{decodeError}));
 if(!transports?.includes('websocket')){
  const native=new Source(path);
  Object.defineProperty(source,'readyState',{get:()=>native.readyState});
  source.close=()=>{closed=true;native.close()};
  native.onopen=event=>{if(!closed)source.onopen?.(event)};
  native.onerror=event=>{if(!closed)source.onerror?.(event)};
  for(const event of events)native.addEventListener(event,message=>{
   if(closed)return;
   let data,decodeError;try{data=JSON.parse(message.data)}catch(error){decodeError=error}
   // The app ignores malformed snapshots/shell events, but recovers a broken
   // delta chain. Keep that decision in its existing event handlers.
   emit(event,data,decodeError);
  });
  return source;
 }
 const url=new URL(path,origin);url.protocol=url.protocol==='https:'?'wss:':'ws:';url.pathname+='/ws';
 const socket=new Socket(url.href);
 source.readyState=0;
 source.close=()=>{closed=true;source.readyState=2;socket.close()};
 socket.onopen=()=>{if(closed)return;source.readyState=1;source.onopen?.()};
 socket.onmessage=message=>{
  if(closed)return;
  try{
   const frame=JSON.parse(message.data);
   if(!events.includes(frame.event)||!frame.data||typeof frame.data!=='object')throw Error('Invalid state frame');
   emit(frame.event,frame.data);
  }catch{socket.close(1002,'Invalid state frame')}
 };
 // Reconnection belongs to the existing app lifecycle. Never replay commands.
 socket.onclose=()=>{source.readyState=2;if(!closed)source.onerror?.()};
 socket.onerror=()=>{if(!closed)source.onerror?.()};
 return source;
}
