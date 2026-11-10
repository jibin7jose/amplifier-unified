// ImageDecoder supplies composited frames, including transparency/disposal.
// This owner retains at most current + next and one pending decode.
let activeDecodes=0;
const decodeWaiters=[];
async function boundedDecode(decoder,args,live){
 if(activeDecodes<2)activeDecodes++;
 else await new Promise(resolve=>decodeWaiters.push(resolve));
 try{return live()?await decoder.decode(args):null}
 finally{const next=decodeWaiters.shift();if(next)next();else activeDecodes--}
}
export class GifPlayer{
 constructor({decoder,draw,report=()=>{},loopCount=null,now=()=>performance.now(),
  schedule=(fn,delay)=>setTimeout(fn,delay),cancel=id=>clearTimeout(id)}){
  Object.assign(this,{decoder,draw,report,loopCount,now,schedule,cancel});
  this.current=null;this.next=null;this.pending=null;this.timer=null;
  this.index=0;this.cycles=0;this.remaining=0;this.playing=false;this.disposed=false;
 }
 async ready(){
  try{
   await this.decoder.tracks.ready;if(this.disposed)return;
   this.count=this.decoder.tracks.selectedTrack?.frameCount;
   if(!Number.isInteger(this.count)||this.count<1||this.count>500)throw new Error('Unsupported GIF frame count.');
   await this.decode(0,true);
  }catch(error){if(!this.disposed)this.fail(error)}
 }
 async decode(index,initial=false){
  if(this.disposed||this.pending)return;
  const pending=boundedDecode(this.decoder,{frameIndex:index,completeFramesOnly:true},()=>!this.disposed);this.pending=pending;
  try{
   const result=await pending;
   if(!result)return;
   if(this.disposed){result.image.close();return}
   this.next={frame:result.image,index};
   if(initial||this.playing)this.promote();
  }catch(error){if(!this.disposed)this.fail(error)}
  finally{if(this.pending===pending)this.pending=null}
 }
 promote(){
  if(!this.next||this.disposed)return;
  const next=this.next;this.next=null;
  this.current?.close();this.current=next.frame;this.index=next.index;
  this.remaining=Math.max(10,(this.current.duration||100_000)/1000);
  this.draw(this.current);this.report('ready',this.playing?'playing':'paused');
  if(this.playing)this.arm();
 }
 arm(){
  if(!this.playing||this.disposed||this.timer!==null)return;
  this.deadline=this.now()+this.remaining;
  this.timer=this.schedule(()=>{this.timer=null;this.remaining=0;this.advance()},this.remaining);
 }
 advance(){
  if(!this.playing||this.disposed)return;
  if(this.next){this.promote();return}
  if(this.pending)return;
  let next=this.index+1;
  if(next===this.count){
   if(this.loopCount===null||this.loopCount!==0&&this.cycles>=this.loopCount){
    this.playing=false;this.report('ready','ended');return;
   }
   this.cycles++;next=0;
  }
  void this.decode(next);
 }
 play(){
  if(this.disposed||!this.current||this.playing)return;
  this.playing=true;this.report('ready','playing');
  if(this.next)this.promote();else if(this.remaining>0)this.arm();else this.advance();
 }
 pause(){
  if(this.disposed)return;
  if(this.timer!==null){
   this.remaining=Math.max(0,this.deadline-this.now());this.cancel(this.timer);this.timer=null;
  }
  this.playing=false;if(this.current)this.report('ready','paused');
 }
 fail(error){this.report('error','paused',String(error.message||error));this.dispose()}
 dispose(){
  if(this.disposed)return;this.disposed=true;this.playing=false;
  if(this.timer!==null)this.cancel(this.timer);this.timer=null;
  this.current?.close();this.current=null;this.next?.frame.close();this.next=null;
  this.decoder.close();
 }
}