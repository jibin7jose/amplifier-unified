import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'ignore'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const patch=values=>action('view.update',{patch:values});
const box=selector=>page.locator(selector).boundingBox();
const settled=()=>page.waitForTimeout(120);
const toolbarsMatch=visible=>page.locator('.a-canvas-toolbar').evaluateAll((nodes,visible)=>nodes.length>0&&nodes.every(node=>!!node.getClientRects().length===visible),visible);
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 await action('canvas.show',{kind:'html',title:'A canvas that keeps its place',content:'<!doctype html><html><head><style>body{margin:0;background:linear-gradient(135deg,#eef2ff,#e6f9f5);font:18px system-ui;min-height:100vh}main{padding:40px}button{font:inherit;padding:12px 20px;border:0;border-radius:12px;background:#6261db;color:white}</style></head><body><main><h1>Room to explore</h1><p>This interactive view stays alive as you resize and focus it.</p><button onclick="this.textContent=String(Number(this.textContent)+1)">0</button></main></body></html>'});
 await patch({canvasControlsPinned:false,canvasControlsExpanded:false});await page.mouse.move(100,30);await settled();
 const beforeHover=await box('.a-canvas-html');await page.screenshot({path:'/tmp/amplifier-canvas-before-hover.png'});
 assert.equal(await page.getByRole('button',{name:'Canvas controls',exact:true}).count(),0);
 // Hover stays engaged while moving from the header into viewer-specific tools.
 await page.locator('.a-canvas-head').hover();await page.getByRole('button',{name:'Copy canvas source'}).hover();
 assert.equal(await toolbarsMatch(true),true);assert.deepEqual(await box('.a-canvas-html'),beforeHover);await page.screenshot({path:'/tmp/amplifier-canvas-hover.png'});
 await page.mouse.move(100,30);await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').dataset.controls==='false');
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('button',{name:'0',exact:true}).click();
 await patch({canvasControlsPinned:false,canvasControlsExpanded:false});await page.mouse.move(100,30);
 assert.equal(await toolbarsMatch(false),true);
 await page.getByRole('button',{name:'Pin canvas controls',exact:true}).focus();await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').dataset.controls==='true');
 await page.getByRole('textbox',{name:'Message Amplifier'}).focus();await page.mouse.move(100,30);await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').dataset.controls==='false');
 // Opening a form and clicking its input must not keep unpinned controls open.
 for(const [button,input,value] of [['Open a file in canvas','#canvas-file-path','notes.md'],['Open a website in canvas','#canvas-browser-url','https://example.com/']]){
  await page.locator('.a-canvas-head').hover();await page.getByRole('button',{name:button,exact:true}).click();
  const field=page.locator(input);await field.fill(value);
  await page.mouse.move(100,30);await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').dataset.controls==='false');
  assert.deepEqual(await box('.a-canvas-html'),beforeHover,'unpinning and dismissing do not move Canvas content');
  await page.locator('.a-canvas-head').hover();assert.equal(await field.inputValue(),value,'hiding controls keeps the unfinished form');
  await page.getByRole('button',{name:'Pin canvas controls',exact:true}).click();await page.mouse.move(100,30);await settled();
  assert.equal(await page.locator('.a-canvas-panel').getAttribute('data-controls'),'true');
  await page.getByRole('button',{name:'Unpin canvas controls',exact:true}).click();await page.mouse.move(100,30);
  await page.waitForFunction(()=>document.querySelector('.a-canvas-panel').dataset.controls==='false');
 }
 await patch({canvasDraft:{},canvasControlsExpanded:false});
 const header=await box('.a-canvas-head'),body=await box('.a-canvas-body'),iframe=await box('.a-canvas-html');
 assert.equal(header.height,36);assert.ok(Math.abs(body.x-iframe.x)<1&&Math.abs(body.width-iframe.width)<1);assert.ok(Math.abs(body.y-iframe.y)<1);assert.ok(Math.abs(iframe.y+iframe.height-950)<1);
 const separator=page.getByRole('separator',{name:'Resize canvas'});await separator.focus();await page.keyboard.press('End');await settled();
 const expanded=await box('.a-canvas-panel'),chat=await box('.a-conversation');assert.ok(expanded.width>1000);assert.ok(chat.width>=360&&chat.width<=361);
 const composer=await box('.a-composer');assert.ok(composer.x>=chat.x&&composer.x+composer.width<=chat.x+chat.width);assert.ok(await page.locator('.a-compose-bottom').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.getByRole('button',{name:'Model and reasoning settings'}).click();
 const models=await box('.a-model-popover');assert.ok(models.x>=0&&models.x+models.width<=1600); // Compact selectors are positioned in the viewport.
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.screenshot({path:'/tmp/amplifier-canvas-wide.png'});
 // Mouse drag reaches the same bounds and persists only its completed preference.
 const handle=await separator.boundingBox();await page.mouse.move(handle.x+handle.width/2,handle.y+80);await page.mouse.down();await page.mouse.move(handle.x+200,handle.y+80,{steps:6});await page.mouse.up();await settled();
 assert.ok((await box('.a-canvas-panel')).width<expanded.width-150);
 await patch({navPinned:true,navExpanded:true});await settled();
 const nav=page.getByRole('separator',{name:'Resize navigation'});await nav.focus();await page.keyboard.press('End');await settled();
 assert.ok((await box('.a-nav-slot')).width>700);assert.ok((await box('.a-canvas-panel')).width>=300);assert.ok((await box('.a-conversation')).width>=360);
 await nav.focus();await page.keyboard.press('Home');await separator.focus();await page.keyboard.press('End');await settled();assert.equal(Math.round((await box('.a-nav-slot')).width),216);assert.equal(Math.round((await box('.a-conversation')).width),360);
 await patch({canvasControlsExpanded:true});await page.getByRole('button',{name:'Pin canvas controls',exact:true}).click();await page.mouse.move(100,30);await settled();assert.equal(await toolbarsMatch(true),true);assert.ok((await box('.a-canvas-body')).y>(await box('.a-canvas-head')).y+36);
 await patch({canvasControlsPinned:false,canvasControlsExpanded:false});
 await page.getByRole('button',{name:'Focus canvas',exact:true}).click();await page.mouse.move(500,400);await patch({canvasControlsExpanded:false});await settled();
 assert.deepEqual(await box('.a-canvas-panel'),{x:0,y:0,width:1600,height:950});assert.equal(await page.locator('.a-conversation').evaluate(el=>el.inert),true);
 assert.equal(await frame.getByRole('button',{name:'1',exact:true}).count(),1);await frame.getByRole('button',{name:'1',exact:true}).click();
 await page.screenshot({path:'/tmp/amplifier-canvas-focus.png'});
 await page.getByRole('button',{name:'Exit canvas focus',exact:true}).focus();await page.keyboard.press('Escape');await settled();assert.equal(await page.locator('.a-canvas-panel').getAttribute('data-focused'),'false');assert.equal(await frame.getByRole('button',{name:'2',exact:true}).count(),1);assert.equal(await page.locator('.a-conversation').evaluate(el=>el.inert),false);
 // Agent uses the same full-frame action; close resets focus for subsequent reopen.
 await patch({canvasFocused:true});await action('canvas.close',{});await action('canvas.reopen',{});assert.equal(await page.locator('.a-canvas-panel').getAttribute('data-focused'),'false');
 for(const width of [1050,800,740,700,390]){
  await page.setViewportSize({width,height:844});await settled();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
  const canvas=await box('.a-canvas-panel');assert.ok(canvas.x>=0&&canvas.x+canvas.width<=width);
  if(width>=800)assert.ok((await box('.a-conversation')).width>=360);
 }
 await page.screenshot({path:'/tmp/amplifier-canvas-narrow.png'});
 await patch({canvasFocused:true});await settled();assert.deepEqual(await box('.a-canvas-panel'),{x:0,y:0,width:390,height:844});await page.getByRole('button',{name:'Exit canvas focus',exact:true}).click();
 assert.deepEqual(errors,[]);console.log('Flexible canvas passed: minimum widths, drag/keyboard resize, compact/pinned controls, edge-to-edge, live focus/escape/restore, agent actions and narrow layouts.');
}finally{await browser.close();fixture.kill()}
