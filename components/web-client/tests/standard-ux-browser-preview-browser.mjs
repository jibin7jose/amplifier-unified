import {showFixtureConversation} from './browser-chat-pages.mjs';
// Synthetic loopback preview and real public browser viewer; no external website.
import './composer-test-helpers.mjs';
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory required');await mkdir(out,{recursive:true});
const visits=[];
const preview=createServer((request,response)=>{visits.push(request.url);response.setHeader('Content-Type','text/html');if(request.url==='/blocked')response.setHeader('X-Frame-Options','DENY');response.end('<h1>Exact local preview '+(request.url==='/blocked'?'blocked':'allowed')+'</h1><button onclick="document.querySelector(\'p\').textContent=\'Local click handled\'">Try local control</button><p>Ready</p>')});
await new Promise(resolve=>preview.listen(0,'127.0.0.1',resolve));const local='http://127.0.0.1:'+preview.address().port;
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]),state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();await action('session.create');await composer.fill('Keep local preview draft');await page.getByRole('button',{name:'Open canvas',exact:true}).click();
 await action('canvas.show',{kind:'browser',title:'Owned local preview',url:local+'/allowed'});
 const panel=page.locator('#workspace-canvas'),frame=panel.frameLocator('iframe');await expect(frame.getByRole('heading',{name:'Exact local preview allowed'})).toBeVisible();await frame.getByRole('button',{name:'Try local control'}).click();await expect(frame.locator('p')).toHaveText('Local click handled');
 assert.equal(await frame.locator('body').evaluate(()=>{try{void parent.document.body;return false}catch{return true}}),true);
 await expect.poll(async()=>(await state()).canvas.renderReports?.preview?.status).toBe('unverified');
 await panel.getByRole('button',{name:'Preview help',exact:true}).click();await expect(panel).toContainText('cannot verify the contents');
 const options=panel.getByRole('button',{name:'Canvas options',exact:true});if(await options.getAttribute('aria-expanded')!=='true')await options.click();
 const beforeReload=visits.filter(x=>x==='/allowed').length;await panel.getByRole('button',{name:'Reload browser preview'}).click();await expect.poll(()=>visits.filter(x=>x==='/allowed').length).toBe(beforeReload+1);await expect(frame.locator('p')).toHaveText('Ready');
 await action('canvas.show',{kind:'browser',title:'Owned denied preview',url:local+'/blocked'});await expect.poll(()=>visits.filter(x=>x==='/blocked').length).toBeGreaterThan(0);await expect.poll(async()=>(await state()).canvas.renderReports?.preview?.status).toBe('unverified');await expect(frame.getByRole('heading',{name:'Exact local preview blocked'})).toHaveCount(0);
 await panel.getByRole('button',{name:'Preview help',exact:true}).click();await expect(panel).toContainText('Some sites block embedding');const external=panel.getByRole('link',{name:'Open in browser',exact:true});await expect(external).toHaveAttribute('href',local+'/blocked');const opened=page.waitForEvent('popup');await external.click();const popup=await opened;await expect(popup.getByRole('heading',{name:'Exact local preview blocked'})).toBeVisible();await popup.close();
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await panel.getByRole('button',{name:'Back to chat',exact:true}).click();await expect(composer).toHaveDraft('Keep local preview draft');assert.deepEqual(await page.request.get(url+'/fixture').then(r=>r.json()).then(x=>x.sent),[]);
 await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks:['Actual loopback iframe content/control; opaque parent boundary.','Public Reload performs exactly one additional local request and resets local control.','Allowed and X-Frame-Options denied previews stay honestly unverified; help explains embedding policy.','Public Open in browser opens exact denied local page outside frame.','Narrow page containment, draft and no model input preserved.'],limits:['Synthetic loopback pages only; no external account/authentication/site or cross-machine reachability claim.','Browser kind seeded through public action as fixture setup; tested viewer controls are ordinary UI.','Does not exercise HTTPS mixed-content rejection.']},null,2)+'\n');
}finally{try{await browser?.close()}finally{try{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);await done.finally(()=>clearTimeout(timer))}}finally{preview.closeAllConnections();await new Promise(resolve=>preview.close(resolve))}}}
