import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {StandardUxEvidence} from './standard-ux-evidence.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_OUTPUT;
if(!out)throw Error('Owned AMPLIFIER_TEST_OUTPUT required');
await mkdir(out,{recursive:true});
const evidence=new StandardUxEvidence(out,{source:'integration-e27593c67 plus this test fixture',state:'disposable synthetic managed files/history',runtime:'synthetic status admission only; no worker execution',timings:'shared-load functional observations'});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/managed_cleanup_ui_server.py']);
let browser,page,stderr='';fixture.stderr.on('data',b=>stderr+=b);
try{
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout '+stderr)),25000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+' '+stderr))});fixture.stdout.on('data',b=>{text+=b;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
 page=await browser.newPage({viewport:{width:1400,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(7000);evidence.page=page;
 const errors=[],actions=[];page.on('pageerror',e=>errors.push(e.message));
 const command=request=>{const body=request.postDataJSON();return body?.action==='shell.command'?body.args:body};
 page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(command(request))});
 const info=async()=>(await page.request.get(boot.url+'/api/fixture/managed-cleanup')).json();
 const status=async(target,status)=>{const response=await page.request.post(boot.url+'/api/fixture/managed-cleanup/state',{data:{target,status}});assert.ok(response.ok())};
 const probe=async(id,flows,run)=>{const row=await evidence.probe(id,flows,async()=>{await run();return evidence.snapshot(page,id,'#amp-one')});if(row.status!=='passed')throw Error('Stopped after '+id)};
 const click=(label,control)=>evidence.action(label,()=>control.click());
 await page.goto(boot.url);await showFixtureConversation(page,{navPinned:true});const composer=page.getByRole('textbox',{name:'Message Amplifier',exact:true});await expect(composer).toBeVisible();
 const initial=await info(),pinned=page.locator('[data-sidebar-section="pinned"]');
 await expect(pinned.locator('[data-session-id]')).toHaveCount(5);await composer.fill('Selected chat draft stays local');
 const openDelete=async record=>{const row=pinned.locator(`[data-session-id="${record.id}"]`);if(!await row.isVisible())await click('Open navigation',page.getByRole('button',{name:'Open navigation',exact:true}));await row.hover();await click('Open exact chat row actions',row.getByRole('button',{name:'Details and actions for '+record.title,exact:true}));await click('Delete managed chat',page.getByRole('dialog',{name:'Details for '+record.title,exact:true}).getByRole('button',{name:'Delete '+record.title,exact:true}));};
 const review=page.locator('.a-nav-form .a-chat-delete:visible'),remove=review.getByRole('button',{name:'Delete permanently',exact:true});
 await probe('managed-delete-review-cancel-and-unknown-reconcile',['nav.delete-managed'],async()=>{
  const previews=()=>actions.filter(c=>c.action==='session.deletePreview').length;
  await openDelete(initial.records.delete);await expect(remove).toBeEnabled();assert.equal(previews(),1,'One editor owns the initial review token');await expect(review).toContainText('This cannot be undone.');await expect(review).toContainText(initial.records.delete.title);await expect(review).toContainText('saved conversation');
  await click('Reveal exact delete identity',review.getByText('Exact data being removed',{exact:true}));await expect(review.locator('code')).toHaveText(initial.records.delete.id);
  assert.deepEqual((await info()).records,initial.records);await click('Cancel permanent deletion',review.getByRole('button',{name:'Keep chat',exact:true}));assert.deepEqual((await info()).records,initial.records);assert.equal(actions.filter(c=>c.action==='session.delete').length,0);
  await openDelete(initial.records.delete);await expect(remove).toBeEnabled();assert.equal(previews(),2,'Reopening creates exactly one fresh preview');let lost=false,accepted;
  await page.route('**/api/actions',async route=>{const c=command(route.request());if(!lost&&c.action==='session.delete'){lost=true;const response=await route.fetch();accepted=await response.json();await writeFile(out+'/delete-receipt.json',JSON.stringify(accepted,null,2));if(accepted.accepted===false){await route.fulfill({response});return}await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Synthetic acknowledgement lost after completed deletion'})})}else await route.continue()});
  await click('Confirm reviewed managed deletion once',remove);await expect(review.getByRole('alert')).toBeVisible();assert.equal(accepted.accepted,true);await expect(remove).toHaveCount(0);
  await expect.poll(async()=>(await info()).records.delete.fileSha).toBe(null);const after=await info();assert.equal(after.records.delete.historySha,null);assert.equal(after.records.delete.present,false);
  for(const key of ['selected','active','main','old'])assert.deepEqual(after.records[key],initial.records[key]);
  await click('Dismiss unknown receipt without replay',review.getByRole('button',{name:'Keep chat',exact:true}));await expect(pinned.locator(`[data-session-id="${initial.records.delete.id}"]`)).toHaveCount(0);await expect(composer).toHaveDraft('Selected chat draft stays local');
  await page.unrouteAll({behavior:'wait'});await page.reload();await expect(pinned.locator('[data-session-id]')).toHaveCount(4);await expect(pinned.locator(`[data-session-id="${initial.records.delete.id}"]`)).toHaveCount(0);await expect(composer).toHaveDraft('Selected chat draft stays local');
  assert.equal(actions.filter(c=>c.action==='session.delete').length,1);assert.equal((await info()).deletions.find(r=>r.id===initial.records.delete.id).phase,'done');
 });
 await probe('main-browser-delete-has-one-active-review-owner',['nav.delete-managed'],async()=>{
  await click('Open main chat search',page.getByRole('button',{name:'Search chats',exact:true}));
  const main=page.locator('.a-work-browser');await main.getByRole('searchbox',{name:'Search chats',exact:true}).fill(initial.records.main.title);
  const row=main.locator(`[data-session-id="${initial.records.main.id}"]`);await expect(row).toBeVisible();await row.hover();
  await click('Open main browser row actions',row.getByRole('button',{name:'Details and actions for '+initial.records.main.title,exact:true}));
  const before=actions.filter(c=>c.action==='session.deletePreview').length;
  await click('Review main browser managed deletion',page.getByRole('dialog',{name:'Details for '+initial.records.main.title,exact:true}).getByRole('button',{name:'Delete '+initial.records.main.title,exact:true}));
  await expect(main.locator('.a-chat-delete')).toBeVisible();await expect(remove).toBeEnabled();assert.equal(actions.filter(c=>c.action==='session.deletePreview').length,before+1);
  await click('Confirm main browser managed deletion',remove);await expect.poll(async()=>(await info()).records.main.fileSha).toBe(null);assert.equal((await info()).records.main.historySha,null);await expect(row).toHaveCount(0);
  await click('Return to original selected chat',pinned.locator(`[data-session-id="${initial.records.selected.id}"] [data-navigation-select]`));await expect(composer).toHaveDraft('Selected chat draft stays local');
 });
 await probe('active-managed-chat-refuses-review-and-late-confirmation',['nav.delete-managed'],async()=>{
  await openDelete(initial.records.active);await expect(review.getByRole('alert')).toContainText('active work');await expect(remove).toHaveCount(0);assert.deepEqual((await info()).records.active,initial.records.active);
  await status('active','idle');await click('Review now idle managed chat',review.getByRole('button',{name:'Review again',exact:true}));await expect(remove).toBeEnabled();await status('active','working');
  await click('Reject newly active owner at confirmation',remove);await expect(review.getByRole('alert')).toContainText('active work');await expect(remove).toHaveCount(0);assert.deepEqual((await info()).records.active,initial.records.active);await click('Keep protected managed chat',review.getByRole('button',{name:'Keep chat',exact:true}));
 });
 const openPrivacy=async()=>{const settings=page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/});if(!await settings.isVisible())await click('Open navigation for Settings',page.getByRole('button',{name:'Open navigation',exact:true}));await click('Open Settings',settings);await click('Open Privacy and files',page.locator('[data-settings-section="privacy"]'))};
 await probe('active-workspace-preserved-before-and-after-preview',['workspace.cleanup'],async()=>{
  await openPrivacy();await click('Open Storage',page.locator('.a-settings-page-content:not([hidden]) [data-settings-destination="storage"]'));
  const storage=page.getByRole('region',{name:'Storage usage'});await click('Review workspace files',storage.getByRole('button',{name:/^Workspace files /}));await storage.getByRole('searchbox',{name:'Filter storage'}).fill('Protected cleanup workspace');await click('Open exact protected workspace',storage.getByRole('button',{name:'Protected cleanup workspace',exact:true}));
  const cleanup=storage.getByRole('region',{name:'Workspace file cleanup'}),preview=cleanup.getByRole('button',{name:'Review workspace file cleanup',exact:true}),confirm=cleanup.getByRole('button',{name:'Permanently delete workspace files',exact:true});
  await click('Refuse active workspace review',preview);await expect(cleanup.getByRole('alert')).toContainText('active work');await expect(confirm).toHaveCount(0);assert.equal((await info()).workspace.fileSha,initial.workspace.fileSha);
  await status('workspace','idle');await click('Preview idle workspace files',preview);await expect(cleanup).toContainText('Git history and uncommitted work');await cleanup.getByRole('textbox',{name:'Confirm workspace folder path'}).fill(initial.workspace.path);await expect(confirm).toBeEnabled();await status('workspace','working');
  await click('Refuse active work arriving after preview',confirm);await expect(cleanup.getByRole('alert')).toContainText('active work');assert.equal((await info()).workspace.fileSha,initial.workspace.fileSha);await click('Close Settings after refusal',page.getByRole('button',{name:'Close panel',exact:true}));
 });
 await probe('aged-cleanup-keeps-active-selected-and-native-history',['privacy.cleanup'],async()=>{
  await openPrivacy();await click('Open Saved work',page.locator('.a-settings-page-content:not([hidden]) [data-settings-destination="history"]'));
  const history=page.locator('.a-settings-page-content:not([hidden])');await click('Preview old conversation cleanup',history.getByRole('button',{name:'Preview cleanup',exact:true}));
  await expect(history).toContainText('inactive conversations eligible. Your selected conversation is kept.');const preview=(await info()).cleanupPreview;assert.ok(preview.sessions.some(r=>r.id===initial.records.old.id));assert.ok(preview.sessions.every(r=>![initial.records.selected.id,initial.records.active.id,initial.workspace.owner].includes(r.id)));
  const before=await info();await click('Explicitly clean eligible old list entries',history.getByRole('button',{name:'Clean up these conversations',exact:true}));await expect.poll(async()=>(await info()).cleanupPreview?.applied).toBe(true);
  const after=await info();for(const key of ['selected','active'])assert.deepEqual(after.records[key],before.records[key]);assert.equal(after.records.old.historySha,before.records.old.historySha);assert.equal(after.records.old.fileSha,before.records.old.fileSha);assert.equal(after.records.old.present,false);assert.equal(after.workspace.fileSha,before.workspace.fileSha);
  assert.deepEqual(actions.filter(c=>['conversation.send','session.warm'].includes(c.action)),[]);assert.deepEqual(errors,[]);
 });
 console.log('PASS managed cleanup:5 actual UI probes, both deletion surfaces, exact deletion/cancel/lost acknowledgement, active before/after review and aged cleanup protection.');
}finally{
 try{await browser?.close()}finally{if(fixture.exitCode===null){const end=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await end}finally{clearTimeout(timer)}}await writeFile(out+'/fixture.stderr',stderr);await evidence.save()}
}
