import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',()=>reject(Error('Fixture exited')));fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[],connectRequests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{if(new URL(request.url()).pathname==='/api/voice/connect')connectRequests.push(request.postDataJSON())});
 await page.addInitScript(()=>{
  const probe=window.voiceProbe={};
  class Peer extends EventTarget{constructor(){super();this.connectionState='new';this.iceGatheringState='complete';probe.peer=this}createDataChannel(){const channel=new EventTarget();channel.readyState='open';channel.close=()=>{};return channel}addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(value){this.localDescription=value}async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){this.connectionState='closed'}}
  window.RTCPeerConnection=Peer;window.microphoneDenied=true;window.microphoneAttempts=0;navigator.mediaDevices.getUserMedia=async()=>{window.microphoneAttempts++;if(window.microphoneDenied)throw new DOMException('Fixture permission declined','NotAllowedError');const track=probe.track=new EventTarget();Object.assign(track,{enabled:true,readyState:'live',stop(){this.readyState='ended'}});return {getTracks:()=>[track],getAudioTracks:()=>[track]}};
 });
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const activity=(sessionId,status)=>page.evaluate(async data=>{const response=await fetch('/fixture/activity',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!response.ok)throw Error('Fixture activity failed')},{sessionId,status});
 const inspect=()=>page.evaluate(async()=>await(await fetch('/fixture')).json());
 await action('session.create',{});
 const original=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await page.getByRole('button',{name:'Start voice call',exact:true}).click();
 await expect(page.getByText('Microphone access was declined. Allow microphone access for this site, then call again.',{exact:true})).toBeVisible();
 assert.equal(connectRequests.length,0,'Declined microphone must not create a host/provider call');
 assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).stopped,[]);
 await expect(page.getByRole('button',{name:'Start voice call',exact:true})).toBeEnabled();
 await page.evaluate(()=>{window.microphoneDenied=false});
 await page.getByRole('button',{name:'Start voice call',exact:true}).click();
 await page.getByRole('button',{name:'End call',exact:true}).waitFor();
 assert.equal(connectRequests.length,1);assert.equal(connectRequests[0].sessionId,original);
 assert.equal(await page.evaluate(()=>window.microphoneAttempts),2);
 // A second browser's rejected start cannot finalize the owner's call.
 const observer=await page.context().newPage();
 await observer.goto(url);await showFixtureConversation(observer);await observer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await observer.evaluate(id=>window.amplifier.dispatch('session.select',{id}),original);
 const beforeEnds=(await inspect()).ends.length;
 const rejected=await observer.evaluate(()=>window.amplifier.dispatch('call.start').then(()=>null,error=>error.code));
 assert.equal(rejected,'call_active');assert.equal((await inspect()).ends.length,beforeEnds);
 assert.equal(await page.evaluate(()=>voiceProbe.peer.connectionState),'connected');await observer.close();
 await activity(original,'working');
 await action('view.update',{patch:{workSurface:'home'}});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeHidden();
 await expect(page.getByRole('button',{name:'End call',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Mute microphone',exact:true}).click();
 await expect(page.getByText('Microphone muted. Replies and work continue.',{exact:true})).toBeVisible();
 assert.deepEqual((await inspect()).stopped,[]);
 await page.getByRole('button',{name:'Stop work',exact:true}).waitFor();
 await action('session.create',{});
 const other=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);assert.notEqual(other,original);
 await page.getByRole('button',{name:'Stop work',exact:true}).click();
 await expect.poll(async()=>(await inspect()).stopped).toEqual([original]);
 await expect(page.getByRole('button',{name:'End call',exact:true})).toBeVisible();
 await activity(original,'working');
 // Hold an unrelated command, then both call bookkeeping requests. Local
 // microphone and hangup controls must still act before any response arrives.
 const held=new Map();let hold=true,endRoute;
 await page.route('**/api/actions',route=>{
  const action=route.request().postDataJSON().action;
  if(hold&&['settings.update','call.mute','call.end'].includes(action))held.set(action,route);else route.continue();
 });
 await page.route('**/api/voice/end',route=>{if(hold)endRoute=route;else route.continue()});
 await page.evaluate(()=>{window.pendingVoiceSettings=window.amplifier.dispatch('settings.update',{patch:{voiceName:'marin'}})});
 await expect.poll(()=>held.has('settings.update')).toBe(true);
 await page.getByRole('button',{name:'Unmute microphone',exact:true}).click();
 assert.equal(await page.evaluate(()=>voiceProbe.track.enabled),true);
 await expect.poll(()=>held.has('call.mute')).toBe(true);
 await page.getByRole('button',{name:'Mute microphone',exact:true}).click();
 assert.equal(await page.evaluate(()=>voiceProbe.track.enabled),false);
 await page.getByRole('button',{name:'End call',exact:true}).click();
 await expect.poll(()=>held.has('call.end')&&!!endRoute).toBe(true);
 assert.equal(await page.evaluate(()=>voiceProbe.track.enabled),false);
 await expect(page.getByRole('button',{name:'End call',exact:true})).toBeDisabled();
 assert.equal(await page.evaluate(()=>voiceProbe.peer.connectionState),'connected','Keep event transport while finalization drains');
 hold=false;
 await held.get('settings.update').continue();await held.get('call.mute').continue();await held.get('call.end').continue();await endRoute.continue();
 await page.evaluate(()=>window.pendingVoiceSettings);
 await expect(page.getByRole('button',{name:'End call',exact:true})).toHaveCount(0);
 assert.equal(await page.evaluate(()=>voiceProbe.track.readyState),'ended');
 await page.unroute('**/api/actions');await page.unroute('**/api/voice/end');
 assert.deepEqual((await inspect()).stopped,[original]);
 assert.deepEqual(errors,[]);
 console.log('Voice work controls browser passed: permission denial creates no call; second-tab rejection preserves owner; local mute/end bypass stalled HTTP and unrelated command queue; delayed echoes preserve newer local intent; stop remains scoped to call conversation; hangup preserves work and drains finalization. Synthetic media/signaling; no physical microphone or provider-audio claim.');
}finally{if(browser)await browser.close();fixture.kill();}
