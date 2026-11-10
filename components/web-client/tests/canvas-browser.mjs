import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'inherit'});
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'},permissions:['clipboard-read','clipboard-write']});
const page=await context.newPage(),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const action=(name,args)=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const ready=()=>page.waitForFunction(()=>window.amplifier.getState().canvas.renderReports?.preview?.status==='ready',{},{timeout:30000});
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'html',title:'Interactive example',content:`<!doctype html><html><head><style>body{font:16px system-ui;padding:24px;background:#eef2ff}button{padding:12px}</style></head><body><h1>Interactive canvas</h1><button onclick="this.textContent='Clicked'">Try it</button><p id="boundary"></p><script>let denied=false;try{parent.document.title}catch(e){denied=true}document.getElementById('boundary').textContent=denied?'Parent isolated':'FAILED';fetch('/api/state').then(()=>document.body.dataset.network='FAILED').catch(()=>document.body.dataset.network='blocked');</script></body></html>`});
 await ready();
 await page.waitForFunction(()=>window.amplifier.getState().canvas.document?.controls?.some(c=>c.label==='Try it'));
 const canvasState=await page.evaluate(()=>window.amplifier.getState().canvas);
 await action('canvas.interact',{id:canvasState.id,controlId:canvasState.document.controls.find(c=>c.label==='Try it').id,event:'click'});
 await page.waitForFunction(()=>window.amplifier.getState().canvas.document?.text.includes('Clicked'));
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('button',{name:'Clicked'}).evaluate(el=>el.textContent='Try it');await frame.getByRole('button',{name:'Try it'}).click();await frame.getByText('Clicked',{exact:true}).waitFor();await frame.getByText('Parent isolated',{exact:true}).waitFor();assert.equal(await frame.locator('body').getAttribute('data-network'),'blocked');
 await page.getByRole('button',{name:'Source',exact:true}).click();await page.locator('.a-canvas-code').waitFor();assert.match(await page.locator('.a-canvas-code').innerText(),/Interactive canvas/);
 await page.getByRole('button',{name:'Copy canvas source'}).click();await page.waitForFunction(()=>window.amplifier.getState().canvas.renderReports?.clipboard?.status==='ready');assert.match(await page.evaluate(()=>navigator.clipboard.readText()),/Interactive canvas/);
 const downloadEvent=page.waitForEvent('download');await page.getByRole('button',{name:'Download canvas source'}).click();assert.equal((await downloadEvent).suggestedFilename(),'Interactive example.html');
 await action('canvas.show',{kind:'mermaid',title:'Amplifier session',content:'flowchart TD\n User --> AmplifierSession\n AmplifierSession --> Worker\n AmplifierSession --> Canvas'});await ready();await page.locator('.a-diagram-stage img').waitFor();assert.ok(await page.locator('.a-diagram-stage img').evaluate(el=>el.complete&&el.naturalWidth>0));
 // Exercise Mermaid's transitive KaTeX API after its security override.
 await action('canvas.show',{kind:'mermaid',title:'Math diagram',content:'flowchart LR\n A["$$x^2 + y^2 = z^2$$"] --> B["Verified"]'});await ready();
 await page.locator('.a-diagram-stage img').waitFor();
 assert.ok(await page.locator('.a-diagram-stage img').evaluate(el=>el.complete&&el.naturalWidth>0),'Math diagram did not decode');
 await page.getByRole('button',{name:'Zoom in',exact:true}).click();await page.waitForFunction(()=>window.amplifier.getState().canvas.view.zoom>1);await page.getByRole('button',{name:'Fit',exact:true}).click();
 await page.screenshot({path:'/tmp/amplifier-canvas-mermaid.png'});
 await action('canvas.show',{kind:'dot',title:'Work routing',content:'digraph G { root [label="AmplifierSession", goal="Help the user"]; root -> worker; root -> canvas; }'});await ready();await page.getByLabel('Inspect graph node').selectOption('root');await page.getByText('Help the user',{exact:true}).waitFor();await page.getByLabel('Graph layout').selectOption('circo');await ready();await page.screenshot({path:'/tmp/amplifier-canvas-dot.png'});
 await action('canvas.show',{kind:'markdown',title:'Mixed document',content:'# A visual document\n\n```mermaid\ngraph LR; A-->B\n```\n\n```dot\ndigraph {a->b}\n```'});
 await page.waitForFunction(()=>{const reports=Object.entries(window.amplifier.getState().canvas.renderReports||{}).filter(([k])=>k.startsWith('fence-'));return reports.length===2&&reports.every(([,v])=>v.status==='ready')});assert.equal(await page.locator('.a-diagram-stage img').count(),2);
 await action('canvas.show',{kind:'dot',content:'this is not DOT'});await page.waitForFunction(()=>window.amplifier.getState().canvas.renderReports?.preview?.status==='error');assert.match(await page.locator('.a-canvas-viewer').innerText(),/syntax error/i);
 await action('canvas.show',{kind:'jsonl',content:'{"name":"alpha"}\n{"name":"beta"}'});await page.getByRole('searchbox',{name:'Filter data records'}).fill('beta');await page.waitForFunction(()=>window.amplifier.getState().canvas.view.query==='beta');assert.equal(await page.locator('.a-canvas-data pre').count(),1);
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));await page.screenshot({path:'/tmp/amplifier-canvas-mobile.png'});
 assert.equal(await page.getByText('This canvas has been replaced. Read the current canvas first.',{exact:true}).count(),0);
 assert.deepEqual(errors,[]);console.log('Canvas browser checks passed: isolated interactive HTML, source/copy/download, Mermaid, Graphviz, graph controls, Markdown diagram fences, render errors, JSONL filtering, mobile containment.');
}finally{await browser.close();fixture.kill()}
