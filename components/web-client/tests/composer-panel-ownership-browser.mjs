// Add -> keyboard Settings -> Escape must belong to the newer surface.
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {startSettingsFixture} from './settings-fixture.mjs';
import {finishFixture} from './fixture-lifecycle.mjs';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
const output=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/composer-panel-ownership';
await mkdir(output,{recursive:true});
const {base,stop}=await startSettingsFixture(),observations=[];
let browser,page,failure;
try{
 browser=await chromium.launch({headless:true});
 for(const {width,platform,shortcut} of [{width:1280,platform:'Linux x86_64',shortcut:'Control+,'},{width:390,platform:'MacIntel',shortcut:'Meta+,'}]){
  const context=await browser.newContext({viewport:{width,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
  await context.addInitScript(platform=>Object.defineProperty(navigator,'platform',{get:()=>platform}),platform);
  page=await context.newPage();const errors=[],actions=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/actions')actions.push(r.postDataJSON().action)});
  await page.goto(base);await showFixtureConversation(page,{navPinned:false});
  const draft=page.getByRole('textbox',{name:'Message Amplifier'}),add=page.getByRole('button',{name:'Add attachments',exact:true}),menu=page.getByRole('dialog',{name:'Add to your message',exact:true});
  const settings=page.locator('[data-part="settings-experience"]');
  const text='Keep this draft while Settings owns the keyboard.';await draft.fill(text);
  const openFromAdd=async()=>{
   await add.click();await expect(menu).toBeVisible();await expect(menu.getByRole('button',{name:'Attach files',exact:true})).toBeFocused();
   // No pointer event may dismiss Add for us before the application shortcut.
   await page.keyboard.press(shortcut);await expect(settings).toBeVisible();
   await expect(menu).toHaveCount(0);await expect(add).toHaveAttribute('aria-expanded','false');
   await expect.poll(()=>settings.evaluate(el=>el.contains(document.activeElement))).toBe(true);
  };
  await openFromAdd();await page.keyboard.press('Escape');await expect(settings).toBeHidden();await expect(add).toBeFocused();await expect(draft).toHaveDraft(text);
  // Ordinary dismissal still returns focus to Add.
  await add.click();await expect(menu).toBeVisible();await page.keyboard.press('Escape');await expect(menu).toHaveCount(0);await expect(add).toBeFocused();
  await openFromAdd();
  // Navigate within the already-open synthetic Settings surface. Use the public
  // action instead of importing a source-only navigation helper into a wheel run.
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'settings',settingsSection:'setup',settingsExpanded:['providers']}}));
  await expect(settings).toHaveAttribute('data-settings-page','providers');
  await page.locator('[data-part=provider-settings] [data-collection-id=one] button').click();
  await page.evaluate(async()=>{for(const accessOpen of [false,true])await window.amplifier.dispatch('view.update',{patch:{providerEditor:{...window.amplifier.getState().view.providerEditor,accessOpen}}})});
  await page.locator('#provider-key-source').selectOption('private');
  const secret='synthetic-unsaved-settings-value';await page.locator('#provider-key').fill(secret);
  await page.keyboard.press('Escape');const guard=page.getByRole('alertdialog');await expect(guard).toBeVisible();await expect(menu).toHaveCount(0);
  await expect.poll(()=>guard.evaluate(el=>el.contains(document.activeElement))).toBe(true);
  await page.getByRole('button',{name:'Keep settings open',exact:true}).click();await expect(page.locator('#provider-key')).toHaveValue(secret);await expect(settings).toBeVisible();
  await page.locator('#provider-key').focus();await page.keyboard.press('Escape');await expect(guard).toBeVisible();
  await page.getByRole('button',{name:'Discard changes',exact:true}).click();await expect(settings).toBeHidden();await expect(add).toBeFocused();await expect(draft).toHaveDraft(text);await expect(menu).toHaveCount(0);
  assert.equal(actions.filter(a=>['conversation.send','attachment.add','providers.save','access.set'].includes(a)).length,0);
  assert.deepEqual(errors,[]);observations.push({width,platform,shortcut,addDismissedWithoutPointer:true,settingsEscapeOwned:true,dirtyGuardKept:true,draftKept:true,ordinaryAddFocusReturned:true,settingsReturnFocus:true});
  await page.screenshot({path:`${output}/keyboard-settings-${width}.png`});await context.close();
 }
 await writeFile(output+'/result.json',JSON.stringify({passed:true,observations,limits:['Synthetic host and navigator platform branch, not physical macOS keyboard']},null,2));
 console.log('PASS Add keyboard handoff: Settings owns Escape/focus, unsaved-settings guard and composer draft retained; ordinary Add focus return preserved.');
}catch(error){failure=error;await writeFile(output+'/result.json',JSON.stringify({passed:false,observations,error:String(error.stack||error)},null,2));await page?.screenshot({path:output+'/failure.png'}).catch(()=>{});throw error;
}finally{await finishFixture({label:'Composer panel ownership',close:[['browser',()=>browser?.close()]],stop,originalError:failure})}
