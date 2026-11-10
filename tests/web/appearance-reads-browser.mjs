// Real authenticated dispatcher/host; synthetic fixture only, no model sends.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect as playwrightExpect} from '@playwright/test';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const expect=playwrightExpect.configure({timeout:10000});
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance read queue fixture'});
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-appearance-reads';
const report={status:'running',phase:'fixture',requests:[],holds:[],observerErrors:[],checks:[]};
const holds=[],routeTasks=new Set();let browser,page,failure;
function hold(label,match){
 let release;const gate=new Promise(resolve=>{release=resolve});
 const state={label,matched:false,hostCompleted:false,released:false};
 const entry={state,match,gate,release:()=>{state.released=true;release()}};
 holds.push(entry);report.holds.push(state);return entry;
}
const state=()=>page.evaluate(()=>window.amplifier.getState());
const action=(name,args,id)=>page.evaluate(async({name,args,id})=>{
 let timer;try{return await Promise.race([
  window.amplifier.dispatch(name,args,{id}),
  new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Dispatcher deadline: '+name)),10000)}),
 ])}finally{clearTimeout(timer)}
},{name,args,id});
const waitHeld=entry=>expect.poll(()=>entry.state.hostCompleted,{message:entry.state.label+' reached the real host'}).toBe(true);
try{
 await mkdir(out,{recursive:true});
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture readiness deadline')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const startup=hold('startup theme.list',body=>body.action==='theme.list');
 const thumbnail=hold('visible thumbnail theme.read',body=>body.action==='theme.read'&&body.args.id==='builtin:digital-rain');
 await page.route('**/api/actions',route=>{
  const task=(async()=>{
   const request=route.request();if(request.method()!=='POST'){await route.continue();return}
   const body=request.postDataJSON();assert.ok(body&&typeof body==='object'&&!Array.isArray(body));
   assert.ok(report.requests.length<128,'Bounded action evidence');
   const row={action:body.action,id:body.id,at:performance.now()};report.requests.push(row);
   const entry=holds.find(item=>!item.state.matched&&item.match(body));
   if(!entry){await route.continue();return}
   entry.state.matched=true;entry.state.id=body.id;
   // Complete the real production handler/auth before withholding its response.
   // Never fulfill a fabricated action receipt or substitute an APIRequest call.
   const response=await route.fetch({timeout:10000});
   const receipt=await response.json();assert.equal(receipt.accepted,true);
   entry.state.hostCompleted=true;entry.state.fingerprint=receipt.result?.fingerprint;
   entry.state.status=response.status();entry.state.hostCompletedAt=performance.now();
   await entry.gate;entry.state.releasedAt=performance.now();await route.fulfill({response});
  })().catch(error=>{report.observerErrors.push(String(error.stack||error).slice(0,2000));route.abort().catch(()=>{})});
  routeTasks.add(task);task.finally(()=>routeTasks.delete(task));return task;
 });
 report.phase='held startup catalog';await page.goto(url);
 await page.waitForFunction(()=>window.amplifier?.getState()?.client?.hostInstanceId);
 await waitHeld(startup);
 const created=await action('session.create',{},'sr01-create');assert.equal(created.accepted,true);
 const sessionId=(await state()).selectedSessionId;assert.ok(sessionId);
 async function progress(label){
  const stopReceipt=await action('conversation.stop',{sessionId},'sr01-stop-'+label);
  assert.equal(stopReceipt.accepted,true);
  const writeReceipt=await action('view.update',{patch:{themeDraftName:'SR01 '+label}},'sr01-write-'+label);
  assert.equal(writeReceipt.accepted,true);
  const facts=await (await page.request.get(url+'/fixture')).json();
  assert.ok(facts.stopped.includes(sessionId),'Stop reached the synthetic runtime');
  assert.deepEqual(facts.sent,[],'No conversation/model send');
  assert.deepEqual(facts.started,[],'No conversation/model start');
  report.checks.push({label,stopAccepted:true,writeAccepted:true,stops: facts.stopped.length,at:performance.now()});
  return 'SR01 '+label;
 }
 const startupMarker=await progress('startup');assert.equal(startup.state.released,false);
 startup.release();
 await expect.poll(async()=>(await state()).view.themeDraftName).toBe(startupMarker);
 report.phase='held visible thumbnail';await openSettingsPage(page,'appearance');
 const card=page.getByRole('button',{name:'Use Digital Rain',exact:true});await card.scrollIntoViewIfNeeded();
 await waitHeld(thumbnail);
 const thumbMarker=await progress('thumbnail');assert.equal(thumbnail.state.released,false);
 const sameTheme=(await state()).theme;
 thumbnail.release();
 await expect(card.locator('[data-thumbnail-state=ready]')).toBeVisible();
 await expect(card.locator('[data-appearance-fingerprint]')).toHaveAttribute('data-appearance-fingerprint',thumbnail.state.fingerprint);
 assert.deepEqual((await state()).theme,sameTheme,'Late read cannot apply a theme');
 assert.equal((await state()).view.themeDraftName,thumbMarker,'Late read cannot restore older view state');
 report.phase='preceding write barrier';
 const prior=hold('preceding ordered write',body=>body.id==='sr01-prior-write');
 const read=hold('read after preceding write',body=>body.id==='sr01-after-write-read');
 await page.evaluate(()=>{
  window.sr01Pending={};
  const start=(key,action,args,id)=>{window.sr01Pending[key]=window.amplifier.dispatch(action,args,{id})
   .then(receipt=>({receipt}),error=>({error:String(error)}))};
  start('write','view.update',{patch:{themeDraftName:'SR01 prior'}},'sr01-prior-write');
 });
 await waitHeld(prior);
 await page.evaluate(()=>{window.sr01Pending.read=window.amplifier.dispatch('theme.read',{id:'builtin:ready'},{id:'sr01-after-write-read'})
  .then(receipt=>({receipt}),error=>({error:String(error)}))});
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(read.state.matched,false,'Read still observes the preceding ordered write');
 prior.release();await waitHeld(read);
 const marker=await progress('after-write');assert.equal(read.state.released,false);
 read.release();
 const pending=await page.evaluate(async()=>({write:await window.sr01Pending.write,read:await window.sr01Pending.read}));
 assert.equal(pending.write.receipt?.accepted,true);assert.equal(pending.read.receipt?.accepted,true);
 assert.equal((await state()).view.themeDraftName,marker);
 for(const entry of holds)assert.equal(report.requests.filter(row=>row.id===entry.state.id).length,1,'No repeated held request');
 for(const row of report.checks){
  const entry=row.label==='startup'?startup:row.label==='thumbnail'?thumbnail:read;
  assert.ok(row.at<entry.state.releasedAt,'Stop and write completed before optional response release');
  for(const action of ['stop','write'])assert.equal(report.requests.filter(request=>request.id==='sr01-'+action+'-'+row.label).length,1);
 }
 assert.deepEqual(errors,[]);assert.deepEqual(report.observerErrors,[]);
 report.status='passed';report.phase='complete';
 console.log('Appearance reads passed: real held startup list/thumbnail read, Stop and ordered write before release, prior-write barrier, exact fingerprint, no stale view/theme adoption, no duplicate/model calls.');
}catch(error){failure=error;report.status='failed';report.failure=String(error.stack||error).slice(0,6000);throw error}
finally{
 for(const entry of holds)entry.release();
 try{
  await finishFixture({label:'Appearance read queue fixture',
   close:[['Appearance read browser',async()=>{await browser?.close();report.browserClosed=true}]],
   stop:async()=>{const result=await stop();report.fixtureCleanup=result;return result},originalError:failure});
 }catch(error){report.status='failed';report.cleanupError=String(error.stack||error).slice(0,2000);throw error}
 finally{await writeFile(out+'/appearance-read-queue.json',JSON.stringify(report,null,2))}
}
