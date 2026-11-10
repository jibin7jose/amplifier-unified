// Real shared attention actions, synthetic update lifecycle, no providers or user data.
import {spawn} from 'node:child_process';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../../',import.meta.url));
const artifacts=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp';
const installed=process.env.ATTENTION_INSTALLED==='1';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/browser_detail_server.py',process.env.ATTENTION_PACKAGE_PARENT||root],{stdio:['ignore','pipe','inherit']});
let browser,vite,currentPage;const errors=[],failedRequests=[];
try{
 await mkdir(artifacts,{recursive:true});
 const ready=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code))});fixture.stdout.on('data',chunk=>{output+=chunk;for(const line of output.split('\n'))try{const value=JSON.parse(line);if(value.port){clearTimeout(timer);resolve(value)}}catch{}})});
 const target=`http://127.0.0.1:${ready.port}`,headers={Authorization:'Bearer fixture-detail-token','content-type':'application/json'};
 const control=async step=>{const response=await fetch(target+'/api/fixture/control',{method:'POST',headers,body:JSON.stringify({op:'attention-priority',step})});assert.equal(response.status,200);return response.json()};
 let url=target;
 if(!installed){vite=await createServer({configFile:false,root:root+'frontend',server:{host:'127.0.0.1',port:0,hmr:false,proxy:{'/api':{target,changeOrigin:true,ws:true,configure(proxy){for(const event of ['proxyReq','proxyReqWs'])proxy.on(event,request=>request.setHeader('Origin',target))}},'/branding':target}}});await vite.listen();url=vite.resolvedUrls.local[0];}
 browser=await chromium.launch({headless:true});
 for(const narrow of [false,true]){
  const context=await browser.newContext({viewport:narrow?{width:390,height:844}:{width:1280,height:920},hasTouch:narrow,extraHTTPHeaders:headers});
  const page=await context.newPage();currentPage=page;page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',request=>failedRequests.push({url:request.url(),error:request.failure()?.errorText}));
  page.on('response',response=>{if(response.status()>=400)failedRequests.push({url:response.url(),status:response.status()})});
  await control('recover');const before=await control('seed');
  await page.goto(url);await page.locator('#amp-one').waitFor();
  const activity=page.getByRole('button',{name:/^Activity(?: \d+ (?:unread items|items needing attention))?$/});
  if(!await activity.isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await activity.click();const inbox=page.getByRole('region',{name:'Activity to review',exact:true});
  await expect(inbox.getByText('All caught up.',{exact:true})).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(0);
  await expect(inbox.getByRole('button',{name:'Mark all reviewed',exact:true})).toBeDisabled();
  const news=inbox.locator('summary').filter({hasText:'News and receipts'});await news.click();await expect(inbox.getByText('Optional news 0',{exact:true})).toBeVisible();await news.click();
  await control('fail');const needs=inbox.getByRole('region',{name:'Needs action',exact:true});
  await expect(needs.locator('.a-attention-item')).toHaveCount(1);
  await expect(needs).toContainText('Current components remain active');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(1);
  const review=needs.getByRole('button',{name:'Mark reviewed: Component update needs attention',exact:true});
  await review.focus();await page.keyboard.press('Enter');
  await expect(needs).toContainText('Reviewed · still unresolved');
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(1);
  const first=await page.evaluate(()=>window.amplifier.getState().attention.items.find(i=>i.actionRequired).fingerprint);
  await control('fail');await expect(needs).toContainText('2 occurrences');
  assert.equal(await page.evaluate(()=>window.amplifier.getState().attention.items.find(i=>i.actionRequired).fingerprint),first);
  await control('check');await expect(needs).toContainText('Reviewed · still unresolved');
  const open=needs.getByRole('button',{name:/^Component update needs attention/});
  if(narrow)await open.tap();else{await open.focus();await page.keyboard.press('Enter')}
  await expect(page.locator('[data-part="updates"]')).toBeVisible();
  await expect(page.getByRole('status',{name:'Overall update status'})).toContainText('Component update needs attention');
  await page.reload();await expect(page.locator('[data-part="updates"]')).toContainText('Reading this does not resolve it');
  await page.screenshot({path:`${artifacts}/attention-${installed?'installed':'source'}-${narrow?'narrow':'desktop'}-updates.png`,fullPage:true});
  // Return using the ordinary action; it neither retries updates nor navigates chats.
  await page.evaluate(()=>window.amplifier.dispatch('view.update',{patch:{panel:'activity'}}));
  await expect(needs).toBeVisible();
  await control('distinct');await expect(needs).toContainText('amplifier-module-hooks-approval');
  await expect(needs.locator('.a-attention-item')).toHaveCount(2);
  await expect(needs.getByRole('button',{name:'Mark reviewed: Component update needs attention',exact:true})).toBeVisible();
  const preference=inbox.getByRole('checkbox',{name:'Count finished responses in the app badge',exact:true});
  await preference.check();await expect(preference).toBeEnabled();await preference.uncheck();await expect(preference).toBeEnabled();
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().attention.badgeUnread)).toBe(2);
  await page.screenshot({path:`${artifacts}/attention-${installed?'installed':'source'}-${narrow?'narrow':'desktop'}-activity.png`,fullPage:true});
  assert.ok(await inbox.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  const recovered=await control('recover');await expect(needs).toHaveCount(0);await expect(inbox.getByText('All caught up.',{exact:true})).toBeVisible();
  assert.ok(recovered.state.updates.diagnostics.events.some(e=>e.status==='failed'));
  assert.equal(recovered.runtimeStarts,before.runtimeStarts);assert.equal(recovered.runtimeSends,before.runtimeSends);
  await control('distinct');await expect(needs.getByRole('button',{name:'Mark reviewed: Component update needs attention',exact:true})).toBeVisible();
  await control('recover');await control('uncertainty');
  await expect(needs.locator('.a-attention-item')).toHaveCount(2);
  await inbox.getByRole('button',{name:'Mark all reviewed',exact:true}).click();
  await expect(needs.getByText('Reviewed · still unresolved',{exact:true})).toHaveCount(2);
  const unknown=needs.locator('.a-attention-item').filter({hasText:'Application update needs attention'});
  const overflow=needs.locator('.a-attention-item').filter({hasText:'Additional update failures need review'});
  const startClose=async row=>{const button=row.getByRole('button',{name:'Close unresolved notice',exact:true});if(narrow)await button.tap();else{await button.focus();await page.keyboard.press('Enter')}};
  await startClose(unknown);
  const confirmation=unknown.getByRole('region',{name:'Confirm uncertainty disposition',exact:true});
  await expect(confirmation).toContainText('It does not verify recovery');
  // Freeze the reviewed occurrence while another matching failure arrives.
  await control('unknown-repeat');
  await confirmation.getByRole('button',{name:'Close with recovery unverified',exact:true}).click();
  await expect(confirmation.getByRole('alert')).toContainText('changed');
  await expect(needs.locator('.a-attention-item')).toHaveCount(2);
  await confirmation.getByRole('button',{name:'Cancel',exact:true}).click();
  await startClose(unknown);
  await page.screenshot({path:`${artifacts}/disposition-${installed?'installed':'source'}-${narrow?'narrow':'desktop'}-confirm.png`,fullPage:true});
  await confirmation.getByRole('button',{name:'Close with recovery unverified',exact:true}).click();
  await expect(needs.locator('.a-attention-item')).toHaveCount(1);
  await startClose(overflow);
  // A distinct failure after confirmation must survive closing the overflow.
  await control('distinct');
  await overflow.getByRole('button',{name:'Close with recovery unverified',exact:true}).click();
  await expect(needs.locator('.a-attention-item')).toHaveCount(1);
  await expect(needs).toContainText('amplifier-module-hooks-approval');
  await page.reload();
  await page.locator('#amp-one').waitFor();
  if(!await inbox.isVisible()){
   if(!await activity.isVisible())await page.getByRole('button',{name:'Open navigation',exact:true}).click();
   await activity.click();
  }
  await expect.poll(()=>page.evaluate(()=>window.amplifier.getState().updates.diagnostics.failureDispositions.length)).toBeGreaterThanOrEqual(2);
  const closed=inbox.locator('summary').filter({hasText:'Closed notices · recovery unverified'});
  await closed.click();await expect(inbox).toContainText('Explicitly acknowledged uncertainty, not verified recovery.');
  await page.screenshot({path:`${artifacts}/disposition-${installed?'installed':'source'}-${narrow?'narrow':'desktop'}-closed.png`,fullPage:true});
  assert.ok(await inbox.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  const after=await control('recover');
  assert.equal(after.runtimeStarts,before.runtimeStarts);assert.equal(after.runtimeSends,before.runtimeSends);
  assert.ok(after.state.updates.diagnostics.failureDispositions.every(r=>r.recoveryVerified===false&&r.status==='acknowledged-uncertainty'));
  await context.close();
 }
 assert.deepEqual(errors,[]);
 console.log('PASS attention priority: quiet 53 notices + 3 receipts; first/read/repeat/check/distinct/recovery/next; preferences; explicit unknown/overflow disposition; stale confirmation refusal; concurrent failure preserved; closed-history reload; keyboard + touch; desktop + narrow; '+(installed?'installed':'source')+' boundary.');
}catch(error){
 console.error('Browser diagnostics:',JSON.stringify({errors,failedRequests}));
 if(currentPage&&!currentPage.isClosed()){
  await currentPage.screenshot({path:`${artifacts}/attention-failed-${installed?'installed':'source'}.png`,fullPage:true});
  await writeFile(`${artifacts}/attention-failed-${installed?'installed':'source'}.html`,await currentPage.content());
 }
 throw error;
}finally{await browser?.close();await vite?.close();fixture.kill('SIGTERM');await new Promise(resolve=>fixture.exitCode!==null?resolve():fixture.once('exit',resolve));}
