import './composer-test-helpers.mjs';
// Same serving fixture and real action/capture engine as voice; synthetic pixels only.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try {
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',()=>reject(Error('Fixture exited')));fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  class Peer extends EventTarget {constructor(){super();this.connectionState='new'}createDataChannel(){const c=new EventTarget();c.readyState='open';c.close=()=>{};return c}addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(v){this.localDescription=v}async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){}}
  window.RTCPeerConnection=Peer;
  navigator.mediaDevices.getUserMedia=async()=>new MediaStream();
  navigator.mediaDevices.getDisplayMedia=async()=>{
   window.pickerCalls=(window.pickerCalls||0)+1;
   const canvas=document.createElement('canvas');canvas.width=320;canvas.height=200;const c=canvas.getContext('2d');c.fillStyle='navy';c.fillRect(0,0,320,200);c.fillStyle='white';c.font='22px sans-serif';c.fillText('Synthetic text chat',15,80);
   const stream=canvas.captureStream(0),track=stream.getVideoTracks()[0];Object.defineProperty(track,'label',{value:'Synthetic text window'});track.getSettings=()=>({displaySurface:'window'});window.captureTrack=track;return stream;
  };
 });
 await page.goto(url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 const inspect=()=>page.request.get(url+'/fixture').then(r=>r.json());
 await action('session.create',{});
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId),clientId=await page.evaluate(()=>window.amplifier.getState().client.id);
 const agent=async(action,args={},id)=>{const r=await page.request.post(url+'/fixture/agent',{data:{sessionId:sid,action,args:{...args,clientId},id}});assert.equal(r.status(),200,await r.text());return r.json()};
 await expect(page.getByRole('button',{name:'Choose screen source',exact:true})).toHaveCount(0);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Unsent text remains');
 await agent('view.update',{patch:{runtimeDraft:{section:'desktop-host'}}});
 await expect(page.getByRole('heading',{name:'Screen context for this chat in this browser'})).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>document.activeElement?.dataset.runtimeSection)).toBe('desktop-host');
 assert.equal(await page.evaluate(()=>window.pickerCalls||0),0);assert.deepEqual((await inspect()).nativeCalls,[]);assert.deepEqual((await inspect()).computerGrants,[]);
 for(let i=0;i<2;i++){
  await agent('view.update',{patch:{runtimeDraft:{section:'screen-source'}}});
  await expect.poll(()=>page.evaluate(()=>document.activeElement?.dataset.runtimeSection)).toBe('screen-source');
  await page.getByRole('button',{name:'Computer use',exact:true}).focus();
 }
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.runtimeDraft.revealRevision)).toBe(3);
 await page.getByRole('button',{name:'Choose screen source',exact:true}).click();
 await expect(page.getByRole('button',{name:'Capture screen',exact:true})).toBeEnabled();
 assert.equal((await inspect()).grant,null);assert.equal((await inspect()).computerGrants[0].callId,null);
 await page.getByRole('button',{name:'Capture screen',exact:true}).click();
 await expect.poll(async()=>(await inspect()).computerReceipts.length).toBe(1);
 const saved=(await inspect()).computerReceipts[0];assert.equal(saved.width,320);assert.equal(saved.height,200);assert.equal(saved.callId,null);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Unsent text remains');assert.equal((await inspect()).sent.length,0);
 await page.screenshot({path:process.env.COMPUTER_VISUAL_SCREENSHOT||'/tmp/amplifier-computer-controls.png',fullPage:true});
 await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Choose screen source',exact:true})).toHaveCount(0);
 await expect(page.getByRole('status',{name:'Screen sharing active'})).toBeVisible();
 const sharedGrant=(await inspect()).computerGrants[0].id;
 await action('call.start');await expect(page.getByRole('button',{name:'End call',exact:true})).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().voice.status)).toBe('connected');
 assert.equal((await inspect()).computerGrants[0].id,sharedGrant);assert.equal(await page.evaluate(()=>window.captureTrack.readyState),'live');
 await page.getByRole('button',{name:'End call',exact:true}).click();
 assert.equal((await inspect()).computerGrants[0].id,sharedGrant);await expect(page.getByRole('status',{name:'Screen sharing active'})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 const stop=page.getByRole('button',{name:'Stop sharing',exact:true});await expect(stop).toBeVisible();
 const stopBox=await stop.boundingBox();assert.ok(stopBox.x>=0&&stopBox.x+stopBox.width<=390);
 await page.screenshot({path:process.env.SHARING_INDICATOR_SCREENSHOT||'/tmp/amplifier-sharing-indicator.png',fullPage:true});
 await page.setViewportSize({width:1400,height:1000});
 await new Promise(r=>setTimeout(r,2100));
 const captured=await agent('computer.visual.capture',{sessionId:sid},'text-agent-capture');assert.equal(captured.result.source.label,'Synthetic text window');
 await page.getByRole('button',{name:'Stop sharing',exact:true}).click();await expect.poll(()=>page.evaluate(()=>window.captureTrack.readyState)).toBe('ended');await expect.poll(async()=>(await inspect()).computerGrants.length).toBe(0);await expect(page.getByRole('status',{name:'Screen sharing active'})).toHaveCount(0);
 await page.getByRole('button',{name:'Chat controls',exact:true}).click();
 await page.getByRole('button',{name:'Choose screen source',exact:true}).click();await expect(page.getByRole('button',{name:'Capture screen',exact:true})).toBeEnabled();
 await action('session.create',{});await expect.poll(()=>page.evaluate(()=>window.captureTrack.readyState)).toBe('ended');
 await action('session.select',{id:sid});await expect.poll(async()=>(await inspect()).computerGrants.length).toBe(0);
 assert.equal((await inspect()).sent.length,0);assert.deepEqual(errors,[]);
 console.log('Computer use browser acceptance passed: hidden tab, agent open/tab/repeated section reveal without consent, explicit nonvoice synthetic PNG, UI/agent captures, no model turn, preserved draft, close panel, revoke and chat-switch cleanup.');
} finally {await browser?.close();fixture.kill('SIGTERM')}
