import test from 'node:test';
import assert from 'node:assert/strict';
import {VoiceVisualClient} from '../src/voice-visual.js';
const target={status:'connected',id:'call-one',sessionId:'session-one'};
function client(){const requests=[],states=[];const value=new VoiceVisualClient({request:async(...args)=>{requests.push(args);return {}},onState:s=>states.push({...s}),getVoice:()=>target,media:{}});return {value,requests,states}}

test('missing browser permission API is explicit, no request or capture',async()=>{const {value,requests}=client();await assert.rejects(value.choose(),/unavailable in this browser/);assert.equal(requests.length,0);value.dispose()});
test('reconnect and replaced call revoke, release tracks, never restore a source',()=>{for(const reason of ['disconnect','replace','revoke']){const {value,requests}=client();let stopped=0;value.stream={getTracks:()=>[{stop(){stopped++}}]};value.grant={id:'grant',callId:reason==='replace'?'old-call':target.id};value.sync(target,reason!=='disconnect',reason==='revoke'?{available:false}:{available:true});assert.equal(stopped,1);assert.equal(value.grant,null);assert.match(requests[0][0],/revoke/);value.sync(target,true,{available:true,id:'grant'});assert.equal(value.stream,null);value.dispose()}});
test('stale commands return an explicit error without attempting a video frame',async()=>{const {value,requests}=client();await value.capture({id:'late',grantId:'old',callId:'old',sessionId:'other'});assert.match(requests[0][1].body.error,/no longer authorized/);assert.equal(requests[0][1].body.image,undefined);value.dispose()});

test('native check and grant are separate explicit requests without browser capture',async()=>{
 const requests=[],host={id:'fixture-host',label:'Fixture host'};let voice={...target};
 const value=new VoiceVisualClient({getVoice:()=>voice,media:{getDisplayMedia(){throw Error('must not capture browser')}},request:async(path,options)=>{requests.push([path,options]);if(path.endsWith('/status'))return {available:true,host,hostInstanceId:'instance'};if(path.endsWith('/grant'))return {id:'native',callId:target.id,sessionId:target.sessionId,expiresAt:Date.now()/1000+60,source:{kind:'native-foreground',label:host.label}};return {}}});
 await assert.rejects(value.chooseNative(),/Check the desktop host/);assert.equal(requests.length,0);
 await value.checkNative();assert.equal(value.grant,null);assert.equal(requests.length,1);
 await value.chooseNative();assert.equal(value.stream,null);assert.equal(value.grant.id,'native');assert.deepEqual(requests[1][1].body.source,{kind:'native-foreground',hostId:'fixture-host',hostInstanceId:'instance'});
 value.sync(voice,false,{available:true,id:'native'});assert.equal(value.grant,null);assert.match(requests.at(-1)[0],/revoke/);value.dispose();
});

function captureFixture(t,{grab,legacy=false}={}) {
 const {value,requests}=client(),draws=[],cancelled=[];let closed=0,callbacks=0;
 const track={readyState:'live',muted:false,stop(){this.readyState='ended'}};
 const bitmap={width:1920,height:1080,close(){closed++}};
 const video={videoWidth:640,videoHeight:360,pause(){},requestVideoFrameCallback(fn){callbacks++;this.callback=fn;return 7},cancelVideoFrameCallback(id){cancelled.push(id)}};
 const previous={document:globalThis.document,ImageCapture:globalThis.ImageCapture};
 globalThis.document={createElement(type){assert.equal(type,'canvas');return {getContext:()=>({drawImage(...args){draws.push(args)}}),toDataURL:()=> 'data:image/png;base64,cGl4ZWxz'}}};
 globalThis.ImageCapture=legacy?undefined:class {constructor(actual){assert.equal(actual,track)}grabFrame(){return grab?grab(bitmap):Promise.resolve(bitmap)}};
 t.after(()=>{value.dispose();for(const [key,old] of Object.entries(previous)){if(old===undefined)delete globalThis[key];else globalThis[key]=old}});
 value.stream={getVideoTracks:()=>[track],getTracks:()=>[track]};value.video=video;
 value.grant={id:'grant',callId:target.id,sessionId:target.sessionId};
 const command={id:'capture',grantId:'grant',callId:target.id,sessionId:target.sessionId};
 return {value,requests,track,bitmap,video,draws,cancelled,command,get closed(){return closed},get callbacks(){return callbacks}};
}

