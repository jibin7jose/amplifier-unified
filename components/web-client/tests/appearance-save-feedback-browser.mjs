// Real host/storage and production assets; delayed receipts, no model calls.
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
 browser=await chromium.launch({headless:true,args:process.env.DTU_CHROMIUM_SINGLE_PROCESS?['--no-zygote','--single-process','--disable-gpu']:[]});
 const page=await browser.newPage({viewport:{width:1280,height:900},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(url);await openSettingsPage(page,'custom-appearance');
 await page.getByText('Advanced customization',{exact:true}).click();
 await page.locator('#theme-name').fill('Retained appearance draft');
 const originalCss=await page.locator('#theme-css').inputValue();
 let held=null,requestCount=0,outcome='success';
 await page.route('**/api/actions',async route=>{
  const body=route.request().postDataJSON();
  if(body?.action!=='theme.save')return route.continue();
  requestCount++;held=body;const result=outcome;
  await new Promise(resolve=>release=resolve);
  if(result==='error')return route.fulfill({status:409,json:{accepted:false,error:'Fixture save was rejected; your draft is kept.'}});
  return route.continue();
 });
 const save=page.getByRole('button',{name:'Save as copy',exact:true});
 const beforeBox=await save.boundingBox();
 await save.focus();await page.keyboard.press('Enter');
 await expect.poll(()=>held?.action).toBe('theme.save');
 await expect(page.locator('#theme-name')).toBeDisabled();
 await expect(page.locator('#theme-css')).toBeDisabled();
 await expect(page.getByRole('button',{name:'Saving…',exact:true})).toBeDisabled();
 await expect(page.getByRole('status').filter({hasText:'Saving appearance…'})).toBeVisible();
 const working=page.getByRole('button',{name:'Saving…',exact:true});
 assert.equal((await working.boundingBox()).width,beforeBox.width,'Working label preserves button width');
 for(const colorScheme of ['light','dark'])for(const reducedMotion of ['reduce','no-preference']){
  await page.emulateMedia({colorScheme,reducedMotion});
  await expect(page.locator('#theme-css')).toBeDisabled();
  await expect(working).toBeDisabled();
  assert.equal(await working.evaluate(el=>getComputedStyle(el).animationName),'none');
 }
 await page.emulateMedia({forcedColors:'active',reducedMotion:'reduce'});
 assert.equal(await page.locator('#theme-css').evaluate(el=>getComputedStyle(el).opacity),'1');
 await page.emulateMedia({forcedColors:'none',reducedMotion:'reduce'});
 await page.keyboard.press('Enter');assert.equal(requestCount,1);
 await expect(page.locator('#theme-name')).toHaveValue('Retained appearance draft');
 await expect(page.locator('#theme-css')).toHaveValue(originalCss);
 release();release=null;
 await expect(page.locator('#theme-name')).toBeEnabled();
 await expect(page.getByText('Saved to your appearance library. Apply separately to use it.',{exact:true})).toBeVisible();
 // A failed write re-enables the same retained draft and offers another try.
 held=null;outcome='error';await save.click();await expect.poll(()=>held?.action).toBe('theme.save');release();release=null;
 await expect(page.getByRole('alert').filter({hasText:'Fixture save was rejected'})).toBeVisible();
 await expect(page.locator('#theme-name')).toBeEnabled();await expect(page.locator('#theme-css')).toHaveValue(originalCss);
 // Closing or visiting another page remains available. A completion while the
 // page is inactive must not leave its local save lock stuck on return.
 held=null;outcome='success';await save.click();await expect.poll(()=>held?.action).toBe('theme.save');
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 await openSettingsPage(page,'notifications');
 const finished=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.save');
 release();release=null;await finished;
 await openSettingsPage(page,'custom-appearance');
 if(!await page.locator('#theme-name').isVisible())await page.getByText('Advanced customization',{exact:true}).click();
 await expect(page.locator('#theme-name')).toBeEnabled();await expect(page.locator('#theme-name')).toHaveValue('Retained appearance draft');
 held=null;outcome='error';await save.click();await expect.poll(()=>held?.action).toBe('theme.save');
 await openSettingsPage(page,'notifications');
 const rejected=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.save');
 release();release=null;await rejected;
 await openSettingsPage(page,'custom-appearance');
 await expect(page.getByRole('alert').filter({hasText:'Fixture save was rejected'})).toBeVisible();
 await expect(page.locator('#theme-name')).toBeEnabled();
 // Reverse the ordering: return before rejection without starting a successor.
 // Releasing the abandoned local lock must not discard this request's error.
 held=null;outcome='error';await save.click();await expect.poll(()=>held?.action).toBe('theme.save');
 await openSettingsPage(page,'notifications');await openSettingsPage(page,'custom-appearance');
 await expect(page.locator('#theme-name')).toBeEnabled();
 const returnedRejection=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='theme.save');
 release();release=null;await returnedRejection;
 await expect(page.getByRole('alert').filter({hasText:'Fixture save was rejected'})).toBeVisible();
 await expect(page.locator('#theme-name')).toHaveValue('Retained appearance draft');
 await expect(page.locator('#theme-css')).toHaveValue(originalCss);
 await expect(page.locator('#theme-name')).toBeEnabled();
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 const inspection=await(await page.request.get(url+'/fixture')).json();assert.deepEqual(inspection.sent,[]);
 assert.deepEqual(errors,[]);
 console.log('Appearance save feedback passed: immediate keyboard activation guard, precise draft locking, working label/status, retained text, failure/retry, inactive completion and return-before-rejection, contrast/motion and mobile.');
}finally{release?.();await browser?.close();fixture.kill('SIGTERM')}
