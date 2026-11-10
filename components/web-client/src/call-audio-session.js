/** Optional browser/OS call integration. It cannot grant background execution. */
export class CallAudioSession {
  constructor({navigator=globalThis.navigator, document=globalThis.document,
    Metadata=globalThis.MediaMetadata, getAudio=()=>null, onState=()=>{}, onAction=()=>{}, onError=()=>{}}={}) {
    Object.assign(this,{navigator,document,Metadata,getAudio,onState,onAction,onError});
    this.active=false;this.epoch=0;this.handlers=[];this.trackListeners=[];this.tracks=[];
    this.onVisibility=()=>{if(this.active&&document?.visibilityState==='visible')this.resumePlayback();};
    document?.addEventListener('visibilitychange',this.onVisibility);
  }

  start() {
    if(this.active)return;
    this.active=true;++this.epoch;
    this.onState({audioSessionState:'unavailable',microphoneInterrupted:false,microphoneEnded:false,playbackBlocked:false});
    this.audioSession=this.navigator?.audioSession;
    if(this.audioSession) {
      try {this.previousType=this.audioSession.type;this.audioSession.type='play-and-record';} catch {this.previousType=undefined;}
      this.audioChanged=()=>{
        if(!this.active)return;
        this.onState({audioSessionState:this.audioSession.state});this.syncMicrophone();
        if(this.audioSession.state==='active')this.resumePlayback();
      };
      this.audioSession.addEventListener?.('statechange',this.audioChanged);
      this.audioChanged();
    }
    this.mediaSession=this.navigator?.mediaSession;
    if(!this.mediaSession)return;
    try {
      this.previousMetadata=this.mediaSession.metadata;
      this.previousPlaybackState=this.mediaSession.playbackState;
      if(this.Metadata){this.metadata=new this.Metadata({title:'Amplifier voice call'});this.mediaSession.metadata=this.metadata;}
      this.mediaSession.playbackState='playing';
    } catch { /* Media integration must not prevent a call. */ }
    for(const [name,action] of [['togglemicrophone',()=>this.onAction('call.mute',{muted:!this.muted})],['hangup',()=>this.onAction('call.end')]]) {
      const epoch=this.epoch;
      try {
        this.mediaSession.setActionHandler(name,()=>{
          if(!this.active||epoch!==this.epoch)return;
          try {Promise.resolve(action()).catch(error=>this.onError(error));} catch(error){this.onError(error);}
        });
        this.handlers.push(name);
      } catch { /* Each action has independent browser support. */ }
    }
  }

  attachStream(stream) {
    this.removeTrackListeners();
    this.tracks=stream.getAudioTracks();
    for(const track of this.tracks)for(const name of ['mute','unmute','ended']) {
      const listener=()=>{
        if(!this.active)return;
        this.syncMicrophone();
        if(name==='unmute')this.resumePlayback();
      };
      track.addEventListener(name,listener);this.trackListeners.push([track,name,listener]);
    }
    this.syncMicrophone();
  }

  setMuted(muted) {this.muted=muted;this.syncMicrophone();}

  syncMicrophone() {
    if(!this.active)return;
    const live=this.tracks.filter(track=>track.readyState!=='ended');
    const interrupted=live.some(track=>track.muted);
    this.onState({microphoneInterrupted:interrupted,microphoneEnded:this.tracks.length>0&&!live.length});
    try {Promise.resolve(this.mediaSession?.setMicrophoneActive?.(!this.muted&&this.audioSession?.state!=='interrupted'&&live.some(track=>track.enabled&&!track.muted))).catch(()=>{});} catch {}
  }

  async resumePlayback() {
    const audio=this.getAudio();
    if(!this.active||!audio?.srcObject||this.audioSession?.state==='interrupted'||this.playPending)return;
    const epoch=this.epoch;
    // Invoke play synchronously so a Resume audio click retains user activation.
    let pending;
    try {pending=Promise.resolve(audio.play());} catch(error){pending=Promise.reject(error);}
    this.playPending=pending;
    try {await pending;if(this.active&&epoch===this.epoch)this.onState({playbackBlocked:false});}
    catch {if(this.active&&epoch===this.epoch)this.onState({playbackBlocked:true});}
    finally {if(this.playPending===pending)this.playPending=null;}
  }

  removeTrackListeners() {
    for(const [track,name,listener] of this.trackListeners)track.removeEventListener(name,listener);
    this.trackListeners=[];this.tracks=[];
  }

  stop() {
    if(!this.active)return;
    this.active=false;++this.epoch;this.playPending=null;
    this.removeTrackListeners();
    this.audioSession?.removeEventListener?.('statechange',this.audioChanged);
    try {if(this.previousType!==undefined&&this.audioSession.type==='play-and-record')this.audioSession.type=this.previousType;} catch {}
    for(const name of this.handlers)try {this.mediaSession.setActionHandler(name,null);} catch {}
    this.handlers=[];
    try {Promise.resolve(this.mediaSession?.setMicrophoneActive?.(false)).catch(()=>{});} catch {}
    try {
      if(this.mediaSession&&(!this.metadata||this.mediaSession.metadata===this.metadata)) {
        this.mediaSession.metadata=this.previousMetadata??null;
        this.mediaSession.playbackState=this.previousPlaybackState||'none';
      }
    } catch {}
    this.audioSession=this.mediaSession=this.metadata=null;
    this.onState({audioSessionState:'inactive',microphoneInterrupted:false,microphoneEnded:false,playbackBlocked:false});
  }

  dispose() {this.stop();this.document?.removeEventListener('visibilitychange',this.onVisibility);}
}
