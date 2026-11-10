import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceClient} from '../src/voice.js';

for(const boundary of ['configuration','microphone'])test(`navigation cancels setup awaiting ${boundary} without restarting audio`,async t=>{
  const previous=Object.fromEntries(['window','document','navigator'].map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  const window=new EventTarget(),document=new EventTarget();document.visibilityState='visible';window.RTCPeerConnection=class {};
  let ready,resolve;const pending=new Promise(r=>resolve=r),started=new Promise(r=>ready=r);
  const track={stopped:false,stop(){this.stopped=true;}},requests=[];
  const audioSession=new EventTarget();audioSession.type='auto';audioSession.state='active';
  const navigator={audioSession,mediaDevices:{getUserMedia:async()=>{ready();return pending;}}};
  for(const [key,value] of Object.entries({window,document,navigator}))Object.defineProperty(globalThis,key,{value,configurable:true});
  t.after(()=>{for(const [key,value] of Object.entries(previous)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}});
  const client=new VoiceClient({request:async path=>{requests.push(path);if(boundary==='configuration'){ready();return pending;}return {available:true};}});
  const call=client.start();await started;window.dispatchEvent(new Event('pagehide'));
  resolve(boundary==='configuration'?{available:true}:{getTracks:()=>[track]});await call;
  assert.equal(client.state.status,'ended');assert.equal(client.state.id,null);assert.equal(audioSession.type,'auto');
  assert.deepEqual(requests,['/api/voice/config']);assert.equal(track.stopped,boundary==='microphone');client.dispose();
});

const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function fixture(t,{request,ice='complete',autoStart=true,timeouts={}}={}){
 const keys=['window','document','navigator','RTCPeerConnection','Audio','MediaStream'];
 const previous=Object.fromEntries(keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 const window=new EventTarget(),document=new EventTarget();document.visibilityState='visible';
 const requests=[],peers=[],tracks=[],errors=[];let sequence=0;
 class Track extends EventTarget {constructor(){super();this.enabled=true;this.muted=false;this.readyState='live'}stop(){this.readyState='ended'}}
 class Peer extends EventTarget {
  constructor(){super();this.connectionState='new';this.iceGatheringState=ice;peers.push(this)}
  createDataChannel(){const channel=this.channel=new EventTarget();channel.readyState='open';channel.sent=[];channel.send=text=>channel.sent.push(JSON.parse(text));channel.close=()=>{channel.readyState='closed';channel.dispatchEvent(new Event('close'))};return channel}
  addTrack(){}async createOffer(){return {type:'offer',sdp:'v=0\n'}}async setLocalDescription(value){this.localDescription=value}
  async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'));const event=new Event('track');event.streams=[{}];this.dispatchEvent(event);if(autoStart)this.message({type:'session.started'})}
  message(data){this.channel.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(data)}))}
  completeIce(){this.iceGatheringState='complete';this.localDescription.sdp+='a=candidate:fixture\n';this.dispatchEvent(new Event('icegatheringstatechange'))}
  close(){this.connectionState='closed';this.dispatchEvent(new Event('connectionstatechange'))}
 }
 class Audio {setAttribute(){}pause(){this.paused=true}play(){this.paused=false;return Promise.resolve()}}
 const navigator={mediaDevices:{getUserMedia:async()=>{const track=new Track();tracks.push(track);return {getTracks:()=>[track],getAudioTracks:()=>[track]}}}};
 window.RTCPeerConnection=Peer;
 for(const [key,value] of Object.entries({window,document,navigator,RTCPeerConnection:Peer,Audio,MediaStream:class {}}))Object.defineProperty(globalThis,key,{value,configurable:true});
 const client=new VoiceClient({timeouts:{ice:1000,connect:2000,request:1000,health:20,close:30,closeFallback:5,...timeouts},onError:error=>errors.push(error.message),request:async(path,options)=>{
  requests.push([path,options]);const override=request?.(path,options);if(override!==undefined)return override;
  if(path.endsWith('/config'))return {available:true};
  if(path.endsWith('/connect'))return {id:'call-'+(++sequence),provider:'live',sdp:'answer'};
  if(path.endsWith('/ready'))return {ready:true};
  if(path.endsWith('/end'))return {closed:true,finalized:true,workContinues:true};
 }});
 t.after(()=>{client.dispose();for(const [key,value] of Object.entries(previous)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key]}});
 return {client,requests,peers,tracks,errors,window};
}

