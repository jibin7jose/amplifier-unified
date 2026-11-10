import test from 'node:test';
import assert from 'node:assert/strict';
import {GifPlayer} from '../src/gif-player.js';

test('pause keeps current frame and remaining nonuniform delay; dispose closes every frame',async()=>{
 let now=0,callback,closed=0,decoderClosed=0;
 const frames=[80_000,140_000].map((duration,index)=>({duration,index,close(){closed++}}));
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:2}},decode:async({frameIndex})=>({image:frames[frameIndex]}),close(){decoderClosed++}};
 const drawn=[];
 const player=new GifPlayer({decoder,draw:frame=>drawn.push(frame.index),now:()=>now,
  schedule:fn=>{callback=fn;return 1},cancel:()=>{},loopCount:null});
 await player.ready();
 assert.deepEqual(drawn,[0]);
 player.play();now=30;player.pause();
 assert.equal(player.remaining,50);
 now=200;player.play();
 assert.equal(player.deadline,250);
 now=250;callback();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(drawn,[0,1]);
 now=300;player.pause();assert.equal(player.remaining,90);
 const pausedDraws=[...drawn];now=1000;player.play();
 assert.deepEqual(drawn,pausedDraws); // SAME nonzero canvas frame, no restart.
 assert.equal(player.deadline,1090); // remaining delay, not full 140 ms.
 player.dispose();assert.equal(closed,2);assert.equal(decoderClosed,1);
});

test('finite repeats and infinite loops preserve delays without predecoding the library',async()=>{
 for(const loopCount of [null,1,0]){
  let callback,calls=0,closed=0;
  const drawn=[],reports=[];
  const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:2}},
   decode:async({frameIndex})=>{calls++;return {image:{index:frameIndex,duration:frameIndex?140_000:80_000,close(){closed++}}}},close(){}};
  const player=new GifPlayer({decoder,draw:frame=>drawn.push(frame.index),loopCount,report:(...args)=>reports.push(args),
   schedule:fn=>{callback=fn;return 1},cancel:()=>{}});
  await player.ready();assert.equal(calls,1);player.play();
  for(let step=0;step<6&&player.playing;step++){callback();await new Promise(resolve=>setImmediate(resolve))}
  assert.deepEqual(drawn,loopCount===null?[0,1]:loopCount===1?[0,1,0,1]:[0,1,0,1,0,1,0]);
  assert.equal(player.playing,loopCount===0);
  if(loopCount!==0)assert.deepEqual(reports.at(-1),['ready','ended']);
  player.dispose();assert.equal(closed,calls);
 }
});

test('compositor draw failure closes current, queued and decoder resources',async()=>{
 let framesClosed=0,decoderClosed=0;
 const reports=[];
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:2}},
  decode:async()=>({image:{duration:80_000,close(){framesClosed++}}}),close(){decoderClosed++}};
 const player=new GifPlayer({decoder,draw:()=>{throw Error('compositor failure')},report:(...args)=>reports.push(args)});
 await player.ready();
 assert.equal(framesClosed,1);assert.equal(decoderClosed,1);
 assert.deepEqual(reports.at(-1),['error','paused','compositor failure']);
 player.play();player.dispose();assert.equal(decoderClosed,1);
});

test('late pending decode closes its result after disposal',async()=>{
 let resolve,closed=0,decoderClosed=0;
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:1}},
  decode:()=>new Promise(done=>{resolve=done}),close(){decoderClosed++}};
 const player=new GifPlayer({decoder,draw:()=>assert.fail('disposed draw')});
 const loading=player.ready();await Promise.resolve();player.dispose();
 resolve({image:{duration:80_000,close(){closed++}}});
 await loading;assert.equal(closed,1);assert.equal(decoderClosed,1);
});

test('pause during decode retains current and one next frame without drawing or a second decode',async()=>{
 let callback,resolve,calls=0,closed=0;
 const frame=index=>({index,duration:80_000,close(){closed++}});
 const decoder={tracks:{ready:Promise.resolve(),selectedTrack:{frameCount:2}},
  decode:({frameIndex})=>{calls++;return frameIndex?new Promise(done=>{resolve=done}):Promise.resolve({image:frame(0)})},close(){}};
 const drawn=[];
 const player=new GifPlayer({decoder,draw:f=>drawn.push(f.index),schedule:fn=>{callback=fn;return 1},cancel:()=>{}});
 await player.ready();player.play();callback();player.pause();
 resolve({image:frame(1)});await new Promise(done=>setImmediate(done));
 assert.deepEqual(drawn,[0]);assert.equal(calls,2);
 player.play();assert.deepEqual(drawn,[0,1]);assert.equal(closed,1);
 player.dispose();assert.equal(closed,2);
});