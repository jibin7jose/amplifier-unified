import './composer-test-helpers.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const python=process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python';
const outputBase=resolve(process.env.AMPLIFIER_TEST_OUTPUT_DIR||join(root,'working-files'));
await mkdir(outputBase,{recursive:true});
const outputDir=await mkdtemp(join(outputBase,'assignment-adoption-browser-'));
const fixture=spawn(python,[root+'/tests/fixtures/assignment_adoption_ui_server.py'],{
 stdio:['ignore','pipe','inherit'],env:{...process.env,AMPLIFIER_TEST_OUTPUT_DIR:outputDir}});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
  fixture.once('error',reject);
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1100,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.setDefaultTimeout(10000);
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page);
 const inspect=()=>page.request.get(url+'/fixture').then(response=>response.json());
 const before=await inspect();
 const composer=page.getByRole('textbox',{name:'Message Amplifier'});
 await composer.fill('Keep my unsent manager draft');
 const open=async()=>{
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-work'}}));
  await page.getByRole('button',{name:'Tasks and workers',exact:true}).click();
  await page.getByText('Shared tasks and assignment',{exact:true}).click();
  await page.getByText('Add an existing chat',{exact:true}).click();
 };
 await open();
 await page.getByLabel('Chat to add',{exact:true}).selectOption({label:'Existing navigation work'});
 await expect(page.getByRole('button',{name:'Add chat',exact:true})).toBeEnabled();
 await page.request.post(url+'/fixture/change-target');
 await page.getByRole('button',{name:'Add chat',exact:true}).click();
 await expect(page.getByRole('alert').filter({hasText:'The chat or assignment changed'})).toBeVisible();
 assert.equal((await inspect()).targetAssignment,null);
 // A rejected revision is not an unknown outcome or another cleanup chore.
 await expect(page.getByText('A change has no confirmed response.',{exact:false})).toHaveCount(0);
 await page.locator('details').filter({has:page.locator('summary', {hasText:/^Add an existing chat$/})}).last()
  .getByRole('button',{name:'Refresh',exact:true}).click();
 await expect(page.getByRole('button',{name:'Add chat',exact:true})).toBeEnabled();
 let lostReply=false;const attempts=[];
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='assignment.adopt'){
   attempts.push(body);
   if(!lostReply){lostReply=true;await route.fetch();await route.abort('connectionreset');return}
  }
  await route.continue();
 });
 await page.getByRole('button',{name:'Add chat',exact:true}).click();
 await expect(page.getByRole('button',{name:'Check the same change',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Check the same change',exact:true}).click();
 await expect(page.getByRole('button',{name:'Check the same change',exact:true})).toHaveCount(0);
 assert.equal(attempts.length,2);assert.deepEqual(attempts[0],attempts[1]);
 await expect.poll(async()=>Object.keys((await inspect()).assignment.members).length).toBe(2);
 const after=await inspect();
 assert.equal(after.targetAssignment.id,after.assignment.id);
 assert.equal(after.assignment.members[before.target].role,'adopted');
 assert.deepEqual(after.targetMessages,before.targetMessages);
 assert.deepEqual(after.sent,[]);assert.deepEqual(after.stops,[]);
 assert.equal(after.targetStatus,before.targetStatus);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),before.source);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await expect(composer).toHaveDraft('Keep my unsent manager draft');
 await page.reload();await showFixtureConversation(page);await open();
 await expect(page.getByRole('status').filter({hasText:'2 current members · 2 retained members'})).toBeVisible();
 const dialog=page.locator('[data-part="dialog"]:visible');
 await page.getByText('Add an existing chat',{exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:join(outputDir,'assignment-desktop.png'),animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await page.getByText('Add an existing chat',{exact:true}).scrollIntoViewIfNeeded();
 assert.equal(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth),true);
 await page.screenshot({path:join(outputDir,'assignment-mobile.png'),animations:'disabled'});
 assert.deepEqual(errors,[]);
 const result={passed:true,productionService:true,productionBuiltUI:true,
  deterministicRuntime:true,providerCalls:false,checks:[
   'stale review rejected without stuck pending action','lost acknowledgment retries exact identity',
   'explicit review then adoption','unchanged history',
   'no implicit input or Stop','draft and selection preserved',
   'cold reopen membership','desktop and mobile layout']};
 await writeFile(join(outputDir,'result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify({outputDir,...result}));
}finally{
 await browser?.close();fixture.kill('SIGTERM');
}
