import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:http';
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[fileURLToPath(new URL('../../tests/fixtures/chat_ui_server.py',import.meta.url))],{stdio:'inherit'});
const demo=createServer((req,res)=>{res.setHeader('Content-Type','text/html');res.end('<h1>Launched app</h1><button onclick="this.textContent=\'It works\'">Try demo</button><p id="isolation"></p><script>try{parent.document.title;document.getElementById("isolation").textContent="UNSAFE"}catch(e){document.getElementById("isolation").textContent="Isolated from Amplifier"}</script>')});
await new Promise(resolve=>demo.listen(0,'127.0.0.1',resolve));
for(let i=0;i<100;i++){try{if((await fetch('http://127.0.0.1:8958/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
page.on('pageerror',e=>errors.push(e.message));
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
try{
 await page.goto('http://127.0.0.1:8958/');await page.waitForSelector('#amp-one');
 await action('view.update',{patch:{canvasControlsPinned:true}});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Make visuals');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState();return s.sessions.find(x=>x.id===s.selectedSessionId)?.status==='idle'});
 await action('canvas.show',{kind:'markdown',title:'Saved plan',content:'# The plan\n\nKept across tabs.'});
 const first=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await action('canvas.show',{kind:'html',title:'Interactive card',content:'<h1>Second artifact</h1>'});
 assert.equal(await page.getByRole('tab').count(),2);
 await page.getByRole('tab',{name:'Saved plan',exact:true}).click();await page.locator('.a-canvas-body').getByRole('heading',{name:'The plan',exact:true}).waitFor();
 await page.getByRole('button',{name:'Close tab Saved plan',exact:true}).click();await page.frameLocator('.a-canvas-html').getByRole('heading',{name:'Second artifact'}).waitFor();
 await page.getByRole('button',{name:'Saved artifacts (2)',exact:true}).click();await page.getByRole('searchbox',{name:'Filter saved artifacts'}).fill('Saved*');
 await page.getByRole('button',{name:'Saved plan markdown · Saved snapshot',exact:true}).click();await page.locator('.a-canvas-body').getByRole('heading',{name:'The plan',exact:true}).waitFor();
 await page.reload();await page.locator('.a-canvas-body').getByRole('heading',{name:'The plan',exact:true}).waitFor();assert.equal(await page.getByRole('tab').count(),2);
 await page.getByRole('button',{name:'Close canvas panel',exact:true}).click();await page.locator('.a-chat-artifacts').getByRole('button',{name:'Saved plan',exact:true}).click();await page.locator('.a-canvas-body').getByRole('heading',{name:'The plan',exact:true}).waitFor();
 await page.getByRole('button',{name:'Open a website in canvas',exact:true}).click();
 const address=`http://127.0.0.1:${demo.address().port}/demo`;await page.getByLabel('App or website address').fill(address);await page.locator('.a-canvas-address').getByRole('button',{name:'Open',exact:true}).click();
 const frame=page.frameLocator('.a-browser-preview iframe');await frame.getByRole('heading',{name:'Launched app'}).waitFor();await frame.getByRole('button',{name:'Try demo'}).click();await frame.getByRole('button',{name:'It works'}).waitFor();await frame.getByText('Isolated from Amplifier',{exact:true}).waitFor();assert.equal(await page.getByRole('tab').count(),3);
 assert.equal(await page.getByRole('link',{name:'Open in browser',exact:true}).first().getAttribute('href'),address);
 await page.getByRole('button',{name:'Reload browser preview',exact:true}).click();await frame.getByRole('button',{name:'Try demo'}).waitFor();
 await page.screenshot({path:'/tmp/amplifier-canvas-tabs.png'});
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight));await page.screenshot({path:'/tmp/amplifier-canvas-tabs-mobile.png'});
 assert.deepEqual(errors,[]);console.log('Artifact browser verified: multiple tabs, close/reopen library, filter, reload persistence, chat links, launched app with live interaction, reload, isolation, external link, mobile containment.');
}finally{await browser.close();fixture.kill();demo.close()}
