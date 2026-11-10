import {fixturePython} from './fixture-python.mjs';
// Focused presentation acceptance. Run only against the isolated fixture.
import {showFixtureConversation} from './browser-chat-pages.mjs';
import {openSettingsPage} from '../../.build/web-client/tests/browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

const evidence=process.env.ARTIFACT_EVIDENCE_DIR||'/artifacts/one-surface';
await mkdir(evidence,{recursive:true});
const reservation=createServer();
await new Promise((resolve,reject)=>{reservation.once('error',reject);reservation.listen(0,'127.0.0.1',resolve)});
const port=reservation.address().port,base=`http://127.0.0.1:${port}`;
await new Promise((resolve,reject)=>reservation.close(error=>error?reject(error):resolve()));
const fixture=spawn(fixturePython(process.env.FIXTURE_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url))),[process.env.ARTIFACT_FIXTURE||fileURLToPath(new URL('../../tests/fixtures/inline_artifacts_server.py',import.meta.url)),String(port)],{stdio:'inherit'});
const stop=ownFixture(fixture,{label:'Artifact presentation fixture'});
let browser,page,failure,identity;const errors=[];
const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
const presentationState=()=>page.evaluate(id=>{
 const s=window.amplifier.getState(),canvas=s.canvas||{},card=document.querySelector(`.a-document-output[data-artifact-id="${id}"]`);
 return {clientId:s.client?.id,selectedSessionId:s.selectedSessionId,
  primary:{id:canvas.id,open:canvas.open,selectedVersion:canvas.selectedVersion,disposed:!!canvas._presentationDisposed},
  choices:(s.canvasWorkspace?.presentations||[]).filter(row=>row.id===id),
  inlineViews:(s.canvasWorkspace?.views||[]).filter(row=>row.resourceId===id&&row.viewId.startsWith('inline-')).map(row=>({viewId:row.viewId,resourceRevision:row.resourceRevision,generation:row.generation,selectedVersion:row.selectedVersion})),
  disclosure:card?.querySelector('.a-document-preview-toggle')?.getAttribute('aria-expanded'),
  inlineFrames:card?.querySelectorAll('iframe').length||0,
  primaryFrames:document.querySelectorAll('.a-canvas-workspace iframe').length,
  notices:[...(card?.querySelectorAll('.a-inline-artifact-notice')||[])].map(node=>node.textContent)};
},identity);
try{
 for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)break}catch{}if(fixture.exitCode!==null)throw Error('Fixture exited before readiness');await new Promise(resolve=>setTimeout(resolve,100));}
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS?['--single-process']:[]});
 const context=await browser.newContext({viewport:{width:1400,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/login');
 await page.getByLabel('Username').fill('inline-fixture');await page.getByLabel('Password').fill('fixture-password');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Review one saved artifact.');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await page.waitForFunction(()=>{const s=window.amplifier.getState(),chat=s.sessions.find(r=>r.id===s.selectedSessionId);return chat?.status==='idle'&&chat.messages?.some(r=>r.role==='assistant'&&r.text?.startsWith('## Ready'))});
 await action('canvas.show',{kind:'html',title:'One surface example',content:'<h1>Exact saved example</h1><label>Local note<input aria-label="Local note"></label><button onclick="this.textContent=\'Clicked\'">Try it</button>'});
 identity=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 if(process.env.ARTIFACT_DUPLICATE_PROBE==='1'){
  const legacyCard=page.locator('.a-inline-artifact').filter({hasText:'One surface example'}).first();
  await legacyCard.scrollIntoViewIfNeeded();
  await legacyCard.locator('iframe').waitFor();
  await page.locator('.a-canvas-workspace iframe').waitFor();
  const count=await page.locator(`iframe[src*="/api/canvas/${identity}/document"]`).count();
  await writeFile(`${evidence}/negative-mounts.json`,JSON.stringify({count}));
  assert.equal(count,1,'ONE_SURFACE_NEGATIVE: restored old default mounts duplicate executing frames');
  throw Error('Negative control did not restore the original duplicate default');
 }
 const card=page.locator(`.a-document-output[data-artifact-id="${identity}"]`);
 await card.scrollIntoViewIfNeeded();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await expect(card.getByRole('button',{name:'Preview in chat',exact:true})).toHaveAttribute('aria-expanded','false');
 await page.waitForFunction(()=>window.amplifier.getState().canvasWorkspace.views.some(v=>v.viewId==='primary'&&Object.values(v.renderReports||{}).some(r=>r.status==='ready')));
 const mounts=await page.evaluate(id=>({
  primary:document.querySelectorAll('.a-canvas-workspace iframe').length,
  inline:document.querySelectorAll('.a-chat-artifacts iframe').length,
  inlineLeases:window.amplifier.getState().canvasWorkspace.views.filter(v=>v.viewId.startsWith('inline-')&&v.resourceId===id).length
 }),identity);
 await writeFile(`${evidence}/default-mounts.json`,JSON.stringify(mounts,null,2));
 await page.screenshot({path:`${evidence}/default-desktop.png`});
 assert.equal(mounts.inline,0,'One canvas.show must not mount a second full HTML renderer inline');
 assert.equal(mounts.inlineLeases,0,'A compact card must not allocate an invisible full preview');
 assert.equal(mounts.primary,1);
 // Observe actual DOM instances, including hidden frames, at every mutation.
 await page.evaluate(id=>{
  window.surfaceCounts=[];
  const observe=()=>window.surfaceCounts.push(document.querySelectorAll(`iframe[src*="/api/canvas/${id}/document"]`).length);
  window.surfaceObserver=new MutationObserver(observe);window.surfaceObserver.observe(document.body,{childList:true,subtree:true});observe();
 },identity);
 const original=await page.locator('.a-canvas-workspace iframe').elementHandle();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByLabel('Local note').fill('Keep local input');
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.getByRole('button',{name:'Keep current preview',exact:true}).click();
 assert.ok(await original.evaluate(node=>node.isConnected),'Cancelled move must retain the exact frame');
 await expect(page.locator('.a-canvas-workspace').frameLocator('iframe').getByLabel('Local note')).toHaveValue('Keep local input');
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and preview in chat',exact:true}).click();
 await card.frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await expect(page.locator('.a-canvas-workspace iframe')).toHaveCount(0);
 assert.equal(await original.evaluate(node=>node.isConnected),false);
 await expect(card.getByRole('button',{name:'Hide preview',exact:true})).toHaveAttribute('aria-expanded','true');
 await card.getByRole('button',{name:'Open One surface example in canvas',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await page.locator('.a-canvas-workspace').frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 await expect(card.locator('iframe')).toHaveCount(0);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.selectedVersion),1);
 // Second cycle, hide, reopen and reload preserve choice, not iframe-local state.
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and preview in chat',exact:true}).click();
 await card.locator('iframe').waitFor();
 await card.getByRole('button',{name:'Hide preview',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and hide',exact:true}).click();
 await expect(page.locator(`iframe[src*="/api/canvas/${identity}/document"]`)).toHaveCount(0);
 const observedCounts=await page.evaluate(()=>{window.surfaceObserver.disconnect();return window.surfaceCounts});
 assert.ok(observedCounts.every(count=>count<=1),`Simultaneous executing frames: ${observedCounts}`);
 await writeFile(`${evidence}/transition-mounts.json`,JSON.stringify(observedCounts));
 await page.reload();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 await expect(card.getByRole('button',{name:'Preview in chat',exact:true})).toHaveAttribute('aria-expanded','false');
 await card.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await card.frameLocator('iframe').getByRole('heading',{name:'Exact saved example'}).waitFor();
 const beforeReload=await presentationState();
 await writeFile(`${evidence}/reload-presentation.json`,JSON.stringify({before:beforeReload},null,2));
 await page.reload();await page.waitForSelector('#amp-one');await showFixtureConversation(page);
 const afterReload=await presentationState();
 await writeFile(`${evidence}/reload-presentation.json`,JSON.stringify({before:beforeReload,after:afterReload},null,2));
 assert.notEqual(afterReload.clientId,beforeReload.clientId,'A new page owns a new client');
 assert.equal(afterReload.selectedSessionId,beforeReload.selectedSessionId);
 assert.deepEqual(afterReload.choices,beforeReload.choices,'Reload must retain the exact inline publication choice');
 assert.equal(afterReload.primary.disposed,true,'Reload must not reclaim the primary for an inline presentation');
 assert.equal(afterReload.primary.open,false);
 await card.scrollIntoViewIfNeeded();await card.locator('iframe').waitFor();
 await expect(page.locator('.a-canvas-workspace iframe')).toHaveCount(0);
 const restored=await presentationState();
 await writeFile(`${evidence}/reload-presentation.json`,JSON.stringify({before:beforeReload,after:afterReload,restored},null,2));
 assert.equal(restored.inlineFrames,1);
 assert.equal(restored.inlineViews.length,1);
 assert.ok(restored.inlineViews.every(row=>beforeReload.inlineViews.every(old=>old.viewId!==row.viewId)),'Reload must allocate a fresh inline lease');
 // A separate client changes its own choice, not this page's.
 const other=await page.context().newPage();await other.goto(base+'/');
 await other.waitForSelector('#amp-one');await showFixtureConversation(other);
 const sid=await page.evaluate(()=>window.amplifier.getState().selectedSessionId);
 await other.evaluate(id=>window.amplifier.dispatch('session.select',{id}),sid);
 await other.evaluate(id=>window.amplifier.dispatch('canvas.select',{id,version:1}),identity);
 await other.locator('.a-canvas-workspace iframe').waitFor();
 await expect(card.locator('iframe')).toHaveCount(1);await other.close();
 await card.getByRole('button',{name:'Open One surface example in canvas',exact:true}).click();
 await card.getByRole('button',{name:'Reset preview and open in Canvas',exact:true}).click();
 await page.locator('.a-canvas-workspace iframe').waitFor();
 // Short document previews must preserve the unrelated dirty primary and draft.
 const retained=await page.locator('.a-canvas-workspace iframe').elementHandle();
 const primary=await page.evaluate(()=>{const v=window.amplifier.getState().canvasWorkspace.views.find(v=>v.viewId==='primary');return Object.fromEntries(['viewId','resourceId','resourceRevision','generation'].map(k=>[k,v[k]]))});
 await action('canvas.views.dirty',{...primary,dirty:true});
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Preserve the unsent parent draft');
 const counts=await page.evaluate(()=>{const s=window.amplifier.getState();return {messages:s.sessions.find(v=>v.id===s.selectedSessionId).messages.length,artifacts:s.canvasArtifacts.length}});
 let lastCard;
 for(const [kind,content] of [['markdown','# Short review\n\nA **saved** review.'],['code','const result = 42;'],['json','{"answer":42}']]){
  const saved=await action('canvas.show',{kind,title:`Short ${kind}`,content,presentation:'inline'});
  const document=page.locator(`.a-document-output[data-artifact-id="${saved.result.id}"]`);
  await document.scrollIntoViewIfNeeded();await document.locator('.a-canvas-viewer').waitFor();
  assert.ok(await retained.evaluate(node=>node.isConnected));
  await expect(document.getByRole('button',{name:'Hide preview',exact:true})).toHaveAttribute('aria-expanded','true');
  await document.getByRole('button',{name:'Hide preview',exact:true}).focus();await page.keyboard.press('Enter');
  await expect(document.locator('.a-canvas-viewer')).toHaveCount(0);
  await expect(document.getByRole('button',{name:'Preview in chat',exact:true})).toBeFocused();
  await page.keyboard.press('Enter');await document.locator('.a-canvas-viewer').waitFor();
  lastCard=document;
 }
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveText('Preserve the unsent parent draft');
 // Delay only the confirmed hide receipt. A later user focus choice must win,
 // while the preceding keyboard-only hide still restores its own disclosure.
 const hiddenId=await lastCard.getAttribute('data-artifact-id');
 for(const destination of ['composer','iframe']){
  let releaseHide,hideAccepted;
  const hideGate=new Promise(resolve=>{releaseHide=resolve});
  const acceptedGate=new Promise(resolve=>{hideAccepted=resolve});
  const delayHide=async route=>{
   const data=route.request().postDataJSON();
   if(data?.action!=='canvas.select'||data.args?.id!==hiddenId||data.args?.presentation!=='saved')return route.continue();
   const response=await route.fetch();hideAccepted();await hideGate;await route.fulfill({response});
  };
  await page.route('**/api/actions',delayHide);
  await lastCard.getByRole('button',{name:'Hide preview',exact:true}).click();
  await acceptedGate;
  const focused=destination==='composer'?page.getByRole('textbox',{name:'Message Amplifier'}):page.locator('.a-canvas-workspace iframe');
  if(destination==='composer')await focused.focus();
  else await page.locator('.a-canvas-workspace').frameLocator('iframe').getByLabel('Local note').focus();
  releaseHide();
  await expect(lastCard.getByRole('button',{name:'Preview in chat',exact:true})).toBeEnabled();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await expect(focused).toBeFocused();
  await page.unroute('**/api/actions',delayHide);
  await lastCard.getByRole('button',{name:'Preview in chat',exact:true}).click();
  await lastCard.locator('.a-canvas-viewer').waitFor();
 }
 await writeFile(`${evidence}/delayed-hide-focus.json`,JSON.stringify({laterComposerFocusPreserved:true,laterIframeFocusPreserved:true}));
 assert.equal(await page.evaluate(()=>{const s=window.amplifier.getState();return s.sessions.find(v=>v.id===s.selectedSessionId).messages.length}),counts.messages);
 await action('canvas.views.dirty',{...primary,dirty:false});
 await page.screenshot({path:`${evidence}/documents-desktop.png`});
 await page.emulateMedia({reducedMotion:'reduce'});await action('canvas.close');
 await page.setViewportSize({width:390,height:844});await lastCard.scrollIntoViewIfNeeded();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Narrow shell overflow');
 assert.ok(await lastCard.evaluate(node=>node.scrollWidth<=node.clientWidth),'Narrow card overflow');
 await page.screenshot({path:`${evidence}/documents-narrow.png`});
 // No preview transition above generated another saved definition.
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvasArtifacts.length),counts.artifacts+3);
 await writeFile(`${evidence}/geometry.json`,JSON.stringify(await lastCard.evaluate(node=>({card:node.getBoundingClientRect().toJSON(),viewport:{width:innerWidth,height:innerHeight},controls:[...node.querySelectorAll('button')].map(button=>({label:button.getAttribute('aria-label')||button.textContent,rect:button.getBoundingClientRect().toJSON()}))})),null,2));
 await page.setViewportSize({width:1400,height:950});
 await action('canvas.show',{kind:'settings'});
 await openSettingsPage(page,'providers');
 const [selectionResponse]=await Promise.all([
  page.waitForResponse(response=>{if(!response.url().endsWith('/api/actions'))return false;const request=response.request().postDataJSON();return request?.action==='view.update'&&request.args?.patch?.providerEditor?.id==='one'&&request.args.patch.providerEditor.detailOpen===true}),
  page.locator('[data-part=provider-settings] [data-collection-id=one] button').click()
 ]);
 assert.equal((await selectionResponse.json()).accepted,true);
 const providerDetail=page.locator('[data-part=provider-settings] .a-collection-detail');
 await expect(providerDetail).toBeVisible();
 const access=providerDetail.locator('.a-provider-access');
 if(!await access.evaluate(node=>node.open))await access.locator('summary').click();
 await expect(page.locator('#provider-key-source')).toBeVisible();
 await page.locator('#provider-key-source').selectOption('private');
 await page.locator('#provider-key').fill('synthetic-unsaved-preview-test');
 const settingsId=await page.evaluate(()=>window.amplifier.getState().canvas.id);
 await lastCard.getByRole('button',{name:'Hide preview',exact:true}).click();
 await expect(lastCard.getByRole('button',{name:'Preview in chat',exact:true})).toBeEnabled();
 await expect(page.getByRole('alertdialog')).toHaveCount(0);
 await expect(page.locator('#provider-key')).toHaveValue('synthetic-unsaved-preview-test');
 await lastCard.getByRole('button',{name:'Preview in chat',exact:true}).click();
 await lastCard.locator('.a-canvas-viewer').waitFor();
 assert.equal(await page.evaluate(()=>window.amplifier.getState().canvas.id),settingsId);
 await expect(page.locator('#provider-key')).toHaveValue('synthetic-unsaved-preview-test');
 await lastCard.getByRole('button',{name:'Open Short json in canvas',exact:true}).click();
 await expect(page.getByRole('alertdialog')).toBeVisible();
 await page.getByRole('button',{name:'Keep settings open',exact:true}).click();
 await expect(page.locator('#provider-key')).toHaveValue('synthetic-unsaved-preview-test');
 await writeFile(`${evidence}/settings-draft.json`,JSON.stringify({unrelatedInlineDidNotNavigate:true,unsavedValuePreserved:true,replacementGuarded:true}));
 assert.deepEqual(errors,[]);
 console.log('PASS one-surface default, explicit transitions/reset refusal, hide/reopen/reload/second client, exact version, shared document controls, dirty unrelated Canvas/draft, keyboard and narrow containment');
}catch(error){
 failure=error;
 if(page){
  const presentation=identity?await presentationState().catch(error=>({unavailable:error.message})):null;
  await writeFile(`${evidence}/failure-state.json`,JSON.stringify({error:error.message,pageErrors:errors,presentation},null,2));
  await page.screenshot({path:`${evidence}/failure.png`});
 }
 throw error;
}
finally{await finishFixture({label:'Artifact presentation fixture',close:[['Artifact browser',()=>browser?.close()]],stop,originalError:failure})}
