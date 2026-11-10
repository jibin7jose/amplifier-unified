import {showFixtureConversation} from './browser-chat-pages.mjs';
// Ordinary public sharing path; expiry is advanced only in disposable fixture data.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory is required');
await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/standard_ux_share_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;const actions=[],errors=[],checks=[];
fixture.stderr.on('data',chunk=>logs+=chunk);
try{
 const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Share fixture timed out')),15000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Share fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:950},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions'){const value=request.postDataJSON();actions.push(value.action==='shell.command'?value.args:value)}});
 const observe=async()=>{const response=await page.request.get(url+'/fixture/share');assert.equal(response.status(),200);return response.json()};
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});const draft=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(draft).toBeEditable();await draft.fill('PRIVATE unsent draft must never be shared');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.draft)).toBe('PRIVATE unsent draft must never be shared');
 await page.getByRole('button',{name:'Chat',exact:true}).click();await page.getByRole('button',{name:'Share or save',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Share or save',exact:true});await expect(dialog).toBeVisible();await dialog.locator('summary').filter({hasText:'Share a copy'}).click();
 assert.deepEqual((await observe()).snapshots,[]);await dialog.getByRole('button',{name:'Preview snapshot',exact:true}).click();
 const preview=dialog.getByLabel('Snapshot preview',{exact:true});await expect(preview).toContainText('Saved sharing question');await expect(preview).not.toContainText('PRIVATE unsent draft');
 const previewOnly=await observe();assert.equal(previewOnly.snapshots.length,1);assert.equal(previewOnly.snapshots[0].status,'preview');assert.equal(previewOnly.snapshots[0].sharedAt,undefined);
 await dialog.getByRole('button',{name:'Read more of this snapshot',exact:true}).click();await expect(preview).toContainText('End of saved answer');checks.push('Preview is paged, complete and unpublished; unsent draft omitted');
 await dialog.getByLabel('Link expires after',{exact:true}).selectOption('86400');await dialog.getByRole('button',{name:'Create share link',exact:true}).click();
 const link=dialog.getByLabel('Snapshot link',{exact:true});await expect(link).toBeVisible();const href=await link.inputValue(),shared=(await observe()).snapshots[0];
 assert.equal(shared.status,'shared');assert.ok(Math.abs(shared.expiresAt-shared.sharedAt-86400)<1);
 const publicResponse=await fetch(href);assert.equal(publicResponse.status,200);const original=await publicResponse.text();assert.ok(original.includes('End of saved answer'));assert.ok(!original.includes('PRIVATE unsent draft'));
 await dialog.getByRole('button',{name:'Copy link',exact:true}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe(href);
 await page.request.post(url+'/fixture/share/append');assert.equal(await (await fetch(href)).text(),original);checks.push('Explicit one-day link serves exact immutable reviewed bytes without authentication');
 await dialog.getByRole('button',{name:'Revoke link',exact:true}).click();await expect(dialog.getByText(/^revoked · expires /)).toBeVisible();assert.equal((await fetch(href)).status,404);await expect(link).toHaveCount(0);checks.push('Explicit revoke removes the link and public access');
 await dialog.getByRole('button',{name:'Preview snapshot',exact:true}).click();await expect(preview).toBeVisible();await dialog.getByRole('button',{name:'Create share link',exact:true}).click();await expect(link).toBeVisible();
 const expiringHref=await link.inputValue(),expiring=(await observe()).snapshots.find(row=>row.status==='shared');assert.ok(expiring);
 const expired=await page.request.post(url+'/fixture/share/expire',{data:{id:expiring.id}});assert.equal(expired.status(),200);
 await dialog.getByRole('button',{name:'Show shared snapshots',exact:true}).click();await expect(dialog.getByText(/^expired · expires /)).toBeVisible();assert.equal((await fetch(expiringHref)).status,404);await expect(link).toHaveCount(0);checks.push('Expired stored receipt is truthfully listed and no longer public');
 const final=await observe();assert.equal(final.messages,3);assert.deepEqual(final.runtimeCalls,[]);assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'PRIVATE unsent draft must never be shared');assert.equal(actions.filter(row=>row.action==='session.shareCreate').length,2);assert.equal(actions.filter(row=>row.action==='session.shareRevoke').length,1);assert.deepEqual(actions.filter(row=>['conversation.send','session.warm'].includes(row.action)),[]);assert.deepEqual(errors,[]);
 await page.setViewportSize({width:390,height:844});assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth));await page.screenshot({path:out+'/sharing.png'});
 await writeFile(out+'/observations.json',JSON.stringify({passed:true,workflow:'messages.share',expectedIndices:[0,1,2],checks,sourceContext:'synthetic immutable sharing; local-only bearer links',limits:['Expiry time is advanced through authenticated fixture data, not a real one-day wait.','No remote recipient/network/OS clipboard qualification.','Desktop ordinary path plus narrow containment; no full keyboard/touch journey.']},null,2)+'\n');
 console.log('Sharing passed: paged private preview, explicit immutable one-day link, copy, revoke, controlled expiry, unsent draft and original chat retained.');
}catch(error){failure=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(error){cleanup.push(error)}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.log',logs)}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Sharing cleanup failed')}
