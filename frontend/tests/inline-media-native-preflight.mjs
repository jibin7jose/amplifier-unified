// Tiny capability preflight. No app, accounts, model, or host desktop.
import {nativeKey as key,nativeInput,nativeControl,nativeTab,launchNativeBrowser,bounded,wrongWindowControl} from './inline-media-native-input.mjs';
import {writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const out=process.env.INLINE_MEDIA_EVIDENCE_DIR;
if(!out)throw Error('Explicit evidence directory required');
await mkdir(out,{recursive:true});
const evidence={input:nativeInput,phases:Object.fromEntries(
 ['launch','target-negative','sample','fullscreen','volume','zoom','visibility'].map(name=>[name,{status:'NOT_RUN'}]))};
let native,browser,context,current='launch';
try{
 native=await launchNativeBrowser();({browser,context}=native);
 evidence.phases.launch={status:'PASS'};current='sample';
 evidence.browser=browser.version();
 context.setDefaultTimeout(5000);
 const page=await context.newPage();
 await page.setContent('<title>Owned native media preflight</title><button id="start">Generate silent sample</button><video controls muted width="640" height="360"></video>');
 current='target-negative';
 await page.bringToFront();evidence.wrongWindow=wrongWindowControl();
 evidence.phases['target-negative']={status:'PASS'};current='sample';
 await page.locator('#start').evaluate(button=>button.onclick=()=>window.sample=(async()=>{
  const mime=['video/mp4;codecs=avc1.42001E,mp4a.40.2','video/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
  if(!mime)throw Error('Audio-bearing MP4 MediaRecorder unavailable');
  const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
  const ctx=canvas.getContext('2d'),stream=canvas.captureStream(10);
  const audio=new AudioContext(),oscillator=audio.createOscillator(),gain=audio.createGain(),destination=audio.createMediaStreamDestination();
  gain.gain.value=0;oscillator.connect(gain);gain.connect(destination);oscillator.start();await audio.resume();
  stream.addTrack(destination.stream.getAudioTracks()[0]);
  const chunks=[],recorder=new MediaRecorder(stream,{mimeType:mime});
  recorder.ondataavailable=e=>chunks.push(e.data);
  const stopped=new Promise(resolve=>recorder.onstop=resolve);recorder.start();
  // Distinct frames, not an alternating pattern whose first/last sampled
  // pixels can coincide despite real playback.
  for(let n=0;n<40;n++){ctx.fillStyle=`rgb(${n*5},${200-n*3},${n*2})`;ctx.fillRect(0,0,320,180);await new Promise(r=>setTimeout(r,100));}
  recorder.stop();await stopped;
  oscillator.stop();stream.getTracks().forEach(track=>track.stop());await audio.close();
  const blob=new Blob(chunks,{type:'video/mp4'});document.querySelector('video').src=URL.createObjectURL(blob);
  return {mime,audioTracks:1,audioGain:0,bytes:Array.from(new Uint8Array(await blob.arrayBuffer()))};
 })());
 await page.locator('#start').click();
  const sample=await bounded(page.evaluate(()=>window.sample),'Silent sample generation'),bytes=Buffer.from(sample.bytes);
  await writeFile(out+'/silent-audio.mp4',bytes);
  evidence.sample={mime:sample.mime,audioTracks:sample.audioTracks,audioGain:sample.audioGain,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
  await page.waitForFunction(()=>document.querySelector('video').readyState>=2);
  const cdp=await context.newCDPSession(page);
  evidence.ax=(await cdp.send('Accessibility.getFullAXTree')).nodes.filter(n=>['button','slider','Video'].includes(n.role?.value));
  evidence.dom=await cdp.send('DOM.getDocument',{depth:-1,pierce:true});
  await page.screenshot({path:out+'/preflight-controls.png'});
  evidence.phases.sample={status:'PASS'};current='fullscreen';
  evidence.fullscreenControl=await nativeControl(page,'video','button',/^enter full screen$/i);
  key('space');
  await page.waitForFunction(()=>document.fullscreenElement===document.querySelector('video'));
  evidence.fullscreen=true;
  await page.screenshot({path:out+'/preflight-fullscreen.png'});
  key('Escape');await page.waitForFunction(()=>document.fullscreenElement===null);
  evidence.fullscreenExit=true;
  evidence.phases.fullscreen={status:'PASS'};current='volume';
  await nativeControl(page,'video','button',/^unmute$/i);key('space');
  await page.waitForFunction(()=>!document.querySelector('video').muted);
  evidence.volumeBefore=await page.locator('video').evaluate(v=>({volume:v.volume,muted:v.muted,paused:v.paused}));
  evidence.volumeControl=await nativeControl(page,'video','slider',/^volume$/i);
  key('Left');
  await page.waitForFunction(before=>document.querySelector('video').volume!==before,evidence.volumeBefore.volume);
  evidence.volumeAfter=await page.locator('video').evaluate(v=>({volume:v.volume,muted:v.muted,paused:v.paused}));
  assert.equal(evidence.volumeAfter.paused,true);
  evidence.phases.volume={status:'PASS'};current='zoom';
 const metrics=()=>page.evaluate(()=>({dpr:devicePixelRatio,innerWidth,outerWidth,screenWidth:screen.width,visualScale:visualViewport.scale,scrollWidth:document.documentElement.scrollWidth,hidden:document.hidden,visibilityState:document.visibilityState}));
 key('Control_L','0');
 await page.waitForFunction(()=>devicePixelRatio===1);
 evidence.baseline=await metrics();
 for(let n=0;n<5;n++){key('Control_L','equal');await page.waitForTimeout(100);}
 await page.waitForFunction(()=>devicePixelRatio===2,{},{timeout:3000});
 evidence.zoomed=await metrics();assert.equal(evidence.zoomed.dpr,2);
 key('Control_L','0');await page.waitForFunction(()=>devicePixelRatio===1);
 evidence.reset=await metrics();evidence.phases.zoom={status:'PASS'};
 current='visibility';
 const other=await nativeTab(page);
 await other.goto('data:text/html,<title>Owned background control</title>Second actual tab');
 await other.bringToFront();
 await page.waitForFunction(()=>document.hidden,{},{polling:100,timeout:3000});
 evidence.background=await metrics();
 assert.equal(evidence.background.hidden,true);assert.equal(evidence.background.visibilityState,'hidden');
 await page.bringToFront();evidence.foreground=await metrics();assert.equal(evidence.foreground.hidden,false);
 evidence.phases.visibility={status:'PASS'};
}catch(error){
 evidence.phases[current]={status:'NOT_QUALIFIED',reason:error.message};
 evidence.error={phase:current,message:error.message,stack:error.stack,cleanup:error.cleanup};
}
finally{
 // Retain the first outcome even if shutdown cannot settle.
 try{await writeFile(out+'/preflight-before-cleanup.json',JSON.stringify(evidence,null,2))}
 catch(error){evidence.retentionError=error.message}
 try{if(native)evidence.cleanup=await native.close()}
 catch(error){evidence.cleanup={status:'FAIL',reason:error.message,...error.cleanup}}
 evidence.disconnected=browser?!browser.isConnected():null;
 evidence.closed=evidence.cleanup?.processExited===true;
 evidence.qualified=Object.values(evidence.phases).every(row=>row.status==='PASS')&&
  evidence.closed&&evidence.cleanup?.profileRemoved===true&&evidence.cleanup?.status==='PASS'&&
  Array.isArray(evidence.cleanup?.errors)&&evidence.cleanup.errors.length===0&&!evidence.retentionError;
 process.exitCode=evidence.qualified?0:1;
 await writeFile(out+'/preflight.json',JSON.stringify(evidence,null,2));
 console.log(JSON.stringify({...evidence,ax:undefined,dom:undefined}));
}
