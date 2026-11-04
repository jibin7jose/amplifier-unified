// Built app, synthetic lifecycle, real host presentation. No live model/user data.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {chromium,expect as assertions} from '@playwright/test';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
const expect=assertions.configure({timeout:10000}),root=fileURLToPath(new URL('../../',import.meta.url));
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/night-drive';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--appearance-states'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Night Drive fixture'});
const report={status:'running',phase:'fixture',measurements:[],reconnects:[],screenshots:[],limits:{successScreenshots:8,failureScreenshots:1},platform:'Linux Chromium; native titlebar geometry is simulated, not macOS OS acceptance'};
let browser,page,failure;
const hash=value=>createHash('sha256').update(value).digest('hex');
try{
 await mkdir(out,{recursive:true});
 const css=await readFile(root+'examples/appearances/night-drive.amplifier.css','utf8');report.cssSha256=hash(css);
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},reducedMotion:'no-preference',colorScheme:'light'});
 // Forward actual authenticated WebSocket frames, holding only delivery for
 // the reconnect proof. Never fabricate a baseline or mutate browser state.
 const sockets=[];let holdFrames=false;
 await context.routeWebSocket('**/api/events/ws?*',socket=>{
  const server=socket.connectToServer(),entry={socket,server,frames:[],closed:false};sockets.push(entry);
  server.onMessage(message=>{
   if(holdFrames){
    assert.ok(entry.frames.length<128,'Bounded held state frames');
    entry.frames.push(message);
   }else socket.send(message);
  });
 });
 await context.addInitScript(()=>{
  const overlay=new EventTarget();let rect={x:80,y:0,width:1200,height:38};overlay.visible=false;overlay.getTitlebarAreaRect=()=>rect;
  Object.defineProperty(navigator,'windowControlsOverlay',{configurable:true,value:overlay});
  window.testTitlebar=(value,visible)=>{rect=value;overlay.visible=visible;overlay.dispatchEvent(new Event('geometrychange'))};
 });
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);
 const app=page.locator('#amp-one'),header=app.locator('[data-part=header]');
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const action=(name,args={})=>page.evaluate(async([name,args])=>{const result=await window.amplifier.dispatch(name,args);if(!result.accepted)throw Error(name+' refused');return result},[name,args]);
 const presentation=patch=>page.evaluate(async patch=>{
  const shell=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;
  const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:shell.revision,composition:{...shell.effectiveComposition,presentation:{...shell.effectiveComposition.presentation,...patch}}});
  if(!prepared.accepted)throw Error('Prepare refused');
  const receipt=await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:shell.revision,changeId:prepared.result.id});if(!receipt.accepted)throw Error('Apply refused');
 },patch);
 const status=async(id,value)=>{const response=await page.request.post(url+'/fixture/appearance-status',{data:{sessionId:id,status:value}});assert.ok(response.ok())};
 const shot=async name=>{assert.ok(report.screenshots.length<8);const bytes=await page.screenshot({path:out+'/'+name+'.png'});report.screenshots.push({name,sha256:hash(bytes)})};
 const scanner=()=>header.evaluate(el=>{const style=getComputedStyle(el,'::after'),box=el.getBoundingClientRect();return {animation:style.animationName,duration:style.animationDuration,position:style.backgroundPosition,image:style.backgroundImage,height:parseFloat(style.height),top:box.bottom-parseFloat(style.bottom)-parseFloat(style.height)-2,bottom:box.bottom-parseFloat(style.bottom),display:style.display,content:style.content}});
 const measure=async label=>{const value=await scanner();report.measurements.push({label,...value});assert.ok(value.height>=4&&value.height<=6,label+': scanner has visible painted height');return value};
 // Park real pointer outside controls for unfiltered text/paint measurements.
 const contrast=async(locator,label)=>{
  await page.mouse.move(0,999);
  const value=await locator.evaluate(el=>{
   const rgba=text=>{const values=text.match(/[\d.]+/g).map(Number);return [...values.slice(0,3),values.length>3?values[3]:1]};
   const style=getComputedStyle(el);let background;
   for(let node=el;node;node=node.parentElement){const s=getComputedStyle(node);if(Number(s.opacity)!==1||s.filter!=='none'||s.mixBlendMode!=='normal')throw Error('Unqualified text paint');const bg=rgba(s.backgroundColor);if(bg[3]===1){background=bg;break}if(bg[3]!==0)throw Error('Translucent backing')}
   if(!background)throw Error('No opaque backing');
   const foreground=rgba(style.color);if(foreground[3]!==1)throw Error('Translucent ink');
   const luminance=values=>values.slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
   const a=luminance(foreground),b=luminance(background);return {ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05),foreground,background};
  });assert.ok(value.ratio>=4.5,label+': '+JSON.stringify(value));report.measurements.push({label,...value});
 };
 report.phase='global idle and off-screen lifecycle';
 await action('session.create');const selected=(await state()).selectedSessionId;
 await action('session.create');const other=(await state()).selectedSessionId;assert.notEqual(other,selected);
 await action('session.select',{id:selected});
 await action('theme.apply',{name:'Night Drive',css});
 await page.getByRole('textbox',{name:'Message Amplifier',exact:true}).fill('Retained Night Drive draft');
 await expect(app).toHaveAttribute('data-app-activity','idle');assert.equal((await measure('initial idle')).animation,'none');
 await status(other,'working');await expect(app).toHaveAttribute('data-app-activity','active');
 assert.equal((await state()).selectedSessionId,selected,'Other chat activity must not navigate');
 let active=await measure('off-screen work');assert.equal(active.animation,'night-drive-scanner');assert.equal(active.duration,'8s');
 const position=active.position;await expect.poll(async()=>(await scanner()).position).not.toBe(position);
 report.phase='reconnect open before authoritative baseline';
 for(const prior of ['working','idle']){
  await status(other,prior);
  const previousActivity=prior==='working'?'active':'idle',next=prior==='working'?'idle':'working';
  await expect(app).toHaveAttribute('data-app-activity',previousActivity);
  const previousSocket=sockets.at(-1);assert.ok(previousSocket,'Real state WebSocket is used');
  holdFrames=true;
  try{
   // Server state changes while the old browser snapshot is retained. The
   // replacement connection must not claim that old active OR idle is current.
   await status(other,next);
   previousSocket.closed=true;previousSocket.socket.close({code:1001});previousSocket.server.close({code:1001});
   await expect(app).toHaveAttribute('data-app-connection','disconnected');
   const count=sockets.length;
   await page.evaluate(()=>window.dispatchEvent(new Event('online')));
   await expect.poll(()=>sockets.length).toBeGreaterThan(count);
   const current=sockets.at(-1);
   await expect(app).toHaveAttribute('data-app-connection','connected');
   await expect.poll(()=>current.frames.some(message=>JSON.parse(String(message)).event==='state')).toBe(true);
   await expect(app).toHaveAttribute('data-app-activity','unknown');
   assert.equal((await state()).appActivity.active,prior==='working','Old snapshot remains retained until baseline delivery');
   const unready=await measure('reconnect open after '+previousActivity);
   assert.equal(unready.animation,'none');assert.ok(!unready.image.includes('rgb(189, 119, 112)'),'Unknown has no active hotspot');
   const held=current.frames.splice(0);holdFrames=false;
   for(const message of held)current.socket.send(message);
   const expected=next==='working'?'active':'idle';
   await expect(app).toHaveAttribute('data-app-activity',expected);
   assert.equal((await state()).appActivity.active,next==='working');
   report.reconnects.push({prior:previousActivity,transportOpen:true,baselineHeld:true,
    during:'unknown',animation:unready.animation,heldFrames:held.length,after:expected});
  }finally{holdFrames=false}
 }
 // Local attention remains a native notice; decoration never acknowledges it.
 const created=await action('question.create',{sessionId:other,prompt:'Synthetic optional design check',required:false,dependency:'Synthetic appearance attention check'});
 await expect(app).toHaveClass(/a-app-attention/);
 await action('question.cancel',{sessionId:other,id:created.result.id,expectedRevision:created.result.revision});
 for(const scheme of ['light','dark']){
  report.phase=scheme+' desktop';await presentation({scheme,decorations:true});await expect(app).toHaveAttribute('data-theme-scheme',scheme);
  await status(other,'idle');await expect(app).toHaveAttribute('data-app-activity','idle');
  assert.equal((await measure(scheme+' idle')).animation,'none');
  await shot('night-drive-'+scheme+'-idle');
  await status(other,'working');await expect(app).toHaveAttribute('data-app-activity','active');
  await contrast(page.getByRole('textbox',{name:'Message Amplifier',exact:true}),scheme+' composer');
  await contrast(header.locator('.a-work-heading').first(),scheme+' heading');
  await shot('night-drive-'+scheme+'-active');
 }
 report.phase='media preferences';
 await page.emulateMedia({reducedMotion:'reduce'});assert.equal((await measure('reduced motion active')).animation,'none');
 await page.emulateMedia({reducedMotion:'no-preference'});await expect.poll(async()=>(await scanner()).animation).toBe('night-drive-scanner');
 await presentation({decorations:false});await expect(app).toHaveAttribute('data-decorations','off');assert.equal((await scanner()).display,'none');assert.equal((await scanner()).animation,'none');
 await presentation({decorations:true});await page.emulateMedia({forcedColors:'active'});assert.equal((await scanner()).display,'none');
 await page.emulateMedia({forcedColors:'none'});
 report.phase='narrow and simulated native titlebar';
 for(const scheme of ['light','dark']){
  await page.setViewportSize({width:390,height:900});await presentation({scheme});await expect(app).toHaveAttribute('data-theme-scheme',scheme);
  await expect(page.getByRole('textbox',{name:'Message Amplifier',exact:true})).toBeVisible();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await shot('night-drive-'+scheme+'-390');
 }
 await page.setViewportSize({width:1440,height:1000});
 for(const [scheme,rect] of [['light',{x:80,y:0,width:1360,height:38}],['dark',{x:80,y:8,width:1280,height:52}]]){
  await presentation({scheme});await expect(app).toHaveAttribute('data-theme-scheme',scheme);
  await page.evaluate(rect=>window.testTitlebar(rect,true),rect);await expect(app).toHaveAttribute('data-window-controls-overlay','true');
  await expect.poll(()=>app.evaluate(el=>el.style.getPropertyValue('--wco-height'))).toBe(rect.height+'px');
  const value=await measure(scheme+' simulated native titlebar');
  assert.ok(value.top>=rect.y+rect.height+2,'Scanner below native titlebar: '+JSON.stringify({rect,value}));
  const controls=await header.locator('button').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>{const box=n.getBoundingClientRect();return {x:box.x,right:box.right,top:box.top,bottom:box.bottom}}));
  for(const box of controls){assert.ok(box.x>=rect.x-1&&box.right<=rect.x+rect.width+1);assert.ok(box.bottom<=rect.y+rect.height+1)}
  await shot('night-drive-'+scheme+'-pwa');
 }
 await status(other,'idle');await expect(app).toHaveAttribute('data-app-activity','idle');assert.equal((await scanner()).animation,'none');
 await expect(page.getByRole('textbox',{name:'Message Amplifier',exact:true})).toHaveDraft('Retained Night Drive draft');
 assert.equal((await state()).selectedSessionId,selected);
 const facts=await (await page.request.get(url+'/fixture')).json();assert.deepEqual(facts.sent,[]);assert.deepEqual(facts.started,[]);assert.deepEqual(errors,[]);
 report.status='passed';report.phase='complete';console.log('Night Drive passed: app-wide active/idle/attention, 8s scanner and real motion, native-geometry strip, light/dark/narrow, reduced motion/decorations/forced colors, contrast and retained draft; zero model calls.');
}catch(error){failure=error;report.status='failed';report.failure=String(error.stack||error).slice(0,6000);if(page)try{await page.screenshot({path:out+'/failure.png',timeout:2000})}catch{}throw error}
finally{
 try{await finishFixture({label:'Night Drive fixture',close:[['Night Drive browser',async()=>{await browser?.close();report.browserClosed=true}]],stop:async()=>{report.fixtureCleanup=await stop();return report.fixtureCleanup},originalError:failure})}
 catch(error){report.status='failed';report.cleanupError=String(error.stack||error).slice(0,2000);throw error}
 finally{await writeFile(out+'/report.json',JSON.stringify(report,null,2))}
}
