import {showFixtureConversation} from './browser-chat-pages.mjs';
// Actual OS denial as an unprivileged DTU user, without weakening agent policy.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/task_storage_ui_server.py'],{env:{...process.env,TASK_STORAGE_DENY_WORKSPACE:'1'},stdio:['ignore','pipe','inherit']});
let browser;
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1366,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),writes=[],errors=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.url().endsWith('/api/actions')&&request.method()==='POST'&&request.postDataJSON().action==='workspace.resources.provision')writes.push(request.postDataJSON())});
 const facts=async()=>{const response=await page.request.get(boot.url+'/fixture/storage');assert.ok(response.ok());return response.json()};
 await page.goto(boot.url);await showFixtureConversation(page,{navPinned:true});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();const before=await facts();assert.equal(before.writeDenied,true);assert.equal(before.managedPathExists,false);
 // Workspace placement is a fixture prerequisite; allocation is the public UI.
 await page.evaluate(workspaceId=>window.amplifier.dispatch('view.update',{patch:{panel:null,setupJourney:'done',workSurface:'workspace',workWorkspaceId:workspaceId,workWorkspaceTab:'details'}}),before.workspaceId);
 await page.getByText('Managed resources',{exact:true}).click();const storage=page.getByRole('region',{name:'Task storage',exact:true});
 await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
 await expect(storage.getByRole('alert')).toBeVisible();
 console.log(JSON.stringify({observedPage:await page.locator('body').innerText(),after:await facts()}));
 await expect(page.locator('body')).toContainText(/Permission denied|permission/i);
 const after=await facts();assert.equal(after.managedPathExists,false);assert.deepEqual(after.files,before.files);assert.equal(writes.length,1);assert.ok(after.items.every(row=>row.status!=='ready'));assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,realOSPermissionDenial:true,workspaceFilesUnchanged:true,managedPathCreated:false,readyAllocations:0,limits:['Controlled workspace-entry prerequisite','Human UI intent is independent of agent file-policy admission; six backend policy cases qualify that separate boundary.']}));
}finally{try{await browser?.close()}finally{if(fixture.exitCode===null){const done=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await done}catch{}finally{clearTimeout(timer)}}}}
