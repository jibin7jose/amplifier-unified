import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public rename/error retention, all Search choices and explicit saved-chat refresh.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory required');await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/standard_navigation_ui_server.py'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;const errors=[],checks=[];fixture.stderr.on('data',data=>logs+=data);
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Navigation fixture timed out')),15000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Navigation fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',error=>errors.push(error.message));
 const inspect=async()=>{const r=await page.request.get(ready.url+'/fixture/navigation');assert.equal(r.status(),200);return r.json()};
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Private navigation draft');
 const before=await inspect(),selected=ready.ids[0];assert.deepEqual(before.runtime,{started:[],sent:[],stopped:[]});
 const recent=page.locator('[data-sidebar-section="recent"]'),row=recent.locator(`[data-session-id="${selected}"]`);
 await expect(row).toBeVisible();await row.hover();await row.getByRole('button',{name:'Details and actions for Alpha ordinary',exact:true}).click();
 let detail=page.getByRole('dialog',{name:'Details for Alpha ordinary',exact:true});await detail.getByRole('button',{name:'Rename Alpha ordinary',exact:true}).click();
 let name=detail.getByRole('textbox',{name:'New name for Alpha ordinary',exact:true});await name.fill('Discard this name');await detail.getByRole('button',{name:'Cancel conversation rename',exact:true}).click();assert.equal((await inspect()).titles[selected],'Alpha ordinary');
 await detail.getByRole('button',{name:'Rename Alpha ordinary',exact:true}).click();name=detail.getByRole('textbox',{name:'New name for Alpha ordinary',exact:true});await name.fill('Retained edited name');
 let attempted=0,held=false,release;
 const rejectRename=async route=>{const body=route.request().postDataJSON(),action=body.action==='shell.command'?(body.args?.command||body.args)?.action:body.action;if(action!=='session.rename')return route.continue();attempted++;held=true;await new Promise(resolve=>{const timer=setTimeout(resolve,8000);release=()=>{clearTimeout(timer);resolve()}});await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic rename temporarily unavailable'})})};
 await page.route('**/api/actions',rejectRename);await detail.getByRole('button',{name:'Save conversation name',exact:true}).click();await expect.poll(()=>held).toBe(true);await expect(name).toBeDisabled();await expect(detail.getByRole('button',{name:'Save conversation name',exact:true})).toBeDisabled();assert.equal((await inspect()).titles[selected],'Alpha ordinary');
 release();await expect(detail.getByRole('alert')).toContainText('Synthetic rename temporarily unavailable');await expect(name).toHaveValue('Retained edited name');await expect(name).toBeEnabled();assert.equal(attempted,1);await page.unroute('**/api/actions',rejectRename);
 await detail.getByRole('button',{name:'Save conversation name',exact:true}).click();await expect.poll(async()=>(await inspect()).titles[selected]).toBe('Retained edited name');await page.keyboard.press('Escape');
 checks.push('Cancel preserves original title; held refused rename disables repeat and retains exact edit; explicit retry changes only the intended title.');
 await page.getByRole('button',{name:'Search chats',exact:true}).click();const search=page.getByRole('region',{name:'Chat search',exact:true});await search.locator('summary').click();
 const archive=search.getByRole('combobox',{name:'Active or archived conversations'}),status=search.getByRole('combobox',{name:'Filter chats by status'}),location=search.getByRole('combobox',{name:'Filter chats by location'}),sort=search.getByRole('combobox',{name:'Sort conversations'});
 const resultRows=search.getByLabel('Search results',{exact:true}),ids=()=>resultRows.locator('[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId));
 const expectIds=async expected=>{await expect(resultRows).toHaveAttribute('aria-busy','false');await expect.poll(async()=>(await ids()).sort()).toEqual([...expected].sort());await expect(search.getByRole('status')).toHaveText(`${expected.length} ${expected.length===1?'chat':'chats'}`);assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected)};
 await expectIds(ready.ids.slice(0,3));await archive.selectOption('archived');await expectIds([ready.ids[3]]);await archive.selectOption('all');await expectIds(ready.ids);await archive.selectOption('active');await expectIds(ready.ids.slice(0,3));
 await status.selectOption('working');await expectIds([ready.ids[1]]);await status.selectOption('attention');await expectIds([ready.ids[2]]);await status.selectOption('all');await expectIds(ready.ids.slice(0,3));
 await location.selectOption('managed');await expectIds([]);await expect(search.getByRole('heading',{name:'No chats found',exact:true})).toBeVisible();await location.selectOption('all');await expectIds(ready.ids.slice(0,3));
 for(const value of ['created','name','activity']){await sort.selectOption(value);await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().snapshots.chats.sidebarNavigation.search.scope.sort||'activity')).toBe(value);await expectIds(ready.ids.slice(0,3));if(value==='name')assert.deepEqual(await ids(),[ready.ids[1],ready.ids[2],ready.ids[0]])}
 await search.getByRole('searchbox',{name:'Search chats',exact:true}).fill('Nothing matches this fixture');await expect.poll(ids).toEqual([]);await expect(search.getByRole('heading',{name:'No chats found',exact:true})).toBeVisible();await search.getByRole('button',{name:'Clear search and filters',exact:true}).click();await expectIds(ready.ids.slice(0,3));
 checks.push('Every declared Search archive/location/status/sort choice responds with exact fixture membership/counts, real empty state, and unchanged selected chat.');
 await row.locator('.a-nav-chat-select').click();await expect(composer).toBeVisible();
 const seeded=await page.request.post(ready.url+'/fixture/seed-native');assert.equal(seeded.status(),200);
 const refresh=page.getByRole('button',{name:'Refresh workspaces and chats',exact:true});await refresh.click();await expect(recent.getByText('New saved native chat',{exact:true})).toBeVisible({timeout:10000});await expect(refresh).toBeEnabled();
 await expect(recent.locator(`[data-session-id="${selected}"]`)).toBeVisible();const after=await inspect();assert.deepEqual(after.messages,before.messages);assert.equal(after.file,before.file);assert.deepEqual(after.runtime,before.runtime);assert.equal(after.nativePresent,true);assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);await expect(composer).toHaveDraft('Private navigation draft');assert.deepEqual(errors,[]);
 checks.push('Explicit Refresh discovers actual new saved native files and settles enabled, preserving existing rows, canonical messages, file, selection, draft and zero runtime calls.');
 await page.screenshot({path:out+'/navigation-actions.png'});await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks,limits:['Desktop synthetic fixture; native selects exercised with selectOption, not native OS picker gestures.','Rename refusal is controlled HTTP503 before effect; no lost-ack claim.','Refresh observed successful bounded completion; no held refresh/readable-pending assertion.','Sort membership/all options proven; exact created/activity order beyond membership is not separately asserted.']},null,2)+'\n');console.log('Navigation actions passed: rename cancel/pending/refusal/retry, all Search choices and counts, actual saved-native Refresh, no execution or data loss.');
}catch(error){failure=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(error){cleanup.push(error)}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.log',logs)}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Navigation cleanup failed')}
