// Real preview writes, held before delivery or after server acceptance; no inference.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/empty_host_ui_server.py'],{stdio:['ignore','pipe','inherit']});
let browser,release;
try{
 const url=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),20000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value.url)}}catch{}})});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const state=()=>page.evaluate(()=>window.amplifier.getState());
 await page.goto(url);await openSettingsPage(page,'custom-appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 await page.locator('#theme-name').fill('Navigation preview');
 await page.locator('#theme-css').fill('#amp-one { --a-accent: #6b4dcc; }');
 await expect.poll(async()=>(await state()).view.themeDraftName).toBe('Navigation preview');
 const applied=(await state()).theme;
 let mode='before',held=0;
 await page.route('**/api/actions',async route=>{
  if(route.request().postDataJSON()?.action!=='theme.preview'||mode==='pass')return route.continue();
  const response=mode==='after'?await route.fetch():null;
  held++;await new Promise(resolve=>release=resolve);
  if(response)return route.fulfill({response});
  return route.continue();
 });
 for(const phase of ['before','after','aba']){
  mode=phase==='after'?'after':'before';
  const target=held+1;
  await page.getByRole('button',{name:'Preview',exact:true}).click();
  await expect.poll(()=>held).toBe(target);
  await expect(page.getByRole('button',{name:'Preparing…',exact:true})).toBeDisabled();
  await openSettingsPage(page,'updates');
  if(phase==='aba')await openSettingsPage(page,'custom-appearance');
  const completed=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.preview');
  release();release=null;await completed;
  await expect.poll(async()=>(await state()).view.themePreview,{message:phase+' navigation must retire pending preview'}).toBe(false);
  await openSettingsPage(page,'custom-appearance');
  await expect(page.locator('.a-appearance-preview')).toHaveCount(0);
  await expect(page.locator('#theme-name')).toHaveValue('Navigation preview');
  await expect(page.locator('#theme-css')).toHaveValue('#amp-one { --a-accent: #6b4dcc; }');
  assert.deepEqual((await state()).theme,applied,'A preview must not apply shared appearance');
 }
 // A closed panel has a new component instance. Its later explicit preview
 // must run after retiring the old request, not be cancelled by old cleanup.
 mode='before';const oldCount=held;
 await page.getByRole('button',{name:'Preview',exact:true}).click();await expect.poll(()=>held).toBe(oldCount+1);
 const closed=page.waitForResponse(response=>{
  if(response.request().method()!=='POST'||!response.url().endsWith('/api/actions'))return false;
  const body=response.request().postDataJSON();
  return body?.action==='view.update'&&body.args?.patch?.panel===null;
 });
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 // Closure is local while the old preview holds the command queue. Do not let
 // the opening helper reuse the retiring Settings component.
 await expect.poll(async()=>(await state()).view.panel).toBe(null);
 await expect(page.locator('.a-settings-experience')).toHaveCount(0);
 await openSettingsPage(page,'custom-appearance');await page.getByText('Advanced customization',{exact:true}).click();
 await page.locator('#theme-name').fill('Successor preview');
 await expect.poll(async()=>(await state()).view.themeDraftName).toBe('Successor preview');
 const successor=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.preview'&&response.request().postDataJSON().args.name==='Successor preview');
 await page.getByRole('button',{name:'Preview',exact:true}).click();
 mode='pass';release();release=null;await successor;
 const closeReceipt=await closed;assert.ok(closeReceipt.ok());assert.equal((await closeReceipt.json()).accepted,true);
 await expect.poll(async()=>(await state()).view.themePreview).toBe(true);
 await expect(page.locator('.a-appearance-preview')).toContainText('Successor preview');
 await openSettingsPage(page,'updates');await expect.poll(async()=>(await state()).view.themePreview).toBe(false);
 await openSettingsPage(page,'custom-appearance');
 // Saved-library previews use the same ownership rule as the CSS button.
 await page.getByRole('button',{name:'Save as copy',exact:true}).click();
 await expect(page.getByText('Saved to your appearance library. Apply separately to use it.',{exact:true})).toBeVisible();
 mode='before';const savedCount=held;
 await page.getByRole('button',{name:'Preview Successor preview',exact:true}).click();await expect.poll(()=>held).toBe(savedCount+1);
 await openSettingsPage(page,'updates');
 const savedResponse=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.preview');
 release();release=null;await savedResponse;
 await expect.poll(async()=>(await state()).view.themePreview).toBe(false);
 await openSettingsPage(page,'custom-appearance');
 await page.unrouteAll({behavior:'wait'});
 await page.getByRole('button',{name:'Preview',exact:true}).click();
 await expect(page.locator('.a-appearance-preview')).toBeVisible();
 await expect.poll(async()=>(await state()).view.themePreview).toBe(true);
 await openSettingsPage(page,'updates');
 await expect.poll(async()=>(await state()).view.themePreview).toBe(false);
 await openSettingsPage(page,'custom-appearance');
 await expect(page.locator('.a-appearance-preview')).toHaveCount(0);
 assert.deepEqual((await state()).theme,applied);
 assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 assert.deepEqual(errors,[]);
 console.log('Appearance preview navigation passed: pending request/receipt and settled preview retire without applying or losing draft.');
}finally{
 release?.();await browser?.close();
 if(fixture.exitCode===null){const exit=new Promise(resolve=>fixture.once('exit',resolve));fixture.kill('SIGTERM');await Promise.race([exit,new Promise((_,reject)=>setTimeout(()=>reject(Error('Fixture did not exit')),5000).unref())]);}
}
