import {clearComposer} from './composer-test-helpers.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
// Real packaged UI, isolated storage, and synthetic runtime. No model calls.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],{stdio:['ignore','pipe','inherit']});
let browser;
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/amplifier-chat-controls';
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true,...(process.env.DTU_CHROMIUM_SINGLE_PROCESS?{args:['--no-zygote','--single-process','--disable-gpu']}:{ }),...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write'],extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')calls.push(request.postDataJSON())});
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const inspect=async()=>await(await page.request.get(url+'/fixture')).json();
 // Ordinary browser frames contain selected context, not catalog membership.
 // A two-row bounded page proves both the exact owner and no extra creation.
 const expectCatalog=async ids=>{const response=await page.request.get(url+'/api/sessions?kind=root&limit=2',{headers:{'X-Amplifier-Client':(await state()).client.id}});assert.equal(response.status(),200);const pageValue=await response.json();assert.deepEqual(pageValue.items.map(row=>row.id).sort(),[...ids].sort());assert.equal(pageValue.nextOffset,null)};
 await page.goto(url);await page.locator('.a-adaptive-home').getByRole('button',{name:'New chat',exact:true}).click();const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await action('view.update',{patch:{navPinned:true}});
 const launcher=page.getByRole('button',{name:'New chat',exact:true});await expect(launcher).toHaveCount(1);
 assert.equal(await page.locator('.a-top [data-action="session.create"],.a-top [data-action="session.draft"]').count(),0);
 await expect(page.getByRole('button',{name:'Open canvas',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Chat',exact:true})).toHaveCount(0);
 const originalWorkspace=(await state()).settings.workspace;
 await expect(page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings'})).toContainText('first');
 await page.getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await showComposerOption(page,'bundle');
 await expect(page.getByLabel('Registered root bundles',{exact:true}).locator('option:checked')).toHaveText('Work');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 assert.deepEqual((await state()).view.newSessionDraft?.selection||{},{});

 await launcher.click();await launcher.click();assert.equal((await state()).sessions.length,0);await expectCatalog([]);
 await expect(page.getByText('Chat settings',{exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Notify me when ready',exact:true})).toHaveCount(0);
 await page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true}).click();
 await showComposerOption(page,'bundle');
 await page.getByLabel('Registered root bundles',{exact:true}).selectOption('work');
 await showComposerOption(page,'model');
 await page.locator('select#chat-model').selectOption('chosen-model');
 await showComposerOption(page,'effort');
 await page.locator('#chat-effort').press('End');
 await page.getByRole('button',{name:'Close chat options',exact:true}).click();
 await page.getByRole('button',{name:'Choose workspace',exact:true}).click();
 await page.getByRole('button',{name:'Use existing folder',exact:true}).click();
 await page.getByRole('textbox',{name:/Folder on/}).fill(originalWorkspace+'/../');
 await page.getByRole('button',{name:'Use folder',exact:true}).click();
 await expect(page.getByRole('dialog',{name:'Choose a workspace',exact:true})).toHaveCount(0);
 await expect.poll(async()=>(await state()).view.newSessionDraft.workspace).not.toBe(originalWorkspace);
 await composer.fill('Do the planned work');await expect(composer).toHaveDraft('Do the planned work');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Use these notes')});await page.getByRole('button',{name:'Remove notes.txt'}).waitFor();
 assert.equal((await state()).sessions.length,0);assert.deepEqual((await inspect()).sent,[]);assert.deepEqual((await inspect()).started,[]);
 await page.reload();await expect(composer).toHaveDraft('Do the planned work');await expect(page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings'})).toContainText('chosen-model');assert.equal((await state()).view.newSessionDraft.selection.effort,'high');assert.equal((await state()).view.newSessionDraft.bundle,'work');await expect(page.getByRole('button',{name:'Remove notes.txt'})).toBeVisible();
 await page.screenshot({path:out+'/new-chat-desktop.png'});
 await action('view.update',{patch:{navPinned:false,navExpanded:false}});
 for(const width of [390,320]){await page.setViewportSize({width,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:out+`/new-chat-${width}.png`});
 await page.locator('.a-composer').getByRole('button',{name:'Model and reasoning settings',exact:true}).click();const panel=page.getByRole('dialog',{name:'Chat options',exact:true});await expect(panel).toBeVisible();
 const assertPanelFits=async()=>{const geometry=await panel.evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}});
 assert.ok(geometry.x>=0&&geometry.y>=0&&geometry.right<=width&&geometry.bottom<=844,JSON.stringify(geometry))};
 await assertPanelFits();
 await showComposerOption(page,'model');await expect(panel.getByRole('combobox',{name:'Conversation model',exact:true})).toBeVisible();await assertPanelFits();
 await showComposerOption(page,'bundle');await expect(panel.getByLabel('Registered root bundles',{exact:true})).toBeVisible();await assertPanelFits();
 await panel.getByRole('button',{name:'Back to chat options',exact:true}).click();
 await expect(panel.locator('input:not([type=range])')).toHaveCount(0);await page.screenshot({path:out+`/compact-options-${width}.png`});await panel.getByRole('button',{name:'Close chat options',exact:true}).click();
 }
 await page.setViewportSize({width:1280,height:900});await action('view.update',{patch:{navPinned:true}});
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();
 let current=await state();assert.equal(current.sessions.length,1);const sid=current.selectedSessionId,chat=current.sessions.find(row=>row.id===sid);assert.equal(chat.title,'Do the planned work');assert.equal(chat.bundle,'work');assert.equal(chat.selection.model,'chosen-model');assert.notEqual(chat.workspace,originalWorkspace);
 const submitted=(await inspect()).sent;assert.equal(submitted.length,1);assert.equal(submitted[0].selection.model,'chosen-model');assert.equal(submitted[0].attachments[0].name,'notes.txt');
 await expect(page.getByRole('button',{name:'Open canvas',exact:true})).toBeEnabled();
 const openChatPage=async name=>{await page.getByRole('button',{name:'Chat',exact:true}).click();await page.getByRole('button',{name,exact:true}).click()};
 await openChatPage('Advanced');await page.getByRole('button',{name:/^Work & automation/}).click();await expect(page.getByLabel('Goal condition')).toBeVisible();await expect(page.getByLabel('Mode',{exact:true})).toBeVisible();await page.screenshot({path:out+'/chat-work-controls.png'});await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await openChatPage('Chat info');const details=page.getByRole('dialog',{name:'Chat info',exact:true});await expect(details.getByRole('button',{name:'Copy session id',exact:true})).toBeVisible();await details.getByRole('button',{name:'Copy session id',exact:true}).click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),sid);await expect(details.getByLabel('Goal condition')).toHaveCount(0);await page.screenshot({path:out+'/header-chat-info.png'});await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await openChatPage('Share or save');const share=page.getByRole('dialog',{name:'Share or save',exact:true});await share.getByRole('button',{name:'Preview Markdown',exact:true}).click();await expect(share.getByRole('button',{name:'Download Markdown',exact:true})).toBeVisible();const downloaded=page.waitForEvent('download');await share.getByRole('button',{name:'Download Markdown',exact:true}).click();const file=await downloaded;assert.match(file.suggestedFilename(),/\.md$/);await page.getByRole('button',{name:'Close panel',exact:true}).click();
 const activity=async(status,workers=[])=>{assert.equal((await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status,workers}})).status(),200)};
 await activity('working');
 for(const scheme of ['light','dark']){await action('view.update',{patch:{scheme,navPinned:false,navExpanded:false}});for(const width of [390,320]){await page.setViewportSize({width,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await expect(page.getByRole('button',{name:'Chat',exact:true})).toBeInViewport();await expect(page.getByRole('button',{name:'Stop response',exact:true})).toBeInViewport();await page.screenshot({path:out+`/composer-${scheme}-${width}.png`})}}
 await page.setViewportSize({width:1280,height:900});await action('view.update',{patch:{scheme:'light',navPinned:true}});const stop=page.getByRole('button',{name:'Stop response',exact:true});await expect(stop).toHaveCount(1);const box=await stop.boundingBox();await composer.fill('A correction');const send=page.getByRole('button',{name:'Send message',exact:true});await expect(send).toBeEnabled();assert.deepEqual(await send.boundingBox(),box);await clearComposer(composer);await expect(stop).toBeEnabled();assert.deepEqual(await stop.boundingBox(),box);
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'correction.txt',mimeType:'text/plain',buffer:Buffer.from('Next attachment')});await page.getByRole('button',{name:'Remove correction.txt'}).waitFor();await expect(send).toBeEnabled();await page.getByRole('button',{name:'Remove correction.txt'}).click();await expect(stop).toBeEnabled();await stop.click();await expect(page.getByRole('button',{name:'Start voice call',exact:true})).toBeEnabled();assert.deepEqual((await inspect()).stopped,[sid]);
 await activity('idle',[{id:'worker',status:'running',title:'Background work'}]);await expect(stop).toBeEnabled();await activity('idle');
 // The damaged source stays referenced and the drawer still opens.
 await action('canvas.show',{kind:'markdown',title:'Recoverable artifact',content:'# Saved artifact'});const aid=(await state()).canvas.id;await action('canvas.close');assert.equal((await page.request.post(url+'/fixture/damage',{data:{artifactId:aid}})).status(),200);await page.getByRole('button',{name:'Open canvas',exact:true}).click();await expect(page.getByRole('complementary',{name:'Canvas',exact:true})).toBeVisible();await expect(page.getByText('The saved artifact source is unavailable.',{exact:true}).first()).toBeVisible();assert.equal((await state()).canvas.id,aid);assert.equal((await state()).selectedSessionId,sid);await page.screenshot({path:out+'/unavailable-artifact.png'});await action('canvas.close');
 await composer.fill('Keep this existing chat draft');await launcher.click();await expect(page.getByRole('region',{name:'New chat',exact:true})).toBeVisible();await expect(composer).toHaveDraft('');await expectCatalog([sid]);await composer.fill('A different new draft');await action('session.select',{id:sid});await expect(composer).toHaveDraft('Keep this existing chat draft');await launcher.click();await expect(composer).toHaveDraft('A different new draft');
 assert.deepEqual(errors,[]);assert.equal(calls.filter(row=>row.action==='session.create').length,1);
 console.log('New chat browser passed: one launcher, uncommitted configurable draft, reload and attachments, exact first submission, composer controls, header identity/export, send/stop transitions, damaged canvas, draft isolation, desktop/mobile.');
}finally{await browser?.close();fixture.kill()}