test('explicit snapshot reads a stationary selected track without waiting for video presentation',async t=>{
 const f=captureFixture(t);await f.value.capture(f.command);
 assert.equal(f.callbacks,0);assert.equal(f.draws[0][0],f.bitmap);assert.deepEqual(f.draws[0].slice(1),[0,0,1280,720]);
 assert.equal(f.closed,1);assert.equal(f.requests[0][1].body.image,'cGl4ZWxz');
});
test('a revoked source discards and closes an in-flight bitmap without uploading it',async t=>{
 let finish;const f=captureFixture(t,{grab:()=>new Promise(r=>finish=r)});
 const capture=f.value.capture(f.command);f.value.stop();finish(f.bitmap);await capture;
 assert.equal(f.closed,1);assert.equal(f.draws.length,0);assert.ok(f.requests.every(([,options])=>!options.body.image));
});
test('track capture failure does not fall back to an old video frame',async t=>{
 const f=captureFixture(t,{grab:()=>Promise.reject(Error('Track snapshot failed'))});await f.value.capture(f.command);
 assert.equal(f.callbacks,0);assert.equal(f.draws.length,0);assert.equal(f.requests[0][1].body.error,'Track snapshot failed');
});
test('timed-out track capture closes a late bitmap and never uploads it',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});let finish;
 const f=captureFixture(t,{grab:()=>new Promise(r=>finish=r)});const capture=f.value.capture(f.command);
 t.mock.timers.tick(2001);await capture;finish(f.bitmap);await Promise.resolve();
 assert.equal(f.closed,1);assert.equal(f.draws.length,0);assert.match(f.requests[0][1].body.error,/No fresh screen frame/);
});
test('browsers without ImageCapture retain the fresh video-frame fallback',async t=>{
 const f=captureFixture(t,{legacy:true});const capture=f.value.capture(f.command);f.video.callback();await capture;
 assert.equal(f.draws[0][0],f.video);assert.equal(f.requests[0][1].body.image,'cGl4ZWxz');
});
test('the video fallback cancels its callback when a frame times out',async t=>{
 t.mock.timers.enable({apis:['setTimeout']});const f=captureFixture(t,{legacy:true});const capture=f.value.capture(f.command);
 t.mock.timers.tick(2001);await capture;assert.deepEqual(f.cancelled,[7]);assert.equal(f.draws.length,0);
});

import {ComputerVisualClient} from '../src/voice-visual.js';
test('text chat native consent is distinct, session scoped and cleared on switch and disconnect',async()=>{
 let session='chat-one';const requests=[];
 const value=new ComputerVisualClient({getSession:()=>session,request:async(path,{body})=>{requests.push([path,body]);return path.endsWith('/status')?{available:true,host:{id:'host',label:'Synthetic'},hostInstanceId:'host-instance'}:{id:'grant',sessionId:session,callId:null,expiresAt:Date.now()/1000+60,source:{kind:'native-foreground'}}}});
 value.sync(null,true,{});await value.checkNative();assert.equal(value.grant,null);
 await value.chooseNative();assert.equal(requests[1][0],'/api/computer/visual/grant');assert.equal(requests[1][1].callId,null);
 assert.equal(value.grant.id,'grant');session='chat-two';value.sync(null,true,{});
 assert.equal(value.grant,null);assert.equal(value.value.native,null);
 session='chat-one';value.sync(null,true,{id:'grant',available:true});assert.equal(value.grant,null);
 await value.capture({id:'old',grantId:'grant',sessionId:session,callId:null});assert.ok(requests.every(([,body])=>!body.image));value.dispose();
});
test('changing text chats while the picker is pending discards its late track',async()=>{
 let session='chat-one',finish,stopped=0;const requests=[];
 const value=new ComputerVisualClient({getSession:()=>session,media:{getDisplayMedia:()=>new Promise(r=>finish=r)},request:async(...args)=>{requests.push(args)}});
 value.sync(null,true,{});const choosing=value.choose();session='chat-two';value.sync(null,true,{});session='chat-one';value.sync(null,true,{});
 finish({getTracks:()=>[{stop(){stopped++}}]});await assert.rejects(choosing,/changed/);
 assert.equal(stopped,1);assert.equal(requests.length,0);value.dispose();
});
