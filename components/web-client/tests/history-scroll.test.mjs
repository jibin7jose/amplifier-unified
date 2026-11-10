import test from 'node:test';
import assert from 'node:assert/strict';
import {followEarlierHistory,followLaterHistory} from '../src/history-scroll.js';

const input=(pane,type,values={})=>{
 const event=new Event(type);Object.assign(event,values);pane.dispatchEvent(event);
};
const wheel=pane=>input(pane,'wheel',{deltaY:100,deltaX:0});
const laterPane=()=>{
 const pane=new EventTarget(),document=new EventTarget(),window=new EventTarget(),frames=new Map();
 let frame=0;
 Object.assign(window,{requestAnimationFrame:fn=>{frames.set(++frame,fn);return frame},cancelAnimationFrame:id=>frames.delete(id)});
 Object.assign(pane,{scrollTop:0,scrollHeight:1000,clientHeight:300,clientWidth:300,offsetWidth:316,
  getBoundingClientRect:()=>({top:0,left:0,right:316,bottom:300})});
 document.defaultView=window;pane.ownerDocument=document;
 return {pane,document,window,frames,nextFrame:()=>{const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn())}};
};

test('older pages load on upward scrolling, once per pending request',async()=>{
 const {pane}=laterPane();pane.scrollTop=300;let calls=0,release;
 const cleanup=followEarlierHistory(pane,()=>true,()=>{calls++;return new Promise(resolve=>{release=resolve})});
 assert.equal(calls,0);
 pane.scrollTop=500;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,0);
 input(pane,'wheel',{deltaY:-100});pane.scrollTop=90;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
 pane.scrollTop=30;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
 release();await new Promise(resolve=>setTimeout(resolve,0));
 cleanup();pane.scrollTop=0;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
});

test('unavailable earlier history does not start requests',async()=>{
 const {pane}=laterPane();pane.scrollTop=300;let calls=0;
 const cleanup=followEarlierHistory(pane,()=>false,()=>calls++);
 input(pane,'wheel',{deltaY:-100});pane.scrollTop=0;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();
 assert.equal(calls,0);cleanup();
});

test('earlier history ignores restored positions, expired gestures and disposed requests',async()=>{
 const {pane,document,window,nextFrame,frames}=laterPane();pane.scrollTop=500;let calls=0;
 const cleanup=followEarlierHistory(pane,()=>true,()=>calls++);
 const move=async top=>{pane.scrollTop=top;input(pane,'scroll');await Promise.resolve()};
 await move(80);assert.equal(calls,0,'a restored reading position does not request history');
 input(pane,'wheel',{deltaY:-100});input(window,'resize');await move(70);assert.equal(calls,0);
 input(pane,'wheel',{deltaY:-100});input(document,'focusin');await move(60);assert.equal(calls,0);
 input(pane,'wheel',{deltaY:-100});nextFrame();await move(50);assert.equal(calls,0);
 input(pane,'wheel',{deltaY:100});await move(40);assert.equal(calls,0,'contrary input is not permission');
 input(pane,'keydown',{key:'PageUp'});await move(30);assert.equal(calls,1);
 await new Promise(resolve=>setImmediate(resolve));
 input(pane,'wheel',{deltaY:-100});pane.scrollTop=20;input(pane,'scroll');cleanup();
 await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1,'switching chats cancels a scheduled read');
 assert.equal(frames.size,0);
});

test('earlier touch and scrollbar gestures load in their actual direction',async()=>{
 const {pane}=laterPane();pane.scrollTop=100;let calls=0;
 const cleanup=followEarlierHistory(pane,()=>true,()=>calls++);
 const move=async top=>{pane.scrollTop=top;input(pane,'scroll');await new Promise(resolve=>setImmediate(resolve))};
 const touch=y=>({touches:[{identifier:1,clientY:y}]});
 input(pane,'touchstart',touch(100));input(pane,'touchmove',touch(150));await move(90);assert.equal(calls,1);
 input(pane,'touchmove',touch(120));await move(80);assert.equal(calls,1,'finger up is newer-history intent');
 input(pane,'pointerdown',{pointerId:2,pointerType:'mouse',button:0,clientX:310,clientY:1});
 await move(70);assert.equal(calls,2,'track above thumb loads earlier');
 input(pane,'pointerdown',{pointerId:3,pointerType:'mouse',button:0,clientX:310,clientY:50});
 input(pane,'pointermove',{pointerId:3,buttons:1,clientY:40});await move(60);assert.equal(calls,3);
 cleanup();
});

