// Real browser geometry, synthetic history, no application state or model calls.
import {readFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const source=await readFile(new URL('../src/chat-scroll.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true});
try {
 const page=await browser.newPage();
 await page.goto('about:blank');
 await page.setContent('<style>#pane{height:500px;overflow:auto;overflow-anchor:none}article{margin:0;height:100px}.old{height:1800px}.reply{height:1600px}</style><div id="pane"></div>');
 await page.evaluate(async source=>{
  const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));
  // about:blank does not provide sessionStorage. Supply isolated tab storage.
  const values=new Map();Object.defineProperty(window,'sessionStorage',{value:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}});
  window.following={current:true};window.pane=document.querySelector('#pane');
  window.render=reply=>{pane.innerHTML='<article class="old" data-message-id="old">Old messages</article><article data-message-id="user" data-input-id="input">Latest request</article>'+(reply?'<article class="reply" data-message-id="reply">New reply</article>':'');};
  render(false);window.controller=createChatScroll(pane,following);controller.select('alpha');
 },source);
 const top=()=>page.evaluate(()=>pane.scrollTop);
 const atStart=()=>page.evaluate(()=>pane.querySelector('[data-message-id="user"]').getBoundingClientRect().bottom-pane.getBoundingClientRect().top);
 await expect.poll(top).toBeGreaterThan(1000);
 await page.evaluate(()=>controller.submitted('input'));
 // Leave while the request is near the bottom, return after a long reply arrives.
 await page.evaluate(()=>{controller.select('beta',false);render(false)});
 await page.evaluate(()=>{render(true);controller.select('alpha')});
 await expect.poll(atStart).toBeCloseTo(16,0);
 assert.equal(await page.evaluate(()=>following.current),true,'Returning should preserve the submitted reply anchor');
 // Reply growth must not pull the reader down the page.
 const anchored=await top();await page.evaluate(()=>pane.querySelector('.reply').style.height='2200px');
 await page.waitForTimeout(100);assert.equal(await top(),anchored);
 // A React commit may change an above-transcript notice after the observer
 // frame already ran. The layout effect must settle before that commit paints,
 // not one animation frame later. This covers insertion and removal while
 // following the submitted reply, then while deliberately reading scrollback.
 for(const reading of [false,true]){
  if(reading)await page.evaluate(()=>{pane.dispatchEvent(new WheelEvent('wheel',{deltaY:-400}));pane.scrollTop=1850;pane.dispatchEvent(new Event('scroll'))});
  const before=await atStart();
  for(const insert of [true,false]){
   const result=await page.evaluate(insert=>new Promise(resolve=>requestAnimationFrame(()=>{
    if(insert)pane.insertAdjacentHTML('afterbegin','<div class="work-notice" style="height:70px">Loading work details…</div>');
    else pane.querySelector('.work-notice').remove();
    controller.settleLayout();
    resolve({offset:pane.querySelector('[data-message-id="user"]').getBoundingClientRect().bottom-pane.getBoundingClientRect().top,following:following.current});
   })),insert);
   assert.ok(Math.abs(result.offset-before)<1,'Work notice must preserve the reading anchor before paint');
   assert.equal(result.following,!reading,'Layout settlement must preserve explicit scrollback intent');
  }
 }
 await page.evaluate(()=>controller.submitted('input'));await expect.poll(atStart).toBeCloseTo(16,0);
 // Browser-driven scrolling (including focusing an iframe control) is also
 // reading intent. It does not deliver wheel/pointer events to this document.
 await page.evaluate(()=>{
  const target=document.createElement('button');target.textContent='Preview control';
  target.style.marginTop='800px';pane.querySelector('.reply').append(target);
  target.scrollIntoView({block:'center'});
 });
 await expect.poll(()=>page.evaluate(()=>following.current)).toBe(false);
 const focused=await top();
 await page.evaluate(()=>{pane.querySelector('.reply').append(document.createTextNode('Preview ready'));controller.update()});
 await page.waitForTimeout(100);assert.equal(await top(),focused,'Preview updates must not undo browser focus scrolling');
 // Deliberate scrollback takes precedence, including after returning/reloading.
 await page.evaluate(()=>{pane.dispatchEvent(new WheelEvent('wheel',{deltaY:-400}));pane.scrollTop=500;pane.dispatchEvent(new Event('scroll'))});
 await page.evaluate(()=>{controller.select('beta',false);render(false)});
 await page.evaluate(()=>{render(true);controller.select('alpha')});
 await expect.poll(top).toBe(500);assert.equal(await page.evaluate(()=>following.current),false);
 // An earlier image/section growing must preserve the visible message offset.
 await page.evaluate(()=>{pane.dispatchEvent(new WheelEvent('wheel',{deltaY:1}));pane.scrollTop=1850;pane.dispatchEvent(new Event('scroll'))});
 const visible=await atStart();await page.evaluate(()=>pane.querySelector('.old').style.height='2000px');
 await expect.poll(atStart).toBeCloseTo(visible,0);
 await page.evaluate(()=>pane.querySelector('.old').style.height='1800px');
 await expect.poll(atStart).toBeCloseTo(visible,0);
 // No-scroll switch: flush the current intent even if no new scroll event fired.
 await page.evaluate(()=>controller.submitted('input'));await expect.poll(atStart).toBeCloseTo(16,0);
 await page.evaluate(()=>{controller.dispose();controller=null});
 await page.evaluate(async source=>{const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));controller=createChatScroll(pane,following);controller.select('alpha')},source);
 await expect.poll(atStart).toBeCloseTo(16,0);assert.equal(await page.evaluate(()=>following.current),true);
 // Jump-to-latest is explicit and remains a one-time jump.
 await page.evaluate(()=>controller.reveal());const latest=await top();await page.evaluate(()=>pane.querySelector('.reply').style.height='2400px');
 await page.waitForTimeout(100);assert.equal(await top(),latest);assert.equal(await page.evaluate(()=>following.current),false);
 // A saved anchor may fall outside the loaded history window on a later visit.
 await page.evaluate(()=>controller.submitted('input'));await expect.poll(atStart).toBeCloseTo(16,0);
 await page.evaluate(()=>{controller.select('beta',false);pane.innerHTML=''});
 await page.evaluate(()=>{pane.innerHTML='<article class="reply" data-message-id="newer">Newer history window</article>';controller.select('alpha')});
 await expect.poll(()=>page.evaluate(()=>pane.scrollHeight-pane.scrollTop-pane.clientHeight)).toBe(0);
 await expect.poll(()=>page.evaluate(()=>following.current)).toBe(false);
 // A just-submitted input can precede its DOM insertion. Do not treat this as
 // an obsolete restored anchor and stop following before the message arrives.
 await page.evaluate(()=>controller.submitted('delayed-input'));
 await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>following.current),true);
 await page.evaluate(()=>{pane.insertAdjacentHTML('beforeend','<article data-message-id="delayed" data-input-id="delayed-input">Sent</article><article class="reply">Response</article>')});
 await expect.poll(()=>page.evaluate(()=>pane.querySelector('[data-input-id="delayed-input"]').getBoundingClientRect().bottom-pane.getBoundingClientRect().top)).toBeCloseTo(16,0);
 // Reading a message outside the tail loads one bounded window. Failed
 // restoration keeps the saved anchor for a later visit, without a retry loop.
 await page.evaluate(async source=>{
  controller.dispose();
  sessionStorage.setItem('amplifier.chat-reading.v1',JSON.stringify({gamma:{messageId:'historical',offset:-30,top:600}}));
  pane.innerHTML='<article class="reply" data-message-id="tail">Tail</article>';
  const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));
  window.loads=0;window.finishLoad=null;window.loadCurrent=null;
  window.makeController=()=>createChatScroll(pane,following,()=>{},{loadAnchor:(sessionId,messageId,current)=>{loads++;loadCurrent=current;return new Promise(resolve=>{finishLoad=resolve})}});
  controller=makeController();controller.select('gamma');
 },source);
 await expect.poll(()=>page.evaluate(()=>loads)).toBe(1);
 // The API can finish before React commits the requested window. Keep the
 // restoration pending across frames until its message actually mounts.
 await page.evaluate(()=>finishLoad(true));
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await page.evaluate(()=>{pane.innerHTML='<article style="height:600px" data-message-id="before">Before</article><article class="reply" data-message-id="historical">History</article>'});
 await expect.poll(()=>page.evaluate(()=>pane.querySelector('[data-message-id="historical"]').getBoundingClientRect().top-pane.getBoundingClientRect().top)).toBe(-30);
 // Leave/return with only the tail, then fail the restoration request.
 await page.evaluate(()=>{controller.select('beta',false);pane.innerHTML='<article class="reply" data-message-id="tail">Tail</article>';controller.select('gamma')});
 await expect.poll(()=>page.evaluate(()=>loads)).toBe(2);
 await page.evaluate(()=>finishLoad());
 await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>loads),2);
 await page.evaluate(()=>controller.dispose());
 assert.equal(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.chat-reading.v1')).gamma.messageId),'historical');
 await page.evaluate(()=>{controller=makeController();controller.select('gamma')});
 await expect.poll(()=>page.evaluate(()=>loads)).toBe(3);
 // Deliberate navigation cancels restoration authority before a late result.
 await page.evaluate(()=>controller.reveal());assert.equal(await page.evaluate(()=>loadCurrent()),false);
 await page.evaluate(()=>finishLoad());
 await page.evaluate(async source=>{
  controller.dispose();
  pane.innerHTML='<div class="notice" style="height:70px">Preparing work</div><article data-message-id="spoken" style="height:1800px">Spoken transcript</article>';
  const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));
  controller=createChatScroll(pane,following);controller.select('call');controller.setCall('live-call','call');
 },source);
 const remaining=()=>page.evaluate(()=>pane.scrollHeight-pane.scrollTop-pane.clientHeight);
 await expect.poll(remaining).toBe(0);
 // Removing a completed preparation notice clamps scrollTop in the browser.
 // Deliver its scroll before the layout observer's frame, the failing order
 // observed in the production voice view. It must not pause voice following.
 await page.evaluate(()=>{pane.querySelector('.notice').remove();void pane.scrollTop;pane.dispatchEvent(new Event('scroll'))});
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await page.evaluate(()=>pane.querySelector('article').style.height='2200px');
 await expect.poll(remaining).toBe(0);
 // Actual scrollback still pauses the call across later growth and resize.
 await page.evaluate(()=>{pane.dispatchEvent(new WheelEvent('wheel',{deltaY:-400}));pane.scrollTop=500;pane.dispatchEvent(new Event('scroll'))});
 await page.evaluate(()=>pane.querySelector('article').style.height='2600px');
 await page.waitForTimeout(100);assert.equal(await top(),500);
 await page.evaluate(()=>controller.reveal());await expect.poll(remaining).toBe(0);
 await page.evaluate(()=>{const target=document.createElement('button');target.textContent='Call transcript control';target.style.marginTop='600px';pane.querySelector('article').append(target);target.scrollIntoView({block:'center'})});
 await expect.poll(remaining).toBeGreaterThan(500);
 const callFocused=await top();
 await page.evaluate(()=>pane.querySelector('article').style.height='3000px');
 await page.waitForTimeout(100);assert.equal(await top(),callFocused,'Browser focus scrolling still pauses a live call');
 await page.evaluate(()=>controller.reveal());await expect.poll(remaining).toBe(0);
 await page.evaluate(()=>{pane.style.height='350px';pane.querySelector('article').style.height='3400px'});
 await expect.poll(remaining).toBe(0);
 await page.evaluate(()=>controller.dispose());console.log('PASS: return during reply, top alignment, growth, manual scrollback, controller reload, explicit latest jump, missing history anchor.');
 // Reproduce the measured submitted-input layout, including the browser's
 // native clamp scroll BEFORE the queued controller frame. No application
 // state or synthetic scroll event is used; only controller frame order is
 // held in this isolated fixture. Keep all deliberate-reading controls first
 // so an old-controller failure still reports their outcomes.
 const clampResults=[];
 for(const mode of ['wheel','touch','key','external-focus','not-submitted','below-limit','submitted-clamp']){
  const clampPage=await browser.newPage({viewport:{width:1280,height:900}});
  try{
   await clampPage.goto('about:blank');
   await clampPage.setContent('<style>body{margin:0}#pane{position:absolute;top:52px;width:1040px;height:679px;overflow:auto;overflow-anchor:none}#old{height:9986px;position:relative}#old button{position:absolute;top:8000px}#user{height:322px}#tail{height:56px}</style><div id="pane"><div id="old" data-message-id="old"><button>Earlier control</button></div><div id="user" data-message-id="input" data-input-id="input">Submitted message</div><div id="tail"></div></div>');
   const result=await clampPage.evaluate(async({source,mode})=>{
    const {createChatScroll}=await import('data:text/javascript;base64,'+btoa(source));
    Object.defineProperty(window,'sessionStorage',{value:{getItem:()=>null,setItem:()=>{}}});
    const pane=document.querySelector('#pane'),following={current:true},frames=new Map();
    const nativeFrame=window.requestAnimationFrame,nativeCancel=window.cancelAnimationFrame;
    let frame=0,controller;
    window.requestAnimationFrame=callback=>{frames.set(++frame,callback);return frame};
    window.cancelAnimationFrame=id=>frames.delete(id);
    const nextFrame=()=>{const pending=[...frames.values()];frames.clear();for(const callback of pending)callback(performance.now())};
    const geometry=()=>({
     top:pane.scrollTop,height:pane.scrollHeight,client:pane.clientHeight,
     max:pane.scrollHeight-pane.clientHeight,following:following.current,
     messageId:document.querySelector('#user').dataset.messageId,
     inputId:document.querySelector('#user').dataset.inputId,
     offset:document.querySelector('#user').getBoundingClientRect().bottom-pane.getBoundingClientRect().top
    });
    // Listener is installed after the controller's scroll listener. Its receipt
    // includes the supplied following ref AFTER the actual handler returned.
    const nativeScroll=change=>new Promise((resolve,reject)=>{
     const cleanup=()=>{clearTimeout(timer);pane.removeEventListener('scroll',observe)};
     const observe=event=>{if(!event.isTrusted)return;cleanup();resolve({trusted:event.isTrusted,...geometry()})};
     const timer=setTimeout(()=>{cleanup();reject(Error('No native scroll at controlled layout boundary'))},2000);
     pane.addEventListener('scroll',observe);
     try{change();void pane.scrollTop}catch(error){cleanup();reject(error)}
    });
    try{
     controller=createChatScroll(pane,following);controller.select('submitted-clamp');
     controller.submitted('input');
     const initial=await nativeScroll(nextFrame);
     if(mode==='wheel')pane.dispatchEvent(new WheelEvent('wheel',{deltaY:-100}));
     if(mode==='touch')pane.dispatchEvent(new Event('touchmove'));
     if(mode==='key')pane.dispatchEvent(new KeyboardEvent('keydown',{key:'PageUp'}));
     if(mode==='not-submitted')controller.reveal();
     const boundary=await nativeScroll(()=>{
      if(mode==='external-focus')document.querySelector('#old button').focus();
      else{
       pane.style.height='726px';void pane.scrollTop;
       if(mode==='below-limit')pane.scrollTop=9600;
      }
     });
     // Exact same input, new canonical message ID; range grows by 65+84=149.
     const canonical=document.querySelector('#user').cloneNode(true);canonical.dataset.messageId='canonical';
     document.querySelector('#user').replaceWith(canonical);
     document.querySelector('#tail').style.height='121px';pane.style.height='642px';
     controller.update();nextFrame();
     const replaced=geometry();
     document.querySelector('#tail').style.height='1600px';controller.update();nextFrame();
     return {mode,initial,boundary,replaced,grown:geometry()};
    }finally{
     controller?.dispose();frames.clear();
     window.requestAnimationFrame=nativeFrame;window.cancelAnimationFrame=nativeCancel;
    }
   },{source,mode});
   clampResults.push(result);console.log('SUBMITTED-CLAMP-CONTROL',JSON.stringify(result));
   assert.equal(result.initial.trusted,true);assert.equal(result.initial.following,true);
   assert.deepEqual([result.initial.top,result.initial.height,result.initial.client,result.initial.max],[9685,10364,679,9685]);
   assert.equal(result.boundary.trusted,true,'The browser, not dispatchEvent, must deliver the changed position');
   if(mode==='external-focus')assert.ok(result.boundary.top<9685,'Focus must actually scroll the pane');
   else assert.deepEqual([result.boundary.top,result.boundary.height,result.boundary.client,result.boundary.max],
    [mode==='below-limit'?9600:9638,10364,726,9638]);
   const following=mode==='submitted-clamp';
   assert.equal(result.boundary.following,following,'Only an automatic clamp with active submitted intent may preserve following');
   assert.equal(result.replaced.following,following);
   assert.equal(result.replaced.messageId,'canonical');assert.equal(result.replaced.inputId,'input');
   assert.deepEqual([result.replaced.height,result.replaced.client,result.replaced.max],[10429,642,9787]);
   if(following){
    assert.equal(result.replaced.top,9787,'Canonical replacement must reach the available bottom');
    assert.ok(Math.abs(result.grown.offset-16)<1,'Later reply growth must retain the submitted anchor');
   }else{
    assert.equal(result.replaced.top,result.boundary.top,'Layout must not resume deliberately stopped following');
    assert.equal(result.grown.top,result.replaced.top,'Later growth must preserve reading position');
   }
  }finally{await clampPage.close()}
 }
 assert.equal(clampResults.length,7);
 console.log('PASS: native submitted clamp, canonical replacement, growth, and six reading-intent controls.');
}finally{await browser.close()}
