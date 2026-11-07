import {fixturePython} from './fixture-python.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
// Build an independently authored package only after the production app opens.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {settingsButton} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(fixturePython(root+'.venv/bin/python'),[root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,page;const errors=[];
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}}catch{}});fixture.once('exit',code=>reject(Error('Fixture exited '+code)))});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await showFixtureConversation(page,{navPinned:false});await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toBeVisible();
 const clientId=await page.evaluate(()=>window.amplifier.shellClientId);
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const inspect=()=>action('shell.inspect',{clientId}).then(row=>row.result);
 const openPreferences=async()=>{await page.locator('[data-settings-section="advanced"]').click();await page.locator('[data-settings-destination="shell:preferences"]').click()};
 const change=async composition=>{const current=await inspect();const prepared=await action('shell.changes.prepare',{clientId,expectedRevision:current.revision,composition});return {clientId,expectedRevision:current.revision,changeId:prepared.result.id}};
 await action('session.create',{title:'Component acceptance'});
 // App status/actions belong to the navigation footer in the current shell.
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await action('canvas.show',{kind:'html',title:'Keep this viewer',content:'<h1>Persistent view</h1><input aria-label="Viewer note" value="Preserved"/>'});
 await page.locator('.a-canvas-viewer iframe').waitFor();
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Draft survives contribution changes');
 await page.waitForFunction(()=>window.amplifier.getState().view.draft==='Draft survives contribution changes');
 await page.evaluate(()=>{window.componentProof={root:document.getElementById('amp-one'),composer:document.querySelector('[aria-label="Message Amplifier"]'),viewer:document.querySelector('.a-canvas-viewer iframe'),session:window.amplifier.getState().selectedSessionId}});
 const componentDirectory=process.env.AMPLIFIER_SHELL_COMPONENT_PACKAGE||root+'examples/shell-controls/dist';
 if(!process.env.AMPLIFIER_SHELL_COMPONENT_PACKAGE)await import('../scripts/build-shell-controls-example.mjs');
 const manifest=JSON.parse(await readFile(componentDirectory+'/manifest.json','utf8'));
 const source=await readFile(componentDirectory+'/module.mjs','utf8');
 await assert.rejects(()=>action('shell.packages.stage',{manifest:{...manifest,apiVersion:'99.0'},source}),/'1\.0' was expected/);
 const staged=await action('shell.packages.stage',{manifest,source});const digest=staged.result.digest;
 const validation=await action('shell.packages.validate',{digest});assert.equal(validation.result.status,'passed',JSON.stringify(validation));
 const original=(await inspect()).composition,composition=structuredClone(original);
 composition.instances.push({id:'core.app.actions',slot:'app.actions',package:'builtin.app-actions'},
  {id:'extra-actions',slot:'app.actions',package:digest},{id:'status-widget',slot:'app.status',package:digest},
  {id:'preferences',slot:'settings.section',package:digest},
  {id:'canvas-controls',slot:'canvas.toolbar',package:digest});
 const prepared=await change(composition);await action('shell.changes.preview',prepared);
 await expect(page.locator('[data-shell-component="extra-actions"]')).toBeVisible();
 await expect(page.getByLabel('Conversation status',{exact:true})).toBeVisible();
 await action('shell.changes.apply',prepared);
 await page.waitForFunction(()=>window.amplifier.getShellState()?.revision===1);
 const controls=page.locator('[data-shell-component="extra-actions"]');
 await controls.getByRole('button',{name:'Toggle compact spacing'}).click();
 await expect(page.locator('#amp-one')).toHaveAttribute('data-density','compact');
 await settingsButton(page).click();
 await openPreferences();
 const editor=page.locator('[data-shell-component="preferences"]');
 await editor.getByLabel('Component note').fill('Keep this unfinished edit');
 await expect.poll(async()=>((await inspect()).views.preferences||{}).dirty).toBe(true);
 const without=structuredClone((await inspect()).effectiveComposition);without.instances=without.instances.filter(row=>row.id!=='preferences');
 const removal=await change(without);await assert.rejects(()=>action('shell.changes.apply',removal),/Finish or cancel/);
 await expect(editor.getByLabel('Component note')).toHaveValue('Keep this unfinished edit');
 await editor.getByRole('button',{name:'Save component note'}).click();
 await expect.poll(async()=>((await inspect()).views.preferences||{}).dirty).toBe(false);
 await expect.poll(async()=>(await inspect()).reported?.status).toBe('ready');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 // A dormant Settings instance must earn new mounting evidence after it is
 // removed and re-added, even if the package and saved view state are identical.
 const beforeRemoval=structuredClone((await inspect()).composition);
 const removed=structuredClone(beforeRemoval);removed.instances=removed.instances.filter(row=>row.id!=='preferences');
 const removedChange=await change(removed);await action('shell.changes.apply',removedChange);
 await page.waitForFunction(revision=>window.amplifier.getShellState()?.revision===revision,removedChange.expectedRevision+1);
 const restoredChange=await change(beforeRemoval);await action('shell.changes.apply',restoredChange);
 await page.waitForFunction(revision=>window.amplifier.getShellState()?.revision===revision,restoredChange.expectedRevision+1);
 await expect.poll(async()=>(await inspect()).reported?.instances?.preferences).toBe('inactive');
 assert.equal((await inspect()).reported.status,'incomplete');
 await settingsButton(page).click();
 await openPreferences();
 await expect(page.getByLabel('Component note')).toHaveValue('Keep this unfinished edit');
 await expect.poll(async()=>(await inspect()).reported?.status).toBe('ready');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 assert.deepEqual(await page.evaluate(()=>({root:window.componentProof.root===document.getElementById('amp-one'),composer:window.componentProof.composer===document.querySelector('[aria-label="Message Amplifier"]'),viewer:window.componentProof.viewer===document.querySelector('.a-canvas-viewer iframe'),session:window.componentProof.session===window.amplifier.getState().selectedSessionId})),{root:true,composer:true,viewer:true,session:true});
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Draft survives contribution changes');
 const output=(process.env.AMPLIFIER_TEST_ARTIFACTS||root+'output/shell-components-proof')+'/';await mkdir(output,{recursive:true});await page.screenshot({path:output+'contributions.png'});
 const overflowGeometry=()=>controls.getByRole('button',{name:'Toggle compact spacing'}).evaluate(button=>{
  const header=document.querySelector('.a-work-header').getBoundingClientRect(),popup=button.closest('.a-slot-overflow-items').getBoundingClientRect(),target=button.getBoundingClientRect();
  const hit=document.elementFromPoint(target.x+target.width/2,target.y+target.height/2);
  return {belowHeader:popup.top>=header.bottom,inViewport:popup.left>=0&&popup.right<=innerWidth&&popup.bottom<=innerHeight,receivesPointer:!!hit&&(hit===button||button.contains(hit))};
 });
 const usableOverflow={belowHeader:true,inViewport:true,receivesPointer:true};
 for(const width of [320,390,600,760]){
  await page.setViewportSize({width,height:844});
  await expect(page.locator('.a-layout[data-pane-layout]')).toHaveAttribute('data-narrow','true');
  if(!await page.locator('#workspace-navigation').isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Workspaces and chats',exact:true})).toBeVisible();
  await expect.poll(()=>page.locator('.a-work-footer').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  await page.getByRole('button',{name:'Additional controls',exact:true}).click();
  await expect.poll(overflowGeometry).toEqual(usableOverflow);
  await page.getByRole('button',{name:'Additional controls',exact:true}).click();
 }
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:output+'mobile-contributions.png'});
 assert.ok(await page.locator('.a-work-footer').evaluate(el=>el.scrollWidth<=el.clientWidth),'Navigation footer contributions fit a narrow viewport');
 const additional=page.getByRole('button',{name:'Additional controls',exact:true});await additional.click();
 await expect(controls.getByRole('button',{name:'Toggle compact spacing'})).toBeVisible();
 // Keep the popover usable when the measured header changes while it is open.
 // This controlled content-height change exercises ResizeObserver without changing app state.
 const header=page.locator('.a-work-header'),oldMinHeight=await header.evaluate(el=>el.style.minHeight);
 await header.evaluate(el=>{el.style.minHeight='160px'});
 await expect.poll(overflowGeometry).toEqual(usableOverflow);
 // Reintroducing the old fixed offset must fail the same overlap boundary.
 const popup=page.locator('.a-slot-overflow[data-open="true"] .a-slot-overflow-items');
 const oldTop=await popup.evaluate(el=>el.style.top);
 await popup.evaluate(el=>{el.style.top='60px'});
 assert.equal((await overflowGeometry()).belowHeader,false);
 await popup.evaluate((el,value)=>{el.style.top=value},oldTop);
 await expect.poll(overflowGeometry).toEqual(usableOverflow);
 await header.evaluate((el,value)=>{el.style.minHeight=value},oldMinHeight);
 await expect.poll(overflowGeometry).toEqual(usableOverflow);
 await controls.getByRole('button',{name:'Toggle compact spacing'}).click();
 await expect(page.locator('#amp-one')).toHaveAttribute('data-density','comfortable');
 await page.screenshot({path:output+'mobile-controls.png'});
 // The SSE presentation can update before the save promise re-enables its button.
 const spacing=controls.getByRole('button',{name:'Toggle compact spacing'});
 await expect(spacing).toBeEnabled();await spacing.focus();await expect(spacing).toBeFocused();
 await spacing.press('Escape');
 await expect(additional).toBeFocused();await expect(additional).toHaveAttribute('aria-expanded','false');
 await page.getByRole('button',{name:'Additional status',exact:true}).click();await expect(page.getByLabel('Conversation status',{exact:true})).toBeVisible();
 const mobileSnapshot=(await action('shell.query',{clientId,instanceId:'extra-actions'})).result;
 await action('shell.command',{clientId,instanceId:'extra-actions',generation:mobileSnapshot.generation,action:'panel.open',args:{panel:'settings',section:'preferences'}});
 const settings=page.locator('.a-settings-experience');
 await expect(settings).toHaveAttribute('data-settings-route','shell:preferences');
 await expect(page.getByLabel('Component note')).toHaveValue('Keep this unfinished edit');
 await page.getByRole('button',{name:'Back to Advanced',exact:true}).click();
 await expect(settings).toHaveAttribute('data-settings-route','advanced');
 await page.getByRole('button',{name:'Back to Settings',exact:true}).click();
 await expect(settings).toHaveAttribute('data-settings-route','index');
 await openPreferences();
 await expect(settings).toHaveAttribute('data-settings-route','shell:preferences');
 await expect(page.getByLabel('Component note')).toHaveValue('Keep this unfinished edit');
 await page.getByRole('button',{name:'Close settings',exact:true}).click();await expect(settings).toHaveCount(0);
 await page.setViewportSize({width:1440,height:1000});
 const replacement=await action('shell.packages.stage',{manifest:{...manifest,version:'1.0.1'},source:source+'\n// Reviewed revision'});
 assert.equal((await action('shell.packages.validate',{digest:replacement.result.digest})).result.status,'passed');
 const old=(await action('shell.query',{clientId,instanceId:'extra-actions'})).result;
 const updated=structuredClone((await inspect()).effectiveComposition);updated.instances.find(row=>row.id==='extra-actions').package=replacement.result.digest;
 await action('shell.changes.apply',await change(updated));
 await assert.rejects(()=>action('shell.command',{clientId,instanceId:'extra-actions',generation:old.generation,action:'panel.open',args:{panel:'settings'}}),/replaced/);
 await page.reload();await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Draft survives contribution changes');
 await settingsButton(page).click();await openPreferences();
 await expect(page.getByLabel('Component note')).toHaveValue('Keep this unfinished edit');
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 // Reload attaches a fresh client while cloning this composition and view state.
 const reloaded=await page.evaluate(()=>window.amplifier.shellClientId);
 const current=(await action('shell.inspect',{clientId:reloaded})).result;
 const broken=(await action('shell.packages.stage',{manifest:{...manifest,version:'1.0.2',slots:['app.actions']},source:'export default ({React})=>({host})=>{if(host.getSnapshot().conversation?.title==="Component acceptance")throw Error("Live-only component failure");return React.createElement("p",null,"Fixture ready")}' })).result.digest;
 assert.equal((await action('shell.packages.validate',{digest:broken})).result.status,'passed');
 const brokenComposition=structuredClone(current.composition);brokenComposition.instances.find(row=>row.id==='extra-actions').package=broken;
 const brokenChange=await action('shell.changes.prepare',{clientId:reloaded,expectedRevision:current.revision,composition:brokenComposition});
 await action('shell.changes.apply',{clientId:reloaded,expectedRevision:current.revision,changeId:brokenChange.result.id});
 await expect(page.locator('[data-shell-component="extra-actions"]').getByRole('alert')).toContainText('Live-only component failure');
 await expect.poll(async()=>(await action('shell.inspect',{clientId:reloaded})).result.reported?.instances?.['extra-actions']).toBe('error');
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Draft survives contribution changes');
 await action('shell.recover',{clientId:reloaded,expectedRevision:current.revision+1,target:'default'});
 await expect(page.locator('[data-shell-component]')).toHaveCount(0);
 await expect(settingsButton(page)).toBeVisible();
 assert.deepEqual(errors,[]);
 console.log('Component slots passed: independent hot loading, host validation, user actions, agent preview/apply, bounded summaries, dirty deferral, stale callbacks, persistent module state/draft, unchanged viewer/composer and default recovery.');
}catch(error){console.error('Browser diagnostics',errors,await page?.locator('[role=alert]').allTextContents());throw error}finally{await browser?.close();fixture.kill()}
