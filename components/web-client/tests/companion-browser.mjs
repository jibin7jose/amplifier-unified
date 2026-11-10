import {fixturePython} from './fixture-python.mjs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';

const fixture = spawn(fixturePython(fileURLToPath(new URL('../../.venv/bin/python', import.meta.url))),
 [fileURLToPath(new URL('../../tests/fixtures/companion_ui_server.py', import.meta.url))],
 {stdio: ['ignore', 'pipe', 'inherit']});
let browser;
try {
 const ready = await new Promise((resolve, reject) => {
  let output = '';
  const timer = setTimeout(() => reject(Error('Fixture timeout')), 15000);
  fixture.stdout.on('data', chunk => {
   output += chunk;
   for (const line of output.split('\n')) try {
    const value = JSON.parse(line);
    if (value.url) { clearTimeout(timer); resolve(value); }
   } catch {}
  });
  fixture.once('exit', code => reject(Error('Fixture exit ' + code)));
 });
 browser = await chromium.launch({headless: true, args: process.env.DTU_CHROMIUM_SINGLE_PROCESS === '1'
  ? ['--no-zygote', '--single-process', '--disable-gpu'] : []});
 const context = await browser.newContext({extraHTTPHeaders: {Authorization: 'Bearer fixture-browser-control-token'}});
 const ordinary = await context.newPage(), ordinaryActions = [], companionRequests = [];
 context.on('request', request => {
  if (new URL(request.url()).pathname === '/api/companion') companionRequests.push(request.url());
 });
 ordinary.on('request', request => {
  if (request.url().endsWith('/api/actions') && request.method() === 'POST') ordinaryActions.push(request.postDataJSON());
 });
 await ordinary.goto(ready.url + '/');
 await expect(ordinary.locator('.a-adaptive-home')).toBeVisible();
 assert.equal(await ordinary.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.original);
 assert.equal(ordinaryActions.filter(row => row.action === 'session.select').length, 0, 'Normal launch does not navigate');
 const page = await context.newPage(), errors = [], actions = [];
 page.on('pageerror', error => errors.push(error.message));
 page.on('request', request => {
  if (request.url().endsWith('/api/actions') && request.method() === 'POST') actions.push(request.postDataJSON());
 });
 await page.addInitScript(()=>{Object.defineProperty(window,'launchQueue',{value:{setConsumer(fn){window.tempoLaunch=fn}},configurable:true})});
 await page.goto(ready.url + '/?chat=' + encodeURIComponent(ready.destination));
 await expect(page.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Companion destination');
 await expect(page).toHaveURL(ready.url + '/');
 assert.equal(actions.filter(row => row.action === 'session.select' && row.args.id === ready.destination).length, 1);
 assert.equal(actions.filter(row => row.action === 'conversation.send').length, 0, 'Opening a notification cannot send a turn');
 await page.reload();
 await expect(page.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Companion destination');
 assert.equal(actions.filter(row => row.action === 'session.select' && row.args.id === ready.destination).length, 1, 'Reload does not repeat navigation');
 await ordinary.reload();
 await expect(ordinary.locator('.a-adaptive-home')).toBeVisible();
 assert.equal(await ordinary.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.original);
 assert.equal(ordinaryActions.filter(row => row.action === 'conversation.send').length, 0);
 // Warm installed-app launches preserve the document and select only this window.
 await page.evaluate(()=>{window.tempoDocumentMarker='same-document'});
 await page.evaluate(url=>window.tempoLaunch({targetURL:url}),ready.url+'/?chat='+ready.original);
 await expect(page.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Settings test');
 await page.evaluate(url=>window.tempoLaunch({targetURL:url}),ready.url+'/?chat='+ready.destination);
 await expect(page.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Companion destination');
 assert.equal(await page.evaluate(()=>window.tempoDocumentMarker),'same-document','Launch must preserve the mounted document');
 await expect(ordinary.locator('.a-adaptive-home')).toBeVisible();
 assert.equal(await ordinary.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.original);
 assert.deepEqual(companionRequests, [], 'Browser clients never poll Tempo summaries');
 assert.deepEqual(errors, []);
 // A failed warm launch retains exact intent and mounted client state until retry.
 await page.evaluate(async()=>{
  await window.amplifier.dispatch('canvas.show',{kind:'html',title:'Warm launch canvas',content:'<input aria-label="Local canvas draft">'});
  await window.amplifier.dispatch('view.update',{patch:{draft:'unsent warm-launch draft'}});
  window.tempoCanvasMount=document.querySelector('#amp-one');
 });
 await page.frameLocator('.a-canvas-html').getByRole('textbox').fill('canvas-local input');
 await page.evaluate(()=>{window.tempoCanvasFrame=document.querySelector('.a-canvas-html')});
 let rejectNavigation=true;
 await page.route('**/api/actions**',async route=>{
  const body=route.request().postDataJSON();
  if(rejectNavigation&&body?.action==='session.select'&&body.args.id===ready.original)return route.abort('failed');
  return route.continue();
 });
 await page.evaluate(url=>window.tempoLaunch({targetURL:url}),ready.url+'/?chat='+ready.original);
 await expect(page.locator('[data-chat-link-failure]')).toBeVisible();
 await expect(page).toHaveURL(ready.url+'/?chat='+ready.original);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.destination);
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'unsent warm-launch draft');
 assert.equal(await page.evaluate(()=>window.tempoCanvasFrame===document.querySelector('.a-canvas-html')),true,'Failed navigation preserves the mounted canvas');
 await expect(page.frameLocator('.a-canvas-html').getByRole('textbox')).toHaveValue('canvas-local input');
 rejectNavigation=false;
 await page.getByRole('button',{name:'Retry opening chat',exact:true}).click();
 await expect(page.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Settings test');
 await expect(page).toHaveURL(ready.url+'/');
 assert.equal(await page.evaluate(()=>window.tempoDocumentMarker),'same-document');
 assert.equal(await page.evaluate(()=>window.tempoCanvasMount===document.querySelector('#amp-one')),true);
 await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),ready.destination);
 await expect(page.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Companion destination');
 assert.equal(await page.evaluate(()=>window.amplifier.getState().view.draft),'unsent warm-launch draft');
 await expect(ordinary.locator('.a-adaptive-home')).toBeVisible();
 assert.equal(await ordinary.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.original);
 await page.unroute('**/api/actions**');
 // Deleted/missing warm targets fail once. A newer user selection supersedes them.
 for(const target of ['unavailable-link-target','00000000-0000-4000-8000-000000000001']){
  const before=actions.filter(row=>row.action==='session.select'&&row.args.id===target).length;
  await page.evaluate(url=>window.tempoLaunch({targetURL:url}),ready.url+'/?chat='+target);
  await expect(page.locator('[data-chat-link-failure]')).toBeVisible();
  await expect(page).toHaveURL(ready.url+'/?chat='+target);
  assert.equal(await page.evaluate(()=>window.amplifier.getState().selectedSessionId),ready.destination);
  await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),ready.original);
  await expect(page.locator('[data-chat-link-failure]')).toHaveCount(0);
  await expect(page).toHaveURL(ready.url+'/');
  assert.equal(actions.filter(row=>row.action==='session.select'&&row.args.id===target).length,before+1);
  await page.evaluate(id=>window.amplifier.dispatch('session.select',{id}),ready.destination);
 }
 // Unavailable links report failure without clearing the target or selecting a substitute.
 for (const target of ['unavailable-link-target', '00000000-0000-4000-8000-000000000001']) {
  await page.goto(ready.url + '/?chat=' + target);
  await expect(page.getByRole('alert')).toContainText('Select or create a conversation first.');
  await expect(page).toHaveURL(ready.url + '/?chat=' + target);
  assert.equal(await page.evaluate(() => window.amplifier.getState().selectedSessionId), ready.destination);
 }
 // A fresh browser also works with the DTU's single-process Chromium mode.
 await browser.close();
 browser = await chromium.launch({headless: true, args: process.env.DTU_CHROMIUM_SINGLE_PROCESS === '1'
  ? ['--no-zygote', '--single-process', '--disable-gpu'] : []});
 const loginContext = await browser.newContext();
 const login = await loginContext.newPage();
 const submissions = [];
 login.on('request', request => {
  if (request.method() === 'POST' && request.url().endsWith('/api/actions')) submissions.push(request.postDataJSON());
 });
 await login.goto(ready.url + '/?chat=' + ready.destination);
 await expect(login).toHaveURL(/\/login\?next=/);
 await login.getByLabel('Username').fill('fixture-user');
 await login.getByLabel('Password').fill('fixture-only');
 await login.getByRole('button', {name:'Sign in', exact:true}).click();
 await expect(login.getByRole('navigation',{name:'Breadcrumb'}).locator('[aria-current=page]')).toContainText('Companion destination');
 await expect(login).toHaveURL(ready.url + '/');
 assert.equal(submissions.filter(row => row.action === 'conversation.send').length, 0);
 assert.equal(actions.filter(row => row.action === 'conversation.send').length, 0);
 console.log('Chat links passed: exact target, warm failure/retry, draft and canvas input preservation, newer-navigation supersession, URL cleanup, reload, independent windows, unavailable/deleted targets, login round trip, no turn or polling.');
} finally {
 await browser?.close();
 fixture.kill();
}
