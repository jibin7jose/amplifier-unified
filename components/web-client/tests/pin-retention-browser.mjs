import {showFixtureConversation} from './browser-chat-pages.mjs';
// Focused installed-wheel pin retention acceptance; synthetic history, no inference.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/recent_visibility_server.py',import.meta.url))],{stdio:['ignore','pipe','inherit']});
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-pin-retention';
let browser,page;
try{
 const boot=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture timeout')),45000);let text='';
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}});
 });
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({extraHTTPHeaders:{Authorization:'Bearer fixture-recent-visibility-token'},viewport:{width:1440,height:1000}});
 page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const recent=page.locator('[data-shell-instance=chats] [data-sidebar-section=recent]');
 const pinned=page.locator('[data-shell-instance=chats] [data-sidebar-section=pinned]');
 const ids=region=>region.locator('.a-nav-chat[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId));
 const projection=()=>page.evaluate(()=>window.amplifier.getShellState()?.snapshots?.chats?.recentNavigation);
 await page.goto(boot.url);await showFixtureConversation(page,{navPinned:true});await expect(recent.locator('.a-nav-chat[data-session-id]')).toHaveCount(20);
 const metrics=async()=>{const response=await page.request.get(boot.url+'/fixture/metrics');assert.ok(response.ok());return response.json()};
 const initial=await metrics(),clientId=(await state()).client.id;
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});
 await composer.fill('Retain draft while pinning');await expect(composer).toHaveDraft('Retain draft while pinning');
 const before=await state();
 await action('shell.view.update',{clientId,instanceId:'chats',patch:{navShowAgentCreated:true}});
 await expect.poll(async()=>(await projection())?.total).toBe(134);
 const expected=await ids(recent);assert.ok(expected.includes(initial.pinned));
 await expect(pinned.locator(`[data-session-id="${initial.pinned}"]`)).toHaveCount(1);
 // Choose two ordinary eligible chats. Pinning and reordering cannot promote them in Recent.
 const ordinary=expected.filter(id=>id!==initial.pinned).slice(0,2);
 for(const id of ordinary)await action('session.pin',{id,pinned:true});
 await expect.poll(()=>ids(pinned)).toEqual([initial.pinned,...ordinary]);
 const reordered=[...ordinary].reverse().concat(initial.pinned);
 await action('session.pinOrder',{ids:reordered});
 await expect.poll(()=>ids(pinned)).toEqual(reordered);
 assert.deepEqual(await ids(recent),expected);
 for(const id of ordinary)await expect(recent.locator(`[data-session-id="${id}"]`)).toHaveCount(1);
 // Both copies open one identity-bound details flyout; unpin from Recent leaves its row in place.
 const id=ordinary[0];
 for(const region of [pinned,recent]){
  await region.locator(`[data-session-id="${id}"]`).hover();
  await region.locator(`[data-session-id="${id}"]`).getByRole('button',{name:/Details and actions/}).click();
  await expect(page.getByRole('dialog')).toHaveCount(1);await page.keyboard.press('Escape');
 }
 await recent.locator(`[data-session-id="${id}"]`).hover();
 await recent.locator(`[data-session-id="${id}"]`).getByRole('button',{name:/Details and actions/}).click();
 await page.getByRole('dialog').getByRole('button',{name:/^Unpin /}).click();
 await page.keyboard.press('Escape');
 await expect(pinned.locator(`[data-session-id="${id}"]`)).toHaveCount(0);
 assert.deepEqual(await ids(recent),expected);
 await page.reload();await expect.poll(()=>ids(recent)).toEqual(expected);
 await expect.poll(()=>ids(pinned)).toEqual(reordered.filter(value=>value!==id));
 await expect(composer).toHaveDraft('Retain draft while pinning');
 const after=await state();assert.equal(after.selectedSessionId,before.selectedSessionId);assert.equal(after.selectedWorkspaceId,before.selectedWorkspaceId);
 assert.deepEqual((await metrics()).runtimeCalls,[]);assert.deepEqual(errors,[]);
 await mkdir(output,{recursive:true});await page.screenshot({path:output+'/pin-retention.png'});
 const result={status:'passed',applicationModule:boot.applicationModule,expectedPackageChecked:boot.expectedPackageChecked,
  checks:['eligible pin in both sections','ordinary Recent rank unchanged by pin/unpin/reorder','details from both copies','pin order survives reload','composer and selection retained','no runtime calls']};
 await writeFile(output+'/pin-retention.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));
}catch(error){if(page){await mkdir(output,{recursive:true});await page.screenshot({path:output+'/pin-retention-failure.png'})}throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
