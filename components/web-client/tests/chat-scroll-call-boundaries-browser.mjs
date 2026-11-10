// Browser geometry and input boundaries; synthetic DOM, no app or model calls.
import {readFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const source=await readFile(process.env.AMPLIFIER_CHAT_SCROLL_SOURCE||new URL('../src/chat-scroll.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1200,height:900},deviceScaleFactor:1.25});
 await page.goto('about:blank');
 await page.setContent('<style>body{margin:0}#pane{height:500.5px;width:600px;overflow:auto;overflow-anchor:none}article{margin:0}</style><div id="pane"></div>');
 await page.evaluate(async source=>{
  window.createChatScroll=(await import('data:text/javascript;base64,'+btoa(source))).createChatScroll;
  const values=new Map();Object.defineProperty(window,'sessionStorage',{value:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)}});
  window.pane=document.querySelector('#pane');window.following={current:true};
 },source);
 const geometry=()=>page.evaluate(()=>({top:pane.scrollTop,height:pane.scrollHeight,viewport:pane.clientHeight,remaining:pane.scrollHeight-pane.clientHeight-pane.scrollTop}));
 const bottom=()=>expect.poll(async()=>Math.abs((await geometry()).remaining)).toBeLessThan(2);
 for(const zoom of [0.9,1.1,1.25]){
  await page.evaluate(zoom=>{
   window.controller?.dispose();pane.style.zoom=String(zoom);
   pane.innerHTML='<div class="work" style="height:70.5px">Expanded work</div><article data-message-id="speech" style="height:1800.25px">Spoken transcript</article>';
   window.controller=createChatScroll(pane,following);controller.select('scale-'+zoom);controller.setCall('call-'+zoom,'scale-'+zoom);
  },zoom);
  await bottom();const before=await geometry();
  // Work collapsing clamps the browser's scroll position before the next
  // observer frame. Fractional CSS coordinates must not look like scrollback.
  await page.evaluate(()=>{pane.querySelector('.work').remove();void pane.scrollTop;pane.dispatchEvent(new Event('scroll'))});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const collapsed=await geometry();
  await page.evaluate(()=>pane.querySelector('article').style.height='2200.75px');
  console.log(JSON.stringify({zoom,before,collapsed,afterGrowth:await geometry()}));
  await bottom();
  // Growing/collapsing work without input, and resizing the viewport, retain
  // the call's follow mode without requiring another spoken event to resume it.
  await page.evaluate(()=>{pane.insertAdjacentHTML('afterbegin','<div class="work" style="height:123.25px">Expanded work</div>');pane.style.height='430.25px'});
  await bottom();
  await page.evaluate(()=>{pane.querySelector('.work').remove();pane.style.height='500.5px'});
  await bottom();
  const box=await page.locator('#pane').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
  await page.mouse.wheel(0,300);await page.mouse.wheel(100,0);await page.mouse.wheel(0,0);await page.waitForTimeout(100);
  await page.evaluate(()=>pane.querySelector('article').style.height='2400.75px');await bottom();
  await page.mouse.wheel(0,-300);await expect.poll(async()=>(await geometry()).remaining).toBeGreaterThan(100);
  await page.waitForTimeout(100);const held=(await geometry()).top;
  await page.evaluate(()=>pane.querySelector('article').style.height='2600.75px');await page.waitForTimeout(100);
  assert.ok(Math.abs((await geometry()).top-held)<2,'Intentional scrollback remains paused at scale '+zoom);
 }
 await page.evaluate(()=>controller.dispose());
 console.log('PASS: fractional-scale call following survives Work collapse/growth, viewport resize, and wheel gestures that cannot move vertically, while real upward wheel pauses it.');
}finally{await browser.close()}
