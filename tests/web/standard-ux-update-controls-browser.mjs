// Four input contexts, real action admission, synthetic busy-lock activation refusal.
// No installation, promotion, restart, or real provider effects.
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=process.env.AMPLIFIER_TEST_ARTIFACTS;
if(!out)throw Error('Owned AMPLIFIER_TEST_ARTIFACTS is required');
await mkdir(out,{recursive:true});
const variants=[['desktop-pointer',1280,950,'pointer'],['desktop-keyboard',1280,950,'keyboard'],['narrow-touch',390,844,'touch'],['narrow-keyboard',390,844,'keyboard']];
const results=[];
for(const [id,width,height,mode] of variants){
 let fixture,browser,page,logs='';const row={id,status:'pending',context:'synthetic-update-refusal',activationEffects:false,limits:['Keyboard activation uses explicit focus plus Enter; full sequential Tab traversal not claimed.','No actual installation/restart; real manager rejects at held operation lock.']},errors=[],external=[],actions=[];
 try{
  fixture=spawn(process.env.AMPLIFIER_TEST_PYTHON||root+'.venv/bin/python',['-u',root+'tests/fixtures/update_restart_server.py']);
  fixture.stderr.on('data',chunk=>logs+=chunk);
  const boot=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),15000);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code))});fixture.stdout.on('data',chunk=>{text+=chunk;for(const line of text.split('\n'))try{const data=JSON.parse(line);if(data.url){clearTimeout(timer);resolve(data)}}catch{}})});
  assert.equal(boot.package,root+'amplifier_web/__init__.py');
  browser=await chromium.launch({headless:true,args:['--no-zygote','--single-process','--disable-gpu']});
  page=await browser.newPage({viewport:{width,height},hasTouch:mode==='touch',isMobile:mode==='touch',extraHTTPHeaders:{Authorization:'Bearer fixture-browser-control-token'}});page.setDefaultTimeout(6000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/actions')actions.push(request.postDataJSON())});
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(['http:','https:'].includes(url.protocol)&&url.hostname!=='127.0.0.1'){external.push(url.origin);return route.abort()}return route.continue()});
  const activate=async control=>{await expect(control).toBeVisible();await expect(control).toBeEnabled();if(mode==='keyboard'){await control.focus();await expect(control).toBeFocused();await control.press('Enter')}else if(mode==='touch')await control.tap();else await control.click()};
  await page.goto(boot.url);await page.waitForSelector('#amp-one');
  const settings=page.getByRole('button',{name:/^Settings(?: \d+ unread items)?$/});
  if(!await settings.isVisible())await activate(page.getByRole('button',{name:'Open navigation',exact:true}));
  await activate(settings);await expect(page.locator('.a-settings-experience')).toBeVisible();
  await page.waitForFunction(()=>{const el=document.querySelector('.a-settings-experience');return el&&el.dataset.compact===String((el.closest('.a-overlay')||el).clientWidth<960)});
  const panel=page.locator('.a-settings-experience');
  for(let depth=0;await panel.getAttribute('data-compact')==='true'&&await panel.getAttribute('data-settings-index')!=='true';depth++){
   if(depth>=6)throw Error('Settings Back did not reach index');const previous=await panel.getAttribute('data-settings-route');
   await activate(panel.locator('.a-settings-mobile-head button').first());await expect(panel).not.toHaveAttribute('data-settings-route',previous);
  }
  await activate(page.locator('[data-settings-section="updates"]'));
  await expect(page.locator('.a-settings-experience')).toHaveAttribute('data-settings-page','updates');
  const state=async()=>{const response=await page.request.get(boot.url+'/fixture/update-restart');assert.ok(response.ok());return response.json()};
  const blockers=page.locator('[data-part="update-blockers"]'),entry=blockers.getByRole('button',{name:'Force restart…',exact:true});
  await expect(entry).not.toBeVisible();await activate(blockers.locator('summary'));await expect(entry).toBeVisible();
  await expect(blockers).toContainText('restarting will not resolve it');assert.equal((await state()).calls.length,0);
  const dialog=page.getByRole('alertdialog',{name:'Confirm update restart'}),cancel=dialog.getByRole('button',{name:'Cancel',exact:true}),confirm=dialog.getByRole('button',{name:'Interrupt work and restart',exact:true});
  await activate(entry);await expect(cancel).toBeFocused();await expect(dialog).toContainText('will not be replayed automatically');
  await activate(cancel);await expect(entry).toBeFocused();assert.equal((await state()).calls.length,0);
  await activate(entry);await page.keyboard.press('Escape');await expect(entry).toBeFocused();assert.equal((await state()).calls.length,0);
  await activate(entry);assert.ok((await page.request.post(boot.url+'/fixture/update-restart')).ok());
  await expect(confirm).toBeDisabled();await expect(dialog).toContainText('staged update or host changed');await activate(cancel);assert.equal((await state()).calls.length,0);
  await activate(entry);const target=(await state()).target;
  const accepted=page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.action==='updates.forceRestart');
  await activate(confirm);const response=await accepted;assert.ok(response.ok());const receipt=await response.json();assert.equal(receipt.accepted,true);assert.ok(receipt.requestId);
  await expect(blockers.getByRole('alert')).toContainText('update operation');await expect(entry).toBeEnabled();
  const actual=await state();assert.equal(actual.calls.length,1);assert.deepEqual(actual.calls[0].args,{...target,confirm:true});assert.equal(actual.calls[0].requestId,receipt.requestId);assert.equal(actual.unknown,true);
  // A failed asynchronous activation permits a new explicit review, never an automatic retry.
  await activate(entry);await expect(cancel).toBeFocused();await activate(cancel);assert.equal((await state()).calls.length,1);
  assert.deepEqual(actions.filter(value=>['updates.install','updates.check','conversation.send','session.warm'].includes(value.action)),[]);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:out+'/'+id+'.png'});
  row.status='passed';row.claims=[{workflow:'updates.force',expectedIndices:[0,1,2,3]}];row.receipt={requestId:receipt.requestId,unknownFenceRetained:true,calls:1};
 }catch(error){row.status='failed';row.error=String(error);if(page)try{await page.screenshot({path:out+'/'+id+'-failure.png'})}catch{}}
 finally{
  try{await browser?.close()}catch(error){row.cleanupError=String(error);row.status='failed'}
  finally{if(fixture&&fixture.exitCode===null){const exited=once(fixture,'exit');fixture.kill('SIGTERM');const timer=setTimeout(()=>fixture.kill('SIGKILL'),5000);try{await exited}finally{clearTimeout(timer)}}await writeFile(out+'/'+id+'-fixture.log',logs);results.push(row);await writeFile(out+'/results.json',JSON.stringify({schemaVersion:1,results},null,2))}
 }
}
console.log(JSON.stringify({results}));if(results.some(row=>row.status!=='passed'))process.exitCode=1;
