import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';

const evidence=process.env.AMPLIFIER_TEST_ARTIFACTS||await mkdtemp(join(tmpdir(),'inline-artifacts-'));
await mkdir(evidence,{recursive:true});
const reservation=createServer();
await new Promise((resolve,reject)=>{reservation.once('error',reject);reservation.listen(0,'127.0.0.1',resolve)});
const port=reservation.address().port,base=`http://127.0.0.1:${port}`;
await new Promise((resolve,reject)=>reservation.close(error=>error?reject(error):resolve()));
const fixture=spawn(fixturePython(process.env.FIXTURE_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/inline_artifacts_server.py',import.meta.url)),String(port)],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}if(fixture.exitCode!==null)throw Error('Fixture exited before readiness');await new Promise(resolve=>setTimeout(resolve,100))}
let browser,page;const errors=[];
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
try{
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process']:[]});
 page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/login');await page.getByLabel('Username').fill('inline-fixture');await page.getByLabel('Password').fill('fixture-password');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Create an interactive example and two image concepts.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 // Sending is asynchronous: the initial idle state can still be visible after
 // the click. Wait for the fixture reply as well, otherwise its long transcript
 // can arrive after we reveal the preview and move the iframe offscreen.
 await page.waitForFunction(()=>{const state=window.amplifier.getState(),session=state.sessions.find(row=>row.id===state.selectedSessionId);return session?.status==='idle'&&session.messages?.some(row=>row.role==='assistant'&&row.text?.startsWith('## Ready'))});
 let releasePreview;
 const previewGate=new Promise(resolve=>{releasePreview=resolve});
 await page.route('**/api/canvas/views/inline-*/resource?*',async route=>{await previewGate;await route.continue()});
 const saved=await action('canvas.show',{kind:'html',presentation:'inline',title:'Interactive example',content:'<h1>Original saved version</h1><button onclick="this.textContent=\'Clicked inline\'">Try it</button><p id="boundary"></p><script>try{parent.document.title;document.getElementById("boundary").textContent="UNSAFE"}catch{document.getElementById("boundary").textContent="Parent isolated"}</script>'});
 const identity=saved.result.id;
 const card=page.locator('.a-inline-artifact').filter({has:page.locator('strong',{hasText:'Interactive example'})}).first();
 await card.scrollIntoViewIfNeeded();
 await card.locator('.a-inline-artifact-placeholder').waitFor();
 const pendingBounds=await card.boundingBox();
 releasePreview();
 await card.locator('iframe').waitFor();
 const loadedBounds=await card.boundingBox();
 assert.ok(Math.abs(loadedBounds.height-pendingBounds.height)<=1,
  `Preview must reserve its loaded height: ${pendingBounds.height} -> ${loadedBounds.height}`);
 await page.unroute('**/api/canvas/views/inline-*/resource?*');
 const frame=card.frameLocator('iframe');try{await frame.getByRole('button',{name:'Try it',exact:true}).click()}catch(error){
  await page.screenshot({path:join(evidence,'failure.png')});
  console.log('GEOMETRY',await card.evaluate(node=>[node,...node.querySelectorAll('iframe,.a-inline-artifact-body,.a-canvas-viewer')].map(n=>({tag:n.tagName,class:n.className,rect:n.getBoundingClientRect().toJSON(),display:getComputedStyle(n).display,visibility:getComputedStyle(n).visibility}))),await page.locator('.a-messages').evaluate(n=>({top:n.scrollTop,height:n.scrollHeight,client:n.clientHeight})));
  throw error;
 }
 await frame.getByRole('button',{name:'Clicked inline',exact:true}).waitFor();await frame.getByText('Parent isolated',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.open),false,'Inline interactions must not open Canvas');
 await action('canvas.versions.revise',{id:identity,expectedRevision:1,content:'<h1>New saved version</h1>'});
 await frame.getByRole('heading',{name:'Original saved version',exact:true}).waitFor();
 await action('canvas.select',{id:identity});
 const primary=await page.evaluate(()=>{const view=window.amplifier.getState().canvasWorkspace.views.find(view=>view.viewId==='primary');return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,view[key]]))});
 await action('canvas.views.dirty',{...primary,dirty:true});
 await card.getByRole('button',{name:'Open Interactive example in canvas',exact:true}).click();
 await page.getByText('Finish or cancel the primary viewer edit before leaving it. Use viewer recovery only to discard that edit.',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.selectedVersion??null),null,'A refused full-view action must not silently retarget');
 await action('canvas.views.dirty',{...primary,dirty:false});
 await card.getByRole('button',{name:'Open Interactive example in canvas',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByRole('heading',{name:'Original saved version',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.selectedVersion),1);
 await action('view.update',{patch:{canvasFocused:false}});await action('canvas.close');
 const png=await page.evaluate(()=>{const canvas=document.createElement('canvas');canvas.width=400;canvas.height=300;const context=canvas.getContext('2d');context.fillStyle='#197b94';context.fillRect(0,0,400,300);context.fillStyle='white';context.font='28px sans-serif';context.fillText('Saved image',30,150);return canvas.toDataURL('image/png')});
 await action('canvas.show',{kind:'image',title:'First concept',content:png,presentation:'inline'});await action('canvas.show',{kind:'image',title:'Second concept',content:png,presentation:'inline'});await action('canvas.close');
 const images=page.locator('.a-inline-gallery');await images.scrollIntoViewIfNeeded();
 await images.locator('.a-inline-image img').waitFor();assert.equal(await images.getAttribute('aria-label'),'Image gallery, 2 images');
 await images.getByRole('button',{name:'Image 2: Second concept',exact:true}).click();
 await images.getByRole('button',{name:'Open Second concept in canvas',exact:true}).last().waitFor();
 assert.ok(await images.locator('.a-inline-image img').evaluate(image=>image.complete&&image.naturalWidth===400));
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Please keep these instructions.');
 const messageCount=await page.evaluate(()=>{const state=window.amplifier.getState();return state.sessions.find(row=>row.id===state.selectedSessionId).messages.length});
 await images.getByRole('button',{name:'Use in a follow-up',exact:true}).click();
 await images.getByText('Image added to your draft. Add your instructions, then send.',{exact:true}).waitFor();
 assert.equal(await page.getByRole('textbox',{name:'Message Amplifier'}).innerText(),'Please keep these instructions.');
 assert.equal(await page.evaluate(()=>{const state=window.amplifier.getState();return state.sessions.find(row=>row.id===state.selectedSessionId).messages.length}),messageCount);
 const downloadEvent=page.waitForEvent('download');await images.getByRole('button',{name:'Download image',exact:true}).click();
 const download=await downloadEvent;assert.equal(download.suggestedFilename(),'canvas.png');
 assert.deepEqual(await readFile(await download.path()),Buffer.from(png.split(',')[1],'base64'));
 await images.locator('.a-inline-image').click();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.title==='Second concept');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.title),'Second concept');
 await action('view.update',{patch:{canvasFocused:false}});await action('canvas.close');
 await images.scrollIntoViewIfNeeded();await page.screenshot({path:join(evidence,'desktop.png')});
 await page.emulateMedia({reducedMotion:'reduce'});await page.setViewportSize({width:390,height:844});await images.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile shell overflow');
 assert.ok(await images.evaluate(node=>node.scrollWidth<=node.clientWidth),'Gallery overflow');
 await page.screenshot({path:join(evidence,'mobile.png')});
 await action('session.create');
 assert.equal(await page.locator('.a-inline-artifact').count(),0);
 assert.ok(!(await page.evaluate(()=>window.amplifier.getState().canvasWorkspace.views)).some(view=>view.viewId.startsWith('inline-')));
 assert.deepEqual(errors,[]);
 console.log('Inline artifacts passed: interactive isolated HTML, exact saved version in chat and full view, image gallery, binary download, image follow-up preserving unsent draft without sending, mobile containment, reduced motion, chat cleanup.');
}finally{await browser?.close();fixture.kill()}
