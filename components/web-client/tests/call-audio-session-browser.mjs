import {fixturePython} from './fixture-python.mjs';
// Production UI and shared actions with synthetic audio/signaling. No device or API call.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try {
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',()=>reject(Error('Fixture exited')));fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  const probe=window.callProbe={handlers:{},microphones:[],plays:0,captures:0,blocked:true,denied:false};
  const session=probe.audioSession=new EventTarget();session.type='auto';session.state='active';
  Object.defineProperty(navigator,'audioSession',{value:session,configurable:true});
  Object.defineProperty(navigator,'mediaSession',{value:{metadata:null,playbackState:'none',setActionHandler:(name,handler)=>probe.handlers[name]=handler,setMicrophoneActive:value=>probe.microphones.push(value)},configurable:true});
  window.Audio=class {setAttribute(){} pause(){} play(){probe.plays++;probe.lastPlayUserActivation=navigator.userActivation.isActive;return probe.blocked?Promise.reject(Error('Playback blocked')):Promise.resolve();}};
  class Peer extends EventTarget {
   constructor(){super();this.connectionState='new';this.iceGatheringState='complete';probe.peer=this;}
   createDataChannel(){const channel=new EventTarget();channel.readyState='open';channel.close=()=>{};return channel;}
   addTrack(){} async createOffer(){return {type:'offer',sdp:'synthetic-offer'};}
   async setLocalDescription(value){this.localDescription=value;}
   async setRemoteDescription(){this.connectionState='connected';const event=new Event('track');event.streams=[{}];this.dispatchEvent(event);this.dispatchEvent(new Event('connectionstatechange'));}
   close(){this.connectionState='closed';}
  }
  window.RTCPeerConnection=Peer;
  navigator.mediaDevices.getUserMedia=async()=>{
   probe.captures++;if(probe.denied)throw new DOMException('Denied','NotAllowedError');
   if(probe.holdCapture)await new Promise(resolve=>probe.releaseCapture=resolve);
   const track=probe.track=new EventTarget();Object.assign(track,{enabled:true,muted:false,readyState:'live',stop(){this.readyState='ended';}});
   return {getAudioTracks:()=>[track],getTracks:()=>[track]};
  };
 });
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await action('session.create');await page.evaluate(()=>callProbe.holdCapture=true);await action('call.start');
 await expect.poll(()=>page.evaluate(()=>typeof callProbe.releaseCapture)).toBe('function');
 await page.evaluate(()=>callProbe.handlers.togglemicrophone());
 await expect(page.getByRole('button',{name:'Unmute microphone',exact:true})).toBeVisible();
 await page.evaluate(()=>{callProbe.holdCapture=false;callProbe.releaseCapture();});
 await expect(page.getByRole('button',{name:'Resume audio',exact:true})).toBeVisible();
 assert.equal(await page.evaluate(()=>navigator.audioSession.type),'play-and-record');
 await page.evaluate(()=>callProbe.blocked=false);await page.getByRole('button',{name:'Resume audio',exact:true}).click();
 await expect(page.getByRole('button',{name:'Resume audio',exact:true})).toHaveCount(0);
 assert.equal(await page.evaluate(()=>callProbe.lastPlayUserActivation),true);
 assert.equal(await page.evaluate(()=>callProbe.track.enabled),false);
 await page.getByRole('button',{name:'Unmute microphone',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>callProbe.track.enabled)).toBe(true);
 await page.evaluate(()=>callProbe.handlers.togglemicrophone());
 await expect(page.getByRole('button',{name:'Unmute microphone',exact:true})).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().voice.muted)).toBe(true);
 assert.equal(await page.evaluate(()=>callProbe.track.enabled),false);
 await page.evaluate(()=>{callProbe.audioSession.state='interrupted';callProbe.audioSession.dispatchEvent(new Event('statechange'));});
 await expect(page.getByText('Call audio interrupted by your device. Waiting for audio to become available.',{exact:true})).toBeVisible();
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
 assert.equal(await page.evaluate(()=>callProbe.peer.connectionState),'connected');
 await page.evaluate(()=>{callProbe.audioSession.state='active';callProbe.audioSession.dispatchEvent(new Event('statechange'));Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
 await expect(page.getByText('Call audio interrupted by your device. Waiting for audio to become available.',{exact:true})).toHaveCount(0);
 assert.equal(await page.evaluate(()=>callProbe.track.enabled),false);assert.equal(await page.evaluate(()=>callProbe.captures),1);
 await page.getByRole('button',{name:'Unmute microphone',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>callProbe.microphones.at(-1))).toBe(true);
 await page.evaluate(()=>{callProbe.track.readyState='ended';callProbe.track.dispatchEvent(new Event('ended'));});
 await expect(page.getByText('Microphone stopped. End this call and start again.',{exact:true})).toBeVisible();
 await page.evaluate(()=>callProbe.handlers.hangup());await expect(page.getByRole('button',{name:'End call',exact:true})).toHaveCount(0);
 assert.equal(await page.evaluate(()=>callProbe.handlers.hangup),null);assert.equal(await page.evaluate(()=>navigator.audioSession.type),'auto');
 assert.equal(await page.evaluate(()=>navigator.mediaSession.metadata),null);
 await page.evaluate(()=>callProbe.denied=true);await action('call.start');
 await expect(page.getByText(/Microphone access was declined/)).toBeVisible();
 assert.equal(await page.evaluate(()=>navigator.audioSession.type),'auto');
 await page.evaluate(()=>{callProbe.denied=false;Object.defineProperty(navigator,'audioSession',{value:undefined});Object.defineProperty(navigator,'mediaSession',{value:undefined});});
 await action('call.start');await expect(page.getByRole('button',{name:'End call',exact:true})).toBeVisible();
 await action('call.end');await expect(page.getByRole('button',{name:'End call',exact:true})).toHaveCount(0);
 assert.deepEqual(errors,[]);assert.deepEqual((await (await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log(JSON.stringify({sharedSystemControls:true,interruptionKeepsCallAndMute:true,noRecaptureOnReturn:true,gestureResumesPlayback:true,permissionDenialRestoresAudio:true,unsupportedFallback:true,browserErrors:0,physicalDeviceTested:false}));
}finally{await browser?.close();fixture.kill('SIGTERM');}
