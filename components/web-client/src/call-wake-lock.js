/** Keep an active call visible; this is not background execution permission. */
export class CallWakeLock {
 constructor({document=globalThis.document,navigator=globalThis.navigator,onState=()=>{}}={}){
  this.document=document;this.navigator=navigator;this.onState=onState;
  this.active=false;this.enabled=true;this.sentinel=null;this.pending=null;this.epoch=0;
  this.visible=()=>{if(this.document?.visibilityState==='visible')this.acquire();};
  this.document?.addEventListener('visibilitychange',this.visible);
 }
 setActive(active){
  if(this.active===active)return;
  this.active=active;
  if(active)this.acquire();else this.release();
 }
 setEnabled(enabled){this.enabled=enabled;if(enabled)this.acquire();else this.release();}
 async acquire(){
  if(!this.active||!this.enabled||this.document?.visibilityState!=='visible'||this.sentinel||this.pending)return;
  if(!this.navigator?.wakeLock?.request){this.onState('unsupported');return;}
  const epoch=this.epoch;
  const pending=this.pending=Promise.resolve().then(()=>this.navigator.wakeLock.request('screen'));
  try{
   const sentinel=await pending;
   if(epoch!==this.epoch||!this.active||!this.enabled||this.document.visibilityState!=='visible'){
    await sentinel.release();return;
   }
   this.sentinel=sentinel;
   sentinel.addEventListener('release',()=>{
    if(this.sentinel!==sentinel)return;
    this.sentinel=null;this.onState(this.active&&this.enabled?'released':'off');
   },{once:true});
   this.onState(sentinel.released?'released':'active');
   if(sentinel.released)this.sentinel=null;
  }catch{if(epoch===this.epoch&&this.active&&this.enabled)this.onState('unavailable');}
  finally{
   if(this.pending===pending)this.pending=null;
   // A later call may have started while an old request was pending.
   if(epoch!==this.epoch&&this.active&&this.enabled)this.acquire();
  }
 }
 release(){
  ++this.epoch;
  const sentinel=this.sentinel;this.sentinel=null;
  if(sentinel)Promise.resolve(sentinel.release()).catch(()=>{});
  this.onState('off');
 }
 dispose(){this.active=false;this.release();this.document?.removeEventListener('visibilitychange',this.visible);}
}
