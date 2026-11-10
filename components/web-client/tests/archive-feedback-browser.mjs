import {spawn} from 'node:child_process';
import {prepareCanvasReaderInput,recordCanvasInputFailure} from './canvas-reader-input.mjs';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage,settingsButton} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';

const root=process.env.AMPLIFIER_TEST_ROOT||fileURLToPath(new URL('../../',import.meta.url));
const output=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/archive-feedback';
await mkdir(output,{recursive:true});
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'/.venv/bin/python',[root+'/tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stopFixture=ownFixture(fixture,{label:'Archive feedback fixture'});
let browser,page,release,releaseNavigation,failure;
try{
 const url=await new Promise((resolve,reject)=>{
  let text='';const timer=setTimeout(()=>reject(Error('Fixture startup timed out')),20000);
  fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});
  fixture.once('error',reject);fixture.once('exit',code=>reject(Error('Fixture exited '+code)));
 });
 browser=await chromium.launch({headless:true,args:JSON.parse(process.env.AMPLIFIER_CHROMIUM_ARGS||'[]')});
 const context=await browser.newContext({viewport:{width:1200,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();const peer=await context.newPage(),errors=[];
 for(const p of [page,peer]){p.on('pageerror',e=>errors.push(e.message));await p.goto(url);await p.waitForFunction(()=>window.amplifier?.dispatch)}
 const action=(name,args={},p=page)=>p.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const workspace=(await state()).workspaces.find(row=>row.path);
 await action('session.create',{title:'Archive example',workspace:workspace.path,location:{kind:'workspace'}});
 const sid=(await state()).selectedSessionId;
 await action('session.create',{title:'Other example',workspace:workspace.path,location:{kind:'workspace'}});
 const other=(await state()).selectedSessionId;
 await action('session.select',{id:sid});await action('session.select',{id:sid},peer);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Draft is preserved');
 await action('attachment.add',{sessionId:sid,name:'draft.txt',base64:Buffer.from('Synthetic attachment').toString('base64')});
 await action('view.update',{patch:{navPinned:true}});
 const before=await page.request.get(url+'/fixture').then(r=>r.json());
 let mode='hold',archiveCalls=0,restoreCalls=0,holdNavigation=false,workspaceRequests=0;
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action==='view.update'&&body.args?.patch?.workSurface==='workspace')workspaceRequests++;
  if(holdNavigation&&body?.action==='view.update'&&body.args?.patch?.workSurface==='chat'){
   holdNavigation=false;await new Promise(resolve=>{releaseNavigation=resolve});
  }
  if(body?.action==='session.restore')restoreCalls++;
  if(body?.action!=='session.archive'){await route.continue();return}
  archiveCalls++;
  if(mode==='reject'||mode==='reject200'){await route.fulfill({status:mode==='reject'?403:200,contentType:'application/json',body:JSON.stringify({accepted:false,error:'Synthetic archive rejection'})});return}
  if(mode==='unknown'){await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'Synthetic uncertain transport'})});return}
  const response=await route.fetch();
  if(mode==='hold')await new Promise(resolve=>{release=resolve});
  await route.fulfill({response});
 });
 async function headerArchive(){
  await page.getByRole('button',{name:'Chat',exact:true}).click();
  await page.getByRole('group',{name:'Chat',exact:true}).getByRole('button',{name:'Archive',exact:true}).click();
 }
 await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Archiving…');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeFocused();
 await page.screenshot({path:output+'/pending.png'});
 await expect.poll(()=>!!release).toBe(true);release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 await expect(page.locator('[data-part="work-browser"] h1')).toHaveText(workspace.name);
 await page.waitForFunction(()=>document.activeElement?.matches('[data-part="work-browser"] h1'));
 assert.equal((await state()).view.workSurface,'workspace');
 assert.equal((await state()).selectedSessionId,sid); // No replacement chat opens.
 const peerState=await peer.evaluate(()=>window.amplifier.getState());
 assert.equal(peerState.selectedSessionId,sid);assert.equal(peerState.view.workSurface||'chat','chat');
 await page.screenshot({path:output+'/archived-desktop.png'});
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal(restoreCalls,1);assert.equal((await state()).view.workSurface,'workspace');
 await action('session.select',{id:sid});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Draft is preserved');
 await expect(page.locator('[data-part="composer"]')).toContainText('draft.txt');
 // A renderer's edit exists only in component memory, not a saved artifact.
 await action('view.update',{patch:{canvasControlsPinned:true,canvasControlsExpanded:true}});
 await action('canvas.show',{kind:'markdown',title:'Editable archive example',content:'# Original source'});
 const manifest={id:'example.archive-dirty-editor',label:'Unsaved archive editor',version:'1.0.0',apiVersion:'1.0',profile:'trusted-native-renderer-v1',stateSchema:'canvas-view-v1',resourceKinds:['markdown'],capabilities:['canvas.resource.read','canvas.view.update']};
 const source='export default ({React})=>function Editor({host}){const [text,setText]=React.useState("");return React.createElement("label",null,"Unsaved archive edit",React.createElement("input",{value:text,onChange:event=>{setText(event.target.value);host.setDirty(true)}}))}';
 const digest=(await action('shell.packages.stage',{manifest,source})).result.digest;
 const qualification=(await action('shell.packages.validate',{digest})).result;assert.equal(qualification.status,'passed',JSON.stringify(qualification));
 await page.getByRole('combobox',{name:'Open with',exact:true}).selectOption(digest);
 const canvasEdit=page.locator('[data-canvas-view="primary"]').getByLabel('Unsaved archive edit');
 await prepareCanvasReaderInput(page,{target:canvasEdit,label:'archive-editor',outputDir:output,expectCovered:true});
 await canvasEdit.fill('UNSAVED CANVAS ARCHIVE INPUT');
 const inspectViews=async()=>(await action('canvas.views.inspect')).result.views;
 await expect.poll(async()=>(await inspectViews()).find(v=>v.viewId==='primary')?.dirty).toBe(true);
 const viewTarget=v=>Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(key=>[key,v[key]]));
 const originalViews=(await inspectViews()).map(viewTarget);
 await headerArchive();await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='workspace');
 await expect(canvasEdit).toBeHidden();
 await action('session.select',{id:sid});
 await expect(canvasEdit).toHaveValue('UNSAVED CANVAS ARCHIVE INPUT');
 assert.deepEqual((await inspectViews()).map(viewTarget),originalViews);
 assert.equal((await state()).canvas.content,'# Original source');
 await page.screenshot({path:output+'/unsaved-canvas.png'});
 // Explicit fixture cleanup only after proving the input survived the round trip.
 await action('session.restore',{id:sid});
 await action('canvas.views.recover',viewTarget((await inspectViews()).find(v=>v.viewId==='primary')));
 await action('canvas.close');
 mode='reject';await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Synthetic archive rejection');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeFocused();
 mode='reject200';await headerArchive();
 await expect(page.locator('[data-archive-notice][role="alert"]')).toContainText('Synthetic archive rejection');
 assert.equal((await state()).view.workSurface||'chat','chat');
 mode='unknown';const unknownBefore=archiveCalls;await headerArchive();
 await expect(page.locator('[data-archive-notice][role="status"]')).toContainText('Could not confirm the change.');
 assert.equal(archiveCalls,unknownBefore+1);assert.equal((await state()).view.workSurface||'chat','chat');
 await expect(page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true})).toHaveCount(0);
 // Same A -> B -> A identity is not authority for an old archive to redirect.
 mode='hold';await headerArchive();await expect.poll(()=>!!release).toBe(true);
 await action('session.select',{id:other});await action('session.select',{id:sid});
 release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 assert.equal((await state()).view.workSurface||'chat','chat');
 await action('session.restore',{id:sid});
 // A queued browse must recheck user intent before sending its request.
 holdNavigation=true;
 await page.evaluate(()=>{window.heldNavigation=window.amplifier.dispatch('view.update',{patch:{workSurface:'chat'}})});
 await expect.poll(()=>!!releaseNavigation).toBe(true);
 const workspaceBefore=workspaceRequests;
 await headerArchive();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 // The confirmed mutation cannot be locked behind a queued, stalled browse.
 const restoresBefore=restoreCalls;
 await page.locator('[data-archive-notice]').getByRole('button',{name:'Undo',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat restored');
 assert.equal(restoreCalls,restoresBefore+1);
 await page.getByRole('button',{name:'Dismiss archive notification'}).click();
 await page.evaluate(()=>{window.newerNavigation=window.amplifier.dispatch('view.update',{patch:{workSurface:'chats'}})});
 releaseNavigation();releaseNavigation=null;
 await page.evaluate(()=>Promise.all([window.heldNavigation,window.newerNavigation]));
 assert.equal((await state()).view.workSurface,'chats');assert.equal(workspaceRequests,workspaceBefore);
 await action('session.restore',{id:sid});
 // A row action for another chat doesn't leave the current conversation.
 await action('session.select',{id:sid});await action('session.pin',{id:other,pinned:true});
 const otherRow=page.locator('[data-shell-instance=chats] [data-sidebar-section=pinned]').locator(`[data-session-id="${other}"]`);
 await otherRow.hover();
 await otherRow.getByRole('button',{name:'Details and actions for Other example',exact:true}).click();
 await page.getByRole('button',{name:'Archive Other example',exact:true}).click();
 await expect(page.locator('[data-archive-notice]')).toContainText('Chat archived');
 assert.equal((await state()).selectedSessionId,sid);assert.equal((await state()).view.workSurface||'chat','chat');
 // Public integration dispatch never invokes interactive navigation.
 await action('session.archive',{id:sid});
 assert.equal((await state()).view.workSurface||'chat','chat');await action('session.restore',{id:sid});
 await page.setViewportSize({width:390,height:844});
 await headerArchive();
 await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='workspace');
 await expect(page.locator('[data-part="work-browser"] h1')).toBeVisible();
 assert.ok(await page.locator('[data-archive-notice]').evaluate(el=>el.scrollWidth<=el.clientWidth));
 await page.screenshot({path:output+'/archived-mobile.png'});
 // Managed chats land on the existing full chat list rather than their private folder.
 await action('session.create',{title:'Managed example',location:{kind:'managed'}});
 await headerArchive();await page.waitForFunction(()=>window.amplifier.getState().view.workSurface==='chats');
 const chatSearch=page.getByRole('region',{name:'Chat search',exact:true});
 await expect(page.locator('[data-landing="chats"]').getByRole('heading',{name:'All chats',exact:true})).toBeVisible();
 await expect(chatSearch.getByRole('searchbox',{name:'Search chats',exact:true})).toBeVisible();
 // An archive receipt arriving after Settings opens must preserve its private draft.
 // Settings is modal: clicking the covered chat header is not a real user path.
 const managed=(await state()).selectedSessionId;
 await action('session.select',{id:managed});await action('session.restore',{id:managed});
 await page.setViewportSize({width:1440,height:1050});
 await expect(settingsButton(page)).toBeVisible();
 mode='hold';await headerArchive();await expect.poll(()=>!!release).toBe(true);
 await openSettingsPage(page,'providers');
 const settings=page.locator('.a-settings-experience[data-settings-page="providers"]');
 await settings.getByRole('button',{name:'Add provider',exact:true}).click();
 await settings.getByRole('button',{name:'Show advanced configuration',exact:true}).click();
 const settingsEdit=settings.locator('[id$="provider-config"]');
 await settingsEdit.fill('{"unsaved-archive-input":');
 release();release=null;mode='normal';
 await expect(page.locator('[data-archive-notice]:visible')).toContainText('Chat archived');
 await expect(settings).toBeVisible();await expect(settingsEdit).toHaveValue('{"unsaved-archive-input":');
 assert.equal((await state()).view.workSurface||'chat','chat');
 assert.ok(!JSON.stringify(await state()).includes('{"unsaved-archive-input":'));
 await page.screenshot({path:output+'/unsaved-settings.png'});
 const after=await page.request.get(url+'/fixture').then(r=>r.json());
 assert.deepEqual(after.started,before.started);assert.deepEqual(after.stopped,before.stopped);assert.deepEqual(after.sent,before.sent);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,archiveCalls,restoreCalls,pending:true,workspaceAndManagedDestination:true,undo:true,explicitRejection:true,http200Rejection:true,unknownOutcome:true,roundtripRace:true,queuedNavigationRace:true,stalledNavigationUndo:true,unsavedCanvasRetained:true,unsavedSettingsRetained:true,noncurrentRow:true,rawApiUnchanged:true,peerUnchanged:true,draftAndAttachmentPreserved:true,narrow:true,noRuntimeLifecycleChanges:true}));
}catch(error){failure=await recordCanvasInputFailure(page,error,{outputDir:output,label:'archive'});throw failure}
finally{
 release?.();releaseNavigation?.();
 await finishFixture({label:'Archive feedback fixture',close:[['Archive feedback browser',()=>browser?.close()]],stop:stopFixture,originalError:failure});
}
