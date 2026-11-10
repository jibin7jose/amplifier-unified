/** No recording loop: a selected source stays local until an explicit request. */
function displayFrame(track,video) {
 return new Promise((resolve,reject)=>{
  let settled=false,callback;
  const finish=(error,frame)=>{
   if(settled){frame?.close?.();return}
   settled=true;clearTimeout(timer);
   if(callback!==undefined)video.cancelVideoFrameCallback?.(callback);
   if(error)reject(error);else resolve(frame);
  };
  const timer=setTimeout(()=>finish(Error('No fresh screen frame arrived.')),2000);
  try {
   // A stationary tab/window may never present another video frame. Request a
   // snapshot of this track instead of requiring its pixels to change first.
   if(globalThis.ImageCapture)new globalThis.ImageCapture(track).grabFrame().then(frame=>finish(null,frame),error=>finish(error));
   else if(video.requestVideoFrameCallback)callback=video.requestVideoFrameCallback(()=>finish(null,video));
   else finish(Error('Fresh frame capture is unsupported by this browser.'));
  } catch(error){finish(error)}
 });
}

export class VoiceVisualClient {
 constructor({request,onState=()=>{},getVoice,scope='voice',media=globalThis.navigator?.mediaDevices}) {
  Object.assign(this,{request,onState,getVoice,media,scope});this.generation=0;this.stream=null;this.grant=null;this.video=null;
  this.onPageHide=()=>this.stop();globalThis.window?.addEventListener('pagehide',this.onPageHide);
 }
 url(operation){return `/api/${this.scope}/visual/${operation}`}
 target(voice){return {sessionId:voice.sessionId,callId:this.scope==='voice'?voice.id:null}}
 state(patch){this.value={...this.value,...patch};this.onState(this.value)}
 active(callId){const voice=this.getVoice();return voice?.status==='connected'&&voice.id===callId}
 async choose() {
  const voice=this.getVoice();
  if(voice?.status!=='connected'||!voice.id)throw Error(this.scope==='voice'?'Connect a voice call before choosing a screen source.':'Open a conversation before choosing a screen source.');
  if(!this.media?.getDisplayMedia)throw Error('Screen capture is unavailable in this browser. Use a desktop browser on localhost or HTTPS.');
  this.stop();const generation=this.generation;this.state({status:'choosing',error:null});
  try {
   // Must run synchronously from a user click; an agent cannot open this picker.
   const stream=await this.media.getDisplayMedia({video:{width:{ideal:1280},height:{ideal:1280}},audio:false});
   if(generation!==this.generation||!this.active(voice.id)){stream.getTracks().forEach(t=>t.stop());throw Error('The chat or call changed while choosing a source.');}
   this.stream=stream;const track=stream.getVideoTracks()[0];
   if(!track)throw Error('The selected source has no video track.');
   track.addEventListener('ended',()=>this.stop());
   const video=document.createElement('video');video.muted=true;video.playsInline=true;video.srcObject=stream;this.video=video;
   await video.play();
   if(generation!==this.generation||!this.active(voice.id))throw Error('The chat or call ended while preparing the source.');
   const kind=track.getSettings().displaySurface||'unknown',source={kind:['browser','window','monitor'].includes(kind)?kind:'unknown',label:(track.label||'User-selected source').slice(0,200)};
   const grant=await this.request(this.url('grant'),{method:'POST',body:{...this.target(voice),source}});
   if(generation!==this.generation||!this.active(voice.id)){await this.request(this.url('revoke'),{method:'POST',body:{grantId:grant.id}}).catch(()=>{});throw Error('The chat or call changed; source permission was discarded.');}
   this.grant=grant;this.expiry=setTimeout(()=>this.stop(),Math.max(0,grant.expiresAt*1000-Date.now()));this.state({status:'ready',source,grantId:grant.id});
  } catch(error){if(generation===this.generation){this.stop();this.state({status:'error',error:error.name==='NotAllowedError'?'Screen permission was not granted.':error.message})}throw error}
 }
 async checkNative() {
  const voice=this.getVoice();
  const native=await this.request(this.url('native/status'),{method:'POST',body:this.target(voice)});
  if(!this.active(voice.id))throw Error('The chat or call ended while checking the desktop host.');
  this.state({native});return native;
 }
 async chooseNative() {
  const voice=this.getVoice(),native=this.value?.native;
  if(!native?.available||!this.active(voice.id))throw Error('Check the desktop host before granting access.');
  this.stop();const generation=this.generation;
  const grant=await this.request(this.url('grant'),{method:'POST',body:{...this.target(voice),source:{kind:'native-foreground',hostId:native.host.id,hostInstanceId:native.hostInstanceId}}});
  if(generation!==this.generation||!this.active(voice.id)){await this.request(this.url('revoke'),{method:'POST',body:{grantId:grant.id}}).catch(()=>{});throw Error('The chat or call changed; source permission was discarded.');}
  this.grant=grant;this.expiry=setTimeout(()=>this.stop(),Math.max(0,grant.expiresAt*1000-Date.now()));this.state({status:'ready',source:grant.source,grantId:grant.id});
 }
 sync(voice,connected,serverVisual) {
  const identity=this.getVoice()?.id;
  if(this.scope==='computer'&&this.lastIdentity!==undefined&&this.lastIdentity!==identity){this.stop();this.state({native:null,capturedAt:null})}
  this.lastIdentity=identity;
  if(this.grant&&(!connected||!this.active(this.grant?.callId||this.grant?.sessionId||voice?.id)||this.grant&&(serverVisual?.available===false||serverVisual?.id&&serverVisual.id!==this.grant.id)))this.stop();
  if(this.grant&&serverVisual?.lastCapture?.grantId===this.grant.id&&serverVisual.lastCapture.capturedAt!==this.value?.capturedAt)this.state({capturedAt:serverVisual.lastCapture.capturedAt});
 }
 async capture(command) {
  const grant=this.grant,generation=this.generation;
  const target={requestId:command.id,grantId:command.grantId,sessionId:command.sessionId,callId:command.callId};
  try {
   if(!grant||command.grantId!==grant.id||command.callId!==grant.callId||command.sessionId!==grant.sessionId||!this.active(grant.callId||grant.sessionId))throw Error('This screen source is no longer authorized.');
   const track=this.stream?.getVideoTracks()[0];if(!track||track.readyState!=='live'||track.muted)throw Error('The selected source is unavailable or paused.');
   const video=this.video,frame=await displayFrame(track,video);let image;
   try {
    if(generation!==this.generation||!this.active(grant.callId||grant.sessionId)||track.readyState!=='live'||track.muted)throw Error('Screen sharing ended before capture.');
    const width=frame===video?video.videoWidth:frame.width,height=frame===video?video.videoHeight:frame.height;
    if(!width||!height)throw Error('The source did not provide visible pixels.');
    const canvas=document.createElement('canvas');const scale=Math.min(1,1280/Math.max(width,height));canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));canvas.getContext('2d').drawImage(frame,0,0,canvas.width,canvas.height);
    image=canvas.toDataURL('image/png').split(',')[1];if(image.length>666668)throw Error('This image is too large. Select a smaller window.');
   } finally {if(frame!==video)frame.close()}
   const capturedAt=Date.now()/1000;
   await this.request(this.url('complete'),{method:'POST',body:{...target,image,capturedAt}});
   if(generation===this.generation&&this.grant?.id===grant.id)this.state({status:'ready',capturedAt,error:null});
  } catch(error){this.state({error:error.message});await this.request(this.url('complete'),{method:'POST',body:{...target,error:error.message}}).catch(()=>{});}
 }
 stop(){
  ++this.generation;clearTimeout(this.expiry);const grant=this.grant;this.grant=null;
  this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;
  if(this.video){this.video.pause();this.video.srcObject=null;this.video=null}
  this.state({status:'idle',source:null,grantId:null,capturedAt:null});
  if(grant)this.request(this.url('revoke'),{method:'POST',body:{grantId:grant.id},keepalive:true}).catch(()=>{});
 }
 dispose(){this.stop();globalThis.window?.removeEventListener('pagehide',this.onPageHide)}
}

/** A browser-owned source belongs to the conversation, across text and voice. */
export class ComputerVisualClient extends VoiceVisualClient {
 constructor({getSession,...options}){super({...options,scope:'computer',getVoice:()=>{const sessionId=getSession();return {sessionId,id:sessionId,status:sessionId?'connected':'idle'}}})}
}
