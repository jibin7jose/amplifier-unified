import {showFixtureConversation} from './browser-chat-pages.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import './composer-test-helpers.mjs';
import {showComposerOption} from './composer-options-helpers.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',[root+'tests/fixtures/empty_host_ui_server.py','--chat-controls'],{stdio:['ignore','pipe','inherit']});
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';await mkdir(output,{recursive:true});
let browser;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--single-process','--no-zygote']:[]});
 const page=await browser.newPage({viewport:{width:390,height:844},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);await showFixtureConversation(page);const composer=page.getByRole('textbox',{name:'Message Amplifier'}),options=page.getByRole('button',{name:'Model and reasoning settings',exact:true}),menu=page.getByRole('dialog',{name:'Model for this chat',exact:true});
 const actionResponse=(action,matches)=>page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;
  const body=response.request().postDataJSON();
  return body?.action===action&&matches(body.args);
 });
 const accepted=async pending=>{
  const response=await pending;assert.equal(response.ok(),true);
  const receipt=await response.json();assert.equal(receipt.accepted,true);return receipt;
 };
 const closeMenu=async()=>{
  const saved=actionResponse('view.update',args=>args?.patch?.composerModel?.open===false);
  await menu.getByRole('button',{name:'Close chat options',exact:true}).click();
  await accepted(saved);
  await expect(menu).toHaveCount(0);
 };
 const discovery=async()=>{const response=await page.request.get(url+'/fixture/model-discovery');assert.equal(response.ok(),true);return response.json()};
 const draftCatalog=()=>page.evaluate(()=>{
  const state=window.amplifier.getState(),key=state.setup?.providerCatalogs?.['test-provider']?.sharedCatalogKey;
  return {key,entry:state.modelCatalogs?.[key]};
 });
 const assertDiscoveredOptions=async select=>expect.poll(()=>select.locator('option').evaluateAll(nodes=>nodes.map(node=>node.value))).toEqual(['','first','chosen-model']);
 await expect(options).toBeVisible();await composer.fill('Use my selected model');
 await options.click();await expect(menu).toBeVisible();await assertDiscoveredOptions(menu.locator('select#chat-model'));
 await expect(menu.locator('#chat-provider')).toBeFocused();
 const initialCatalog=await draftCatalog();assert.ok(initialCatalog.key);assert.equal(initialCatalog.entry.phase,'ready');assert.equal(initialCatalog.entry.supported,true);assert.equal(initialCatalog.entry.provenance,'provider-api');assert.equal(typeof initialCatalog.entry.loadedAt,'number');
 assert.equal((await discovery()).calls,1);
 await closeMenu();
 // Neither a configured default nor a saved pin is a discovered option.
 // Connection defaults scope provider-owned recommendation discovery; changing
 // that default invalidates its catalog, unlike a conversation-only pin.
 assert.equal((await page.request.post(url+'/fixture/provider-preference',{data:{model:'unlisted-default'}})).ok(),true);
 await expect(options).toContainText('unlisted-default');
 await options.click();await assertDiscoveredOptions(menu.locator('select#chat-model'));
 await expect(menu.locator('select#chat-model')).toHaveValue('');
 await expect(menu.getByText('Saved selection: unlisted-default (not in the discovered list)',{exact:true})).toBeVisible();
 const preferenceCatalog=await draftCatalog();
 assert.notEqual(preferenceCatalog.key,initialCatalog.key);
 assert.deepEqual(preferenceCatalog.entry.models,initialCatalog.entry.models);
 assert.equal((await discovery()).calls,2);
 await closeMenu();
 const pinned=await page.evaluate(async()=>{const setup=window.amplifier.getState().view.newSessionDraft||{};return window.amplifier.dispatch('view.update',{patch:{newSessionDraft:{...setup,selection:{instance:'test-provider',model:'owned-custom-model',effort:'high'}}}})});
 assert.equal(pinned.accepted,true);
 await options.click();await assertDiscoveredOptions(menu.locator('select#chat-model'));await expect(menu.locator('select#chat-model')).toHaveValue('');
 await expect(menu.getByText('Saved selection: owned-custom-model (not in the discovered list)',{exact:true})).toBeVisible();
 await closeMenu();await page.reload();await options.click();await assertDiscoveredOptions(menu.locator('select#chat-model'));await expect(menu.locator('select#chat-model')).toHaveValue('');
 await expect(menu.getByText('Saved selection: owned-custom-model (not in the discovered list)',{exact:true})).toBeVisible();await expect(composer).toHaveDraft('Use my selected model');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection)).toEqual({instance:'test-provider',model:'owned-custom-model',effort:'high'});
 assert.deepEqual(await draftCatalog(),preferenceCatalog);assert.equal((await discovery()).calls,2);
 const unsent=await(await page.request.get(url+'/fixture')).json();assert.deepEqual(unsent.started,[]);assert.deepEqual(unsent.sent,[]);
 await expect(menu.locator('#chat-provider')).toBeFocused();
 await expect(menu.getByRole('button',{name:/^(Choose model|Choose provider|Choose reasoning effort|Back to chat options)$/})).toHaveCount(0);
 for(const section of ['provider','model','effort'])await showComposerOption(page,section);
 const bundle=menu.locator('#composer-options-bundle');
 await expect(bundle).toBeHidden();await showComposerOption(page,'bundle');
 const bundleSaved=actionResponse('view.update',args=>args?.patch?.newSessionDraft?.bundle==='work');
 await bundle.selectOption('work');await accepted(bundleSaved);
 const modelSaved=actionResponse('view.update',args=>args?.patch?.newSessionDraft?.selection?.model==='chosen-model');
 await menu.locator('select#chat-model').selectOption('chosen-model');await accepted(modelSaved);
 // Optimistic draft state is visible before its save is acknowledged. Reload
 // only after this exact selection has been accepted by the server.
 const effortSaved=actionResponse('view.update',args=>args?.patch?.newSessionDraft?.selection?.model==='chosen-model'&&args.patch.newSessionDraft.selection.effort==='high');
 await menu.locator('#chat-effort').press('End');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection.effort)).toBe('high');
 await expect(menu.locator('#chat-effort')).toHaveAttribute('aria-valuetext','high');
 await closeMenu();await expect(composer).toHaveDraft('Use my selected model');
 await accepted(effortSaved);
 await page.reload();await expect(options).toBeVisible();await options.click();await expect(menu.locator('select#chat-model')).toHaveValue('chosen-model');await showComposerOption(page,'bundle');await expect(bundle).toHaveValue('work');
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().view.newSessionDraft.selection.effort)).toBe('high');
 await expect(menu.getByRole('button',{name:'Refresh this connection',exact:true})).toBeVisible();
 await closeMenu();
 // The same controls remain mounted across widths; only their popup position changes.
 await options.click();await page.setViewportSize({width:1280,height:900});await expect(menu).toBeVisible();await expect(options).toHaveCount(1);
 await expect(options).toContainText('chosen-model');await expect(menu.locator('#chat-model')).toHaveValue('chosen-model');
 await page.setViewportSize({width:320,height:700});await expect(menu).toBeVisible();await expect(menu.locator('#chat-model')).toHaveValue('chosen-model');
 await showComposerOption(page,'bundle');
 const touchTargets=await menu.locator('button,select,input,summary').evaluateAll(nodes=>nodes.filter(el=>el.getClientRects().length).map(el=>({name:el.getAttribute('aria-label')||el.id||el.textContent,width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height})));
 for(const target of touchTargets)assert.ok(target.width>=44&&target.height>=44,`Phone control is too small: ${JSON.stringify(target)}`);
 await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(options).toBeFocused();
 await options.click();await page.screenshot({path:output+'/readiness107-options-mobile.png'});await closeMenu();
 await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Synthetic first response',{exact:true}).waitFor();
 const data=await(await page.request.get(url+'/fixture')).json();assert.equal(data.sent.length,1);assert.equal(data.sent[0].selection.instance,'test-provider');assert.equal(data.sent[0].bundle,'work');assert.equal(data.sent[0].selection.model,'chosen-model');assert.equal(data.sent[0].selection.effort,'high');
 // A running conversation allows future settings without mutating its captured
 // request. This state is synthetic; no external provider is running.
 const sid=data.sent[0].sessionId;await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status:'working'}});
 await composer.fill('Preserve this unsent draft during active work');
 await options.click();await expect(menu.locator('#chat-provider')).toBeEnabled();await expect(menu.locator('#chat-model')).toBeEnabled();
 await expect(menu.locator('#chat-provider')).toBeFocused();
 await expect(menu.getByText('Your current turn stays unchanged. You can save settings for your next message.',{exact:true})).toBeVisible();
 const nextModel=actionResponse('conversation.configure',args=>args.selection?.model==='first');
 await menu.locator('#chat-model').selectOption('first');await accepted(nextModel);
 const nextEffort=actionResponse('conversation.configure',args=>args.selection?.effort==='high');
 await menu.locator('#chat-effort').press('End');await accepted(nextEffort);
 await showComposerOption(page,'bundle');await expect(bundle).toBeEnabled();
 const nextBundle=actionResponse('conversation.configure',args=>args.bundle==='anchors');
 await bundle.selectOption('anchors');await accepted(nextBundle);
 const restoreBundle=actionResponse('conversation.configure',args=>args.bundle==='work');
 await bundle.selectOption('work');await accepted(restoreBundle);
 const restoreModel=actionResponse('conversation.configure',args=>args.selection?.model==='chosen-model');
 await menu.locator('#chat-model').selectOption('chosen-model');await accepted(restoreModel);
 const restoreEffort=actionResponse('conversation.configure',args=>args.selection?.effort==='high');
 await menu.locator('#chat-effort').press('End');await accepted(restoreEffort);
 const active=await(await page.request.get(url+'/fixture')).json();
 assert.deepEqual(active.sent,data.sent,'No current request or captured selection changes');
 assert.deepEqual(active.started,data.started,'Saving does not start another worker');
 await expect(page.getByText('Synthetic first response',{exact:true})).toBeVisible();
 await closeMenu();await expect(composer).toHaveDraft('Preserve this unsent draft during active work');
 await page.request.post(url+'/fixture/activity',{data:{sessionId:sid,status:'idle'}});
 const menuSaved=page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||new URL(response.url()).pathname!=='/api/actions')return false;
  const body=response.request().postDataJSON();
  return body?.action==='view.update'&&body.args?.patch?.composerModel?.open===true&&body.args.patch.composerModel.sessionId===sid;
 });
 await options.click();await expect(menu).toBeVisible();
 const menuReceipt=await menuSaved;assert.equal(menuReceipt.ok(),true);assert.equal((await menuReceipt.json()).accepted,true);
 await page.reload();
 // Persisted presentation may reopen, but reload is not a local request to steal focus.
 await composer.waitFor();
 assert.equal(await page.evaluate(()=>!!document.activeElement?.closest('.a-composer-options-panel')),false);
 await expect(menu).toBeVisible();
 await expect.poll(()=>menu.evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.left>=0&&r.bottom<=innerHeight+1&&r.right<=innerWidth+1})).toBe(true);
 await page.screenshot({path:output+'/restored-options-mobile.png'});
 await closeMenu();
 await composer.fill('Keep this draft while managing connections');
 await options.click();await expect(menu).toBeVisible();
 await showComposerOption(page,'provider');
 await menu.getByRole('button',{name:'Manage connections'}).click();
 await expect(menu).toHaveCount(0);
 // Settings is a canvas surface, not the old generic panel. Use its own
 // accessible close control, which also unwinds compact Settings history.
 const settings=page.locator('[data-part="settings-experience"]');
 await expect(settings).toBeVisible();
 await expect(settings).toHaveAttribute('data-settings-page','ai-connections');
 await settings.getByRole('button',{name:'Close settings',exact:true}).click();
 await expect(settings).toBeHidden();
 await expect.poll(()=>page.evaluate(()=>{
  const state=window.amplifier.getState();
  return [state.view.panel??null,state.canvas?.kind==='settings'&&state.canvas.open===true,!!history.state?.amplifierSettings];
 })).toEqual([null,false,false]);
 await expect(composer).toBeVisible();
 await expect(composer).toHaveDraft('Keep this draft while managing connections');
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,1,'Opening and closing settings must not send again');
 // Model controls, Chat actions and navigation More are different layers.
 // Exercise ordinary clicks/Escape under reduced motion without losing a draft.
 await page.emulateMedia({reducedMotion:'reduce'});
 const chat=page.getByRole('button',{name:'Chat',exact:true}),chatActions=page.getByRole('group',{name:'Chat',exact:true});
 await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();
 await expect.poll(()=>menu.evaluate(el=>el.getBoundingClientRect().top>=document.querySelector('.a-work-header').getBoundingClientRect().bottom)).toBe(true);
 await chat.click();await expect(menu).toHaveCount(0);await expect(chatActions).toBeVisible();
 assert.ok(await chatActions.evaluate(el=>el.contains(document.activeElement)));
 await page.keyboard.press('Escape');await expect(chatActions).toBeHidden();await expect(chat).toBeFocused();
 await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();
 await expect(menu.locator('#chat-effort')).toHaveAttribute('aria-valuetext','high');
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 const drawer=page.getByRole('dialog',{name:'Workspaces and chats',exact:true});
 await expect(drawer).toBeVisible();await expect(menu).toHaveCount(0);
 assert.equal(await page.locator('.a-conversation').evaluate(el=>el.inert),true);
 assert.equal(await drawer.locator('.a-nav-rail').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
 const more=drawer.locator('.a-navigation-more').first();
 await more.focus();await more.click();await expect(page.locator('.a-navigation-flyout')).toBeVisible();
 await page.keyboard.press('Escape');await expect(page.locator('.a-navigation-flyout')).toBeHidden();await expect(drawer).toBeVisible();
 await page.keyboard.press('Escape');await expect(drawer).toBeHidden();
 await expect(composer).toHaveDraft('Keep this draft while managing connections');
 await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();
 const optionsGeometry=await options.boundingBox();assert.ok(optionsGeometry.width>=44&&optionsGeometry.height>=44);
 await page.keyboard.press('Escape');await expect(options).toBeFocused();
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,1);
 await writeFile(output+'/layering.json',JSON.stringify({width:320,reducedMotion:true,optionsGeometry,modelAndEffortRetained:true,chatAndMoreDismissedIndependently:true,draftRetained:true,noExtraSubmission:true},null,2));
 for(const scheme of ['light','dark']){
  await page.evaluate(scheme=>window.amplifier.dispatch('view.update',{patch:{scheme}}),scheme);await expect(options).toBeVisible();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:`${output}/readiness107-composer-${scheme}.png`});
  await options.click();await expect(menu.locator('#chat-provider')).toBeFocused();await expect(menu.locator('select#chat-model')).toHaveValue('chosen-model');
  await expect.poll(()=>menu.evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.left>=0&&r.bottom<=innerHeight+1&&r.right<=innerWidth+1})).toBe(true);
  await page.screenshot({path:`${output}/readiness107-options-${scheme}.png`});await closeMenu();
 }
 // A failed refresh keeps the real last-good list and offers explicit manual
 // input. Exercise DOM input, its blur commit and the saved revision before
 // sending; never inject a model option or set the selection through the API.
 const currentCatalog=()=>page.evaluate(sid=>{
  const state=window.amplifier.getState(),catalog=state.runtimeControl?.[sid]?.['configuration.catalog'];
  const key=catalog?.providers?.find(row=>row.id==='test-provider')?.sharedCatalogKey;
  return {key,entry:state.modelCatalogs?.[key]};
 },sid);
 await options.click();await expect(menu.locator('select#chat-model')).toHaveValue('chosen-model');
 assert.deepEqual(await currentCatalog(),preferenceCatalog);assert.equal((await discovery()).calls,2);
 assert.equal((await page.request.post(url+'/fixture/model-discovery',{data:{mode:'error'}})).ok(),true);
 await menu.getByRole('button',{name:'Refresh this connection',exact:true}).click();
 const manual=menu.getByRole('textbox',{name:'Conversation model',exact:true});
 await expect(manual).toBeVisible();await expect(manual).toHaveValue('chosen-model');
 await expect(menu.getByText('Manual ID — not verified by discovery. The connection checks it when you send.',{exact:true})).toBeVisible();
 await assertDiscoveredOptions(menu.locator('select#chat-model-catalog'));
 const failedCatalog=await currentCatalog();assert.equal(failedCatalog.key,preferenceCatalog.key);assert.equal(failedCatalog.entry.phase,'error');assert.equal(failedCatalog.entry.loadedAt,preferenceCatalog.entry.loadedAt);assert.equal(failedCatalog.entry.provenance,'provider-api');assert.deepEqual(failedCatalog.entry.models,preferenceCatalog.entry.models);
 assert.equal((await discovery()).calls,3);
 const expectedRevision=await page.evaluate(sid=>window.amplifier.getState().sessions.find(row=>row.id===sid).nextMessageRevision||0,sid);
 const manualSaved=actionResponse('conversation.configure',args=>args?.sessionId===sid&&args.expectedRevision===expectedRevision&&args.selection?.instance==='test-provider'&&args.selection.model==='manual-deployment-id');
 await manual.fill('manual-deployment-id');await manual.press('Tab');
 const manualReceipt=await accepted(manualSaved);assert.equal(manualReceipt.result.revision,expectedRevision+1);
 await expect.poll(()=>page.evaluate(sid=>window.amplifier.getState().sessions.find(row=>row.id===sid)?.pendingModelSelection?.model,sid)).toBe('manual-deployment-id');
 await assertDiscoveredOptions(menu.locator('select#chat-model-catalog'));await expect(menu.locator('select#chat-model-catalog')).toHaveValue('');
 await closeMenu();await page.reload();await expect(options).toContainText('manual-deployment-id');await options.click();await expect(manual).toHaveValue('manual-deployment-id');
 await expect(menu.getByText('Saved selection: manual-deployment-id (not in the discovered list)',{exact:true})).toBeVisible();
 await assertDiscoveredOptions(menu.locator('select#chat-model-catalog'));assert.deepEqual(await currentCatalog(),failedCatalog);assert.equal((await discovery()).calls,3);
 await closeMenu();await expect(composer).toHaveDraft('Keep this draft while managing connections');
 assert.equal((await(await page.request.get(url+'/fixture')).json()).sent.length,1);
 await composer.fill('Use the explicit manual deployment');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(async()=>(await(await page.request.get(url+'/fixture')).json()).sent.length).toBe(2);
 const manualSend=(await(await page.request.get(url+'/fixture')).json()).sent[1];
 assert.equal(manualSend.sessionId,sid);assert.equal(manualSend.text,'Use the explicit manual deployment');assert.equal(manualSend.selection.instance,'test-provider');assert.equal(manualSend.selection.model,'manual-deployment-id');assert.equal(manualSend.bundle,'work');
 assert.equal((await discovery()).calls,3);
 await writeFile(output+'/catalog-cache.json',JSON.stringify({initialCatalog,preferenceCatalog,failedCatalog,manualSelection:manualSend.selection,discovery:await discovery()},null,2));
 assert.deepEqual(errors,[]);console.log('PASS: one model panel with Advanced bundle; real host cache and explicit manual-ID save/send, no default/pin injection, preserved drafts/reload, exact submissions, touch/focus, light/dark reduced-motion 320px.');
}finally{await browser?.close();fixture.kill()}
