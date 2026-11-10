import {spawn} from 'node:child_process';
import {mkdir,writeFile,readFile,stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-managed-resources';
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/publishing_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;
try{
 const ready=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value)}}catch{}})});
 browser=await chromium.launch({headless:true});
 page=await browser.newPage({viewport:{width:1100,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(ready.url);await page.getByRole('textbox',{name:'Message Amplifier'}).waitFor();
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=await page.evaluate(()=>window.amplifier.getState()),workspace=state.workspaces[0],sid=ready.sessionId;
 const built=await action('publishing.build',{sessionId:sid,siteId:'managed-resources-fixture',sourcePath:'dist',requestId:'managed-build'});
 assert.equal(built.accepted,true);const release=built.result;
 const started=await action('publishing.preview',{sessionId:sid,releaseId:release.id,requestId:'managed-preview'});
 assert.equal(started.accepted,true);
 await action('view.update',{patch:{workSurface:'workspace',workWorkspaceId:workspace.id,workWorkspaceTab:'details'}});
 await page.getByText('Managed resources',{exact:true}).click();
 const panel=page.getByRole('region',{name:'Managed resources'});
 await expect(panel.getByText('Static preview · running',{exact:true})).toBeVisible();
 const observed=await action('workspace.resources.managed',{workspaceId:workspace.id,sessionId:sid});
 const preview=observed.result.publishing.items[0];
 await expect(panel.getByText('Bound port: '+preview.port,{exact:true})).toBeVisible();
 await expect(panel.getByText('Served directory: '+preview.releaseDirectory,{exact:true})).toBeVisible();
 const fetched=await page.request.get(preview.url);assert.match(await fetched.text(),/First release/);
 await expect(panel.getByRole('heading',{name:'Task storage',exact:true})).toBeVisible();
 const storage=panel.getByRole('region',{name:'Task storage',exact:true});
 await storage.getByLabel('Storage label (optional)',{exact:true}).fill('Keep resource draft');
 await page.reload();await page.getByText('Managed resources',{exact:true}).click();
 await expect(storage.getByLabel('Storage label (optional)',{exact:true})).toHaveValue('Keep resource draft');
 const commands=[];let loseResponse=true;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='workspace.resources.provision'){
   commands.push(body);
   if(loseResponse){loseResponse=false;await route.fetch();await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Fixture lost response after allocation'})});return}
  }
  await route.continue();
 });
 await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
 await expect(storage.getByRole('button',{name:'Check the same storage change',exact:true})).toBeVisible();
 await page.reload();await page.getByText('Managed resources',{exact:true}).click();
 await storage.getByRole('button',{name:'Check the same storage change',exact:true}).click();
 await expect(storage.getByText('Keep resource draft · ready',{exact:true})).toBeVisible();
 assert.equal(commands.length,2);assert.deepEqual(commands[0],commands[1]);
 for(const [kind,label] of [['browser-profile','Profile bytes'],['sqlite-database','Local database']]){
  await storage.getByLabel('Storage type',{exact:true}).selectOption(kind);
  await storage.getByLabel('Storage label (optional)',{exact:true}).fill(label);
  await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
  await expect(storage.getByText(label+' · ready',{exact:true})).toBeVisible();
 }
 const allocations=(await action('workspace.resources.read',{workspaceId:workspace.id,sessionId:sid})).result.items;
 assert.equal(allocations.length,3);assert.equal(new Set(allocations.map(row=>row.path)).size,3);
 assert.ok(allocations.every(row=>row.path.startsWith(workspace.path+'/.amplifier/task-storage/')&&row.placement==='working-folder'&&!row.fileAccessChanged));
 for(const row of allocations){assert.ok((await stat(row.path)).isDirectory()||row.kind==='sqlite-database');assert.equal(row.access.status,'unknown')}
 const scratch=allocations.find(row=>row.kind==='scratch-directory');
 await writeFile(scratch.path+'/retained.txt','Preserve unknown user content');
 const ownRow=storage.locator('[data-task-resource="'+scratch.id+'"]');
 await ownRow.getByText('Ownership and access details',{exact:true}).click();
 await expect(storage.getByRole('heading',{name:'Task storage',exact:true})).toBeVisible();
 await ownRow.getByRole('button',{name:'Retire and retain files',exact:true}).click();
 await expect(storage.getByText('Keep resource draft · retired',{exact:true})).toBeVisible();
 assert.equal(await readFile(scratch.path+'/retained.txt','utf8'),'Preserve unknown user content');
 await page.screenshot({path:output+'/desktop.png'});
 const peer=await action('session.create',{workspace:workspace.path,title:'Unallocated peer',select:false});
 const peerId=peer.sessionId;assert.ok(peerId);
 assert.equal((await page.evaluate(()=>window.amplifier.getState())).sessions.some(row=>row.id===peerId),false,'Other owners come from the explicit picker query');
 await panel.getByRole('button',{name:'Refresh chats',exact:true}).click();
 await panel.getByLabel('Resources for chat',{exact:true}).selectOption(peerId);
 await expect(panel.getByText('No local publishing listeners on this page.',{exact:true})).toBeVisible();
 await expect(storage.getByText('No task storage on this page.',{exact:true})).toBeVisible();
 await storage.getByRole('button',{name:'Create task storage',exact:true}).click();
 await expect(storage.getByText('Scratch folder · ready',{exact:true})).toBeVisible();
 const peerStorage=(await action('workspace.resources.read',{workspaceId:workspace.id,sessionId:peerId})).result.items;
 assert.equal(peerStorage.length,1);assert.ok(!allocations.some(row=>row.path===peerStorage[0].path));
 await panel.getByLabel('Resources for chat',{exact:true}).selectOption(sid);
 await expect(panel.getByText('Static preview · running',{exact:true})).toBeVisible();
 await action('publishing.stop',{sessionId:sid,siteId:'managed-resources-fixture',expectedRevision:0,requestId:'managed-stop'});
 await panel.getByRole('button',{name:'Refresh managed resources',exact:true}).click();
 await expect(panel.getByText('Static preview · stopped',{exact:true})).toBeVisible();
 await expect(panel.getByRole('link',{name:'Open local preview'})).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No horizontal clipping');
 await page.screenshot({path:output+'/mobile.png'});
 const inspection=await page.request.get(ready.url+'/fixture').then(response=>response.json());
 assert.equal(inspection.sent.length,0);assert.deepEqual(errors,[]);
 const report={status:'passed',scenarios:15,evidence:'Real local publisher, HTTP listener and task scratch/profile/SQLite allocation; same-command lost-response recovery, retained draft/files and distinct task owners; scripted conversation runtime; no model input.'};
 await writeFile(output+'/report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error}
finally{await browser?.close();fixture.kill('SIGTERM')}