test('Live waits for gathered ICE and session.started before reporting ready',async t=>{
 const f=fixture(t,{ice:'gathering',autoStart:false});const starting=f.client.start();await flush();
 assert.equal(f.requests.some(([path])=>path.endsWith('/connect')),false);
 f.peers[0].completeIce();await starting;await flush();
 assert.match(f.requests.find(([path])=>path.endsWith('/connect'))[1].body.sdp,/a=candidate/);
 assert.equal(f.requests.some(([path])=>path.endsWith('/ready')),false);assert.equal(f.client.state.status,'connecting');
 f.peers[0].message({type:'session.started',session:{id:'call-1'}});await flush();
 assert.equal(f.client.state.status,'connected');assert.equal(f.requests.filter(([path])=>path.endsWith('/ready')).length,1);
});

test('ICE timeout releases microphone without creating a provider session',async t=>{
 const f=fixture(t,{ice:'gathering',timeouts:{ice:5}});
 await assert.rejects(f.client.start(),/network connection details/);
 assert.equal(f.tracks[0].readyState,'ended');assert.equal(f.requests.some(([path])=>path.endsWith('/connect')||path.endsWith('/end')),false);
});

test('ending while gathering ICE cancels setup without creating a call',async t=>{
 const f=fixture(t,{ice:'gathering'});const starting=f.client.start();await flush();await f.client.end();await starting;
 assert.equal(f.client.state.status,'ended');assert.equal(f.requests.some(([path])=>path.endsWith('/connect')),false);
});

test('a competing call rejection never sends unscoped or other-call cleanup',async t=>{
 const f=fixture(t,{request:path=>path.endsWith('/connect')?Promise.reject(Error('A call is already active.')):undefined});
 await assert.rejects(f.client.start(),/already active/);
 assert.equal(f.requests.some(([path])=>path.endsWith('/end')),false);assert.equal(f.tracks[0].readyState,'ended');
});

test('late ready failure cannot end a replacement call',async t=>{
 const first=deferred();let readyCount=0;
 const f=fixture(t,{request:path=>path.endsWith('/ready')&&++readyCount===1?first.promise:undefined});
 await f.client.start();await f.client.end();await f.client.start();await flush();
 const current=f.client.state.id;first.reject(Error('Old ready failed'));await flush();
 assert.equal(f.client.state.id,current);assert.equal(f.client.state.status,'connected');assert.deepEqual(f.errors,[]);
 assert.equal(f.requests.filter(([path])=>path.endsWith('/end')).length,1);
});

test('a terminal event cannot be undone by a pending ready success',async t=>{
 const pending=deferred();const f=fixture(t,{request:path=>path.endsWith('/ready')?pending.promise:undefined});
 await f.client.start();f.peers[0].message({type:'session.closed',usage:{seconds:2}});pending.resolve({ready:true});await flush();
 assert.equal(f.client.state.status,'ended');assert.equal(f.client.state.id,null);assert.equal(f.client.state.finalized,true);
});

test('hangup silences immediately while the final event drains despite stalled HTTP',async t=>{
 const pending=deferred();const f=fixture(t,{request:path=>path.endsWith('/end')?pending.promise:undefined});
 await f.client.start();await flush();const call=f.client.call;const ending=f.client.end();
 assert.equal(f.tracks[0].enabled,false);assert.equal(call.audio.muted,true);assert.equal(call.audio.paused,true);
 assert.equal(call.events.readyState,'open');assert.equal(call.peer.connectionState,'connected');
 await sleep(8);assert.equal(call.events.sent[0].type,'session.close');
 call.peer.message({type:'session.closed',usage:{seconds:4}});const result=await ending;
 assert.equal(result.finalized,true);assert.deepEqual(result.usage,{seconds:4});assert.equal(call.peer.connectionState,'closed');assert.deepEqual(f.errors,[]);
 pending.resolve({finalized:false});await flush();assert.equal(f.client.state.finalized,true);
});

