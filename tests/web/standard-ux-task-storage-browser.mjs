import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),['-u',root+'tests/fixtures/task_storage_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1366,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page,{navPinned:true});await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const facts=()=>page.request.get(url+'/fixture/storage').then(response=>response.json());
 const original=await facts();
 // Controlled workspace-entry prerequisite; all storage operations use UI controls.
 await page.evaluate(workspaceId=>window.amplifier.dispatch('view.update',{patch:{panel:null,setupJourney:'done',workSurface:'workspace',workWorkspaceId:workspaceId,workWorkspaceTab:'details'}}),original.workspaceId);
 await page.getByText('Managed resources',{exact:true}).click();
 const storage=page.getByRole('region',{name:'Task storage',exact:true});
 await expect(storage.getByRole('button',{name:'Create task storage',exact:true})).toBeVisible();
 assert.deepEqual((await facts()).items,[]);
 await page.evaluate(()=>{window.fixtureSetItem=Storage.prototype.setItem;Storage.prototype.setItem=()=>{throw Error('Storage blocked')}});
 await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
 await expect(storage.getByRole('alert')).toContainText('Nothing was sent.');
 assert.deepEqual((await facts()).items,[],'Browser receipt failure creates no server allocation');
 await page.evaluate(()=>{Storage.prototype.setItem=window.fixtureSetItem;delete window.fixtureSetItem});
 let drop=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='workspace.resources.provision'){
   writes.push(body);
   if(drop){drop=false;await route.fetch();await route.abort('connectionreset');return}
  }
  await route.continue();
 });
 await storage.getByLabel('Storage label (optional)',{exact:true}).fill('Retained scratch');
 await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
 await expect(storage.getByRole('button',{name:'Check the same storage change',exact:true})).toBeEnabled();
 await page.reload();await page.getByText('Managed resources',{exact:true}).click();
 await storage.getByRole('button',{name:'Check the same storage change',exact:true}).click();
 await expect(storage.getByRole('button',{name:'Check the same storage change',exact:true})).toHaveCount(0);
 assert.equal(writes.length,2);assert.equal(writes[0].id,writes[1].id);assert.deepEqual(writes[0].args,writes[1].args);
 const first=(await facts()).items;assert.equal(first.length,1);assert.equal(first[0].sessionId,original.sessionId);
 assert.ok(first[0].path.startsWith(original.workspace+'/.amplifier/task-storage/'));
 assert.equal(first[0].fileAccessChanged,false);
 await page.request.post(url+'/fixture/storage-marker',{data:{id:first[0].id}});
 await storage.getByRole('button',{name:'Retire and retain files',exact:true}).click();
 await expect(storage.getByText('Retained scratch · retired',{exact:true})).toBeVisible();
 const retired=(await facts()).items[0];assert.equal(retired.exists,true);assert.equal(retired.marker,'Synthetic contents must survive retirement');
 for(const kind of ['browser-profile','sqlite-database']){
  await storage.getByLabel('Storage type',{exact:true}).selectOption(kind);
  await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
  await expect.poll(async()=>(await facts()).items.some(row=>row.kind===kind&&row.status==='ready'&&row.exists)).toBe(true);
 }
 await storage.getByRole('button',{name:'Refresh task storage',exact:true}).click();
 await expect(storage.locator('[data-task-resource]')).toHaveCount(3);
 await storage.getByText('Ownership and access details',{exact:true}).first().click();
 await expect(storage.locator('details[open]')).toContainText(original.sessionId);
 await page.setViewportSize({width:390,height:844});await storage.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,realFiles:true,allocationKinds:3,lostAckSameId:true,blockedBrowserStorageNoEffect:true,retirementRetainsContents:true,ownershipVisible:true,narrowNoOverflow:true,limits:['Controlled workspace-entry prerequisite','Native selectOption is not a touch/keyboard picker gesture','No real environment/provider started','File-policy denial is qualified separately at the agent admission boundary']}));
}finally{await browser?.close();fixture.kill('SIGTERM')}
