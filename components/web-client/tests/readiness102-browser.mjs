import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';

const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/conversation_export_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
let browser,vite;
try{
 const url=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Export fixture timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Export fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}}});
 });
 const target=url,root=fileURLToPath(new URL('../',import.meta.url));
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true,args:process.env.UNIFIED_BROWSER_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const context=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(vite.resolvedUrls.local[0]);await showFixtureConversation(page);await page.waitForSelector('#amp-one');
 // Simulate the Mac modifier contract in Chromium; native PWA acceptance is separate.
 await page.evaluate(()=>Object.defineProperty(navigator,'platform',{configurable:true,value:'MacIntel'}));
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:null,draft:'Keep my draft'}}));
 await page.keyboard.press('Meta+,');
 await expect(page.getByRole('dialog')).toBeVisible();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.panel),'settings');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep my draft');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const markdown='# A report\n\n**Bold** and *emphasis*\n\n- First\n- Second\n\n| Item | Value |\n|---|---|\n| Test | 42 |\n\n```js\nconst a = 1;\n```\n\n<script>alert("no")</script>\n\n![Remote](https://example.invalid/track)';
 await page.evaluate(content=>window.amplifier.dispatch('canvas.show',{kind:'markdown',title:'Report',content}),markdown);
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}}));
 await page.getByRole('button',{name:'Copy formatted',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'Copied formatted document.'})).toBeVisible();
 const copied=await page.evaluate(async()=>{const [item]=await navigator.clipboard.read();return {html:await (await item.getType('text/html')).text(),text:await (await item.getType('text/plain')).text()}});
 assert.equal(copied.text,markdown);
 for(const tag of ['h1','strong','em','ul','li','table','pre','code'])assert.ok(copied.html.includes('<'+tag),tag);
 assert.ok(!copied.html.includes('<script'));assert.ok(!copied.html.includes('<img'));assert.ok(!copied.html.includes('example.invalid'));
 // Clipboard denial and browsers supporting text only remain actionable.
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.savedText=text}}}));
 await page.getByRole('button',{name:'Copy formatted',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'copied Markdown instead'})).toBeVisible();
 assert.equal(await page.evaluate(()=>window.savedText),markdown);
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Object.assign(Error('denied'),{name:'NotAllowedError'})}}}));
 await page.getByRole('button',{name:'Copy formatted',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'Clipboard access was denied'})).toBeVisible();
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.locator('#amp-one').evaluate(el=>el.scrollWidth<=el.clientWidth));
 assert.deepEqual(errors,[]);
 console.log('Readiness102 browser passed: Mac Settings shortcut, retained draft, rich Markdown copy, safe HTML, exact plain fallback, denied clipboard, narrow viewport.');
}finally{await browser?.close();await vite?.close();fixture.kill();}
