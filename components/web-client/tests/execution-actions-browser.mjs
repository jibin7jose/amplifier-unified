import {showFixtureConversation} from './browser-chat-pages.mjs';
import {chromium,expect} from '@playwright/test';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const outputDir=process.env.AMPLIFIER_TEST_OUTPUT_DIR||await mkdtemp(join(tmpdir(),'amplifier-execution-actions-'));
await mkdir(outputDir,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/browser_detail_server.py',import.meta.url)),fileURLToPath(new URL('../../',import.meta.url))],{stdio:['ignore','pipe','pipe'],env:{...process.env,AMPLIFIER_TEST_WORKER_DIAGNOSTICS:'1'}});
let log='';fixture.stderr.on('data',chunk=>log+=chunk);
const port=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out: '+log)),30000);fixture.stdout.on('data',chunk=>{output+=chunk;const line=output.split('\n').find(row=>row.startsWith('{"port":'));if(line){clearTimeout(timer);resolve(JSON.parse(line).port)}});fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+log))})});
const base=`http://127.0.0.1:${port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
const control=async body=>{const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify(body)});if(!response.ok)throw Error(await response.text()+' '+log);return response.json()};
await control({op:'inspection'});await control({op:'inspection-actions'});await control({op:'inspection-finish'});
const browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
let workProjection;
try{
 const page=await browser.newPage({viewport:{width:1300,height:1050},extraHTTPHeaders:headers}),errors=[];
 const fieldReads=[];page.on('request',r=>{if(r.url().includes('/api/conversation/detail?')){const params=new URL(r.url()).searchParams;if(params.get('part')==='nodes'&&params.has('field'))fieldReads.push(r.url())}});
 page.on('pageerror',e=>{errors.push(e.message);console.error('Page error:',e.message)});
 await page.goto(base);await showFixtureConversation(page);
 const turn=page.locator('[data-group-id="inspect@long-answer"]');
 // Wait for canonical groups before clicking; positional locators can retarget as history arrives.
 await expect.poll(async()=>{
  const value=await page.locator('.a-messages').getAttribute('data-work-projection');
  workProjection=value?JSON.parse(value):null;
  return workProjection;
 }).toMatchObject({status:'ready',current:true,receipt:{scope:'recent-events'}});
 for(const id of ['inspect@question','inspect@long-answer']){
  const group=page.locator(`[data-group-id="${id}"] > button.a-execution-turn-line`);
  await group.waitFor();
  if(await group.getAttribute('aria-expanded')==='false')await group.click();
  await expect(group).toHaveAttribute('aria-expanded','true');
 }
 const action=id=>turn.locator(`[data-node-id$=":${id}"]`),open=async id=>{const row=action(id);await row.waitFor();const button=row.locator(':scope > button.a-execution-action-line');if(await button.getAttribute('aria-expanded')==='false')await button.click();return row};
 await expect(action('command').locator(':scope > button')).toContainText('bash');
 await expect(action('command').locator(':scope > button')).toContainText('python -m pytest');
 await expect(action('app').locator(':scope > button')).toContainText('session.rename');
 await expect(action('code').locator(':scope > button')).toContainText('tools.check');
 await expect(action('code').locator(':scope > button')).toContainText('FixtureError: exact failure detail');
 await expect(action('code').locator(':scope > button')).toContainText('Exit 9');
 assert.equal(fieldReads.length,0,'Skimming collapsed rows must not fetch complete payloads');
 await action('code').scrollIntoViewIfNeeded();
 await page.screenshot({path:join(outputDir,'collapsed-action-hints.png')});
 const code=await open('code');
 await expect(code.getByRole('button',{name:/Show all .* lines/}).first()).toBeVisible();
 await code.getByRole('button',{name:/Show all .* lines/}).first().click();
 await expect(code).toContainText('CODE-END');
 await expect(code).toContainText('FixtureError: exact failure detail');
 const patch=await open('patch');await patch.locator('.a-execution-diff').waitFor();
 assert.equal(await patch.locator('.a-execution-diff-row.add').count(),2);assert.equal(await patch.locator('.a-execution-diff-row.remove').count(),1);
 assert.match(await patch.innerText(),/result = retry\(operation\)/);
 const command=await open('command');assert.match(await command.innerText(),/12 passed in 0.21s/);assert.match(await command.innerText(),/Exit 0/);
 assert.equal(await command.getByText('Call:',{exact:false}).isVisible(),false);
 const read=await open('read');assert.match(await read.innerText(),/42  +assert result.status/);
 const tasks=await open('tasks');assert.equal(await tasks.locator('.a-execution-checklist li').count(),2);assert.match(await tasks.innerText(),/Review the patch/);
 const app=await open('app');assert.match(await app.innerText(),/session.rename/);assert.match(await app.innerText(),/Retry operation ID check/);
 const empty=await open('empty');assert.match(await empty.innerText(),/No output/);
 const failed=await open('failed');assert.match(await failed.innerText(),/AssertionError: expected committed/);assert.match(await failed.innerText(),/Exit 1/);assert.equal(await failed.locator('.a-execution-warning').count(),1);
 const malformed=await open('malformed');assert.match(await malformed.innerText(),/unknown/);assert.match(await malformed.innerText(),/Invalid task input/);
 const delegated=turn.getByRole('region',{name:'Request',exact:true});
 await expect(delegated).toContainText('Review the retry test');
 await expect(turn.locator('[data-kind="worker"] > button')).toHaveCount(0);
 await expect(turn.getByRole('button',{name:/^Worker activity/})).toHaveCount(0);
 await expect(action('nested').locator(':scope > button')).toHaveAttribute('aria-expanded','false');
 const nested=await open('nested');assert.match(await nested.innerText(),/git diff --check/);assert.match(await nested.innerText(),/No output/);
 await page.locator('[data-node-id="legacy"] > button').click();
 assert.match(await page.locator('[data-node-id="legacy"]').innerText(),/Action content is not (?:available|in the loaded saved work)/);assert.ok(!(await turn.innerText()).includes('Tool completed'));
 // Agent-facing view update uses exactly the same expansion path as buttons.
 const expanded=await page.evaluate(()=>window.amplifier.getState().view.executionExpanded);
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{executionExpanded:[]}}));
 await page.waitForFunction(()=>!document.querySelector('[data-group-id="inspect@long-answer"] .a-execution-body'));
 await page.evaluate(expanded=>window.amplifier.dispatch('view.update',{patch:{executionExpanded:expanded}}),expanded);
 await patch.locator('.a-execution-diff').waitFor();
 await page.reload();await patch.locator('.a-execution-diff').waitFor();assert.match(await command.innerText(),/12 passed/);
 // Capture the two primary action views together at a reviewable size.
 const ids=[await patch.getAttribute('data-node-id'),await command.getAttribute('data-node-id')];
 await page.evaluate(ids=>window.amplifier.dispatch('view.update',{patch:{executionExpanded:['turn:inspect@long-answer',...ids],navPinned:false,navExpanded:false}}),ids);
 await patch.locator('.a-execution-diff').waitFor();await patch.scrollIntoViewIfNeeded();await page.mouse.move(1250,20);
 await page.screenshot({path:join(outputDir,'actions-desktop.png')});
 await page.emulateMedia({colorScheme:'dark'});await page.screenshot({path:join(outputDir,'actions-dark.png')});
 await page.setViewportSize({width:390,height:844});await patch.scrollIntoViewIfNeeded();await page.mouse.move(380,830);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:join(outputDir,'actions-mobile.png')});
 assert.deepEqual(errors,[]);
 console.log('Canonical event-log command/result, patch, read, todo, app and nested worker payloads render; empty, error and legacy states are honest; shared expansion survives reload and mobile fits.');
}catch(error){
 const failure={error:String(error),workProjection};
 try{
  const response=await fetch(base+'/api/fixture/control',{method:'POST',headers,
   body:JSON.stringify({op:'inspect-work-diagnostics'}),signal:AbortSignal.timeout(2000)});
  if(!response.ok)throw Error('Diagnostics HTTP '+response.status);
  failure.fixture=await response.json();
 }catch(diagnosticError){failure.diagnosticError=String(diagnosticError)}
 console.error('Execution failure diagnostics:',JSON.stringify(failure));
 const saved=await Promise.allSettled([
  writeFile(join(outputDir,'failure.json'),JSON.stringify(failure,null,2)),
  writeFile(join(outputDir,'fixture.stderr.log'),log),
 ]);
 for(const result of saved)if(result.status==='rejected')console.error('Diagnostic write failed:',result.reason);
 console.error(log);throw error;
}finally{await browser.close();fixture.kill()}
