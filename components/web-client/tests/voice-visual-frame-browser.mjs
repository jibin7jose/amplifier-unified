import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium} from '@playwright/test';

// Real browser media primitives, entirely synthetic pixels and intercepted HTTP.
// No getDisplayMedia, user screen, voice provider, or running app is accessed.
const source=await readFile(new URL('../src/voice-visual.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage();
 await page.route('**/*',route=>route.fulfill({contentType:route.request().url().endsWith('/voice-visual.js')?'text/javascript':'text/html',body:route.request().url().endsWith('/voice-visual.js')?source:'<!doctype html><title>Synthetic display track</title>'}));
 await page.goto('http://localhost:32119');
 const result=await page.evaluate(async()=>{
  const {VoiceVisualClient}=await import('/voice-visual.js');
  const voice={id:'fixture-call',sessionId:'fixture-session',status:'connected'},requests=[],rows=[];
  let kind,color,track,canvas;
  const value=new VoiceVisualClient({getVoice:()=>voice,media:{async getDisplayMedia(){
   canvas=document.createElement('canvas');canvas.width=320;canvas.height=200;const context=canvas.getContext('2d');context.fillStyle=color;context.fillRect(0,0,320,200);
   const stream=canvas.captureStream(0);track=stream.getVideoTracks()[0];track.getSettings=()=>({displaySurface:kind});return stream;
  }},request:async(path,options)=>{
   requests.push([path,options.body]);
   if(path.endsWith('/grant'))return {id:'grant-'+kind,callId:voice.id,sessionId:voice.sessionId,expiresAt:Date.now()/1000+60,source:options.body.source};
   return {accepted:true};
  }});
  try {
   for(const spec of [['browser','#ff0000'],['window','#0000ff'],['monitor','#00ff00']]) {
    [kind,color]=spec;await value.choose();
    // Leave a valid current frame but generate no subsequent presentation frame.
    await new Promise(resolve=>setTimeout(resolve,100));
    await value.capture({id:'capture-'+kind,grantId:value.grant.id,callId:voice.id,sessionId:voice.sessionId});
    const complete=requests.filter(([path])=>path.endsWith('/complete')).at(-1)[1];
    let pixel=null;
    if(complete.image){const image=new Image();image.src='data:image/png;base64,'+complete.image;await image.decode();const read=document.createElement('canvas');read.width=320;read.height=200;const c=read.getContext('2d');c.drawImage(image,0,0);pixel=[...c.getImageData(10,10,1,1).data]}
    rows.push({kind,error:complete.error??null,pixel,trackState:track.readyState});
   }
   return rows;
  } finally {value.dispose()}
 });
 assert.deepEqual(result,[
  {kind:'browser',error:null,pixel:[255,0,0,255],trackState:'live'},
  {kind:'window',error:null,pixel:[0,0,255,255],trackState:'live'},
  {kind:'monitor',error:null,pixel:[0,255,0,255],trackState:'live'},
 ]);
 console.log(JSON.stringify({passed:true,cases:result,scope:'Synthetic stationary MediaStreamTracks in actual Chromium; no physical screen, voice, or model acceptance.'}));
} finally {await browser.close()}
