// Production host/UI with synthetic permission settings; no provider calls.
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
 const presentation=patch=>page.evaluate(async patch=>{const state=window.amplifier.getShellState(),clientId=window.amplifier.shellClientId;const prepared=await window.amplifier.dispatch('shell.changes.prepare',{clientId,expectedRevision:state.revision,composition:{...state.effectiveComposition,presentation:{...state.effectiveComposition.presentation,...patch}}},{presentation:true});await window.amplifier.dispatch('shell.changes.apply',{clientId,expectedRevision:state.revision,changeId:prepared.result.id},{presentation:true})},patch);
 let hold=true,rejected=true,count=0;
 await page.route('**/api/actions',async route=>{
  const envelope=route.request().postDataJSON(),body=envelope?.action==='shell.command'?envelope.args:envelope;
  if(!hold||body?.action!=='permissions.save')return route.continue();
  count++;await new Promise(resolve=>release=resolve);
  if(rejected)return route.fulfill({status:503,json:{accepted:false,error:'Synthetic permission save failure'}});
  return route.continue();
 });
 await page.goto(url);
 await page.evaluate(()=>window.amplifier.dispatch('session.create',{title:'Synthetic file access check'}));
 await openSettingsPage(page,'permissions');
 // Other visited settings editors stay mounted but hidden to retain drafts.
 await expect(page.locator('[data-settings-page="permissions"] .a-settings-page-content:not([hidden]) select')).toHaveCount(0);
 await page.getByRole('button',{name:'Close panel',exact:true}).click();
 await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'chat-files'}}));
 const section=page.locator('.a-chat-pages'),allowed=section.locator('#allowed-folders'),denied=section.locator('#denied-folders'),scope=section.locator('select').first();
 await scope.selectOption('project');
 await allowed.fill('/tmp/synthetic-allowed');await denied.fill('/tmp/synthetic-denied');
 const save=page.getByRole('button',{name:'Save permissions',exact:true}),width=(await save.boundingBox()).width;
 await save.click();await expect.poll(()=>count).toBe(1);
 const saving=page.getByRole('button',{name:'Saving…',exact:true});
 await expect(saving).toBeVisible();await expect(saving).toBeDisabled();
 assert.ok(Math.abs((await saving.boundingBox()).width-width)<0.01,'Saving label must retain button width within browser geometry precision');
 for(const control of [allowed,denied,scope,section.getByRole('button',{name:'Load permissions',exact:true}),...await section.getByRole('button',{name:'Browse',exact:true}).all()])await expect(control).toBeDisabled();
 await expect(section.getByRole('status')).toContainText('Saving file access settings…');
 await page.screenshot({path:'/tmp/permissions-saving-desktop.png',animations:'disabled'});
 await saving.evaluate(el=>el.click());assert.equal(count,1);
 await expect(page.getByRole('button',{name:'Close panel',exact:true})).toBeEnabled();
 for(const scheme of ['light','dark'])for(const detail of ['minimal','standard','detailed']){
  await presentation({scheme,interfaceDetail:detail});
  await expect(page.locator('#amp-one')).toHaveAttribute('data-theme-scheme',scheme);
  await expect(page.locator('#amp-one')).toHaveAttribute('data-interface-detail',detail);
  await expect(saving).toBeVisible();await expect(allowed).toBeDisabled();
 }
 await page.emulateMedia({reducedMotion:'reduce',forcedColors:'active'});
 assert.equal(await section.locator('.a-progress-spinner').evaluate(el=>getComputedStyle(el).animationName),'none');
 release();release=null;
 await expect(section.getByRole('alert')).toContainText('Your entries are kept');
 await expect(allowed).toBeEnabled();await expect(scope).toHaveValue('project');
 await expect(allowed).toHaveValue('/tmp/synthetic-allowed');await expect(denied).toHaveValue('/tmp/synthetic-denied');
 await denied.fill('/tmp/synthetic-denied-revised');rejected=false;
 await save.click();await expect.poll(()=>count).toBe(2);release();release=null;
 await expect(section.getByText('Saved. Idle conversations use these settings on their next message.',{exact:true})).toBeVisible();
 await expect(section.getByRole('alert')).toHaveCount(0);await expect(allowed).toBeEnabled();
 await page.evaluate(()=>window.amplifier.dispatch('permissions.get',{sessionId:window.amplifier.getState().selectedSessionId,scope:'project'}));
 const prefs=await page.evaluate(()=>window.amplifier.getState().permissions);
 assert.deepEqual(prefs.allowed,['/tmp/synthetic-allowed']);assert.deepEqual(prefs.denied,['/tmp/synthetic-denied-revised']);assert.equal(prefs.scope,'project');
 // The chat-owned form must remain usable and locked on a small viewport.
 await page.setViewportSize({width:390,height:844});await save.click();await expect.poll(()=>count).toBe(3);
 await expect(saving).toBeDisabled();await expect(scope).toBeDisabled();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.screenshot({path:'/tmp/permissions-saving-mobile.png',animations:'disabled'});
 await page.getByRole('button',{name:'Close panel',exact:true}).click();release();release=null;
 await expect(section).toHaveCount(0);
 assert.deepEqual(errors,[]);assert.deepEqual((await(await page.request.get(url+'/fixture')).json()).sent,[]);
 console.log('Permission save passed: immediate stable working label, scoped fields disabled, duplicate prevention, rejected draft retained, corrected retry persisted to selected scope, actual appearance choices, reduced motion, forced colors, narrow footer and close while saving.');
}finally{release?.();await browser?.close();fixture.kill('SIGTERM')}
