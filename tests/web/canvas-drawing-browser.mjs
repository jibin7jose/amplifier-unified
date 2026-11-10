import {showFixtureConversation} from './browser-chat-pages.mjs';
import {prepareCanvasReaderInput,recordCanvasInputFailure} from './canvas-reader-input.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {drawingSurface} from './fixtures/drawing-surface.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page,failure;
const outputDir=process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/canvas-proof';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timed out')),20000);fixture.once('exit',()=>reject(Error('Fixture exited')));fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data.url)}}catch{}}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1500,height:1100},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(action,args={})=>page.evaluate(([action,args])=>window.amplifier.dispatch(action,args),[action,args]);
 await action('session.create',{});await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:650}});
 const draft='Keep my drawing conversation draft';await page.getByRole('textbox',{name:'Message Amplifier'}).fill(draft);
 const row=(await action('canvas.apps.create',drawingSurface())).result,id=row.id;
 const inspect=()=>action('canvas.apps.inspect',{id}).then(r=>r.result),cas=async()=>{const r=await inspect();return {id,expectedRevision:r.app.revision,expectedStateRevision:r.app.stateRevision}};
 const frame=()=>page.locator('[data-canvas-view="primary"]').frameLocator('iframe').frameLocator('iframe');
 const inner=()=>page.frames().find(f=>f.url().includes('/document'));
 await frame().getByRole('heading',{name:'Shared sketch'}).waitFor();const mount=await inner().evaluate(()=>window.fixtureMount);
 // This text is written only by the fixture's canvasApp.ready render callback,
 // after current.strokes/color exist. A static heading is not input readiness.
 await expect(frame().locator('#count')).toHaveText('0 saved strokes');
 await prepareCanvasReaderInput(page,{target:frame().getByLabel('Shared drawing'),label:'drawing-input',
  outputDir,expectCovered:true});
 assert.equal(await inner().evaluate(()=>window.fixtureMount),mount,'Close Details retains the drawing document');
 const draw=async()=>{const box=await frame().getByLabel('Shared drawing').boundingBox();await page.mouse.move(box.x+30,box.y+40);await page.mouse.down();await page.mouse.move(box.x+180,box.y+110,{steps:8});await page.mouse.up()};
 await draw();await expect.poll(async()=>(await inspect()).app.state.strokes.length).toBe(1);
 await expect.poll(async()=>(await inspect()).views.some(v=>v.dirty)).toBe(false);
 const saved=(await inspect()).app.state.strokes;
 await frame().getByRole('button',{name:'Clock',exact:true}).click();await frame().getByLabel('Clock',{exact:true}).waitFor();
 await frame().getByRole('button',{name:'Sketch',exact:true}).click();await frame().getByLabel('Shared drawing').waitFor();
 await page.setViewportSize({width:1300,height:950});
 assert.deepEqual((await inspect()).app.state.strokes,saved);assert.equal(await inner().evaluate(()=>window.fixtureMount),mount);
 assert.ok(await inner().evaluate(()=>{const c=document.querySelector('#sketch');return c.width>1&&c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)}));
 await action('canvas.apps.event',{...await cas(),name:'setcolor',payload:{value:'#992244'}});await expect(frame().getByLabel('Pen color')).toHaveValue('#992244');
 // A rejected save retains both protection and the local sketch; Retry preserves it.
 let fail=true;await page.route('**/api/actions',async route=>{const body=route.request().postDataJSON();if(fail&&body?.action==='canvas.apps.event'&&body.args.name==='setstrokes'){fail=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Synthetic save conflict'})})}await route.continue()});
 await draw();await expect(frame().locator('#error')).toHaveText('Synthetic save conflict');
 await expect.poll(async()=>(await inspect()).views.some(v=>v.dirty)).toBe(true);
 await assert.rejects(action('canvas.apps.revise',{...await cas(),content:drawingSurface(true).content}),/Finish or cancel/);
 await frame().getByRole('button',{name:'Clock',exact:true}).click();await expect.poll(async()=>(await inspect()).views.some(v=>v.dirty)).toBe(true);
 await frame().getByRole('button',{name:'Sketch',exact:true}).click();await frame().getByRole('button',{name:'Save drawing',exact:true}).click();
 await expect.poll(async()=>(await inspect()).app.state.strokes.length).toBe(2);await expect.poll(async()=>(await inspect()).views.some(v=>v.dirty)).toBe(false);
 await page.unrouteAll({behavior:'wait'});
 await action('canvas.apps.revise',{...await cas(),content:drawingSurface(true).content});await frame().getByRole('heading',{name:'Sketch, refined'}).waitFor();
 await expect(frame().locator('#count')).toHaveText('2 saved strokes');await expect(page.getByRole('tab',{name:'Shared sketch',exact:true})).toHaveCount(1);
 await page.reload();await frame().getByRole('heading',{name:'Sketch, refined'}).waitFor();await expect(frame().locator('#count')).toHaveText('2 saved strokes');await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft(draft);
 assert.deepEqual(errors,[]);assert.ok((await inspect()).views.every(v=>v.renderStatus?.status!=='error'));
 await mkdir(root+'output/canvas-proof',{recursive:true});await page.screenshot({path:root+'output/canvas-proof/drawing.png',fullPage:true});await writeFile(root+'output/canvas-proof/drawing.json',JSON.stringify({passed:true,checks:['strokes shared','agent control','tab switches','resize redraw','no frame replacement','failed save preserves input','unrelated event retains dirty','retry saves','same tab refinement','refresh','draft preserved','no render errors']},null,2));
 console.log('Drawing acceptance passed');
}catch(error){failure=await recordCanvasInputFailure(page,error,{outputDir,label:'drawing'});throw failure}
finally{try{await browser?.close()}catch(error){throw failure?new AggregateError([failure,error],'Drawing failure and browser cleanup',{cause:failure}):error}finally{fixture.kill('SIGTERM')}}
