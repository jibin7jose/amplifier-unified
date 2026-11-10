import {showFixtureConversation} from './browser-chat-pages.mjs';
// Production UI + real transcript persistence, synthetic signaling; no real audio.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/voice_visual_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try {
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},reducedMotion:'reduce',colorScheme:'dark'});const page=await context.newPage();
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{
  window.Audio=class {setAttribute(){} pause(){} play(){return Promise.resolve()}};
  window.RTCPeerConnection=class extends EventTarget {
   constructor(){super();this.connectionState='new';this.iceGatheringState='complete'}
   createDataChannel(){const c=new EventTarget();c.readyState='open';c.close=()=>{};return c}
   addTrack(){}async createOffer(){return {type:'offer',sdp:'synthetic-offer'}}async setLocalDescription(value){this.localDescription=value}
   async setRemoteDescription(){this.connectionState='connected';this.dispatchEvent(new Event('connectionstatechange'))}close(){this.connectionState='closed'}
  };
  navigator.mediaDevices.getUserMedia=async()=>{const track=new EventTarget();Object.assign(track,{enabled:true,muted:false,readyState:'live',stop(){this.readyState='ended'}});return {getTracks:()=>[track],getAudioTracks:()=>[track]}};
 });
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const selected=()=>page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 const workReady=async(target=page,sessionId=null)=>{
  await target.waitForFunction(expected=>{const pane=document.querySelector('.a-messages'),selected=window.amplifier?.getState()?.selectedSessionId;try{const receipt=JSON.parse(pane?.dataset.workProjection);return (!expected||selected===expected)&&pane.dataset.sessionId===selected&&receipt.sessionId===selected&&receipt.status==='ready'&&receipt.current===true&&!pane.querySelector('[data-part="work-projection-status"]')}catch{return false}},sessionId);
  await target.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 };
 const readingPosition=()=>pane.evaluate(el=>{const top=el.getBoundingClientRect().top,node=[...el.querySelectorAll('[data-message-id]')].find(row=>row.getBoundingClientRect().bottom>top);return {id:node?.dataset.messageId,offset:node?.getBoundingClientRect().top-top}});
 await action('session.create',{title:'Call conversation'});const call=await selected();
 await action('session.create',{title:'Other conversation'});const other=await selected();
 let sequence=0;
 const append=async(sessionId,count=1)=>{const response=await page.request.post(url+'/fixture/transcript',{data:{sessionId,messages:Array.from({length:count},()=>({id:'voice-'+(++sequence),role:sequence%2?'user':'assistant',text:'Spoken item '+sequence+'\n\n'+('A useful explanation for this conversation. '.repeat(90))}))}});assert.equal(response.status(),200);if(await selected()===sessionId){await expect(page.locator('.a-messages')).toContainText('Spoken item '+sequence+'\n');await workReady()}};
 const pane=page.locator('.a-messages'),top=()=>pane.evaluate(el=>el.scrollTop),remaining=()=>pane.evaluate(el=>el.scrollHeight-el.scrollTop-el.clientHeight);
 const bottom=()=>expect.poll(remaining).toBeLessThan(3);
 const scrollBack=async()=>{await workReady();const box=await pane.boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-800);await expect.poll(remaining).toBeGreaterThan(500);await page.waitForTimeout(150)};
 await append(other,12);await action('session.select',{id:call});await append(call,12);
 const observer=await page.context().newPage();await observer.goto(url);await showFixtureConversation(observer);await observer.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 await observer.evaluate(id=>window.amplifier.dispatch('session.select',{id}),call);
 const observerPane=observer.locator('.a-messages');await expect(observerPane).toContainText('Spoken item '+sequence+'\n');
 await observerPane.evaluate(el=>{el.dispatchEvent(new WheelEvent('wheel',{deltaY:-800}));el.scrollTop=1000;el.dispatchEvent(new Event('scroll'))});
 await workReady(observer);const observerTop=await observerPane.evaluate(el=>el.scrollTop);
 // Starting this client's call intentionally follows speech even when it was
 // reading earlier history; another observing client's position is independent.
 await scrollBack();assert.ok(await remaining()>500);
 await action('call.start');await page.getByRole('button',{name:'End call',exact:true}).waitFor();await bottom();
 // A wheel gesture toward newer messages at the bottom cannot move the pane.
 // It must not silently pause follow-latest for the next spoken fragment.
 const bottomBox=await pane.boundingBox();await page.mouse.move(bottomBox.x+bottomBox.width/2,bottomBox.y+bottomBox.height/2);
 await page.mouse.wheel(0,500);await page.waitForTimeout(100);await bottom();
 await append(call,2);await bottom();
 // A retained user bubble can still receive a late fragment after an assistant
 // reply. Growth above that reply must not strand it below the viewport. New
 // alternating speech also follows without any backend delegation/tool call.
 const speak=async messages=>{const response=await page.request.post(url+'/fixture/transcript',{data:{sessionId:call,messages}});assert.equal(response.status(),200);await workReady()};
 await speak([{id:'growth-user',role:'user',text:'Sure.'},{id:'growth-answer',role:'assistant',text:'Here are the findings you asked for.'}]);
 await expect(pane).toContainText('Here are the findings you asked for.');await bottom();
 for(const size of [20,60,120]){
  await speak([{id:'growth-user',role:'user',text:'Sure. '+('Later transcript detail. '.repeat(size))}]);
  await expect(pane).toContainText('Later transcript detail. '.repeat(size).trim());await bottom();
 }
 await speak([{id:'later-user',role:'user',text:'Are you working on that?'},{id:'later-answer',role:'assistant',text:'The requested answer is ready to explain.'}]);
 await expect(pane).toContainText('The requested answer is ready to explain.');await bottom();
 assert.equal(await pane.getByText('The requested answer is ready to explain.',{exact:true}).evaluate(el=>{const p=el.closest('.a-messages').getBoundingClientRect(),r=el.getBoundingClientRect();return r.top>=p.top&&r.bottom<=p.bottom}),true,'Newest spoken reply stays inside the conversation viewport');
 // Reading/clicking a transcript is not a request to stop following speech.
 const readingBox=await pane.boundingBox();await page.mouse.click(readingBox.x+60,readingBox.y+readingBox.height-100);
 await append(call,2);await bottom();
 await expect(observerPane).toContainText('Spoken item '+sequence+'\n');await workReady(observer);await expect.poll(()=>observerPane.evaluate(el=>el.scrollTop)).toBe(observerTop);await observer.close();
 // Work preparation notices may appear above messages while an append is read.
 // Compare raw scrollTop only after they settle, and retain the visible anchor.
 await scrollBack();const held=await top(),heldReading=await readingPosition();await append(call,2);await page.waitForTimeout(250);assert.deepEqual(await readingPosition(),heldReading,'Call scrollback retains the visible message and offset');assert.ok(Math.abs(await top()-held)<3,'Deliberate scrollback pauses call following');
 await action('session.select',{id:other});await scrollBack();const otherTop=await top(),otherReading=await readingPosition();await append(call,2);await page.waitForTimeout(250);
 // An unrelated update can briefly insert a Work preparation notice. Keep the
 // visible reading anchor throughout; compare raw scrollTop after it settles.
 assert.deepEqual(await readingPosition(),otherReading,'Call updates preserve the other conversation reading anchor');
 await workReady(page,other);
 assert.deepEqual(await readingPosition(),otherReading,'The other conversation reading anchor remains after Work settles');
 assert.ok(Math.abs(await top()-otherTop)<3,'Call updates must not scroll a different conversation');
 await action('session.select',{id:call});await workReady();await expect.poll(top).toBeCloseTo(held,0);
 await page.getByRole('button',{name:'Jump to latest messages'}).click();await bottom();await append(call,2);await bottom();
 await page.setViewportSize({width:390,height:844});await append(call,2);await bottom();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/unified-voice-follow-latest-mobile.png'});
 await action('call.end');await page.getByRole('button',{name:'End call',exact:true}).waitFor({state:'hidden'});
 await workReady();
 const geometry=()=>pane.evaluate(el=>({top:el.scrollTop,height:el.scrollHeight,viewport:el.clientHeight,width:el.clientWidth,rect:el.getBoundingClientRect().toJSON(),anchors:[...el.querySelectorAll('[data-message-id]')].filter(n=>n.getBoundingClientRect().bottom>el.getBoundingClientRect().top).slice(0,3).map(n=>({id:n.dataset.messageId,top:n.getBoundingClientRect().top,height:n.getBoundingClientRect().height}))}));const beforeGeometry=await geometry();
 const ended=await top();await append(call,2);await page.waitForTimeout(250);assert.ok(Math.abs(await top()-ended)<3,'Ending the call restores normal reading behavior: '+JSON.stringify({before:beforeGeometry,after:await geometry(),remaining:await remaining()}));
 const result=await(await page.request.get(url+'/fixture')).json();assert.deepEqual(result.sent,[]);assert.deepEqual(errors,[]);
 console.log('Voice view passed: live transcript growth, late earlier-bubble growth with newer replies visible, alternating speech without delegation, deliberate scrollback, other chat isolation, paused position on return, explicit latest resumes, mobile/reduced motion and hangup restores reading behavior. Synthetic signaling only; no physical audio acceptance.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
