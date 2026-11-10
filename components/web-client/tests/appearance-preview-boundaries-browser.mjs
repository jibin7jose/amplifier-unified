// Production host/renderer and shared actions; no provider or private user data.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
import {showFixtureConversation} from './browser-chat-pages.mjs';
import './composer-test-helpers.mjs';
import {openSettingsPage} from './browser-settings.mjs';
import {ownFixture,finishFixture} from './fixture-lifecycle.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',
 [root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
const stop=ownFixture(fixture,{label:'Appearance preview boundaries fixture'});
let browser,failure;
try{
 const url=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);
  fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});
  fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{
   const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row.url)}
  }catch{}});
 });
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000},
  extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 const action=(name,args={})=>page.evaluate(([name,args])=>window.amplifier.dispatch(name,args),[name,args]);
 const button=name=>page.getByRole('button',{name,exact:true});
 const accent=()=>page.locator('#amp-one').evaluate(el=>getComputedStyle(el).getPropertyValue('--a-accent').trim());
 await page.goto(url);await showFixtureConversation(page);
 await action('session.create');
 await page.getByRole('textbox',{name:'Message Amplifier'}).fill('Retain this conversation draft.');
 const committed={name:'Committed green',css:'#amp-one{--a-accent:#116633}'};
 await action('theme.apply',committed);
 const saved=(await action('theme.save',{name:'Editor A',description:'Retain my description',
  css:'#amp-one{--a-accent:#883355}'})).result;
 await openSettingsPage(page,'custom-appearance');
 await button('Preview Editor A').click();await button('Edit Editor A').click();
 const fields={name:'#theme-name',css:'#theme-css',description:'#theme-description'};
 const draft={name:'My unsaved A',css:'#amp-one{--a-accent:#8855aa}',description:'My private description'};
 for(const [key,selector] of Object.entries(fields))await page.locator(selector).fill(draft[key]);
 const retained=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-editor.v1')));
 assert.equal(retained.entry.id,saved.id);
 const preview={name:'Shared preview B',css:'#amp-one{--a-accent:#1144cc}'};
 await action('theme.preview',preview);
 await expect.poll(accent).toBe('#1144cc');
 const scenario=process.env.APPEARANCE_PREVIEW_SCENARIO||'all';
 if(scenario!=='confirmation'){
  const previousClient=(await state()).client.id;
  // No closing Settings or applying another theme before this reload.
  await page.reload();
  await page.waitForFunction(()=>window.amplifier?.getState()?.client?.id);
  assert.notEqual((await state()).client.id,previousClient);
  await expect.poll(async()=>(await state()).view.themePreview).toBe(false);
  await expect.poll(accent).toBe('#116633');
  await openSettingsPage(page,'custom-appearance');
  if(!await page.locator(fields.css).isVisible())await page.getByText('Advanced customization',{exact:true}).click();
  for(const [key,selector] of Object.entries(fields))await expect(page.locator(selector)).toHaveValue(draft[key]);
  const after=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-editor.v1')));
  assert.deepEqual(after,retained);
  assert.deepEqual((await state()).theme,committed);
 }
 if(scenario!=='reload'){
  await action('theme.preview',preview);
  await expect.poll(accent).toBe('#1144cc');
  await expect(page.locator('.a-appearance-preview')).toContainText('Previewing Shared preview B');
  await expect(button('Use Shared preview B')).toBeVisible();
  await button('Use Shared preview B').click();
  await expect.poll(async()=>(await state()).theme).toEqual(preview);
  await expect.poll(accent).toBe('#1144cc');
  for(const [key,selector] of Object.entries(fields))await expect(page.locator(selector)).toHaveValue(draft[key]);
  const after=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('amplifier.appearance-editor.v1')));
  assert.deepEqual(after,retained,'Confirmation applies B, not editor A, without replacing A');
  // The separate editor Apply button deliberately applies A.
  await button('Apply appearance').click();
  await expect.poll(async()=>(await state()).theme.name).toBe(draft.name);
  await expect.poll(accent).toBe('#8855aa');
 }
 await page.getByRole('button',{name:/^Close (panel|settings)$/}).click();
 await showFixtureConversation(page);
 await expect(page.getByRole('textbox',{name:'Message Amplifier'})).toHaveDraft('Retain this conversation draft.');
 assert.deepEqual(errors,[]);
 console.log(`Appearance preview boundaries passed (${scenario}): shared preview paint/label/exact confirmation, independent draft and reload ending settled preview.`);
}catch(error){failure=error;throw error}
finally{await finishFixture({label:'Appearance preview boundaries fixture',
 close:[['Appearance preview boundaries browser',()=>browser?.close()]],stop,originalError:failure})}