test('downward Recent follows user input near 120px, never mount, resize or recursive append',async()=>{
 const pane=new EventTarget();Object.assign(pane,{scrollTop:0,scrollHeight:1000,clientHeight:300});
 const window=new EventTarget();pane.ownerDocument={defaultView:window};
 let calls=0,release,available=true;
 const follow=followLaterHistory(pane,()=>available,()=>{calls++;return new Promise(resolve=>{release=resolve})});
 const move=async(top,user=true)=>{
  if(user)wheel(pane);
  pane.scrollTop=top;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();
 };
 assert.equal(calls,0);
 await move(600,false);assert.equal(calls,0,'programmatic movement is not intent');
 wheel(pane);window.dispatchEvent(new Event('resize'));
 await move(700,false);assert.equal(calls,0,'resize clears stale intent at the old boundary');
 await move(500);assert.equal(calls,0,'upward movement does not load');
 await move(579);assert.equal(calls,0,'121px is outside the threshold');
 await move(580);assert.equal(calls,1);
 await move(700);assert.equal(calls,1,'pending scrolls share one request');
 pane.scrollHeight=2200;release();await new Promise(resolve=>setTimeout(resolve,0));
 await move(1900,false);assert.equal(calls,1,'append/anchor adjustment cannot refill');
 wheel(pane);
 follow.suppress(()=>{pane.scrollTop=1850});
 await move(1900,false);assert.equal(calls,1,'explicit restore clears pending user intent');
 available=false;await move(1901);assert.equal(calls,1,'exhausted/hidden/inert/collapsed gate');
 follow.dispose();available=true;await move(1902);assert.equal(calls,1);
});

test('tall Recent has no automatic fill and keyboard scroll is one user gesture',async()=>{
 const pane=new EventTarget();Object.assign(pane,{scrollTop:0,scrollHeight:200,clientHeight:900});
 let calls=0;
 const follow=followLaterHistory(pane,()=>true,()=>calls++);
 pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,0);
 pane.scrollHeight=1500;
 const key=new Event('keydown');Object.assign(key,{key:'PageDown'});
 pane.dispatchEvent(key);pane.scrollTop=600;pane.dispatchEvent(new Event('scroll'));
 await new Promise(resolve=>setTimeout(resolve,0));assert.equal(calls,1);
 pane.scrollTop=601;pane.dispatchEvent(new Event('scroll'));await Promise.resolve();assert.equal(calls,1);
 follow.dispose();
});

test('unused or contrary intent cannot authorize a later focus/anchor scroll',async()=>{
 const {pane,document,window,nextFrame,frames}=laterPane();let calls=0;
 const follow=followLaterHistory(pane,()=>true,()=>calls++);
 const move=async top=>{pane.scrollTop=top;input(pane,'scroll');await Promise.resolve()};
 for(const values of [{deltaY:-100},{deltaY:0,deltaX:100},{deltaY:100,ctrlKey:true}]){
  await move(0);input(pane,'wheel',values);await move(650);assert.equal(calls,0);
 }
 await move(0);input(pane,'wheel',{deltaY:-100});input(document,'focusin');
 await move(650);assert.equal(calls,0,'upward at top followed by focus restoration stays passive');
 await move(700);wheel(pane);input(document,'focusin');await move(701);
 assert.equal(calls,0,'wheel at bottom with no scroll cannot survive unrelated focus');
 for(const type of ['focusout','pointerdown','click','keydown','touchstart']){
  wheel(pane);input(document,type);await move(pane.scrollTop+1);assert.equal(calls,0,type);
 }
 for(const values of [{key:'ArrowUp'},{key:'End',ctrlKey:true},{key:'PageDown',altKey:true},{key:' ',shiftKey:true}]){
  wheel(pane);input(pane,'keydown',values);await move(pane.scrollTop+1);assert.equal(calls,0);
 }
 wheel(pane);nextFrame();await move(pane.scrollTop+1);assert.equal(calls,0,'unused intent expires at the input frame');
 wheel(pane);input(window,'blur');await move(pane.scrollTop+1);assert.equal(calls,0);
 wheel(pane);await move(pane.scrollTop+1);assert.equal(calls,1,'active downward intent loads once');
 follow.dispose();assert.equal(frames.size,0);
});

