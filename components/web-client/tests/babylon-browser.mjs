import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifacts required');
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--canvas-download-cookie'],{stdio:['ignore','pipe','inherit']});
let browser,page;const errors=[],responses=[];
const boot=new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
const content=`<style>html,body{margin:0;height:100%;overflow:hidden}canvas{width:100%;height:85%;touch-action:none}button{padding:8px;margin:8px}</style><h2>Orbit studio</h2><button id="color">Change planet color</button><span id="status">Blue planet</span><canvas id="scene" aria-label="Interactive 3D planet"></canvas><script>
if(!BABYLON.Engine.IsSupported)throw Error('WebGL is unavailable');
const canvas=document.getElementById('scene'),engine=new BABYLON.Engine(canvas,true),scene=new BABYLON.Scene(engine);
scene.clearColor=new BABYLON.Color4(.05,.07,.15,1);
const camera=new BABYLON.ArcRotateCamera('camera',Math.PI/3,Math.PI/3,5,BABYLON.Vector3.Zero(),scene);camera.attachControl(canvas,true);
new BABYLON.HemisphericLight('light',new BABYLON.Vector3(0,1,0),scene);
const planet=BABYLON.MeshBuilder.CreateSphere('planet',{diameter:2,segments:32},scene),material=new BABYLON.StandardMaterial('planet-material',scene);material.diffuseColor=new BABYLON.Color3(.2,.5,1);planet.material=material;
window.scene=scene;window.engine=engine;window.framesRendered=0;
engine.runRenderLoop(()=>{scene.render();window.framesRendered++;});addEventListener('resize',()=>engine.resize());
document.getElementById('color').onclick=()=>{material.diffuseColor=new BABYLON.Color3(1,.3,.4);document.getElementById('status').textContent='Rose planet'};
</script>`;
try{
 const url=await boot;browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader','--use-angle=swiftshader']});page=await browser.newPage({acceptDownloads:true,viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(new URL(r.url()).pathname.includes('/download'))responses.push({path:new URL(r.url()).pathname,status:r.status(),type:r.headers()['content-type']})});
 const cookie=await page.request.post(url+'/fixture/browser-session');assert.equal(cookie.status(),200);
 await page.goto(url);await page.waitForSelector('#amp-one');await page.evaluate(()=>window.amplifier.dispatch('session.create'));
 await page.evaluate(content=>window.amplifier.dispatch('canvas.show',{kind:'babylon',title:'Orbit studio',content}),content);
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('heading',{name:'Orbit studio'}).waitFor();
 await frame.locator('#scene').evaluate(async()=>{for(let i=0;i<100&&!(window.framesRendered>=5);i++)await new Promise(r=>setTimeout(r,100));if(!(window.framesRendered>=5))throw Error('No rendered frames')});
 assert.equal(await frame.locator('#scene').evaluate(()=>window.scene.meshes.length),1);
 const angle=await frame.locator('#scene').evaluate(()=>scene.activeCamera.alpha);const box=await frame.locator('#scene').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+80,box.y+box.height/2,{steps:8});await page.mouse.up();assert.notEqual(await frame.locator('#scene').evaluate(()=>scene.activeCamera.alpha),angle);
 await page.waitForFunction(()=>window.amplifier.getState().canvas.document?.controls?.some(c=>c.label==='Change planet color'));
 await page.evaluate(()=>{const c=window.amplifier.getState().canvas;return window.amplifier.dispatch('canvas.interact',{id:c.id,controlId:c.document.controls.find(c=>c.label==='Change planet color').id,event:'click'})});await frame.getByText('Rose planet',{exact:true}).waitFor();
 const options=page.getByRole('button',{name:'Canvas options',exact:true});if(await options.getAttribute('aria-expanded')!=='true')await options.click();
 const [result]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download canvas source'}).click()]);assert.equal(result.suggestedFilename(),'Orbit studio.html');const downloadFailure=await result.failure();await writeFile(out+'/download.json',JSON.stringify({url:result.url(),filename:result.suggestedFilename(),failure:downloadFailure,responses},null,2));assert.equal(downloadFailure,null,'Public download must complete');const exported=await readFile(await result.path(),'utf8');assert.ok(exported.includes('data-amplifier-library="babylon"')&&exported.includes('Orbit studio'));
 await page.screenshot({path:out+'/babylon-scene.png'});await page.reload();await page.frameLocator('.a-canvas-html').getByText('Blue planet',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Close canvas panel'}).click();
 const closeNav=page.getByRole('button',{name:'Close navigation',exact:true});if(await closeNav.isVisible())await closeNav.click();const toggle=page.getByRole('button',{name:'Open navigation',exact:true});await toggle.hover();await toggle.click();await page.waitForFunction(()=>window.amplifier.getState().view.navPinned===true);assert.equal(await page.getByRole('button',{name:'Close navigation',exact:true}).count(),1);await page.mouse.move(700,30);assert.equal(await page.locator('.a-nav-slot').evaluate(el=>el.classList.contains('is-pinned')),true);await page.getByRole('button',{name:'Close navigation',exact:true}).click();await page.waitForFunction(()=>window.amplifier.getState().view.navPinned===false);
 assert.deepEqual(errors,[]);console.log('Babylon renders WebGL frames, supports camera interaction and agent-operated DOM controls, exports its bundled runtime, persists across reload; single sidebar icon pins and unpins.');
}catch(e){console.log('Errors',errors,page?await page.evaluate(()=>window.amplifier?.getState()?.canvas?.renderReports):null);throw e}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=new Promise(resolve=>fixture.once('exit',resolve));fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);await done.finally(()=>clearTimeout(timer))}}}
