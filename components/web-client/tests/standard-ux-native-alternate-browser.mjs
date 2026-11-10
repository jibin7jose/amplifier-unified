import {showFixtureConversation} from './browser-chat-pages.mjs';
// Explicit native-history display is separate from authoritative saved web rows.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory is required');
await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/conversation_export_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;const errors=[],pages=[],checks=[],requests=[];
fixture.stderr.on('data',chunk=>logs+=chunk);
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Native history fixture timed out')),15000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Native history fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1280,height:950},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>requests.push(new URL(request.url())));
 const inspect=async()=>{const response=await page.request.get(ready.url+'/api/fixture/nativeWindow');assert.equal(response.status(),200);return response.json()};
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeVisible();await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep private alternate-history draft');
 const original=await inspect(),messages=page.locator('article[data-message-id]');
 await expect.poll(()=>messages.evaluateAll(rows=>rows.map(row=>row.dataset.messageId))).toEqual(original.messageIds);
 assert.equal(original.messageIds.length,21);assert.ok(original.messageIds.includes('voice'));assert.deepEqual(original.runtimeCalls,[]);
 await expect(page.getByRole('button',{name:'View saved runtime history',exact:true})).toBeVisible();
 assert.equal(requests.some(url=>url.pathname==='/api/conversation/navigation'),false);
 const readNative=()=>page.waitForResponse(response=>{const u=new URL(response.url());return u.pathname==='/api/conversation/detail'&&u.searchParams.get('native')==='true'&&response.status()===200}).then(response=>response.json());
 const initialPage=readNative();await page.getByRole('button',{name:'View saved runtime history',exact:true}).click();pages.push(await initialPage);
 const native=page.locator('article[data-message-id^="native-window:"]');await expect(native).toHaveCount(100);await expect(page.locator('article[data-message-id="voice"]')).toHaveCount(0);
 const latestIds=await native.evaluateAll(rows=>rows.map(row=>row.dataset.messageId));assert.equal(new Set(latestIds).size,100);
 for(const row of pages[0].items){assert.equal(row.displayOnly,true);assert.equal(Object.hasOwn(row,'nativeIndex'),false);assert.equal(Object.hasOwn(row,'nativeMessageId'),false)}
 assert.equal(pages[0].total,null);assert.equal(pages[0].offset,null);assert.equal(pages[0].mode,'cursor');assert.equal(pages[0].hasEarlier,true);
 assert.equal(pages[0].coverage.globalInputUniqueness??pages[0].globalInputUniqueness,'unknown');
 await expect(native.locator('[data-action="session.fork"], [data-action="message.reaction"], [data-action="message.reply"]')).toHaveCount(0);await expect(native.getByRole('button',{name:'Edit message',exact:true})).toHaveCount(0);await expect(native.getByRole('button',{name:'React to message',exact:true})).toHaveCount(0);
 await expect(native.locator('.a-inline-media')).toHaveCount(0);
 checks.push('Explicit alternate view loads exactly100 unique display-only source rows with unknown totals and no guessed native ordinals or unsupported mutation/media actions.');
 const olderPage=readNative();await page.getByRole('button',{name:'Load earlier conversation',exact:true}).click();pages.push(await olderPage);await expect(native).toHaveCount(140);
 const allIds=await native.evaluateAll(rows=>rows.map(row=>row.dataset.messageId));assert.equal(new Set(allIds).size,140);assert.deepEqual(allIds.slice(-100),latestIds);await expect(native.first()).toContainText('The earliest question');
 assert.equal(pages[1].total,null);assert.equal(pages[1].offset,null);assert.equal(pages[1].hasEarlier,false);await expect(page.getByRole('button',{name:'Load earlier conversation',exact:true})).toHaveCount(0);
 const readsBeforeCopy=requests.length;
 await native.first().hover();await native.first().getByRole('button',{name:'Copy message as Markdown',exact:true}).click();await expect.poll(()=>page.evaluate(()=>navigator.clipboard.readText())).toBe('The earliest question');
 assert.deepEqual(requests.slice(readsBeforeCopy).filter(url=>url.pathname==='/api/conversation/detail'&&url.searchParams.get('part')==='message'),[],'Display-only copy uses its displayed source text, never a canonical message lookup');
 assert.deepEqual(requests.filter(url=>url.pathname==='/api/conversation/detail'&&url.searchParams.get('id')?.startsWith('native-window:')),[],'Synthetic display-only IDs are never sent to the canonical detail owner');
 assert.deepEqual(await inspect(),original);
 checks.push('Explicit older cursor adds exactly40 earlier source rows, preserves the100-row suffix, stops at source start, copies exact local text, and changes neither canonical web messages nor native bytes.');
 await page.getByRole('button',{name:'Return to latest',exact:true}).click();await expect.poll(()=>messages.evaluateAll(rows=>rows.map(row=>row.dataset.messageId))).toEqual(original.messageIds);await expect(native).toHaveCount(0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep private alternate-history draft');assert.deepEqual(await inspect(),original);assert.deepEqual(errors,[]);
 checks.push('Return to latest restores exact canonical web IDs including recorded voice and keeps the private draft; no runtime calls or native navigation-index reads.');
 assert.equal(requests.some(url=>url.pathname==='/api/conversation/navigation'),false);
 await page.screenshot({path:out+'/native-alternate.png'});await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks,pages:pages.map(row=>({items:row.items.length,total:row.total,offset:row.offset,hasEarlier:row.hasEarlier,coverage:row.coverage})),limits:['Synthetic140-row saved native transcript and21 canonical web messages; no real user data or provider.','Desktop pointer ordinary path only; no native OS interactions or full four-mode repetition.','No canonical global uniqueness or complete Work reconstruction claim.']},null,2)+'\n');console.log('Explicit native alternate history passed:100+40display-only rows, unknown totals, no unsupported actions, exact canonical return, unchanged source files and zero runtime calls.');
}catch(error){failure=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(error){cleanup.push(error)}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.log',logs)}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Native alternate cleanup failed')}
