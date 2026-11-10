import test from 'node:test';
import assert from 'node:assert/strict';
import {createStateStream} from '../src/state-stream.js';
class Socket {static last;constructor(url){this.url=url;Socket.last=this}close(code,reason){this.code=code;this.reason=reason;this.closes=(this.closes||0)+1;this.onclose?.()}}
class Source extends EventTarget {
 static last;
 constructor(path){super();this.path=path;this.readyState=0;Source.last=this}
 close(){this.readyState=2;this.closes=(this.closes||0)+1}
 emit(event,data){this.dispatchEvent(new MessageEvent(event,{data}))}
}
test('state socket preserves identity, events and close/reconnect ownership',()=>{
 const stream=createStateStream('/api/events?transport=delta-v1&clientId=private',['websocket'],{Socket,origin:'https://example.test'});
 const socket=Socket.last;assert.equal(socket.url,'wss://example.test/api/events/ws?transport=delta-v1&clientId=private');
 let opened=0,errors=0,frame;stream.onopen=()=>opened++;stream.onerror=()=>errors++;
 stream.addEventListener('state',e=>frame=e.data);socket.onopen();assert.equal(stream.readyState,1);assert.equal(opened,1);
 socket.onmessage({data:JSON.stringify({event:'state',data:{revision:1}})});assert.deepEqual(frame,{revision:1});
 socket.onclose();assert.equal(stream.readyState,2);assert.equal(errors,1);
 stream.close();assert.equal(errors,1,'intentional close must not restart');
});
test('malformed WebSocket frames close 1002 without delivering data',()=>{
 for(const data of ['not json','null','{}','{"event":"unknown","data":{}}','{"event":"state","data":null}','{"event":"state","data":"text"}']){
  const stream=createStateStream('/api/events',['websocket'],{Socket,origin:'http://localhost'});let errors=0,delivered=0;stream.onerror=()=>errors++;
  for(const event of ['state','state-delta','shell'])stream.addEventListener(event,()=>delivered++);
  Socket.last.onmessage({data});assert.equal(Socket.last.code,1002);assert.equal(Socket.last.reason,'Invalid state frame');assert.equal(errors,1);assert.equal(delivered,0);
 }
});
test('SSE keeps identity, native retry state and application-owned close',()=>{
 const path='/api/events?transport=delta-v1&clientId=private';
 const stream=createStateStream(path,['http','sse'],{Source}),native=Source.last;
 assert.equal(native.path,path);assert.equal(stream.readyState,0);
 let opened=0,errors=[];stream.onopen=()=>opened++;stream.onerror=event=>errors.push({event,readyState:stream.readyState});
 native.readyState=1;native.onopen();assert.equal(opened,1);assert.equal(stream.readyState,1);
 const retry=new Event('error');native.readyState=0;native.onerror(retry);
 assert.equal(errors[0].event,retry);assert.equal(errors[0].readyState,0);assert.equal(native.closes,undefined,'native SSE retry remains open');
 native.readyState=1;native.onopen();assert.equal(opened,2);
 const stopped=new Event('error');native.readyState=2;native.onerror(stopped);assert.equal(errors[1].readyState,2);
 stream.close();assert.equal(native.closes,1);assert.equal(stream.readyState,2);
});
test('both transports deliver exact decoded objects with one parse and no re-encoding',t=>{
 for(const transport of ['websocket','sse'])for(const count of [1,128])for(const event of ['state','state-delta','shell']){
  const messages=Array.from({length:count},(_,i)=>({id:String(i),text:'quoted " and newline\n λ '+ 'x'.repeat(2048),nested:{values:[true,null,i]}}));
  const expected=event==='state'?{revision:8,client:{id:'private',hostInstanceId:'host'},sessions:[{id:'chat',messages}]}
   :event==='state-delta'?{baseRevision:8,revision:9,changes:[{path:['revision'],value:9},{path:['sessions',0,'messages'],value:messages}]}
   :{revision:9,instances:{example:{messages}}};
  const wire=JSON.stringify(transport==='websocket'?{event,data:expected}:expected);
  const stream=createStateStream('/api/events',[transport],{Socket,Source,origin:'https://example.test'});let delivered;
  stream.addEventListener(event,message=>{delivered=message});
  const parse=JSON.parse,stringify=JSON.stringify;let parses=0,encodes=0,decoded;
  try{
   t.mock.method(JSON,'parse',(...args)=>{parses++;decoded=parse(...args);return decoded});
   t.mock.method(JSON,'stringify',(...args)=>{encodes++;return stringify(...args)});
   if(transport==='websocket')Socket.last.onmessage({data:wire});else Source.last.emit(event,wire);
  }finally{t.mock.restoreAll()}
  assert.equal(parses,1,`${transport}/${event}/${count} decodes once`);assert.equal(encodes,0,`${transport}/${event}/${count} does not re-encode`);
  assert.equal(delivered.decodeError,undefined);assert.deepEqual(delivered.data,expected);
  assert.equal(delivered.data,transport==='websocket'?decoded.data:decoded,'listeners receive the decoded payload without copying');
 }
});
test('SSE decode failures reach the matching handler without taking recovery ownership',()=>{
 const stream=createStateStream('/api/events',undefined,{Source}),native=Source.last;let errors=0;stream.onerror=()=>errors++;
 for(const event of ['state','state-delta','shell']){
  let received;stream.addEventListener(event,message=>{received=message});
  native.emit(event,'not json');assert.ok(received.decodeError instanceof SyntaxError);assert.equal(received.data,null);
  native.emit(event,'{"revision":9}');assert.equal(received.decodeError,undefined);assert.deepEqual(received.data,{revision:9});
 }
 assert.equal(native.closes,undefined);assert.equal(errors,0,'the app decides how a malformed event affects its baseline');
});
test('intentional close suppresses late frames, open and error callbacks for both transports',()=>{
 for(const transport of ['websocket','sse']){
  const stream=createStateStream('/api/events',[transport],{Socket,Source,origin:'https://example.test'});let opened=0,errors=0,delivered=0;
  stream.onopen=()=>opened++;stream.onerror=()=>errors++;
  for(const event of ['state','state-delta','shell'])stream.addEventListener(event,()=>delivered++);
  stream.close();const native=transport==='websocket'?Socket.last:Source.last;
  native.onopen();native.onerror();
  if(transport==='websocket'){native.onmessage({data:'not json'});native.onclose()}
  else for(const event of ['state','state-delta','shell'])native.emit(event,'not json');
  assert.equal(opened,0);assert.equal(errors,0);assert.equal(delivered,0);assert.equal(native.closes,1);assert.equal(stream.readyState,2);
 }
});
