import {spawn,execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {once} from 'node:events';
import {mkdtempSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/review_ui_server.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let log='';fixture.stderr.on('data',chunk=>log+=chunk);
const ready=new Promise((resolve,reject)=>{let text='';fixture.stdout.on('data',chunk=>{text+=chunk;const line=text.split('\n').find(row=>row.startsWith('{"port":'));if(line)resolve(JSON.parse(line).port)});fixture.once('exit',code=>reject(Error(`Fixture exited ${code}: ${log}`)))});
const temp=mkdtempSync(join(tmpdir(),'amplifier-preview-tls-'));
let vite,browser;
try{
 const port=await ready,target=`http://127.0.0.1:${port}`;
 execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',join(temp,'key.pem'),'-out',join(temp,'cert.pem'),'-days','1','-subj','/CN=localhost'],{stdio:'ignore'});
 vite=await createServer({configFile:false,root,server:{host:'127.0.0.1',port:0,hmr:false,https:{key:readFileSync(join(temp,'key.pem')),cert:readFileSync(join(temp,'cert.pem'))},proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}},optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']}});await vite.listen();
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},ignoreHTTPSErrors:true,extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const complete=(sessionId,generationId)=>page.evaluate(async data=>{const r=await fetch('/api/fixture/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});if(!r.ok)throw Error(await r.text())},{sessionId,generationId});
 await page.goto(vite.resolvedUrls.local[0]);await page.waitForSelector('#amp-one');
 const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await action('session.create',{title:'Another conversation'});
 await complete(first,'g1');
 await page.waitForFunction(id=>window.amplifier.getState().attention.sessions[id]===1,first);
 await page.reload();await page.waitForFunction(id=>window.amplifier?.getState().attention?.sessions?.[id]===1,first);
 await page.getByRole('button',{name:'Pin navigation open',exact:true}).click();
 await page.locator('.a-workspace-explorer').waitFor();
 await page.locator('.a-workspace-row .a-navigation-status[data-kind="unread"]').waitFor();
 assert.equal(await page.locator('.a-workspace-row .a-navigation-status[data-kind="unread"]').getAttribute('aria-label'),'1 chats with unread activity');
 // The header Activity button is removed; the shared review surface remains available.
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'activity'}}));
 await page.getByRole('region',{name:'Finished responses',exact:true}).getByRole('button',{name:'Settings test',exact:true}).waitFor();
 await page.screenshot({path:'/tmp/amplifier-completion-inbox.png'});
 await page.getByRole('region',{name:'Finished responses',exact:true}).getByRole('button',{name:'Settings test',exact:true}).click();
 await page.waitForFunction(id=>!window.amplifier.getState().attention.sessions[id],first);
 // Scrolling back must keep a new completion unread until the end is visible.
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new WheelEvent('wheel',{deltaY:-300,bubbles:true}))});
 await complete(first,'g2');
 await page.waitForFunction(id=>window.amplifier.getState().attention.sessions[id]===1,first);
 await page.waitForTimeout(1200);
 assert.equal(await page.evaluate(id=>window.amplifier.getState().attention.sessions[id],first),1);
 await page.locator('.a-messages').evaluate(el=>{el.scrollTop=el.scrollHeight});
 await page.waitForFunction(id=>!window.amplifier.getState().attention.sessions[id],first);
 // A selected chat in an unfocused tab must remain unread.
 // Headless Chromium emulates focus on every page; explicitly emulate the
 // browser visibility event used by background tabs.
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))});
 await complete(first,'g3');await page.waitForTimeout(1200);
 assert.equal(await page.evaluate(id=>window.amplifier.getState().attention.sessions[id],first),1);
 await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))});await page.waitForFunction(id=>!window.amplifier.getState().attention.sessions[id],first);
 await action('canvas.show',{kind:'auto',path:'large.html'});
 const frame=page.frameLocator('.a-canvas-html');await frame.getByRole('heading',{name:'Large saved preview'}).waitFor();
 await frame.getByRole('button',{name:'Run',exact:true}).click();await frame.getByRole('button',{name:'Interactive',exact:true}).waitFor();
 assert.ok(await page.evaluate(()=>!!window.amplifier.getState().canvas.contentResource&&!window.amplifier.getState().canvas.content));
 await page.reload();await page.frameLocator('.a-canvas-html').getByRole('heading',{name:'Large saved preview'}).waitFor();
 let id=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await action('canvas.view',{id,patch:{source:true}});await page.locator('.a-canvas-code').filter({hasText:'Large saved preview'}).waitFor();
 await action('canvas.view',{id,patch:{source:false}});
 const download=page.waitForEvent('download');await action('canvas.download',{id});assert.equal((await download).suggestedFilename(),'canvas.html');
 // Do not access the issue reporter's host: TEST-NET-1 is rejected before navigation.
 await action('canvas.show',{kind:'browser',url:'http://192.0.2.1:5190/content',title:'HTTP app'});
 await page.getByRole('heading',{name:'Open this page in your browser'}).waitFor();
 assert.equal(await page.locator('.a-browser-preview iframe').count(),0);
 await page.waitForFunction(()=>window.amplifier.getState().canvas.renderReports.preview?.status==='error');
 assert.ok(await page.locator('.a-browser-help').getByRole('link',{name:'Open in browser',exact:true}).isVisible());
 await page.screenshot({path:'/tmp/amplifier-browser-fallback-desktop.png'});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));
 await page.screenshot({path:'/tmp/amplifier-browser-fallback-narrow.png'});
 assert.deepEqual(errors,[]);
 console.log('Browser regressions passed: persisted completion rollups, visible/focused/end-of-chat acknowledgement, hidden-tab preservation, 3.8MB interactive HTML/source/download/reload, HTTPS mixed-content fallback, narrow layout.');
}finally{await browser?.close();await vite?.close();if(fixture.exitCode===null){fixture.kill('SIGTERM');await once(fixture,'exit').catch(()=>{})}rmSync(temp,{recursive:true,force:true})}
