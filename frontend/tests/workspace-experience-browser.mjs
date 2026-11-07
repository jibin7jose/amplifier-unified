import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import './composer-test-helpers.mjs';
// Real host/actions and production assets, isolated files, synthetic model only.
import {finishFixture} from './fixture-lifecycle.mjs';
import {startFixture} from './fixture-readiness.mjs';
import {fileURLToPath} from 'node:url';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const {base:url,stop}=await startFixture({
 command:process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),
 args:[fileURLToPath(new URL('../../tests/fixtures/empty_host_ui_server.py',import.meta.url)),'--chat-controls'],
 env:process.env,tag:'empty-host-ui',name:'Workspace experience',timeoutMs:20000,
});
let browser,failure;
const out=process.env.AMPLIFIER_TEST_OUTPUT||'/tmp/amplifier-workspace-experience';
try{
 await mkdir(out,{recursive:true});browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'});await composer.waitFor();
 await action('view.update',{patch:{navPinned:true}});
 await page.getByRole('button',{name:'New chat',exact:true}).click();
 const picker=page.getByRole('dialog',{name:'Choose a workspace',exact:true});
 const beginSetup=async name=>{await page.getByRole('button',{name:'Choose workspace',exact:true}).click();await picker.getByRole('button',{name,exact:true}).click()};
 await expect(page.getByRole('button',{name:'Choose workspace',exact:true})).toBeVisible();
 await composer.fill('Keep this draft through workspace setup');
 await action('attachment.add',{sessionId:null,name:'notes.txt',base64:'aGVsbG8='});
 await beginSetup('New workspace');let form=page.getByRole('form',{name:'Create workspace'});
 await form.getByLabel('Workspace name',{exact:true}).fill('Launch plan');
 await page.screenshot({path:out+'/name-first.png'});
 await form.getByRole('button',{name:'Create workspace',exact:true}).click();
 await expect(form).toHaveCount(0);await expect(composer).toHaveDraft('Keep this draft through workspace setup');
 let current=await state();assert.equal(current.sessions.length,0);assert.equal(current.draftAttachments.length,1);assert.equal(current.view.newSessionDraft.workspace,path.join(current.workspaceDefaults.root,'launch-plan'));
 assert.equal(current.view.newSessionDraft.location.kind,'workspace');
 await expect(page.getByRole('button',{name:'Open chats in '+current.view.newSessionDraft.workspace,exact:true})).toBeVisible();
 const home=path.dirname(current.workspaceDefaults.root),existing=path.join(home,'borrowed');await mkdir(existing);await writeFile(path.join(existing,'keep.txt'),'Do not replace');
 await beginSetup('Use existing folder');form=page.getByRole('form',{name:'Use existing folder'});
 await form.getByRole('textbox',{name:/Folder on/}).fill(existing);
 await expect(form.getByText('Developer options',{exact:true})).toHaveCount(0);
 await page.screenshot({path:out+'/attach-existing.png'});
 await form.getByRole('button',{name:'Cancel',exact:true}).click();await page.keyboard.press('Escape');await expect(picker).toBeHidden();
 await expect(composer).toHaveDraft('Keep this draft through workspace setup');
 assert.equal((await state()).view.newSessionDraft.workspace,current.view.newSessionDraft.workspace);
 await beginSetup('Use existing folder');form=page.getByRole('form',{name:'Use existing folder'});await form.getByRole('textbox',{name:/Folder on/}).fill(existing);await form.getByRole('button',{name:'Use folder',exact:true}).click();
 await expect(form).toHaveCount(0);await expect(composer).toHaveDraft('Keep this draft through workspace setup');
 assert.equal((await state()).view.newSessionDraft.workspace,existing);assert.equal(await readFile(path.join(existing,'keep.txt'),'utf8'),'Do not replace');
 await beginSetup('New workspace');form=page.getByRole('form',{name:'Create workspace'});await form.getByLabel('Workspace name',{exact:true}).fill('Launch plan');await form.getByRole('button',{name:'Create workspace',exact:true}).click();
 await expect(form.getByRole('button',{name:'Open workspace',exact:true})).toBeVisible();await form.getByRole('button',{name:'Open workspace',exact:true}).click();
 await expect(composer).toHaveDraft('Keep this draft through workspace setup');
 for(const scheme of ['light','dark']){await action('view.update',{patch:{scheme,navPinned:false,navExpanded:false}});for(const width of [320,390]){await page.setViewportSize({width,height:844});await beginSetup('Use existing folder');await expect(page.getByRole('form',{name:'Use existing folder'})).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`No horizontal clipping at ${width}`);await page.screenshot({path:out+`/attach-${scheme}-${width}.png`});await page.getByRole('form',{name:'Use existing folder'}).getByRole('button',{name:'Cancel',exact:true}).click();await page.keyboard.press('Escape');await expect(picker).toBeHidden()}}
 await page.setViewportSize({width:1280,height:900});await action('view.update',{patch:{scheme:'light',navPinned:true}});
 await openSettingsPage(page,'workspaces');
 const root=path.join(home,'my-workspaces');await page.getByRole('textbox',{name:/Default workspace folder/}).fill(root);await page.getByRole('button',{name:'Save',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'Saved. Existing workspaces'})).toBeVisible();
 await page.screenshot({path:out+'/workspace-settings.png'});await page.getByRole('button',{name:/^Close (panel|settings)$/i}).click();
 await expect(composer).toHaveDraft('Keep this draft through workspace setup');await beginSetup('New workspace');form=page.getByRole('form',{name:'Create workspace'});await form.getByLabel('Workspace name',{exact:true}).fill('Research');await form.getByRole('button',{name:'Create workspace',exact:true}).click();await expect(form).toHaveCount(0);assert.equal((await state()).view.newSessionDraft.workspace,path.join(root,'research'));
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();current=await state();assert.equal(current.sessions.length,1);assert.equal(current.sessions[0].workspace,path.join(root,'research'));assert.equal(current.sessions[0].messages[0].attachments[0].name,'notes.txt');
 await page.screenshot({path:out+'/sidebar.png'});
 // Default navigation retains direct ordering and never changes the active chat
 // or its unsent message. Use the real shared pin-order action and persistence.
 const first=current.selectedSessionId,firstTitle=current.sessions.find(row=>row.id===first).title;
 await action('session.create',{workspace:path.join(root,'research'),title:'Second pinned chat'});const second=(await state()).selectedSessionId;
 await action('session.create',{workspace:path.join(root,'research'),title:'Third pinned chat'});const third=(await state()).selectedSessionId;
 for(const id of [first,second,third])await action('session.pin',{id,pinned:true});
 await action('session.select',{id:first});await composer.fill('Keep this unsent draft while moving pins');
 const pinned=page.getByRole('region',{name:'Pinned items'}),handle=title=>pinned.getByRole('button',{name:'Reorder '+title,exact:true});
 const order=()=>pinned.locator('[data-session-id]').evaluateAll(rows=>rows.map(row=>row.dataset.sessionId));
 await expect.poll(order).toEqual([first,second,third]);
 await handle('Second pinned chat').focus();await page.keyboard.press('Alt+ArrowUp');
 await expect.poll(order).toEqual([second,first,third]);await expect(handle('Second pinned chat')).toBeFocused();
 await expect(handle('Second pinned chat')).not.toHaveAttribute('aria-disabled','true');await page.keyboard.press('Alt+ArrowDown');await expect.poll(order).toEqual([first,second,third]);
 await expect(handle('Third pinned chat')).not.toHaveAttribute('aria-disabled','true');const from=await handle('Third pinned chat').boundingBox(),to=await pinned.locator('[data-session-id="'+first+'"]').boundingBox();await page.mouse.move(from.x+from.width/2,from.y+from.height/2);await page.mouse.down();await page.mouse.move(from.x+from.width/2,to.y+to.height/2,{steps:8});await page.mouse.up();
 await expect.poll(order).toEqual([third,first,second]);await expect.poll(async()=>(await state()).pinnedSessionIds).toEqual([third,first,second]);
 assert.equal((await state()).selectedSessionId,first);await expect(composer).toHaveDraft('Keep this unsent draft while moving pins');
 await page.screenshot({path:out+'/pins-reordered.png'});
 await page.reload();await expect(handle(firstTitle)).toBeVisible();await expect.poll(order).toEqual([third,first,second]);
 assert.equal((await state()).selectedSessionId,first);await expect(composer).toHaveDraft('Keep this unsent draft while moving pins');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'passed',scenarios:13,screenshots:out}));
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Workspace experience fixture',close:[['Workspace experience browser',()=>browser?.close()]],stop,originalError:failure})}
