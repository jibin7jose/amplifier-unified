import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Delivery/draft recovery uses the fixture's synthetic provider discovery too;
// these scenarios must not provision a real Foundation runtime in background.
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser,primaryError;
async function stopFixture(){
 let escalated=false;
 const result=await new Promise((resolve,reject)=>{
  if(fixture.exitCode!==null||fixture.signalCode!==null)return resolve([fixture.exitCode,fixture.signalCode]);
  const kill=setTimeout(()=>{escalated=true;fixture.kill('SIGKILL')},10000);
  const timeout=setTimeout(()=>reject(Error('Empty host did not exit after cleanup escalation')),15000);
  fixture.once('exit',(code,signal)=>{clearTimeout(kill);clearTimeout(timeout);resolve([code,signal])});
  fixture.kill('SIGTERM');
 });
 assert.equal(escalated,false,'Fixture cleanup exceeded its graceful shutdown budget');
 // Windows kill uses TerminateProcess, not a catchable POSIX signal.
 if(process.platform==='win32')assert.ok(result[0]===0||result[1]==='SIGTERM','Fixture must exit after termination');
 else assert.deepEqual(result,[0,null],'Fixture must finish application and temporary-directory cleanup');
}
try{
 const url=await new Promise((resolve,reject)=>{
  const timeout=setTimeout(()=>reject(Error('Empty host startup timed out')),15000);let output='';
  fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Empty host exited '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){try{const value=JSON.parse(line);if(value.url){clearTimeout(timeout);resolve(value.url)}}catch{}}});
 });
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const persistedDraft=async text=>expect.poll(async()=>{
  const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
  const response=await page.request.get(url+'/api/state',{headers:{'X-Amplifier-Client':clientId}});
  assert.equal(response.status(),200);
  const result=await response.json();return (result.state||result).view?.draft;
 }).toBe(text);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const bootError=new Promise((_,reject)=>page.once('pageerror',reject));
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();
 await Promise.race([page.getByRole('textbox',{name:'Message Amplifier'}).waitFor(),bootError]);
 const initial=await page.evaluate(()=>window.amplifier.getState());
 assert.equal(initial.sessions.length,0);
 assert.ok(!initial.selectedSessionId);
 await expect(page.getByRole('region',{name:'New chat',exact:true})).toBeVisible();
 assert.equal(await page.locator('.a-message').count(),0);
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await expect(composer).toBeEditable();
 await expect(page.getByText('Sending message…',{exact:true})).toHaveCount(0);
 // Pause before sending so the empty composer's debounced autosave really runs.
 const autosaved=page.waitForResponse(response=>{
  const request=response.request();
  return new URL(response.url()).pathname==='/api/actions'&&request.method()==='POST'&&
   request.postDataJSON()?.action==='view.update'&&Object.hasOwn(request.postDataJSON()?.args?.patch||{},'draft');
 });
 await composer.fill('Unsent before any conversation');
 const saved=await autosaved;
 assert.equal(saved.status(),200,await saved.text());
 await expect(page.getByRole('alert')).toHaveCount(0);
 assert.equal((await page.evaluate(()=>window.amplifier.getState())).sessions.length,0);
 await page.reload();
 await expect(composer).toHaveDraft('Unsent before any conversation');
 assert.ok(!(await page.evaluate(()=>window.amplifier.getState())).selectedSessionId);
 // The first send creates its conversation. Hold its HTTP acknowledgement to
 // exercise the genuine pending-send state as well as the empty idle state.
 let release,releaseCreate,createSeen;
 const held=new Promise(resolve=>{release=resolve});
 const creating=new Promise(resolve=>{createSeen=resolve});
 const heldCreate=new Promise(resolve=>{releaseCreate=resolve});
 let firstCreate=true,createAttempts=0,sendAttempts=0;
 await page.route('**/api/actions',async route=>{
  const action=route.request().method()==='POST'&&route.request().postDataJSON()?.action;
  if(action==='session.create')createAttempts++;
  if(action==='conversation.send')sendAttempts++;
  if(action==='session.create'&&firstCreate){firstCreate=false;createSeen();await heldCreate;if(process.argv.includes('--lost-create')){await route.fetch();return route.fulfill({status:503,json:{error:'Creation acknowledgement lost'}})}if(process.argv.includes('--fail-create'))return route.fulfill({status:503,json:{error:'Synthetic creation failure'}})}
  // Selection arrives before the queued draft save. Keep that interval visible
  // so reload acceptance must observe durable state rather than win a race.
  if(action==='view.update'&&route.request().postDataJSON()?.args?.patch?.draft==='Next draft while the first delivery is pending'&&process.argv.includes('--lost-create'))await new Promise(resolve=>setTimeout(resolve,500));
  if(action==='conversation.send')await held;
  await route.continue();
 });
 await composer.fill('First input on an empty host');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const sendingStatus=page.locator('.a-composer .a-sr-only[role="status"]');
 await expect(sendingStatus).toHaveText('Sending message…');
 assert.match(await sendingStatus.ariaSnapshot(),/Sending message…/,'Sending remains in the accessibility tree');
 assert.equal(await sendingStatus.evaluate(el=>getComputedStyle(el).position),'absolute','Status must not participate in composer layout');
 await expect(page.locator('.a-compose-bottom [role="status"]')).toHaveCount(0);
 await expect(composer).toBeEditable();
 await expect(composer).toHaveDraft('');
 await creating;
 await composer.fill('Next draft while the first delivery is pending');
 // Exceed the debounce while creation is held; the draft must remain bound to
 // this first conversation even if autosave becomes ready before its ID exists.
 await page.waitForTimeout(350);
 await expect(composer).toHaveDraft('Next draft while the first delivery is pending');
 releaseCreate();
 if(process.argv.includes('--fail-create')){
  await page.getByRole('button',{name:'Check delivery',exact:true}).waitFor();
  await expect(composer).toHaveDraft('Next draft while the first delivery is pending');
  await page.getByRole('button',{name:'Check delivery',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry original submission',exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'New chat',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Summarize a file',exact:false})).toHaveCount(0);
  assert.equal(createAttempts,1,'Checking delivery must not retry conversation creation');
  assert.equal(sendAttempts,0,'Checking delivery must not send the message');
  await page.getByRole('button',{name:'Retry original submission',exact:true}).click();
 }
 release();
 if(process.argv.includes('--lost-create')){
  await page.waitForFunction(()=>window.amplifier.getState().selectedSessionId);
  const first=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
  await persistedDraft('Next draft while the first delivery is pending');
  await page.reload();await composer.waitFor();
  const check=page.getByRole('button',{name:'Check delivery',exact:true});
  if(await check.count())await check.click();
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),first);
 }
 await page.getByText('Synthetic first response',{exact:true}).waitFor();
 await expect(composer).toHaveDraft('Next draft while the first delivery is pending');
 await expect(composer).toBeEditable();
 await expect(page.getByText('Sending message…',{exact:true})).toHaveCount(0);
 assert.equal(await page.locator('.a-message.a-user').count(),1);
 const sent=await (await page.request.get(url+'/fixture')).json();
 assert.equal(sent.foundationCachePresent,false,'Synthetic delivery must not provision a Foundation runtime');
 assert.equal(sent.sent.length,1);
 assert.equal(sent.sent[0].text,'First input on an empty host');
 assert.equal(createAttempts,process.argv.includes('--fail-create')?2:1);
 assert.equal(sendAttempts,1,'The confirmed first message is submitted exactly once');
 // The public UI snapshot includes pending edits. Reload acceptance must wait
 // for the actual client state saved by the host, not the optimistic display.
 await persistedDraft('Next draft while the first delivery is pending');
 await page.reload();
 await expect(composer).toHaveDraft('Next draft while the first delivery is pending');
 await page.waitForFunction(()=>window.amplifier.getState().sessions.length===1&&window.amplifier.getState().selectedSessionId);
 const selected=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('An unsent first-chat draft');
 await persistedDraft('An unsent first-chat draft');
 await page.reload();
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('An unsent first-chat draft');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);
 assert.equal(await page.locator('.a-message.a-user').count(),1);
 assert.deepEqual(errors,[]);
 console.log('Empty host passed: debounced draft before any conversation, delayed creation '+(process.argv.includes('--fail-create')?'failure/retry':'success')+', next draft preserved, exactly one first delivery, draft and selection after reload; synthetic runtime only.');
}catch(error){primaryError=error;throw error}
finally{
 const failures=[];
 try{await browser?.close()}catch(error){failures.push(error)}
 try{await stopFixture()}catch(error){failures.push(error)}
 if(failures.length){
  if(primaryError)console.error('Empty host cleanup also failed',new AggregateError(failures));
  else throw new AggregateError(failures,'Empty host cleanup failed');
 }
}
