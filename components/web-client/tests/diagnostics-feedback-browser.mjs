// Production host/UI with synthetic local diagnostics; no outbound delivery or provider calls.
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
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const presentation=async patch=>{
  const revision=await page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}},{presentation:true});await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id},{presentation:true});return state.revision+1;},patch);
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getShellState().revision)).toBe(revision);
 };


 let count=0,rejected=true;
 await page.route('**/api/actions',async route=>{
  const envelope=route.request().postDataJSON(),body=envelope?.action==='shell.command'?envelope.args:envelope;
  if(body?.action!=='diagnostics.configure')return route.continue();
  count++;await new Promise(resolve=>release=resolve);
  return rejected?route.fulfill({status:503,json:{accepted:false,error:'Synthetic diagnostics save failure'}}):route.continue();
 });
 await page.goto(url);await openSettingsPage(page,'diagnostics');
 const days=page.getByLabel('Index days',{exact:true}),records=page.getByLabel('Maximum indexed records',{exact:true});await days.fill('21');
 await days.blur();await expect.poll(()=>count).toBe(1);
 const saving=page.getByText('Saving diagnostics settings…',{exact:true});await expect(saving).toBeVisible();
 assert.equal(await page.locator('.a-diagnostics-fields').evaluate(el=>getComputedStyle(el).borderTopWidth),'0px');
 await expect(days).toBeEnabled();await expect(records).toBeEnabled();await expect(page.getByRole('button',{name:'Add server',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 await expect(page.getByLabel('Stream filter',{exact:true})).toBeEnabled();
 for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed']){
  await presentation({scheme,interfaceDetail:detail});
  await expect(saving).toBeVisible();await expect(days).toBeEnabled();
 }
 await page.emulateMedia({reducedMotion:'reduce',forcedColors:'active'});
 assert.equal(await page.locator('.a-check-result.pending .a-progress-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
 assert.equal(count,1);
 release();release=null;
 await expect(page.getByRole('alert').filter({hasText:'Your entries are kept'})).toBeVisible();await expect(days).toHaveValue('21');await expect(days).toBeEnabled();
 rejected=false;await page.getByRole('button',{name:'Retry saving preference',exact:true}).click();await expect.poll(()=>count).toBe(2);
 // Read-only record filters are independent of the submitted capture settings.
 await page.getByLabel('Stream filter',{exact:true}).fill('updates');
 release();release=null;
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().diagnostics?.config?.retentionDays)).toBe(21);
 await expect(days).toBeEnabled();await expect(page.getByLabel('Stream filter',{exact:true})).toHaveValue('updates');
 // A concurrent view update can supply a newer draft even while native inputs
 // are locked. The older accepted save must not erase that draft.
 await days.fill('22');await days.blur();await expect.poll(()=>count).toBe(3);
 await page.evaluate(()=>{const s=window.amplifier.getState(),d=s.view.diagnosticsDraft;void window.amplifier.dispatch('view.update',{patch:{diagnosticsDraft:{...d,config:{...d.config,retentionDays:23}}}},{presentation:true});});
 await expect(days).toHaveValue('23');release();release=null;
 await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays)).toBe(22);
 await expect(days).toBeEnabled();await expect(days).toHaveValue('23');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays),22);
 await days.focus();await days.blur();await expect.poll(()=>count).toBe(4);release();release=null;await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays)).toBe(23);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.retentionDays),23);
 await page.getByRole('button',{name:'Add server',exact:true}).click();
 const name=page.getByLabel('Name',{exact:true});await name.fill('Synthetic disabled destination');await page.getByLabel('Server URL',{exact:true}).fill('https://example.invalid/diagnostics');
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Save destination',exact:true}).click();await expect.poll(()=>count).toBe(5);
 await expect(name).toBeDisabled();await expect(page.getByRole('button',{name:'Saving…',exact:true})).toBeDisabled();
 await expect(page.getByLabel('Server URL',{exact:true})).toBeDisabled();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/diagnostics-saving-mobile.png',animations:'disabled'});
 release();release=null;await expect(name).toBeEnabled();
 const stored=await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations);assert.equal(stored.length,1);assert.equal(stored[0].enabled,false);
 await page.getByRole('button',{name:'Remove destination',exact:true}).click();await expect.poll(()=>count).toBe(6);
 await expect(page.getByRole('button',{name:'Removing…',exact:true})).toBeDisabled();await expect(name).toBeDisabled();release();release=null;
 await expect(days).toBeVisible();
 assert.deepEqual(await page.evaluate(()=>window.amplifier.getState().diagnostics.config.destinations),[]);
 assert.deepEqual(errors,[]);assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Diagnostics save passed: stable working labels, scoped input locking, rejected-save retry, independent record filters, newer concurrent draft retained, disabled destination save/remove, narrow footer; no outbound delivery or model calls.');
}finally{release?.();await browser?.close();fixture.kill('SIGTERM')}