test('touch and scrollbar input must establish direction; disposal removes every listener',async()=>{
 const {pane,document,window,frames}=laterPane(),listeners=[];
 for(const target of [pane,document,window]){
  const add=target.addEventListener.bind(target),remove=target.removeEventListener.bind(target);
  target.addEventListener=(type,fn,options)=>{listeners.push({target,type,fn});add(type,fn,options)};
  target.removeEventListener=(type,fn,options)=>{
   const index=listeners.findIndex(row=>row.target===target&&row.type===type&&row.fn===fn);
   assert.notEqual(index,-1);listeners.splice(index,1);remove(type,fn,options);
  };
 }
 let calls=0;
 const follow=followLaterHistory(pane,()=>true,()=>calls++);
 const move=async top=>{pane.scrollTop=top;input(pane,'scroll');await Promise.resolve()};
 const touch=y=>({touches:[{identifier:1,clientY:y}]});
 input(pane,'touchmove',touch(100));await move(610);assert.equal(calls,0,'orphan move is not direction');
 input(pane,'touchstart',touch(100));input(pane,'touchmove',touch(150));await move(620);
 assert.equal(calls,0,'finger down is upward history');
 input(pane,'touchend');input(pane,'touchstart',touch(150));
 input(document,'pointercancel');input(pane,'touchmove',touch(100));
 await move(630);assert.equal(calls,1,'pointer cancellation must not erase ongoing touch direction');
 await new Promise(resolve=>setImmediate(resolve));
 input(pane,'pointerdown',{pointerId:2,pointerType:'mouse',button:0,clientX:310,clientY:275});
 input(pane,'pointermove',{pointerId:2,pointerType:'mouse',buttons:1,clientX:310,clientY:260});
 await move(640);assert.equal(calls,1,'upward thumb movement is not down intent');
 input(pane,'pointermove',{pointerId:2,pointerType:'mouse',buttons:1,clientX:310,clientY:290});
 await move(650);assert.equal(calls,2);
 await new Promise(resolve=>setImmediate(resolve));
 input(pane,'pointerup');input(pane,'pointerdown',{pointerId:3,pointerType:'mouse',button:0,clientX:50,clientY:50});
 input(pane,'pointermove',{pointerId:3,pointerType:'mouse',buttons:1,clientX:50,clientY:100});
 await move(660);assert.equal(calls,2,'content dragging cannot authorize a later scroll');
 // A track click below the thumb is a real down gesture, not any pane click.
 await move(0);input(pane,'pointerdown',{pointerId:4,pointerType:'mouse',button:0,clientX:310,clientY:250});
 await move(670);assert.equal(calls,3);
 await new Promise(resolve=>setImmediate(resolve));
 wheel(pane);assert.equal(frames.size,1);follow.dispose();
 assert.equal(frames.size,0);assert.deepEqual(listeners,[]);
 input(pane,'touchstart',touch(150));input(pane,'touchmove',touch(100));await move(680);assert.equal(calls,3);
});


test('passive wheel observes compositor movement before the scroll event',async()=>{
 const {pane}=laterPane();pane.scrollTop=550;let calls=0;
 const follow=followLaterHistory(pane,()=>true,()=>calls++);
 // Chromium may expose the new offset while dispatching the wheel itself.
 pane.scrollTop=650;wheel(pane);input(pane,'scroll');
 await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1);
 input(pane,'scroll');await Promise.resolve();assert.equal(calls,1);
 follow.dispose();
});
