import {CallWakeLock} from './call-wake-lock.js';
import {CallAudioSession} from './call-audio-session.js';

const defaults = {ice: 10000, connect: 45000, request: 12000, health: 10000, close: 12000, closeFallback: 2000};
const deferred = () => {let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};

/** Browser media belongs to one setup attempt; accepted work belongs to the host. */
export class VoiceClient {
  constructor({request, onState = () => {}, onError = () => {}, onAction, timeouts = {}}) {
    Object.assign(this, {request, onState, onError});
    this.timeouts = {...defaults, ...timeouts};
    this.state = {status: 'idle', muted: false, keepAwake: true, wakeLock: 'off'};
    this.wakeLock = new CallWakeLock({onState: wakeLock => this.update({wakeLock})});
    this.generation = 0;
    this.call = null;
    this.retiredAttempts = new Set();
    this.callAudio = new CallAudioSession({getAudio:()=>this.call?.audio,onState:patch=>this.update(patch),onError,
      onAction:onAction||((action,args)=>this.control(action,{...this.scope(),...args}))});
    this.onPageHide = () => {
      const call = this.call;
      if (!call) return;
      if(call.connecting || call.id)this.request('/api/voice/end', {method:'POST',body:this.identity(call),keepalive:true}).catch(()=>{});
      this.finish(call, {closed:true,finalized:false,workContinues:true});
    };
    window.addEventListener('pagehide', this.onPageHide);
  }

  current(call) {return this.call === call && !call.closed;}
  scope() {return this.call ? {attemptId:this.call.attemptId,...(this.call.id?{callId:this.call.id}:{})} : null;}
  identity(call) {return {attemptId:call.attemptId,...(call.id?{id:call.id}:{})};}
  matches(scope = {}) {
    return !!this.call && (!scope.callId || scope.callId === this.call.id) && (!scope.attemptId || scope.attemptId === this.call.attemptId);
  }

  update(patch) {
    this.state = {...this.state, ...patch};
    this.onState({...this.state});
    if(patch.status)this.wakeLock.setActive(['connecting','connected'].includes(patch.status));
  }

