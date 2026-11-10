// Built UI, real action receipt/rejection, no installation or restart effects.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {openSettingsPage} from './browser-settings.mjs';
const fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||fileURLToPath(new URL('../../.venv/bin/python',import.meta.url)),['-u',fileURLToPath(new URL('../../tests/fixtures/update_restart_server.py',import.meta.url))]);
const out=process.env.AMPLIFIER_TEST_ARTIFACTS||'/tmp/update-restart';
let browser,page,log='';const result={status:'pending',realHTTP:true,activationEffects:false};
fixture.stderr.on('data',data=>log+=data);
try{
 await mkdir(out,{recursive:true});
 const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error(log||'Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error(`Fixture exited ${code}: ${log}`))});fixture.stdout.on('data',data=>{text+=data;for(const line of text.split('\n'))try{const row=JSON.parse(line);if(row.url){clearTimeout(timer);resolve(row)}}catch{}})});
 assert.equal(boot.package,fileURLToPath(new URL('../../amplifier_web/__init__.py',import.meta.url)));
 browser=await chromium.launch({headless:true,args:process.env.CHROMIUM_SINGLE_PROCESS==='1'?['--no-zygote','--single-process','--disable-gpu']:[]});
 page=await browser.newPage({viewport:{width:1280,height:950},extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(boot.url);await page.waitForSelector('#amp-one');await openSettingsPage(page,'updates');
 const blockers=page.locator('[data-part="update-blockers"]'),entry=blockers.getByRole('button',{name:'Force restart…',exact:true});
 await expect(entry).not.toBeVisible();await blockers.locator('summary').click();await expect(entry).toBeVisible();
 await expect(blockers).toContainText('restarting will not resolve it');
 const dialog=page.getByRole('alertdialog',{name:'Confirm update restart'}),confirm=dialog.getByRole('button',{name:'Interrupt work and restart',exact:true}),cancel=dialog.getByRole('button',{name:'Cancel',exact:true});
 await entry.click();await expect(cancel).toBeFocused();await expect(dialog).toContainText('will not be replayed automatically');
 await cancel.click();await expect(entry).toBeFocused();
 const evidence=async()=>{const response=await page.request.get(boot.url+'/fixture/update-restart');assert.ok(response.ok());return response.json()};
 assert.equal((await evidence()).calls.length,0);
 await entry.click();await page.keyboard.press('Escape');await expect(entry).toBeFocused();
 await entry.click();assert.ok((await page.request.post(boot.url+'/fixture/update-restart')).ok());
 await expect(confirm).toBeDisabled();await expect(dialog).toContainText('staged update or host changed');
 await cancel.click();assert.equal((await evidence()).calls.length,0);
 for(const width of [1280,390]){
  await page.setViewportSize({width,height:900});
  await entry.click();await expect(cancel).toBeFocused();
  const target=(await evidence()).target;
  const response=page.waitForResponse(r=>r.url().endsWith('/api/actions')&&r.request().method()==='POST'&&r.request().postDataJSON()?.action==='updates.forceRestart');
  await confirm.click();const receipt=await(await response).json();assert.equal(receipt.accepted,true);assert.ok(receipt.requestId);
  await expect(blockers.getByRole('alert')).toContainText('update operation');await expect(entry).toBeEnabled();
  const observed=await evidence();const last=observed.calls.at(-1);
  assert.deepEqual(last.args,{...target,confirm:true});assert.equal(last.requestId,receipt.requestId);assert.equal(observed.unknown,true);
  await page.screenshot({path:`${out}/confirmed-${width}.png`});
 }
 assert.equal((await evidence()).calls.length,2);assert.deepEqual(errors,[]);
 result.status='passed';result.checks={placement:true,cancelNoEffect:true,focus:true,staleTarget:true,exactReceipt:true,asyncRejectionRecovery:true,desktopAndMobile:true,unknownFencePreserved:true};
}catch(error){result.status='failed';result.error=String(error);if(page)await page.screenshot({path:out+'/failed.png'});throw error}
finally{
 try{await browser?.close()}
 finally{
  fixture.kill('SIGTERM');
  await new Promise(resolve=>{if(fixture.exitCode!==null)return resolve();const timer=setTimeout(()=>{fixture.kill('SIGKILL');resolve()},5000);fixture.once('exit',()=>{clearTimeout(timer);resolve()})});
  result.fixtureLog=log;await writeFile(out+'/result.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }
}
