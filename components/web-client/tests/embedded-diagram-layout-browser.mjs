import {showFixtureConversation} from './browser-chat-pages.mjs';
// Production UI and renderers; isolated synthetic host, no provider calls.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--canvas-versions'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,...(process.env.UNIFIED_BROWSER_SINGLE_PROCESS==='1'?{args:['--no-zygote','--single-process','--disable-gpu']}: {})});
 const page=await browser.newPage({viewport:{width:1450,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));await page.goto(url);await showFixtureConversation(page);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 await action('session.create');await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true,canvasWidth:640}});
 const mermaid='flowchart TD\n'+Array.from({length:35},(_,i)=>`N${i}[Step ${i}] --> N${i+1}[Step ${i+1}]`).join('\n');
 const dot='digraph { '+Array.from({length:35},(_,i)=>`N${i} -> N${i+1};`).join(' ')+' }';
 const content='## Tall diagrams\n\n```mermaid\n'+mermaid+'\n```\n\n```dot\n'+dot+'\n```\n\nEnd of document.';
 const artifact=(await action('canvas.show',{kind:'markdown',title:'Embedded diagrams',content})).result;
 const stages=page.locator('.a-diagram.embedded .a-diagram-stage');
 await expect(stages).toHaveCount(2);
 // The 640px pane presents Details over an inert reader. Dismiss that sheet
 // through its control before qualifying the reader's keyboard scrolling.
 await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
 const reader=page.locator('.a-canvas-reader');
 for(const width of [1450,390]){
  await page.setViewportSize({width,height:1000});await action('view.update',{patch:{canvasFocused:width===390}});
  await expect.poll(()=>reader.evaluate(el=>el.inert)).toBe(false);
  for(let i=0;i<2;i++){
   const stage=stages.nth(i);await stage.locator('img').waitFor();
   await expect.poll(()=>stage.locator('img').evaluate(img=>img.complete&&img.naturalHeight>0)).toBe(true);
   const bounds=await stage.evaluate(el=>({height:el.clientHeight,scroll:el.scrollHeight,width:el.clientWidth,scrollWidth:el.scrollWidth,touch:getComputedStyle(el).touchAction}));
   assert.ok(bounds.height<=650&&bounds.height>=160);assert.ok(bounds.scroll>bounds.height,'Tall content remains reachable inside the bounded stage');assert.ok(bounds.scrollWidth<=bounds.width+1);assert.equal(bounds.touch,'auto');
   await stage.evaluate(el=>el.scrollTop=0);await stage.focus();await expect(stage).toBeFocused();await stage.press('ArrowDown');await expect.poll(()=>stage.evaluate(el=>el.scrollTop)).toBeGreaterThan(0);
   await stage.press('Control+End');await stage.evaluate(el=>el.scrollTop=el.scrollHeight);
   assert.ok(await stage.evaluate(el=>el.scrollTop+el.clientHeight>=el.scrollHeight-1),'Bottom of diagram is reachable');
  }
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:`/tmp/embedded-diagram-${width}.png`});
 }
 const saved=(await action('canvas.versions.inspect',{id:artifact.id,version:1,includeSource:true})).result;
 assert.equal(saved.source.content,content,'Exploring a diagram never rewrites its source');
 await page.reload();await expect(stages).toHaveCount(2);
 await action('view.update',{patch:{canvasFocused:true,canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'mermaid',title:'Standalone controls',content:mermaid});
 await page.getByRole('button',{name:'Close canvas details',exact:true}).click();
 await expect.poll(()=>reader.evaluate(el=>el.inert)).toBe(false);
 const standalone=page.locator('.a-diagram-stage');await standalone.locator('img').waitFor();
 await standalone.focus();await expect(standalone).toBeFocused();await standalone.press('ArrowRight');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.view.panX)).toBe(30);
 await page.getByRole('button',{name:'Fit',exact:true}).click();
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().canvas.view.panX)).toBe(0);
 assert.deepEqual(errors,[]);
 console.log('Embedded diagrams passed: bounded tall Mermaid/DOT, keyboard scrolling, reachable bottom, mobile width, retained source/reload, standalone pan and Fit.');
}finally{await browser?.close();fixture.kill('SIGTERM')}
