// Notifications only: never schedule/pace media frames. Retain one latest
// snapshot while transport is busy, with at most 1 Hz same-state dispatch.
export class MediaReporter{
 constructor({generation,send,onState=()=>{},now=()=>performance.now(),
  schedule=(fn,delay)=>setTimeout(fn,delay),cancel=id=>clearTimeout(id)}){
  Object.assign(this,{generation,send,onState,now,schedule,cancel});
  this.sequence=0;this.pending=null;this.timer=null;this.busy=false;this.disposed=false;
  this.state=null;this.sentState=null;this.sentAt=-Infinity;
 }
 report(snapshot){
  if(this.disposed)return;
  const value={...snapshot,detail:(snapshot.detail||'').slice(0,500)};
  const state=JSON.stringify([value.renderState,value.playback,value.detail]);
  this.pending={value,state};
  // React status updates track transitions, not every GIF/native frame.
  if(state!==this.state){this.state=state;this.onState(value)}
  this.flush();
 }
 flush(){
  if(this.disposed||this.busy||!this.pending)return;
  const delay=this.pending.state===this.sentState?this.sentAt+1000-this.now():0;
  if(delay>0){
   if(this.timer===null)this.timer=this.schedule(()=>{this.timer=null;this.flush()},delay);
   return;
  }
  if(this.timer!==null){this.cancel(this.timer);this.timer=null}
  const {value,state}=this.pending;this.pending=null;
  // Reserve synchronously, before invoking even a reentrant/synchronous send.
  this.busy=true;this.sentState=state;this.sentAt=this.now();
  const payload={...value,generation:this.generation,sequence:++this.sequence};
  const settled=()=>{this.busy=false;this.flush()};
  try{
   const pending=this.send(payload);
   // Anchor after the synchronous dispatch attempt so payload construction
   // cannot make the next actual POST start less than a second after this one.
   this.sentAt=this.now();Promise.resolve(pending).then(settled,settled);
  }
  catch{settled()}
  // Failure consumes this notification. No retry, regeneration or reattach;
  // only a newer producer snapshot may be dispatched after settlement.
 }
 dispose(){
  if(this.disposed)return;this.disposed=true;this.pending=null;
  if(this.timer!==null)this.cancel(this.timer);this.timer=null;
 }
}