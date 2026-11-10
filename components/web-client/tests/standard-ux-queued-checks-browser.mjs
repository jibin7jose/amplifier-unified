import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public queued-check controls; controlled local backend, no container/provider acceptance.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory required');await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/test_environments_ui_server.py','--port','0'],{stdio:['ignore','pipe','pipe']});
let browser,page,failure,logs='';const checks=[],errors=[];fixture.stderr.on('data',data=>logs+=data);
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Queue fixture timeout')),30000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Queue fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:1050},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',error=>errors.push(error.message));
 const facts=async()=>{const response=await page.request.get(ready.url+'/fixture/environments');assert.equal(response.status(),200);return response.json()};
 const mode=async value=>{const response=await page.request.post(ready.url+'/fixture/environment-mode',{data:value});assert.equal(response.status(),200)};
 // This credential-free fixture has no connection; dismiss its real setup prompt.
 const dismissSetup=async()=>{const dialog=page.getByRole('dialog',{name:'Settings',exact:true});await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'Close panel',exact:true}).click();await expect(dialog).toHaveCount(0)};
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});await dismissSetup();const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep the queued check private draft');const initial=await facts();
 await page.locator('[data-part="header"] .a-work-crumb:not(.a-work-home-crumb)').click();
 await page.getByRole('group',{name:'Workspace view',exact:true}).getByRole('button',{name:'Details',exact:true}).click();
 await page.getByText('Managed resources',{exact:true}).click();const panel=page.getByRole('region',{name:'Test environments',exact:true});
 await expect(panel.getByRole('form',{name:'Queue isolated check'})).toBeVisible();assert.deepEqual((await facts()).calls,[]);
 await mode({setupRequired:true});await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
 await expect(panel).toContainText('Use Docker for new ordinary checks with the managed Smart Tool runner');
 await expect(panel.getByRole('button',{name:'Set up with Incus',exact:true})).toBeVisible();
 await page.screenshot({path:out+'/managed-runner-setup.png'});
 await panel.getByRole('button',{name:'Set up with Docker',exact:true}).click();await expect(panel.getByRole('form',{name:'Queue isolated check'})).toBeVisible();assert.equal((await facts()).setupCalls.length,1);assert.equal((await facts()).setupCalls[0].backend,'docker');checks.push('Setup recommends managed Docker, preserves explicit Incus, and dispatches the same host action from workspace controls.');
 const originalScope={sessionId:initial.sessionId,workspaceId:initial.workspaceId};
 const invariant=async()=>{const now=await facts();assert.equal(now.sessionId,originalScope.sessionId);assert.equal(now.workspaceId,originalScope.workspaceId);assert.deepEqual(now.messages,initial.messages);assert.deepEqual(now.runtimeCalls,initial.runtimeCalls);assert.equal(now.workspaceFile,initial.workspaceFile);assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep the queued check private draft')};
 const queued=panel.getByRole('region',{name:'Queued checks',exact:true});
 await mode({capacityAvailable:false});const form=panel.getByRole('form',{name:'Queue isolated check'});await form.getByLabel('Check environment',{exact:true}).selectOption('browser-test');await expect(form).toContainText('20-minute limit');await form.getByLabel('Check command',{exact:true}).fill('python check.py');
 await form.getByRole('button',{name:'Queue check',exact:true}).focus();await page.keyboard.press('Enter');await expect(queued.getByText('Check: queued',{exact:true})).toBeVisible();assert.equal((await facts()).callCounts.start||0,0);
 await page.reload();await page.getByText('Managed resources',{exact:true}).click();await expect(queued.getByText('Check: queued',{exact:true})).toBeVisible();
 await queued.getByRole('button',{name:'Cancel check',exact:true}).click();await expect(queued.getByText('Check: cancelled',{exact:true})).toBeVisible();assert.equal((await facts()).callCounts.start||0,0);await invariant();
 checks.push('Public workspace Details entry; keyboard Queue saves waiting intent, reload retains it, Cancel starts no backend effect.');
 await panel.getByLabel('Check environment',{exact:true}).selectOption('linux-python');await mode({capacityAvailable:true,longOutput:true});await panel.getByRole('button',{name:'Queue check',exact:true}).click();
 await expect.poll(async()=>(await facts()).callCounts.remove,{timeout:20000}).toBe(1);await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
 const passed=queued.getByRole('listitem').filter({has:page.getByText('Check: passed',{exact:true})});await expect(passed).toBeVisible();
 await passed.getByText('Check details',{exact:true}).click();const details=JSON.parse(await passed.locator('details[open] pre').textContent()),completed=await facts();
 assert.equal(details.resourceId,completed.calls.filter(call=>call.action==='start').at(-1).resourceId);assert.equal(details.check.requestId,completed.calls.filter(call=>call.action==='execute').at(-1).requestId);assert.equal(details.timeoutSeconds,300);assert.equal(details.removal.state,'completed');
 const beforeRead=(await facts()).calls;await passed.getByRole('button',{name:'Read check result',exact:true}).click();const result=passed.getByLabel('Saved check result',{exact:true});
 await expect(result).toContainText('queue-page-start');await expect(result).not.toContainText('queue-page-end');assert.ok((await result.locator('pre').textContent()).length<=6000);
 await page.setViewportSize({width:390,height:844});await result.getByRole('button',{name:'Next output page',exact:true}).focus();await page.keyboard.press('Enter');await expect(result).toContainText('queue-page-end');await expect(result).not.toContainText('queue-page-start');await expect(result.getByRole('button',{name:'Next output page',exact:true})).toHaveCount(0);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual((await facts()).calls,beforeRead);await invariant();checks.push('Completed check retains bounded exact output pages and check details; explicit next page and narrow layout perform no execution.');
 for(const [status,settings] of [['failed',{executeFailure:true,longOutput:false}],['unknown',{executeFailure:false,executeTimeout:true}]]){
  await mode(settings);await panel.getByRole('button',{name:'Queue check',exact:true}).click();const expected=2;
  await expect.poll(async()=>(await facts()).callCounts.remove,{timeout:20000}).toBe(expected);await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();
  const row=queued.getByRole('listitem').filter({has:page.getByText('Check: '+status,{exact:true})});await expect.poll(async()=>{await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();return row.count()},{timeout:20000}).toBe(1);await expect(row.getByRole('button',{name:'Cancel check',exact:true})).toHaveCount(0);
  await row.getByRole('button',{name:'Read check result',exact:true}).click();await expect(row.getByLabel('Saved check result',{exact:true})).toContainText(status==='failed'?'Exit code: 1':'Recorded request: outcome unknown');
  const calls=(await facts()).calls;await page.reload();await page.getByText('Managed resources',{exact:true}).click();await expect(row).toBeVisible();await panel.getByRole('button',{name:'Refresh test environments',exact:true}).click();await expect(row).toBeVisible();assert.deepEqual((await facts()).calls.filter(call=>!['read','job_read'].includes(call.action)),calls.filter(call=>!['read','job_read'].includes(call.action)));await invariant();
  checks.push('Exact '+status+' result survives reopen and Refresh without replay; unknown jobs preserve environment ownership.');
 }
 assert.deepEqual(errors,[]);const final=await facts();assert.equal(final.callCounts.start,3);assert.equal(final.callCounts.execute,3);assert.equal(final.callCounts.stop,2);assert.equal(final.callCounts.remove,2);
 await page.screenshot({path:out+'/queued-checks.png'});await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks,counts:final.callCounts,errors,limits:['Controlled local backend; no actual Docker/Incus allocation or external provider.','Keyboard activation covers Queue and output paging, not complete Tab traversal.','Reload is browser reopen, not host restart; restart authority is separately tested in Python.','Unknown result is a controlled backend missing acknowledgement; no remote side-effect claim.']},null,2)+'\n');
}catch(error){failure=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(error){cleanup.push(error)}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.log',logs);await writeFile(out+'/observed-prefix.json',JSON.stringify({checks,errors,failure:failure?.message||null},null,2)+'\n')}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Queue browser cleanup failed')}
