import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect as playwrightExpect} from '@playwright/test';
import {openChatAdvancedPage} from './browser-chat-pages.mjs';

// Uses the compiled application and real shared service actions. This browser
// scope deliberately stops before account checks and destination activation;
// separate service/process acceptance owns the complete two-host protocol.
const expect=playwrightExpect.configure({timeout:30000});
const fixture=spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),
 [fileURLToPath(new URL('../../tests/fixtures/portability_ui_server.py',import.meta.url))],
 {stdio:['ignore','pipe','inherit']});
const screenshots=process.env.PORTABILITY_SCREENSHOT_DIR;
let browser;
try{
 const ready=await new Promise((resolve,reject)=>{
  let output='';
  const timer=setTimeout(()=>reject(Error('Portability fixture timeout')),45000);
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n')){
   try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row);}}catch{}
  }});
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Portability fixture exited '+code));});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1280,height:900},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const state=async()=>{const response=await page.request.get(ready.url+'/fixture/portability-state');assert.equal(response.status(),200);return response.json();};
 const screenshot=async name=>{if(screenshots){await mkdir(screenshots,{recursive:true});await page.screenshot({path:join(screenshots,name)});}};

 await page.goto(ready.url);await showFixtureConversation(page);
 await openChatAdvancedPage(page,'Work & automation');
 const panel=page.getByRole('region',{name:'Move task between hosts'});
 const worktrees=page.getByRole('region',{name:'Task worktrees'});
 await panel.getByText('Export this task',{exact:true}).click();
 const exportButton=panel.getByRole('button',{name:'Stage export and stop execution here',exact:true});
 await expect(exportButton).toBeDisabled();
 await panel.getByRole('button',{name:'Inspect hosts and transfers',exact:true}).click();
 await expect(panel.getByRole('option',{name:ready.peer.label,exact:true})).toHaveCount(1);
 await panel.getByLabel('Destination',{exact:true}).selectOption(ready.peer.id);
 await expect(exportButton).toBeDisabled();
 await worktrees.getByRole('button',{name:'Inspect repository',exact:true}).click();
 await expect(worktrees.getByText('main · Uncommitted changes present',{exact:true})).toBeVisible();
 await panel.getByLabel('Include staged, unstaged and untracked work',{exact:true}).check();
 await expect(exportButton).toBeDisabled();
 await panel.getByLabel('I reviewed this task’s transfer contents',{exact:true}).check();
 await expect(exportButton).toBeEnabled();
 assert.equal((await state()).receipts.length,0);

 await exportButton.click();
 const card=panel.locator('[data-transfer-id]').first();
 await expect(card.getByRole('status')).toHaveText('Export · preparing');
 await expect(exportButton).toBeDisabled();
 const pending=await state();
 assert.equal(pending.releaseStarted,true);
 assert.equal(pending.releaseCount,1);
 assert.equal(pending.historyUnchanged,true);
 assert.equal(pending.workspaceUnchanged,true);
 assert.equal(pending.providerWorkers,0);
 await page.request.post(ready.url+'/fixture/allow-release');
 await expect(card.getByRole('status')).toHaveText('Export · prepared');
 const prepared=await state();
 assert.equal(prepared.nativeFence?.role,'source');
 assert.equal(prepared.nativeFence?.phase,'staged');
 assert.equal(prepared.ordinaryAcquisitionAllowed,false);
 assert.equal(prepared.packages[0].mode,'carry_dirty');
 assert.deepEqual(prepared.packages[0].untracked,['new.txt']);
 assert.equal(prepared.packages[0].ignoredFilesIncluded,false);
 await expect(card.getByRole('button',{name:'Release source ownership',exact:true})).toBeDisabled();
 await panel.getByRole('button',{name:'Cancel unreleased export',exact:true}).scrollIntoViewIfNeeded();
 await screenshot('portability-desktop-prepared.png');

 await page.reload();
 await openChatAdvancedPage(page,'Work & automation');
 await expect(card.getByRole('status')).toHaveText('Export · prepared');
 const cancel=panel.getByRole('button',{name:'Cancel unreleased export',exact:true});
 await expect(cancel).toBeDisabled();
 await panel.getByLabel('What did you verify?',{exact:true}).fill('   ');
 await expect(cancel).toBeDisabled();
 await panel.getByLabel('What did you verify?',{exact:true}).fill('No destination release occurred. Retain the unchanged source task and its files.');
 await expect(cancel).toBeEnabled();
 await cancel.click();
 await expect(card.getByRole('status')).toHaveText('Export · cancelled');
 const cancelled=await state();
 assert.equal(cancelled.nativeFence,null);
 assert.equal(cancelled.ordinaryAcquisitionAllowed,true);
 assert.equal(cancelled.historyUnchanged,true);
 assert.equal(cancelled.workspaceUnchanged,true);
 assert.equal(cancelled.releaseCount,1);
 assert.equal(cancelled.providerWorkers,0);

 await panel.getByText('Import a staged task',{exact:true}).click();
 const importButton=panel.getByRole('button',{name:'Verify and stage import',exact:true});
 await expect(importButton).toBeDisabled();
 await panel.getByLabel('Package path on this host',{exact:true}).fill(ready.invalidPackage);
 await expect(importButton).toBeDisabled();
 await panel.getByLabel('Destination repository',{exact:true}).fill(ready.root);
 await importButton.click();
 const importError=page.getByRole('dialog',{name:'Work & automation',exact:true})
  .getByRole('alert').filter({hasText:'Unsupported transfer receipt'});
 await expect(importError).toBeVisible();
 await expect(card.getByRole('status')).toHaveText('Export · cancelled');
 assert.equal((await state()).receipts.length,1);

 await page.setViewportSize({width:390,height:844});
 await importError.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Document overflows at 390px');
 assert.ok(await panel.evaluate(element=>element.scrollWidth<=element.clientWidth),'Portability panel overflows at 390px');
 await screenshot('portability-mobile-error.png');
 await card.scrollIntoViewIfNeeded();
 await screenshot('portability-mobile-cancelled.png');
 await page.reload();
 await openChatAdvancedPage(page,'Work & automation');
 await expect(card.getByRole('status')).toHaveText('Export · cancelled');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Reloaded mobile document overflows');
 assert.deepEqual(errors,[]);
 console.log('Portability browser passed: paired-host inspect, review gating, dirty export preparing/prepared, real native fence, reload, evidence-gated cancel, signed import rejection, and 390px mobile. Scope excludes account/provider execution and full destination activation.');
}finally{
 await browser?.close();
 if(fixture.exitCode===null&&fixture.signalCode===null){
  await new Promise(resolve=>{
   const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve();},5000);
   fixture.once('exit',()=>{clearTimeout(timer);resolve();});
   fixture.kill();
  });
 }
}