test('stalled finalization has a bounded local cleanup and honest accounting',async t=>{
 const f=fixture(t,{request:path=>path.endsWith('/end')?new Promise(()=>{}):undefined,timeouts:{close:10}});
 await f.client.start();await flush();const result=await f.client.end();
 assert.equal(result.finalized,false);assert.equal(result.workContinues,true);assert.equal(f.tracks[0].readyState,'ended');
 assert.equal(f.client.state.status,'ended');assert.match(f.errors[0],/could not be confirmed/);
});

test('old mute echoes and scoped controls cannot override newer local intent',async t=>{
 const f=fixture(t);await f.client.start();await flush();const scope=f.client.scope();
 const mute={...scope,controlId:'mute',muted:true};f.client.control('call.mute',mute);
 f.client.control('call.mute',{...scope,controlId:'unmute',muted:false});f.client.control('call.mute',mute);
 assert.equal(f.tracks[0].enabled,true);
 await f.client.end();await f.client.start();await flush();f.client.control('call.mute',mute);await f.client.control('call.end',scope);
 assert.equal(f.client.state.status,'connected');assert.equal(f.tracks[1].enabled,true);
});

test('late connect after cancellation closes only its own provider identity',async t=>{
 const pending=deferred();let count=0;
 const f=fixture(t,{request:path=>path.endsWith('/connect')&&++count===1?pending.promise:undefined});
 const starting=f.client.start({attemptId:'first'});await flush();await f.client.end();await f.client.start({attemptId:'second'});await flush();
 const replacement=f.client.state.id;pending.resolve({id:'late-call',provider:'live',sdp:'late'});await starting;await flush();
 assert.equal(f.client.state.id,replacement);assert.equal(f.client.state.status,'connected');
 assert.deepEqual(f.requests.filter(([path])=>path.endsWith('/end')).map(([,options])=>options.body),[{attemptId:'first'},{attemptId:'first',id:'late-call'}]);
});

for(const failure of ['channel','sideband'])test(`${failure} loss closes owned media and preserves work`,async t=>{
 const f=fixture(t);await f.client.start();await flush();const id=f.client.state.id;
 f.client.sync({id:'other-call',status:'error'},true);assert.equal(f.client.state.status,'connected');
 if(failure==='channel')f.peers[0].channel.dispatchEvent(new Event('close'));else f.client.sync({id,status:'error',error:'Sideband disconnected'},true);
 await flush();assert.equal(f.client.state.status,'ended');assert.equal(f.tracks[0].enabled,false);
 assert.equal(f.requests.at(-1)[1].body.id,id);assert.ok(f.errors.length);
});

test('transient media interruption recovers but persistent disconnection closes',async t=>{
 const f=fixture(t);await f.client.start();await flush();const peer=f.peers[0];
 peer.connectionState='disconnected';peer.dispatchEvent(new Event('connectionstatechange'));assert.equal(f.client.state.mediaStatus,'reconnecting');
 peer.connectionState='connected';peer.dispatchEvent(new Event('connectionstatechange'));await sleep(25);assert.equal(f.client.state.status,'connected');
 peer.connectionState='disconnected';peer.dispatchEvent(new Event('connectionstatechange'));await sleep(25);assert.equal(f.client.state.status,'ended');
});

test('provider command errors surface once without treating rejected speech as hangup',async t=>{
 const f=fixture(t);await f.client.start();await flush();
 f.peers[0].message({type:'error',error:{code:'content_filter'}});
 f.client.sync({id:f.client.state.id,status:'connected',error:'Voice provider reported an error: content_filter'},true);
 assert.equal(f.client.state.status,'connected');assert.deepEqual(f.errors,['Voice provider reported an error: content_filter']);
});
