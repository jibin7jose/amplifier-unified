import {showFixtureConversation} from './browser-chat-pages.mjs';
// Public workspace archive/restore/removal changes registration only.
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned artifact directory required');await mkdir(out,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/standard_navigation_ui_server.py','--workspace-actions'],{stdio:['ignore','pipe','pipe']});
let browser,page,logs='',failure;const errors=[],checks=[];fixture.stderr.on('data',data=>logs+=data);
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Workspace fixture timed out')),15000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Workspace fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});page=await browser.newPage({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.on('pageerror',error=>errors.push(error.message));
 const inspect=async()=>{const r=await page.request.get(ready.url+'/fixture/navigation');assert.equal(r.status(),200);return r.json()};
 await page.goto(ready.url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeEditable();await composer.fill('Keep private registration draft');const original=await inspect();
 const [archive,remove]=ready.workspaces;assert.equal(original.registeredWorkspaces.length,3);const selected=ready.ids[0];assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected,'Fixture must start in the stated original chat');
 const body=page.locator('.a-work-browser');await page.getByRole('button',{name:'All workspaces',exact:true}).click();await expect(body.getByRole('heading',{name:'All workspaces',exact:true})).toBeVisible();
 const row=workspace=>body.locator('.a-workspace-row').filter({has:page.getByRole('button',{name:'Open chats in '+workspace.path,exact:true})});
 const details=async workspace=>{await row(workspace).hover();await row(workspace).getByRole('button',{name:'Details and actions for '+workspace.name,exact:true}).click();return page.getByRole('dialog',{name:'Details for '+workspace.name,exact:true})};
 const invariant=async()=>{const now=await inspect();assert.deepEqual(now.messages,original.messages);assert.deepEqual(now.ownedFiles,original.ownedFiles);assert.equal(now.preservedNativeSha256,original.preservedNativeSha256);assert.deepEqual(now.runtime,original.runtime);assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),selected);assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'Keep private registration draft');return now};
 let detail=await details(archive);await detail.getByRole('button',{name:'Archive workspace',exact:true}).click();await expect(row(archive)).toHaveCount(0);await expect.poll(async()=>(await inspect()).registeredWorkspaces.find(row=>row.id===archive.id).archived).toBe(true);await invariant();
 const filter=body.getByRole('combobox',{name:'Workspace archive filter',exact:true});await filter.selectOption('archived');await expect(row(archive)).toBeVisible();await page.reload();await expect(filter).toHaveValue('archived');await expect(row(archive)).toBeVisible();await invariant();
 detail=await details(archive);await detail.getByRole('button',{name:'Restore workspace',exact:true}).click();await filter.selectOption('active');await expect(row(archive)).toBeVisible();await expect.poll(async()=>(await inspect()).registeredWorkspaces.find(row=>row.id===archive.id).archived).toBe(false);await invariant();
 checks.push('Actual menu Archive hides only active registration, Archived filter/reload retain it, and Restore returns it; original native transcript bytes/files/messages/current chat/private draft and runtime stay unchanged.');
 detail=await details(remove);await detail.getByRole('button',{name:'Remove',exact:true}).click();let form=body.locator('.a-nav-form').filter({hasText:'Remove workspace?'});await expect(form).toContainText('Its files and chats will stay on disk.');await form.getByRole('button',{name:'Cancel navigation edit',exact:true}).click();assert.ok((await inspect()).registeredWorkspaces.some(row=>row.id===remove.id));await invariant();
 for(const workspace of [remove,archive]){
  detail=await details(workspace);await detail.getByRole('button',{name:'Remove',exact:true}).click();form=body.locator('.a-nav-form').filter({hasText:'Remove workspace?'});await form.getByRole('button',{name:'Remove registration',exact:true}).click();await expect.poll(async()=>(await inspect()).registeredWorkspaces.some(row=>row.id===workspace.id)).toBe(false);await expect(row(workspace)).toHaveCount(0);await invariant();
 }
 const remaining=(await inspect()).registeredWorkspaces;assert.equal(remaining.length,1);detail=await details(remaining[0]);await expect(detail.getByRole('button',{name:'Remove',exact:true})).toBeDisabled();await page.keyboard.press('Escape');
 await page.reload();await expect(body.getByRole('heading',{name:'All workspaces',exact:true})).toBeVisible();assert.equal((await inspect()).registeredWorkspaces.length,1);await invariant();assert.deepEqual(errors,[]);
 checks.push('Cancel removal keeps registration; explicit Remove registration removes only each chosen registration and survives reload; last remaining workspace removal is disabled while all saved native transcript/file/chat bytes remain.');
 await page.screenshot({path:out+'/workspace-registration.png'});await writeFile(out+'/observations.json',JSON.stringify({passed:true,checks,limits:['Desktop ordinary public path only; synthetic local native/filesystem owners.','Disabled last-registration control proven; unavailable filesystem control is a separate untested state.','No model/provider/network side effects; fixture working row represents retained status, not actual worker.']},null,2)+'\n');console.log('Workspace registration passed: public archive/restore, cancel/remove, last-workspace guard, native/files/chat/draft preserved.');
}catch(error){failure=error;if(page)await page.screenshot({path:out+'/failure.png'}).catch(()=>{});throw error}
finally{const cleanup=[];try{await browser?.close()}catch(error){cleanup.push(error)}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch(error){cleanup.push(error)}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.log',logs)}if(cleanup.length)throw new AggregateError([...(failure?[failure]:[]),...cleanup],'Workspace registration cleanup failed')}
