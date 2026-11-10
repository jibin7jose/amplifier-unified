// Production assets and disposable retained history; no provider calls/user data.
import {chromium,expect} from '@playwright/test';
import {openSettingsPage} from './browser-settings.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||await mkdtemp(join(tmpdir(),'amplifier-bootstrap-recovery-'));
await mkdir(artifacts,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe']});
let fixtureLog='';fixture.stderr.on('data',chunk=>fixtureLog+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+fixtureLog)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+fixtureLog))})});
const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>(await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)})).json();
// Both active synthetic owners stay resident. Idle mock rows without native
// files are not a valid retained-history fixture under bounded residency.
const seeded=await control({op:'heavy',active:true,messages:520,otherMessages:549,chars:5000,nodes:400,nodeBytes:10000});
assert.equal(seeded.retainedSessions,2);assert.equal(seeded.retainedMessages,1069);assert.ok(seeded.canonicalBytes>5000000);
// This probe supplies resident web messages, not a native transcript file.
await control({op:'patch',sessions:{[seeded.alpha]:{historyManaged:false},[seeded.beta]:{historyManaged:false}}});
const compact=await (await fetch(base+'/api/state',{headers})).text();assert.ok(Buffer.byteLength(compact)<1000000);
let browser;
try{
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1400,height:1000},extraHTTPHeaders:headers});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let initialSocket;
 await page.routeWebSocket('**/api/events/ws?**',socket=>{initialSocket=socket});
 const start=Date.now();await page.goto(base);await expect(page.locator('.boot')).toContainText('Connecting to your workspace');
 await expect(page.getByRole('region',{name:'New chat',exact:true})).toHaveCount(0);await expect(page.locator('[data-message-id]')).toHaveCount(0);
 await expect.poll(()=>!!initialSocket).toBe(true);initialSocket.connectToServer();
 await page.getByRole('button',{name:'Settings',exact:true}).waitFor();const shellMs=Date.now()-start;const initialIds=await page.locator('[data-message-id]').evaluateAll(rows=>rows.map(row=>row.dataset.messageId));assert.equal(initialIds.length,100);assert.deepEqual(initialIds,Array.from({length:100},(_,i)=>seeded.alpha+'-m-'+(420+i)));
 const built=await page.request.get(base+'/build.json').then(response=>response.json());
 assert.equal(await page.evaluate(()=>window.amplifier.getState().client.build.frontend.id),built.id,'Streamed identity must match the served build');
 let blockedDetail;await page.route('**/api/conversation/detail?**',route=>{blockedDetail=route});
 await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();
 await expect(page.getByRole('button',{name:'Loading earlier conversation…',exact:true})).toBeDisabled();
 await openSettingsPage(page,'history');await page.locator('.a-settings-experience[data-settings-page="history"]').waitFor();
 assert.ok(blockedDetail,'Slow history is independent of Settings/Maintenance');
 await blockedDetail.fulfill({status:503,json:{error:'Fixture history temporarily unavailable'}});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();await page.getByRole('alert').filter({hasText:'Fixture history temporarily unavailable'}).waitFor();
 await page.unroute('**/api/conversation/detail?**');const earlier=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/conversation/detail'&&response.ok()).then(response=>response.json());await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();const older=await earlier;assert.ok(older.items.length>0&&older.items.length<=100);await expect(page.locator('[data-message-id]')).toHaveCount(initialIds.length+older.items.length);
 const messageIds=await page.locator('[data-message-id]').evaluateAll(els=>els.map(el=>el.dataset.messageId));assert.equal(new Set(messageIds).size,messageIds.length);assert.deepEqual(messageIds.slice(-100),initialIds);assert.deepEqual(messageIds.slice(0,-100),older.items.map(row=>row.id));
 const alpha=await (await fetch(base+'/api/state?sessionId='+seeded.alpha,{headers})).json(),beta=await (await fetch(base+'/api/state?sessionId='+seeded.beta,{headers})).json();
 assert.equal(alpha.sessions.find(s=>s.id===seeded.alpha).messages.length,520);assert.equal(beta.sessions.find(s=>s.id===seeded.beta).messages.length,549);
 assert.ok((await page.getByRole('textbox',{name:'Message Amplifier'}).isEnabled()),'Retained chat stays usable');

 // The event stream can bootstrap the UI even when the initial state read stalls.
 const streamed=await context.newPage();await streamed.route('**/api/state',()=>{});await streamed.goto(base);await streamed.getByRole('button',{name:'Settings',exact:true}).waitFor({timeout:5000});await streamed.close();
 // With the first state stream unavailable, show a finite retry and reconnect.
 const retry=await context.newPage();let first=true;
 await retry.routeWebSocket('**/api/events/ws?**',socket=>{if(first){first=false;socket.close()}else socket.connectToServer()});
 await retry.goto(base);await retry.getByRole('button',{name:'Retry connection',exact:true}).waitFor({timeout:13000});await retry.getByRole('button',{name:'Retry connection',exact:true}).click();await retry.getByRole('button',{name:'Settings',exact:true}).waitFor();await retry.close();
 const retained=await control({op:'inspect'});assert.equal(retained.retainedMessages,1069);assert.equal(retained.retainedSessions,2);assert.equal(retained.runtimeSends,0);assert.equal(retained.runtimeStarts,0);assert.deepEqual(errors,[]);
 const result={sessions:2,messages:1069,canonicalBytes:seeded.canonicalBytes,browserBytes:Buffer.byteLength(compact),initialMessages:initialIds.length,loadedMessages:messageIds.length,shellMs,maintenanceDuringSlowDetail:true,recoveredDetailFailure:true,stateStreamFallback:true,startupRetry:true,historyRetained:true};
 await writeFile(join(artifacts,'amplifier-bootstrap-recovery-results.json'),JSON.stringify(result,null,2));console.log({...result,artifacts});
}finally{await browser?.close();fixture.kill()}