  async requestWithin(path, options, timeout = this.timeouts.request) {
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([
        this.request(path, {...options,signal:controller.signal}),
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('The voice control request timed out.')),timeout);}),
      ]);
    } finally {clearTimeout(timer);controller.abort();}
  }

  async gatherIce(call) {
    const peer = call.peer;
    if (peer.iceGatheringState === 'complete') return;
    await new Promise((resolve,reject)=>{
      let timer;
      const finish = error => {
        clearTimeout(timer);
        peer.removeEventListener('icegatheringstatechange',changed);
        call.cancelIce = null;
        error ? reject(error) : resolve();
      };
      const changed = () => {if(peer.iceGatheringState === 'complete')finish();};
      call.cancelIce = () => finish(new Error('Voice setup was canceled.'));
      timer = setTimeout(()=>finish(new Error('Voice could not gather network connection details. Check your network and try again.')),this.timeouts.ice);
      peer.addEventListener('icegatheringstatechange',changed);
      changed();
    });
  }

  async start({provider = 'auto', sessionId = null, attemptId = crypto.randomUUID()} = {}) {
    if (this.call || this.retiredAttempts.has(attemptId)) return this.state;
    if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
      throw new Error('Voice needs a browser with microphone and WebRTC support, on localhost or HTTPS.');
    }
    const call = this.call = {generation:++this.generation,attemptId,sessionId,controls:new Set(),final:deferred()};
    this.update({status:'connecting',id:null,attemptId,sessionId,error:null,muted:false,finalized:false,controlStatus:'connecting',provider:null,model:null,fallbackReason:null});
    try {
      const config = await this.requestWithin('/api/voice/config');
      if (!this.current(call)) return this.state;
      if (!config.available) throw new Error(config.reason || 'Voice is not configured on the host.');
      this.callAudio.start();
      this.callAudio.setMuted(this.state.muted);
      const stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if (!this.current(call) || call.closing) {
        stream.getTracks().forEach(track=>track.stop());
        return this.state;
      }
      call.stream = stream;
      stream.getAudioTracks().forEach(track=>{track.enabled=!this.state.muted;});
      this.callAudio.attachStream(stream);
      const peer = call.peer = new RTCPeerConnection();
      call.audio = new Audio();
      call.audio.autoplay = true;
      call.audio.setAttribute('playsinline','');
      peer.addEventListener('track',event=>{
        if (!this.current(call) || call.closing) return;
        call.audio.srcObject = event.streams[0] || new MediaStream([event.track]);
        this.callAudio.resumePlayback();
      });
      stream.getTracks().forEach(track=>peer.addTrack(track,stream));
      const channel = call.events = peer.createDataChannel('oai-events');
      channel.addEventListener('open',()=>this.ready(call));
      channel.addEventListener('message',event=>{
        if (!this.current(call)) return;
        let data;
        try {data=JSON.parse(event.data);} catch {return;}
        // The server records transcripts and owns delegation. These events only
        // establish media readiness and finalization for this browser's call.
        if (data.type === 'session.started' && (!data.session?.id || data.session.id === call.id)) {
          call.started = true;
          this.ready(call);
        } else if (data.type === 'session.closed') {
          this.finish(call,{closed:true,finalized:true,workContinues:true,usage:data.usage,reason:data.reason});
        } else if(data.type==='error') {
          this.report(call,'Voice provider reported an error: '+(data.error?.code||'unknown'));
        }
      });
      for (const name of ['close','error']) channel.addEventListener(name,()=>{
        if(this.current(call) && !call.closing)this.fail(call,'Voice control disconnected. Your background work continues.');
      });
      peer.addEventListener('connectionstatechange',()=>{
        if (!this.current(call) || call.closing) return;
        if (peer.connectionState === 'connected') {
          clearTimeout(call.mediaTimer);
          call.mediaTimer = null;
          this.update({mediaStatus:'connected'});
          this.ready(call);
        } else if (peer.connectionState === 'disconnected') {
          this.update({mediaStatus:'reconnecting'});
          call.mediaTimer ||= setTimeout(()=>this.fail(call,'Voice media did not reconnect. Your background work continues.'),this.timeouts.health);
        } else if (['failed','closed'].includes(peer.connectionState)) {
          this.fail(call,'The voice connection failed. Your background work continues.');
        }
      });
      call.connectionTimer = setTimeout(()=>this.fail(call,'Voice media could not connect. Check microphone and network access, then try again.'),this.timeouts.connect);
      await peer.setLocalDescription(await peer.createOffer());
      if (!this.current(call) || call.closing) return this.state;
      await this.gatherIce(call);
      if (!this.current(call) || call.closing) return this.state;
      // Keep receiving a late creation response so cancellation can clean up
      // precisely the session it created, without touching a replacement call.
      call.connecting = true;
      const answer = await this.request('/api/voice/connect',{method:'POST',body:{sdp:peer.localDescription.sdp,provider,sessionId,attemptId}});
      call.id = answer.id;
      if (!this.current(call) || call.closing) {
        this.requestWithin('/api/voice/end',{method:'POST',body:this.identity(call)}).catch(()=>{});
        return this.state;
      }
      call.provider = answer.provider;
      this.update({id:answer.id,provider:answer.provider,model:answer.model,voice:answer.voice,preferredModel:answer.preferredModel,fallbackReason:answer.fallbackReason||null});
      await peer.setRemoteDescription({type:'answer',sdp:answer.sdp});
      this.ready(call);
      return this.state;
    } catch (error) {
      if (!this.current(call) || call.closing) return this.state;
      const explanation = error.name === 'NotAllowedError' ? 'Microphone access was declined. Allow microphone access for this site, then call again.' : error.message;
      if (call.id) this.requestWithin('/api/voice/end',{method:'POST',body:this.identity(call)}).catch(()=>{});
      this.finish(call,{closed:true,finalized:false,workContinues:true});
      this.update({status:'error',error:explanation});
      throw new Error(explanation);
    }
  }

  async ready(call) {
    if (!this.current(call) || call.closing || call.readySent || !call.id || call.peer.connectionState !== 'connected' || call.events.readyState !== 'open' || call.provider === 'live' && !call.started) return;
    call.readySent = true;
    try {
      await this.requestWithin('/api/voice/ready',{method:'POST',body:this.identity(call)});
      if (!this.current(call) || call.closing) return;
      clearTimeout(call.connectionTimer);
      this.update({status:'connected',controlStatus:'ready',mediaStatus:'connected'});
    } catch (error) {
      if (this.current(call) && !call.closing) this.fail(call,error.message);
    }
  }

  /** Reconcile host control health only for the call this browser owns. */
  sync(voice = {}, hostConnected = true) {
    const call = this.call;
    if (!call || call.closing || !call.id) return;
    if (voice.id === call.id) {
      if (['ended','disconnected'].includes(voice.status)) {
        this.finish(call,{closed:true,finalized:voice.finalized===true,workContinues:true,usage:voice.usage});
        return;
      }
      if (voice.status === 'error' || voice.controlStatus === 'error') {
        this.fail(call,voice.error||'Voice control disconnected. Your background work continues.');
        return;
      }
      if(voice.error)this.report(call,voice.error);
    }
    if (!hostConnected) {
      if(this.state.controlStatus!=='reconnecting')this.update({controlStatus:'reconnecting'});
      call.hostTimer ||= setTimeout(()=>this.fail(call,'The connection to Amplifier did not recover. Your background work continues.'),this.timeouts.health);
    } else {
      clearTimeout(call.hostTimer);call.hostTimer=null;
      if(this.state.status==='connected' && this.state.controlStatus!=='ready')this.update({controlStatus:'ready'});
    }
  }

  control(action, args = {}) {
    if (!this.matches(args)) return Promise.resolve({closed:true,ignored:true});
    const call = this.call;
    if (args.controlId && call.controls.has(args.controlId)) return call.ending || Promise.resolve();
    if (args.controlId) call.controls.add(args.controlId);
    if (action === 'call.mute') this.setMuted(args.muted);
    if (action === 'call.end') return this.end(args);
    return Promise.resolve();
  }

  setKeepAwake(enabled) {this.update({keepAwake:Boolean(enabled)});this.wakeLock.setEnabled(Boolean(enabled));}
  setMuted(muted) {
    const call = this.call;
    if (!call || call.closing) return;
    const value = Boolean(muted);
    call.stream?.getAudioTracks().forEach(track=>{track.enabled=!value;});
    this.update({muted:value});
    this.callAudio.setMuted(value);
  }

  fail(call, message) {
    if (!this.current(call) || call.closing) return;
    this.update({controlStatus:'error'});
    this.report(call,message);
    this.end(this.scope());
  }

  report(call, message) {
    if(!this.current(call)||call.closing||call.reportedError===message)return;
    call.reportedError=message;
    this.update({error:message});
    this.onError(new Error(message));
  }

  end(scope = {}) {
    if (!this.matches(scope)) return Promise.resolve({closed:true,finalized:this.state.finalized===true,workContinues:true});
    const call = this.call;
    if (call.ending) return call.ending;
    call.closing = true;
    call.cancelIce?.();
    clearTimeout(call.connectionTimer);clearTimeout(call.mediaTimer);clearTimeout(call.hostTimer);
    // A local hangup never waits for HTTP before silencing microphone/speaker.
    call.stream?.getAudioTracks().forEach(track=>{track.enabled=false;});
    if(call.audio){call.audio.muted=true;call.audio.pause();}
    this.callAudio.setMuted(true);
    this.update({status:'ending',muted:true,controlStatus:'ending',mediaStatus:'ending'});
    if(!call.connecting && !call.id){const result={closed:true,finalized:true,workContinues:true};this.finish(call,result);return Promise.resolve(result);}
    const controller = new AbortController();
    let timer, fallback;
    const closeThroughMedia = () => {
      if(!this.current(call) || call.provider!=='live' || !call.started || call.events?.readyState!=='open' || call.closeSent)return;
      call.closeSent=true;
      try{call.events.send(JSON.stringify({type:'session.close',event_id:crypto.randomUUID()}));}catch{}
    };
    const timeout = new Promise(resolve=>{timer=setTimeout(()=>resolve({closed:true,finalized:false,workContinues:true}),this.timeouts.close);});
    // The sideband normally owns close. The primary transport can still finish
    // a Live session if the browser's application-server connection is stalled.
    fallback=setTimeout(closeThroughMedia,this.timeouts.closeFallback);
    const request = this.request('/api/voice/end',{method:'POST',body:this.identity(call),signal:controller.signal}).catch(()=>{closeThroughMedia();return call.final.promise;});
    call.ending = Promise.race([call.final.promise,request,timeout]).then(result=>{
      const final = {closed:true,workContinues:true,...result,finalized:result?.finalized===true};
      if(this.current(call))this.finish(call,final);
      if(call.id && !final.finalized)this.onError(new Error('Call ended locally. Final provider usage could not be confirmed.'));
      return final;
    }).finally(()=>{clearTimeout(timer);clearTimeout(fallback);controller.abort();});
    return call.ending;
  }

  finish(call, result) {
    if (!this.current(call)) return;
    call.closed = true;
    ++this.generation;
    this.retiredAttempts.add(call.attemptId);
    if(this.retiredAttempts.size>100)this.retiredAttempts.delete(this.retiredAttempts.values().next().value);
    this.release(call);
    this.call = null;
    this.update({status:'ended',id:null,attemptId:null,finalized:result.finalized===true,controlStatus:'ended',mediaStatus:'ended'});
    call.final.resolve(result);
  }

  release(call) {
    this.callAudio.stop();
    this.wakeLock.setActive(false);
    clearTimeout(call.connectionTimer);clearTimeout(call.mediaTimer);clearTimeout(call.hostTimer);
    call.cancelIce?.();
    call.stream?.getTracks().forEach(track=>track.stop());
    call.events?.close();call.peer?.close();
    if(call.audio){call.audio.pause();call.audio.srcObject=null;}
  }

  dispose() {
    this.onPageHide();
    window.removeEventListener('pagehide',this.onPageHide);
    this.wakeLock.dispose();this.callAudio.dispose();
  }
}
