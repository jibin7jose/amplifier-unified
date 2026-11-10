import test from 'node:test';
import assert from 'node:assert/strict';
import {CallAudioSession} from '../src/call-audio-session.js';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({unsupported=[],rejectPlayback=false}={}) {
  const document=new EventTarget();document.visibilityState='visible';
  const audioSession=new EventTarget();audioSession.type='auto';audioSession.state='active';
  const handlers={},microphones=[],actions=[],states=[];
  const metadata={title:'Previous media'};
  const mediaSession={metadata,playbackState:'paused',setMicrophoneActive:value=>microphones.push(value),
    setActionHandler(name,value){if(unsupported.includes(name))throw Error('Unsupported');handlers[name]=value;}};
  const audio={srcObject:{},plays:0,play(){this.plays++;return rejectPlayback?Promise.reject(Error('Blocked')):Promise.resolve();}};
  const track=new EventTarget();Object.assign(track,{readyState:'live',enabled:true,muted:false});
  const value=new CallAudioSession({navigator:{audioSession,mediaSession},document,Metadata:class {constructor(data){Object.assign(this,data)}},getAudio:()=>audio,
    onState:state=>states.push(state),onAction:(...args)=>actions.push(args)});
  return {value,document,audioSession,mediaSession,metadata,audio,track,handlers,microphones,actions,states};
}

test('call controls reflect actual capture and release all call integration',async()=>{
  const f=fixture();f.value.start();f.value.attachStream({getAudioTracks:()=>[f.track]});
  assert.equal(f.audioSession.type,'play-and-record');assert.equal(f.mediaSession.metadata.title,'Amplifier voice call');
  assert.equal(f.microphones.at(-1),true);f.handlers.togglemicrophone();assert.deepEqual(f.actions.at(-1),['call.mute',{muted:true}]);
  f.track.enabled=false;f.value.setMuted(true);assert.equal(f.microphones.at(-1),false);
  f.handlers.togglemicrophone();assert.deepEqual(f.actions.at(-1),['call.mute',{muted:false}]);
  const oldHangup=f.handlers.hangup;oldHangup();assert.deepEqual(f.actions.at(-1),['call.end']);
  f.value.stop();assert.equal(f.audioSession.type,'auto');assert.equal(f.microphones.at(-1),false);
  assert.equal(f.mediaSession.metadata,f.metadata);assert.equal(f.mediaSession.playbackState,'paused');
  assert.equal(f.handlers.togglemicrophone,null);assert.equal(f.handlers.hangup,null);
  f.value.start();const count=f.actions.length;oldHangup();assert.equal(f.actions.length,count);
  f.value.dispose();await flush();
});

test('interruptions preserve the user mute choice and resume only existing playback',async()=>{
  const f=fixture();f.value.start();f.value.attachStream({getAudioTracks:()=>[f.track]});await flush();
  f.track.enabled=false;f.value.setMuted(true);
  f.audioSession.state='interrupted';f.audioSession.dispatchEvent(new Event('statechange'));
  const played=f.audio.plays;await f.value.resumePlayback();assert.equal(f.audio.plays,played);
  f.audioSession.state='active';f.audioSession.dispatchEvent(new Event('statechange'));await flush();
  assert.equal(f.audio.plays,played+1);assert.equal(f.track.enabled,false);assert.equal(f.value.muted,true);
  f.track.muted=true;f.track.dispatchEvent(new Event('mute'));assert.equal(f.states.at(-1).microphoneInterrupted,true);
  f.track.muted=false;f.track.dispatchEvent(new Event('unmute'));await flush();assert.equal(f.track.enabled,false);
  f.track.readyState='ended';f.track.dispatchEvent(new Event('ended'));assert.equal(f.states.at(-1).microphoneEnded,true);
  assert.deepEqual(f.actions,[]);f.value.dispose();
});

test('background visibility never ends a call; return retries blocked playback',async()=>{
  const f=fixture({rejectPlayback:true});f.value.start();await flush();
  assert.ok(f.states.some(state=>state.playbackBlocked));
  const count=f.audio.plays;f.document.visibilityState='hidden';f.document.dispatchEvent(new Event('visibilitychange'));await flush();
  assert.equal(f.audio.plays,count);assert.equal(f.value.active,true);assert.deepEqual(f.actions,[]);
  f.document.visibilityState='visible';f.document.dispatchEvent(new Event('visibilitychange'));await flush();assert.equal(f.audio.plays,count+1);
  f.value.dispose();f.document.dispatchEvent(new Event('visibilitychange'));await flush();assert.equal(f.audio.plays,count+1);
});

test('unsupported or rejecting optional APIs do not block call lifecycle',async()=>{
  const bare=new CallAudioSession({navigator:{},document:new EventTarget()});bare.start();bare.stop();bare.dispose();
  const f=fixture({unsupported:['togglemicrophone']});f.mediaSession.setMicrophoneActive=()=>Promise.reject(Error('Not supported'));
  Object.defineProperty(f.audioSession,'type',{get:()=> 'auto',set:()=>{throw Error('Denied')}});
  f.value.start();f.value.attachStream({getAudioTracks:()=>[f.track]});assert.equal(typeof f.handlers.hangup,'function');
  f.handlers.hangup();assert.deepEqual(f.actions,[['call.end']]);f.value.dispose();await flush();
});

test('late playback rejection cannot overwrite the state of a later call',async()=>{
  const f=fixture();let reject;f.audio.play=()=>new Promise((_,r)=>reject=r);
  f.value.start();f.value.stop();f.audio.play=()=>Promise.resolve();f.value.start();await flush();
  reject(Error('Old call'));await flush();assert.equal(f.states.at(-1).playbackBlocked,false);f.value.dispose();
});
